@echo off
setlocal
cd /d "%~dp0.."
where py >nul 2>&1 || (echo Python was not found. Install Python 3.10 or newer. & exit /b 1)
if not exist .venv\Scripts\python.exe py -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name CafeDesk --collect-all customtkinter --collect-all pypdfium2 --collect-all pypdfium2_raw --hidden-import win32com.client --hidden-import pythoncom --add-data "assets;assets" --add-data "cv-design-gallery.html;." app.py
if errorlevel 1 exit /b 1
echo.
echo Portable folder ready: dist\CafeDesk
echo Copy the ENTIRE CafeDesk folder to USB and run CafeDesk.exe from that folder.
echo Optional installer: install Inno Setup 6 and compile packaging\windows-installer.iss.
pause
