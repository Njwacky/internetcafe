"""End-to-end regression tests for release signing & downloaded artifact verification.

Proves `.github/check_release_key.py` and `tools/verify_release_key.py` against
authentic PyInstaller `CArchive` (`MEI\\x0c\\x0b\\x0a\\x0b\\x0e`) + zlib-compressed `PYZ`
archives, native shared-library symbol noise, Inno Setup single-file installers,
cross-Python-version bytecode inspection, and Ed25519 release-signature verification.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import marshal
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cloud_sync  # noqa: E402
import license_guard  # noqa: E402
from tools import verify_release_key  # noqa: E402

DEMO_KEY = 'tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI'


def _gen_keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    priv_b64 = base64.urlsafe_b64encode(
        priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode('ascii').rstrip('=')
    pub_b64 = base64.urlsafe_b64encode(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode('ascii').rstrip('=')
    return priv_b64, pub_b64


def _build_pyz_bytes(modules: dict[str, str]) -> bytes:
    """Build an authentic PyInstaller `PYZ` (`ZlibArchive`) byte blob using stdlib."""
    header_len = 17
    buf = bytearray(b'\x00' * header_len)
    toc: list[tuple[str, tuple[int, int, int]]] = []
    for mod_name, source in modules.items():
        code_obj = compile(source, f'{mod_name}.py', 'exec')
        compressed = zlib.compress(marshal.dumps(code_obj), 6)
        pos = len(buf)
        buf.extend(compressed)
        toc.append((mod_name, (0, pos, len(compressed))))
    toc_offset = len(buf)
    buf.extend(marshal.dumps(toc))
    buf[0:4] = b'PYZ\x00'
    buf[4:8] = importlib.util.MAGIC_NUMBER
    buf[8:12] = struct.pack('!i', toc_offset)
    return bytes(buf)


def _build_pyinstaller_exe_bytes(pyz_bytes: bytes, trailing_authenticode: bytes = b'') -> bytes:
    """Wrap `pyz_bytes` inside an authentic PyInstaller `CArchive` (`PKG`) appended to an EXE stub."""
    pe_stub = b'MZ' + (b'\x00' * 4094)
    pkg = bytearray()
    entry_offset = 0
    pkg.extend(pyz_bytes)
    toc_offset = len(pkg)

    raw_name = b'PYZ-00.pyz\x00'
    pad_len = (16 - ((18 + len(raw_name)) % 16)) % 16
    padded_name = raw_name + (b'\x00' * pad_len)
    entry_len = 18 + len(padded_name)
    toc_entry = struct.pack(
        '!IIIIBc',
        entry_len,
        entry_offset,
        len(pyz_bytes),
        len(pyz_bytes),
        0,
        b'z',
    ) + padded_name
    pkg.extend(toc_entry)
    toc_len = len(toc_entry)

    cookie = struct.pack(
        '!8sIIII64s',
        b'MEI\x0c\x0b\x0a\x0b\x0e',
        len(pkg) + 88,
        toc_offset,
        toc_len,
        311,
        b'python311.dll'.ljust(64, b'\x00'),
    )
    pkg.extend(cookie)
    return pe_stub + bytes(pkg) + trailing_authenticode


def _guard_source_with_key(pub_b64: str, *, legacy_literal_demo: bool = False) -> str:
    src = (REPO_ROOT / 'license_guard.py').read_text(encoding='utf-8')
    if legacy_literal_demo:
        src = re.sub(
            r"^DEMO_OWNER_PUBLIC_KEY_B64\s*=\s*.*$",
            f"DEMO_OWNER_PUBLIC_KEY_B64 = '{DEMO_KEY}'",
            src,
            count=1,
            flags=re.MULTILINE,
        )
    updated, count = re.subn(
        r"^DEFAULT_OWNER_PUBLIC_KEY_B64\s*=\s*'[^']*'",
        f"DEFAULT_OWNER_PUBLIC_KEY_B64 = '{pub_b64}'",
        src,
        count=1,
        flags=re.MULTILINE,
    )
    assert count == 1
    return updated


class ReleaseSigningArtifactTests(unittest.TestCase):
    def _run_check_guard(self, args: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != 'CAFE_DESK_PUBLIC_KEY'}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / '.github' / 'check_release_key.py'), *args],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _run_verify_tool(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / 'tools' / 'verify_release_key.py'), *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_dist_dir_and_zip_verifier_catch_demo_key_inside_zlib_compressed_pyinstaller_pyz(self):
        """Regression: PyInstaller zlib-compresses `license_guard` inside `PYZ-00.pyz`."""
        _, own_pub = _gen_keypair()
        pyz = _build_pyz_bytes({'license_guard': _guard_source_with_key(DEMO_KEY)})
        exe_bytes = _build_pyinstaller_exe_bytes(pyz)
        self.assertNotIn(DEMO_KEY.encode('ascii'), exe_bytes, 'PYZ must genuinely compress the key literal')

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / 'CafeDesk'
            (dist / '_internal').mkdir(parents=True)
            (dist / 'CafeDesk.exe').write_bytes(exe_bytes)

            gate = self._run_check_guard(['--dist-dir', str(dist)])
            self.assertEqual(gate.returncode, 1, gate.stdout + gate.stderr)
            self.assertIn('Refusing to package', gate.stdout + gate.stderr)
            self.assertFalse((dist / '_internal' / 'release-key.meta').exists())

            zpath = Path(tmp) / 'CafeDesk-Windows-USB-Portable.zip'
            with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(dist / 'CafeDesk.exe', 'CafeDesk/CafeDesk.exe')

            ver = self._run_verify_tool([str(zpath), '--expect', '-1eFv9Y-3SzW1iqOXIiKDdMswHniwEZdQZfdm8_jgTI'])
            self.assertEqual(ver.returncode, 1, ver.stdout + ver.stderr)
            self.assertIn('DEMO KEY', ver.stdout)

    def test_signed_pyinstaller_build_passes_with_native_symbol_noise_authenticode_and_docs(self):
        """Signed builds pass `--dist-dir`, stamp `release-key.meta`, and ignore `.pyd`/`.dll` symbol noise."""
        # Also test an identifier-only 43-char key (no `-` hyphen), which CPython interns (`0xda`).
        ident_pub = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq'
        pyz = _build_pyz_bytes({'license_guard': _guard_source_with_key(ident_pub)})
        authenticode_tail = b'\x30\x82\x04\x00PKCS7-AUTHENTICODE-SIGNATURE' + (b'\xab' * 8192)
        exe_bytes = _build_pyinstaller_exe_bytes(pyz, trailing_authenticode=authenticode_tail)

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / 'CafeDesk'
            (dist / '_internal').mkdir(parents=True)
            (dist / 'CafeDesk.exe').write_bytes(exe_bytes)
            # Native `.pyd`/`.dll` extensions contain hundreds of 43+ char symbol names.
            fake_pyd = (
                b'\x7fELF'
                + b'AEADDecryptionContextfinalize_with_tagtagIn\x00'
                + b'1decrypt_dercertificateprivate_keyoptionsde\x00'
            )
            (dist / '_internal' / '_rust.pyd').write_bytes(fake_pyd)
            (dist / 'README.md').write_text(f'Demo key example: {DEMO_KEY}\n', encoding='utf-8')

            gate = self._run_check_guard(['--dist-dir', str(dist)], {'CAFE_DESK_PUBLIC_KEY': ident_pub})
            self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
            meta_file = dist / '_internal' / 'release-key.meta'
            self.assertTrue(meta_file.is_file())
            self.assertEqual(
                meta_file.read_text(encoding='utf-8').strip(),
                f'CAFEDESK-EMBEDDED-PUBLIC-KEY-V1:{ident_pub}',
            )

            # Mismatched CAFE_DESK_PUBLIC_KEY must be caught by --dist-dir.
            _, other_pub = _gen_keypair()
            mismatch = self._run_check_guard(['--dist-dir', str(dist)], {'CAFE_DESK_PUBLIC_KEY': other_pub})
            self.assertEqual(mismatch.returncode, 1, mismatch.stdout + mismatch.stderr)
            self.assertIn('mismatch', (mismatch.stdout + mismatch.stderr).lower())

            zpath = Path(tmp) / 'CafeDesk-Windows-USB-Portable.zip'
            with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in dist.rglob('*'):
                    if p.is_file():
                        zf.write(p, f'CafeDesk/{p.relative_to(dist).as_posix()}')

            ver = self._run_verify_tool([str(zpath), '--expect', ident_pub])
            self.assertEqual(ver.returncode, 0, ver.stdout + ver.stderr)
            self.assertIn('YOUR KEY', ver.stdout)
            self.assertNotIn('AEADDecryptionContext', ver.stdout)
            self.assertIn(hashlib.sha256(ident_pub.encode('ascii')).hexdigest(), ver.stdout)

    def test_legacy_v085_dual_constant_pyz_and_cross_python_fallback_extract_owner_key(self):
        """Builds where `DEMO_OWNER_PUBLIC_KEY_B64` was a plain literal AND cross-Python marshal fallback both work."""
        _, own_pub = _gen_keypair()
        legacy_src = _guard_source_with_key(own_pub, legacy_literal_demo=True)
        pyz = _build_pyz_bytes({'license_guard': legacy_src})
        exe_bytes = _build_pyinstaller_exe_bytes(pyz)

        with tempfile.TemporaryDirectory() as tmp:
            exe_path = Path(tmp) / 'CafeDesk.exe'
            exe_path.write_bytes(exe_bytes)

            # 1) Same-Python-version path (`marshal.loads` + `dis`)
            res = self._run_verify_tool([str(exe_path), '--expect', own_pub])
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn('YOUR KEY', res.stdout)
            self.assertNotIn('DEMO KEY', res.stdout)

            # 2) Cross-Python-version path (simulate Python 3.12+ rejecting Python 3.11 `TYPE_CODE`)
            real_loads = marshal.loads

            def reject_code_objects(blob: bytes):
                obj = real_loads(blob)
                if hasattr(obj, 'co_code'):
                    raise ValueError('bad marshal data (simulated cross-version code object)')
                return obj

            with mock.patch.object(verify_release_key.marshal, 'loads', side_effect=reject_code_objects):
                with exe_path.open('rb') as fh:
                    extracted = verify_release_key._extract_pyinstaller_stream_keys(fh, DEMO_KEY)
            self.assertEqual(extracted, [own_pub])

    def test_verify_tool_does_not_treat_applied_owner_key_in_license_guard_as_demo_key(self):
        """Regression: running `--apply-public-key` locally must not make `verify_release_key.py` flag the owner key as DEMO."""
        self.assertEqual(verify_release_key._demo_key(), DEMO_KEY)
        self.assertEqual(license_guard.DEMO_OWNER_PUBLIC_KEY_B64, DEMO_KEY)

    def test_inno_setup_exe_stamp_and_legacy_lzma_diagnostic(self):
        """Single-file `CafeDesk-Setup.exe` verifies via `release-key.meta` stamp and diagnoses legacy unstamped builds."""
        priv_b64, own_pub = _gen_keypair()
        _, other_pub = _gen_keypair()

        with tempfile.TemporaryDirectory() as tmp:
            stamped_setup = Path(tmp) / 'CafeDesk-Setup.exe'
            stamped_setup.write_bytes(
                b'MZ'
                + (b'\x00' * 1024)
                + b'Inno Setup Setup Data (6.4.0)\x00'
                + b'TSetupWizardFormIdentifierNoise43CharsLong!!\x00'
                + f'CAFEDESK-EMBEDDED-PUBLIC-KEY-V1:{own_pub}\n'.encode('ascii')
                + os.urandom(4096)
            )
            ok = self._run_verify_tool([str(stamped_setup), '--expect', own_pub])
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn('YOUR KEY', ok.stdout)

            wrong = self._run_verify_tool([str(stamped_setup), '--expect', other_pub])
            self.assertEqual(wrong.returncode, 1, wrong.stdout + wrong.stderr)

            # Also verify `--verify-release-sig` end-to-end against `cloud_sync.verify_release_signature`!
            sha_hex = hashlib.sha256(stamped_setup.read_bytes()).hexdigest()
            priv = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(priv_b64 + '='))
            msg = f'CAFE-DESK-RELEASE-V1:0.8.5:{sha_hex}'.encode('ascii')
            sig_b64 = base64.urlsafe_b64encode(priv.sign(msg)).decode('ascii').rstrip('=')

            sig_ok = self._run_verify_tool([
                str(stamped_setup),
                '--expect', own_pub,
                '--verify-release-sig', 'v0.8.5', sig_b64,
            ])
            self.assertEqual(sig_ok.returncode, 0, sig_ok.stdout + sig_ok.stderr)
            self.assertIn('Release signature OK', sig_ok.stdout)
            self.assertTrue(cloud_sync.verify_release_signature('0.8.5', sha_hex, sig_b64, own_pub))

            sig_bad = self._run_verify_tool([
                str(stamped_setup),
                '--expect', own_pub,
                '--verify-release-sig', '0.8.6', sig_b64,
            ])
            self.assertEqual(sig_bad.returncode, 1, sig_bad.stdout + sig_bad.stderr)
            self.assertIn('release signature verification FAILED', sig_bad.stdout)

            # Pre-stamp Inno Setup installer must report a clear diagnostic instead of Delphi symbol noise.
            legacy_setup = Path(tmp) / 'Legacy-Setup.exe'
            legacy_setup.write_bytes(
                b'MZ'
                + b'Inno Setup Setup Data (6.4.0)\x00'
                + b'TSetupWizardFormIdentifierNoise43CharsLongXX\x00'
            )
            leg = self._run_verify_tool([str(legacy_setup), '--expect', own_pub])
            self.assertEqual(leg.returncode, 1, leg.stdout + leg.stderr)
            self.assertIn('solid LZMA2 compression', leg.stdout)
            self.assertNotIn('TSetupWizardFormIdentifierNoise43CharsLongXX', leg.stdout)


if __name__ == '__main__':
    unittest.main()
