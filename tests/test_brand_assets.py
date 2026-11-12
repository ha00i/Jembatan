"""Brand assets are bundled, accessible, and served from a fixed allowlist."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
BRIDGE = (ROOT / "bridge.py").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


class BrandAssetsTest(TestCase):
    def test_svg_files_exist_and_have_valid_xml(self):
        for name in ("jembatan-mark.svg", "jembatan-lockup.svg"):
            with self.subTest(name=name):
                path = ROOT / "assets" / name
                self.assertTrue(path.is_file())
                root = ET.parse(path).getroot()
                self.assertTrue(root.tag.endswith("svg"))
                self.assertIn("viewBox", root.attrib)

    def test_assets_are_standalone_and_do_not_embed_external_resources(self):
        for name in ("jembatan-mark.svg", "jembatan-lockup.svg"):
            text = (ROOT / "assets" / name).read_text(encoding="utf-8")
            self.assertNotIn("<script", text)
            self.assertNotIn("http://", text.replace("http://www.w3.org/2000/svg", ""))
            self.assertNotIn("https://", text)
            self.assertNotIn("<foreignObject", text)

    def test_brand_is_used_in_sidebar_header_welcome_and_favicon(self):
        self.assertIn('<link rel="icon" type="image/svg+xml" href="/assets/jembatan-mark.svg">', HTML)
        self.assertIn('<img src="/assets/jembatan-mark.svg" alt=""', HTML)
        self.assertIn('<img src="/assets/jembatan-lockup.svg" alt="Jembatan', HTML)
        self.assertIn("Jembatan — Development Control", HTML)

    def test_brand_routes_use_fixed_allowlist(self):
        self.assertIn('"/assets/jembatan-mark.svg": "jembatan-mark.svg"', BRIDGE)
        self.assertIn('"/assets/jembatan-lockup.svg": "jembatan-lockup.svg"', BRIDGE)
        self.assertIn('u.path in brand_assets', BRIDGE)
        self.assertNotIn('(HERE / u.path)', BRIDGE)

    def test_logo_requests_work_without_api_token(self):
        import bridge
        for name in ("jembatan-mark.svg", "jembatan-lockup.svg"):
            with self.subTest(name=name):
                fake = SimpleNamespace(path="/assets/" + name, reply=Mock())
                bridge.H.handle_any(fake, "GET")
                code, data, mime = fake.reply.call_args.args
                self.assertEqual(code, 200)
                self.assertIn("image/svg+xml", mime)
                self.assertTrue(data.startswith(b"<svg"))

    def test_unknown_asset_is_not_exposed(self):
        import bridge
        fake = SimpleNamespace(path="/assets/../bridge.py", reply=Mock())
        bridge.H.handle_any(fake, "GET")
        self.assertEqual(fake.reply.call_args.args[0], 404)

    def test_docker_image_contains_assets(self):
        self.assertIn("COPY assets/ ./assets/", DOCKERFILE)

    def test_rebrand_respects_motion_and_mobile(self):
        self.assertIn("prefers-reduced-motion:reduce", HTML)
        self.assertIn("@media(max-width:760px)", HTML)
        self.assertIn("Jembatan brand system", HTML)


if __name__ == "__main__":
    import unittest
    unittest.main()
