"""Docker & QA tests: no real Docker, SSH, Git, or deployment is invoked."""
import importlib.util
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("docker_control", HERE / "docker_control.py")
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

SHA = "a" * 40


class StubApp:
    DEPLOY_MAP = {}

    def __init__(self, workdir):
        self.WORKDIR = Path(workdir)


class DockerControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = StubApp(self.tmp.name)

    def test_repository_dot_segments_are_not_valid_names(self):
        bad = ("../another-repo", "./another-repo", "owner/..",
               "owner/.", "owner/repo/../other", "owner//repo")
        for repo in bad:
            with self.subTest(repo=repo):
                self.assertFalse(ctl.REPO_RE.fullmatch(repo))
        # GitHub supports a dedicated '.github' repository name;
        # reject only the exact dot/dotdot path segments, not all dots.
        self.assertIsNotNone(ctl.REPO_RE.fullmatch("example/.github"))
        self.assertIsNotNone(ctl.REPO_RE.fullmatch("ha00i/Jembatan"))

    def test_subprocess_closes_stdout_after_success_and_timeout(self):
        import gc
        import warnings
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ResourceWarning)
            result = ctl._command([sys.executable, "-c", "print('OK')"], timeout=3)
            self.assertEqual(result, "OK")
            with self.assertRaises(ctl.DockerError):
                ctl._command([sys.executable, "-c", "import time;time.sleep(5)"], timeout=0.05)
            gc.collect()
        leaked = [str(item.message) for item in captured
                  if issubclass(item.category, ResourceWarning) and "unclosed file" in str(item.message)]
        self.assertEqual(leaked, [])

    def test_subprocess_output_is_memory_bounded(self):
        output = ctl._command([sys.executable, "-c", "print('x' * 100000)"], timeout=5)
        self.assertLessEqual(len(output), 65536)
        self.assertTrue(output.endswith("xxxx"))

    def test_subprocess_timeout_reports_error(self):
        with self.assertRaises(ctl.DockerError) as ctx:
            ctl._command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
        self.assertEqual(ctx.exception.code, 504)

    def test_local_is_default_and_has_no_secrets(self):
        settings = ctl.public_settings(self.app)
        self.assertEqual(settings["mode"], "local")
        self.assertNotIn("password", settings)
        self.assertNotIn("ssh_key", settings)

    def test_rejects_remote_user_root(self):
        with self.assertRaises(ctl.DockerError):
            ctl.save_connection(self.app, {"mode": "ssh", "host": "192.168.1.2",
                                           "username": "root", "port": 22})

    def test_rejects_malicious_hostname(self):
        for host in ("a;rm -rf /", "-oProxyCommand=bad", "test..lan", ""):
            with self.subTest(host=host):
                with self.assertRaises(ctl.DockerError):
                    ctl.save_connection(self.app, {"mode": "ssh", "host": host,
                                                   "username": "dev", "port": 22})

    def test_rejects_bad_port(self):
        with self.assertRaises(ctl.DockerError):
            ctl.save_connection(self.app, {"mode": "ssh", "host": "192.168.1.2",
                                           "username": "dev", "port": 0})

    def test_ssh_saves_no_password_and_file_private(self):
        obj = ctl.save_connection(self.app, {"mode": "ssh", "host": "192.168.1.2",
                                             "username": "dev", "port": 2222, "password": "secret"})
        self.assertEqual(obj["mode"], "ssh")
        path = ctl._cfg_path(self.app)
        self.assertNotIn("secret", path.read_text())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_prevent_qa_on_production_named_container(self):
        with self.assertRaisesRegex(ctl.DockerError, "container QA khusus"):
            ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "qa_container": "erp-app",
                                    "qa_profile": "laravel"})

    def test_qa_requires_profile_and_container_together(self):
        with self.assertRaises(ctl.DockerError):
            ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "qa_container": "vortex-qa-app"})

    def test_qa_disabled_without_mapping(self):
        with self.assertRaises(ctl.DockerError):
            ctl.run_qa(self.app, "ha00i/Jembatan")

    def set_qa(self):
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "qa_container": "vortex-qa-app",
                                "qa_profile": "laravel", "deploy_path": ""})

    def test_qa_rejects_privileged_container(self):
        self.set_qa()
        with patch.object(ctl, "_docker", return_value="true|true|bridge"):
            with self.assertRaisesRegex(ctl.DockerError, "isolasi"):
                ctl.run_qa(self.app, "ha00i/Jembatan")

    def test_qa_rejects_host_network(self):
        self.set_qa()
        with patch.object(ctl, "_docker", return_value="true|false|host"):
            with self.assertRaisesRegex(ctl.DockerError, "isolasi"):
                ctl.run_qa(self.app, "ha00i/Jembatan")

    def test_qa_uses_fixed_argv_without_shell(self):
        self.set_qa()
        with patch.object(ctl, "_docker", side_effect=["true|false|bridge", "Unit tests PASS"]) as mock:
            result = ctl.run_qa(self.app, "ha00i/Jembatan")
        self.assertTrue(result["ok"])
        self.assertEqual(mock.call_args_list[-1].args[1:],
                         ("exec", "vortex-qa-app", "php", "artisan", "test", "--no-ansi"))

    def test_qa_failure_returns_nonpass_result(self):
        self.set_qa()
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", ctl.DockerError("test failed", 502)
        ]):
            result = ctl.run_qa(self.app, "ha00i/Jembatan")
        self.assertFalse(result["ok"])

    def test_qa_checkout_git_sha_is_attested_before_and_after_tests(self):
        self.set_qa()
        sha = "a" * 40
        output = "Tests: 4 passed"
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", sha, "", output, sha, ""
        ]) as mock:
            result = ctl.run_qa(self.app, "ha00i/Jembatan", on_event=lambda *x: None)
        self.assertTrue(result["ok"])
        self.assertTrue(result["source_verified"])
        self.assertEqual(result["source_sha"], sha)
        self.assertEqual(mock.call_count, 6)

    def test_qa_without_git_metadata_may_pass_but_is_not_deploy_ready(self):
        self.set_qa()
        events = []
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", ctl.DockerError("git unavailable", 502), "Tests PASS"
        ]):
            result = ctl.run_qa(self.app, "ha00i/Jembatan",
                                on_event=lambda kind, msg: events.append((kind, msg)))
        self.assertTrue(result["ok"])
        self.assertFalse(result["source_verified"])
        self.assertIsNone(result["source_sha"])
        self.assertTrue(any("Git checkout" in msg for _, msg in events))

    def test_qa_sha_changes_during_test_is_not_verified(self):
        self.set_qa()
        sha = "a" * 40
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", sha, "", "PASS", "b" * 40, ""
        ]):
            result = ctl.run_qa(self.app, "ha00i/Jembatan", on_event=lambda *x: None)
        self.assertTrue(result["ok"])
        self.assertFalse(result["source_verified"])

    def test_active_qa_uses_container_checkout_not_deploy_target(self):
        self.set_qa()
        sha = "a" * 40
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", "feature/login",
            "https://github.com/ha00i/Jembatan.git",
            sha, "", "All tests passed", sha, "", "feature/login"
        ]) as dock:
            result = ctl.run_qa(self.app, "ha00i/Jembatan", on_event=lambda *x: None,
                                branch_mode="active")
        self.assertTrue(result["source_verified"])
        self.assertEqual(result["source_branch"], "feature/login")
        self.assertEqual(dock.call_count, 9)

    def test_default_qa_requires_exact_checkout_without_switching(self):
        self.set_qa()
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", "feature/login",
            "https://github.com/ha00i/Jembatan"
        ]) as dock:
            with self.assertRaisesRegex(ctl.DockerError, "Pindahkan checkout QA dahulu"):
                ctl.run_qa(self.app, "ha00i/Jembatan", on_event=lambda *x: None,
                           branch_mode="default", required_branch="main")
        self.assertEqual(dock.call_count, 3)

    def test_qa_refuses_mismatched_checkout_origin_before_running_tests(self):
        self.set_qa()
        with patch.object(ctl, "_docker", side_effect=[
            "true|false|bridge", "main", "https://github.com/other/other.git"
        ]) as dock:
            with self.assertRaisesRegex(ctl.DockerError, "Origin Git container QA"):
                ctl.run_qa(self.app, "ha00i/Jembatan", on_event=lambda *x: None,
                           branch_mode="active")
        self.assertEqual(dock.call_count, 3)

    def test_default_qa_rejects_missing_branch(self):
        self.set_qa()
        with self.assertRaisesRegex(ctl.DockerError, "Branch default GitHub"):
            ctl.run_qa(self.app, "ha00i/Jembatan", on_event=lambda *x: None,
                       branch_mode="default", required_branch="../bad")

    def test_deploy_requires_exact_confirmation(self):
        with self.assertRaisesRegex(ctl.DockerError, "Konfirmasi"):
            ctl.deploy(self.app, "ha00i/Jembatan", "yes")

    def test_deploy_without_explicit_mapping_blocked(self):
        with self.assertRaisesRegex(ctl.DockerError, "Path deploy"):
            ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)

    def test_local_deploy_requires_existing_git_compose_folder(self):
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": "/srv/unknown-docker-path"})
        with self.assertRaisesRegex(ctl.DockerError, "Mount folder"):
            ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)

    def test_local_deploy_runs_only_mapped_directory(self):
        folder = Path(self.tmp.name) / "app"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", side_effect=[
            "https://github.com/ha00i/Jembatan.git", "main", "", "", SHA, "updated", SHA
        ]) as git:
            with patch.object(ctl, "_docker", side_effect=[
                "created", '{"State":"running","Health":"healthy"}'
            ]) as dock:
                result = ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)
        self.assertTrue(result["ok"])
        self.assertIn(["git", "pull", "--ff-only", "origin", "main"],
                          [call.args[0] for call in git.call_args_list])
        self.assertTrue(result["health_verified"])
        self.assertEqual(result["commit_sha"], SHA)
        self.assertEqual(dock.call_args_list[0].args[1:],
                         ("compose", "up", "-d", "--build"))
        self.assertEqual(dock.call_args_list[0].kwargs["cwd"], folder)

    def test_wrong_repository_mapping_is_blocked_before_deploy(self):
        folder = Path(self.tmp.name) / "wrong-app"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "docker-compose.yml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", return_value="https://github.com/other/wrong.git"):
            with patch.object(ctl, "_docker") as dock:
                with self.assertRaisesRegex(ctl.DockerError, "tidak cocok"):
                    ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)
                dock.assert_not_called()

    def test_dirty_deploy_checkout_is_rejected(self):
        folder = Path(self.tmp.name) / "dirty-app"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", side_effect=["git@github.com:ha00i/Jembatan.git", "main", " M compose.yaml"]):
            with patch.object(ctl, "_docker") as docker:
                with self.assertRaisesRegex(ctl.DockerError, "perubahan lokal"):
                    ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)
                docker.assert_not_called()

    def test_fake_github_host_is_rejected(self):
        folder = Path(self.tmp.name) / "fake-origin"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", return_value="https://fakegithub.com/ha00i/Jembatan"):
            with self.assertRaisesRegex(ctl.DockerError, "tidak cocok"):
                ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)

    def test_rejects_unsafe_deploy_branch(self):
        for name in ("../prod", "feature//bad", "feature;rm -rf /", "feature/.."):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ctl.DockerError, "Branch deploy tidak valid"):
                    ctl.save_app(self.app, {"repo": "ha00i/Jembatan",
                                            "deploy_branch": name})

    def test_allows_any_valid_deploy_branch_independent_from_qa(self):
        app = ctl.save_app(self.app, {"repo": "ha00i/Jembatan",
                                     "deploy_branch": "release/v2",
                                     "qa_branch_mode": "default"})
        self.assertEqual(app["deploy_branch"], "release/v2")
        self.assertEqual(app["qa_branch_mode"], "default")
        with self.assertRaisesRegex(ctl.DockerError, "Sumber branch QA"):
            ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "qa_branch_mode": "release/v2"})

    def test_deploy_without_sha_evidence_is_blocked(self):
        with self.assertRaisesRegex(ctl.DockerError, "Bukti QA"):
            ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main")

    def test_wrong_active_branch_blocks_deploy_before_build(self):
        folder = Path(self.tmp.name) / "wrong-branch"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", side_effect=[
            "https://github.com/ha00i/Jembatan", "staging"
        ]):
            with patch.object(ctl, "_docker") as dock:
                with self.assertRaisesRegex(ctl.DockerError, "Branch aktif"):
                    ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)
                dock.assert_not_called()

    def test_remote_tip_changed_after_qa_blocks_build(self):
        folder = Path(self.tmp.name) / "changed-sha"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", side_effect=[
            "https://github.com/ha00i/Jembatan", "main", "", "", "b" * 40
        ]):
            with patch.object(ctl, "_docker") as dock:
                with self.assertRaisesRegex(ctl.DockerError, "Commit GitHub berubah"):
                    ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)
                dock.assert_not_called()

    def test_compose_health_unknown_is_not_claimed_healthy(self):
        self.assertEqual(ctl._compose_health('{"State":"running","Health":""}'), "unverified")
        self.assertEqual(ctl._compose_health('{"State":"running","Health":"unhealthy"}'), "unhealthy")
        self.assertEqual(ctl._compose_health('{"State":"running","Health":"healthy"}'), "healthy")

    def test_connection_only_checks_read_only_info(self):
        with patch.object(ctl, "_docker", return_value="27.4|2|mail") as dock:
            result = ctl.connection_test(self.app)
        self.assertTrue(result["connected"])
        self.assertEqual(dock.call_args.args[1], "info")

    def test_remote_deploy_escapes_path_and_uses_strict_ssh(self):
        ctl.save_connection(self.app, {"mode": "ssh", "host": "192.168.1.2",
                                       "username": "dev", "port": 22})
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan",
                                "deploy_path": "/srv/my project"})
        with patch.object(ctl, "_command",
                          return_value='deployed\n--JEMBATAN-COMPOSE-PS--\n{"State":"running","Health":"healthy"}') as runner:
            result = ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan", "main", SHA)
        cmd = runner.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", cmd)
        self.assertIn("dev@192.168.1.2", cmd)
        self.assertIn("/srv/my project", cmd[-1])
        self.assertTrue(result["ok"])

    def test_docker_base_local_socket_and_remote_ssh(self):
        self.assertEqual(ctl._docker_base(self.app)[1:], ["--host", "unix:///var/run/docker.sock"])
        ctl.save_connection(self.app, {"mode": "ssh", "host": "mail.lan",
                                       "username": "dev", "port": 2222})
        self.assertEqual(ctl._docker_base(self.app)[2], "ssh://dev@mail.lan:2222")

    def test_reject_qa_injection_as_profile(self):
        with self.assertRaises(ctl.DockerError):
            ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "qa_container": "vortex-qa-app",
                                    "qa_profile": "bash -c rm -rf /"})


if __name__ == "__main__":
    unittest.main()
