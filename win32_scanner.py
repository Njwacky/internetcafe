"""Windows WIA scanner discovery and acquisition, one flatbed page at a time.

Each call creates and releases its COM objects on the calling worker thread. This
module is importable on other platforms but scanning is only available on Windows.
"""
import sys
from pathlib import Path
from contextlib import contextmanager
from PIL import Image

BMP_FORMAT='{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}'
MAX_SCAN_BYTES=200*1024*1024

@contextmanager
def _manager():
    if sys.platform!='win32':raise ValueError('Direct scanning is currently available on Windows only. Use your scanner application to import images on this computer.')
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:raise ValueError('Windows scanning support is missing. Install the application requirements or rebuild with pywin32.') from exc
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    manager=None
    try:
        manager=win32com.client.Dispatch('WIA.DeviceManager')
        yield manager
    finally:
        manager=None
        pythoncom.CoUninitialize()

def _devices(manager):
    result=[];infos=manager.DeviceInfos
    for i in range(1,infos.Count+1):
        info=infos.Item(i)
        if int(info.Type)!=1:continue  # WIA scanner, not a printer-only device
        try:name=str(info.Properties.Item('Name').Value or 'Scanner')
        except Exception:name='Windows scanner'
        result.append({'id':str(info.DeviceID),'name':name})
    return result

def list_scanners():
    try:
        with _manager() as manager:
            result=_devices(manager)
            manager=None
        return result
    except ValueError:raise
    except Exception as exc:raise ValueError('Could not find Windows scanners. Check the USB cable, scanner driver and Windows Image Acquisition service.') from exc

def scan_page(device_id,destination):
    """Acquire BMP from the chosen discovered scanner. Never overwrite a file."""
    target=Path(destination)
    if not isinstance(device_id,str) or not device_id:raise ValueError('Select a scanner first.')
    if target.exists():raise ValueError('Temporary scan destination already exists.')
    try:
        with _manager() as manager:
            # Release all WIA COM proxies on this same thread before COM is uninitialised.
            infos=candidate=info=device=item=image=None
            try:
                infos=manager.DeviceInfos
                for i in range(1,infos.Count+1):
                    candidate=infos.Item(i)
                    if int(candidate.Type)==1 and str(candidate.DeviceID)==device_id:info=candidate;break
                if info is None:raise ValueError('The selected scanner is no longer available. Reconnect it and refresh the list.')
                device=info.Connect()
                if int(device.Items.Count)<1:raise ValueError('This device did not provide a scanning source.')
                item=device.Items.Item(1)
                image=item.Transfer(BMP_FORMAT)  # actual WIA acquisition; driver controls its default settings
                if image is None:raise ValueError('No scanned page was returned.')
                image.SaveFile(str(target))
            finally:
                image=item=device=info=candidate=infos=None
                manager=None
        if not target.is_file() or target.stat().st_size>MAX_SCAN_BYTES:raise ValueError('The scan is missing or too large (limit 200 MB).')
        with Image.open(target) as test:
            if test.format!='BMP':raise ValueError('Scanner returned an unexpected image format.')
            test.verify()
        return str(target)
    except ValueError:
        target.unlink(missing_ok=True);raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise ValueError('The scan could not be completed. Check that the scanner is on, connected by USB, not busy, and has a WIA scanner driver. Some drivers do not support BMP transfer. Try scanning in Epson Scan 2 to check the device.') from exc
