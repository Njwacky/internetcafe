"""Activation Dialog & License Status Card for Café Desk (Hardware Machine-ID + 1-Year Lock)."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import customtkinter as ctk

import license_guard
import services

BG = '#101522'
WHITE = '#1B2333'
INK = '#F2F4FA'
MUTED = '#A2ACC5'
GREEN = '#445A86'
GREEN_HOVER = '#536B9D'
LIME = '#B9FF70'
BORDER = '#34415A'
SOFT = '#303C55'
RED = '#E89886'
FONT = 'Segoe UI'


def _lbl(parent, text, size=13, bold=False, color=INK, **kw):
    return ctk.CTkLabel(
        parent, text=text, text_color=color,
        font=ctk.CTkFont(family=FONT, size=size, weight='bold' if bold else 'normal'),
        **kw
    )


def _btn(parent, text, cmd, secondary=False, **kw):
    opts = dict(
        text=text, command=cmd, corner_radius=10, height=38,
        font=ctk.CTkFont(family=FONT, size=13, weight='bold'),
        fg_color=SOFT if secondary else GREEN,
        hover_color='#40506D' if secondary else GREEN_HOVER,
        text_color='#E1E8F6' if secondary else 'white',
    )
    opts.update(kw)
    return ctk.CTkButton(parent, **opts)


class ActivationWindow(ctk.CTk):
    """Standalone gate window shown before CafeDesk launches if this PC is unlicensed or expired."""

    def __init__(self, initial_status: dict):
        super().__init__()
        self.title('Activate Café Desk — 1-Year Computer License')
        self.geometry('720x590')
        self.minsize(640, 540)
        self.configure(fg_color=BG)
        self.activated_info: dict | None = None
        self.machine_id = initial_status.get('machine_id') or license_guard.get_machine_id()
        reason = initial_status.get('reason') or 'This computer requires a valid activation key.'

        outer = ctk.CTkFrame(self, fg_color=WHITE, corner_radius=18, border_width=1, border_color=BORDER)
        outer.pack(fill='both', expand=True, padx=26, pady=24)

        _lbl(outer, 'CAFÉ DESK  •  COMPUTER ACTIVATION REQUIRED', size=11, bold=True, color=LIME).pack(
            anchor='w', padx=26, pady=(22, 4)
        )
        _lbl(outer, 'Activate Café Desk on This Computer', size=22, bold=True).pack(
            anchor='w', padx=26, pady=(0, 6)
        )
        _lbl(
            outer,
            'Each Café Desk installation is locked to one specific computer (1-year license). '
            'Copying the app to another PC requires a fresh activation key from the owner.',
            size=12, color=MUTED, wraplength=630, justify='left'
        ).pack(anchor='w', padx=26, pady=(0, 12))

        reason_box = ctk.CTkFrame(outer, fg_color='#342329', corner_radius=10, border_width=1, border_color='#6A3E49')
        reason_box.pack(fill='x', padx=26, pady=(0, 14))
        _lbl(reason_box, f'Status: {reason}', size=12, bold=True, color='#FFB8B0', wraplength=610, justify='left').pack(
            anchor='w', padx=14, pady=10
        )

        # Step 1 — Machine ID box
        mid_box = ctk.CTkFrame(outer, fg_color='#131B2A', corner_radius=12, border_width=1, border_color=BORDER)
        mid_box.pack(fill='x', padx=26, pady=(0, 14))
        _lbl(mid_box, 'STEP 1 — SEND THIS COMPUTER\'S MACHINE ID TO THE OWNER', size=10, bold=True, color=LIME).pack(
            anchor='w', padx=16, pady=(12, 4)
        )
        mid_row = ctk.CTkFrame(mid_box, fg_color='transparent')
        mid_row.pack(fill='x', padx=16, pady=(0, 8))
        mid_entry = ctk.CTkEntry(
            mid_row, height=40, corner_radius=9, border_color=BORDER, fg_color='#0D1320',
            text_color=LIME, font=ctk.CTkFont(family='Consolas', size=16, weight='bold')
        )
        mid_entry.insert(0, self.machine_id)
        mid_entry.configure(state='readonly')
        mid_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))

        def copy_mid():
            self.clipboard_clear()
            self.clipboard_append(self.machine_id)
            self.update_idletasks()
            messagebox.showinfo(
                'Machine ID copied',
                f'Copied Machine ID:\n{self.machine_id}\n\nSend this code via WhatsApp ({services.DEVELOPER_WHATSAPP_LOCAL}) or email ({services.DEVELOPER_EMAIL}) to receive your 1-year activation key.',
                parent=self,
            )

        def whatsapp_mid():
            msg = f'Hi, please issue a 1-Year Cafe Desk Activation Key for Machine ID: {self.machine_id}'
            try:
                services.open_external_url(services.whatsapp_chat_url(msg))
            except ValueError as exc:
                messagebox.showerror('Could not open WhatsApp', str(exc), parent=self)

        _btn(mid_row, '⧉ Copy ID', copy_mid, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=120).pack(
            side='left', padx=(0, 8)
        )
        _btn(mid_row, '💬 WhatsApp ID', whatsapp_mid, fg_color='#25D366', hover_color='#1EBE5D', text_color='#0B1F13', width=155).pack(
            side='left'
        )
        _lbl(
            mid_box,
            f'WhatsApp: {services.DEVELOPER_WHATSAPP_LOCAL}   •   Email: {services.DEVELOPER_EMAIL}',
            size=11, color=MUTED
        ).pack(anchor='w', padx=16, pady=(0, 12))

        # Step 2 — Enter activation key or load .cafelicense
        key_box = ctk.CTkFrame(outer, fg_color='#131B2A', corner_radius=12, border_width=1, border_color=BORDER)
        key_box.pack(fill='x', padx=26, pady=(0, 16))
        _lbl(key_box, 'STEP 2 — PASTE YOUR 1-YEAR ACTIVATION KEY OR LOAD A LICENSE FILE', size=10, bold=True, color=LIME).pack(
            anchor='w', padx=16, pady=(12, 4)
        )
        self.key_text = ctk.CTkTextbox(
            key_box, height=84, corner_radius=9, border_width=1, border_color=BORDER,
            fg_color='#0D1320', text_color=INK, font=ctk.CTkFont(family='Consolas', size=12)
        )
        self.key_text.pack(fill='x', padx=16, pady=(0, 12))

        actions = ctk.CTkFrame(outer, fg_color='transparent')
        actions.pack(fill='x', padx=26, pady=(0, 16))

        def do_activate():
            raw = self.key_text.get('1.0', 'end').strip()
            try:
                info = license_guard.activate_and_save_license(raw)
            except license_guard.LicenseError as exc:
                messagebox.showerror('Activation failed', str(exc), parent=self)
                return
            self.activated_info = info
            messagebox.showinfo(
                'Computer activated',
                f'Café Desk is now activated for:\n'
                f'• Customer: {info["customer"]}\n'
                f'• Machine ID: {info["machine_id"]}\n'
                f'• Valid until: {info["expires_at"]} ({info["days_remaining"]} days remaining)',
                parent=self,
            )
            self.destroy()

        def load_file():
            chosen = filedialog.askopenfilename(
                parent=self,
                title='Select .cafelicense file',
                filetypes=[('Café Desk License', '*.cafelicense *.txt *.json'), ('All files', '*.*')],
            )
            if not chosen:
                return
            try:
                raw = Path(chosen).read_text(encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('Could not read file', str(exc), parent=self)
                return
            self.key_text.delete('1.0', 'end')
            self.key_text.insert('1.0', raw.strip())
            do_activate()

        _btn(actions, '✓ Activate This Computer', do_activate, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=215).pack(
            side='left', padx=(0, 10)
        )
        _btn(actions, 'Load .cafelicense file…', load_file, secondary=True, width=190).pack(side='left', padx=(0, 10))
        _btn(actions, 'Exit', self.destroy, secondary=True, width=90).pack(side='right')


def ensure_computer_licensed() -> dict | None:
    """Check license at startup; if required and missing/invalid, show ActivationWindow.

    Returns license status dict if allowed to run, or `None` if the user closed ActivationWindow.
    """
    status = license_guard.current_license_status()
    if status.get('valid'):
        return status
    if not license_guard.is_license_required():
        # Developer source mode (`python app.py` without CAFE_DESK_REQUIRE_LICENSE=1)
        return status
    win = ActivationWindow(status)
    win.mainloop()
    return win.activated_info


def open_renewal_modal(parent, on_updated=None):
    """Allow entering a renewal / replacement 1-year activation key from inside About & contact."""
    dlg = ctk.CTkToplevel(parent)
    dlg.title('Enter Activation / 1-Year Renewal Key')
    dlg.geometry('620x380')
    dlg.configure(fg_color=BG)
    dlg.transient(parent)
    dlg.grab_set()

    mid = license_guard.get_machine_id()
    box = ctk.CTkFrame(dlg, fg_color=WHITE, corner_radius=14, border_width=1, border_color=BORDER)
    box.pack(fill='both', expand=True, padx=20, pady=20)
    _lbl(box, f'This Computer\'s Machine ID:  {mid}', size=14, bold=True, color=LIME).pack(
        anchor='w', padx=18, pady=(16, 4)
    )
    _lbl(
        box,
        'Paste a new 1-year activation key (CDLIC1...) issued for this Machine ID, or load a .cafelicense file:',
        size=12, color=MUTED, wraplength=560, justify='left'
    ).pack(anchor='w', padx=18, pady=(0, 10))

    txt = ctk.CTkTextbox(
        box, height=110, corner_radius=9, border_width=1, border_color=BORDER,
        fg_color='#0D1320', text_color=INK, font=ctk.CTkFont(family='Consolas', size=12)
    )
    txt.pack(fill='x', padx=18, pady=(0, 14))

    row = ctk.CTkFrame(box, fg_color='transparent')
    row.pack(fill='x', padx=18, pady=(0, 14))

    def apply_key():
        raw = txt.get('1.0', 'end').strip()
        try:
            info = license_guard.activate_and_save_license(raw)
        except license_guard.LicenseError as exc:
            messagebox.showerror('Invalid activation key', str(exc), parent=dlg)
            return
        messagebox.showinfo(
            'License updated',
            f'Activated for {info["customer"]} until {info["expires_at"]} ({info["days_remaining"]} days left).',
            parent=dlg,
        )
        dlg.destroy()
        if callable(on_updated):
            on_updated(info)

    def browse_file():
        chosen = filedialog.askopenfilename(
            parent=dlg,
            title='Select .cafelicense file',
            filetypes=[('Café Desk License', '*.cafelicense *.txt *.json'), ('All files', '*.*')],
        )
        if not chosen:
            return
        try:
            txt.delete('1.0', 'end')
            txt.insert('1.0', Path(chosen).read_text(encoding='utf-8').strip())
            apply_key()
        except OSError as exc:
            messagebox.showerror('Could not read file', str(exc), parent=dlg)

    _btn(row, '✓ Save & Activate', apply_key, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=170).pack(
        side='left', padx=(0, 8)
    )
    _btn(row, 'Load .cafelicense…', browse_file, secondary=True, width=165).pack(side='left', padx=(0, 8))
    _btn(row, 'Cancel', dlg.destroy, secondary=True, width=95).pack(side='right')


def open_addon_modal(
    parent,
    feature: str = license_guard.FEATURE_PDF_UNLOCK,
    feature_title: str = 'PDF Password Remover',
    on_updated=None,
):
    """Modal to request owner approval & enter a Machine-ID-locked Paid Add-on Key (`CDADD1...`)."""
    dlg = ctk.CTkToplevel(parent)
    dlg.title(f'Activate Paid Add-on — {feature_title}')
    dlg.geometry('680x500')
    dlg.minsize(600, 460)
    dlg.configure(fg_color=BG)
    dlg.transient(parent)
    dlg.grab_set()

    mid = license_guard.get_machine_id()
    box = ctk.CTkFrame(dlg, fg_color=WHITE, corner_radius=16, border_width=1, border_color=BORDER)
    box.pack(fill='both', expand=True, padx=22, pady=20)

    _lbl(box, 'PAID ADD-ON  •  OWNER APPROVAL REQUIRED PER COMPUTER', size=10, bold=True, color='#FFDBA2').pack(
        anchor='w', padx=20, pady=(18, 3)
    )
    _lbl(box, f'Unlock {feature_title} on This Computer', size=19, bold=True).pack(
        anchor='w', padx=20, pady=(0, 5)
    )
    _lbl(
        box,
        'This paid function requires owner approval and a Paid Add-on Key (CDADD1...) signed specifically '
        'for this computer\'s Machine ID. A key from another PC will not work.',
        size=12, color=MUTED, wraplength=600, justify='left',
    ).pack(anchor='w', padx=20, pady=(0, 12))

    # Step 1: Send Machine ID to Owner for approval & payment
    step1 = ctk.CTkFrame(box, fg_color='#131B2A', corner_radius=12, border_width=1, border_color=BORDER)
    step1.pack(fill='x', padx=20, pady=(0, 12))
    _lbl(step1, 'STEP 1 — REQUEST OWNER APPROVAL WITH THIS MACHINE ID', size=10, bold=True, color=LIME).pack(
        anchor='w', padx=14, pady=(10, 4)
    )
    mid_row = ctk.CTkFrame(step1, fg_color='transparent')
    mid_row.pack(fill='x', padx=14, pady=(0, 6))
    mid_entry = ctk.CTkEntry(
        mid_row, height=36, corner_radius=8, border_color=BORDER, fg_color='#0D1320',
        text_color=LIME, font=ctk.CTkFont(family='Consolas', size=15, weight='bold'),
    )
    mid_entry.insert(0, mid)
    mid_entry.configure(state='readonly')
    mid_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))

    def copy_mid():
        dlg.clipboard_clear()
        dlg.clipboard_append(mid)
        dlg.update_idletasks()
        messagebox.showinfo(
            'Machine ID copied',
            f'Copied Machine ID:\n{mid}\n\nSend this ID to the owner via WhatsApp ({services.DEVELOPER_WHATSAPP_LOCAL}) or email ({services.DEVELOPER_EMAIL}) to purchase & activate {feature_title}.',
            parent=dlg,
        )

    def request_whatsapp():
        msg = (
            f'Hi, I would like to pay for and activate the {feature_title} add-on on my computer. '
            f'My Machine ID is: {mid}'
        )
        try:
            services.open_external_url(services.whatsapp_chat_url(msg))
        except ValueError as exc:
            messagebox.showerror('Could not open WhatsApp', str(exc), parent=dlg)

    _btn(mid_row, '⧉ Copy ID', copy_mid, secondary=True, width=110, height=36).pack(side='left', padx=(0, 8))
    _btn(
        mid_row, '💬 Request Approval (WhatsApp)', request_whatsapp,
        fg_color='#25D366', hover_color='#1EBE5D', text_color='#0B1F13', width=225, height=36,
    ).pack(side='left')
    _lbl(
        step1,
        f'Owner WhatsApp: {services.DEVELOPER_WHATSAPP_LOCAL}   •   Email: {services.DEVELOPER_EMAIL}',
        size=11, color=MUTED,
    ).pack(anchor='w', padx=14, pady=(0, 10))

    # Step 2: Enter Paid Add-on Key
    step2 = ctk.CTkFrame(box, fg_color='#131B2A', corner_radius=12, border_width=1, border_color=BORDER)
    step2.pack(fill='x', padx=20, pady=(0, 14))
    _lbl(step2, 'STEP 2 — PASTE YOUR APPROVED ADD-ON KEY (CDADD1...)', size=10, bold=True, color=LIME).pack(
        anchor='w', padx=14, pady=(10, 4)
    )
    txt = ctk.CTkTextbox(
        step2, height=82, corner_radius=8, border_width=1, border_color=BORDER,
        fg_color='#0D1320', text_color=INK, font=ctk.CTkFont(family='Consolas', size=12),
    )
    txt.pack(fill='x', padx=14, pady=(0, 12))

    row = ctk.CTkFrame(box, fg_color='transparent')
    row.pack(fill='x', padx=20, pady=(0, 14))

    def apply_addon():
        raw = txt.get('1.0', 'end').strip()
        try:
            info = license_guard.activate_and_save_addon(raw, feature=feature)
        except license_guard.LicenseError as exc:
            messagebox.showerror('Add-on activation failed', str(exc), parent=dlg)
            return
        messagebox.showinfo(
            f'{feature_title} activated!',
            f'{feature_title} is now unlocked on this computer:\n'
            f'• Customer: {info["customer"]}\n'
            f'• Machine ID: {info["machine_id"]}\n'
            f'• Valid until: {info["expires_at"]} ({info["days_remaining"]} days remaining)',
            parent=dlg,
        )
        dlg.destroy()
        if callable(on_updated):
            on_updated(info)

    def browse_addon():
        chosen = filedialog.askopenfilename(
            parent=dlg,
            title='Select .cafeaddon or .cafelicense file',
            filetypes=[('Café Desk Add-on Key', '*.cafeaddon *.cafelicense *.txt *.json'), ('All files', '*.*')],
        )
        if not chosen:
            return
        try:
            txt.delete('1.0', 'end')
            txt.insert('1.0', Path(chosen).read_text(encoding='utf-8').strip())
            apply_addon()
        except OSError as exc:
            messagebox.showerror('Could not read file', str(exc), parent=dlg)

    _btn(row, '✓ Activate Add-on', apply_addon, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=180).pack(
        side='left', padx=(0, 8)
    )
    _btn(row, 'Load key file…', browse_addon, secondary=True, width=145).pack(side='left', padx=(0, 8))
    _btn(row, 'Cancel', dlg.destroy, secondary=True, width=95).pack(side='right')
