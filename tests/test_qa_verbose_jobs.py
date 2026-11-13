"""QA verbose job tests: mocked Docker and stdout; no production access."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qa_jobs", ROOT / "qa_jobs.py")
qa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa)


class FakeApp:
    GH_TOKEN = "ghp_SUPERSECRET_FAKE_TOKEN_12345678"
    ADMIN = "another-secret-admin-12345"


def mapping(app, repo):
    return {"repo": repo, "qa_container": "vortex-qa-qa-app-1",
            "qa_profile": "python", "qa_branch_mode": "active",
            "deploy_branch": "staging", "deploy_path": ""}


class QAJobTests(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        with qa._LOCK:
            qa._JOBS.clear()
            qa._ACTIVE = None

    def wait_done(self, repo="ha00i/Jembatan"):
        for _ in range(250):
            status = qa.status(self.app, repo)["job"]
            if status and status["state"] in ("passed", "failed"):
                return status
            time.sleep(0.01)
        self.fail("QA worker did not finish")

    def test_start_and_stream_real_chunks_during_qa(self):
        ready = threading.Event()
        release = threading.Event()

        def fake_run(app, repo, on_event, **kwargs):
            on_event("stage", "Menjalankan Python")
            on_event("output", "test_login ... ok\\n")
            ready.set()
            self.assertTrue(release.wait(2))
            on_event("output", "test_api ... ok\\n")
            return {"ok": True, "output": "OK", "container": "vortex-qa-qa-app-1"}

        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", side_effect=fake_run):
                started = qa.start(self.app, "ha00i/Jembatan")
                self.assertTrue(ready.wait(2))
                current = qa.status(self.app, "ha00i/Jembatan")["job"]
                self.assertEqual(current["id"], started["job_id"])
                self.assertEqual(current["stage"], "Menjalankan Python")
                self.assertIn("test_login ... ok", current["log"])
                self.assertEqual(current["state"], "running")
                release.set()
                finished = self.wait_done()
                self.assertEqual(finished["state"], "passed")
                self.assertTrue(finished["ok"])
                self.assertIn("test_api ... ok", finished["log"])

    def test_second_job_blocked_while_first_runs(self):
        ready, release = threading.Event(), threading.Event()
        def fake_run(app, repo, on_event, **kwargs):
            ready.set()
            self.assertTrue(release.wait(2))
            return {"ok": True, "output": ""}

        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", side_effect=fake_run):
                qa.start(self.app, "ha00i/Jembatan")
                self.assertTrue(ready.wait(2))
                with self.assertRaisesRegex(qa.docker_control.DockerError, "QA masih berjalan"):
                    qa.start(self.app, "ha00i/Other")
                release.set()
                self.wait_done()

    def test_failed_qa_is_failed_not_pass(self):
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", return_value={"ok": False, "output": "FAIL"}):
                qa.start(self.app, "ha00i/Jembatan")
                result = self.wait_done()
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["exit_code"])  # nonzero exit code not exposed by legacy Docker runner

    def test_worker_exception_is_visible_as_failed(self):
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", side_effect=RuntimeError("simulated worker error")):
                qa.start(self.app, "ha00i/Jembatan")
                result = self.wait_done()
        self.assertEqual(result["state"], "failed")
        self.assertIn("simulated worker error", result["log"])

    def test_qa_issue_logs_hide_credentials_before_publication(self):
        value = qa._sanitize(
            "Authorization: Bearer secretJWT\n"
            "AWS_SECRET_ACCESS_KEY=abc1234\n"
            "https://username:password123@example.com/private\n"
            "CI_JOB_TOKEN=tokenvalue", self.app)
        for secret in ("secretJWT", "abc1234", "password123", "tokenvalue"):
            self.assertNotIn(secret, value)
        self.assertIn("[REDACTED]", value)

    def test_secrets_are_redacted_from_status(self):
        log = ("secret " + self.app.GH_TOKEN + "\\n" + self.app.ADMIN
               + "\\npassword=examplepassword\\n")
        value = qa._sanitize(log, self.app)
        self.assertNotIn("SUPERSECRET", value)
        self.assertNotIn(self.app.ADMIN, value)
        self.assertNotIn("examplepassword", value)
        self.assertIn("[REDACTED]", value)

    def test_bounded_output_truncation(self):
        obj = dict(id="abc", repo="ha00i/Jembatan", container="vortex-qa-qa-app-1",
                   profile="python", state="running", stage="test",
                   started_at=time.time(), updated_at=time.time(),
                   finished_at=None, exit_code=None, ok=None,
                   log="", truncated=False)
        qa._append(obj, "x" * (qa.MAX_LOG + 400))
        self.assertEqual(len(obj["log"]), qa.MAX_LOG)
        self.assertFalse(qa._public(obj, self.app)["job"]["ok"])

    def test_unknown_repo_read_is_rejected(self):
        with self.assertRaises(qa.docker_control.DockerError):
            qa.status(self.app, "../another-repo")

    def test_profile_missing_fails_before_worker_start(self):
        with patch.object(qa.docker_control, "_app_settings",
                          return_value={"qa_container": "", "qa_profile": ""}):
            with self.assertRaises(qa.docker_control.DockerError):
                qa.start(self.app, "ha00i/Jembatan")
        self.assertFalse(qa.active())

    def test_verified_qa_evidence_allows_same_branch_and_sha(self):
        sha = "a" * 40
        self.app.gh = lambda path: {"commit": {"sha": sha}}
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", return_value={
                "ok": True, "source_verified": True, "source_sha": sha,
                "source_reason": "Git clean and HEAD matched",
                "source_branch": "main", "output": "OK"
            }):
                qa.start(self.app, "ha00i/Jembatan")
                result = self.wait_done()
                self.assertTrue(result["qa_verified"])
                proof = qa.require_verified(self.app, "ha00i/Jembatan", "main", sha)
        self.assertEqual(proof["sha"], sha)

    def test_changed_sha_or_target_branch_blocks_deploy(self):
        sha = "a" * 40
        self.app.gh = lambda path: {"commit": {"sha": sha}}
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", return_value={
                "ok": True, "source_verified": True, "source_sha": sha,
                "source_branch": "main"
            }):
                qa.start(self.app, "ha00i/Jembatan")
                self.wait_done()
                with self.assertRaisesRegex(qa.docker_control.DockerError, "Deploy diblokir"):
                    qa.require_verified(self.app, "ha00i/Jembatan", "main", "b" * 40)
                # QA main can release staging if the tested commit SHA is identical.
                proof = qa.require_verified(self.app, "ha00i/Jembatan", "staging", sha)
                self.assertEqual(proof["qa_branch"], "main")

    def test_passed_test_with_unverified_container_does_not_grant_deploy(self):
        sha = "a" * 40
        self.app.gh = lambda path: {"commit": {"sha": sha}}
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", return_value={
                "ok": True, "source_verified": False, "source_sha": None,
                "source_reason": "Git not installed",
                "source_branch": "main", "output": "OK"
            }):
                qa.start(self.app, "ha00i/Jembatan")
                result = self.wait_done()
                self.assertEqual(result["state"], "passed")
                self.assertFalse(result["qa_verified"])
                with self.assertRaisesRegex(qa.docker_control.DockerError, "Git not installed"):
                    qa.require_verified(self.app, "ha00i/Jembatan", "main", sha)

    def test_default_mode_uses_github_default_branch_not_deploy_branch(self):
        sha = "b" * 40
        observed = []
        self.app.gh = lambda path: (
            {"default_branch": "develop"} if path == "/repos/ha00i/Jembatan"
            else {"commit": {"sha": sha}}
        )
        def with_default(app, repo):
            result = mapping(app, repo)
            result["qa_branch_mode"] = "default"
            result["deploy_branch"] = "release/v2"
            return result
        def fake_run(app, repo, on_event, **kwargs):
            observed.append(kwargs)
            return {"ok": True, "source_branch": "develop",
                    "source_sha": sha, "source_verified": True}
        with patch.object(qa.docker_control, "_app_settings", side_effect=with_default):
            with patch.object(qa.docker_control, "run_qa", side_effect=fake_run):
                qa.start(self.app, "ha00i/Jembatan")
                result = self.wait_done()
                self.assertTrue(result["qa_verified"])
                self.assertEqual(result["qa_branch"], "develop")
                with self.assertRaises(qa.docker_control.DockerError):
                    qa.require_verified(self.app, "ha00i/Jembatan", "release/v2", "a" * 40)
                proof = qa.require_verified(self.app, "ha00i/Jembatan", "release/v2", sha)
        self.assertEqual(proof["qa_branch"], "develop")
        self.assertEqual(observed[0]["branch_mode"], "default")
        self.assertEqual(observed[0]["required_branch"], "develop")

    def test_active_mode_uses_checkout_branch_not_deploy_config(self):
        sha = "c" * 40
        urls = []
        def fake_gh(url):
            urls.append(url)
            return {"commit": {"sha": sha}}
        self.app.gh = fake_gh
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa",
                              return_value={"ok": True, "source_branch": "feature/login",
                                            "source_sha": sha, "source_verified": True}) as run:
                qa.start(self.app, "ha00i/Jembatan")
                result = self.wait_done()
        self.assertTrue(result["qa_verified"])
        self.assertEqual(result["qa_branch"], "feature/login")
        self.assertTrue(any("/branches/feature%2Flogin" in url for url in urls))
        self.assertEqual(run.call_args.kwargs["branch_mode"], "active")
        self.assertIsNone(run.call_args.kwargs["required_branch"])

    def test_failed_manual_qa_reopens_associated_issue_automatically(self):
        from unittest.mock import Mock
        self.app.gh = lambda path: (
            {"number": 19, "state": "closed"} if "/issues/19" in path
            else {"commit": {"sha": "a" * 40}}
        )
        self.app.report_qa_failure = Mock(return_value={
            "issue": 19, "url": "https://example/issues/19",
            "prompt": "STATUS: SELESAI — lanjutkan perbaikan"
        })
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa", return_value={
                "ok": False, "source_branch": "main", "source_sha": "a" * 40,
                "source_verified": True, "output": "FAIL test_api"
            }):
                qa.start(self.app, "ha00i/Jembatan",
                         issue_number=19, report_on_fail=True)
                result = self.wait_done()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["issue_number"], 19)
        self.assertIn("issues/19", result["report_issue_url"])
        self.assertIn("STATUS: SELESAI", result["followup_prompt"])
        self.app.report_qa_failure.assert_called_once()

    def test_successful_manual_qa_never_creates_issue(self):
        from unittest.mock import Mock
        self.app.report_qa_failure = Mock()
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with patch.object(qa.docker_control, "run_qa",
                              return_value={"ok": True, "output": "PASS"}):
                qa.start(self.app, "ha00i/Jembatan", report_on_fail=True)
                result = self.wait_done()
        self.assertEqual(result["state"], "passed")
        self.app.report_qa_failure.assert_not_called()

    def test_issue_tracking_option_validates_issue_reference(self):
        self.app.gh = lambda url: {"number": 19, "pull_request": {"url": "pr"}}
        with patch.object(qa.docker_control, "_app_settings", side_effect=mapping):
            with self.assertRaisesRegex(qa.docker_control.DockerError, "mengarah ke PR"):
                qa.start(self.app, "ha00i/Jembatan", issue_number=19,
                         report_on_fail=True)
        self.assertFalse(qa.active())

    def test_stdout_callback_receives_chunks_from_real_subprocess(self):
        chunks = []
        out = qa.docker_control._command([
            "python3", "-c",
            "import sys,time;print('stage-1',flush=True);time.sleep(.04);print('stage-2',flush=True)"],
            timeout=3, on_chunk=chunks.append)
        self.assertIn("stage-1", out)
        self.assertIn("stage-2", "".join(chunks))


if __name__ == "__main__":
    unittest.main()
