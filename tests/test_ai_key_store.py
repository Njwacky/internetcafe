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
        self.assertIn("self._set_key_memory('')", lock_body)
        # The in-memory key set/clear is the single point that also keeps the
        # durable-log secret redactor in sync.
        self.assertIn("def _set_key_memory(self,value):", src)
        self.assertIn("logging_utils.register_secret(value)", src)
        self.assertIn("logging_utils.unregister_secret(old)", src)
        # Request paths register the exact key with the log redactor as well.
        for fn in ("def test_ai():", "def improve(self):", "def save_key_now():"):
            body = src[src.index(fn):]
            for stop in ("\n    def ", "\n        def ", "\n\nif __name__"):
                idx = body.find(stop)
                if idx != -1:
                    body = body[:idx]
                    break
            self.assertIn("logging_utils.register_secret(key)", body)
        # Saving asks for and verifies the PIN first; PIN change re-encrypts.
        save_fn = src[src.index("        def save_key_now():"):src.index("        def forget_key_now():")]
        self.assertLess(save_fn.index("settings_store.verify_pin(pin)"), save_fn.index("ai_key_store.save_key("))
        self.assertIn("Incorrect PIN. The key was not saved.", save_fn)
        self.assertIn("ai_key_store.rewrap_for_new_pin(old,new)", src)

    def test_logging_redactor_removes_registered_and_pattern_secrets(self):
        import logging_utils

        logging_utils.register_secret(KEY)
        self.addCleanup(logging_utils.unregister_secret, KEY)
        text = f"connection failed for {KEY} and gsk_othertoken123 and AIzaSyA1234567890abc"
        out = logging_utils.redact_text(text)
        self.assertNotIn(KEY, out)
        self.assertNotIn("gsk_othertoken123", out)
        self.assertNotIn("AIzaSyA1234567890abc", out)
        self.assertIn("[hidden key]", out)
        # A non-pattern secret is only redacted while it is registered.
        plain = "plainsecretvalue123"
        logging_utils.register_secret(plain)
        self.addCleanup(logging_utils.unregister_secret, plain)
        self.assertNotIn(plain, logging_utils.redact_text(f"now shows {plain}"))
        logging_utils.unregister_secret(plain)
        self.assertIn(plain, logging_utils.redact_text(f"now shows {plain}"))

    def test_logger_formatter_redacts_messages_and_tracebacks(self):
        import logging

        import logging_utils

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(logging_utils._SecretRedactingFormatter(logging_utils._LOG_FORMAT).format(record))

        handler = Capture()
        handler.setFormatter(logging_utils._SecretRedactingFormatter(logging_utils._LOG_FORMAT))
        log = logging.getLogger("cafedesk-redaction-test")
        log.handlers = [handler]
        log.setLevel(logging.INFO)
        log.propagate = False
        logging_utils.register_secret(KEY)
        self.addCleanup(logging_utils.unregister_secret, KEY)
        # A record whose message carries the key, and a chained traceback whose
        # exception message carries it too - neither may reach the output.
        try:
            raise ValueError(f"request to https://example.com?key={KEY} failed")
        except ValueError:
            log.error("AI request failed with key %s", KEY, exc_info=True)
        output = "\n".join(records)
        self.assertNotIn(KEY, output)
        self.assertIn("[hidden key]", output)

    def test_record_last_error_stores_a_redacted_note(self):
        # The note is surfaced in the Help screen, so the storage boundary needs the
        # same redaction as the log sink (recheck finding).
        import logging_utils

        store = {}
        logging_utils.register_secret("sk-live-abcdef123456")
        try:
            logging_utils.record_last_error(store, "ai call", "failed with sk-live-abcdef123456")
            self.assertNotIn("sk-live-abcdef123456", store["_last_gui_error"])
            self.assertIn("[hidden key]", store["_last_gui_error"])
        finally:
            logging_utils.unregister_secret("sk-live-abcdef123456")

    def test_root_logger_has_a_redacting_handler(self):
        # Library/third-party loggers propagate to root; without a redacting handler
        # there, Python's lastResort would print their records unredacted (recheck).
        import logging

        import logging_utils

        root = logging.getLogger()
        handlers = [h for h in root.handlers if getattr(h, "_cafedesk_redacting", False)]
        self.assertTrue(handlers, "root logger must carry a redacting handler")
        self.assertIsInstance(handlers[0].formatter, logging_utils._SecretRedactingFormatter)

    def test_set_key_memory_sets_var_and_trace_syncs_registry(self):
        # Architecture (recheck): _set_key_memory only writes the StringVar; the
        # trace-driven _sync_key_registration keeps the log redactor in step, so a
        # key typed into the masked entry is registered too, not just programmatic sets.
        import ast

        import logging_utils

        src = (REPO / "app.py").read_text(encoding="utf-8")
        cls = next(n for n in ast.parse(src).body
                   if isinstance(n, ast.ClassDef) and n.name == "CafeDesk")
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        ns = {"logging_utils": logging_utils}
        exec(compile(ast.Module(body=[methods["_set_key_memory"], methods["_sync_key_registration"]],
                               type_ignores=[]), "app.py", "exec"), ns)
        app = mock.Mock()
        app._registered_key = "gsk_previouskey123"
        app.key.get.side_effect = ["gsk_newkey456", ""]
        with mock.patch.object(logging_utils, "register_secret") as register, \
                mock.patch.object(logging_utils, "unregister_secret") as unregister:
            ns["_set_key_memory"](app, "gsk_newkey456")
            app.key.set.assert_called_once_with("gsk_newkey456")
            # The trace handler (fired by the set in real Tk) registers the new key
            # and unregisters the previous one.
            ns["_sync_key_registration"](app)
            register.assert_called_once_with("gsk_newkey456")
            unregister.assert_called_once_with("gsk_previouskey123")
            # Clearing: the trace unregisters the current key and stores an empty value.
            ns["_set_key_memory"](app, "")
            app.key.set.assert_called_with("")
            ns["_sync_key_registration"](app)
            self.assertEqual(register.call_count, 1)  # clearing registers nothing new
            self.assertEqual(unregister.call_args_list,
                             [mock.call("gsk_previouskey123"), mock.call("gsk_newkey456")])


class FiveWhysRegressionTests(unittest.TestCase):
    """Gaps found when re-checking the first version with a 5 Whys review."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / store.FILE_NAME
        patcher = mock.patch.object(store, "os_protection_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        store.save_key(KEY, PIN, provider="Groq (api.groq.com)", model="openai/gpt-oss-120b", path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _edit(self, **changes):
        obj = json.loads(self.path.read_text())
        obj.update(changes)
        self.path.write_text(json.dumps(obj))

    def test_redirecting_the_key_to_another_server_is_blocked(self):
        self._edit(provider="Custom OpenAI-compatible URL", custom_url="https://evil.example.com/v1")
        with self.assertRaisesRegex(store.SavedKeyError, "changed outside"):
            store.load_key(PIN, path=self.path)

    def test_editing_model_or_hint_is_blocked(self):
        for field, value in (("model", "attacker-model"), ("hint", "gsk_…ZZZZ")):
            store.save_key(KEY, PIN, provider="Groq (api.groq.com)", model="openai/gpt-oss-120b", path=self.path)
            self._edit(**{field: value})
            with self.assertRaises(store.SavedKeyError, msg=field):
                store.load_key(PIN, path=self.path)

    def test_settings_come_from_the_encrypted_payload(self):
        data = store.load_key(PIN, path=self.path)
        self.assertEqual((data["provider"], data["model"], data["custom_url"]),
                         ("Groq (api.groq.com)", "openai/gpt-oss-120b", ""))

    def test_oversized_file_is_refused(self):
        self.path.write_text('{"format":"%s","pad":"%s"}' % (store.FORMAT, "A" * (store.MAX_FILE_BYTES + 1)))
        self.assertTrue(store.saved_key_info(self.path)["damaged"])
        with self.assertRaises(store.SavedKeyError):
            store.load_key(PIN, path=self.path)

    def test_low_memory_is_reported_cleanly(self):
        with mock.patch.object(store, "_derive", side_effect=MemoryError):
            with self.assertRaises(store.SavedKeyError):
                store.load_key(PIN, path=self.path)
            with self.assertRaises(ValueError):
                store.save_key(KEY, PIN, path=self.path)

    def test_app_rejects_unsafe_saved_url_and_warns_on_short_pin(self):
        src = (REPO / "app.py").read_text(encoding="utf-8")
        self.assertIn("services.detect_ai_provider(data['api_key'],data.get('provider',''),data['custom_url'])", src)
        self.assertIn("if len(pin)<8 and not messagebox.askyesno('Short PIN'", src)

    def test_save_key_rejects_invalid_hostname_or_secret_in_custom_url(self):
        fresh = Path(self.tmp.name) / "fresh-ai-key.json"
        for provider, bad_url in (
            ("Custom OpenAI-compatible URL", ""),
            ("Custom OpenAI-compatible URL", "http://localhost.evil.com/v1"),
            ("Custom OpenAI-compatible URL", "http://localhost@evil.com/v1"),
            ("Custom OpenAI-compatible URL", "https:///v1"),
            ("Groq (api.groq.com)", "https://example.com/v1?key=" + KEY),
            ("Groq (api.groq.com)", "http://localhost.evil.com/v1"),
        ):
            with self.subTest(provider=provider, url=bad_url):
                with self.assertRaises(ValueError):
                    store.save_key(KEY, PIN, provider=provider, model="gpt-4o-mini", custom_url=bad_url, path=fresh)
                self.assertFalse(fresh.exists())

        store.save_key(
            KEY,
            PIN,
            provider="Custom OpenAI-compatible URL",
            model="gpt-4o-mini",
            custom_url="https://Example.COM/v1",
            path=fresh,
        )
        loaded = store.load_key(PIN, path=fresh)
        self.assertEqual(loaded["custom_url"], "https://example.com/v1")

    def test_load_saved_ai_key_validates_url_before_mutating_app_state(self):
        import ast

        import ai_safety
        import services

        src = (REPO / "app.py").read_text(encoding="utf-8")
        cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "CafeDesk")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_load_saved_ai_key")
        ns = {"ai_key_store": store, "services": services, "ai_safety": ai_safety}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "app.py", "exec"), ns)

        for bad_record in (
            {"api_key": KEY, "provider": "Custom OpenAI-compatible URL", "model": "m", "custom_url": "http://localhost.evil.com/v1"},
            {"api_key": KEY, "provider": "Custom OpenAI-compatible URL", "model": "m", "custom_url": ""},
            {"api_key": KEY, "provider": "Groq (api.groq.com)", "model": "m", "custom_url": "http://localhost.evil.com/v1"},
        ):
            app = mock.Mock()
            app.key.get.return_value = ""
            with mock.patch.object(store, "saved_key_info", return_value={"damaged": False, "hint": "gsk_…a1b2"}), \
                 mock.patch.object(store, "load_key", return_value=bad_record):
                ns["_load_saved_ai_key"](app, PIN)
            app._set_key_memory.assert_called_once_with("")
            app.ai_provider.set.assert_not_called()
            app.model.set.assert_not_called()
            app.ai_custom_url.set.assert_not_called()
            self.assertIn("not loaded", app.status.set.call_args[0][0])

    def test_recheck_unlock_overwrites_stale_custom_routing_and_rejects_unknown_provider(self):
        import ast

        import ai_safety
        import services

        # save_key refuses unrecognised non-empty provider names.
        fresh = Path(self.tmp.name) / "unknown-prov.json"
        with self.assertRaises(ValueError):
            store.save_key(KEY, PIN, provider="Unknown Provider", path=fresh)
        self.assertFalse(fresh.exists())

        src = (REPO / "app.py").read_text(encoding="utf-8")
        cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "CafeDesk")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_load_saved_ai_key")
        ns = {"ai_key_store": store, "services": services, "ai_safety": ai_safety}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "app.py", "exec"), ns)

        # If UI state held a custom URL before locking, unlocking an auto-detect key
        # (saved with provider="", custom_url="") MUST reset ai_provider and ai_custom_url
        # so the decrypted key cannot be sent to the previous session's custom URL.
        app = mock.Mock()
        app.key.get.return_value = ""
        with mock.patch.object(store, "saved_key_info", return_value={"damaged": False, "hint": "gsk_…a1b2"}), \
             mock.patch.object(store, "load_key", return_value={"api_key": KEY, "provider": "", "model": "", "custom_url": ""}):
            ns["_load_saved_ai_key"](app, PIN)
        app._set_key_memory.assert_called_once_with(KEY)
        app.ai_provider.set.assert_called_once_with("Auto-detect from API key")
        app.model.set.assert_called_once_with("openai/gpt-oss-120b")
        app.ai_custom_url.set.assert_called_once_with("")

        # An unrecognised provider inside a saved record must fail closed without loading the key.
        app2 = mock.Mock()
        app2.key.get.return_value = ""
        with mock.patch.object(store, "saved_key_info", return_value={"damaged": False, "hint": "gsk_…a1b2"}), \
             mock.patch.object(store, "load_key", return_value={"api_key": KEY, "provider": "RogueProvider", "model": "m", "custom_url": ""}):
            ns["_load_saved_ai_key"](app2, PIN)
        app2._set_key_memory.assert_called_once_with("")
        app2.ai_provider.set.assert_not_called()

    def test_windows_ci_runs_the_real_dpapi_self_test(self):
        workflow = (REPO / ".github" / "workflows" / "build-usb.yml").read_text(encoding="utf-8")
        self.assertIn("python .github/dpapi_selftest.py", workflow)
        self.assertTrue((REPO / ".github" / "dpapi_selftest.py").is_file())


@unittest.skipUnless(sys.platform == "win32", "real DPAPI only on Windows")
class RealWindowsDpapiTests(unittest.TestCase):
    def test_real_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / store.FILE_NAME
            store.save_key(KEY, PIN, path=path)
            self.assertEqual(store.saved_key_info(path)["protection"], "dpapi+pin")
            self.assertEqual(store.load_key(PIN, path=path)["api_key"], KEY)


if __name__ == "__main__":
    unittest.main()
