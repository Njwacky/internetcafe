"""Regression and real-process integration tests for Café Desk's in-app self-updater."""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app_updater
import cloud_sync

REPO = Path(__file__).resolve().parent.parent


def _make_keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    priv_b64 = base64.urlsafe_b64encode(priv_raw).decode('ascii').rstrip('=')
    pub_b64 = base64.urlsafe_b64encode(pub_raw).decode('ascii').rstrip('=')
    return priv_b64, pub_b64


def _build_portable_zip_bytes(
    exe_name: str = 'CafeDesk.exe',
    exe_bytes: bytes = b'MZ' + b'\x90' * 5000,
    nested_root: str = 'CafeDesk',
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    buf = io.BytesIO()
    prefix = f'{nested_root}/' if nested_root else ''
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(f'{prefix}{exe_name}')
        info.external_attr = (0o755 & 0xFFFF) << 16
        zf.writestr(info, exe_bytes)
        zf.writestr(f'{prefix}_internal/new_runtime.dat', b'v0.8.5-runtime')
        for rel, data in (extra_files or {}).items():
            zf.writestr(rel, data)
    return buf.getvalue()


class _FakeHTTPStream:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.headers = {'Content-Length': str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _extract_cafe_desk_methods(*names: str) -> dict:
    src = (REPO / 'app.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CafeDesk')
    wanted = set(names)
    ns = {
        'APP_BUILD': '0.8.4',
        'app_updater': app_updater,
        'cloud_sync': cloud_sync,
        'time': time,
    }
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), ns)
    return ns


class InAppUpdaterUnitTests(unittest.TestCase):
    def test_detect_install_mode_source_installed_and_portable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / 'CafeDesk.exe'
            exe.write_bytes(b'MZ' + b'\x00' * 5000)

            self.assertEqual(
                app_updater.detect_install_mode(frozen=False, executable=exe, platform_name='win32'),
                'source',
            )
            self.assertEqual(
                app_updater.detect_install_mode(frozen=True, executable=exe, platform_name='win32'),
                'portable',
            )
            (root / 'unins000.exe').write_bytes(b'MZ')
            self.assertEqual(
                app_updater.detect_install_mode(frozen=True, executable=exe, platform_name='win32'),
                'installed',
            )
            self.assertEqual(
                app_updater.detect_install_mode(frozen=True, executable=exe, platform_name='linux'),
                'portable',
            )

    def test_select_release_package_prefers_portable_zip_for_portable_mode(self):
        rel = {
            'latest_version': '0.8.5',
            'download_url': 'https://example.com/CafeDesk-Setup.exe',
            'sha256': 'a' * 64,
            'signature': 'sig-exe',
            'portable_download_url': 'https://example.com/CafeDesk-Windows-USB-Portable.zip',
            'portable_sha256': 'b' * 64,
            'portable_signature': 'sig-zip',
        }
        inst = app_updater.select_release_package(rel, 'installed', platform_name='win32')
        self.assertEqual(inst['kind'], 'exe')
        self.assertEqual(inst['signature'], 'sig-exe')

        port = app_updater.select_release_package(rel, 'portable', platform_name='win32')
        self.assertEqual(port['kind'], 'zip')
        self.assertEqual(port['signature'], 'sig-zip')

        # Portable Windows still works when a release only published Setup.exe
        exe_only = {
            'latest_version': '0.8.5',
            'download_url': 'https://example.com/CafeDesk-Setup.exe',
            'sha256': 'a' * 64,
            'signature': 'sig-exe',
        }
        fallback = app_updater.select_release_package(exe_only, 'portable', platform_name='win32')
        self.assertEqual(fallback['kind'], 'exe')

        # Non-Windows refuses .exe-only package cleanly
        with self.assertRaises(app_updater.UnsupportedPlatformUpdateError):
            app_updater.select_release_package(exe_only, 'portable', platform_name='linux')

        # Unsigned release fails closed before downloading
        unsigned = dict(exe_only, signature='')
        with self.assertRaises(app_updater.UnsignedUpdateError):
            app_updater.select_release_package(unsigned, 'installed', platform_name='win32')

    def test_extract_portable_zip_handles_nested_folder_and_strips_protected_dirs(self):
        raw_zip = _build_portable_zip_bytes(
            extra_files={
                'CafeDesk/CafeDeskData/settings.json': b'{"wipe":"never"}',
                'CafeDesk/owner-keys/private.key': b'secret',
                'CafeDesk/admin_keygen.py': b'forbidden',
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / 'pkg.zip'
            zpath.write_bytes(raw_zip)
            payload_root = app_updater.extract_verified_portable_zip(
                zpath,
                Path(tmp) / 'extracted',
                exe_name='CafeDesk.exe',
            )
            self.assertEqual(payload_root.name, 'CafeDesk')
            self.assertTrue((payload_root / 'CafeDesk.exe').is_file())
            self.assertTrue((payload_root / '_internal' / 'new_runtime.dat').is_file())
            self.assertFalse((payload_root / 'CafeDeskData').exists())
            self.assertFalse((payload_root / 'owner-keys').exists())
            self.assertFalse((payload_root / 'admin_keygen.py').exists())

    def test_extract_portable_zip_blocks_zip_slip_absolute_paths_and_symlinks(self):
        for bad_entry, is_symlink in (
            ('../outside.dll', False),
            ('CafeDesk/../../escape.exe', False),
            ('/etc/passwd', False),
            ('C:/Windows/win.ini', False),
            ('CafeDesk/link_out', True),
        ):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as zf:
                zf.writestr('CafeDesk/CafeDesk.exe', b'MZ' + b'\x00' * 5000)
                zi = zipfile.ZipInfo(bad_entry)
                if is_symlink:
                    zi.external_attr = (0o120777 & 0xFFFF) << 16
                zf.writestr(zi, b'bad')
            with tempfile.TemporaryDirectory() as tmp:
                zpath = Path(tmp) / 'bad.zip'
                zpath.write_bytes(buf.getvalue())
                with self.assertRaises(ValueError):
                    app_updater.extract_verified_portable_zip(zpath, Path(tmp) / 'out')

    def test_prepare_in_app_update_stages_in_temp_and_cleans_up_on_bad_binary(self):
        priv_b64, pub_b64 = _make_keypair()
        exe_bytes = b'MZ' + b'\x90' * 6000
        good_sha = hashlib.sha256(exe_bytes).hexdigest()
        good_sig = cloud_sync.sign_release_sha256('0.8.5', good_sha, priv_b64)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / 'Programs' / 'Café Desk'
            app_dir.mkdir(parents=True)
            target_exe = app_dir / 'CafeDesk.exe'
            target_exe.write_bytes(b'MZ' + b'\x00' * 5000)
            staging_root = root / 'temp_staging'
            data_dir = root / 'appdata'

            rel = {
                'latest_version': '0.8.5',
                'download_url': 'https://releases.example.com/CafeDesk-Setup.exe',
                'sha256': good_sha,
                'signature': good_sig,
            }
            progress_seen = []
            with mock.patch.dict(os.environ, {'CAFE_DESK_PUBLIC_KEY': pub_b64}, clear=False):
                with mock.patch('urllib.request.urlopen', return_value=_FakeHTTPStream(exe_bytes)):
                    prepared = app_updater.prepare_in_app_update(
                        rel,
                        install_mode='installed',
                        executable=target_exe,
                        data_dir=data_dir,
                        staging_parent=staging_root,
                        progress_cb=lambda d, t: progress_seen.append((d, t)),
                        platform_name='win32',
                    )
            self.assertEqual(prepared.version, '0.8.5')
            self.assertEqual(prepared.package_kind, 'exe')
            self.assertTrue(prepared.package_path.is_relative_to(staging_root.resolve()))
            self.assertTrue(progress_seen)

            # Non-MZ payload (e.g. HTML error page signed accidentally) is rejected and staging is deleted
            html_bytes = b'<html>not an exe</html>' * 400
            html_sha = hashlib.sha256(html_bytes).hexdigest()
            html_sig = cloud_sync.sign_release_sha256('0.8.5', html_sha, priv_b64)
            bad_rel = {
                'latest_version': '0.8.5',
                'download_url': 'https://releases.example.com/CafeDesk-Setup.exe',
                'sha256': html_sha,
                'signature': html_sig,
            }
            shutil_rm = prepared.staging_dir
            app_updater.shutil.rmtree(shutil_rm, ignore_errors=True)
            with mock.patch.dict(os.environ, {'CAFE_DESK_PUBLIC_KEY': pub_b64}, clear=False):
                with mock.patch('urllib.request.urlopen', return_value=_FakeHTTPStream(html_bytes)):
                    with self.assertRaisesRegex(ValueError, 'valid Windows executable'):
                        app_updater.prepare_in_app_update(
                            bad_rel,
                            install_mode='installed',
                            executable=target_exe,
                            data_dir=data_dir,
                            staging_parent=staging_root,
                            platform_name='win32',
                        )
            self.assertEqual(list(staging_root.glob('cafedesk-inapp-update-*')), [])

    def test_windows_updater_script_handles_accented_path_and_silent_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / 'cafedesk-inapp-update-1'
            staging.mkdir()
            pkg = staging / 'CafeDesk-Setup-v0.8.5.exe'
            pkg.write_bytes(b'MZ' + b'\x00' * 5000)
            target_dir = root / 'Local' / 'Programs' / 'Café Desk'
            target_exe = target_dir / 'CafeDesk.exe'
            prepared = app_updater.PreparedUpdate(
                version='0.8.5',
                install_mode='installed',
                package_kind='exe',
                staging_dir=staging,
                package_path=pkg,
                payload_root=None,
                target_dir=target_dir,
                target_exe=target_exe,
                status_file=root / 'appdata' / 'updates' / 'last-update.json',
                sha256='a' * 64,
            )
            spawn_calls = []
            script_path = app_updater.apply_prepared_update(
                prepared,
                parent_pid=43210,
                platform_name='win32',
                popen_fn=lambda *a, **kw: spawn_calls.append((a, kw)),
            )
            self.assertEqual(len(spawn_calls), 1)
            args, kwargs = spawn_calls[0]
            cmd = args[0]
            self.assertIn('-WindowStyle', cmd)
            self.assertIn('Hidden', cmd)
            # Script must have UTF-8 BOM for Windows PowerShell 5.1 and remain pure ASCII
            raw_bytes = script_path.read_bytes()
            self.assertTrue(raw_bytes.startswith(b'\xef\xbb\xbf'))
            script_txt = raw_bytes[3:].decode('ascii')
            for flag in ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CLOSEAPPLICATIONS', '/DIR="'):
                self.assertIn(flag, script_txt)
            # Accented target directory travels via Unicode env, never interpolated into script source
            self.assertEqual(kwargs['env']['CAFEDESK_TARGET_DIR'], str(target_dir))
            self.assertEqual(kwargs['env']['CAFEDESK_PARENT_PID'], '43210')
            self.assertNotEqual(Path(kwargs['cwd']).resolve(), target_dir.resolve())

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX shell integration test')
    def test_posix_end_to_end_live_process_swap_preserves_cafedeskdata_and_relaunches(self):
        priv_b64, pub_b64 = _make_keypair()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / 'USBDrive' / 'Café Desk'
            (app_dir / '_internal').mkdir(parents=True)
            (app_dir / '_internal' / 'stale_old.txt').write_text('old', encoding='utf-8')
            (app_dir / 'CafeDeskData').mkdir()
            customer_settings = app_dir / 'CafeDeskData' / 'settings.json'
            customer_settings.write_text('{"business_name":"Durban Print Hub"}', encoding='utf-8')

            target_exe = app_dir / 'CafeDesk'
            target_exe.write_text('#!/bin/sh\necho old\n', encoding='utf-8')
            os.chmod(target_exe, 0o755)

            relaunch_marker = root / 'relaunched-v085.txt'
            relaunch_env_marker = root / 'relaunched-env.txt'
            new_exe_script = (
                '#!/bin/sh\n'
                f'printf "0.8.5-running" > "{relaunch_marker}"\n'
                f'printf "RESET=%s|TCL=%s|PIDVAR=%s" "${{PYINSTALLER_RESET_ENVIRONMENT:-}}" "${{TCL_LIBRARY:-}}" "${{CAFEDESK_PARENT_PID:-}}" > "{relaunch_env_marker}"\n'
            ).encode('utf-8')
            zip_bytes = _build_portable_zip_bytes(exe_name='CafeDesk', exe_bytes=new_exe_script)
            zip_sha = hashlib.sha256(zip_bytes).hexdigest()
            zip_sig = cloud_sync.sign_release_sha256('0.8.5', zip_sha, priv_b64)

            rel = {
                'latest_version': '0.8.5',
                'download_url': 'https://releases.example.com/CafeDesk-Portable.zip',
                'sha256': zip_sha,
                'signature': zip_sig,
            }
            data_dir = root / 'appdata'
            staging_root = root / 'staging'
            with mock.patch.dict(os.environ, {'CAFE_DESK_PUBLIC_KEY': pub_b64}, clear=False):
                with mock.patch('urllib.request.urlopen', return_value=_FakeHTTPStream(zip_bytes)):
                    prepared = app_updater.prepare_in_app_update(
                        rel,
                        install_mode='portable',
                        executable=target_exe,
                        data_dir=data_dir,
                        staging_parent=staging_root,
                        platform_name='linux',
                    )

            # Simulate the currently running CafeDesk process (with frozen PyInstaller env vars)
            fake_running_app = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.35)'])
            with mock.patch.dict(
                os.environ,
                {
                    '_MEIPASS2': '/tmp/old-meipass',
                    'TCL_LIBRARY': '/tmp/old-tcl',
                    'TK_LIBRARY': '/tmp/old-tk',
                },
                clear=False,
            ):
                app_updater.apply_prepared_update(
                    prepared,
                    parent_pid=fake_running_app.pid,
                    platform_name='linux',
                )
            fake_running_app.wait(timeout=5)

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if relaunch_marker.is_file() and not prepared.staging_dir.exists():
                    break
                time.sleep(0.1)

            self.assertTrue(relaunch_marker.is_file(), 'Updated CafeDesk binary was not relaunched automatically')
            self.assertEqual(relaunch_marker.read_text(encoding='utf-8'), '0.8.5-running')
            self.assertEqual(
                relaunch_env_marker.read_text(encoding='utf-8'),
                'RESET=1|TCL=|PIDVAR=',
            )
            self.assertFalse((app_dir / '_internal' / 'stale_old.txt').exists())
            self.assertTrue((app_dir / '_internal' / 'new_runtime.dat').is_file())
            self.assertFalse((app_dir / '_internal.upd-bak').exists())
            self.assertFalse((app_dir / 'CafeDesk.upd-bak').exists())
            # Customer portable data folder is 100% untouched
            self.assertEqual(
                customer_settings.read_text(encoding='utf-8'),
                '{"business_name":"Durban Print Hub"}',
            )
            # Staging folder cleaned up automatically
            self.assertFalse(prepared.staging_dir.exists())
            # Startup status notice recorded and consumed once
            notice = app_updater.consume_last_update_result(data_dir)
            self.assertIsNotNone(notice)
            self.assertEqual(notice['status'], 'completed')
            self.assertEqual(notice['version'], '0.8.5')
            self.assertIsNone(app_updater.consume_last_update_result(data_dir))

    def test_app_controller_defers_restart_on_unsaved_work_without_redownloading(self):
        app_src = (REPO / 'app.py').read_text(encoding='utf-8')
        # The desktop app must no longer dump installers into ~/Downloads or call os.startfile for updates
        self.assertNotIn("Path.home()/'Downloads'", app_src)
        self.assertNotIn("Path.home() / 'Downloads'", app_src)

        methods = _extract_cafe_desk_methods(
            '_confirm_ready_for_update_restart',
            '_apply_prepared_update_and_restart',
            'start_in_app_update',
            '_consume_startup_update_notice',
        )

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / 'CafeDesk-Setup-v0.8.5.exe'
            pkg_bytes = b'MZ' + b'\x00' * 5000
            pkg.write_bytes(pkg_bytes)
            real_sha = hashlib.sha256(pkg_bytes).hexdigest()
            prepared = app_updater.PreparedUpdate(
                version='0.8.5',
                install_mode='installed',
                package_kind='exe',
                staging_dir=Path(tmp),
                package_path=pkg,
                payload_root=None,
                target_dir=Path(tmp) / 'app',
                target_exe=Path(tmp) / 'app' / 'CafeDesk.exe',
                status_file=Path(tmp) / 'status.json',
                sha256=real_sha,
            )

            applied = []
            destroyed = []
            statuses = []

            class FakeStatus:
                def set(self, val):
                    statuses.append(val)

            class FakeRecovery:
                def __init__(self):
                    self.invalidated = 0

                def invalidate(self):
                    self.invalidated += 1

            class LockedSessionTemp:
                name = str(Path(tmp) / 'session')

                def cleanup(self):
                    # Even if Windows holds a lock on a temp preview image, exit must never block on a modal!
                    raise OSError('preview image still open')

            class FakePool:
                def run(self, fn):
                    pass

            class FakeWorkers:
                _pool = FakePool()

            class FakeSelf:
                busy = False
                receipt_items = []
                unsaved = True
                _available_update = {
                    'ok': True,
                    'update_available': True,
                    'latest_version': '0.8.5',
                    'download_url': 'https://example.com/CafeDesk-Setup.exe',
                    'sha256': real_sha,
                    'signature': 'sig',
                    'release_notes': 'Seamless updates',
                }
                _prepared_update = prepared
                status = FakeStatus()
                _recovery_store = FakeRecovery()
                session_temp = LockedSessionTemp()
                _workers = FakeWorkers()

                def has_unsaved_work(self):
                    return self.unsaved

                def destroy(self):
                    destroyed.append(True)

                def _log_exception(self, where):
                    raise AssertionError(where)

            fake = FakeSelf()
            fake._confirm_ready_for_update_restart = methods['_confirm_ready_for_update_restart'].__get__(fake, FakeSelf)
            fake._apply_prepared_update_and_restart = methods['_apply_prepared_update_and_restart'].__get__(fake, FakeSelf)
            fake.start_in_app_update = methods['start_in_app_update'].__get__(fake, FakeSelf)
            fake._consume_startup_update_notice = methods['_consume_startup_update_notice'].__get__(fake, FakeSelf)

            fake_msgbox = mock.Mock()
            fake_msgbox.askyesno.return_value = False
            fake_saved = mock.Mock()
            fake_saved.unprotected_sales.return_value = []

            with mock.patch.dict(
                methods['_confirm_ready_for_update_restart'].__globals__,
                {'messagebox': fake_msgbox, 'saved_services': fake_saved},
            ), mock.patch.object(app_updater, 'detect_install_mode', return_value='installed'), \
               mock.patch.object(app_updater.sys, 'platform', 'win32'), \
               mock.patch.object(app_updater, 'apply_prepared_update', side_effect=lambda p: applied.append(p)):
                # 1. Staff cancels restart to save customer CV first -> updater NOT spawned, prepared kept!
                fake.start_in_app_update()
                self.assertEqual(applied, [])
                self.assertEqual(destroyed, [])
                self.assertIs(fake._prepared_update, prepared)
                self.assertTrue(any('verified & ready' in s for s in statuses))

                # 2. Staff saves customer work -> clicking Update again applies immediately with ZERO re-download!
                fake.unsaved = False
                fake.start_in_app_update()
                self.assertEqual(applied, [prepared])
                self.assertEqual(destroyed, [True])
                self.assertIsNone(fake._prepared_update)
                self.assertEqual(fake._recovery_store.invalidated, 1)

            # 3. On relaunch, startup notice greets the user in status bar
            statuses.clear()
            with mock.patch.object(
                app_updater,
                'consume_last_update_result',
                return_value={'status': 'completed', 'version': '0.8.5'},
            ):
                fake._consume_startup_update_notice()
            self.assertTrue(any('Updated to Café Desk' in s for s in statuses))

    def test_check_for_app_update_portable_columns_and_v083_db_fallback(self):
        cfg = {'supabase_url': 'https://demo.supabase.co', 'supabase_anon_key': 'anon'}
        # 1. Full schema with portable_* columns
        with mock.patch.object(
            cloud_sync,
            '_supabase_request',
            return_value=[{
                'version': '0.8.5',
                'download_url': 'https://example.com/CafeDesk-Setup.exe',
                'sha256': 'a' * 64,
                'signature': 'sig-exe',
                'portable_download_url': 'https://example.com/CafeDesk-Portable.zip',
                'portable_sha256': 'b' * 64,
                'portable_signature': 'sig-zip',
                'release_notes': 'In-app updater',
                'mandatory': False,
                'published_at': '2026-11-22T00:00:00Z',
            }],
        ):
            res = cloud_sync.check_for_app_update(cfg, current_version='0.8.4')
        self.assertTrue(res['update_available'])
        self.assertEqual(res['signature'], 'sig-exe')
        self.assertEqual(res['portable_signature'], 'sig-zip')

        # 2. v0.8.3 DB lacking portable_* columns preserves `signature` on fallback!
        calls = []

        def fake_v083_db(url, key, method, path, **kw):
            calls.append(path)
            if 'portable_download_url' in path:
                raise RuntimeError('Supabase HTTP 400: column app_releases.portable_download_url does not exist')
            return [{
                'version': '0.8.5',
                'download_url': 'https://example.com/CafeDesk-Setup.exe',
                'sha256': 'a' * 64,
                'signature': 'preserved-owner-sig',
                'release_notes': 'Signed',
                'mandatory': False,
                'published_at': '2026-11-22T00:00:00Z',
            }]

        with mock.patch.object(cloud_sync, '_supabase_request', side_effect=fake_v083_db):
            res2 = cloud_sync.check_for_app_update(cfg, current_version='0.8.4')
        self.assertEqual(len(calls), 2)
        self.assertEqual(res2['signature'], 'preserved-owner-sig')

    def test_5whys_forged_signature_never_touches_network_and_oversized_stream_aborts(self):
        priv_b64, pub_b64 = _make_keypair()
        other_priv, _ = _make_keypair()
        payload = b'MZ' + b'\x90' * 5000
        sha = hashlib.sha256(payload).hexdigest()
        forged_sig = cloud_sync.sign_release_sha256('0.8.5', sha, other_priv)
        good_sig = cloud_sync.sign_release_sha256('0.8.5', sha, priv_b64)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {'CAFE_DESK_PUBLIC_KEY': pub_b64}, clear=False):
                # 1. Forged non-empty signature is rejected BEFORE any outbound HTTP request!
                with mock.patch('urllib.request.urlopen', return_value=_FakeHTTPStream(payload)) as net_mock:
                    with self.assertRaisesRegex(ValueError, 'Security check failed'):
                        cloud_sync.download_and_verify_update(
                            'https://evil.example.com/10gb-bomb.exe',
                            sha,
                            tmp,
                            version_label='0.8.5',
                            expected_signature=forged_sig,
                        )
                    net_mock.assert_not_called()

                # 2. Oversized stream exceeding MAX_UPDATE_DOWNLOAD_BYTES aborts and cleans .part file
                with mock.patch.object(cloud_sync, 'MAX_UPDATE_DOWNLOAD_BYTES', 1024):
                    with mock.patch('urllib.request.urlopen', return_value=_FakeHTTPStream(payload)):
                        with self.assertRaisesRegex(ValueError, 'maximum safe download size'):
                            cloud_sync.download_and_verify_update(
                                'https://releases.example.com/CafeDesk-Setup.exe',
                                sha,
                                tmp,
                                version_label='0.8.5',
                                expected_signature=good_sig,
                            )
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_5whys_toctou_tampering_and_can_apply_in_app_gating(self):
        priv_b64, pub_b64 = _make_keypair()
        exe_bytes = b'MZ' + b'\x90' * 5000
        exe_sha = hashlib.sha256(exe_bytes).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / 'CafeDesk-Setup-v0.8.5.exe'
            pkg.write_bytes(exe_bytes)
            prep = app_updater.PreparedUpdate(
                version='0.8.5',
                install_mode='installed',
                package_kind='exe',
                staging_dir=Path(tmp),
                package_path=pkg,
                payload_root=None,
                target_dir=Path(tmp) / 'app',
                target_exe=Path(tmp) / 'app' / 'CafeDesk.exe',
                status_file=Path(tmp) / 'status.json',
                sha256=exe_sha,
            )
            self.assertTrue(app_updater.is_prepared_update_intact(prep))
            # Mutate 1 byte in %TEMP% after staging -> TOCTOU re-verification rejects it!
            pkg.write_bytes(b'MZ' + b'\x91' + b'\x90' * 4999)
            self.assertFalse(app_updater.is_prepared_update_intact(prep))

        sig = cloud_sync.sign_release_sha256('0.8.5', exe_sha, priv_b64)
        rel = {
            'update_available': True,
            'latest_version': '0.8.5',
            'download_url': 'https://releases.example.com/CafeDesk-Setup.exe',
            'sha256': exe_sha,
            'signature': sig,
        }
        with mock.patch.dict(os.environ, {'CAFE_DESK_PUBLIC_KEY': pub_b64}, clear=False):
            self.assertTrue(app_updater.can_apply_in_app(rel, install_mode='installed', platform_name='win32'))
            self.assertFalse(app_updater.can_apply_in_app(rel, install_mode='source', platform_name='win32'))
            self.assertFalse(app_updater.can_apply_in_app(rel, install_mode='portable', platform_name='linux'))
            self.assertFalse(app_updater.can_apply_in_app(dict(rel, signature=''), install_mode='installed', platform_name='win32'))

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIXhung-PID & chmod test')
    def test_5whys_hung_parent_pid_and_readonly_portable_dir_never_corrupt_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1. Read-only portable target fails BEFORE making any network download
            ro_dir = root / 'readonly_usb'
            ro_dir.mkdir()
            ro_exe = ro_dir / 'CafeDesk'
            ro_exe.write_text('#!/bin/sh\necho v084\n', encoding='utf-8')
            os.chmod(ro_dir, 0o555)
            try:
                if os.geteuid() != 0:  # root ignores 0o555 in containers; test via mock fallback if root
                    with mock.patch('urllib.request.urlopen') as net_mock:
                        with self.assertRaises(PermissionError):
                            app_updater.prepare_in_app_update(
                                {
                                    'latest_version': '0.8.5',
                                    'download_url': 'https://example.com/CafeDesk.zip',
                                    'sha256': 'a' * 64,
                                    'signature': 'sig',
                                },
                                install_mode='portable',
                                executable=ro_exe,
                                platform_name='linux',
                            )
                        net_mock.assert_not_called()
            finally:
                os.chmod(ro_dir, 0o755)

            # 2. If parent PID stays alive past wait window, updater aborts without overwriting or double-launching
            live_dir = root / 'live_app'
            live_dir.mkdir()
            live_exe = live_dir / 'CafeDesk'
            live_exe.write_text('#!/bin/sh\necho original-v084\n', encoding='utf-8')
            os.chmod(live_exe, 0o755)

            payload_dir = root / 'staging' / 'extracted' / 'CafeDesk'
            payload_dir.mkdir(parents=True)
            relaunch_probe = root / 'should-not-relaunch.txt'
            (payload_dir / 'CafeDesk').write_text(f'#!/bin/sh\ntouch "{relaunch_probe}"\n', encoding='utf-8')
            os.chmod(payload_dir / 'CafeDesk', 0o755)

            status_file = root / 'appdata' / 'updates' / 'last-update.json'
            script_txt = app_updater.build_posix_updater_script().replace('"$i" -lt 180', '"$i" -lt 2')
            script_file = root / 'staging' / 'apply-update.sh'
            script_file.write_text(script_txt, encoding='utf-8')
            os.chmod(script_file, 0o700)

            hung_proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            try:
                env = dict(os.environ)
                env.update({
                    'CAFEDESK_PARENT_PID': str(hung_proc.pid),
                    'CAFEDESK_UPDATE_VERSION': '0.8.5',
                    'CAFEDESK_PAYLOAD_ROOT': str(payload_dir),
                    'CAFEDESK_TARGET_DIR': str(live_dir),
                    'CAFEDESK_TARGET_EXE': str(live_exe),
                    'CAFEDESK_STAGING_DIR': str(root / 'staging'),
                    'CAFEDESK_STATUS_FILE': str(status_file),
                })
                subprocess.run(['/bin/sh', str(script_file)], env=env, check=True, timeout=10)
            finally:
                hung_proc.terminate()
                hung_proc.wait(timeout=5)

            self.assertIn('original-v084', live_exe.read_text(encoding='utf-8'))
            self.assertFalse(relaunch_probe.exists())
            notice = app_updater.consume_last_update_result(root / 'appdata')
            self.assertEqual(notice['status'], 'failed')
            self.assertIn('did not close in time', notice['detail'])


if __name__ == '__main__':
    unittest.main()
