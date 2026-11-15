"""Explicit shortcut to Windows' own Bluetooth file transfer and settings."""
import os,sys,subprocess
from pathlib import Path

def _require_windows():
    if sys.platform!='win32':raise ValueError('This Bluetooth shortcut is available on Windows only. Use your operating system’s Bluetooth sharing tools instead.')

def open_transfer():
    """Open the Windows wizard; the user still selects Receive files there."""
    _require_windows()
    root=os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    if not root:raise ValueError('Windows system folder was not found. Open Bluetooth File Transfer from Windows Settings instead.')
    # 32-bit apps on 64-bit Windows may access the native system directory via Sysnative.
    folder='Sysnative' if os.environ.get('PROCESSOR_ARCHITEW6432') else 'System32'
    exe=Path(root)/folder/'fsquirt.exe'
    if not exe.is_file():raise ValueError('Windows Bluetooth File Transfer was not found. Open it from Windows Bluetooth settings instead.')
    try:subprocess.Popen([str(exe)],shell=False)
    except OSError as exc:raise ValueError('Could not open Windows Bluetooth File Transfer. Check your Bluetooth adapter and Windows settings.') from exc

def open_settings():
    _require_windows()
    try:os.startfile('ms-settings:bluetooth')
    except OSError as exc:raise ValueError('Could not open Windows Bluetooth settings. Open Settings and select Bluetooth & devices manually.') from exc
