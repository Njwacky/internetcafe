"""Release-time guard: refuse to ship a build signed with the sample/demo owner key.

Why this exists
---------------
`license_guard.py` ships with `DEFAULT_OWNER_PUBLIC_KEY_B64`, a throw-away "demo"
public key that lives in this repository. Any artifact built straight from a plain
checkout therefore verifies activation keys against that placeholder instead of the
owner's real keypair — so nobody, including the owner, controls who can issue licences
for it. (The demo *private* half was never committed, so a stranger cannot forge a key
from the public half alone; the problem is loss of control and the inability to revoke,
not immediate forgery. Shipping it is still unacceptable for a paid product.)

The Windows release job must inject the owner's real public key (repository secret
`CAFE_DESK_PUBLIC_KEY`) and apply it before PyInstaller runs. This script makes that
step mandatory for release/packaging builds, and turns it into a loud warning (not a
failure) on pull requests, so contributors can still build and test freely.

Usage
-----
    python .github/check_release_key.py              # enforce (release builds)
    python .github/check_release_key.py --warn-only  # pull-request builds
    python .github/check_release_key.py --dist-dir dist/CafeDesk

Exit codes: 0 = ok / warned, 1 = enforcement failed.
"""
from __future__ import annotations

import argparse
import base64
import dis
import io
import marshal
import os
import re
import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import admin_keygen  # noqa: E402
import license_guard  # noqa: E402


_B64U_RE = re.compile(r'^[A-Za-z0-9_-]+={0,2}$')
RELEASE_KEY_META_PREFIX = 'CAFEDESK-EMBEDDED-PUBLIC-KEY-V1:'
RELEASE_KEY_META_REL = Path('_internal') / 'release-key.meta'

_PKG_COOKIE_MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'
_PKG_COOKIE_FORMAT = '!8sIIII64s'
_PKG_COOKIE_LEN = struct.calcsize(_PKG_COOKIE_FORMAT)
_PKG_TOC_ENTRY_FORMAT = '!IIIIBc'
_PKG_TOC_ENTRY_LEN = struct.calcsize(_PKG_TOC_ENTRY_FORMAT)
_PYZ_MAGIC = b'PYZ\x00'
_SHORT_ASCII_43_RE = re.compile(rb'[\x5a\xda\x7a\xfa]\x2b([A-Za-z0-9_-]{43})')
_LONG_ASCII_43_RE = re.compile(rb'[\x41\xc1\x61\xe1\x73\xf3\x75\xf5]\x2b\x00\x00\x00([A-Za-z0-9_-]{43})')
_PYZ_TOC_FALLBACK_RE = re.compile(
    rb'[\x5a\xda\x7a\xfa]\x0dlicense_guard[\x29\xa9]\x03[\x69\xe9](.{4})[\x69\xe9](.{4})[\x69\xe9](.{4})',
    re.DOTALL,
)


def validate_public_key_material(value: str) -> str:
    """Reject anything that is not a usable Ed25519 PUBLIC key.

    A pasted value can be wrong in ways that are invisible at build time and only
    surface at the customer's desk:

    * an Ed25519 PRIVATE key is also 32 bytes, so pasting `cafedesk_owner.private.key`
      by mistake would be embedded as the verification key and every activation would
      fail with "Configured owner public key is invalid";
    * a `.cafedesk` license token, a PEM block, or a key with a trailing newline
      silently corrupts the embedded constant;
    * a key of the wrong length can never verify a signature.

    Returns the cleaned key, or raises ValueError with a copy-pasteable message.
    """
    raw = (value or '').strip().strip('"').strip("'")
    if not raw:
        raise ValueError('The value is empty.')
    if 'BEGIN' in raw or 'PRIVATE' in raw.upper():
        raise ValueError('That looks like a PEM/private key file. Use the CONTENTS of '
                         'owner-keys/cafedesk_owner.public.txt only.')
    if raw.startswith(('CDLIC1.', 'CDADD1.')):
        raise ValueError('That is a customer license/activation key, not the owner public key.')
    if not _B64U_RE.match(raw):
        raise ValueError('That is not a base64url string. Copy the single line from '
                         'owner-keys/cafedesk_owner.public.txt with no spaces or quotes.')
    padded = raw + '=' * ((4 - len(raw) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ValueError(f'That is not valid base64url ({exc}).') from exc
    if len(decoded) != 32:
        raise ValueError(f'An Ed25519 public key must decode to 32 bytes; this decodes to '
                         f'{len(decoded)}. Copy the line from owner-keys/cafedesk_owner.public.txt.')

    # A 32-byte PRIVATE key is byte-for-byte indistinguishable from a public key by shape,
    # and pasting it would pass every structural check yet break activation on every
    # customer PC ("Configured owner public key is invalid"). On the owner's own machine we
    # can compare against the private key file directly; in CI the file does not exist
    # (it is gitignored), so this is skipped there.
    priv_path = REPO_ROOT / 'owner-keys' / 'cafedesk_owner.private.key'
    if priv_path.is_file():
        try:
            if priv_path.read_text(encoding='utf-8').strip() == raw:
                raise ValueError('That is your PRIVATE signing key. Never put it in a GitHub secret. '
                                 'Use owner-keys/cafedesk_owner.public.txt instead.')
        except OSError:
            pass
    return raw


def _emit(level: str, title: str, message: str) -> None:
    """Emit a GitHub annotation when running in Actions, plain text otherwise."""
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print(f'::{level} title={title}::{message}')
    else:
        print(f'{level.upper()}: {title}: {message}')


def _is_keyish_str(candidate: str) -> bool:
    if len(candidate) != 43 or not _B64U_RE.match(candidate):
        return False
    try:
        return len(base64.urlsafe_b64decode(candidate + '=')) == 32
    except Exception:
        return False


def _extract_keys_from_marshalled_guard(raw_code: bytes, demo_key: str) -> list[str]:
    """Extract `DEFAULT_OWNER_PUBLIC_KEY_B64` from marshalled `license_guard` code bytes."""
    for candidate_blob in (raw_code, raw_code[16:] if len(raw_code) > 16 else b''):
        if not candidate_blob:
            continue
        try:
            code_obj = marshal.loads(candidate_blob)
        except Exception:
            code_obj = None
        if code_obj is not None and hasattr(code_obj, 'co_code'):
            last_const = None
            try:
                for instr in dis.get_instructions(code_obj):
                    if instr.opname == 'LOAD_CONST':
                        last_const = instr.argval
                    elif instr.opname in ('STORE_NAME', 'STORE_GLOBAL') and instr.argval == 'DEFAULT_OWNER_PUBLIC_KEY_B64':
                        if isinstance(last_const, str) and last_const:
                            return [last_const.strip()]
                        last_const = None
                    else:
                        last_const = None
            except Exception:
                pass

    # Cross-Python-version fallback (when the verifier runs on a different Python minor
    # version than the build environment, `marshal.loads` rejects `TYPE_CODE`).
    ordered: list[str] = []
    for regex in (_SHORT_ASCII_43_RE, _LONG_ASCII_43_RE):
        for match in regex.finditer(raw_code):
            val = match.group(1).decode('ascii')
            if _is_keyish_str(val) and val not in ordered:
                ordered.append(val)
    if len(ordered) == 2 and ordered[0] == demo_key and ordered[1] != demo_key:
        return [ordered[1]]
    return ordered


def _extract_guard_keys_from_pyz_bytes(pyz_blob: bytes, demo_key: str) -> list[str]:
    if len(pyz_blob) < 17 or pyz_blob[:4] != _PYZ_MAGIC:
        return []
    toc_offset = struct.unpack('!i', pyz_blob[8:12])[0]
    if toc_offset <= 0 or toc_offset >= len(pyz_blob):
        return []
    toc_bytes = pyz_blob[toc_offset:]
    entry_loc: tuple[int, int] | None = None
    try:
        toc_obj = marshal.loads(toc_bytes)
        if isinstance(toc_obj, dict) and 'license_guard' in toc_obj:
            _, pos, length = toc_obj['license_guard']
            entry_loc = (int(pos), int(length))
        elif isinstance(toc_obj, list):
            for item in toc_obj:
                if isinstance(item, tuple) and len(item) == 2 and item[0] == 'license_guard':
                    _, pos, length = item[1]
                    entry_loc = (int(pos), int(length))
                    break
    except Exception:
        entry_loc = None

    if entry_loc is None:
        m = _PYZ_TOC_FALLBACK_RE.search(toc_bytes)
        if m:
            _, pos, length = (struct.unpack('<i', m.group(i))[0] for i in (1, 2, 3))
            entry_loc = (pos, length)

    if entry_loc is None:
        return []
    pos, length = entry_loc
    if pos < 0 or length <= 0 or pos + length > len(pyz_blob):
        return []
    try:
        raw_code = zlib.decompress(pyz_blob[pos:pos + length])
    except zlib.error:
        return []
    return _extract_keys_from_marshalled_guard(raw_code, demo_key)


def _find_pkg_cookie(fh: io.BufferedReader | io.BytesIO, max_tail: int = 16 * 1024 * 1024) -> int:
    fh.seek(0, os.SEEK_END)
    end_pos = fh.tell()
    min_pos = max(0, end_pos - max_tail)
    chunk_size = 65536
    while end_pos - min_pos >= len(_PKG_COOKIE_MAGIC):
        start_pos = max(min_pos, end_pos - chunk_size)
        fh.seek(start_pos, os.SEEK_SET)
        buf = fh.read(end_pos - start_pos)
        pos = buf.rfind(_PKG_COOKIE_MAGIC)
        if pos != -1:
            return start_pos + pos
        if start_pos == min_pos:
            break
        end_pos = start_pos + len(_PKG_COOKIE_MAGIC) - 1
    return -1


def extract_pyinstaller_guard_keys(path: Path, demo_key: str) -> list[str]:
    """Extract `DEFAULT_OWNER_PUBLIC_KEY_B64` from a PyInstaller EXE/PKG/PYZ if present."""
    found: list[str] = []
    try:
        with path.open('rb') as fh:
            head4 = fh.read(4)
            if head4 == _PYZ_MAGIC:
                fh.seek(0, os.SEEK_SET)
                return _extract_guard_keys_from_pyz_bytes(fh.read(), demo_key)

            cookie_pos = _find_pkg_cookie(fh)
            if cookie_pos < 0:
                return []
            fh.seek(cookie_pos, os.SEEK_SET)
            cookie_buf = fh.read(_PKG_COOKIE_LEN)
            if len(cookie_buf) != _PKG_COOKIE_LEN:
                return []
            _, archive_len, toc_off, toc_len, _, _ = struct.unpack(_PKG_COOKIE_FORMAT, cookie_buf)
            end_offset = cookie_pos + _PKG_COOKIE_LEN
            if archive_len <= 0 or toc_len <= 0 or archive_len > end_offset or toc_off + toc_len > archive_len:
                return []
            pkg_start = end_offset - archive_len
            fh.seek(pkg_start + toc_off, os.SEEK_SET)
            toc_data = fh.read(toc_len)
            cur = 0
            while cur + _PKG_TOC_ENTRY_LEN <= len(toc_data):
                entry_len, entry_off, data_len, _, comp_flag, typecode = struct.unpack(
                    _PKG_TOC_ENTRY_FORMAT, toc_data[cur:cur + _PKG_TOC_ENTRY_LEN]
                )
                if entry_len < _PKG_TOC_ENTRY_LEN or cur + entry_len > len(toc_data):
                    break
                raw_name = toc_data[cur + _PKG_TOC_ENTRY_LEN:cur + entry_len]
                cur += entry_len
                name = raw_name.rstrip(b'\x00').decode('utf-8', errors='replace')
                tc = typecode.decode('ascii', errors='replace')
                if tc == 'z':
                    fh.seek(pkg_start + entry_off, os.SEEK_SET)
                    pyz_blob = fh.read(data_len)
                    if comp_flag:
                        try:
                            pyz_blob = zlib.decompress(pyz_blob)
                        except zlib.error:
                            continue
                    for k in _extract_guard_keys_from_pyz_bytes(pyz_blob, demo_key):
                        if k not in found:
                            found.append(k)
                elif tc in ('m', 'M', 's') and name in ('license_guard', 'license_guard.py', 'license_guard.pyc'):
                    fh.seek(pkg_start + entry_off, os.SEEK_SET)
                    mod_blob = fh.read(data_len)
                    if comp_flag:
                        try:
                            mod_blob = zlib.decompress(mod_blob)
                        except zlib.error:
                            continue
                    for k in _extract_keys_from_marshalled_guard(mod_blob, demo_key):
                        if k not in found:
                            found.append(k)
    except OSError:
        return []
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Verify the embedded owner Ed25519 public key is not the demo key.')
    parser.add_argument('--warn-only', action='store_true', help='Report but do not fail when the demo key is active.')
    parser.add_argument('--allow-demo', action='store_true',
                        help='Build a throw-away test copy with the demo key (also enabled by CAFE_DESK_ALLOW_DEMO_KEY=1).')
    parser.add_argument('--apply', action='store_true', help='Write $CAFE_DESK_PUBLIC_KEY into license_guard.py before checking (fails if unset).')
    parser.add_argument('--apply-if-available', action='store_true', help='Same as --apply, but continue quietly when the secret is unset (pull requests).')
    parser.add_argument('--dist-dir', metavar='FOLDER',
                        help='Scan a built app folder (e.g. dist/CafeDesk) for the demo key. Used to block '
                             'packaging an installer from a demo-key build, even in throw-away/PR runs.')
    args = parser.parse_args(argv)

    if args.dist_dir:
        # Deliberately ignores --warn-only / --allow-demo / CAFE_DESK_ALLOW_DEMO_KEY: those
        # let a contributor produce a test app FOLDER, but an installable Setup.exe is a
        # customer-facing artifact and must never wrap a demo-key app.
        return _check_dist_dir(Path(args.dist_dir))

    secret = os.environ.get('CAFE_DESK_PUBLIC_KEY', '').strip()

    apply_requested = args.apply or args.apply_if_available
    if apply_requested:
        if not secret:
            if args.apply_if_available:
                _emit('notice', 'No signing key in this build',
                      'CAFE_DESK_PUBLIC_KEY is not set; this pull-request artifact keeps the demo key '
                      'and must not be distributed to a customer.')
                return 0
            _emit('error', 'Missing signing key', 'CAFE_DESK_PUBLIC_KEY is empty; cannot apply the owner public key.')
            return 1
        try:
            secret = validate_public_key_material(secret)
        except ValueError as exc:
            _emit('error', 'Invalid CAFE_DESK_PUBLIC_KEY secret',
                  f'{exc} (Expected the 32-byte Ed25519 PUBLIC key from '
                  f'owner-keys/cafedesk_owner.public.txt.)')
            return 1
        try:
            admin_keygen.apply_public_key_to_guard(secret)
        except (RuntimeError, OSError) as exc:
            _emit('error', 'Could not apply owner public key', str(exc))
            return 1

    # Always read the value from the SOURCE FILE that PyInstaller bundles. Reading the
    # imported module would still see the pre-`--apply` value in this process, which
    # would wrongly block every correctly-signed release build.
    active = _key_in_source()
    try:
        active = validate_public_key_material(active)
    except ValueError as exc:
        _emit('error', 'Embedded owner key is unusable', str(exc))
        return 1
    demo_active = active == _pristine_demo_key()
    if demo_active:
        _emit(
            'warning',
            'DEMO SIGNING KEY ACTIVE',
            'This build would verify licenses against the public demo key that ships in the repo. '
            'Set the CAFE_DESK_PUBLIC_KEY repository secret (or run --apply in the release job).',
        )
        if args.warn_only or args.allow_demo or os.environ.get('CAFE_DESK_ALLOW_DEMO_KEY', '').strip() in ('1', 'true', 'yes', 'on'):
            print('Demo key active — allowed for this throw-away/test build only. '
                  'NEVER hand this artifact to a customer.')
            return 0
        _emit('error', 'Release blocked', 'Refusing to package a build signed with the demo key.')
        return 1

    print(f'Owner signing key OK ({active[:12]}…).')
    return 0


# Documentation / data files bundled into the app folder (README.md, the HTML manuals,
# HOW-TO-RUN-FROM-USB.txt, fonts, icons, ...). These may *mention* the demo key as an
# example — the repo README documents it verbatim for the owner's find/replace step — and
# that is harmless: the key only matters when it is the active verification constant in
# Python bytecode. Scanning them would block every correctly-signed build, so the
# dist-folder gate skips them and inspects only files that can carry executable bytecode.
# Everything not listed here (unknown or missing extensions included) is still scanned,
# so a new code-bearing file type can never slip past the gate unnoticed.
_DATA_FILE_EXTENSIONS = frozenset({
    '.md', '.txt', '.html', '.htm', '.rst', '.json', '.csv', '.tsv', '.xml',
    '.pdf', '.css', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico',
    '.webp', '.tif', '.tiff', '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.mp3', '.wav', '.ogg', '.mp4', '.zip', '.gz', '.tar', '.7z', '.log',
    '.yaml', '.yml', '.toml', '.ini', '.cfg', '.map', '.lock',
})


def _check_dist_dir(dist_dir: Path) -> int:
    """Fail if a built app folder still embeds the demo owner key.

    A folder build is fine for contributors, but the installer that wraps it is not: this is
    the last gate before `iscc` turns that folder into a double-clickable Setup.exe.
    PyInstaller compresses pure-Python modules with zlib inside `PYZ-00.pyz` embedded in
    `CafeDesk.exe`'s CArchive, so we inspect BOTH PyInstaller CArchive/PYZ bytecode AND raw
    file bytes. Documentation/data files (see `_DATA_FILE_EXTENSIONS`) are skipped: they may
    quote the demo key as an example without making the app verify licences against it.
    """
    if not dist_dir.is_dir():
        _emit('error', 'Build folder missing', f'{dist_dir} does not exist, so the demo-key check cannot run.')
        return 1
    demo = _pristine_demo_key()
    needle = demo.encode('ascii')
    expected_env = os.environ.get('CAFE_DESK_PUBLIC_KEY', '').strip()
    if expected_env:
        try:
            expected_env = validate_public_key_material(expected_env)
        except ValueError:
            expected_env = ''

    found_demo = False
    verified_pyz_keys: list[str] = []
    scanned = 0
    for path in dist_dir.rglob('*'):
        if not path.is_file():
            continue
        if path.suffix.lower() in _DATA_FILE_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
            if size > 400 * 1024 * 1024:
                continue
            scanned += 1
            if path.suffix.lower() in ('', '.exe', '.pkg', '.pyz') and size >= 17:
                pyz_keys = extract_pyinstaller_guard_keys(path, demo)
                for key in pyz_keys:
                    if key == demo:
                        found_demo = True
                        print(f'  demo key found in PyInstaller bytecode inside {path}')
                    elif key not in verified_pyz_keys:
                        verified_pyz_keys.append(key)
            if needle in path.read_bytes():
                found_demo = True
                print(f'  demo key found in {path}')
        except OSError:
            continue

    if found_demo:
        _emit('error', 'Refusing to package a demo-key installer',
              'The built app folder still embeds the demo signing key, so any Setup.exe made from it '
              'would verify licences against a key you do not control. Set the CAFE_DESK_PUBLIC_KEY '
              'secret on release builds; on a machine with owner-keys/, run '
              '`python .github/check_release_key.py --apply` and rebuild.')
        return 1

    for key in verified_pyz_keys:
        try:
            validate_public_key_material(key)
        except ValueError as exc:
            _emit('error', 'Invalid embedded signing key in built app', str(exc))
            return 1
        if expected_env and key != expected_env:
            _emit('error', 'Embedded signing key mismatch',
                  f'Built bytecode embeds {key[:12]}…, which does not match CAFE_DESK_PUBLIC_KEY ({expected_env[:12]}…).')
            return 1

    # When PyInstaller bytecode embeds a single verified non-demo owner key, write a small
    # metadata stamp inside `_internal/` so Inno Setup (`Flags: nocompression`) preserves a
    # verifiable record inside `CafeDesk-Setup.exe` for `tools/verify_release_key.py`.
    if len(verified_pyz_keys) == 1 and (dist_dir / '_internal').is_dir():
        meta_path = dist_dir / RELEASE_KEY_META_REL
        try:
            meta_path.write_text(f'{RELEASE_KEY_META_PREFIX}{verified_pyz_keys[0]}\n', encoding='utf-8')
        except OSError:
            pass

    print(f'App folder clean: no demo signing key in {scanned} file(s) under {dist_dir}.')
    return 0


_KEY_RE = re.compile(r"^DEFAULT_OWNER_PUBLIC_KEY_B64\s*=\s*'([^']*)'", re.MULTILINE)


def _key_in_source() -> str:
    """Read `DEFAULT_OWNER_PUBLIC_KEY_B64` straight from license_guard.py on disk."""
    src = (REPO_ROOT / 'license_guard.py').read_text(encoding='utf-8')
    found = _KEY_RE.search(src)
    return found.group(1).strip() if found else ''


def _pristine_demo_key() -> str:
    """The sample demo key constant — never `DEFAULT_OWNER_PUBLIC_KEY_B64`, which an owner may replace."""
    demo = getattr(license_guard, 'DEMO_OWNER_PUBLIC_KEY_B64', '').strip()
    return demo or 'tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI'


if __name__ == '__main__':
    raise SystemExit(main())
