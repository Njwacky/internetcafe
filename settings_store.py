"""Local per-user preferences and PIN verifier. Not a security boundary."""
import hashlib,hmac,json,os,sys,tempfile
from pathlib import Path
from decimal import Decimal,InvalidOperation
DEFAULT_NAME='Café Desk'; DEFAULT_ICON='office'; MAX_NAME=48
ICONS={'office':'Office','document':'Document','printer':'Printer','scanner':'Scanner','briefcase':'Briefcase','globe':'Globe','star':'Star','diamond':'Diamond','store':'Shop','leaf':'Leaf','custom':'Custom logo'}
def settings_path():
    if sys.platform=='win32': root=Path(os.environ.get('APPDATA') or Path.home()/'AppData'/'Roaming')
    elif sys.platform=='darwin': root=Path.home()/'Library'/'Application Support'
    else:
        xdg=os.environ.get('XDG_CONFIG_HOME',''); root=Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home()/'.config'
    return root/'CafeDesk'/'settings.json'
def logo_path(): return settings_path().parent/'business-logo.png'
def clean_name(name):
    name=' '.join(str(name).split())
    if not name: raise ValueError('Enter a business name.')
    if len(name)>MAX_NAME: raise ValueError(f'Use {MAX_NAME} characters or fewer.')
    return name
def load_settings(path=None):
    default={'business_name':DEFAULT_NAME,'business_icon':DEFAULT_ICON,'currency':'R','prices':[]}
    try:
        obj=json.loads(Path(path or settings_path()).read_text(encoding='utf-8'))
        if not isinstance(obj,dict): return default
        name=clean_name(obj.get('business_name',DEFAULT_NAME)); icon=obj.get('business_icon',DEFAULT_ICON)
        default.update({k:v for k,v in obj.items() if k in ('pin_salt','pin_hash','currency','prices')})
        default.update(business_name=name,business_icon=icon if icon in ICONS else DEFAULT_ICON)
        if not isinstance(default['prices'],list): default['prices']=[]
        return default
    except (OSError,ValueError,TypeError,AttributeError): return default
def load_name(path=None): return load_settings(path)['business_name']
def _write(data,path=None):
    target=Path(path or settings_path()); target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.settings-',suffix='.json',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def save_settings(name,icon,path=None):
    name=clean_name(name)
    if icon not in ICONS: raise ValueError('Please select an available business icon.')
    data=load_settings(path); data.update(business_name=name,business_icon=icon); _write(data,path); return name
def save_name(name,path=None): return save_settings(name,load_settings(path)['business_icon'],path)
def set_pin(pin,path=None):
    if not pin.isascii() or not pin.isdigit() or not 6<=len(pin)<=12: raise ValueError('Use a PIN of 6–12 digits.')
    salt=os.urandom(16); digest=hashlib.pbkdf2_hmac('sha256',pin.encode(),salt,300000)
    data=load_settings(path); data.update(pin_salt=salt.hex(),pin_hash=digest.hex()); _write(data,path)
def pin_is_set(path=None):
    d=load_settings(path); return bool(d.get('pin_salt') and d.get('pin_hash'))
def verify_pin(pin,path=None):
    d=load_settings(path)
    try:
        digest=hashlib.pbkdf2_hmac('sha256',pin.encode(),bytes.fromhex(d['pin_salt']),300000).hex()
        return hmac.compare_digest(digest,d['pin_hash'])
    except (KeyError,ValueError,TypeError): return False
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
    prices=parse_price_list(text); data=load_settings(path); data.update(currency=currency,prices=prices); _write(data,path); return prices
