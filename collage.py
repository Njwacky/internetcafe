"""A4 image sheets, with coordinates stored as 0..1 from the page top-left."""
from pathlib import Path
from PIL import Image,ImageOps,ImageEnhance
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from services import _atomic_write, _different

PRESETS={'2 per page':(1,2),'4 per page':(2,2),'6 per page':(2,3),'9 per page':(3,3),'Free layout':(3,3),'Custom size (mm)':None}

def layout(paths,preset,width_mm=None,height_mm=None):
    if preset not in PRESETS: raise ValueError('Choose a valid layout.')
    if preset=='Custom size (mm)':
        try: width=float(width_mm);height=float(height_mm)
        except (ValueError,TypeError): raise ValueError('Enter a custom width and height in millimetres.')
        import math
        if not math.isfinite(width) or not math.isfinite(height) or width<5 or height<5 or width>196 or height>283:
            raise ValueError('Custom width must be 5–196 mm and height 5–283 mm (A4 with 7 mm margins).')
        margin_x=7/210; margin_y=7/297;gap_x=3/210;gap_y=3/297;w=width/210;h=height/297
        cols=math.floor((199+1e-9)/(width+3));rows=math.floor((286+1e-9)/(height+3))
        if cols<1 or rows<1: raise ValueError('This size does not fit inside the A4 margins.')
        capacity=cols*rows
        coords=lambda i:(margin_x+(i%capacity%cols)*(w+gap_x),margin_y+(i%capacity//cols)*(h+gap_y))
    else:
        cols,rows=PRESETS[preset];capacity=cols*rows;margin=.035;gap=.018
        w=(1-2*margin-gap*(cols-1))/cols;h=(1-2*margin-gap*(rows-1))/rows
        coords=lambda i:(margin+(i%capacity%cols)*(w+gap),margin+(i%capacity//cols)*(h+gap))
    result=[]
    for i,value in enumerate(paths):
        path=value['path'] if isinstance(value,dict) else value;rotation=value.get('rotation',0) if isinstance(value,dict) else 0
        x,y=coords(i);result.append(dict(path=str(path),page=i//capacity,x=x,y=y,w=w,h=h,rotation=rotation,crop=list(value.get('crop',[0,0,1,1])) if isinstance(value,dict) else [0,0,1,1]))
    return result

def photo(path,brightness=1.0,grayscale=False,rotation=0):
    with Image.open(path) as src:
        image=ImageOps.exif_transpose(src).convert('RGBA')
        base=Image.new('RGB',image.size,'white'); base.paste(image,mask=image.getchannel('A'))
    if rotation not in (0,90,180,270): raise ValueError('Rotation must be a right angle.')
    if rotation: base=base.rotate(-rotation,expand=True)
    if grayscale: base=ImageOps.grayscale(base).convert('RGB')
    if brightness!=1: base=ImageEnhance.Brightness(base).enhance(brightness)
    return base

def validate_crop(crop):
    """Normalised rectangle in the already-rotated image, [left, top, right, bottom]."""
    import math
    if not isinstance(crop,(list,tuple)) or len(crop)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in crop):raise ValueError('Invalid picture crop.')
    l,t,r,b=crop
    if not (0<=l<r<=1 and 0<=t<b<=1 and r-l>=0.001 and b-t>=0.001):raise ValueError('Invalid picture crop.')
    return [float(v) for v in crop]

def crop_image(image,crop):
    import math
    l,t,r,b=validate_crop(crop)
    box=(max(0,math.floor(l*image.width)),max(0,math.floor(t*image.height)),min(image.width,math.ceil(r*image.width)),min(image.height,math.ceil(b*image.height)))
    return image.crop(box)

def rotate_crop(crop):
    l,t,r,b=validate_crop(crop)
    return [1-b,l,1-t,r]

def resize_frame(item,width_mm,height_mm):
    """Set one A4 frame's dimensions; source photo and other frames are untouched."""
    import math
    try:width=float(width_mm);height=float(height_mm)
    except (ValueError,TypeError):raise ValueError('Enter a width and height in millimetres.')
    if not math.isfinite(width) or not math.isfinite(height) or width<5 or height<5 or width>210 or height>297:
        raise ValueError('Width must be 5–210 mm and height 5–297 mm.')
    x=item['x'];y=item['y'];w=width/210;h=height/297
    if x+w>1+1e-9 or y+h>1+1e-9:raise ValueError('This size will not fit at the current position. Move the picture up or left, or enter a smaller size.')
    item.update(w=min(w,1-x),h=min(h,1-y))

def render_pdf(items,destination,mode='Fit whole image',brightness=1.0,grayscale=False):
    if not items: raise ValueError('Add one or more pictures first.')
    if mode not in ('Fit whole image','Fill & crop'): raise ValueError('Choose a valid image fitting mode.')
    if not 0.25<=brightness<=2: raise ValueError('Brightness must be between 25% and 200%.')
    _different([i['path'] for i in items],destination)
    for item in items:
        if item['page']<0 or not (0<=item['x']<1 and 0<=item['y']<1 and 0<item['w']<=1 and 0<item['h']<=1 and item['x']+item['w']<=1.00001 and item['y']+item['h']<=1.00001): raise ValueError('A picture is outside its page.')
    def write(tmp):
        out=canvas.Canvas(tmp,pagesize=A4); pw,ph=A4
        for page in range(max(i['page'] for i in items)+1):
            for item in (x for x in items if x['page']==page):
                image=crop_image(photo(item['path'],brightness,grayscale,item.get('rotation',0)),item.get('crop',[0,0,1,1])); w=item['w']*pw; h=item['h']*ph; x=item['x']*pw; y=ph-(item['y']*ph)-h
                scale=(max if mode=='Fill & crop' else min)(w/image.width,h/image.height); dw=image.width*scale; dh=image.height*scale
                out.saveState(); clip=out.beginPath(); clip.rect(x,y,w,h); out.clipPath(clip,stroke=0,fill=0)
                out.drawImage(ImageReader(image),x+(w-dw)/2,y+(h-dh)/2,width=dw,height=dh,mask='auto'); out.restoreState()
            out.showPage()
        out.save()
    _atomic_write(destination,write)
