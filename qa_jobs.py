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
    value = re.sub(r"(?im)(Authorization\s*:\s*(?:Bearer|Basic)\s+)\S+",
                   r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    value = re.sub(r"(?im)((?:password|secret|api[_-]?key|token)\s*[:=]\s*)\S+",
                   r"\1[REDACTED]", value)
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
        "qa_branch_mode": job.get("qa_branch_mode", "active"),
        "qa_branch": job.get("qa_branch"),
        "target_branch": job.get("qa_branch"),
        "source_sha": job.get("source_sha"), "qa_verified": job.get("qa_verified", False),
        "verify_reason": job.get("verify_reason", "Belum ada bukti SHA"),
        "issue_number": job.get("issue_number"),
        "post_merge_branch": job.get("post_merge_branch"),
        "merged_pr_number": job.get("merged_pr_number"),
        "report_issue_url": job.get("report_issue_url"),
        "followup_prompt": job.get("followup_prompt"),
        "report_error": job.get("report_error"),
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
        mode = job["qa_branch_mode"]
        required_branch = None
        if mode == "default":
            meta = app.gh(f"/repos/{job['repo']}")
            required_branch = meta.get("default_branch")
            if not docker_control.valid_branch(required_branch):
                raise docker_control.DockerError("Default branch GitHub tidak valid", 409)
        report("[Jembatan] Sumber QA: " + mode +
               ((" (" + required_branch + ")") if required_branch else " (branch checkout aktif)") + "\n")
        result = docker_control.run_qa(app, job["repo"], on_event=on_event,
                                       branch_mode=mode, required_branch=required_branch)
        source_branch = result.get("source_branch")
        source_sha = result.get("source_sha")
        branch_matches_mode = (docker_control.valid_branch(source_branch) and
                               (mode == "active" or source_branch == required_branch))
        expected_sha = None
        source_reason = result.get("source_reason") or "Bukti checkout QA tidak tersedia"
        if branch_matches_mode:
            try:
                from urllib.parse import quote
                remote = app.gh(f"/repos/{job['repo']}/branches/{quote(source_branch, safe='')}")
                expected_sha = (remote.get("commit") or {}).get("sha")
                if not isinstance(expected_sha, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", expected_sha):
                    expected_sha = None
            except Exception:
                expected_sha = None
        post_merge_branch = job.get("post_merge_branch")
        correct_pr_branch = (not post_merge_branch or source_branch == post_merge_branch)
        verified = bool(result["ok"] and expected_sha and branch_matches_mode and
                        correct_pr_branch and result.get("source_verified") and
                        source_sha == expected_sha)
        if verified:
            reason = "QA " + source_branch + " PASS pada SHA GitHub " + source_sha[:12]
        elif result["ok"] and post_merge_branch and not correct_pr_branch:
            reason = ("QA harus dijalankan pada branch hasil merge PR: " +
                      post_merge_branch + ", bukan " + str(source_branch))
        elif result["ok"] and expected_sha and source_sha and source_sha != expected_sha:
            reason = "SHA checkout QA tidak cocok dengan branch GitHub " + source_branch
        elif result["ok"] and not expected_sha and branch_matches_mode:
            reason = "SHA branch QA tidak dapat diverifikasi terhadap GitHub"
        else:
            reason = source_reason
        report(f"[Jembatan] Branch QA: {source_branch or 'belum diketahui'}" +
               (f" · GitHub SHA {expected_sha[:12]}" if expected_sha else "") + "\n")
        # For issue-linked final QA, a green test on the wrong or unverifiable
        # commit is not a passed QA gate. Unlinked QA keeps the test result and
        # separately reports whether SHA evidence was verified.
        passed = bool(result["ok"] and (verified if job.get("issue_number") else True))
        with _LOCK:
            job["source_sha"] = source_sha
            job["qa_branch"] = source_branch if branch_matches_mode else None
            job["qa_verified"] = verified
            job["verify_reason"] = reason
            job["ok"] = passed
            job["exit_code"] = 0 if passed else None
            job["state"] = ("passed" if passed else
                            "running" if job.get("report_on_fail") else "failed")
            job["stage"] = "Tes dan SHA terverifikasi" if passed and job.get("issue_number") else (
                "Tes selesai" if passed else "Tes/gate SHA gagal")
        report("\n[Jembatan] " + ("QA FINAL PASS" if passed and job.get("issue_number") else
               "QA COMMAND PASS" if passed else "QA FINAL FAIL") + " — periksa hasil tes di atas.\n")
        report("[Jembatan] Bukti commit: " + reason + "\n")
    except Exception as exc:
        # Error strings from the Docker controller can contain container output.
        report("\n[Jembatan] ERROR: " + str(exc)[-1200:] + "\n")
        with _LOCK:
            job["state"] = "running" if job.get("report_on_fail") else "failed"
            job["stage"] = "Gagal menjalankan QA"
            job["ok"] = False
            job["exit_code"] = None
    finally:
        if job.get("ok") is False and job.get("report_on_fail"):
            with _LOCK:
                job["state"] = "running"
                job["stage"] = "Melaporkan QA gagal ke GitHub"
                job["updated_at"] = time.time()
            try:
                result = app.report_qa_failure(
                    job["repo"], job.get("issue_number"), job["log"],
                    job.get("qa_branch"))
                with _LOCK:
                    job["issue_number"] = result["issue"]
                    job["report_issue_url"] = result["url"]
                    job["followup_prompt"] = result["prompt"]
                report("\n[Jembatan] Issue QA: " + result["url"] + "\n")
            except Exception as exc:
                with _LOCK:
                    job["report_error"] = str(exc)[:500]
                report("\n[Jembatan] Pelaporan issue gagal: " + str(exc)[:300] + "\n")
            with _LOCK:
                job["state"] = "failed"
                job["stage"] = "QA gagal"
        with _LOCK:
            job["finished_at"] = time.time()
            job["updated_at"] = job["finished_at"]
            if _ACTIVE == job["id"]:
                _ACTIVE = None


def start(app, repo, issue_number=None, report_on_fail=False):
    global _ACTIVE
    # Validate repo/profile before reserving the singleton worker slot.
    mapping = docker_control._app_settings(app, repo)
    if not mapping["qa_container"] or mapping["qa_profile"] not in docker_control.QA_PROFILES or not docker_control.QA_NAME_RE.search(mapping["qa_container"]):
        raise docker_control.DockerError("Atur container QA dan profil tes valid terlebih dahulu", 409)
    if type(report_on_fail) is not bool:
        raise docker_control.DockerError("Pengaturan laporan QA harus boolean", 400)
    if issue_number not in (None, ""):
        try:
            issue_number = int(issue_number)
        except (TypeError, ValueError) as exc:
            raise docker_control.DockerError("Nomor issue QA tidak valid", 409) from exc
        if issue_number < 1:
            raise docker_control.DockerError("Nomor issue QA harus positif", 409)
        issue = app.gh(f"/repos/{repo}/issues/{issue_number}")
        if issue.get("pull_request"):
            raise docker_control.DockerError("Nomor issue QA mengarah ke PR", 409)
        if not callable(getattr(app, "issue_pr_status", None)):
            raise docker_control.DockerError("Pemeriksaan PR untuk QA tidak tersedia", 409)
        pr = app.issue_pr_status(repo, issue_number)
        if not pr.get("merged"):
            raise docker_control.DockerError(
                "Issue belum memiliki PR merged dari Jembatan. Merge dahulu sebelum QA final.", 409)
        post_merge_branch = pr.get("base")
        if not docker_control.valid_branch(post_merge_branch):
            raise docker_control.DockerError("Branch tujuan PR merged tidak valid", 409)
        pr_number = pr.get("number")
    else:
        issue_number = None
        post_merge_branch = None
        pr_number = None
    with _LOCK:
        if _ACTIVE:
            busy = next((j for j in _JOBS.values() if j["id"] == _ACTIVE), None)
            raise docker_control.DockerError("QA masih berjalan" + (f" untuk {busy['repo']}" if busy else "") + ". Tunggu hingga selesai.", 409)
        identifier = uuid.uuid4().hex
        now = time.time()
        mode = mapping.get("qa_branch_mode", "active")
        if mode not in ("active", "default"):
            raise docker_control.DockerError("Sumber branch QA tidak valid", 409)
        job = dict(id=identifier, repo=repo, container=mapping["qa_container"],
                   profile=mapping["qa_profile"], qa_branch_mode=mode, qa_branch=None,
                   issue_number=issue_number, report_on_fail=report_on_fail,
                   post_merge_branch=post_merge_branch, merged_pr_number=pr_number,
                   report_issue_url=None, report_error=None, followup_prompt=None,
                   source_sha=None, qa_verified=False, verify_reason="Menunggu pemeriksaan",
                   state="queued", stage="Menyiapkan QA",
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


def require_verified(app, repo, branch, sha):
    """Deploy may use another branch, but only if its exact commit was tested."""
    if not docker_control.valid_branch(branch):
        raise docker_control.DockerError("Branch target deployment tidak valid", 409)
    if not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", sha):
        raise docker_control.DockerError("SHA target deployment tidak valid", 409)
    mapping = docker_control._app_settings(app, repo)
    with _LOCK:
        record = _JOBS.get(repo)
        valid = bool(record and record.get("state") == "passed" and
                     record.get("qa_verified") is True and
                     record.get("source_sha") == sha and
                     record.get("container") == mapping.get("qa_container") and
                     record.get("profile") == mapping.get("qa_profile") and
                     record.get("qa_branch_mode") == mapping.get("qa_branch_mode", "active"))
        if valid:
            return {"sha": sha, "branch": branch, "qa_branch": record.get("qa_branch"),
                    "qa_job_id": record["id"]}
        detail = record.get("verify_reason") if record else "Belum ada hasil QA"
        qa_branch = record.get("qa_branch") if record else None
        qa_sha = record.get("source_sha") if record else None
    if qa_branch and qa_sha and qa_sha != sha:
        detail = ("QA terakhir pada branch " + qa_branch + " (" + qa_sha[:12] +
                  "), tetapi branch deploy " + branch + " memerlukan " + sha[:12] +
                  ". Jalankan QA pada checkout commit yang sama.")
    raise docker_control.DockerError(
        "Deploy diblokir: belum ada QA PASS terverifikasi untuk SHA " + sha[:12] +
        " pada target " + branch + ". " + detail, 409)
