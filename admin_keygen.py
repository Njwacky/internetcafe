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

import cloud_sync
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
    pub_b64 = str(pub_b64 or '').strip()
    try:
        raw = _b64u_decode(pub_b64)
    except Exception as exc:
        raise RuntimeError(f'Owner public key is not valid URL-safe base64: {exc}') from exc
    if len(raw) != 32:
        raise RuntimeError(f'Owner public key must decode to 32 bytes (Ed25519), got {len(raw)} bytes.')
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


def _make_shop_id(customer: str, explicit_shop_id: str | None = None) -> str:
    if explicit_shop_id:
        cleaned = re.sub(r'[^A-Za-z0-9_-]', '', str(explicit_shop_id)).upper()[:36]
        if cleaned:
            return cleaned
    slug = re.sub(r'[^A-Z0-9]+', '-', str(customer or 'CAFE').upper()).strip('-')[:18] or 'CAFE'
    return f'SHOP-{slug}'


def parse_batch_machines(raw_items: list[str] | str) -> list[tuple[str, str]]:
    """Parse multiple Machine IDs (optionally `CD-XXXX-XXXX-XXXX=Counter 1` or comma/newline separated).

    Returns deduplicated `[(normalized_mid, seat_label), ...]` preserving order.
    """
    text = '\n'.join(raw_items) if isinstance(raw_items, list) else str(raw_items or '')
    chunks = [c.strip() for c in re.split(r'[\n,;]+', text) if c.strip()]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for idx, chunk in enumerate(chunks, 1):
        label_part = ''
        mid_part = chunk
        for sep in ('=', '|', ':'):
            if sep in chunk:
                left, right = chunk.split(sep, 1)
                mid_part = left.strip()
                label_part = ' '.join(right.split())[:40]
                break
        norm_mid = license_guard.normalize_machine_id(mid_part)
        if norm_mid in seen:
            continue
        seen.add(norm_mid)
        out.append((norm_mid, label_part or f'PC {len(out) + 1}'))
    if not out:
        raise ValueError('Provide at least one Machine ID (e.g. CD-1111-2222-3333, CD-4444-5555-6666).')
    return out


def sign_license_token(
    machine_id: str,
    customer: str = 'Licensed Café',
    days: int = 365,
    *,
    features: list[str] | None = None,
    shop_id: str | None = None,
    seat_no: int | None = None,
    seat_max: int | None = None,
    seat_label: str | None = None,
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
    if shop_id or seat_no or seat_max:
        payload['shop_id'] = _make_shop_id(clean_cust, shop_id)
    if seat_no is not None and int(seat_no) > 0:
        payload['seat_no'] = int(seat_no)
    if seat_max is not None and int(seat_max) > 0:
        payload['seat_max'] = int(seat_max)
    if seat_label:
        payload['seat_label'] = ' '.join(str(seat_label).split())[:40]
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
    shop_id: str | None = None,
    seat_no: int | None = None,
    seat_max: int | None = None,
    seat_label: str | None = None,
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

    payload: dict = {
        'v': 1,
        'type': 'addon',
        'feat': [feature],
        'mid': norm_mid,
        'cust': clean_cust,
        'iat': iat.isoformat(),
        'exp': exp.isoformat(),
        'lid': lid,
    }
    if shop_id or seat_no or seat_max:
        payload['shop_id'] = _make_shop_id(clean_cust, shop_id)
    if seat_no is not None and int(seat_no) > 0:
        payload['seat_no'] = int(seat_no)
    if seat_max is not None and int(seat_max) > 0:
        payload['seat_max'] = int(seat_max)
    if seat_label:
        payload['seat_label'] = ' '.join(str(seat_label).split())[:40]
    payload_bytes = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    payload_b64 = _b64u_encode(payload_bytes)
    signing_input = f'{license_guard.ADDON_TOKEN_PREFIX}.{payload_b64}'.encode('ascii')

    priv = load_private_key(priv_path=priv_path, private_key_b64=private_key_b64)
    sig_b64 = _b64u_encode(priv.sign(signing_input))
    token = f'{license_guard.ADDON_TOKEN_PREFIX}.{payload_b64}.{sig_b64}'
    return token, payload


def sign_multi_pc_shop_bundle(
    machines: list[str] | str,
    customer: str = 'Licensed Café',
    days: int = 365,
    *,
    shop_id: str | None = None,
    pdf_unlock_mode: str = 'none',
    issued_date: _dt.date | None = None,
    private_key_b64: str | None = None,
    priv_path: Path = DEFAULT_PRIV_PATH,
) -> dict:
    """Issue hardware-locked keys for a shop with 2+ computers (`Seat 1 of N`, `Seat 2 of N`, ...).

    `pdf_unlock_mode`:
      - `'none'`: standard 1-year `CDLIC1` keys only on all seats
      - `'seat1'`: `Seat 1` (Main Cashier PC) gets PDF Password Remover included; other seats standard
      - `'all'`: ALL seats in the shop get PDF Password Remover included
    """
    if pdf_unlock_mode not in ('none', 'seat1', 'all'):
        raise ValueError("pdf_unlock_mode must be 'none', 'seat1', or 'all'.")
    parsed = parse_batch_machines(machines)
    total_seats = len(parsed)
    clean_cust = ' '.join(str(customer or 'Licensed Café').split())[:80] or 'Licensed Café'
    resolved_shop = _make_shop_id(clean_cust, shop_id)

    seats_out: list[dict] = []
    for idx, (mid, s_label) in enumerate(parsed, 1):
        inc_pdf = (pdf_unlock_mode == 'all') or (pdf_unlock_mode == 'seat1' and idx == 1)
        feats = [license_guard.FEATURE_PDF_UNLOCK] if inc_pdf else None
        tok, pay = sign_license_token(
            mid,
            customer=clean_cust,
            days=days,
            features=feats,
            shop_id=resolved_shop,
            seat_no=idx,
            seat_max=total_seats,
            seat_label=s_label,
            issued_date=issued_date,
            private_key_b64=private_key_b64,
            priv_path=priv_path,
        )
        seats_out.append({
            'seat_no': idx,
            'seat_max': total_seats,
            'seat_label': s_label,
            'machine_id': mid,
            'pdf_unlock': inc_pdf,
            'token': tok,
            'payload': pay,
        })

    return {
        'shop_id': resolved_shop,
        'customer': clean_cust,
        'seat_max': total_seats,
        'expires_at': seats_out[0]['payload']['exp'],
        'seats': seats_out,
    }


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
            'Logged UTC', 'Machine ID', 'Customer / Shop', 'Shop ID', 'Seat',
            'Key Type', 'Issued Date', 'Expires Date', 'Status', 'License ID', 'Key (CDLIC1 / CDADD1)',
        ])
        for rec in records:
            exp_str = str(rec.get('exp') or '')
            try:
                days_left = (_dt.date.fromisoformat(exp_str) - today).days
                status_txt = f'ACTIVE ({days_left}d left)' if days_left >= 0 else f'EXPIRED ({abs(days_left)}d ago)'
            except ValueError:
                status_txt = 'UNKNOWN'
            seat_txt = ''
            if rec.get('seat_no') and rec.get('seat_max'):
                seat_txt = f"Seat {rec['seat_no']}/{rec['seat_max']}"
                if rec.get('seat_label'):
                    seat_txt += f" ({rec['seat_label']})"
            elif rec.get('seat_label'):
                seat_txt = str(rec['seat_label'])
            writer.writerow([
                _safe_csv(rec.get('logged_utc', '')[:19].replace('T', ' ')),
                _safe_csv(rec.get('mid', '')),
                _safe_csv(rec.get('cust', '')),
                _safe_csv(rec.get('shop_id', '')),
                _safe_csv(seat_txt),
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
        shop_id = str(rec.get('shop_id') or '')
        seat_meta = ''
        if rec.get('seat_no') and rec.get('seat_max'):
            seat_meta = f"Seat {rec['seat_no']}/{rec['seat_max']}"
            if rec.get('seat_label'):
                seat_meta += f" • {rec['seat_label']}"
        sub_parts = [f'ID: {html.escape(str(rec.get("lid") or ""))}']
        if shop_id:
            sub_parts.append(f'Shop: {html.escape(shop_id)}')
        if seat_meta:
            sub_parts.append(html.escape(seat_meta))
        ktype = _entry_kind_label(rec)
        iat = str(rec.get('iat') or '')
        rows_html.append(
            f'<tr class="lic-row">'
            f'<td>{idx}</td>'
            f'<td class="mono mid">{html.escape(mid)}</td>'
            f'<td><strong>{html.escape(cust)}</strong><div class="sub">{" &nbsp;|&nbsp; ".join(sub_parts)}</div></td>'
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
            or term in str(r.get('shop_id', '')).lower()
            or term in str(r.get('seat_label', '')).lower()
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
        seat_info = ''
        if rec.get('shop_id') or rec.get('seat_no'):
            s_part = f"Seat {rec.get('seat_no')}/{rec.get('seat_max')}" if rec.get('seat_no') and rec.get('seat_max') else ''
            l_part = f" ({rec.get('seat_label')})" if rec.get('seat_label') else ''
            seat_info = f'   |   Shop: {rec.get("shop_id") or "-"} {s_part}{l_part}'.rstrip()
        print(f'[{idx}] Machine ID : {rec.get("mid")}   |   Status: {st}{seat_info}')
        print(f'    Customer   : {rec.get("cust")}   |   Type: {_entry_kind_label(rec)}')
        print(f'    Validity   : {rec.get("iat")} -> {exp_str}   |   License ID: {rec.get("lid")}')
        print(f'    Key        : {rec.get("token")}')
        print('-' * 78)
    return 0


def _save_and_record_shop_bundle(bundle: dict, *, out_root: Path | None = None, log_path: Path = ISSUED_LOG_PATH) -> tuple[Path, Path, Path]:
    base_files = (out_root or (DEFAULT_KEY_DIR / 'issued-files')) / bundle['shop_id']
    base_files.mkdir(parents=True, exist_ok=True)
    summary_lines = [
        f"CAFÉ DESK — MULTI-PC SHOP LICENSE PACK ({bundle['seat_max']} COMPUTERS)",
        f"Shop Name : {bundle['customer']}",
        f"Shop ID   : {bundle['shop_id']}",
        f"Expires   : {bundle['expires_at']}",
        '=' * 74,
        '',
    ]
    csv_file = log_path.parent / 'issued-licenses.csv'
    html_file = log_path.parent / 'issued-licenses-dashboard.html'
    for seat in bundle['seats']:
        mid_compact = seat['machine_id'].replace('-', '')
        ext = '-combo.cafelicense' if seat['pdf_unlock'] else '.cafelicense'
        seat_file = base_files / f"Seat{seat['seat_no']}-{mid_compact}-{bundle['expires_at']}{ext}"
        seat_file.write_text(seat['token'] + '\n', encoding='utf-8')
        csv_file, html_file = _record_issued_license(seat['payload'], seat['token'], saved_file=seat_file, log_path=log_path)
        addon_tag = ' + PDF Password Remover' if seat['pdf_unlock'] else ''
        summary_lines.extend([
            f"[Seat {seat['seat_no']} of {seat['seat_max']}] {seat['seat_label']}  —  Machine ID: {seat['machine_id']}{addon_tag}",
            seat['token'],
            '',
        ])
    pack_txt = base_files / f"{bundle['shop_id']}-ALL-SEATS.txt"
    pack_txt.write_text('\n'.join(summary_lines), encoding='utf-8')
    return pack_txt, csv_file, html_file


def _interactive_cli() -> int:
    print('=' * 70)
    print('  CAFÉ DESK — OWNER LICENSE, MULTI-PC & CLOUD GENERATOR (PRIVATE TOOL)')
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
    print('  [1] Issue Standard 1-Year Computer License (1 PC — CDLIC1...)')
    print('  [2] Issue PAID ADD-ON ONLY: PDF Password Remover Key (CDADD1...)')
    print('  [3] Issue Combo 1-Year Computer License + PDF Password Remover (1 PC)')
    print('  [4] MULTI-PC SHOP PACK: Issue keys for 2+ computers in one shop at once')
    print('  [5] View / Search all issued Machine IDs & CDLIC1/CDADD1 keys (Terminal)')
    print('  [6] Open Private Owner License Dashboard in Browser (HTML + CSV)')
    print('  [7] Cloud / Remote Updates: Setup Supabase, Sync Keys, or Publish App Update')
    kind_choice = input('Select option [1-7, default 1]: ').strip() or '1'

    if kind_choice == '5':
        filt = input('Optional search filter (Machine ID, Shop ID, or Shop name; Enter for all): ').strip()
        return print_issued_registry(query=filt)

    if kind_choice == '6':
        csv_file, html_file = rebuild_owner_exports()
        print(f'\n[OK] Private CSV saved  : {csv_file}')
        print(f'[OK] Private HTML saved : {html_file}')
        webbrowser.open(html_file.as_uri())
        return 0

    if kind_choice == '7':
        print('\nSupabase Owner Cloud Actions (saved privately in owner-keys/supabase-owner.json):')
        print('  [a] Configure Supabase URL + secret service_role key')
        print('  [b] Push/Backup all issued Machine IDs & keys to Supabase')
        print('  [c] Publish a new Remote App Update (version + HTTPS installer URL + SHA-256)')
        print('  [d] Check pending online activation requests from customer PCs')
        sub = input('Choose [a/b/c/d]: ').strip().lower()
        if sub == 'a':
            s_url = input('Supabase Project URL (https://xxxx.supabase.co): ').strip()
            s_srv = input('Supabase secret service_role key (KEEP PRIVATE): ').strip()
            s_anon = input('Supabase public anon key (optional): ').strip()
            cfg_file = cloud_sync.save_owner_supabase_config(s_url, s_srv, s_anon)
            print(f'\n[OK] Saved private owner cloud config to {cfg_file} (gitignored).')
            return 0
        if sub == 'b':
            recs = load_issued_records()
            count = cloud_sync.owner_push_issued_records_to_supabase(recs)
            print(f'\n[OK] Synced {count} issued Machine ID key(s) to Supabase `issued_machine_keys`.')
            return 0
        if sub == 'c':
            ver = input('New App Version (e.g. 0.8.1): ').strip()
            dl_url = input('HTTPS Installer URL (e.g. GitHub Release or Supabase Storage URL): ').strip()
            sha_or_path = input('Local .exe/.zip path (to compute SHA-256) OR 64-char SHA-256 hash: ').strip()
            notes = input('Short release notes for shop staff: ').strip()
            row = cloud_sync.owner_publish_app_release(ver, dl_url, sha_or_path, release_notes=notes)
            print(f"\n[OK] Published Café Desk v{row['version']} (SHA-256: {row['sha256']}) to Supabase!")
            return 0
        if sub == 'd':
            reqs = cloud_sync.owner_list_pending_requests()
            print(f'\nPending Online Activation Requests ({len(reqs)}):')
            for r in reqs:
                print(
                    f"  • {r.get('machine_id')} | Shop: {r.get('shop_name') or r.get('shop_id')} "
                    f"({r.get('station_name')}) | Wants: {r.get('requested_kind')} | Phone: {r.get('contact_phone')}"
                )
            return 0
        return 0

    if kind_choice == '4':
        cust = input('Enter Multi-PC Shop / Café name [Sunrise Cyber Café]: ').strip() or 'Sunrise Cyber Café'
        shop_id = input('Optional Shop ID (Enter to auto-generate, e.g. SHOP-SUNRISE): ').strip()
        print('Paste all Machine IDs for this shop separated by commas (optional `=Desk Name`, e.g. `CD-1111-2222-3333=Counter, CD-4444-5555-6666=Desk 2`):')
        raw_mids = input('Machine IDs: ').strip()
        pdf_opt = input('Include Paid PDF Password Remover? [none / seat1 / all, default none]: ').strip().lower() or 'none'
        days_raw = input('Validity in days [365 for 1 year]: ').strip()
        days = int(days_raw) if days_raw else 365
        bundle = sign_multi_pc_shop_bundle(
            raw_mids, customer=cust, days=days, shop_id=shop_id or None, pdf_unlock_mode=pdf_opt
        )
        pack_txt, csv_file, html_file = _save_and_record_shop_bundle(bundle)
        print('\n' + '=' * 74)
        print(f"MULTI-PC SHOP BUNDLE CREATED: {bundle['customer']} ({bundle['shop_id']}) — {bundle['seat_max']} PCs")
        print(f'All-Seats Handout File : {pack_txt}')
        print(f'Owner Dashboard HTML   : {html_file}  (gitignored)')
        print('=' * 74)
        for s in bundle['seats']:
            print(f"\n[Seat {s['seat_no']}/{s['seat_max']} — {s['seat_label']}] Machine ID: {s['machine_id']}")
            print(s['token'])
        print('\n' + '=' * 74)
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
    parser = argparse.ArgumentParser(description='Owner-only Ed25519 license, multi-PC shop & cloud updater tool for Café Desk.')
    parser.add_argument('--init-keys', action='store_true', help='Generate a new Ed25519 private/public keypair in owner-keys/.')
    parser.add_argument('--force', action='store_true', help='Allow overwriting an existing keypair with --init-keys.')
    parser.add_argument('--apply-public-key', action='store_true', help='Write your owner public key into license_guard.py.')
    parser.add_argument('--list', '-l', action='store_true', help='List all issued Machine IDs and CDLIC1/CDADD1 keys saved in owner-keys/.')
    parser.add_argument('--search', '-s', help='Search issued Machine IDs, Shop IDs, or customer names in owner-keys/.')
    parser.add_argument('--open-dashboard', action='store_true', help='Build and open the private HTML dashboard of all issued Machine IDs & keys.')
    parser.add_argument('--machine-id', '-m', help='Single computer Machine ID (CD-XXXX-XXXX-XXXX).')
    parser.add_argument(
        '--machine-ids',
        help='Multi-PC batch mode: comma-separated Machine IDs for one shop (optional =SeatName, e.g. "CD-1111-2222-3333=Counter,CD-4444-5555-6666=Desk 2").',
    )
    parser.add_argument('--shop-id', help='Optional Shop ID tag (e.g. SHOP-SUNRISE) for multi-PC shops.')
    parser.add_argument('--seat-label', help='Optional computer label (e.g. "Counter PC" or "Desk 2").')
    parser.add_argument('--pdf-unlock-seats', choices=['none', 'seat1', 'all'], default='none', help='For --machine-ids multi-PC packs: unlock PDF Password Remover on none, seat1 (Cashier PC only), or all seats.')
    parser.add_argument('--customer', '-c', default='Licensed Café', help='Customer / internet café name.')
    parser.add_argument('--days', '-d', type=int, default=365, help='License validity in days (default: 365 = 1 year).')
    parser.add_argument('--addon', choices=sorted(license_guard.KNOWN_PAID_FEATURES), help='Generate a standalone Paid Add-on Key (e.g. pdf_unlock) for --machine-id.')
    parser.add_argument('--include-pdf-unlock', action='store_true', help='Include paid pdf_unlock approval inside a base 1-year CDLIC1 computer license.')
    parser.add_argument('--out', '-o', help='Optional path to write the license/add-on file.')
    # Supabase owner cloud flags (strictly gitignored config in owner-keys/supabase-owner.json)
    parser.add_argument('--setup-supabase', nargs=2, metavar=('SUPABASE_URL', 'SERVICE_ROLE_KEY'), help='Save your Supabase URL and secret service_role key into gitignored owner-keys/supabase-owner.json.')
    parser.add_argument('--sync-supabase', action='store_true', help='Push all issued Machine IDs & keys from owner-keys/ to Supabase so customer PCs can click "Check Online Approval".')
    parser.add_argument('--publish-release', nargs=3, metavar=('VERSION', 'HTTPS_URL', 'FILE_OR_SHA256'), help='Publish a new Café Desk release to Supabase for remote over-the-internet shop updates.')
    parser.add_argument('--release-notes', default='', help='Release notes text when using --publish-release.')
    parser.add_argument('--revoke', metavar='LICENSE_ID', help='Remotely revoke a specific License ID on Supabase.')
    parser.add_argument('--pending-requests', action='store_true', help='List pending online activation requests submitted from customer PCs.')
    args = parser.parse_args(argv)

    if args.setup_supabase:
        cfg_file = cloud_sync.save_owner_supabase_config(args.setup_supabase[0], args.setup_supabase[1])
        print(f'Saved private Supabase owner config: {cfg_file} (gitignored)')
        return 0

    if args.sync_supabase:
        count = cloud_sync.owner_push_issued_records_to_supabase(load_issued_records())
        print(f'Synced {count} issued Machine ID key(s) to Supabase.')
        return 0

    if args.publish_release:
        ver, dl_url, file_or_sha = args.publish_release
        row = cloud_sync.owner_publish_app_release(ver, dl_url, file_or_sha, release_notes=args.release_notes)
        print(f"Published Café Desk v{row['version']} -> {row['download_url']} (SHA-256: {row['sha256']})")
        return 0

    if args.revoke:
        cloud_sync.owner_revoke_license_id(args.revoke)
        print(f'Remotely revoked License ID {args.revoke} on Supabase.')
        return 0

    if args.pending_requests:
        reqs = cloud_sync.owner_list_pending_requests()
        if not reqs:
            print('No pending online activation requests.')
            return 0
        for r in reqs:
            print(f"{r.get('machine_id')} | {r.get('shop_name') or r.get('shop_id')} | {r.get('station_name')} | {r.get('requested_kind')} | {r.get('contact_phone')}")
        return 0

    if args.init_keys:
        _, pub_b64 = generate_owner_keypair(overwrite=args.force)
        print(f'Saved secret owner private key : {DEFAULT_PRIV_PATH}')
        print(f'Owner Ed25519 public key       : {pub_b64}')
        if args.apply_public_key:
            apply_public_key_to_guard(pub_b64)
            print('Updated DEFAULT_OWNER_PUBLIC_KEY_B64 in license_guard.py.')
        return 0

    if args.apply_public_key and not args.machine_id and not args.machine_ids:
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

    if args.machine_ids:
        pdf_mode = 'all' if (args.include_pdf_unlock and args.pdf_unlock_seats == 'none') else args.pdf_unlock_seats
        bundle = sign_multi_pc_shop_bundle(
            args.machine_ids,
            customer=args.customer,
            days=args.days,
            shop_id=args.shop_id,
            pdf_unlock_mode=pdf_mode,
        )
        pack_txt, _, _ = _save_and_record_shop_bundle(bundle)
        print(f"# Shop: {bundle['customer']} ({bundle['shop_id']}) — {bundle['seat_max']} seats — saved to {pack_txt}")
        for s in bundle['seats']:
            print(f"Seat {s['seat_no']}/{s['seat_max']} ({s['seat_label']} | {s['machine_id']}): {s['token']}")
        return 0

    if not args.machine_id:
        return _interactive_cli()

    out_dir = DEFAULT_KEY_DIR / 'issued-files'
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.addon:
        token, payload = sign_addon_token(
            args.machine_id,
            feature=args.addon,
            customer=args.customer,
            days=args.days,
            shop_id=args.shop_id,
            seat_label=args.seat_label,
        )
        ext_suffix = f'-{args.addon}.cafeaddon'
    else:
        feats = [license_guard.FEATURE_PDF_UNLOCK] if args.include_pdf_unlock else None
        token, payload = sign_license_token(
            args.machine_id,
            customer=args.customer,
            days=args.days,
            features=feats,
            shop_id=args.shop_id,
            seat_label=args.seat_label,
        )
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
