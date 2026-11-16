"""Cross-platform TrueType Unicode font registration with bundled DejaVu fallback."""
import sys
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_REGISTERED = None


def _bundled_font_tuple():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    fdir = root / 'assets' / 'fonts'
    return (
        str(fdir / 'DejaVuSans.ttf'),
        str(fdir / 'DejaVuSans-Bold.ttf'),
        str(fdir / 'DejaVuSerif.ttf'),
        str(fdir / 'DejaVuSerif-Bold.ttf'),
    )


def _candidate_families():
    return [
        # 1. Bundled DejaVu Unicode TTFs inside assets/fonts/ (works offline on every OS & PyInstaller USB build)
        _bundled_font_tuple(),
        # 2. Linux system DejaVu
        (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf',
        ),
        # 3. Windows Segoe UI + Times New Roman
        (
            r'C:\Windows\Fonts\segoeui.ttf',
            r'C:\Windows\Fonts\segoeuib.ttf',
            r'C:\Windows\Fonts\times.ttf',
            r'C:\Windows\Fonts\timesbd.ttf',
        ),
        # 4. Windows Arial + Georgia
        (
            r'C:\Windows\Fonts\arial.ttf',
            r'C:\Windows\Fonts\arialbd.ttf',
            r'C:\Windows\Fonts\georgia.ttf',
            r'C:\Windows\Fonts\georgiab.ttf',
        ),
        # 5. macOS Supplemental Unicode / Arial
        (
            '/System/Library/Fonts/Supplemental/Arial.ttf',
            '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
            '/System/Library/Fonts/Supplemental/Times New Roman.ttf',
            '/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf',
        ),
    ]


def ensure_fonts():
    """Register Unicode TrueType fonts (bundled DejaVu or OS TTF), falling back to Base-14 only if unavailable."""
    global _REGISTERED
    if _REGISTERED is not None:
        return _REGISTERED
    sans, sans_bold, serif, serif_bold = 'Helvetica', 'Helvetica-Bold', 'Times-Roman', 'Times-Bold'
    for reg_path, bold_path, sreg_path, sbold_path in _candidate_families():
        p_reg, p_bold = Path(reg_path), Path(bold_path)
        if p_reg.is_file() and p_bold.is_file():
            try:
                pdfmetrics.registerFont(TTFont('CafeSans', str(p_reg)))
                pdfmetrics.registerFont(TTFont('CafeSans-Bold', str(p_bold)))
                sans, sans_bold = 'CafeSans', 'CafeSans-Bold'
                p_sreg, p_sbold = Path(sreg_path), Path(sbold_path)
                if p_sreg.is_file() and p_sbold.is_file():
                    pdfmetrics.registerFont(TTFont('CafeSerif', str(p_sreg)))
                    pdfmetrics.registerFont(TTFont('CafeSerif-Bold', str(p_sbold)))
                    serif, serif_bold = 'CafeSerif', 'CafeSerif-Bold'
                else:
                    serif, serif_bold = sans, sans_bold
                pdfmetrics.registerFontFamily(
                    'CafeSans',
                    normal='CafeSans',
                    bold='CafeSans-Bold',
                    italic='CafeSans',
                    boldItalic='CafeSans-Bold',
                )
                pdfmetrics.registerFontFamily(
                    'CafeSerif',
                    normal=serif,
                    bold=serif_bold,
                    italic=serif,
                    boldItalic=serif_bold,
                )
                break
            except Exception:
                sans, sans_bold, serif, serif_bold = 'Helvetica', 'Helvetica-Bold', 'Times-Roman', 'Times-Bold'
    _REGISTERED = {
        'sans': sans,
        'sans_bold': sans_bold,
        'serif': serif,
        'serif_bold': serif_bold,
        'unicode': sans == 'CafeSans',
    }
    return _REGISTERED


def font_pair(family='Helvetica'):
    """Return `(regular_name, bold_name)` mapped to Unicode TTF when available."""
    f = ensure_fonts()
    if family in ('Times', 'Times-Roman', 'Serif'):
        return f['serif'], f['serif_bold']
    return f['sans'], f['sans_bold']
