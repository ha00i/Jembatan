#!/usr/bin/env python3
"""Jembatan: ChatGPT <-> GitHub. Hanya library standar Python. Lihat README.md."""
import base64, hashlib, hmac, json, os, re, shlex, subprocess, sys, tempfile, threading, time
import branch_manager
import docker_control
import qa_jobs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerr, request as urlreq
from urllib.parse import parse_qs, urlparse, quote as urlquote

GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ADMIN = os.environ.get("ADMIN_TOKEN") or GH_TOKEN  # tanpa ADMIN_TOKEN, GitHub token dipakai untuk masuk UI
WORKDIR = Path(os.environ.get("WORKDIR", "./repos")).resolve()
HOST, PORT = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "5111"))
TEST_CMD = os.environ.get("TEST_CMD", "")
DEPLOY_MAP = json.loads(os.environ.get("DEPLOY_MAP") or "{}")
HERE = Path(__file__).resolve().parent
REPO_RE = re.compile(r"^(?!\.{1,2}/)[A-Za-z0-9_.-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+$")
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
    if not isinstance(r, str) or not REPO_RE.fullmatch(r):
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
FORMAT = """## Kontrak penyelesaian (WAJIB)
Kerjakan SEMUA kriteria dan ruang lingkup issue dalam SATU jawaban.
Jangan berhenti di satu fungsi, sebagian file, atau langkah berikutnya.
Tidak boleh mengatakan SELESAI jika masih ada pekerjaan yang kurang.
Jika konteks file belum cukup, balas hanya "BUTUH FILE: path1, path2";
jangan kirim patch parsial atau mengarang hasil tes.
Jika output akan terpotong, minta konteks yang tepat dan jangan klaim SELESAI.

STATUS: SELESAI
KEKURANGAN:
- tidak ada
PERUBAHAN:
- daftar perubahan yang memenuhi SEMUA kriteria issue
VERIFIKASI:
- tes yang benar-benar dijalankan (jika belum, tulis "belum dijalankan")
PATCH:
```diff
(unified diff lengkap, path a/ dan b/, tidak dipotong)
```

STATUS: SEBAGIAN, kekurangan yang belum kosong, atau diff tidak lengkap
akan DITOLAK Jembatan. QA dan merge tetap tahap terpisah."""


MAX_PROMPT_CHARS = 125000


def build_prompt(r, n, branch=None, files=(), target=None):
    d = repo_dir(r)
    base = target or default_branch(d)
    if not docker_control.valid_branch(base) or not git(d, "rev-parse", "--verify", "-q",
                                                      f"refs/remotes/origin/{base}", check=False):
        raise Err("Branch tujuan PR belum tersinkron atau tidak valid", 409)
    ref = need_branch(branch) if branch else base
    issue = gh(f"/repos/{r}/issues/{int(n)}")
    if "pull_request" in issue:
        raise Err("Nomor ini PR, bukan issue", 409)
    tree = git(d, "ls-tree", "-r", "--name-only", f"origin/{ref}").splitlines()
    p = [f"Selesaikan SEPENUHNYA issue #{n} di repository {r}.",
         f"Branch konteks: {ref}; default: {base}.",
         "Merge/QA adalah tahap manual terpisah. Jangan mengaku sudah PASS.",
         "", f"## Issue #{n}: {issue['title']}", issue.get("body") or "(kosong)", "",
         f"## DAFTAR FILE LENGKAP ({len(tree)} file)", "\n".join(tree), ""]
    if branch:
        rng = f"origin/{base}...origin/{ref}"
        p += [f"## Diff utuh {ref} dibanding {base}",
              "```diff", git(d, "diff", rng), "```", ""]
    selected = list(dict.fromkeys(files))
    if len(selected) > 20:
        raise Err("Maksimal 20 file konteks, pilih yang relevan.", 409)
    for file in selected:
        if not isinstance(file, str) or file not in tree:
            raise Err(f"File konteks tidak ditemukan dalam branch {ref}: {str(file)[:160]}", 409)
        p += [f"## ISI UTUH {file}", "```",
              git(d, "show", f"origin/{ref}:{file}"), "```", ""]
    p.append(FORMAT)
    result = "\n".join(p)
    if len(result) > MAX_PROMPT_CHARS:
        raise Err(f"Prompt memerlukan {len(result)} karakter; terlalu besar untuk "
                  "satu permintaan. Pilih lebih sedikit file atau diff branch "
                  "lebih kecil. Jembatan tidak memotong konteks diam-diam.", 409)
    return {"prompt": result, "chars": len(result), "complete_context": True,
            "files_included": selected, "branch": ref}


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


def require_complete_reply(reply):
    """Fail closed on a partial reply, before checking out/creating a branch."""
    if not isinstance(reply, str) or not reply.strip():
        raise Err("Balasan ChatGPT kosong", 409)
    if re.search(r"(?im)^\s*BUTUH FILE\s*:", reply):
        raise Err("ChatGPT masih membutuhkan file. Lengkapi konteks sebelum menerapkan patch.", 409)
    states = re.findall(r"(?im)^STATUS:\s*(SELESAI|SEBAGIAN|BELUM)\s*$", reply)
    if states != ["SELESAI"]:
        raise Err("Balasan belum berstatus SELESAI secara tunggal. "
                  "Tuntaskan seluruh pekerjaan dan kirim satu patch lengkap.", 409)
    section = re.search(r"(?ims)^KEKURANGAN:\s*\n(.*?)(?=^[A-Z][A-Z ]+:|\Z)", reply)
    if not section or not re.fullmatch(r"\s*-\s*tidak ada\s*", section.group(1), re.I):
        raise Err("Masih ada KEKURANGAN; patch sebagian tidak diterapkan.", 409)
    if "```diff" not in reply or not re.search(r"(?ms)^```diff[ \t]*\n.+?\n```", reply):
        raise Err("Blok diff lengkap belum ditemukan atau belum ditutup.", 409)


def apply_patch(r, n, reply, branch=None, target=None):
    d = repo_dir(r)
    base = target or default_branch(d)
    if not docker_control.valid_branch(base) or not git(d, "rev-parse", "--verify", "-q",
                                                      f"refs/remotes/origin/{base}", check=False):
        raise Err("Branch tujuan PR tidak ada pada clone lokal. Sinkron ulang repository.", 409)
    if git(d, "status", "--porcelain"):
        raise Err("Folder repo ini kotor. Jangan edit manual folder kerja bot.", 409)
    require_complete_reply(reply)
    patch = extract_patch(reply)
    # One issue → one stable branch; repeat fixes never generate extra branches.
    stable = f"bridge/issue-{int(n)}"
    if branch and branch != stable:
        raise Err(f"Issue ini harus memakai branch tunggal {stable}", 409)
    branch = stable
    local_exists = bool(git(d, "branch", "--list", branch).strip())
    remote_exists = bool(git(d, "rev-parse", "--verify", "-q",
                             f"refs/remotes/origin/{branch}", check=False))
    created = not local_exists and not remote_exists
    if local_exists:
        git(d, "checkout", "-q", branch)
    elif remote_exists:
        git(d, "checkout", "-q", "-b", branch, f"origin/{branch}")
    else:
        git(d, "checkout", "-q", "--no-track", "-b", branch, f"origin/{base}")
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
    # Legacy TEST_CMD on Jembatan has access to production Docker socket.
    # Never execute arbitrary test commands during patch apply.
    res = {"branch": branch, "commit": git(d, "rev-parse", "--short", "HEAD"),
           "test": None, "qa_required": True}
    return res


def push(r, branch, n, target=None):
    """Push the issue branch and create/reuse a PR targeting operator-selected base."""
    d = repo_dir(r)
    issue_branch = f"bridge/issue-{int(n)}"
    if not docker_control.valid_branch(branch) or branch != issue_branch:
        raise Err("PR hanya boleh memakai branch issue yang tetap", 409)
    base = target or default_branch(d)
    if not docker_control.valid_branch(base) or base == branch:
        raise Err("Branch tujuan PR tidak valid", 409)
    gh(f"/repos/{r}/branches/{urlquote(base, safe='')}")  # must really exist
    git(d, "push", "-u", "origin", branch, net=True, timeout=300)
    try:
        existing = gh_pages(f"/repos/{r}/pulls?state=open&head={r.split('/')[0]}:{branch}", 1)
        for item in existing:
            if item.get("head", {}).get("ref") != branch:
                continue
            pr = item
            if (pr.get("base") or {}).get("ref") != base:
                pr = gh(f"/repos/{r}/pulls/{pr['number']}", "PATCH", {"base": base})
            return {"pushed": True, "pr": pr["html_url"], "number": pr["number"],
                    "base": base, "head_sha": pr["head"]["sha"], "reused": True,
                    "qa_required": True}
        pr = gh(f"/repos/{r}/pulls", "POST", {
            "title": f"Kerjakan #{int(n)}", "head": branch, "base": base,
            # Never auto-close an issue on merge: QA is still the last gate.
            "body": f"Refs #{int(n)}\n\nPatch dari ChatGPT melalui Jembatan. "
                    "Issue tetap menunggu QA manual setelah merge."})
        return {"pushed": True, "pr": pr["html_url"], "number": pr["number"],
                "base": base, "head_sha": pr["head"]["sha"], "reused": False,
                "qa_required": True}
    except Err as exc:
        return {"pushed": True, "pr": None, "error": str(exc)}


def merge_issue_pr(r, issue_number, pr_number, base, expected_sha):
    """An explicit, SHA-locked operator merge; manual QA always follows."""
    repo_dir(r)
    n = int(issue_number)
    pr_number = int(pr_number)
    if n < 1 or pr_number < 1 or not docker_control.valid_branch(base):
        raise Err("PR atau branch tujuan tidak valid", 409)
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", expected_sha):
        raise Err("SHA PR untuk konfirmasi merge tidak valid", 409)
    pr = gh(f"/repos/{r}/pulls/{pr_number}")
    if pr.get("state") != "open" or pr.get("merged_at"):
        raise Err("PR tidak terbuka; merge dibatalkan", 409)
    if (pr.get("head") or {}).get("ref") != f"bridge/issue-{n}":
        raise Err("PR tidak cocok dengan branch issue", 409)
    if (pr.get("base") or {}).get("ref") != base:
        raise Err("Branch tujuan PR berubah. Muat ulang PR sebelum merge.", 409)
    if (pr.get("head") or {}).get("sha") != expected_sha:
        raise Err("SHA PR berubah sesudah persetujuan. Periksa ulang sebelum merge.", 409)
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo and head_repo.lower() != r.lower():
        raise Err("PR berasal dari repository lain", 409)
    if pr.get("mergeable") is False:
        raise Err("PR memiliki konflik dan belum dapat di-merge", 409)
    result = gh(f"/repos/{r}/pulls/{pr_number}/merge", "PUT", {
        "sha": expected_sha, "merge_method": "squash",
        "commit_title": f"Kerjakan #{n} — merge ke {base}"})
    if result.get("merged") is not True:
        raise Err("GitHub tidak mengonfirmasi merge; periksa PR langsung", 502)
    return {"merged": True, "issue": n, "pr_number": pr_number,
            "branch": base, "merge_sha": result.get("sha"),
            "qa_required": True, "next": "Jalankan QA secara manual; issue belum dianggap selesai."}


def issue_pr_status(r, issue_number):
    """Recover an open/merged PR after reloading the issue card."""
    repo_dir(r)
    n = int(issue_number)
    if n < 1:
        raise Err("Nomor issue harus positif", 409)
    branch = f"bridge/issue-{n}"
    values = gh_pages(f"/repos/{r}/pulls?state=all&head={r.split('/')[0]}:{branch}"
                      "&sort=updated&direction=desc", 3)
    prs = [p for p in values if (p.get("head") or {}).get("ref") == branch]
    if not prs:
        return {"found": False, "issue": n}
    pr = next((p for p in prs if p.get("state") == "open"),
              next((p for p in prs if p.get("merged_at")), prs[0]))
    return {"found": True, "issue": n, "number": pr["number"],
            "url": pr.get("html_url"), "base": (pr.get("base") or {}).get("ref"),
            "head_sha": (pr.get("head") or {}).get("sha"),
            "state": pr.get("state"), "merged": bool(pr.get("merged_at"))}


def complete_qa_issue(r, issue_number, confirmed):
    """The operator, not the merge event, explicitly marks manual QA PASS."""
    repo_dir(r)
    n = int(issue_number)
    if n < 1 or confirmed is not True:
        raise Err("Konfirmasi manual QA PASS dibutuhkan", 409)
    issue = gh(f"/repos/{r}/issues/{n}")
    if issue.get("pull_request"):
        raise Err("Nomor yang dipilih adalah PR, bukan issue", 409)
    if issue.get("state") != "closed":
        gh(f"/repos/{r}/issues/{n}/comments", "POST", {
            "body": "QA MANUAL PASS — operator Jembatan menyatakan tes telah selesai. "
                    "Status ini berasal dari konfirmasi pengguna, bukan klaim QA otomatis."})
        issue = gh(f"/repos/{r}/issues/{n}", "PATCH", {"state": "closed"})
    return {"issue": n, "state": issue.get("state"), "url": issue.get("html_url"),
            "message": "Issue ditutup setelah konfirmasi QA PASS manual."}


# ---------- QA manual / automated failure feedback to one GitHub issue ----------
QA_TRACKER = "<!-- jembatan:qa-tracker -->"


def qa_followup_prompt(r, issue, log):
    title = issue.get("title") or "Kegagalan QA"
    body = issue.get("body") or "(kosong)"
    payload = [
        "SELESAIKAN SEMUA masalah, jangan kirim solusi sebagian.",
        f"Repository: {r}. Issue #{issue['number']}: {title}",
        "Tindaklanjuti keseluruhan issue dan error QA berikut sampai selesai.",
        "Jangan menyatakan PASS atau mengarang hasil uji yang tidak dijalankan.",
        "Jika butuh source tambahan: hanya balas BUTUH FILE: path1, path2.",
        "", "## Isi issue lengkap", body,
        "", "## Log QA (disanitasi; bagian terakhir bila log panjang)",
        "~~~~text", log, "~~~~", "", FORMAT,
    ]
    prompt = "\n".join(payload)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise Err("Prompt tindak lanjut QA terlalu besar; pecah laporan issue.", 409)
    return prompt


def report_qa_failure(r, issue_number=None, log="", branch=None):
    """Idempotent tracker-or-linked-issue reporting; invoked only after user QA."""
    repo_dir(r)
    if not isinstance(log, str) or not log.strip():
        raise Err("Log QA gagal wajib diisi sebelum membuat laporan", 409)
    if len(log) > 65536:
        raise Err("Log QA terlalu panjang (>64 KB); kirim cuplikan yang relevan.", 409)
    if branch and not docker_control.valid_branch(branch):
        raise Err("Branch konteks QA tidak valid", 409)
    safe_log = qa_jobs._sanitize(log.strip(), sys.modules[__name__])
    clipped = len(safe_log) > 18000
    shown_log = safe_log[-18000:] if clipped else safe_log
    if clipped:
        shown_log = "[Jembatan] Log panjang: 18.000 karakter terakhir disertakan.\n" + shown_log
    digest = hashlib.sha256((r + "\n" + safe_log).encode("utf-8")).hexdigest()[:20]
    fingerprint = f"<!-- jembatan:qa-failure:{digest} -->"
    issue = None
    if issue_number not in (None, ""):
        try:
            number = int(issue_number)
        except (TypeError, ValueError) as exc:
            raise Err("Nomor issue QA tidak valid", 409) from exc
        if number < 1:
            raise Err("Nomor issue QA harus positif", 409)
        issue = gh(f"/repos/{r}/issues/{number}")
        if issue.get("pull_request"):
            raise Err("Nomor tersebut adalah PR, bukan issue QA", 409)
    else:
        candidates = gh_pages(f"/repos/{r}/issues?state=all&sort=updated", 5)
        issue = next((i for i in candidates if not i.get("pull_request") and
                      QA_TRACKER in (i.get("body") or "")), None)
    detail = (f"QA gagal untuk {r}" +
              (f" (branch {branch})" if branch else "") +
              f".\n\n{fingerprint}\n\n~~~~text\n{shown_log}\n~~~~")
    created = False
    reopened = False
    if issue is None:
        issue = gh(f"/repos/{r}/issues", "POST", {
            "title": f"QA gagal — {r}",
            "body": QA_TRACKER + "\n\n" + detail +
                    "\n\nIssue ini dipakai ulang untuk hasil QA berikutnya.",
        })
        created = True
    elif issue.get("state") == "closed":
        issue = gh(f"/repos/{r}/issues/{issue['number']}", "PATCH", {"state": "open"})
        reopened = True
    if not created:
        # Avoid repeated comments for an unchanged failure on a rerun.
        comments = gh_pages(f"/repos/{r}/issues/{issue['number']}/comments", 5)
        if not any(fingerprint in (c.get("body") or "") for c in comments):
            gh(f"/repos/{r}/issues/{issue['number']}/comments", "POST", {
                "body": "QA manual/terakhir masih gagal:\n\n" + detail})
    prompt = qa_followup_prompt(r, issue, shown_log)
    return {"issue": issue["number"], "url": issue.get("html_url", ""),
            "created": created, "reopened": reopened,
            "prompt": prompt, "chars": len(prompt),
            "message": "Issue QA dibuat/dibuka ulang; salin prompt lengkap untuk ChatGPT."}


# ---------- Docker: explicit mapping, fixed QA commands, operator-approved deploy ----------
def docker_action(func, *args):
    try:
        return func(sys.modules[__name__], *args)
    except docker_control.DockerError as e:
        raise Err(str(e), e.code) from e

def deploy_branch_options(r):
    """Show actual GitHub branches, not a hardcoded main/staging pair."""
    repo_dir(r)
    meta = gh(f"/repos/{r}")
    branches = gh_pages(f"/repos/{r}/branches", limit=5)
    valid = {item.get("name") for item in branches if isinstance(item, dict)
             and docker_control.valid_branch(item.get("name"))}
    default = meta.get("default_branch", "")
    available = sorted(valid, key=lambda name: (name != default, name.lower()))
    current = docker_action(docker_control._app_settings, r)["deploy_branch"]
    return {"repo": r, "available": available, "configured": current,
            "configured_exists": current in valid,
            "default_branch": default, "truncated": len(branches) >= 500}


def save_docker_app(body):
    if not isinstance(body, dict):
        raise Err("Pengaturan aplikasi tidak valid", 400)
    r = body.get("repo")
    repo_dir(r)
    branch = body.get("deploy_branch", "main")
    if not docker_control.valid_branch(branch):
        raise Err("Branch deploy tidak valid", 409)
    mode = body.get("qa_branch_mode", "active")
    if mode not in ("active", "default"):
        raise Err("Mode branch QA harus aktif atau default", 409)
    options = deploy_branch_options(r)
    if branch not in options["available"]:
        names = ", ".join(options["available"][:12]) or "belum ada"
        raise Err(f"Branch '{branch}' tidak ada pada {r}. Pilih branch tersedia: {names}.", 409)
    return docker_action(docker_control.save_app, body)


def deployment_target(r):
    settings = docker_action(docker_control._app_settings, r)
    branch = settings.get("deploy_branch", "main")
    if not docker_control.valid_branch(branch):
        raise Err("Branch target deployment tidak valid", 409)
    try:
        remote = gh(f"/repos/{r}/branches/{urlquote(branch, safe='')}")
    except Err as error:
        if "Branch not found" in str(error) and "GitHub 404:" in str(error):
            raise Err(f"Branch '{branch}' tidak ada di GitHub untuk {r}. Pilih branch yang tersedia di Docker & QA, lalu klik Simpan aplikasi.", 409) from error
        raise
    sha = (remote.get("commit") or {}).get("sha", "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", sha):
        raise Err("GitHub belum memberikan SHA commit yang valid", 502)
    return branch, sha, settings


def deploy_readiness(r):
    try:
        branch, sha, settings = deployment_target(r)
    except Err as error:
        if error.code != 409:
            raise
        settings = docker_action(docker_control._app_settings, r)
        return {"repo": r, "branch": settings.get("deploy_branch", "main"), "sha": None,
                "qa_ready": False, "qa_job_id": None,
                "deploy_path": settings["deploy_path"], "reason": str(error)}
    if not settings["deploy_path"]:
        return {"repo": r, "branch": branch, "sha": sha,
                "qa_ready": False, "qa_job_id": None, "deploy_path": "",
                "reason": "Path Compose aplikasi belum disimpan di Settings Docker & QA."}
    try:
        proof = docker_action(qa_jobs.require_verified, r, branch, sha)
        return {"repo": r, "branch": branch, "sha": sha,
                "qa_ready": True, "qa_job_id": proof["qa_job_id"],
                "deploy_path": settings["deploy_path"], "qa_branch": proof.get("qa_branch"),
                "reason": ("QA PASS pada branch " + str(proof.get("qa_branch") or "QA") +
                           "; commit identik dengan target deploy " + branch + ".")}
    except Err as error:
        if error.code != 409:
            raise
        return {"repo": r, "branch": branch, "sha": sha,
                "qa_ready": False, "qa_job_id": None,
                "deploy_path": settings["deploy_path"], "reason": str(error)}


def deploy(r, confirm):
    if qa_jobs.active():
        raise Err("Deployment ditunda: QA masih berjalan. Tunggu hasil QA selesai.", 409)
    if confirm != r:
        raise Err("Konfirmasi deploy harus nama repository persis", 409)
    branch, sha, _ = deployment_target(r)
    docker_action(qa_jobs.require_verified, r, branch, sha)
    return docker_action(docker_control.deploy, r, confirm, branch, sha)

def legacy_qa(r):
    if qa_jobs.active():
        raise Err("QA lain masih berjalan; lihat log verbose sampai selesai", 409)
    return docker_action(docker_control.run_qa, r)


# ---------- HTTP ----------
ROUTES = {
    ("GET", "/api/repos"): lambda q, b: list_repos(),
    ("POST", "/api/sync"): lambda q, b: sync(b["repos"]),
    ("GET", "/api/overview"): lambda q, b: overview(q["repo"]),
    ("GET", "/api/branches"): lambda q, b: branch_manager.inventory(sys.modules[__name__], q["repo"]),
    ("POST", "/api/branches/delete"): lambda q, b: branch_manager.delete_remote(
        sys.modules[__name__], b["repo"], b["branch"], b["sha"]),
    ("POST", "/api/branches/cleanup"): lambda q, b: branch_manager.cleanup_remote(
        sys.modules[__name__], b["repo"], b["confirm"]),
    ("POST", "/api/prompt"): lambda q, b: build_prompt(b["repo"], int(b["issue"]), b.get("branch") or None,
                                                       b.get("files") or [], b.get("target")),
    ("POST", "/api/apply"): lambda q, b: apply_patch(b["repo"], int(b["issue"]), b["reply"],
                                                     b.get("branch") or None, b.get("target")),
    ("POST", "/api/push"): lambda q, b: push(b["repo"], b["branch"], int(b["issue"]), b.get("target")),
    ("POST", "/api/pr/merge"): lambda q, b: merge_issue_pr(
        b["repo"], b["issue"], b["pr_number"], b["base"], b["head_sha"]),
    ("GET", "/api/pr/status"): lambda q, b: issue_pr_status(q["repo"], q["issue"]),
    ("POST", "/api/qa/pass"): lambda q, b: complete_qa_issue(
        b["repo"], b["issue"], b.get("confirmed")),
    ("POST", "/api/qa/failure"): lambda q, b: report_qa_failure(
        b["repo"], b.get("issue"), b["log"], b.get("branch")),
    ("GET", "/api/docker/settings"): lambda q, b: docker_action(
        docker_control.public_settings, q.get("repo")),
    ("POST", "/api/docker/settings"): lambda q, b: docker_action(
        docker_control.save_connection, b),
    ("POST", "/api/docker/app"): lambda q, b: save_docker_app(b),
    ("GET", "/api/deploy/branches"): lambda q, b: deploy_branch_options(q["repo"]),
    ("GET", "/api/docker/connection"): lambda q, b: docker_action(
        docker_control.connection_test),
    ("GET", "/api/docker/containers"): lambda q, b: docker_action(
        docker_control.containers),
    ("POST", "/api/docker/qa"): lambda q, b: legacy_qa(b["repo"]),
    ("POST", "/api/docker/qa/start"): lambda q, b: docker_action(
        qa_jobs.start, b["repo"], b.get("issue"), b.get("report_on_fail", False)),
    ("GET", "/api/docker/qa/status"): lambda q, b: docker_action(
        qa_jobs.status, q["repo"]),
    ("GET", "/api/deploy/readiness"): lambda q, b: deploy_readiness(q["repo"]),
    ("POST", "/api/deploy"): lambda q, b: deploy(b["repo"], b["confirm"]),
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
        # Only serve explicit packaged brand assets; never expose arbitrary files.
        brand_assets = {
            "/assets/jembatan-mark.svg": "jembatan-mark.svg",
            "/assets/jembatan-lockup.svg": "jembatan-lockup.svg",
        }
        if method == "GET" and u.path in brand_assets:
            return self.reply(200, (HERE / "assets" / brand_assets[u.path]).read_bytes(),
                              "image/svg+xml; charset=utf-8")
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
