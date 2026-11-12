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


class StubApp:
    DEPLOY_MAP = {}

    def __init__(self, workdir):
        self.WORKDIR = Path(workdir)


class DockerControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = StubApp(self.tmp.name)

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

    def test_deploy_requires_exact_confirmation(self):
        with self.assertRaisesRegex(ctl.DockerError, "Konfirmasi"):
            ctl.deploy(self.app, "ha00i/Jembatan", "yes")

    def test_deploy_without_explicit_mapping_blocked(self):
        with self.assertRaisesRegex(ctl.DockerError, "Path deploy"):
            ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")

    def test_local_deploy_requires_existing_git_compose_folder(self):
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": "/unknown-docker-path"})
        with self.assertRaisesRegex(ctl.DockerError, "Mount folder"):
            ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")

    def test_local_deploy_runs_only_mapped_directory(self):
        folder = Path(self.tmp.name) / "app"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", side_effect=["https://github.com/ha00i/Jembatan.git", "", "updated"]) as git:
            with patch.object(ctl, "_docker", side_effect=["created", "healthy"]) as dock:
                result = ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")
        self.assertTrue(result["ok"])
        self.assertEqual(git.call_args.args[0], ["git", "pull", "--ff-only"])
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
                    ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")
                dock.assert_not_called()

    def test_dirty_deploy_checkout_is_rejected(self):
        folder = Path(self.tmp.name) / "dirty-app"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", side_effect=["git@github.com:ha00i/Jembatan.git", " M compose.yaml"]):
            with patch.object(ctl, "_docker") as docker:
                with self.assertRaisesRegex(ctl.DockerError, "perubahan lokal"):
                    ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")
                docker.assert_not_called()

    def test_fake_github_host_is_rejected(self):
        folder = Path(self.tmp.name) / "fake-origin"
        folder.mkdir()
        (folder / ".git").mkdir()
        (folder / "compose.yaml").write_text("services: {}\n")
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan", "deploy_path": str(folder)})
        with patch.object(ctl, "_command", return_value="https://fakegithub.com/ha00i/Jembatan"):
            with self.assertRaisesRegex(ctl.DockerError, "tidak cocok"):
                ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")

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
        with patch.object(ctl, "_command", return_value="deployed") as runner:
            result = ctl.deploy(self.app, "ha00i/Jembatan", "ha00i/Jembatan")
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
