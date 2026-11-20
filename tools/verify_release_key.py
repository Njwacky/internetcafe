"""Verify which owner signing key is embedded in a PUBLISHED Café Desk build.

Why this exists
---------------
`check_release_key.py` runs at BUILD time and fails closed when the demo key would
ship. That still leaves one question a build log cannot answer from the outside:
*is the key inside the file I am handing to a customer actually mine?*

This tool answers that by scanning the artifact itself. Use it on the exact file
you download from the GitHub Releases page, so you are checking what customers get
rather than what you think the pipeline did.

Usage
-----
    # Check the portable ZIP (recommended: it contains the app's bytecode):
    python tools/verify_release_key.py CafeDesk-Windows-USB-Portable.zip

    # Or the single-file installer:
    python tools/verify_release_key.py CafeDesk-Setup.exe

    # If owner-keys/ is present the expected key is read automatically; override with:
    python tools/verify_release_key.py build.zip --expect <base64url-public-key>

Exit codes: 0 = correct key found, 1 = demo key found / key missing, 2 = bad input.

How it works
------------
`license_guard.DEFAULT_OWNER_PUBLIC_KEY_B64` is a 43-character base64url literal in
a pure-Python module, so PyInstaller stores it verbatim in the bundled bytecode
(the PYZ archive or the EXE). We search the raw bytes of every member, which needs
no disassembly — we only ask "is this exact string present?".

We deliberately do NOT try to prove a negative in general: we report the demo key,
the expected key, and any *other* 32-byte base64url candidate found next to the
name, so an unexpected third key is visible rather than silently ignored.
"""
from __future__ import annotations

import argparse
import base64
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_KEY_FALLBACK = 'tuAEglg-NweqdIgh4Jbojl4yQW4iWMOd3fLEbLMV7lI'
LOCAL_PUB_PATH = REPO_ROOT / 'owner-keys' / 'cafedesk_owner.public.txt'

# Documentation / data members of the portable ZIP (README.md, the HTML manuals,
# HOW-TO-RUN-FROM-USB.txt, fonts, icons, ...). The bundled README quotes the demo key
# as an example for the owner's find/replace step, which is harmless — the key only
# matters as the active verification constant in Python bytecode. Reporting it here
# would make every correctly-signed release look like a demo-key build, so these
# members are skipped. Mirrors `_DATA_FILE_EXTENSIONS` in .github/check_release_key.py
# (the build-time gate); keep the two in sync.
_DATA_MEMBER_SUFFIXES = frozenset({
    '.md', '.txt', '.html', '.htm', '.rst', '.json', '.csv', '.tsv', '.xml',
    '.pdf', '.css', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico',
    '.webp', '.tif', '.tiff', '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.mp3', '.wav', '.ogg', '.mp4', '.zip', '.gz', '.tar', '.7z', '.log',
    '.yaml', '.yml', '.toml', '.ini', '.cfg', '.map', '.lock',
})

# A 32-byte Ed25519 key in unpadded base64url is 43 characters. PyInstaller stores the
# literal with no boundary around it (it sits next to the module's identifiers), so we
# search for runs and slide a 43-char window instead of requiring word boundaries.
_RUN_RE = re.compile(rb'[A-Za-z0-9_-]{43,}')
# Scan in chunks so a 40 MB installer never has to be resident in memory twice.
_CHUNK = 8 * 1024 * 1024
# Longest overlap a chunk boundary can cut in half (a 43-char key => 42 bytes).
_CARRY = 64


def _demo_key() -> str:
    """The demo key as committed on the checked-out branch (falls back to a constant)."""
    try:
        src = (REPO_ROOT / 'license_guard.py').read_text(encoding='utf-8')
        found = re.search(r"^DEFAULT_OWNER_PUBLIC_KEY_B64\s*=\s*'([^']*)'", src, re.MULTILINE)
        if found:
            return found.group(1).strip()
    except OSError:
        pass
    return DEMO_KEY_FALLBACK


def _expected_key(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    try:
        return LOCAL_PUB_PATH.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def _is_keyish(token: bytes) -> bool:
    """True when 43 base64url chars decode to exactly 32 bytes (an Ed25519 key)."""
    try:
        return len(base64.urlsafe_b64decode(token + b'=' * ((4 - len(token) % 4) % 4))) == 32
    except Exception:
        return False


def _scan_bytes(blob: bytes, found: set[str], known: set[str]) -> None:
    """Collect key-looking strings from a byte chunk.

    Exact matching for `known` keys first: those are the ones we actually care about
    (the demo key and the owner's key), and a plain substring search cannot produce a
    false negative because of surrounding bytes. The sliding window afterwards only
    exists to surface an *unexpected* third key rather than ignore it.
    """
    for key in known:
        if key and key.encode('ascii') in blob:
            found.add(key)
    for match in _RUN_RE.finditer(blob):
        run = match.group(0)
        if len(run) == 43:
            if _is_keyish(run):
                found.add(run.decode('ascii'))
            continue
        # Longer run: slide a 43-char window across it.
        for i in range(0, len(run) - 42):
            window = run[i:i + 43]
            if _is_keyish(window):
                found.add(window.decode('ascii'))
                break


def scan_file(path: Path, found: set[str], known: set[str]) -> int:
    """Scan a raw file in chunks. Returns bytes scanned."""
    total = 0
    with path.open('rb') as fh:
        carry = b''
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                _scan_bytes(carry, found, known)
                break
            _scan_bytes(carry + chunk, found, known)
            # Keep a tail so a key split across the chunk boundary is not missed.
            carry = chunk[-_CARRY:]
            total += len(chunk)
    return total


def scan_zip(path: Path, found: set[str], known: set[str]) -> list[str]:
    """Scan every code-bearing member of a ZIP; returns members that contained keys.

    Documentation/data members (see `_DATA_MEMBER_SUFFIXES`) are skipped: the bundled
    README quotes the demo key as an example, which must not read as "demo key embedded".
    """
    hits: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if Path(info.filename).suffix.lower() in _DATA_MEMBER_SUFFIXES:
                continue
            try:
                with zf.open(info) as member:
                    before = len(found)
                    carry = b''
                    while True:
                        chunk = member.read(_CHUNK)
                        if not chunk:
                            _scan_bytes(carry, found, known)
                            break
                        _scan_bytes(carry + chunk, found, known)
                        carry = chunk[-_CARRY:]
                    if len(found) > before:
                        hits.append(info.filename)
            except (zipfile.BadZipFile, OSError, RuntimeError):
                continue
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Check which owner Ed25519 public key is embedded in a Café Desk release artifact.')
    parser.add_argument('artifact', help='CafeDesk-Windows-USB-Portable.zip or CafeDesk-Setup.exe')
    parser.add_argument('--expect', help='Expected public key (default: owner-keys/cafedesk_owner.public.txt)')
    args = parser.parse_args(argv)

    target = Path(args.artifact)
    if not target.is_file():
        print(f'ERROR: file not found: {target}', file=sys.stderr)
        return 2

    expected = _expected_key(args.expect)
    demo = _demo_key()
    found: set[str] = set()
    known = {k for k in (expected, demo) if k}

    print(f'Scanning {target.name} ({target.stat().st_size:,} bytes)…')
    if zipfile.is_zipfile(target):
        members = scan_zip(target, found, known)
        if members:
            print(f'  keys found inside: {", ".join(members[:4])}'
                  f'{" …" if len(members) > 4 else ""}')
    else:
        scan_file(target, found, known)

    if not found:
        print('\nFAIL: no 32-byte Ed25519 key string was found in this artifact.')
        print('This is unexpected for a PyInstaller build — check you passed the real')
        print('installer/ZIP and not a checksum or shortcut file.')
        return 1

    # The sliding window can report a 43-char slice that is merely a known key shifted by
    # a few bytes (the literal sits flush against surrounding bytecode, so a window can
    # start mid-identifier). Any candidate sharing a 20-character run with a known key is
    # that kind of noise, not a third keypair — drop it so the report stays trustworthy.
    def _shares_run(candidate: str, known_keys: set[str]) -> bool:
        windows = {candidate[i:i + 20] for i in range(0, max(1, len(candidate) - 19))}
        return any(w and w in known for known in known_keys for w in windows)

    for noise in [k for k in list(found) if k not in (demo, expected) and _shares_run(k, {demo, expected})]:
        found.discard(noise)

    has_demo = demo in found
    has_expected = bool(expected) and expected in found
    others = sorted(found - {demo, expected} - {''})

    print('\nKeys embedded in this artifact:')
    for key in sorted(found):
        tag = []
        if expected and key == expected:
            tag.append('YOUR KEY')
        if key == demo:
            tag.append('*** DEMO KEY — DO NOT DISTRIBUTE ***')
        if key in others:
            tag.append('unexpected')
        print(f'  {key}   {" ".join(tag)}')

    print()
    if has_demo:
        print('FAIL: the DEMO signing key is embedded in this file.')
        print('  Do not give this build to a customer. Fix CAFE_DESK_PUBLIC_KEY and rebuild.')
        return 1
    if not expected:
        print('WARNING: no expected key available (owner-keys/ missing and --expect not given),')
        print('  so this scan could only prove the demo key is absent. Re-run with --expect.')
        return 0
    if has_expected:
        print('PASS: this artifact embeds YOUR owner public key.')
        return 0
    print('FAIL: your expected key was NOT found in this artifact.')
    print(f'  expected: {expected}')
    print('  A build made from a different keypair cannot be activated with your licenses.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
