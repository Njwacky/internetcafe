"""Optional "remember my AI key on this computer" storage.

Security design (shared internet-café PC):

* **Two locks.** The key is encrypted with AES-256-GCM using a key derived from the
  staff PIN (scrypt, deliberately slow and memory-hard). On Windows the result is then
  wrapped with DPAPI, which ties it to *this Windows user on this computer*. A copied
  file (USB, backup, e-mail) is useless elsewhere, and on this PC it is still useless
  without the staff PIN.
* **Only unlocked with the PIN.** The key is decrypted only after staff enter the
  correct PIN; locking staff access clears it from memory again.
* **Own file.** Stored in ``ai-key.json`` next to ``settings.json`` (owner-only file
  permissions) but never inside ``settings.json``, so it is not included in shop-profile
  exports or the settings backup.
* **Nothing secret in clear text, nothing editable.** The key *and* the provider, model
  and custom URL are all inside the encrypted, tamper-proof payload. A short hint (e.g.
  ``gsk_…a1b2``) and copies of the provider/model are readable for display only; if anyone
  edits them the file is rejected, so the key can never be silently redirected to another
  server.

This is protection against customers and casual snooping. Someone with administrator
access to the same Windows account who also knows (or brute-forces) the PIN could still
recover the key - use a free-tier key or a provider spending limit.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

FORMAT = "cafedesk-ai-key-v2"
FILE_NAME = "ai-key.json"
MAX_KEY_LENGTH = 512
MAX_FILE_BYTES = 64 * 1024  # a real record is ~1 KB; refuse oversized/hostile files

# scrypt cost: ~64 MiB and roughly 0.1-0.4 s per attempt, so guessing a 6-12 digit
# PIN offline is slow. Stored in the file so it can be raised later.
SCRYPT_N = 2**16
SCRYPT_R = 8
SCRYPT_P = 1
_SCRYPT_MAXMEM = 256 * 1024 * 1024

_DPAPI_ENTROPY = b"CafeDesk AI key v1"
_DPAPI_DESCRIPTION = "Cafe Desk AI key"


class SavedKeyError(Exception):
    """A saved key exists but cannot be used (wrong PIN, other computer, damaged file)."""


# --------------------------------------------------------------------------------------
# Paths and file helpers
# --------------------------------------------------------------------------------------


def key_path() -> Path:
    import settings_store

    return settings_store.app_data_dir() / FILE_NAME


def _target(path=None) -> Path:
    return Path(path) if path else key_path()


def _harden(path) -> None:
    try:
        import settings_store

        settings_store._harden(path)  # owner-only (POSIX 0600 / Windows icacls)
    except Exception:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _write_atomic(data: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".ai-key-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _harden(tmp)
        os.replace(tmp, target)
        _harden(target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read(target: Path) -> dict | None:
    if not target.is_file():
        return None
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file too large")
        obj = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SavedKeyError("The saved AI key file is damaged. Use “Forget saved key” and save it again.") from exc
    if not isinstance(obj, dict) or obj.get("format") != FORMAT:
        raise SavedKeyError("The saved AI key file is not recognised. Use “Forget saved key” and save it again.")
    return obj


# --------------------------------------------------------------------------------------
# Windows DPAPI (ctypes, no extra dependency). Other systems use the PIN layer only.
# --------------------------------------------------------------------------------------


def os_protection_available() -> bool:
    return sys.platform == "win32"


def _dpapi(data: bytes, protect: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_in, _keep1 = blob(data)
    entropy, _keep2 = blob(_DPAPI_ENTROPY)
    data_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(data_in), ctypes.c_wchar_p(_DPAPI_DESCRIPTION), ctypes.byref(entropy),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, ctypes.byref(entropy),
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
        )
    if not ok:
        raise OSError("Windows data protection failed")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(data_out.pbData, ctypes.c_void_p))


def _os_protect(data: bytes) -> bytes:
    return _dpapi(data, True)


def _os_unprotect(data: bytes) -> bytes:
    return _dpapi(data, False)


# --------------------------------------------------------------------------------------
# PIN layer (scrypt + AES-256-GCM)
# --------------------------------------------------------------------------------------


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM, dklen=32)


def _aad(protection: str) -> bytes:
    return f"{FORMAT}|{protection}".encode()


def key_hint(api_key: str) -> str:
    """Short, non-secret label such as 'gsk_…a1b2' so staff can recognise the key."""
    k = (api_key or "").strip()
    if len(k) <= 8:
        return "…"
    prefix = k[:4]
    for i, ch in enumerate(k[:8]):
        if ch in "_-" and i > 0:
            prefix = k[: i + 1]
            break
    return f"{prefix}…{k[-4:]}"


def _validate_key(api_key: str) -> str:
    k = (api_key or "").strip()
    if not k:
        raise ValueError("Paste an API key first.")
    if len(k) > MAX_KEY_LENGTH or any(c.isspace() for c in k) or not k.isprintable() or not k.isascii():
        raise ValueError("That does not look like a valid API key.")
    return k


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def saved_key_info(path=None) -> dict | None:
    """Non-secret details of the saved key, or None. Never needs the PIN."""
    try:
        obj = _read(_target(path))
    except SavedKeyError:
        return {"damaged": True}
    if not obj:
        return None
    return {
        "hint": str(obj.get("hint", "…"))[:24],
        "provider": str(obj.get("provider", ""))[:80],
        "model": str(obj.get("model", ""))[:120],
        "custom_url": str(obj.get("custom_url", ""))[:300],
        "saved_at": str(obj.get("saved_at", ""))[:32],
        "protection": str(obj.get("protection", "")),
        "damaged": False,
    }


def save_key(api_key: str, pin: str, *, provider: str = "", model: str = "", custom_url: str = "", path=None) -> dict:
    """Encrypt and store the key. `pin` must already be verified by the caller."""
    key = _validate_key(api_key)
    if not isinstance(pin, str) or not pin:
        raise ValueError("The staff PIN is required to save the key.")
    provider = (provider or "").strip()[:80]
    model = (model or "").strip()[:120]
    raw_custom = (custom_url or "").strip()
    if provider == "Custom OpenAI-compatible URL" or raw_custom:
        import ai_safety

        clean_custom = ai_safety.validate_custom_ai_url(raw_custom, key, expand_v1=False)
        if len(clean_custom) > 300:
            raise ValueError("Custom AI endpoint URL must be 300 characters or fewer.")
        custom_url = clean_custom
    else:
        custom_url = ""
    protection = "dpapi+pin" if os_protection_available() else "pin"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # Everything that decides *where* the key is sent lives inside the authenticated payload.
    payload = json.dumps({"api_key": key, "provider": provider, "model": model, "custom_url": custom_url}).encode("utf-8")
    salt, nonce = os.urandom(16), os.urandom(12)
    try:
        derived = _derive(pin, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    except (MemoryError, ValueError) as exc:
        raise ValueError("Not enough memory to encrypt the key safely on this computer.") from exc
    ciphertext = AESGCM(derived).encrypt(nonce, payload, _aad(protection))
    if protection == "dpapi+pin":
        ciphertext = _os_protect(ciphertext)
    record = {
        "format": FORMAT,
        "protection": protection,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt.hex()},
        "nonce": nonce.hex(),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "hint": key_hint(key),
        # Display copies only - verified against the encrypted payload on every load.
        "provider": provider,
        "model": model,
        "custom_url": custom_url,
        "saved_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    _write_atomic(record, _target(path))
    return saved_key_info(path) or {}


def load_key(pin: str, path=None) -> dict | None:
    """Decrypt the saved key with the staff PIN. Returns None if nothing is saved."""
    obj = _read(_target(path))
    if not obj:
        return None
    protection = obj.get("protection")
    try:
        kdf = obj["kdf"]
        salt = bytes.fromhex(kdf["salt"])
        nonce = bytes.fromhex(obj["nonce"])
        ciphertext = base64.b64decode(obj["ciphertext"], validate=True)
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        if kdf.get("name") != "scrypt" or not (2**14 <= n <= 2**20) or not (1 <= r <= 16) or not (1 <= p <= 4):
            raise ValueError("bad kdf")
    except (KeyError, ValueError, TypeError) as exc:
        raise SavedKeyError("The saved AI key file is damaged. Use “Forget saved key” and save it again.") from exc
    if protection == "dpapi+pin":
        if not os_protection_available():
            raise SavedKeyError("The AI key was saved on a Windows computer and cannot be opened here. Paste the key again.")
        try:
            ciphertext = _os_unprotect(ciphertext)
        except OSError as exc:
            raise SavedKeyError(
                "The saved AI key belongs to another computer or Windows account (for example a copied USB). Paste the key again."
            ) from exc
    elif protection != "pin":
        raise SavedKeyError("The saved AI key file is not recognised. Use “Forget saved key” and save it again.")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        derived = _derive(pin or "", salt, n, r, p)
    except (MemoryError, ValueError) as exc:
        raise SavedKeyError("Not enough memory to unlock the saved AI key on this computer.") from exc
    try:
        plain = AESGCM(derived).decrypt(nonce, ciphertext, _aad(protection))
    except InvalidTag as exc:
        raise SavedKeyError("The saved AI key could not be unlocked with this PIN (was the PIN changed?). Paste the key again.") from exc
    try:
        secret = json.loads(plain.decode("utf-8"))
        result = {k: str(secret[k]) for k in ("api_key", "provider", "model", "custom_url")}
    except (ValueError, KeyError, TypeError) as exc:
        raise SavedKeyError("The saved AI key file is damaged. Use “Forget saved key” and save it again.") from exc
    # The readable copies must match the encrypted originals - otherwise someone edited
    # the file (for example to point the key at their own server).
    for field in ("provider", "model", "custom_url"):
        if str(obj.get(field, "")) != result[field]:
            raise SavedKeyError("The saved AI key file was changed outside Café Desk and was blocked. Use “Forget saved key” and save it again.")
    if str(obj.get("hint", "")) != key_hint(result["api_key"]):
        raise SavedKeyError("The saved AI key file was changed outside Café Desk and was blocked. Use “Forget saved key” and save it again.")
    return result


def rewrap_for_new_pin(old_pin: str, new_pin: str, path=None) -> bool:
    """Re-encrypt the saved key after a PIN change. Returns False if nothing to do / not possible."""
    try:
        data = load_key(old_pin, path)
    except SavedKeyError:
        return False
    if not data:
        return False
    custom_url = data["custom_url"] if data["provider"] == "Custom OpenAI-compatible URL" else ""
    save_key(data["api_key"], new_pin, provider=data["provider"], model=data["model"], custom_url=custom_url, path=path)
    return True


def forget_key(path=None) -> bool:
    """Overwrite and delete the saved key file. Returns True if a file was removed."""
    target = _target(path)
    if not target.exists():
        return False
    try:
        size = target.stat().st_size
        with open(target, "r+b") as f:
            f.write(os.urandom(max(size, 1)))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    target.unlink(missing_ok=True)
    return True
