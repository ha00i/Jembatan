"""Authenticated Superpowers foundation endpoints; no external network or Docker."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bridge
import workflow


class WorkflowRoutesTests(unittest.TestCase):
    def test_existing_prompt_route_remains_available(self):
        self.assertIn(("POST", "/api/prompt"), bridge.ROUTES)
        self.assertIn(("POST", "/api/workflow/start"), bridge.ROUTES)
        self.assertIn(("GET", "/api/workflow/state"), bridge.ROUTES)

    def test_start_and_state_routes_delegate_to_workflow_module(self):
        body = {"repo": "ha00i/Jembatan", "issue": 12,
                "mode": "penuh", "execution": "per_tugas"}
        with patch.object(workflow, "start_route", return_value={"run": {"id": "ok"}}) as start:
            self.assertEqual(bridge.ROUTES[("POST", "/api/workflow/start")]({}, body)
                             ["run"]["id"], "ok")
            start.assert_called_once_with(bridge, body)
        query = {"repo": "ha00i/Jembatan", "issue": "12"}
        with patch.object(workflow, "state_route", return_value={"gate": {"blocked": True}}) as state:
            self.assertTrue(bridge.ROUTES[("GET", "/api/workflow/state")](
                query, {})["gate"]["blocked"])
            state.assert_called_once_with(bridge, query)

    @staticmethod
    def _fake_request(method="GET", token=None, body=None):
        data = json.dumps(body or {}).encode()
        return SimpleNamespace(
            path="/api/workflow/state?repo=ha00i%2FJembatan&issue=12"
                 if method == "GET" else "/api/workflow/start",
            client_address=("198.51.100.42", 0),
            headers={"X-Admin-Token": token or "",
                     "Content-Length": str(len(data))},
            rfile=io.BytesIO(data), reply=Mock())

    def test_new_state_endpoint_requires_existing_token(self):
        request = self._fake_request()
        with patch.object(bridge, "ADMIN", "token-workflow-existing"):
            with patch.object(bridge, "blocked", return_value=False):
                bridge.H.handle_any(request, "GET")
        self.assertEqual(request.reply.call_args.args[0], 401)

    def test_authorized_state_uses_issue_lock_not_global_legacy_lock(self):
        request = self._fake_request(token="token-workflow-existing")
        with patch.object(bridge, "ADMIN", "token-workflow-existing"):
            with patch.object(bridge, "blocked", return_value=False):
                with patch.object(bridge, "LOCK", object()):
                    with patch.object(workflow, "state_route",
                                      return_value={"gate": {"blocked": True}}):
                        bridge.H.handle_any(request, "GET")
        self.assertEqual(request.reply.call_args.args[0], 200)
        self.assertTrue(request.reply.call_args.args[1]["gate"]["blocked"])

    def test_workflow_error_returns_safe_http_conflict_not_internal_trace(self):
        request = self._fake_request(token="token-workflow-existing")
        with patch.object(bridge, "ADMIN", "token-workflow-existing"):
            with patch.object(bridge, "blocked", return_value=False):
                with patch.object(workflow, "state_route",
                                  side_effect=workflow.WorkflowError("Run tidak sesuai", 409)):
                    bridge.H.handle_any(request, "GET")
        code, payload = request.reply.call_args.args
        self.assertEqual(code, 409)
        self.assertEqual(payload, {"error": "Run tidak sesuai"})

    def test_legacy_route_still_uses_global_lock(self):
        # Source-level contract: do not loosen existing legacy serialization.
        src = (bridge.H.handle_any.__code__.co_names)
        self.assertIn("LOCK", src)


if __name__ == "__main__":
    unittest.main()
