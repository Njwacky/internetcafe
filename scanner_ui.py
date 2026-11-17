"""In-app scanner picker and scanned-page preview."""
import sys,uuid
from pathlib import Path
import tkinter as tk
from tkinter import messagebox,filedialog
from PIL import Image,ImageOps
import customtkinter as ctk
import win32_scanner,services

def show_scanner(app,parent,card,label,button):
    box=card(parent);box.pack(fill='x',pady=(0,14))
    label(box,'Scan inside Café Desk',size=16,bold=True).pack(anchor='w',padx=20,pady=(18,4))
    label(box,'Windows: connect the scanner by USB, turn it on, then find and select it below. This lists scanners available through Windows WIA, not printer-only or TWAIN-only devices.',size=12,color='#A2ACC5',wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,10))
    if sys.platform!='win32':label(box,'Direct scanning is Windows-only in this version. On macOS or Linux use your scanner app and import the saved image below.',size=11,color='#e8c187',wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,10))
    device_row=ctk.CTkFrame(box,fg_color='transparent');device_row.pack(fill='x',padx=20,pady=(0,8))
    labels=[f"{i+1}. {x['name']}" for i,x in enumerate(app.scanner_devices)] or ['No scanner selected']
    ids=[x['id'] for x in app.scanner_devices]
    index=ids.index(app.scanner_device_id) if app.scanner_device_id in ids else -1
    current=ctk.StringVar(value=labels[index] if index>=0 else 'No scanner selected')
    def choose(value):
        try:app.scanner_device_id=ids[labels.index(value)]
        except (ValueError,IndexError):app.scanner_device_id=None
    menu=ctk.CTkOptionMenu(device_row,values=labels,variable=current,command=choose,fg_color='#445A86',button_color='#536B9D',width=280);menu.pack(side='left',padx=(0,8))
    def refresh():
        if app.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=app)
        def done(devices):
            app.scanner_devices=devices
            if app.scanner_device_id not in [d['id'] for d in devices]:app.scanner_device_id=None
            if app.current=='Scan & images':app.show('Scan & images')
            if not devices:messagebox.showinfo('No Windows scanners found','Check the USB cable and power. Install the Epson scanner driver with Windows WIA support and check the Windows Image Acquisition service. Printer drivers alone may not provide scanning. Then refresh.',parent=app)
            else:app.status.set(f'{len(devices)} Windows scanner(s) found • choose one to scan')
        app.job('Finding Windows scanners…',win32_scanner.list_scanners,done)
    button(device_row,'Find / refresh scanners',refresh,secondary=True,width=165,height=34).pack(side='left',padx=(0,8))
    def scan():
        if app.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=app)
        if not app.scanner_device_id:return messagebox.showinfo('Select a scanner','Click Find / refresh scanners, then select your scanner from the list.',parent=app)
        device=app.scanner_device_id;out=str(Path(app.session_temp.name)/('scan-'+uuid.uuid4().hex+'.bmp'))
        def done(path):
            app.scanned_pages.append(path);app.image_files.append(path)
            if app.current=='Scan & images':app.show('Scan & images')
            app.status.set(f'Scan complete • {len(app.scanned_pages)} scanned page(s) in this session')
        app.job('Scanning one page… please wait',lambda:win32_scanner.scan_page(device,out),done)
    button(device_row,'Scan one page',scan,width=140,height=34).pack(side='left')
    label(box,'One page per scan. Put the next page on the glass and scan again. Uses the scanner driver’s default settings; scanning may take a while.',size=11,color='#A2ACC5',wraplength=730,justify='left').pack(anchor='w',padx=20,pady=(0,8))
    holder=ctk.CTkFrame(box,fg_color='transparent');holder.pack(fill='x',padx=20,pady=(0,10))
    left=ctk.CTkFrame(holder,fg_color='#121A29');left.pack(side='left',fill='both',expand=True,padx=(0,10))
    label(left,'Scanned pages in this session',size=12,bold=True).pack(anchor='w',padx=10,pady=(8,4))
    listing=tk.Listbox(left,height=5,bg='#121A29',fg='#F2F4FA',selectbackground='#405270',relief='flat',highlightthickness=0,exportselection=False);listing.pack(fill='x',padx=10,pady=(0,10))
    for n,_ in enumerate(app.scanned_pages,1):listing.insert('end',f'Scanned page {n}')
    preview=ctk.CTkLabel(holder,text='Select a scanned page\nto preview it',width=180,height=220,fg_color='#273248',corner_radius=8,text_color='#A2ACC5');preview.pack(side='left')
    def show_page(event=None):
        sel=listing.curselection()
        if not sel:return
        try:
            with services.open_safe_image(app.scanned_pages[sel[0]]) as image:
                image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((180,220));photo=ctk.CTkImage(light_image=image,dark_image=image,size=image.size)
            preview.configure(text='',image=photo);preview.image=photo
        except Exception:preview.configure(text='Preview unavailable',image=None);preview.image=None
    listing.bind('<<ListboxSelect>>',show_page)
    if app.scanned_pages:listing.selection_set(len(app.scanned_pages)-1);show_page()
    row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,8))
    def remove():
        if app.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=app)
        sel=listing.curselection()
        if not sel:return messagebox.showinfo('Select a scan','Select a scanned page to remove.',parent=app)
        path=app.scanned_pages.pop(sel[0]);app.image_files[:]=[p for p in app.image_files if p!=path]
        try:Path(path).unlink(missing_ok=True)
        except OSError:messagebox.showwarning('Temporary file remains',f'The scan could not be removed from:\n{path}',parent=app)
        app.show('Scan & images')
    def save_pdf():
        if not app.scanned_pages:return messagebox.showinfo('No scans','Scan at least one page first.',parent=app)
        if app.busy:return messagebox.showinfo('Please wait','Wait for the current task to finish.',parent=app)
        out=filedialog.asksaveasfilename(parent=app,defaultextension='.pdf',filetypes=[('PDF document','*.pdf')],initialfile='scanned-pages.pdf')
        if out:
            paths=list(app.scanned_pages);app.job('Saving scanned pages as PDF…',lambda:services.images_to_pdf(paths,out),lambda _:messagebox.showinfo('Scanned PDF saved',f'Saved to:\n{out}\n\nThe saved PDF is not removed by Next customer.',parent=app))
    button(row,'Save scans as PDF',save_pdf,width=185).pack(side='left',padx=(0,8));button(row,'Delete scan',remove,secondary=True,width=135).pack(side='left')
    label(box,'Scans are temporary files on this computer until saved. Next customer or normal exit attempts to remove them; crashes may leave copies. Saved PDFs are not deleted.',size=11,color='#A2ACC5',wraplength=730,justify='left').pack(anchor='w',padx=20,pady=(0,16))
