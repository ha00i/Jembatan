"""Compose path discovery contract — UI only; no Docker runs."""
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class ComposeDiscoveryUIContract(unittest.TestCase):
    def test_docker_form_has_detection_button_and_read_only_status(self):
        self.assertIn('data-docker="detect-compose"', HTML)
        self.assertIn("Deteksi path Compose", HTML)
        self.assertIn('id="docker-compose-candidates"', HTML)
        self.assertIn('role="status" aria-live="polite"', HTML)

    def test_click_fetches_candidates_without_saving_or_deploying(self):
        self.assertIn("if(act==='detect-compose'){", HTML)
        self.assertIn("/api/docker/compose-paths?repo=", HTML)
        self.assertIn("S.composeCandidates=found;renderComposeCandidates()", HTML)
        self.assertIn("Simpan koneksi Docker sebelum mendeteksi", HTML)

    def test_only_verified_candidates_offer_use_path(self):
        self.assertIn("function renderComposeCandidates()", HTML)
        self.assertIn("(item.verified?", HTML)
        self.assertIn('data-docker="use-compose"', HTML)
        self.assertIn("if(!item?.verified)return notify(", HTML)
        self.assertIn("S.composeCandidates.candidates.find(p=>p.path===b.dataset.composePath)", HTML)

    def test_unverified_candidates_can_be_copied_without_deploying(self):
        self.assertIn('data-docker="copy-compose"', HTML)
        self.assertIn('title="Salin path yang belum terverifikasi"', HTML)
        self.assertIn("if(act==='copy-compose'){", HTML)
        self.assertIn("const copied=await copy(field)", HTML)
        self.assertIn("periksa mount/SSH sebelum digunakan.", HTML)
        self.assertIn("if(!item?.verified)return notify(", HTML)

    def test_selecting_path_requires_save_app_before_deploy(self):
        self.assertIn("$('#docker-deploy-path').value=item.path;", HTML)
        self.assertIn("Path belum disimpan", HTML)
        self.assertIn("Klik Simpan aplikasi", HTML)
        self.assertIn("function dockerAppFormDirty()", HTML)
        self.assertIn("Klik Simpan aplikasi sebelum deploy", HTML)

    def test_unsafe_remote_path_never_becomes_trusted_html(self):
        self.assertIn("esc(item.path)", HTML)
        self.assertIn("esc(item.source||'Docker')", HTML)
        self.assertIn("esc(item.reason||'Periksa konfigurasi')", HTML)
        self.assertIn("esc(msg)", HTML)

    def test_responsive_candidates_and_no_background_polling(self):
        self.assertIn(".compose-candidate-info{display:flex", HTML)
        self.assertIn("@media(max-width:540px){.compose-candidate", HTML)
        self.assertNotIn("setInterval(()=>api('/api/docker/compose-paths", HTML)

    def test_changing_connection_clears_previous_candidates(self):
        self.assertIn("S.composeCandidates=null;drawDocker()", HTML)
        self.assertIn("S.composeCandidates=null;", HTML)


if __name__ == "__main__":
    unittest.main()
