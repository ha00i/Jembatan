"""Static regression checks for the patch-failure notification (stdlib only)."""

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


HTML_PATH = Path(__file__).resolve().parents[1] / "index.html"


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.setdefault(attrs["id"], []).append((tag, attrs))


class PatchToastContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.parser = IdParser()
        cls.parser.feed(cls.html)
        cls.script = re.search(r"<script>([\s\S]*?)</script>", cls.html).group(1)

    def test_toast_markup_is_present_and_unique(self):
        expected = (
            "patch-toast", "patch-toast-title", "patch-toast-message",
            "patch-toast-details", "patch-toast-log", "patch-toast-copy",
            "patch-toast-close", "patch-toast-dismiss",
        )
        for element_id in expected:
            with self.subTest(element_id=element_id):
                self.assertEqual(len(self.parser.ids.get(element_id, [])), 1)

    def test_toast_is_initially_hidden_and_accessible(self):
        tag, attrs = self.parser.ids["patch-toast"][0]
        self.assertEqual(tag, "div")  # No global 'aside' sidebar styling.
        self.assertEqual(attrs.get("role"), "alert")
        self.assertEqual(attrs.get("aria-labelledby"), "patch-toast-title")
        self.assertIn("hidden", attrs)
        self.assertIn("aria-label=\"Tutup notifikasi\"", self.html)

    def test_toast_is_fixed_in_bottom_corner(self):
        self.assertRegex(self.html, r"\.patch-toast\s*\{position:fixed;")
        self.assertIn("right:max(16px,env(safe-area-inset-right))", self.html)
        self.assertIn("bottom:max(16px,env(safe-area-inset-bottom))", self.html)
        self.assertIn("@media(max-width:540px){.patch-toast", self.html)

    def test_apply_failure_is_scoped_and_text_is_safe(self):
        self.assertIn("showPatchError(err)", self.script)
        self.assertRegex(self.script, r"git apply gagal\|patch failed")
        self.assertIn("$('#patch-toast-log').textContent=message", self.script)
        self.assertIn("$('#patch-toast-message').textContent=", self.script)
        self.assertNotIn("$('#patch-toast-log').innerHTML", self.script)
        self.assertIn("$('#patch-toast').hidden=false", self.script)
        self.assertIn("$('#patch-toast').hidden=true", self.script)

    def test_toast_copy_action_and_manual_close(self):
        self.assertIn("navigator.clipboard.writeText(latestPatchError)", self.script)
        self.assertIn("$('#patch-toast-close').onclick=dismissPatchError", self.script)
        self.assertIn("$('#patch-toast-dismiss').onclick=dismissPatchError", self.script)


if __name__ == "__main__":
    unittest.main()
