"""Ten CV designs rendered with flowing ReportLab paragraphs and page numbers."""
import io
from xml.sax.saxutils import escape
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT,TA_CENTER,TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Image as PDFImage,HRFlowable,Table,TableStyle
import pdf_fonts
import services

# Design decisions vary typography, header, section treatment and page accents.
DESIGNS=[
 dict(name='Classic',accent='#28445F',font='Times',header='center',sections='rule',rail='none',size=22),
 dict(name='Modern',accent='#245B83',font='Helvetica',header='band',sections='number',rail='left',size=24),
 dict(name='Minimal',accent='#42494C',font='Helvetica',header='plain',sections='simple',rail='none',size=21),
 dict(name='Executive',accent='#1F314A',font='Times',header='band',sections='rule',rail='right',size=24),
 dict(name='Fresh',accent='#237F6C',font='Helvetica',header='tint',sections='band',rail='left',size=22),
 dict(name='Creative',accent='#795082',font='Helvetica',header='split',sections='number',rail='right',size=25),
 dict(name='Confident',accent='#A14939',font='Helvetica',header='band',sections='band',rail='none',size=23),
 dict(name='Contemporary',accent='#80643B',font='Times',header='split',sections='number',rail='none',size=23),
 dict(name='Professional',accent='#375571',font='Helvetica',header='tint',sections='rule',rail='left',size=21),
 dict(name='Elegant',accent='#64516F',font='Times',header='center',sections='band',rail='right',size=25),
]
SECTIONS={'PROFESSIONAL PROFILE','PROFILE','SUMMARY','SKILLS','EXPERIENCE','WORK EXPERIENCE','EDUCATION','QUALIFICATIONS','CERTIFICATIONS','PROJECTS','REFERENCES','LANGUAGES','VOLUNTEERING','ACHIEVEMENTS'}

def render(text,destination,index,photo_path=None):
    if index>=10: return render_sidebar(text,destination,min(index,12),photo_path)
    if not text.strip(): raise ValueError('Create or write a CV first.')
    design=DESIGNS[max(0,min(9,index))]; accent=colors.HexColor(design['accent']); dark=colors.HexColor('#24303A'); tint=colors.Color(.94,.96,.97)
    regular,bold=pdf_fonts.font_pair(design['font'])
    sans_reg,sans_bold=pdf_fonts.font_pair('Helvetica')
    cvlines=text.splitlines(); name=cvlines[0].strip() if cvlines else ''; contact=cvlines[1].strip() if len(cvlines)>1 else ''; bodylines=cvlines[2:]
    w=A4[0]-110; band=design['header']=='band'; centered=design['header']=='center'
    heading_color=colors.white if band else accent; contact_color=colors.HexColor('#E4EBF2') if band else colors.HexColor('#5F6D78')
    name_style=ParagraphStyle('cv-name',fontName=bold,fontSize=design['size'],leading=design['size']*1.25,textColor=heading_color,alignment=TA_CENTER if centered else TA_LEFT,spaceAfter=3,splitLongWords=1)
    contact_style=ParagraphStyle('cv-contact',fontName=regular,fontSize=9.5,leading=13,textColor=contact_color,alignment=TA_CENTER if centered else TA_LEFT,spaceAfter=0,splitLongWords=1)
    body_style=ParagraphStyle('cv-body',fontName=regular,fontSize=10.5,leading=15.7,textColor=dark,spaceAfter=4,splitLongWords=1,allowWidows=0,allowOrphans=0)
    section_style=ParagraphStyle('cv-section',fontName=bold,fontSize=10,leading=14,textColor=accent,spaceBefore=0,spaceAfter=0,splitLongWords=1)
    small_style=ParagraphStyle('cv-label',fontName=sans_bold,fontSize=8,leading=11,textColor=contact_color,spaceAfter=7)
    photo=None
    if photo_path:
        with services.open_safe_image(photo_path) as src:
            image=ImageOps.fit(ImageOps.exif_transpose(src).convert('RGB'),(360,360),method=Image.Resampling.LANCZOS); buf=io.BytesIO();image.save(buf,format='PNG');buf.seek(0)
        photo=PDFImage(buf,width=64,height=64)
    story=[]
    # Very long header fields bypass an unsplittable table; no text is truncated.
    if len(name)+len(contact)>320:
        long_name=ParagraphStyle('long-name',parent=name_style,textColor=accent)
        long_contact=ParagraphStyle('long-contact',parent=contact_style,textColor=dark)
        story += [Paragraph(escape(name),long_name),Paragraph(escape(contact),long_contact),Spacer(1,8)]
        if photo: story.extend([photo,Spacer(1,8)])
    else:
        parts=[]
        if design['header'] in ('band','tint','split'): parts.append(Paragraph('CURRICULUM VITAE',small_style))
        parts += [Paragraph(escape(name),name_style),Paragraph(escape(contact),contact_style)]
        cells=[parts,photo] if photo else [parts]; widths=[w-84,84] if photo else [w]
        if photo and index in (4,7): cells=[photo,parts];widths=[84,w-84]
        if design['header']=='split' and not photo:
            parts_left=parts; badge=Paragraph('CV',ParagraphStyle('badge',fontName=bold,fontSize=31,leading=37,textColor=accent,alignment=TA_RIGHT));cells=[parts_left,badge];widths=[w-70,70]
        table=Table([cells],colWidths=widths)
        table_style=[('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),12 if band or design['header']=='tint' else 0),('RIGHTPADDING',(0,0),(-1,-1),12 if band or design['header']=='tint' else 0),('TOPPADDING',(0,0),(-1,-1),14 if band or design['header']=='tint' else 4),('BOTTOMPADDING',(0,0),(-1,-1),14 if band or design['header']=='tint' else 8)]
        if band:table_style.append(('BACKGROUND',(0,0),(-1,-1),accent))
        if design['header']=='tint':table_style.append(('BACKGROUND',(0,0),(-1,-1),tint))
        table.setStyle(TableStyle(table_style));story.append(table)
        if design['header'] in ('center','plain','split'):story.append(HRFlowable(width='40%' if centered else '100%',thickness=1 if index!=5 else 2,color=accent,spaceBefore=3,spaceAfter=2))
    story.append(Spacer(1,10));count=0
    for line in bodylines:
        if not line.strip(): story.append(Spacer(1,5));continue
        trimmed=line.strip()
        # Recognise common headings and user-added uppercase section headings.
        is_heading=trimmed in SECTIONS or (len(trimmed)<=65 and any(ch.isalpha() for ch in trimmed) and trimmed==trimmed.upper() and not trimmed.endswith('.'))
        if is_heading:
            count+=1;story.append(Spacer(1,8));p=Paragraph(escape(trimmed),section_style)
            if design['sections']=='number':
                number=Paragraph(f'{count:02d}',ParagraphStyle('num',fontName='Helvetica-Bold',fontSize=9,leading=13,textColor=accent))
                section=Table([[number,p]],colWidths=[30,w-30]);section.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,0),(-1,-1),.5,accent)]));section.keepWithNext=True;story.append(section)
            elif design['sections']=='band':
                section=Table([[p]],colWidths=[w]);section.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),tint),('LINEBEFORE',(0,0),(0,0),3,accent),('LEFTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));section.keepWithNext=True;story.append(section)
            else:
                p.keepWithNext=True;story.append(p)
                if design['sections']=='rule':
                    rule=HRFlowable(width='100%',thickness=.6,color=accent,spaceBefore=2,spaceAfter=3);rule.keepWithNext=True;story.append(rule)
        else:story.append(Paragraph(escape(line),body_style))
    def page(canvas,doc):
        canvas.saveState();page_w,page_h=A4
        if design['rail']!='none':
            canvas.setFillColor(accent);canvas.rect(0 if design['rail']=='left' else page_w-5,0,5,page_h,fill=1,stroke=0)
        canvas.setStrokeColor(colors.HexColor('#d5dce2'));canvas.setLineWidth(.5);canvas.line(55,39,page_w-55,39)
        canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#6f7a83'))
        # Avoid copying an arbitrarily long name into the page footer.
        canvas.drawString(55,25,'CURRICULUM VITAE');canvas.drawRightString(page_w-55,25,f'Page {doc.page}')
        if doc.page>1:
            canvas.setFillColor(accent);canvas.setFont('Helvetica-Bold',8);canvas.drawString(55,page_h-28,'CURRICULUM VITAE  |  CONTINUED')
        canvas.restoreState()
    SimpleDocTemplate(destination,pagesize=A4,leftMargin=55,rightMargin=55,topMargin=50,bottomMargin=55,title='Curriculum vitae').build(story,onFirstPage=page,onLaterPages=page)

# These designs put contact information and an optional photo to the side on
# the first page. Following pages use the full printable width for reliable flow.
def render_sidebar(text,destination,index,photo_path=None):
    from reportlab.platypus import BaseDocTemplate,PageTemplate,Frame,NextPageTemplate
    from reportlab.lib.utils import ImageReader
    from reportlab.lib.colors import HexColor
    lines=text.splitlines(); name=lines[0] if lines else ''; contact=lines[1] if len(lines)>1 else ''; bodylines=lines[2:]
    # Put short skills in the side panel. Longer/ambiguous skills stay in the
    # flowing story, so text is never clipped or silently lost.
    side_skills=None; skills_at=[i for i,line in enumerate(bodylines) if line.strip()=='SKILLS']
    if len(skills_at)==1:
        start=skills_at[0]; end=start+1
        while end<len(bodylines):
            v=bodylines[end].strip()
            if v in SECTIONS or (len(v)<=65 and any(ch.isalpha() for ch in v) and v==v.upper() and not v.endswith('.')): break
            end+=1
        entries=[line.strip() for line in bodylines[start+1:end] if line.strip()]
        if 1<=len(entries)<=3 and sum(len(x) for x in entries)<=180:
            side_skills=entries; bodylines=bodylines[:start]+bodylines[end:]
    pw,ph=A4; right=index==11; compact=index==12
    accent=HexColor('#237F6C' if right else '#344860' if compact else '#245B83')
    sidebar_bg=HexColor('#EDF5F1') if right else HexColor('#283744') if compact else HexColor('#1D374D')
    sidebar_ink=HexColor('#295748') if right else colors.white
    muted_ink=HexColor('#587469') if right else HexColor('#CFDDE8')
    gutter=185; margin=45; first_x=margin if right else gutter+24; first_width=pw-gutter-margin-24
    common_left=55; common_width=pw-110
    fs=9.2 if compact else 10.1; leading=12.3 if compact else 15
    sans_reg,sans_bold=pdf_fonts.font_pair('Helvetica')
    body=ParagraphStyle('sidebody',fontName=sans_reg,fontSize=fs,leading=leading,textColor=HexColor('#26323C'),spaceAfter=2 if compact else 4,splitLongWords=1,allowOrphans=0,allowWidows=0)
    title=ParagraphStyle('sidename',fontName=sans_bold,fontSize=20 if compact else 23,leading=25 if compact else 29,textColor=accent,spaceAfter=3,splitLongWords=1)
    heading=ParagraphStyle('sideheading',fontName=sans_bold,fontSize=9.4,leading=13,textColor=accent,spaceBefore=7,spaceAfter=4,splitLongWords=1,keepWithNext=True)
    sideheading=ParagraphStyle('sidebarheading',fontName=sans_bold,fontSize=8,leading=11,textColor=sidebar_ink,spaceAfter=4)
    sidetext=ParagraphStyle('sidebartext',fontName=sans_reg,fontSize=9,leading=13,textColor=muted_ink,splitLongWords=1)
    # The full contact is shown at the side if it can fit safely. Long contacts
    # remain in the flowing main story and are never truncated.
    use_side_contact=len(contact)<=300
    pic=None
    if photo_path:
        with services.open_safe_image(photo_path) as src:
            image=ImageOps.fit(ImageOps.exif_transpose(src).convert('RGB'),(320,320),method=Image.Resampling.LANCZOS);pic=ImageReader(image)
    def first_page(c,doc):
        c.saveState(); sx=pw-gutter if right else 0; c.setFillColor(sidebar_bg);c.rect(sx,0,gutter,ph,fill=1,stroke=0)
        text_x=sx+24; inner=gutter-48; y=ph-60
        if pic:
            c.drawImage(pic,text_x,y-85,width=85,height=85); y-=105
        head=Paragraph('CONTACT',sideheading); _,h=head.wrap(inner,ph);head.drawOn(c,text_x,y-h);y-=h+6
        if use_side_contact and contact.strip():
            para=Paragraph(escape(contact),sidetext);_,h=para.wrap(inner,ph);para.drawOn(c,text_x,y-h);y-=h+24
        if side_skills:
            head=Paragraph('SKILLS',sideheading);_,h=head.wrap(inner,ph);head.drawOn(c,text_x,y-h);y-=h+6
            para=Paragraph('<br/>'.join(escape(line) for line in side_skills),sidetext);_,h=para.wrap(inner,ph);para.drawOn(c,text_x,y-h)
        c.setFont('Helvetica',8);c.setFillColor(HexColor('#74818C'));c.drawRightString((pw-gutter-20) if right else (pw-45),25,f'Page {doc.page}')
        c.restoreState()
    def later_page(c,doc):
        c.saveState();c.setStrokeColor(HexColor('#D5DCE2'));c.setLineWidth(.5);c.line(55,39,pw-55,39);c.setFont('Helvetica',8);c.setFillColor(HexColor('#74818C'));c.drawString(55,25,'CURRICULUM VITAE');c.drawRightString(pw-55,25,f'Page {doc.page}');c.setFillColor(accent);c.setFont('Helvetica-Bold',8);c.drawString(55,ph-28,'CURRICULUM VITAE  |  CONTINUED');c.restoreState()
    doc=BaseDocTemplate(destination,pagesize=A4,title='Curriculum vitae',leftMargin=55,rightMargin=55,topMargin=48,bottomMargin=55)
    first=Frame(first_x,55,first_width,ph-105,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0,id='first')
    later=Frame(common_left,55,common_width,ph-105,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0,id='later')
    doc.addPageTemplates([PageTemplate(id='First',frames=[first],onPage=first_page),PageTemplate(id='Later',frames=[later],onPage=later_page)])
    story=[NextPageTemplate('Later'),Paragraph('CURRICULUM VITAE',ParagraphStyle('side-label',fontName='Helvetica-Bold',fontSize=8,leading=11,textColor=accent,spaceAfter=8)),Paragraph(escape(name),title)]
    if not use_side_contact and contact.strip():story.append(Paragraph(escape(contact),body))
    story.extend([HRFlowable(width='100%',thickness=1.1,color=accent,spaceBefore=4,spaceAfter=6)])
    for line in bodylines:
        if not line.strip():story.append(Spacer(1,3 if compact else 5));continue
        trim=line.strip(); is_head=trim in SECTIONS or (len(trim)<=65 and any(x.isalpha() for x in trim) and trim==trim.upper() and not trim.endswith('.'))
        if is_head:
            item=Paragraph(escape(trim),heading);item.keepWithNext=True;story.append(item)
        else:story.append(Paragraph(escape(line),body))
    doc.build(story)
