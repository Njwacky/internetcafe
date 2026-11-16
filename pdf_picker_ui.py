"""Visual page selection for extracting pages from one PDF."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import pdf_preview

class PDFPagePicker:
    def __init__(self,app,path,on_apply):
        self.app=app;self.path=path;self.on_apply=on_apply;self.selected=set();self.total=0;self.start=0;self.expected=None;self.cards=[];self.pictures=[]
        self.window=ctk.CTkToplevel(app);self.window.title('Preview and select PDF pages');self.window.geometry('760x650');self.window.minsize(600,480);self.window.configure(fg_color='#101522');self.window.transient(app)
        ctk.CTkLabel(self.window,text='Preview and select pages',font=ctk.CTkFont(size=20,weight='bold')).pack(anchor='w',padx=20,pady=(14,3))
        ctk.CTkLabel(self.window,text=f'{Path(path).name}  •  Click a page to select it. Use View larger to read it.',text_color='#b9c7dc',wraplength=700,justify='left').pack(anchor='w',padx=20,pady=(0,7))
        tools=ctk.CTkFrame(self.window,fg_color='transparent');tools.pack(fill='x',padx=20,pady=(0,8))
        self.prev=ctk.CTkButton(tools,text='‹ Previous pages',width=130,fg_color='#344663',command=lambda:self.change(-1));self.prev.pack(side='left',padx=(0,8))
        self.next=ctk.CTkButton(tools,text='Next pages ›',width=120,fg_color='#344663',command=lambda:self.change(1));self.next.pack(side='left',padx=(0,8))
        self.page_range=ctk.CTkLabel(tools,text='Loading pages…',text_color='#b9c7dc');self.page_range.pack(side='left')
        footer=ctk.CTkFrame(self.window,fg_color='transparent');footer.pack(side='bottom',fill='x',padx=20,pady=(8,15))
        self.summary=ctk.CTkLabel(footer,text='0 selected',text_color='#b9ff70');self.summary.pack(side='left')
        ctk.CTkButton(footer,text='Cancel',width=85,fg_color='#344663',command=self.window.destroy).pack(side='right')
        ctk.CTkButton(footer,text='✓ Use pages',width=135,fg_color='#b9ff70',hover_color='#a9eb65',text_color='#142018',command=self.apply).pack(side='right',padx=(0,8))
        ctk.CTkButton(footer,text='Clear',width=78,fg_color='#344663',command=self.clear).pack(side='right',padx=(0,8))
        ctk.CTkButton(footer,text='Invert',width=78,fg_color='#344663',command=self.invert_all).pack(side='right',padx=(0,8))
        ctk.CTkButton(footer,text='Select all',width=95,fg_color='#344663',command=self.select_all).pack(side='right',padx=(0,8))
        self.grid=ctk.CTkScrollableFrame(self.window,fg_color='transparent');self.grid.pack(fill='both',expand=True,padx=14)
        for i in range(3):self.grid.grid_columnconfigure(i,weight=1)
        self.window.grab_set();self.window.focus_set();self.load()
    def alive(self):return bool(self.window.winfo_exists())
    def load(self):
        if self.app.busy:return messagebox.showinfo('Please wait','Wait for the current preview to finish.',parent=self.window)
        self.page_range.configure(text='Rendering preview…');start=self.start;expected=self.expected
        def done(result):
            if not self.alive():return
            total,pages,fp=result;self.total=total;self.expected=fp;self.page_range.configure(text=f'Pages {start+1}–{min(start+pdf_preview.BATCH_SIZE,total)} of {total}')
            for child in self.grid.winfo_children():child.destroy()
            self.cards=[];self.pictures=[]
            for j,(number,image) in enumerate(pages):
                card=ctk.CTkFrame(self.grid,fg_color='#202b3e',corner_radius=10);card.grid(row=j//3,column=j%3,sticky='n',padx=6,pady=6)
                pic=ctk.CTkImage(light_image=image,dark_image=image,size=image.size);self.pictures.append(pic)
                button=ctk.CTkButton(card,text='',image=pic,width=max(170,image.width+8),height=image.height+8,fg_color='#3e526b',hover_color='#536b84',command=lambda n=number:self.toggle(n));button.pack(padx=7,pady=(7,4))
                badge=ctk.CTkButton(card,text='',width=150,height=28,command=lambda n=number:self.toggle(n));badge.pack(padx=7,pady=(0,4))
                ctk.CTkButton(card,text='View larger',width=150,height=27,fg_color='#344663',command=lambda n=number:self.larger(n)).pack(padx=7,pady=(0,7));self.cards.append((number,badge))
            self.refresh()
        self.app.job('Rendering PDF pages…',lambda:pdf_preview.render_batch(self.path,start,expected),done)
    def change(self,direction):
        if self.app.busy:return
        nxt=self.start+direction*pdf_preview.BATCH_SIZE
        if 0<=nxt<self.total:self.start=nxt;self.load()
    def toggle(self,n):
        if n in self.selected:self.selected.remove(n)
        else:self.selected.add(n)
        self.refresh()
    def refresh(self):
        self.summary.configure(text=f'{len(self.selected)} selected')
        for n,b in self.cards:
            chosen=n in self.selected;b.configure(text=f'{"✓ Selected" if chosen else "Select"}  •  Page {n}',fg_color='#b9ff70' if chosen else '#344663',hover_color='#a9eb65' if chosen else '#40506d',text_color='#142018' if chosen else 'white')
        self.prev.configure(state='normal' if self.start>0 else 'disabled');self.next.configure(state='normal' if self.start+pdf_preview.BATCH_SIZE<self.total else 'disabled')
    def clear(self):self.selected.clear();self.refresh()
    def select_all(self):
        if self.total>0:self.selected=set(range(1,self.total+1));self.refresh()
    def invert_all(self):
        if self.total>0:self.selected=set(range(1,self.total+1))-self.selected;self.refresh()
    def larger(self,n):
        if self.app.busy:return
        def done(image):
            if not self.alive():return
            win=ctk.CTkToplevel(self.window);win.title(f'Page {n}');win.transient(self.window)
            pane=ctk.CTkScrollableFrame(win,fg_color='#202b3e',width=min(650,image.width+30),height=min(700,image.height+50));pane.pack(fill='both',expand=True,padx=12,pady=12)
            photo=ctk.CTkImage(light_image=image,dark_image=image,size=image.size);view=ctk.CTkLabel(pane,text='',image=photo);view.image=photo;view.pack(padx=5,pady=5)
            ctk.CTkButton(win,text='Close preview',fg_color='#344663',command=win.destroy).pack(pady=(0,12));win.grab_set()
            def close():win.destroy();self.window.grab_set()
            win.protocol('WM_DELETE_WINDOW',close)
            # Restore the parent grab for both the button and window close.
            for child in win.winfo_children():
                if isinstance(child,ctk.CTkButton):child.configure(command=close)
        self.app.job(f'Opening page {n}…',lambda:pdf_preview.render_page(self.path,n,self.expected),done)
    def apply(self):
        if self.app.busy:return messagebox.showinfo('Please wait','Wait for the preview to finish.',parent=self.window)
        try:
            if pdf_preview.fingerprint(self.path)!=self.expected:raise ValueError('The PDF changed. Choose it again before extracting pages.')
            spec=pdf_preview.selection_spec(self.selected)
        except (ValueError,OSError) as exc:return messagebox.showerror('Cannot use selection',str(exc),parent=self.window)
        self.on_apply(spec,self.expected);self.window.destroy()
