"""Supabase Cloud Sync & Remote Over-the-Internet App Updater for Café Desk.

Architecture & Security Guarantees:
1. Offline-First Always: Every network call uses strict timeouts and graceful error
   returns. If the internet café loses Wi-Fi or Supabase is unconfigured, Café Desk
   continues working 100% locally without crashing or blocking staff.
2. Public vs Private Keys:
   - Customer PCs ONLY ever use the public Supabase `url` + `anon` key (safe for
     client apps because `supabase_schema.sql` enforces Row-Level Security + RPCs).
   - Your owner `service_role` key NEVER touches `CafeDesk.exe` or GitHub; it lives
     exclusively in gitignored `owner-keys/supabase-owner.json` on your own PC.
3. Tamper-Proof Remote App Updates:
   - Remote installer downloads enforce `https://`, stream in chunks, and verify an
     exact 64-hex SHA-256 checksum BEFORE allowing execution. Releases are ALSO
     Ed25519-signed by the owner (`app_releases.signature` over
     `CAFE-DESK-RELEASE-V1:{version}:{sha256}`) and verified against the embedded
     public key — a compromised Supabase account alone cannot push a fake installer.
     The `anon` key is public by design (embedded in the app and the web UI), so
     the release row is the only attack surface; unsigned releases are therefore
     REFUSED outright (no SHA-256-only fallback) on customer PCs.
   - Updating `CafeDesk.exe` preserves `%APPDATA%\\CafeDesk\\` (licenses, PIN,
     prices, and sales DB stay intact).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

import license_guard

from version import __version__ as APP_VERSION  # single source of truth is version.py

# Default PUBLIC Supabase project endpoint & anon key (safe for client builds — RLS + RPC enforced; NEVER put service_role here!).
PUBLIC_PROJECT_SUPABASE_URL = 'https://rmtkppzqzxuuuhbeufjy.supabase.co'
PUBLIC_PROJECT_SUPABASE_ANON_KEY = (
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
    'eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJtdGtwcHpxenh1dXVoYmV1Zmp5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ4NDg5ODksImV4cCI6MjExMDQyNDk4OX0.'
    'FES1qZBI3btnS3Z96x17pXh3hCneM37mz-tKW7jU2cc'
)
DEFAULT_SUPABASE_URL = os.environ.get('CAFE_DESK_SUPABASE_URL', PUBLIC_PROJECT_SUPABASE_URL).strip()
DEFAULT_SUPABASE_ANON_KEY = os.environ.get('CAFE_DESK_SUPABASE_ANON_KEY', PUBLIC_PROJECT_SUPABASE_ANON_KEY).strip()

OWNER_SUPABASE_CONFIG_PATH = Path(__file__).resolve().parent / 'owner-keys' / 'supabase-owner.json'

_HEX64_RE = re.compile(r'^[0-9a-fA-F]{64}$')


def default_station_name() -> str:
    """Return a clean human-readable computer name (e.g. `COUNTER-PC` or `DESK-2`)."""
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ''
    cleaned = ' '.join(host.split())[:40]
    return cleaned or 'Counter PC 1'


def parse_semver(version_str: str) -> tuple[int, ...]:
    """Convert `'0.8.1'` or `'v1.2.0'` into a comparable integer tuple `(0, 8, 1)`."""
    raw = str(version_str or '').strip().lstrip('vV')
    parts = re.findall(r'\d+', raw)
    if not parts:
        return (0, 0, 0)
    return tuple(int(p) for p in parts[:4])


def is_newer_version(remote_version: str, current_version: str = APP_VERSION) -> bool:
    rem = list(parse_semver(remote_version))
    cur = list(parse_semver(current_version))
    width = max(len(rem), len(cur), 3)
    rem.extend([0] * (width - len(rem)))
    cur.extend([0] * (width - len(cur)))
    return tuple(rem) > tuple(cur)


SHOP_SYNC_MIN_SECRET_LEN = 10


def hash_shop_sync_secret(shop_id: str, sync_secret: str) -> str:
    """Derive a domain-separated SHA-256 hex digest so raw shop sync passwords aren't sent in cleartext.

    Requires 10+ characters (security fix: 4-char secrets were brute-forceable online).
    Existing shops with shorter passcodes must rotate: the owner deletes their
    `shop_sync_profiles` row in Supabase, then the shop re-pushes with a new 10+ char passcode.
    """
    norm_shop = re.sub(r'[^A-Z0-9_-]', '', str(shop_id or '').upper())[:40]
    secret = str(sync_secret or '').strip()
    if not norm_shop or len(secret) < SHOP_SYNC_MIN_SECRET_LEN:
        raise ValueError(
            f'Enter both a Shop ID (e.g. SHOP-SUNRISE) and a Shop Sync Passcode '
            f'({SHOP_SYNC_MIN_SECRET_LEN}+ characters, letters + numbers recommended).'
        )
    material = f'CAFE-DESK-SYNC-V1:{norm_shop}:{secret}'.encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def normalize_supabase_url(url: str) -> str:
    raw = str(url or '').strip().rstrip('/')
    if not raw:
        return ''
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        raise ValueError('Supabase Project URL must look like https://your-project.supabase.co')
    if parsed.scheme != 'https' and not parsed.hostname in ('127.0.0.1', 'localhost'):
        raise ValueError('Supabase Project URL must use https:// for security.')
    return f'{parsed.scheme}://{parsed.netloc}'


def resolve_client_supabase(settings: dict | None = None) -> tuple[str, str]:
    """Return `(supabase_url, anon_key)` from settings, environment, or baked defaults."""
    cfg = settings or {}
    url = str(cfg.get('supabase_url') or DEFAULT_SUPABASE_URL or '').strip()
    key = str(cfg.get('supabase_anon_key') or DEFAULT_SUPABASE_ANON_KEY or '').strip()
    if not url or not key:
        return '', ''
    try:
        return normalize_supabase_url(url), key
    except ValueError:
        return '', ''


def is_supabase_configured(settings: dict | None = None) -> bool:
    url, key = resolve_client_supabase(settings)
    return bool(url and key)


# ---------------------------------------------------------------------------
# Low-level PostgREST / RPC transport (Python stdlib urllib — zero bloat)
# ---------------------------------------------------------------------------
def _supabase_request(
    base_url: str,
    api_key: str,
    method: str,
    subpath: str,
    *,
    payload: object | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> object:
    clean_base = normalize_supabase_url(base_url)
    if not clean_base or not api_key:
        raise ValueError('Supabase URL and API key are not configured yet.')
    full_url = f'{clean_base}{subpath}'
    headers = {
        'apikey': api_key,
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json',
        'User-Agent': f'CafeDesk/{APP_VERSION}',
    }
    if extra_headers:
        headers.update(extra_headers)
    body_bytes: bytes | None = None
    if payload is not None:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'

    req = urllib.request.Request(full_url, data=body_bytes, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace').strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        err_txt = ''
        try:
            err_txt = exc.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        detail = err_txt
        try:
            parsed_err = json.loads(err_txt)
            if isinstance(parsed_err, dict):
                detail = str(parsed_err.get('message') or parsed_err.get('error_description') or parsed_err.get('error') or err_txt)
        except Exception:
            pass
        raise RuntimeError(f'Supabase HTTP {exc.code}: {detail or exc.reason}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Could not reach cloud server ({exc.reason}). Check internet connection.') from exc


# ---------------------------------------------------------------------------
# 1. Remote Over-the-Internet App Updater (No physical shop visits needed!)
# ---------------------------------------------------------------------------
RELEASE_SIGNING_DOMAIN = 'CAFE-DESK-RELEASE-V1'


def release_signing_message(version: str, sha256_hex: str) -> bytes:
    """Canonical bytes signed for a release: `CAFE-DESK-RELEASE-V1:{version}:{sha256}`."""
    clean_ver = str(version or '').strip().lstrip('vV')
    clean_hash = str(sha256_hex or '').strip().lower()
    return f'{RELEASE_SIGNING_DOMAIN}:{clean_ver}:{clean_hash}'.encode('utf-8')


def _b64u_decode_loose(text: str) -> bytes:
    cleaned = re.sub(r'\s+', '', str(text or ''))
    pad = '=' * ((4 - len(cleaned) % 4) % 4)
    return base64.urlsafe_b64decode(cleaned + pad)


def sign_release_sha256(version: str, sha256_hex: str, private_key_b64: str) -> str:
    """Sign a release SHA-256 with the owner's Ed25519 private key (owner laptop only)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = _b64u_decode_loose(private_key_b64)
    if len(raw) != 32:
        raise ValueError('Owner Ed25519 private key must decode to 32 bytes.')
    priv = Ed25519PrivateKey.from_private_bytes(raw)
    sig = priv.sign(release_signing_message(version, sha256_hex))
    return base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')


def verify_release_signature(
    version: str,
    sha256_hex: str,
    signature_b64: str,
    public_key_b64: str | None = None,
) -> bool:
    """Verify a release signature against the embedded owner public key. Never raises."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        pub_raw = _b64u_decode_loose(public_key_b64 or license_guard.configured_public_key_b64())
        if len(pub_raw) != 32:
            return False
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
        sig = _b64u_decode_loose(signature_b64)
        pub.verify(sig, release_signing_message(version, sha256_hex))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    except Exception:
        return False


def check_for_app_update(
    settings: dict | None = None,
    *,
    current_version: str = APP_VERSION,
    supabase_url: str | None = None,
    anon_key: str | None = None,
    timeout: float = 8.0,
) -> dict:
    """Query `public.app_releases` on Supabase and compare against `current_version`."""
    url, key = (normalize_supabase_url(supabase_url), anon_key or '') if supabase_url else resolve_client_supabase(settings)
    if not url or not key:
        return {
            'ok': False,
            'configured': False,
            'error': 'Cloud updates are not configured yet. Add your Supabase Project URL and Anon Key in Business Settings.',
        }
    try:
        rows = _supabase_request(
            url,
            key,
            'GET',
            '/rest/v1/app_releases?select=version,download_url,sha256,signature,release_notes,mandatory,published_at&order=published_at.desc&limit=10',
            timeout=timeout,
        )
    except RuntimeError as exc:
        # Backward compat: databases migrated before release signing lack the `signature`
        # column (PostgREST answers 400/42703). Any OTHER failure — bad key, network,
        # rate limit — must surface as-is instead of silently downgrading the caller to
        # the unsigned release path.
        if not re.search(r'\b(400|42703|column .*signature .*does not exist)\b', str(exc), re.IGNORECASE):
            raise
        rows = _supabase_request(
            url,
            key,
            'GET',
            '/rest/v1/app_releases?select=version,download_url,sha256,release_notes,mandatory,published_at&order=published_at.desc&limit=10',
            timeout=timeout,
        )
    if not isinstance(rows, list) or not rows:
        return {
            'ok': True,
            'configured': True,
            'update_available': False,
            'current_version': current_version,
            'latest_version': current_version,
            'message': f'You are running Café Desk v{current_version} (no newer cloud release published yet).',
        }
    # Pick highest semantic version among latest rows
    best = max(rows, key=lambda r: parse_semver(str((r or {}).get('version') or '0.0.0')))
    latest_ver = str(best.get('version') or current_version).strip()
    has_newer = is_newer_version(latest_ver, current_version)
    return {
        'ok': True,
        'configured': True,
        'update_available': has_newer,
        'current_version': current_version,
        'latest_version': latest_ver,
        'download_url': str(best.get('download_url') or '').strip(),
        'sha256': str(best.get('sha256') or '').strip().lower(),
        'signature': str(best.get('signature') or '').strip(),
        'release_notes': str(best.get('release_notes') or '').strip(),
        'mandatory': bool(best.get('mandatory', False)),
        'published_at': str(best.get('published_at') or ''),
    }



def fetch_app_announcements(settings: dict | None = None, *, limit: int = 10, timeout: float = 8.0) -> list[dict]:
    """Fetch currently active customer-facing notices and promotions."""
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        return []
    try:
        rows = _supabase_request(
            url, key, 'GET',
            '/rest/v1/app_announcements?select=title,message,kind,published_at&active=eq.true&order=published_at.desc&limit=' + str(max(1, min(int(limit), 30))),
            timeout=timeout,
        )
    except Exception:
        # Older installations may not have applied the optional announcements migration yet.
        return []
    return rows if isinstance(rows, list) else []


def compute_file_sha256(file_path: Path | str) -> str:
    h = hashlib.sha256()
    with open(file_path, 'rb') as fh:
        while True:
            chunk = fh.read(1024 * 256)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def download_and_verify_update(
    download_url: str,
    expected_sha256: str,
    dest_dir: Path | str,
    *,
    version_label: str = 'latest',
    expected_signature: str = '',
    progress_cb: Callable[[int, int], None] | None = None,
    timeout: float = 60.0,
    allow_unsigned: bool = False,
) -> Path:
    """Download a remote Café Desk installer/zip over HTTPS and verify it end to end.

    If `expected_signature` is provided, the Ed25519 owner signature over
    `CAFE-DESK-RELEASE-V1:{version}:{sha256}` is also verified and a mismatch
    refuses the file.

    Security (audit fix): the unsigned path is now CLOSED on customer PCs. A missing
    signature used to fall through to SHA-256-only verification, which meant a leaked
    Supabase anon key (public by design, and embedded in the app) plus a writable
    `app_releases` row could push an arbitrary `.exe` to every café. `allow_unsigned`
    exists only for the test suite / owner tooling; it is refused in frozen builds.
    """
    raw_url = str(download_url or '').strip()
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme != 'https' and parsed.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Refusing update download: installer URL must use HTTPS.')
    clean_hash = str(expected_sha256 or '').strip().lower()
    if not _HEX64_RE.match(clean_hash):
        raise ValueError('Refusing update download: release is missing a valid 64-character SHA-256 checksum.')
    # Fail closed BEFORE downloading: no signature means no update on a customer PC.
    clean_sig = str(expected_signature or '').strip()
    unsigned_allowed = bool(allow_unsigned) and not getattr(sys, 'frozen', False)
    if not clean_sig and not unsigned_allowed:
        raise ValueError(
            'Refusing update download: this release is not signed by the owner. '
            'Ask the owner to publish a signed release (this build will not install '
            'an unverified installer).'
        )

    out_folder = Path(dest_dir)
    out_folder.mkdir(parents=True, exist_ok=True)
    url_name = Path(urllib.parse.unquote(parsed.path)).name or ''
    ext = '.zip' if url_name.lower().endswith('.zip') else '.exe'
    safe_ver = re.sub(r'[^A-Za-z0-9._-]', '', str(version_label or 'latest'))[:24] or 'latest'
    final_path = out_folder / f'CafeDesk-Setup-v{safe_ver}{ext}'
    part_path = out_folder / f'.CafeDesk-Setup-v{safe_ver}{ext}.part'

    hasher = hashlib.sha256()
    req = urllib.request.Request(raw_url, headers={'User-Agent': f'CafeDesk-Updater/{APP_VERSION}'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part_path, 'wb') as out_fh:
            total_bytes = int(resp.headers.get('Content-Length') or 0)
            downloaded = 0
            while True:
                chunk = resp.read(1024 * 128)
                if not chunk:
                    break
                out_fh.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total_bytes)
            out_fh.flush()
            os.fsync(out_fh.fileno())
    except Exception:
        part_path.unlink(missing_ok=True)
        raise

    actual_hash = hasher.hexdigest().lower()
    if actual_hash != clean_hash:
        part_path.unlink(missing_ok=True)
        raise ValueError(
            f'Security check failed: downloaded installer SHA-256 ({actual_hash[:12]}…) '
            f'did not match owner signature ({clean_hash[:12]}…). File deleted.'
        )

    if clean_sig:
        if not verify_release_signature(version_label, clean_hash, clean_sig):
            part_path.unlink(missing_ok=True)
            raise ValueError(
                'Security check failed: installer Ed25519 signature is invalid or was not '
                'issued by the owner. File deleted. Do not run this installer.'
            )

    os.replace(part_path, final_path)
    return final_path


# ---------------------------------------------------------------------------
# 2. Online Activation Request & One-Click Key Approval Pull (Customer PC)
# ---------------------------------------------------------------------------
def _activation_request_path() -> Path:
    return license_guard.host_license_dir() / 'activation-request.json'


def _save_activation_request_id(machine_id: str, request_id: object) -> None:
    """Persist the unguessable request UUID used to authorize online key delivery."""
    try:
        clean_mid = license_guard.normalize_machine_id(machine_id)
        clean_id = str(uuid.UUID(str(request_id or '').strip()))
    except (ValueError, TypeError, AttributeError):
        return
    path = _activation_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'machine_id': clean_mid, 'request_id': clean_id}), encoding='utf-8')
    os.replace(temp, path)


def _load_activation_request_id(machine_id: str) -> str:
    try:
        clean_mid = license_guard.normalize_machine_id(machine_id)
        data = json.loads(_activation_request_path().read_text(encoding='utf-8'))
        if license_guard.normalize_machine_id(str(data.get('machine_id') or '')) != clean_mid:
            return ''
        return str(uuid.UUID(str(data.get('request_id') or '')))
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return ''


def request_online_activation(
    settings: dict | None,
    *,
    machine_id: str | None = None,
    shop_id: str = '',
    shop_name: str = '',
    station_name: str = '',
    requested_kind: str = 'license',
    contact_phone: str = '',
) -> dict:
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        raise ValueError('Supabase Cloud is not configured on this PC yet.')
    mid = license_guard.normalize_machine_id(machine_id or license_guard.get_machine_id())
    res = _supabase_request(
        url,
        key,
        'POST',
        '/rest/v1/rpc/submit_activation_request',
        payload={
            'p_machine_id': mid,
            'p_shop_id': shop_id,
            'p_shop_name': shop_name,
            'p_station_name': station_name or default_station_name(),
            'p_requested_kind': requested_kind,
            'p_contact_phone': contact_phone,
        },
    )
    if isinstance(res, dict) and res.get('ok'):
        _save_activation_request_id(mid, res.get('request_id'))
        return res
    err = res.get('error') if isinstance(res, dict) else 'Unexpected cloud response.'
    raise RuntimeError(str(err or 'Activation request failed.'))


def request_license_with_contact(settings: dict | None, *, name: str, company: str, phone: str,
                                 machine_id: str | None = None) -> dict:
    """Submit the customer's details; never send keys or private credentials."""
    name, company, phone = (' '.join(str(v or '').split()) for v in (name, company, phone))
    if not name or not company or not phone:
        raise ValueError('Enter your name, company name and phone number.')
    if len(re.sub(r'[^0-9]', '', phone)) < 7:
        raise ValueError('Enter a valid phone number (at least 7 digits).')
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        raise ValueError('Online requests are not configured on this computer.')
    mid = license_guard.normalize_machine_id(machine_id or license_guard.get_machine_id())
    res = _supabase_request(url, key, 'POST', '/rest/v1/rpc/submit_license_contact_request',
        payload={'p_machine_id': mid, 'p_contact_name': name, 'p_company': company,
                 'p_contact_phone': phone, 'p_station_name': default_station_name()})
    if not isinstance(res, dict) or not res.get('ok'):
        raise RuntimeError(str(res.get('error') if isinstance(res, dict) else 'Unexpected cloud response.'))
    _save_activation_request_id(mid, res.get('request_id'))
    return res


def sync_machine_entitlements_from_cloud(
    settings: dict | None,
    *,
    machine_id: str | None = None,
    public_key_b64: str | None = None,
) -> dict:
    """Pull approved `CDLIC1` / `CDADD1` keys for THIS Machine ID from Supabase and activate locally.

    Also enforces remote owner revocation if a previously issued `license_id` was marked `revoked=true`.
    """
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        raise ValueError('Supabase Cloud is not configured on this PC yet.')
    mid = license_guard.normalize_machine_id(machine_id or license_guard.get_machine_id())
    res = _supabase_request(
        url,
        key,
        'POST',
        '/rest/v1/rpc/check_machine_entitlements',
        payload={'p_machine_id': mid, 'p_request_id': _load_activation_request_id(mid) or None},
    )
    if not isinstance(res, dict) or not res.get('ok'):
        err = res.get('error') if isinstance(res, dict) else 'Could not check online keys.'
        raise RuntimeError(str(err))

    activated_items: list[str] = []
    revoked_items: list[str] = []

    # 1. Apply remote revocations for this Machine ID if any match installed files
    revoked_ids = {
        str(r.get('license_id') or '').strip()
        for r in (res.get('revoked_keys') or [])
        if isinstance(r, dict) and r.get('license_id')
    }
    if revoked_ids:
        cur_lic = license_guard.current_license_status(public_key_b64=public_key_b64)
        if cur_lic.get('license_id') and str(cur_lic.get('license_id')) in revoked_ids:
            license_guard.license_file_path().unlink(missing_ok=True)
            revoked_items.append(f'Base License ({cur_lic.get("license_id")})')
        cur_add = license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK, public_key_b64=public_key_b64)
        if cur_add.get('license_id') and str(cur_add.get('license_id')) in revoked_ids:
            license_guard.addon_license_file_path(license_guard.FEATURE_PDF_UNLOCK).unlink(missing_ok=True)
            revoked_items.append(f'PDF Unlock Add-on ({cur_add.get("license_id")})')

    # 2. Activate any valid Ed25519-signed keys waiting in the cloud for this Machine ID
    for item in (res.get('active_keys') or []):
        if not isinstance(item, dict):
            continue
        tok = str(item.get('token') or '').strip()
        if not tok:
            continue
        try:
            if tok.startswith(license_guard.ADDON_TOKEN_PREFIX + '.'):
                check = license_guard.verify_addon_token(
                    tok,
                    feature=license_guard.FEATURE_PDF_UNLOCK,
                    expected_machine_id=mid,
                    public_key_b64=public_key_b64,
                )
                if check.get('valid'):
                    target = license_guard.addon_license_file_path(license_guard.FEATURE_PDF_UNLOCK)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps({
                        'token': check['token'],
                        'feature': license_guard.FEATURE_PDF_UNLOCK,
                        'customer': check['customer'],
                        'machine_id': check['machine_id'],
                        'issued_at': check['issued_at'],
                        'expires_at': check['expires_at'],
                        'license_id': check['license_id'],
                    }, indent=2), encoding='utf-8')
                    activated_items.append(f'Paid PDF Password Remover (expires {check["expires_at"]})')
            elif tok.startswith(license_guard.TOKEN_PREFIX + '.'):
                check = license_guard.verify_license_token(
                    tok,
                    expected_machine_id=mid,
                    public_key_b64=public_key_b64,
                )
                if check.get('valid'):
                    target = license_guard.license_file_path()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps({
                        'token': check['token'],
                        'customer': check['customer'],
                        'shop_id': check.get('shop_id', ''),
                        'seat_no': check.get('seat_no'),
                        'seat_max': check.get('seat_max'),
                        'seat_label': check.get('seat_label', ''),
                        'machine_id': check['machine_id'],
                        'issued_at': check['issued_at'],
                        'expires_at': check['expires_at'],
                        'license_id': check['license_id'],
                    }, indent=2), encoding='utf-8')
                    seat_txt = (
                        f' [Seat {check["seat_no"]}/{check["seat_max"]}]'
                        if check.get('seat_no') and check.get('seat_max')
                        else ''
                    )
                    activated_items.append(f'1-Year License for {check["customer"]}{seat_txt} (expires {check["expires_at"]})')
        except license_guard.LicenseError:
            continue

    return {
        'ok': True,
        'machine_id': mid,
        'activated': activated_items,
        'revoked': revoked_items,
        'revocation_reasons': sorted({str(r.get('reason') or '').strip()
            for r in (res.get('revoked_keys') or [])
            if isinstance(r, dict) and str(r.get('license_id') or '') in revoked_ids
            and str(r.get('reason') or '').strip()}),
    }


# ---------------------------------------------------------------------------
# 3. Multi-PC Shop Price List & Combined Sales Sync (2+ Computers in 1 Shop)
# ---------------------------------------------------------------------------
def push_shop_price_profile(settings: dict, sync_secret: str) -> dict:
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        raise ValueError('Configure your Supabase Project URL and Anon Key first.')
    shop_id = str(settings.get('shop_id') or '').strip().upper()
    key_hash = hash_shop_sync_secret(shop_id, sync_secret)
    res = _supabase_request(
        url,
        key,
        'POST',
        '/rest/v1/rpc/push_shop_profile',
        payload={
            'p_shop_id': shop_id,
            'p_sync_key_sha256': key_hash,
            'p_business_name': str(settings.get('business_name') or 'Internet Café'),
            'p_currency': str(settings.get('currency') or 'R'),
            'p_prices_json': list(settings.get('prices') or []),
            'p_station': str(settings.get('station_name') or default_station_name()),
        },
    )
    if not isinstance(res, dict) or not res.get('ok'):
        err = res.get('error') if isinstance(res, dict) else 'Could not push shop profile.'
        raise RuntimeError(str(err))
    return res


def pull_shop_price_profile(settings: dict, sync_secret: str) -> dict:
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        raise ValueError('Configure your Supabase Project URL and Anon Key first.')
    shop_id = str(settings.get('shop_id') or '').strip().upper()
    key_hash = hash_shop_sync_secret(shop_id, sync_secret)
    res = _supabase_request(
        url,
        key,
        'POST',
        '/rest/v1/rpc/pull_shop_profile',
        payload={'p_shop_id': shop_id, 'p_sync_key_sha256': key_hash},
    )
    if not isinstance(res, dict) or not res.get('ok'):
        err = res.get('error') if isinstance(res, dict) else 'Could not pull shop profile.'
        raise RuntimeError(str(err))
    return res


def sync_multi_pc_sales(settings: dict, sync_secret: str, local_sales: list[dict], *, since_iso: str = '') -> dict:
    """Upload this PC's recorded sales and return all PCs' sales for the shop."""
    url, key = resolve_client_supabase(settings)
    if not url or not key:
        raise ValueError('Configure your Supabase Project URL and Anon Key first.')
    shop_id = str(settings.get('shop_id') or '').strip().upper()
    key_hash = hash_shop_sync_secret(shop_id, sync_secret)
    res = _supabase_request(
        url,
        key,
        'POST',
        '/rest/v1/rpc/sync_shop_sales',
        payload={
            'p_shop_id': shop_id,
            'p_sync_key_sha256': key_hash,
            'p_sales': local_sales,
            'p_since': since_iso,
        },
        timeout=18.0,
    )
    if not isinstance(res, dict) or not res.get('ok'):
        err = res.get('error') if isinstance(res, dict) else 'Could not sync shop sales.'
        raise RuntimeError(str(err))
    return res


ALLOWED_TELEMETRY_SCREENS = frozenset({
    'Home',
    'Price list',
    'PDF tools',
    'Bluetooth files',
    'Scan & images',
    'A4 picture sheets',
    'Career documents',
    'Cards & invitations',
    'Forms creator',
    'Receipts',
    'Sales reports',
    'Price settings',
    'Business settings',
    'AI settings',
    'Projects & recovery',
    'About & contact',
    'Help & privacy',
})


def send_workspace_telemetry(
    settings: dict | None,
    *,
    current_screen: str = 'Home',
    screen_counts: dict[str, int] | None = None,
    machine_id: str | None = None,
    app_version: str = APP_VERSION,
    os_platform: str | None = None,
    online_seconds: int = 0,
    timeout: float = 5.0,
) -> dict:
    """Send strictly anonymized screen-visit counts & heartbeat to Supabase.

    Privacy guarantee: Only whitelisted screen names (`ALLOWED_TELEMETRY_SCREENS`),
    integer counts, Machine ID, Shop/Station/City labels, app version, and whole-second
    online duration are sent. Never sends document contents, filenames, or customer
    personal data. Silently returns `{'ok': False}` when offline so shop work is never
    interrupted.
    """
    cfg = settings or {}
    url, key = resolve_client_supabase(cfg)
    if not url or not key:
        return {'ok': False, 'reason': 'unconfigured'}
    try:
        mid = license_guard.normalize_machine_id(machine_id or license_guard.get_machine_id())
        lic = license_guard.current_license_status()
        shop_id = str(cfg.get('shop_id') or lic.get('shop_id') or '').strip().upper()[:40]
        shop_name = str(cfg.get('business_name') or lic.get('customer') or 'Café Desk Station').strip()[:80]
        station_name = str(cfg.get('station_name') or lic.get('seat_label') or default_station_name()).strip()[:40]
        city_label = str(cfg.get('city_label') or '').strip()[:60]
        cur_scr = current_screen if current_screen in ALLOWED_TELEMETRY_SCREENS else 'Home'
        clean_counts: dict[str, int] = {}
        for k, v in (screen_counts or {}).items():
            if k in ALLOWED_TELEMETRY_SCREENS:
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                if iv > 0:
                    clean_counts[k] = min(iv, 5000)
        try:
            online_secs = max(0, min(int(online_seconds or 0), 86400))
        except (TypeError, ValueError):
            online_secs = 0
        import sys as _sys
        plat = str(os_platform or _sys.platform)[:32]
        res = _supabase_request(
            url,
            key,
            'POST',
            '/rest/v1/rpc/record_workspace_telemetry',
            payload={
                'p_machine_id': mid,
                'p_shop_id': shop_id,
                'p_shop_name': shop_name,
                'p_station_name': station_name,
                'p_city_label': city_label,
                'p_app_version': str(app_version or APP_VERSION)[:24],
                'p_os_platform': plat,
                'p_current_screen': cur_scr,
                'p_screen_counts': clean_counts,
                'p_online_seconds': online_secs,
            },
            timeout=timeout,
        )
        return res if isinstance(res, dict) else {'ok': False}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


# ---------------------------------------------------------------------------
# 4. Owner-Only Private Cloud Tools (`owner-keys/supabase-owner.json` — GITIGNORED)
# ---------------------------------------------------------------------------
def load_owner_supabase_config(config_path: Path = OWNER_SUPABASE_CONFIG_PATH) -> dict:
    """Load owner-only Supabase `url` + `service_role_key` from gitignored `owner-keys/supabase-owner.json`."""
    env_url = os.environ.get('CAFE_DESK_SUPABASE_URL', '').strip()
    env_srv = os.environ.get('CAFE_DESK_SUPABASE_SERVICE_ROLE_KEY', '').strip()
    data: dict = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    url = normalize_supabase_url(str(data.get('supabase_url') or env_url or '')) if (data.get('supabase_url') or env_url) else ''
    srv = str(data.get('service_role_key') or env_srv or '').strip()
    anon = str(data.get('anon_key') or os.environ.get('CAFE_DESK_SUPABASE_ANON_KEY', '') or '').strip()
    return {'supabase_url': url, 'service_role_key': srv, 'anon_key': anon}


def save_owner_supabase_config(
    supabase_url: str,
    service_role_key: str,
    anon_key: str = '',
    *,
    config_path: Path = OWNER_SUPABASE_CONFIG_PATH,
) -> Path:
    """Save owner Supabase credentials inside gitignored `owner-keys/supabase-owner.json` (0600 permissions)."""
    clean_url = normalize_supabase_url(supabase_url)
    if not clean_url or not str(service_role_key or '').strip():
        raise ValueError('Both Supabase Project URL and service_role key are required.')
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'supabase_url': clean_url,
        'service_role_key': str(service_role_key).strip(),
        'anon_key': str(anon_key or '').strip(),
    }
    config_path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass
    return config_path


def owner_push_issued_records_to_supabase(
    records: list[dict],
    *,
    config_path: Path = OWNER_SUPABASE_CONFIG_PATH,
) -> int:
    """Upsert local `owner-keys/issued-licenses.jsonl` records to private `public.issued_machine_keys` table."""
    cfg = load_owner_supabase_config(config_path)
    if not cfg['supabase_url'] or not cfg['service_role_key']:
        raise ValueError(
            f'Owner Supabase config missing at {config_path}. Run:\n'
            f'  python admin_keygen.py --setup-supabase'
        )
    if not records:
        return 0
    rows = []
    seen_lids: set[str] = set()
    for rec in records:
        lid = str(rec.get('lid') or '').strip()
        tok = str(rec.get('token') or '').strip()
        mid = str(rec.get('mid') or '').strip()
        if not lid or not tok or not mid or lid in seen_lids:
            continue
        seen_lids.add(lid)
        feats = rec.get('feat') if isinstance(rec.get('feat'), list) else []
        ktype = 'addon' if (rec.get('type') == 'addon' or tok.startswith(license_guard.ADDON_TOKEN_PREFIX + '.')) else ('combo' if feats else 'license')
        rows.append({
            'license_id': lid,
            'machine_id': mid,
            'shop_id': str(rec.get('shop_id') or ''),
            'customer': str(rec.get('cust') or 'Licensed Café'),
            'seat_no': rec.get('seat_no'),
            'seat_max': rec.get('seat_max'),
            'seat_label': str(rec.get('seat_label') or ''),
            'key_type': ktype,
            'features': feats,
            'token': tok,
            'issued_at': str(rec.get('iat') or ''),
            'expires_at': str(rec.get('exp') or ''),
        })
    if not rows:
        return 0
    _supabase_request(
        cfg['supabase_url'],
        cfg['service_role_key'],
        'POST',
        '/rest/v1/issued_machine_keys?on_conflict=license_id',
        payload=rows,
        extra_headers={'Prefer': 'resolution=merge-duplicates,return=minimal'},
        timeout=20.0,
    )
    return len(rows)


def owner_publish_app_release(
    version: str,
    download_url: str,
    installer_path_or_sha256: str,
    *,
    release_notes: str = '',
    mandatory: bool = False,
    config_path: Path = OWNER_SUPABASE_CONFIG_PATH,
) -> dict:
    """Publish a new Café Desk release to `public.app_releases` so all customer shops can update remotely."""
    cfg = load_owner_supabase_config(config_path)
    if not cfg['supabase_url'] or not cfg['service_role_key']:
        raise ValueError(f'Owner Supabase config missing at {config_path}. Run `python admin_keygen.py --setup-supabase`.')
    clean_ver = str(version or '').strip().lstrip('vV')
    if not clean_ver:
        raise ValueError('Release version is required (e.g. 0.8.1).')
    parsed = urllib.parse.urlparse(str(download_url or '').strip())
    if parsed.scheme != 'https':
        raise ValueError('Release download_url must use https:// (e.g. Supabase Storage or GitHub Releases URL).')

    candidate = str(installer_path_or_sha256 or '').strip()
    if _HEX64_RE.match(candidate):
        sha256_hex = candidate.lower()
    else:
        fpath = Path(candidate)
        if not fpath.is_file():
            raise FileNotFoundError(f'Installer file not found (and not a 64-char SHA-256 hash): {candidate}')
        sha256_hex = compute_file_sha256(fpath)

    # Sign the release with the owner Ed25519 private key when available so
    # customer PCs can verify the installer even if Supabase is compromised.
    signature = ''
    priv_path = Path(__file__).resolve().parent / 'owner-keys' / 'cafedesk_owner.private.key'
    priv_b64 = os.environ.get('CAFE_DESK_PRIVATE_KEY', '').strip()
    if not priv_b64 and priv_path.is_file():
        try:
            priv_b64 = priv_path.read_text(encoding='utf-8').strip()
        except OSError:
            priv_b64 = ''
    if priv_b64:
        signature = sign_release_sha256(clean_ver, sha256_hex, priv_b64)

    row = {
        'version': clean_ver,
        'download_url': str(download_url).strip(),
        'sha256': sha256_hex,
        'signature': signature,
        'release_notes': str(release_notes or '').strip(),
        'mandatory': bool(mandatory),
    }
    _supabase_request(
        cfg['supabase_url'],
        cfg['service_role_key'],
        'POST',
        '/rest/v1/app_releases?on_conflict=version',
        payload=row,
        extra_headers={'Prefer': 'resolution=merge-duplicates,return=minimal'},
    )
    return row


def owner_revoke_license_id(
    license_id: str,
    reason: str = 'Revoked by owner',
    *,
    config_path: Path = OWNER_SUPABASE_CONFIG_PATH,
) -> None:
    """Remotely revoke an issued `license_id` on Supabase."""
    cfg = load_owner_supabase_config(config_path)
    if not cfg['supabase_url'] or not cfg['service_role_key']:
        raise ValueError(f'Owner Supabase config missing at {config_path}.')
    lid = re.sub(r'[^A-Za-z0-9_-]', '', str(license_id or '').strip())
    if not lid:
        raise ValueError('Provide a valid License ID to revoke.')
    _supabase_request(
        cfg['supabase_url'],
        cfg['service_role_key'],
        'PATCH',
        f'/rest/v1/issued_machine_keys?license_id=eq.{urllib.parse.quote(lid)}',
        payload={'revoked': True, 'revoke_reason': str(reason or 'Revoked by owner')[:160]},
        extra_headers={'Prefer': 'return=minimal'},
    )


def owner_list_pending_requests(*, config_path: Path = OWNER_SUPABASE_CONFIG_PATH) -> list[dict]:
    """Fetch pending online activation requests sent from customer PCs."""
    cfg = load_owner_supabase_config(config_path)
    if not cfg['supabase_url'] or not cfg['service_role_key']:
        raise ValueError(f'Owner Supabase config missing at {config_path}.')
    rows = _supabase_request(
        cfg['supabase_url'],
        cfg['service_role_key'],
        'GET',
        '/rest/v1/activation_requests?status=eq.pending&order=created_at.desc&limit=100',
    )
    return rows if isinstance(rows, list) else []
