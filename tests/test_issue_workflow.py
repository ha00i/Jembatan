"""No real GitHub, Docker, merge or QA operations: mocked workflow tests."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import bridge

COMPLETE = (
    "STATUS: SELESAI\nKEKURANGAN:\n- tidak ada\n"
    "PERUBAHAN:\n- Semua permintaan diselesaikan\n"
    "VERIFIKASI:\n- belum dijalankan\n"
    "PATCH:\n```diff\n"
    "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
)


class PromptContract(TestCase):
    def setUp(self):
        rp = patch.object(bridge, "repo_dir", return_value=Path("/tmp/mock-repo"))
        base = patch.object(bridge, "default_branch", return_value="main")
        issue = patch.object(bridge, "gh", return_value={
            "number": 11, "title": "Complete UI", "body": "B" * 6000})
        for control in (rp, base, issue):
            control.start()
            self.addCleanup(control.stop)

    @staticmethod
    def git_stub(d, *args, **kw):
        if args[0] == "rev-parse":
            return "a" * 40
        if args[0] == "ls-tree":
            return "app.py\nREADME.md"
        if args[0] == "diff":
            return "D" * 32000
        if args[0] == "show":
            return "F" * 16000
        if args[0] == "status":
            return ""
        raise AssertionError("Unexpected Git op")

    def test_prompt_does_not_truncate_issue_diff_or_files(self):
        with patch.object(bridge, "git", side_effect=self.git_stub):
            result = bridge.build_prompt("ha00i/Jembatan", 11,
                                         branch="release/v2", target="release/v2",
                                         files=["app.py"])
        for fragment in ("B" * 6000, "D" * 32000, "F" * 16000):
            self.assertIn(fragment, result["prompt"])
        self.assertTrue(result["complete_context"])

    def test_large_context_fails_explicitly(self):
        def large(d, *args, **kw):
            if args[0] == "show":
                return "X" * 130000
            return self.git_stub(d, *args, **kw)
        with patch.object(bridge, "git", side_effect=large):
            with self.assertRaisesRegex(bridge.Err, "tidak memotong"):
                bridge.build_prompt("ha00i/Jembatan", 11, files=["app.py"])

    def test_missing_file_fails(self):
        with patch.object(bridge, "git", side_effect=self.git_stub):
            with self.assertRaisesRegex(bridge.Err, "tidak ditemukan"):
                bridge.build_prompt("ha00i/Jembatan", 11, files=["missing.py"])

    def test_complete_reply_is_accepted(self):
        bridge.require_complete_reply(COMPLETE)
        self.assertIn("+++ b/a.py", bridge.extract_patch(COMPLETE))

    def test_partial_incomplete_and_missing_files_are_rejected(self):
        for reply in (
            COMPLETE.replace("STATUS: SELESAI", "STATUS: SEBAGIAN"),
            COMPLETE.replace("- tidak ada", "- Menu belum dibuat"),
            "BUTUH FILE: src/other.py\n" + COMPLETE,
            COMPLETE.replace("```diff\n", ""),
            COMPLETE.replace("```\n", ""),
        ):
            with self.subTest(reply=reply[:50]):
                with self.assertRaises(bridge.Err):
                    bridge.require_complete_reply(reply)

    def test_partial_reply_never_checks_out_branch(self):
        with patch.object(bridge, "git", side_effect=self.git_stub) as git:
            with self.assertRaisesRegex(bridge.Err, "SELESAI"):
                bridge.apply_patch("ha00i/Jembatan", 11,
                    COMPLETE.replace("STATUS: SELESAI", "STATUS: SEBAGIAN"),
                    target="main")
        self.assertFalse(any("checkout" in c.args for c in git.call_args_list))


class PullRequestContract(TestCase):
    def setUp(self):
        rp = patch.object(bridge, "repo_dir", return_value=Path("/tmp/mock-repo"))
        rp.start()
        self.addCleanup(rp.stop)

    def test_pr_target_is_release_branch_not_default(self):
        sha = "a" * 40
        def github(route, method="GET", body=None):
            if "/branches/" in route:
                return {"name": "release/v2"}
            if method == "POST":
                self.assertEqual(body["base"], "release/v2")
                self.assertIn("Refs #11", body["body"])
                self.assertNotIn("Closes #11", body["body"])
                return {"html_url": "https://github.com/ha00i/Jembatan/pull/22",
                        "number": 22, "head": {"sha": sha}}
            raise AssertionError(route)
        with patch.object(bridge, "gh", side_effect=github) as api:
            with patch.object(bridge, "gh_pages", return_value=[]):
                with patch.object(bridge, "git", return_value="ok"):
                    result = bridge.push("ha00i/Jembatan", "bridge/issue-11",
                                         11, "release/v2")
        self.assertEqual(result["base"], "release/v2")
        self.assertTrue(result["qa_required"])
        self.assertIn("release%2Fv2", api.call_args_list[0].args[0])

    def test_merge_denies_changed_head_sha(self):
        pr = {"state": "open", "head": {"ref": "bridge/issue-11",
              "sha": "b" * 40}, "base": {"ref": "release/v2"}, "mergeable": True}
        with patch.object(bridge, "gh", return_value=pr) as api:
            with self.assertRaisesRegex(bridge.Err, "SHA PR berubah"):
                bridge.merge_issue_pr("ha00i/Jembatan", 11, 22, "release/v2", "a" * 40)
        self.assertEqual(api.call_count, 1)

    def test_confirmed_merge_still_requires_manual_qa(self):
        sha = "a" * 40
        pr = {"state": "open", "head": {"ref": "bridge/issue-11",
              "sha": sha}, "base": {"ref": "release/v2"}, "mergeable": True}
        with patch.object(bridge, "gh", side_effect=[pr, {"merged": True, "sha": "c" * 40}]) as api:
            result = bridge.merge_issue_pr("ha00i/Jembatan", 11, 22, "release/v2", sha)
        self.assertTrue(result["qa_required"])
        self.assertEqual(api.call_args_list[-1].args[1], "PUT")


class QAFeedbackContract(TestCase):
    def setUp(self):
        rp = patch.object(bridge, "repo_dir", return_value=Path("/tmp/mock-repo"))
        rp.start()
        self.addCleanup(rp.stop)

    def test_repair_prompt_includes_full_verified_source_and_error(self):
        issue = {"number": 11, "title": "Login error",
                 "body": "Perbaiki app/login.py serta test login"}
        head = "a" * 40
        def fake_git(d, *args, **kwargs):
            if args[0] == "rev-parse":
                return head
            if args[0] == "ls-tree":
                return "app/login.py\\nREADME.md"
            if args[0] == "show":
                self.assertEqual(args[1], "origin/main:app/login.py")
                return "def login():\\n    return False\\n"
            raise AssertionError(args)
        with patch.object(bridge, "default_branch", return_value="main"):
            with patch.object(bridge, "git", side_effect=fake_git):
                with patch.object(bridge, "gh",
                                  return_value={"commit": {"sha": head}}):
                    prompt = bridge.qa_followup_prompt(
                        "ha00i/Jembatan", issue,
                        "FAIL app/login.py test_login", "main")
        self.assertIn("SOURCE UTUH app/login.py", prompt)
        self.assertIn("def login():", prompt)
        self.assertIn("FAIL app/login.py", prompt)
        self.assertIn("STATUS: SELESAI", prompt)
        self.assertNotIn("README.md\\n~~~~text", prompt)

    def test_repair_prompt_marks_stale_or_missing_source_explicitly(self):
        issue = {"number": 11, "title": "Regression", "body": "Bug main.py"}
        with patch.object(bridge, "default_branch", return_value="main"):
            with patch.object(bridge, "git", side_effect=bridge.Err("not synced")):
                prompt = bridge.qa_followup_prompt(
                    "ha00i/Jembatan", issue, "FAIL main.py", "main")
        self.assertIn("Konteks source belum tersedia:", prompt)
        self.assertIn("BUTUH FILE:", prompt)
        self.assertNotIn("SOURCE UTUH main.py", prompt)

    def test_repair_prompt_refuses_oversize_instead_of_silent_truncation(self):
        issue = {"number": 11, "title": "Big issue", "body": "Y" * 128000}
        with patch.object(bridge, "qa_followup_source_context",
                          return_value=("Belum ada snapshot", ["source kurang"])):
            with self.assertRaisesRegex(bridge.Err, "melebihi batas aman"):
                bridge.qa_followup_prompt("ha00i/Jembatan", issue, "FAIL", "main")

    def test_closed_issue_reopens_with_error_and_complete_prompt(self):
        closed = {"number": 11, "state": "closed", "title": "Full issue",
                  "body": "Fitur A, B dan C", "html_url": "https://example/issues/11"}
        with patch.object(bridge, "gh", side_effect=[
                closed, dict(closed, state="open"), {"id": 1}]) as api:
            with patch.object(bridge, "gh_pages", return_value=[]):
                result = bridge.report_qa_failure("ha00i/Jembatan", 11, "FAIL: test_login")
        self.assertTrue(result["reopened"])
        self.assertIn("FAIL: test_login", result["prompt"])
        self.assertIn("Fitur A, B dan C", result["prompt"])
        self.assertEqual(api.call_args_list[1].args[1], "PATCH")

    def test_tracker_reuses_existing_issue_instead_of_creating_new(self):
        issue = {"number": 50, "state": "open", "title": "QA tracker",
                 "body": bridge.QA_TRACKER, "html_url": "https://example/issues/50"}
        with patch.object(bridge, "gh_pages", side_effect=[[issue], []]):
            with patch.object(bridge, "gh", return_value={"id": 5}) as github:
                result = bridge.report_qa_failure("ha00i/Jembatan", None, "FAIL")
        self.assertFalse(result["created"])
        self.assertEqual(result["issue"], 50)
        self.assertEqual(github.call_count, 1)

    def test_qa_pass_needs_merged_pr_except_for_tracker(self):
        open_issue = {"number": 11, "state": "open", "body": "Issue"}
        with patch.object(bridge, "gh", return_value=open_issue) as github:
            with patch.object(bridge, "issue_pr_status", return_value={"merged": False}):
                with self.assertRaisesRegex(bridge.Err, "belum memiliki PR merged"):
                    bridge.complete_qa_issue("ha00i/Jembatan", 11, True)
        self.assertEqual(github.call_count, 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
