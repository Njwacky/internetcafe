"""Business cards & Invitation cards studio UI — 13 templates with live preview & enlarge modal."""
from pathlib import Path
from tkinter import filedialog, messagebox
import uuid
import customtkinter as ctk
from PIL import Image
import cards_renderer
import services


def show_cards(app, parent, card, label, button, entry):
    app.heading(
        parent,
        'Cards & invitations',
        'Design business cards or event invitations with all 13 templates, preview live, and export printable A4 sheets.',
    )

    state = app.card_state
    kind_var = ctk.StringVar(value=state['kind'])
    tpl_var = ctk.StringVar(value=state['template'])
    sheet_var = ctk.StringVar(value=state['sheet'])
    marks_var = ctk.BooleanVar(value=state['crop_marks'])
    view_mode = ctk.StringVar(value='Single card')

    # Side-by-side layout: details on left, template selector + live preview on right
    workspace = ctk.CTkFrame(parent, fg_color='transparent')
    workspace.pack(fill='both', expand=True, pady=(0, 14))
    workspace.grid_columnconfigure(0, weight=1)
    workspace.grid_columnconfigure(1, weight=0)

    # LEFT COLUMN: Details form + logo/photo + export
    left_col = ctk.CTkFrame(workspace, fg_color='transparent')
    left_col.grid(row=0, column=0, sticky='nsew', padx=(0, 14))

    form_box = card(left_col)
    form_box.pack(fill='x', pady=(0, 12))
    form_title = label(form_box, '1   Card details', size=16, bold=True)
    form_title.pack(anchor='w', padx=20, pady=(16, 2))
    form_sub = label(
        form_box,
        'Enter your details below — the card preview updates on the right.',
        size=12,
        color='#A2ACC5',
        wraplength=520,
        justify='left',
    )
    form_sub.pack(anchor='w', padx=20, pady=(0, 10))

    fields_holder = ctk.CTkFrame(form_box, fg_color='transparent')
    fields_holder.pack(fill='x', padx=20, pady=(0, 8))
    fields_holder.grid_columnconfigure((0, 1), weight=1)

    photo_row = ctk.CTkFrame(form_box, fg_color='transparent')
    photo_row.pack(fill='x', padx=20, pady=(6, 16))
    photo_lbl = label(
        photo_row,
        'Logo / image: ' + (Path(state['photo']).name if state['photo'] else 'none (optional)'),
        size=11,
        color='#A2ACC5',
    )
    photo_lbl.pack(side='left', padx=(0, 8))

    export_box = card(left_col)
    export_box.pack(fill='x')
    label(export_box, '2   Sheet layout & export', size=16, bold=True).pack(anchor='w', padx=20, pady=(16, 4))
    label(
        export_box,
        'Choose how many cards to arrange on each A4 page, then save as PDF and print at 100% / Actual size.',
        size=12,
        color='#A2ACC5',
        wraplength=520,
        justify='left',
    ).pack(anchor='w', padx=20, pady=(0, 10))

    sheet_row = ctk.CTkFrame(export_box, fg_color='transparent')
    sheet_row.pack(fill='x', padx=20, pady=(0, 10))
    label(sheet_row, 'A4 print layout', size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 4))
    sheet_menu = ctk.CTkOptionMenu(
        sheet_row,
        values=[x[0] for x in cards_renderer.SHEET_OPTIONS[state['kind']]],
        variable=sheet_var,
        fg_color='#445A86',
        button_color='#536B9D',
        height=36,
    )
    sheet_menu.pack(fill='x')

    marks_chk = ctk.CTkCheckBox(
        export_box,
        text='Include trim / crop marks around each card',
        variable=marks_var,
        text_color='#C4D0E5',
        font=ctk.CTkFont(family='Segoe UI', size=12),
    )
    marks_chk.pack(anchor='w', padx=20, pady=(0, 12))

    export_btns = ctk.CTkFrame(export_box, fg_color='transparent')
    export_btns.pack(fill='x', padx=20, pady=(0, 18))

    # RIGHT COLUMN: Card type + 13 templates right around the live preview + icon enlarge button
    right_col = card(workspace)
    right_col.grid(row=0, column=1, sticky='n')

    sel_frame = ctk.CTkFrame(right_col, fg_color='transparent')
    sel_frame.pack(fill='x', padx=16, pady=(16, 10))
    label(sel_frame, 'Card type', size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 4))
    type_menu = ctk.CTkOptionMenu(
        sel_frame,
        values=cards_renderer.CARD_TYPES,
        variable=kind_var,
        fg_color='#445A86',
        button_color='#536B9D',
        height=35,
        width=360,
    )
    type_menu.pack(fill='x', pady=(0, 8))

    label(sel_frame, 'Template • all 13 designs', size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 4))
    tpl_menu = ctk.CTkOptionMenu(
        sel_frame,
        values=cards_renderer.CARD_NAMES,
        variable=tpl_var,
        fg_color='#445A86',
        button_color='#536B9D',
        height=35,
        width=360,
    )
    tpl_menu.pack(fill='x')

    prev_card = ctk.CTkFrame(right_col, fg_color='#232E44', corner_radius=12, border_width=1, border_color='#34415A')
    prev_card.pack(fill='x', padx=16, pady=(2, 16))

    phead = ctk.CTkFrame(prev_card, fg_color='transparent')
    phead.pack(fill='x', padx=12, pady=(10, 6))
    label(phead, 'Live preview', size=13, bold=True).pack(side='left')

    enlarge_icon = app.icon_images.get('enlarge')
    pbtns = ctk.CTkFrame(phead, fg_color='transparent')
    pbtns.pack(side='right')

    mode_seg = ctk.CTkSegmentedButton(
        prev_card,
        values=['Single card', 'A4 sheet'],
        variable=view_mode,
        height=28,
        fg_color='#141C2B',
        selected_color='#445A86',
        selected_hover_color='#536B9D',
    )
    mode_seg.pack(fill='x', padx=12, pady=(0, 8))

    preview_lbl = ctk.CTkLabel(
        prev_card,
        text='Rendering preview…',
        width=350,
        height=275,
        fg_color='#121A29',
        corner_radius=8,
        text_color='#A2ACC5',
        justify='center',
    )
    preview_lbl.pack(padx=12, pady=(0, 12))

    field_vars = {}
    debounce_ref = {'after': None, 'img': None}

    def current_fields():
        kind = kind_var.get()
        bucket = state['business_fields'] if kind == 'Business card' else state['invitation_fields']
        for k, v in field_vars.items():
            bucket[k] = v.get()
        return dict(bucket)

    def current_indices():
        kind = kind_var.get()
        t_idx = cards_renderer.CARD_NAMES.index(tpl_var.get()) if tpl_var.get() in cards_renderer.CARD_NAMES else 0
        s_names = [x[0] for x in cards_renderer.SHEET_OPTIONS[kind]]
        s_idx = s_names.index(sheet_var.get()) if sheet_var.get() in s_names else 0
        return kind, t_idx, s_idx

    def schedule_preview(*_):
        if debounce_ref['after'] is not None:
            try:
                parent.after_cancel(debounce_ref['after'])
            except Exception:
                pass
        debounce_ref['after'] = parent.after(220, refresh_preview)

    def refresh_preview():
        debounce_ref['after'] = None
        if not preview_lbl.winfo_exists() or app.busy:
            return
        state['kind'] = kind_var.get()
        state['template'] = tpl_var.get()
        state['sheet'] = sheet_var.get()
        state['crop_marks'] = bool(marks_var.get())
        kind, t_idx, s_idx = current_indices()
        fields = current_fields()
        single = view_mode.get() == 'Single card'
        box_size = (346, 250) if single else (260, 268)
        try:
            pil_im = services.render_card_preview(
                kind,
                t_idx,
                fields,
                sheet_index=s_idx,
                photo_path=state['photo'],
                crop_marks=state['crop_marks'],
                single_card=single,
                max_size=box_size,
            )
            ctk_im = ctk.CTkImage(light_image=pil_im, dark_image=pil_im, size=pil_im.size)
            debounce_ref['img'] = ctk_im
            preview_lbl.configure(text='', image=ctk_im)
        except Exception as exc:
            preview_lbl.configure(image=None, text=f'Preview error:\n{exc}')

    def open_large_preview():
        if app.busy:
            return
        kind, t_idx, s_idx = current_indices()
        fields = current_fields()
        single = view_mode.get() == 'Single card'
        big_size = (760, 520) if single else (560, 760)
        try:
            pil_im = services.render_card_preview(
                kind,
                t_idx,
                fields,
                sheet_index=s_idx,
                photo_path=state['photo'],
                crop_marks=bool(marks_var.get()),
                single_card=single,
                max_size=big_size,
            )
        except Exception as exc:
            return messagebox.showerror('Preview unavailable', str(exc), parent=app)

        win = ctk.CTkToplevel(app)
        win.title(f'{kind} • {tpl_var.get()} (Large preview)')
        win.configure(fg_color='#101522')
        w_win = max(520, pil_im.width + 64)
        h_win = max(420, pil_im.height + 115)
        win.geometry(f'{w_win}x{h_win}')
        win.transient(app)

        top = ctk.CTkFrame(win, fg_color='transparent')
        top.pack(fill='x', padx=20, pady=(14, 6))
        label(top, f'{kind}  •  {tpl_var.get()}', size=15, bold=True).pack(side='left')
        mode_switch = ctk.StringVar(value='Single card' if single else 'A4 sheet')
        big_holder = {'img': ctk.CTkImage(light_image=pil_im, dark_image=pil_im, size=pil_im.size)}
        img_lbl = ctk.CTkLabel(win, text='', image=big_holder['img'], fg_color='#182132', corner_radius=10)
        img_lbl.pack(expand=True, padx=20, pady=6)

        def switch_big(choice):
            use_single = choice == 'Single card'
            sz = (760, 520) if use_single else (540, 740)
            im2 = services.render_card_preview(
                kind, t_idx, fields, sheet_index=s_idx, photo_path=state['photo'], crop_marks=bool(marks_var.get()), single_card=use_single, max_size=sz
            )
            big_holder['img'] = ctk.CTkImage(light_image=im2, dark_image=im2, size=im2.size)
            img_lbl.configure(image=big_holder['img'])

        ctk.CTkSegmentedButton(
            top,
            values=['Single card', 'A4 sheet'],
            variable=mode_switch,
            command=switch_big,
            height=28,
            fg_color='#1B2333',
            selected_color='#445A86',
        ).pack(side='right')
        button(win, 'Close', win.destroy, secondary=True, width=110, height=32).pack(pady=(4, 14))
        win.grab_set()
        win.focus_set()

    button(pbtns, '↻', refresh_preview, secondary=True, width=34, height=30).pack(side='left', padx=(0, 6))
    enlarge_kw = {'image': enlarge_icon, 'text': ''} if enlarge_icon else {'text': '⤢'}
    ctk.CTkButton(
        pbtns,
        command=open_large_preview,
        width=34,
        height=30,
        corner_radius=8,
        fg_color='#303C55',
        hover_color='#40506D',
        text_color='white',
        **enlarge_kw,
    ).pack(side='left')

    def build_fields():
        for child in fields_holder.winfo_children():
            child.destroy()
        field_vars.clear()
        kind = kind_var.get()
        schema = cards_renderer.BUSINESS_FIELDS if kind == 'Business card' else cards_renderer.INVITATION_FIELDS
        saved = state['business_fields'] if kind == 'Business card' else state['invitation_fields']
        placeholders = {
            'name': 'e.g. Nandi Mkhize',
            'role': 'e.g. Founder & Manager',
            'company': 'e.g. Sunrise Print & Copy',
            'phone': 'e.g. 012 345 6789',
            'email': 'e.g. info@sunrise.co.za',
            'address': 'e.g. 14 Main Road, Benoni',
            'tagline': 'e.g. Fast printing, scanning & design',
            'event': 'e.g. 30th Birthday Celebration',
            'honoree': 'e.g. Hosted by the Mkhize Family',
            'date': 'e.g. Saturday, 28 Nov 2026',
            'time': 'e.g. 14:00 for 14:30',
            'venue': 'e.g. Lakeside Garden Hall, Benoni',
            'rsvp': 'e.g. Nandi • 012 345 6789 by 20 Nov',
            'message': 'e.g. Dress code: Smart casual. Refreshments served.',
        }
        keys = list(schema.keys())
        for i, key in enumerate(keys):
            wide = i == len(keys) - 1
            cell = ctk.CTkFrame(fields_holder, fg_color='transparent')
            if wide:
                cell.grid(row=(i // 2) + (i % 2), column=0, columnspan=2, sticky='ew', pady=(6, 0))
            else:
                cell.grid(row=i // 2, column=i % 2, sticky='ew', padx=(0, 10) if i % 2 == 0 else (0, 0), pady=(6, 0))
            label(cell, schema[key], size=11, bold=True, color='#A2ACC5').pack(anchor='w', pady=(0, 4))
            var = ctk.StringVar(value=saved.get(key, ''))
            var.trace_add('write', schedule_preview)
            field_vars[key] = var
            ent = entry(cell, placeholders.get(key, ''))
            ent.configure(textvariable=var)
            ent.pack(fill='x')

    def on_kind_change(choice):
        current_fields()
        state['kind'] = choice
        opts = [x[0] for x in cards_renderer.SHEET_OPTIONS[choice]]
        sheet_menu.configure(values=opts)
        if sheet_var.get() not in opts:
            sheet_var.set(opts[0])
            state['sheet'] = opts[0]
        form_title.configure(text='1   Business card details' if choice == 'Business card' else '1   Invitation details')
        build_fields()
        refresh_preview()

    def choose_logo():
        path = filedialog.askopenfilename(parent=app, filetypes=[('Images', '*.png *.jpg *.jpeg *.bmp *.tif *.tiff')])
        if not path:
            return
        try:
            with services.open_safe_image(path) as im:
                im.verify()
            state['photo'] = path
            photo_lbl.configure(text='Logo / image: ' + Path(path).name)
            refresh_preview()
        except Exception as exc:
            messagebox.showerror('Invalid image', str(exc), parent=app)

    def remove_logo():
        state['photo'] = None
        photo_lbl.configure(text='Logo / image: none (optional)')
        refresh_preview()

    button(photo_row, '＋ Choose logo', choose_logo, secondary=True, width=135, height=32).pack(side='left', padx=(0, 8))
    button(photo_row, 'Remove', remove_logo, secondary=True, width=100, height=32).pack(side='left')

    def save_pdf():
        if app.busy:
            return messagebox.showinfo('Please wait', 'Wait for the current task to finish.', parent=app)
        kind, t_idx, s_idx = current_indices()
        fields = current_fields()
        default_name = 'business-cards.pdf' if kind == 'Business card' else 'invitation-cards.pdf'
        out = filedialog.asksaveasfilename(parent=app, defaultextension='.pdf', filetypes=[('PDF document', '*.pdf')], initialfile=default_name)
        if not out:
            return
        photo = state['photo']
        marks = bool(marks_var.get())
        app.saved_job(
            f'Saving {kind.lower()} PDF…',
            lambda: services.export_card_pdf(out, kind, t_idx, fields, sheet_index=s_idx, photo_path=photo, crop_marks=marks),
            lambda _: messagebox.showinfo('Saved', f'{kind} sheet saved to:\n{out}\n\nPrint at 100% / Actual size.', parent=app),
            service='cards',
        )

    def add_to_merge():
        if app.busy:
            return messagebox.showinfo('Please wait', 'Wait for the current task to finish.', parent=app)
        kind, t_idx, s_idx = current_indices()
        fields = current_fields()
        photo = state['photo']
        marks = bool(marks_var.get())
        prefix = 'BusinessCards-' if kind == 'Business card' else 'Invitations-'
        temp_path = str(Path(app.session_temp.name) / (prefix + uuid.uuid4().hex[:8] + '.pdf'))

        def done(_):
            app.pdf_files.append(temp_path)
            if messagebox.askyesno(
                'Added to PDF merge',
                'Added this card sheet to PDF merge. Open PDF tools now?',
                parent=app,
            ):
                app.show('PDF tools')

        app.job(
            'Adding card sheet to PDF merge…',
            lambda: services.export_card_pdf(temp_path, kind, t_idx, fields, sheet_index=s_idx, photo_path=photo, crop_marks=marks),
            done,
        )

    button(export_btns, 'Save PDF', save_pdf, width=130).pack(side='left', padx=(0, 8))
    button(export_btns, '＋ Add to PDF merge', add_to_merge, secondary=True, width=165).pack(side='left')

    type_menu.configure(command=on_kind_change)
    tpl_menu.configure(command=lambda _v: refresh_preview())
    sheet_menu.configure(command=lambda _v: refresh_preview())
    marks_chk.configure(command=refresh_preview)
    mode_seg.configure(command=lambda _v: refresh_preview())

    def sync_cards_state():
        if not parent.winfo_exists():
            return
        state['kind'] = kind_var.get()
        state['template'] = tpl_var.get()
        state['sheet'] = sheet_var.get()
        state['crop_marks'] = bool(marks_var.get())
        current_fields()

    app._sync_cards_hook = sync_cards_state

    build_fields()
    parent.after(60, refresh_preview)
