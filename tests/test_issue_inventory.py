"""Read the complete issue inventory and full issue detail without silent truncation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge


class IssueInventoryTests(unittest.TestCase):
    def test_pagination_fetches_beyond_old_three_page_limit(self):
        pages = []
        for page in range(1, 6):
            if page < 5:
                pages.append([{"id": (page - 1) * 100 + i} for i in range(100)])
            else:
                pages.append([{"id": 400 + i} for i in range(37)])
        with patch.object(bridge, "gh", side_effect=pages) as fetch:
            result = bridge.gh_pages("/repos/owner/repo/issues?state=all",
                                     limit=100, require_complete=True)
        self.assertEqual(len(result), 437)
        self.assertEqual(fetch.call_count, 5)
        self.assertIn("per_page=100&page=5", fetch.call_args.args[0])

    def test_page_limit_is_explicit_error_not_silent_cutoff(self):
        with patch.object(bridge, "gh",
                          side_effect=[[{"id": i} for i in range(100)],
                                       [{"id": 101}]]) as fetch:
            with self.assertRaisesRegex(bridge.Err, "tidak dipotong diam-diam"):
                bridge.gh_pages("/repos/owner/repo/issues", limit=1,
                                require_complete=True)
        self.assertEqual(fetch.call_count, 2)

    def test_issue_inventory_keeps_open_and_closed_and_excludes_prs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            raw = []
            for number in range(1, 421):
                item = {"number": number, "title": "Issue " + str(number),
                        "html_url": "https://github.com/example/repo/issues/" + str(number),
                        "state": "closed" if number % 2 == 0 else "open"}
                if number % 10 == 0:
                    item["pull_request"] = {"url": "https://example.test/pr"}
                raw.append(item)
            with patch.object(bridge, "repo_dir", return_value=root):
                with patch.object(bridge, "default_branch", return_value="main"):
                    with patch.object(bridge, "branches", return_value=[]):
                        with patch.object(bridge, "git", return_value=""):
                            with patch.object(bridge, "gh_pages",
                                              return_value=raw) as pages:
                                result = bridge.overview("owner/repo")
        self.assertEqual(result["issue_count"], 378)
        self.assertEqual(result["open_count"], 210)
        self.assertEqual(result["closed_count"], 168)
        self.assertEqual(len(result["issues"]), 378)
        self.assertEqual(result["issues"][-1]["number"], 419)
        self.assertEqual(pages.call_args.kwargs,
                         {"limit": 100, "require_complete": True})

    def test_issue_detail_preserves_large_body_and_paginates_comments(self):
        body = "Complete acceptance criteria\n" + "ABC" * 100000
        issue = {"number": 15, "state": "open", "title": "Long issue",
                 "body": body, "comments": 101,
                 "html_url": "https://github.com/owner/repo/issues/15"}
        first = [{"id": i, "body": "Full comment " + str(i),
                  "user": {"login": "writer"}, "created_at": "2026-11-13"}
                 for i in range(100)]
        with patch.object(bridge, "gh", side_effect=[issue, first, issue,
                                                       [{"id": 100, "body": "Last comment"}]]) as fetch:
            one = bridge.issue_detail("owner/repo", 15)
            two = bridge.issue_detail("owner/repo", 15, comments_page=2)
        self.assertEqual(one["body"], body)
        self.assertEqual(len(one["comments"]), 100)
        self.assertTrue(one["has_more_comments"])
        self.assertEqual(len(two["comments"]), 1)
        self.assertFalse(two["has_more_comments"])
        self.assertEqual(fetch.call_count, 4)

    def test_issue_detail_rejects_pr_and_bad_id(self):
        with patch.object(bridge, "gh",
                          return_value={"pull_request": {"url": "https://example.test"}}):
            with self.assertRaisesRegex(bridge.Err, "PR"):
                bridge.issue_detail("owner/repo", 14)
        with self.assertRaises(bridge.Err):
            bridge.issue_detail("owner/repo", "../14")
        with self.assertRaises(bridge.Err):
            bridge.issue_detail("owner/repo", 1, comments_page=-1)

    def test_issue_detail_route_available(self):
        with patch.object(bridge, "issue_detail", return_value={"number": 2}) as fn:
            value = bridge.ROUTES[("GET", "/api/issue/detail")](
                {"repo": "owner/repo", "issue": "2", "comments_page": "3"}, {})
        self.assertEqual(value["number"], 2)
        fn.assert_called_once_with("owner/repo", "2", "3")


if __name__ == "__main__":
    unittest.main()
