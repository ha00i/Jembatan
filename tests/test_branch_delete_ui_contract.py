"""UI contract: deleting a GitHub branch needs Hapus/Batal clicks, never typing."""
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class BranchDeleteUIContract(unittest.TestCase):
    def test_single_branch_dialog_has_only_two_explicit_actions(self):
        self.assertIn('id="branch-delete-cancel">Batal</button>', SOURCE)
        self.assertIn('id="branch-delete-confirm" class="danger-action">Hapus</button>', SOURCE)
        self.assertIn('id="branch-delete-dialog"', SOURCE)
        self.assertNotIn('id="branch-delete-input"', SOURCE)
        self.assertNotIn("Ketik nama branch persis untuk konfirmasi", SOURCE)

    def test_single_branch_deletion_uses_confirm_click_and_sha(self):
        self.assertIn("function openBranchDelete(repo, name)", SOURCE)
        self.assertIn("pendingBranchDelete={kind:'single',repo,branch:name,sha:row.sha}", SOURCE)
        self.assertIn("if(!row?.safe_to_delete)", SOURCE)
        self.assertIn("{repo:p.repo,branch:p.branch,sha:p.sha}", SOURCE)

    def test_bulk_cleanup_dialog_lists_only_verified_safe_branches(self):
        self.assertIn("function openBranchCleanup(repo)", SOURCE)
        self.assertIn("filter(x=>x.safe_to_delete)", SOURCE)
        self.assertIn("safe.map(x=>x.name).join('\\n')", SOURCE)
        self.assertIn("pendingBranchDelete={kind:'bulk'", SOURCE)
        self.assertIn("if(act==='branches-cleanup')return openBranchCleanup(repo)", SOURCE)

    def test_bulk_deletion_does_not_use_prompt_for_confirmation(self):
        self.assertIn("api('/api/branches/cleanup','POST',{repo:p.repo,confirm:p.repo})", SOURCE)
        self.assertNotIn("Ketik nama repository persis: '+repo", SOURCE)

    def test_cancel_never_invokes_api_and_escape_remains_available(self):
        self.assertIn("if(!branchDeleteBusy)$('#branch-delete-dialog').close()", SOURCE)
        self.assertIn("$('#branch-delete-dialog').oncancel=e=>{if(branchDeleteBusy)e.preventDefault()}", SOURCE)
        self.assertIn("if(!p||branchDeleteBusy||S.repo!==p.repo)return", SOURCE)
        self.assertIn("$('#branch-delete-cancel').focus()", SOURCE)

    def test_failure_does_not_claim_deletion_and_allows_retry(self):
        self.assertIn("$('#branch-delete-error').textContent=e.message", SOURCE)
        self.assertIn("branchDeleteBusy=false;", SOURCE)
        self.assertIn("btn.disabled=true;", SOURCE)
        self.assertIn("btn.textContent='Menghapus...'", SOURCE)

    def test_dialog_is_readable_on_mobile(self):
        self.assertIn("max-height:180px;overflow:auto", SOURCE)
        self.assertIn(".confirm-dialog .confirm-actions button{min-height:44px", SOURCE)
        self.assertIn("aria-labelledby=\"branch-delete-title\" aria-describedby=\"branch-delete-description\"", SOURCE)


if __name__ == "__main__":
    unittest.main()
