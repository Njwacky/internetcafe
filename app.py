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

from templates import NAMES, CV_NAMES, CATEGORIES, LABELS, make_document
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

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('green')
BG='#101522'; WHITE='#1B2333'; INK='#F2F4FA'; MUTED='#A2ACC5'; GREEN='#445A86'; GREEN_HOVER='#536B9D'; LIME='#B9FF70'; BORDER='#34415A'; SOFT='#303C55'; RED='#E89886'
FONT='Segoe UI'

def label(parent,text,size=13,bold=False,color=INK,**kw):
    return ctk.CTkLabel(parent,text=text,text_color=color,font=ctk.CTkFont(family=FONT,size=size,weight='bold' if bold else 'normal'),**kw)
def button(parent,text,cmd,secondary=False,**kw):
    opts=dict(text=text,command=cmd,corner_radius=10,height=38,font=ctk.CTkFont(family=FONT,size=13,weight='bold'),fg_color=SOFT if secondary else GREEN,hover_color='#40506D' if secondary else GREEN_HOVER,text_color='#E1E8F6' if secondary else 'white')
    opts.update(kw); return ctk.CTkButton(parent,**opts)
def card(parent,**kw): return ctk.CTkFrame(parent,fg_color=WHITE,corner_radius=16,border_width=1,border_color=BORDER,**kw)
def entry(parent,placeholder=''): return ctk.CTkEntry(parent,placeholder_text=placeholder,height=38,corner_radius=9,border_color=BORDER,fg_color='#121A29',text_color=INK)

class CafeDesk(ctk.CTk):
    def __init__(self):
        super().__init__(); self.title('Café Desk'); self.geometry('1250x800'); self.minsize(950,680); self.configure(fg_color=BG)
        self.events=queue.Queue(); self.busy=False; self.idle=privacy.IdleLock(300); self.recovery_enabled=False; self.session_temp=tempfile.TemporaryDirectory(prefix='cafedesk-'); self.protocol('WM_DELETE_WINDOW',self.close_app); self.cv_photo=None; self.cv_thumbnail=None; self.sheet={'items':[],'preset':'4 per page','page':0,'selected':None,'mode':'Fit whole image','brightness':100,'gray':False}; self.staff_unlocked=False; self.failed_attempts=0; self.lockout_until=0; self.receipt_items=[]; self.receipt_currency=None; self.receipt_token=uuid.uuid4().hex; self.receipt_timestamp=None; self.receipt_business=None; self.receipt_recorded=False; self.ocr_text=''; self.ocr_editor=None; self.pdf_files=[]; self.image_files=[]; self.split_source=''; self.split_preview_fingerprint=None; self.scanner_devices=[]; self.scanner_device_id=None; self.scanned_pages=[]; self.doc_type=ctk.StringVar(value=CATEGORIES[0]); self.template=ctk.StringVar(value=NAMES[0]); self.model=ctk.StringVar(value='gpt-4o-mini'); self.key=ctk.StringVar(); self.values={k:ctk.StringVar() for k in LABELS}; self.status=ctk.StringVar(value='Ready  •  Local document processing')
        preferences=settings_store.load_settings(); self.business_name=ctk.StringVar(value=preferences['business_name']); self.business_icon=preferences['business_icon']; self.icon_images=self.load_icon_images(); self.clock=ctk.StringVar(); self.body=None; self.doc_editor=None; self.long_fields={}; self.doc_text=''; self.nav_buttons={}; self.current=''; self.create_shell(); self.title(self.business_name.get()); self.show('Home'); self.update_clock(); self.after(100,self.poll_events); self.after(5000,self.check_idle); self.after(60000,self.recovery_tick)
        for sequence in ('<KeyPress>','<ButtonPress>','<MouseWheel>','<Motion>'): self.bind_all(sequence,self.note_activity,add='+')
    def note_activity(self,event=None): self.idle.touch()
    def check_idle(self):
        if self.staff_unlocked and self.idle.expired(): self.lock_staff(); self.status.set('Staff access locked after five minutes of inactivity')
        self.after(5000,self.check_idle)
    def close_app(self):
        if self.busy: return messagebox.showinfo('Please wait','A document is still being processed. Wait for it to finish before closing.',parent=self)
        if self.current=='Career documents': self.sync_long_fields()
        has_work=bool(self.current_text().strip() or any(v.get().strip() for v in self.values.values()) or self.cv_photo or self.sheet['items'] or self.receipt_items or self.scanned_pages)
        if has_work and not messagebox.askyesno('Close Café Desk?','Current workspace data, including unsaved scans, will be closed. Save any work you need first. If recovery is enabled, the latest copy may not include recent changes. Close anyway?',parent=self): return
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
        try:
            with Image.open(settings_store.logo_path()) as image: images['custom']=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(40,40))
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
                with Image.open(path) as original:
                    image=original.convert('RGBA'); image.thumbnail((256,256)); sample=ctk.CTkImage(light_image=image.copy(),dark_image=image.copy(),size=(40,40))
                selected_logo['path']=path; selected_logo['sample']=sample; icon_choice.set(settings_store.ICONS['custom']); icon_preview.configure(image=sample)
            except Exception as exc: messagebox.showerror('Invalid logo',str(exc),parent=self)
        button(icon_row,'Upload logo',choose_logo,secondary=True,width=125,height=36).pack(side='left',padx=10)
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
        button(box,'Save business settings',save,width=180).pack(anchor='w',padx=20,pady=(0,15))
        button(box,'Change staff PIN',self.change_pin,secondary=True,width=150).pack(anchor='w',padx=20,pady=(0,15))
        note=ctk.CTkFrame(box,fg_color='#273248',corner_radius=10); note.pack(fill='x',padx=20,pady=(0,20))
        label(note,'Shared workspace',size=12,bold=True,color='#B9FF70').pack(anchor='w',padx=14,pady=(11,3))
        label(note,'Business settings are staff PIN-protected. The name and icon are stored for this computer user, not on the USB.',size=12,color='#B9C7DC',justify='left',wraplength=680).pack(anchor='w',padx=14,pady=(0,12))
    def require_staff(self):
        if self.staff_unlocked: return True
        if time.monotonic()<self.lockout_until:
            messagebox.showwarning('Try again later','Too many incorrect attempts. Wait one minute.',parent=self); return False
        try:
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
                    self.failed_attempts+=1
                    if self.failed_attempts>=5: self.lockout_until=time.monotonic()+60; self.failed_attempts=0
                    messagebox.showerror('Staff access','Incorrect PIN. After 5 failed attempts, wait one minute.',parent=self); return False
            self.staff_unlocked=True; self.idle.touch(); self.failed_attempts=0; self.status.set('Staff settings unlocked • lock before leaving the computer'); return True
        except (ValueError,OSError) as exc: messagebox.showerror('Staff access',str(exc),parent=self); return False
    def change_pin(self):
        old=simpledialog.askstring('Change staff PIN','Enter current PIN:',show='*',parent=self)
        if old is None: return
        if not settings_store.verify_pin(old): return messagebox.showerror('Staff access','Incorrect current PIN.',parent=self)
        new=simpledialog.askstring('Change staff PIN','Enter a new 6–12 digit PIN:',show='*',parent=self)
        if new is None: return
        confirm=simpledialog.askstring('Confirm staff PIN','Enter the new PIN again:',show='*',parent=self)
        if confirm is None: return
        try:
            if new!=confirm: raise ValueError('PINs do not match.')
            settings_store.set_pin(new); messagebox.showinfo('PIN updated','Staff PIN changed.',parent=self)
        except (ValueError,OSError) as exc: messagebox.showerror('Could not update PIN',str(exc),parent=self)
    def lock_staff(self):
        self.staff_unlocked=False; self.key.set('')
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
    def open_project(self,path=None):
        if self.busy: return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
        path=path or filedialog.askopenfilename(parent=self,filetypes=[('Café Desk project','*.cafeproject')])
        if not path:return
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
        self.job('Opening project…',lambda:projects.load(path,session),loaded)
    def recovery_dir(self):return settings_store.settings_path().parent/'recovery'
    def recovery_exists(self):return any((self.recovery_dir()/(name+'.cafeproject')).exists() for name in ('career','sheet'))
    def delete_recovery_copies(self,notify=True):
        failed=[]
        for name in ('career','sheet'):
            try:(self.recovery_dir()/(name+'.cafeproject')).unlink(missing_ok=True)
            except OSError:failed.append(name)
        if failed:messagebox.showwarning('Recovery files remain','Some recovery files could not be removed. Check the recovery folder manually.',parent=self)
        elif notify:messagebox.showinfo('Recovery cleared','Recovery copies were deleted. Saved projects and original files were not deleted.',parent=self)
    def recover_project(self,kind):
        if not self.require_staff():return
        path=self.recovery_dir()/(kind+'.cafeproject')
        if not path.exists():return messagebox.showinfo('No recovery copy','No recovery copy exists for this project type.',parent=self)
        self.open_project(str(path))
    def perform_recovery(self):
        if not self.recovery_enabled or self.busy:return
        career=self._career_snapshot();sheet=self._sheet_snapshot();jobs=[]
        if career['draft'].strip() or any(v.strip() for v in career['fields'].values()) or career['photo']:jobs.append(('career',career))
        if sheet['items']:jobs.append(('sheet',sheet))
        if not jobs:return
        directory=self.recovery_dir()
        def work():
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            for kind,data in jobs:projects.save(kind,data,str(directory/(kind+'.cafeproject')))
        self.job('Saving recovery copy…',work,lambda _:self.status.set('Recovery copy saved locally • not encrypted'))
    def recovery_tick(self):
        self.perform_recovery();self.after(60000,self.recovery_tick)
    def projects_screen(self):
        self.heading(self.body,'Projects & recovery','Save your work to reopen and keep working later.')
        box=card(self.body);box.pack(fill='x',pady=(0,12));label(box,'Save or open a project',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        label(box,'A project includes the document or picture-sheet layout and copies of its photos. It is not encrypted.',size=12,color=MUTED,wraplength=720,justify='left').pack(anchor='w',padx=20,pady=(0,12))
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,18));button(row,'Save career project',lambda:self.save_project('career'),width=170).pack(side='left',padx=(0,8));button(row,'Save picture sheet',lambda:self.save_project('sheet'),secondary=True,width=165).pack(side='left',padx=(0,8));button(row,'Open a project',self.open_project,secondary=True,width=140).pack(side='left')
        box=card(self.body);box.pack(fill='x',pady=(0,12));label(box,'Optional recovery copies',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        label(box,'When enabled, a local copy is saved about every 60 seconds. It includes private text and photos and is not encrypted. Keep the computer and backups secure.',size=12,color=MUTED,wraplength=720,justify='left').pack(anchor='w',padx=20,pady=(0,12))
        toggle=ctk.BooleanVar(value=self.recovery_enabled)
        def change():
            if toggle.get():
                if not messagebox.askyesno('Enable recovery?', 'Recovery saves customer text and photos to this computer about every 60 seconds, including after a crash or normal exit. The copies are not encrypted. Get customer permission and use Next customer when finished. Enable?',parent=self):toggle.set(False);return
                self.recovery_enabled=True;self.after(1200,self.perform_recovery)
            else:self.recovery_enabled=False
        ctk.CTkSwitch(box,text='Save recovery copies during this session',variable=toggle,command=change).pack(anchor='w',padx=20,pady=(0,14))
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,14));button(row,'Recover career',lambda:self.recover_project('career'),secondary=True,width=140).pack(side='left',padx=(0,8));button(row,'Recover picture sheet',lambda:self.recover_project('sheet'),secondary=True,width=170).pack(side='left',padx=(0,8))
        def delete():
            if self.busy:return messagebox.showinfo('Please wait','Wait until the current task finishes.',parent=self)
            if not self.require_staff():return
            if messagebox.askyesno('Delete recovery copies?','Delete the local career and picture-sheet recovery copies? Saved project files are not deleted.',parent=self):self.recovery_enabled=False;toggle.set(False);self.delete_recovery_copies()
        button(box,'Delete recovery copies',delete,secondary=True,width=175).pack(anchor='w',padx=20,pady=(0,18))
    def quick_help(self):
        messages={
            'Home':'Use the menu to choose a tool. Open Help & privacy for a step-by-step guide.',
            'Projects & recovery':'Save a career or picture-sheet project with its photos, or open a saved project. Recovery is optional and stores unencrypted copies locally.',
            'Help & privacy':'Read the quick steps below. Staff can deliberately delete a selected customer file after confirmation.',
            'Price list':'Search the read-only price list. Staff add prices and units in PIN-protected Price settings.',
            'PDF tools':'Add PDFs, arrange them, then merge and save. You can also extract pages or split each page.',
            'Bluetooth files':'On Windows, pair the phone in Bluetooth settings, open the Windows Bluetooth transfer window, and choose Receive files. After saving, select the file to use it in Café Desk.' ,
            'Scan & images':'On Windows, find and select a WIA scanner, then scan each page in the app and save a PDF. You can also open your scanner app or use English OCR if Tesseract is installed.',
            'A4 picture sheets':'Add images, choose a layout, move and resize in Free layout, then save a PDF or project. Print PDFs at actual size.',
            'Career documents':'Choose a type and template, enter details, create and edit a draft, then save a PDF or project. Add to PDF merge to combine it with other files.',
            'Receipts':'Choose services and quantity. Save a receipt PDF; staff must separately confirm Record sale to add it to history.',
            'Sales reports':'Choose a date range to view locally recorded totals, export a CSV or save a selected sale record. Payment and tax are not tracked.',
            'Price settings':'Enter each business service and price on its own line as service | price. Include units in the service name.',
            'Business settings':'Change the business name, icon, logo or staff PIN. Settings are local to this computer user.',
            'AI settings':'Enter your own API key for this session. Confirm before the complete draft is sent to OpenAI.'}
        messagebox.showinfo('Quick help • '+self.current,messages.get(self.current,'Open Help & privacy for the full guide.'),parent=self)
    def help_screen(self):
        self.heading(self.body,'Help & privacy','A quick guide for staff and customers.')
        sections=[('1  Set prices','A staff member opens Price settings, enters each service and price (include units such as per page), and saves. Everyone can view the read-only Price list.'),('2  Create a CV','Open Career documents, choose a template, add details and optionally a photo. Create the draft, edit it, then save a PDF or a project to continue later.'),('3  Merge PDFs','In the CV screen, add the draft to PDF merge, or open PDF tools and add existing PDFs. Arrange them and save the merged file.'),('4  Scan and extract text','Open Scan & images. On Windows, find and select a WIA scanner, scan each page and save a PDF. Alternatively use your scanner app. English OCR requires Tesseract installed separately.'),('5  Print photos','Open A4 picture sheets, add pictures and choose a layout or custom dimensions. Save the PDF, then print from your PDF viewer at actual size.'),('6  Receipts and reports','Add services in Receipts. Saving a PDF does not record a sale. Staff choose Record sale; staff-only Sales reports show recorded totals. Payment and tax are not tracked.'),('7  Finish with a customer','Use Next customer to clear the current workspace, temporary merge files and recovery copies and lock staff. Saved files and originals stay on the computer.')]
        for title,description in sections:
            box=card(self.body);box.pack(fill='x',pady=(0,8));label(box,title,size=14,bold=True).pack(anchor='w',padx=18,pady=(14,4));label(box,description,size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,14))
        box=card(self.body);box.pack(fill='x',pady=(0,12));label(box,'Privacy and file removal',size=15,bold=True).pack(anchor='w',padx=18,pady=(16,4));label(box,'Staff access locks after five minutes without activity. The PIN is basic app-level protection, not encryption. Next customer does not delete saved files. A staff member can deliberately remove a selected file below.',size=12,color=MUTED,wraplength=740,justify='left').pack(anchor='w',padx=18,pady=(0,12))
        button(box,'Delete a selected customer file',self.delete_selected_file,secondary=True,width=210).pack(anchor='w',padx=18,pady=(0,18))
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
        try:p.unlink();messagebox.showinfo('File deleted','The selected file was deleted. Other copies and backups were not removed.',parent=self)
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
        actions=ctk.CTkFrame(box,fg_color='transparent');actions.pack(fill='x',padx=20,pady=(0,18))
        button(actions,'Create a receipt',lambda:self.show('Receipts'),width=145).pack(side='left',padx=(0,8))
        def export():
            try:text=price_list.as_text(self.business_name.get(),currency,all_rows)
            except ValueError as exc:return messagebox.showinfo('Price list',str(exc),parent=self)
            out=filedialog.asksaveasfilename(parent=self,defaultextension='.pdf',filetypes=[('PDF','*.pdf')],initialfile='price-list.pdf')
            if out:self.job('Saving price list…',lambda:services.export_document_pdf(text,out,'Price list',0),lambda _:messagebox.showinfo('Price list saved',f'All services saved to:\n{out}',parent=self))
        button(actions,'Save full list as PDF',export,secondary=True,width=170).pack(side='left',padx=(0,8))
        button(actions,'Staff: edit prices',lambda:self.show('Price settings'),secondary=True,width=145).pack(side='left',padx=(0,8))
        button(actions,'Refresh',lambda:self.show('Price list'),secondary=True,width=90).pack(side='left')
        label(box,'The PDF includes the full list, even when a search filter is active.',size=11,color=MUTED).pack(anchor='w',padx=20,pady=(0,16))
    def price_screen(self):
        self.heading(self.body,'Price settings','Set your own services and prices. The initial list is empty.')
        data=settings_store.load_settings(); box=card(self.body); box.pack(fill='x')
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
        button(box,'Save price list',save,width=150).pack(anchor='w',padx=20,pady=(0,20))
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
        button(row,'Add service',add,width=120).pack(side='left')
        buttons=ctk.CTkFrame(box,fg_color='transparent');buttons.pack(fill='x',padx=20,pady=(0,10))
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
        def new_receipt():
            if self.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=self)
            if self.receipt_items and not self.receipt_recorded and not messagebox.askyesno('Start a new receipt?','The current receipt has not been recorded. Discard it?',parent=self):return
            self.reset_receipt();refresh()
        button(buttons,'Save receipt PDF',save_receipt,width=135).pack(side='left',padx=(0,8));button(buttons,'Record sale',record,width=115).pack(side='left',padx=(0,8));button(buttons,'New receipt',new_receipt,secondary=True,width=115).pack(side='left')
        label(box,'Record sale is staff-only. No payment status, payment method or tax is tracked.',size=11,color=MUTED).pack(anchor='w',padx=20,pady=(0,18));refresh()
    def ocr_screen_part(self):
        box=card(self.body); box.pack(fill='x',pady=(0,14)); label(box,'Recognise text (OCR)',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
        label(box,'Extract English text from an image using a separately installed Tesseract OCR engine. Review the result for errors.',size=12,color=MUTED,wraplength=750,justify='left').pack(anchor='w',padx=20)
        self.ocr_editor=ctk.CTkTextbox(box,height=150,fg_color='#121A29',border_color=BORDER,border_width=1,text_color=INK); self.ocr_editor.pack(fill='x',padx=20,pady=12); self.ocr_editor.insert('1.0',self.ocr_text)
        row=ctk.CTkFrame(box,fg_color='transparent'); row.pack(fill='x',padx=20,pady=(0,18))
        def recognize():
            path=filedialog.askopenfilename(parent=self,filetypes=[('Images','*.png *.jpg *.jpeg *.bmp *.tif *.tiff')])
            if not path: return
            if self.ocr_editor.get('1.0','end-1c').strip() and not messagebox.askyesno('Replace OCR text?','Replace the current recognised text?',parent=self): return
            original=self.ocr_editor.get('1.0','end-1c')
            def done(text):
                current=self.ocr_editor.get('1.0','end-1c') if self.ocr_editor and self.ocr_editor.winfo_exists() else self.ocr_text
                if current!=original and not messagebox.askyesno('Text changed','Text changed while OCR was running. Replace it?',parent=self): return
                self.ocr_text=text
                if self.ocr_editor and self.ocr_editor.winfo_exists(): self.ocr_editor.delete('1.0','end'); self.ocr_editor.insert('1.0',text)
                messagebox.showinfo('OCR complete','Review the recognised text carefully.',parent=self)
            self.job('Recognising text…',lambda:services.run_ocr(path),done)
        def save():
            text=self.ocr_editor.get('1.0','end-1c').strip()
            if not text: return messagebox.showinfo('OCR','Recognise or enter text first.',parent=self)
            path=filedialog.asksaveasfilename(parent=self,defaultextension='.txt',filetypes=[('Text','*.txt')],initialfile='recognised-text.txt')
            if path: self.job('Saving text…',lambda:services.export_text(text,path),lambda _:messagebox.showinfo('Saved',f'Text saved to:\n{path}',parent=self))
        button(row,'Choose image & recognise',recognize,width=205).pack(side='left',padx=(0,8)); button(row,'Save text',save,secondary=True,width=115).pack(side='left')
    def create_shell(self):
        self.grid_columnconfigure(1,weight=1); self.grid_rowconfigure(0,weight=1)
        side=ctk.CTkFrame(self,width=220,corner_radius=0,fg_color='#141B2A'); side.grid(row=0,column=0,sticky='nsew'); side.grid_propagate(False)
        brand=ctk.CTkFrame(side,fg_color='transparent'); brand.pack(fill='x',padx=23,pady=(29,35))
        self.brand_icon=ctk.CTkLabel(brand,text='',image=self.icon_images[self.business_icon]); self.brand_icon.pack(side='left',padx=(0,10))
        column=ctk.CTkFrame(brand,fg_color='transparent'); column.pack(side='left'); self.brand_label=ctk.CTkLabel(column,textvariable=self.business_name,text_color='white',font=ctk.CTkFont(family=FONT,size=17,weight='bold'),wraplength=150,justify='left'); self.brand_label.pack(anchor='w'); label(column,'DOCUMENT STUDIO',size=10,bold=True,color='#99A9C3').pack(anchor='w')
        label(side,'WORKSPACE',size=10,bold=True,color='#8FA1BF').pack(anchor='w',padx=25,pady=(0,9))
        info=ctk.CTkFrame(side,fg_color='#273248',corner_radius=12); info.pack(side='bottom',fill='x',padx=15,pady=12)
        label(info,'PRIVATE BY DEFAULT',size=10,bold=True,color='#B9FF70').pack(anchor='w',padx=13,pady=(12,3)); label(info,'Files stay on this computer\nunless you choose AI.',size=11,color='#B9C7DC',justify='left').pack(anchor='w',padx=13,pady=(0,12))
        nav=ctk.CTkScrollableFrame(side,fg_color='transparent',corner_radius=0);nav.pack(side='top',fill='both',expand=True,padx=(6,0))
        for name,icon in [('Home','⌂'),('Projects & recovery','▣'),('Help & privacy','?'),('Price list','≡'),('PDF tools','▤'),('Bluetooth files','ᛒ'),('Scan & images','▧'),('A4 picture sheets','▦'),('Career documents','✎'),('Receipts','▣'),('Sales reports','▧'),('Price settings','≡'),('Business settings','⚙'),('AI settings','✦')]:
            b=ctk.CTkButton(nav,text=f'{icon}   {name}',height=38,anchor='w',corner_radius=10,fg_color='transparent',hover_color='#303C55',text_color='#C4D0E5',font=ctk.CTkFont(family=FONT,size=13,weight='bold'),command=lambda n=name:self.show(n)); b.pack(fill='x',padx=(5,4),pady=2); self.nav_buttons[name]=b
        main=ctk.CTkFrame(self,fg_color='transparent'); main.grid(row=0,column=1,sticky='nsew'); main.grid_rowconfigure(1,weight=1); main.grid_columnconfigure(0,weight=1)
        bar=ctk.CTkFrame(main,fg_color=BG,height=67); bar.grid(row=0,column=0,sticky='ew',padx=28); bar.grid_propagate(False)
        self.header_title=label(bar,'Home',size=20,bold=True); self.header_title.pack(side='left')
        button(bar,'Next customer',self.clear_customer,secondary=True,width=130,height=34).pack(side='right',pady=15)
        button(bar,'Lock staff',self.lock_staff,secondary=True,width=90,height=34).pack(side='right',padx=(0,8),pady=15)
        button(bar,'? Help',self.quick_help,secondary=True,width=75,height=34).pack(side='right',padx=(0,8),pady=15)
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
        self.doc_editor=None; self.long_fields={}; self.current=name; self.header_title.configure(text=name)
        for key,b in self.nav_buttons.items(): b.configure(fg_color='#344663' if key==name else 'transparent',text_color='white' if key==name else '#C4D0E5')
        for child in self.container.winfo_children(): child.destroy()
        self.body=ctk.CTkScrollableFrame(self.container,fg_color='transparent',corner_radius=0); self.body.pack(fill='both',expand=True)
        {'Home':self.home,'Projects & recovery':self.projects_screen,'Help & privacy':self.help_screen,'Price list':self.price_list_screen,'PDF tools':self.pdf_screen,'Bluetooth files':self.bluetooth_screen,'Scan & images':self.scan_screen,'A4 picture sheets':lambda:PictureSheet(self,self.body,card,label,button),'Career documents':self.career_screen,'Receipts':self.receipt_screen,'Sales reports':lambda:show_reports(self,self.body,card,label,button,entry),'Price settings':self.price_screen,'Business settings':self.business_screen,'AI settings':self.settings_screen}[name]()
    def heading(self,parent,title,subtitle):
        label(parent,title,size=24,bold=True).pack(anchor='w',pady=(2,3)); label(parent,subtitle,size=13,color=MUTED,wraplength=740,justify='left').pack(anchor='w',pady=(0,18))
    def home(self):
        hero=ctk.CTkFrame(self.body,fg_color='#202B43',corner_radius=18); hero.pack(fill='x',pady=(0,20)); hero.grid_columnconfigure(0,weight=1)
        label(hero,'YOUR ALL-IN-ONE WORKSPACE',size=10,bold=True,color='#B9FF70').pack(anchor='w',padx=26,pady=(25,6))
        label(hero,'Big help for life’s little paperwork.',size=27,bold=True,color='white').pack(anchor='w',padx=26)
        label(hero,'Everything you need for documents and your next career move.',size=13,color='#C3CEE1').pack(anchor='w',padx=26,pady=(7,17))
        button(hero,'Create a career document   →',lambda:self.show('Career documents'),fg_color=LIME,hover_color='#A9EB65',text_color='#142018',width=240).pack(anchor='w',padx=26,pady=(0,25))
        button(self.body,'New here? Open the quick guide  →',lambda:self.show('Help & privacy'),secondary=True,width=270).pack(anchor='w',pady=(0,17))
        if self.recovery_exists(): label(self.body,'A recovery copy is available. Staff can open it from Projects & recovery.',size=11,color='#B9FF70').pack(anchor='w',pady=(0,10))
        label(self.body,'What can we help with?',size=19,bold=True).pack(anchor='w',pady=(0,11))
        grid=ctk.CTkFrame(self.body,fg_color='transparent'); grid.pack(fill='x'); grid.grid_columnconfigure((0,1),weight=1)
        for i,(title,desc,icon,dest) in enumerate([('Price list','See the café’s current services and prices.','≡','Price list'),('Merge & split PDFs','Bring files together or extract pages.','▤','PDF tools'),('Receive via Bluetooth','Open Windows Bluetooth file transfer.','ᛒ','Bluetooth files'),('Scan & convert','Import images and create a PDF.','▧','Scan & images'),('Career documents','Make a CV, cover letter or introduction.','✎','Career documents'),('AI writing help','Improve your draft with your own API key.','✦','AI settings')]):
            box=card(grid); box.grid(row=i//2,column=i%2,sticky='nsew',padx=(0,10) if i%2==0 else (0,0),pady=(0,10)); label(box,icon,size=24,bold=True,color='#B9FF70').pack(anchor='w',padx=18,pady=(14,2)); label(box,title,size=15,bold=True).pack(anchor='w',padx=18); label(box,desc,size=12,color=MUTED).pack(anchor='w',padx=18,pady=(4,10)); button(box,'Open  →',lambda n=dest:self.show(n),secondary=True,width=90,height=32).pack(anchor='w',padx=18,pady=(0,14))
    def job(self,description,fn,success):
        if self.busy: return messagebox.showinfo('Please wait','Another task is still running.',parent=self)
        self.busy=True; self.status.set(description)
        def work():
            try: self.events.put(('ok',success,fn()))
            except Exception as exc: self.events.put(('error',None,str(exc)))
        threading.Thread(target=work,daemon=True).start()
    def poll_events(self):
        try:
            while True:
                kind,callback,result=self.events.get_nowait(); self.busy=False
                if kind=='error': self.status.set('Task could not be completed'); messagebox.showerror('Café Desk',result,parent=self)
                else:
                    self.status.set('Ready  •  Task completed'); callback(result)
        except queue.Empty: pass
        self.after(100,self.poll_events)
    def clear_customer(self):
        if self.busy: return messagebox.showinfo('Please wait','Please wait until the current task finishes.',parent=self)
        if not messagebox.askyesno('Next customer','Clear the current customer’s draft, project photos, picture sheet, OCR text, scanned pages, receipt, temporary merge copies, recovery copies and AI key? Staff will be locked. Saved PDFs, saved projects and original images will NOT be deleted.',parent=self): return
        for var in self.values.values(): var.set('')
        for widget in self.long_fields.values():
            if widget.winfo_exists(): widget.delete('1.0','end')
        self.recovery_enabled=False; self.delete_recovery_copies(notify=False)
        self.key.set(''); self.staff_unlocked=False; self.receipt_items=[]; self.receipt_currency=None; self.receipt_token=uuid.uuid4().hex; self.receipt_timestamp=None; self.receipt_business=None; self.receipt_recorded=False; self.ocr_text=''; self.cv_photo=None; self.cv_thumbnail=None; self.sheet={'items':[],'preset':'4 per page','page':0,'selected':None,'mode':'Fit whole image','brightness':100,'gray':False}
        if self.ocr_editor and self.ocr_editor.winfo_exists(): self.ocr_editor.delete('1.0','end')
        self.doc_text=''; self.pdf_files=[]; self.image_files=[]; self.scanned_pages=[]; self.split_source=''; self.split_preview_fingerprint=None; self.remove_session_files(); self.session_temp=tempfile.TemporaryDirectory(prefix='cafedesk-')
        if self.doc_editor and self.doc_editor.winfo_exists(): self.doc_editor.delete('1.0','end')
        self.show('Home'); self.status.set('Current workspace cleared  •  Saved files were not deleted')
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
            if paths:
                (self.pdf_files if kind=='pdf' else self.image_files).extend(paths)
                self.show('PDF tools' if kind=='pdf' else 'Scan & images')
        button(row,'Add received PDF',lambda:add_received('pdf'),secondary=True,width=160).pack(side='left',padx=(0,8))
        button(row,'Add received images',lambda:add_received('image'),secondary=True,width=170).pack(side='left')
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
            files.extend(filedialog.askopenfilenames(parent=self,filetypes=ft)); refresh()
        def remove():
            sel=lst.curselection()
            if sel: del files[sel[0]]; refresh()
        def move(d):
            sel=lst.curselection()
            if sel:
                i=sel[0]; j=i+d
                if 0<=j<len(files): files[i],files[j]=files[j],files[i]; refresh(j)
        actions=ctk.CTkFrame(box,fg_color='transparent'); actions.pack(fill='x',padx=20,pady=(0,18))
        for title,cmd in [('＋ Add files',add),('Remove',remove),('↑',lambda:move(-1)),('↓',lambda:move(1))]: button(actions,title,cmd,secondary=True,width=88 if len(title)>2 else 40,height=34).pack(side='left',padx=(0,7))
        button(actions,'Merge & save' if kind=='pdf' else 'Convert to PDF',lambda:self.save_files(kind),width=145,height=34).pack(side='right')
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
            if path:self.split_source=path;self.split_preview_fingerprint=None;pages.delete(0,'end');file_label.configure(text=Path(path).name)
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
    def extract(self,spec):
        src=self.split_source
        if not src: return messagebox.showinfo('Select a PDF','Choose a PDF first.',parent=self)
        if not spec.strip(): return messagebox.showinfo('Page selection','Enter page numbers such as 1-3, 5.',parent=self)
        if self.split_preview_fingerprint is not None:
            try:
                import pdf_preview
                if pdf_preview.fingerprint(src)!=self.split_preview_fingerprint: return messagebox.showerror('PDF changed','The PDF changed since it was previewed. Choose it again and reselect the pages.',parent=self)
            except OSError as exc:return messagebox.showerror('Cannot read PDF',str(exc),parent=self)
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
        self.heading(self.body,'Career documents','Thoughtful templates for your next opportunity. Edit every draft before saving.')
        selection=card(self.body); selection.pack(fill='x',pady=(0,12)); label(selection,'1   Choose your starting point',size=16,bold=True).pack(anchor='w',padx=20,pady=(16,10))
        row=ctk.CTkFrame(selection,fg_color='transparent'); row.pack(fill='x',padx=20,pady=(0,17)); row.grid_columnconfigure((0,1),weight=1)
        typecell=ctk.CTkFrame(row,fg_color='transparent');typecell.grid(row=0,column=0,sticky='ew',padx=(0,10));label(typecell,'Document type',size=11,bold=True,color=MUTED).pack(anchor='w',pady=(0,5));ctk.CTkOptionMenu(typecell,values=CATEGORIES,variable=self.doc_type,command=self.change_doc_type,fg_color=GREEN,button_color=GREEN_HOVER,button_hover_color='#374F77',height=36).pack(fill='x')
        tempcell=ctk.CTkFrame(row,fg_color='transparent');tempcell.grid(row=0,column=1,sticky='ew');self.template_caption=label(tempcell,'Template • 13 CV designs' if self.doc_type.get()==CATEGORIES[0] else 'Template • 10 options',size=11,bold=True,color=MUTED);self.template_caption.pack(anchor='w',pady=(0,5));self.template_menu=ctk.CTkOptionMenu(tempcell,values=CV_NAMES if self.doc_type.get()==CATEGORIES[0] else NAMES,variable=self.template,fg_color=GREEN,button_color=GREEN_HOVER,button_hover_color='#374F77',height=36);self.template_menu.pack(fill='x')
        button(selection,'View 13 CV designs',self.open_cv_gallery,secondary=True,width=170,height=34).pack(anchor='w',padx=20,pady=(0,15))
        form=card(self.body); form.pack(fill='x',pady=(0,12)); label(form,'2   Add your details',size=16,bold=True).pack(anchor='w',padx=20,pady=(16,0)); label(form,'Add as many lines as you need for skills, experience and education. Blank fields use placeholders.',size=12,color=MUTED).pack(anchor='w',padx=20,pady=(3,5))
        grid=ctk.CTkFrame(form,fg_color='transparent'); grid.pack(fill='x',padx=20,pady=(0,9)); grid.grid_columnconfigure((0,1),weight=1)
        keys=list(LABELS)
        for i,key in enumerate(keys):
            cell=ctk.CTkFrame(grid,fg_color='transparent'); cell.grid(row=i//2,column=i%2,sticky='nsew',padx=(0,10) if i%2==0 else (0,0),pady=(8,0)); label(cell,LABELS[key],size=11,bold=True,color=MUTED).pack(anchor='w',pady=(0,4))
            if key in ('skills','experience','education'):
                text=ctk.CTkTextbox(cell,height=95,corner_radius=9,border_color=BORDER,border_width=1,fg_color='#121A29',text_color=INK,wrap='word',font=ctk.CTkFont(family=FONT,size=12)); text.pack(fill='x'); text.insert('1.0',self.values[key].get()); self.long_fields[key]=text
            else: ctk.CTkEntry(cell,textvariable=self.values[key],height=37,corner_radius=9,border_color=BORDER,fg_color='#121A29',text_color=INK).pack(fill='x')
        photo_row=ctk.CTkFrame(form,fg_color='transparent'); photo_row.pack(fill='x',padx=20,pady=(8,0))
        self.cv_photo_label=label(photo_row,'CV photo: '+(Path(self.cv_photo).name if self.cv_photo else 'none selected'),size=11,color=MUTED); self.cv_photo_label.pack(side='left',padx=(0,8))
        button(photo_row,'Choose CV photo',self.choose_cv_photo,secondary=True,width=140,height=32).pack(side='left',padx=(0,8))
        button(photo_row,'Remove photo',self.remove_cv_photo,secondary=True,width=120,height=32).pack(side='left')
        label(form,'Photo is optional and appears only in CV PDF exports; the text editor and text files do not show it.',size=11,color=MUTED,wraplength=700,justify='left').pack(anchor='w',padx=20,pady=(5,0))
        button(form,'Create draft   →',self.generate,width=155).pack(anchor='w',padx=20,pady=(3,18))
        editor=card(self.body); editor.pack(fill='both',expand=True,pady=(0,14)); label(editor,'3   Review and save',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,2)); label(editor,'The draft is editable. Always check the details before sharing.',size=12,color=MUTED).pack(anchor='w',padx=20,pady=(0,9))
        self.doc_editor=ctk.CTkTextbox(editor,height=250,corner_radius=10,fg_color='#121A29',border_color=BORDER,border_width=1,text_color=INK,font=ctk.CTkFont(family=FONT,size=13),wrap='word'); self.doc_editor.pack(fill='both',expand=True,padx=20,pady=(0,10)); self.doc_editor.insert('1.0',self.doc_text)
        row=ctk.CTkFrame(editor,fg_color='transparent'); row.pack(fill='x',padx=20,pady=(0,18)); button(row,'Save PDF',self.export_pdf,width=105).pack(side='left',padx=(0,8)); button(row,'Add to PDF merge',self.add_document_to_merge,secondary=True,width=145).pack(side='left',padx=(0,8)); button(row,'Save text',self.export_txt,secondary=True,width=105).pack(side='left',padx=(0,8)); button(row,'✦  Improve with AI',self.improve,secondary=True,width=145).pack(side='left')
        label(editor,'Merge copies are temporary until you save the merged PDF. Next customer removes them.',size=11,color=MUTED,wraplength=700,justify='left').pack(anchor='w',padx=20,pady=(0,10))
        project_row=ctk.CTkFrame(editor,fg_color='transparent');project_row.pack(fill='x',padx=20,pady=(0,18));button(project_row,'Save project',lambda:self.save_project('career'),secondary=True,width=130).pack(side='left',padx=(0,8));button(project_row,'Open project',self.open_project,secondary=True,width=130).pack(side='left')
    def change_doc_type(self,kind):
        choices=CV_NAMES if kind==CATEGORIES[0] else NAMES; self.template.set(choices[0]); self.template_menu.configure(values=choices); self.template_caption.configure(text='Template • 13 CV designs' if kind==CATEGORIES[0] else 'Template • 10 options')
    def selected_template_index(self):
        return (CV_NAMES if self.doc_type.get()==CATEGORIES[0] else NAMES).index(self.template.get())
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
            with Image.open(path) as image: image.verify()
            self.cv_photo=path; self.cv_photo_label.configure(text='CV photo: '+Path(path).name); self.status.set('CV photo selected • shown when you export a CV PDF')
        except Exception as exc: messagebox.showerror('Could not open photo',str(exc),parent=self)
    def remove_cv_photo(self):
        self.cv_photo=None; self.cv_photo_label.configure(text='CV photo: none selected')
    def sync_long_fields(self):
        for key,widget in self.long_fields.items():
            if widget.winfo_exists(): self.values[key].set(widget.get('1.0','end-1c'))
    def generate(self):
        self.sync_long_fields()
        if self.doc_editor.get('1.0','end-1c').strip() and not messagebox.askyesno('Replace draft?','This will replace your current editor text. Continue?',parent=self): return
        content=make_document(self.doc_type.get(),self.selected_template_index(),{k:v.get() for k,v in self.values.items()}); self.doc_editor.delete('1.0','end'); self.doc_editor.insert('1.0',content); self.doc_text=content; self.status.set('Draft created  •  Review and edit before saving')
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
        self.heading(self.body,'AI settings','Optional writing help, when you need a little extra polish.')
        box=card(self.body); box.pack(fill='x'); label(box,'Connect to OpenAI',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,5)); label(box,'Use your own API key and an available model. Internet and an active API account are required.\nYour provider may charge for use.',size=12,color=MUTED,justify='left').pack(anchor='w',padx=20,pady=(0,15))
        label(box,'API key • kept in memory only for this session',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4)); ctk.CTkEntry(box,textvariable=self.key,show='•',height=38,border_color=BORDER,fg_color='#121A29').pack(fill='x',padx=20,pady=(0,12))
        label(box,'Model',size=11,bold=True,color=MUTED).pack(anchor='w',padx=20,pady=(0,4)); ctk.CTkEntry(box,textvariable=self.model,height=38,border_color=BORDER,fg_color='#121A29').pack(fill='x',padx=20,pady=(0,15))
        note=ctk.CTkFrame(box,fg_color='#382F27',corner_radius=10); note.pack(fill='x',padx=20,pady=(0,20)); label(note,'Privacy first',size=12,bold=True,color='#FFDBA2').pack(anchor='w',padx=14,pady=(11,3)); label(note,'AI sends the entire current draft to OpenAI after confirmation. Get the customer’s consent.\nDo not leave your API key accessible to customers on a shared computer.',size=12,color='#F1D1A5',justify='left').pack(anchor='w',padx=14,pady=(0,12))
    def improve(self):
        if not self.require_staff(): return
        text=self.current_text(); key=self.key.get().strip(); model=self.model.get().strip()
        if not text: return messagebox.showinfo('Create a draft','Create or enter a document first.',parent=self)
        if not key or not model: return messagebox.showinfo('Set up AI','Add an API key and model in AI settings first.',parent=self)
        if not messagebox.askyesno('Consent and privacy','Send the complete draft to OpenAI for improvement? This can incur API charges. Please get the customer’s permission first.',parent=self): return
        def request():
            try:
                r=requests.post('https://api.openai.com/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'messages':[{'role':'system','content':'Improve this career document for clarity, grammar and professionalism. Preserve facts and placeholders. Never invent qualifications, dates, employers or achievements. Return only the improved document.'},{'role':'user','content':text}]},timeout=60)
                if not r.ok: raise ValueError(f'AI request failed (HTTP {r.status_code}). Check the API key, model, account billing and internet connection.')
                return r.json()['choices'][0]['message']['content']
            except requests.RequestException: raise ValueError('Could not reach the AI provider. Check your internet connection and try again.')
        def complete(result):
            current=self.current_text()
            if current!=text and not messagebox.askyesno('Draft changed','The draft changed while the request was running. Replace it with the AI result?',parent=self): return
            self.doc_text=result
            if self.doc_editor and self.doc_editor.winfo_exists(): self.doc_editor.delete('1.0','end'); self.doc_editor.insert('1.0',result)
            messagebox.showinfo('Draft improved','Review the AI result and check all facts before saving.',parent=self)
        self.job('Improving draft with AI…',request,complete)

if __name__=='__main__': CafeDesk().mainloop()
