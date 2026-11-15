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
        app.heading(parent,'A4 picture sheets','Arrange several pictures on each A4 page, then save a PDF to print.')
        box=card(parent);box.pack(fill='x',pady=(0,12))
        label(box,'1   Add pictures from your computer',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,3))
        label(box,'Choose pictures separately from your CV photo. Reflowing changes all positions.',size=12,color='#A2ACC5').pack(anchor='w',padx=20,pady=(0,10))
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,17))
        button(row,'＋ Add pictures',self.add,width=135).pack(side='left',padx=(0,8))
        button(row,'Remove selected',self.remove,secondary=True,width=140).pack(side='left',padx=(0,8))
        button(row,'Duplicate selected',self.duplicate,secondary=True,width=150).pack(side='left',padx=(0,8));button(row,'Rotate selected ↻',self.rotate,secondary=True,width=145).pack(side='left')
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,17));button(row,'✂ Crop selected picture',self.crop_selected,width=205).pack(side='left',padx=(0,8));label(row,'Select one picture, then drag the crop handles on it.',size=11,color='#A2ACC5').pack(side='left')
        box=card(parent);box.pack(fill='x',pady=(0,12))
        label(box,'2   Arrange pictures and adjust the sheet',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,3))
        label(box,'Crop and rotate each picture separately. Fit, brightness and black & white apply to the whole sheet. Drag any picture to move it; drag a lime corner to resize it. Changing layouts resets positions and sizes.',size=12,color='#A2ACC5',wraplength=740,justify='left').pack(anchor='w',padx=20,pady=(0,10))
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,9))
        self.layout_var=ctk.StringVar(value=state['preset']);ctk.CTkOptionMenu(row,values=list(collage.PRESETS),variable=self.layout_var,command=self.change_layout,fg_color='#445A86',button_color='#536B9D',width=150).pack(side='left',padx=(0,8))
        self.mode=ctk.StringVar(value=state['mode']);ctk.CTkOptionMenu(row,values=['Fit whole image','Fill & crop'],variable=self.mode,command=self.change_mode,fg_color='#445A86',button_color='#536B9D',width=155).pack(side='left',padx=(0,8))
        self.gray=ctk.BooleanVar(value=state['gray']);ctk.CTkCheckBox(row,text='Black & white',variable=self.gray,command=self.change_adjust,width=130).pack(side='left',padx=(0,8))
        sizes=ctk.CTkFrame(box,fg_color='transparent');sizes.pack(fill='x',padx=20,pady=(0,9));label(sizes,'Grid size for all (mm)',size=11,color='#A2ACC5').pack(side='left',padx=(0,6));self.size_w=ctk.CTkEntry(sizes,width=70,height=34,placeholder_text='Width');self.size_w.pack(side='left',padx=(0,4));self.size_w.insert(0,state.get('width_mm',''));self.size_h=ctk.CTkEntry(sizes,width=70,height=34,placeholder_text='Height');self.size_h.pack(side='left',padx=(0,6));self.size_h.insert(0,state.get('height_mm',''));button(sizes,'Apply to all',self.apply_custom_size,secondary=True,width=140,height=34).pack(side='left')
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,9))
        label(row,'Brightness %',size=11,color='#A2ACC5').pack(side='left',padx=(2,4));self.brightness=ctk.CTkEntry(row,width=65,height=34);self.brightness.insert(0,str(state['brightness']));self.brightness.pack(side='left');button(row,'Apply',self.change_adjust,secondary=True,width=65,height=34).pack(side='left',padx=5)
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,8));button(row,'‹ Previous',lambda:self.turn(-1),secondary=True,width=110,height=32).pack(side='left',padx=(0,8));self.page_label=label(row,'Page 1 / 1',size=12,bold=True);self.page_label.pack(side='left',padx=(0,8));button(row,'Next ›',lambda:self.turn(1),secondary=True,width=90,height=32).pack(side='left')
        # The individual resize controls sit directly above the picture, not among sheet-wide settings.
        resize=ctk.CTkFrame(box,fg_color='#273248',corner_radius=10);resize.pack(fill='x',padx=20,pady=(0,12))
        label(resize,'Resize ONE selected picture',size=14,bold=True,color='#B9FF70').pack(anchor='w',padx=12,pady=(10,3))
        label(resize,'1. Click the picture on the page.  2. Drag any lime corner to change its frame, or enter a size below. Drag inside the frame to move it.',size=11,color='#C4D0E5',wraplength=700,justify='left').pack(anchor='w',padx=12,pady=(0,4))
        self.selected_status=label(resize,'No picture selected — click one on the page below.',size=11,bold=True,color='#B9FF70',wraplength=700,justify='left');self.selected_status.pack(anchor='w',padx=12,pady=(0,6))
        row=ctk.CTkFrame(resize,fg_color='transparent');row.pack(fill='x',padx=12,pady=(0,4));label(row,'Width (mm)',size=11,color='#C4D0E5').pack(side='left',padx=(0,5));self.frame_width=ctk.CTkEntry(row,width=80,height=32,placeholder_text='Width');self.frame_width.pack(side='left',padx=(0,10));label(row,'Height (mm)',size=11,color='#C4D0E5').pack(side='left',padx=(0,5));self.frame_height=ctk.CTkEntry(row,width=80,height=32,placeholder_text='Height');self.frame_height.pack(side='left',padx=(0,8));button(row,'Resize selected',self.resize_selected,width=140,height=32).pack(side='left',padx=(0,8));button(row,'Crop selected',self.crop_selected,secondary=True,width=130,height=32).pack(side='left')
        label(resize,'This changes only the selected picture frame. Cropping chooses what part of the picture appears. Fit may leave white space; Fill & crop may cut the edges.',size=11,color='#C4D0E5',wraplength=700,justify='left').pack(anchor='w',padx=12,pady=(1,11))
        self.w=400; self.h=566; self.canvas=tk.Canvas(box,width=self.w,height=self.h,bg='white',highlightthickness=1,highlightbackground='#53627a');self.canvas.pack(anchor='w',padx=20,pady=(0,13));self.canvas.bind('<Button-1>',self.down);self.canvas.bind('<B1-Motion>',self.drag);self.canvas.bind('<ButtonRelease-1>',lambda e:self.release());self.drag_info=None;self.thumbs=[];self.image_cache={}
        label(box,'For exact custom sizes, print at actual size / 100%. Check printer margins and verify with a ruler.',size=11,color='#A2ACC5').pack(anchor='w',padx=20,pady=(0,18))
        box=card(parent);box.pack(fill='x',pady=(0,12));label(box,'3   Save and print',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,4));label(box,'Save all pages together as a PDF, then open it in your PDF viewer to print.',size=12,color='#A2ACC5').pack(anchor='w',padx=20,pady=(0,12));button(box,'Save A4 picture PDF',self.save,width=195).pack(anchor='w',padx=20,pady=(0,10));row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(0,18));button(row,'Save project',lambda:app.save_project('sheet'),secondary=True,width=130).pack(side='left',padx=(0,8));button(row,'Open project',app.open_project,secondary=True,width=130).pack(side='left');self.draw()
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
        if i is None:return messagebox.showinfo('Select a picture','Click a picture to select it.',parent=self.app)
        item=state['items'][i];item['crop']=collage.rotate_crop(item.get('crop',[0,0,1,1]));item['rotation']=(item.get('rotation',0)+90)%360;self.draw()
    def crop_selected(self):
        i=self.app.sheet['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click the picture you want to crop first.',parent=self.app)
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
        if i is None:return messagebox.showinfo('Select a picture','Click a picture to select it.',parent=self.app)
        if not messagebox.askyesno('Rearrange pictures?','Removing this picture will reset positions and sizes of the remaining pictures. Crops and rotations are kept. Continue?',parent=self.app):return
        del state['items'][i];self.reflow();self.draw()
    def duplicate(self):
        state=self.app.sheet;i=state['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture to select it.',parent=self.app)
        if len(state['items'])>=200:return messagebox.showinfo('Limit reached','Up to 200 pictures are supported.',parent=self.app)
        if not messagebox.askyesno('Rearrange pictures?','Duplicating will reset positions and sizes. Crops and rotations are kept. Continue?',parent=self.app):return
        state['items'].append({**state['items'][i],'crop':list(state['items'][i].get('crop',[0,0,1,1]))});self.reflow();self.draw()
    def update_selection_panel(self):
        state=self.app.sheet;i=state['selected'];item=state['items'][i] if i is not None and 0<=i<len(state['items']) else None
        self.selected_status.configure(text=('Selected: '+Path(item['path']).name+'  •  drag a lime corner to resize') if item else 'No picture selected — click one on the page below.')
        for widget,value in ((self.frame_width,('%.2f' % (item['w']*210)) if item else ''),(self.frame_height,('%.2f' % (item['h']*297)) if item else '')):
            widget.delete(0,'end');widget.insert(0,value)
    def resize_selected(self):
        state=self.app.sheet;i=state['selected']
        if i is None:return messagebox.showinfo('Select a picture','Click a picture on the A4 page first, then drag its corners or enter the size.',parent=self.app)
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
        if not state['items']:c.create_text(self.w/2,self.h/2,text='Add pictures to preview the A4 page',fill='#777777')
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
