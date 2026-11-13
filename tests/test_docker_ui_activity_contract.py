"""Contract checks for animated Docker QA frontend (source-only; no browser)."""
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class DockerActivityUIContract(unittest.TestCase):
    def test_qa_uses_real_api_and_indeterminate_activity(self):
        self.assertIn("api('/api/docker/qa/start','POST',", HTML)
        self.assertIn("report_on_fail:$('#docker-report-on-fail').checked", HTML)
        self.assertIn('class="progress-track" role="progressbar"', HTML)
        self.assertIn("tanpa informasi persentase", HTML)
        self.assertIn("durationLabel(", HTML)

    def test_running_qa_disables_duplicate_mutating_actions(self):
        self.assertIn("if(S.dockerOperation)return false", HTML)
        self.assertIn("Masih ada operasi Docker yang berjalan.", HTML)
        self.assertIn("startDockerOperation('qa',repo)", HTML)
        self.assertIn("finishDockerOperation(ok,title", HTML)

    def test_result_is_preserved_in_render_state(self):
        self.assertIn("S.dockerResult={kind:op.kind", HTML)
        self.assertIn("renderDockerActivity();", HTML)
        self.assertIn("id=\"docker-copy-result\"", HTML)
        self.assertIn("data-docker=\"copy-result\"", HTML)

    def test_success_failure_and_network_responses_are_distinct(self):
        self.assertIn("QA FAIL", HTML)
        self.assertIn("job.state==='failed'", HTML)
        self.assertIn("job.state==='passed'", HTML)
        self.assertIn("QA gagal dimulai", HTML)

    def test_verbose_log_polling_is_real_and_recoverable(self):
        self.assertIn("/api/docker/qa/status?repo=", HTML)
        self.assertIn("async function pollQaStatus(repo)", HTML)
        self.assertIn("S.qaLive?.log", HTML)
        self.assertIn("startQaPoll(repo)", HTML)
        self.assertIn("job.started_at", HTML)
        self.assertIn("truncated", HTML)

    def test_qa_log_does_not_claim_fake_percent(self):
        self.assertIn("tanpa informasi persentase", HTML)
        self.assertIn("Progres waktu nyata tanpa persentase buatan", HTML)
        self.assertIn("aria-label=\"Verbose log Docker QA\"", HTML)

    def test_deploy_readiness_is_sha_bound_in_ui(self):
        self.assertIn("deploy_branch:$('#docker-deploy-branch').value", HTML)
        self.assertIn("id=\"deploy-ready-label\"", HTML)
        self.assertIn("refreshDeployReadiness(repo)", HTML)
        self.assertIn("/api/deploy/readiness?repo=", HTML)
        self.assertIn("SHA belum terverifikasi", HTML)
        self.assertIn("r?.health_verified", HTML)

    def test_deploy_branch_options_are_loaded_from_github(self):
        self.assertIn("/api/deploy/branches?repo=", HTML)
        self.assertIn("async function refreshDeployBranches(repo)", HTML)
        self.assertIn("function renderDeployBranchChoices()", HTML)
        self.assertIn("data.qa_ready?'ready':'blocked'", HTML)
        self.assertIn("data.sha?' · '+data.sha.slice(0,12):' · belum tersedia'", HTML)

    def test_nonexistent_branch_needs_explicit_selection_and_save(self):
        self.assertIn(" (tidak ditemukan)", HTML)
        self.assertIn("Pilih branch dari daftar, lalu klik Simpan aplikasi", HTML)
        self.assertIn("if(!S.deployBranches?.available?.includes(branch))", HTML)
        self.assertIn("Branch belum disimpan", HTML)
        self.assertIn("Memeriksa branch GitHub...", HTML)

    def test_independent_qa_and_deploy_selectors_are_visible(self):
        self.assertIn('id="docker-qa-branch-mode"', HTML)
        self.assertIn("Branch aktif (checkout container QA)", HTML)
        self.assertIn("Branch default GitHub", HTML)
        self.assertIn("Branch untuk deploy (independen dari QA)", HTML)
        self.assertIn("qa_branch_mode:$('#docker-qa-branch-mode').value", HTML)

    def test_unsaved_branch_selections_do_not_run_operations(self):
        self.assertIn("Sumber branch QA belum disimpan", HTML)
        self.assertIn("Branch deploy belum disimpan", HTML)
        self.assertIn("function dockerAppFormDirty()", HTML)
        self.assertIn("Klik Simpan aplikasi sebelum menjalankan QA", HTML)
        self.assertIn("Klik Simpan aplikasi sebelum deploy", HTML)
        self.assertIn("S.dockerCfg?.app?.qa_branch_mode||'active'", HTML)
        self.assertIn("S.dockerCfg?.app?.deploy_branch||'main'", HTML)

    def test_qa_help_does_not_claim_automatic_checkout_switching(self):
        self.assertIn("checkout QA harus sudah berada di branch tersebut", HTML)
        self.assertIn("Jika berbeda branch, deploy hanya diizinkan bila SHA commit sama", HTML)

    def test_pr_target_and_manual_qa_issue_loop_are_present(self):
        self.assertIn("Branch tujuan PR / merge", HTML)
        self.assertIn("data-act=\"merge-pr\"", HTML)
        self.assertIn("data-act=\"pr-status\"", HTML)
        self.assertIn("data-act=\"qa-pass\"", HTML)
        self.assertIn("data-act=\"qa-fail\"", HTML)
        self.assertIn("/api/pr/merge", HTML)
        self.assertIn("/api/qa/failure", HTML)
        self.assertIn("/api/qa/pass", HTML)

    def test_qa_issue_tracking_and_fix_prompt_copy(self):
        self.assertIn('id="docker-qa-issue"', HTML)
        self.assertIn('id="docker-report-on-fail"', HTML)
        self.assertIn('id="docker-qa-follow-prompt"', HTML)
        self.assertIn("data-docker=\"copy-qa-prompt\"", HTML)
        self.assertIn("job?.followup_prompt", HTML)
        self.assertIn("Issue QA #", HTML)

    def test_full_prompt_and_target_are_sent_to_backend(self):
        self.assertIn("Buat prompt LENGKAP untuk ChatGPT", HTML)
        self.assertIn("api('/api/prompt','POST'", HTML)
        self.assertIn("const target=q('[data-pr-target]').value", HTML)

    def test_toasts_are_actionable_and_accessible(self):
        self.assertIn('id="toast-stack" aria-label="Notifikasi aktivitas"', HTML)
        self.assertIn('class="notice-close" aria-label="Tutup notifikasi"', HTML)
        self.assertIn("item.setAttribute('role',type==='error'?'alert':'status')", HTML)
        self.assertIn("item.querySelector('p').textContent=", HTML)

    def test_motion_is_nonblocking_and_accessible(self):
        self.assertIn("@keyframes qa-spin", HTML)
        self.assertIn("@keyframes qa-sweep", HTML)
        self.assertIn("@keyframes notice-in", HTML)
        self.assertIn("@media(prefers-reduced-motion:reduce){.activity-spinner", HTML)
        self.assertIn("@media(forced-colors:active){.progress-track", HTML)

    def test_cards_and_spacing_responsive(self):
        self.assertIn(".docker-grid{display:grid;grid-template-columns:repeat(2", HTML)
        self.assertIn("gap:20px;align-items:start", HTML)
        self.assertIn("@media(max-width:820px){.docker-grid", HTML)
        self.assertIn("@media(max-width:540px){.docker-panel .row button", HTML)

    def test_patch_error_toast_is_preserved(self):
        self.assertIn('id="patch-toast"', HTML)
        self.assertIn("showPatchError(error)", HTML)
        self.assertIn(".patch-toast:not([hidden]){animation:notice-in", HTML)


if __name__ == "__main__":
    unittest.main()
