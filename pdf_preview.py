"""Bounded, local PDF page thumbnails for the visual extraction picker."""
import math
from pathlib import Path
import pypdfium2 as pdfium

BATCH_SIZE=6
MAX_PAGES=2000

def fingerprint(path):
    info=Path(path).stat()
    return (info.st_size,info.st_mtime_ns)

def render_batch(path,start=0,expected=None):
    """Return (total, [(one-based page, copied PIL image), ...], fingerprint).

    Opens one document per batch, renders only six pages, and caps bitmap dimensions.
    The visual selector does not write customer pages to disk.
    """
    before=fingerprint(path)
    if expected is not None and before!=expected:raise ValueError('The PDF changed. Close the selector and choose the PDF again.')
    try:doc=pdfium.PdfDocument(str(path))
    except Exception as exc:raise ValueError('Could not preview this PDF. It may be damaged or password-protected.') from exc
    try:
        total=len(doc)
        if not 1<=total<=MAX_PAGES:raise ValueError(f'This visual selector supports PDFs with 1–{MAX_PAGES} pages.')
        if type(start)!=int or start<0 or start>=total:raise ValueError('Invalid preview page range.')
        result=[]
        for i in range(start,min(start+BATCH_SIZE,total)):
            page=doc[i]
            try:
                width,height=page.get_size()
                if not all(math.isfinite(x) and x>0 for x in (width,height)):raise ValueError('Invalid PDF page dimensions.')
                scale=min(1.5,180/width,250/height)
                bitmap=page.render(scale=scale)
                try:image=bitmap.to_pil().copy()
                finally:bitmap.close()
                result.append((i+1,image))
            finally:page.close()
        if fingerprint(path)!=before:raise ValueError('The PDF changed while it was being previewed. Choose it again.')
        return total,result,before
    finally:doc.close()

def selection_spec(pages):
    if not pages:raise ValueError('Select at least one page.')
    if any(type(p)!=int or p<1 or p>MAX_PAGES for p in pages):raise ValueError('Invalid selected page.')
    return ', '.join(str(p) for p in sorted(set(pages)))

def render_page(path,number,expected):
    if fingerprint(path)!=expected:raise ValueError('The PDF changed. Choose it again.')
    try:doc=pdfium.PdfDocument(str(path))
    except Exception as exc:raise ValueError('Could not preview this PDF.') from exc
    try:
        if type(number)!=int or not 1<=number<=len(doc):raise ValueError('Invalid page number.')
        page=doc[number-1]
        try:
            w,h=page.get_size()
            if not all(math.isfinite(v) and v>0 for v in (w,h)):raise ValueError('Invalid PDF page dimensions.')
            bitmap=page.render(scale=min(2,610/w,700/h))
            try:image=bitmap.to_pil().copy()
            finally:bitmap.close()
        finally:page.close()
        if fingerprint(path)!=expected:raise ValueError('The PDF changed. Choose it again.')
        return image
    finally:doc.close()
