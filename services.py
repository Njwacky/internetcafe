"""Local document operations for Café Desk. No GUI dependencies."""
from pathlib import Path
import os, tempfile, io, shutil, subprocess, sys
import urllib.parse
import ai_safety
from xml.sax.saxutils import escape
from pypdf import PdfReader, PdfWriter
from PIL import Image, ImageOps, ImageSequence
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as PDFImage, PageBreak, HRFlowable, Table, TableStyle
from templates import COLORS
import settings_store

MAX_IMAGE_PIXELS=50_000_000
MAX_IMAGE_FILE_BYTES=60*1024*1024
MAX_IMAGE_FRAMES=300
FREE_SCAN_PAGES=5
MAX_PDF_PAGES=2000
MAX_PDF_FILE_BYTES=150*1024*1024
MAX_PDF_PAGE_DIM_PT=14400.0
Image.MAX_IMAGE_PIXELS=MAX_IMAGE_PIXELS

def harden_file_permissions(path):
    """Best-effort owner-only permissions. POSIX chmod 0600; Windows icacls owner-only ACL.

    Windows `os.chmod` is a no-op for ACLs, so without this step settings/sales
    files inherit broad profile permissions on shared café PCs.
    """
    p = Path(path)
    try:
        if sys.platform == 'win32':
            user = os.environ.get('USERNAME', '').strip()
            if user:
                subprocess.run(
                    ['icacls', str(p), '/inheritance:r', '/grant:r', f'{user}:F'],
                    capture_output=True, timeout=10, check=False,
                )
        else:
            os.chmod(p, 0o600)
    except (OSError, subprocess.SubprocessError):
        pass


def secure_delete(path):
    """Overwrite file contents with random bytes, then unlink (best-effort secure erase).

    SSDs / copy-on-write filesystems may retain data regardless; this is strictly
    better than a plain unlink for magnetic media and shared-PC hygiene.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    if size > 0 and p.is_file() and not p.is_symlink():
        try:
            with open(p, 'r+b') as fh:
                remaining = size
                while remaining > 0:
                    chunk = os.urandom(min(1024 * 1024, remaining))
                    fh.write(chunk)
                    remaining -= len(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass
    p.unlink(missing_ok=True)


def assert_safe_user_input(source, data_dir=None):
    """Refuse to open or exfiltrate files inside Café Desk's internal settings/sales/recovery directory."""
    if settings_store.is_internal_path(source, data_dir=data_dir):
        raise ValueError('Cannot open files from the Café Desk internal configuration, sales or recovery folder.')

def assert_safe_user_output(destination, data_dir=None):
    """Refuse to write user exports over Café Desk's internal settings/sales/recovery directory."""
    if settings_store.is_internal_path(destination, data_dir=data_dir):
        raise ValueError('Cannot save inside the Café Desk internal configuration, sales or recovery folder. Choose another folder.')

def open_safe_image(path, allow_internal=False):
    """Open an untrusted image with internal-path, file-size, and pixel-count bounds."""
    if not allow_internal:
        assert_safe_user_input(path)
    p=Path(path)
    if not p.is_file():
        raise ValueError(f'Image not found: {p.name}')
    if p.stat().st_size>MAX_IMAGE_FILE_BYTES:
        raise ValueError(f'“{p.name}” exceeds the {MAX_IMAGE_FILE_BYTES//(1024*1024)} MB image size limit.')
    try:
        im=Image.open(p)
    except Image.DecompressionBombError as exc:
        raise ValueError(f'“{p.name}” has too many pixels to process safely.') from exc
    except Exception as exc:
        raise ValueError(f'Could not open image: {p.name}') from exc
    if im.width*im.height>MAX_IMAGE_PIXELS:
        im.close()
        raise ValueError(f'“{p.name}” exceeds the {MAX_IMAGE_PIXELS:,}-pixel safety limit.')
    return im

def _atomic_write(destination, write, allow_internal=False):
    if not allow_internal:
        assert_safe_user_output(destination)
    target=Path(destination)
    if target.is_symlink():
        raise ValueError('Cannot overwrite a symbolic link.')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd, tmp=tempfile.mkstemp(prefix='.cafedesk-',suffix=target.suffix,dir=str(target.parent)); os.close(fd)
    try:
        write(tmp)
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _reader(path):
    import math
    assert_safe_user_input(path)
    p=Path(path)
    if not p.is_file():
        raise ValueError(f'PDF not found: {p.name}')
    if p.stat().st_size>MAX_PDF_FILE_BYTES:
        raise ValueError(f'“{p.name}” exceeds the {MAX_PDF_FILE_BYTES//(1024*1024)} MB PDF size limit.')
    try:
        reader=PdfReader(str(p))
    except Exception as exc:
        raise ValueError(f'Could not read PDF “{p.name}”. It may be damaged or unsupported.') from exc
    if reader.is_encrypted and reader.decrypt('') == 0:
        raise ValueError(f'“{p.name}” is password-protected. Remove its password before using it here.')
    if len(reader.pages)>MAX_PDF_PAGES:
        raise ValueError(f'“{p.name}” has more than {MAX_PDF_PAGES} pages.')
    for idx,page in enumerate(reader.pages,1):
        try:
            w=float(page.mediabox.width); h=float(page.mediabox.height)
        except Exception as exc:
            raise ValueError(f'Page {idx} of “{p.name}” has unreadable dimensions.') from exc
        if not (math.isfinite(w) and math.isfinite(h) and 0<w<=MAX_PDF_PAGE_DIM_PT and 0<h<=MAX_PDF_PAGE_DIM_PT):
            raise ValueError(f'Page {idx} of “{p.name}” has invalid or oversized dimensions.')
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

def remove_pdf_password(source, destination, password=''):
    """Remove open/owner password protection from a PDF after verifying Paid Add-on approval for this Machine ID."""
    import math, license_guard
    addon_status = license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK)
    if not addon_status.get('valid'):
        mid = addon_status.get('machine_id') or license_guard.get_machine_id()
        raise ValueError(
            f'PDF Password Remover is a paid add-on and is not activated on this computer (Machine ID: {mid}). '
            f'Request owner approval and enter your Paid Add-on Key first.'
        )

    assert_safe_user_input(source)
    _different([source], destination)
    p = Path(source)
    if not p.is_file():
        raise ValueError(f'PDF not found: {p.name}')
    if p.stat().st_size > MAX_PDF_FILE_BYTES:
        raise ValueError(f'“{p.name}” exceeds the {MAX_PDF_FILE_BYTES // (1024 * 1024)} MB PDF size limit.')

    try:
        reader = PdfReader(str(p))
    except Exception as exc:
        raise ValueError(f'Could not read PDF “{p.name}”. It may be damaged or unsupported.') from exc

    was_encrypted = bool(reader.is_encrypted)
    if was_encrypted:
        pwd = str(password or '')
        unlocked = reader.decrypt(pwd)
        if unlocked == 0 and pwd:
            # Also try stripped password in case of accidental trailing space on paste
            unlocked = reader.decrypt(pwd.strip())
        if unlocked == 0:
            if not pwd:
                raise ValueError(
                    f'“{p.name}” requires an open password. Enter the PDF\'s current password so Café Desk can unlock and save an unprotected copy.'
                )
            raise ValueError(f'Incorrect PDF password for “{p.name}”. Check capitalization and try again.')

    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f'“{p.name}” has more than {MAX_PDF_PAGES} pages.')

    writer = PdfWriter()
    for idx, page in enumerate(reader.pages, 1):
        try:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
        except Exception as exc:
            raise ValueError(f'Page {idx} of “{p.name}” has unreadable dimensions.') from exc
        if not (math.isfinite(w) and math.isfinite(h) and 0 < w <= MAX_PDF_PAGE_DIM_PT and 0 < h <= MAX_PDF_PAGE_DIM_PT):
            raise ValueError(f'Page {idx} of “{p.name}” has invalid or oversized dimensions.')
        writer.add_page(page)

    if reader.metadata:
        try:
            clean_meta = {
                str(k): str(v)
                for k, v in reader.metadata.items()
                if isinstance(k, str) and k.startswith('/') and v is not None
            }
            if clean_meta:
                writer.add_metadata(clean_meta)
        except Exception:
            pass

    _atomic_write(destination, lambda tmp: _write_pdf(writer, tmp))
    return len(reader.pages), was_encrypted

def split_each_page(source,folder):
    assert_safe_user_output(folder)
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

def check_scan_page_limit(page_count):
    """Enforce the free per-job page limit; the paid add-on is machine-specific."""
    if page_count>FREE_SCAN_PAGES:
        import license_guard
        if not license_guard.current_addon_status(license_guard.FEATURE_SCAN_PAGES).get('valid'):
            raise ValueError(f'Free scan and image PDFs are limited to {FREE_SCAN_PAGES} pages per job. Contact the team for a one-time paid unlock: WhatsApp {DEVELOPER_WHATSAPP_LOCAL} or email {DEVELOPER_EMAIL}.')


def images_to_pdf(paths,destination):
    if not paths: raise ValueError('Add at least one image.')
    _different(paths,destination)
    def build(temp):
        story=[]; buffers=[]; total_frames=0
        for path in paths:
            with open_safe_image(path) as source:
                for frame in ImageSequence.Iterator(source):
                    total_frames+=1
                    if total_frames>MAX_IMAGE_FRAMES:
                        raise ValueError(f'Too many image frames (maximum {MAX_IMAGE_FRAMES}).')
                    if total_frames == FREE_SCAN_PAGES+1:
                        check_scan_page_limit(total_frames)
                    if frame.width*frame.height>MAX_IMAGE_PIXELS:
                        raise ValueError(f'Frame in “{Path(path).name}” exceeds the {MAX_IMAGE_PIXELS:,}-pixel safety limit.')
                    image=ImageOps.exif_transpose(frame.copy())
                    if image.mode in ('RGBA','LA') or 'transparency' in image.info:
                        image=image.convert('RGBA'); background=Image.new('RGB',image.size,'white'); background.paste(image,mask=image.getchannel('A')); image=background
                    else: image=image.convert('RGB')
                    w,h=image.size; scale=min((A4[0]-60)/w,(A4[1]-60)/h)
                    buffer=io.BytesIO(); image.save(buffer,format='PNG'); buffer.seek(0); buffers.append(buffer)
                    if story: story.append(PageBreak())
                    story.append(PDFImage(buffer,width=w*scale,height=h*scale))
        SimpleDocTemplate(temp,pagesize=A4,leftMargin=24,rightMargin=24,topMargin=24,bottomMargin=24).build(story)
        if Path(temp).stat().st_size>MAX_PDF_FILE_BYTES:
            raise ValueError(f'The PDF exceeds the {MAX_PDF_FILE_BYTES//(1024*1024)} MB size limit. Save fewer pages per PDF or reduce image sizes.')
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
    import pdf_fonts
    reg_font,_=pdf_fonts.font_pair('Helvetica')
    body=ParagraphStyle('body',fontName=reg_font,fontSize=10.5,leading=16,spaceAfter=3,splitLongWords=1)
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
                scale = min(3.0, max_w / w, max_h / h)
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

def export_card_pdf(destination,kind,template_index,fields,sheet_index=0,photo_path=None,crop_marks=True):
    """Export Business card or Invitation card printable A4 PDF atomically."""
    if photo_path: _different([photo_path],destination)
    import cards_renderer
    _atomic_write(destination,lambda temp:cards_renderer.render_card_pdf(temp,kind,template_index,fields,sheet_index=sheet_index,photo_path=photo_path,crop_marks=crop_marks))

def export_form_pdf(spec,destination):
    """Export printable or interactive AcroForm A4 PDF atomically."""
    import forms_renderer
    validated=forms_renderer.validate_form_spec(spec)
    _atomic_write(destination,lambda temp:forms_renderer.render_form_pdf(validated,temp))

def render_form_preview(spec,page=1,max_size=(340,481)):
    """Rasterize a Form Creator page to a PIL RGB image for live A4 preview."""
    import math, pypdfium2 as pdfium, forms_renderer
    validated=forms_renderer.validate_form_spec(spec)
    max_w,max_h=max_size
    with tempfile.TemporaryDirectory(prefix='cafedesk-formprev-') as tmpdir:
        pdf_path=Path(tmpdir)/'form.pdf'
        # Render flat preview so values and borders appear crisply in pdfium thumbnail
        preview_spec=dict(validated,fillable=False)
        forms_renderer.render_form_pdf(preview_spec,pdf_path)
        doc=pdfium.PdfDocument(str(pdf_path))
        try:
            total=len(doc)
            if total<1: raise ValueError('Form preview produced an empty PDF.')
            pnum=max(1,min(int(page),total))
            page_obj=doc[pnum-1]
            try:
                w,h=page_obj.get_size()
                scale=min(3.0,max_w/w,max_h/h)
                bitmap=page_obj.render(scale=scale)
                try:
                    return bitmap.to_pil().convert('RGB').copy(),pnum,total
                finally:
                    bitmap.close()
            finally:
                page_obj.close()
        finally:
            doc.close()

def render_card_preview(kind,template_index,fields,sheet_index=0,photo_path=None,crop_marks=True,single_card=True,max_size=(380,260)):
    """Rasterize either a single card close-up or the full A4 print sheet to a PIL RGB image."""
    import math, pypdfium2 as pdfium, cards_renderer
    max_w, max_h = max_size
    with tempfile.TemporaryDirectory(prefix='cafedesk-cardprev-') as tmpdir:
        pdf_path = Path(tmpdir) / 'card.pdf'
        if single_card:
            cards_renderer.render_single_card_pdf(pdf_path, kind, template_index, fields, photo_path=photo_path)
        else:
            cards_renderer.render_card_pdf(pdf_path, kind, template_index, fields, sheet_index=sheet_index, photo_path=photo_path, crop_marks=crop_marks)
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            page_obj = doc[0]
            try:
                w, h = page_obj.get_size()
                if not all(math.isfinite(v) and v > 0 for v in (w, h)):
                    raise ValueError('Invalid card PDF dimensions.')
                scale = min(4.0, max_w / w, max_h / h)
                bitmap = page_obj.render(scale=scale)
                try:
                    return bitmap.to_pil().convert('RGB').copy()
                finally:
                    bitmap.close()
            finally:
                page_obj.close()
        finally:
            doc.close()

def save_custom_logo(source,destination):
    """Copy and normalize a logo into the per-user config folder."""
    with open_safe_image(source) as src:
        image=ImageOps.exif_transpose(src).convert('RGBA'); image.thumbnail((256,256))
        _atomic_write(destination,lambda temp:image.save(temp,format='PNG'),allow_internal=True)

TESSERACT_DOWNLOAD_URL='https://github.com/UB-Mannheim/tesseract/wiki'
TESSERACT_WINDOWS_INSTALLER_URL='https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe'
DEVELOPER_EMAIL='njwayelodlamini@gmail.com'
DEVELOPER_WHATSAPP_LOCAL='072 227 9505'
DEVELOPER_WHATSAPP_E164='27722279505'
DEVELOPER_WHATSAPP_URL='https://wa.me/27722279505'
DEVELOPER_GITHUB_URL='https://github.com/Njwacky'
DEVELOPER_REPO_URL='https://github.com/Njwacky/internetcafe'

def whatsapp_chat_url(message=''):
    """Build a direct WhatsApp click-to-chat URL (`https://wa.me/27722279505`) with optional prefilled text."""
    from urllib.parse import quote
    msg=(message or '').strip()
    return f'{DEVELOPER_WHATSAPP_URL}?text={quote(msg)}' if msg else DEVELOPER_WHATSAPP_URL

def open_external_url(url):
    """Open a trusted http/https/mailto link in the user's default browser or mail app."""
    import webbrowser
    target=str(url).strip()
    if not target.startswith(('https://','http://','mailto:')):
        raise ValueError('Unsupported link format.')
    if not webbrowser.open(target):
        raise ValueError(f'Could not open your browser or mail app automatically. Open manually:\n{target}')
    return target

def open_tesseract_link(direct_installer=False):
    """Open the official Tesseract OCR download link in the user's web browser."""
    url=TESSERACT_WINDOWS_INSTALLER_URL if direct_installer else TESSERACT_DOWNLOAD_URL
    return open_external_url(url)

def load_ocr_preview(source,max_size=(260,300)):
    """Load and resize an image safely for OCR preview without altering the source file."""
    p=Path(source)
    if not p.is_file(): raise ValueError(f'Image not found: {p.name}')
    try:
        with open_safe_image(p) as src:
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
    """OCR an image locally with a separately installed or USB-bundled Tesseract, English only."""
    import subprocess,shutil,os
    assert_safe_user_input(source)
    executable=os.environ.get('TESSERACT_CMD')
    if not executable:
        bundled_dir=settings_store.app_exe_dir()/'Tesseract-OCR'
        bundled_exe=bundled_dir/('tesseract.exe' if os.name=='nt' else 'tesseract')
        if bundled_exe.is_file():
            executable=str(bundled_exe)
    if not executable:
        executable=shutil.which('tesseract')
    if not executable and os.name=='nt':
        for env_key in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA'):
            base=os.environ.get(env_key)
            if not base: continue
            candidate=(Path(base)/'Programs'/'Tesseract-OCR'/'tesseract.exe') if env_key=='LOCALAPPDATA' else (Path(base)/'Tesseract-OCR'/'tesseract.exe')
            if candidate.exists(): executable=str(candidate); break
    if not executable: raise ValueError('Tesseract OCR was not found. Open Help & privacy and click the direct Tesseract OCR download link to install it (with English language data), or place a Tesseract-OCR folder beside CafeDesk.exe.')
    if Path(source).is_file():
        with open_safe_image(source):
            pass
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

GOOGLE_AI_STUDIO_KEYS_URL='https://aistudio.google.com/api-keys'
GOOGLE_GEMINI_OPENAI_URL='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'

AI_PROVIDERS={
    'Auto-detect from API key':'',
    'Google Gemini (aistudio.google.com)':'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
    'OpenRouter (openrouter.ai)':'https://openrouter.ai/api/v1/chat/completions',
    'OpenAI (api.openai.com)':'https://api.openai.com/v1/chat/completions',
    'Groq (api.groq.com)':'https://api.groq.com/openai/v1/chat/completions',
    'DeepSeek (api.deepseek.com)':'https://api.deepseek.com/chat/completions',
    'Custom OpenAI-compatible URL':'custom',
}

AI_DEFAULT_MODELS={
    'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions':'gemini-2.5-flash',
    'https://openrouter.ai/api/v1/chat/completions':'openai/gpt-4o-mini',
    'https://api.openai.com/v1/chat/completions':'gpt-4o-mini',
    'https://api.groq.com/openai/v1/chat/completions':'openai/gpt-oss-120b',
    'https://api.deepseek.com/chat/completions':'deepseek-chat',
}

# Groq shut these model IDs down for free/developer tiers (see
# https://console.groq.com/docs/deprecations). Requests to them now fail, so map
# any saved/typed old ID to Groq's recommended replacement.
GROQ_RETIRED_MODELS={
    'llama-3.3-70b-versatile':'openai/gpt-oss-120b',
    'llama-3.1-8b-instant':'openai/gpt-oss-20b',
    'qwen/qwen3-32b':'openai/gpt-oss-120b',
    'qwen/qwen3.6-27b':'qwen/qwen3.8-27b',
    'meta-llama/llama-4-scout-17b-16e-instruct':'openai/gpt-oss-120b',
    'moonshotai/kimi-k2-instruct-0905':'openai/gpt-oss-120b',
    'groq/compound':'openai/gpt-oss-120b',
    'groq/compound-mini':'openai/gpt-oss-20b',
}
# GPT-OSS models on Groq are reasoning models: hidden reasoning tokens count
# toward the completion budget, so tiny limits can leave the visible reply empty.
GROQ_REASONING_MIN_TOKENS=512

def detect_ai_provider(api_key:str, provider_choice:str='Auto-detect from API key', custom_url:str='')->tuple[str,str]:
    """Resolve `(endpoint_url, provider_label)` from key prefix or explicit provider choice."""
    key=(api_key or '').strip()
    choice=(provider_choice or 'Auto-detect from API key').strip()
    if choice=='Custom OpenAI-compatible URL':
        url=(custom_url or '').strip()
        if not url.startswith(('https://','http://localhost','http://127.0.0.1')):
            raise ValueError('Custom AI endpoint must start with https:// (or http://localhost for local Ollama/LM Studio).')
        # Secrets must never travel in a URL: they leak into error messages, logs,
        # proxy logs and browser history. Reject a URL that carries the key (or any
        # key-like token) instead of sending it and leaking it later. Check both the
        # raw URL and its percent-decoded form - an encoded key would otherwise
        # bypass the exact match and the key-pattern regex.
        if ai_safety.redact_secrets(url,key)!=url or ai_safety.redact_secrets(urllib.parse.unquote(url),key)!=url:
            raise ValueError('The custom AI endpoint URL must not contain the API key or another key-like token. Paste the endpoint without the key.')
        if url.rstrip('/').endswith('/v1'):
            url=url.rstrip('/')+'/chat/completions'
        return url,'Custom endpoint'
    mapped=AI_PROVIDERS.get(choice,'')
    if mapped:
        label_name=choice.split(' (')[0]
        return mapped,label_name
    # Auto-detect by key prefix
    if key.startswith('AIza'):
        return GOOGLE_GEMINI_OPENAI_URL,'Google Gemini'
    if key.startswith('sk-or-'):
        return 'https://openrouter.ai/api/v1/chat/completions','OpenRouter'
    if key.startswith('gsk_'):
        return 'https://api.groq.com/openai/v1/chat/completions','Groq'
    return 'https://api.openai.com/v1/chat/completions','OpenAI'

def normalize_ai_model(model:str, endpoint_url:str)->str:
    """Adjust model names to match the resolved provider (Gemini, OpenRouter, Groq, DeepSeek, OpenAI)."""
    m=(model or '').strip()
    if not m:
        return AI_DEFAULT_MODELS.get(endpoint_url,'gpt-4o-mini')
    if 'api.groq.com' in endpoint_url:
        groq_default=AI_DEFAULT_MODELS['https://api.groq.com/openai/v1/chat/completions']
        other_provider_defaults=set(AI_DEFAULT_MODELS.values())-{groq_default}
        other_provider_defaults.update({'openai/gpt-4o-mini','openrouter/auto'})
        if m in other_provider_defaults:
            return groq_default
        return GROQ_RETIRED_MODELS.get(m,m)
    if 'generativelanguage.googleapis.com' in endpoint_url:
        if m.startswith('google/'):
            m=m.split('/',1)[1]
        if m.startswith('models/'):
            m=m.split('/',1)[1]
        # If user left the default OpenAI/Groq/DeepSeek model in the box, auto-map to Gemini
        if not m.startswith(('gemini-','learnlm-','gemma-')):
            return AI_DEFAULT_MODELS[GOOGLE_GEMINI_OPENAI_URL]
        return m
    if 'openrouter.ai' in endpoint_url and '/' not in m:
        if m.startswith(('gpt-','o1','o3','o4')):
            return 'openai/'+m
        if m.startswith('claude-'):
            return 'anthropic/'+m
        if m.startswith('gemini-'):
            return 'google/'+m
    return m

def _extract_ai_error_detail(body:object)->str:
    """Extract a readable error message from OpenAI/OpenRouter dict or Google Gemini list-wrapped error JSON."""
    if isinstance(body,list) and body:
        body=body[0]
    if isinstance(body,dict):
        err=body.get('error')
        if isinstance(err,dict):
            return str(err.get('message') or err.get('status') or err.get('code') or '').strip()
        if isinstance(err,str):
            return err.strip()
        if body.get('message'):
            return str(body['message']).strip()
    return ''

def call_ai_chat(api_key:str, model:str, user_text:str, *, provider_choice:str='Auto-detect from API key', custom_url:str='', system_prompt:str|None=None, max_tokens:int|None=None, timeout:int=60)->tuple[str,str,str]:
    """Send a chat completion request to Google Gemini / OpenRouter / OpenAI / Groq / DeepSeek / compatible endpoint.

    Returns `(reply_text, provider_label, resolved_model)`. Never logs or echoes `api_key`.
    """
    import requests
    key=(api_key or '').strip()
    if not key:
        raise ValueError('Enter an API key in AI settings first.')
    if not (user_text or '').strip():
        raise ValueError('Enter or create a draft first.')
    endpoint,provider_label=detect_ai_provider(key,provider_choice,custom_url)
    resolved_model=normalize_ai_model(model,endpoint)
    headers={
        'Authorization':f'Bearer {key}',
        'Content-Type':'application/json',
    }
    if 'openrouter.ai' in endpoint:
        headers['HTTP-Referer']='https://github.com/Njwacky/internetcafe'
        headers['X-Title']='Cafe Desk Document Studio'
    sys_msg=system_prompt or ai_safety.AI_SYSTEM_PROMPT
    payload:dict={
        'model':resolved_model,
        'messages':[
            {'role':'system','content':sys_msg},
            {'role':'user','content':user_text},
        ],
    }
    if 'api.groq.com' in endpoint:
        # Groq documents `max_completion_tokens`; GPT-OSS needs reasoning headroom.
        if resolved_model.startswith('openai/gpt-oss'):
            payload['reasoning_effort']='low'
            if max_tokens is not None:
                payload['max_completion_tokens']=max(int(max_tokens),GROQ_REASONING_MIN_TOKENS)
        elif max_tokens is not None:
            payload['max_completion_tokens']=int(max_tokens)
    elif max_tokens is not None:
        payload['max_tokens']=int(max_tokens)
    if 'api.openai.com' in endpoint:
        payload['store']=False  # do not keep the completion in the OpenAI dashboard
    try:
        # No redirects: a redirect could re-send the customer's text to another server.
        resp=requests.post(endpoint,headers=headers,json=payload,timeout=timeout,allow_redirects=False)
    except requests.RequestException as exc:
        # The endpoint URL is user-supplied and may have carried a key; never let it
        # (or a chained requests traceback) reach a message, log or dialog unredacted.
        raise ValueError(ai_safety.redact_secrets(
            f'Could not reach {provider_label} ({endpoint}). Check your internet connection and try again.',key)) from exc

    try:
        body=resp.json()
    except ValueError:
        body={}

    if 300<=resp.status_code<400:
        raise ValueError(ai_safety.redact_secrets(
            f'{provider_label} tried to redirect the request elsewhere (HTTP {resp.status_code}). '
            f'It was blocked to protect customer data. Check the endpoint URL: {endpoint}.',key))
    if not resp.ok:
        detail=ai_safety.redact_secrets(_extract_ai_error_detail(body),key)
        hint=f': {detail[:220]}' if detail else '.'
        raise ValueError(f'{provider_label} request failed (HTTP {resp.status_code}){hint} Check the API key, model ("{resolved_model}"), and account quota/credits.')

    try:
        content=body['choices'][0]['message']['content']
    except (KeyError,IndexError,TypeError) as exc:
        raise ValueError(f'{provider_label} returned an unexpected response format.') from exc
    if not isinstance(content,str) or not content.strip():
        raise ValueError(f'{provider_label} returned an empty response. Try another model (for example openai/gpt-4o-mini or openrouter/auto).')
    if len(content)>ai_safety.MAX_AI_OUTPUT_CHARS:
        raise ValueError(f'{provider_label} returned an unexpectedly long response; it was discarded.')
    return content.strip(),provider_label,resolved_model

def improve_document_with_ai(api_key:str, model:str, text:str, *, provider_choice:str='Auto-detect from API key', custom_url:str='', timeout:int=60)->dict:
    """Privacy-safe "improve draft": hide personal details, call the AI, clean and restore.

    Returns a dict with `text` (final plain-text document), `provider`, `model`,
    `hidden` (summary of what was kept private) and `missing` (placeholders the AI
    dropped - the matching details may be missing from the result).
    """
    if not (text or '').strip():
        raise ValueError('Enter or create a draft first.')
    if len(text)>ai_safety.MAX_AI_INPUT_CHARS:
        raise ValueError(f'The draft is too long to send to AI ({len(text):,} characters; limit {ai_safety.MAX_AI_INPUT_CHARS:,}). Improve it in smaller parts.')
    protected=ai_safety.protect_personal_details(text)
    if not protected.text.strip():
        raise ValueError('Nothing to improve: the draft only contains the name/contact header.')
    reply,provider_label,resolved_model=call_ai_chat(
        api_key,model,ai_safety.wrap_document(protected.text),
        provider_choice=provider_choice,custom_url=custom_url,
        system_prompt=ai_safety.AI_SYSTEM_PROMPT,timeout=timeout,
    )
    cleaned=ai_safety.clean_ai_document(reply,original=protected.text)
    if not cleaned.strip():
        raise ValueError(f'{provider_label} returned no usable text. Try again or choose another model.')
    final,missing=ai_safety.restore_personal_details(cleaned,protected)
    return {'text':final,'provider':provider_label,'model':resolved_model,'hidden':protected.summary(),'missing':missing}

