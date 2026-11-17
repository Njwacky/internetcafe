"""Local per-user preferences and PIN verifier. Not a security boundary against OS admins."""
import hashlib,hmac,json,os,shutil,sys,tempfile,time
from pathlib import Path
from decimal import Decimal,InvalidOperation
DEFAULT_NAME='Café Desk'; DEFAULT_ICON='office'; MAX_NAME=48
PIN_MAX_ATTEMPTS=5; PIN_LOCKOUT_SECONDS=60
ICONS={'office':'Office','document':'Document','printer':'Printer','scanner':'Scanner','briefcase':'Briefcase','globe':'Globe','star':'Star','diamond':'Diamond','store':'Shop','leaf':'Leaf','custom':'Custom logo'}

class CorruptedSettingsError(ValueError):
    """Raised when the settings file exists on disk but is unreadable or damaged."""

def _quarantine_corrupt_settings(target):
    """Preserve a copy of damaged settings.json once as settings.json.corrupt before failing closed."""
    p=Path(target)
    if not p.exists(): return None
    corrupt_copy=p.with_name(p.name+'.corrupt')
    try:
        if not corrupt_copy.exists():
            shutil.copy2(p,corrupt_copy)
    except OSError:
        pass
    return corrupt_copy

def restore_backup_settings(path=None):
    """Restore a verified settings.json.bak over a damaged settings.json when available."""
    target=Path(path or settings_path())
    backup=target.with_name(target.name+'.bak')
    if not backup.is_file():
        raise ValueError('No verified settings.json.bak backup was found.')
    data=_parse_settings_file(backup)
    _quarantine_corrupt_settings(target)
    fd,tmp=tempfile.mkstemp(prefix='.settings-restore-',suffix='.json',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2)
        try: os.chmod(tmp,0o600)
        except OSError: pass
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return data

def app_exe_dir():
    """Return the directory containing CafeDesk.exe when frozen, or the repo folder in source mode."""
    if getattr(sys,'frozen',False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def is_portable_data_mode():
    """Return True if a portable `CafeDeskData` folder exists beside the executable or CAFE_DESK_PORTABLE_DIR is set."""
    override=os.environ.get('CAFE_DESK_PORTABLE_DIR','').strip()
    if override:
        return True
    return (app_exe_dir()/'CafeDeskData').is_dir()

def app_data_dir():
    override=os.environ.get('CAFE_DESK_PORTABLE_DIR','').strip()
    if override:
        return Path(override)
    portable_dir=app_exe_dir()/'CafeDeskData'
    if portable_dir.is_dir():
        return portable_dir
    if sys.platform=='win32': root=Path(os.environ.get('APPDATA') or Path.home()/'AppData'/'Roaming')
    elif sys.platform=='darwin': root=Path(os.environ.get('CAFE_DESK_MAC_DIR') or (Path.home()/'Library'/'Application Support'))
    else:
        xdg=os.environ.get('XDG_CONFIG_HOME',''); root=Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home()/'.config'
    return root/'CafeDesk'
def settings_path(): return app_data_dir()/'settings.json'
def logo_path(): return settings_path().parent/'business-logo.png'
def is_internal_path(candidate, data_dir=None):
    """Return True if candidate resolves inside the Café Desk app-data or per-PC license folder."""
    roots=[Path(data_dir)] if data_dir is not None else [settings_path().parent]
    try:
        import license_guard
        if data_dir is None:
            roots.append(license_guard.host_license_dir())
    except Exception:
        pass
    try:
        cand_res=Path(candidate).resolve()
    except OSError:
        return True
    for base in roots:
        try:
            base_res=base.resolve()
        except OSError:
            return True
        if cand_res==base_res or cand_res.is_relative_to(base_res):
            return True
    return False
def clean_name(name):
    name=' '.join(str(name).split())
    if not name: raise ValueError('Enter a business name.')
    if len(name)>MAX_NAME: raise ValueError(f'Use {MAX_NAME} characters or fewer.')
    return name
def _default_station():
    try:
        import socket
        h=' '.join(socket.gethostname().split())[:40]
        if h: return h
    except Exception:
        pass
    return 'Counter PC 1'

def _default_dict():
    import cloud_sync
    return {
        'business_name':DEFAULT_NAME,
        'business_icon':DEFAULT_ICON,
        'currency':'R',
        'prices':[],
        'station_name':_default_station(),
        'shop_id':'',
        'city_label':'',
        'lan_sync_folder':'',
        'supabase_url':os.environ.get('CAFE_DESK_SUPABASE_URL',cloud_sync.PUBLIC_PROJECT_SUPABASE_URL).strip(),
        'supabase_anon_key':os.environ.get('CAFE_DESK_SUPABASE_ANON_KEY',cloud_sync.PUBLIC_PROJECT_SUPABASE_ANON_KEY).strip(),
    }
def _parse_settings_file(target):
    default=_default_dict()
    if not target.exists(): return default
    try:
        raw=target.read_text(encoding='utf-8')
        obj=json.loads(raw)
    except (OSError,ValueError,TypeError) as exc:
        _quarantine_corrupt_settings(target)
        raise CorruptedSettingsError('Settings file is damaged or unreadable. Staff access is locked until an administrator restores settings.json.bak or removes the damaged file.') from exc
    if not isinstance(obj,dict):
        _quarantine_corrupt_settings(target)
        raise CorruptedSettingsError('Settings file has an invalid structure. Staff access is locked.')
    has_salt='pin_salt' in obj and bool(obj.get('pin_salt'))
    has_hash='pin_hash' in obj and bool(obj.get('pin_hash'))
    if has_salt!=has_hash:
        _quarantine_corrupt_settings(target)
        raise CorruptedSettingsError('Staff PIN record in settings.json is incomplete or damaged. Staff access is locked.')
    if has_salt:
        try:
            salt_b=bytes.fromhex(str(obj['pin_salt']))
            hash_b=bytes.fromhex(str(obj['pin_hash']))
            if len(salt_b)!=16 or len(hash_b)!=32: raise ValueError()
        except (ValueError,TypeError) as exc:
            _quarantine_corrupt_settings(target)
            raise CorruptedSettingsError('Staff PIN verifier in settings.json is damaged. Staff access is locked.') from exc
    try: name=clean_name(obj.get('business_name',DEFAULT_NAME))
    except ValueError: name=DEFAULT_NAME
    icon=obj.get('business_icon',DEFAULT_ICON)
    default.update({
        k:v for k,v in obj.items()
        if k in (
            'pin_salt','pin_hash','currency','prices','failed_attempts','lockout_until_epoch',
            'station_name','shop_id','city_label','lan_sync_folder','supabase_url','supabase_anon_key',
        )
    })
    default.update(business_name=name,business_icon=icon if icon in ICONS else DEFAULT_ICON)
    if not isinstance(default['prices'],list): default['prices']=[]
    default['station_name']=' '.join(str(default.get('station_name') or '').split())[:40] or _default_station()
    default['shop_id']=''.join(c for c in str(default.get('shop_id') or '').upper() if c.isalnum() or c in ('-','_'))[:40]
    default['city_label']=' '.join(str(default.get('city_label') or '').split())[:60]
    default['lan_sync_folder']=str(default.get('lan_sync_folder') or '').strip()[:260]
    default['supabase_url']=str(default.get('supabase_url') or '').strip()[:200]
    default['supabase_anon_key']=str(default.get('supabase_anon_key') or '').strip()[:512]
    return default
def load_settings(path=None,strict=False):
    target=Path(path or settings_path())
    try:
        return _parse_settings_file(target)
    except CorruptedSettingsError:
        if strict: raise
        return _default_dict()
def load_name(path=None): return load_settings(path)['business_name']
def _harden(path):
    """Owner-only permissions: POSIX 0600 + Windows icacls (chmod alone is a no-op on Windows)."""
    try:
        os.chmod(path,0o600)
    except OSError:
        pass
    if sys.platform=='win32':
        try:
            import subprocess
            user=os.environ.get('USERNAME','').strip()
            if user:
                subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'{user}:F'],capture_output=True,timeout=10,check=False)
        except (OSError,subprocess.SubprocessError):
            pass

def _write(data,path=None):
    target=Path(path or settings_path()); target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup=target.with_name(target.name+'.bak')
    fd,tmp=tempfile.mkstemp(prefix='.settings-',suffix='.json',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
        _harden(tmp)
        os.replace(tmp,target)
        _harden(target)
        # Keep settings.json.bak synchronized with the latest verified good state
        try:
            _parse_settings_file(target)
            fd_b,tmp_b=tempfile.mkstemp(prefix='.settings-bak-',suffix='.json',dir=target.parent)
            try:
                with os.fdopen(fd_b,'wb') as fb: fb.write(target.read_bytes())
                _harden(tmp_b)
                os.replace(tmp_b,backup)
                _harden(backup)
            finally:
                if os.path.exists(tmp_b): os.unlink(tmp_b)
        except Exception:
            pass
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def save_settings(name,icon,path=None):
    name=clean_name(name)
    if icon not in ICONS: raise ValueError('Please select an available business icon.')
    data=load_settings(path,strict=True); data.update(business_name=name,business_icon=icon); _write(data,path); return name
def save_name(name,path=None): return save_settings(name,load_settings(path,strict=True)['business_icon'],path)
def set_pin(pin,path=None):
    if not isinstance(pin,str) or not pin.isascii() or not pin.isdigit() or not 6<=len(pin)<=12: raise ValueError('Use a PIN of 6–12 digits.')
    salt=os.urandom(16); digest=hashlib.pbkdf2_hmac('sha256',pin.encode(),salt,300000)
    data=load_settings(path,strict=True); data.update(pin_salt=salt.hex(),pin_hash=digest.hex(),failed_attempts=0,lockout_until_epoch=0); _write(data,path)
def pin_is_set(path=None):
    """Return True if a valid PIN is configured. Raises CorruptedSettingsError (fails closed) if settings.json is damaged."""
    d=load_settings(path,strict=True); return bool(d.get('pin_salt') and d.get('pin_hash'))
def lockout_remaining(path=None,now=None):
    current=time.time() if now is None else float(now)
    d=load_settings(path,strict=True)
    until=d.get('lockout_until_epoch',0)
    try: until_f=float(until)
    except (ValueError,TypeError): return 0.0
    # Clamp implausible far-future clock jumps to PIN_LOCKOUT_SECONDS
    if until_f>current+PIN_LOCKOUT_SECONDS*10:
        until_f=current+PIN_LOCKOUT_SECONDS
    return max(0.0,until_f-current)
def verify_pin(pin,path=None,record_failure=True,now=None):
    current=time.time() if now is None else float(now)
    d=load_settings(path,strict=True)
    if record_failure and lockout_remaining(path,now=current)>0:
        return False
    try:
        ok=isinstance(pin,str) and hmac.compare_digest(
            hashlib.pbkdf2_hmac('sha256',pin.encode('utf-8'),bytes.fromhex(d['pin_salt']),300000).hex(),
            d['pin_hash'],
        )
    except (KeyError,ValueError,TypeError):
        ok=False
    if record_failure and d.get('pin_salt') and d.get('pin_hash'):
        if ok:
            if d.get('failed_attempts',0) or d.get('lockout_until_epoch',0):
                d['failed_attempts']=0; d['lockout_until_epoch']=0
                try: _write(d,path)
                except OSError: pass
        else:
            fails=int(d.get('failed_attempts',0) or 0)+1
            if fails>=PIN_MAX_ATTEMPTS:
                d['failed_attempts']=0
                d['lockout_until_epoch']=current+PIN_LOCKOUT_SECONDS
            else:
                d['failed_attempts']=fails
            try: _write(d,path)
            except OSError: pass
    return ok
def parse_price_list(text):
    prices=[]; seen=set()
    for num,line in enumerate(text.splitlines(),1):
        if not line.strip(): continue
        parts=line.split('|')
        if len(parts)!=2: raise ValueError(f'Line {num}: use service name | price.')
        name=parts[0].strip()
        if not name or len(name)>80: raise ValueError(f'Line {num}: service name must be 1–80 characters.')
        if name.casefold() in seen: raise ValueError(f'Line {num}: service names must be unique.')
        try:
            value=Decimal(parts[1].strip())
            if not value.is_finite() or value<0 or value>10000000 or value.as_tuple().exponent < -2: raise ValueError()
        except (InvalidOperation,ValueError): raise ValueError(f'Line {num}: enter a valid non-negative price with at most two decimals.')
        seen.add(name.casefold()); prices.append({'name':name,'price':format(value,'.2f')})
    if len(prices)>200: raise ValueError('Use at most 200 services.')
    return prices
def save_prices(currency,text,path=None):
    currency=currency.strip()
    if not currency or len(currency)>8 or any(c in currency for c in '\n\r|'): raise ValueError('Enter a currency symbol or code, up to 8 characters.')
    prices=parse_price_list(text); data=load_settings(path,strict=True); data.update(currency=currency,prices=prices); _write(data,path); return prices

def save_multi_pc_settings(station_name,shop_id='',lan_sync_folder='',supabase_url='',supabase_anon_key='',city_label='',path=None):
    """Save Multi-PC station name, Shop ID, city/area label, optional LAN sync folder, and public Supabase URL + anon key."""
    clean_station=' '.join(str(station_name or '').split())[:40]
    if not clean_station:
        raise ValueError('Enter a Computer / Station Name (e.g. Counter PC 1 or Desk 2).')
    clean_shop=''.join(c for c in str(shop_id or '').strip().upper() if c.isalnum() or c in ('-','_'))[:40]
    clean_city=' '.join(str(city_label or '').split())[:60]
    clean_lan=str(lan_sync_folder or '').strip()[:260]
    clean_url=str(supabase_url or '').strip().rstrip('/')[:200]
    if clean_url and not (clean_url.startswith('https://') or clean_url.startswith('http://127.0.0.1') or clean_url.startswith('http://localhost')):
        raise ValueError('Supabase Project URL must start with https:// (e.g. https://xxxx.supabase.co).')
    clean_anon=str(supabase_anon_key or '').strip()[:512]
    if 'service_role' in clean_anon:
        raise ValueError('Never paste your secret service_role key into a customer PC! Use ONLY the public anon key here.')
    data=load_settings(path,strict=True)
    data.update(
        station_name=clean_station,
        shop_id=clean_shop,
        city_label=clean_city,
        lan_sync_folder=clean_lan,
        supabase_url=clean_url,
        supabase_anon_key=clean_anon,
    )
    _write(data,path)
    return data

def export_shop_profile_pack(dest_path,path=None):
    """Export business name, currency, Shop ID, and price list to a `.cafeshop.json` file for other PCs in the same shop."""
    dest=Path(dest_path)
    if is_internal_path(dest):
        raise ValueError('Choose an export location outside the internal Café Desk data folder (e.g. USB drive or Shared Folder).')
    d=load_settings(path,strict=True)
    pack={
        'format':'cafedesk-shop-pack-v1',
        'shop_id':d.get('shop_id',''),
        'business_name':d.get('business_name',DEFAULT_NAME),
        'currency':d.get('currency','R'),
        'prices':d.get('prices',[]),
        'supabase_url':d.get('supabase_url',''),
        'supabase_anon_key':d.get('supabase_anon_key',''),
    }
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(pack,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dest

def import_shop_profile_pack(src_path,path=None):
    """Import business name, currency, Shop ID, and price list onto another PC in the shop (preserving this PC's station_name & PIN)."""
    src=Path(src_path)
    raw=json.loads(src.read_text(encoding='utf-8'))
    if not isinstance(raw,dict) or raw.get('format')!='cafedesk-shop-pack-v1':
        raise ValueError('Selected file is not a valid Café Desk Shop Pack (.cafeshop.json).')
    bname=clean_name(raw.get('business_name',DEFAULT_NAME))
    curr=str(raw.get('currency') or 'R').strip()
    if not curr or len(curr)>8 or any(c in curr for c in '\n\r|'):
        raise ValueError('Pack contains an invalid currency symbol.')
    raw_prices=raw.get('prices')
    if not isinstance(raw_prices,list):
        raise ValueError('Pack contains an invalid price list.')
    lines='\n'.join(f"{p.get('name','')} | {p.get('price','0.00')}" for p in raw_prices if isinstance(p,dict) and p.get('name'))
    validated_prices=parse_price_list(lines) if lines.strip() else []
    data=load_settings(path,strict=True)
    data['business_name']=bname
    data['currency']=curr
    data['prices']=validated_prices
    if raw.get('shop_id'):
        data['shop_id']=''.join(c for c in str(raw['shop_id']).upper() if c.isalnum() or c in ('-','_'))[:40]
    if raw.get('supabase_url') and not data.get('supabase_url'):
        s_url=str(raw['supabase_url']).strip().rstrip('/')
        if s_url.startswith('https://'):
            data['supabase_url']=s_url[:200]
    if raw.get('supabase_anon_key') and not data.get('supabase_anon_key'):
        s_anon=str(raw['supabase_anon_key']).strip()
        if 'service_role' not in s_anon:
            data['supabase_anon_key']=s_anon[:512]
    _write(data,path)
    return data
