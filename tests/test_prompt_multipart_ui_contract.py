"""UI contract for oversized prompt transport and manual ChatGPT copy flow."""
from pathlib import Path
import unittest

HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class PromptMultipartUIContract(unittest.TestCase):
    def test_stepper_has_previous_next_and_copy(self):
        self.assertIn('data-act="prompt-prev"', HTML)
        self.assertIn('data-act="prompt-next"', HTML)
        self.assertIn('data-sel="[data-prompt]">Salin bagian ini', HTML)
        self.assertIn('data-prompt-step role="status"', HTML)

    def test_prompt_pack_tracks_all_parts_in_browser(self):
        self.assertIn("promptPacks:{}", HTML)
        self.assertIn("S.promptPacks[repo+'#'+n]=record", HTML)
        self.assertIn("const parts=Array.isArray(r.parts)", HTML)
        self.assertIn("record.index=Math.min(record.parts.length-1", HTML)

    def test_next_step_copies_selected_full_part(self):
        self.assertIn("renderPromptPack(box,record)", HTML)
        self.assertIn("if(step>0)copy(q('[data-prompt]'))", HTML)
        self.assertIn("find('[data-prompt]').value=record.parts[record.index]", HTML)

    def test_manual_chatgpt_receives_final_only_instruction(self):
        self.assertIn("Tempel setiap bagian secara berurutan dalam SATU percakapan ChatGPT.", HTML)
        self.assertIn("Jangan meminta ChatGPT mulai mengerjakan sebelum bagian terakhir.", HTML)
        self.assertIn("BAGIAN TERAKHIR: sesudah ditempel", HTML)

    def test_single_part_stays_compatible(self):
        self.assertIn("r.parts:[r.prompt].filter(Boolean)", HTML)
        self.assertIn("status.hidden=!multi", HTML)
        self.assertIn("prev.hidden=!multi", HTML)
        self.assertIn("next.hidden=!multi", HTML)


if __name__ == "__main__":
    unittest.main()
