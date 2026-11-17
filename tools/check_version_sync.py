#!/usr/bin/env python3
"""Fail the build if version strings drift away from `version.py`.

`version.py::__version__` is the single source of truth for the Café Desk
version. This check is run by CI (`.github/workflows/build-usb.yml`) and by the
test suite. It verifies:

* `pyproject.toml` `version` matches `version.py`
* `app.py` / `cloud_sync.py` import `__version__` instead of hardcoding a literal
* `packaging/windows-installer.iss` default `MyAppVersion` matches
* no build script pins a stale `vX.Y.Z` literal

Usage:
    python tools/check_version_sync.py           # report & exit non-zero on drift
    python tools/check_version_sync.py --check   # same (kept for symmetry with sync_owner_digest.py)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')


def main() -> int:
    version_src = read('version.py')
    match = re.search(r"__version__\s*=\s*'([^']+)'", version_src)
    if not match:
        print('::error::version.py does not define __version__ = \'x.y.z\'')
        return 1
    ver = match.group(1)

    problems: list[str] = []

    pyproject = read('pyproject.toml')
    pm = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
    if not pm:
        problems.append('pyproject.toml is missing a top-level version field')
    elif pm.group(1) != ver:
        problems.append(f'pyproject.toml version "{pm.group(1)}" != version.py "{ver}"')

    app_src = read('app.py')
    if 'from version import __version__' not in app_src:
        problems.append('app.py does not import __version__ from version.py')
    if re.search(r"APP_BUILD\s*=\s*'\d", app_src):
        problems.append('app.py still hardcodes APP_BUILD=\'x.y.z\'')

    cloud_src = read('cloud_sync.py')
    if 'from version import __version__' not in cloud_src:
        problems.append('cloud_sync.py does not import __version__ from version.py')
    if re.search(r"APP_VERSION\s*=\s*'\d", cloud_src):
        problems.append('cloud_sync.py still hardcodes APP_VERSION=\'x.y.z\'')

    iss_src = read('packaging/windows-installer.iss')
    im = re.search(r'#define MyAppVersion "([^"]+)"', iss_src)
    if not im:
        problems.append('packaging/windows-installer.iss is missing MyAppVersion')
    elif im.group(1) != ver:
        problems.append(f'windows-installer.iss default MyAppVersion "{im.group(1)}" != version.py "{ver}"')

    for rel in ('.github/workflows/build-usb.yml', 'packaging/build-windows.bat', 'packaging/build-unix.sh'):
        for stale in re.finditer(r'v(\d+\.\d+\.\d+)', read(rel)):
            if stale.group(1) != ver:
                problems.append(f'{rel} pins stale version v{stale.group(1)} (expected v{ver} or a dynamic read)')

    for problem in problems:
        print(f'::error::{problem}')
    if problems:
        print(f'Version drift detected — align everything to version.py ({ver}).')
        return 1
    print(f'Version OK: {ver} is the single source everywhere it matters.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
