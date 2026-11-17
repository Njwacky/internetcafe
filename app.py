"""Café Desk — CustomTkinter desktop application."""
import os, sys, subprocess, queue, threading, webbrowser, tempfile, uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import time
import customtkinter as ctk
from PIL import Image, ImageOps
import requests

# CustomTkinter 5.2.x can fire placeholder callbacks after a widget has been destroyed,
# which raises TclError during screen changes. Guard the callbacks so the desktop app
# remains stable while switching between views.
_original_ctk_entry_textvariable_callback = ctk.CTkEntry._textvariable_callback
_original_ctk_entry_activate_placeholder = ctk.CTkEntry._activate_placeholder

def _safe_ctk_entry_textvariable_callback(self, var_name, index, mode):
    try:
        if not self.winfo_exists() or not self._entry.winfo_exists():
            return
        if self._textvariable is not None and self._textvariable.get() == "":
            self._activate_placeholder()
    except (AttributeError, tk.TclError):
        pass

def _safe_ctk_entry_activate_placeholder(self):
    try:
        if not self.winfo_exists() or not self._entry.winfo_exists():
            return
        if self._entry.get() == "" and self._placeholder_text is not None and (self._textvariable is None or self._textvariable == ""):
            self._placeholder_text_active = True
            self._pre_placeholder_arguments = {"show": self._entry.cget("show")}
            self._entry.config(fg=self._apply_appearance_mode(self._placeholder_text_color),
                               disabledforeground=self._apply_appearance_mode(self._placeholder_text_color),
                               show="")
            self._entry.delete(0, tk.END)
            self._entry.insert(0, self._placeholder_text)
    except (AttributeError, tk.TclError):
        pass

ctk.CTkEntry._textvariable_callback = _safe_ctk_entry_textvariable_callback
ctk.CTkEntry._activate_placeholder = _safe_ctk_entry_activate_placeholder

from templates import NAMES, CV_NAMES, CATEGORIES, LABELS, field_labels_for, uses_company_field, make_document
import services
import settings_store
import sales_store
import price_list
import projects
import privacy
import bluetooth_access
from sales_ui import show_reports
from collage_ui import PictureSheet
from scanner_ui import show_scanner
from ocr_ui import show_ocr
import cards_renderer
from cards_ui import show_cards
import forms_renderer
from forms_ui import show_forms
import license_guard
import license_ui
import cloud_sync

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('green')
APP_BUILD='0.8.2'
BG='#101522'; WHITE='#1B2333'; INK='#F2F4FA'; MUTED='#A2ACC5'; GREEN='#445A86'; GREEN_HOVER='#536B9D'; LIME='#B9FF70'; BORDER='#34415A'; SOFT='#303C55'; RED='#E89886'
FONT='Segoe UI'

def label(parent,text,size=13,bold=False,color=INK,**kw):
    return ctk.CTkLabel(parent,text=text,text_color=color,font=ctk.CTkFont(family=FONT,size=size,weight='bold' if bold else 'normal'),**kw)

# ---- Button scale -----------------------------------------------------------
# One metric for every screen: primary (the one thing to do), secondary (an equal
# alternative), ghost (small/utility, e.g. "Copy link"). Heights come from the
# scale instead of being hand-tuned per call, so rows line up and nothing is tiny.
BTN_HEIGHT={'primary':38,'secondary':38,'ghost':32}
BTN_PADX=10          # gap between buttons in a row
BTN_PADY=(0,10)      # gap below a button row
BTN_WRAP=620         # px before a button row wraps onto a second line

def button(parent,text,cmd,secondary=False,ghost=False,**kw):
    if ghost:
        opts=dict(text=text,command=cmd,corner_radius=9,height=BTN_HEIGHT['ghost'],
                  font=ctk.CTkFont(family=FONT,size=12,weight='normal'),
                  fg_color='transparent',hover_color=SOFT,text_color=MUTED,border_width=1,border_color=BORDER)
    elif secondary:
        opts=dict(text=text,command=cmd,corner_radius=10,height=BTN_HEIGHT['secondary'],
                  font=ctk.CTkFont(family=FONT,size=13,weight='bold'),
                  fg_color=SOFT,hover_color='#40506D',text_color='#E1E8F6')
    else:
        opts=dict(text=text,command=cmd,corner_radius=10,height=BTN_HEIGHT['primary'],
                  font=ctk.CTkFont(family=FONT,size=13,weight='bold'),
                  fg_color=GREEN,hover_color=GREEN_HOVER,text_color='white')
    opts.update(kw); return ctk.CTkButton(parent,**opts)

def button_row(parent,padx=None,pady=BTN_PADY,wrap=BTN_WRAP):
    """A transparent container for one horizontal group of buttons, with even gaps.

    Use `action_row` for cards: primary first, then secondary, then ghost utilities.
    """
    row=ctk.CTkFrame(parent,fg_color='transparent')
    row.pack(fill='x',padx=padx or 20,pady=pady)
    row._cd_wrap=wrap; row._cd_used=0
    return row

def action_row(parent,primary=(),secondary=(),ghost=(),padx=20,pady=BTN_PADY,wrap=BTN_WRAP):
    """Lay out a card's actions as ONE ordered group on a consistent grid.

    primary/secondary/ghost are tuples of (text, command) pairs. Every button uses
    the shared height scale and an even gap, so mixed hand-picked widths are gone.
    """
    row=button_row(parent,padx=padx,pady=pady,wrap=wrap)
    made=[]
    for spec,opts in ((primary,{}),(secondary,{'secondary':True}),(ghost,{'ghost':True})):
        for text,cmd in spec:
            made.append(button(row,text,cmd,**opts).pack(side='left',padx=(0,BTN_PADX)))
    return row

def utility_row(parent,padx=20,pady=(0,10)):
    """Small right-aligned utilities (Copy / Open) kept visually quieter than actions."""
    return button_row(parent,padx=padx,pady=pady)

def card(parent,**kw): return ctk.CTkFrame(parent,fg_color=WHITE,corner_radius=16,border_width=1,border_color=BORDER,**kw)
def entry(parent,placeholder='',**kw): return ctk.CTkEntry(parent,placeholder_text=placeholder,height=38,corner_radius=9,border_color=BORDER,fg_color='#121A29',text_color=INK,**kw)

class CafeDesk(ctk.CTk):
    def __init__(self):
        super().__init__(); self.title('Café Desk'); self.geometry('1250x800'); self.minsize(950,680); self.configure(fg_color=BG)
        self.events=queue.Queue(); self.busy=False; self.idle=privacy.IdleLock(300); self.recovery_enabled=False; self.session_temp=tempfile.TemporaryDirectory(prefix='cafedesk-'); self.protocol('WM_DELETE_WINDOW',self.close_app); self.cv_photo=None; self.cv_thumbnail=None; self.doc_preview_page=1; self.doc_preview_total=1; self.doc_preview_image=None; self.doc_preview_label=None; self.doc_preview_page_label=None; self.company_cell=None; self.role_label_widget=None; self.company_label_widget=None; self.sheet={'items':[],'preset':'4 per page','page':0,'selected':None,'mode':'Fit whole image','brightness':100,'gray':False}; self.card_state={'kind':'Business card','template':cards_renderer.CARD_NAMES[0],'sheet':cards_renderer.SHEET_OPTIONS['Business card'][0][0],'crop_marks':True,'photo':None,'business_fields':cards_renderer.default_fields('Business card'),'invitation_fields':cards_renderer.default_fields('Invitation card')}; self.form_state=forms_renderer.default_form_spec(); self.staff_unlocked=False; self.failed_attempts=0; self.lockout_until=0; self.receipt_items=[]; self.receipt_currency=None; self.receipt_token=uuid.uuid4().hex; self.receipt_timestamp=None; self.receipt_business=None; self.receipt_recorded=False; self.ocr_text=''; self.ocr_editor=None; self.ocr_images=[]; self.ocr_selected_index=None; self.ocr_scan_animating=True; self.pdf_files=[]; self.image_files=[]; self.split_source=''; self.split_preview_fingerprint=None; self.unlock_pdf_source=''; self.scanner_devices=[]; self.scanner_device_id=None; self.scanned_pages=[]; self.doc_type=ctk.StringVar(value=CATEGORIES[0]); self.template=ctk.StringVar(value=NAMES[0]); self.ai_provider=ctk.StringVar(value='Auto-detect from API key'); self.ai_custom_url=ctk.StringVar(); self.model=ctk.StringVar(value='gpt-4o-mini'); self.key=ctk.StringVar(); self.ai_generation=0; self.values={k:ctk.StringVar() for k in LABELS}; self.status=ctk.StringVar(value=f'Ready  •  v{APP_BUILD}  •  Local document processing')
        settings_corrupted=False
        try:
            preferences=settings_store.load_settings(strict=True)
        except settings_store.CorruptedSettingsError:
            settings_corrupted=True
            preferences=settings_store.load_settings(strict=False)
        self.business_name=ctk.StringVar(value=preferences['business_name']); self.business_icon=preferences['business_icon']; self.icon_images=self.load_icon_images(); self.clock=ctk.StringVar(); self.body=None; self.doc_editor=None; self.long_fields={}; self.doc_text=''; self.nav_buttons={}; self.current=''; self._telemetry_lock=threading.Lock(); self._telemetry_counts={}; self._telemetry_inflight=False; self._last_telemetry_flush=0.0; self._entitlement_sync_running=False; self.create_shell(); self.title(self.business_name.get()); self.show('Home'); self.update_clock(); self.after(100,self.poll_events); self.after(5000,self.check_idle); self.after(60000,self.recovery_tick); self.after(45000,self._telemetry_tick); self.after(12000,self.remote_entitlement_tick)
        if settings_corrupted:
            self.status.set('⚠ Settings file damaged • staff access locked fail-closed until restored')
        for sequence in ('<KeyPress>','<ButtonPress>','<MouseWheel>','<Motion>'): self.bind_all(sequence,self.note_activity,add='+')
        self.bind_all('<F1>',lambda _e:self.quick_help(),add='+')
        self.bind_all('<Control-l>',lambda _e:self.lock_staff(),add='+')
        self.bind_all('<Control-L>',lambda _e:self.lock_staff(),add='+')
        self.bind_all('<Control-Shift-N>',lambda _e:self.clear_customer(),add='+')
        self.bind_all('<Control-Shift-n>',lambda _e:self.clear_customer(),add='+')
        lic_init=license_guard.current_license_status()
        if lic_init.get('valid') and lic_init.get('days_remaining',999)<=14 and not settings_corrupted:
            self.status.set(f"⚠ License expires in {lic_init['days_remaining']} day(s) ({lic_init['expires_at']}) • open About & contact to renew")
    def note_activity(self,event=None): self.idle.touch()
    def check_idle(self):
        if self.staff_unlocked and self.idle.expired(): self.lock_staff(); self.status.set('Staff access locked after five minutes of inactivity')
        self.after(5000,self.check_idle)
    def has_unsaved_work(self):
        """Return True if any active tool holds unsaved customer text, files, scans, cards or forms."""
        if self.current=='Career documents': self.sync_long_fields()
        if self.current=='Cards & invitations' and callable(getattr(self,'_sync_cards_hook',None)):
            try: self._sync_cards_hook()
            except Exception: pass
        if self.current=='Forms creator' and callable(getattr(self,'_sync_forms_hook',None)):
            try: self._sync_forms_hook()
            except Exception: pass
        ocr_live=(self.ocr_editor and self.ocr_editor.winfo_exists() and self.ocr_editor.get('1.0','end-1c').strip())
        card_dirty=bool(
            self.card_state.get('photo')
            or any(str(v).strip() for v in self.card_state.get('business_fields',{}).values())
            or any(str(v).strip() for v in self.card_state.get('invitation_fields',{}).values())
        )
        default_form=forms_renderer.default_form_spec(self.form_state.get('template','General Application Form'))
        form_dirty=(
            str(self.form_state.get('organization','')).strip()!=''
            or self.form_state.get('title')!=default_form.get('title')
            or self.form_state.get('subtitle')!=default_form.get('subtitle')
            or self.form_state.get('instructions')!=default_form.get('instructions')
            or bool(self.form_state.get('fillable',True))!=bool(default_form.get('fillable',True))
            or bool(self.form_state.get('signature_block',True))!=bool(default_form.get('signature_block',True))
            or self.form_state.get('fields')!=default_form.get('fields')
        )
        return bool(
            self.current_text().strip()
            or any(v.get().strip() for v in self.values.values())
            or self.cv_photo
            or self.sheet['items']
            or self.receipt_items
            or self.scanned_pages
            or self.ocr_text.strip()
            or ocr_live
            or self.ocr_images
            or self.pdf_files
            or self.image_files
            or self.split_source
            or self.unlock_pdf_source
            or card_dirty
            or form_dirty
        )
    def close_app(self):
        if self.busy: return messagebox.showinfo('Please wait','A document is still being processed. Wait for it to finish before closing.',parent=self)
        if self.has_unsaved_work() and not messagebox.askyesno('Close Café Desk?','Current workspace data (drafts, scans, OCR text/images, PDF queues, cards, forms or sheets) will be closed. Save any work you need first. If recovery is enabled, the latest copy may not include recent changes. Close anyway?',parent=self): return
        self.remove_session_files(); self.destroy()
    def remove_session_files(self):
        try: self.session_temp.cleanup()
        except OSError: messagebox.showwarning('Temporary files remain',f'Could not remove temporary files. Check this folder manually:\n{self.session_temp.name}',parent=self)
    def load_icon_images(self):
        root=Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        images={}
        for key in settings_store.ICONS:
            if key=='custom': continue
            with Image.open(root / 'assets' / 'icons' / (key+'.png')) as image:
                images[key]=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(40,40))
        with Image.open(root / 'assets' / 'icons' / 'bluetooth.png') as image:
            images['bluetooth_card']=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(34,34))
        with Image.open(root / 'assets' / 'icons' / 'bluetooth-nav.png') as image:
            cropped=image.crop(image.getbbox() or (0,0,image.width,image.height))
            images['bluetooth_nav']=ctk.CTkImage(light_image=cropped.copy(),dark_image=cropped.copy(),size=(13,16))
        with Image.open(root / 'assets' / 'icons' / 'bluetooth-nav-muted.png') as image:
            cropped=image.crop(image.getbbox() or (0,0,image.width,image.height))
            images['bluetooth_nav_muted']=ctk.CTkImage(light_image=cropped.copy(),dark_image=cropped.copy(),size=(13,16))
        with Image.open(root / 'assets' / 'icons' / 'enlarge.png') as image:
            images['enlarge']=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(16,16))
        with Image.open(root / 'assets' / 'icons' / 'lock-staff.png') as image:
            images['lock_staff']=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(18,18))
        with Image.open(root / 'assets' / 'icons' / 'unlock-staff.png') as image:
            images['unlock_staff']=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(18,18))
        try:
            with services.open_safe_image(settings_store.logo_path(),allow_internal=True) as image: images['custom']=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(40,40))
        except (OSError,ValueError): images['custom']=images['office']
        return images
    def update_clock(self):
        self.clock.set(datetime.now().strftime('%a, %d %b  %H:%M:%S'))
        self.after(1000,self.update_clock)
    def business_screen(self):
        self.heading(self.body,'Business settings','Make this workspace feel like your own.')
        box=card(self.body); box.pack(fill='x')
        label(box,'Your business name & icon',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,5))
        label(box,'Choose a name, an icon or your own logo. The name also appears in the window title.',size=12,color=MUTED,justify='left',wraplength=700).pack(anchor='w',padx=20,pady=(0,15))
        label(box,'Business name • up to 48 characters',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        business_input=entry(box,'Enter your business name'); business_input.insert(0,self.business_name.get()); business_input.pack(fill='x',padx=20,pady=(0,10))
        label(box,'Business icon • 10 options or custom logo',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,5))
        icon_row=ctk.CTkFrame(box,fg_color='transparent'); icon_row.pack(fill='x',padx=20,pady=(0,13))
        icon_preview=ctk.CTkLabel(icon_row,image=self.icon_images[self.business_icon],text=''); icon_preview.pack(side='left',padx=(0,12))
        icon_choice=ctk.StringVar(value=settings_store.ICONS[self.business_icon]); selected_logo={'path':None}
        def on_icon_change(selection):
            key=next(k for k,v in settings_store.ICONS.items() if v==selection); icon_preview.configure(image=self.icon_images[key])
        ctk.CTkOptionMenu(icon_row,values=list(settings_store.ICONS.values()),variable=icon_choice,command=on_icon_change,fg_color=GREEN,button_color=GREEN_HOVER,height=36,width=180).pack(side='left')
        def choose_logo():
            path=filedialog.askopenfilename(parent=self,filetypes=[('Image files','*.png *.jpg *.jpeg *.bmp')])
            if not path: return
            try:
                with services.open_safe_image(path) as original:
                    image=ImageOps.exif_transpose(original).convert('RGBA'); image.thumbnail((256,256)); sample=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(40,40))
                selected_logo['path']=path; selected_logo['sample']=sample; icon_choice.set(settings_store.ICONS['custom']); icon_preview.configure(image=sample)
            except Exception as exc: messagebox.showerror('Invalid logo',str(exc),parent=self)
        button(icon_row,'Upload logo',choose_logo,secondary=True,width=125).pack(side='left',padx=(12,0))
        def save():
            try:
                icon=next(k for k,v in settings_store.ICONS.items() if v==icon_choice.get())
                if icon=='custom' and selected_logo['path']:
                    services.save_custom_logo(selected_logo['path'],settings_store.logo_path()); self.icon_images['custom']=selected_logo['sample']
                elif icon=='custom' and not settings_store.logo_path().exists(): raise ValueError('Upload a logo before selecting Custom logo.')
                value=settings_store.save_settings(business_input.get(),icon); self.business_name.set(value); self.title(value); self.business_icon=icon; self.brand_icon.configure(image=self.icon_images[icon])
                business_input.delete(0,'end'); business_input.insert(0,value)
                self.status.set('Business identity saved on this computer account')
                messagebox.showinfo('Business settings saved','Your business name and icon have been saved on this computer account.',parent=self)
            except (ValueError,OSError) as exc: messagebox.showerror('Could not save business name',str(exc),parent=self)
        action_row(box,
                   primary=(('Save business settings',save),),
                   secondary=(('Change staff PIN',self.change_pin),),
                   pady=(0,15))
        note=ctk.CTkFrame(box,fg_color='#273248',corner_radius=10); note.pack(fill='x',padx=20,pady=(0,20))
        label(note,'Shared workspace',size=12,bold=True,color='#B9FF70').pack(anchor='w',padx=14,pady=(11,3))
        label(note,'Business settings are staff PIN-protected. The name and icon are stored for this computer user, not on the USB.',size=12,color='#B9C7DC',justify='left',wraplength=680).pack(anchor='w',padx=14,pady=(0,12))

        # --- Multi-PC Shop & Supabase Cloud Configuration Card ---
        mp_cfg=settings_store.load_settings()
        lic_cur=license_guard.current_license_status()
        mp_box=card(self.body); mp_box.pack(fill='x',pady=(12,12))
        label(mp_box,'Multi-PC Shop (2+ Computers) & Supabase Cloud Sync',size=16,bold=True,color=LIME).pack(anchor='w',padx=20,pady=(18,4))
        label(
            mp_box,
            'Running 2, 4, or 10+ computers in one shop? Give each PC a clear Station Name (e.g. "Counter 1", "Desk 2") so Sales Reports show which PC recorded each sale. '
            'Sync prices & sales across PCs offline via a Shop Pack / Shared LAN Folder, or over the internet via Supabase (customer documents are NEVER uploaded).',
            size=12,color=MUTED,justify='left',wraplength=740,
        ).pack(anchor='w',padx=20,pady=(0,12))

        grid_mp=ctk.CTkFrame(mp_box,fg_color='transparent'); grid_mp.pack(fill='x',padx=20,pady=(0,10))
        grid_mp.grid_columnconfigure(0,weight=1); grid_mp.grid_columnconfigure(1,weight=1)

        c_left=ctk.CTkFrame(grid_mp,fg_color='transparent'); c_left.grid(row=0,column=0,sticky='ew',padx=(0,8))
        label(c_left,'This Computer / Station Name (shown on Sales Reports)',size=11,bold=True,color=MUTED).pack(anchor='w',pady=(0,4))
        st_in=entry(c_left,'e.g. Counter PC 1 or Desk 2')
        st_in.insert(0,mp_cfg.get('station_name') or lic_cur.get('seat_label') or '')
        st_in.pack(fill='x')

        c_right=ctk.CTkFrame(grid_mp,fg_color='transparent'); c_right.grid(row=0,column=1,sticky='ew',padx=(8,0))
        label(c_right,'Shop ID (shared by all PCs in your shop, e.g. SHOP-SUNRISE)',size=11,bold=True,color=MUTED).pack(anchor='w',pady=(0,4))
        sh_in=entry(c_right,'e.g. SHOP-SUNRISE')
        sh_in.insert(0,mp_cfg.get('shop_id') or lic_cur.get('shop_id') or '')
        sh_in.pack(fill='x')

        label(mp_box,'Shop City / Suburb / Branch Area (optional — shown on Owner Cloud Analytics)',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        city_in=entry(mp_box,'e.g. Benoni, Gauteng  or  Johannesburg CBD')
        if mp_cfg.get('city_label'): city_in.insert(0,mp_cfg['city_label'])
        city_in.pack(fill='x',padx=20,pady=(0,10))

        label(mp_box,'Optional Shared LAN / USB Sync Folder (for offline multi-PC sales sync)',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        lan_row=ctk.CTkFrame(mp_box,fg_color='transparent'); lan_row.pack(fill='x',padx=20,pady=(0,10))
        lan_in=entry(lan_row,'e.g. \\\\COUNTER-PC\\CafeDeskSync or D:\\ShopSync')
        if mp_cfg.get('lan_sync_folder'): lan_in.insert(0,mp_cfg['lan_sync_folder'])
        lan_in.pack(side='left',fill='x',expand=True,padx=(0,8))
        def pick_lan_dir():
            d=filedialog.askdirectory(parent=self,title='Choose Shared LAN / USB Sync Folder')
            if d: lan_in.delete(0,'end'); lan_in.insert(0,d)
        button(lan_row,'Browse…',pick_lan_dir,secondary=True,width=105).pack(side='left')

        label(mp_box,'Optional Supabase Project URL (for remote over-the-internet updates & cloud shop sync)',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        sb_url_in=entry(mp_box,'https://your-project.supabase.co')
        if mp_cfg.get('supabase_url'): sb_url_in.insert(0,mp_cfg['supabase_url'])
        sb_url_in.pack(fill='x',padx=20,pady=(0,10))

        label(mp_box,'Supabase Public Anon Key (PUBLIC anon key ONLY — NEVER enter service_role key here)',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        sb_key_in=entry(mp_box,'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...')
        if mp_cfg.get('supabase_anon_key'): sb_key_in.insert(0,mp_cfg['supabase_anon_key'])
        sb_key_in.pack(fill='x',padx=20,pady=(0,14))

        def save_mp():
            try:
                saved=settings_store.save_multi_pc_settings(
                    st_in.get(),
                    shop_id=sh_in.get(),
                    lan_sync_folder=lan_in.get(),
                    supabase_url=sb_url_in.get(),
                    supabase_anon_key=sb_key_in.get(),
                    city_label=city_in.get(),
                )
                self.status.set(f"Saved Multi-PC station '{saved['station_name']}'" + (f" • Shop {saved['shop_id']}" if saved.get('shop_id') else ''))
                messagebox.showinfo('Multi-PC & Cloud Settings Saved',f"Saved settings for station: {saved['station_name']}",parent=self)
                self._flush_telemetry_async(force=True)
            except Exception as exc:
                messagebox.showerror('Could not save settings',str(exc),parent=self)

        def export_pack():
            dest=filedialog.asksaveasfilename(
                parent=self,
                defaultextension='.cafeshop.json',
                filetypes=[('Café Desk Shop Pack','*.cafeshop.json'),('JSON','*.json')],
                initialfile=f"{(sh_in.get().strip() or 'shop').lower()}-prices.cafeshop.json",
            )
            if not dest: return
            try:
                save_mp()
                settings_store.export_shop_profile_pack(dest)
                messagebox.showinfo('Shop Price Pack Exported',f'Saved Shop Pack to:\n{dest}\n\nCopy this file to your other shop PCs and click "Import Shop Pack" there to clone all prices in 1 click.',parent=self)
            except Exception as exc:
                messagebox.showerror('Export failed',str(exc),parent=self)

        def import_pack():
            src=filedialog.askopenfilename(
                parent=self,
                title='Select .cafeshop.json Price & Profile Pack',
                filetypes=[('Café Desk Shop Pack','*.cafeshop.json *.json'),('All files','*.*')],
            )
            if not src: return
            try:
                updated=settings_store.import_shop_profile_pack(src)
                self.business_name.set(updated['business_name']); self.title(updated['business_name'])
                messagebox.showinfo('Shop Pack Imported',f"Imported {len(updated.get('prices',[]))} service price(s) for '{updated['business_name']}' (this PC's Station Name & PIN were preserved).",parent=self)
                self.show('Business settings')
            except Exception as exc:
                messagebox.showerror('Import failed',str(exc),parent=self)

        def cloud_push_prices():
            try:
                saved=settings_store.save_multi_pc_settings(st_in.get(),shop_id=sh_in.get(),lan_sync_folder=lan_in.get(),supabase_url=sb_url_in.get(),supabase_anon_key=sb_key_in.get(),city_label=city_in.get())
            except Exception as exc:
                return messagebox.showerror('Check settings',str(exc),parent=self)
            if not saved.get('shop_id') or not cloud_sync.is_supabase_configured(saved):
                return messagebox.showinfo('Setup needed','Enter a Shop ID plus your Supabase Project URL and Anon Key above first.',parent=self)
            sec=simpledialog.askstring('Push Shop Prices to Supabase',f"Set/enter Shop Sync Passcode for {saved['shop_id']} (10+ characters):",show='•',parent=self)
            if not sec: return
            self.job('Uploading shop price list to Supabase…',lambda:cloud_sync.push_shop_price_profile(saved,sec),lambda _:messagebox.showinfo('Prices Pushed to Cloud',f"Uploaded {len(saved.get('prices',[]))} price(s) for {saved['shop_id']}. Other PCs in your shop can now click 'Pull Prices from Cloud'.",parent=self))

        def cloud_pull_prices():
            try:
                saved=settings_store.save_multi_pc_settings(st_in.get(),shop_id=sh_in.get(),lan_sync_folder=lan_in.get(),supabase_url=sb_url_in.get(),supabase_anon_key=sb_key_in.get(),city_label=city_in.get())
            except Exception as exc:
                return messagebox.showerror('Check settings',str(exc),parent=self)
            if not saved.get('shop_id') or not cloud_sync.is_supabase_configured(saved):
                return messagebox.showinfo('Setup needed','Enter your Shop ID plus Supabase URL and Anon Key above first.',parent=self)
            sec=simpledialog.askstring('Pull Shop Prices from Supabase',f"Enter Shop Sync Passcode for {saved['shop_id']}:",show='•',parent=self)
            if not sec: return
            def _do_pull():
                res=cloud_sync.pull_shop_price_profile(saved,sec)
                lines='\n'.join(f"{p.get('name','')} | {p.get('price','0.00')}" for p in (res.get('prices') or []) if isinstance(p,dict) and p.get('name'))
                settings_store.save_prices(str(res.get('currency') or 'R'),lines)
                settings_store.save_name(str(res.get('business_name') or saved['business_name']))
                return res
            def _done_pull(res):
                bname=str(res.get('business_name') or self.business_name.get())
                self.business_name.set(bname); self.title(bname)
                messagebox.showinfo('Prices Synced from Cloud',f"Pulled latest prices for '{bname}' (updated by {res.get('updated_by') or 'shop'}).",parent=self)
                self.show('Business settings')
            self.job('Pulling shop price list from Supabase…',_do_pull,_done_pull)

        label(mp_box,'Shop pack',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,6))
        action_row(mp_box,
                   primary=(('Save Multi-PC & Cloud Settings',save_mp),),
                   secondary=(('📤 Export Shop Pack (.cafeshop)',export_pack),
                              ('📥 Import Shop Pack',import_pack)),
                   pady=(0,14))

        label(mp_box,'Cloud price sync',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,6))
        action_row(mp_box,
                   secondary=(('☁ Push Prices to Cloud',cloud_push_prices),
                              ('☁ Pull Prices from Cloud',cloud_pull_prices)),
                   pady=(0,18))
    def require_staff(self):
        if self.staff_unlocked: return True
        try:
            rem=settings_store.lockout_remaining()
            if rem>0 or time.monotonic()<self.lockout_until:
                wait_s=max(1,int(max(rem,self.lockout_until-time.monotonic())))
                messagebox.showwarning('Try again later',f'Too many incorrect PIN attempts. Wait {wait_s} seconds.',parent=self); return False
            if not settings_store.pin_is_set():
                messagebox.showinfo('Set up staff access','The first staff member must set a 6–12 digit PIN. Keep it private. This is a basic local access control, not protection against someone with computer administrator access.',parent=self)
                pin=simpledialog.askstring('Create staff PIN','Choose a 6–12 digit PIN:',show='*',parent=self)
                if pin is None: return False
                confirm=simpledialog.askstring('Confirm staff PIN','Enter the PIN again:',show='*',parent=self)
                if confirm is None: return False
                if pin!=confirm: raise ValueError('PINs do not match.')
                settings_store.set_pin(pin)
            else:
                pin=simpledialog.askstring('Staff access','Enter your staff PIN:',show='*',parent=self)
                if pin is None: return False
                if not settings_store.verify_pin(pin):
                    rem_after=settings_store.lockout_remaining()
                    if rem_after>0: self.lockout_until=time.monotonic()+rem_after
                    messagebox.showerror('Staff access','Incorrect PIN. After 5 failed attempts, staff access locks for 60 seconds.',parent=self); return False
            self.staff_unlocked=True; self.idle.touch(); self.failed_attempts=0; self.lockout_until=0; self.update_lock_button(); self.status.set('Staff settings unlocked • click the lock icon before leaving the computer'); return True
        except settings_store.CorruptedSettingsError as exc:
            bak=settings_store.settings_path().with_name('settings.json.bak')
            if bak.is_file() and messagebox.askyesno('Settings damaged (fail-closed)',f'{exc}\n\nA verified backup (settings.json.bak) exists. Restore settings.json.bak now so you can enter your staff PIN?',parent=self):
                try:
                    restored=settings_store.restore_backup_settings()
                    self.business_name.set(restored['business_name']); self.title(restored['business_name'])
                    messagebox.showinfo('Backup restored','settings.json.bak was restored (the damaged file was quarantined as settings.json.corrupt). Click again to enter your staff PIN.',parent=self)
                except Exception as r_exc:
                    messagebox.showerror('Restore failed',f'Could not restore settings.json.bak:\n{r_exc}\n\nAn OS administrator must inspect:\n{settings_store.settings_path().parent}',parent=self)
            else:
                messagebox.showerror('Settings damaged (fail-closed)',f'{exc}\n\nFolder:\n{settings_store.settings_path().parent}',parent=self)
            return False
        except (ValueError,OSError) as exc: messagebox.showerror('Staff access',str(exc),parent=self); return False
    def change_pin(self):
        try:
            rem=settings_store.lockout_remaining()
            if rem>0 or time.monotonic()<self.lockout_until:
                wait_s=max(1,int(max(rem,self.lockout_until-time.monotonic())))
                return messagebox.showwarning('Try again later',f'Too many incorrect PIN attempts. Wait {wait_s} seconds.',parent=self)
            old=simpledialog.askstring('Change staff PIN','Enter current PIN:',show='*',parent=self)
            if old is None: return
            if not settings_store.verify_pin(old):
                rem_after=settings_store.lockout_remaining()
                if rem_after>0: self.lockout_until=time.monotonic()+rem_after
                return messagebox.showerror('Staff access','Incorrect current PIN.',parent=self)
            new=simpledialog.askstring('Change staff PIN','Enter a new 6–12 digit PIN:',show='*',parent=self)
            if new is None: return
            confirm=simpledialog.askstring('Confirm staff PIN','Enter the new PIN again:',show='*',parent=self)
            if confirm is None: return
            if new!=confirm: raise ValueError('PINs do not match.')
            settings_store.set_pin(new); messagebox.showinfo('PIN updated','Staff PIN changed.',parent=self)
        except (ValueError,OSError) as exc: messagebox.showerror('Could not update PIN',str(exc),parent=self)
    def update_lock_button(self):
        btn=getattr(self,'lock_staff_btn',None)
        if btn and btn.winfo_exists():
            btn.configure(image=self.icon_images['unlock_staff' if self.staff_unlocked else 'lock_staff'])
    def lock_staff(self):
        self.staff_unlocked=False; self.key.set(''); self.ai_generation+=1; self.update_lock_button()
        if self.current in ('Business settings','Price settings','AI settings','Sales reports'): self.show('Home')
        self.status.set('Staff access locked • session API key cleared')
    def _career_snapshot(self):
        if self.current=='Career documents': self.sync_long_fields()
        return {'category':self.doc_type.get(),'template':self.template.get(),'fields':{k:v.get() for k,v in self.values.items()},'draft':self.current_text(),'photo':self.cv_photo}
    def _sheet_snapshot(self):
        return {**self.sheet,'items':[dict(item) for item in self.sheet['items']]}
    def save_project(self,kind):
        if self.busy: return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
        snapshot=self._career_snapshot() if kind=='career' else self._sheet_snapshot()
        if kind=='career' and not snapshot['draft'].strip() and not any(x.strip() for x in snapshot['fields'].values()) and not snapshot['photo']:
            return messagebox.showinfo('No work to save','Enter a document or details first.',parent=self)
        if kind=='sheet' and not snapshot['items']: return messagebox.showinfo('No pictures','Add pictures first.',parent=self)
        if not messagebox.askyesno('Save private project','The project will include document details and copies of any photos. It is not encrypted. Save only where appropriate for the customer. Continue?',parent=self): return
        filename='career-project.cafeproject' if kind=='career' else 'picture-sheet.cafeproject'
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.cafeproject',filetypes=[('Café Desk project','*.cafeproject')],initialfile=filename)
        if path:self.job('Saving project…',lambda:projects.save(kind,snapshot,path),lambda _:messagebox.showinfo('Project saved',f'Project saved to:\n{path}\n\nKeep it private; it includes photos.',parent=self))
    def open_project(self,path=None,allow_recovery=False):
        if self.busy: return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
        path=path or filedialog.askopenfilename(parent=self,filetypes=[('Café Desk project','*.cafeproject')])
        if not path:return
        if not allow_recovery and settings_store.is_internal_path(path):
            return messagebox.showerror('Staff recovery required','Projects inside the Café Desk recovery or configuration folder require Staff recovery. Use Recover career / Recover picture sheet instead.',parent=self)
        def loaded(result):
            kind,data=result;what='career document' if kind=='career' else 'picture sheet'
            if not messagebox.askyesno('Open project',f'Opening this project replaces the current {what} in the workspace. Save your current work first if needed. Continue?',parent=self):return
            self.show('Home')
            if kind=='career':
                self.doc_type.set(data['category']);self.template.set(data['template']);self.doc_text=data['draft'];self.cv_photo=data['photo']
                for key,var in self.values.items():var.set(data['fields'].get(key,''))
                self.show('Career documents')
            else:self.sheet=data;self.show('A4 picture sheets')
            self.status.set('Project opened • included photos are temporary until saved again')
        session=self.session_temp.name
        self.job('Opening project…',lambda:projects.load(path,session,allow_internal=allow_recovery),loaded)
    def recovery_dir(self):return settings_store.settings_path().parent/'recovery'
    def recovery_exists(self):return any((self.recovery_dir()/(name+ext)).exists() for name in ('career','sheet') for ext in ('.cafeproject','.prev.cafeproject'))
    def delete_recovery_copies(self,notify=True):
        failed=[]
        for name in ('career','sheet'):
            for ext in ('.cafeproject','.prev.cafeproject'):
                try:(self.recovery_dir()/(name+ext)).unlink(missing_ok=True)
                except OSError:failed.append(name+ext)
        if failed:messagebox.showwarning('Recovery files remain','Some recovery files could not be removed. Check the recovery folder manually.',parent=self)
        elif notify:messagebox.showinfo('Recovery cleared','Recovery copies were deleted. Saved projects and original files were not deleted.',parent=self)
    def recover_project(self,kind):
        if not self.require_staff():return
        primary=self.recovery_dir()/(kind+'.cafeproject')
        prev=self.recovery_dir()/(kind+'.prev.cafeproject')
        candidates=[p for p in (primary,prev) if p.exists()]
        if not candidates:return messagebox.showinfo('No recovery copy','No recovery copy exists for this project type.',parent=self)
        session=self.session_temp.name
        def load_with_fallback():
            last_err=None
            for idx,cand in enumerate(candidates):
                try:
                    return projects.load(str(cand),session,allow_internal=True), idx>0
                except Exception as exc:
                    last_err=exc
            raise last_err
        def loaded(res):
            (proj_kind,data),used_fallback=res
            what='career document' if proj_kind=='career' else 'picture sheet'
            note='\n\nNote: The latest recovery file was unreadable, so the previous verified checkpoint (.prev.cafeproject) was loaded.' if used_fallback else ''
            if not messagebox.askyesno('Open recovery copy',f'Opening this recovery copy replaces the current {what} in the workspace. Save your current work first if needed.{note}\n\nContinue?',parent=self):return
            self.show('Home')
            if proj_kind=='career':
                self.doc_type.set(data['category']);self.template.set(data['template']);self.doc_text=data['draft'];self.cv_photo=data['photo']
                for key,var in self.values.items():var.set(data['fields'].get(key,''))
                self.show('Career documents')
            else:self.sheet=data;self.show('A4 picture sheets')
            self.status.set('Recovery project restored' + (' from previous checkpoint' if used_fallback else '') + ' • temporary until saved')
        self.job('Opening recovery copy…',load_with_fallback,loaded)
    def perform_recovery(self):
        if not self.recovery_enabled or self.busy:return
        career=self._career_snapshot();sheet=self._sheet_snapshot();jobs=[]
        if career['draft'].strip() or any(v.strip() for v in career['fields'].values()) or career['photo']:jobs.append(('career',career))
        if sheet['items']:jobs.append(('sheet',sheet))
        if not jobs:return
        directory=self.recovery_dir()
        def work():
            import shutil
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            for kind,data in jobs:
                cur_file=directory/(kind+'.cafeproject')
                prev_file=directory/(kind+'.prev.cafeproject')
                if cur_file.exists():
                    try: shutil.copy2(cur_file,prev_file)
                    except OSError: pass
                projects.save(kind,data,str(cur_file),allow_internal=True)
        def on_fail(err_msg):
            self.status.set(f'⚠ Auto-recovery failed ({err_msg}) • save your work manually!')
        self.job('Saving recovery copy…',work,lambda _:self.status.set('Recovery copy saved locally • previous checkpoint retained • not encrypted'),error_cb=on_fail)
    def recovery_tick(self):
        self.perform_recovery();self.after(60000,self.recovery_tick)
    def remote_entitlement_tick(self):
        # Remote pause/resume and new approvals are applied on launch and every 10 minutes.
        self.after(600000,self.remote_entitlement_tick)
        cfg=settings_store.load_settings()
        if self._entitlement_sync_running or not cloud_sync.is_supabase_configured(cfg): return
        self._entitlement_sync_running=True
        def check_online():
            try: result=cloud_sync.sync_machine_entitlements_from_cloud(cfg)
            except Exception as exc: result={'ok':False,'error':str(exc)}
            self.events.put(('ok',self._finish_remote_entitlement_sync,result))
        threading.Thread(target=check_online,daemon=True).start()
    def _finish_remote_entitlement_sync(self,result):
        self._entitlement_sync_running=False
        if not isinstance(result,dict) or not result.get('ok'): return
        revoked=result.get('revoked') or []
        activated=result.get('activated') or []
        if revoked:
            self.status.set('License access paused by the owner • contact support if unexpected')
            messagebox.showwarning('Café Desk access paused', 'The owner remotely paused this computer’s license. Contact the Café Desk owner if you believe this is a mistake.',parent=self)
            if license_guard.is_license_required() and not license_guard.current_license_status().get('valid'):
                self.close_app()
        elif activated:
            self.status.set('Cloud license approval applied successfully')
    def projects_screen(self):
        self.heading(self.body,'Projects & recovery','Save your work to reopen and keep working later.')
        box=card(self.body);box.pack(fill='x',pady=(0,12));label(box,'Save or open a project',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        label(box,'A project includes the document or picture-sheet layout and copies of its photos. It is not encrypted.',size=12,color=MUTED,wraplength=720,justify='left').pack(anchor='w',padx=20,pady=(0,12))
        action_row(box,
                   primary=(('Save career project',lambda:self.save_project('career')),),
                   secondary=(('Save picture sheet',lambda:self.save_project('sheet')),
                              ('Open a project',self.open_project)),
                   pady=(0,18))
        box=card(self.body);box.pack(fill='x',pady=(0,12));label(box,'Optional recovery copies',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        label(box,'When enabled, a local copy is saved about every 60 seconds into:\n'+str(self.recovery_dir())+'\nIt includes private text and photos and is not encrypted. Keep the computer and backups secure.',size=12,color=MUTED,wraplength=720,justify='left').pack(anchor='w',padx=20,pady=(0,12))
        toggle=ctk.BooleanVar(value=self.recovery_enabled)
        def change():
            if toggle.get():
                if not messagebox.askyesno('Enable recovery?', 'Recovery saves customer text and photos to this computer about every 60 seconds, including after a crash or normal exit. The copies are not encrypted. Get customer permission and use Next customer when finished. Enable?',parent=self):toggle.set(False);return
                self.recovery_enabled=True;self.after(1200,self.perform_recovery)
            else:self.recovery_enabled=False
        ctk.CTkSwitch(box,text='Save recovery copies during this session',variable=toggle,command=change).pack(anchor='w',padx=20,pady=(0,14))
        recovery_row=action_row(box,
                   secondary=(('Recover career',lambda:self.recover_project('career')),
                              ('Recover picture sheet',lambda:self.recover_project('sheet'))),
                   pady=(0,14))
        def delete():
            if self.busy:return messagebox.showinfo('Please wait','Wait until the current task finishes.',parent=self)
            if not self.require_staff():return
            if messagebox.askyesno('Delete recovery copies?','Delete the local career and picture-sheet recovery copies? Saved project files are not deleted.',parent=self):self.recovery_enabled=False;toggle.set(False);self.delete_recovery_copies()
        destroy_row=button_row(box,pady=(0,18));button(destroy_row,'Delete recovery copies',delete,ghost=True).pack(side='left')
    def quick_help(self):
        messages={
            'Home':'Use the menu to choose a tool. Open Help & privacy for a step-by-step guide.',
            'Projects & recovery':'Save a career or picture-sheet project with its photos, or open a saved project. Recovery is optional and stores unencrypted copies locally.',
            'Help & privacy':'Read the quick steps below, use the direct link to download Tesseract OCR, or delete a selected customer file (staff).',
            'About & contact':'View Café Desk version details, developer contact email, and GitHub links — click to open or copy.',
            'Price list':'Search the read-only price list. Staff add prices and units in PIN-protected Price settings.',
            'PDF tools':'Add PDFs, arrange them, then merge and save. Extract or split pages, or unlock password-protected PDFs (paid Machine-ID add-on with owner approval).',
            'Bluetooth files':'On Windows, pair the phone in Bluetooth settings, open the Windows Bluetooth transfer window, and choose Receive files. After saving, select the file to use it in Café Desk.' ,
            'Scan & images':'On Windows, find and select a WIA scanner, then scan each page in the app and save a PDF. Preview selected images and recognise English text with OCR (direct Tesseract download link is in Help & privacy).',
            'A4 picture sheets':'Add images, choose a layout, move and resize in Free layout, then save a PDF or project. Print PDFs at actual size.',
            'Career documents':'Add your details, create and edit a draft, then choose the Document type and Template right beside the live A4 preview (click the enlarge icon to inspect full size).',
            'Cards & invitations':'Design Business cards or Invitation cards across all 13 templates, preview a single card or A4 sheet live, and save printable A4 PDFs with optional trim marks.',
            'Forms creator':'Build printable or digitally fillable AcroForm A4 PDFs — general applications, affidavits, consent forms, customer intake sheets, or custom fields.',
            'Receipts':'Choose services and quantity. Save a receipt PDF; staff must separately confirm Record sale to add it to history.',
            'Sales reports':'Choose a date range to view locally recorded totals, export a CSV or save a selected sale record. Simple internal service log — payment status, cash/card drawer reconciliation and tax/VAT are NOT calculated.',
            'Price settings':'Enter each business service and price on its own line as service | price. Include units in the service name.',
            'Business settings':'Change the business name, icon, logo or staff PIN. Settings are local to this computer user.',
            'AI settings':'Enter your session API key — Google Gemini (AIza... from aistudio.google.com/api-keys), OpenRouter, OpenAI, Groq, DeepSeek or custom URL. Confirm before sending any draft.\n\nShortcuts: F1 = Quick help  •  Ctrl+L = Lock staff  •  Ctrl+Shift+N = Next customer'}
        messagebox.showinfo('Quick help • '+self.current,messages.get(self.current,'Open Help & privacy for the full guide.'),parent=self)
    def open_user_manual(self):
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent)); path=root/'cafe-desk-manual.html'
        try:
            if not path.exists(): raise FileNotFoundError('The full HTML manual is missing from this installation.')
            if not webbrowser.open(path.as_uri()): raise RuntimeError('Could not open your browser.')
            self.status.set('Opened full Café Desk Manual & Sales Guide in your browser')
        except Exception as exc: messagebox.showerror('Could not open manual',str(exc),parent=self)
    def help_screen(self):
        self.heading(self.body,'Help & privacy','A quick guide for staff and customers.')
        manual_banner=card(self.body);manual_banner.pack(fill='x',pady=(0,10))
        label(manual_banner,'Full Printable User Manual, Menu Guide & Sales Handbook',size=15,bold=True,color=LIME).pack(anchor='w',padx=18,pady=(15,4))
        label(manual_banner,'Open the complete formatted HTML handbook (all 17 sidebar tools, 5-minute shop setup, 30-second pitch & printable A4 handout) locally in your browser.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,10))
        button(manual_banner,'📖 Open Full Manual & Sales Guide (HTML)  ↗',self.open_user_manual,fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(anchor='w',padx=18,pady=(0,15))
        sections=[('1  Set prices','A staff member opens Price settings, enters each service and price (include units such as per page), and saves. Everyone can view the read-only Price list.'),('2  Create a CV','Open Career documents, choose a template, add details and optionally a photo. Create the draft, edit it, then save a PDF or a project to continue later.'),('3  Merge PDFs','In the CV screen, add the draft to PDF merge, or open PDF tools and add existing PDFs. Arrange them and save the merged file.'),('4  Scan and extract text','Open Scan & images. On Windows, find and select a WIA scanner, scan each page and save a PDF. Alternatively use your scanner app. Preview selected images and extract English text with Tesseract OCR (direct download link below).'),('5  Print photos','Open A4 picture sheets, add pictures and choose a layout or custom dimensions. Save the PDF, then print from your PDF viewer at actual size.'),('6  Receipts and reports','Add services in Receipts. Saving a receipt PDF does not record a sale. Staff choose Record sale; staff-only Sales reports show recorded totals. Payment and tax are not tracked.'),('7  Finish with a customer','Use Next customer to clear the current workspace, temporary merge files and recovery copies and lock staff. Saved files and originals stay on the computer.')]
        for title,description in sections:
            box=card(self.body);box.pack(fill='x',pady=(0,8));label(box,title,size=14,bold=True).pack(anchor='w',padx=18,pady=(14,4));label(box,description,size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,14))
        ocr_box=card(self.body);ocr_box.pack(fill='x',pady=(0,10))
        label(ocr_box,'Direct download: Tesseract OCR (for English text recognition)',size=15,bold=True,color='#B9FF70').pack(anchor='w',padx=18,pady=(16,4))
        label(ocr_box,'If you share Café Desk with another computer, install Tesseract OCR (keep English language data ticked during setup), then restart Café Desk. Click a direct link below or copy the URL.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,10))
        def open_ocr_url(direct_exe=False):
            try:
                url=services.open_tesseract_link(direct_installer=direct_exe)
                self.status.set('Opened Tesseract OCR download link in your browser  •  '+url)
            except ValueError as exc:
                messagebox.showerror('Could not open link',str(exc),parent=self)
        def copy_ocr_url():
            self.clipboard_clear();self.clipboard_append(services.TESSERACT_DOWNLOAD_URL);self.update_idletasks()
            self.status.set('Copied Tesseract OCR link to clipboard')
            messagebox.showinfo('Link copied',f'Copied to clipboard:\n{services.TESSERACT_DOWNLOAD_URL}',parent=self)
        ocr_btns=ctk.CTkFrame(ocr_box,fg_color='transparent');ocr_btns.pack(fill='x',padx=18,pady=(0,10))
        button(ocr_btns,'Download Tesseract OCR (Windows .exe)  ↗',lambda:open_ocr_url(True),fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(side='left',padx=(0,BTN_PADX))
        button(ocr_btns,'Open downloads page  ↗',lambda:open_ocr_url(False),secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(ocr_btns,'Copy link',copy_ocr_url,ghost=True).pack(side='left')
        url_entry=entry(ocr_box);url_entry.insert(0,services.TESSERACT_DOWNLOAD_URL);url_entry.configure(state='readonly');url_entry.pack(fill='x',padx=18,pady=(0,16))
        usb_box=card(self.body);usb_box.pack(fill='x',pady=(0,10))
        label(usb_box,'Share Café Desk to other computers via USB',size=15,bold=True,color='#B9FF70').pack(anchor='w',padx=18,pady=(16,4))
        mode_note='ACTIVE (saving inside CafeDeskData beside the app)' if settings_store.is_portable_data_mode() else 'Per-PC mode (create a "CafeDeskData" folder beside CafeDesk.exe to store prices/settings on USB)'
        label(usb_box,'1. Build once on Windows by double-clicking packaging\\build-windows.bat (or download CafeDesk-Windows-USB-Portable.zip from GitHub Actions on the repository).\n2. Copy the ENTIRE CafeDesk folder (including _internal) onto a USB flash drive — target Windows 10/11 PCs run CafeDesk.exe directly with NO Python installation needed.\n3. Optional USB Portable Data Mode: '+mode_note+'.\n4. Optional USB OCR: install Tesseract OCR on the target PC or place a Tesseract-OCR folder right inside the USB CafeDesk folder.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,16))
        box=card(self.body);box.pack(fill='x',pady=(0,12));label(box,'Privacy, local data folders & file removal',size=15,bold=True).pack(anchor='w',padx=18,pady=(16,4));label(box,'Staff access locks after five minutes without activity. The PIN is basic app-level protection — run Café Desk inside a standard (non-Administrator) Windows/OS kiosk user account on shared PCs.\n\n• Session temp folder (wiped on Next customer & app close): '+str(self.session_temp.name)+'\n• App configuration, sales & recovery folder (protected from public exports): '+str(settings_store.settings_path().parent)+'\n\nNext customer clears in-app workspace data, temporary merge copies and recovery files, and locks staff, without deleting customer-saved PDFs or Downloads/Bluetooth files. A staff member can deliberately remove a selected customer file below.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,12))
        hbtns=ctk.CTkFrame(box,fg_color='transparent');hbtns.pack(fill='x',padx=18,pady=(0,18))
        button(hbtns,'Delete a selected customer file',self.delete_selected_file,secondary=True).pack(side='left',padx=(0,BTN_PADX))
        def open_data_folder():
            if not self.require_staff():return
            folder=str(settings_store.settings_path().parent)
            Path(folder).mkdir(parents=True,exist_ok=True,mode=0o700)
            try:
                if sys.platform.startswith('win'):os.startfile(folder)
                elif sys.platform=='darwin':subprocess.Popen(['open',folder])
                else:subprocess.Popen(['xdg-open',folder])
            except Exception:messagebox.showinfo('Local data folder',f'App configuration & recovery folder:\n{folder}',parent=self)
        button(hbtns,'📂 Staff: open local data folder',open_data_folder,ghost=True).pack(side='left')
    def about_screen(self):
        self.heading(self.body,'About & contact','Information about Café Desk, developer contacts, and GitHub repository.')
        hero=card(self.body);hero.pack(fill='x',pady=(0,12))
        top=ctk.CTkFrame(hero,fg_color='transparent');top.pack(fill='x',padx=20,pady=(18,10))
        ctk.CTkLabel(top,text='',image=self.icon_images[self.business_icon]).pack(side='left',padx=(0,14))
        info_col=ctk.CTkFrame(top,fg_color='transparent');info_col.pack(side='left')
        label(info_col,f'{self.business_name.get()}  •  Café Desk Document Studio',size=18,bold=True).pack(anchor='w')
        label(info_col,f'Version {APP_BUILD}  •  Local-first desktop workspace',size=12,bold=True,color=LIME).pack(anchor='w',pady=(2,0))
        label(hero,'Café Desk helps internet cafés, print shops and walk-in customers handle everyday paperwork locally on one workspace: PDF merge/split/extract, WIA scanning, English OCR, A4 picture sheets, 13-template CVs & cover letters, 13-template business & invitation cards, printable & AcroForm-fillable forms, Bluetooth file shortcuts, price lists, and simple receipts.',size=12,color=MUTED,wraplength=760,justify='left').pack(anchor='w',padx=20,pady=(0,18))

        def open_link(url,desc):
            try:
                services.open_external_url(url)
                self.status.set(f'Opened {desc}')
            except ValueError as exc:
                messagebox.showerror('Could not open link',str(exc),parent=self)
        def copy_val(val,desc):
            self.clipboard_clear();self.clipboard_append(val);self.update_idletasks()
            self.status.set(f'Copied {desc} to clipboard')
            messagebox.showinfo('Copied',f'{desc} copied to clipboard:\n{val}',parent=self)

        contact_box=card(self.body);contact_box.pack(fill='x',pady=(0,12))

        # --- Remote Over-the-Internet App Updater Card ---
        upd_box=card(self.body); upd_box.pack(fill='x',pady=(0,12),before=contact_box)
        label(upd_box,'Remote App Updates & Online Key Sync (Over the Internet)',size=16,bold=True,color=LIME).pack(anchor='w',padx=20,pady=(18,4))
        label(
            upd_box,
            f'Current installed version: v{APP_BUILD}. Check for newer Café Desk releases over the internet (SHA-256 verified installer; your license, PIN, price list, and sales history stay untouched).',
            size=12,color=MUTED,wraplength=740,justify='left',
        ).pack(anchor='w',padx=20,pady=(0,12))


        def update_dot_state(available):
            if not update_dot.winfo_exists(): return
            if available:
                update_dot.pack(side='left',before=check_updates_button,padx=(0,8))
            else:
                update_dot.pack_forget()

        def check_updates_click():
            cfg=settings_store.load_settings()
            if not cloud_sync.is_supabase_configured(cfg):
                return messagebox.showinfo(
                    'Configure Cloud Updates',
                    'Add your Supabase Project URL and Public Anon Key in Business Settings → Multi-PC Shop & Supabase Cloud Sync to enable 1-click over-the-internet updates.',
                    parent=self,
                )
            def _run_chk():
                return {
                    'update': cloud_sync.check_for_app_update(cfg,current_version=APP_BUILD),
                    'announcements': cloud_sync.fetch_app_announcements(cfg),
                }
            def _done_chk(result):
                res=result['update']
                announcements=result.get('announcements') or []
                if announcements:
                    notice_text='\n\n'.join(f"{str(a.get('title') or 'Café Desk notice')}\n{str(a.get('message') or '')}" for a in announcements[:5])
                    messagebox.showinfo('Café Desk news & offers',notice_text,parent=self)
                if not res.get('ok'):
                    return messagebox.showerror('Update Check Failed',str(res.get('error') or 'Could not check updates.'),parent=self)
                update_dot_state(bool(res.get('update_available')))
                if not res.get('update_available'):
                    self.status.set(f'Up to date • Café Desk v{APP_BUILD}')
                    return messagebox.showinfo('Up to Date',f"You already have the latest Café Desk version (v{APP_BUILD}).",parent=self)
                notes=res.get('release_notes') or 'Bug fixes and improvements.'
                ask_msg=(
                    f"New version available: Café Desk v{res['latest_version']} (you have v{APP_BUILD})\n\n"
                    f"What's new:\n{notes}\n\n"
                    f"Download & verify the official SHA-256 signed update now?\n"
                    f"(Your computer license, PIN, prices, and sales history are preserved.)"
                )
                if not messagebox.askyesno('Update Available!',ask_msg,parent=self):
                    return
                dl_dir=Path.home()/'Downloads'
                if not dl_dir.is_dir(): dl_dir=Path(self.session_temp.name)
                def _run_dl():
                    return cloud_sync.download_and_verify_update(
                        res['download_url'],
                        res['sha256'],
                        dl_dir,
                        version_label=res['latest_version'],
                        expected_signature=res.get('signature',''),
                    )
                def _done_dl(saved_installer):
                    self.status.set(f'Verified update downloaded: {saved_installer.name}')
                    if sys.platform.startswith('win') and str(saved_installer).lower().endswith('.exe'):
                        if messagebox.askyesno(
                            'Update Verified — Launch Installer?',
                            f'SHA-256 security verification passed!\nSaved to:\n{saved_installer}\n\nClose Café Desk and launch the v{res["latest_version"]} installer now?',
                            parent=self,
                        ):
                            try:
                                os.startfile(str(saved_installer))
                                self.close_app()
                            except Exception as exc:
                                messagebox.showerror('Could not start installer',str(exc),parent=self)
                    else:
                        messagebox.showinfo(
                            'Update Downloaded & Verified',
                            f'SHA-256 checksum verified!\nSaved update package to:\n{saved_installer}',
                            parent=self,
                        )
                self.job(f"Downloading & verifying Café Desk v{res['latest_version']}…",_run_dl,_done_dl)
            self.job('Checking for remote Café Desk updates…',_run_chk,_done_chk)

        def sync_keys_online_click():
            cfg=settings_store.load_settings()
            if not cloud_sync.is_supabase_configured(cfg):
                return messagebox.showinfo(
                    'Cloud Not Configured',
                    'Add your Supabase Project URL and Public Anon Key in Business Settings first, or enter keys offline below.',
                    parent=self,
                )
            def _do_k():
                return cloud_sync.sync_machine_entitlements_from_cloud(cfg)
            def _done_k(res):
                act=res.get('activated') or []
                rev=res.get('revoked') or []
                msg_lines=[]
                if act: msg_lines.append('Activated / refreshed:\n• ' + '\n• '.join(act))
                if rev: msg_lines.append('Remotely revoked:\n• ' + '\n• '.join(rev))
                if not msg_lines: msg_lines.append(f"Checked Machine ID {res.get('machine_id')}: no new cloud keys waiting.")
                messagebox.showinfo('Online Key Sync',('\n\n'.join(msg_lines)),parent=self)
                self.show('About & contact')
            self.job('Syncing Machine ID approvals from cloud…',_do_k,_done_k)

        urow=ctk.CTkFrame(upd_box,fg_color='transparent'); urow.pack(fill='x',padx=20,pady=(0,18))
        update_dot=ctk.CTkLabel(urow,text='● Update available',text_color='#FFCC66',font=('Segoe UI',11,'bold'))
        check_updates_button=button(urow,'⬇ Check for App Updates',check_updates_click,fg_color=LIME,hover_color='#A9EB65',text_color='#142018')
        check_updates_button.pack(side='left',padx=(0,BTN_PADX))
        button(urow,'☁ Sync Approved Keys Online',sync_keys_online_click,secondary=True).pack(side='left')
        if cloud_sync.is_supabase_configured(settings_store.load_settings()):
            def _check_badge_in_background():
                try:
                    res=cloud_sync.check_for_app_update(settings_store.load_settings(),current_version=APP_BUILD)
                    if res.get('ok'):
                        self.after(0,lambda:update_dot_state(bool(res.get('update_available'))))
                except Exception:
                    pass  # Offline cafés stay quiet; the manual check explains any connection problem.
            threading.Thread(target=_check_badge_in_background,daemon=True).start()
        lic_status=license_guard.current_license_status()
        lic_box=card(self.body);lic_box.pack(fill='x',pady=(0,12))
        lic_box.pack_forget()
        lic_box.pack(fill='x',pady=(0,12),before=contact_box)
        label(lic_box,'1-Year Computer License & Machine ID',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        _demo_warn=license_guard.demo_key_warning()
        if _demo_warn:
            label(lic_box,_demo_warn,size=12,bold=True,color='#FFB8B0',wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,10))
        mid_val=lic_status.get('machine_id') or license_guard.get_machine_id()
        if lic_status.get('valid'):
            days_left=lic_status.get('days_remaining',0)
            badge_color='#FFB8B0' if days_left<=30 else LIME
            seat_badge=''
            if lic_status.get('seat_no') and lic_status.get('seat_max'):
                seat_badge=f"  •  Seat {lic_status['seat_no']} of {lic_status['seat_max']}"
                if lic_status.get('seat_label'):
                    seat_badge+=f" ({lic_status['seat_label']})"
            elif lic_status.get('shop_id'):
                seat_badge=f"  •  Shop {lic_status['shop_id']}"
            summary=f'ACTIVE  •  Licensed to: {lic_status["customer"]}{seat_badge}  •  Expires: {lic_status["expires_at"]} ({days_left} days remaining)'
        else:
            badge_color='#FFDBA2'
            summary=f'Developer / Unactivated mode  •  {lic_status.get("reason","No per-PC license installed yet.")}'
        label(lic_box,summary,size=12,bold=True,color=badge_color,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,10))
        mrow=ctk.CTkFrame(lic_box,fg_color='transparent');mrow.pack(fill='x',padx=20,pady=(0,10))
        m_entry=entry(mrow);m_entry.insert(0,f'Machine ID: {mid_val}');m_entry.configure(state='readonly');m_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        button(mrow,'⧉ Copy',lambda:copy_val(mid_val,'Machine ID'),ghost=True).pack(side='left',padx=(0,BTN_PADX))
        button(mrow,'🔑 Enter / Renew 1-Year Key',lambda:license_ui.open_renewal_modal(self,on_updated=lambda _info:self.show('About & contact')),fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(side='left')

        addon_info=license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK)
        def view_saved_keys():
            if not self.require_staff(): return
            lic_path=license_guard.license_file_path()
            add_path=license_guard.addon_license_file_path(license_guard.FEATURE_PDF_UNLOCK)
            dlg=ctk.CTkToplevel(self); dlg.title('Saved Machine ID & Activated Keys on This PC')
            dlg.geometry('660x430'); dlg.configure(fg_color=BG); dlg.transient(self); dlg.grab_set()
            wrap=card(dlg); wrap.pack(fill='both',expand=True,padx=20,pady=18)
            label(wrap,f'This Computer\'s Machine ID:  {mid_val}',size=15,bold=True,color=LIME).pack(anchor='w',padx=18,pady=(16,4))
            label(wrap,f'Saved locally per-PC inside: {lic_path.parent}\n(Never stored inside the GitHub repo — kept private on this computer)',size=11,color=MUTED,justify='left').pack(anchor='w',padx=18,pady=(0,10))

            label(wrap,'1-Year Computer Key (CDLIC1 • license.json)',size=11,bold=True,color=INK).pack(anchor='w',padx=18,pady=(2,3))
            b_tok=lic_status.get('token') or 'No active CDLIC1 key saved on this PC.'
            t1=ctk.CTkTextbox(wrap,height=68,corner_radius=8,border_width=1,border_color=BORDER,fg_color='#0D1320',text_color=INK,font=ctk.CTkFont(family='Consolas',size=11))
            t1.pack(fill='x',padx=18,pady=(0,10)); t1.insert('1.0',b_tok); t1.configure(state='disabled')

            label(wrap,'Paid PDF Password Remover Key (CDADD1 • addon-pdf_unlock.json)',size=11,bold=True,color=INK).pack(anchor='w',padx=18,pady=(2,3))
            a_tok=addon_info.get('token') or 'Not activated on this PC yet.'
            t2=ctk.CTkTextbox(wrap,height=68,corner_radius=8,border_width=1,border_color=BORDER,fg_color='#0D1320',text_color=INK,font=ctk.CTkFont(family='Consolas',size=11))
            t2.pack(fill='x',padx=18,pady=(0,12)); t2.insert('1.0',a_tok); t2.configure(state='disabled')

            brow=ctk.CTkFrame(wrap,fg_color='transparent'); brow.pack(fill='x',padx=18,pady=(0,14))
            if lic_status.get('token'):
                button(brow,'⧉ Copy CDLIC1 Key',lambda:copy_val(lic_status['token'],'CDLIC1 License Key'),secondary=True).pack(side='left',padx=(0,BTN_PADX))
            if addon_info.get('token'):
                button(brow,'⧉ Copy CDADD1 Key',lambda:copy_val(addon_info['token'],'CDADD1 Add-on Key'),secondary=True).pack(side='left',padx=(0,BTN_PADX))
            def open_lic_dir():
                lic_path.parent.mkdir(parents=True,exist_ok=True)
                try:
                    if sys.platform.startswith('win'): os.startfile(str(lic_path.parent))
                    elif sys.platform=='darwin': subprocess.Popen(['open',str(lic_path.parent)])
                    else: subprocess.Popen(['xdg-open',str(lic_path.parent)])
                except Exception:
                    messagebox.showinfo('License folder',f'Saved at:\n{lic_path.parent}',parent=dlg)
            button(brow,'📂 Open License Folder',open_lic_dir,ghost=True).pack(side='left')
            button(brow,'Close',dlg.destroy,secondary=True,width=90).pack(side='right')

        krow=ctk.CTkFrame(lic_box,fg_color='transparent'); krow.pack(fill='x',padx=20,pady=(0,14))
        label(krow,f'Local license folder (private per-PC, never uploaded): {license_guard.host_license_dir()}',size=11,color=MUTED).pack(side='left',fill='x',expand=True,anchor='w')
        button(krow,'👁 View saved keys',view_saved_keys,ghost=True).pack(side='right')
        addon_bar=ctk.CTkFrame(lic_box,fg_color='#141C2B',corner_radius=12,border_width=1,border_color=BORDER)
        addon_bar.pack(fill='x',padx=20,pady=(0,18))
        label(addon_bar,'PAID ADD-ON  •  PDF PASSWORD REMOVER (LOCKED TO THIS MACHINE ID)',size=10,bold=True,color='#FFDBA2').pack(anchor='w',padx=16,pady=(12,3))
        arow=ctk.CTkFrame(addon_bar,fg_color='transparent'); arow.pack(fill='x',padx=16,pady=(0,12))
        if addon_info.get('valid'):
            a_txt=f"ACTIVE  •  Approved until {addon_info['expires_at']} ({addon_info['days_remaining']} days remaining)"
            a_col=LIME
        else:
            a_txt='LOCKED  •  Requires owner approval & Paid Add-on Key (CDADD1...) for this Machine ID'
            a_col=MUTED
        label(arow,a_txt,size=12,bold=True,color=a_col).pack(side='left',fill='x',expand=True,anchor='w')
        button(arow,'🔑 Enter / Renew PDF Unlock Key',lambda:license_ui.open_addon_modal(self,on_updated=lambda _info:self.show('About & contact')),secondary=bool(addon_info.get('valid')),fg_color=SOFT if addon_info.get('valid') else LIME,hover_color='#40506D' if addon_info.get('valid') else '#A9EB65',text_color='#E1E8F6' if addon_info.get('valid') else '#142018').pack(side='right')
        label(contact_box,'Developer & support contacts',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        label(contact_box,'Have feedback, feature ideas, or need help setting up Café Desk? Reach out directly or visit GitHub.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,14))

        # WhatsApp row
        wa_card=ctk.CTkFrame(contact_box,fg_color='#141C2B',corner_radius=12,border_width=1,border_color=BORDER);wa_card.pack(fill='x',padx=20,pady=(0,10))
        label(wa_card,'WHATSAPP SUPPORT & LICENSING',size=10,bold=True,color=LIME).pack(anchor='w',padx=16,pady=(12,2))
        wrow=ctk.CTkFrame(wa_card,fg_color='transparent');wrow.pack(fill='x',padx=16,pady=(0,12))
        w_entry=entry(wrow);w_entry.insert(0,f'{services.DEVELOPER_WHATSAPP_LOCAL}  (+27 72 227 9505)');w_entry.configure(state='readonly');w_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        button(wrow,'💬 WhatsApp chat',lambda:open_link(services.whatsapp_chat_url(f'Hi, I am using Cafe Desk (Machine ID: {mid_val}) and need assistance.'),'WhatsApp chat'),fg_color='#25D366',hover_color='#1EBE5D',text_color='#0B1F13').pack(side='left',padx=(0,BTN_PADX))
        button(wrow,'⧉ Copy number',lambda:copy_val('0722279505','WhatsApp number'),ghost=True).pack(side='left')

        # Email row
        email_card=ctk.CTkFrame(contact_box,fg_color='#141C2B',corner_radius=12,border_width=1,border_color=BORDER);email_card.pack(fill='x',padx=20,pady=(0,10))
        label(email_card,'EMAIL',size=10,bold=True,color=LIME).pack(anchor='w',padx=16,pady=(12,2))
        erow=ctk.CTkFrame(email_card,fg_color='transparent');erow.pack(fill='x',padx=16,pady=(0,12))
        e_entry=entry(erow);e_entry.insert(0,services.DEVELOPER_EMAIL);e_entry.configure(state='readonly');e_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        button(erow,'✉ Send email',lambda:open_link('mailto:'+services.DEVELOPER_EMAIL,'email app'),fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(side='left',padx=(0,BTN_PADX))
        button(erow,'⧉ Copy email',lambda:copy_val(services.DEVELOPER_EMAIL,'Email address'),ghost=True).pack(side='left')

        # GitHub profile row
        gh_card=ctk.CTkFrame(contact_box,fg_color='#141C2B',corner_radius=12,border_width=1,border_color=BORDER);gh_card.pack(fill='x',padx=20,pady=(0,10))
        label(gh_card,'GITHUB PROFILE (@Njwacky)',size=10,bold=True,color=LIME).pack(anchor='w',padx=16,pady=(12,2))
        grow=ctk.CTkFrame(gh_card,fg_color='transparent');grow.pack(fill='x',padx=16,pady=(0,12))
        g_entry=entry(grow);g_entry.insert(0,services.DEVELOPER_GITHUB_URL);g_entry.configure(state='readonly');g_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        button(grow,'↗ Open GitHub',lambda:open_link(services.DEVELOPER_GITHUB_URL,'GitHub profile')).pack(side='left',padx=(0,BTN_PADX))
        button(grow,'⧉ Copy link',lambda:copy_val(services.DEVELOPER_GITHUB_URL,'GitHub profile URL'),ghost=True).pack(side='left')

        # Project repository row
        repo_card=ctk.CTkFrame(contact_box,fg_color='#141C2B',corner_radius=12,border_width=1,border_color=BORDER);repo_card.pack(fill='x',padx=20,pady=(0,18))
        label(repo_card,'PROJECT REPOSITORY & UPDATES',size=10,bold=True,color=LIME).pack(anchor='w',padx=16,pady=(12,2))
        rrow=ctk.CTkFrame(repo_card,fg_color='transparent');rrow.pack(fill='x',padx=16,pady=(0,12))
        r_entry=entry(rrow);r_entry.insert(0,services.DEVELOPER_REPO_URL);r_entry.configure(state='readonly');r_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        button(rrow,'↗ Open repo',lambda:open_link(services.DEVELOPER_REPO_URL,'GitHub repository')).pack(side='left',padx=(0,BTN_PADX))
        button(rrow,'⧉ Copy link',lambda:copy_val(services.DEVELOPER_REPO_URL,'Repository URL'),ghost=True).pack(side='left')

        priv_box=card(self.body);priv_box.pack(fill='x',pady=(0,12))
        label(priv_box,'Local-first & privacy summary',size=15,bold=True).pack(anchor='w',padx=20,pady=(16,4))
        label(priv_box,'All PDFs, scans, OCR text, picture sheets, CVs, forms and cards are processed locally on this computer. Nothing is uploaded unless a staff member unlocks AI settings and confirms sending a draft to your chosen AI provider.',size=12,color=MUTED,wraplength=760,justify='left').pack(anchor='w',padx=20,pady=(0,16))
    def delete_selected_file(self):
        if self.busy:return messagebox.showinfo('Please wait','Wait until the current task finishes.',parent=self)
        if not self.require_staff():return
        path=filedialog.askopenfilename(parent=self,title='Choose ONE customer file to permanently delete',filetypes=[('Customer files','*.cafeproject *.pdf *.txt *.png *.jpg *.jpeg *.tif *.tiff')])
        if not path:return
        p=Path(path)
        if p.is_symlink() or not p.is_file():return messagebox.showerror('Cannot delete','Choose a regular file, not a link or folder.',parent=self)
        if p.suffix.lower() not in ('.cafeproject','.pdf','.txt','.png','.jpg','.jpeg','.tif','.tiff'):return messagebox.showerror('Cannot delete','Choose a supported customer document or image file.',parent=self)
        if p.resolve().is_relative_to(settings_store.settings_path().parent.resolve()):return messagebox.showerror('Cannot delete','App settings, sales records and recovery files are not removed here. Use the recovery controls instead.',parent=self)
        if not messagebox.askyesno('Permanent deletion',f'Permanently delete this file? It will not go to the Recycle Bin and cannot be undone:\n\n{path}\n\nDeleting an original photo can break a project. This does not securely erase other copies or backups.',parent=self):return
        confirm=simpledialog.askstring('Confirm deletion','Type DELETE to permanently remove the selected file:',parent=self)
        if confirm!='DELETE':return
        try:services.secure_delete(p);messagebox.showinfo('File deleted','The selected file was securely overwritten and deleted. Other copies and backups were not removed.',parent=self)
        except OSError as exc:messagebox.showerror('Could not delete file',str(exc),parent=self)
    def price_list_screen(self):
        self.heading(self.body,'Price list','A clear, read-only guide to this business’s services and prices.')
        box=card(self.body);box.pack(fill='x',pady=(0,14))
        label(box,self.business_name.get(),size=17,bold=True).pack(anchor='w',padx=20,pady=(18,3))
        label(box,'Look up a service below. Rates can include units such as per page. Staff update them in Price settings.',size=12,color=MUTED,justify='left').pack(anchor='w',padx=20,pady=(0,10))
        search=ctk.StringVar();ctk.CTkEntry(box,textvariable=search,height=38,corner_radius=9,border_color=BORDER,fg_color='#121A29',text_color=INK,placeholder_text='Search services…').pack(fill='x',padx=20,pady=(0,13))
        rows_frame=ctk.CTkFrame(box,fg_color='transparent');rows_frame.pack(fill='x',padx=20,pady=(0,12))
        try:
            currency,all_rows=price_list.read_prices(settings_store.load_settings())
        except ValueError as exc:
            all_rows=[];currency='';messagebox.showerror('Price list',str(exc),parent=self)
        def render(*_):
            for widget in rows_frame.winfo_children():widget.destroy()
            q=search.get().strip().casefold();rows=[r for r in all_rows if q in r['name'].casefold()]
            if not all_rows:
                label(rows_frame,'No prices have been added yet.',size=14,bold=True).pack(anchor='w',pady=(8,3));label(rows_frame,'A staff member can add this business’s rates in Price settings.',size=12,color=MUTED).pack(anchor='w',pady=(0,8))
            elif not rows:label(rows_frame,'No services match your search.',size=12,color=MUTED).pack(anchor='w',pady=10)
            else:
                for item in rows:
                    line=ctk.CTkFrame(rows_frame,fg_color='#242F42',corner_radius=9);line.pack(fill='x',pady=(0,6))
                    label(line,item['name'],size=13,bold=True,anchor='w',wraplength=490,justify='left').pack(side='left',fill='x',expand=True,padx=12,pady=10)
                    label(line,f"{currency} {item['price']}",size=13,bold=True,color='#B9FF70').pack(side='right',padx=12,pady=10)
        search.trace_add('write',render);render()
        actions=button_row(box,pady=(0,8))
        button(actions,'Create a receipt',lambda:self.show('Receipts')).pack(side='left',padx=(0,BTN_PADX))
        def export():
            try:text=price_list.as_text(self.business_name.get(),currency,all_rows)
            except ValueError as exc:return messagebox.showinfo('Price list',str(exc),parent=self)
            out=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF','*.pdf')],initialfile='price-list.pdf')
            if out:self.job('Saving price list…',lambda:services.export_document_pdf(text,out,'Price list',0),lambda _:messagebox.showinfo('Price list saved',f'All services saved to:\n{out}',parent=self))
        button(actions,'Save full list as PDF',export,secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(actions,'Staff: edit prices',lambda:self.show('Price settings'),secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(actions,'Refresh',lambda:self.show('Price list'),ghost=True).pack(side='left')
        label(box,'The PDF includes the full list, even when a search filter is active.',size=11,color=MUTED).pack(anchor='w',padx=20,pady=(0,16))
    def price_screen(self):
        self.heading(self.body,'Price settings','Set your own services and prices. The initial list is empty.')
        try:
            data=settings_store.load_settings(strict=True)
        except settings_store.CorruptedSettingsError as exc:
            messagebox.showerror('Settings damaged (fail-closed)',str(exc),parent=self); self.show('Home'); return
        box=card(self.body); box.pack(fill='x')
        label(box,'Your price list',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,5))
        label(box,'Enter one service per line: service name | price. Example format: Printing | 2.50.\nThe example is not a preset price; enter your own rates and include units in the service name.',size=12,color=MUTED,justify='left').pack(anchor='w',padx=20,pady=(0,12))
        label(box,'Currency symbol or code',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20); currency=entry(box,'R or USD'); currency.insert(0,str(data.get('currency','R'))); currency.pack(fill='x',padx=20,pady=(3,10))
        rates=ctk.CTkTextbox(box,height=210,fg_color='#121A29',border_color=BORDER,border_width=1,text_color=INK); rates.pack(fill='x',padx=20,pady=(0,12)); rates.insert('1.0','\n'.join(f"{x['name']} | {x['price']}" for x in data['prices']))
        def save():
            try:
                if self.busy: return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
                if self.receipt_items and not self.receipt_recorded and not messagebox.askyesno('Discard unrecorded receipt?','Saving new prices clears the current unrecorded receipt. Continue?',parent=self): return
                settings_store.save_prices(currency.get(),rates.get('1.0','end-1c')); self.reset_receipt()
                messagebox.showinfo('Price list saved','Your rates were saved. The current unsaved receipt has been cleared.',parent=self)
            except (ValueError,OSError) as exc: messagebox.showerror('Could not save rates',str(exc),parent=self)
        action_row(box,primary=(('Save price list',save),),pady=(0,20))
    def reset_receipt(self):
        self.receipt_items=[];self.receipt_currency=None;self.receipt_token=uuid.uuid4().hex;self.receipt_timestamp=None;self.receipt_business=None;self.receipt_recorded=False
    def receipt_screen(self):
        self.heading(self.body,'Receipts','Create a simple receipt from your business’s own price list.')
        data=settings_store.load_settings();prices=data['prices'];currency=data.get('currency','R');box=card(self.body);box.pack(fill='x')
        label(box,'Add a service',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,5))
        if not prices:
            label(box,'No prices have been set. A staff member can add services under Price settings.',size=12,color=MUTED).pack(anchor='w',padx=20,pady=(0,16))
            button(box,'Open price settings',lambda:self.show('Price settings'),secondary=True,width=150).pack(anchor='w',padx=20,pady=(0,20));return
        lookup={x['name']:x['price'] for x in prices};row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(4,12))
        choice=ctk.StringVar(value=prices[0]['name']);ctk.CTkOptionMenu(row,values=list(lookup),variable=choice,fg_color=GREEN,button_color=GREEN_HOVER,width=220).pack(side='left',padx=(0,8))
        qty=entry(row,'Qty');qty.insert(0,'1');qty.configure(width=75);qty.pack(side='left',padx=(0,8))
        receipt=ctk.CTkTextbox(box,height=225,fg_color='#121A29',border_color=BORDER,border_width=1,text_color=INK);receipt.pack(fill='x',padx=20,pady=(0,10))
        status=label(box,'',size=11,bold=True,color='#B9FF70');status.pack(anchor='w',padx=20,pady=(0,8))
        def draft_text():
            return services.receipt_text(self.receipt_business,self.receipt_currency,self.receipt_items,timestamp=self.receipt_timestamp,reference=self.receipt_token)
        def refresh():
            if not receipt.winfo_exists():return
            receipt.configure(state='normal');receipt.delete('1.0','end')
            if self.receipt_items:receipt.insert('1.0',draft_text())
            receipt.configure(state='disabled');status.configure(text='Recorded in sales history' if self.receipt_recorded else 'Not yet recorded in sales history')
        def add():
            if self.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
            if self.receipt_recorded:return messagebox.showinfo('Recorded sale','Start a new receipt before adding services.',parent=self)
            try:
                n=int(qty.get());assert 1<=n<=999
            except (ValueError,AssertionError):return messagebox.showerror('Quantity','Enter a quantity from 1 to 999.',parent=self)
            if not self.receipt_items:
                self.receipt_timestamp=datetime.now().astimezone().isoformat(timespec='seconds');self.receipt_business=self.business_name.get();self.receipt_currency=currency
            self.receipt_items.append({'name':choice.get(),'price':lookup[choice.get()],'qty':n});refresh()
        button(row,'Add service',add).pack(side='left')
        buttons=button_row(box,pady=(0,10))
        def save_receipt():
            if not self.receipt_items:return messagebox.showinfo('Receipt','Add a service first.',parent=self)
            out=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF','*.pdf')],initialfile='receipt.pdf')
            if out:
                text=draft_text();recorded=self.receipt_recorded;self.job('Saving receipt…',lambda:services.export_document_pdf(text,out,'Receipt',0),lambda _:messagebox.showinfo('Receipt saved',f'Saved to:\n{out}\n\n'+('This sale is already recorded.' if recorded else 'Saving a PDF does not record a sale. Use Record sale for that.'),parent=self))
        def record():
            if self.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
            if not self.receipt_items:return messagebox.showinfo('Receipt','Add a service first.',parent=self)
            if self.receipt_recorded:return messagebox.showinfo('Already recorded','This receipt has already been recorded. Start a new receipt for another sale.',parent=self)
            if not self.require_staff():return
            if not messagebox.askyesno('Record sale','Record this sale in local sales history? Payment status is not tracked. Once recorded, start a new receipt to make changes.',parent=self):return
            token=self.receipt_token;business=self.receipt_business;curr=self.receipt_currency;items=[dict(x) for x in self.receipt_items];when=datetime.now().astimezone().isoformat(timespec='seconds')
            def complete(created):
                if self.receipt_token==token:self.receipt_recorded=True;refresh()
                messagebox.showinfo('Sale recorded' if created else 'Already recorded','The sale is in local sales history. This does not confirm payment.',parent=self)
            self.job('Recording sale…',lambda:sales_store.record_sale(token,business,curr,items,when),complete)
        def remove_line():
            if self.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
            if self.receipt_recorded:return messagebox.showinfo('Recorded sale','Start a new receipt before making changes.',parent=self)
            if self.receipt_items:
                self.receipt_items.pop();refresh()
        def new_receipt():
            if self.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
            if self.receipt_items and not self.receipt_recorded and not messagebox.askyesno('Start a new receipt?','The current receipt has not been recorded. Discard it?',parent=self):return
            self.reset_receipt();refresh()
        button(buttons,'Record sale',record).pack(side='left',padx=(0,BTN_PADX))
        button(buttons,'Save receipt PDF',save_receipt,secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(buttons,'Undo last item',remove_line,ghost=True).pack(side='left',padx=(0,BTN_PADX))
        button(buttons,'New receipt',new_receipt,ghost=True).pack(side='left')
        label(box,'Record sale is staff-only. No payment status, payment method or tax is tracked.',size=11,color=MUTED).pack(anchor='w',padx=20,pady=(0,18));refresh()
    def ocr_screen_part(self):
        show_ocr(self,self.body,card,label,button)
    def create_shell(self):
        self.grid_columnconfigure(1,weight=1); self.grid_rowconfigure(0,weight=1)
        side=ctk.CTkFrame(self,width=220,corner_radius=0,fg_color='#141B2A'); side.grid(row=0,column=0,sticky='nsew'); side.grid_propagate(False)
        brand=ctk.CTkFrame(side,fg_color='transparent'); brand.pack(fill='x',padx=23,pady=(29,35))
        self.brand_icon=ctk.CTkLabel(brand,text='',image=self.icon_images[self.business_icon]); self.brand_icon.pack(side='left',padx=(0,10))
        column=ctk.CTkFrame(brand,fg_color='transparent'); column.pack(side='left'); self.brand_label=ctk.CTkLabel(column,textvariable=self.business_name,text_color='white',font=ctk.CTkFont(family=FONT,size=17,weight='bold'),wraplength=150,justify='left'); self.brand_label.pack(anchor='w'); label(column,f'DOCUMENT STUDIO • v{APP_BUILD}',size=10,bold=True,color='#99A9C3').pack(anchor='w')
        label(side,'WORKSPACE',size=10,bold=True,color='#8FA1BF').pack(anchor='w',padx=25,pady=(0,9))
        info=ctk.CTkFrame(side,fg_color='#273248',corner_radius=12); info.pack(side='bottom',fill='x',padx=15,pady=12)
        label(info,'PRIVATE BY DEFAULT',size=10,bold=True,color='#B9FF70').pack(anchor='w',padx=13,pady=(12,3)); label(info,'Files stay on this computer\nunless you choose AI.',size=11,color='#B9C7DC',justify='left').pack(anchor='w',padx=13,pady=(0,12))
        nav=ctk.CTkScrollableFrame(side,fg_color='transparent',corner_radius=0);nav.pack(side='top',fill='both',expand=True,padx=(6,0))
        for name,icon in [('Home','⌂'),('Price list','≡'),('PDF tools','▤'),('Bluetooth files',None),('Scan & images','▧'),('A4 picture sheets','▦'),('Career documents','✎'),('Cards & invitations','▭'),('Forms creator','⊞'),('Receipts','▣'),('Sales reports','▧'),('Price settings','≡'),('Business settings','⚙'),('AI settings','✦'),('Projects & recovery','▣'),('About & contact','ⓘ'),('Help & privacy','?')]:
            btn_kw={'image':self.icon_images['bluetooth_nav_muted'],'compound':'left','text':f' {name}'} if icon is None else {'text':f'{icon}   {name}'}
            b=ctk.CTkButton(nav,height=38,anchor='w',corner_radius=10,fg_color='transparent',hover_color='#303C55',text_color='#C4D0E5',font=ctk.CTkFont(family=FONT,size=13,weight='bold'),command=lambda n=name:self.show(n),**btn_kw); b.pack(fill='x',padx=(5,4),pady=2); self.nav_buttons[name]=b
        main=ctk.CTkFrame(self,fg_color='transparent'); main.grid(row=0,column=1,sticky='nsew'); main.grid_rowconfigure(1,weight=1); main.grid_columnconfigure(0,weight=1)
        bar=ctk.CTkFrame(main,fg_color=BG,height=67); bar.grid(row=0,column=0,sticky='ew',padx=28); bar.grid_propagate(False)
        self.header_title=label(bar,'Home',size=20,bold=True); self.header_title.pack(side='left')
        button(bar,'Next customer',self.clear_customer,secondary=True,width=130,height=34).pack(side='right',pady=15)
        self.lock_staff_btn=button(bar,'',self.lock_staff,secondary=True,image=self.icon_images['lock_staff'],width=38,height=34)
        self.lock_staff_btn.pack(side='right',padx=(0,8),pady=15)
        self.lock_staff_btn.bind('<Enter>',lambda _e:self.status.set('Click to lock staff access & clear AI key' if self.staff_unlocked else 'Staff access is locked'))
        button(bar,'? Help',self.quick_help,secondary=True,width=80,height=34).pack(side='right',padx=(0,8),pady=15)
        ctk.CTkLabel(bar,textvariable=self.clock,text_color='#C4D0E5',font=ctk.CTkFont(family=FONT,size=12,weight='bold')).pack(side='right',padx=(0,20))
        self.container=ctk.CTkFrame(main,fg_color='transparent'); self.container.grid(row=1,column=0,sticky='nsew',padx=28,pady=(0,8))
        footer=ctk.CTkFrame(main,fg_color='transparent',height=25); footer.grid(row=2,column=0,sticky='ew',padx=30,pady=(0,10)); label(footer,'●',size=10,color='#B9FF70').pack(side='left',padx=(0,5)); ctk.CTkLabel(footer,textvariable=self.status,text_color=MUTED,font=ctk.CTkFont(family=FONT,size=11)).pack(side='left')
    def show(self,name):
        if name in ('Business settings','Price settings','AI settings','Sales reports') and not self.require_staff(): return
        if self.current=='Scan & images' and self.ocr_editor and self.ocr_editor.winfo_exists(): self.ocr_text=self.ocr_editor.get('1.0','end-1c')
        self.ocr_editor=None
        if self.current=='Career documents':
            self.sync_long_fields()
            if self.doc_editor and self.doc_editor.winfo_exists(): self.doc_text=self.doc_editor.get('1.0','end-1c')
        if self.current=='Cards & invitations' and callable(getattr(self,'_sync_cards_hook',None)):
            try: self._sync_cards_hook()
            except Exception: pass
        if self.current=='Forms creator' and callable(getattr(self,'_sync_forms_hook',None)):
            try: self._sync_forms_hook()
            except Exception: pass
        self._sync_cards_hook=None; self._sync_forms_hook=None
        self.doc_editor=None; self.doc_preview_label=None; self.doc_preview_page_label=None; self.company_cell=None; self.role_label_widget=None; self.company_label_widget=None; self.long_fields={}; self.current=name; self.header_title.configure(text=name)
        for key,b in self.nav_buttons.items():
            active=(key==name)
            b.configure(fg_color='#344663' if active else 'transparent',text_color='white' if active else '#C4D0E5')
            if key=='Bluetooth files':
                b.configure(image=self.icon_images['bluetooth_nav' if active else 'bluetooth_nav_muted'])
        for child in self.container.winfo_children(): child.destroy()
        self.body=ctk.CTkScrollableFrame(self.container,fg_color='transparent',corner_radius=0); self.body.pack(fill='both',expand=True)
        {'Home':self.home,'Projects & recovery':self.projects_screen,'Help & privacy':self.help_screen,'About & contact':self.about_screen,'Price list':self.price_list_screen,'PDF tools':self.pdf_screen,'Bluetooth files':self.bluetooth_screen,'Scan & images':self.scan_screen,'A4 picture sheets':lambda:PictureSheet(self,self.body,card,label,button),'Career documents':self.career_screen,'Cards & invitations':lambda:show_cards(self,self.body,card,label,button,entry),'Forms creator':lambda:show_forms(self,self.body,card,label,button,entry),'Receipts':self.receipt_screen,'Sales reports':lambda:show_reports(self,self.body,card,label,button,entry),'Price settings':self.price_screen,'Business settings':self.business_screen,'AI settings':self.settings_screen}[name]()
        self._note_screen_visit(name)

    def _note_screen_visit(self, screen_name):
        if screen_name not in cloud_sync.ALLOWED_TELEMETRY_SCREENS:
            return
        with self._telemetry_lock:
            self._telemetry_counts[screen_name]=self._telemetry_counts.get(screen_name,0)+1
            total_queued=sum(self._telemetry_counts.values())
        now_m=time.monotonic()
        if self._last_telemetry_flush==0.0 or total_queued>=5 or (now_m-self._last_telemetry_flush)>=60.0:
            self._flush_telemetry_async()

    def _flush_telemetry_async(self, force=False):
        with self._telemetry_lock:
            if self._telemetry_inflight:
                return
            snapshot=dict(self._telemetry_counts)
            if not snapshot and not force:
                return
            self._telemetry_counts.clear()
            self._telemetry_inflight=True
            self._last_telemetry_flush=time.monotonic()
        cur_screen=self.current or 'Home'
        def _bg():
            try:
                cfg=settings_store.load_settings()
                res=cloud_sync.send_workspace_telemetry(cfg,current_screen=cur_screen,screen_counts=snapshot,app_version=APP_BUILD)
                if not res.get('ok') and snapshot:
                    with self._telemetry_lock:
                        for k,v in snapshot.items():
                            self._telemetry_counts[k]=min(5000,self._telemetry_counts.get(k,0)+v)
            except Exception:
                pass
            finally:
                with self._telemetry_lock:
                    self._telemetry_inflight=False
        threading.Thread(target=_bg,daemon=True).start()

    def _telemetry_tick(self):
        self._flush_telemetry_async(force=True)
        self.after(90000,self._telemetry_tick)
    def heading(self,parent,title,subtitle):
        label(parent,title,size=24,bold=True).pack(anchor='w',pady=(2,3)); label(parent,subtitle,size=13,color=MUTED,wraplength=740,justify='left').pack(anchor='w',pady=(0,18))
    def home(self):
        hero=ctk.CTkFrame(self.body,fg_color='#202B43',corner_radius=18); hero.pack(fill='x',pady=(0,20)); hero.grid_columnconfigure(0,weight=1)
        label(hero,'YOUR ALL-IN-ONE WORKSPACE',size=10,bold=True,color='#B9FF70').pack(anchor='w',padx=26,pady=(25,6))
        label(hero,'Big help for life’s little paperwork.',size=27,bold=True,color='white').pack(anchor='w',padx=26)
        label(hero,'Everything you need for documents and your next career move.',size=13,color='#C3CEE1').pack(anchor='w',padx=26,pady=(7,17))
        button(hero,'Create a career document   →',lambda:self.show('Career documents'),fg_color=LIME,hover_color='#A9EB65',text_color='#142018',width=260).pack(anchor='w',padx=26,pady=(0,25))
        hbtns=ctk.CTkFrame(self.body,fg_color='transparent'); hbtns.pack(anchor='w',pady=(0,17))
        button(hbtns,'New here? Open the quick guide  →',lambda:self.show('Help & privacy'),secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(hbtns,'ⓘ About & contact',lambda:self.show('About & contact'),ghost=True).pack(side='left')
        if self.recovery_exists(): label(self.body,'A recovery copy is available. Staff can open it from Projects & recovery.',size=11,color='#B9FF70').pack(anchor='w',pady=(0,10))
        label(self.body,'What can we help with?',size=19,bold=True).pack(anchor='w',pady=(0,11))
        grid=ctk.CTkFrame(self.body,fg_color='transparent'); grid.pack(fill='x'); grid.grid_columnconfigure((0,1),weight=1)
        for i,(title,desc,icon,dest) in enumerate([('Price list','See the café’s current services and prices.','≡','Price list'),('Merge & split PDFs','Bring files together or extract pages.','▤','PDF tools'),('Receive via Bluetooth','Open Windows Bluetooth file transfer.',None,'Bluetooth files'),('Scan & convert','Import images and create a PDF.','▧','Scan & images'),('Career documents','Make a CV, cover letter or introduction.','✎','Career documents'),('Cards & invitations','Design business cards & event invitations (13 templates).','▭','Cards & invitations'),('Forms creator','Build printable & fillable PDF forms and affidavits.','⊞','Forms creator'),('A4 picture sheets','Arrange, crop and resize pictures on A4.','▦','A4 picture sheets'),('AI writing help','Improve your draft with your own API key.','✦','AI settings')]):
            box=card(grid); box.grid(row=i//2,column=i%2,sticky='nsew',padx=(0,10) if i%2==0 else (0,0),pady=(0,10))
            if icon is None: ctk.CTkLabel(box,text='',image=self.icon_images['bluetooth_card']).pack(anchor='w',padx=18,pady=(14,4))
            else: label(box,icon,size=24,bold=True,color='#B9FF70').pack(anchor='w',padx=18,pady=(14,2))
            label(box,title,size=15,bold=True).pack(anchor='w',padx=18); label(box,desc,size=12,color=MUTED).pack(anchor='w',padx=18,pady=(4,10)); button(box,'Open  →',lambda n=dest:self.show(n),ghost=True).pack(anchor='w',padx=18,pady=(0,14))
    def job(self,description,fn,success,error_cb=None):
        if self.busy: return messagebox.showinfo('Please wait','Another task is still running.',parent=self)
        self.busy=True; self.status.set(description)
        def work():
            try: self.events.put(('ok',success,fn()))
            except Exception as exc: self.events.put(('error',error_cb,str(exc)))
        threading.Thread(target=work,daemon=True).start()
    def poll_events(self):
        try:
            while True:
                kind,callback,result=self.events.get_nowait(); self.busy=False
                if kind=='error':
                    if callback is not None: callback(result)
                    else: self.status.set('Task could not be completed')
                    messagebox.showerror('Café Desk',result,parent=self)
                else:
                    self.status.set('Ready  •  Task completed'); callback(result)
        except queue.Empty: pass
        self.after(100,self.poll_events)
    def clear_customer(self):
        if self.busy: return messagebox.showinfo('Please wait','Please wait until the current task finishes.',parent=self)
        if not messagebox.askyesno('Next customer','Clear the current customer’s draft, project photos, picture sheet, cards, forms, OCR text/images, scanned pages, receipt, temporary merge copies, recovery copies and AI key? Staff will be locked. Saved PDFs, saved projects, Downloads and Bluetooth-received files on disk will NOT be deleted.',parent=self): return
        for var in self.values.values(): var.set('')
        for widget in self.long_fields.values():
            if widget.winfo_exists(): widget.delete('1.0','end')
        self.recovery_enabled=False; self.delete_recovery_copies(notify=False)
        self.key.set(''); self.ai_generation+=1; self.staff_unlocked=False; self.update_lock_button(); self.receipt_items=[]; self.receipt_currency=None; self.receipt_token=uuid.uuid4().hex; self.receipt_timestamp=None; self.receipt_business=None; self.receipt_recorded=False; self.ocr_text=''; self.ocr_images=[]; self.ocr_selected_index=None; self.ocr_scan_animating=True; self.cv_photo=None; self.cv_thumbnail=None; self.sheet={'items':[],'preset':'4 per page','page':0,'selected':None,'mode':'Fit whole image','brightness':100,'gray':False}; self.card_state={'kind':'Business card','template':cards_renderer.CARD_NAMES[0],'sheet':cards_renderer.SHEET_OPTIONS['Business card'][0][0],'crop_marks':True,'photo':None,'business_fields':cards_renderer.default_fields('Business card'),'invitation_fields':cards_renderer.default_fields('Invitation card')}; self.form_state=forms_renderer.default_form_spec()
        if self.ocr_editor and self.ocr_editor.winfo_exists(): self.ocr_editor.delete('1.0','end')
        self.doc_text=''; self.pdf_files=[]; self.image_files=[]; self.scanned_pages=[]; self.split_source=''; self.split_preview_fingerprint=None; self.unlock_pdf_source=''; self.remove_session_files(); self.session_temp=tempfile.TemporaryDirectory(prefix='cafedesk-')
        if self.doc_editor and self.doc_editor.winfo_exists(): self.doc_editor.delete('1.0','end')
        self.show('Home'); self.status.set('Current workspace cleared  •  Saved files on disk were not deleted')
    def bluetooth_screen(self):
        self.heading(self.body,'Bluetooth files','Receive customer files using the Windows Bluetooth transfer window.')
        box=card(self.body);box.pack(fill='x',pady=(0,12))
        label(box,'1   Pair the phone and computer',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,4))
        label(box,'Turn Bluetooth on and pair the phone in Windows settings. The phone must support Bluetooth file sharing. Windows will ask you to confirm pairing.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,11))
        def launch(action):
            try:action()
            except (ValueError,OSError) as exc:messagebox.showerror('Bluetooth shortcut',str(exc),parent=self)
        button(box,'Open Windows Bluetooth settings',lambda:launch(bluetooth_access.open_settings),secondary=True,width=220).pack(anchor='w',padx=20,pady=(0,18))
        box=card(self.body);box.pack(fill='x',pady=(0,12))
        label(box,'2   Receive the file',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,4))
        label(box,'Open Windows Bluetooth File Transfer, choose Receive files, then send the file from the paired phone. In Windows, choose where to save it and select Finish.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,11))
        button(box,'Receive via Bluetooth',lambda:launch(bluetooth_access.open_transfer),width=195).pack(anchor='w',padx=20,pady=(0,10))
        label(box,'This opens a separate Windows window. Café Desk does not receive or open files automatically. Android devices with Bluetooth file sharing may work; iPhone Bluetooth file transfer to Windows is generally unavailable.',size=11,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,18))
        box=card(self.body);box.pack(fill='x',pady=(0,12))
        label(box,'3   Choose the received file',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,4))
        label(box,'After Windows has saved the file, choose it below. You can also open it from another Café Desk tool. Do not open files from senders you do not trust.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,11))
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,12))
        def add_received(kind):
            types=[('PDF files','*.pdf')] if kind=='pdf' else [('Images','*.png *.jpg *.jpeg *.bmp *.tif *.tiff')]
            paths=filedialog.askopenfilenames(parent=self,title='Choose received '+('PDF files' if kind=='pdf' else 'images'),filetypes=types)
            if not paths: return
            valid=[]; skipped=[]
            for p in paths:
                try:
                    if kind=='pdf': services.assert_safe_user_input(p)
                    else:
                        with services.open_safe_image(p): pass
                    valid.append(p)
                except ValueError as exc:
                    skipped.append(f'{Path(p).name}: {exc}')
            if skipped: messagebox.showwarning('Some files skipped','\n'.join(skipped[:5]),parent=self)
            if valid:
                if kind=='pdf':
                    self.pdf_files.extend(valid)
                    self.show('PDF tools')
                else:
                    if kind=='ocr': self.ocr_images.extend(valid)
                    else: self.image_files.extend(valid)
                    self.show('Scan & images')
        button(row,'Add received PDF',lambda:add_received('pdf'),secondary=True,width=160).pack(side='left',padx=(0,8))
        button(row,'Add received images',lambda:add_received('image'),secondary=True,width=170).pack(side='left',padx=(0,8))
        button(row,'Add received for OCR',lambda:add_received('ocr'),secondary=True,width=175).pack(side='left')
        label(box,'Windows saves the received files on the computer. Next customer clears the app’s lists, not the saved files. Delete them separately when appropriate.',size=11,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,18))
        if sys.platform!='win32':label(self.body,'The shortcuts on this page currently work on Windows only. Use your operating system’s file-sharing tools on macOS or Linux.',size=12,color='#E8C187',wraplength=740,justify='left').pack(anchor='w',pady=(0,10))
    def filebox(self,parent,kind):
        box=card(parent); box.pack(fill='x',pady=(0,14)); files=self.pdf_files if kind=='pdf' else self.image_files
        title='Files to merge' if kind=='pdf' else 'Images to convert'; desc='Select PDFs and arrange them in the order you want.' if kind=='pdf' else 'Choose images in the order you want them in the PDF. Multi-page TIFFs are supported.'
        label(box,title,size=16,bold=True).pack(anchor='w',padx=20,pady=(17,3)); label(box,desc,size=12,color=MUTED).pack(anchor='w',padx=20)
        frame=ctk.CTkFrame(box,fg_color='#121A29',corner_radius=10,border_width=1,border_color=BORDER); frame.pack(fill='x',padx=20,pady=(13,9))
        lst=tk.Listbox(frame,height=5,font=(FONT,11),bg='#121A29',fg=INK,selectbackground='#405270',selectforeground='#FFFFFF',relief='flat',highlightthickness=0,exportselection=False); lst.pack(fill='x',padx=10,pady=10)
        def refresh(select=None):
            lst.delete(0,'end')
            for f in files: lst.insert('end',Path(f).name+'   —   '+str(Path(f).parent))
            if select is not None and 0<=select<len(files): lst.selection_set(select)
        def add():
            ft=[('PDF files','*.pdf')] if kind=='pdf' else [('Images','*.png *.jpg *.jpeg *.bmp *.tif *.tiff')]
            chosen=filedialog.askopenfilenames(parent=self,filetypes=ft)
            if not chosen: return
            skipped=[]
            for p in chosen:
                try:
                    if kind=='pdf': services.assert_safe_user_input(p)
                    else:
                        with services.open_safe_image(p): pass
                    files.append(p)
                except ValueError as exc:
                    skipped.append(f'{Path(p).name}: {exc}')
            if skipped: messagebox.showwarning('Some files skipped','\n'.join(skipped[:5]),parent=self)
            refresh()
        def remove():
            sel=lst.curselection()
            if sel: del files[sel[0]]; refresh()
        def clear_all():
            if files and messagebox.askyesno('Clear list','Remove all files from this list? (Original files on disk are not deleted.)',parent=self):
                files.clear(); refresh()
        def move(d):
            sel=lst.curselection()
            if sel:
                i=sel[0]; j=i+d
                if 0<=j<len(files): files[i],files[j]=files[j],files[i]; refresh(j)
        actions=button_row(box,pady=(0,18))
        button(actions,'Merge & save' if kind=='pdf' else 'Convert to PDF',lambda:self.save_files(kind),width=150).pack(side='left',padx=(0,BTN_PADX))
        button(actions,'＋ Add files',add,secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(actions,'✕ Remove',remove,secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(actions,'Clear all',clear_all,ghost=True).pack(side='left',padx=(0,BTN_PADX))
        button(actions,'↑',lambda:move(-1),ghost=True,width=38).pack(side='left',padx=(0,4))
        button(actions,'↓',lambda:move(1),ghost=True,width=38).pack(side='left')
        refresh()
    def save_files(self,kind):
        paths=list(self.pdf_files if kind=='pdf' else self.image_files)
        if not paths: return messagebox.showinfo('Add files first','Please add files before continuing.',parent=self)
        out=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF document','*.pdf')],initialfile='merged.pdf' if kind=='pdf' else 'images.pdf')
        if not out: return
        task=services.merge_pdf if kind=='pdf' else services.images_to_pdf
        self.job('Working on your PDF…',lambda:task(paths,out),lambda _:messagebox.showinfo('All done',f'Your PDF was saved:\n{out}',parent=self))
    def pdf_screen(self):
        self.heading(self.body,'PDF tools','Simple, reliable tools for the documents you use every day.')
        self.filebox(self.body,'pdf')
        box=card(self.body); box.pack(fill='x',pady=(0,14)); label(box,'Split or extract pages',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,3)); label(box,'Choose a PDF, preview its pages and select the ones to extract, or enter page numbers manually.',size=12,color=MUTED,wraplength=700,justify='left').pack(anchor='w',padx=20)
        row=ctk.CTkFrame(box,fg_color='transparent'); row.pack(fill='x',padx=20,pady=(12,8))
        file_label=label(row,Path(self.split_source).name if self.split_source else 'No PDF selected',size=12,color=MUTED); file_label.pack(side='left',fill='x',expand=True,anchor='w')
        def choose():
            path=filedialog.askopenfilename(parent=self,filetypes=[('PDF document','*.pdf')])
            if path:
                try: services.assert_safe_user_input(path)
                except ValueError as exc: return messagebox.showerror('Cannot open PDF',str(exc),parent=self)
                self.split_source=path;self.split_preview_fingerprint=None;pages.delete(0,'end');file_label.configure(text=Path(path).name)
        button(row,'Choose PDF',choose,secondary=True,width=120).pack(side='right')
        controls=ctk.CTkFrame(box,fg_color='transparent'); controls.pack(fill='x',padx=20,pady=(0,9)); pages=entry(controls,'Pages, e.g. 1-3, 5'); pages.pack(side='left',fill='x',expand=True,padx=(0,8))
        def preview():
            if not self.split_source:return messagebox.showinfo('Select a PDF','Choose a PDF first.',parent=self)
            if self.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
            from pdf_picker_ui import PDFPagePicker
            source=self.split_source
            def apply(spec,fp):
                if self.split_source!=source or not pages.winfo_exists():return
                pages.delete(0,'end');pages.insert(0,spec);self.split_preview_fingerprint=fp
            PDFPagePicker(self,source,apply)
        button(controls,'Preview & select pages',preview,secondary=True,width=185).pack(side='left',padx=(0,8));button(controls,'Extract pages',lambda:self.extract(pages.get()),width=130).pack(side='left')
        button(box,'Split every page',self.split_all,secondary=True,width=140).pack(anchor='w',padx=20,pady=(0,19))

        # Paid Add-on: Remove PDF password (Machine-ID owner approval required)
        addon_st=license_guard.current_addon_status(license_guard.FEATURE_PDF_UNLOCK)
        mid_now=addon_st.get('machine_id') or license_guard.get_machine_id()
        ubox=card(self.body); ubox.pack(fill='x',pady=(0,14))
        uhead=ctk.CTkFrame(ubox,fg_color='transparent'); uhead.pack(fill='x',padx=20,pady=(17,3))
        label(uhead,'Remove PDF password',size=16,bold=True).pack(side='left')
        if addon_st.get('valid'):
            label(uhead,f"✓ PAID ADD-ON ACTIVE  •  Expires {addon_st['expires_at']} ({addon_st['days_remaining']}d left)",size=11,bold=True,color=LIME).pack(side='right')
            label(ubox,'Choose a password-protected PDF, enter its current password if required to open it, and save a clean unlocked PDF copy (ready for printing, merging or splitting).',size=12,color=MUTED,wraplength=730,justify='left').pack(anchor='w',padx=20,pady=(0,10))
            urow1=ctk.CTkFrame(ubox,fg_color='transparent'); urow1.pack(fill='x',padx=20,pady=(0,8))
            ufile_lbl=label(urow1,Path(self.unlock_pdf_source).name if self.unlock_pdf_source else 'No protected PDF selected',size=12,color=MUTED)
            ufile_lbl.pack(side='left',fill='x',expand=True,anchor='w')
            def choose_locked_pdf():
                p=filedialog.askopenfilename(parent=self,filetypes=[('PDF document','*.pdf')])
                if p:
                    try: services.assert_safe_user_input(p)
                    except ValueError as exc: return messagebox.showerror('Cannot open PDF',str(exc),parent=self)
                    self.unlock_pdf_source=p; ufile_lbl.configure(text=Path(p).name)
            button(urow1,'Choose protected PDF',choose_locked_pdf,secondary=True,width=170).pack(side='right')

            urow2=ctk.CTkFrame(ubox,fg_color='transparent'); urow2.pack(fill='x',padx=20,pady=(0,18))
            pwd_var=ctk.StringVar(); show_pwd=ctk.BooleanVar(value=False)
            pwd_ent=entry(urow2,'PDF password (leave blank if only print/edit restrictions)',textvariable=pwd_var,show='•')
            pwd_ent.pack(side='left',fill='x',expand=True,padx=(0,8))
            def toggle_pwd(): pwd_ent.configure(show='' if show_pwd.get() else '•')
            ctk.CTkCheckBox(urow2,text='Show',variable=show_pwd,command=toggle_pwd,width=64,fg_color=LIME,checkmark_color='#102006',text_color=MUTED).pack(side='left',padx=(0,8))

            def run_unlock(add_merge=False):
                src=self.unlock_pdf_source
                if not src: return messagebox.showinfo('Select a PDF','Choose a password-protected PDF first.',parent=self)
                pwd_val=pwd_var.get()
                if add_merge:
                    dest=str(Path(self.session_temp.name)/(Path(src).stem+'-unlocked-'+uuid.uuid4().hex[:6]+'.pdf'))
                else:
                    stem=Path(src).stem
                    dest=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF document','*.pdf')],initialfile=f'{stem}-unlocked.pdf')
                    if not dest: return
                def done(res):
                    pages_n,was_enc=res
                    pwd_var.set('')
                    if add_merge:
                        self.pdf_files.append(dest)
                        self.show('PDF tools')
                        messagebox.showinfo('Unlocked & added to merge',f'Removed password from {pages_n} page(s) and added the unlocked copy to Files to merge.',parent=self)
                    else:
                        note='Password removed!' if was_enc else 'Note: this PDF had no password encryption; saved a clean copy.'
                        messagebox.showinfo('PDF unlocked',f'{note}\n{pages_n} page(s) saved to:\n{dest}',parent=self)
                self.job('Removing PDF password…',lambda:services.remove_pdf_password(src,dest,pwd_val),done)

            button(urow2,'🔓 Unlock & save PDF',lambda:run_unlock(False),fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(side='left',padx=(0,BTN_PADX))
            button(urow2,'＋ Unlock to merge',lambda:run_unlock(True),secondary=True).pack(side='left')
        else:
            label(uhead,'🔒 PAID ADD-ON  •  OWNER APPROVAL REQUIRED',size=11,bold=True,color='#FFDBA2').pack(side='right')
            label(ubox,'Disable and strip passwords from customer PDFs (bank statements, payslips, e-statements & restricted PDFs) so they print and merge cleanly.\nThis is a paid add-on locked per computer — send this PC’s Machine ID to the owner for approval and paste your Paid Add-on Key (CDADD1...).',size=12,color=MUTED,wraplength=730,justify='left').pack(anchor='w',padx=20,pady=(0,12))
            lock_row=ctk.CTkFrame(ubox,fg_color='#131B2A',corner_radius=10,border_width=1,border_color=BORDER); lock_row.pack(fill='x',padx=20,pady=(0,18))
            label(lock_row,f'Machine ID:  {mid_now}',size=13,bold=True,color=LIME).pack(side='left',padx=14,pady=12)
            button(lock_row,'🔑 Activate Paid Add-on Key',lambda:license_ui.open_addon_modal(self,on_updated=lambda _i:self.show('PDF tools')),fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(side='right',padx=12,pady=10)
            def ask_owner_wa():
                try:
                    services.open_external_url(services.whatsapp_chat_url(f'Hi, I would like to pay for and activate the PDF Password Remover add-on for Machine ID: {mid_now}'))
                except ValueError as exc:
                    messagebox.showerror('Could not open WhatsApp',str(exc),parent=self)
            button(lock_row,'💬 Request Owner Approval',ask_owner_wa,secondary=True,width=210).pack(side='right',padx=(0,BTN_PADX),pady=10)
    def extract(self,spec):
        src=self.split_source
        if not src: return messagebox.showinfo('Select a PDF','Choose a PDF first.',parent=self)
        if not spec.strip(): return messagebox.showinfo('Page selection','Enter page numbers such as 1-3, 5.',parent=self)
        if self.split_preview_fingerprint is not None:
            try:
                import pdf_preview
                if pdf_preview.fingerprint(src)!=self.split_preview_fingerprint: return messagebox.showerror('PDF changed','The PDF changed since it was previewed. Choose it again and reselect the pages.',parent=self)
            except (OSError,ValueError) as exc:return messagebox.showerror('Cannot read PDF',str(exc),parent=self)
        out=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF document','*.pdf')],initialfile='extracted-pages.pdf')
        if out: self.job('Extracting pages…',lambda:services.extract_pdf(src,spec,out),lambda count:messagebox.showinfo('All done',f'{count} page(s) saved to:\n{out}',parent=self))
    def split_all(self):
        src=self.split_source
        if not src: return messagebox.showinfo('Select a PDF','Choose a PDF first.',parent=self)
        folder=filedialog.askdirectory(parent=self,title='Choose output folder (existing files will not be overwritten)')
        if folder: self.job('Splitting pages…',lambda:services.split_each_page(src,folder),lambda count:messagebox.showinfo('All done',f'{count} PDF file(s) saved in:\n{folder}',parent=self))
    def scan_screen(self):
        self.heading(self.body,'Scan & convert','Bring paper documents and images into your digital workspace.')
        show_scanner(self,self.body,card,label,button)
        box=card(self.body); box.pack(fill='x',pady=(0,14)); label(box,'Scan a document',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4)); label(box,'Open your computer’s scanner app, scan and save. Then import the saved images below.\nIf you saved a PDF, use PDF tools instead.',size=12,color=MUTED,justify='left').pack(anchor='w',padx=20); button(box,'Open scanner app   ↗',self.open_scanner,secondary=True,width=175).pack(anchor='w',padx=20,pady=(14,18))
        self.filebox(self.body,'image'); self.ocr_screen_part()
    def open_scanner(self):
        try:
            if sys.platform.startswith('win'): os.startfile('ms-windows-scan:')
            elif sys.platform=='darwin': subprocess.Popen(['open','-a','Image Capture'])
            else: subprocess.Popen(['simple-scan'])
        except Exception: messagebox.showinfo('Scanner app','Could not launch the scanner app. Install/open Windows Scan, Image Capture, or Linux Document Scanner (simple-scan). Install your scanner drivers too. You can use any scanner app and import the result.',parent=self)
    def career_screen(self):
        self.heading(self.body,'Career documents','Add your details, edit your draft, and switch document types & all 13 designs right beside the live A4 preview.')

        form=card(self.body); form.pack(fill='x',pady=(0,12)); label(form,'1   Add your details',size=16,bold=True).pack(anchor='w',padx=20,pady=(16,0))
        self.career_hint=label(form,'',size=12,color=MUTED,wraplength=760,justify='left'); self.career_hint.pack(anchor='w',padx=20,pady=(3,5))
        self.details_grid=ctk.CTkFrame(form,fg_color='transparent'); self.details_grid.pack(fill='x',padx=20,pady=(0,9)); self.details_grid.grid_columnconfigure((0,1),weight=1)
        self.field_cells={}; self.field_labels={}
        for key in LABELS:
            cell=ctk.CTkFrame(self.details_grid,fg_color='transparent')
            lbl=label(cell,LABELS[key],size=11,bold=True,color=MUTED); lbl.pack(anchor='w',pady=(0,4))
            self.field_cells[key]=cell; self.field_labels[key]=lbl
            if key in ('skills','experience','education'):
                text=ctk.CTkTextbox(cell,height=92,corner_radius=9,border_color=BORDER,border_width=1,fg_color='#121A29',text_color=INK,wrap='word',font=ctk.CTkFont(family=FONT,size=12)); text.pack(fill='x'); text.insert('1.0',self.values[key].get()); self.long_fields[key]=text
            else:
                ph={'name':'e.g. Nandi Mkhize','contact':'Phone • email • city','role':'Leave blank for a general CV, or e.g. Cashier / Admin','company':'Only for a specific employer — optional'}.get(key,'')
                ctk.CTkEntry(cell,textvariable=self.values[key],placeholder_text=ph,height=37,corner_radius=9,border_color=BORDER,fg_color='#121A29',text_color=INK).pack(fill='x')
        self.layout_career_fields()

        photo_row=ctk.CTkFrame(form,fg_color='transparent'); photo_row.pack(fill='x',padx=20,pady=(8,0))
        self.cv_photo_label=label(photo_row,'CV photo: '+(Path(self.cv_photo).name if self.cv_photo else 'none'),size=11,color=MUTED); self.cv_photo_label.pack(side='left',padx=(0,8))
        button(photo_row,'＋ Choose CV photo',self.choose_cv_photo,secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(photo_row,'✕ Remove photo',self.remove_cv_photo,ghost=True).pack(side='left')
        label(form,'Optional CV photo appears in the A4 preview and exported CV PDF.',size=11,color=MUTED,wraplength=700,justify='left').pack(anchor='w',padx=20,pady=(5,6))
        button(form,'Create draft   →',self.generate,fg_color=LIME,hover_color='#A9EB65',text_color='#142018',width=165).pack(anchor='w',padx=20,pady=(2,18))

        editor=card(self.body); editor.pack(fill='both',expand=True,pady=(0,14))
        label(editor,'2   Review, choose template & preview',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,2))
        label(editor,'Edit your text on the left, and choose the Document type & Template right above the live A4 preview.',size=12,color=MUTED).pack(anchor='w',padx=20,pady=(0,10))

        split=ctk.CTkFrame(editor,fg_color='transparent'); split.pack(fill='both',expand=True,padx=20,pady=(0,12))
        split.grid_columnconfigure(0,weight=1)

        left_pane=ctk.CTkFrame(split,fg_color='transparent')
        left_pane.grid(row=0,column=0,sticky='nsew',padx=(0,16))
        self.doc_editor=ctk.CTkTextbox(left_pane,height=470,corner_radius=10,fg_color='#121A29',border_color=BORDER,border_width=1,text_color=INK,font=ctk.CTkFont(family=FONT,size=13),wrap='word')
        self.doc_editor.pack(fill='both',expand=True,pady=(0,10))
        self.doc_editor.insert('1.0',self.doc_text)

        row=ctk.CTkFrame(left_pane,fg_color='transparent'); row.pack(fill='x',pady=(0,8))
        button(row,'⬇ Save PDF',self.export_pdf,width=115).pack(side='left',padx=(0,8))
        button(row,'＋ Add to PDF merge',self.add_document_to_merge,secondary=True,width=155).pack(side='left',padx=(0,8))
        button(row,'⬇ Save text',self.export_txt,secondary=True,width=110).pack(side='left',padx=(0,8))
        button(row,'✦ Improve with AI',self.improve,secondary=True,width=150).pack(side='left')
        project_row=ctk.CTkFrame(left_pane,fg_color='transparent');project_row.pack(fill='x',pady=(0,4))
        button(project_row,'💾 Save project',lambda:self.save_project('career'),secondary=True,width=135).pack(side='left',padx=(0,8))
        button(project_row,'📂 Open project',self.open_project,secondary=True,width=135).pack(side='left')
        label(left_pane,'Merge copies are temporary until you save the merged PDF. Next customer removes them.',size=11,color=MUTED,wraplength=520,justify='left').pack(anchor='w',pady=(4,0))

        right_pane=ctk.CTkFrame(split,fg_color='#232E44',corner_radius=12,border_width=1,border_color=BORDER)
        right_pane.grid(row=0,column=1,sticky='n')

        # Document type & Template placed right around the preview
        picker_box=ctk.CTkFrame(right_pane,fg_color='transparent')
        picker_box.pack(fill='x',padx=12,pady=(12,8))
        label(picker_box,'Document type',size=11,bold=True,color=MUTED).pack(anchor='w',pady=(0,3))
        ctk.CTkOptionMenu(picker_box,values=CATEGORIES,variable=self.doc_type,command=self.change_doc_type,fg_color=GREEN,button_color=GREEN_HOVER,button_hover_color='#374F77',height=33,width=290).pack(fill='x',pady=(0,7))
        self.template_caption=label(picker_box,'Template • 13 CV designs' if self.doc_type.get()==CATEGORIES[0] else 'Template • 10 options',size=11,bold=True,color=MUTED)
        self.template_caption.pack(anchor='w',pady=(0,3))
        self.template_menu=ctk.CTkOptionMenu(picker_box,values=CV_NAMES if self.doc_type.get()==CATEGORIES[0] else NAMES,variable=self.template,command=self.on_template_change,fg_color=GREEN,button_color=GREEN_HOVER,button_hover_color='#374F77',height=33,width=290)
        self.template_menu.pack(fill='x',pady=(0,6))
        button(picker_box,'↗ Compare all 13 CV designs',self.open_cv_gallery,secondary=True,width=290,height=28).pack(fill='x')

        phead=ctk.CTkFrame(right_pane,fg_color='transparent'); phead.pack(fill='x',padx=12,pady=(6,6))
        label(phead,'A4 PDF preview',size=13,bold=True).pack(side='left')
        pbtns=ctk.CTkFrame(phead,fg_color='transparent'); pbtns.pack(side='right')
        button(pbtns,'↻',lambda:self.refresh_doc_preview(),secondary=True,width=34,height=28).pack(side='left',padx=(0,6))
        ctk.CTkButton(pbtns,text='',image=self.icon_images['enlarge'],command=self.enlarge_doc_preview,width=34,height=28,corner_radius=8,fg_color=SOFT,hover_color='#40506D').pack(side='left')
        self.doc_preview_label=ctk.CTkLabel(right_pane,text='Click "Create draft →"\nor type in the editor,\nthen click "↻"\nto preview the A4 PDF.',width=290,height=390,fg_color='#121A29',corner_radius=8,text_color=MUTED,justify='center')
        self.doc_preview_label.pack(padx=12,pady=(0,8))
        pnav=ctk.CTkFrame(right_pane,fg_color='transparent'); pnav.pack(fill='x',padx=12,pady=(0,10))
        button(pnav,'‹',lambda:self.turn_doc_preview(-1),secondary=True,width=38,height=28).pack(side='left')
        self.doc_preview_page_label=label(pnav,'Page 1 / 1',size=11,bold=True,color=MUTED)
        self.doc_preview_page_label.pack(side='left',expand=True)
        button(pnav,'›',lambda:self.turn_doc_preview(1),secondary=True,width=38,height=28).pack(side='right')

        if self.doc_text.strip():
            self.after(80,self.refresh_doc_preview)

    def layout_career_fields(self):
        if not hasattr(self,'details_grid') or not self.details_grid.winfo_exists(): return
        kind=self.doc_type.get()
        labels=field_labels_for(kind)
        show_company=uses_company_field(kind)
        if kind in (CATEGORIES[0],CATEGORIES[2]):
            self.career_hint.configure(text='Building a general CV? Leave Desired role blank — no job title or company is required. Add multiple lines for skills, experience and education.')
        else:
            self.career_hint.configure(text='Applying for a specific job? Add the role or company if known, or leave either blank for a general speculative application.')
        for cell in self.field_cells.values():
            cell.grid_forget()
        visible=[k for k in LABELS if k!='company' or show_company]
        row_idx=0; col_idx=0
        for key in visible:
            self.field_labels[key].configure(text=labels[key])
            cell=self.field_cells[key]
            # If company is hidden, let role span full width neatly so long-text fields start aligned on the next row
            if key=='role' and not show_company:
                if col_idx==1: row_idx+=1; col_idx=0
                cell.grid(row=row_idx,column=0,columnspan=2,sticky='nsew',padx=0,pady=(8,0))
                row_idx+=1; col_idx=0
            else:
                cell.grid(row=row_idx,column=col_idx,sticky='nsew',padx=(0,10) if col_idx==0 else (0,0),pady=(8,0))
                if col_idx==1: row_idx+=1; col_idx=0
                else: col_idx=1

    def on_template_change(self,_choice=None):
        self.doc_preview_page=1
        if self.current_text(): self.refresh_doc_preview()

    def turn_doc_preview(self,delta):
        if not self.current_text(): return
        nxt=max(1,min(self.doc_preview_total,self.doc_preview_page+delta))
        if nxt!=self.doc_preview_page:
            self.doc_preview_page=nxt
            self.refresh_doc_preview()

    def refresh_doc_preview(self):
        if not self.doc_preview_label or not self.doc_preview_label.winfo_exists(): return
        text=self.current_text()
        if not text:
            self.doc_preview_image=None
            self.doc_preview_label.configure(image=None,text='Click "Create draft →"\nor type in the editor,\nthen click "↻ Refresh"\nto preview the A4 PDF.')
            if self.doc_preview_page_label and self.doc_preview_page_label.winfo_exists():
                self.doc_preview_page_label.configure(text='Page 1 / 1')
            return
        if self.busy: return
        category=self.doc_type.get(); index=self.selected_template_index(); photo=self.cv_photo if category==CATEGORIES[0] else None; page=self.doc_preview_page
        def done(res):
            pil_img,clamped_page,total=res
            self.doc_preview_page=clamped_page; self.doc_preview_total=total
            if self.doc_preview_label and self.doc_preview_label.winfo_exists():
                ctk_img=ctk.CTkImage(light_image=pil_img,dark_image=pil_img,size=pil_img.size)
                self.doc_preview_image=ctk_img
                self.doc_preview_label.configure(text='',image=ctk_img)
            if self.doc_preview_page_label and self.doc_preview_page_label.winfo_exists():
                self.doc_preview_page_label.configure(text=f'Page {clamped_page} / {total}')
        self.job('Rendering A4 preview…',lambda:services.render_document_preview(text,category,index,photo,page,max_size=(290,390)),done)

    def enlarge_doc_preview(self):
        text=self.current_text()
        if not text: return messagebox.showinfo('Preview','Create a draft or type in the editor first to enlarge the A4 preview.',parent=self)
        if self.busy: return
        category=self.doc_type.get(); index=self.selected_template_index(); photo=self.cv_photo if category==CATEGORIES[0] else None
        modal_state={'page':self.doc_preview_page,'total':1,'img':None}
        try:
            pil_img,clamped,total=services.render_document_preview(text,category,index,photo,modal_state['page'],max_size=(560,780))
        except Exception as exc:
            return messagebox.showerror('Preview unavailable',str(exc),parent=self)
        modal_state['page']=clamped; modal_state['total']=total
        win=ctk.CTkToplevel(self)
        win.title(f'{category}  •  {self.template.get()} (Large A4 preview)')
        win.configure(fg_color=BG)
        win_w=max(620,pil_img.width+80); win_h=min(880,max(640,pil_img.height+130))
        win.geometry(f'{win_w}x{win_h}')
        win.transient(self)
        head=ctk.CTkFrame(win,fg_color='transparent'); head.pack(fill='x',padx=22,pady=(14,6))
        label(head,f'{category}  •  {self.template.get()}',size=15,bold=True).pack(side='left')
        page_lbl=label(head,f'Page {clamped} / {total}',size=12,bold=True,color=LIME); page_lbl.pack(side='right')
        scroll=ctk.CTkScrollableFrame(win,fg_color='#151D2C',corner_radius=12)
        scroll.pack(fill='both',expand=True,padx=20,pady=6)
        modal_state['img']=ctk.CTkImage(light_image=pil_img,dark_image=pil_img,size=pil_img.size)
        big_lbl=ctk.CTkLabel(scroll,text='',image=modal_state['img'])
        big_lbl.pack(pady=10)
        foot=ctk.CTkFrame(win,fg_color='transparent'); foot.pack(fill='x',padx=22,pady=(6,14))
        def turn_big(delta):
            nxt=max(1,min(modal_state['total'],modal_state['page']+delta))
            if nxt==modal_state['page']: return
            im2,cl2,tot2=services.render_document_preview(text,category,index,photo,nxt,max_size=(560,780))
            modal_state['page']=cl2; modal_state['total']=tot2
            modal_state['img']=ctk.CTkImage(light_image=im2,dark_image=im2,size=im2.size)
            big_lbl.configure(image=modal_state['img']); page_lbl.configure(text=f'Page {cl2} / {tot2}')
        button(foot,'‹ Previous page',lambda:turn_big(-1),secondary=True).pack(side='left',padx=(0,BTN_PADX))
        button(foot,'Next page ›',lambda:turn_big(1),secondary=True).pack(side='left')
        button(foot,'✕ Close',win.destroy,ghost=True).pack(side='right')
        win.grab_set(); win.focus_set()

    def change_doc_type(self,kind):
        choices=CV_NAMES if kind==CATEGORIES[0] else NAMES
        if self.template.get() not in choices: self.template.set(choices[0])
        self.template_menu.configure(values=choices)
        self.template_caption.configure(text='Template • 13 CV designs' if kind==CATEGORIES[0] else 'Template • 10 options')
        self.doc_preview_page=1
        self.layout_career_fields()
        if self.current_text(): self.refresh_doc_preview()
    def selected_template_index(self):
        choices=CV_NAMES if self.doc_type.get()==CATEGORIES[0] else NAMES
        val=self.template.get()
        return choices.index(val) if val in choices else 0
    def open_cv_gallery(self):
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent)); path=root/'cv-design-gallery.html'
        try:
            if not path.exists(): raise FileNotFoundError('The CV design gallery is missing from this installation.')
            if not webbrowser.open(path.as_uri()): raise RuntimeError('Could not open your browser.')
        except Exception as exc: messagebox.showerror('Could not open CV gallery',str(exc),parent=self)
    def choose_cv_photo(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('Image files','*.png *.jpg *.jpeg *.bmp *.tif *.tiff')])
        if not path: return
        try:
            with services.open_safe_image(path) as image: image.verify()
            self.cv_photo=path; self.cv_photo_label.configure(text='CV photo: '+Path(path).name); self.status.set('CV photo selected • shown in CV preview and PDF')
            if self.current_text() and self.doc_type.get()==CATEGORIES[0]: self.refresh_doc_preview()
        except Exception as exc: messagebox.showerror('Could not open photo',str(exc),parent=self)
    def remove_cv_photo(self):
        self.cv_photo=None; self.cv_photo_label.configure(text='CV photo: none')
        if self.current_text() and self.doc_type.get()==CATEGORIES[0]: self.refresh_doc_preview()
    def sync_long_fields(self):
        for key,widget in self.long_fields.items():
            if widget.winfo_exists(): self.values[key].set(widget.get('1.0','end-1c'))
    def generate(self):
        self.sync_long_fields()
        if self.doc_editor.get('1.0','end-1c').strip() and not messagebox.askyesno('Replace draft?','This will replace your current editor text. Continue?',parent=self): return
        content=make_document(self.doc_type.get(),self.selected_template_index(),{k:v.get() for k,v in self.values.items()}); self.doc_editor.delete('1.0','end'); self.doc_editor.insert('1.0',content); self.doc_text=content; self.doc_preview_page=1; self.status.set('Draft created  •  Review and edit before saving')
        self.refresh_doc_preview()
    def current_text(self): return self.doc_editor.get('1.0','end-1c').strip() if self.doc_editor else self.doc_text.strip()
    def export_pdf(self):
        text=self.current_text()
        if not text: return messagebox.showinfo('Create a draft','Create or enter a document first.',parent=self)
        out=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF document','*.pdf')],initialfile='career-document.pdf')
        if out:
            category=self.doc_type.get(); index=self.selected_template_index(); photo=self.cv_photo if category==CATEGORIES[0] else None; self.job('Saving document…',lambda:services.export_document_pdf(text,out,category,index,photo),lambda _:messagebox.showinfo('All done',f'PDF saved:\n{out}',parent=self))
    def add_document_to_merge(self):
        text=self.current_text()
        if not text: return messagebox.showinfo('Create a draft','Create or enter a document first.',parent=self)
        category=self.doc_type.get(); index=self.selected_template_index(); photo=self.cv_photo if category==CATEGORIES[0] else None
        temp_path=str(Path(self.session_temp.name)/('CV-' + uuid.uuid4().hex[:10] + '.pdf'))
        def complete(_):
            self.pdf_files.append(temp_path)
            if messagebox.askyesno('Added to PDF merge','A temporary PDF copy was added. Open PDF tools to add other PDFs and merge them? The copy is removed when you clear customer data or close the app.',parent=self): self.show('PDF tools')
        self.job('Adding document to PDF merge…',lambda:services.export_document_pdf(text,temp_path,category,index,photo),complete)
    def export_txt(self):
        text=self.current_text()
        if not text: return messagebox.showinfo('Create a draft','Create or enter a document first.',parent=self)
        out=filedialog.asksaveasfilename(parent=self,defaultextension='.txt',filetypes=[('Text file','*.txt')],initialfile='career-document.txt')
        if out: self.job('Saving document…',lambda:services.export_text(text,out),lambda _:messagebox.showinfo('All done',f'Text file saved:\n{out}',parent=self))
    def settings_screen(self):
        self.heading(self.body,'AI settings','Optional writing help — supports Google Gemini (AI Studio), OpenRouter, OpenAI, Groq, DeepSeek & compatible APIs.')
        box=card(self.body); box.pack(fill='x')
        label(box,'Connect an AI provider',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,5))
        label(box,'Paste a Google Gemini key (AIza... from aistudio.google.com/api-keys), OpenRouter (sk-or-v1-...), OpenAI (sk-...), Groq (gsk_...), or DeepSeek key. Auto-detect routes AIza... keys to Gemini, sk-or-... keys to OpenRouter, and gsk_... keys to Groq with a compatible default model.',size=12,color=MUTED,justify='left',wraplength=740).pack(anchor='w',padx=20,pady=(0,12))

        gem_card=ctk.CTkFrame(box,fg_color='#141C2B',corner_radius=12,border_width=1,border_color=BORDER)
        gem_card.pack(fill='x',padx=20,pady=(0,14))
        label(gem_card,'GOOGLE AI STUDIO — GET A GEMINI API KEY (FREE TIER AVAILABLE)',size=10,bold=True,color=LIME).pack(anchor='w',padx=14,pady=(10,3))
        gem_row=ctk.CTkFrame(gem_card,fg_color='transparent'); gem_row.pack(fill='x',padx=14,pady=(0,10))
        gem_url_entry=entry(gem_row); gem_url_entry.insert(0,services.GOOGLE_AI_STUDIO_KEYS_URL); gem_url_entry.configure(state='readonly'); gem_url_entry.pack(side='left',fill='x',expand=True,padx=(0,8))
        def open_gemini_keys():
            try:
                services.open_external_url(services.GOOGLE_AI_STUDIO_KEYS_URL)
                self.status.set('Opened Google AI Studio API keys page in your browser')
            except ValueError as exc:
                messagebox.showerror('Could not open link',str(exc),parent=self)
        def copy_gemini_keys():
            self.clipboard_clear(); self.clipboard_append(services.GOOGLE_AI_STUDIO_KEYS_URL); self.update_idletasks()
            self.status.set('Copied Google AI Studio link to clipboard')
            messagebox.showinfo('Copied',f'Copied link:\n{services.GOOGLE_AI_STUDIO_KEYS_URL}',parent=self)
        button(gem_row,'↗ Get Gemini API key',open_gemini_keys,fg_color=LIME,hover_color='#A9EB65',text_color='#142018').pack(side='left',padx=(0,BTN_PADX))
        button(gem_row,'⧉ Copy link',copy_gemini_keys,ghost=True).pack(side='left')

        label(box,'Provider',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        custom_wrap=ctk.CTkFrame(box,fg_color='transparent')
        known_defaults=set(services.AI_DEFAULT_MODELS.values())|{'gpt-4o-mini','openai/gpt-4o-mini','openrouter/auto'}
        def on_provider_change(_choice=None):
            choice=self.ai_provider.get()
            if choice=='Custom OpenAI-compatible URL':
                custom_wrap.pack(fill='x',padx=20,pady=(0,12),after=prov_menu)
            else:
                custom_wrap.pack_forget()
            mapped_ep=services.AI_PROVIDERS.get(choice,'')
            if mapped_ep in services.AI_DEFAULT_MODELS:
                cur_m=self.model.get().strip()
                if not cur_m or cur_m in known_defaults:
                    self.model.set(services.AI_DEFAULT_MODELS[mapped_ep])
        prov_menu=ctk.CTkOptionMenu(box,values=list(services.AI_PROVIDERS.keys()),variable=self.ai_provider,command=on_provider_change,fg_color=GREEN,button_color=GREEN_HOVER,height=36,width=330)
        prov_menu.pack(anchor='w',padx=20,pady=(0,12))
        label(custom_wrap,'Custom Chat Completions URL (https://...)',size=11,bold=True,color=MUTED).pack(anchor='w',pady=(0,4))
        ctk.CTkEntry(custom_wrap,textvariable=self.ai_custom_url,placeholder_text='https://openrouter.ai/api/v1/chat/completions',height=38,border_color=BORDER,fg_color='#121A29').pack(fill='x')
        on_provider_change()

        label(box,'API key • kept in memory only for this session (cleared when staff locks)',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        ctk.CTkEntry(box,textvariable=self.key,show='•',height=38,border_color=BORDER,fg_color='#121A29').pack(fill='x',padx=20,pady=(0,12))

        label(box,'Model (e.g. gemini-2.5-flash, gemini-2.5-pro, gpt-4o-mini, openrouter/auto, deepseek-chat)',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4))
        ctk.CTkEntry(box,textvariable=self.model,height=38,border_color=BORDER,fg_color='#121A29').pack(fill='x',padx=20,pady=(0,12))

        def test_ai():
            key=self.key.get().strip(); model=self.model.get().strip(); prov=self.ai_provider.get(); curl=self.ai_custom_url.get().strip()
            if not key: return messagebox.showinfo('Enter API key','Paste your API key above first.',parent=self)
            def run_check():
                return services.call_ai_chat(key,model,'Reply with the single word CONNECTED.',provider_choice=prov,custom_url=curl,system_prompt='Reply only with the single word CONNECTED.',max_tokens=16,timeout=30)
            def done(res):
                reply,prov_label,resolved_model=res
                self.status.set(f'AI connection verified • {prov_label} ({resolved_model})')
                messagebox.showinfo('AI connection working',f'Connected to {prov_label}!\n\nModel: {resolved_model}\nTest reply: {reply[:80]}',parent=self)
            self.job('Testing AI connection…',run_check,done)

        def clear_key():
            self.key.set(''); self.ai_generation+=1; self.status.set('Session AI key cleared')
        btn_row=ctk.CTkFrame(box,fg_color='transparent'); btn_row.pack(fill='x',padx=20,pady=(0,15))
        button(btn_row,'⚡ Test AI connection',test_ai,fg_color=LIME,hover_color='#A9EB65',text_color='#142018',width=195).pack(side='left',padx=(0,10))
        button(btn_row,'Clear key now',clear_key,secondary=True,width=130).pack(side='left')

        note=ctk.CTkFrame(box,fg_color='#382F27',corner_radius=10); note.pack(fill='x',padx=20,pady=(0,20)); label(note,'Privacy & data minimization',size=12,bold=True,color='#FFDBA2').pack(anchor='w',padx=14,pady=(11,3)); label(note,'AI sends the entire current draft to your selected AI provider after confirmation. Get the customer’s explicit consent, avoid sending unnecessary ID numbers/private identifiers when polishing wording, and never leave your API key unlocked on a shared computer.',size=12,color='#F1D1A5',justify='left',wraplength=700).pack(anchor='w',padx=14,pady=(0,12))
    def improve(self):
        if not self.require_staff(): return
        text=self.current_text(); key=self.key.get().strip(); model=self.model.get().strip(); prov=self.ai_provider.get(); curl=self.ai_custom_url.get().strip()
        if not text: return messagebox.showinfo('Create a draft','Create or enter a document first.',parent=self)
        if not key or not model: return messagebox.showinfo('Set up AI','Add an API key and model in AI settings first.',parent=self)
        try:
            _,prov_label=services.detect_ai_provider(key,prov,curl)
        except ValueError as exc:
            return messagebox.showerror('AI settings',str(exc),parent=self)
        if not messagebox.askyesno('Customer consent & privacy',f'Send the complete draft ({len(text):,} characters) to {prov_label} ({model})?\n\n• Get the customer’s explicit permission first.\n• Tip: remove ID numbers or sensitive personal details you do not want transmitted.\n• Standard API charges may apply.\n\nSend draft now?',parent=self): return
        req_gen=self.ai_generation
        def request():
            reply,used_prov,used_model=services.call_ai_chat(key,model,text,provider_choice=prov,custom_url=curl,timeout=60)
            return reply,used_prov,used_model
        def complete(res):
            if self.ai_generation!=req_gen:
                self.status.set('Discarded AI reply from a locked or cleared session')
                return
            result,used_prov,used_model=res
            current=self.current_text()
            if current!=text and not messagebox.askyesno('Draft changed','The draft changed while the request was running. Replace it with the AI result?',parent=self): return
            self.doc_text=result
            if self.doc_editor and self.doc_editor.winfo_exists(): self.doc_editor.delete('1.0','end'); self.doc_editor.insert('1.0',result)
            self.refresh_doc_preview()
            self.status.set(f'Draft improved via {used_prov} ({used_model})')
            messagebox.showinfo('Draft improved',f'Improved via {used_prov} ({used_model}).\nReview the AI result and check all facts before saving.',parent=self)
        self.job(f'Improving draft with {prov_label}…',request,complete)

if __name__=='__main__':
    if license_ui.ensure_computer_licensed() is not None:
        CafeDesk().mainloop()

