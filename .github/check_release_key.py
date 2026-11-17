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
    python .github/check_release_key.py            # enforce (release builds)
    python .github/check_release_key.py --warn-only  # pull-request builds

Exit codes: 0 = ok / warned, 1 = enforcement failed.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import admin_keygen  # noqa: E402


_B64U_RE = re.compile(r'^[A-Za-z0-9_-]+={0,2}$')


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


def _check_dist_dir(dist_dir: Path) -> int:
    """Fail if a built app folder still embeds the demo owner key.

    A folder build is fine for contributors, but the installer that wraps it is not: this is
    the last gate before `iscc` turns that folder into a double-clickable Setup.exe.
    PyInstaller keeps the 43-character base64url literal verbatim in the bundled bytecode,
    so scanning raw bytes is enough and needs no disassembly.
    """
    if not dist_dir.is_dir():
        _emit('error', 'Build folder missing', f'{dist_dir} does not exist, so the demo-key check cannot run.')
        return 1
    demo = _pristine_demo_key()
    needle = demo.encode('ascii')
    found_demo = False
    scanned = 0
    # Onedir builds hide the app code in .pyc files inside the PYZ/EXE; still, the literal
    # appears in the archive bytes, so plain binary searching covers it.
    for path in dist_dir.rglob('*'):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 400 * 1024 * 1024:
                continue
            scanned += 1
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
    print(f'App folder clean: no demo signing key in {scanned} file(s) under {dist_dir}.')
    return 0


_KEY_RE = re.compile(r"^DEFAULT_OWNER_PUBLIC_KEY_B64\s*=\s*'([^']*)'", re.MULTILINE)


def _key_in_source() -> str:
    """Read `DEFAULT_OWNER_PUBLIC_KEY_B64` straight from license_guard.py on disk."""
    src = (REPO_ROOT / 'license_guard.py').read_text(encoding='utf-8')
    found = _KEY_RE.search(src)
    return found.group(1).strip() if found else ''


_DEMO_CACHE: list[str] = []


def _pristine_demo_key() -> str:
    """The demo key value as committed — read from git so an --apply run cannot mask it."""
    if _DEMO_CACHE:
        return _DEMO_CACHE[0]
    demo = ''
    try:
        shown = subprocess.run(
            ['git', 'show', 'HEAD:license_guard.py'],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20, check=False,
        )
        if shown.returncode == 0:
            for line in shown.stdout.splitlines():
                if line.startswith('DEFAULT_OWNER_PUBLIC_KEY_B64'):
                    demo = line.split('=', 1)[1].strip().strip('\'"')
                    break
    except (OSError, subprocess.SubprocessError):
        demo = ''
    if not demo:
        # Fallback: the shipped default (works in a shallow/tarball checkout).
        demo = 'tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI'
    _DEMO_CACHE.append(demo)
    return demo


if __name__ == '__main__':
    raise SystemExit(main())
