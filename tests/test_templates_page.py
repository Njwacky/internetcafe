"""Regression tests for the public templates gallery (templates.html).

The gallery reads the same Supabase `templates` table as the desktop app
(online_templates.py) and the owner dashboard. These tests pin the wiring
so a future edit cannot silently break the page or leak the service key.
"""

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TemplatesPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (REPO / "templates.html").read_text(encoding="utf-8")
        cls.vercel = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))

    def test_page_exists_and_reads_live_catalogue(self):
        self.assertIn("/rest/v1/templates", self.page)
        self.assertIn("is_active", self.page)
        self.assertIn("eq.true", self.page)
        # Same public project as the desktop app + owner dashboard.
        self.assertIn("rmtkppzqzxuuuhbeufjy.supabase.co", self.page)

    def test_page_covers_all_desktop_categories(self):
        # Read VALID_CATEGORIES from source so this test needs no deps.
        src = (REPO / "online_templates.py").read_text(encoding="utf-8")
        for cat in ("cv", "card", "form", "collage"):
            self.assertIn(f'"{cat}"', src)
            self.assertIn(f'data-cat="{cat}"', self.page)

    def test_vercel_rewrites_templates_to_page(self):
        rewrites = self.vercel.get("rewrites", [])
        pairs = {(r.get("source"), r.get("destination")) for r in rewrites}
        self.assertIn(("/templates", "/templates.html"), pairs)
        self.assertIn(("/templates/", "/templates.html"), pairs)

    def test_page_never_embeds_service_role_key(self):
        self.assertNotIn("service_role", self.page)
        self.assertNotIn("service-role", self.page)

    def test_page_renders_db_values_safely(self):
        # Cards must be built with textContent, not innerHTML interpolation.
        self.assertIn("textContent", self.page)
        self.assertNotIn("innerHTML", self.page)
        # Preview URLs must be https-only before becoming an <img> src.
        self.assertIn("isSafeHttpsUrl", self.page)
        self.assertIn('"https:"', self.page)

    def test_download_page_links_to_gallery(self):
        download = (REPO / "download.html").read_text(encoding="utf-8")
        self.assertIn('href="/templates"', download)


if __name__ == "__main__":
    unittest.main()
