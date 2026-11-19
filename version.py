"""Café Desk — single source of truth for the application version.

Every runtime and build-time consumer reads the version from here:

* `cloud_sync.APP_VERSION`  — update checks, user-agent, telemetry, releases
* `app.py` (`APP_BUILD`)    — status bar, About screen, window title
* `pyproject.toml`          — package metadata (kept honest by `tools/check_version_sync.py`)
* `packaging/windows-installer.iss`, `packaging/build-windows.bat`,
  `.github/workflows/build-usb.yml` — read it dynamically at build time

Bump this ONE line per release. `python tools/check_version_sync.py` (also run in
CI) fails the build if any consumer drifts away from this value.
"""

__version__ = '0.8.3'
