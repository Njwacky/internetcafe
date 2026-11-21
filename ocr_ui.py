"""OCR image selection, live preview canvas with animated up-and-down scan line, and text extraction."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import ImageTk
import customtkinter as ctk
import services


def scan_step(y, direction, min_y, max_y, speed=4.0):
    """Advance a scan-line y position smoothly between min_y and max_y (bounce up and down)."""
    if max_y <= min_y:
        return float(min_y), 1
    nxt = float(y) + float(direction) * float(speed)
    if nxt >= max_y:
        return float(max_y), -1
    if nxt <= min_y:
        return float(min_y), 1
    return nxt, (1 if direction >= 0 else -1)


def show_ocr(app, parent, card, label, button):
    box = card(parent)
    box.pack(fill='x', pady=(0, 14))
    label(box, 'Recognise text (OCR)', size=16, bold=True).pack(anchor='w', padx=20, pady=(18, 4))
    label(
        box,
        'Choose one or more images, preview them below, and extract English text locally with Tesseract OCR. '
        'Need Tesseract on this computer? Open Help & privacy for the direct download link.',
        size=12,
        color='#A2ACC5',
        wraplength=750,
        justify='left',
    ).pack(anchor='w', padx=20, pady=(0, 10))

    # Top controls to choose/manage OCR images
    pick_row = ctk.CTkFrame(box, fg_color='transparent')
    pick_row.pack(fill='x', padx=20, pady=(0, 10))

    # Preview & list area
    preview_holder = ctk.CTkFrame(box, fg_color='transparent')
    preview_holder.pack(fill='x', padx=20, pady=(0, 10))

    left = ctk.CTkFrame(preview_holder, fg_color='#121A29', corner_radius=10)
    left.pack(side='left', fill='both', expand=True, padx=(0, 12))
    list_header = label(left, 'OCR images (0)', size=12, bold=True)
    list_header.pack(anchor='w', padx=12, pady=(10, 4))
    listing = tk.Listbox(
        left,
        height=7,
        bg='#121A29',
        fg='#F2F4FA',
        selectbackground='#405270',
        relief='flat',
        highlightthickness=0,
        exportselection=False,
    )
    listing.pack(fill='both', expand=True, padx=12, pady=(0, 6))
    meta_label = label(left, 'Choose image(s) to preview them here.', size=11, color='#A2ACC5', wraplength=360, justify='left')
    meta_label.pack(anchor='w', padx=12, pady=(0, 10))

    right = ctk.CTkFrame(preview_holder, fg_color='#121A29', corner_radius=10)
    right.pack(side='left')
    canvas_w, canvas_h = 240, 270
    canvas = tk.Canvas(
        right,
        width=canvas_w,
        height=canvas_h,
        bg='#0E1522',
        highlightthickness=1,
        highlightbackground='#34415A',
    )
    canvas.pack(padx=10, pady=(10, 6))

    preview_btns = ctk.CTkFrame(right, fg_color='transparent')
    preview_btns.pack(fill='x', padx=10, pady=(0, 10))

    state = {
        'photo': None,
        'bounds': (16, 16, canvas_w - 16, canvas_h - 16),
        'has_image': False,
        'animating': bool(getattr(app, 'ocr_scan_animating', True)),
        'scan_y': 20.0,
        'scan_dir': 1,
        'after_id': None,
        'demo_until': 0,
    }

    def stop_timer():
        if state['after_id'] is not None:
            try:
                canvas.after_cancel(state['after_id'])
            except Exception:
                pass
            state['after_id'] = None

    def draw_scan_overlay():
        canvas.delete('scan_fx')
        if not state['has_image'] or not state['animating']:
            return
        x1, y1, x2, y2 = state['bounds']
        y = max(y1, min(y2, state['scan_y']))
        # Subtle trailing glow band behind the moving scan line
        trail = 16 if state['scan_dir'] > 0 else -16
        ty1, ty2 = (max(y1, y - trail), y) if trail > 0 else (y, min(y2, y - trail))
        if abs(ty2 - ty1) >= 2:
            canvas.create_rectangle(
                x1 + 1, ty1, x2 - 1, ty2,
                fill='#B9FF70', stipple='gray25', outline='', tags='scan_fx'
            )
        # Outer soft glow + bright lime scanner laser line + corner brackets
        canvas.create_line(x1, y, x2, y, fill='#55C96A', width=5, tags='scan_fx')
        canvas.create_line(x1, y, x2, y, fill='#B9FF70', width=2, tags='scan_fx')
        canvas.create_line(x1 + 6, y, x2 - 6, y, fill='#F4FFE6', width=1, tags='scan_fx')
        # Subtle scanner frame corners
        corner = 10
        for cx, cy, dx, dy in ((x1, y1, corner, corner), (x2, y1, -corner, corner), (x1, y2, corner, -corner), (x2, y2, -corner, -corner)):
            canvas.create_line(cx, cy, cx + dx, cy, fill='#B9FF70', width=2, tags='scan_fx')
            canvas.create_line(cx, cy, cx, cy + dy, fill='#B9FF70', width=2, tags='scan_fx')

    def tick():
        state['after_id'] = None
        if not canvas.winfo_exists():
            return
        if state['has_image'] and state['animating']:
            _, y1, _, y2 = state['bounds']
            speed = 5.5 if app.busy else 3.2
            state['scan_y'], state['scan_dir'] = scan_step(state['scan_y'], state['scan_dir'], y1 + 2, y2 - 2, speed=speed)
            draw_scan_overlay()
            state['after_id'] = canvas.after(35, tick)

    def ensure_animating():
        if state['has_image'] and state['animating'] and state['after_id'] is None and canvas.winfo_exists():
            state['after_id'] = canvas.after(35, tick)

    canvas.bind('<Destroy>', lambda _e: stop_timer(), add='+')

    def render_preview():
        canvas.delete('all')
        files = app.ocr_images
        idx = app.ocr_selected_index
        if not files or idx is None or not (0 <= idx < len(files)):
            state['has_image'] = False
            state['photo'] = None
            stop_timer()
            canvas.create_text(
                canvas_w // 2,
                canvas_h // 2,
                text='Image preview\nappears here\n\nSelect image(s) to\npreview & scan',
                fill='#8FA1BF',
                font=('Segoe UI', 11),
                justify='center',
            )
            return
        path = files[idx]
        try:
            pil_img = services.load_ocr_preview(path, max_size=(canvas_w - 20, canvas_h - 20))
            iw, ih = pil_img.size
            x1 = (canvas_w - iw) // 2
            y1 = (canvas_h - ih) // 2
            photo = ImageTk.PhotoImage(pil_img, master=canvas)
            state['photo'] = photo
            state['bounds'] = (x1, y1, x1 + iw, y1 + ih)
            state['has_image'] = True
            if not (y1 <= state['scan_y'] <= y1 + ih):
                state['scan_y'] = float(y1 + 2)
                state['scan_dir'] = 1
            canvas.create_rectangle(x1 - 1, y1 - 1, x1 + iw + 1, y1 + ih + 1, outline='#42526E', fill='#141C2B')
            canvas.create_image(x1, y1, image=photo, anchor='nw')
            meta_label.configure(text=f'Previewing ({idx + 1}/{len(files)}): {Path(path).name}')
            if state['animating']:
                draw_scan_overlay()
                ensure_animating()
        except ValueError as exc:
            state['has_image'] = False
            state['photo'] = None
            stop_timer()
            canvas.create_text(
                canvas_w // 2,
                canvas_h // 2,
                text='Preview unavailable',
                fill='#E89886',
                font=('Segoe UI', 11, 'bold'),
                justify='center',
            )
            meta_label.configure(text=str(exc))

    def refresh_list(select_index=None):
        listing.delete(0, 'end')
        files = app.ocr_images
        for i, p in enumerate(files, 1):
            listing.insert('end', f'{i}. {Path(p).name}')
        list_header.configure(text=f'OCR images ({len(files)})')
        if not files:
            app.ocr_selected_index = None
            meta_label.configure(text='Choose image(s) to preview them here.')
        else:
            if select_index is not None:
                app.ocr_selected_index = max(0, min(len(files) - 1, select_index))
            elif app.ocr_selected_index is None or app.ocr_selected_index >= len(files):
                app.ocr_selected_index = 0
            listing.selection_set(app.ocr_selected_index)
            listing.see(app.ocr_selected_index)
        render_preview()

    def on_select(_event=None):
        sel = listing.curselection()
        if not sel:
            return
        app.ocr_selected_index = sel[0]
        render_preview()

    listing.bind('<<ListboxSelect>>', on_select)

    def toggle_scan_fx():
        state['animating'] = not state['animating']
        app.ocr_scan_animating = state['animating']
        fx_btn.configure(text='Pause scan line' if state['animating'] else 'Start scan line')
        if state['animating']:
            draw_scan_overlay()
            ensure_animating()
        else:
            stop_timer()
            canvas.delete('scan_fx')

    def view_larger():
        files = app.ocr_images
        idx = app.ocr_selected_index
        if not files or idx is None or not (0 <= idx < len(files)):
            return messagebox.showinfo('Select an image', 'Choose an image to view larger.', parent=app)
        path = files[idx]
        try:
            large = services.load_ocr_preview(path, max_size=(760, 560))
        except ValueError as exc:
            return messagebox.showerror('Preview unavailable', str(exc), parent=app)
        win = ctk.CTkToplevel(app)
        win.title(f'OCR image preview • {Path(path).name}')
        win.configure(fg_color='#101522')
        win.transient(app)
        win.resizable(False, False)
        ctk.CTkLabel(win, text=Path(path).name, font=ctk.CTkFont(size=15, weight='bold'), text_color='white').pack(anchor='w', padx=16, pady=(14, 6))
        lw, lh = large.size
        big_canvas = tk.Canvas(win, width=lw, height=lh, bg='#0E1522', highlightthickness=1, highlightbackground='#34415A')
        big_canvas.pack(padx=16, pady=(0, 10))
        big_photo = ImageTk.PhotoImage(large, master=big_canvas)
        big_canvas.image = big_photo
        big_canvas.create_image(0, 0, image=big_photo, anchor='nw')
        modal_state = {'y': 2.0, 'dir': 1, 'after': None}

        def big_tick():
            if not big_canvas.winfo_exists():
                return
            big_canvas.delete('scan_fx')
            if state['animating']:
                modal_state['y'], modal_state['dir'] = scan_step(modal_state['y'], modal_state['dir'], 2, max(3, lh - 2), speed=5.0)
                y = modal_state['y']
                big_canvas.create_line(0, y, lw, y, fill='#55C96A', width=5, tags='scan_fx')
                big_canvas.create_line(0, y, lw, y, fill='#B9FF70', width=2, tags='scan_fx')
            modal_state['after'] = big_canvas.after(35, big_tick)

        def close_big():
            if modal_state['after'] is not None:
                try:
                    big_canvas.after_cancel(modal_state['after'])
                except Exception:
                    pass
            win.destroy()

        win.protocol('WM_DELETE_WINDOW', close_big)
        ctk.CTkButton(win, text='Close preview', width=130, fg_color='#344663', command=close_big).pack(pady=(0, 14))
        win.grab_set()
        win.focus_set()
        big_tick()

    fx_btn = button(
        preview_btns,
        'Pause scan line' if state['animating'] else 'Start scan line',
        toggle_scan_fx,
        secondary=True,
        width=118,
        height=30,
    )
    fx_btn.pack(side='left', padx=(0, 6))
    button(preview_btns, 'View larger', view_larger, secondary=True, width=106, height=30).pack(side='left')

    def add_images():
        if app.busy:
            return messagebox.showinfo('Please wait', 'Wait for the current task to finish.', parent=app)
        paths = filedialog.askopenfilenames(
            parent=app,
            title='Choose image(s) for OCR',
            filetypes=[('Images', '*.png *.jpg *.jpeg *.bmp *.tif *.tiff')],
        )
        if not paths:
            return
        valid = []
        skipped = []
        for p in paths:
            try:
                with services.open_safe_image(p):
                    pass
                valid.append(p)
            except ValueError as exc:
                skipped.append(f'{Path(p).name}: {exc}')
        if skipped:
            messagebox.showwarning('Some images skipped', '\n'.join(skipped[:5]), parent=app)
        if not valid:
            return
        start_idx = len(app.ocr_images)
        app.ocr_images.extend(valid)
        # Ensure scan line starts moving when images are chosen
        state['animating'] = True
        app.ocr_scan_animating = True
        fx_btn.configure(text='Pause scan line')
        refresh_list(select_index=start_idx)
        app.status.set(f'{len(app.ocr_images)} OCR image(s) selected • ready to recognise text')

    def remove_selected():
        if app.busy:
            return messagebox.showinfo('Please wait', 'Wait for the current task to finish.', parent=app)
        idx = app.ocr_selected_index
        if idx is None or not (0 <= idx < len(app.ocr_images)):
            return messagebox.showinfo('Select an image', 'Select an image in the OCR list first.', parent=app)
        app.ocr_images.pop(idx)
        refresh_list(select_index=min(idx, len(app.ocr_images) - 1) if app.ocr_images else None)

    def clear_images():
        if app.busy:
            return messagebox.showinfo('Please wait', 'Wait for the current task to finish.', parent=app)
        if not app.ocr_images:
            return
        app.ocr_images.clear()
        refresh_list()

    button(pick_row, '＋ Add images', add_images, width=140, height=34).pack(side='left', padx=(0, 8))
    button(pick_row, 'Remove', remove_selected, secondary=True, width=105, height=34).pack(side='left', padx=(0, 8))
    button(pick_row, '⌫ Clear all', clear_images, secondary=True, width=110, height=34).pack(side='left')

    # Editor + OCR execution actions
    app.ocr_editor = ctk.CTkTextbox(
        box, height=150, fg_color='#121A29', border_color='#34415A', border_width=1, text_color='#F2F4FA'
    )
    app.ocr_editor.pack(fill='x', padx=20, pady=(4, 12))
    app.ocr_editor.insert('1.0', app.ocr_text)

    action_row = ctk.CTkFrame(box, fg_color='transparent')
    action_row.pack(fill='x', padx=20, pady=(0, 18))

    def run_recognition(paths):
        if app.busy:
            return messagebox.showinfo('Please wait', 'Wait for the current task to finish.', parent=app)
        if not paths:
            add_images()
            paths = list(app.ocr_images) if len(app.ocr_images) == 1 else ([app.ocr_images[app.ocr_selected_index]] if app.ocr_images and app.ocr_selected_index is not None else [])
            if not paths:
                return
        current_editor_text = app.ocr_editor.get('1.0', 'end-1c')
        if current_editor_text.strip() and not messagebox.askyesno('Replace OCR text?', 'Replace the current recognised text?', parent=app):
            return
        original = current_editor_text
        # Turn on scan-line animation during OCR recognition
        state['animating'] = True
        app.ocr_scan_animating = True
        fx_btn.configure(text='Pause scan line')
        draw_scan_overlay()
        ensure_animating()
        targets = list(paths)

        def done(text):
            now_text = app.ocr_editor.get('1.0', 'end-1c') if app.ocr_editor and app.ocr_editor.winfo_exists() else app.ocr_text
            if now_text != original and not messagebox.askyesno('Text changed', 'Text changed while OCR was running. Replace it?', parent=app):
                return
            app.ocr_text = text
            if app.ocr_editor and app.ocr_editor.winfo_exists():
                app.ocr_editor.delete('1.0', 'end')
                app.ocr_editor.insert('1.0', text)
            messagebox.showinfo('OCR complete', 'Review the recognised text carefully.', parent=app)

        label_msg = 'Recognising text…' if len(targets) == 1 else f'Recognising text from {len(targets)} images…'
        app.job(label_msg, lambda: services.run_ocr_many(targets), done)

    def recognize_selected():
        if not app.ocr_images:
            return run_recognition([])
        idx = app.ocr_selected_index if app.ocr_selected_index is not None else 0
        run_recognition([app.ocr_images[idx]])

    def recognize_all():
        if not app.ocr_images:
            return run_recognition([])
        run_recognition(list(app.ocr_images))

    def save():
        text = app.ocr_editor.get('1.0', 'end-1c').strip()
        if not text:
            return messagebox.showinfo('OCR', 'Recognise or enter text first.', parent=app)
        path = filedialog.asksaveasfilename(
            parent=app,
            defaultextension='.txt',
            filetypes=[('Text', '*.txt')],
            initialfile='recognised-text.txt',
        )
        if path:
            app.saved_job('Saving text…', lambda: services.export_text(text, path), lambda _: messagebox.showinfo('Saved', f'Text saved to:\n{path}', parent=app),'ocr')

    button(action_row, 'Recognise image', recognize_selected, width=165).pack(side='left', padx=(0, 8))
    button(action_row, 'Recognise all', recognize_all, secondary=True, width=145).pack(side='left', padx=(0, 8))
    button(action_row, 'Save text', save, secondary=True, width=115).pack(side='left', padx=(0, 8))
    button(action_row, '↗ Get Tesseract OCR', lambda: app.show('Help & privacy'), secondary=True, width=165).pack(side='left')

    refresh_list()
