"""Safe merged-branch cleanup: never delete main or branches lacking proof."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "branch_manager.py"
spec = importlib.util.spec_from_file_location("branch_manager_cleanup", SOURCE)
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)


class FakeApp:
    class Err(Exception):
        def __init__(self, message, code=400):
            super().__init__(message)
            self.code = code


class BranchCleanupTests(unittest.TestCase):
    def test_confirmation_required_before_network(self):
        with patch.object(bm, "inventory") as inv:
            with self.assertRaises(FakeApp.Err):
                bm.cleanup_remote(FakeApp(), "ha00i/Jembatan", "yes")
            inv.assert_not_called()

    def test_only_safe_branches_are_removed(self):
        rows = [{"name": "main", "sha": "a", "safe_to_delete": False},
                {"name": "feature-merged", "sha": "b", "safe_to_delete": True},
                {"name": "feature-active", "sha": "c", "safe_to_delete": False}]
        with patch.object(bm, "inventory", return_value={"branches": rows}):
            with patch.object(bm, "delete_remote", return_value={"name": "feature-merged"}) as delete:
                out = bm.cleanup_remote(FakeApp(), "ha00i/Jembatan", "ha00i/Jembatan")
        delete.assert_called_once_with(unittest.mock.ANY, "ha00i/Jembatan", "feature-merged", "b")
        self.assertEqual(out["deleted"], ["feature-merged"])
        self.assertEqual(out["not_safe"], ["main", "feature-active"])

    def test_failed_deletion_is_reported_not_silently_discarded(self):
        rows = [{"name": "merged", "sha": "b", "safe_to_delete": True}]
        with patch.object(bm, "inventory", return_value={"branches": rows}):
            with patch.object(bm, "delete_remote", side_effect=FakeApp.Err("sha changed")):
                out = bm.cleanup_remote(FakeApp(), "ha00i/Jembatan", "ha00i/Jembatan")
        self.assertEqual(out["deleted"], [])
        self.assertIn("sha changed", out["skipped"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
