"""Career document templates; no network or GUI dependencies."""
NAMES = ['Classic','Modern','Minimal','Executive','Fresh','Creative','Confident','Contemporary','Professional','Elegant']
CV_NAMES = NAMES + ['Modern Sidebar', 'Fresh Sidebar', 'Compact Profile']
COLORS = ['#23405d','#325f83','#444444','#172e49','#257868','#825185','#a34736','#886b3f','#334d68','#625275']
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
LABELS={'name':'Full name','contact':'Contact details','role':'Job title','company':'Company','skills':'Skills','experience':'Experience','education':'Education'}

def make_document(category,index,values):
    d={k:values.get(k,'').strip() or '['+label+']' for k,label in LABELS.items()}
    if category == CATEGORIES[0]:
        # A profile should not repeat all multi-line job and skill entries; full text remains below.
        summary_data=dict(d)
        for field in ('skills','experience','education'):
            first=next((x.strip() for x in d[field].splitlines() if x.strip()),d[field])
            summary_data[field]=first if len(first)<=160 else first[:157]+'...'
        return f"{d['name']}\n{d['contact']}\n\nPROFESSIONAL PROFILE\n{SUMMARIES[index % len(SUMMARIES)].format(**summary_data)}\n\nSKILLS\n{d['skills']}\n\nEXPERIENCE\n{d['experience']}\n\nEDUCATION\n{d['education']}"
    if category == CATEGORIES[1]:
        return f"{d['name']}\n{d['contact']}\n\nHiring Manager\n{d['company']}\n\nDear Hiring Manager,\n\n{OPENINGS[index].format(**d)}\n\nMy experience includes {d['experience']}. I can offer skills in {d['skills']}. My education includes {d['education']}.\n\n{CLOSINGS[index]}\n\nYours sincerely,\n{d['name']}"
    return (SUMMARIES if category==CATEGORIES[2] else INTRODUCTIONS)[index].format(**d)

