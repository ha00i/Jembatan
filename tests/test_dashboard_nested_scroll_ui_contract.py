"""Dashboard contract: compact viewport, nested issue/branch scroll and focus mode.

These are source-level regression tests; runtime browser verification remains separate.
"""
from pathlib import Path
import unittest

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class DashboardNestedScrollContract(unittest.TestCase):
    def test_desktop_app_has_fixed_viewport_and_main_content_does_not_scroll_page(self):
        self.assertIn("@media(min-width:761px)", HTML)
        self.assertIn("html,body{height:100%;overflow:hidden}", HTML)
        self.assertIn(".app{height:100dvh;min-height:0;overflow:hidden", HTML)
        self.assertIn(".app>main{display:flex;flex-direction:column", HTML)
        self.assertIn("min-height:0;flex:1;display:flex;flex-direction:column;overflow:hidden", HTML)

    def test_sidebar_repository_list_is_independently_scrollable(self):
        self.assertIn(".app>aside #repos{min-height:0;flex:1;overflow-y:auto", HTML)
        self.assertIn("overscroll-behavior:contain", HTML)

    def test_issue_list_has_its_own_scroll_and_is_keyboard_accessible(self):
        self.assertIn('id="work-items" role="region"', HTML)
        self.assertIn('aria-label="Daftar issue, gulir di dalam panel" tabindex="0"', HTML)
        self.assertIn(".app #view.repo-workspace #work-items{flex:1;min-height:90px;overflow-y:auto", HTML)
        self.assertIn(".issue-workspace #work-items{display:flex;flex-direction:column", HTML)
        self.assertIn("#work-items:focus-visible", HTML)

    def test_issue_and_branch_management_have_separate_tabs_and_panels(self):
        self.assertIn('data-act="workspace-issues"', HTML)
        self.assertIn('data-act="workspace-branches"', HTML)
        self.assertIn('id="issue-pane" class="issue-workspace"', HTML)
        self.assertIn('id="branch-pane" class="branch-workspace"', HTML)
        self.assertIn("function setRepoPanel(which)", HTML)
        self.assertIn("branches.hidden=S.repoPanel!=='branches'", HTML)
        self.assertIn("if(act==='workspace-issues'||act==='workspace-branches')", HTML)
        self.assertIn('class="branch-panel"', HTML)

    def test_branch_tab_count_updates_after_async_inventory(self):
        self.assertIn("'[data-act=\"workspace-branches\"] .switch-count'", HTML)
        self.assertIn("count.textContent=String(S.branchData?.branches?.length??'…')", HTML)

    def test_filters_and_pagination_remain_above_scrollable_list(self):
        self.assertIn('class="workbar"', HTML)
        self.assertIn('class="chips" aria-label="Filter status issue"', HTML)
        self.assertIn('class="issue-pagination"', HTML)
        self.assertIn(".app #view.repo-workspace .chips{flex-wrap:nowrap;overflow-x:auto", HTML)
        self.assertIn("pageSize=50", HTML)

    def test_focus_toggle_expands_list_without_repainting_issue_cards(self):
        self.assertIn('data-act="toggle-list-focus"', HTML)
        self.assertIn("S.listFocus=!S.listFocus", HTML)
        self.assertIn("classList.toggle('is-list-focused',S.listFocus)", HTML)
        self.assertIn("b.textContent=S.listFocus?'Tampilkan ringkasan':'Fokus daftar'", HTML)
        self.assertIn("is-list-focused>.metrics{display:none}", HTML)

    def test_nested_issue_description_scroll_remains_available(self):
        self.assertIn("max-height:min(48dvh,450px);overscroll-behavior-y:contain", HTML)
        self.assertIn('data-issue-content role="region"', HTML)
        self.assertIn("max-height:min(65dvh,640px);overflow-y:auto", HTML)

    def test_mobile_keeps_page_usable_and_list_scrolled(self):
        self.assertIn("@media(max-width:760px)", HTML)
        self.assertIn("max-height:min(64dvh,610px);overflow-y:auto", HTML)
        self.assertIn("max-height:min(72dvh,690px);overflow-y:auto", HTML)
        self.assertIn("-webkit-overflow-scrolling:touch", HTML)

    def test_merge_summary_follows_selected_repository_default_branch(self):
        self.assertIn("Diduga masuk ${esc(d.base)}", HTML)
        self.assertNotIn("Diduga sudah di main", HTML)

    def test_qa_and_pr_actions_are_not_removed_by_rebranding(self):
        self.assertIn('data-act="prompt"', HTML)
        self.assertIn('data-act="push"', HTML)
        self.assertIn('data-act="merge-pr"', HTML)
        self.assertIn('data-act="qa-fail"', HTML)
        self.assertIn("$('#nav-docker').onclick=()=>setPage('docker')", HTML)


if __name__ == "__main__":
    unittest.main()
