"""Read-only Docker Compose path discovery, no real Docker/SSH/deployment."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bridge
import docker_control as ctl


class ComposeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workdir = Path(self.temp.name) / "repos"
        self.workdir.mkdir()
        self.app = SimpleNamespace(WORKDIR=self.workdir, DEPLOY_MAP={})

    def project(self, name="project", compose="compose.yaml"):
        root = Path(self.temp.name) / name
        root.mkdir()
        (root / ".git").mkdir()
        (root / compose).write_text("services: {}\n", encoding="utf-8")
        return root

    @staticmethod
    def labels(path, project="jembatan"):
        return json.dumps({"com.docker.compose.project.working_dir": str(path),
                           "com.docker.compose.project": project})

    def test_detect_verified_compose_path_by_docker_label_and_git_remote(self):
        root = self.project()
        with patch.object(ctl, "_docker", side_effect=[
                "abcdef123456\n", self.labels(root)]) as docker:
            with patch.object(ctl, "_command",
                              return_value="https://github.com/ha00i/Jembatan.git") as git:
                result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        match = next(x for x in result["candidates"] if x["path"] == str(root))
        self.assertTrue(match["verified"])
        self.assertEqual(match["compose_file"], "compose.yaml")
        self.assertEqual(result["auto_saved"], False)
        self.assertEqual(docker.call_count, 2)
        self.assertEqual(git.call_args.kwargs["cwd"], root)
        self.assertIn("inspect", docker.call_args.args)

    def test_different_git_origin_is_never_trusted(self):
        root = self.project()
        with patch.object(ctl, "_docker", side_effect=[
                "abcdef123456\n", self.labels(root)]):
            with patch.object(ctl, "_command",
                              return_value="https://github.com/other/production.git"):
                result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        item = next(x for x in result["candidates"] if x["path"] == str(root))
        self.assertFalse(item["verified"])
        self.assertIn("berbeda", item["reason"])

    def test_inaccessible_host_path_reports_mount_instead_of_verification(self):
        missing = "/mnt/apps/vortex-source"
        with patch.object(ctl, "_docker", side_effect=[
                "abcdef123456\n", self.labels(missing)]):
            with patch.object(ctl, "_command") as git:
                result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        item = next(x for x in result["candidates"] if x["path"] == missing)
        self.assertFalse(item["verified"])
        self.assertIn("mount", item["reason"])
        git.assert_not_called()

    def test_synced_source_checkout_is_never_claimed_as_running_deploy(self):
        source = self.workdir / "ha00i__Jembatan"
        source.mkdir()
        (source / ".git").mkdir()
        (source / "docker-compose.yml").write_text("services: {}\n")
        with patch.object(ctl, "_docker", return_value=""):
            with patch.object(ctl, "_command",
                              return_value="https://github.com/ha00i/Jembatan"):
                result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        item = next(x for x in result["candidates"] if x["path"] == str(source))
        self.assertFalse(item["verified"])
        self.assertEqual(item["compose_file"], "docker-compose.yml")
        self.assertIn("belum terbukti", item["reason"])

    def test_missing_compose_or_git_worktree_is_not_verified(self):
        root = self.project()
        (root / "compose.yaml").unlink()
        other = Path(self.temp.name) / "no-git"
        other.mkdir()
        (other / "compose.yaml").write_text("services: {}\n")
        no_compose = ctl._compose_candidate(self.app, "ha00i/Jembatan", str(root),
                                             "Docker Compose")
        no_git = ctl._compose_candidate(self.app, "ha00i/Jembatan", str(other),
                                         "Docker Compose")
        self.assertFalse(no_compose["verified"])
        self.assertFalse(no_git["verified"])
        self.assertIn("Compose", no_compose["reason"])
        self.assertIn("Git", no_git["reason"])

    def test_rejects_injected_and_relative_paths(self):
        for path in ("../../tmp/project", "/tmp/x/../project",
                     "/tmp/project\nrm -rf /", "/tmp/project\x00bad",
                     "/tmp/project\rbad", "/tmp/abc"):
            with self.subTest(path=path):
                self.assertFalse(ctl._compose_path_valid(path))
        self.assertTrue(ctl._compose_path_valid("/srv/erp/releases/v1"))

    def test_discovery_handles_docker_metadata_outage_without_mutation(self):
        root = self.project()
        ctl.save_app(self.app, {"repo": "ha00i/Jembatan",
                                "deploy_path": str(root)})
        with patch.object(ctl, "_docker",
                          side_effect=ctl.DockerError("Docker unreachable", 503)):
            with patch.object(ctl, "_command",
                              return_value="https://github.com/ha00i/Jembatan"):
                with patch.object(ctl, "_atomic_config") as write:
                    result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        self.assertTrue(result["warnings"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertTrue(result["candidates"][0]["verified"])
        write.assert_not_called()

    def test_ssh_inspection_uses_validated_remote_path_and_never_returns_origin(self):
        remote = "/srv/apps/jembatan release"
        saved = {"mode": "ssh", "host": "ssh.example.net",
                 "username": "deploy", "port": 2222, "apps": {}}
        with patch.object(ctl, "load", return_value=saved):
            with patch.object(ctl, "_docker", side_effect=[
                    "abcdef123456\n", self.labels(remote)]):
                with patch.object(ctl, "_command",
                                  return_value="VERIFIED:compose.yaml") as ssh:
                    result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        item = next(x for x in result["candidates"] if x["path"] == remote)
        self.assertTrue(item["verified"])
        self.assertEqual(item["compose_file"], "compose.yaml")
        self.assertIn("sh -lc", ssh.call_args.args[0][-1])
        self.assertIn("ssh.example.net", ssh.call_args.args[0][-2])
        self.assertNotIn("github.com", json.dumps(result))

    def test_remote_mismatch_does_not_make_path_verified(self):
        settings = {"mode": "ssh", "host": "ssh.example.net",
                    "username": "deploy", "port": 22, "apps": {}}
        with patch.object(ctl, "load", return_value=settings):
            with patch.object(ctl, "_docker", side_effect=[
                    "abcdef123456\n", self.labels("/srv/vortex/project")]):
                with patch.object(ctl, "_command", return_value="ORIGIN_MISMATCH"):
                    result = ctl.discover_compose_paths(self.app, "ha00i/Jembatan")
        self.assertFalse(result["candidates"][0]["verified"])
        self.assertIn("berbeda", result["candidates"][0]["reason"])

    def test_api_route_is_read_only_and_requires_repository(self):
        with patch.object(bridge, "docker_action",
                          return_value={"candidates": []}) as action:
            result = bridge.ROUTES[("GET", "/api/docker/compose-paths")](
                {"repo": "ha00i/Jembatan"}, {})
        self.assertEqual(result["candidates"], [])
        self.assertIs(action.call_args.args[0], ctl.discover_compose_paths)
        self.assertEqual(action.call_args.args[1], "ha00i/Jembatan")


if __name__ == "__main__":
    unittest.main()
