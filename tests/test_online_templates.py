import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import online_templates


class OnlineTemplatesTests(unittest.TestCase):
    def test_sanitize_slug_rejects_traversal_and_invalid(self):
        self.assertEqual(online_templates.sanitize_slug("Classic-CV_1"), "classic-cv_1")
        for bad in ("", "../evil", "a/b", "a\\b", "bad slug", "x" * 81):
            with self.assertRaises(ValueError):
                online_templates.sanitize_slug(bad)

    def test_save_list_and_delete_downloaded_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            item = {
                "id": "tpl-1",
                "slug": "modern-cv",
                "category": "cv",
                "name": "Modern CV",
                "description": "Clean two-column CV",
                "version": 2,
                "definition": {"layout": "two_column", "accent": "#1d4ed8"},
            }
            saved = online_templates.save_downloaded_template(item, base_dir=base)
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.parent.name, "cv")

            listed = online_templates.list_downloaded_templates(base_dir=base)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["slug"], "modern-cv")
            self.assertEqual(listed[0]["version"], 2)

            self.assertTrue(online_templates.delete_downloaded_template("cv", "modern-cv", base_dir=base))
            self.assertFalse(online_templates.delete_downloaded_template("cv", "modern-cv", base_dir=base))
            self.assertEqual(online_templates.list_downloaded_templates(base_dir=base), [])

    def test_fetch_online_templates_queries_supabase_rest(self):
        mock_resp = Mock()
        mock_resp.json.return_value = [
            {
                "id": "1",
                "slug": "flyer-a5",
                "category": "card",
                "name": "A5 Flyer",
                "description": "Promo card",
                "version": 1,
                "preview_url": "",
                "definition": {"width_mm": 148, "height_mm": 210},
            }
        ]
        mock_resp.raise_for_status.return_value = None

        with patch("online_templates.requests.get", return_value=mock_resp) as get_mock:
            rows = online_templates.fetch_online_templates(
                category="card",
                supabase_url="https://example.supabase.co",
                anon_key="anon-key",
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["slug"], "flyer-a5")
            _, kwargs = get_mock.call_args
            self.assertEqual(kwargs["params"]["category"], "eq.card")
            self.assertEqual(kwargs["params"]["is_active"], "eq.true")


if __name__ == "__main__":
    unittest.main()
