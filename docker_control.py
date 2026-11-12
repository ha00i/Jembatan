"""Small, explicit Docker control for Jembatan.

No arbitrary commands from the browser. No automatic deployment. QA may target
only a configured QA-named container; never an unknown production container.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import selectors
import signal
import time

QA_PROFILES = {
    "laravel": ("Laravel (php artisan test)", ["php", "artisan", "test", "--no-ansi"]),
    "python": ("Python (unittest discover)", ["python", "-m", "unittest", "discover"]),
    "node": ("Node (npm test)", ["npm", "test"]),
    "rust": ("Rust (cargo test --locked)", ["cargo", "test", "--locked"]),
}
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
HOST_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
USER_RE = re.compile(r"^[a-z_][a-zA-Z0-9_.-]{0,63}$")
REPO_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
QA_NAME_RE = re.compile(r"(^|[-_])qa([-_]|$)", re.I)


class DockerError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _base():
    return {"mode": "local", "host": "", "username": "", "port": 22, "apps": {}}


def _cfg_path(app):
    return app.WORKDIR / ".docker-control.json"


def load(app):
    path = _cfg_path(app)
    if not path.exists():
        return _base()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DockerError("Konfigurasi Docker tidak bisa dibaca", 500) from exc
    if not isinstance(data, dict):
        raise DockerError("Format konfigurasi Docker tidak valid", 500)
    # no secret fields are ever returned/saved
    result = _base()
    result.update({k: data[k] for k in ("mode", "host", "username", "port", "apps") if k in data})
    if not isinstance(result["apps"], dict):
        raise DockerError("Daftar aplikasi Docker tidak valid", 500)
    return result


def _atomic_config(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".docker-settings-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _validate_connection(mode, host, username, port):
    if mode not in ("local", "ssh"):
        raise DockerError("Metode Docker harus local atau ssh")
    if mode == "ssh":
        if not isinstance(host, str) or not HOST_RE.fullmatch(host) or ".." in host:
            raise DockerError("Hostname/IP SSH tidak valid")
        if not isinstance(username, str) or not USER_RE.fullmatch(username) or username == "root":
            raise DockerError("Username SSH tidak valid (root tidak diizinkan)")
        if type(port) is not int or not (1 <= port <= 65535):
            raise DockerError("Port SSH harus 1–65535")
    return {"mode": mode, "host": host if mode == "ssh" else "",
            "username": username if mode == "ssh" else "", "port": port if mode == "ssh" else 22}


def save_connection(app, body):
    if not isinstance(body, dict):
        raise DockerError("Input tidak valid")
    old = load(app)
    new = _validate_connection(body.get("mode"), body.get("host", ""),
                               body.get("username", ""), body.get("port", 22))
    old.update(new)
    _atomic_config(_cfg_path(app), old)
    return public_settings(app)


def _app_settings(app, repo):
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise DockerError("Repository tidak valid")
    cfg = load(app)
    current = cfg["apps"].get(repo, {})
    if not isinstance(current, dict):
        current = {}
    return {
        "repo": repo,
        "qa_container": current.get("qa_container", ""),
        "qa_profile": current.get("qa_profile", ""),
        "deploy_path": current.get("deploy_path", app.DEPLOY_MAP.get(repo, "")),
        "deploy_branch": current.get("deploy_branch", "main"),
    }


def public_settings(app, repo=None):
    cfg = load(app)
    result = {k: cfg[k] for k in ("mode", "host", "username", "port")}
    result["profiles"] = [{"id": k, "label": v[0]} for k, v in QA_PROFILES.items()]
    if repo:
        result["app"] = _app_settings(app, repo)
    return result


def save_app(app, body):
    if not isinstance(body, dict):
        raise DockerError("Input tidak valid")
    repo = body.get("repo", "")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise DockerError("Repository tidak valid")
    container = body.get("qa_container", "")
    profile = body.get("qa_profile", "")
    deploy_path = body.get("deploy_path", "")
    deploy_branch = body.get("deploy_branch", "main")
    if deploy_branch not in ("main", "staging"):
        raise DockerError("Branch deploy hanya boleh main atau staging")
    if not isinstance(container, str) or (container and not NAME_RE.fullmatch(container)):
        raise DockerError("Nama container QA tidak valid")
    if container and not QA_NAME_RE.search(container):
        raise DockerError("Pilih container QA khusus (namanya mengandung 'qa'), bukan production")
    if profile and profile not in QA_PROFILES:
        raise DockerError("Profil QA tidak tersedia")
    if not isinstance(profile, str) or (container and not profile) or (profile and not container):
        raise DockerError("Profil QA dan nama container wajib diisi bersamaan")
    if not isinstance(deploy_path, str) or (deploy_path and
            (not deploy_path.startswith("/") or "\x00" in deploy_path or "\n" in deploy_path)):
        raise DockerError("Path deploy harus absolut, misalnya /srv/apps/app")
    if deploy_path and (".." in Path(deploy_path).parts or len(deploy_path) > 400 or
                        len(Path(deploy_path).parts) < 3):
        raise DockerError("Path deploy tidak valid")
    cfg = load(app)
    cfg["apps"][repo] = {"qa_container": container, "qa_profile": profile,
                         "deploy_path": deploy_path, "deploy_branch": deploy_branch}
    _atomic_config(_cfg_path(app), cfg)
    return _app_settings(app, repo)


def _docker_base(app):
    cfg = load(app)
    mode = cfg["mode"]
    if mode == "local":
        return ["docker", "--host", "unix:///var/run/docker.sock"]
    _validate_connection(mode, cfg["host"], cfg["username"], cfg["port"])
    return ["docker", "--host", f"ssh://{cfg['username']}@{cfg['host']}:{cfg['port']}"]


def _command(args, timeout=20, cwd=None, on_chunk=None):
    """Bound memory for subprocess output and kill the whole QA process group on timeout."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("DOCKER_HOST", "DOCKER_CONTEXT", "GITHUB_TOKEN", "ADMIN_TOKEN", "TEST_CMD")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    process = None
    output = b""
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   cwd=cwd, env=env, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                ready = selector.select(remaining)
                if not ready:
                    raise subprocess.TimeoutExpired(args, timeout)
                for key, _ in ready:
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output = (output + chunk)[-65536:]
                        if on_chunk:
                            on_chunk(chunk.decode("utf-8", errors="replace"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout)
        code = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise DockerError("Operasi Docker melewati batas waktu", 504)
    except OSError as e:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise DockerError(f"Perintah Docker tidak tersedia: {type(e).__name__}", 503)
    except BaseException:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    out = output.decode("utf-8", errors="replace").strip()
    if code:
        if code == 255:
            raise DockerError("Koneksi SSH gagal. Periksa username, SSH key dan known_hosts.", 502)
        raise DockerError("Docker gagal: " + out[-1000:], 502)
    return out


def _docker(app, *args, timeout=20, cwd=None, on_chunk=None):
    if on_chunk is None:
        return _command([*_docker_base(app), *args], timeout=timeout, cwd=cwd)
    return _command([*_docker_base(app), *args], timeout=timeout, cwd=cwd, on_chunk=on_chunk)


def connection_test(app):
    output = _docker(app, "info", "--format",
                     "{{.ServerVersion}}|{{.CgroupVersion}}|{{.Name}}", timeout=12)
    version, cgroup, name = (output.split("|", 2) + ["", "", ""])[:3]
    return {"connected": True, "engine_version": version,
            "cgroup_version": cgroup, "server": name}


def containers(app):
    data = _docker(app, "ps", "--format", "{{json .}}", timeout=20)
    result = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
            result.append({"name": str(row.get("Names") or ""),
                           "state": str(row.get("State") or ""),
                           "status": str(row.get("Status") or "")})
        except (ValueError, TypeError):
            continue
    return {"containers": result[:150]}


def run_qa(app, repo, on_event=None):
    mapping = _app_settings(app, repo)
    name, profile = mapping["qa_container"], mapping["qa_profile"]
    if not name or profile not in QA_PROFILES or not QA_NAME_RE.search(name):
        raise DockerError("Atur container QA khusus dan profil tes dahulu", 409)
    # A name alone is not proof of isolation; do not use privileged/host-network targets.
    if on_event:
        on_event("stage", "Memeriksa keamanan container QA")
    proof = _docker(app, "inspect", "--format",
                    "{{.State.Running}}|{{.HostConfig.Privileged}}|{{.HostConfig.NetworkMode}}",
                    name, timeout=12).split("|")
    if len(proof) != 3 or proof[0] != "true" or proof[1] != "false" or proof[2] == "host":
        raise DockerError("Container QA tidak running atau konfigurasi isolasinya tidak memenuhi syarat", 409)
    # No input-defined shell/argv: fixed profile prevents remote command injection.
    command = QA_PROFILES[profile][1]
    if on_event:
        on_event("stage", "Menjalankan " + QA_PROFILES[profile][0])
        on_event("output", "[Jembatan] Container: " + name + "\n[Jembatan] Profil: " + profile + "\n[Jembatan] Perintah: " + " ".join(command) + "\n\n")
    source_sha = None
    source_clean = False
    source_reason = "Bukti SHA tidak tersedia pada QA sinkron lama"
    if on_event:
        # We do not pretend a test proves which commit ran if the QA checkout
        # lacks a Git HEAD or has modified tracked source files.
        try:
            source_sha = _docker(app, "exec", name, "git", "rev-parse", "--verify", "HEAD", timeout=12).strip()
            dirty = _docker(app, "exec", name, "git", "status", "--porcelain",
                            "--untracked-files=no", timeout=12).strip()
            source_clean = bool(re.fullmatch(r"[a-fA-F0-9]{40}", source_sha)) and not dirty
            source_reason = ("Git HEAD dan tracked files QA valid" if source_clean else
                             "Checkout QA tidak bersih atau SHA Git tidak valid")
        except DockerError:
            source_reason = "Container QA tidak memiliki Git checkout yang dapat diverifikasi"
        on_event("output", "[Jembatan] Bukti sumber QA: " + source_reason + "\\n")
    try:
        if on_event:
            out = _docker(app, "exec", name, *command, timeout=300,
                          on_chunk=lambda text: on_event("output", text))
        else:
            out = _docker(app, "exec", name, *command, timeout=300)
        if on_event and source_clean:
            try:
                after_sha = _docker(app, "exec", name, "git", "rev-parse", "--verify", "HEAD", timeout=12).strip()
                after_dirty = _docker(app, "exec", name, "git", "status", "--porcelain",
                                      "--untracked-files=no", timeout=12).strip()
                source_clean = after_sha == source_sha and not after_dirty
                if not source_clean:
                    source_reason = "Git SHA/tracked files berubah selama tes"
            except DockerError:
                source_clean = False
                source_reason = "Git checkout QA tidak dapat diperiksa sesudah tes"
        return {"repo": repo, "container": name, "profile": profile,
                "ok": True, "output": out[-8000:],
                "source_sha": source_sha if source_clean else None,
                "source_verified": source_clean, "source_reason": source_reason}
    except DockerError as e:
        if e.code not in (502, 504):
            raise
        return {"repo": repo, "container": name, "profile": profile,
                "ok": False, "output": str(e)[-3000:]}


def _deploy_dir(app, repo):
    item = _app_settings(app, repo)
    path = item["deploy_path"]
    if not path or not Path(path).is_absolute() or ".." in Path(path).parts:
        raise DockerError("Path deploy belum diatur untuk repository ini", 409)
    return path


def deploy(app, repo, confirm):
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo) or confirm != repo:
        raise DockerError("Konfirmasi deploy harus nama repository persis", 409)
    path = _deploy_dir(app, repo)
    cfg = load(app)
    if cfg["mode"] == "local":
        directory = Path(path)
        if not directory.is_dir() or not (directory / ".git").exists():
            raise DockerError("Folder deploy tidak tersedia di container Jembatan. Mount folder ini terlebih dahulu.", 409)
        if not any((directory / name).is_file() for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")):
            raise DockerError("Compose file tidak ditemukan pada folder deploy", 409)
        # Prevent accidentally deploying a different repository through a mapped path.
        origin = _command(["git", "remote", "get-url", "origin"], timeout=12, cwd=directory).strip()
        normalized = origin.rstrip("/").removesuffix(".git")
        allowed_origins = {f"https://github.com/{repo}", f"git@github.com:{repo}",
                           f"ssh://git@github.com/{repo}"}
        if normalized not in allowed_origins:
            raise DockerError("Remote Git pada folder deploy tidak cocok dengan repository terpilih", 409)
        dirty = _command(["git", "status", "--porcelain"], timeout=12, cwd=directory)
        if dirty:
            raise DockerError("Folder deploy memiliki perubahan lokal; periksa dahulu sebelum deploy", 409)
        # no fallback to arbitrary source/branch; deploy path explicitly configured
        _command(["git", "pull", "--ff-only"], timeout=180, cwd=directory)
        output = _docker(app, "compose", "up", "-d", "--build", timeout=1200, cwd=directory)
        status = _docker(app, "compose", "ps", timeout=30, cwd=directory)
    else:
        _validate_connection(cfg["mode"], cfg["host"], cfg["username"], cfg["port"])
        import shlex
        # execute only fixed allowlisted deployment command, with quoted absolute path
        quoted = shlex.quote(path)
        script = f"cd -- {quoted} && git pull --ff-only && docker compose up -d --build && docker compose ps"
        output = _command(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                           "-p", str(cfg["port"]), f"{cfg['username']}@{cfg['host']}",
                           "sh -lc " + shlex.quote(script)], timeout=1200)
        status = "Status tercantum pada output deploy remote."
    return {"repo": repo, "path": path, "ok": True, "output": output[-8000:],
            "status": status[-4000:]}
