"""Desktop A4 picture-sheet editor."""
import math, os, subprocess, sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox
from PIL import Image,ImageTk,ImageOps
import customtkinter as ctk
import collage

class PictureSheet:
    def __init__(self,app,parent,card,label,button):
        self.app=app; self.parent=parent; self.card=card; self.label=label; self.button=button
        state=app.sheet
        app.heading(parent,'A4 picture sheets','Place, crop and resize photos on A4 pages, then save a print-ready PDF.')

        # Compact top sheet bar: layout, fit, B&W, brightness, and optional grid-wide mm size
        bar=card(parent);bar.pack(fill='x',pady=(0,12))
        top_row=ctk.CTkFrame(bar,fg_color='transparent');top_row.pack(fill='x',padx=18,pady=(14,8))
        label(top_row,'Sheet layout',size=12,bold=True,color='#A2ACC5').pack(side='left',padx=(0,8))
        self.layout_var=ctk.StringVar(value=state['preset'])
        ctk.CTkOptionMenu(top_row,values=list(collage.PRESETS),variable=self.layout_var,command=self.change_layout,fg_color='#445A86',button_color='#536B9D',width=155,height=34).pack(side='left',padx=(0,10))
        self.mode=ctk.StringVar(value=state['mode'])
        ctk.CTkOptionMenu(top_row,values=['Fit whole image','Fill & crop'],variable=self.mode,command=self.change_mode,fg_color='#445A86',button_color='#536B9D',width=145,height=34).pack(side='left',padx=(0,12))
        self.gray=ctk.BooleanVar(value=state['gray'])
        ctk.CTkCheckBox(top_row,text='Black & white',variable=self.gray,command=self.change_adjust,width=120).pack(side='left',padx=(0,12))
        label(top_row,'Brightness %',size=11,color='#A2ACC5').pack(side='left',padx=(0,5))
        self.brightness=ctk.CTkEntry(top_row,width=58,height=32)
        self.brightness.insert(0,str(state['brightness']))
        self.brightness.pack(side='left',padx=(0,5))
        button(top_row,'✓ Apply',self.change_adjust,secondary=True,width=72,height=32).pack(side='left')

        sizes=ctk.CTkFrame(bar,fg_color='transparent');sizes.pack(fill='x',padx=18,pady=(0,13))
        label(sizes,'Uniform grid size (mm):',size=11,color='#A2ACC5').pack(side='left',padx=(0,6))
        self.size_w=ctk.CTkEntry(sizes,width=68,height=32,placeholder_text='W mm')
        self.size_w.pack(side='left',padx=(0,4))
        self.size_w.insert(0,state.get('width_mm',''))
        label(sizes,'×',size=12,color='#A2ACC5').pack(side='left',padx=(0,4))
        self.size_h=ctk.CTkEntry(sizes,width=68,height=32,placeholder_text='H mm')
        self.size_h.pack(side='left',padx=(0,8))
        self.size_h.insert(0,state.get('height_mm',''))
        button(sizes,'⊞ Tile all',self.apply_custom_size,secondary=True,width=105,height=32).pack(side='left',padx=(0,12))
        label(sizes,'Print at 100% / actual size for exact millimetre dimensions.',size=11,color='#8FA1BF').pack(side='left')

        # Side-by-side workspace: A4 page preview on left + picture & export controls right next to it
        workspace=card(parent);workspace.pack(fill='x',pady=(0,12))
        columns=ctk.CTkFrame(workspace,fg_color='transparent');columns.pack(fill='x',padx=18,pady=16)
        columns.grid_columnconfigure(1,weight=1)

        left_col=ctk.CTkFrame(columns,fg_color='transparent')
        left_col.grid(row=0,column=0,sticky='nw',padx=(0,18))
        page_bar=ctk.CTkFrame(left_col,fg_color='transparent');page_bar.pack(fill='x',pady=(0,8))
        button(page_bar,'‹ Prev',lambda:self.turn(-1),secondary=True,width=80,height=30).pack(side='left')
        self.page_label=label(page_bar,'Page 1 / 1',size=12,bold=True)
        self.page_label.pack(side='left',expand=True)
        button(page_bar,'Next ›',lambda:self.turn(1),secondary=True,width=80,height=30).pack(side='right')

        self.w=400; self.h=566
        self.canvas=tk.Canvas(left_col,width=self.w,height=self.h,bg='white',highlightthickness=1,highlightbackground='#53627a')
        self.canvas.pack(anchor='w')
        self.canvas.bind('<Button-1>',self.down)
        self.canvas.bind('<B1-Motion>',self.drag)
        self.canvas.bind('<ButtonRelease-1>',lambda e:self.release())
        self.drag_info=None;self.thumbs=[];self.image_cache={}

        # Right inspector panel beside the A4 sheet (no scrolling back up needed)
        right_col=ctk.CTkFrame(columns,fg_color='transparent')
        right_col.grid(row=0,column=1,sticky='nsew')

        pic_box=ctk.CTkFrame(right_col,fg_color='#232E44',corner_radius=12,border_width=1,border_color='#34415A')
        pic_box.pack(fill='x',pady=(0,12))
        label(pic_box,'Pictures & tools',size=14,bold=True).pack(anchor='w',padx=14,pady=(12,4))
        self.selected_status=label(pic_box,'Click any picture on the A4 sheet to edit it.',size=11,bold=True,color='#B9FF70',wraplength=320,justify='left')
        self.selected_status.pack(anchor='w',padx=14,pady=(0,10))

        btn_grid=ctk.CTkFrame(pic_box,fg_color='transparent');btn_grid.pack(fill='x',padx=14,pady=(0,10))
        btn_grid.grid_columnconfigure((0,1),weight=1)
        button(btn_grid,'＋ Add pictures',self.add,height=36).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,7))
        button(btn_grid,'✂ Crop',self.crop_selected,secondary=True,height=34).grid(row=1,column=0,sticky='ew',padx=(0,4),pady=(0,7))
        button(btn_grid,'↻ Rotate 90°',self.rotate,secondary=True,height=34).grid(row=1,column=1,sticky='ew',padx=(4,0),pady=(0,7))
        button(btn_grid,'⧉ Duplicate',self.duplicate,secondary=True,height=34).grid(row=2,column=0,sticky='ew',padx=(0,4))
        button(btn_grid,'✕ Delete',self.remove,secondary=True,height=34).grid(row=2,column=1,sticky='ew',padx=(4,0))
        label(pic_box,'Drag inside a picture to move it, or drag a lime corner handle.',size=11,color='#A2ACC5',wraplength=320,justify='left').pack(anchor='w',padx=14,pady=(0,12))

        size_box=ctk.CTkFrame(right_col,fg_color='#232E44',corner_radius=12,border_width=1,border_color='#34415A')
        size_box.pack(fill='x',pady=(0,12))
        label(size_box,'Frame size (mm)',size=14,bold=True).pack(anchor='w',padx=14,pady=(12,3))
        label(size_box,'Exact width × height for the active picture frame:',size=11,color='#A2ACC5',wraplength=320,justify='left').pack(anchor='w',padx=14,pady=(0,8))
        dim_row=ctk.CTkFrame(size_box,fg_color='transparent');dim_row.pack(fill='x',padx=14,pady=(0,12))
        self.frame_width=ctk.CTkEntry(dim_row,width=72,height=34,placeholder_text='W mm')
        self.frame_width.pack(side='left',padx=(0,5))
        label(dim_row,'×',size=12,color='#C4D0E5').pack(side='left',padx=(0,5))
        self.frame_height=ctk.CTkEntry(dim_row,width=72,height=34,placeholder_text='H mm')
        self.frame_height.pack(side='left',padx=(0,8))
        button(dim_row,'⤢ Resize',self.resize_selected,width=105,height=34).pack(side='left')

        save_box=ctk.CTkFrame(right_col,fg_color='#232E44',corner_radius=12,border_width=1,border_color='#34415A')
        save_box.pack(fill='x')
        label(save_box,'Export & project',size=14,bold=True).pack(anchor='w',padx=14,pady=(12,8))
        button(save_box,'⬇ Save A4 PDF',self.save,fg_color='#B9FF70',hover_color='#A9EB65',text_color='#142018',height=38).pack(fill='x',padx=14,pady=(0,8))
        proj_row=ctk.CTkFrame(save_box,fg_color='transparent');proj_row.pack(fill='x',padx=14,pady=(0,12))
        proj_row.grid_columnconfigure((0,1),weight=1)
        button(proj_row,'💾 Save project',lambda:app.save_project('sheet'),secondary=True,height=34).grid(row=0,column=0,sticky='ew',padx=(0,4))
        button(proj_row,'📂 Open project',app.open_project,secondary=True,height=34).grid(row=0,column=1,sticky='ew',padx=(4,0))

        self.draw()

    def n_pages(self): return max([item['page'] for item in self.app.sheet['items']],default=0)+1
    def reflow(self):
        state=self.app.sheet;state['items']=collage.layout(state['items'],state['preset'],state.get('width_mm'),state.get('height_mm'));state['page']=min(state['page'],self.n_pages()-1);state['selected']=None
    def add(self):
        paths=filedialog.askopenfilenames(parent=self.app,filetypes=[('Images','*.jpg *.jpeg *.png *.bmp *.tif *.tiff')])
        if not paths:return
        state=self.app.sheet
        if state['items'] and not messagebox.askyesno('Rearrange pictures?','Adding pictures will reset current positions and sizes. Crops and rotations are kept. Continue?',parent=self.app):return
        try:
            if len(state['items'])+len(paths)>200:raise ValueError('Add up to 200 pictures per sheet.')
            for path in paths:
                with Image.open(path) as im:im.verify()
            state['items'].extend({'path':p} for p in paths);self.reflow();self.draw()
        except Exception as exc:messagebox.showerror('Could not add pictures',str(exc),parent=self.app)
    def change_layout(self,new):
        state=self.app.sheet
        if state['items'] and not messagebox.askyesno('Rearrange pictures?','Changing the layout will reset picture positions and sizes on every page. Crops and rotations are kept. Continue?',parent=self.app): self.layout_var.set(state['preset']);return
        if new=='Custom size (mm)':
            try: collage.layout([],new,self.size_w.get(),self.size_h.get())
            except ValueError as exc: self.layout_var.set(state['preset']);messagebox.showerror('Custom size',str(exc),parent=self.app);return
            state['width_mm']=self.size_w.get().strip();state['height_mm']=self.size_h.get().strip()
        state['preset']=new;state['page']=0;self.reflow();self.draw()
    def apply_custom_size(self):
        state=self.app.sheet
        try: collage.layout([],'Custom size (mm)',self.size_w.get(),self.size_h.get())
        except ValueError as exc: return messagebox.showerror('Custom size',str(exc),parent=self.app)
        if state['items'] and not messagebox.askyesno('Rearrange pictures?','Applying a custom size resets positions and sizes of all pictures. Crops and rotations are kept. Continue?',parent=self.app):return
        state.update(preset='Custom size (mm)',width_mm=self.size_w.get().strip(),height_mm=self.size_h.get().strip(),page=0);self.layout_var.set(state['preset']);self.reflow();self.draw()
    def rotate(self):
        state=self.app.sheet;i=state['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture on the A4 sheet first.',parent=self.app)
        item=state['items'][i];item['crop']=collage.rotate_crop(item.get('crop',[0,0,1,1]));item['rotation']=(item.get('rotation',0)+90)%360;self.draw()
    def crop_selected(self):
        i=self.app.sheet['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture on the A4 sheet first.',parent=self.app)
        from crop_editor import CropEditor
        def applied(crop):
            self.app.sheet['items'][i]['crop']=crop;self.draw()
        try:CropEditor(self.app,self.app.sheet['items'][i],applied)
        except Exception as exc:messagebox.showerror('Could not crop picture',str(exc),parent=self.app)
    def change_mode(self,value):self.app.sheet['mode']=value;self.draw()
    def change_adjust(self):
        try:
            val=int(self.brightness.get())
            if not 25<=val<=200:raise ValueError()
        except ValueError:return messagebox.showerror('Brightness','Enter a whole number from 25 to 200.',parent=self.app)
        self.app.sheet['brightness']=val;self.app.sheet['gray']=self.gray.get();self.draw()
    def turn(self,delta):
        self.app.sheet['page']=max(0,min(self.n_pages()-1,self.app.sheet['page']+delta));self.app.sheet['selected']=None;self.draw()
    def remove(self):
        state=self.app.sheet;i=state['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture on the A4 sheet first.',parent=self.app)
        if not messagebox.askyesno('Rearrange pictures?','Removing this picture will reset positions and sizes of the remaining pictures. Crops and rotations are kept. Continue?',parent=self.app):return
        del state['items'][i];self.reflow();self.draw()
    def duplicate(self):
        state=self.app.sheet;i=state['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture on the A4 sheet first.',parent=self.app)
        if len(state['items'])>=200:return messagebox.showinfo('Limit reached','Up to 200 pictures are supported.',parent=self.app)
        if not messagebox.askyesno('Rearrange pictures?','Duplicating will reset positions and sizes. Crops and rotations are kept. Continue?',parent=self.app):return
        state['items'].append({**state['items'][i],'crop':list(state['items'][i].get('crop',[0,0,1,1]))});self.reflow();self.draw()
    def update_selection_panel(self):
        state=self.app.sheet;i=state['selected'];item=state['items'][i] if i is not None and 0<=i<len(state['items']) else None
        self.selected_status.configure(text=('Active: '+Path(item['path']).name) if item else 'Click any picture on the A4 sheet to edit it.')
        for widget,value in ((self.frame_width,('%.2f' % (item['w']*210)) if item else ''),(self.frame_height,('%.2f' % (item['h']*297)) if item else '')):
            widget.delete(0,'end');widget.insert(0,value)
    def resize_selected(self):
        state=self.app.sheet;i=state['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture on the A4 sheet first, then drag its corners or enter the size.',parent=self.app)
        try:collage.resize_frame(state['items'][i],self.frame_width.get(),self.frame_height.get())
        except ValueError as exc:return messagebox.showerror('Cannot resize picture',str(exc),parent=self.app)
        self.draw()
    def draw(self):
        self.update_selection_panel()
        c=self.canvas;c.delete('all');self.thumbs=[];state=self.app.sheet;self.page_label.configure(text=f"Page {state['page']+1} / {self.n_pages()}")
        for i,item in enumerate(state['items']):
            if item['page']!=state['page']:continue
            x=int(item['x']*self.w);y=int(item['y']*self.h);w=max(1,int(item['w']*self.w));h=max(1,int(item['h']*self.h))
            c.create_rectangle(x,y,x+w,y+h,fill='#f0f0f0',outline='#b1b1b1')
            try:
                key=(item['path'],Path(item['path']).stat().st_mtime_ns,state['brightness'],state['gray'],item.get('rotation',0))
                if key not in self.image_cache:
                    image=collage.photo(item['path'],state['brightness']/100,state['gray'],item.get('rotation',0));image.thumbnail((1200,1200));self.image_cache[key]=image
                    if len(self.image_cache)>32:self.image_cache.pop(next(iter(self.image_cache)))
                image=collage.crop_image(self.image_cache[key],item.get('crop',[0,0,1,1]))
                image=ImageOps.fit(image,(w,h),method=Image.Resampling.LANCZOS) if state['mode']=='Fill & crop' else ImageOps.contain(image,(w,h),method=Image.Resampling.LANCZOS)
                thumb=ImageTk.PhotoImage(image);self.thumbs.append(thumb);c.create_image(x+w/2,y+h/2,image=thumb)
            except Exception:c.create_text(x+w/2,y+h/2,text='Image unavailable',fill='#9a3333',width=w)
            if i==state['selected']:
                c.create_rectangle(x,y,x+w,y+h,outline='#76b526',width=3)
                for hx,hy in ((x,y),(x+w,y),(x,y+h),(x+w,y+h)):
                    c.create_rectangle(hx-5,hy-5,hx+5,hy+5,fill='#b9ff70',outline='#55812b')
        if not state['items']:c.create_text(self.w/2,self.h/2,text='Click "＋ Add pictures" to start',fill='#777777')
    def down(self,event):
        state=self.app.sheet;selected=None;handle=None
        # Handles of the current selection remain usable even just outside its bounds.
        i=state['selected']
        if i is not None and 0<=i<len(state['items']):
            a=state['items'][i]
            if a['page']==state['page']:
                corners=[('tl',a['x']*self.w,a['y']*self.h),('tr',(a['x']+a['w'])*self.w,a['y']*self.h),('bl',a['x']*self.w,(a['y']+a['h'])*self.h),('br',(a['x']+a['w'])*self.w,(a['y']+a['h'])*self.h)]
                near=min(corners,key=lambda p:(p[1]-event.x)**2+(p[2]-event.y)**2)
                if (near[1]-event.x)**2+(near[2]-event.y)**2<=13**2:selected=i;handle=near[0]
        if selected is None:
            for i in reversed(range(len(state['items']))):
                a=state['items'][i]
                if a['page']==state['page'] and a['x']*self.w<=event.x<=(a['x']+a['w'])*self.w and a['y']*self.h<=event.y<=(a['y']+a['h'])*self.h:selected=i;break
        state['selected']=selected;self.drag_info=None
        if selected is not None:
            self.drag_info=(event.x,event.y,dict(state['items'][selected]),handle)
        self.draw()
    def drag(self,event):
        if not self.drag_info:return
        x,y,old,handle=self.drag_info;item=self.app.sheet['items'][self.app.sheet['selected']];dx=(event.x-x)/self.w;dy=(event.y-y)/self.h
        l=old['x'];t=old['y'];r=l+old['w'];b=t+old['h']
        if handle:
            if 'l' in handle:l=max(0,min(r-5/210,l+dx))
            if 'r' in handle:r=min(1,max(l+5/210,r+dx))
            if 't' in handle:t=max(0,min(b-5/297,t+dy))
            if 'b' in handle:b=min(1,max(t+5/297,b+dy))
            item.update(x=l,y=t,w=r-l,h=b-t)
        else:item['x']=max(0,min(1-old['w'],old['x']+dx));item['y']=max(0,min(1-old['h'],old['y']+dy))
        self.draw()
    def release(self):self.drag_info=None
    def save(self):
        state=self.app.sheet
        if not state['items']:return messagebox.showinfo('Add pictures','Add pictures first.',parent=self.app)
        path=filedialog.asksaveasfilename(parent=self.app,defaultextension='.pdf',filetypes=[('PDF','*.pdf')],initialfile='picture-sheets.pdf')
        if not path:return
        items=[dict(x) for x in state['items']];mode=state['mode'];brightness=state['brightness']/100;gray=state['gray']
        def complete(_):
            if messagebox.askyesno('Picture PDF saved','Open the PDF in your usual viewer to print all pages?',parent=self.app):
                try:
                    if sys.platform.startswith('win'):os.startfile(path)
                    elif sys.platform=='darwin':subprocess.Popen(['open',path])
                    else:subprocess.Popen(['xdg-open',path])
                except Exception:messagebox.showinfo('Open your PDF',f'Open this file in a PDF viewer to print:\n{path}',parent=self.app)
        self.app.job('Creating A4 picture sheets…',lambda:collage.render_pdf(items,path,mode,brightness,gray),complete)
