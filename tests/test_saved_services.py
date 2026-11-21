"""Saved-work accounting, including desktop callbacks without a Tk display."""
import ast
from contextlib import nullcontext
from datetime import datetime, date, timedelta
from decimal import Decimal
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import price_list
import sales_store
import sales_outbox
import saved_services
import services
import settings_store

REPO = Path(__file__).resolve().parents[1]


def desktop_method(name, namespace):
    """Execute the real controller method without importing unavailable Tk on CI."""
    tree = ast.parse((REPO / 'app.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CafeDesk')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace[name]


class Widget:
    def __init__(self, *args, value='', **kwargs):
        self.value = value
    def pack(self, **kwargs): pass
    def configure(self, **kwargs): self.options = kwargs
    def bind(self, *args, **kwargs): pass
    def winfo_exists(self): return True
    def delete(self, *args): self.value = ''
    def insert(self, index, text): self.value = str(text)
    def get(self, *args): return self.value
    def set(self, text): self.value = text


class SavedServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch('settings_store.settings_path', return_value=self.root / 'config' / 'settings.json').start()
        settings_store.save_prices('R', 'Scan job | 12.50\nWriting job | 40.00\nFree | 0.00')
        settings_store.save_receipt_service_links({'scan': 'Scan job', 'career': 'Writing job'})
        self.item = {'name': 'Scan job', 'price': '12.50', 'qty': 1}
        self.buttons = {}
        def button(parent, title, callback, **kwargs):
            self.buttons[title] = callback
            return Widget()
        self.dialogs = Mock()
        self.files = Mock()
        self.inputs = Mock()
        self.ns = dict(settings_store=settings_store, sales_store=sales_store,
                       saved_services=saved_services, sales_outbox=sales_outbox, services=services, price_list=price_list,
                       uuid=uuid, datetime=datetime, messagebox=self.dialogs,
                       filedialog=self.files, simpledialog=self.inputs,
                       ctk=SimpleNamespace(CTkTextbox=Widget, CTkFrame=Widget,
                                           CTkOptionMenu=Widget, StringVar=Widget),
                       card=Widget, label=Widget, entry=Widget, button=button,
                       button_row=Widget, BORDER='', INK='', MUTED='', GREEN='',
                       GREEN_HOVER='', BTN_PADX=8)
        self.app = SimpleNamespace(busy=False, _recovery_store=Mock(), business_name=Widget(value='Shop'),
                                   body=None, heading=Mock(), status=Widget(),
                                   require_staff=Mock(return_value=True), show=Mock())
        can_clear = desktop_method('can_clear_receipt', self.ns)
        self.app.can_clear_receipt = lambda: can_clear(self.app)
        reset = desktop_method('reset_receipt', self.ns)
        self.app.reset_receipt = lambda: reset(self.app)
        self.app.reset_receipt()
        def job(description, work, done, error_cb=None):
            try:
                result = work()
            except Exception as exc:
                if error_cb: error_cb(str(exc))
                raise
            done(result)
        self.app.job = job
        self.saved_job = desktop_method('saved_job', self.ns)
        self.receipt_screen = desktop_method('receipt_screen', self.ns)

    def rows(self):
        return sales_store.export_sales_records()

    def total(self):
        data = sales_store.report('2000-01-01', '2100-01-01')
        return sum(x['cents'] for x in data['totals'])

    def save_scan(self):
        out = self.root / 'customer.txt'
        self.saved_job(self.app, 'Save scan', lambda: out.write_text('customer content'),
                       Mock(), 'scan')
        return out

    def test_successful_save_adds_receipt_and_earnings_without_staff_confirmation(self):
        out = self.save_scan()
        self.assertTrue(out.exists())
        self.assertEqual(self.total(), 1250)
        self.assertEqual(len(self.app.receipt_items), 1)
        self.assertTrue(self.app.receipt_recorded)
        self.app.require_staff.assert_not_called()
        row = self.rows()[0]
        self.assertEqual(row['items'], [self.item])
        self.assertNotIn('customer content', str(row))
        self.assertNotIn(str(out), str(row))

    def test_failed_save_never_records_or_changes_receipt(self):
        def fail(): raise OSError('disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
            saved_services.save_and_record(fail, 'failed', 'Shop', 'R', self.item)
        self.saved_job(self.app, 'Save', fail, Mock(), 'scan')
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.app.receipt_items, [])

    def test_busy_desk_does_not_save_or_record(self):
        self.app.busy = True
        write = Mock()
        self.saved_job(self.app, 'Save', write, Mock(), 'scan')
        write.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_unlinked_rate_cancel_does_not_save_or_record(self):
        self.inputs.askinteger.return_value = None
        write = Mock()
        self.saved_job(self.app, 'Save', write, Mock(), 'ocr')
        write.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_unlinked_rate_staff_selection_is_persisted(self):
        self.inputs.askinteger.return_value = 2
        self.saved_job(self.app, 'Save', lambda: None, Mock(), 'ocr')
        self.assertEqual(self.total(), 4000)
        self.assertEqual(settings_store.load_settings()['receipt_service_links']['ocr'], 'Writing job')

    def test_unlinked_rate_staff_denied_does_not_save(self):
        self.app.require_staff.return_value = False
        write = Mock()
        self.saved_job(self.app, 'Save', write, Mock(), 'ocr')
        write.assert_not_called()
        self.inputs.askinteger.assert_not_called()

    def test_deleted_unpriced_draft_does_not_count(self):
        self.app.receipt_items.append(dict(self.item))
        self.app.reset_receipt()
        self.assertEqual(self.total(), 0)

    def test_discard_manual_receipt_lines_does_not_count(self):
        self.app.receipt_items = [dict(self.item)]
        self.app.receipt_currency = 'R'
        self.app.receipt_business = 'Shop'
        self.receipt_screen(self.app)
        self.buttons['Undo last item']()
        self.assertEqual(self.app.receipt_items, [])
        self.assertEqual(self.total(), 0)

    def test_receipt_copy_never_double_counts_saved_services(self):
        self.save_scan()
        self.receipt_screen(self.app)
        out = self.root / 'receipt.pdf'
        self.files.asksaveasfilename.return_value = str(out)
        self.buttons['Save receipt PDF']()
        self.buttons['Save receipt PDF']()
        self.assertTrue(out.exists())
        self.assertEqual(self.total(), 1250)
        self.assertEqual(len(self.rows()), 1)

    def test_manual_receipt_save_records_only_new_lines(self):
        self.save_scan()
        self.receipt_screen(self.app)
        self.buttons['Add service']()
        self.files.asksaveasfilename.return_value = str(self.root / 'receipt.pdf')
        self.buttons['Save receipt PDF']()
        self.assertEqual(self.total(), 2500)
        self.buttons['Add service']()
        self.buttons['Save receipt PDF']()
        self.assertEqual(self.total(), 3750)
        self.assertEqual(len(self.rows()), 3)
        self.assertTrue(self.app.receipt_recorded)

    def test_cancel_receipt_save_does_not_record_manual_lines(self):
        self.receipt_screen(self.app)
        self.buttons['Add service']()
        self.files.asksaveasfilename.return_value = ''
        self.buttons['Save receipt PDF']()
        self.assertEqual(self.total(), 0)

    def test_saved_file_delete_or_next_customer_does_not_erase_earnings(self):
        out = self.save_scan()
        out.unlink()
        self.app.reset_receipt()
        self.assertEqual(self.total(), 1250)

    def test_saved_lines_cannot_be_silently_undone(self):
        self.save_scan()
        self.receipt_screen(self.app)
        self.buttons['Undo last item']()
        self.assertEqual(len(self.app.receipt_items), 1)
        self.assertEqual(self.total(), 1250)

    def test_accounting_failure_retains_line_and_retry_is_idempotent(self):
        with patch('sales_store.record_sale', side_effect=OSError('unavailable')):
            self.save_scan()
        self.assertFalse(self.app.receipt_recorded)
        self.assertEqual(self.total(), 0)
        self.dialogs.showerror.assert_called_once()
        self.receipt_screen(self.app)
        self.buttons['Record sale']()
        self.buttons['Record sale']()
        self.assertEqual(self.total(), 1250)
        self.assertEqual(len(self.rows()), 1)

    def test_partial_record_retry_then_new_manual_group(self):
        self.app.receipt_items = [dict(self.item), dict(self.item, _reference='second')]
        self.app.receipt_business = 'Shop'
        self.app.receipt_currency = 'R'
        self.receipt_screen(self.app)
        original = sales_store.record_sale
        calls = []
        def record(sale_id, *args, **kwargs):
            calls.append(sale_id)
            if len(calls) == 2: raise OSError('interrupted')
            return original(sale_id, *args, **kwargs)
        with patch('sales_store.record_sale', side_effect=record):
            with self.assertRaises(OSError): self.buttons['Record sale']()
        self.assertEqual(self.total(), 1250)
        self.buttons['Add service']()
        self.buttons['Record sale']()
        self.assertEqual(self.total(), 3750)
        self.assertEqual(len(self.rows()), 3)

    def test_today_month_currency_and_void_totals(self):
        today = date.today().isoformat()
        sales_store.record_sale('today', 'Shop', 'R', [self.item], today+'T10:00:00+02:00')
        sales_store.record_sale('old', 'Shop', 'R', [self.item], '2001-01-01T10:00:00+02:00')
        sales_store.record_sale('other-currency', 'Shop', 'USD', [self.item], today+'T10:00:00+02:00')
        for start in (today, date.today().replace(day=1).isoformat()):
            result = sales_store.report(start, today)
            self.assertEqual([r['cents'] for r in result['totals']], [1250, 1250])
        sales_store.void_sale('today')
        result = sales_store.report(today, today)
        self.assertEqual(result['totals'][0]['currency'], 'USD')
        self.assertEqual(result['voided_count'], 1)

    def test_explicit_free_rate_and_stale_links(self):
        settings_store.save_receipt_service_links({'scan': 'Free'})
        self.save_scan()
        self.assertEqual(self.total(), 0)
        self.assertEqual(len(self.rows()), 1)
        settings_store.save_prices('R', 'Different | 5.00')
        _, item = saved_services.linked_price(settings_store.load_settings(), 'scan')
        self.assertIsNone(item)
        with self.assertRaises(ValueError):
            settings_store.save_receipt_service_links({'scan': 'missing'})

    def test_latest_rate_applies_only_to_future_saves(self):
        self.save_scan()
        settings_store.save_prices('R', 'Scan job | 20.00')
        self.save_scan()
        self.assertEqual(self.total(), 3250)
        self.assertEqual({r['items'][0]['price'] for r in self.rows()}, {'12.50', '20.00'})

    def test_mixed_currency_with_unsaved_lines_blocks_export(self):
        self.app.receipt_items = [dict(self.item)]
        self.app.receipt_currency = 'USD'
        self.app.receipt_business = 'Shop'
        write = Mock()
        self.saved_job(self.app, 'Save', write, Mock(), 'scan')
        write.assert_not_called()
        self.assertEqual(self.total(), 0)

    def test_no_customer_output_writes_accidentally_hooked_to_previews(self):
        for filename in ('app.py', 'cards_ui.py', 'forms_ui.py', 'scanner_ui.py',
                         'collage_ui.py', 'ocr_ui.py'):
            tree = ast.parse((REPO / filename).read_text())
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute): continue
                if call.func.attr != 'saved_job': continue
                if call.args and isinstance(call.args[0], ast.Constant):
                    description = call.args[0].value.lower()
                    for word in ('preview', 'recovery', 'adding', 'rendering', 'scanning one'):
                        self.assertNotIn(word, description)
        self.assertIn("button(quick,'Today'", (REPO / 'sales_ui.py').read_text())
        self.assertIn("button(quick,'This month'", (REPO / 'sales_ui.py').read_text())

    def test_pending_saved_sale_survives_customer_reset(self):
        with patch('sales_store.record_sale', side_effect=OSError('offline')):
            self.save_scan()
        self.assertEqual(sales_outbox.pending_count(), 1)
        self.assertTrue(self.app.reset_receipt())
        self.assertEqual(self.app.receipt_items, [])
        self.assertEqual(sales_outbox.retry_pending()['recorded'], 1)
        self.assertEqual(self.total(), 1250)

    def test_no_durable_copy_blocks_receipt_clear_until_retried(self):
        with patch('sales_outbox._remember', side_effect=OSError('disk full')), \
             patch('sales_store.record_sale', side_effect=OSError('offline')):
            self.save_scan()
        self.assertFalse(self.app.reset_receipt())
        self.assertEqual(len(self.app.receipt_items), 1)
        self.receipt_screen(self.app)
        self.buttons['Record sale']()
        self.assertTrue(self.app.reset_receipt())
        self.assertEqual(self.total(), 1250)

    def test_failed_manual_receipt_pdf_does_not_queue_or_count(self):
        self.receipt_screen(self.app)
        self.buttons['Add service']()
        self.files.asksaveasfilename.return_value = str(self.root / 'receipt.pdf')
        with patch('services.export_document_pdf', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.buttons['Save receipt PDF']()
        self.assertEqual(self.total(), 0)
        self.assertEqual(sales_outbox.pending_count(), 0)
        self.buttons['Undo last item']()
        self.assertEqual(self.app.receipt_items, [])

    def test_manual_receipt_retry_preserves_save_day_and_station(self):
        class SaveDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromisoformat('2026-11-30T23:59:59+02:00')
            def astimezone(self, tz=None):
                return self if tz is None else super().astimezone(tz)
        self.ns['datetime'] = SaveDate
        settings_store.save_multi_pc_settings('Original station')
        self.receipt_screen(self.app)
        self.buttons['Add service']()
        self.files.asksaveasfilename.return_value = str(self.root / 'receipt.pdf')
        with patch('sales_store.record_sale', side_effect=OSError('offline')):
            with self.assertRaisesRegex(ValueError, 'Receipt PDF saved'):
                self.buttons['Save receipt PDF']()
        self.assertTrue((self.root / 'receipt.pdf').exists())
        settings_store.save_multi_pc_settings('Different station')
        sales_outbox.retry_pending()
        row = self.rows()[0]
        self.assertEqual(row['created_at'], '2026-11-30T23:59:59+02:00')
        self.assertEqual(row['station'], 'Original station')

    def test_all_voided_receipt_cannot_be_exported_as_billable(self):
        self.save_scan()
        sales_store.void_sale(self.rows()[0]['sale_id'])
        self.receipt_screen(self.app)
        self.buttons['Save receipt PDF']()
        self.files.asksaveasfilename.assert_not_called()
        self.assertTrue(self.app.receipt_items[0]['_voided'])
        self.assertEqual(self.total(), 0)

    def install_real_job_pump(self):
        self.ns.update(queue=queue, threading=threading,
                       tk=SimpleNamespace(TclError=type('TclError', (Exception,), {})))
        self.app.events = queue.Queue()
        self.app._active_job_id = None
        self.app._log_exception = Mock()
        self.app._quiet_job_error = lambda _: False
        self.timers = []
        self.app.after = lambda delay, callback: self.timers.append((delay, callback))
        job = desktop_method('job', self.ns)
        self.app.job = lambda *a, **k: job(self.app, *a, **k)
        pump = desktop_method('poll_events', self.ns)
        self.app.poll_events = lambda: pump(self.app)

    def test_real_worker_records_before_main_thread_receipt_update(self):
        self.install_real_job_pump()
        self.save_scan()
        event = self.app.events.get(timeout=5)
        self.assertEqual(event[0], 'ok')
        self.assertEqual(self.total(), 1250)
        self.assertEqual(self.app.receipt_items, [])
        self.app.events.put(event)
        self.app.poll_events()
        self.assertFalse(self.app.busy)
        self.assertEqual(len(self.app.receipt_items), 1)
        self.assertTrue(self.app.receipt_recorded)
        self.app._log_exception.assert_not_called()

    def test_watchdog_late_save_failure_keeps_retry_without_leaking_to_next_customer(self):
        self.install_real_job_pump()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def write():
            started.set()
            if not release.wait(5): raise RuntimeError('test did not release writer')
            (self.root / 'late.txt').write_text('private old customer')
        with patch('sales_store.record_sale', side_effect=OSError('offline')):
            self.saved_job(self.app, 'Save late', write, Mock(), 'scan')
            self.assertTrue(started.wait(5))
            self.timers[0][1]()  # Trigger the real watchdog without a 180-second wait.
            self.assertFalse(self.app.busy)
            self.app.reset_receipt()
            release.set()
            event = self.app.events.get(timeout=5)
        self.app.events.put(event)
        self.app.poll_events()
        self.assertEqual(self.app.receipt_items, [])
        self.assertEqual(sales_outbox.pending_count(), 1)
        self.assertEqual(sales_outbox.retry_pending()['recorded'], 1)
        self.assertEqual(self.total(), 1250)

    def test_report_shortcuts_execute_real_callbacks_at_month_boundaries(self):
        tree = ast.parse((REPO / 'sales_ui.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'show_reports')
        entries = []
        def report_entry(*args, **kwargs):
            widget = Widget()
            entries.append(widget)
            return widget
        self.ns.update(tk=SimpleNamespace(Listbox=Widget), Decimal=Decimal,
                       json=__import__('json'), timedelta=timedelta)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'sales_ui.py', 'exec'), self.ns)
        for today in (date(2026, 12, 1), date(2027, 1, 1), date(2028, 2, 29)):
            class ReportDate(date):
                @classmethod
                def today(cls): return today
            self.ns['date'] = ReportDate
            entries.clear()
            self.ns['show_reports'](self.app, None, Widget, Widget, self.ns['button'], report_entry)
            self.buttons['Today']()
            self.assertEqual([w.get() for w in entries], [today.isoformat(), today.isoformat()])
            self.buttons['This month']()
            self.assertEqual([w.get() for w in entries], [today.replace(day=1).isoformat(), today.isoformat()])
            self.buttons['Retry pending saved sales']()

    def test_real_customer_export_matrix_enters_ledger_and_receipt(self):
        from PIL import Image
        from pypdf import PdfWriter, PdfReader
        import cards_renderer, forms_renderer, collage, projects
        from templates import CATEGORIES, NAMES
        settings_store.save_receipt_service_links({key:'Scan job' for key in saved_services.SERVICES})
        image = self.root / 'input.png'
        Image.new('RGB', (100, 100), 'white').save(image)
        source = self.root / 'input.pdf'
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        with source.open('wb') as stream: writer.write(stream)
        sheet = [dict(path=str(image), page=0, x=.1, y=.1, w=.5, h=.5)]
        work = {
            'scan': lambda out: services.images_to_pdf([image], out),
            'images': lambda out: services.images_to_pdf([image], out),
            'merge': lambda out: services.merge_pdf([source, source], out),
            'extract': lambda out: services.extract_pdf(source, '1', out),
            'split': lambda out: services.split_each_page(source, out),
            'unlock': lambda out: services.remove_pdf_password(source, out, ''),
            'career': lambda out: services.export_document_pdf('A saved CV', out, CATEGORIES[0], 0),
            'ocr': lambda out: services.export_text('Recognised text', out),
            'pictures': lambda out: collage.render_pdf(sheet, out),
            'cards': lambda out: services.export_card_pdf(out, 'Business card', 0, cards_renderer.default_fields('Business card')),
            'forms': lambda out: services.export_form_pdf(forms_renderer.default_form_spec(), out),
            'project': lambda out: projects.save('career', dict(category=CATEGORIES[0], template=NAMES[0], fields={}, draft='Saved project', photo=None), out),
        }
        self.assertEqual(set(work), set(saved_services.SERVICES))
        for service, build in work.items():
            with self.subTest(service=service):
                extension = '.txt' if service=='ocr' else '.cafeproject' if service=='project' else '.pdf'
                out = self.root / (service + extension)
                if service=='split':out.mkdir()  # askdirectory supplies an existing folder in the desktop.
                entitlement=patch('license_guard.current_addon_status',return_value={'valid':True}) if service=='unlock' else nullcontext()
                with entitlement:
                    self.saved_job(self.app, 'Save '+service, lambda: build(out), Mock(), service)
                self.assertTrue(out.exists())
                if extension=='.pdf' and service!='split':
                    self.assertGreaterEqual(len(PdfReader(out).pages), 1)
        self.assertEqual(len(self.rows()), len(work))
        self.assertEqual(len(self.app.receipt_items), len(work))
        self.assertEqual(self.total(), 1250*len(work))
        self.receipt_screen(self.app)
        out = self.root / 'all-services-receipt.pdf'
        self.files.asksaveasfilename.return_value = str(out)
        self.buttons['Save receipt PDF']()
        self.assertIn('TOTAL: R150.00', '\n'.join(p.extract_text() for p in PdfReader(out).pages))
        self.assertEqual(self.total(), 15000)


if __name__ == '__main__':
    unittest.main()
