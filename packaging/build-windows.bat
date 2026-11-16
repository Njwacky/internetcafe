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
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all reportlab --hidden-import win32com.client --hidden-import pythoncom --add-data "assets;assets" --add-data "cv-design-gallery.html;." --add-data "README.md;." app.py
if errorlevel 1 exit /b 1

> "dist\CafeDesk\HOW-TO-RUN-FROM-USB.txt" (
  echo ============================================================
  echo   Cafe Desk v0.6.1 -- Portable USB Edition ^(Windows x64^)
  echo ============================================================
  echo.
  echo 1. COPY OR RUN FROM USB:
  echo    Keep this ENTIRE "CafeDesk" folder together ^(including "_internal"^).
  echo    Copy the whole "CafeDesk" folder onto a USB flash drive or onto
  echo    another Windows 10/11 PC ^(for example Desktop or C:\CafeDesk^),
  echo    then double-click "CafeDesk.exe". No Python installation is needed
  echo    on the other computers!
  echo.
  echo 2. PORTABLE USB DATA MODE ^(OPTIONAL^):
  echo    By default, business name, logo, prices, staff PIN and sales history
  echo    are saved separately on each Windows user account ^(%%APPDATA%%\CafeDesk^).
  echo    If you want settings, prices and sales to travel ON THE USB DRIVE instead,
  echo    create an empty folder named "CafeDeskData" inside this "CafeDesk" folder
  echo    ^(next to CafeDesk.exe^) or double-click "Enable-USB-Portable-Data.bat".
  echo.
  echo 3. OPTIONAL -- ENGLISH OCR ^(TESSERACT^):
  echo    If you want image-to-text OCR on a target PC, install Tesseract OCR
  echo    ^(keep English language data ticked^) using the direct link inside
  echo    Cafe Desk -^> Help ^& privacy, OR copy a Tesseract-OCR folder inside
  echo    this "CafeDesk\Tesseract-OCR" folder ^(with tesseract.exe^).
)

> "dist\CafeDesk\Enable-USB-Portable-Data.bat" (
  echo @echo off
  echo mkdir "%%~dp0CafeDeskData" 2^>nul
  echo echo Portable USB data folder enabled: "%%~dp0CafeDeskData"
  echo echo Settings, prices, logo and sales will now stay inside this CafeDesk folder.
  echo pause
)

echo.
echo ======================================================================
echo Portable USB folder ready: dist\CafeDesk
echo   1^) Copy the ENTIRE "dist\CafeDesk" folder to your USB drive.
echo   2^) On any Windows 10/11 PC, open the folder and run "CafeDesk.exe"
echo      ^(or copy the folder to that PC's Desktop first for faster startup^).
echo   3^) Optional single-file setup.exe: compile packaging\windows-installer.iss
echo      with Inno Setup 6 to create installer-output\CafeDesk-Setup.exe.
echo ======================================================================
pause
