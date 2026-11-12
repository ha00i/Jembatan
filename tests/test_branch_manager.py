"""Offline tests for merge evidence and guarded branch deletion (stdlib only)."""
import tempfile
import unittest
from pathlib import Path

import branch_manager


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


class FakeErr(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class FakeBridge:
    Err = FakeErr

    def __init__(self, location):
        self.location = Path(location)
        (self.location / ".git").mkdir()
        self.head = SHA_A
        self.default = "main"
        self.protected = False
        self.open_prs = []
        self.closed_prs = []
        self.contains = []
        self.deleted = False
        self.calls = []
        self.fail_push = False

    def repo_dir(self, repo):
        if repo != "owner/repo":
            raise self.Err("Invalid repo")
        return self.location

    def need_branch(self, value):
        if value in ("main", "") or ".." in value:
            raise self.Err("Nama branch tidak valid")
        return value

    def gh(self, path):
        if path == "/repos/owner/repo":
            return {"default_branch": self.default}
        if path.startswith("/repos/owner/repo/branches?"):
            return [{"name": "main", "protected": True},
                    {"name": "feature", "protected": self.protected}]
        if "/pulls?state=open" in path:
            return self.open_prs
        if "/pulls?state=closed" in path:
            return self.closed_prs
        raise AssertionError(path)

    def git(self, cwd, *args, **opts):
        self.calls.append(args)
        if args[:2] == ("fetch", "origin"):
            return ""
        if args[:3] == ("rev-parse", "--verify", "-q"):
            return SHA_B
        if args[0] == "for-each-ref":
            if any(a.startswith("--contains=") for a in args):
                return "\n".join(["origin/feature", *["origin/" + t for t in self.contains]])
            return "origin/HEAD\x1f" + SHA_B + "\x1f2026-11-12\norigin/main\x1f" + SHA_B + "\x1f2026-11-12\n" + (
                "" if self.deleted else "origin/feature\x1f" + self.head + "\x1f2026-11-12")
        if args[:3] == ("rev-list", "--left-right", "--count"):
            return "1 2"
        if args[:2] == ("check-ref-format", "--branch"):
            return args[2]
        if args[0] == "push":
            if self.fail_push:
                raise FakeErr("Remote changed", 409)
            self.deleted = True
            return "deleted"
        raise AssertionError(args)


def pr(number, sha=SHA_A, base="main", merged=True):
    return {
        "number": number,
        "merged_at": "2026-11-11T01:00:00Z" if merged else None,
        "base": {"ref": base},
        "head": {"ref": "feature", "sha": sha, "repo": {"full_name": "owner/repo"}}
    }


class BranchSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = FakeBridge(self.tmp.name)

    def item(self):
        return next(b for b in branch_manager.inventory(self.app, "owner/repo")["branches"] if b["name"] == "feature")

    def test_main_is_visible_and_not_deletable(self):
        rows = branch_manager.inventory(self.app, "owner/repo")["branches"]
        main = next(x for x in rows if x["name"] == "main")
        self.assertEqual(main["status"], "blocked")
        self.assertFalse(main["safe_to_delete"])

    def test_exact_ancestry_in_main_is_safe(self):
        self.app.contains = ["main"]
        row = self.item()
        self.assertTrue(row["safe_to_delete"])
        self.assertEqual(row["merged_into"][0]["name"], "main")
        self.assertEqual(row["merged_into"][0]["evidence"], "ancestry")
        self.assertEqual(row["ahead"], 2)
        self.assertEqual(row["behind"], 1)

    def test_exact_sha_merged_pr_into_main_is_safe(self):
        self.app.closed_prs = [pr(12)]
        row = self.item()
        self.assertTrue(row["safe_to_delete"])
        self.assertEqual(row["merged_into"][0]["pr"], 12)
        self.assertEqual(row["merged_into"][0]["evidence"], "merged_pr")

    def test_old_pr_sha_does_not_make_updated_branch_safe(self):
        self.app.closed_prs = [pr(12, sha=SHA_C)]
        self.assertEqual(self.item()["status"], "keep")

    def test_open_pr_blocks_deletion(self):
        self.app.contains = ["main"]
        self.app.open_prs = [pr(3, merged=False)]
        self.assertEqual(self.item()["status"], "blocked")

    def test_protected_branch_blocks_deletion(self):
        self.app.contains = ["main"]
        self.app.protected = True
        self.assertEqual(self.item()["status"], "blocked")

    def test_only_merged_into_staging_is_not_safe_for_default(self):
        self.app.contains = ["staging"]
        row = self.item()
        self.assertEqual(row["status"], "review")
        self.assertEqual(row["merged_into"][0]["name"], "staging")

    def test_delete_checks_expected_sha(self):
        self.app.contains = ["main"]
        with self.assertRaises(FakeErr) as ctx:
            branch_manager.delete_remote(self.app, "owner/repo", "feature", SHA_C)
        self.assertEqual(ctx.exception.code, 409)
        self.assertFalse(any(args[0] == "push" for args in self.app.calls))

    def test_delete_of_unmerged_is_blocked(self):
        with self.assertRaises(FakeErr) as ctx:
            branch_manager.delete_remote(self.app, "owner/repo", "feature", SHA_A)
        self.assertEqual(ctx.exception.code, 409)
        self.assertFalse(self.app.deleted)

    def test_delete_uses_sha_lease(self):
        self.app.contains = ["main"]
        result = branch_manager.delete_remote(self.app, "owner/repo", "feature", SHA_A)
        self.assertTrue(result["deleted"])
        push_args = next(args for args in self.app.calls if args[0] == "push")
        self.assertIn("--force-with-lease=refs/heads/feature:" + SHA_A, push_args)
        self.assertIn(":refs/heads/feature", push_args)

    def test_remote_ref_rejection_leaves_branch_intact(self):
        self.app.contains = ["main"]
        self.app.fail_push = True
        with self.assertRaises(FakeErr):
            branch_manager.delete_remote(self.app, "owner/repo", "feature", SHA_A)
        self.assertFalse(self.app.deleted)

    def test_default_branch_cannot_be_deleted(self):
        self.app.need_branch = lambda value: value
        with self.assertRaises(FakeErr) as ctx:
            branch_manager.delete_remote(self.app, "owner/repo", "main", SHA_B)
        self.assertEqual(ctx.exception.code, 403)
        self.assertFalse(self.app.deleted)

    def test_closed_unmerged_pr_is_not_merge_evidence(self):
        self.app.closed_prs = [pr(11, merged=False)]
        self.assertEqual(self.item()["status"], "keep")

    def test_invalid_sha_is_rejected(self):
        with self.assertRaises(FakeErr):
            branch_manager.delete_remote(self.app, "owner/repo", "feature", "not-sha")

    def test_fallback_uses_default_branch_not_hardcoded_main(self):
        self.app.default = "release"
        self.app.contains = ["main"]
        self.assertEqual(self.item()["status"], "review")


if __name__ == "__main__":
    unittest.main()
