"""Business cards & invitation cards — 13 designs rendered onto printable A4 PDF sheets."""
from pathlib import Path
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas
import pdf_fonts
import services

CARD_TYPES = ['Business card', 'Invitation card']

# Exact same 13 template names & visual language as CV_NAMES so users get a cohesive studio
CARD_TEMPLATES = [
    dict(name='Classic', accent='#28445F', bg='#FFFFFF', ink='#22303C', muted='#5C6B77', font='Times', style='center_rule'),
    dict(name='Modern', accent='#245B83', bg='#FFFFFF', ink='#1E2B38', muted='#5A6B7C', font='Helvetica', style='top_band'),
    dict(name='Minimal', accent='#42494C', bg='#FFFFFF', ink='#262B2E', muted='#687075', font='Helvetica', style='minimal'),
    dict(name='Executive', accent='#1F314A', bg='#F9FAFC', ink='#1B283B', muted='#56657A', font='Times', style='exec_band'),
    dict(name='Fresh', accent='#237F6C', bg='#F4FAF8', ink='#1C3A33', muted='#4E6E66', font='Helvetica', style='tint_rail'),
    dict(name='Creative', accent='#795082', bg='#FFFFFF', ink='#2B2230', muted='#6A5B70', font='Helvetica', style='corner_accent'),
    dict(name='Confident', accent='#A14939', bg='#FFFFFF', ink='#2E2220', muted='#6F5B57', font='Helvetica', style='bold_band'),
    dict(name='Contemporary', accent='#80643B', bg='#FCFBF8', ink='#2E271E', muted='#6E6250', font='Times', style='double_frame'),
    dict(name='Professional', accent='#375571', bg='#F5F8FB', ink='#203040', muted='#56697B', font='Helvetica', style='left_rail'),
    dict(name='Elegant', accent='#64516F', bg='#FDFCFE', ink='#2B2330', muted='#685C70', font='Times', style='ornate_frame'),
    dict(name='Modern Sidebar', accent='#245B83', bg='#FFFFFF', ink='#1E2B38', muted='#5A6B7C', font='Helvetica', style='left_sidebar', side_bg='#1D374D', side_ink='#FFFFFF', side_muted='#CFDDE8'),
    dict(name='Fresh Sidebar', accent='#237F6C', bg='#FFFFFF', ink='#1F3831', muted='#536F67', font='Helvetica', style='right_sidebar', side_bg='#EDF5F1', side_ink='#245042', side_muted='#547267'),
    dict(name='Compact Profile', accent='#344860', bg='#FFFFFF', ink='#222E3A', muted='#5B6875', font='Helvetica', style='compact_sidebar', side_bg='#283744', side_ink='#FFFFFF', side_muted='#D0DCE6'),
]

CARD_NAMES = [d['name'] for d in CARD_TEMPLATES]

BUSINESS_FIELDS = {
    'name': 'Full name',
    'role': 'Role / title (optional)',
    'company': 'Business / organisation',
    'phone': 'Phone / WhatsApp',
    'email': 'Email / website',
    'address': 'Address / location (optional)',
    'tagline': 'Tagline or services (optional)',
}

INVITATION_FIELDS = {
    'event': 'Event title (e.g. Birthday, Wedding)',
    'honoree': 'Hosted by / For whom',
    'date': 'Date',
    'time': 'Time',
    'venue': 'Venue / address',
    'rsvp': 'RSVP / contact details',
    'message': 'Short message / dress code (optional)',
}

SHEET_OPTIONS = {
    'Business card': [
        ('10 per A4 sheet (85 × 55 mm)', 'sheet', 85 * mm, 55 * mm, 2, 5),
        ('8 per A4 sheet (90 × 50 mm)', 'sheet', 90 * mm, 50 * mm, 2, 4),
        ('Single card proof (centred on A4)', 'single', 85 * mm, 55 * mm, 1, 1),
    ],
    'Invitation card': [
        ('2 per A4 sheet — A5 landscape (148 × 105 mm)', 'sheet', 148 * mm, 105 * mm, 1, 2),
        ('4 per A4 sheet — A6 postcard (105 × 148 mm)', 'sheet', 105 * mm, 148 * mm, 2, 2),
        ('1 per A4 sheet — Large invite (180 × 130 mm)', 'single', 180 * mm, 130 * mm, 1, 1),
    ],
}


def default_fields(kind):
    if kind == 'Invitation card':
        return {k: '' for k in INVITATION_FIELDS}
    return {k: '' for k in BUSINESS_FIELDS}


def _clean(val, fallback=''):
    s = str(val or '').strip()
    return ' '.join(s.split()) if s else fallback


def _wrap_lines(c, text, font_name, font_size, max_width, max_lines=3):
    text = str(text or '').strip()
    if not text or max_width <= 12:
        return []
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for raw in raw_lines:
        words = raw.split()
        cur = ''
        for word in words:
            trial = f'{cur} {word}'.strip()
            if c.stringWidth(trial, font_name, font_size) <= max_width:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                # Break an ultra-long token if needed
                while c.stringWidth(word, font_name, font_size) > max_width and len(word) > 3:
                    cut = max(1, int(len(word) * max_width / max(1.0, c.stringWidth(word, font_name, font_size))))
                    out.append(word[:cut])
                    word = word[cut:]
                cur = word
        if cur:
            out.append(cur)
    if len(out) > max_lines:
        out = out[:max_lines]
        last = out[-1]
        while len(last) > 3 and c.stringWidth(last + '…', font_name, font_size) > max_width:
            last = last[:-1]
        out[-1] = last.rstrip() + '…'
    return out


def _fit_size(c, text, font_name, preferred, min_size, max_width):
    sz = float(preferred)
    while sz > min_size and c.stringWidth(text, font_name, sz) > max_width:
        sz -= 0.5
    return sz


def _load_logo_reader(photo_path):
    if not photo_path:
        return None
    p = Path(photo_path)
    if not p.is_file():
        return None
    with services.open_safe_image(p) as src:
        im = ImageOps.exif_transpose(src).convert('RGBA')
        bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
        flat = Image.alpha_composite(bg, im).convert('RGB')
        flat.thumbnail((360, 360), Image.Resampling.LANCZOS)
        return ImageReader(flat)


def _draw_crop_marks(c, x, y, w, h):
    mark = 3.2 * mm
    gap = 1.2 * mm
    c.saveState()
    c.setStrokeColor(colors.HexColor('#9AA5B1'))
    c.setLineWidth(0.35)
    for cx, cy, dx, dy in (
        (x, y, -1, -1),
        (x + w, y, 1, -1),
        (x, y + h, -1, 1),
        (x + w, y + h, 1, 1),
    ):
        c.line(cx + dx * gap, cy, cx + dx * (gap + mark), cy)
        c.line(cx, cy + dy * gap, cx, cy + dy * (gap + mark))
    c.restoreState()


def _draw_business_card(c, x, y, w, h, design, data, logo_reader):
    accent = colors.HexColor(design['accent'])
    bg = colors.HexColor(design['bg'])
    ink = colors.HexColor(design['ink'])
    muted = colors.HexColor(design['muted'])
    reg, bold = pdf_fonts.font_pair(design['font'])
    italic = reg
    style = design['style']

    name = _clean(data.get('name'), 'Your Name')
    role = _clean(data.get('role'), '')
    company = _clean(data.get('company'), 'Business Name')
    phone = _clean(data.get('phone'), '012 345 6789')
    email = _clean(data.get('email'), 'hello@example.com')
    address = _clean(data.get('address'), '')
    tagline = _clean(data.get('tagline'), '')

    c.saveState()
    p = c.beginPath()
    p.rect(x, y, w, h)
    c.clipPath(p, stroke=0, fill=0)

    c.setFillColor(bg)
    c.rect(x, y, w, h, fill=1, stroke=0)

    pad = 5.5 * mm

    if style in ('left_sidebar', 'right_sidebar', 'compact_sidebar'):
        side_w = w * (0.34 if style != 'compact_sidebar' else 0.31)
        is_right = style == 'right_sidebar'
        sx = x + w - side_w if is_right else x
        mx = x if is_right else x + side_w
        mw = w - side_w
        c.setFillColor(colors.HexColor(design['side_bg']))
        c.rect(sx, y, side_w, h, fill=1, stroke=0)
        c.setFillColor(accent)
        stripe_x = sx - 1.2 * mm if is_right else sx + side_w
        c.rect(stripe_x, y, 1.2 * mm, h, fill=1, stroke=0)

        # Sidebar: logo or monogram + company / tagline
        side_ink = colors.HexColor(design['side_ink'])
        side_muted = colors.HexColor(design['side_muted'])
        top_sy = y + h - pad
        if logo_reader:
            lsz = min(side_w - 8 * mm, 16 * mm)
            c.drawImage(logo_reader, sx + (side_w - lsz) / 2, top_sy - lsz, width=lsz, height=lsz, preserveAspectRatio=True, anchor='c')
            top_sy -= lsz + 3.5 * mm
        else:
            initials = ''.join(part[0].upper() for part in (company or name).split()[:2]) or 'CD'
            r_circ = 6.2 * mm
            cx_i = sx + side_w / 2
            cy_i = top_sy - r_circ - 1 * mm
            c.setFillColor(accent)
            c.circle(cx_i, cy_i, r_circ, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont(bold, 10)
            c.drawCentredString(cx_i, cy_i - 3.3, initials)
            top_sy = cy_i - r_circ - 4 * mm

        c.setFillColor(side_ink)
        comp_lines = _wrap_lines(c, company.upper(), bold, 6.8, side_w - 6 * mm, max_lines=3)
        c.setFont(bold, 6.8)
        for ln in comp_lines:
            c.drawCentredString(sx + side_w / 2, top_sy, ln)
            top_sy -= 8.5
        if tagline:
            top_sy -= 2
            c.setFillColor(side_muted)
            c.setFont(reg, 6.2)
            for ln in _wrap_lines(c, tagline, reg, 6.2, side_w - 6 * mm, max_lines=3):
                c.drawCentredString(sx + side_w / 2, top_sy, ln)
                top_sy -= 7.6

        # Main area: name, role, divider, contact details
        tx = mx + 5 * mm
        tw = mw - 9.5 * mm
        cy = y + h - pad - 3 * mm
        nsz = _fit_size(c, name, bold, 13.0, 9.0, tw)
        c.setFillColor(ink)
        c.setFont(bold, nsz)
        c.drawString(tx, cy, name)
        cy -= nsz * 1.1
        if role:
            c.setFillColor(accent)
            c.setFont(bold, 7.6)
            for ln in _wrap_lines(c, role, bold, 7.6, tw, max_lines=1):
                c.drawString(tx, cy, ln)
                cy -= 9.5
        c.setStrokeColor(accent)
        c.setLineWidth(0.8)
        c.line(tx, cy - 1, tx + min(tw, 24 * mm), cy - 1)
        cy -= 11

        c.setFillColor(ink)
        c.setFont(reg, 7.3)
        contacts = [v for v in (phone, email, address) if v]
        for item in contacts:
            for ln in _wrap_lines(c, item, reg, 7.3, tw, max_lines=2):
                if cy < y + 3.5 * mm:
                    break
                c.drawString(tx, cy, ln)
                cy -= 9.2
    else:
        # Standard 10 designs (0..9)
        if style in ('top_band', 'exec_band', 'bold_band'):
            band_h = h * (0.36 if style == 'bold_band' else 0.31)
            c.setFillColor(accent)
            c.rect(x, y + h - band_h, w, band_h, fill=1, stroke=0)
            if style == 'exec_band':
                c.setFillColor(colors.HexColor('#C8A96E'))
                c.rect(x, y + h - band_h - 1.1 * mm, w, 1.1 * mm, fill=1, stroke=0)
            # Company / tagline in band
            bx = x + pad
            bw = w - 2 * pad - (15 * mm if logo_reader else 0)
            if logo_reader:
                lsz = min(band_h - 4 * mm, 12 * mm)
                c.drawImage(logo_reader, x + w - pad - lsz, y + h - (band_h + lsz) / 2, width=lsz, height=lsz, preserveAspectRatio=True, anchor='c')
            c.setFillColor(colors.white)
            csz = _fit_size(c, company.upper(), bold, 8.8, 6.8, bw)
            c.setFont(bold, csz)
            c.drawString(bx, y + h - band_h * 0.48, company.upper())
            if tagline:
                c.setFillColor(colors.HexColor('#E5ECF4'))
                c.setFont(italic, 6.8)
                for ln in _wrap_lines(c, tagline, italic, 6.8, bw, max_lines=1):
                    c.drawString(bx, y + h - band_h * 0.78, ln)

            cy = y + h - band_h - 5.8 * mm
            tw = w - 2 * pad
            nsz = _fit_size(c, name, bold, 12.8, 9.0, tw)
            c.setFillColor(ink)
            c.setFont(bold, nsz)
            c.drawString(x + pad, cy, name)
            cy -= 10.0
            if role:
                c.setFillColor(accent)
                c.setFont(bold, 7.6)
                for ln in _wrap_lines(c, role, bold, 7.6, tw, max_lines=1):
                    c.drawString(x + pad, cy, ln)
                    cy -= 9.5
            cy -= 2
            c.setFillColor(muted)
            c.setFont(reg, 7.2)
            for item in [v for v in (phone, email, address) if v]:
                for ln in _wrap_lines(c, item, reg, 7.2, tw, max_lines=1):
                    if cy < y + 3.2 * mm:
                        break
                    c.drawString(x + pad, cy, ln)
                    cy -= 8.8
        elif style in ('center_rule', 'double_frame', 'ornate_frame'):
            if style == 'double_frame':
                c.setStrokeColor(accent)
                c.setLineWidth(1.0)
                c.rect(x + 2.5 * mm, y + 2.5 * mm, w - 5 * mm, h - 5 * mm, fill=0, stroke=1)
                c.setLineWidth(0.4)
                c.rect(x + 3.7 * mm, y + 3.7 * mm, w - 7.4 * mm, h - 7.4 * mm, fill=0, stroke=1)
            elif style == 'ornate_frame':
                c.setStrokeColor(accent)
                c.setLineWidth(0.8)
                c.rect(x + 3 * mm, y + 3 * mm, w - 6 * mm, h - 6 * mm, fill=0, stroke=1)
                c.setFillColor(accent)
                c.rect(x + w - 2.2 * mm, y, 2.2 * mm, h, fill=1, stroke=0)
            else:
                c.setFillColor(accent)
                c.rect(x, y, w, 1.8 * mm, fill=1, stroke=0)

            cx = x + w / 2
            tw = w - 12 * mm
            cy = y + h - 6.5 * mm
            if logo_reader:
                lsz = 10 * mm
                c.drawImage(logo_reader, cx - lsz / 2, cy - lsz + 1.5 * mm, width=lsz, height=lsz, preserveAspectRatio=True, anchor='c')
                cy -= lsz + 1.5 * mm
            c.setFillColor(accent)
            c.setFont(bold, 7.5)
            for ln in _wrap_lines(c, company.upper(), bold, 7.5, tw, max_lines=1):
                c.drawCentredString(cx, cy, ln)
                cy -= 11
            nsz = _fit_size(c, name, bold, 13.5, 9.2, tw)
            c.setFillColor(ink)
            c.setFont(bold, nsz)
            c.drawCentredString(cx, cy, name)
            cy -= 9.5
            if role:
                c.setFillColor(muted)
                c.setFont(italic, 7.8)
                for ln in _wrap_lines(c, role, italic, 7.8, tw, max_lines=1):
                    c.drawCentredString(cx, cy, ln)
                    cy -= 8.5
            c.setStrokeColor(accent)
            c.setLineWidth(0.7)
            c.line(cx - 14 * mm, cy, cx + 14 * mm, cy)
            cy -= 9.0
            c.setFillColor(ink)
            c.setFont(reg, 7.1)
            details = [v for v in ('  •  '.join([p for p in (phone, email) if p]), address, tagline) if v]
            for dline in details:
                for ln in _wrap_lines(c, dline, reg, 7.1, tw, max_lines=1):
                    if cy < y + 4 * mm:
                        break
                    c.drawCentredString(cx, cy, ln)
                    cy -= 8.4
        else:
            # minimal, tint_rail, corner_accent, left_rail
            if style in ('tint_rail', 'left_rail'):
                c.setFillColor(accent)
                c.rect(x, y, 2.5 * mm, h, fill=1, stroke=0)
            elif style == 'corner_accent':
                c.setFillColor(accent)
                tri = c.beginPath()
                tri.moveTo(x + w - 18 * mm, y + h)
                tri.lineTo(x + w, y + h)
                tri.lineTo(x + w, y + h - 18 * mm)
                tri.close()
                c.drawPath(tri, fill=1, stroke=0)
                c.rect(x, y, w, 1.6 * mm, fill=1, stroke=0)
            else:
                c.setStrokeColor(accent)
                c.setLineWidth(0.8)
                c.line(x + pad, y + h - 14 * mm, x + w - pad, y + h - 14 * mm)

            lx = x + pad + (1.8 * mm if style in ('tint_rail', 'left_rail') else 0)
            tw = w - (lx - x) - pad - (13 * mm if logo_reader else 0)
            if logo_reader:
                lsz = 11.5 * mm
                c.drawImage(logo_reader, x + w - pad - lsz, y + h - pad - lsz, width=lsz, height=lsz, preserveAspectRatio=True, anchor='c')

            cy = y + h - pad - 2.5 * mm
            c.setFillColor(accent)
            c.setFont(bold, 7.5)
            for ln in _wrap_lines(c, company.upper(), bold, 7.5, tw, max_lines=1):
                c.drawString(lx, cy, ln)
            cy -= 13
            nsz = _fit_size(c, name, bold, 13.2, 9.0, tw)
            c.setFillColor(ink)
            c.setFont(bold, nsz)
            c.drawString(lx, cy, name)
            cy -= 9.8
            if role:
                c.setFillColor(muted)
                c.setFont(bold, 7.6)
                for ln in _wrap_lines(c, role, bold, 7.6, tw, max_lines=1):
                    c.drawString(lx, cy, ln)
                    cy -= 9.2
            if tagline:
                c.setFillColor(muted)
                c.setFont(italic, 6.9)
                for ln in _wrap_lines(c, tagline, italic, 6.9, w - (lx - x) - pad, max_lines=1):
                    c.drawString(lx, cy, ln)
                    cy -= 8.8
            cy = min(cy - 1, y + 18 * mm)
            c.setFillColor(ink)
            c.setFont(reg, 7.2)
            for item in [v for v in (phone, email, address) if v]:
                for ln in _wrap_lines(c, item, reg, 7.2, w - (lx - x) - pad, max_lines=1):
                    if cy < y + 3.5 * mm:
                        break
                    c.drawString(lx, cy, ln)
                    cy -= 8.6

    # Subtle outer card border so white cards remain crisp in preview & print
    c.setStrokeColor(colors.HexColor('#D5DCE4'))
    c.setLineWidth(0.45)
    c.rect(x, y, w, h, fill=0, stroke=1)
    c.restoreState()


def _draw_invitation_card(c, x, y, w, h, design, data, logo_reader):
    accent = colors.HexColor(design['accent'])
    bg = colors.HexColor(design['bg'])
    ink = colors.HexColor(design['ink'])
    muted = colors.HexColor(design['muted'])
    reg, bold = pdf_fonts.font_pair(design['font'])
    italic = reg
    style = design['style']
    portrait = h > w

    event = _clean(data.get('event'), 'Special Celebration')
    honoree = _clean(data.get('honoree'), 'Hosted by Friends & Family')
    date_val = _clean(data.get('date'), 'Saturday, 28 November 2026')
    time_val = _clean(data.get('time'), '14:00')
    venue = _clean(data.get('venue'), '123 Garden Venue, Main Road')
    rsvp = _clean(data.get('rsvp'), '')
    message = _clean(data.get('message'), 'We would be delighted to celebrate this special occasion with you.')

    scale = min(w / (148 * mm), h / (105 * mm)) if not portrait else min(w / (105 * mm), h / (148 * mm))
    scale = max(0.78, min(1.25, scale))

    c.saveState()
    p = c.beginPath()
    p.rect(x, y, w, h)
    c.clipPath(p, stroke=0, fill=0)

    c.setFillColor(bg)
    c.rect(x, y, w, h, fill=1, stroke=0)

    pad = 8 * mm * scale

    if style in ('left_sidebar', 'right_sidebar', 'compact_sidebar') and not portrait:
        side_w = w * (0.33 if style != 'compact_sidebar' else 0.30)
        is_right = style == 'right_sidebar'
        sx = x + w - side_w if is_right else x
        mx = x if is_right else x + side_w
        mw = w - side_w
        c.setFillColor(colors.HexColor(design['side_bg']))
        c.rect(sx, y, side_w, h, fill=1, stroke=0)
        c.setFillColor(accent)
        c.rect(sx - 1.5 * mm if is_right else sx + side_w, y, 1.5 * mm, h, fill=1, stroke=0)

        side_ink = colors.HexColor(design['side_ink'])
        side_muted = colors.HexColor(design['side_muted'])
        sy = y + h - pad - 2 * mm
        if logo_reader:
            lsz = min(side_w - 12 * mm, 24 * mm)
            c.drawImage(logo_reader, sx + (side_w - lsz) / 2, sy - lsz, width=lsz, height=lsz, preserveAspectRatio=True, anchor='c')
            sy -= lsz + 5 * mm
        c.setFillColor(side_muted)
        c.setFont(bold, 7.5 * scale)
        c.drawCentredString(sx + side_w / 2, sy, 'WHEN')
        sy -= 12 * scale
        c.setFillColor(side_ink)
        c.setFont(bold, 9.2 * scale)
        for ln in _wrap_lines(c, date_val, bold, 9.2 * scale, side_w - 8 * mm, max_lines=2):
            c.drawCentredString(sx + side_w / 2, sy, ln)
            sy -= 11.5 * scale
        if time_val:
            c.setFont(reg, 8.8 * scale)
            for ln in _wrap_lines(c, time_val, reg, 8.8 * scale, side_w - 8 * mm, max_lines=1):
                c.drawCentredString(sx + side_w / 2, sy, ln)
                sy -= 13 * scale
        sy -= 4 * scale
        c.setFillColor(side_muted)
        c.setFont(bold, 7.5 * scale)
        c.drawCentredString(sx + side_w / 2, sy, 'WHERE')
        sy -= 12 * scale
        c.setFillColor(side_ink)
        c.setFont(reg, 8.3 * scale)
        for ln in _wrap_lines(c, venue, reg, 8.3 * scale, side_w - 8 * mm, max_lines=3):
            c.drawCentredString(sx + side_w / 2, sy, ln)
            sy -= 10.5 * scale

        # Main invitation content
        tx = mx + pad
        tw = mw - 2 * pad
        cy = y + h - pad - 4 * mm
        c.setFillColor(accent)
        c.setFont(bold, 8.2 * scale)
        c.drawString(tx, cy, 'YOU ARE CORDIALLY INVITED')
        cy -= 18 * scale
        esz = _fit_size(c, event, bold, 19 * scale, 12 * scale, tw)
        c.setFillColor(ink)
        c.setFont(bold, esz)
        for ln in _wrap_lines(c, event, bold, esz, tw, max_lines=2):
            c.drawString(tx, cy, ln)
            cy -= esz * 1.15
        if honoree:
            c.setFillColor(accent)
            c.setFont(italic, 10.5 * scale)
            for ln in _wrap_lines(c, honoree, italic, 10.5 * scale, tw, max_lines=2):
                c.drawString(tx, cy, ln)
                cy -= 13 * scale
        c.setStrokeColor(accent)
        c.setLineWidth(1.0)
        c.line(tx, cy - 2, tx + min(tw, 35 * mm), cy - 2)
        cy -= 14 * scale
        if message:
            c.setFillColor(muted)
            c.setFont(reg, 9.2 * scale)
            for ln in _wrap_lines(c, message, reg, 9.2 * scale, tw, max_lines=3):
                c.drawString(tx, cy, ln)
                cy -= 12.2 * scale
        if rsvp:
            c.setFillColor(ink)
            c.setFont(bold, 8.3 * scale)
            for ln in _wrap_lines(c, 'RSVP: ' + rsvp, bold, 8.3 * scale, tw, max_lines=2):
                c.drawString(tx, max(y + pad * 0.7, cy - 4 * scale), ln)
                cy -= 10.5 * scale
    else:
        # Framed / banded / centered invitation layouts
        cx = x + w / 2
        tw = w - 2.4 * pad
        cy = y + h - pad

        if style in ('top_band', 'exec_band', 'bold_band', 'left_sidebar', 'compact_sidebar'):
            bh = h * 0.25
            c.setFillColor(accent)
            c.rect(x, y + h - bh, w, bh, fill=1, stroke=0)
            c.setFillColor(colors.HexColor('#E8EEF5'))
            c.setFont(bold, 7.6 * scale)
            c.drawCentredString(cx, y + h - bh * 0.36, 'YOU ARE INVITED')
            esz = _fit_size(c, event, bold, 17.5 * scale, 11 * scale, tw)
            c.setFillColor(colors.white)
            c.setFont(bold, esz)
            c.drawCentredString(cx, y + h - bh * 0.75, event)
            cy = y + h - bh - 7 * mm * scale
        else:
            if style in ('double_frame', 'center_rule', 'ornate_frame', 'right_sidebar'):
                c.setStrokeColor(accent)
                c.setLineWidth(1.1)
                c.rect(x + 4 * mm, y + 4 * mm, w - 8 * mm, h - 8 * mm, fill=0, stroke=1)
                if style in ('double_frame', 'ornate_frame'):
                    c.setLineWidth(0.45)
                    c.rect(x + 5.6 * mm, y + 5.6 * mm, w - 11.2 * mm, h - 11.2 * mm, fill=0, stroke=1)
            elif style in ('tint_rail', 'left_rail'):
                c.setFillColor(accent)
                c.rect(x, y, 3.5 * mm, h, fill=1, stroke=0)
                c.rect(x + w - 3.5 * mm, y, 3.5 * mm, h, fill=1, stroke=0)
            elif style == 'corner_accent':
                c.setFillColor(accent)
                for dx, dy in ((0, 1), (1, 0)):
                    c.rect(x, y + (h - 2.2 * mm if dy else 0), w, 2.2 * mm, fill=1, stroke=0)

            cy -= 3 * mm * scale
            if logo_reader:
                lsz = 14 * mm * scale
                c.drawImage(logo_reader, cx - lsz / 2, cy - lsz, width=lsz, height=lsz, preserveAspectRatio=True, anchor='c')
                cy -= lsz + 3.5 * mm * scale
            c.setFillColor(accent)
            c.setFont(bold, 7.8 * scale)
            c.drawCentredString(cx, cy, 'YOU ARE CORDIALLY INVITED TO')
            cy -= 16 * scale
            esz = _fit_size(c, event, bold, 19.5 * scale, 12 * scale, tw)
            c.setFillColor(ink)
            c.setFont(bold, esz)
            for ln in _wrap_lines(c, event, bold, esz, tw, max_lines=2):
                c.drawCentredString(cx, cy, ln)
                cy -= esz * 1.12

        if honoree:
            c.setFillColor(accent)
            c.setFont(italic, 10.5 * scale)
            for ln in _wrap_lines(c, honoree, italic, 10.5 * scale, tw, max_lines=2):
                c.drawCentredString(cx, cy, ln)
                cy -= 13 * scale

        c.setStrokeColor(accent)
        c.setLineWidth(0.75)
        c.line(cx - 22 * mm, cy - 1, cx + 22 * mm, cy - 1)
        cy -= 13 * scale

        when_line = '   •   '.join([v for v in (date_val, time_val) if v])
        if when_line:
            c.setFillColor(ink)
            c.setFont(bold, 9.6 * scale)
            for ln in _wrap_lines(c, when_line, bold, 9.6 * scale, tw, max_lines=2):
                c.drawCentredString(cx, cy, ln)
                cy -= 12 * scale
        if venue:
            c.setFillColor(ink)
            c.setFont(reg, 9.0 * scale)
            for ln in _wrap_lines(c, venue, reg, 9.0 * scale, tw, max_lines=2):
                c.drawCentredString(cx, cy, ln)
                cy -= 11.5 * scale
        if message:
            cy -= 2 * scale
            c.setFillColor(muted)
            c.setFont(italic, 8.6 * scale)
            for ln in _wrap_lines(c, message, italic, 8.6 * scale, tw, max_lines=2):
                if cy < y + 8 * mm:
                    break
                c.drawCentredString(cx, cy, ln)
                cy -= 10.8 * scale
        if rsvp and cy >= y + 6 * mm:
            c.setFillColor(accent)
            c.setFont(bold, 8.0 * scale)
            for ln in _wrap_lines(c, 'RSVP: ' + rsvp, bold, 8.0 * scale, tw, max_lines=1):
                c.drawCentredString(cx, max(y + 5.5 * mm, cy - 2 * scale), ln)

    c.setStrokeColor(colors.HexColor('#D5DCE4'))
    c.setLineWidth(0.45)
    c.rect(x, y, w, h, fill=0, stroke=1)
    c.restoreState()


def render_card_pdf(destination, kind, template_index, fields, sheet_index=0, photo_path=None, crop_marks=True):
    """Render a Business card or Invitation card PDF (multi-up A4 sheet or single proof)."""
    services.assert_safe_user_output(destination)
    if photo_path:
        services._different([photo_path], destination)
    if kind not in CARD_TYPES:
        raise ValueError('Choose Business card or Invitation card.')
    design = CARD_TEMPLATES[max(0, min(len(CARD_TEMPLATES) - 1, int(template_index)))]
    layouts = SHEET_OPTIONS[kind]
    _, mode, cw, ch, cols, rows = layouts[max(0, min(len(layouts) - 1, int(sheet_index)))]
    logo_reader = _load_logo_reader(photo_path)

    pw, ph = A4
    c = pdf_canvas.Canvas(str(destination), pagesize=A4)
    c.setTitle(f'{kind} — {design["name"]}')

    gap_x = 4 * mm if cols > 1 else 0
    gap_y = 4 * mm if rows > 1 else 0
    if kind == 'Invitation card' and rows > 1:
        gap_x = 6 * mm if cols > 1 else 0
        gap_y = 8 * mm

    total_w = cols * cw + (cols - 1) * gap_x
    total_h = rows * ch + (rows - 1) * gap_y
    start_x = (pw - total_w) / 2
    start_y = (ph - total_h) / 2

    drawer = _draw_business_card if kind == 'Business card' else _draw_invitation_card
    for r in range(rows):
        for col in range(cols):
            cx = start_x + col * (cw + gap_x)
            cy = start_y + (rows - 1 - r) * (ch + gap_y)
            drawer(c, cx, cy, cw, ch, design, fields or {}, logo_reader)
            if crop_marks:
                _draw_crop_marks(c, cx, cy, cw, ch)

    sans_reg, _ = pdf_fonts.font_pair('Helvetica')
    c.setFont(sans_reg, 7.5)
    c.setFillColor(colors.HexColor('#7A8694'))
    c.drawCentredString(pw / 2, 6 * mm, f'{kind.upper()}  •  {design["name"].upper()}  •  Print at 100% / Actual size')
    c.showPage()
    c.save()


def render_single_card_pdf(destination, kind, template_index, fields, photo_path=None):
    """Render just a single cropped card page at exact card dimensions for crisp UI preview."""
    services.assert_safe_user_output(destination)
    if photo_path:
        services._different([photo_path], destination)
    if kind not in CARD_TYPES:
        raise ValueError('Choose Business card or Invitation card.')
    design = CARD_TEMPLATES[max(0, min(len(CARD_TEMPLATES) - 1, int(template_index)))]
    cw, ch = (85 * mm, 55 * mm) if kind == 'Business card' else (148 * mm, 105 * mm)
    logo_reader = _load_logo_reader(photo_path)
    c = pdf_canvas.Canvas(str(destination), pagesize=(cw, ch))
    drawer = _draw_business_card if kind == 'Business card' else _draw_invitation_card
    drawer(c, 0, 0, cw, ch, design, fields or {}, logo_reader)
    c.showPage()
    c.save()
