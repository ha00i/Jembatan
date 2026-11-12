"""Single-slot, in-memory QA jobs with real Docker output and bounded logs.

No shell text comes from the user. The existing docker_control.run_qa enforces
QA container and preset validation. Jobs survive browser reloads, not server restarts.
"""
import re
import threading
import time
import uuid

import docker_control

MAX_LOG = 64000
_JOBS = {}
_ACTIVE = None
_LOCK = threading.Lock()


def _sanitize(text, app):
    # Redact control-plane credentials even when a QA program echoes them.
    value = str(text)
    for key in ("GH_TOKEN", "ADMIN"):
        secret = getattr(app, key, None)
        if isinstance(secret, str) and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+)", "[REDACTED_TOKEN]", value)
    value = re.sub(r"(?im)((?:password|secret|api[_-]?key|token)\s*[:=]\s*)\S+", r"\1[REDACTED]", value)
    return value


def _append(job, data):
    # Raw output is kept bounded and never returned without redaction.
    with _LOCK:
        job["log"] = (job["log"] + str(data))[-MAX_LOG:]
        job["updated_at"] = time.time()


def _public(job, app):
    if not job:
        return {"job": None}
    now = time.time()
    # Values are copied under _LOCK by caller.
    return {"job": {
        "id": job["id"], "repo": job["repo"], "container": job["container"],
        "profile": job["profile"], "state": job["state"], "stage": job["stage"],
        "started_at": job["started_at"], "updated_at": job["updated_at"],
        "elapsed_seconds": int(max(0, (job["finished_at"] or now) - job["started_at"])),
        "exit_code": job["exit_code"], "ok": job["ok"],
        "log": _sanitize(job["log"], app), "truncated": job["truncated"],
    }}


def _runner(app, job):
    global _ACTIVE
    def report(data):
        with _LOCK:
            if len(job["log"]) + len(str(data)) > MAX_LOG:
                job["truncated"] = True
        _append(job, data)

    try:
        with _LOCK:
            job["state"] = "running"
            job["stage"] = "Memeriksa container QA"
            job["updated_at"] = time.time()
        report("[Jembatan] Memeriksa container QA dan profil tes.\n")
        def on_event(kind, text):
            if kind == "stage":
                with _LOCK:
                    job["stage"] = str(text)[:100]
                    job["updated_at"] = time.time()
            elif kind == "output":
                report(text)
        result = docker_control.run_qa(app, job["repo"], on_event=on_event)
        with _LOCK:
            job["ok"] = result["ok"]
            job["exit_code"] = 0 if result["ok"] else None
            job["state"] = "passed" if result["ok"] else "failed"
            job["stage"] = "Tes selesai" if result["ok"] else "Tes gagal"
        report("\n[Jembatan] " + ("PASS" if result["ok"] else "FAIL") + " — periksa hasil tes di atas.\n")
    except Exception as exc:
        # Error strings from the Docker controller can contain container output.
        report("\n[Jembatan] ERROR: " + str(exc)[-1200:] + "\n")
        with _LOCK:
            job["state"] = "failed"
            job["stage"] = "Gagal menjalankan QA"
            job["ok"] = False
            job["exit_code"] = None
    finally:
        with _LOCK:
            job["finished_at"] = time.time()
            job["updated_at"] = job["finished_at"]
            if _ACTIVE == job["id"]:
                _ACTIVE = None


def start(app, repo):
    global _ACTIVE
    # Validate repo/profile before reserving the singleton worker slot.
    mapping = docker_control._app_settings(app, repo)
    if not mapping["qa_container"] or mapping["qa_profile"] not in docker_control.QA_PROFILES or not docker_control.QA_NAME_RE.search(mapping["qa_container"]):
        raise docker_control.DockerError("Atur container QA dan profil tes valid terlebih dahulu", 409)
    with _LOCK:
        if _ACTIVE:
            busy = next((j for j in _JOBS.values() if j["id"] == _ACTIVE), None)
            raise docker_control.DockerError("QA masih berjalan" + (f" untuk {busy['repo']}" if busy else "") + ". Tunggu hingga selesai.", 409)
        identifier = uuid.uuid4().hex
        now = time.time()
        job = dict(id=identifier, repo=repo, container=mapping["qa_container"],
                   profile=mapping["qa_profile"], state="queued", stage="Menyiapkan QA",
                   started_at=now, updated_at=now, finished_at=None,
                   ok=None, exit_code=None, log="", truncated=False)
        # Keep only the last job per repo; bound memory across repositories.
        _JOBS.pop(repo, None)
        _JOBS[repo] = job
        if len(_JOBS) > 16:
            oldest = next(iter(_JOBS))
            if oldest != repo:
                _JOBS.pop(oldest)
        _ACTIVE = identifier
    threading.Thread(target=_runner, args=(app, job), daemon=True, name="jembatan-qa").start()
    return {"job_id": identifier, "state": "queued", "repo": repo}


def status(app, repo):
    if not isinstance(repo, str) or not docker_control.REPO_RE.fullmatch(repo):
        raise docker_control.DockerError("Repository tidak valid", 400)
    with _LOCK:
        job = _JOBS.get(repo)
        return _public(dict(job) if job else None, app)


def active():
    with _LOCK:
        return _ACTIVE is not None
