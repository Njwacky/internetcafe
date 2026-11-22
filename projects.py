"""Portable, local Café Desk project bundles. Treat input archives as untrusted."""
import hashlib,io,json,math,os,re,shutil,uuid,zipfile
from pathlib import Path
from PIL import Image
import services,collage,settings_store
from templates import LABELS,CATEGORIES,NAMES,CV_NAMES

FORMAT='cafe-desk-project';VERSION=1;SUPPORTED_VERSIONS=(1,2)
KNOWN_FEATURES={'crop_rect','custom_mm'}
MAX_ASSET=20*1024*1024;MAX_TOTAL=80*1024*1024;MAX_MANIFEST=2*1024*1024;MAX_ITEMS=200
HEX=re.compile(r'^[0-9a-f]{64}$')

def _image(path,allow_internal=False):
    if not allow_internal:
        services.assert_safe_user_input(path)
    p=Path(path)
    if p.stat().st_size>MAX_ASSET:raise ValueError('Each image must be 20 MB or smaller for a project.')
    data=p.read_bytes()
    if len(data)>MAX_ASSET:raise ValueError('Each image must be 20 MB or smaller for a project.')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>services.MAX_IMAGE_PIXELS:
                raise ValueError()
            image.verify()
    except Exception as exc:raise ValueError(f'Could not read image: {p.name}') from exc
    return data

def _asset(path,assets,allow_internal=False):
    if not path:return None
    data=_image(path,allow_internal=allow_internal);key=hashlib.sha256(data).hexdigest();assets.setdefault(key,data)
    if sum(len(v) for v in assets.values())>MAX_TOTAL:raise ValueError('Project images exceed the 80 MB total limit.')
    return key

def _text(value,name,limit):
    if not isinstance(value,str) or len(value)>limit:raise ValueError(f'Invalid or oversized {name}.')
    return value

def _archive_parts(kind,data,allow_internal=False):
    if kind not in ('career','sheet'):raise ValueError('Unknown project type.')
    assets={};paths=[];features=[]
    if kind=='career':
        category=data.get('category');template=data.get('template')
        if category not in CATEGORIES or template not in (CV_NAMES if category==CATEGORIES[0] else NAMES):raise ValueError('Invalid document type or template.')
        fields=data.get('fields',{});fields={k:_text(fields.get(k,''),k,50000) for k in LABELS}
        photo=data.get('photo');paths=[photo] if photo else []
        payload={'category':category,'template':template,'fields':fields,'draft':_text(data.get('draft',''),'draft',1000000),'photo':_asset(photo,assets,allow_internal=allow_internal)}
    else:
        sheet=data;items=sheet.get('items',[])
        if not isinstance(items,list) or len(items)>MAX_ITEMS:raise ValueError('Too many pictures in the project.')
        paths=[x.get('path') for x in items]
        saved=[];has_crop=False
        for item in items:
            crop=collage.validate_crop(item.get('crop',[0,0,1,1]))
            if crop!=[0.0,0.0,1.0,1.0]:has_crop=True
            entry={k:item.get(k) for k in ('page','x','y','w','h','rotation')};entry['asset']=_asset(item.get('path'),assets,allow_internal=allow_internal);entry['crop']=crop;saved.append(entry)
        if has_crop:features.append('crop_rect')
        if sheet.get('preset')=='Custom size (mm)':features.append('custom_mm')
        payload={'items':saved,'preset':sheet.get('preset'),'page':sheet.get('page',0),'mode':sheet.get('mode'),'brightness':sheet.get('brightness'),'gray':sheet.get('gray'),'width_mm':str(sheet.get('width_mm','')),'height_mm':str(sheet.get('height_mm',''))}
        _validate_sheet(payload)
    version=2 if features else VERSION
    manifest={'format':FORMAT,'version':version,'features':features,'kind':kind,'data':payload}
    encoded=json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    if len(encoded)>MAX_MANIFEST:raise ValueError('The project contains too much text.')
    return encoded,assets,paths

def _write_archive(target,encoded,assets):
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr('manifest.json',encoded)
        for key,value in assets.items():z.writestr('assets/'+key,value)

def project_bytes(kind,data,allow_internal=False):
    """Serialize without touching disk, for generation-checked recovery publication."""
    encoded,assets,_=_archive_parts(kind,data,allow_internal=allow_internal)
    target=io.BytesIO()
    _write_archive(target,encoded,assets)
    return target.getvalue()

def save(kind,data,destination,allow_internal=False):
    if not allow_internal:services.assert_safe_user_output(destination)
    encoded,assets,paths=_archive_parts(kind,data,allow_internal=allow_internal)
    services._different([p for p in paths if p],destination)
    services._atomic_write(destination,lambda tmp:_write_archive(tmp,encoded,assets),allow_internal=allow_internal)

def _valid_asset(key):return key is None or (isinstance(key,str) and HEX.fullmatch(key) is not None)

def _validate_sheet(sheet):
    if not isinstance(sheet,dict):raise ValueError('Invalid picture-sheet project.')
    preset=sheet.get('preset');mode=sheet.get('mode');brightness=sheet.get('brightness');gray=sheet.get('gray');items=sheet.get('items')
    if preset not in collage.PRESETS or mode not in ('Fit whole image','Fill & crop') or type(brightness)!=int or not 25<=brightness<=200 or type(gray)!=bool or not isinstance(items,list) or len(items)>MAX_ITEMS:raise ValueError('Invalid picture-sheet settings.')
    width=_text(sheet.get('width_mm',''),'width',20);height=_text(sheet.get('height_mm',''),'height',20)
    if preset=='Custom size (mm)':collage.layout([],preset,width,height)
    page=sheet.get('page',0)
    if type(page)!=int or not 0<=page<=MAX_ITEMS:raise ValueError('Invalid page number.')
    for item in items:
        if not isinstance(item,dict) or not _valid_asset(item.get('asset')) or item.get('asset') is None:raise ValueError('Invalid picture reference.')
        collage.validate_crop(item.get('crop',[0,0,1,1]))
        if type(item.get('page'))!=int or not 0<=item['page']<MAX_ITEMS or type(item.get('rotation'))!=int or item['rotation'] not in (0,90,180,270):raise ValueError('Invalid picture placement.')
        for k in ('x','y','w','h'):
            v=item.get(k)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ValueError('Invalid picture placement.')
        if not(0<=item['x']<1 and 0<=item['y']<1 and 0<item['w']<=1 and 0<item['h']<=1 and item['x']+item['w']<=1.00001 and item['y']+item['h']<=1.00001):raise ValueError('Picture placement is outside the page.')
    return sheet

def load(source,session_dir,allow_internal=False):
    if not allow_internal and settings_store.is_internal_path(source):
        raise ValueError('Projects inside the Café Desk recovery or configuration folder can only be opened via Staff recovery.')
    try:
        with zipfile.ZipFile(source,'r') as z:
            infos=z.infolist();names=[x.filename for x in infos]
            if len(infos)>MAX_ITEMS+2 or len(names)!=len(set(names)) or 'manifest.json' not in names:raise ValueError('Invalid project archive.')
            if any(not (i.filename=='manifest.json' or (i.filename.startswith('assets/') and HEX.fullmatch(i.filename[7:]))) for i in infos):raise ValueError('Unexpected file in project archive.')
            if sum(i.file_size for i in infos)>MAX_TOTAL+MAX_MANIFEST:raise ValueError('Project archive is too large.')
            info=z.getinfo('manifest.json')
            if info.file_size>MAX_MANIFEST:raise ValueError('Project manifest is too large.')
            manifest=json.loads(z.read(info).decode('utf-8'))
            if not isinstance(manifest,dict) or manifest.get('format')!=FORMAT or manifest.get('version') not in SUPPORTED_VERSIONS:raise ValueError('Unsupported project format or version.')
            feats=manifest.get('features',[])
            if not isinstance(feats,list) or any(not isinstance(f,str) or f not in KNOWN_FEATURES for f in feats):
                raise ValueError('This project uses newer features not supported by this version of Café Desk.')
            kind=manifest.get('kind');payload=manifest.get('data')
            if not isinstance(payload,dict):raise ValueError('Invalid project data.')
            if kind=='career':
                category=payload.get('category');template=payload.get('template');fields=payload.get('fields');photo=payload.get('photo')
                if category not in CATEGORIES or template not in (CV_NAMES if category==CATEGORIES[0] else NAMES) or not isinstance(fields,dict) or not _valid_asset(photo):raise ValueError('Invalid career project.')
                _text(payload.get('draft'),'draft',1000000)
                for key in LABELS:_text(fields.get(key,''),key,50000)
                refs=[photo] if photo else []
            elif kind=='sheet':
                _validate_sheet(payload);refs=[x['asset'] for x in payload['items']]
            else:raise ValueError('Unknown project type.')
            if set(names)!=({'manifest.json'}|{'assets/'+key for key in refs}):raise ValueError('Missing or unexpected project images.')
            decoded={}
            for key in set(refs):
                info=z.getinfo('assets/'+key)
                if info.file_size>MAX_ASSET:raise ValueError('A project image is too large.')
                raw=z.read(info)
                if hashlib.sha256(raw).hexdigest()!=key:raise ValueError('A project image is damaged.')
                try:
                    with Image.open(io.BytesIO(raw)) as img:
                        if img.width*img.height>services.MAX_IMAGE_PIXELS:
                            raise ValueError()
                        img.verify()
                except Exception as exc:raise ValueError('A project image is invalid.') from exc
                decoded[key]=raw
    except (zipfile.BadZipFile,UnicodeError,json.JSONDecodeError,KeyError,OSError) as exc:raise ValueError('Could not read the project. It may be damaged or unsupported.') from exc
    dest=Path(session_dir)/('project-'+uuid.uuid4().hex);dest.mkdir(parents=True,exist_ok=False)
    try:
        files={}
        for key,raw in decoded.items():
            path=dest/(key+'.img');path.write_bytes(raw);files[key]=str(path)
        if kind=='career':
            result={'category':payload['category'],'template':payload['template'],'fields':{k:payload['fields'].get(k,'') for k in LABELS},'draft':payload['draft'],'photo':files.get(payload.get('photo'))}
        else:
            result={k:payload[k] for k in ('preset','page','mode','brightness','gray','width_mm','height_mm')};result['selected']=None;result['items']=[]
            for item in payload['items']:
                result['items'].append({**{k:item[k] for k in ('page','x','y','w','h','rotation')},'path':files[item['asset']],'crop':collage.validate_crop(item.get('crop',[0,0,1,1]))})
            maxpage=max((item['page'] for item in result['items']),default=0);result['page']=min(result['page'],maxpage)
        return kind,result
    except Exception:
        shutil.rmtree(dest,ignore_errors=True);raise
