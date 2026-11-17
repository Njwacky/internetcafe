"""Activation Dialog & License Status Card for Café Desk (Hardware Machine-ID + 1-Year Lock)."""
from __future__ import annotations

from tkinter import filedialog, messagebox
from pathlib import Path
import customtkinter as ctk

import cloud_sync
import license_guard
import services
import settings_store

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


def open_license_request(parent, machine_id=None):
    """Ask for the business owner's contact details before submitting a request."""
    dlg = ctk.CTkToplevel(parent)
    dlg.title('Request a Café Desk license')
    dlg.geometry('480x440')
    dlg.transient(parent)
    dlg.grab_set()
    mid = machine_id or license_guard.get_machine_id()
    _lbl(dlg, 'Request a license online', size=20, bold=True).pack(anchor='w', padx=24, pady=(22, 8))
    _lbl(dlg, f'Machine ID: {mid}\nYour details will be sent to the Café Desk owner.',
         size=12, color=MUTED, justify='left').pack(anchor='w', padx=24, pady=(0, 12))
    fields = {}
    for key, caption in (('name', 'Your name'), ('company', 'Company / shop name'), ('phone', 'Phone number')):
        _lbl(dlg, caption, size=12, bold=True).pack(anchor='w', padx=24, pady=(6, 2))
        fields[key] = ctk.CTkEntry(dlg, height=36)
        fields[key].pack(fill='x', padx=24)
    def submit():
        send.configure(state='disabled')
        try:
            cloud_sync.request_license_with_contact(settings_store.load_settings(), machine_id=mid,
                name=fields['name'].get(), company=fields['company'].get(), phone=fields['phone'].get())
        except Exception as exc:
            messagebox.showerror('Request not sent', str(exc), parent=dlg)
            send.configure(state='normal')
            return
        messagebox.showinfo('Request sent', 'Your request was sent to the owner. Once approved, use Check online approval.', parent=dlg)
        dlg.destroy()
    send = _btn(dlg, 'Send request', submit)
    send.pack(anchor='w', padx=24, pady=18)


class ActivationWindow(ctk.CTk):
    """Standalone gate window shown before CafeDesk launches if this PC is unlicensed or expired."""

    def __init__(self, initial_status: dict):
        super().__init__()
        self.title('Activate Café Desk')
        self.geometry('760x700')
        self.minsize(700, 640)
        self.configure(fg_color=BG)
        self.activated_info: dict | None = None
        self.machine_id = initial_status.get('machine_id') or license_guard.get_machine_id()
        reason = initial_status.get('reason') or 'This computer needs a 1-year activation key.'

        # A single centred column keeps the eye on one path: ID → key → activate.
        page = ctk.CTkScrollableFrame(self, fg_color='transparent')
        page.pack(fill='both', expand=True)
        outer = ctk.CTkFrame(page, fg_color=WHITE, corner_radius=20, border_width=1, border_color=BORDER)
        outer.pack(fill='both', expand=True, padx=28, pady=24)

        # ---- Header: who this is for, and that it takes a minute -------------------
        _lbl(outer, 'CAFÉ DESK  •  FIRST-TIME SETUP', size=11, bold=True, color=LIME).pack(
            anchor='w', padx=28, pady=(26, 6)
        )
        _lbl(outer, 'Activate this computer', size=26, bold=True).pack(anchor='w', padx=28, pady=(0, 8))
        _lbl(
            outer,
            'One activation key unlocks Café Desk on this PC for a full year. '
            'Send the Machine ID below to the owner, then paste the key you receive.',
            size=13, color=MUTED, wraplength=650, justify='left',
        ).pack(anchor='w', padx=28, pady=(0, 14))

        status_chip = ctk.CTkFrame(outer, fg_color='#1F2937', corner_radius=9, border_width=1, border_color=BORDER)
        status_chip.pack(fill='x', padx=28, pady=(0, 20))
        _lbl(status_chip, reason, size=12, color='#FFDBA2', wraplength=610, justify='left').pack(
            anchor='w', padx=14, pady=10
        )

        # ---- Step 1: Machine ID ---------------------------------------------------
        step1 = ctk.CTkFrame(outer, fg_color='#131B2A', corner_radius=14, border_width=1, border_color=BORDER)
        step1.pack(fill='x', padx=28, pady=(0, 14))
        head1 = ctk.CTkFrame(step1, fg_color='transparent')
        head1.pack(fill='x', padx=18, pady=(14, 2))
        ctk.CTkLabel(head1, text=' 1 ', fg_color=LIME, corner_radius=20, text_color='#142018',
                     font=ctk.CTkFont(family=FONT, size=11, weight='bold')).pack(side='left', padx=(0, 10))
        _lbl(head1, 'Send your Machine ID to the owner', size=13, bold=True).pack(side='left')
        _lbl(
            step1,
            'This ID is tied to this computer\'s hardware. It contains no customer data.',
            size=11, color=MUTED,
        ).pack(anchor='w', padx=58, pady=(0, 10))

        # ID on its own full-width line, actions beneath it: at the minimum window width a
        # narrow field beside two buttons clipped the code customers are asked to read out.
        mid_entry = ctk.CTkEntry(
            step1, height=44, corner_radius=10, border_color=BORDER, fg_color='#0D1320',
            text_color=LIME, font=ctk.CTkFont(family='Consolas', size=18, weight='bold'), justify='center',
        )
        mid_entry.insert(0, self.machine_id)
        mid_entry.configure(state='readonly')
        mid_entry.pack(fill='x', padx=18, pady=(0, 10))
        mid_row = ctk.CTkFrame(step1, fg_color='transparent')
        mid_row.pack(fill='x', padx=18, pady=(0, 14))

        def copy_mid():
            self.clipboard_clear()
            self.clipboard_append(self.machine_id)
            self.update_idletasks()
            messagebox.showinfo(
                'Machine ID copied',
                f'Copied Machine ID:\n{self.machine_id}\n\n'
                f'Send it via WhatsApp ({services.DEVELOPER_WHATSAPP_LOCAL}) or email '
                f'({services.DEVELOPER_EMAIL}) to receive your 1-year activation key.',
                parent=self,
            )

        def whatsapp_mid():
            msg = f'Hi, please issue a 1-Year Cafe Desk Activation Key for Machine ID: {self.machine_id}'
            try:
                services.open_external_url(services.whatsapp_chat_url(msg))
            except ValueError as exc:
                messagebox.showerror('Could not open WhatsApp', str(exc), parent=self)

        _btn(mid_row, 'Copy ID', copy_mid, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=110).pack(
            side='left', padx=(0, 8)
        )
        _btn(mid_row, 'WhatsApp the owner', whatsapp_mid, fg_color='#25D366', hover_color='#1EBE5D',
             text_color='#0B1F13', width=180).pack(side='left')

        _btn(mid_row, 'Request online', lambda: open_license_request(self, self.machine_id), secondary=True, width=145).pack(side='left', padx=(8, 0))

        # ---- Step 2: paste the key ------------------------------------------------
        step2 = ctk.CTkFrame(outer, fg_color='#131B2A', corner_radius=14, border_width=1, border_color=BORDER)
        step2.pack(fill='x', padx=28, pady=(0, 16))
        head2 = ctk.CTkFrame(step2, fg_color='transparent')
        head2.pack(fill='x', padx=18, pady=(14, 2))
        ctk.CTkLabel(head2, text=' 2 ', fg_color=LIME, corner_radius=20, text_color='#142018',
                     font=ctk.CTkFont(family=FONT, size=11, weight='bold')).pack(side='left', padx=(0, 10))
        _lbl(head2, 'Paste the activation key you received', size=13, bold=True).pack(side='left')
        _lbl(
            step2,
            'Keys start with CDLIC1 and are locked to this Machine ID. Have a .cafelicense file? '
            'Use “Load a license file” below.',
            size=11, color=MUTED, wraplength=600, justify='left',
        ).pack(anchor='w', padx=58, pady=(0, 10))
        self.key_text = ctk.CTkTextbox(
            step2, height=96, corner_radius=10, border_width=1, border_color=BORDER,
            fg_color='#0D1320', text_color=INK, font=ctk.CTkFont(family='Consolas', size=12),
        )
        self.key_text.pack(fill='x', padx=18, pady=(0, 14))

        # ---- Actions --------------------------------------------------------------
        def do_activate():
            raw = self.key_text.get('1.0', 'end').strip()
            try:
                info = license_guard.activate_and_save_license(raw)
            except license_guard.LicenseError as exc:
                messagebox.showerror('That key did not work', str(exc), parent=self)
                return
            self.activated_info = info
            seat_line = (
                f'• Shop seat: Seat {info["seat_no"]} of {info["seat_max"]}'
                + (f' ({info["seat_label"]})\n' if info.get('seat_label') else '\n')
                if info.get('seat_no') and info.get('seat_max')
                else ''
            )
            messagebox.showinfo(
                'Café Desk is activated',
                f'This computer is ready to use:\n'
                f'• Licensed to: {info["customer"]}\n'
                f'{seat_line}'
                f'• Machine ID: {info["machine_id"]}\n'
                f'• Valid until: {info["expires_at"]} ({info["days_remaining"]} days remaining)\n\n'
                f'You can renew from About & contact when the year is nearly up.',
                parent=self,
            )
            self.destroy()

        def check_cloud_approval():
            cfg = settings_store.load_settings()
            if not cloud_sync.is_supabase_configured(cfg):
                messagebox.showinfo(
                    'Online approval is not set up yet',
                    'Paste your CDLIC1 key above instead, or ask the owner to enable online approval.',
                    parent=self,
                )
                return
            try:
                res = cloud_sync.sync_machine_entitlements_from_cloud(cfg, machine_id=self.machine_id)
            except Exception as exc:
                messagebox.showerror('Online check could not complete', str(exc), parent=self)
                return
            status = license_guard.current_license_status()
            if status.get('valid'):
                self.activated_info = status
                messagebox.showinfo(
                    'Activated online',
                    'Your approved key was downloaded and applied:\n• ' +
                    '\n• '.join(res.get('activated') or ['Computer activated']),
                    parent=self,
                )
                self.destroy()
            elif res.get('revoked'):
                messagebox.showwarning('License paused',
                    'This license has been paused. Contact the Café Desk owner to request approval again.\n\n' +
                    '\n'.join(res.get('revocation_reasons') or []), parent=self)
            else:
                messagebox.showinfo(
                    'Nothing approved yet',
                    f'No active online licence was found for Machine ID {self.machine_id}.\n\n'
                    'Send your Machine ID to the owner first, then try again.',
                    parent=self,
                )

        def load_file():
            chosen = filedialog.askopenfilename(
                parent=self,
                title='Select your .cafelicense file',
                filetypes=[('Café Desk license', '*.cafelicense *.txt *.json'), ('All files', '*.*')],
            )
            if not chosen:
                return
            try:
                raw = Path(chosen).read_text(encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('Could not read that file', str(exc), parent=self)
                return
            self.key_text.delete('1.0', 'end')
            self.key_text.insert('1.0', raw.strip())
            do_activate()

        actions = ctk.CTkFrame(outer, fg_color='transparent')
        actions.pack(fill='x', padx=28, pady=(0, 8))
        _btn(actions, 'Activate this computer', do_activate, fg_color=LIME, hover_color='#A9EB65',
             text_color='#142018', width=210, height=42).pack(side='left', padx=(0, 10))
        _btn(actions, 'Check online approval', check_cloud_approval, secondary=True, width=190, height=42).pack(
            side='left'
        )

        secondary = ctk.CTkFrame(outer, fg_color='transparent')
        secondary.pack(fill='x', padx=28, pady=(0, 18))
        _btn(secondary, 'Load a license file…', load_file, secondary=True, width=175).pack(side='left', padx=(0, 10))
        _lbl(
            secondary,
            f'Need help?  {services.DEVELOPER_WHATSAPP_LOCAL}  •  {services.DEVELOPER_EMAIL}',
            size=11, color=MUTED,
        ).pack(side='left', padx=(0, 10))
        _btn(secondary, 'Exit', self.destroy, secondary=True, width=80).pack(side='right')


def ensure_computer_licensed() -> dict | None:
    """Check license at startup; if required and missing/invalid, show ActivationWindow.

    Returns license status dict if allowed to run, or `None` if the user closed ActivationWindow.

    Build hygiene (the demo signing key) is reported INSIDE the app as an owner notice card,
    never as a blocking error dialog before the window exists. An installer that greets a new
    customer with a red error box reads like a broken or infected download, so the check keeps
    its teeth where it belongs — the release pipeline (`packaging/build-*.{bat,sh}` and
    `.github/check_release_key.py`) refuses to produce a distributable build at all.
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

    _btn(row, 'Save & Activate', apply_key, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=170).pack(
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

    _btn(mid_row, 'Copy ID', copy_mid, secondary=True, width=110, height=36).pack(side='left', padx=(0, 8))
    _btn(
        mid_row, 'Request Approval (WhatsApp)', request_whatsapp,
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

    def check_addon_online():
        cfg = settings_store.load_settings()
        if not cloud_sync.is_supabase_configured(cfg):
            messagebox.showinfo(
                'Cloud Not Configured Yet',
                'Configure Supabase Cloud in Business Settings to pull approved keys automatically, or paste your CDADD1 key above.',
                parent=dlg,
            )
            return
        try:
            res = cloud_sync.sync_machine_entitlements_from_cloud(cfg, machine_id=mid)
        except Exception as exc:
            messagebox.showerror('Online Check Failed', str(exc), parent=dlg)
            return
        st = license_guard.current_addon_status(feature)
        if st.get('valid'):
            messagebox.showinfo(
                f'{feature_title} Unlocked Online!',
                f'{feature_title} was approved by the owner and activated automatically until {st.get("expires_at")}.',
                parent=dlg,
            )
            dlg.destroy()
            if callable(on_updated):
                on_updated(st)
        else:
            messagebox.showinfo(
                'Not Approved Online Yet',
                f'No active online {feature_title} approval was found yet for Machine ID {mid}.',
                parent=dlg,
            )

    _btn(row, 'Activate Add-on', apply_addon, fg_color=LIME, hover_color='#A9EB65', text_color='#142018', width=170).pack(
        side='left', padx=(0, 8)
    )
    _btn(row, 'Check Online Approval', check_addon_online, secondary=True, width=185).pack(side='left', padx=(0, 8))
    _btn(row, 'Load file…', browse_addon, secondary=True, width=115).pack(side='left', padx=(0, 8))
    _btn(row, 'Cancel', dlg.destroy, secondary=True, width=85).pack(side='right')
