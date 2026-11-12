"""Contract checks for animated Docker QA frontend (source-only; no browser)."""
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class DockerActivityUIContract(unittest.TestCase):
    def test_qa_uses_real_api_and_indeterminate_activity(self):
        self.assertIn("api('/api/docker/qa','POST',{repo})", HTML)
        self.assertIn('class="progress-track" role="progressbar"', HTML)
        self.assertIn("tanpa informasi persentase", HTML)
        self.assertIn("durationLabel(", HTML)

    def test_running_qa_disables_duplicate_mutating_actions(self):
        self.assertIn("if(S.dockerOperation)return false", HTML)
        self.assertIn("Masih ada operasi Docker yang berjalan.", HTML)
        self.assertIn("startDockerOperation('qa',repo)", HTML)
        self.assertIn("finishDockerOperation(!!r?.ok", HTML)

    def test_result_is_preserved_in_render_state(self):
        self.assertIn("S.dockerResult={kind:op.kind", HTML)
        self.assertIn("renderDockerActivity();", HTML)
        self.assertIn("id=\"docker-copy-result\"", HTML)
        self.assertIn("data-docker=\"copy-result\"", HTML)

    def test_success_failure_and_network_responses_are_distinct(self):
        self.assertIn("QA belum lulus", HTML)
        self.assertIn("QA COMMAND FAIL", HTML)
        self.assertIn("QA COMMAND PASS", HTML)
        self.assertIn("Permintaan QA gagal. Lihat notifikasi", HTML)

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
