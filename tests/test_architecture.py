"""Architecture review regression tests.

Covers the five review recommendations without requiring tkinter/customtkinter:

1. Screen controllers — dispatch still covers all 17 screens (bodies staged).
2. Background workers — WorkerPool / BackgroundTasks run off-thread, handle errors.
3. State manager — SessionState is the single source for telemetry + queue.
4. Library internals — ctk_safety no longer patches CustomTkinter private methods.
5. Logging — central logger, record_last_error, no stdout traceback printing.
"""

import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class WorkerPoolTests(unittest.TestCase):
    def test_run_delivers_result_off_thread(self):
        from workers import WorkerPool

        results = []
        done = threading.Event()

        def _cb(res):
            results.append(res)
            done.set()

        WorkerPool().run(lambda: 42, on_result=_cb)
        self.assertTrue(done.wait(5))
        self.assertEqual(results, [42])

    def test_run_surfaces_exception_as_error_dict(self):
        from workers import WorkerPool

        results = []
        done = threading.Event()

        def _boom():
            raise RuntimeError("kaboom")

        WorkerPool().run(_boom, on_result=lambda r: (results.append(r), done.set()))
        self.assertTrue(done.wait(5))
        self.assertFalse(results[0]["ok"])
        self.assertIn("kaboom", results[0]["error"])

    def test_merge_failed_telemetry_caps_at_5000(self):
        from workers import BackgroundTasks

        counts = {"Home": 4999}
        lock = threading.Lock()
        BackgroundTasks().merge_failed_telemetry({"Home": 10, "PDF tools": 3}, counts, lock)
        self.assertEqual(counts["Home"], 5000)
        self.assertEqual(counts["PDF tools"], 3)

    def test_run_telemetry_flush_calls_on_done(self):
        from unittest import mock

        import workers

        counts = {}
        lock = threading.Lock()
        done = threading.Event()
        # cloud_sync / settings_store are imported lazily inside run_telemetry_flush,
        # so patch them at their source modules.
        with mock.patch(
            "cloud_sync.send_workspace_telemetry", return_value={"ok": True}
        ), mock.patch("settings_store.load_settings", return_value={}):
            workers.BackgroundTasks().run_telemetry_flush(
                {"Home": 1}, "Home", "0.0.0", counts, lock, done.set
            )
            self.assertTrue(done.wait(5))


class SessionStateTests(unittest.TestCase):
    def test_cafe_desk_does_not_shadow_tk_window_state_method(self):
        import ast

        app_src = (REPO / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(app_src)
        cafe_desk = next(node for node in tree.body
                         if isinstance(node, ast.ClassDef) and node.name == "CafeDesk")
        state_writes = [
            node for node in ast.walk(cafe_desk)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == "state"
            and isinstance(node.ctx, ast.Store)
        ]
        self.assertEqual(state_writes, [], "CafeDesk must not shadow tkinter's state() method")

    def test_increment_snapshot_and_total(self):
        from app_state import SessionState

        state = SessionState()
        self.assertEqual(state.increment("Home"), 1)
        self.assertEqual(state.increment("Home"), 2)
        self.assertEqual(state.increment("PDF tools"), 3)
        self.assertEqual(state.snapshot(), {"Home": 2, "PDF tools": 1})
        self.assertEqual(state.total_queued(), 3)

    def test_shared_objects_stay_in_sync(self):
        import queue

        from app_state import SessionState

        events = queue.Queue()
        counts = {}
        lock = threading.Lock()
        state = SessionState(events, counts, lock)
        state.increment("Home")
        self.assertEqual(counts, {"Home": 1})
        self.assertIs(state.events, events)


class LoggingTests(unittest.TestCase):
    def test_record_last_error_supports_dict_and_object(self):
        from logging_utils import record_last_error

        store = {}
        record_last_error(store, "test", "boom")
        self.assertIn("test: boom", store["_last_gui_error"])

        class Obj:
            pass

        obj = Obj()
        record_last_error(obj, "test", "bang")
        self.assertIn("test: bang", obj._last_gui_error)

    def test_log_exception_never_raises(self):
        from logging_utils import log_exception

        log_exception("test", ValueError("x"))
        log_exception("test")

    def test_app_uses_logger_not_stdout_tracebacks(self):
        app_src = (REPO / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("traceback.print_exc", app_src)
        self.assertNotIn("traceback.print_exception", app_src)
        self.assertIn("logger.exception", app_src)


class CtkSafetyTests(unittest.TestCase):
    def test_install_is_noop_and_returns_false(self):
        from ctk_safety import install_ctk_entry_guard

        self.assertFalse(install_ctk_entry_guard())

    def test_no_library_patch_remains(self):
        src = (REPO / "ctk_safety.py").read_text(encoding="utf-8")
        self.assertNotIn("CTkEntry._textvariable_callback =", src)
        self.assertNotIn("CTkEntry._activate_placeholder =", src)

    def test_safe_destroy_never_raises(self):
        from ctk_safety import safe_destroy

        safe_destroy(None)

        class Fake:
            def winfo_exists(self):
                return True

            def destroy(self):
                raise RuntimeError("already gone")

        safe_destroy(Fake())


class WiringTests(unittest.TestCase):
    def test_telemetry_flush_goes_through_workers(self):
        app_src = (REPO / "app.py").read_text(encoding="utf-8")
        self.assertIn("run_telemetry_flush", app_src)
        self.assertNotIn("threading.Thread(target=_bg", app_src)
        workers_src = (REPO / "workers.py").read_text(encoding="utf-8")
        self.assertIn("cloud_sync.send_workspace_telemetry", workers_src)

    def test_online_templates_uses_worker_pool(self):
        src = (REPO / "online_templates_ui.py").read_text(encoding="utf-8")
        self.assertIn("WorkerPool", src)
        self.assertNotIn("threading.Thread", src)

    def test_screen_dispatch_covers_all_17(self):
        app_src = (REPO / "app.py").read_text(encoding="utf-8")
        for name in (
            "Home",
            "Price list",
            "PDF tools",
            "Bluetooth files",
            "Scan & images",
            "A4 picture sheets",
            "Career documents",
            "Cards & invitations",
            "Forms creator",
            "Receipts",
            "Sales reports",
            "Price settings",
            "Business settings",
            "AI settings",
            "Projects & recovery",
            "About & contact",
            "Help & privacy",
        ):
            self.assertIn(f"'{name}'", app_src)


if __name__ == "__main__":
    unittest.main()
