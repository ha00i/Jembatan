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
