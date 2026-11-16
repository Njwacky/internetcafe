#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3.11 or newer first.'; exit 1; }
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || { echo 'Python 3.11 or newer is required (Pillow>=13.0.0).'; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all reportlab --add-data "assets:assets" --add-data "cv-design-gallery.html:." --add-data "README.md:." app.py
case "$(uname -s)" in
  Darwin) echo 'macOS app ready: dist/CafeDesk.app — copy the entire .app bundle to USB or target Macs.' ;;
  *) echo 'Portable folder ready: dist/CafeDesk — copy the entire folder to USB and run its CafeDesk executable.' ;;
esac
