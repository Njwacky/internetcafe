"""Local document operations for Café Desk. No GUI dependencies."""
from pathlib import Path
import os, tempfile, io, shutil
from xml.sax.saxutils import escape
from pypdf import PdfReader, PdfWriter
from PIL import Image, ImageOps, ImageSequence
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as PDFImage, PageBreak, HRFlowable, Table, TableStyle
from templates import COLORS

def _atomic_write(destination, write):
    target=Path(destination)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd, tmp=tempfile.mkstemp(prefix='.cafedesk-',suffix=target.suffix,dir=str(target.parent)); os.close(fd)
    try:
        write(tmp)
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _reader(path):
    reader=PdfReader(str(path))
    if reader.is_encrypted and reader.decrypt('') == 0:
        raise ValueError(f'“{Path(path).name}” is password-protected. Remove its password before using it here.')
    return reader

def _different(source_paths, destination):
    dest=Path(destination).resolve()
    if any(Path(p).resolve()==dest for p in source_paths):
        raise ValueError('Choose an output file different from the source file.')

def merge_pdf(paths,destination):
    if not paths: raise ValueError('Add at least one PDF.')
    _different(paths,destination)
    writer=PdfWriter()
    for path in paths:
        reader=_reader(path)
        for page in reader.pages: writer.add_page(page)
    _atomic_write(destination,lambda tmp: _write_pdf(writer,tmp))

def _write_pdf(writer,path):
    with open(path,'wb') as file: writer.write(file)

def parse_pages(spec,total):
    result=[]
    for part in spec.split(','):
        part=part.strip()
        if not part: raise ValueError('Enter page numbers like 1-3, 5.')
        try:
            if '-' in part:
                parts=part.split('-')
                if len(parts)!=2: raise ValueError()
                first,last=map(int,parts)
                if first>last: raise ValueError('Page ranges must be in ascending order.')
                seq=range(first,last+1)
            else: seq=[int(part)]
            for p in seq:
                if p<1 or p>total: raise ValueError(f'Page {p} is outside the document (1–{total}).')
                if p not in result: result.append(p)
        except ValueError as exc:
            if str(exc): raise
            raise ValueError('Enter page numbers like 1-3, 5.') from exc
    return result

def pdf_page_count(source): return len(_reader(source).pages)

def extract_pdf(source,spec,destination):
    _different([source],destination); reader=_reader(source); pages=parse_pages(spec,len(reader.pages)); writer=PdfWriter()
    for n in pages: writer.add_page(reader.pages[n-1])
    _atomic_write(destination,lambda tmp:_write_pdf(writer,tmp))
    return len(pages)

def split_each_page(source,folder):
    reader=_reader(source); root=Path(folder); stem=Path(source).stem
    targets=[root / f'{stem}_page_{i+1:03d}.pdf' for i in range(len(reader.pages))]
    if not targets: raise ValueError('The selected PDF has no pages.')
    existing=[p.name for p in targets if p.exists()]
    if existing: raise FileExistsError(f'Output already exists: {existing[0]}. Choose an empty folder or remove the existing files.')
    # Generate all files first; never overwrite any existing target.
    with tempfile.TemporaryDirectory(prefix='.cafedesk-',dir=root) as temp:
        for i,page in enumerate(reader.pages):
            writer=PdfWriter(); writer.add_page(page); _write_pdf(writer,Path(temp)/targets[i].name)
        created=[]
        try:
            for target in targets:
                # Exclusive creation avoids overwriting if another process creates a file.
                with open(target,'xb') as out:
                    created.append(target)
                    with open(Path(temp)/target.name,'rb') as src: shutil.copyfileobj(src,out)
        except Exception:
            for target in created: target.unlink(missing_ok=True)
            raise
    return len(targets)

def images_to_pdf(paths,destination):
    if not paths: raise ValueError('Add at least one image.')
    _different(paths,destination)
    def build(temp):
        story=[]; buffers=[]
        for path in paths:
            with Image.open(path) as source:
                for frame in ImageSequence.Iterator(source):
                    image=ImageOps.exif_transpose(frame.copy())
                    if image.mode in ('RGBA','LA') or 'transparency' in image.info:
                        image=image.convert('RGBA'); background=Image.new('RGB',image.size,'white'); background.paste(image,mask=image.getchannel('A')); image=background
                    else: image=image.convert('RGB')
                    w,h=image.size; scale=min((A4[0]-60)/w,(A4[1]-60)/h)
                    buffer=io.BytesIO(); image.save(buffer,format='PNG'); buffer.seek(0); buffers.append(buffer)
                    if story: story.append(PageBreak())
                    story.append(PDFImage(buffer,width=w*scale,height=h*scale))
        SimpleDocTemplate(temp,pagesize=A4,leftMargin=24,rightMargin=24,topMargin=24,bottomMargin=24).build(story)
    _atomic_write(destination,build)

def export_text(text,destination):
    if not text.strip(): raise ValueError('Create or write a document first.')
    _atomic_write(destination,lambda tmp: Path(tmp).write_text(text,encoding='utf-8'))

def export_document_pdf(text,destination,category,index,photo_path=None):
    if not text.strip(): raise ValueError('Create or write a document first.')
    if photo_path: _different([photo_path],destination)
    if category=='Résumé / CV':
        import cv_renderer
        _atomic_write(destination,lambda temp:cv_renderer.render(text,temp,index,photo_path))
        return
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=10.5,leading=16,spaceAfter=3,splitLongWords=1)
    def build(temp):
        story=[Paragraph(escape(line),body) if line.strip() else Spacer(1,7) for line in text.splitlines()]
        SimpleDocTemplate(temp,pagesize=A4,leftMargin=55,rightMargin=55,topMargin=50,bottomMargin=50,title=category).build(story)
    _atomic_write(destination,build)

def render_document_preview(text,category,index=0,photo_path=None,page=1,max_size=(340,481)):
    """Render a career document page to a PIL RGB image for in-app A4 preview.

    Returns `(image, clamped_page, total_pages)` without leaving temporary files behind.
    """
    if not text or not text.strip():
        raise ValueError('Create or write a document first to preview it.')
    import math, pypdfium2 as pdfium
    max_w, max_h = max_size
    with tempfile.TemporaryDirectory(prefix='cafedesk-docprev-') as tmpdir:
        pdf_path = Path(tmpdir) / 'preview.pdf'
        export_document_pdf(text, pdf_path, category, index, photo_path if category=='Résumé / CV' else None)
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            total = len(doc)
            if total < 1:
                raise ValueError('Preview generated an empty PDF.')
            page_num = max(1, min(int(page), total))
            page_obj = doc[page_num - 1]
            try:
                w, h = page_obj.get_size()
                if not all(math.isfinite(v) and v > 0 for v in (w, h)):
                    raise ValueError('Invalid PDF page dimensions.')
                scale = min(2.0, max_w / w, max_h / h)
                bitmap = page_obj.render(scale=scale)
                try:
                    image = bitmap.to_pil().convert('RGB').copy()
                finally:
                    bitmap.close()
            finally:
                page_obj.close()
            return image, page_num, total
        finally:
            doc.close()

def save_custom_logo(source,destination):
    """Copy and normalize a logo into the per-user config folder."""
    with Image.open(source) as src:
        image=ImageOps.exif_transpose(src).convert('RGBA'); image.thumbnail((256,256))
        _atomic_write(destination,lambda temp:image.save(temp,format='PNG'))

TESSERACT_DOWNLOAD_URL='https://github.com/UB-Mannheim/tesseract/wiki'
TESSERACT_WINDOWS_INSTALLER_URL='https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe'

def open_tesseract_link(direct_installer=False):
    """Open the official Tesseract OCR download link in the user's web browser."""
    import webbrowser
    url=TESSERACT_WINDOWS_INSTALLER_URL if direct_installer else TESSERACT_DOWNLOAD_URL
    if not webbrowser.open(url):
        raise ValueError(f'Could not open your web browser automatically. Open this link manually:\n{url}')
    return url

def load_ocr_preview(source,max_size=(260,300)):
    """Load and resize an image safely for OCR preview without altering the source file."""
    p=Path(source)
    if not p.is_file(): raise ValueError(f'Image not found: {p.name}')
    try:
        with Image.open(p) as src:
            image=ImageOps.exif_transpose(src).convert('RGB')
            image.thumbnail(max_size,Image.Resampling.LANCZOS)
            return image.copy()
    except Exception as exc:
        raise ValueError(f'Could not preview {p.name}: {exc}') from exc

def run_ocr_many(sources):
    """Run English OCR across one or more images in order."""
    paths=[str(s) for s in sources if str(s).strip()]
    if not paths: raise ValueError('Select at least one image first.')
    if len(paths)==1: return run_ocr(paths[0])
    parts=[]
    for i,path in enumerate(paths,1):
        text=run_ocr(path)
        header=f'--- Image {i}: {Path(path).name} ---'
        parts.append(f'{header}\n{text}' if text else f'{header}\n[No text detected]')
    return '\n\n'.join(parts)

def run_ocr(source):
    """OCR an image locally with a separately installed Tesseract, English only."""
    import subprocess,shutil,os
    executable=os.environ.get('TESSERACT_CMD') or shutil.which('tesseract')
    if not executable and os.name=='nt':
        for env_key in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA'):
            base=os.environ.get(env_key)
            if not base: continue
            candidate=(Path(base)/'Programs'/'Tesseract-OCR'/'tesseract.exe') if env_key=='LOCALAPPDATA' else (Path(base)/'Tesseract-OCR'/'tesseract.exe')
            if candidate.exists(): executable=str(candidate); break
    if not executable: raise ValueError('Tesseract OCR was not found. Open Help & privacy and click the direct Tesseract OCR download link to install it (with English language data), then restart the app.')
    try:
        p=subprocess.run([str(executable),str(source),'stdout','-l','eng'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc: raise ValueError('OCR could not run or timed out. Check that Tesseract is installed correctly.') from exc
    if p.returncode: raise ValueError('OCR failed. Check the image and your Tesseract English language installation.')
    return p.stdout.decode('utf-8',errors='replace').strip()

def receipt_text(business,currency,items,customer='',timestamp=None,reference=None):
    from decimal import Decimal
    from datetime import datetime
    if not items: raise ValueError('Add at least one service.')
    when=timestamp or datetime.now().strftime('%Y-%m-%d %H:%M')
    result=[business,'RECEIPT',str(when)]
    if reference: result.append('Reference: '+str(reference))
    if customer.strip(): result.append('Customer: '+customer.strip())
    result+=['','SERVICES']
    total=Decimal('0')
    for item in items:
        price=Decimal(str(item['price'])); qty=int(item['qty']); total+=price*qty
        result.append(f"{item['name']}  x {qty}  @ {currency}{price:.2f}  = {currency}{price*qty:.2f}")
    result+=['',f'TOTAL: {currency}{total:.2f}','','Simple receipt • tax not calculated • payment status not tracked']
    return '\n'.join(result)
