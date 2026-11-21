"""Windows-only CI check: the real DPAPI path of ai_key_store works end to end.

Run by the Windows build job. Fails the build if saving/loading a key with genuine
Windows data protection does not round-trip, or if a wrong PIN is accepted.
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import ai_key_store as store  # noqa: E402


def main() -> int:
    if sys.platform != "win32":
        print("Not Windows - skipping DPAPI self-test.")
        return 0
    assert store.os_protection_available()
    raw = b"cafe-desk dpapi self-test \x00\xff"
    wrapped = store._os_protect(raw)
    assert wrapped != raw and store._os_unprotect(wrapped) == raw, "DPAPI round trip failed"
    try:
        store._os_unprotect(b"not a dpapi blob")
    except OSError:
        pass
    else:
        raise AssertionError("DPAPI accepted garbage")

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / store.FILE_NAME
        key = "gsk_SelfTestKey0123456789abcdefWXYZ"
        store.save_key(key, "48291573", provider="Groq (api.groq.com)", model="openai/gpt-oss-120b", path=path)
        info = store.saved_key_info(path)
        assert info["protection"] == "dpapi+pin", info
        assert key not in path.read_text(encoding="utf-8")
        assert store.load_key("48291573", path=path)["api_key"] == key
        try:
            store.load_key("00000000", path=path)
        except store.SavedKeyError:
            pass
        else:
            raise AssertionError("wrong PIN accepted")
    print("Windows DPAPI self-test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
