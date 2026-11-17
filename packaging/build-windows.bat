@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
where py >nul 2>&1 || (echo Python was not found. Install Python 3.11 or newer from python.org and tick "Add Python to PATH". & exit /b 1)
py -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 || (echo Python 3.11 or newer is required ^(Pillow^>=13.0.0^). Please install Python 3.11+ from python.org. & exit /b 1)
if not exist .venv\Scripts\python.exe py -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

rem --- Refuse to build a distributable copy while the DEMO signing key is embedded. ---
rem A build using the demo key lets anyone mint a valid 1-year licence. Run
rem   python admin_keygen.py --init-keys --apply-public-key
rem once on your private laptop first (only the PUBLIC half is committed).
.venv\Scripts\python.exe .github\check_release_key.py
if errorlevel 1 (
  echo.
  echo ======================================================================
  echo  BUILD STOPPED: the DEMO owner signing key is still embedded.
  echo  Anyone could generate their own licence with that key.
  echo  Run this ONCE on your private laptop, then build again:
  echo      python admin_keygen.py --init-keys --apply-public-key
  echo  To build a throw-away test copy anyway:
  echo      set CAFE_DESK_ALLOW_DEMO_KEY=1
  echo ======================================================================
  exit /b 1
)

.venv\Scripts\python.exe -c "from version import __version__; print(__version__)" > "%TEMP%\cafedesk_version.txt" || (echo Failed to read version.py & exit /b 1)
set /p CDVER=<"%TEMP%\cafedesk_version.txt"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --icon "assets\icons\cafedesk.ico" --exclude-module admin_keygen --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all reportlab --collect-all cryptography --hidden-import win32com.client --hidden-import pythoncom --add-data "assets;assets" --add-data "cv-design-gallery.html;." --add-data "cafe-desk-manual.html;." --add-data "README.md;." app.py
if errorlevel 1 exit /b 1

rem Final gate on the artifact itself: the folder is what becomes Setup.exe.
.venv\Scripts\python.exe .github\check_release_key.py --dist-dir dist\CafeDesk
if errorlevel 1 (
  echo ======================================================================
  echo  BUILD STOPPED: the built app folder still contains the DEMO key.
  echo  Do not turn this folder into an installer.
  echo ======================================================================
  exit /b 1
)

rem One source of truth for the customer-facing read-me, with the version stamped in.
copy /y "packaging\HOW-TO-RUN-FROM-USB.txt" "dist\CafeDesk\HOW-TO-RUN-FROM-USB.txt" >nul
if errorlevel 1 (echo Could not copy HOW-TO-RUN-FROM-USB.txt & exit /b 1)
>> "dist\CafeDesk\HOW-TO-RUN-FROM-USB.txt" echo.
>> "dist\CafeDesk\HOW-TO-RUN-FROM-USB.txt" echo   This copy: Cafe Desk v!CDVER!

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
echo   - Next: compile packaging\windows-installer.iss with Inno Setup 6
echo     to produce installer-output\CafeDesk-Setup.exe
echo ======================================================================
pause
