"""Routes and deployment exclusion for verbose QA; no real Docker calls."""
import unittest
from unittest.mock import patch
import bridge
import qa_jobs


class QARoutesTests(unittest.TestCase):
    def test_bridge_rejects_traversal_repo_names(self):
        for repo in ("../another-repo", "owner/..", "./foo", "owner/.", "owner/repo\n", None):
            with self.subTest(repo=repo):
                with self.assertRaisesRegex(bridge.Err, "tidak valid"):
                    bridge.repo_dir(repo)

    def test_available_deploy_branches_includes_valid_feature_branches(self):
        fake = [{"name": "main"}, {"name": "feature"}, {"name": "dev"}]
        with patch.object(bridge, "gh", return_value={"default_branch": "main"}):
            with patch.object(bridge, "gh_pages", return_value=fake):
                with patch.object(bridge, "docker_action", return_value={"deploy_branch": "staging"}):
                    result = bridge.deploy_branch_options("ha00i/Jembatan")
        self.assertEqual(result["available"], ["main", "dev", "feature"])
        self.assertEqual(result["configured"], "staging")
        self.assertFalse(result["configured_exists"])

    def test_deployment_target_encodes_feature_branch_in_github_api(self):
        sha = "a" * 40
        with patch.object(bridge, "docker_action", return_value={
                "deploy_branch": "release/v2", "deploy_path": "/srv/app"}):
            with patch.object(bridge, "gh", return_value={"commit": {"sha": sha}}) as github:
                branch, result_sha, settings = bridge.deployment_target("ha00i/Jembatan")
        self.assertEqual(branch, "release/v2")
        self.assertEqual(result_sha, sha)
        github.assert_called_once_with("/repos/ha00i/Jembatan/branches/release%2Fv2")

    def test_save_app_accepts_feature_deploy_branch_independent_from_qa(self):
        with patch.object(bridge, "deploy_branch_options",
                          return_value={"available": ["main", "release/v2"]}):
            with patch.object(bridge, "docker_action",
                              return_value={"deploy_branch": "release/v2",
                                            "qa_branch_mode": "active"}) as docker:
                result = bridge.save_docker_app({
                    "repo": "ha00i/Jembatan", "deploy_branch": "release/v2",
                    "qa_branch_mode": "active"})
        self.assertEqual(result["qa_branch_mode"], "active")
        self.assertIs(docker.call_args.args[0], bridge.docker_control.save_app)

    def test_stale_branch_must_not_be_silently_replaced(self):
        settings = {"deploy_branch": "staging", "deploy_path": "/srv/app"}
        with patch.object(bridge, "docker_action", return_value=settings) as docker:
            with patch.object(bridge, "gh", side_effect=bridge.Err(
                    'GitHub 404: {"message":"Branch not found"}', 502)):
                result = bridge.deploy_readiness("ha00i/Jembatan")
        self.assertFalse(result["qa_ready"])
        self.assertIsNone(result["sha"])
        self.assertEqual(result["branch"], "staging")
        self.assertIn("Simpan aplikasi", result["reason"])
        self.assertEqual(docker.call_count, 2)

    def test_stale_branch_blocks_deploy_before_docker_actions(self):
        settings = {"deploy_branch": "staging", "deploy_path": "/srv/app"}
        with patch.object(qa_jobs, "active", return_value=False):
            with patch.object(bridge, "docker_action", return_value=settings) as docker:
                with patch.object(bridge, "gh", side_effect=bridge.Err(
                        'GitHub 404: {"message":"Branch not found"}', 502)):
                    with self.assertRaisesRegex(bridge.Err, "tidak ada di GitHub"):
                        bridge.deploy("ha00i/Jembatan", "ha00i/Jembatan")
        self.assertEqual(docker.call_count, 1)

    def test_save_app_rejects_nonexistent_branch(self):
        with patch.object(bridge, "deploy_branch_options",
                          return_value={"available": ["main"], "configured": "staging"}):
            with patch.object(bridge, "docker_action") as docker:
                with self.assertRaisesRegex(bridge.Err, "Pilih branch tersedia: main"):
                    bridge.save_docker_app({"repo": "ha00i/Jembatan",
                                            "deploy_branch": "staging"})
                docker.assert_not_called()

    def test_save_app_accepts_existing_branch(self):
        with patch.object(bridge, "deploy_branch_options",
                          return_value={"available": ["main"], "configured": "main"}):
            with patch.object(bridge, "docker_action", return_value={"deploy_branch": "main"}) as action:
                result = bridge.save_docker_app({"repo": "ha00i/Jembatan", "deploy_branch": "main"})
        self.assertEqual(result["deploy_branch"], "main")
        self.assertIs(action.call_args.args[0], bridge.docker_control.save_app)

    def test_branch_options_route_uses_live_github(self):
        with patch.object(bridge, "deploy_branch_options",
                          return_value={"available": ["main"]}) as check:
            result = bridge.ROUTES[("GET", "/api/deploy/branches")](
                {"repo": "ha00i/Jembatan"}, {})
        self.assertEqual(result["available"], ["main"])
        check.assert_called_once_with("ha00i/Jembatan")

    def test_start_route_is_exposed(self):
        with patch.object(bridge, "docker_action", return_value={"job_id": "job1"}) as action:
            result = bridge.ROUTES[("POST", "/api/docker/qa/start")]({}, {"repo": "ha00i/Jembatan"})
        self.assertEqual(result["job_id"], "job1")
        self.assertIs(action.call_args.args[0], qa_jobs.start)

    def test_status_route_is_read_only(self):
        with patch.object(bridge, "docker_action", return_value={"job": None}) as action:
            result = bridge.ROUTES[("GET", "/api/docker/qa/status")]({"repo": "ha00i/Jembatan"}, {})
        self.assertEqual(result, {"job": None})
        self.assertIs(action.call_args.args[0], qa_jobs.status)

    def test_deploy_blocked_while_qa_is_active(self):
        with patch.object(qa_jobs, "active", return_value=True):
            with patch.object(bridge, "docker_action") as command:
                with self.assertRaisesRegex(bridge.Err, "QA masih berjalan"):
                    bridge.deploy("ha00i/Jembatan", "ha00i/Jembatan")
                command.assert_not_called()

    def test_legacy_qa_blocked_while_verbose_qa_active(self):
        with patch.object(qa_jobs, "active", return_value=True):
            with patch.object(bridge, "docker_action") as command:
                with self.assertRaisesRegex(bridge.Err, "QA lain masih berjalan"):
                    bridge.legacy_qa("ha00i/Jembatan")
                command.assert_not_called()

    def test_deploy_still_requires_existing_controller_when_idle(self):
        with patch.object(qa_jobs, "active", return_value=False):
            with patch.object(bridge, "deployment_target", return_value=("main", "a" * 40, {})):
                with patch.object(bridge, "docker_action", return_value={"ok": True}) as action:
                    result = bridge.deploy("ha00i/Jembatan", "ha00i/Jembatan")
        self.assertTrue(result["ok"])
        self.assertIs(action.call_args.args[0], bridge.docker_control.deploy)
        self.assertEqual(action.call_args.args[-2:], ("main", "a" * 40))

    def test_deploy_readiness_is_dry_run_only(self):
        with patch.object(bridge, "deployment_target", return_value=("main", "a" * 40, {"deploy_path": "/srv/app"})):
            with patch.object(bridge, "docker_action", return_value={"qa_job_id": "job1"}) as action:
                result = bridge.deploy_readiness("ha00i/Jembatan")
        self.assertTrue(result["qa_ready"])
        self.assertIs(action.call_args.args[0], qa_jobs.require_verified)

    def test_deploy_readiness_without_qa_proof_is_blocked(self):
        with patch.object(bridge, "deployment_target", return_value=("main", "a" * 40, {"deploy_path": "/srv/app"})):
            with patch.object(bridge, "docker_action", side_effect=bridge.Err("QA not verified", 409)):
                result = bridge.deploy_readiness("ha00i/Jembatan")
        self.assertFalse(result["qa_ready"])
        self.assertIn("QA not verified", result["reason"])

    def test_unconfirmed_deploy_does_not_call_github_or_docker(self):
        with patch.object(qa_jobs, "active", return_value=False):
            with patch.object(bridge, "deployment_target") as github:
                with patch.object(bridge, "docker_action") as docker:
                    with self.assertRaises(bridge.Err):
                        bridge.deploy("ha00i/Jembatan", "yes")
                    github.assert_not_called()
                    docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
