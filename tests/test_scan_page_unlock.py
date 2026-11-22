import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image
from pypdf import PdfReader

import admin_keygen
import cloud_sync
import license_guard
import services


class ScanPageUnlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mid = 'CD-1234-5678-9ABC'
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.priv = license_guard._b64u_encode(raw)
        self.pub = license_guard._b64u_encode(pub)
        self.issue_date = dt.date(2026, 1, 1)
        self.mid_patch = patch.object(license_guard, 'get_machine_id', return_value=self.mid)
        self.mid_patch.start()
        self.addCleanup(self.mid_patch.stop)
        self.dir_patch = patch.object(license_guard, 'host_license_dir', return_value=self.root / 'license')
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.pub_patch = patch.object(license_guard, 'configured_public_key_b64', return_value=self.pub)
        self.pub_patch.start()
        self.addCleanup(self.pub_patch.stop)

    def make_token(self, feature=license_guard.FEATURE_SCAN_PAGES):
        return admin_keygen.sign_addon_token(self.mid, feature=feature, issued_date=self.issue_date, private_key_b64=self.priv)

    def make_tiff(self, pages):
        path = self.root / f'{pages}.tiff'
        images = [Image.new('RGB', (12, 12), (i * 30, 0, 0)) for i in range(pages)]
        images[0].save(path, save_all=True, append_images=images[1:])
        return path

    def test_free_limit_per_job_and_tiff_frames(self):
        five, six = self.make_tiff(5), self.make_tiff(6)
        out = self.root / 'scan.pdf'
        services.check_scan_page_limit(5)
        services.images_to_pdf([five], out)
        self.assertEqual(len(PdfReader(out).pages), 5)
        old = out.read_bytes()
        with self.assertRaisesRegex(ValueError, '5 pages per job.*Contact the team'):
            services.check_scan_page_limit(6)
        with self.assertRaisesRegex(ValueError, '5 pages per job.*Contact the team'):
            services.images_to_pdf([six], out)
        self.assertEqual(out.read_bytes(), old)
        self.assertEqual(list(self.root.glob('.cafedesk-*')), [])
        # A new job is allowed to use another five pages.
        services.images_to_pdf([five], self.root / 'another.pdf')

    def test_permanent_signed_key_unlocks_and_is_machine_bound(self):
        token, payload = self.make_token()
        self.assertEqual(payload['exp'], 'permanent')
        info = license_guard.verify_addon_token(token, feature=license_guard.FEATURE_SCAN_PAGES, public_key_b64=self.pub, expected_machine_id=self.mid, today=dt.date(2099, 1, 1))
        self.assertTrue(info['permanent'])
        self.assertIsNone(info['days_remaining'])
        with self.assertRaisesRegex(license_guard.LicenseError, 'another computer'):
            license_guard.verify_addon_token(token, feature=license_guard.FEATURE_SCAN_PAGES, public_key_b64=self.pub, expected_machine_id='CD-FFFF-EEEE-DDDD', today=self.issue_date)
        with self.assertRaises(license_guard.LicenseError):
            license_guard.verify_addon_token(token, feature=license_guard.FEATURE_PDF_UNLOCK, public_key_b64=self.pub, expected_machine_id=self.mid, today=self.issue_date)
        license_guard.activate_and_save_addon(token, feature=license_guard.FEATURE_SCAN_PAGES, public_key_b64=self.pub, today=self.issue_date)
        self.assertTrue(license_guard.current_addon_status(license_guard.FEATURE_SCAN_PAGES, today=dt.date(2099, 1, 1))['valid'])
        services.check_scan_page_limit(6)
        out = self.root / 'paid.pdf'
        services.images_to_pdf([self.make_tiff(6)], out)
        self.assertEqual(len(PdfReader(out).pages), 6)
        with patch.object(services, 'MAX_IMAGE_FRAMES', 6):
            with self.assertRaisesRegex(ValueError, 'maximum 6'):
                services.images_to_pdf([self.make_tiff(7)], out)

    def test_standard_or_tampered_key_cannot_unlock_scanning(self):
        token, _ = self.make_token()
        parts = token.split('.')
        payload = json.loads(license_guard._b64u_decode(parts[1]))
        payload['mid'] = 'CD-FFFF-EEEE-DDDD'
        parts[1] = license_guard._b64u_encode(json.dumps(payload).encode())
        with self.assertRaisesRegex(license_guard.LicenseError, 'signature'):
            license_guard.verify_addon_token('.'.join(parts), feature=license_guard.FEATURE_SCAN_PAGES, public_key_b64=self.pub, expected_machine_id=self.mid, today=self.issue_date)
        other, _ = self.make_token(license_guard.FEATURE_PDF_UNLOCK)
        with self.assertRaises(license_guard.LicenseError):
            license_guard.verify_addon_token(other, feature=license_guard.FEATURE_SCAN_PAGES, public_key_b64=self.pub, expected_machine_id=self.mid, today=self.issue_date)
        base, _ = admin_keygen.sign_license_token(self.mid, features=[license_guard.FEATURE_SCAN_PAGES], issued_date=self.issue_date, private_key_b64=self.priv)
        with self.assertRaisesRegex(license_guard.LicenseError, 'separate permanent'):
            license_guard.verify_addon_token(base, feature=license_guard.FEATURE_SCAN_PAGES, public_key_b64=self.pub, expected_machine_id=self.mid, today=self.issue_date)

    def test_manual_scan_key_is_not_pushed_to_dated_cloud_schema(self):
        token, payload = self.make_token()
        record = dict(payload, token=token)
        with patch.object(cloud_sync, 'load_owner_supabase_config', return_value={'supabase_url': 'https://example.com', 'service_role_key': 'secret'}), patch.object(cloud_sync, '_supabase_request') as request:
            self.assertEqual(cloud_sync.owner_push_issued_records_to_supabase([record]), 0)
            request.assert_not_called()
        csv, html = admin_keygen._record_issued_license(payload, token, log_path=self.root / 'issued.jsonl')
        self.assertIn('ACTIVE (permanent)', csv.read_text(encoding='utf-8-sig'))
        self.assertIn('ACTIVE • permanent', html.read_text(encoding='utf-8'))
