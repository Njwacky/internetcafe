"""Career document templates; no network or GUI dependencies."""
import json

NAMES = ['Classic','Modern','Minimal','Executive','Fresh','Creative','Confident','Contemporary','Professional','Elegant']
CV_NAMES = NAMES + ['Modern Sidebar', 'Fresh Sidebar', 'Compact Profile']
COLORS = ['#23405d','#325f83','#444444','#172e49','#257868','#825185','#a34736','#886b3f','#334d68','#625275']
CAREER_ENTRY_FIELDS = ('skills','experience','education')
_CAREER_ENTRIES_PREFIX = '\x1eCD-CAREER-ENTRIES-V1:'
SUMMARIES = [
 '{name} is a professional interested in {role}. Key strengths include {skills}.',
 '{role} candidate with strengths in {skills}. Background: {experience}.',
 'Bringing {skills} to opportunities in {role}. Experience includes {experience}.',
 'With experience in {experience}, {name} offers {skills} for a {role} opportunity.',
 'Motivated {role} candidate ready to learn and contribute, with skills in {skills}.',
 'Career changer pursuing {role}, offering transferable strengths in {skills} and experience in {experience}.',
 'Collaborative {role} candidate with skills in {skills} and a background in {experience}.',
 'Committed to growing in {role}, with a foundation in {skills} and experience in {experience}.',
 '{name} brings {skills} and experience in {experience} to a potential {role} position.',
 '{name} is a candidate for {role} with a background in {experience} and skills that include {skills}.']
GENERAL_SUMMARIES = [
 '{name} is a reliable, motivated job seeker ready for new opportunities. Key strengths include {skills}.',
 'Dedicated candidate with strengths in {skills}. Background: {experience}.',
 'Bringing {skills} to new work opportunities. Experience includes {experience}.',
 'With experience in {experience}, {name} offers {skills} and a dependable work ethic.',
 'Motivated candidate ready to learn and contribute, with skills in {skills}.',
 'Adaptable professional offering transferable strengths in {skills} and experience in {experience}.',
 'Collaborative team member with skills in {skills} and a background in {experience}.',
 'Committed to learning and growing on the job, with a foundation in {skills} and experience in {experience}.',
 '{name} brings {skills} and experience in {experience} to new employment opportunities.',
 '{name} is a dependable candidate with a background in {experience} and skills that include {skills}.']
OPENINGS = [
 'I am applying for the {role} position at {company}.',
 'Please consider my application for {role} at {company}.',
 'I am excited to apply for {role} at {company}.',
 'I am writing to express my interest in joining {company} as a {role}.',
 'I am eager to develop my career as a {role} with {company}.',
 'I am applying for the {role} role at {company} as I take the next step in my career.',
 'I am interested in the {role} opportunity at {company} and the chance to contribute.',
 'I would like to join {company} as a {role}.',
 'The {role} position at {company} appeals to me as an opportunity to contribute and grow.',
 'Please accept this letter as my application for the position of {role} at {company}.']
CLOSINGS = [
 'I would welcome the opportunity to discuss how I can contribute to your team.',
 'Thank you for considering my application; I would value an interview.',
 'I look forward to discussing the experience I could bring to the role.',
 'I would be pleased to share more about my motivation and suitability.',
 'Thank you for considering my application and my willingness to learn.',
 'I would welcome the chance to explain how my transferable skills fit your needs.',
 'I hope to discuss how my experience and skills can support your goals.',
 'I would appreciate an opportunity to meet and discuss the position.',
 'I look forward to the possibility of learning and contributing with your team.',
 'Thank you for your time and consideration. I look forward to hearing from you.']
INTRODUCTIONS = [
 'Hello, my name is {name}. I am interested in {role} and have skills in {skills}.',
 'My name is {name}, and I am interested in the {role} opportunity. My background includes {experience}.',
 'Hi, I am {name}. I am exploring opportunities in {role} and would be glad to connect.',
 'Hello, I am {name}. I bring skills in {skills} and am looking for opportunities in {role}.',
 'I am {name}, with experience in {experience}. I am interested in a {role} position.',
 'Hello, I am {name}. I am keen to learn and build my career in {role}, using my skills in {skills}.',
 'I am {name}, and I am moving toward a career in {role}. I bring transferable skills in {skills}.',
 'Good day. My name is {name}, and I would like to introduce myself as a candidate for {role}.',
 'Hi, I am {name}. I enjoy using my skills in {skills} and am interested in working as a {role}.',
 'Hello, I am {name}. I am interested in contributing to {company} in a {role} position.']
CATEGORIES=['Résumé / CV','Cover letter','Professional summary','Introduction']
LABELS={
    'name':'Full name',
    'contact':'Contact details',
    'gender':'Gender (optional)',
    'id_number':'ID number (optional — include only if needed)',
    'role':'Desired role or field (optional)',
    'company':'Target company (optional)',
    'skills':'Skills',
    'experience':'Experience',
    'education':'Education'
}

def field_labels_for(category):
    """Return user-facing field labels and which fields apply to `category`."""
    labels=dict(LABELS)
    if category in (CATEGORIES[0], CATEGORIES[2]):
        labels['role']='Desired role or field (optional — leave blank for a general CV)'
    elif category == CATEGORIES[1]:
        labels['role']='Role or vacancy (optional — leave blank for a general application)'
        labels['company']='Company or organisation (optional)'
    else:
        labels['role']='Desired role or field (optional)'
        labels['company']='Company or organisation (optional)'
    return labels

def uses_company_field(category):
    """CVs and standalone professional summaries are general career documents and do not need a target company."""
    return category in (CATEGORIES[1], CATEGORIES[3])

def parse_career_entries(value):
    """Decode repeatable career-section entries; treat older plain text as one entry."""
    raw = str(value or '')
    if raw.startswith(_CAREER_ENTRIES_PREFIX):
        try:
            entries = json.loads(raw[len(_CAREER_ENTRIES_PREFIX):])
        except json.JSONDecodeError:
            entries = None
        if isinstance(entries, list) and all(
            isinstance(item, dict)
            and isinstance(item.get('heading'), str)
            and isinstance(item.get('details'), str)
            for item in entries
        ):
            return entries
    return [{'heading':'','details':raw}]

def serialize_career_entries(entries):
    """Encode repeatable career-section entries for existing project field storage."""
    normalized = [
        {
            'heading': str(item.get('heading') or '').strip(),
            'details': str(item.get('details') or '').strip(),
        }
        for item in entries
    ]
    normalized = [item for item in normalized if item['heading'] or item['details']]
    if not normalized:
        return ''
    return _CAREER_ENTRIES_PREFIX + json.dumps(normalized, ensure_ascii=False, separators=(',',':'))

def format_career_entries(value):
    """Make saved entries readable in generated career documents."""
    blocks = []
    for item in parse_career_entries(value):
        heading = item['heading'].strip()
        details = item['details'].strip()
        lines = []
        if heading:
            lines.append(heading.upper())
        if details:
            lines.append(details)
        if lines:
            blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)

def _general_opening(index, role, company):
    i = index % len(OPENINGS)
    if role and company:
        return OPENINGS[i].format(role=role, company=company)
    if role:
        options = [
            f'I am writing to apply for a {role} position with your organisation.',
            f'Please consider my application for a {role} role on your team.',
            f'I am excited to apply for a {role} opportunity with your team.',
            f'I am writing to express my interest in joining your team as a {role}.',
            f'I am eager to develop my career as a {role} within your organisation.',
            f'I am applying for a {role} role as I take the next step in my career.',
            f'I am interested in a {role} opportunity with your organisation and the chance to contribute.',
            f'I would welcome the opportunity to join your team as a {role}.',
            f'Working as a {role} appeals to me as an opportunity to contribute and grow.',
            f'Please accept this letter as my application for a {role} position.'
        ]
        return options[i]
    if company:
        options = [
            f'I am writing to express my interest in suitable employment opportunities at {company}.',
            f'Please consider my application for any suitable opening at {company}.',
            f'I am keen to contribute my skills and work ethic to the team at {company}.',
            f'I am writing to enquire about current or upcoming opportunities to join {company}.',
            f'I am eager to develop my career and contribute to {company}.',
            f'I am submitting my application for consideration at {company} as I take the next step in my career.',
            f'I am interested in working with {company} and supporting your day-to-day operations.',
            f'I would welcome the opportunity to join the team at {company}.',
            f'Working at {company} appeals to me as an opportunity to contribute and grow.',
            f'Please accept this letter as my general application for employment with {company}.'
        ]
        return options[i]
    options = [
        'I am writing to express my interest in suitable employment opportunities with your organisation.',
        'Please consider my application for any suitable role within your team.',
        'I am keen to bring my skills and dependable work ethic to your organisation.',
        'I am writing to enquire about current or upcoming job opportunities on your team.',
        'I am eager to learn, contribute and grow within your organisation.',
        'Please consider my general application as I take the next step in my career.',
        'I am interested in joining your team and contributing wherever my skills are most useful.',
        'I would welcome the opportunity to contribute to your organisation.',
        'Joining your team appeals to me as an opportunity to work hard and grow.',
        'Please accept this letter as my application for suitable opportunities with your organisation.'
    ]
    return options[i]

def _general_introduction(index, d, role, company):
    i = index % len(INTRODUCTIONS)
    if role and (i != 9 or company):
        data = dict(d, role=role, company=company or 'your team')
        return INTRODUCTIONS[i].format(**data)
    if role and i == 9:
        return f"Hello, I am {d['name']}. I am interested in contributing to your team in a {role} position."
    if company:
        return f"Hello, my name is {d['name']}. I am interested in opportunities with {company} and bring strengths in {d['skills']}."
    options = [
        f"Hello, my name is {d['name']}. I am looking for new work opportunities and have skills in {d['skills']}.",
        f"My name is {d['name']}, and I am exploring new job opportunities. My background includes {d['experience']}.",
        f"Hi, I am {d['name']}. I am open to new employment opportunities and would be glad to connect.",
        f"Hello, I am {d['name']}. I bring skills in {d['skills']} and am ready for new opportunities.",
        f"I am {d['name']}, with experience in {d['experience']}. I am currently seeking my next role.",
        f"Hello, I am {d['name']}. I am keen to learn and contribute, using my skills in {d['skills']}.",
        f"I am {d['name']}, ready for my next career step with transferable skills in {d['skills']}.",
        f"Good day. My name is {d['name']}, and I would like to introduce myself for suitable opportunities.",
        f"Hi, I am {d['name']}. I enjoy putting my skills in {d['skills']} to work and am ready to contribute.",
        f"Hello, I am {d['name']}. I am interested in contributing to your team and growing in a suitable role."
    ]
    return options[i]

def make_document(category,index,values):
    raw_role = values.get('role','').strip()
    raw_company = values.get('company','').strip()
    d = {k: values.get(k,'').strip() or '['+label+']' for k,label in {
        'name':'Full name',
        'contact':'Contact details',
        'skills':'Skills',
        'experience':'Experience',
        'education':'Education'
    }.items()}
    for field in CAREER_ENTRY_FIELDS:
        d[field] = format_career_entries(values.get(field,'')) or f'[{LABELS[field]}]'
    d['role'] = raw_role
    d['company'] = raw_company
    if category == CATEGORIES[0]:
        # A profile should not repeat all multi-line job and skill entries; full text remains below.
        summary_data = dict(d)
        for field in ('skills','experience','education'):
            first = next((x.strip() for x in d[field].splitlines() if x.strip()), d[field])
            summary_data[field] = first if len(first)<=160 else first[:157]+'...'
        templates = SUMMARIES if raw_role else GENERAL_SUMMARIES
        profile = templates[index % len(templates)].format(**summary_data)
        personal_details = [d['contact']]
        gender = str(values.get('gender') or '').strip()
        id_number = str(values.get('id_number') or '').strip()
        if gender:
            personal_details.append(f'Gender: {gender}')
        if id_number:
            personal_details.append(f'ID number: {id_number}')
        contact_line = ' • '.join(item for item in personal_details if item)
        return f"{d['name']}\n{contact_line}\n\nPROFESSIONAL PROFILE\n{profile}\n\nSKILLS\n{d['skills']}\n\nEXPERIENCE\n{d['experience']}\n\nEDUCATION\n{d['education']}"
    if category == CATEGORIES[1]:
        recipient = f"Hiring Manager\n{raw_company}" if raw_company else "Hiring Manager"
        opening = _general_opening(index, raw_role, raw_company)
        closing = CLOSINGS[index % len(CLOSINGS)]
        return f"{d['name']}\n{d['contact']}\n\n{recipient}\n\nDear Hiring Manager,\n\n{opening}\n\nMy experience includes {d['experience']}. I can offer skills in {d['skills']}. My education includes {d['education']}.\n\n{closing}\n\nYours sincerely,\n{d['name']}"
    if category == CATEGORIES[2]:
        templates = SUMMARIES if raw_role else GENERAL_SUMMARIES
        return templates[index % len(templates)].format(**d)
    return _general_introduction(index, d, raw_role, raw_company)
