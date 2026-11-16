#!/usr/bin/env python3
"""OWNER-ONLY License Key Generator for Café Desk (NEVER ship this file or `owner-keys/` to customers!).

Usage on YOUR private computer:
  1) First-time setup (generates your secret Ed25519 signing keypair in `owner-keys/`):
       python admin_keygen.py --init-keys --apply-public-key

  2) Issue a standard 1-year (365-day) computer license for a customer's Machine ID:
       python admin_keygen.py --machine-id CD-8F3A-91BC-204E --customer "Benoni Cyber Cafe" --days 365

  3) Approve & issue a PAID PDF Password Remover Add-on Key (`CDADD1...`) for a Machine ID:
       python admin_keygen.py --addon pdf_unlock --machine-id CD-8F3A-91BC-204E --customer "Benoni Cyber Cafe" --days 365

  4) Or issue a combo 1-year computer license that ALSO includes PDF Password Remover:
       python admin_keygen.py --include-pdf-unlock --machine-id CD-8F3A-91BC-204E --customer "Benoni Cyber Cafe" --days 365

  5) View, search, or open the visual dashboard of ALL issued Machine IDs & keys (`owner-keys/` — gitignored!):
       python admin_keygen.py --list
       python admin_keygen.py --search "Benoni"
       python admin_keygen.py --open-dashboard

  6) Or simply run with no arguments to open the interactive Owner Keygen & Registry menu:
       python admin_keygen.py
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as _dt
import html
import json
import os
import re
import sys
import uuid
import webbrowser
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import license_guard

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_KEY_DIR = REPO_DIR / 'owner-keys'
DEFAULT_PRIV_PATH = DEFAULT_KEY_DIR / 'cafedesk_owner.private.key'
DEFAULT_PUB_PATH = DEFAULT_KEY_DIR / 'cafedesk_owner.public.txt'
ISSUED_LOG_PATH = DEFAULT_KEY_DIR / 'issued-licenses.jsonl'
ISSUED_CSV_PATH = DEFAULT_KEY_DIR / 'issued-licenses.csv'
ISSUED_HTML_PATH = DEFAULT_KEY_DIR / 'issued-licenses-dashboard.html'


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _b64u_decode(text: str) -> bytes:
    cleaned = re.sub(r'\s+', '', str(text or ''))
    pad = '=' * ((4 - len(cleaned) % 4) % 4)
    return base64.urlsafe_b64decode(cleaned + pad)


def generate_owner_keypair(priv_path: Path = DEFAULT_PRIV_PATH, pub_path: Path = DEFAULT_PUB_PATH, overwrite: bool = False) -> tuple[str, str]:
    """Generate a fresh Ed25519 private/public keypair and save inside gitignored `owner-keys/`."""
    if priv_path.exists() and not overwrite:
        raise FileExistsError(f'Private key already exists at {priv_path}. Pass --force to overwrite.')
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_b64 = _b64u_encode(priv_raw)
    pub_b64 = _b64u_encode(pub_raw)

    priv_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_text(priv_b64 + '\n', encoding='utf-8')
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass
    pub_path.write_text(pub_b64 + '\n', encoding='utf-8')
    return priv_b64, pub_b64


def apply_public_key_to_guard(pub_b64: str, guard_file: Path | None = None) -> None:
    """Update `DEFAULT_OWNER_PUBLIC_KEY_B64` inside `license_guard.py` before building customer `.exe`."""
    target = guard_file or (REPO_DIR / 'license_guard.py')
    src = target.read_text(encoding='utf-8')
    updated, count = re.subn(
        r"^DEFAULT_OWNER_PUBLIC_KEY_B64\s*=\s*'[^']*'",
        f"DEFAULT_OWNER_PUBLIC_KEY_B64 = '{pub_b64}'",
        src,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f'Could not locate DEFAULT_OWNER_PUBLIC_KEY_B64 in {target}')
    target.write_text(updated, encoding='utf-8')


def load_private_key(priv_path: Path = DEFAULT_PRIV_PATH, private_key_b64: str | None = None) -> Ed25519PrivateKey:
    raw_b64 = (private_key_b64 or os.environ.get('CAFE_DESK_PRIVATE_KEY', '')).strip()
    if not raw_b64:
        if not priv_path.is_file():
            raise FileNotFoundError(
                f'Owner private key not found at {priv_path}.\n'
                f'Run this ONCE first to create your secret signing key:\n'
                f'  python admin_keygen.py --init-keys --apply-public-key'
            )
        raw_b64 = priv_path.read_text(encoding='utf-8').strip()
    raw = _b64u_decode(raw_b64)
    if len(raw) != 32:
        raise ValueError('Ed25519 private key must decode to 32 bytes.')
    return Ed25519PrivateKey.from_private_bytes(raw)


def sign_license_token(
    machine_id: str,
    customer: str = 'Licensed Café',
    days: int = 365,
    *,
    features: list[str] | None = None,
    issued_date: _dt.date | None = None,
    private_key_b64: str | None = None,
    priv_path: Path = DEFAULT_PRIV_PATH,
) -> tuple[str, dict]:
    """Create a signed `CDLIC1.<payload>.<signature>` license string for a single Machine ID."""
    if days < 1 or days > 3650:
        raise ValueError('License duration (--days) must be between 1 and 3650 days.')
    norm_mid = license_guard.normalize_machine_id(machine_id)
    clean_cust = ' '.join(str(customer or 'Licensed Café').split())[:80] or 'Licensed Café'
    iat = issued_date or _dt.datetime.now(_dt.timezone.utc).date()
    exp = iat + _dt.timedelta(days=int(days))
    lid = uuid.uuid4().hex[:12].upper()

    payload: dict = {
        'v': 1,
        'mid': norm_mid,
        'cust': clean_cust,
        'iat': iat.isoformat(),
        'exp': exp.isoformat(),
        'lid': lid,
    }
    if features:
        valid_feats = sorted({f for f in features if f in license_guard.KNOWN_PAID_FEATURES})
        if valid_feats:
            payload['feat'] = valid_feats
    payload_bytes = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    payload_b64 = _b64u_encode(payload_bytes)
    signing_input = f'{license_guard.TOKEN_PREFIX}.{payload_b64}'.encode('ascii')

    priv = load_private_key(priv_path=priv_path, private_key_b64=private_key_b64)
    sig_b64 = _b64u_encode(priv.sign(signing_input))
    token = f'{license_guard.TOKEN_PREFIX}.{payload_b64}.{sig_b64}'
    return token, payload


def sign_addon_token(
    machine_id: str,
    feature: str = license_guard.FEATURE_PDF_UNLOCK,
    customer: str = 'Licensed Café',
    days: int = 365,
    *,
    issued_date: _dt.date | None = None,
    private_key_b64: str | None = None,
    priv_path: Path = DEFAULT_PRIV_PATH,
) -> tuple[str, dict]:
    """Create an owner-approved Paid Add-on Key (`CDADD1.<payload>.<signature>`) locked to one Machine ID."""
    if feature not in license_guard.KNOWN_PAID_FEATURES:
        raise ValueError(f'Unknown paid feature "{feature}". Supported: {sorted(license_guard.KNOWN_PAID_FEATURES)}')
    if days < 1 or days > 3650:
        raise ValueError('Add-on duration (--days) must be between 1 and 3650 days.')
    norm_mid = license_guard.normalize_machine_id(machine_id)
    clean_cust = ' '.join(str(customer or 'Licensed Café').split())[:80] or 'Licensed Café'
    iat = issued_date or _dt.datetime.now(_dt.timezone.utc).date()
    exp = iat + _dt.timedelta(days=int(days))
    lid = uuid.uuid4().hex[:12].upper()

    payload = {
        'v': 1,
        'type': 'addon',
        'feat': [feature],
        'mid': norm_mid,
        'cust': clean_cust,
        'iat': iat.isoformat(),
        'exp': exp.isoformat(),
        'lid': lid,
    }
    payload_bytes = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    payload_b64 = _b64u_encode(payload_bytes)
    signing_input = f'{license_guard.ADDON_TOKEN_PREFIX}.{payload_b64}'.encode('ascii')

    priv = load_private_key(priv_path=priv_path, private_key_b64=private_key_b64)
    sig_b64 = _b64u_encode(priv.sign(signing_input))
    token = f'{license_guard.ADDON_TOKEN_PREFIX}.{payload_b64}.{sig_b64}'
    return token, payload


def _entry_kind_label(entry: dict) -> str:
    token = str(entry.get('token') or '')
    feats = entry.get('feat') or []
    if entry.get('type') == 'addon' or token.startswith(license_guard.ADDON_TOKEN_PREFIX + '.'):
        return 'Paid Add-on (' + (', '.join(feats) if feats else 'addon') + ')'
    if feats:
        return '1-Year Combo (' + ', '.join(feats) + ')'
    return '1-Year Computer License'


def load_issued_records(log_path: Path = ISSUED_LOG_PATH) -> list[dict]:
    """Read all issued Machine ID + key records from local gitignored `owner-keys/issued-licenses.jsonl`."""
    if not log_path.is_file():
        return []
    records: list[dict] = []
    for raw_line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get('mid') and obj.get('token'):
                records.append(obj)
        except json.JSONDecodeError:
            continue
    return records


def rebuild_owner_exports(
    log_path: Path = ISSUED_LOG_PATH,
    csv_path: Path | None = None,
    html_path: Path | None = None,
) -> tuple[Path, Path]:
    """Refresh gitignored `issued-licenses.csv` and `issued-licenses-dashboard.html` beside `log_path`."""
    base_dir = log_path.parent
    base_dir.mkdir(parents=True, exist_ok=True)
    target_csv = csv_path or (base_dir / 'issued-licenses.csv')
    target_html = html_path or (base_dir / 'issued-licenses-dashboard.html')
    records = list(reversed(load_issued_records(log_path)))  # Newest first
    today = _dt.datetime.now(_dt.timezone.utc).date()

    # 1. Write Excel-ready CSV (UTF-8 BOM + formula-trigger guard)
    def _safe_csv(val: object) -> str:
        s = str(val or '')
        stripped = s.lstrip()
        if stripped and stripped[0] in ('=', '+', '-', '@', '\uff1d', '\uff0b', '\uff0d', '\uff20', '\u2212'):
            return "'" + s
        return s

    with open(target_csv, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow([
            'Logged UTC', 'Machine ID', 'Customer / Shop', 'Key Type',
            'Issued Date', 'Expires Date', 'Status', 'License ID', 'Key (CDLIC1 / CDADD1)',
        ])
        for rec in records:
            exp_str = str(rec.get('exp') or '')
            try:
                days_left = (_dt.date.fromisoformat(exp_str) - today).days
                status_txt = f'ACTIVE ({days_left}d left)' if days_left >= 0 else f'EXPIRED ({abs(days_left)}d ago)'
            except ValueError:
                status_txt = 'UNKNOWN'
            writer.writerow([
                _safe_csv(rec.get('logged_utc', '')[:19].replace('T', ' ')),
                _safe_csv(rec.get('mid', '')),
                _safe_csv(rec.get('cust', '')),
                _safe_csv(_entry_kind_label(rec)),
                _safe_csv(rec.get('iat', '')),
                _safe_csv(exp_str),
                _safe_csv(status_txt),
                _safe_csv(rec.get('lid', '')),
                _safe_csv(rec.get('token', '')),
            ])

    # 2. Write local searchable HTML Owner Dashboard (with one-click Copy Key buttons)
    rows_html: list[str] = []
    for idx, rec in enumerate(records, 1):
        exp_str = str(rec.get('exp') or '')
        try:
            days_left = (_dt.date.fromisoformat(exp_str) - today).days
            is_active = days_left >= 0
            badge_cls = 'badge-exp' if not is_active else ('badge-warn' if days_left <= 30 else 'badge-ok')
            badge_txt = f'ACTIVE • {days_left}d left' if is_active else f'EXPIRED • {abs(days_left)}d ago'
        except ValueError:
            badge_cls = 'badge-warn'
            badge_txt = 'UNKNOWN'
        tok = str(rec.get('token') or '')
        mid = str(rec.get('mid') or '')
        cust = str(rec.get('cust') or '')
        ktype = _entry_kind_label(rec)
        iat = str(rec.get('iat') or '')
        lid = str(rec.get('lid') or '')
        rows_html.append(
            f'<tr class="lic-row">'
            f'<td>{idx}</td>'
            f'<td class="mono mid">{html.escape(mid)}</td>'
            f'<td><strong>{html.escape(cust)}</strong><div class="sub">ID: {html.escape(lid)}</div></td>'
            f'<td>{html.escape(ktype)}</td>'
            f'<td class="mono">{html.escape(iat)} &rarr; {html.escape(exp_str)}</td>'
            f'<td><span class="badge {badge_cls}">{html.escape(badge_txt)}</span></td>'
            f'<td class="key-cell">'
            f'<input type="text" readonly value="{html.escape(tok)}" id="k{idx}" onclick="this.select()">'
            f'<button type="button" onclick="copyKey(\'k{idx}\', this)">Copy Key</button>'
            f'</td>'
            f'</tr>'
        )

    body_rows = '\n'.join(rows_html) if rows_html else (
        '<tr><td colspan="7" style="text-align:center;padding:28px;color:#9eb0cc;">'
        'No Machine IDs or keys have been generated yet.</td></tr>'
    )
    dashboard_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Café Desk — Private Owner License &amp; Machine ID Registry</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; background: #0e1422; color: #f2f5fc; margin: 0; padding: 28px; }}
  .wrap {{ max-width: 1360px; margin: 0 auto; }}
  .banner {{ background: #36251c; border: 1px solid #8a5b36; color: #ffdba2; padding: 12px 18px; border-radius: 10px; font-size: 13px; margin-bottom: 18px; }}
  h1 {{ margin: 0 0 6px; font-size: 24px; }}
  .lead {{ color: #a3b1cc; font-size: 14px; margin-bottom: 18px; }}
  .toolbar {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }}
  .search {{ flex: 1; min-width: 260px; padding: 10px 14px; border-radius: 9px; border: 1px solid #30405e; background: #151e30; color: #fff; font-size: 14px; }}
  .stat {{ background: #182236; border: 1px solid #2d3c59; padding: 9px 14px; border-radius: 9px; font-size: 13px; color: #b9ff70; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; background: #162033; border-radius: 12px; overflow: hidden; border: 1px solid #2b3a56; }}
  th, td {{ padding: 11px 12px; text-align: left; border-bottom: 1px solid #24324b; font-size: 13px; vertical-align: middle; }}
  th {{ background: #1c2942; color: #b9ff70; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }}
  .mono {{ font-family: Consolas, monospace; }}
  .mid {{ color: #b9ff70; font-weight: 700; font-size: 13.5px; white-space: nowrap; }}
  .sub {{ color: #8fa0bf; font-size: 11px; margin-top: 2px; }}
  .badge {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap; }}
  .badge-ok {{ background: rgba(185,255,112,0.15); color: #b9ff70; border: 1px solid rgba(185,255,112,0.35); }}
  .badge-warn {{ background: rgba(255,213,132,0.15); color: #ffd584; border: 1px solid rgba(255,213,132,0.35); }}
  .badge-exp {{ background: rgba(255,156,143,0.15); color: #ff9c8f; border: 1px solid rgba(255,156,143,0.35); }}
  .key-cell {{ display: flex; gap: 6px; align-items: center; min-width: 290px; }}
  .key-cell input {{ flex: 1; background: #0d1320; color: #dbe6fb; border: 1px solid #2c3b57; border-radius: 6px; padding: 6px 8px; font-family: Consolas, monospace; font-size: 11.5px; }}
  .key-cell button {{ background: #b9ff70; color: #142018; border: none; border-radius: 6px; padding: 6px 10px; font-weight: 700; font-size: 12px; cursor: pointer; white-space: nowrap; }}
  .key-cell button:hover {{ background: #a5ef5a; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="banner"><strong>PRIVATE OWNER RECORD (GITIGNORED — NEVER UPLOADED TO GITHUB):</strong> Stored locally inside <code>owner-keys/</code> on your private computer. Never copy this folder onto customer USBs.</div>
  <h1>Café Desk — Activated Machines &amp; Issued Keys</h1>
  <div class="lead">Every <code>CDLIC1</code> computer key and <code>CDADD1</code> paid add-on key you have generated, matched to its Machine ID.</div>
  <div class="toolbar">
    <input class="search" type="search" placeholder="Search by Machine ID (CD-...), Shop/Customer name, or key type..." oninput="filterRows(this.value)">
    <div class="stat">Total Issued Keys: {len(records)}</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Machine ID</th>
        <th>Customer / Shop</th>
        <th>Key Type</th>
        <th>Valid Period</th>
        <th>Status</th>
        <th>CDLIC1 / CDADD1 Key</th>
      </tr>
    </thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
</div>
<script>
function filterRows(q) {{
  var term = (q || '').toLowerCase().trim();
  var rows = document.querySelectorAll('.lic-row');
  for (var i = 0; i < rows.length; i++) {{
    rows[i].style.display = (!term || rows[i].textContent.toLowerCase().indexOf(term) !== -1) ? '' : 'none';
  }}
}}
function copyKey(id, btn) {{
  var el = document.getElementById(id);
  el.select();
  navigator.clipboard.writeText(el.value);
  var old = btn.textContent;
  btn.textContent = 'Copied!';
  setTimeout(function() {{ btn.textContent = old; }}, 1400);
}}
</script>
</body>
</html>
"""
    target_html.write_text(dashboard_doc, encoding='utf-8')
    return target_csv, target_html


def _record_issued_license(
    payload: dict,
    token: str,
    *,
    log_path: Path = ISSUED_LOG_PATH,
    saved_file: Path | None = None,
) -> tuple[Path, Path]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(
        payload,
        token=token,
        saved_file=str(saved_file) if saved_file else '',
        logged_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    try:
        os.chmod(log_path, 0o600)
    except OSError:
        pass
    return rebuild_owner_exports(log_path=log_path)


def print_issued_registry(query: str = '', log_path: Path = ISSUED_LOG_PATH) -> int:
    """Print a formatted table of all issued Machine IDs and their full CDLIC1/CDADD1 keys."""
    records = list(reversed(load_issued_records(log_path)))
    term = (query or '').strip().lower()
    if term:
        records = [
            r for r in records
            if term in str(r.get('mid', '')).lower()
            or term in str(r.get('cust', '')).lower()
            or term in _entry_kind_label(r).lower()
            or term in str(r.get('lid', '')).lower()
        ]
    csv_path, html_path = rebuild_owner_exports(log_path=log_path)
    print('=' * 78)
    print(f'  CAFÉ DESK — ISSUED MACHINES & KEYS ({len(records)} matching record(s))')
    print(f'  Local JSONL : {log_path}')
    print(f'  Excel CSV   : {csv_path}')
    print(f'  HTML View   : {html_path}  (gitignored — NEVER uploaded to GitHub)')
    print('=' * 78)
    if not records:
        print('  No matching issued keys found yet.')
        print('=' * 78)
        return 0
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for idx, rec in enumerate(records, 1):
        exp_str = str(rec.get('exp') or '')
        try:
            dleft = (_dt.date.fromisoformat(exp_str) - today).days
            st = f'ACTIVE ({dleft}d left)' if dleft >= 0 else f'EXPIRED ({abs(dleft)}d ago)'
        except ValueError:
            st = 'UNKNOWN'
        print(f'[{idx}] Machine ID : {rec.get("mid")}   |   Status: {st}')
        print(f'    Customer   : {rec.get("cust")}   |   Type: {_entry_kind_label(rec)}')
        print(f'    Validity   : {rec.get("iat")} -> {exp_str}   |   License ID: {rec.get("lid")}')
        print(f'    Key        : {rec.get("token")}')
        print('-' * 78)
    return 0


def _interactive_cli() -> int:
    print('=' * 70)
    print('  CAFÉ DESK — OWNER LICENSE & PAID ADD-ON GENERATOR (PRIVATE TOOL)')
    print('=' * 70)
    if not DEFAULT_PRIV_PATH.is_file():
        ans = input('No owner signing keypair found yet. Generate one now and update license_guard.py? [Y/n]: ').strip().lower()
        if ans in ('', 'y', 'yes'):
            _, pub_b64 = generate_owner_keypair()
            apply_public_key_to_guard(pub_b64)
            print(f'\n[OK] Private key saved to: {DEFAULT_PRIV_PATH} (KEEP SECRET — NEVER SHARE!)')
            print(f'[OK] Public key written to license_guard.py: {pub_b64}')
            print('     Rebuild dist\\CafeDesk before distributing to customers.\n')
        else:
            return 1

    print('\nChoose an action:')
    print('  [1] Issue Standard 1-Year Computer License (CDLIC1...)')
    print('  [2] Issue PAID ADD-ON ONLY: PDF Password Remover Key (CDADD1...)')
    print('  [3] Issue Combo 1-Year Computer License + PDF Password Remover included')
    print('  [4] View / Search all issued Machine IDs & CDLIC1/CDADD1 keys (Terminal)')
    print('  [5] Open Private Owner License Dashboard in Browser (HTML + CSV)')
    kind_choice = input('Select option [1-5, default 1]: ').strip() or '1'

    if kind_choice == '4':
        filt = input('Optional search filter (Machine ID or Shop name, or press Enter for all): ').strip()
        return print_issued_registry(query=filt)

    if kind_choice == '5':
        csv_file, html_file = rebuild_owner_exports()
        print(f'\n[OK] Private CSV saved  : {csv_file}')
        print(f'[OK] Private HTML saved : {html_file}')
        webbrowser.open(html_file.as_uri())
        return 0

    mid = input('Enter customer Machine ID (e.g. CD-8F3A-91BC-204E): ').strip()
    cust = input('Enter Café / Customer name [Licensed Café]: ').strip() or 'Licensed Café'
    days_raw = input('Validity in days [365 for 1 year]: ').strip()
    days = int(days_raw) if days_raw else 365

    out_dir = DEFAULT_KEY_DIR / 'issued-files'
    out_dir.mkdir(parents=True, exist_ok=True)

    if kind_choice == '2':
        token, payload = sign_addon_token(mid, feature=license_guard.FEATURE_PDF_UNLOCK, customer=cust, days=days)
        label_title = 'PAID ADD-ON KEY — PDF PASSWORD REMOVER (CDADD1)'
        ext_suffix = '-pdf-unlock.cafeaddon'
    elif kind_choice == '3':
        token, payload = sign_license_token(
            mid, customer=cust, days=days, features=[license_guard.FEATURE_PDF_UNLOCK]
        )
        label_title = '1-YEAR COMPUTER LICENSE + PDF PASSWORD REMOVER INCLUDED'
        ext_suffix = '-combo.cafelicense'
    else:
        token, payload = sign_license_token(mid, customer=cust, days=days)
        label_title = '1-YEAR ACTIVATION KEY (STANDARD)'
        ext_suffix = '.cafelicense'

    safe_mid = payload['mid'].replace('-', '')
    lic_file = out_dir / f'{safe_mid}-{payload["exp"]}{ext_suffix}'
    lic_file.write_text(token + '\n', encoding='utf-8')
    csv_file, html_file = _record_issued_license(payload, token, saved_file=lic_file)

    print('\n' + '-' * 70)
    print(f'Key Type      : {label_title}')
    print(f'Customer      : {payload["cust"]}')
    print(f'Machine ID    : {payload["mid"]}')
    print(f'Valid From    : {payload["iat"]}  ->  Expires: {payload["exp"]} ({days} days)')
    print(f'Saved KeyFile : {lic_file}')
    print(f'Registry CSV  : {csv_file}  (gitignored)')
    print(f'Dashboard HTML: {html_file}  (gitignored)')
    print('-' * 70)
    print(f'\n{label_title} (send this text or file to customer):\n')
    print(token)
    print('\n' + '=' * 70)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Owner-only Ed25519 license & paid add-on generator for Café Desk.')
    parser.add_argument('--init-keys', action='store_true', help='Generate a new Ed25519 private/public keypair in owner-keys/.')
    parser.add_argument('--force', action='store_true', help='Allow overwriting an existing keypair with --init-keys.')
    parser.add_argument('--apply-public-key', action='store_true', help='Write your owner public key into license_guard.py.')
    parser.add_argument('--list', '-l', action='store_true', help='List all issued Machine IDs and CDLIC1/CDADD1 keys saved in owner-keys/.')
    parser.add_argument('--search', '-s', help='Search issued Machine IDs or customer names in owner-keys/.')
    parser.add_argument('--open-dashboard', action='store_true', help='Build and open the private HTML dashboard of all issued Machine IDs & keys.')
    parser.add_argument('--machine-id', '-m', help='Target computer Machine ID (CD-XXXX-XXXX-XXXX).')
    parser.add_argument('--customer', '-c', default='Licensed Café', help='Customer / internet café name.')
    parser.add_argument('--days', '-d', type=int, default=365, help='License validity in days (default: 365 = 1 year).')
    parser.add_argument('--addon', choices=sorted(license_guard.KNOWN_PAID_FEATURES), help='Generate a standalone Paid Add-on Key (e.g. pdf_unlock) for this Machine ID.')
    parser.add_argument('--include-pdf-unlock', action='store_true', help='Include paid pdf_unlock approval inside a base 1-year CDLIC1 computer license.')
    parser.add_argument('--out', '-o', help='Optional path to write the license/add-on file.')
    args = parser.parse_args(argv)

    if args.init_keys:
        _, pub_b64 = generate_owner_keypair(overwrite=args.force)
        print(f'Saved secret owner private key : {DEFAULT_PRIV_PATH}')
        print(f'Owner Ed25519 public key       : {pub_b64}')
        if args.apply_public_key:
            apply_public_key_to_guard(pub_b64)
            print('Updated DEFAULT_OWNER_PUBLIC_KEY_B64 in license_guard.py.')
        return 0

    if args.apply_public_key and not args.machine_id:
        if not DEFAULT_PUB_PATH.is_file():
            parser.error(f'{DEFAULT_PUB_PATH} not found. Run with --init-keys first.')
        pub_b64 = DEFAULT_PUB_PATH.read_text(encoding='utf-8').strip()
        apply_public_key_to_guard(pub_b64)
        print(f'Updated DEFAULT_OWNER_PUBLIC_KEY_B64 in license_guard.py ({pub_b64}).')
        return 0

    if args.open_dashboard:
        csv_file, html_file = rebuild_owner_exports()
        print(f'Updated CSV  : {csv_file}')
        print(f'Opening HTML : {html_file}')
        webbrowser.open(html_file.as_uri())
        return 0

    if args.list or args.search is not None:
        return print_issued_registry(query=args.search or '')

    if not args.machine_id:
        return _interactive_cli()

    out_dir = DEFAULT_KEY_DIR / 'issued-files'
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.addon:
        token, payload = sign_addon_token(args.machine_id, feature=args.addon, customer=args.customer, days=args.days)
        ext_suffix = f'-{args.addon}.cafeaddon'
    else:
        feats = [license_guard.FEATURE_PDF_UNLOCK] if args.include_pdf_unlock else None
        token, payload = sign_license_token(args.machine_id, customer=args.customer, days=args.days, features=feats)
        ext_suffix = '-combo.cafelicense' if feats else '.cafelicense'

    safe_mid = payload['mid'].replace('-', '')
    auto_file = Path(args.out) if args.out else (out_dir / f'{safe_mid}-{payload["exp"]}{ext_suffix}')
    auto_file.parent.mkdir(parents=True, exist_ok=True)
    auto_file.write_text(token + '\n', encoding='utf-8')
    _record_issued_license(payload, token, saved_file=auto_file)
    print(token)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
