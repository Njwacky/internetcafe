"""Release-time guard: refuse to ship a build signed with the sample/demo owner key.

Why this exists
---------------
`license_guard.py` ships with `DEFAULT_OWNER_PUBLIC_KEY_B64`, a throw-away "demo"
public key that lives in this repository. Any artifact built straight from a plain
checkout therefore verifies activation keys against a key whose *private* half is
public knowledge — i.e. anyone could mint a license.

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
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import license_guard  # noqa: E402
import admin_keygen  # noqa: E402


def _emit(level: str, title: str, message: str) -> None:
    """Emit a GitHub annotation when running in Actions, plain text otherwise."""
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print(f'::{level} title={title}::{message}')
    else:
        print(f'{level.upper()}: {title}: {message}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Verify the embedded owner Ed25519 public key is not the demo key.')
    parser.add_argument('--warn-only', action='store_true', help='Report but do not fail when the demo key is active.')
    parser.add_argument('--apply', action='store_true', help='Write $CAFE_DESK_PUBLIC_KEY into license_guard.py before checking.')
    args = parser.parse_args(argv)

    secret = os.environ.get('CAFE_DESK_PUBLIC_KEY', '').strip()

    if args.apply:
        if not secret:
            _emit('error', 'Missing signing key', 'CAFE_DESK_PUBLIC_KEY is empty; cannot apply the owner public key.')
            return 1
        try:
            admin_keygen.apply_public_key_to_guard(secret)
        except (RuntimeError, OSError) as exc:
            _emit('error', 'Could not apply owner public key', str(exc))
            return 1
        # apply_public_key_to_guard rewrites the source; re-import so the check sees it.
        for module in ('license_guard', 'admin_keygen'):
            sys.modules.pop(module, None)
        import importlib
        importlib.invalidate_caches()

    # The check reads the value PyInstaller will actually bundle.
    active = license_guard.DEFAULT_OWNER_PUBLIC_KEY_B64
    demo_active = active == _pristine_demo_key()
    if demo_active:
        _emit(
            'warning',
            'DEMO SIGNING KEY ACTIVE',
            'This build would verify licenses against the public demo key that ships in the repo. '
            'Set the CAFE_DESK_PUBLIC_KEY repository secret (or run --apply in the release job).',
        )
        if args.warn_only:
            print('Demo key active — allowed because this is a non-release build (--warn-only).')
            return 0
        _emit('error', 'Release blocked', 'Refusing to package a build signed with the demo key.')
        return 1

    print(f'Owner signing key OK ({active[:12]}…).')
    return 0


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
