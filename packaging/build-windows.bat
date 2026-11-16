@echo off
setlocal
cd /d "%~dp0.."
where py >nul 2>&1 || (echo Python was not found. Install Python 3.11 or newer from python.org and tick "Add Python to PATH". & exit /b 1)
py -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 || (echo Python 3.11 or newer is required ^(Pillow^>=13.0.0^). Please install Python 3.11+ from python.org. & exit /b 1)
if not exist .venv\Scripts\python.exe py -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --exclude-module admin_keygen --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all reportlab --collect-all cryptography --hidden-import win32com.client --hidden-import pythoncom --add-data "assets;assets" --add-data "cv-design-gallery.html;." --add-data "cafe-desk-manual.html;." --add-data "README.md;." app.py
if errorlevel 1 exit /b 1

> "dist\CafeDesk\HOW-TO-RUN-FROM-USB.txt" (
  echo ============================================================
  echo   Cafe Desk v0.7.0 -- Commercial 1-Year Licensed Edition
  echo ============================================================
  echo.
  echo 1. COPY TO TARGET PC ^& ACTIVATE ^(1-YEAR PER-COMPUTER LICENSE^):
  echo    Keep this ENTIRE "CafeDesk" folder together ^(including "_internal"^).
  echo    Copy the whole "CafeDesk" folder onto a Windows 10/11 PC and run
  echo    "CafeDesk.exe". No Python installation is needed!
  echo.
  echo    On first launch on a new computer, Cafe Desk displays that PC's
  echo    unique hardware Machine ID ^(CD-XXXX-XXXX-XXXX^) and asks for a
  echo    1-year Activation Key. Copying this folder to another computer will
  echo    NOT transfer activation -- every computer requires its own key from
  echo    the owner.
  echo.
  echo 2. PORTABLE USB SETTINGS MODE ^(OPTIONAL^):
  echo    Create an empty folder named "CafeDeskData" beside CafeDesk.exe
  echo    ^(or run "Enable-USB-Portable-Data.bat"^) if you want prices/settings
  echo    stored inside this folder. Note: computer activation ^(license.json^)
  echo    is always bound per-PC.
  echo.
  echo 3. OPTIONAL -- ENGLISH OCR ^(TESSERACT^):
  echo    Install Tesseract OCR ^(keep English ticked^) using the direct link
  echo    in Help ^& privacy, OR place a "Tesseract-OCR" folder inside this
  echo    "CafeDesk\Tesseract-OCR" folder.
)

> "dist\CafeDesk\Enable-USB-Portable-Data.bat" (
  echo @echo off
  echo mkdir "%%~dp0CafeDeskData" 2^>nul
  echo echo Portable USB data folder enabled: "%%~dp0CafeDeskData"
  echo pause
)

echo.
echo ======================================================================
echo Licensed USB folder ready: dist\CafeDesk
echo   IMPORTANT FOR COMMERCIAL SALES:
echo   - Make sure you ran: python admin_keygen.py --init-keys --apply-public-key
echo     BEFORE building so only YOUR secret private key can activate PCs!
echo   - NEVER copy "admin_keygen.py" or "owner-keys\" onto customer USBs!
echo ======================================================================
pause
