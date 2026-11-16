"""Printable and AcroForm-fillable A4 PDF Form Creator for internet cafés and service desks."""
import re
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
import pdf_fonts

FORM_TEMPLATES = {
    'General Application Form': {
        'subtitle': 'Walk-in application / registration form',
        'instructions': 'Complete all applicable sections clearly in block letters or fill digitally.',
        'signature_block': True,
        'fields': [
            {'label': 'Full Name(s) & Surname', 'type': 'text', 'value': ''},
            {'label': 'ID / Passport Number', 'type': 'text', 'value': ''},
            {'label': 'Date of Birth (YYYY-MM-DD)', 'type': 'date', 'value': ''},
            {'label': 'Phone / WhatsApp Number', 'type': 'text', 'value': ''},
            {'label': 'Email Address', 'type': 'text', 'value': ''},
            {'label': 'Residential / Postal Address', 'type': 'multiline', 'value': ''},
            {'label': 'Position / Programme / Service Applied For', 'type': 'text', 'value': ''},
            {'label': 'Highest Qualification / Supporting Summary', 'type': 'multiline', 'value': ''},
            {'label': 'I confirm that the information supplied above is true and complete.', 'type': 'checkbox', 'checked': False},
        ],
    },
    'Affidavit / Sworn Declaration': {
        'subtitle': 'Statement under oath / affirmation',
        'instructions': 'Sign only in the presence of an authorised Commissioner of Oaths / Notary where legally required.',
        'signature_block': True,
        'fields': [
            {'label': 'Deponent Full Name & Surname', 'type': 'text', 'value': ''},
            {'label': 'Identity / Passport Number', 'type': 'text', 'value': ''},
            {'label': 'Residential Address', 'type': 'multiline', 'value': ''},
            {'label': 'Contact Telephone Number', 'type': 'text', 'value': ''},
            {'label': 'Statement / Declaration Facts', 'type': 'multiline', 'value': 'I, the undersigned, do hereby make oath / affirm and state that:\n'},
            {'label': 'Place Signed (City / Town)', 'type': 'text', 'value': ''},
            {'label': 'Commissioner of Oaths Name, Rank & Stamp Area', 'type': 'multiline', 'value': ''},
            {'label': 'I know and understand the contents of this declaration.', 'type': 'checkbox', 'checked': False},
        ],
    },
    'Consent / Authorization Form': {
        'subtitle': 'Written permission & representative authorization',
        'instructions': 'Specify who is granting permission, who is authorised, and the exact scope or validity period.',
        'signature_block': True,
        'fields': [
            {'label': 'Person Granting Consent (Full Name)', 'type': 'text', 'value': ''},
            {'label': 'ID / Passport Number', 'type': 'text', 'value': ''},
            {'label': 'Contact Phone & Email', 'type': 'text', 'value': ''},
            {'label': 'Authorised Person / Organisation', 'type': 'text', 'value': ''},
            {'label': 'Authorised Person ID / Reference', 'type': 'text', 'value': ''},
            {'label': 'Scope / Purpose of Consent or Collection', 'type': 'multiline', 'value': ''},
            {'label': 'Valid From — Until (Dates)', 'type': 'date', 'value': ''},
            {'label': 'I voluntarily grant the authorization described above.', 'type': 'checkbox', 'checked': False},
        ],
    },
    'Customer / Walk-in Intake Form': {
        'subtitle': 'Service desk job sheet & customer intake record',
        'instructions': 'Record customer instructions, deadlines, and collection details clearly.',
        'signature_block': True,
        'fields': [
            {'label': 'Customer Name', 'type': 'text', 'value': ''},
            {'label': 'Phone / WhatsApp', 'type': 'text', 'value': ''},
            {'label': 'Date Received', 'type': 'date', 'value': ''},
            {'label': 'Service Requested (Typing / CV / Scan / Print / Binding)', 'type': 'text', 'value': ''},
            {'label': 'Detailed Job Notes & Page Count', 'type': 'multiline', 'value': ''},
            {'label': 'Quoted Amount / Deposit', 'type': 'text', 'value': ''},
            {'label': 'Customer consented to temporary processing of supplied documents.', 'type': 'checkbox', 'checked': False},
        ],
    },
    'Custom Blank Form': {
        'subtitle': 'Custom printable & fillable form',
        'instructions': 'Please complete the fields below clearly.',
        'signature_block': True,
        'fields': [
            {'label': 'Full Name', 'type': 'text', 'value': ''},
            {'label': 'Contact Number / Email', 'type': 'text', 'value': ''},
            {'label': 'Date', 'type': 'date', 'value': ''},
            {'label': 'Details / Notes', 'type': 'multiline', 'value': ''},
        ],
    },
}

FORM_TEMPLATE_NAMES = list(FORM_TEMPLATES.keys())
FIELD_TYPES = ('text', 'multiline', 'checkbox', 'date', 'signature')
MAX_FORM_FIELDS = 60


def default_form_spec(template_name='General Application Form', org_name=''):
    tpl = FORM_TEMPLATES.get(template_name) or FORM_TEMPLATES['General Application Form']
    return {
        'template': template_name if template_name in FORM_TEMPLATES else 'General Application Form',
        'title': template_name if template_name != 'Custom Blank Form' else 'Custom Form',
        'subtitle': tpl['subtitle'],
        'organization': str(org_name or '').strip(),
        'instructions': tpl['instructions'],
        'fillable': True,
        'signature_block': bool(tpl.get('signature_block', True)),
        'fields': [dict(f) for f in tpl['fields']],
    }


def validate_form_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError('Invalid form specification.')
    title = ' '.join(str(spec.get('title') or '').split())
    if not title:
        raise ValueError('Enter a form title.')
    if len(title) > 120:
        raise ValueError('Form title must be 120 characters or fewer.')
    raw_fields = spec.get('fields')
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError('Add at least one field to the form.')
    if len(raw_fields) > MAX_FORM_FIELDS:
        raise ValueError(f'Use at most {MAX_FORM_FIELDS} fields per form.')
    cleaned_fields = []
    for idx, item in enumerate(raw_fields, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Field {idx} is invalid.')
        label = ' '.join(str(item.get('label') or '').split())
        if not label:
            raise ValueError(f'Field {idx}: enter a field label.')
        if len(label) > 160:
            raise ValueError(f'Field {idx}: label must be 160 characters or fewer.')
        ftype = str(item.get('type') or 'text').strip().lower()
        if ftype not in FIELD_TYPES:
            raise ValueError(f'Field {idx}: unsupported field type "{ftype}".')
        val = str(item.get('value') or '')
        if len(val) > 5000:
            raise ValueError(f'Field {idx}: pre-filled value is too long.')
        cleaned_fields.append({
            'label': label,
            'type': ftype,
            'value': val,
            'checked': bool(item.get('checked', False)),
        })
    return {
        'title': title,
        'subtitle': ' '.join(str(spec.get('subtitle') or '').split())[:160],
        'organization': ' '.join(str(spec.get('organization') or '').split())[:100],
        'instructions': str(spec.get('instructions') or '').strip()[:600],
        'fillable': bool(spec.get('fillable', True)),
        'signature_block': bool(spec.get('signature_block', True)),
        'fields': cleaned_fields,
    }


def _wrap_text(c, text, font_name, font_size, max_w):
    lines = []
    for raw in str(text or '').splitlines():
        words = raw.split()
        if not words:
            lines.append('')
            continue
        cur = ''
        for w in words:
            trial = f'{cur} {w}'.strip()
            if c.stringWidth(trial, font_name, font_size) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


def _safe_acro_name(prefix, idx):
    return f'{prefix}_{idx:03d}'


def render_form_pdf(spec, destination):
    """Render an A4 printable (and optionally AcroForm interactive fillable) PDF to `destination`."""
    data = validate_form_spec(spec)
    reg_font, bold_font = pdf_fonts.font_pair('Helvetica')
    pw, ph = A4
    margin_x = 18 * mm
    top_margin = 16 * mm
    bottom_margin = 18 * mm
    content_w = pw - 2 * margin_x

    accent = colors.HexColor('#245B83')
    ink = colors.HexColor('#1E293B')
    muted = colors.HexColor('#5A6878')
    border_col = colors.HexColor('#94A3B8')
    box_bg = colors.HexColor('#F8FAFC')

    c = pdf_canvas.Canvas(str(destination), pagesize=A4)
    c.setTitle(data['title'])
    acro = c.acroForm if data['fillable'] else None

    page_num = 1

    def draw_footer():
        c.saveState()
        c.setStrokeColor(colors.HexColor('#CBD5E1'))
        c.setLineWidth(0.5)
        c.line(margin_x, 12.5 * mm, pw - margin_x, 12.5 * mm)
        c.setFont(reg_font, 8)
        c.setFillColor(muted)
        left_note = data['organization'] if data['organization'] else data['title']
        c.drawString(margin_x, 8.2 * mm, left_note[:70])
        c.drawRightString(pw - margin_x, 8.2 * mm, f'Page {page_num}')
        c.restoreState()

    def start_new_page():
        nonlocal page_num
        draw_footer()
        c.showPage()
        page_num += 1
        c.setFillColor(accent)
        c.setFont(bold_font, 8.5)
        c.drawString(margin_x, ph - 12 * mm, f"{data['title'].upper()}  •  CONTINUED")
        c.setStrokeColor(colors.HexColor('#CBD5E1'))
        c.setLineWidth(0.5)
        c.line(margin_x, ph - 14 * mm, pw - margin_x, ph - 14 * mm)
        return ph - 21 * mm

    # Header band on page 1
    y = ph - top_margin
    c.setFillColor(accent)
    c.roundRect(margin_x, y - 22 * mm, content_w, 22 * mm, 4, fill=1, stroke=0)
    hx = margin_x + 5 * mm
    if data['organization']:
        c.setFillColor(colors.HexColor('#CFE3F2'))
        c.setFont(bold_font, 8.5)
        c.drawString(hx, y - 6.2 * mm, data['organization'].upper())
        c.setFillColor(colors.white)
        c.setFont(bold_font, 15.5)
        c.drawString(hx, y - 13.2 * mm, data['title'][:68])
    else:
        c.setFillColor(colors.white)
        c.setFont(bold_font, 16.5)
        c.drawString(hx, y - 10.5 * mm, data['title'][:68])
    if data['subtitle']:
        c.setFillColor(colors.HexColor('#E2EEF7'))
        c.setFont(reg_font, 9.2)
        c.drawString(hx, y - 18.8 * mm, data['subtitle'][:95])
    y -= 27 * mm

    if data['instructions']:
        inst_lines = _wrap_text(c, data['instructions'], reg_font, 9.2, content_w - 8 * mm)
        box_h = max(9 * mm, (len(inst_lines) * 12.5) + 10)
        c.setFillColor(colors.HexColor('#EFF6FF'))
        c.setStrokeColor(colors.HexColor('#BFDBFE'))
        c.setLineWidth(0.6)
        c.roundRect(margin_x, y - box_h, content_w, box_h, 3, fill=1, stroke=1)
        c.setFillColor(ink)
        c.setFont(reg_font, 9.2)
        ty = y - 11.5
        for ln in inst_lines:
            c.drawString(margin_x + 4 * mm, ty, ln)
            ty -= 12.5
        y -= box_h + 5 * mm

    # Note: ReportLab AcroForm standard fields use Base-14 font names ('Helvetica') reliably across PDF viewers
    for idx, field in enumerate(data['fields'], 1):
        ftype = field['type']
        lbl = field['label']
        val = field['value']
        label_lines = _wrap_text(c, lbl, bold_font, 9.5, content_w - (10 * mm if ftype == 'checkbox' else 0))

        if ftype == 'checkbox':
            needed = max(9 * mm, len(label_lines) * 12 + 4)
            if y - needed < bottom_margin:
                y = start_new_page()
            box_size = 13
            bx = margin_x
            by = y - box_size - 1
            if acro is not None:
                acro.checkbox(
                    name=_safe_acro_name('chk', idx),
                    tooltip=lbl[:120],
                    checked=field['checked'],
                    x=bx,
                    y=by,
                    size=box_size,
                    buttonStyle='check',
                    borderColor=border_col,
                    fillColor=box_bg,
                    textColor=ink,
                    borderWidth=0.8,
                )
            else:
                c.setFillColor(box_bg)
                c.setStrokeColor(border_col)
                c.setLineWidth(0.8)
                c.rect(bx, by, box_size, box_size, fill=1, stroke=1)
                if field['checked']:
                    c.setFillColor(ink)
                    c.setFont(bold_font, 10)
                    c.drawString(bx + 2.5, by + 2.5, 'X')
            c.setFillColor(ink)
            c.setFont(reg_font, 9.6)
            ty = y - 10.5
            for ln in label_lines:
                c.drawString(bx + box_size + 7, ty, ln)
                ty -= 12
            y = min(by - 6, ty - 2)
            continue

        box_h = 56 if ftype == 'multiline' else (34 if ftype == 'signature' else 22)
        field_w = content_w * 0.48 if ftype == 'date' else content_w
        needed = len(label_lines) * 12 + box_h + 12
        if y - needed < bottom_margin:
            y = start_new_page()

        c.setFillColor(ink)
        c.setFont(bold_font, 9.5)
        for ln in label_lines:
            c.drawString(margin_x, y - 10, ln)
            y -= 12
        y -= 2

        by = y - box_h
        if acro is not None and ftype != 'signature':
            flags = 'multiline' if ftype == 'multiline' else ''
            acro.textfield(
                name=_safe_acro_name('fld', idx),
                tooltip=lbl[:120],
                value=val,
                x=margin_x,
                y=by,
                width=field_w,
                height=box_h,
                borderStyle='solid',
                borderColor=border_col,
                fillColor=box_bg,
                textColor=ink,
                borderWidth=0.75,
                fontName='Helvetica',
                fontSize=10,
                fieldFlags=flags,
            )
        else:
            c.setFillColor(box_bg)
            c.setStrokeColor(border_col)
            c.setLineWidth(0.75)
            c.roundRect(margin_x, by, field_w, box_h, 2, fill=1, stroke=1)
            if ftype == 'signature':
                c.setStrokeColor(colors.HexColor('#64748B'))
                c.setDash(2, 2)
                c.line(margin_x + 8, by + 9, margin_x + field_w - 8, by + 9)
                c.setDash()
                c.setFillColor(muted)
                c.setFont(reg_font, 7.5)
                c.drawString(margin_x + 8, by + 2, 'Sign above')
            if val.strip():
                c.setFillColor(ink)
                c.setFont(reg_font, 9.8)
                vlines = _wrap_text(c, val, reg_font, 9.8, field_w - 10)
                vy = y - 13.5
                for vln in vlines[: (4 if ftype == 'multiline' else 1)]:
                    c.drawString(margin_x + 5, vy, vln)
                    vy -= 12

        y = by - 9

    if data['signature_block']:
        sig_needed = 28 * mm
        if y - sig_needed < bottom_margin:
            y = start_new_page()
        y -= 4 * mm
        c.setStrokeColor(colors.HexColor('#CBD5E1'))
        c.setLineWidth(0.5)
        c.line(margin_x, y, pw - margin_x, y)
        y -= 15 * mm
        sig_w = content_w * 0.58
        date_w = content_w * 0.34
        date_x = margin_x + content_w - date_w
        c.setStrokeColor(ink)
        c.setLineWidth(0.75)
        c.line(margin_x, y, margin_x + sig_w, y)
        c.line(date_x, y, date_x + date_w, y)
        c.setFillColor(muted)
        c.setFont(bold_font, 8.5)
        c.drawString(margin_x, y - 11, 'Signature')
        c.drawString(date_x, y - 11, 'Date (YYYY-MM-DD)')
        if acro is not None:
            acro.textfield(
                name='form_signature_date',
                tooltip='Date signed',
                value='',
                x=date_x,
                y=y + 2,
                width=date_w,
                height=18,
                borderStyle='solid',
                borderColor=border_col,
                fillColor=box_bg,
                textColor=ink,
                borderWidth=0.6,
                fontName='Helvetica',
                fontSize=9.5,
            )

    draw_footer()
    c.save()
