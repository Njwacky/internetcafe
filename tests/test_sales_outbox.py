"""Five-whys regressions: durable saved sales, retries, dates, voids and privacy."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import sales_outbox
import sales_store
import saved_services
import settings_store


class SalesOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'config' / 'sales.sqlite3'
        self.item = {'name': 'Scan job', 'qty': 1, 'price': '12.50'}
        self.when = '2026-11-30T23:59:59+02:00'
        self.addCleanup(patch.stopall)
        patch('settings_store.settings_path', return_value=self.db.parent / 'settings.json').start()

    def submit(self, reference='sale', **kwargs):
        return sales_outbox.submit(reference, 'Shop', 'R', [self.item],
                                   self.when, station='Counter 1', path=self.db, **kwargs)

    def stage_failure(self, reference='sale'):
        with patch('sales_store.record_sale', side_effect=OSError('ledger offline')):
            with self.assertRaises(OSError): self.submit(reference)

    def records(self):
        return sales_store.export_sales_records(path=self.db)

    def test_failure_survives_restart_and_records_once_on_original_day(self):
        self.stage_failure()
        self.assertEqual(self.records(), [])
        self.assertTrue(sales_outbox.has_pending('sale', self.db))
        child = subprocess.run([sys.executable, '-c',
            'import json,sys,sales_outbox; print(json.dumps(sales_outbox.retry_pending(sys.argv[1])))',
            str(self.db)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True, timeout=15)
        result = json.loads(child.stdout)
        self.assertEqual(result, dict(recorded=1, remaining=0, errors=[]))
        self.assertEqual(sales_outbox.retry_pending(self.db)['recorded'], 0)
        row = self.records()[0]
        self.assertEqual(row['created_at'], self.when)
        self.assertEqual(row['station'], 'Counter 1')
        self.assertEqual(row['total_cents'], 1250)
        self.assertEqual(sales_store.report('2026-11-30', '2026-11-30', path=self.db)['totals'][0]['cents'], 1250)
        self.assertEqual(sales_store.report('2026-12-01', '2026-12-31', path=self.db)['totals'], [])

    def test_retry_keeps_original_timestamp_station_and_price_snapshot(self):
        self.stage_failure()
        sales_outbox.submit('sale', 'Shop', 'R', [self.item],
                            '2026-12-01T10:00:00+02:00', station='Changed counter', path=self.db)
        self.assertEqual(self.records()[0]['created_at'], self.when)
        self.assertEqual(self.records()[0]['station'], 'Counter 1')

    def test_success_removes_retry_copy(self):
        self.assertTrue(self.submit())
        self.assertFalse(sales_outbox.has_pending('sale', self.db))
        self.assertEqual(sales_outbox.pending_count(self.db), 0)

    def test_committed_sale_with_leftover_journal_never_duplicates_or_unvoids(self):
        self.stage_failure()
        sales_store.record_sale('sale', 'Shop', 'R', [self.item], path=self.db)
        sales_store.void_sale('sale', path=self.db)
        self.assertEqual(sales_outbox.retry_pending(self.db), dict(recorded=0, remaining=0, errors=[]))
        self.assertEqual(len(self.records()), 1)
        self.assertTrue(self.records()[0]['voided_at'])
        self.assertEqual(sales_store.report('2000-01-01', '2100-01-01', path=self.db)['totals'], [])

    def test_failed_writer_never_creates_a_journal_or_sale(self):
        def fail(): raise OSError('export failed')
        with self.assertRaises(OSError):
            saved_services.save_and_record(fail, 'failed', 'Shop', 'R', self.item, path=self.db)
        self.assertEqual(sales_outbox.pending_count(self.db), 0)
        self.assertEqual(self.records(), [])

    def test_pending_replay_does_not_count_temporary_or_incomplete_journal_files(self):
        folder = self.db.parent / 'pending-sales'
        folder.mkdir(parents=True)
        (folder / '.pending-incomplete').write_text('{')
        self.assertEqual(sales_outbox.retry_pending(self.db), dict(recorded=0, remaining=0, errors=[]))
        self.assertEqual(self.records(), [])

    def test_corrupt_journal_is_retained_and_reported_not_counted(self):
        self.stage_failure()
        target = next((self.db.parent / 'pending-sales').glob('*.json'))
        target.write_text('{broken')
        result = sales_outbox.retry_pending(self.db)
        self.assertEqual(result['recorded'], 0)
        self.assertEqual(result['remaining'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertTrue(target.exists())
        self.assertFalse(sales_outbox.has_pending('sale', self.db))
        self.assertEqual(self.records(), [])

    def test_pending_reference_cannot_be_rebound_to_different_details(self):
        self.stage_failure()
        with self.assertRaisesRegex(ValueError, 'different details'):
            sales_outbox.submit('sale', 'Other shop', 'R', [self.item], path=self.db)
        self.assertEqual(self.records(), [])
        sales_outbox.retry_pending(self.db)
        self.assertEqual(self.records()[0]['business'], 'Shop')

    def test_journal_failure_still_attempts_ledger(self):
        with patch('sales_outbox._remember', side_effect=OSError('no journal space')):
            self.assertTrue(self.submit())
        self.assertEqual(len(self.records()), 1)

    def test_both_stores_failing_gives_explicit_unprotected_warning(self):
        with patch('sales_outbox._remember', side_effect=OSError('no space')), \
             patch('sales_store.record_sale', side_effect=OSError('db down')):
            with self.assertRaisesRegex(ValueError, 'durable retry copy could not be saved'):
                self.submit()
        self.assertFalse(sales_outbox.has_pending('sale', self.db))

    def test_pending_file_contains_only_accounting_metadata(self):
        out = self.root / 'private-name.txt'
        with patch('sales_store.record_sale', side_effect=OSError('offline')):
            _, _, error = saved_services.save_and_record(lambda: out.write_text('private content'),
                                                         'private-check', 'Shop', 'R', self.item, path=self.db)
        self.assertIsNotNone(error)
        target = next((self.db.parent / 'pending-sales').glob('*.json'))
        raw = target.read_text()
        self.assertNotIn('private-name', raw)
        self.assertNotIn('private content', raw)
        self.assertNotIn(str(out), raw)
        self.assertEqual(set(json.loads(raw)), {'sale_id', 'business', 'currency', 'items', 'created_at', 'station'})
        if __import__('os').name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_reconcile_excludes_voided_lines_from_receipt_total(self):
        self.submit()
        items = [dict(self.item, _reference='sale', _recorded=True)]
        sales_store.void_sale('sale', path=self.db)
        saved_services.reconcile_receipt(items, path=self.db)
        text = saved_services.receipt_text('Shop', 'R', items, reference='customer')
        self.assertIn('TOTAL: R0.00', text)
        self.assertIn('VOIDED', text)
        self.assertNotIn('@ R12.50', text)

    def test_mixed_receipt_total_ignores_voided_but_keeps_active_services(self):
        self.submit('active')
        self.submit('void')
        sales_store.void_sale('void', path=self.db)
        items = [dict(self.item, _reference=ref, _recorded=True) for ref in ('active', 'void')]
        saved_services.reconcile_receipt(items, path=self.db)
        text = saved_services.receipt_text('Shop', 'R', items)
        self.assertIn('TOTAL: R12.50', text)
        self.assertEqual(text.count('@ R12.50'), 1)

    def test_retry_synchronizes_an_open_receipt_without_recharging(self):
        self.stage_failure()
        items = [dict(self.item, _reference='sale', _recorded=False, _saved_work=True)]
        sales_outbox.retry_pending(self.db)
        saved_services.reconcile_receipt(items, path=self.db)
        self.assertTrue(items[0]['_recorded'])
        self.assertEqual(saved_services.record_pending(items, 'Shop', 'R', 'new-ref', path=self.db), 0)
        self.assertEqual(len(self.records()), 1)

    def test_queued_manual_sale_cannot_be_treated_as_an_unsaved_draft(self):
        items = [dict(self.item)]
        with patch('sales_store.record_sale', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                saved_services.record_pending(items, 'Shop', 'R', 'manual', path=self.db)
        self.assertTrue(items[0]['_saved_work'])
        self.assertTrue(sales_outbox.has_pending('manual', self.db))
        self.assertEqual(saved_services.unprotected_sales(items, path=self.db), [])

    def test_retry_after_real_database_lock(self):
        # Exercise SQLite failure, not just a mocked exception.
        conn = sales_store.connect(self.db)
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('BEGIN EXCLUSIVE')
        def quick_connect(path=None):
            # Opening/creating tables also needs the lock. A short timeout keeps CI fast.
            connection = sqlite3.connect(path or self.db, timeout=0.01)
            return connection
        try:
            with patch('sales_store.connect', side_effect=quick_connect):
                with self.assertRaisesRegex(sales_store.SalesStoreError, 'busy or locked'): self.submit()
            self.assertFalse(self.db.with_name(self.db.name+'.corrupt').exists())
            self.assertEqual(sales_outbox.pending_count(self.db), 1)
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(sales_outbox.retry_pending(self.db)['recorded'], 1)
        self.assertEqual(len(self.records()), 1)

    def test_inaccessible_retry_folder_is_an_error_not_an_empty_queue(self):
        with patch('pathlib.Path.iterdir', side_effect=PermissionError('retry directory denied')):
            with self.assertRaises(PermissionError): sales_outbox.pending_count(self.db)
            with self.assertRaises(PermissionError): sales_outbox.retry_pending(self.db)

    def test_readonly_or_full_errors_are_not_called_corruption(self):
        for code, message in ((sqlite3.SQLITE_FULL, 'disk is full'),
                              (sqlite3.SQLITE_READONLY, 'read-only')):
            error = sqlite3.OperationalError('failure')
            error.sqlite_errorcode = code
            result = sales_store._database_error(error, self.db)
            self.assertIn(message, str(result))
            self.assertFalse(self.db.with_name(self.db.name+'.corrupt').exists())

    def test_replayed_saved_sales_merge_across_pcs_without_duplicates_and_keep_voids(self):
        self.stage_failure()
        sales_outbox.retry_pending(self.db)
        peer = self.root / 'other-pc' / 'sales.sqlite3'
        first = sales_store.merge_external_sales(self.records(), path=peer)
        again = sales_store.merge_external_sales(self.records(), path=peer)
        self.assertEqual(first['inserted'], 1)
        self.assertEqual(again['inserted'], 0)
        sales_store.void_sale('sale', path=peer)
        peer_records = sales_store.export_sales_records(path=peer)
        self.assertEqual(sales_store.merge_external_sales(peer_records, path=self.db)['updated_voids'], 1)
        self.assertTrue(self.records()[0]['voided_at'])
        self.assertEqual(sales_store.report('2000-01-01', '2100-01-01', path=self.db)['totals'], [])

    def test_receipt_status_lookup_fails_fast_on_real_lock_without_corruption_advice(self):
        self.submit()
        conn = sales_store.connect(self.db)
        conn.execute('BEGIN EXCLUSIVE')
        try:
            with self.assertRaisesRegex(sales_store.SalesStoreError, 'busy or locked'):
                sales_outbox.sale_states(['sale'], self.db)
        finally:
            conn.rollback()
            conn.close()
        self.assertFalse(self.db.with_name(self.db.name+'.corrupt').exists())

    def test_real_corrupt_database_keeps_completed_sale_for_recovery(self):
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b'not a SQLite database')
        output = self.root / 'customer.txt'
        _, when, error = saved_services.save_and_record(lambda: output.write_text('saved'),
                                                       'recover-corruption', 'Shop', 'R', self.item, path=self.db)
        self.assertTrue(output.exists())
        self.assertIn('damaged', error)
        self.assertTrue(self.db.with_name(self.db.name+'.corrupt').exists())
        self.assertEqual(sales_outbox.pending_count(self.db), 1)
        # Simulate an administrator archiving the broken DB in this disposable profile.
        self.db.rename(self.db.with_name('archived-broken.sqlite3'))
        self.assertEqual(sales_outbox.retry_pending(self.db)['recorded'], 1)
        self.assertEqual(self.records()[0]['created_at'], when)


if __name__ == '__main__':
    unittest.main()
