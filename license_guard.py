"""Offline Hardware Machine-ID + 1-Year Ed25519 License Guard for Café Desk.

How commercial per-computer licensing works:
1. Every target PC produces a deterministic hardware Machine ID (`CD-XXXX-XXXX-XXXX`)
   derived from OS-level machine identifiers (Windows MachineGuid + BIOS/CSProduct UUID,
   macOS IOPlatformUUID, or Linux /etc/machine-id).
2. A license token (`CDLIC1.<payload_b64url>.<sig_b64url>`) contains:
   - v: 1 (schema version)
   - mid: normalized Machine ID (`CD-XXXX-XXXX-XXXX`)
   - cust: licensed café / customer name
   - iat: issue date (`YYYY-MM-DD`)
   - exp: expiry date (`YYYY-MM-DD`, default +365 days = 1 year)
   - lid: unique license ID
3. ONLY the owner's private Ed25519 key (used in `admin_keygen.py`, kept strictly off
   customer PCs and gitignored) can sign a token. The app embeds ONLY the Ed25519
   PUBLIC key (`OWNER_PUBLIC_KEY_B64`).
4. License files are ALWAYS stored per-computer in the host OS user data folder
   (`%APPDATA%\\CafeDesk\\license.json`), NEVER inside a shared USB `CafeDeskData`
   folder — so copying the USB folder or `license.json` to another computer immediately
   fails the Machine ID verification and opens the Activation screen.
5. Basic clock-rollback protection records the highest observed UTC date; if a user
   rolls their PC system clock backwards significantly to bypass the 1-year expiry,
   validation rejects the license until the clock is restored or a renewal key is entered.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TOKEN_PREFIX = 'CDLIC1'
MACHINE_ID_SALT = b'CafeDesk-Hardware-Lock-v1'
CLOCK_ROLLBACK_GRACE_DAYS = 2

# Default embedded Ed25519 public key (32 raw bytes, URL-safe base64).
# Run `python admin_keygen.py --init-keys` once on YOUR private laptop to generate
# your own private/public keypair and replace this public key before selling builds!
DEFAULT_OWNER_PUBLIC_KEY_B64 = '14W-XVzHc9Ju8X-87VLoIt8frfFWHPWvSJPsHijrz3w'


class LicenseError(ValueError):
    """Raised when a license is missing, invalid, copied from another PC, or expired."""


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _b64u_decode(text: str) -> bytes:
    cleaned = re.sub(r'\s+', '', str(text or ''))
    pad = '=' * ((4 - len(cleaned) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(cleaned + pad)
    except Exception as exc:
        raise LicenseError('Activation key format is damaged or incomplete.') from exc


def configured_public_key_b64() -> str:
    """Return active owner Ed25519 public key (env override supported for build pipelines/tests)."""
    env_key = os.environ.get('CAFE_DESK_PUBLIC_KEY', '').strip()
    return env_key or DEFAULT_OWNER_PUBLIC_KEY_B64


def is_demo_public_key_active() -> bool:
    return configured_public_key_b64() == DEFAULT_OWNER_PUBLIC_KEY_B64


def is_license_required() -> bool:
    """Frozen `.exe` builds ALWAYS enforce license activation.

    When running from source (`python app.py`) during development, enforcement can be
    toggled on via `CAFE_DESK_REQUIRE_LICENSE=1` (or happens automatically if a license
    is already installed or `CAFE_DESK_REQUIRE_LICENSE` is not disabled).
    By default we enforce in frozen builds and whenever `CAFE_DESK_REQUIRE_LICENSE=1`.
    """
    if getattr(sys, 'frozen', False):
        return True
    return os.environ.get('CAFE_DESK_REQUIRE_LICENSE', '').strip() in ('1', 'true', 'TRUE', 'yes', 'on')


def host_license_dir() -> Path:
    """Per-PC OS folder for `license.json` (intentionally ignores USB `CafeDeskData`)."""
    override = os.environ.get('CAFE_DESK_LICENSE_DIR', '').strip()
    if override:
        return Path(override)
    if sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA') or (Path.home() / 'AppData' / 'Roaming'))
    elif sys.platform == 'darwin':
        root = Path(os.environ.get('CAFE_DESK_MAC_DIR') or (Path.home() / 'Library' / 'Application Support'))
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME', '')
        root = Path(xdg) if xdg and Path(xdg).is_absolute() else (Path.home() / '.config')
    return root / 'CafeDesk'


def license_file_path() -> Path:
    return host_license_dir() / 'license.json'


def clock_guard_path() -> Path:
    return host_license_dir() / 'license-clock.json'


def _read_windows_machine_guid() -> str:
    if sys.platform != 'win32':
        return ''
    try:
        import winreg  # type: ignore
        access = winreg.KEY_READ | getattr(winreg, 'KEY_WOW64_64KEY', 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0, access) as key:
            val, _ = winreg.QueryValueEx(key, 'MachineGuid')
            return str(val or '').strip()
    except Exception:
        return ''


def _read_windows_csproduct_uuid() -> str:
    if sys.platform != 'win32':
        return ''
    try:
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             '(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID'],
            capture_output=True, text=True, timeout=4, creationflags=creationflags
        )
        val = (proc.stdout or '').strip()
        if val and val.upper() not in ('FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF', '00000000-0000-0000-0000-000000000000'):
            return val
    except Exception:
        pass
    return ''


def _read_macos_platform_uuid() -> str:
    if sys.platform != 'darwin':
        return ''
    try:
        proc = subprocess.run(
            ['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
            capture_output=True, text=True, timeout=4
        )
        m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', proc.stdout or '')
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ''


def _read_linux_machine_id() -> str:
    if sys.platform.startswith('win') or sys.platform == 'darwin':
        return ''
    for candidate in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            txt = Path(candidate).read_text(encoding='utf-8', errors='ignore').strip()
            if txt:
                return txt
        except OSError:
            continue
    return ''


def collect_hardware_traits() -> list[str]:
    """Collect stable OS/hardware identifiers for this computer."""
    traits: list[str] = []
    if sys.platform == 'win32':
        guid = _read_windows_machine_guid()
        if guid:
            traits.append(f'win-guid:{guid.lower()}')
        hw_uuid = _read_windows_csproduct_uuid()
        if hw_uuid:
            traits.append(f'win-uuid:{hw_uuid.lower()}')
    elif sys.platform == 'darwin':
        mac_uuid = _read_macos_platform_uuid()
        if mac_uuid:
            traits.append(f'mac-uuid:{mac_uuid.lower()}')
    else:
        lin_id = _read_linux_machine_id()
        if lin_id:
            traits.append(f'linux-id:{lin_id.lower()}')

    if not traits:
        # Fallback if OS queries are unavailable in a restricted environment
        traits.append(f'node:{uuid.getnode():012x}')
        traits.append(f'host:{platform.node().strip().lower()}')
    traits.append(f'sys:{sys.platform}')
    return traits


def normalize_machine_id(raw_mid: str) -> str:
    """Normalize a Machine ID into `CD-XXXX-XXXX-XXXX` uppercase format."""
    cleaned = re.sub(r'[^A-Za-z0-9]', '', str(raw_mid or '')).upper()
    if cleaned.startswith('CD') and len(cleaned) == 14:
        cleaned = cleaned[2:]
    if len(cleaned) != 12 or not re.fullmatch(r'[0-9A-F]{12}', cleaned):
        raise LicenseError('Machine ID must look like CD-XXXX-XXXX-XXXX (12 hex characters after CD-).')
    return f'CD-{cleaned[0:4]}-{cleaned[4:8]}-{cleaned[8:12]}'


def get_machine_id() -> str:
    """Return this computer's unique `CD-XXXX-XXXX-XXXX` Machine ID."""
    override = os.environ.get('CAFE_DESK_TEST_MACHINE_ID', '').strip()
    if override:
        return normalize_machine_id(override)
    digest = hashlib.sha256()
    digest.update(MACHINE_ID_SALT)
    for item in collect_hardware_traits():
        digest.update(b'|')
        digest.update(item.encode('utf-8'))
    hex12 = digest.hexdigest()[:12].upper()
    return f'CD-{hex12[0:4]}-{hex12[4:8]}-{hex12[8:12]}'


def _today_utc() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


def _parse_iso_date(value: str, field_name: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(str(value or '').strip())
    except ValueError as exc:
        raise LicenseError(f'License {field_name} is not a valid YYYY-MM-DD date.') from exc


def _check_and_update_clock_guard(today: _dt.date) -> None:
    """Detect significant backward system-clock tampering used to bypass 1-year expiry."""
    guard_file = clock_guard_path()
    last_seen: _dt.date | None = None
    if guard_file.is_file():
        try:
            obj = json.loads(guard_file.read_text(encoding='utf-8'))
            if isinstance(obj, dict) and obj.get('max_utc_date'):
                last_seen = _dt.date.fromisoformat(str(obj['max_utc_date']))
        except Exception:
            last_seen = None

    if last_seen is not None and today < (last_seen - _dt.timedelta(days=CLOCK_ROLLBACK_GRACE_DAYS)):
        raise LicenseError(
            f'System clock appears to have been set backwards (current date {today.isoformat()}, '
            f'previously {last_seen.isoformat()}). Correct this computer\'s date and time to use Café Desk.'
        )

    newest = max(today, last_seen) if last_seen is not None else today
    if last_seen != newest:
        try:
            guard_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = guard_file.with_suffix('.tmp')
            tmp.write_text(json.dumps({'max_utc_date': newest.isoformat()}, indent=2), encoding='utf-8')
            tmp.replace(guard_file)
        except OSError:
            pass


def verify_license_token(
    token: str,
    *,
    expected_machine_id: str | None = None,
    public_key_b64: str | None = None,
    today: _dt.date | None = None,
    enforce_clock_guard: bool = True,
) -> dict:
    """Verify an Ed25519-signed license token and return normalized license details."""
    cleaned = re.sub(r'\s+', '', str(token or ''))
    if not cleaned:
        raise LicenseError('Enter an activation key.')
    parts = cleaned.split('.')
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise LicenseError('Invalid activation key. Expected a key starting with "CDLIC1.".')

    payload_bytes = _b64u_decode(parts[1])
    sig_bytes = _b64u_decode(parts[2])
    pub_raw = _b64u_decode(public_key_b64 or configured_public_key_b64())
    if len(pub_raw) != 32:
        raise LicenseError('Configured owner public key is invalid.')

    pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    signing_input = f'{parts[0]}.{parts[1]}'.encode('ascii')
    try:
        pub.verify(sig_bytes, signing_input)
    except InvalidSignature as exc:
        raise LicenseError('Activation key signature is invalid or was not issued by the owner.') from exc

    try:
        payload = json.loads(payload_bytes.decode('utf-8'))
    except Exception as exc:
        raise LicenseError('Activation key payload could not be read.') from exc
    if not isinstance(payload, dict) or payload.get('v') != 1:
        raise LicenseError('Unsupported activation key version.')

    token_mid = normalize_machine_id(payload.get('mid', ''))
    local_mid = normalize_machine_id(expected_machine_id or get_machine_id())
    if token_mid != local_mid:
        raise LicenseError(
            f'This activation key belongs to another computer ({token_mid}). '
            f'This computer\'s Machine ID is {local_mid}. Please request a new key from the owner.'
        )

    issued_date = _parse_iso_date(payload.get('iat', ''), 'issue date')
    expiry_date = _parse_iso_date(payload.get('exp', ''), 'expiry date')
    if expiry_date < issued_date:
        raise LicenseError('License expiry date precedes its issue date.')

    now_date = today or _today_utc()
    if enforce_clock_guard and today is None:
        _check_and_update_clock_guard(now_date)

    if now_date < (issued_date - _dt.timedelta(days=CLOCK_ROLLBACK_GRACE_DAYS)):
        raise LicenseError(
            f'This computer\'s date ({now_date.isoformat()}) is earlier than the license issue date '
            f'({issued_date.isoformat()}). Check Windows date & time settings.'
        )
    if now_date > expiry_date:
        raise LicenseError(
            f'Your 1-year Café Desk license expired on {expiry_date.isoformat()}. '
            f'Send Machine ID {local_mid} to the owner to renew.'
        )

    days_left = (expiry_date - now_date).days
    customer = ' '.join(str(payload.get('cust') or 'Licensed Café').split())[:80] or 'Licensed Café'
    license_id = str(payload.get('lid') or '')[:40]
    return {
        'valid': True,
        'machine_id': token_mid,
        'customer': customer,
        'issued_at': issued_date.isoformat(),
        'expires_at': expiry_date.isoformat(),
        'days_remaining': days_left,
        'license_id': license_id,
        'token': cleaned,
    }


def extract_token_from_text(raw_text: str) -> str:
    """Accept either a raw `CDLIC1...` string or a `.cafelicense` / `license.json` file body."""
    txt = (raw_text or '').strip()
    if not txt:
        raise LicenseError('Activation file or key is empty.')
    if txt.startswith('{'):
        try:
            obj = json.loads(txt)
            if isinstance(obj, dict) and obj.get('token'):
                return str(obj['token']).strip()
        except Exception as exc:
            raise LicenseError('Selected license file is damaged.') from exc
    m = re.search(r'CDLIC1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', re.sub(r'\s+', '', txt))
    if m:
        return m.group(0)
    return txt


def activate_and_save_license(raw_key_or_json: str, *, public_key_b64: str | None = None, today: _dt.date | None = None) -> dict:
    """Verify a token for THIS computer and save it to per-PC `license.json`."""
    token = extract_token_from_text(raw_key_or_json)
    info = verify_license_token(token, public_key_b64=public_key_b64, today=today)
    target = license_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'token': info['token'],
        'customer': info['customer'],
        'machine_id': info['machine_id'],
        'issued_at': info['issued_at'],
        'expires_at': info['expires_at'],
        'license_id': info['license_id'],
    }
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(record, indent=2), encoding='utf-8')
    tmp.replace(target)
    return info


def current_license_status(*, public_key_b64: str | None = None, today: _dt.date | None = None) -> dict:
    """Return current computer's license status dict (`valid` bool + metadata or `reason`)."""
    mid = get_machine_id()
    target = license_file_path()
    if not target.is_file():
        return {
            'valid': False,
            'machine_id': mid,
            'reason': 'This computer has not been activated yet.',
        }
    try:
        raw = target.read_text(encoding='utf-8')
        token = extract_token_from_text(raw)
        return verify_license_token(token, expected_machine_id=mid, public_key_b64=public_key_b64, today=today)
    except (OSError, LicenseError) as exc:
        return {
            'valid': False,
            'machine_id': mid,
            'reason': str(exc),
        }
