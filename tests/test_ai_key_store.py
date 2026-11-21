"""Tests for the optional encrypted "remember AI key" storage."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import ai_key_store as store  # noqa: E402

KEY = "gsk_TestKeyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789a1b2"
PIN = "482915"


class AiKeyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / store.FILE_NAME
        # Run the portable (PIN-only) path by default; DPAPI is covered with fakes below.
        patcher = mock.patch.object(store, "os_protection_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_with_correct_pin(self):
        store.save_key(KEY, PIN, provider="Groq (api.groq.com)", model="openai/gpt-oss-120b", path=self.path)
        data = store.load_key(PIN, path=self.path)
        self.assertEqual(data["api_key"], KEY)
        self.assertEqual(data["provider"], "Groq (api.groq.com)")
        self.assertEqual(data["model"], "openai/gpt-oss-120b")

    def test_file_never_contains_the_key_or_pin(self):
        store.save_key(KEY, PIN, path=self.path)
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(KEY, raw)
        self.assertNotIn(KEY[4:20], raw)
        self.assertNotIn(PIN, raw)
        info = store.saved_key_info(self.path)
        self.assertEqual(info["hint"], "gsk_…a1b2")
        self.assertEqual(info["protection"], "pin")

    def test_wrong_pin_is_rejected(self):
        store.save_key(KEY, PIN, path=self.path)
        with self.assertRaisesRegex(store.SavedKeyError, "PIN"):
            store.load_key("000000", path=self.path)

    def test_tampered_file_is_rejected(self):
        store.save_key(KEY, PIN, path=self.path)
        obj = json.loads(self.path.read_text())
        ct = bytearray(store.base64.b64decode(obj["ciphertext"]))
        ct[0] ^= 0xFF
        obj["ciphertext"] = store.base64.b64encode(bytes(ct)).decode()
        self.path.write_text(json.dumps(obj))
        with self.assertRaises(store.SavedKeyError):
            store.load_key(PIN, path=self.path)

    def test_switching_protection_label_is_detected(self):
        """The protection mode is authenticated, so a downgrade edit fails."""
        store.save_key(KEY, PIN, path=self.path)
        obj = json.loads(self.path.read_text())
        obj["protection"] = "dpapi+pin"
        self.path.write_text(json.dumps(obj))
        with self.assertRaises(store.SavedKeyError):
            store.load_key(PIN, path=self.path)

    def test_damaged_file_reports_damage_without_crashing(self):
        self.path.write_text("{not json")
        self.assertTrue(store.saved_key_info(self.path)["damaged"])
        with self.assertRaises(store.SavedKeyError):
            store.load_key(PIN, path=self.path)

    def test_nothing_saved_returns_none(self):
        self.assertIsNone(store.saved_key_info(self.path))
        self.assertIsNone(store.load_key(PIN, path=self.path))

    def test_rewrap_moves_key_to_new_pin(self):
        store.save_key(KEY, PIN, model="m", path=self.path)
        self.assertTrue(store.rewrap_for_new_pin(PIN, "731406", path=self.path))
        self.assertEqual(store.load_key("731406", path=self.path)["api_key"], KEY)
        with self.assertRaises(store.SavedKeyError):
            store.load_key(PIN, path=self.path)
        self.assertFalse(store.rewrap_for_new_pin("999999", "111111", path=self.path))

    def test_forget_deletes_the_file(self):
        store.save_key(KEY, PIN, path=self.path)
        self.assertTrue(store.forget_key(self.path))
        self.assertFalse(self.path.exists())
        self.assertFalse(store.forget_key(self.path))

    def test_invalid_keys_are_refused(self):
        for bad in ("", "   ", "gsk_abc def", "x" * (store.MAX_KEY_LENGTH + 1), "gsk_\u00e9\u00e9\u00e9\u00e9\u00e9"):
            with self.assertRaises(ValueError):
                store.save_key(bad, PIN, path=self.path)
        with self.assertRaises(ValueError):
            store.save_key(KEY, "", path=self.path)

    def test_hints(self):
        self.assertEqual(store.key_hint("sk-proj-abcdefghijklmnop1234"), "sk-…1234")
        self.assertEqual(store.key_hint("AIzaSyA1234567890abcdWXYZ"), "AIza…WXYZ")
        self.assertEqual(store.key_hint("short"), "…")


class WindowsProtectionTests(unittest.TestCase):
    """DPAPI layer, simulated with a reversible fake so it runs on any OS."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / store.FILE_NAME

    def tearDown(self):
        self.tmp.cleanup()

    def _fake(self, data):
        return bytes(b ^ 0x5A for b in data)

    def test_dpapi_layer_round_trip(self):
        with mock.patch.object(store, "os_protection_available", return_value=True), \
             mock.patch.object(store, "_os_protect", side_effect=self._fake) as protect, \
             mock.patch.object(store, "_os_unprotect", side_effect=self._fake) as unprotect:
            store.save_key(KEY, PIN, path=self.path)
            self.assertEqual(store.saved_key_info(self.path)["protection"], "dpapi+pin")
            self.assertEqual(store.load_key(PIN, path=self.path)["api_key"], KEY)
            protect.assert_called_once()
            unprotect.assert_called_once()

    def test_copied_to_another_computer_is_refused(self):
        with mock.patch.object(store, "os_protection_available", return_value=True), \
             mock.patch.object(store, "_os_protect", side_effect=self._fake):
            store.save_key(KEY, PIN, path=self.path)
        # Another Windows account / PC: DPAPI cannot decrypt.
        with mock.patch.object(store, "os_protection_available", return_value=True), \
             mock.patch.object(store, "_os_unprotect", side_effect=OSError("nope")):
            with self.assertRaisesRegex(store.SavedKeyError, "another computer"):
                store.load_key(PIN, path=self.path)
        # A non-Windows machine.
        with mock.patch.object(store, "os_protection_available", return_value=False):
            with self.assertRaisesRegex(store.SavedKeyError, "Windows"):
                store.load_key(PIN, path=self.path)


class IntegrationTests(unittest.TestCase):
    def test_key_file_is_separate_from_settings_and_exports(self):
        import settings_store

        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {"CAFE_DESK_PORTABLE_DIR": d}), \
             mock.patch.object(store, "os_protection_available", return_value=False):
            settings_store.set_pin(PIN)
            store.save_key(KEY, PIN)
            self.assertEqual(store.key_path(), Path(d) / store.FILE_NAME)
            self.assertNotIn(KEY, settings_store.settings_path().read_text())
            out = Path(d).parent / (Path(d).name + "-export.cafeshop.json")
            try:
                settings_store.export_shop_profile_pack(out)
                exported = out.read_text()
                self.assertNotIn(KEY, exported)
                self.assertNotIn("ciphertext", exported)
            finally:
                out.unlink(missing_ok=True)

    def test_app_wiring(self):
        src = (REPO / "app.py").read_text(encoding="utf-8")
        # Unlock loads the saved key; locking still clears it from memory.
        self.assertIn("self._load_saved_ai_key(pin)", src)
        start = src.index("    def lock_staff(self):")
        lock_body = src[start:src.index("\n    def ", start + 10)]
        self.assertIn("self.key.set('')", lock_body)
        # Saving asks for and verifies the PIN first; PIN change re-encrypts.
        self.assertIn("settings_store.verify_pin(pin): return messagebox.showerror('Staff access','Incorrect PIN. The key was not saved.'", src)
        self.assertIn("ai_key_store.rewrap_for_new_pin(old,new)", src)


if __name__ == "__main__":
    unittest.main()
