#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3.11 or newer first.'; exit 1; }
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || { echo 'Python 3.11 or newer is required (Pillow>=13.0.0).'; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-build.txt

# Refuse to build a distributable copy while the DEMO signing key is embedded
# (anyone could mint their own licence with it). Run once on your own machine:
#   python admin_keygen.py --init-keys --apply-public-key
.venv/bin/python .github/check_release_key.py || {
  echo '======================================================================' >&2
  echo ' BUILD STOPPED: the DEMO owner signing key is still embedded.' >&2
  echo ' Run this ONCE, then build again:' >&2
  echo '     python admin_keygen.py --init-keys --apply-public-key' >&2
  echo ' Throw-away test copy: CAFE_DESK_ALLOW_DEMO_KEY=1 sh packaging/build-unix.sh' >&2
  echo '======================================================================' >&2
  exit 1
}

.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --exclude-module admin_keygen --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all reportlab --collect-all cryptography --add-data "assets:assets" --add-data "cv-design-gallery.html:." --add-data "cafe-desk-manual.html:." --add-data "README.md:." app.py

# Final gate on the artifact itself: this folder is what gets zipped and handed out.
.venv/bin/python .github/check_release_key.py --dist-dir dist/CafeDesk || {
  echo '======================================================================' >&2
  echo ' BUILD STOPPED: the built app folder still contains the DEMO key.' >&2
  echo ' Do not distribute or package this folder.' >&2
  echo '======================================================================' >&2
  exit 1
}

# One source of truth for the customer-facing read-me, with the version stamped in.
cp packaging/HOW-TO-RUN-FROM-USB.txt dist/CafeDesk/HOW-TO-RUN-FROM-USB.txt
printf '\n   This copy: Cafe Desk v%s\n' "$(.venv/bin/python -c 'from version import __version__; print(__version__)')" >> dist/CafeDesk/HOW-TO-RUN-FROM-USB.txt

case "$(uname -s)" in
  Darwin) echo 'macOS app ready: dist/CafeDesk — each Mac requires its own 1-year activation key.' ;;
  *) echo 'Portable folder ready: dist/CafeDesk — each computer requires its own 1-year activation key.' ;;
esac
