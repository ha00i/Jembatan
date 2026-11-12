#!/usr/bin/env python3
"""Jembatan: ChatGPT <-> GitHub. Hanya library standar Python. Lihat README.md."""
import base64, hmac, json, os, re, shlex, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerr, request as urlreq
from urllib.parse import parse_qs, urlparse

GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ADMIN = os.environ.get("ADMIN_TOKEN") or GH_TOKEN  # tanpa ADMIN_TOKEN, GitHub token dipakai untuk masuk UI
WORKDIR = Path(os.environ.get("WORKDIR", "./repos")).resolve()
HOST, PORT = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "5111"))
TEST_CMD = os.environ.get("TEST_CMD", "")
DEPLOY_MAP = json.loads(os.environ.get("DEPLOY_MAP") or "{}")
HERE = Path(__file__).resolve().parent
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
LOCK = threading.Lock()
FAILS = {}  # ip -> (jumlah salah, waktu terakhir); 10 salah = diblokir 5 menit


def blocked(ip):
    c, t = FAILS.get(ip, (0, 0))
    if c >= 10 and time.time() - t >= 300:
        FAILS.pop(ip, None)
        return False
    return c >= 10


class Err(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


# ---------- proses, git, GitHub ----------
def run(args, label, cwd=None, timeout=120, check=True):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        raise Err(f"{label} melewati batas waktu", 504)
    except OSError as e:
        raise Err(f"{label}: {e}", 500)
    out = (p.stdout + p.stderr).strip()
    if check and p.returncode:
        raise Err(f"{label} gagal: {out[-1500:]}")
    return out


def auth():
    b = base64.b64encode(f"x-access-token:{GH_TOKEN}".encode()).decode()
    return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {b}"]


def git(d, *a, net=False, timeout=120, check=True):
    label = "git " + next((x for x in a if not x.startswith("-") and "=" not in x), "")
    return run(["git", *(auth() if net else []), *a], label, cwd=d, timeout=timeout, check=check)


def gh(path, method="GET", body=None):
    req = urlreq.Request("https://api.github.com" + path, method=method,
                         data=json.dumps(body).encode() if body else None,
                         headers={"Authorization": f"Bearer {GH_TOKEN}", "User-Agent": "jembatan",
                                  "Accept": "application/vnd.github+json",
                                  "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urlreq.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urlerr.HTTPError as e:
        raise Err(f"GitHub {e.code}: {e.read().decode()[:300]}", 502)
    except urlerr.URLError as e:
        raise Err(f"GitHub tidak terjangkau: {e.reason}", 502)


def gh_pages(path, limit=5):
    out = []
    for p in range(1, limit + 1):
        items = gh(f"{path}{'&' if '?' in path else '?'}per_page=100&page={p}")
        out += items
        if len(items) < 100:
            break
    return out


def repo_dir(r):
    if not REPO_RE.match(r or ""):
        raise Err("Nama repo tidak valid")
    return WORKDIR / r.replace("/", "__")


def need_branch(b):
    if not b or not BRANCH_RE.match(b) or b.startswith("-"):
        raise Err("Nama branch tidak valid")
    return b


def default_branch(d):
    try:
        return git(d, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").split("/", 1)[1]
    except Err:
        for b in ("main", "master", "dev"):
            if git(d, "rev-parse", "--verify", "-q", f"origin/{b}", check=False):
                return b
        raise Err("Branch default tidak ditemukan")


# ---------- repo & sinkron ----------
def list_repos():
    items = gh_pages("/user/repos?sort=updated&affiliation=owner,collaborator,organization_member")
    return [{"name": i["full_name"], "private": i["private"],
             "synced": (repo_dir(i["full_name"]) / ".git").exists()} for i in items]


def sync(repos):
    WORKDIR.mkdir(parents=True, exist_ok=True)
    res = {}
    for r in repos:
        try:
            d = repo_dir(r)
            if (d / ".git").exists():
                git(d, "fetch", "--all", "--prune", net=True, timeout=300)
            else:
                git(None, "clone", f"https://github.com/{r}.git", str(d), net=True, timeout=900)
            res[r] = "ok"
        except Err as e:
            res[r] = str(e)
    return res


# ---------- baca branch & issue, bandingkan ----------
def nums(text):
    return {int(n) for n in re.findall(r"(?<![\w/])#(\d+)", text)}


def name_nums(name):
    return {int(n) for n in re.findall(r"(?<![\dv.])(\d{1,6})(?!\d)", name)}


def branches(d, base):
    merged = {l.strip() for l in git(d, "branch", "-r", "--merged", f"origin/{base}").splitlines()}
    fmt = "--format=%(refname:short)\x1f%(committerdate:short)"
    out = []
    for line in git(d, "for-each-ref", "refs/remotes/origin", fmt).splitlines():
        ref, date = line.split("\x1f", 1)
        if ref in ("origin", "origin/HEAD", f"origin/{base}"):
            continue
        behind, ahead = map(int, git(d, "rev-list", "--left-right", "--count",
                                     f"origin/{base}...{ref}").split())
        out.append(dict(name=ref[7:], date=date, ahead=ahead, behind=behind, merged=ref in merged,
                        subjects=git(d, "log", f"origin/{base}..{ref}", "--format=%s", "-n", "100")))
    return out


def overview(r):
    d = repo_dir(r)
    if not (d / ".git").exists():
        raise Err("Repo belum disinkron. Centang lalu klik Sinkron.", 404)
    base = default_branch(d)
    brs = branches(d, base)
    in_base = nums(git(d, "log", f"origin/{base}", "-n", "3000", "--format=%s%n%b"))
    issues = [i for i in gh_pages(f"/repos/{r}/issues?state=all&sort=updated", 3) if "pull_request" not in i]
    linked, rows = set(), []
    for i in issues:
        n = i["number"]
        lb = [b for b in brs if n in name_nums(b["name"]) or n in nums(b["subjects"])]
        linked.update(b["name"] for b in lb)
        top = max(lb, key=lambda b: b["ahead"], default=None)
        if i["state"] == "closed":
            st = "done"
        elif top and top["ahead"] > 0:
            st = "ready"
        elif lb or n in in_base:
            st = "merged_open"
        else:
            st = "none"
        cmd = ""
        if st == "ready":
            msg = f"Merge {top['name']} (closes #{n})"
            cmd = (f"git checkout {base} && git pull --ff-only && "
                   f"git merge --no-ff {shlex.quote('origin/' + top['name'])} -m {shlex.quote(msg)} && "
                   f"git push origin {base}")
        keep = ("name", "ahead", "behind", "merged", "date")
        rows.append(dict(number=n, title=i["title"], url=i["html_url"], state=i["state"], status=st,
                         branches=[{k: b[k] for k in keep} for b in lb], merge_cmd=cmd))
    orphans = [{k: b[k] for k in ("name", "ahead", "behind", "merged", "date")}
               for b in brs if b["name"] not in linked]
    return dict(repo=r, base=base, issues=rows, orphans=orphans)


# ---------- prompt untuk ChatGPT ----------
FORMAT = """## Yang saya minta
Jawab persis dengan format ini, tanpa penjelasan di luar format:

STATUS: SELESAI | SEBAGIAN | BELUM
KEKURANGAN:
- (poin-poin yang masih kurang; tulis "tidak ada" bila sudah lengkap)
PATCH:
(satu blok ```diff berformat unified diff; path relatif dari root repo dengan awalan a/ dan b/;
 kosongkan bila tidak perlu perubahan)

Jika kamu butuh file lain untuk menjawab, tulis satu baris "BUTUH FILE: path1, path2" dan jangan membuat patch."""


def build_prompt(r, n, branch=None, files=()):
    d = repo_dir(r)
    base = default_branch(d)
    ref = need_branch(branch) if branch else base
    issue = gh(f"/repos/{r}/issues/{int(n)}")
    tree = git(d, "ls-tree", "-r", "--name-only", f"origin/{base}").splitlines()
    p = [f"Kamu membantu mengerjakan issue #{n} di repo {r} (branch default: {base}).", "",
         f"## Issue #{n}: {issue['title']}", (issue.get("body") or "(kosong)")[:4000], "",
         f"## Daftar file ({len(tree)} file, maksimal 400 ditampilkan)", "\n".join(tree[:400]), ""]
    if branch:
        rng = f"origin/{base}...origin/{ref}"
        p += [f"## Pekerjaan di branch {ref} (dibanding {base})", git(d, "diff", "--stat", rng),
              "```diff", git(d, "diff", rng)[:30000], "```", ""]
    for f in list(files)[:10]:
        p += [f"## Isi file {f}", "```", git(d, "show", f"origin/{ref}:{f}")[:12000], "```", ""]
    p.append(FORMAT)
    text = "\n".join(p)
    return {"prompt": text, "chars": len(text)}


# ---------- terapkan patch dari ChatGPT ----------
def extract_patch(text):
    text = text.replace("\r\n", "\n")
    blocks = [b for b in re.findall(r"```(?:diff|patch)?[ \t]*\n(.*?)```", text, re.S)
              if re.search(r"^(diff --git|--- )", b, re.M)]
    if blocks:
        patch = "\n".join(blocks)
    else:
        m = re.search(r"^diff --git", text, re.M)
        patch = text[m.start():] if m else ""
    if not patch.strip():
        raise Err("Tidak ada patch (blok ```diff) di balasan itu.")
    return patch.rstrip("\n") + "\n"


def apply_patch(r, n, reply, branch=None):
    d = repo_dir(r)
    base = default_branch(d)
    if git(d, "status", "--porcelain"):
        raise Err("Folder repo ini kotor. Jangan edit manual folder kerja bot.", 409)
    patch = extract_patch(reply)
    created = not branch
    if created:
        branch = f"bridge/issue-{int(n)}-{time.strftime('%m%d-%H%M%S')}-{os.urandom(2).hex()}"
        git(d, "checkout", "-q", "--no-track", "-b", branch, f"origin/{base}")
    else:
        if not need_branch(branch).startswith("bridge/"):
            raise Err("Hanya branch bridge/* yang boleh diubah bot.")
        git(d, "checkout", "-q", branch)
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(patch)
    try:
        git(d, "apply", "--recount", "--whitespace=nowarn", f.name)
    except Err as e:
        if created:
            git(d, "checkout", "-q", "--detach", f"origin/{base}", check=False)
            git(d, "branch", "-D", branch, check=False)
        raise Err("Patch tidak cocok dengan kode sekarang. Minta ChatGPT membuat ulang dengan "
                  f"file terbaru. Detail: {e}")
    finally:
        os.unlink(f.name)
    git(d, "add", "-A")
    if not git(d, "status", "--porcelain"):
        raise Err("Patch tidak mengubah apa pun.")
    git(d, "-c", "user.name=Jembatan", "-c", "user.email=jembatan@localhost",
        "commit", "-q", "-m", f"Kerjakan #{int(n)} (patch dari ChatGPT)")
    res = {"branch": branch, "commit": git(d, "rev-parse", "--short", "HEAD"), "test": None}
    if TEST_CMD:
        try:
            p = subprocess.run(shlex.split(TEST_CMD), cwd=d, capture_output=True, text=True, timeout=900)
            res["test"] = {"ok": p.returncode == 0, "output": (p.stdout + p.stderr)[-4000:]}
        except (OSError, subprocess.TimeoutExpired) as e:
            res["test"] = {"ok": False, "output": str(e)}
        if not res["test"]["ok"]:
            res["followup"] = (f"Patch tadi sudah diterapkan di branch {branch}, tetapi test gagal.\n"
                               f"Output test:\n```\n{res['test']['output']}\n```\n"
                               "Balas dengan format yang sama (STATUS/KEKURANGAN/PATCH). Patch harus "
                               "relatif terhadap kondisi branch SEKARANG, yaitu setelah patch pertama.")
    return res


def push(r, branch, n):
    d = repo_dir(r)
    if not need_branch(branch).startswith("bridge/"):
        raise Err("Bot hanya boleh push ke branch bridge/*.")
    git(d, "push", "-u", "origin", branch, net=True, timeout=300)
    try:
        pr = gh(f"/repos/{r}/pulls", "POST", {
            "title": f"Kerjakan #{int(n)}", "head": branch, "base": default_branch(d),
            "body": f"Closes #{int(n)}\n\nDibuat lewat Jembatan (patch dari ChatGPT)."})
        return {"pushed": True, "pr": pr["html_url"]}
    except Err as e:
        return {"pushed": True, "pr": None, "error": str(e)}


# ---------- deploy dengan Docker ----------
def deploy(r):
    d = Path(DEPLOY_MAP.get(r) or repo_dir(r))
    if not (d / ".git").exists():
        raise Err("Folder deploy belum ada atau bukan repo git.", 404)
    if r in DEPLOY_MAP:
        git(d, "pull", "--ff-only", net=True, timeout=300)
    else:
        git(d, "fetch", "--prune", net=True, timeout=300)
        git(d, "checkout", "-q", "--detach", f"origin/{default_branch(d)}")
    out = run(["docker", "compose", "up", "-d", "--build"], "docker compose", cwd=d, timeout=1800)
    return {"output": out[-4000:]}


# ---------- HTTP ----------
ROUTES = {
    ("GET", "/api/repos"): lambda q, b: list_repos(),
    ("POST", "/api/sync"): lambda q, b: sync(b["repos"]),
    ("GET", "/api/overview"): lambda q, b: overview(q["repo"]),
    ("POST", "/api/prompt"): lambda q, b: build_prompt(b["repo"], int(b["issue"]), b.get("branch") or None,
                                                       b.get("files") or []),
    ("POST", "/api/apply"): lambda q, b: apply_patch(b["repo"], int(b["issue"]), b["reply"],
                                                     b.get("branch") or None),
    ("POST", "/api/push"): lambda q, b: push(b["repo"], b["branch"], int(b["issue"])),
    ("POST", "/api/deploy"): lambda q, b: deploy(b["repo"]),
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))), ("Cache-Control", "no-store")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method):
        u = urlparse(self.path)
        if method == "GET" and u.path == "/":
            return self.reply(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        fn = ROUTES.get((method, u.path))
        if not fn:
            return self.reply(404, {"error": "Tidak ditemukan"})
        ip = self.client_address[0]
        if blocked(ip):
            return self.reply(429, {"error": "Terlalu banyak token salah. Coba lagi 5 menit lagi."})
        if not hmac.compare_digest(self.headers.get("X-Admin-Token", ""), ADMIN):
            FAILS[ip] = (FAILS.get(ip, (0, 0))[0] + 1, time.time())
            return self.reply(401, {"error": "Token salah atau kosong"})
        FAILS.pop(ip, None)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise Err("Isi terlalu besar", 413)
            body = json.loads(self.rfile.read(n) or b"{}") if method == "POST" else {}
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            with LOCK:
                self.reply(200, fn(q, body))
        except Err as e:
            self.reply(e.code, {"error": str(e)})
        except (KeyError, ValueError, TypeError):
            self.reply(400, {"error": "Input tidak lengkap atau tidak valid"})
        except Exception as e:  # jangan bocorkan detail internal ke klien
            print("ERROR", repr(e), file=sys.stderr)
            self.reply(500, {"error": "Kesalahan internal; lihat log server"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


def main():
    if not GH_TOKEN or len(ADMIN) < 12:
        sys.exit("Isi GITHUB_TOKEN dulu (token GitHub asli, minimal 12 karakter). Lihat README.md.")
    WORKDIR.mkdir(parents=True, exist_ok=True)
    print(f"Jembatan jalan di {HOST}:{PORT}  (buka http://<IP-server>:{PORT})  folder kerja: {WORKDIR}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()


if __name__ == "__main__":
    main()
