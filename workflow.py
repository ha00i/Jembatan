"""Opt-in Superpowers workflow foundation: SQLite state, immutable audit, and API.

Iteration 1 intentionally does not execute worktrees, ChatGPT, tests, merge or deploy.
The existing /api/prompt and all other legacy endpoints are independent.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

REPO_PATTERN = re.compile(r"^(?!\.{1,2}/)[A-Za-z0-9_.-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
MODES = ("penuh", "cepat")
EXECUTIONS = ("per_tugas", "sekaligus")
EVENT_MAX_BYTES = 20_000
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class WorkflowError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _issue(value):
    if isinstance(value, bool):
        raise WorkflowError("Nomor issue tidak valid", 400)
    if isinstance(value, int):
        issue = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        issue = int(value)
    else:
        raise WorkflowError("Nomor issue tidak valid", 400)
    if not 1 <= issue <= 10_000_000:
        raise WorkflowError("Nomor issue di luar batas", 400)
    return issue


def _repo(value):
    if not isinstance(value, str) or not REPO_PATTERN.fullmatch(value):
        raise WorkflowError("Repository tidak valid", 400)
    return value


def _lock(repo, issue):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault((repo, issue), threading.RLock())


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class WorkflowStore:
    """One SQLite database, one append-only event journal, per-issue concurrency locks."""

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or os.environ.get("JEMBATAN_DATA", "/srv/jembatan"))
        self.db_path = self.data_dir / "workflow.db"
        self._ready = False
        self._init_lock = threading.Lock()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self):
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.db_path.is_symlink():
                raise WorkflowError("Database workflow tidak boleh berupa symbolic link", 409)
            with closing(self._connect()) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        id TEXT PRIMARY KEY,
                        repo TEXT NOT NULL,
                        issue INTEGER NOT NULL,
                        mode TEXT NOT NULL CHECK(mode IN ('penuh','cepat')),
                        execution TEXT NOT NULL CHECK(execution IN ('per_tugas','sekaligus')),
                        phase TEXT NOT NULL,
                        status TEXT NOT NULL,
                        branch TEXT,
                        worktree_path TEXT,
                        base_branch TEXT NOT NULL,
                        base_sha TEXT NOT NULL,
                        head_sha TEXT,
                        round_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_active_issue
                      ON workflow_runs(repo, issue)
                      WHERE phase NOT IN ('done', 'aborted');
                    CREATE INDEX IF NOT EXISTS idx_workflow_issue
                      ON workflow_runs(repo, issue, created_at);
                    CREATE TABLE IF NOT EXISTS workflow_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL REFERENCES workflow_runs(id),
                        ts TEXT NOT NULL,
                        actor TEXT NOT NULL CHECK(actor IN ('user','chatgpt','backend')),
                        event TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        head_sha TEXT,
                        payload_json TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS workflow_events_no_update
                      BEFORE UPDATE ON workflow_events BEGIN
                        SELECT RAISE(ABORT, 'workflow_events append only');
                      END;
                    CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete
                      BEFORE DELETE ON workflow_events BEGIN
                        SELECT RAISE(ABORT, 'workflow_events append only');
                      END;
                    CREATE TABLE IF NOT EXISTS workflow_artifacts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL REFERENCES workflow_runs(id),
                        kind TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        path_in_repo TEXT,
                        UNIQUE(run_id, kind, version)
                    );
                """)
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass
            self._ready = True

    @staticmethod
    def _event(db, run_id, actor, event, phase, head_sha, payload):
        # Never accept raw user prompts/logs or token values in the audit event.
        allow = {"mode", "execution", "base_branch", "base_sha"}
        if not isinstance(payload, dict) or set(payload) - allow:
            raise WorkflowError("Payload audit berisi field yang tidak diizinkan", 400)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > EVENT_MAX_BYTES:
            raise WorkflowError("Peristiwa audit melebihi 20 KB", 413)
        db.execute("INSERT INTO workflow_events (run_id,ts,actor,event,phase,head_sha,payload_json) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (run_id, _timestamp(), actor, event, phase, head_sha, encoded))

    def start(self, repo, issue, mode, execution, base_branch, base_sha):
        repo, issue = _repo(repo), _issue(issue)
        if mode not in MODES or execution not in EXECUTIONS:
            raise WorkflowError("Mode atau eksekusi tidak valid", 400)
        if mode == "cepat" and execution != "sekaligus":
            raise WorkflowError("Alur cepat hanya mendukung eksekusi sekaligus", 400)
        if not isinstance(base_branch, str) or not base_branch or len(base_branch) > 128:
            raise WorkflowError("Branch dasar tidak valid", 400)
        if not SHA_PATTERN.fullmatch(base_sha or ""):
            raise WorkflowError("SHA dasar GitHub tidak valid", 400)
        self._init()
        with _lock(repo, issue):
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    current = db.execute(
                        "SELECT * FROM workflow_runs WHERE repo=? AND issue=? "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (repo, issue)).fetchone()
                    if current:
                        same = current["mode"] == mode and current["execution"] == execution
                        if not same:
                            raise WorkflowError("Issue sudah memiliki workflow dengan mode berbeda", 409)
                        if current["phase"] in ("done", "aborted"):
                            raise WorkflowError("Workflow selesai; membuat run baru belum didukung", 409)
                        db.execute("COMMIT")
                        return self._state(repo, issue, reused=True)
                    run_id = uuid.uuid4().hex
                    phase = "brainstorm" if mode == "penuh" else "worktree"
                    status = "awaiting_chatgpt" if mode == "penuh" else "pending"
                    now = _timestamp()
                    db.execute("INSERT INTO workflow_runs "
                               "(id,repo,issue,mode,execution,phase,status,base_branch,base_sha,created_at,updated_at) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (run_id, repo, issue, mode, execution, phase, status,
                                base_branch, base_sha.lower(), now, now))
                    self._event(db, run_id, "user", "workflow_started", phase, None,
                                {"mode": mode, "execution": execution,
                                 "base_branch": base_branch, "base_sha": base_sha.lower()})
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
        return self._state(repo, issue, reused=False)

    def _state(self, repo, issue, reused=False):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM workflow_runs WHERE repo=? AND issue=? "
                             "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                             (repo, issue)).fetchone()
            if row is None:
                raise WorkflowError("Workflow belum dimulai pada issue ini", 404)
            events = db.execute("SELECT id,ts,actor,event,phase,head_sha,payload_json "
                                "FROM workflow_events WHERE run_id=? ORDER BY id ASC",
                                (row["id"],)).fetchall()
            artifacts = db.execute("SELECT id,kind,version,sha256,path_in_repo "
                                   "FROM workflow_artifacts WHERE run_id=? ORDER BY id ASC",
                                   (row["id"],)).fetchall()
        gate_reason = ("Fase Brainstorming belum tersedia; menunggu iterasi 2. "
                       "Jangan menganggap balasan ChatGPT sudah diverifikasi."
                       if row["phase"] == "brainstorm" else
                       "Worktree dan pemeriksaan baseline belum tersedia; menunggu iterasi 4.")
        return {
            "run": dict(row), "reused": reused,
            "gate": {"can_advance": False, "blocked": True, "reason": gate_reason,
                     "qa_verified": False, "merge_allowed": False, "deploy_allowed": False},
            "iterations": {"brainstorm": row["round_count"], "patch": 0, "review": 0},
            "artifacts": [dict(item) for item in artifacts],
            "events": [dict(item, payload=json.loads(item["payload_json"]))
                       for item in events],
        }

    def state(self, repo, issue):
        repo, issue = _repo(repo), _issue(issue)
        self._init()
        return self._state(repo, issue)


_STORE = None
_STORE_LOCK = threading.Lock()


def _store():
    global _STORE
    with _STORE_LOCK:
        data_dir = os.environ.get("JEMBATAN_DATA", "/srv/jembatan")
        if _STORE is None or str(_STORE.data_dir) != data_dir:
            _STORE = WorkflowStore(data_dir)
        return _STORE


def start_route(app, body):
    if not isinstance(body, dict):
        raise WorkflowError("Input workflow harus JSON object", 400)
    if set(body) - {"repo", "issue", "mode", "execution"}:
        raise WorkflowError("Field permintaan workflow tidak dikenal", 400)
    repo, issue = _repo(body.get("repo")), _issue(body.get("issue"))
    mode = body.get("mode")
    execution = body.get("execution", "sekaligus" if mode == "cepat" else None)
    if mode not in MODES or execution not in EXECUTIONS:
        raise WorkflowError("Pilih alur penuh/cepat dan eksekusi yang valid", 400)
    if mode == "cepat" and execution != "sekaligus":
        raise WorkflowError("Alur cepat tidak mendukung eksekusi per tugas", 400)
    app.repo_dir(repo)
    # GitHub validates that this is an issue (not a PR) and binds base SHA.
    item = app.gh(f"/repos/{repo}/issues/{issue}")
    if item.get("pull_request"):
        raise WorkflowError("Nomor ini adalah PR, bukan issue", 409)
    meta = app.gh(f"/repos/{repo}")
    branch = meta.get("default_branch")
    if not app.docker_control.valid_branch(branch):
        raise WorkflowError("Branch default GitHub tidak valid", 409)
    remote = app.gh(f"/repos/{repo}/branches/{quote(branch, safe='')}")
    sha = (remote.get("commit") or {}).get("sha")
    if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
        raise WorkflowError("GitHub belum memberikan SHA branch default yang valid", 502)
    return _store().start(repo, issue, mode, execution, branch, sha)


def state_route(app, query):
    if not isinstance(query, dict):
        raise WorkflowError("Query workflow tidak valid", 400)
    repo, issue = _repo(query.get("repo")), _issue(query.get("issue"))
    app.repo_dir(repo)
    return _store().state(repo, issue)
