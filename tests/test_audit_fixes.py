"""Regressions for the deep audit: real files/SQLite and real controller methods."""
import ast
from datetime import datetime
import json
import os
import queue
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cloud_sync
import projects
import sales_store
from recovery_store import RecoveryStore
from templates import CATEGORIES, CV_NAMES

ROOT = Path(__file__).resolve().parents[1]
ITEM = {'name': 'Test service', 'qty': 1, 'price': '1.00'}
STAMP = '2026-01-01T10:00:00+02:00'


def sale(sid='sale', **changes):
    return dict({'sale_id': sid, 'created_at': STAMP, 'station': 'Counter',
                 'business': 'Shop', 'currency': 'R', 'total_cents': 100,
                 'items': [dict(ITEM)], 'voided_at': None, 'void_reason': None}, **changes)


def desktop_methods(*names):
    cls = next(n for n in ast.parse((ROOT / 'app.py').read_text()).body
               if isinstance(n, ast.ClassDef) and n.name == 'CafeDesk')
    ns = dict(threading=threading, uuid=uuid, messagebox=Mock(),
              tk=SimpleNamespace(TclError=type('TclError', (Exception,), {})))
    for name in names:
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'app.py', 'exec'), ns)
    return ns


class FileFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = patch('settings_store.settings_path', return_value=self.root / 'config' / 'settings.json')
        self.config.start()
        self.addCleanup(self.config.stop)
        self.one, self.two = self.root / 'one.db', self.root / 'two.db'


class RecoveryBarrierTests(FileFixture):
    def snapshot(self, text='PRIVATE CUSTOMER A'):
        return {'category': CATEGORIES[0], 'template': CV_NAMES[0], 'fields': {},
                'draft': text, 'photo': None}

    def test_actual_watchdog_clear_cannot_be_undone_by_old_worker(self):
        directory = self.root / 'config' / 'recovery'
        store = RecoveryStore(directory)
        ns = desktop_methods('job', 'perform_recovery', 'delete_recovery_copies')
        timers = []
        app = SimpleNamespace(busy=False, recovery_enabled=True, _active_job_id=None,
                              events=queue.Queue(), status=Mock(), _recovery_store=store,
                              after=lambda ms, cb: timers.append(cb),
                              _career_snapshot=self.snapshot, _sheet_snapshot=lambda: {'items': []})
        app.job = lambda *args, **kw: ns['job'](app, *args, **kw)
        started, release = threading.Event(), threading.Event()
        serialize = projects.project_bytes
        def blocked(*args, **kwargs):
            started.set()
            if not release.wait(5): raise RuntimeError('Timed out waiting for test release')
            return serialize(*args, **kwargs)
        with patch('projects.project_bytes', side_effect=blocked):
            try:
                ns['perform_recovery'](app)
                self.assertTrue(started.wait(5))
                timers.pop()()  # actual job watchdog, not a simulated busy toggle
                self.assertFalse(app.busy)
                app.recovery_enabled = False
                ns['delete_recovery_copies'](app, notify=False)
                self.assertFalse(directory.exists())  # no stale plaintext temp archive
            finally:
                release.set()
            event = app.events.get(timeout=5)
        self.assertEqual(event[0], 'ok')
        self.assertFalse(event[2])
        self.assertFalse(directory.exists())

    def test_old_generation_cannot_overwrite_new_customer(self):
        store = RecoveryStore(self.root / 'recovery')
        old = store.generation()
        store.clear()
        self.assertTrue(store.save(store.generation(), [('career', self.snapshot('NEW CUSTOMER'))]))
        self.assertFalse(store.save(old, [('career', self.snapshot())]))
        path = store.directory / 'career.cafeproject'
        _, data = projects.load(path, self.root / 'session', allow_internal=True)
        self.assertEqual(data['draft'], 'NEW CUSTOMER')
        self.assertFalse((store.directory / 'career.prev.cafeproject').exists())

    def test_lock_fails_fast_on_a_filesystem_without_locking(self):
        # A filesystem that cannot enforce advisory locks (ENOLCK/EOPNOTSUPP/ENOSYS)
        # is a permanent condition: retrying for the full timeout hides it and stalls
        # clear/save. The barrier must fail closed, quickly and with a clear message.
        import errno as errno_mod
        import time

        from recovery_store import _FileLock

        lock = _FileLock(self.root / 'state' / '.cafedesk-recovery.lock', timeout=15.0)
        with patch('fcntl.flock', side_effect=OSError(errno_mod.ENOLCK, 'no locks')):
            start = time.monotonic()
            with self.assertRaises(OSError) as ctx:
                lock.__enter__()
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 2.0, 'must fail fast, not retry for the full timeout')
        self.assertIn('cross-process locking', str(ctx.exception))

    def test_save_rejects_generation_below_the_inprocess_mirror(self):
        # invalidate()'s in-memory fallback bumps only the mirror when the
        # cross-process lock is unavailable. save() must consult the mirror too,
        # otherwise an abandoned worker's snapshot (captured before the fallback)
        # still publishes after the watchdog invalidated it.
        store = RecoveryStore(self.root / 'recovery')
        marker = store._state_dir / '.cafedesk-recovery.generation'
        marker.write_text('5', encoding='ascii')
        store._generation = 6  # mirror ahead, marker stale (invalidate fallback state)
        self.assertFalse(store.save(5, [('career', self.snapshot())]))
        self.assertFalse((store.directory / 'career.cafeproject').exists())
        # The next successful invalidate/clear realigns the marker; saves resume.
        store.invalidate()  # lock works in the test; marker 5 -> 6, mirror 6
        self.assertTrue(store.save(6, [('career', self.snapshot('NEW CUSTOMER'))]))
        path = store.directory / 'career.cafeproject'
        _, data = projects.load(path, self.root / 'session', allow_internal=True)
        self.assertEqual(data['draft'], 'NEW CUSTOMER')

    def test_unrelated_ui_errors_do_not_release_an_active_worker(self):
        ns = desktop_methods('report_callback_exception')
        ns.update(logger=Mock(),datetime=datetime)
        app = SimpleNamespace(busy=True,_active_job_id='still-running')
        for error in (RuntimeError('UI callback failed'), ns['tk'].TclError('destroyed widget')):
            ns['report_callback_exception'](app,type(error),error,None)
            self.assertTrue(app.busy)
            self.assertEqual(app._active_job_id,'still-running')
        # Without a tracked worker, an orphan busy flag may still be recovered.
        app._active_job_id = None
        ns['report_callback_exception'](app,RuntimeError,RuntimeError('UI error'),None)
        self.assertFalse(app.busy)

    def test_rotation_clear_and_disable_are_generation_checked(self):
        store = RecoveryStore(self.root / 'recovery')
        store.save(store.generation(), [('career', self.snapshot('FIRST'))])
        store.save(store.generation(), [('career', self.snapshot('SECOND'))])
        _, data = projects.load(store.directory / 'career.prev.cafeproject', self.root / 'session', allow_internal=True)
        self.assertEqual(data['draft'], 'FIRST')
        generation = store.generation()
        store.invalidate()
        self.assertFalse(store.save(generation, [('career', self.snapshot('ABANDONED'))]))
        (store.directory / '.cafedesk-crash.cafeproject').write_bytes(b'old customer staging data')
        self.assertEqual(store.clear(), [])
        self.assertEqual(list(store.directory.iterdir()), [])

    def test_clear_waits_for_publication_then_removes_it(self):
        store = RecoveryStore(self.root / 'recovery')
        entered, release, cleared = threading.Event(), threading.Event(), threading.Event()
        errors = []
        atomic = projects.services._atomic_write
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError('No test release')
            return atomic(*args, **kwargs)
        def save_worker():
            try: store.save(store.generation(), [('career', self.snapshot())])
            except Exception as exc: errors.append(exc)
        def clear_worker():
            store.clear()
            cleared.set()
        with patch('services._atomic_write', side_effect=blocked):
            saving = threading.Thread(target=save_worker)
            saving.start()
            self.assertTrue(entered.wait(5))
            clearing = threading.Thread(target=clear_worker)
            clearing.start()
            try: self.assertFalse(cleared.wait(.05))
            finally: release.set()
            saving.join(5); clearing.join(5)
        self.assertFalse(saving.is_alive())
        self.assertTrue(cleared.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(list(store.directory.iterdir()), [])

    # -- cross-process barrier (five-whys recheck, section 1) ------------------
    # Two RecoveryStore instances in one test process model two Café Desk
    # instances on one computer sharing the same recovery folder.

    def test_cross_process_clear_rejects_late_worker_from_another_store(self):
        directory = self.root / 'config' / 'recovery'
        first, second = RecoveryStore(directory), RecoveryStore(directory)
        old_generation = first.generation()
        second.clear()
        # The other instance's late worker captured the generation before the
        # clear; it must not be able to publish the previous customer's file.
        self.assertFalse(first.save(old_generation, [('career', self.snapshot('PRIVATE CUSTOMER A'))]))
        self.assertFalse(directory.exists())
        # A fresh worker for the new customer still publishes normally.
        self.assertTrue(first.save(first.generation(), [('career', self.snapshot('NEW CUSTOMER'))]))
        _, data = projects.load(directory / 'career.cafeproject', self.root / 'session', allow_internal=True)
        self.assertEqual(data['draft'], 'NEW CUSTOMER')

    def test_cross_process_invalidate_rejects_other_store_late_worker(self):
        directory = self.root / 'config' / 'recovery'
        first, second = RecoveryStore(directory), RecoveryStore(directory)
        old_generation = first.generation()
        second.invalidate()
        self.assertFalse(first.save(old_generation, [('career', self.snapshot('PRIVATE CUSTOMER A'))]))
        self.assertFalse(directory.exists())

    def test_cross_process_save_rotation_keeps_other_store_in_sync(self):
        directory = self.root / 'config' / 'recovery'
        first, second = RecoveryStore(directory), RecoveryStore(directory)
        self.assertTrue(first.save(first.generation(), [('career', self.snapshot('FIRST'))]))
        # The second instance sees the same generation and can rotate the file.
        self.assertTrue(second.save(second.generation(), [('career', self.snapshot('SECOND'))]))
        _, data = projects.load(directory / 'career.prev.cafeproject', self.root / 'session', allow_internal=True)
        self.assertEqual(data['draft'], 'FIRST')
        # A worker that captured the original generation before the rotation is
        # rejected, even in the other instance.
        original = first.generation() - 1
        self.assertFalse(first.save(original, [('career', self.snapshot('ABANDONED'))]))
        _, data = projects.load(directory / 'career.cafeproject', self.root / 'session', allow_internal=True)
        self.assertEqual(data['draft'], 'SECOND')

    def test_marker_and_lock_live_outside_the_recovery_folder(self):
        directory = self.root / 'config' / 'recovery'
        store = RecoveryStore(directory)
        store.save(store.generation(), [('career', self.snapshot())])
        store.clear()
        # The recovery folder holds only customer recovery data; shared state
        # (generation marker + lock) lives in the protected parent folder.
        self.assertEqual(list(store.directory.iterdir()), [])
        self.assertTrue((store._state_dir / '.cafedesk-recovery.generation').exists())
        self.assertTrue((store._state_dir / '.cafedesk-recovery.lock').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX flock conflicts across fds in one process')
    def test_lock_contention_blocks_save_and_clear(self):
        # A second process holds the publication lock: the store must fail
        # cleanly instead of publishing or deleting under the concurrent write.
        directory = self.root / 'config' / 'recovery'
        store = RecoveryStore(directory, lock_timeout=0.5)
        store.save(store.generation(), [('career', self.snapshot('CUSTOMER A'))])
        lock_path = store._state_dir / '.cafedesk-recovery.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaises(OSError):
                store.save(store.generation(), [('career', self.snapshot('CUSTOMER B'))])
            with self.assertRaises(OSError):
                store.clear()
            # Nothing was published and nothing was deleted.
            _, data = projects.load(directory / 'career.cafeproject', self.root / 'session', allow_internal=True)
            self.assertEqual(data['draft'], 'CUSTOMER A')
        finally:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class LicenceRevocationEnforcementTests(unittest.TestCase):
    """Section 3, part B: remote revocation must actually stop the app."""

    def _methods(self, *names):
        ns = desktop_methods(*names)
        ns.update(ctk=Mock(), label=Mock(), button=Mock(),
                  BG='#000', WHITE='#fff', MUTED='#m', RED='#r',
                  saved_services=Mock(), license_guard=Mock(),
                  settings_store=Mock(), time=Mock(monotonic=lambda: 0.0),
                  messagebox=Mock(), simpledialog=Mock())
        return ns

    def test_force_close_skips_every_confirmation(self):
        ns = self._methods('close_app')
        app = SimpleNamespace(busy=True, _close_confirmed_busy=False, receipt_items=[],
                              has_unsaved_work=lambda: True, _recovery_store=Mock())
        app.remove_session_files = Mock()
        app.destroy = Mock()
        ns['close_app'](app, force=True)
        ns['messagebox'].askyesno.assert_not_called()
        app._recovery_store.invalidate.assert_called_once()
        app.remove_session_files.assert_called_once()
        app.destroy.assert_called_once()

    def test_ordinary_close_still_asks_before_closing(self):
        ns = self._methods('close_app')
        ns['messagebox'].askyesno.return_value = False
        app = SimpleNamespace(busy=False, _close_confirmed_busy=False, receipt_items=[],
                              has_unsaved_work=lambda: True, _recovery_store=Mock())
        app.remove_session_files = Mock()
        app.destroy = Mock()
        ns['close_app'](app)
        ns['messagebox'].askyesno.assert_called()
        app.destroy.assert_not_called()

    def test_revocation_callback_enforces_instead_of_cancelable_close(self):
        ns = self._methods('_finish_remote_entitlement_sync')
        ns['license_guard'].is_license_required.return_value = True
        ns['license_guard'].current_license_status.return_value = {'valid': False}
        app = SimpleNamespace(_entitlement_sync_running=True, status=Mock(),
                              _enforce_licence_revocation=Mock(), close_app=Mock())
        ns['_finish_remote_entitlement_sync'](app, {
            'ok': True, 'revoked': ['Base License (L1)'], 'revocation_reasons': ['Paused by owner']})
        app._enforce_licence_revocation.assert_called_once_with(['Paused by owner'])
        app.close_app.assert_not_called()

    def test_revocation_callback_does_not_enforce_when_not_required(self):
        ns = self._methods('_finish_remote_entitlement_sync')
        ns['license_guard'].is_license_required.return_value = False
        app = SimpleNamespace(_entitlement_sync_running=True, status=Mock(),
                              _enforce_licence_revocation=Mock(), close_app=Mock())
        ns['_finish_remote_entitlement_sync'](app, {'ok': True, 'revoked': ['Base License (L1)']})
        app._enforce_licence_revocation.assert_not_called()
        app.close_app.assert_not_called()

    def test_enforcement_locks_and_force_closes_when_no_display(self):
        ns = self._methods('_enforce_licence_revocation')
        ns['ctk'].CTkToplevel.side_effect = ns['tk'].TclError('no display')
        app = SimpleNamespace(_licence_revoked=False, status=Mock(), lock_staff=Mock(), close_app=Mock())
        ns['_enforce_licence_revocation'](app, ['Paused by owner'])
        self.assertTrue(app._licence_revoked)
        app.lock_staff.assert_called_once()
        app.close_app.assert_called_once_with(force=True)

    def test_enforcement_shows_a_non_dismissable_notice(self):
        ns = self._methods('_enforce_licence_revocation')
        dlg = Mock()
        ns['ctk'].CTkToplevel.return_value = dlg
        app = SimpleNamespace(_licence_revoked=False, status=Mock(), lock_staff=Mock(), close_app=Mock())
        ns['_enforce_licence_revocation'](app, ['Paused by owner'])
        self.assertTrue(app._licence_revoked)
        app.lock_staff.assert_called_once()
        dlg.transient.assert_called_once_with(app)
        dlg.grab_set.assert_called_once()
        dlg.protocol.assert_called_once()
        protocol_args = dlg.protocol.call_args.args
        self.assertEqual(protocol_args[0], 'WM_DELETE_WINDOW')
        self.assertIsNone(protocol_args[1]())  # the handler is a no-op: cannot dismiss
        # The only way forward is Quit, which force-closes.
        quit_cmd = ns['button'].call_args.args[2]
        quit_cmd()
        app.close_app.assert_called_once_with(force=True)


    def test_require_staff_refuses_while_revoked_even_if_unlocked(self):
        # Fail closed: the deny path is the default once the licence is revoked;
        # the staff PIN must not re-open the gate.
        ns = self._methods('require_staff')
        app = SimpleNamespace(_licence_revoked=True, staff_unlocked=True,
                              _enforce_licence_revocation=Mock())
        self.assertFalse(ns['require_staff'](app))
        app._enforce_licence_revocation.assert_called_once_with(None)

    def test_require_staff_still_unlocks_normally_when_not_revoked(self):
        ns = self._methods('require_staff')
        ns['settings_store'].lockout_remaining.return_value = 0
        ns['settings_store'].pin_is_set.return_value = True
        ns['settings_store'].verify_pin.return_value = True
        app = SimpleNamespace(_licence_revoked=False, staff_unlocked=False, idle=Mock(touch=Mock()),
                              failed_attempts=0, lockout_until=0, update_lock_button=Mock(),
                              status=Mock(), _enforce_licence_revocation=Mock(),
                              _load_saved_ai_key=Mock())
        self.assertTrue(ns['require_staff'](app))
        app._enforce_licence_revocation.assert_not_called()
        app._load_saved_ai_key.assert_called_once()

    def test_revocation_landing_during_pin_entry_re_enforces(self):
        # Race: revocation confirmed while the PIN dialog is open - the unlock must
        # be rolled back and enforcement applied before any saved key is loaded.
        ns = self._methods('require_staff')
        ns['settings_store'].lockout_remaining.return_value = 0
        ns['settings_store'].pin_is_set.return_value = True
        app = SimpleNamespace(_licence_revoked=False, staff_unlocked=False, idle=Mock(touch=Mock()),
                              failed_attempts=0, lockout_until=0, update_lock_button=Mock(),
                              status=Mock(), _enforce_licence_revocation=Mock(),
                              _load_saved_ai_key=Mock())
        ns['settings_store'].verify_pin.side_effect = lambda _pin: setattr(app, '_licence_revoked', True) or True
        self.assertFalse(ns['require_staff'](app))
        app._enforce_licence_revocation.assert_called_once_with(None)
        app._load_saved_ai_key.assert_not_called()

    def test_enforcement_force_closes_when_the_shell_is_gone(self):
        # If the widget shell is already destroyed, lock_staff()/status raise
        # TclError; enforcement must still hold by force-closing, not propagate.
        ns = self._methods('_enforce_licence_revocation')
        app = SimpleNamespace(_licence_revoked=False, _licence_enforcement_shown=False,
                              status=Mock(), lock_staff=Mock(side_effect=ns['tk'].TclError('destroyed')),
                              close_app=Mock())
        ns['_enforce_licence_revocation'](app, ['Paused by owner'])
        self.assertTrue(app._licence_revoked)
        app.close_app.assert_called_once_with(force=True)

    def test_enforcement_does_not_stack_dialogs(self):
        ns = self._methods('_enforce_licence_revocation')
        ns['ctk'].CTkToplevel.return_value = Mock()
        app = SimpleNamespace(_licence_revoked=False, _licence_enforcement_shown=False,
                              status=Mock(), lock_staff=Mock())
        ns['_enforce_licence_revocation'](app, ['r1'])
        ns['_enforce_licence_revocation'](app, ['r2'])
        self.assertTrue(app._licence_revoked)
        app.lock_staff.assert_called()
        ns['ctk'].CTkToplevel.assert_called_once()


class SalesSyncIntegrityTests(FileFixture):
    def seed(self, path, count=1):
        sales_store.merge_external_sales([sale()], path=path)
        if count > 1:
            conn = sales_store.connect(path)
            try:
                conn.executemany('INSERT INTO sales(sale_id,created_at,station,business,currency,total_cents,items_json) VALUES(?,?,?,?,?,?,?)',
                                 [(f'new-{i:06}', '2026-11-21T12:00:00+02:00', 'Counter', 'Shop', 'R', 100, json.dumps([ITEM])) for i in range(count - 1)])
                conn.commit()
            finally: conn.close()

    def test_same_name_and_renamed_pcs_do_not_collide(self):
        sales_store.merge_external_sales([sale('one')], path=self.one)
        sales_store.merge_external_sales([sale('two')], path=self.two)
        folder = self.root / 'lan'
        first = sales_store.sync_lan_folder(folder, 'Counter 1', path=self.one)
        second = sales_store.sync_lan_folder(folder, 'Counter 1', path=self.two)
        self.assertNotEqual(first['own_file'], second['own_file'])
        self.assertEqual(second['inserted'], 1)
        self.assertTrue(second['complete'])
        renamed = sales_store.sync_lan_folder(folder, 'Desk 99', path=self.one)
        self.assertEqual(first['own_file'], renamed['own_file'])
        self.assertEqual(renamed['inserted'], 1)
        self.assertEqual(len(list(folder.glob('*.json'))), 2)
        self.assertEqual({r['sale_id'] for r in sales_store.export_sales_records(path=self.one)}, {'one', 'two'})

    def test_copy_on_another_machine_gets_a_different_identity(self):
        with patch('license_guard.get_machine_id', return_value='MACHINE A'):
            original = sales_store.sync_identity(self.one)
        shutil.copy2(self.one, self.two)
        with patch('license_guard.get_machine_id', return_value='MACHINE B'):
            self.assertNotEqual(original, sales_store.sync_identity(self.two))

    def test_old_void_propagates_beyond_old_lan_window(self):
        self.seed(self.one, 5002)
        sales_store.merge_external_sales([sale()], path=self.two)
        sales_store.void_sale('sale', path=self.one)
        folder = self.root / 'lan'
        first = sales_store.sync_lan_folder(folder, '', path=self.one)
        self.assertEqual(len(json.loads(Path(first['own_file']).read_text())['sales']), 5002)
        result = sales_store.sync_lan_folder(folder, '', path=self.two)
        self.assertEqual(result['updated_voids'], 1)
        self.assertEqual(len(sales_store.export_sales_records(path=self.two)), 5002)
        report = sales_store.report('2026-01-01', '2026-11-30', path=self.two)
        self.assertEqual(report['totals'][0]['cents'], 500100)

    def test_full_sqlite_import_exceeds_old_fifty_thousand_limit(self):
        self.seed(self.one, 50002)
        result = sales_store.merge_from_file(self.one, path=self.two)
        self.assertEqual(result['inserted'], 50002)
        self.assertEqual(len(sales_store.export_sales_records(path=self.two)), 50002)

    def test_corrupt_peer_does_not_hide_healthy_peer_or_partial_status(self):
        folder = self.root / 'lan'
        folder.mkdir()
        (folder / 'sales-corrupt.cafesales.json').write_text('{invalid')
        (folder / 'sales-healthy.cafesales.json').write_text(json.dumps({'sales': [sale()]}))
        result = sales_store.sync_lan_folder(folder, path=self.one)
        self.assertEqual(result['inserted'], 1)
        self.assertFalse(result['complete'])
        self.assertIn('sales-corrupt', result['errors'][0])

    def test_invalid_rows_are_rejected_without_aborting_valid_rows(self):
        result = sales_store.merge_external_sales([
            sale('valid'), sale('broken', items=None, items_json='{broken'),
            sale('fraction', items=[dict(ITEM, qty=1.9)]),
            sale('date', voided_at='not-a-date'), sale('total', total_cents=999),
        ], path=self.one)
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['rejected'], 4)
        self.assertEqual(len(result['errors']), 4)
        self.assertEqual(len(sales_store.export_sales_records(path=self.one)), 1)

    def test_conflicting_reference_cannot_void_different_sale(self):
        sales_store.merge_external_sales([sale()], path=self.one)
        result = sales_store.merge_external_sales([sale(currency='USD', voided_at=STAMP)], path=self.one)
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(result['updated_voids'], 0)
        self.assertIsNone(sales_store.export_sales_records(path=self.one)[0]['voided_at'])
        result = sales_store.merge_external_sales([sale(voided_at=STAMP)], path=self.one)
        self.assertEqual(result['updated_voids'], 1)
        self.assertEqual(sales_store.merge_external_sales([sale()], path=self.one)['updated_voids'], 0)
        self.assertTrue(sales_store.export_sales_records(path=self.one)[0]['voided_at'])

    def test_free_service_signed_zero_has_one_canonical_identity(self):
        items, total = sales_store.validate_items([dict(ITEM,price='-0.00')])
        self.assertEqual(total,0)
        self.assertEqual(items[0]['price'],'0.00')
        sales_store.merge_external_sales([sale('free',total_cents=0,items=[dict(ITEM,price='-0.00')])],path=self.one)
        result = sales_store.merge_external_sales([sale('free',total_cents=0,items=[dict(ITEM,price='0.00')],voided_at=STAMP)],path=self.one)
        self.assertEqual(result['updated_voids'],1)
        self.assertEqual(result['conflicts'],0)
        # A pre-upgrade ledger may still contain the old signed-zero text.
        conn = sales_store.connect(self.one)
        try:
            conn.execute('UPDATE sales SET items_json=? WHERE sale_id=?',
                         (json.dumps([dict(ITEM,price='-0.00')]),'free'))
            conn.commit()
        finally: conn.close()
        self.assertFalse(sales_store.record_sale('free','Shop','R',[dict(ITEM,price='0.00')],path=self.one))
        self.assertTrue(sales_store.export_sales_records(path=self.one)[0]['voided_at'])

    def test_quantity_is_an_integer_not_a_coercion(self):
        for qty in (True, 1.9, 1.0, '1.9', '1e1', None):
            with self.subTest(qty=qty), self.assertRaises(ValueError):
                sales_store.validate_items([dict(ITEM, qty=qty)])
        self.assertEqual(sales_store.validate_items([dict(ITEM, qty='2')])[1], 200)

    def test_rejected_peer_rows_surface_as_partial_sync(self):
        folder = self.root / 'lan'
        folder.mkdir()
        (folder / 'sales-peer.cafesales.json').write_text(json.dumps({'sales': [sale(), sale('bad', total_cents=999)]}))
        result = sales_store.sync_lan_folder(folder, path=self.one)
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['rejected'], 1)
        self.assertFalse(result['complete'])


class CloudPaginationTests(unittest.TestCase):
    config = {'supabase_url': 'https://test.supabase.co', 'supabase_anon_key': 'test', 'shop_id': 'TEST'}

    def test_upload_and_download_all_pages_including_an_old_void(self):
        remote = {f'old-{i:04}': sale(f'old-{i:04}') for i in range(2251)}
        local = [sale(f'new-{i:04}') for i in range(1501)] + [sale('old-0000', voided_at=STAMP)]
        calls = []
        def rpc(*args, payload, **kwargs):
            self.assertTrue(args[3].endswith('sync_shop_sales_v2'))
            calls.append(payload)
            rows = payload['p_sales']
            self.assertLessEqual(len(rows), 250)
            for row in rows: remote[row['sale_id']] = row
            page = sorted((k for k in remote if k > payload['p_after']))[:250] if payload['p_read'] else []
            return {'ok': True, 'protocol': 2, 'pushed_count': len(rows),
                    'sales': [remote[k] for k in page], 'next_after': page[-1] if page else None}
        with patch('cloud_sync._supabase_request', side_effect=rpc):
            result = cloud_sync.sync_multi_pc_sales(self.config, 'long-test-passcode', local)
        self.assertEqual(result['pushed_count'], 1502)
        self.assertEqual(len(result['sales']), 3752)
        self.assertTrue(next(r for r in result['sales'] if r['sale_id'] == 'old-0000')['voided_at'])
        self.assertGreater(len(calls), 20)

    def test_missing_protocol_or_bad_cursor_is_not_success(self):
        for response in ({'ok': True}, {'ok': True, 'protocol': 2, 'sales': [sale()], 'next_after': 'wrong'}):
            with self.subTest(response=response), patch('cloud_sync._supabase_request', return_value=response):
                with self.assertRaises(RuntimeError):
                    cloud_sync.sync_multi_pc_sales(self.config, 'long-test-passcode', [])

    def test_missing_migration_has_actionable_error_and_no_fallback(self):
        with patch('cloud_sync._supabase_request', side_effect=RuntimeError('Supabase HTTP 404: sync_shop_sales_v2 missing')) as rpc:
            with self.assertRaisesRegex(RuntimeError, 'database migration'):
                cloud_sync.sync_multi_pc_sales(self.config, 'long-test-passcode', [sale()])
        self.assertEqual(rpc.call_count, 1)

    def test_filtered_sync_and_partial_ack_are_rejected(self):
        with self.assertRaises(ValueError):
            cloud_sync.sync_multi_pc_sales(self.config, 'long-test-passcode', [], since_iso=STAMP)
        with patch('cloud_sync._supabase_request', return_value={'ok': True, 'protocol': 2, 'pushed_count': 0}):
            with self.assertRaisesRegex(RuntimeError, 'acknowledge'):
                cloud_sync.sync_multi_pc_sales(self.config, 'long-test-passcode', [sale()])
