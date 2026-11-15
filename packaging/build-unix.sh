#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3.10 or newer first.'; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --add-data "assets:assets" --add-data "cv-design-gallery.html:." app.py
case "$(uname -s)" in
  Darwin) echo 'macOS app: dist/CafeDesk.app. Copy the entire .app bundle to the target Mac.' ;;
  *) echo 'Portable folder: dist/CafeDesk. Copy the entire folder and run its CafeDesk executable.' ;;
esac
