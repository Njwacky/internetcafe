"""Seamless In-App Self-Updater for Café Desk (Installed & Portable Editions).

Updates Café Desk directly inside the app without saving installers to ``Downloads``
or walking staff through a setup wizard:

1. Downloads into an isolated temporary staging directory and verifies HTTPS, the
   owner's Ed25519 release signature, and exact 64-character SHA-256 BEFORE touching
   the existing installation (`cloud_sync.download_and_verify_update`).
2. Detects how Café Desk is running:
   - ``installed``: Inno Setup Windows installation -> runs the verified installer
     silently in-place (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-
     /CLOSEAPPLICATIONS /DIR="..."`), then automatically relaunches `CafeDesk.exe`
     with a clean `PYINSTALLER_RESET_ENVIRONMENT=1` environment.
   - ``portable``: USB flash drive / portable folder -> prefers the signed portable
     `.zip` archive when published (atomically swapping `CafeDesk.exe` and
     `_internal/` with `.upd-bak` rollback while never touching `CafeDeskData/`), or
     falls back cleanly to a silent `/NOICONS /DIR="..."` update on Windows.
   - ``source``: developer git checkout (`not sys.frozen`).
3. Records `updates/last-update.json` in `app_data_dir()` so the relaunched app can
   confirm the completed update in its status bar and deletes staging artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable
import uuid
import zipfile

import cloud_sync
import settings_store

INNO_APP_ID = '{4B36D389-A2D2-4A1C-9CC7-F2AF905552E7}_is1'
INNO_UNINSTALL_KEY = rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\{INNO_APP_ID}'

STAGING_PREFIX = 'cafedesk-inapp-update-'
MAX_PORTABLE_ZIP_ENTRIES = 5000
MAX_PORTABLE_ZIP_UNCOMPRESSED_BYTES = 1_500_000_000  # 1.5 GB safety bound
MIN_WINDOWS_EXE_BYTES = 4096

_SKIP_TOP_NAMES = frozenset({'cafedeskdata', 'owner-keys'})
_PYINSTALLER_ENV_KEYS = ('_MEIPASS', '_MEIPASS2', 'TCL_LIBRARY', 'TK_LIBRARY')


class UnsignedUpdateError(ValueError):
    """Raised when a published release lacks an owner Ed25519 signature."""


class UnsupportedPlatformUpdateError(RuntimeError):
    """Raised when the published package format cannot self-apply on this OS."""


@dataclass(frozen=True)
class PreparedUpdate:
    version: str
    install_mode: str          # 'installed' | 'portable'
    package_kind: str          # 'exe' | 'zip'
    staging_dir: Path
    package_path: Path
    payload_root: Path | None  # extracted app root when package_kind == 'zip'
    target_dir: Path
    target_exe: Path
    status_file: Path
    sha256: str


def update_status_path(data_dir: Path | str | None = None) -> Path:
    base = Path(data_dir) if data_dir is not None else settings_store.app_data_dir()
    return base / 'updates' / 'last-update.json'


def _read_inno_install_location() -> Path | None:
    """Return Inno Setup's recorded InstallLocation on Windows if present."""
    if not sys.platform.startswith('win'):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, INNO_UNINSTALL_KEY) as key:
                val, _ = winreg.QueryValueEx(key, 'InstallLocation')
                if val and str(val).strip():
                    return Path(str(val).strip()).resolve()
        except OSError:
            continue
    return None


def detect_install_mode(
    *,
    frozen: bool | None = None,
    executable: Path | str | None = None,
    platform_name: str | None = None,
) -> str:
    """Return `'source'`, `'installed'`, or `'portable'`."""
    is_frozen = getattr(sys, 'frozen', False) if frozen is None else bool(frozen)
    if not is_frozen:
        return 'source'
    plat = str(platform_name or sys.platform).lower()
    exe_path = Path(executable or sys.executable).resolve()
    exe_dir = exe_path.parent
    if plat.startswith('win'):
        for marker in ('unins000.exe', 'unins000.dat', 'unins001.exe'):
            if (exe_dir / marker).is_file():
                return 'installed'
        if executable is None:
            reg_dir = _read_inno_install_location()
            if reg_dir is not None and reg_dir == exe_dir:
                return 'installed'
    return 'portable'


def _url_package_kind(url: str) -> str:
    clean_path = url.split('?', 1)[0].split('#', 1)[0].strip().lower()
    if clean_path.endswith('.zip'):
        return 'zip'
    return 'exe'


def select_release_package(
    release: dict,
    install_mode: str,
    *,
    platform_name: str | None = None,
    allow_unsigned: bool = False,
) -> dict:
    """Choose the signed update asset (`exe` or `zip`) appropriate for `install_mode`."""
    plat = str(platform_name or sys.platform).lower()
    ver = str(release.get('latest_version') or release.get('version') or '').strip().lstrip('vV')
    if not ver:
        raise ValueError('Release metadata is missing a version number.')

    p_url = str(release.get('portable_download_url') or '').strip()
    p_sha = str(release.get('portable_sha256') or '').strip().lower()
    p_sig = str(release.get('portable_signature') or '').strip()
    portable_ready = bool(p_url and p_sha and (p_sig or allow_unsigned))

    # Portable installations (and non-Windows frozen builds) prefer the portable .zip
    # so files update cleanly in-place without creating Windows installer registry state.
    if (install_mode == 'portable' or not plat.startswith('win')) and portable_ready:
        chosen = {
            'version': ver,
            'url': p_url,
            'sha256': p_sha,
            'signature': p_sig,
            'kind': _url_package_kind(p_url),
        }
    else:
        url = str(release.get('download_url') or '').strip()
        sha = str(release.get('sha256') or '').strip().lower()
        sig = str(release.get('signature') or '').strip()
        chosen = {
            'version': ver,
            'url': url,
            'sha256': sha,
            'signature': sig,
            'kind': _url_package_kind(url),
        }

    if not chosen['url'] or not chosen['sha256']:
        raise ValueError(f'Release v{ver} is missing a download URL or SHA-256 checksum.')
    if not chosen['signature'] and not allow_unsigned:
        raise UnsignedUpdateError(
            f'Version {ver} is published without an owner signature. '
            'For safety Café Desk will not apply an unverified update.'
        )
    if chosen['kind'] == 'exe' and not plat.startswith('win'):
        raise UnsupportedPlatformUpdateError(
            f'Café Desk v{ver} only lists a Windows .exe package, which cannot self-apply on {plat}.'
        )
    return chosen


def can_apply_in_app(
    release: dict | None,
    *,
    install_mode: str | None = None,
    platform_name: str | None = None,
) -> bool:
    """Return True only when `release` can self-apply on this machine with a valid owner signature."""
    if not isinstance(release, dict) or not release.get('update_available'):
        return False
    plat = str(platform_name or sys.platform).lower()
    mode = install_mode or detect_install_mode(platform_name=plat)
    if mode == 'source':
        return False
    try:
        chosen = select_release_package(release, mode, platform_name=plat, allow_unsigned=False)
    except Exception:
        return False
    return cloud_sync.verify_release_signature(
        chosen['version'],
        chosen['sha256'],
        chosen['signature'],
    )


def validate_windows_exe_file(exe_path: Path) -> None:
    """Confirm a downloaded `.exe` has a valid PE `MZ` header and non-trivial size."""
    p = Path(exe_path)
    if not p.is_file():
        raise ValueError('Downloaded update file is missing.')
    size = p.stat().st_size
    if size < MIN_WINDOWS_EXE_BYTES:
        raise ValueError('Downloaded installer is unexpectedly truncated.')
    with open(p, 'rb') as fh:
        magic = fh.read(2)
    if magic != b'MZ':
        raise ValueError('Downloaded file is not a valid Windows executable.')


def _ensure_portable_target_writable(target_dir: Path) -> None:
    """Verify `target_dir` is writable before downloading a portable `.zip` update."""
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        probe = target_dir / f'.cafedesk-write-probe-{uuid.uuid4().hex[:10]}'
        probe.write_bytes(b'ok')
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise PermissionError(
            f'Café Desk cannot update itself because the folder "{target_dir}" is '
            'write-protected or requires Administrator permission.'
        ) from exc


def is_prepared_update_intact(prepared: PreparedUpdate | None) -> bool:
    """Re-verify a staged `PreparedUpdate` before applying a deferred restart (closes TOCTOU)."""
    if prepared is None:
        return False
    try:
        if not prepared.staging_dir.is_dir() or not prepared.package_path.is_file():
            return False
        if cloud_sync.compute_file_sha256(prepared.package_path) != prepared.sha256.lower():
            return False
        if prepared.package_kind == 'exe':
            validate_windows_exe_file(prepared.package_path)
            return True
        if prepared.payload_root is None or not (prepared.payload_root / prepared.target_exe.name).is_file():
            return False
        return True
    except Exception:
        return False


def _should_skip_portable_member(parts: tuple[str, ...]) -> bool:
    for part in parts:
        low = part.lower()
        if low in _SKIP_TOP_NAMES or 'admin_keygen' in low:
            return True
    return False


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def extract_verified_portable_zip(
    zip_path: Path | str,
    extract_dir: Path | str,
    *,
    exe_name: str = 'CafeDesk.exe',
) -> Path:
    """Safely extract a verified portable `.zip` and return the folder containing `exe_name`."""
    zpath = Path(zip_path)
    out_root = Path(extract_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zpath, 'r') as zf:
        infos = zf.infolist()
        if not infos:
            raise ValueError('Portable update archive is empty.')
        if len(infos) > MAX_PORTABLE_ZIP_ENTRIES:
            raise ValueError('Portable update archive contains too many entries.')
        total_uncompressed = 0
        for info in infos:
            raw_name = info.filename.replace('\\', '/')
            if '\x00' in raw_name:
                raise ValueError('Unsafe null byte in update archive entry name.')
            if re.match(r'^[A-Za-z]:', raw_name) or raw_name.startswith('/'):
                raise ValueError(f'Unsafe absolute path in update archive: {info.filename}')
            rel_parts = tuple(p for p in PurePosixPath(raw_name).parts if p not in ('', '.'))
            if not rel_parts:
                continue
            if any(p == '..' for p in rel_parts):
                raise ValueError(f'Unsafe traversal path in update archive: {info.filename}')
            if _is_zip_symlink(info):
                raise ValueError(f'Refusing symlink in update archive: {info.filename}')
            if _should_skip_portable_member(rel_parts):
                continue
            if info.file_size < 0:
                raise ValueError('Invalid negative entry size in update archive.')
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_PORTABLE_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError('Update archive exceeds safe uncompressed size limit.')

            dest = (out_root / Path(*rel_parts)).resolve()
            if dest != out_root and not dest.is_relative_to(out_root):
                raise ValueError(f'Archive path escaped staging directory: {info.filename}')
            if info.is_dir() or raw_name.endswith('/'):
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, 'r') as src_fh, open(dest, 'wb') as dst_fh:
                shutil.copyfileobj(src_fh, dst_fh, length=1024 * 128)
            # Preserve executable permission bits on POSIX portable binaries
            unix_mode = (info.external_attr >> 16) & 0o777
            if unix_mode & 0o111:
                try:
                    os.chmod(dest, 0o755)
                except OSError:
                    pass

    # Locate the payload root containing CafeDesk.exe (or POSIX CafeDesk binary)
    if (out_root / exe_name).is_file():
        return out_root
    subdirs = [p for p in out_root.iterdir() if p.is_dir()]
    for candidate in subdirs:
        if candidate.name.lower() == 'cafedesk' and (candidate / exe_name).is_file():
            return candidate
    if len(subdirs) == 1 and (subdirs[0] / exe_name).is_file():
        return subdirs[0]
    raise ValueError(f'Verified update archive does not contain expected executable ({exe_name}).')


def prepare_in_app_update(
    release: dict,
    *,
    install_mode: str | None = None,
    executable: Path | str | None = None,
    data_dir: Path | str | None = None,
    staging_parent: Path | str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    platform_name: str | None = None,
    allow_unsigned: bool = False,
) -> PreparedUpdate:
    """Download, cryptographically verify, and stage an update without touching `Downloads`."""
    plat = str(platform_name or sys.platform).lower()
    mode = install_mode or detect_install_mode(executable=executable, platform_name=plat)
    if mode == 'source':
        raise RuntimeError(
            'Running from a Python source checkout. Use `git pull` to update source files; '
            'packaged Café Desk builds update themselves automatically in-app.'
        )

    target_exe = Path(executable or sys.executable).resolve()
    target_dir = target_exe.parent
    selected = select_release_package(
        release,
        mode,
        platform_name=plat,
        allow_unsigned=allow_unsigned,
    )
    if selected['kind'] == 'zip':
        _ensure_portable_target_writable(target_dir)

    parent_dir = Path(staging_parent) if staging_parent is not None else Path(tempfile.gettempdir())
    parent_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=str(parent_dir))).resolve()
    try:
        os.chmod(staging_dir, 0o700)
    except OSError:
        pass

    try:
        pkg_path = cloud_sync.download_and_verify_update(
            selected['url'],
            selected['sha256'],
            staging_dir,
            version_label=selected['version'],
            expected_signature=selected['signature'],
            progress_cb=progress_cb,
            allow_unsigned=allow_unsigned,
        )
        payload_root: Path | None = None
        if selected['kind'] == 'exe':
            validate_windows_exe_file(pkg_path)
        else:
            payload_root = extract_verified_portable_zip(
                pkg_path,
                staging_dir / 'extracted',
                exe_name=target_exe.name,
            )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return PreparedUpdate(
        version=selected['version'],
        install_mode=mode,
        package_kind=selected['kind'],
        staging_dir=staging_dir,
        package_path=pkg_path,
        payload_root=payload_root,
        target_dir=target_dir,
        target_exe=target_exe,
        status_file=update_status_path(data_dir),
        sha256=selected['sha256'],
    )


def build_windows_updater_script() -> str:
    """Return a pure-ASCII PowerShell updater script driven entirely by `$env:CAFEDESK_*`.

    Using environment variables via wide-char `CreateProcessW` + UTF-8 BOM guarantees
    zero codepage corruption on paths containing spaces or accents (such as `Café Desk`)
    and eliminates command-line/script quoting injection.
    """
    return """$ErrorActionPreference = 'Stop'
$parentPid = 0
[void][int]::TryParse($env:CAFEDESK_PARENT_PID, [ref]$parentPid)
$version = $env:CAFEDESK_UPDATE_VERSION
$installMode = $env:CAFEDESK_INSTALL_MODE
$kind = $env:CAFEDESK_PACKAGE_KIND
$pkgPath = $env:CAFEDESK_PACKAGE_PATH
$expectedSha = ([string]$env:CAFEDESK_EXPECTED_SHA256).Trim().ToLowerInvariant()
$payloadRoot = $env:CAFEDESK_PAYLOAD_ROOT
$targetDir = $env:CAFEDESK_TARGET_DIR
$targetExe = $env:CAFEDESK_TARGET_EXE
$stagingDir = $env:CAFEDESK_STAGING_DIR
$statusFile = $env:CAFEDESK_STATUS_FILE

function Write-UpdateStatus([string]$state, [string]$detail) {
  try {
    if (-not $statusFile) { return }
    $dir = Split-Path -Parent $statusFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
      New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $obj = @{
      status = $state
      version = $version
      detail = $detail
      updated_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    $json = $obj | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($statusFile, $json, [System.Text.UTF8Encoding]::new($false))
  } catch {}
}

function Invoke-RetryIo([scriptblock]$action, [int]$attempts = 12, [int]$delayMs = 400) {
  for ($i = 1; $i -le $attempts; $i++) {
    try {
      & $action
      return
    } catch {
      if ($i -ge $attempts) { throw }
      Start-Sleep -Milliseconds $delayMs
    }
  }
}

function Test-FolderWritable([string]$folder) {
  try {
    if (-not (Test-Path -LiteralPath $folder)) { return $true }
    $probe = Join-Path $folder ('.cafedesk-upd-probe-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    [System.IO.File]::WriteAllText($probe, 'ok')
    Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    return $true
  } catch {
    return $false
  }
}

$parentStillAlive = $false
if ($parentPid -gt 0) {
  try {
    Wait-Process -Id $parentPid -Timeout 90 -ErrorAction SilentlyContinue
  } catch {}
  Start-Sleep -Milliseconds 600
  if ($null -ne (Get-Process -Id $parentPid -ErrorAction SilentlyContinue)) {
    $parentStillAlive = $true
  }
}

$ok = $false
$errMsg = ''

if ($parentStillAlive) {
  $errMsg = 'Previous Cafe Desk window did not close in time.'
} elseif ($expectedSha -and (-not (Test-Path -LiteralPath $pkgPath) -or ((Get-FileHash -LiteralPath $pkgPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedSha))) {
  $errMsg = 'Staged update package failed SHA-256 verification before launch.'
} elseif ($kind -eq 'exe') {
  try {
    $cleanDir = $targetDir.TrimEnd('\\')
    if ($cleanDir -match '^[A-Za-z]:$') { $cleanDir = $cleanDir + '\\.' }
    $dirArg = '/DIR="' + $cleanDir.Replace('"', '') + '"'
    $argParts = @(
      '/VERYSILENT',
      '/SUPPRESSMSGBOXES',
      '/NORESTART',
      '/SP-',
      '/CLOSEAPPLICATIONS',
      $dirArg
    )
    if ($installMode -eq 'portable') {
      $argParts += '/NOICONS'
    }
    $argLine = $argParts -join ' '
    if (Test-FolderWritable $targetDir) {
      $proc = Start-Process -FilePath $pkgPath -ArgumentList $argLine -Wait -PassThru -WindowStyle Hidden
    } else {
      $proc = Start-Process -FilePath $pkgPath -ArgumentList $argLine -Verb RunAs -Wait -PassThru
    }
    if ($null -ne $proc -and $proc.ExitCode -eq 0 -and (Test-Path -LiteralPath $targetExe)) {
      $ok = $true
    } else {
      $code = if ($null -ne $proc) { $proc.ExitCode } else { -1 }
      $errMsg = "Silent update installer exited with code $code."
    }
  } catch {
    $errMsg = $_.Exception.Message
  }
} elseif ($kind -eq 'zip') {
  $exeBak = $targetExe + '.upd-bak'
  $intPath = Join-Path $targetDir '_internal'
  $intBak = Join-Path $targetDir '_internal.upd-bak'
  try {
    if (-not (Test-Path -LiteralPath $targetDir)) {
      New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $exeBak) { Invoke-RetryIo { Remove-Item -LiteralPath $exeBak -Force } }
    if (Test-Path -LiteralPath $intBak) { Invoke-RetryIo { Remove-Item -LiteralPath $intBak -Recurse -Force } }
    if (Test-Path -LiteralPath $targetExe) { Invoke-RetryIo { Move-Item -LiteralPath $targetExe -Destination $exeBak -Force } }
    if (Test-Path -LiteralPath $intPath) { Invoke-RetryIo { Move-Item -LiteralPath $intPath -Destination $intBak -Force } }

    Get-ChildItem -LiteralPath $payloadRoot -Force | ForEach-Object {
      $low = $_.Name.ToLowerInvariant()
      if ($low -eq 'cafedeskdata' -or $low -eq 'owner-keys' -or $low -like '*admin_keygen*') {
        return
      }
      $dest = Join-Path $targetDir $_.Name
      if ($_.PSIsContainer) {
        if (Test-Path -LiteralPath $dest) {
          Invoke-RetryIo { Remove-Item -LiteralPath $dest -Recurse -Force }
        }
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force
      } else {
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
      }
    }

    if (-not (Test-Path -LiteralPath $targetExe)) {
      throw 'Updated CafeDesk executable missing after portable update.'
    }
    $ok = $true
    if (Test-Path -LiteralPath $exeBak) { Remove-Item -LiteralPath $exeBak -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $intBak) { Remove-Item -LiteralPath $intBak -Recurse -Force -ErrorAction SilentlyContinue }
  } catch {
    $errMsg = $_.Exception.Message
    try {
      if (-not (Test-Path -LiteralPath $targetExe) -and (Test-Path -LiteralPath $exeBak)) {
        Move-Item -LiteralPath $exeBak -Destination $targetExe -Force
      }
      if (Test-Path -LiteralPath $intBak) {
        if (Test-Path -LiteralPath $intPath) { Remove-Item -LiteralPath $intPath -Recurse -Force -ErrorAction SilentlyContinue }
        Move-Item -LiteralPath $intBak -Destination $intPath -Force
      }
    } catch {}
  }
}

if ($ok) {
  Write-UpdateStatus 'completed' "Updated to Cafe Desk v$version"
} else {
  Write-UpdateStatus 'failed' $errMsg
}

if (-not $parentStillAlive -and ($env:CAFEDESK_NO_RELAUNCH -ne '1') -and (Test-Path -LiteralPath $targetExe)) {
  try {
    foreach ($k in @(
      'CAFEDESK_PARENT_PID', 'CAFEDESK_UPDATE_VERSION', 'CAFEDESK_INSTALL_MODE',
      'CAFEDESK_PACKAGE_KIND', 'CAFEDESK_PACKAGE_PATH', 'CAFEDESK_EXPECTED_SHA256',
      'CAFEDESK_PAYLOAD_ROOT', 'CAFEDESK_TARGET_DIR', 'CAFEDESK_TARGET_EXE',
      'CAFEDESK_STAGING_DIR', 'CAFEDESK_STATUS_FILE', 'CAFEDESK_NO_RELAUNCH',
      '_MEIPASS', '_MEIPASS2', 'TCL_LIBRARY', 'TK_LIBRARY'
    )) {
      Remove-Item -Path ("Env:\\" + $k) -ErrorAction SilentlyContinue
    }
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $targetExe -WorkingDirectory $targetDir | Out-Null
  } catch {}
}

try {
  Set-Location -LiteralPath ([System.IO.Path]::GetTempPath())
  if ($stagingDir -and (Test-Path -LiteralPath $stagingDir)) {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
  }
} catch {}
"""


def build_posix_updater_script() -> str:
    """Return a POSIX `/bin/sh` portable folder updater driven by `$CAFEDESK_*` env vars."""
    return """#!/bin/sh
set -u

write_status() {
  st="$1"
  dt="$(printf '%s' "$2" | tr '\\"\\n\\r\\\\' '    ')"
  ver_clean="$(printf '%s' "${CAFEDESK_UPDATE_VERSION:-}" | tr '\\"\\n\\r\\\\' '    ')"
  if [ -n "${CAFEDESK_STATUS_FILE:-}" ]; then
    mkdir -p "$(dirname "$CAFEDESK_STATUS_FILE")" 2>/dev/null || true
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo '')"
    printf '{"status":"%s","version":"%s","detail":"%s","updated_at":"%s"}\\n' "$st" "$ver_clean" "$dt" "$ts" > "$CAFEDESK_STATUS_FILE" 2>/dev/null || true
  fi
}

parent_alive=0
if [ -n "${CAFEDESK_PARENT_PID:-}" ] && [ "$CAFEDESK_PARENT_PID" -gt 0 ] 2>/dev/null; then
  i=0
  while kill -0 "$CAFEDESK_PARENT_PID" 2>/dev/null && [ "$i" -lt 180 ]; do
    sleep 0.25 2>/dev/null || sleep 1
    i=$((i + 1))
  done
  if kill -0 "$CAFEDESK_PARENT_PID" 2>/dev/null; then
    parent_alive=1
  fi
fi

exe_bak="${CAFEDESK_TARGET_EXE}.upd-bak"
int_path="${CAFEDESK_TARGET_DIR}/_internal"
int_bak="${CAFEDESK_TARGET_DIR}/_internal.upd-bak"

ok=0
err_msg=""

if [ "$parent_alive" -eq 1 ]; then
  err_msg="Previous Cafe Desk window did not close in time."
elif [ -d "${CAFEDESK_PAYLOAD_ROOT:-}" ]; then
  rm -f "$exe_bak" 2>/dev/null || true
  if [ -d "$int_bak" ]; then
    chmod -R u+w "$int_bak" 2>/dev/null || true
    rm -rf "$int_bak" 2>/dev/null || true
  fi
  mkdir -p "$CAFEDESK_TARGET_DIR" 2>/dev/null || true
  if [ -f "$CAFEDESK_TARGET_EXE" ]; then mv -f "$CAFEDESK_TARGET_EXE" "$exe_bak" 2>/dev/null || true; fi
  if [ -d "$int_path" ]; then mv -f "$int_path" "$int_bak" 2>/dev/null || true; fi

  copy_failed=0
  for item in "$CAFEDESK_PAYLOAD_ROOT"/* "$CAFEDESK_PAYLOAD_ROOT"/.[!.]* "$CAFEDESK_PAYLOAD_ROOT"/..?*; do
    [ -e "$item" ] || continue
    base="$(basename "$item")"
    low="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"
    case "$low" in
      cafedeskdata|owner-keys|*admin_keygen*) continue ;;
    esac
    dest_item="${CAFEDESK_TARGET_DIR}/${base}"
    if [ -d "$item" ] && [ -d "$dest_item" ]; then
      chmod -R u+w "$dest_item" 2>/dev/null || true
      rm -rf "$dest_item" 2>/dev/null || true
    fi
    cp -Rf "$item" "$CAFEDESK_TARGET_DIR/" || copy_failed=1
  done

  if [ "$copy_failed" -eq 0 ] && [ -f "$CAFEDESK_TARGET_EXE" ]; then
    chmod +x "$CAFEDESK_TARGET_EXE" 2>/dev/null || true
    ok=1
    rm -f "$exe_bak" 2>/dev/null || true
    if [ -d "$int_bak" ]; then
      chmod -R u+w "$int_bak" 2>/dev/null || true
      rm -rf "$int_bak" 2>/dev/null || true
    fi
  else
    err_msg="Portable file swap failed; previous version restored."
    if [ ! -f "$CAFEDESK_TARGET_EXE" ] && [ -f "$exe_bak" ]; then
      mv -f "$exe_bak" "$CAFEDESK_TARGET_EXE" 2>/dev/null || true
    fi
    if [ -d "$int_bak" ]; then
      rm -rf "$int_path" 2>/dev/null || true
      mv -f "$int_bak" "$int_path" 2>/dev/null || true
    fi
  fi
else
  err_msg="Extracted update payload directory missing."
fi

if [ "$ok" -eq 1 ]; then
  write_status "completed" "Updated to Cafe Desk v${CAFEDESK_UPDATE_VERSION:-}"
else
  write_status "failed" "$err_msg"
fi

relaunch_flag="${CAFEDESK_NO_RELAUNCH:-0}"
target_dir_launch="$CAFEDESK_TARGET_DIR"
target_exe_launch="$CAFEDESK_TARGET_EXE"
staging_cleanup="${CAFEDESK_STAGING_DIR:-}"

if [ "$parent_alive" -eq 0 ] && [ "$relaunch_flag" != "1" ] && [ -x "$target_exe_launch" ]; then
  (
    unset CAFEDESK_PARENT_PID CAFEDESK_UPDATE_VERSION CAFEDESK_INSTALL_MODE
    unset CAFEDESK_PACKAGE_KIND CAFEDESK_PACKAGE_PATH CAFEDESK_EXPECTED_SHA256
    unset CAFEDESK_PAYLOAD_ROOT CAFEDESK_TARGET_DIR CAFEDESK_TARGET_EXE
    unset CAFEDESK_STAGING_DIR CAFEDESK_STATUS_FILE CAFEDESK_NO_RELAUNCH
    unset _MEIPASS _MEIPASS2 TCL_LIBRARY TK_LIBRARY
    export PYINSTALLER_RESET_ENVIRONMENT=1
    cd "$target_dir_launch" && "$target_exe_launch" >/dev/null 2>&1 &
  ) || true
fi

cd "${TMPDIR:-/tmp}" 2>/dev/null || cd /
if [ -n "$staging_cleanup" ] && [ -d "$staging_cleanup" ]; then
  rm -rf "$staging_cleanup" 2>/dev/null || true
fi
"""


def _build_clean_updater_env(prepared: PreparedUpdate, pid: int, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build child env scrubbed of frozen PyInstaller runtime state so relaunch starts fresh."""
    child_env = dict(os.environ)
    for key in _PYINSTALLER_ENV_KEYS:
        child_env.pop(key, None)
    if 'LD_LIBRARY_PATH_ORIG' in child_env:
        orig_ld = child_env.pop('LD_LIBRARY_PATH_ORIG', '')
        if orig_ld:
            child_env['LD_LIBRARY_PATH'] = orig_ld
        else:
            child_env.pop('LD_LIBRARY_PATH', None)
    child_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    child_env.update({
        'CAFEDESK_PARENT_PID': str(pid),
        'CAFEDESK_UPDATE_VERSION': str(prepared.version),
        'CAFEDESK_INSTALL_MODE': str(prepared.install_mode),
        'CAFEDESK_PACKAGE_KIND': str(prepared.package_kind),
        'CAFEDESK_PACKAGE_PATH': str(prepared.package_path),
        'CAFEDESK_EXPECTED_SHA256': str(prepared.sha256).lower(),
        'CAFEDESK_PAYLOAD_ROOT': str(prepared.payload_root or ''),
        'CAFEDESK_TARGET_DIR': str(prepared.target_dir),
        'CAFEDESK_TARGET_EXE': str(prepared.target_exe),
        'CAFEDESK_STAGING_DIR': str(prepared.staging_dir),
        'CAFEDESK_STATUS_FILE': str(prepared.status_file),
    })
    if extra_env:
        child_env.update(extra_env)
    return child_env


def apply_prepared_update(
    prepared: PreparedUpdate,
    *,
    parent_pid: int | None = None,
    platform_name: str | None = None,
    popen_fn: Callable[..., object] | None = None,
    extra_env: dict[str, str] | None = None,
) -> Path:
    """Spawn the detached out-of-process updater that waits for exit, updates, & relaunches."""
    plat = str(platform_name or sys.platform).lower()
    pid = os.getpid() if parent_pid is None else int(parent_pid)
    spawn = popen_fn or subprocess.Popen
    child_env = _build_clean_updater_env(prepared, pid, extra_env=extra_env)

    # Always use system temp as child cwd so Windows never holds a directory handle on target_dir.
    safe_cwd = str(Path(tempfile.gettempdir()).resolve())

    if plat.startswith('win'):
        script_path = prepared.staging_dir / 'apply-update.ps1'
        script_path.write_text(build_windows_updater_script(), encoding='utf-8-sig')
        sys_root = os.environ.get('SystemRoot') or r'C:\Windows'
        ps_candidate = Path(sys_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        ps_exe = str(ps_candidate) if ps_candidate.is_file() else 'powershell.exe'
        creationflags = (
            getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
            | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        )
        spawn(
            [
                ps_exe,
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-WindowStyle',
                'Hidden',
                '-File',
                str(script_path),
            ],
            cwd=safe_cwd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        return script_path

    if prepared.package_kind != 'zip':
        raise UnsupportedPlatformUpdateError('Only portable .zip updates can self-apply on POSIX systems.')
    script_path = prepared.staging_dir / 'apply-update.sh'
    script_path.write_text(build_posix_updater_script(), encoding='utf-8')
    os.chmod(script_path, 0o700)
    spawn(
        ['/bin/sh', str(script_path)],
        cwd=safe_cwd,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return script_path


def consume_last_update_result(data_dir: Path | str | None = None) -> dict | None:
    """Read and clear `last-update.json` written by the background updater on restart."""
    p = update_status_path(data_dir)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raw = None
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
    return raw if isinstance(raw, dict) else None


def cleanup_stale_staging(
    temp_root: Path | str | None = None,
    *,
    max_age_seconds: float = 21600.0,
) -> int:
    """Remove abandoned `cafedesk-inapp-update-*` folders older than `max_age_seconds`."""
    root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(60.0, float(max_age_seconds))
    removed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for item in entries:
        if not item.name.startswith(STAGING_PREFIX):
            continue
        try:
            if item.stat().st_mtime <= cutoff:
                shutil.rmtree(item, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
