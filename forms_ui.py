"""Form Creator UI — build printable & AcroForm-fillable A4 PDFs with live preview."""
from pathlib import Path
from tkinter import filedialog, messagebox
import uuid
import customtkinter as ctk
import forms_renderer
import services


def show_forms(app, parent, card, label, button, entry):
    app.heading(
        parent,
        'Forms creator',
        'Build printable and digitally fillable A4 forms — applications, affidavits, consent forms, intake sheets, or custom fields.',
    )

    state = app.form_state
    tpl_var = ctk.StringVar(value=state['template'])
    title_var = ctk.StringVar(value=state['title'])
    sub_var = ctk.StringVar(value=state['subtitle'])
    org_var = ctk.StringVar(value=state['organization'])
    fillable_var = ctk.BooleanVar(value=state['fillable'])
    sig_var = ctk.BooleanVar(value=state['signature_block'])

    workspace = ctk.CTkFrame(parent, fg_color='transparent')
    workspace.pack(fill='both', expand=True, pady=(0, 14))
    workspace.grid_columnconfigure(0, weight=1)
    workspace.grid_columnconfigure(1, weight=0)

    left_col = ctk.CTkFrame(workspace, fg_color='transparent')
    left_col.grid(row=0, column=0, sticky='nsew', padx=(0, 14))

    header_box = card(left_col)
    header_box.pack(fill='x', pady=(0, 12))
    label(header_box, '1   Form header & settings', size=16, bold=True).pack(anchor='w', padx=20, pady=(16, 4))

    grid_hdr = ctk.CTkFrame(header_box, fg_color='transparent')
    grid_hdr.pack(fill='x', padx=20, pady=(4, 8))
    grid_hdr.grid_columnconfigure((0, 1), weight=1)

    c0 = ctk.CTkFrame(grid_hdr, fg_color='transparent')
    c0.grid(row=0, column=0, sticky='ew', padx=(0, 8), pady=4)
    label(c0, 'Form title', size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 3))
    entry(c0, textvariable=title_var).pack(fill='x')

    c1 = ctk.CTkFrame(grid_hdr, fg_color='transparent')
    c1.grid(row=0, column=1, sticky='ew', pady=4)
    label(c1, 'Organisation / business header (optional)', size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 3))
    entry(c1, textvariable=org_var).pack(fill='x')

    c2 = ctk.CTkFrame(grid_hdr, fg_color='transparent')
    c2.grid(row=1, column=0, columnspan=2, sticky='ew', pady=4)
    label(c2, 'Subtitle', size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 3))
    entry(c2, textvariable=sub_var).pack(fill='x')

    label(header_box, 'Instructions box (optional)', size=11, bold=True, color='#A2ACC5').pack(anchor='w', padx=20, pady=(4, 3))
    inst_box = ctk.CTkTextbox(header_box, height=58, fg_color='#121A29', border_width=1, border_color='#2C3850', text_color='#EEF2FF')
    inst_box.pack(fill='x', padx=20, pady=(0, 10))
    if state.get('instructions'):
        inst_box.insert('1.0', state['instructions'])

    opts_row = ctk.CTkFrame(header_box, fg_color='transparent')
    opts_row.pack(fill='x', padx=20, pady=(0, 16))
    ctk.CTkCheckBox(
        opts_row,
        text='Interactive fillable PDF (AcroForm fields)',
        variable=fillable_var,
        fg_color='#B9FF70',
        checkmark_color='#102006',
        text_color='#EEF2FF',
    ).pack(side='left', padx=(0, 18))
    ctk.CTkCheckBox(
        opts_row,
        text='Include bottom Signature & Date block',
        variable=sig_var,
        fg_color='#B9FF70',
        checkmark_color='#102006',
        text_color='#EEF2FF',
    ).pack(side='left')

    # 2. Fields Builder
    fields_box = card(left_col)
    fields_box.pack(fill='x', pady=(0, 12))
    fhead = ctk.CTkFrame(fields_box, fg_color='transparent')
    fhead.pack(fill='x', padx=20, pady=(16, 4))
    label(fhead, '2   Form fields', size=16, bold=True).pack(side='left')

    fields_list = ctk.CTkFrame(fields_box, fg_color='transparent')
    fields_list.pack(fill='x', padx=20, pady=(4, 8))

    add_row = ctk.CTkFrame(fields_box, fg_color='transparent')
    add_row.pack(fill='x', padx=20, pady=(4, 16))
    new_lbl_var = ctk.StringVar()
    new_type_var = ctk.StringVar(value='text')
    entry(add_row, placeholder='New field label (e.g. Emergency Contact)', textvariable=new_lbl_var).pack(side='left', fill='x', expand=True, padx=(0, 8))
    ctk.CTkOptionMenu(
        add_row,
        values=list(forms_renderer.FIELD_TYPES),
        variable=new_type_var,
        width=115,
        height=36,
        fg_color='#445A86',
        button_color='#536B9D',
    ).pack(side='left', padx=(0, 8))

    # RIGHT COLUMN: Template switcher + live A4 preview + export
    right_col = card(workspace)
    right_col.grid(row=0, column=1, sticky='ns')

    phead = ctk.CTkFrame(right_col, fg_color='transparent')
    phead.pack(fill='x', padx=16, pady=(14, 6))
    label(phead, 'Form template & A4 preview', size=14, bold=True).pack(side='left')
    page_lbl = label(phead, 'Page 1 / 1', size=11, bold=True, color='#B9FF70')
    page_lbl.pack(side='right', padx=(0, 6))

    label(right_col, 'Preset template', size=11, bold=True, color='#A2ACC5').pack(anchor='w', padx=16, pady=(2, 3))
    tpl_menu = ctk.CTkOptionMenu(
        right_col,
        values=forms_renderer.FORM_TEMPLATE_NAMES,
        variable=tpl_var,
        fg_color='#445A86',
        button_color='#536B9D',
        height=34,
        width=310,
    )
    tpl_menu.pack(fill='x', padx=16, pady=(0, 10))

    preview_well = ctk.CTkFrame(right_col, width=310, height=430, fg_color='#141C2B', corner_radius=10, border_width=1, border_color='#2C3850')
    preview_well.pack(padx=16, pady=(0, 8))
    preview_well.pack_propagate(False)
    preview_img_lbl = ctk.CTkLabel(preview_well, text='Preview ready', text_color='#A2ACC5')
    preview_img_lbl.pack(expand=True)

    preview_state = {'page': 1, 'total': 1, 'ctk_img': None, 'after_id': None}

    def sync_state():
        state['template'] = tpl_var.get()
        state['title'] = title_var.get()
        state['subtitle'] = sub_var.get()
        state['organization'] = org_var.get()
        state['instructions'] = inst_box.get('1.0', 'end-1c')
        state['fillable'] = bool(fillable_var.get())
        state['signature_block'] = bool(sig_var.get())
        return dict(state, fields=[dict(f) for f in state['fields']])

    def refresh_preview():
        if not preview_img_lbl.winfo_exists():
            return
        try:
            spec = sync_state()
            pil_im, pnum, tot = services.render_form_preview(spec, page=preview_state['page'], max_size=(294, 414))
            preview_state['page'] = pnum
            preview_state['total'] = tot
            preview_state['ctk_img'] = ctk.CTkImage(light_image=pil_im, dark_image=pil_im, size=pil_im.size)
            preview_img_lbl.configure(image=preview_state['ctk_img'], text='')
            page_lbl.configure(text=f'Page {pnum} / {tot}')
        except Exception as exc:
            preview_img_lbl.configure(image=None, text=str(exc))

    def schedule_preview(*_):
        if preview_state['after_id']:
            try:
                parent.after_cancel(preview_state['after_id'])
            except Exception:
                pass
        preview_state['after_id'] = parent.after(260, refresh_preview)

    def rebuild_fields_ui():
        for w in fields_list.winfo_children():
            w.destroy()
        for idx, fitem in enumerate(state['fields']):
            row = ctk.CTkFrame(fields_list, fg_color='#141C2B', corner_radius=8, border_width=1, border_color='#28354D')
            row.pack(fill='x', pady=3)
            top = ctk.CTkFrame(row, fg_color='transparent')
            top.pack(fill='x', padx=10, pady=(7, 4))
            badge = f"[{fitem['type']}]"
            label(top, f"{idx + 1}. {fitem['label']}  {badge}", size=12, bold=True).pack(side='left')

            def move_field(i=idx, d=-1):
                j = i + d
                if 0 <= j < len(state['fields']):
                    state['fields'][i], state['fields'][j] = state['fields'][j], state['fields'][i]
                    rebuild_fields_ui()
                    schedule_preview()

            def delete_field(i=idx):
                if len(state['fields']) <= 1:
                    messagebox.showinfo('Keep one field', 'A form needs at least one field.', parent=app)
                    return
                del state['fields'][i]
                rebuild_fields_ui()
                schedule_preview()

            button(top, 'Delete', delete_field, secondary=True, width=32, height=26).pack(side='right', padx=(4, 0))
            button(top, '↓', lambda i=idx: move_field(i, 1), secondary=True, width=32, height=26).pack(side='right', padx=(4, 0))
            button(top, '↑', lambda i=idx: move_field(i, -1), secondary=True, width=32, height=26).pack(side='right')

            if fitem['type'] == 'checkbox':
                cvar = ctk.BooleanVar(value=bool(fitem.get('checked', False)))
                def on_chk(i=idx, v=cvar):
                    state['fields'][i]['checked'] = bool(v.get())
                    schedule_preview()
                ctk.CTkCheckBox(
                    row,
                    text='Pre-checked by default',
                    variable=cvar,
                    command=on_chk,
                    fg_color='#B9FF70',
                    checkmark_color='#102006',
                    text_color='#A2ACC5',
                ).pack(anchor='w', padx=10, pady=(0, 7))
            elif fitem['type'] != 'signature':
                vvar = ctk.StringVar(value=str(fitem.get('value', '')))
                def on_val(*_, i=idx, v=vvar):
                    state['fields'][i]['value'] = v.get()
                    schedule_preview()
                vvar.trace_add('write', on_val)
                ent = entry(row, placeholder='Optional pre-filled text (leave blank for walk-in completion)', textvariable=vvar)
                ent.configure(height=30)
                ent.pack(fill='x', padx=10, pady=(0, 7))

    def add_field():
        lbl = new_lbl_var.get().strip()
        if not lbl:
            messagebox.showinfo('Enter label', 'Enter a label for the new field first.', parent=app)
            return
        if len(state['fields']) >= forms_renderer.MAX_FORM_FIELDS:
            messagebox.showwarning('Limit reached', f'Forms support up to {forms_renderer.MAX_FORM_FIELDS} fields.', parent=app)
            return
        state['fields'].append({'label': lbl, 'type': new_type_var.get(), 'value': '', 'checked': False})
        new_lbl_var.set('')
        rebuild_fields_ui()
        schedule_preview()

    button(add_row, '＋ Add field', add_field, secondary=True, width=115, height=36).pack(side='left')

    def on_template_change(choice):
        fresh = forms_renderer.default_form_spec(choice, org_name=org_var.get())
        state.update(fresh)
        title_var.set(fresh['title'])
        sub_var.set(fresh['subtitle'])
        inst_box.delete('1.0', 'end')
        inst_box.insert('1.0', fresh['instructions'])
        fillable_var.set(fresh['fillable'])
        sig_var.set(fresh['signature_block'])
        preview_state['page'] = 1
        rebuild_fields_ui()
        schedule_preview()

    tpl_menu.configure(command=on_template_change)
    for v in (title_var, sub_var, org_var, fillable_var, sig_var):
        v.trace_add('write', schedule_preview)
    inst_box.bind('<KeyRelease>', schedule_preview)

    nav_row = ctk.CTkFrame(right_col, fg_color='transparent')
    nav_row.pack(fill='x', padx=16, pady=(0, 8))

    def turn_page(delta):
        nxt = max(1, min(preview_state['total'], preview_state['page'] + delta))
        if nxt != preview_state['page']:
            preview_state['page'] = nxt
            refresh_preview()

    button(nav_row, '‹ Prev', lambda: turn_page(-1), secondary=True, width=72, height=30).pack(side='left', padx=(0, 6))
    button(nav_row, 'Next ›', lambda: turn_page(1), secondary=True, width=72, height=30).pack(side='left')

    def open_large_preview():
        try:
            spec = sync_state()
            pil_im, pnum, tot = services.render_form_preview(spec, page=preview_state['page'], max_size=(560, 780))
        except Exception as exc:
            messagebox.showerror('Preview unavailable', str(exc), parent=app)
            return
        win = ctk.CTkToplevel(app)
        win.title(f"{spec['title']} — Large A4 Form Preview")
        win.configure(fg_color='#182030')
        win.geometry('660x840')
        win.transient(app)
        st = {'img': ctk.CTkImage(light_image=pil_im, dark_image=pil_im, size=pil_im.size)}
        sc = ctk.CTkScrollableFrame(win, fg_color='#141C2B', corner_radius=12)
        sc.pack(fill='both', expand=True, padx=18, pady=(16, 8))
        ctk.CTkLabel(sc, text='', image=st['img']).pack(pady=8)
        button(win, 'Close', win.destroy, secondary=True, width=100, height=32).pack(pady=(0, 14))
        win.grab_set()

    button(nav_row, '⛶ Enlarge', open_large_preview, secondary=True, width=96, height=30).pack(side='right')

    act_col = ctk.CTkFrame(right_col, fg_color='transparent')
    act_col.pack(fill='x', padx=16, pady=(2, 16))

    def export_pdf():
        try:
            spec = forms_renderer.validate_form_spec(sync_state())
        except ValueError as exc:
            messagebox.showerror('Check form fields', str(exc), parent=app)
            return
        slug = '-'.join(spec['title'].lower().split())[:40] or 'form'
        out = filedialog.asksaveasfilename(
            parent=app,
            defaultextension='.pdf',
            filetypes=[('PDF document', '*.pdf')],
            initialfile=f'{slug}.pdf',
        )
        if out:
            app.saved_job(
                'Saving PDF form…',
                lambda: services.export_form_pdf(spec, out),
                lambda _: messagebox.showinfo('Form saved', f'Printable / fillable PDF saved:\n{out}', parent=app),
                service='forms',
            )

    def send_to_merge():
        try:
            spec = forms_renderer.validate_form_spec(sync_state())
        except ValueError as exc:
            messagebox.showerror('Check form fields', str(exc), parent=app)
            return
        temp_path = str(Path(app.session_temp.name) / ('Form-' + uuid.uuid4().hex[:10] + '.pdf'))
        def done(_):
            app.pdf_files.append(temp_path)
            if messagebox.askyesno('Added to PDF merge', 'Form PDF added to your merge queue. Open PDF tools now?', parent=app):
                app.show('PDF tools')
        app.job('Adding form to PDF merge…', lambda: services.export_form_pdf(spec, temp_path), done)

    button(act_col, 'Save Form PDF', export_pdf, height=38).pack(fill='x', pady=(0, 6))
    button(act_col, 'Add to PDF merge', send_to_merge, secondary=True, height=34).pack(fill='x')

    def sync_forms_state():
        if inst_box.winfo_exists():
            sync_state()

    app._sync_forms_hook = sync_forms_state

    rebuild_fields_ui()
    refresh_preview()
