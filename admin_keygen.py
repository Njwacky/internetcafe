#!/usr/bin/env python3
"""OWNER-ONLY License Key Generator for Café Desk (NEVER ship this file or `owner-keys/` to customers!).

Usage on YOUR private computer:
  1) First-time setup (generates your secret Ed25519 signing keypair in `owner-keys/`):
       python admin_keygen.py --init-keys --apply-public-key

  2) Issue a 1-year (365-day) license for a customer's Machine ID:
       python admin_keygen.py --machine-id CD-8F3A-91BC-204E --customer "Benoni Cyber Cafe" --days 365

  3) Or simply double-click / run with no arguments to open the interactive Owner Keygen prompt
     (or GUI window if Tkinter is available):
       python admin_keygen.py
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import os
import re
import sys
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import license_guard

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_KEY_DIR = REPO_DIR / 'owner-keys'
DEFAULT_PRIV_PATH = DEFAULT_KEY_DIR / 'cafedesk_owner.private.key'
DEFAULT_PUB_PATH = DEFAULT_KEY_DIR / 'cafedesk_owner.public.txt'
ISSUED_LOG_PATH = DEFAULT_KEY_DIR / 'issued-licenses.jsonl'


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

    payload = {
        'v': 1,
        'mid': norm_mid,
        'cust': clean_cust,
        'iat': iat.isoformat(),
        'exp': exp.isoformat(),
        'lid': lid,
    }
    payload_bytes = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    payload_b64 = _b64u_encode(payload_bytes)
    signing_input = f'{license_guard.TOKEN_PREFIX}.{payload_b64}'.encode('ascii')

    priv = load_private_key(priv_path=priv_path, private_key_b64=private_key_b64)
    sig_b64 = _b64u_encode(priv.sign(signing_input))
    token = f'{license_guard.TOKEN_PREFIX}.{payload_b64}.{sig_b64}'
    return token, payload


def _record_issued_license(payload: dict, token: str) -> None:
    try:
        ISSUED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = dict(payload, token=token, logged_utc=_dt.datetime.now(_dt.timezone.utc).isoformat())
        with open(ISSUED_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass


def _interactive_cli() -> int:
    print('=' * 66)
    print('  CAFÉ DESK — OWNER 1-YEAR LICENSE GENERATOR (PRIVATE TOOL)')
    print('=' * 66)
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

    mid = input('Enter customer Machine ID (e.g. CD-8F3A-91BC-204E): ').strip()
    cust = input('Enter Café / Customer name [Licensed Café]: ').strip() or 'Licensed Café'
    days_raw = input('Validity in days [365 for 1 year]: ').strip()
    days = int(days_raw) if days_raw else 365

    token, payload = sign_license_token(mid, customer=cust, days=days)
    _record_issued_license(payload, token)

    out_dir = DEFAULT_KEY_DIR / 'issued-files'
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_mid = payload['mid'].replace('-', '')
    lic_file = out_dir / f'{safe_mid}-{payload["exp"]}.cafelicense'
    lic_file.write_text(token + '\n', encoding='utf-8')

    print('\n' + '-' * 66)
    print(f'Customer   : {payload["cust"]}')
    print(f'Machine ID : {payload["mid"]}')
    print(f'Valid From : {payload["iat"]}  ->  Expires: {payload["exp"]} ({days} days)')
    print(f'Saved File : {lic_file}')
    print('-' * 66)
    print('\n1-YEAR ACTIVATION KEY (send this text or the .cafelicense file):\n')
    print(token)
    print('\n' + '=' * 66)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Owner-only Ed25519 1-year license generator for Café Desk.')
    parser.add_argument('--init-keys', action='store_true', help='Generate a new Ed25519 private/public keypair in owner-keys/.')
    parser.add_argument('--force', action='store_true', help='Allow overwriting an existing keypair with --init-keys.')
    parser.add_argument('--apply-public-key', action='store_true', help='Write your owner public key into license_guard.py.')
    parser.add_argument('--machine-id', '-m', help='Target computer Machine ID (CD-XXXX-XXXX-XXXX).')
    parser.add_argument('--customer', '-c', default='Licensed Café', help='Customer / internet café name.')
    parser.add_argument('--days', '-d', type=int, default=365, help='License validity in days (default: 365 = 1 year).')
    parser.add_argument('--out', '-o', help='Optional path to write a .cafelicense file.')
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

    if not args.machine_id:
        return _interactive_cli()

    token, payload = sign_license_token(args.machine_id, customer=args.customer, days=args.days)
    _record_issued_license(payload, token)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(token + '\n', encoding='utf-8')
    print(token)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
