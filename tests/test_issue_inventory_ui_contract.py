"""UI contract: all issue states, full details, explicit pagination."""
from pathlib import Path
import unittest

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class IssueInventoryUIContract(unittest.TestCase):
    def test_closed_issues_visible_by_default(self):
        self.assertIn("page:'repos',showClosed:true", HTML)
        self.assertIn("S.showClosed||i.state==='open'", HTML)
        self.assertIn("Sembunyikan issue selesai", HTML)

    def test_issue_list_paginates_without_hiding_total(self):
        self.assertIn("function filteredIssues()", HTML)
        self.assertIn("function renderIssueList()", HTML)
        self.assertIn("pageSize=50", HTML)
        self.assertIn("issuePage:0", HTML)
        self.assertIn("Menampilkan ", HTML)
        self.assertIn("data-act=\"issue-prev\"", HTML)
        self.assertIn("data-act=\"issue-next\"", HTML)

    def test_issue_body_and_comments_fetch_on_demand(self):
        self.assertIn("Baca deskripsi lengkap dan komentar", HTML)
        self.assertIn("function loadIssueDetail(repo,number,box,page=1)", HTML)
        self.assertIn("/api/issue/detail?repo=", HTML)
        self.assertIn("data-issue-content hidden", HTML)
        self.assertIn("data-issue-comments", HTML)
        self.assertIn("Muat komentar berikutnya", HTML)
        self.assertIn("data.has_more_comments", HTML)

    def test_dynamic_issue_text_is_escaped_not_injected_raw(self):
        self.assertIn("esc(data.body||'(Deskripsi kosong)')", HTML)
        self.assertIn("esc(c.body||'(Komentar kosong)')", HTML)
        self.assertIn("esc(c.author||'GitHub user')", HTML)
        self.assertIn("white-space:pre-wrap;overflow-wrap:anywhere", HTML)

    def test_issue_detail_has_its_own_scroll_area(self):
        self.assertIn('data-issue-content role="region"', HTML)
        self.assertIn('aria-label="Isi lengkap issue dan komentar, gulir di dalam panel"', HTML)
        self.assertIn('tabindex="0" hidden', HTML)
        self.assertIn("max-height:min(65dvh,640px);overflow-y:auto;overflow-x:hidden", HTML)
        self.assertIn("scrollbar-gutter:stable;overscroll-behavior-y:auto", HTML)

    def test_mobile_scroll_stays_inside_reading_panel(self):
        self.assertIn("@media(max-width:760px){.issue-full-detail{max-height:min(55dvh,440px)", HTML)
        self.assertIn("-webkit-overflow-scrolling:touch", HTML)
        self.assertIn(".issue-full-detail:focus-visible", HTML)
        self.assertIn(".issue-full-body,.issue-comment pre{white-space:pre-wrap", HTML)
        self.assertNotIn(".issue-full-detail{height:100vh", HTML)

    def test_issue_search_and_filter_reset_page(self):
        self.assertIn("S.issueQuery=e.target.value;S.issuePage=0;renderIssueList()", HTML)
        self.assertIn("S.filter=c.dataset.chip;S.issuePage=0", HTML)
        self.assertIn("S.showClosed=!S.showClosed;S.issuePage=0", HTML)


if __name__ == "__main__":
    unittest.main()
