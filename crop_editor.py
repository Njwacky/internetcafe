"""Drag a crop rectangle directly on an individual picture without altering its source."""
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk
import collage

class CropEditor:
    def __init__(self, parent, item, on_apply):
        self.parent=parent; self.on_apply=on_apply; self.crop=collage.validate_crop(item.get('crop',[0,0,1,1])); self.drag_state=None
        image=collage.photo(item['path'],rotation=item.get('rotation',0))
        image.thumbnail((680,440),Image.Resampling.LANCZOS)
        self.width,self.height=image.size
        self.window=ctk.CTkToplevel(parent); self.window.title('Crop picture'); self.window.transient(parent)
        self.window.configure(fg_color='#101522'); self.window.resizable(False,False)
        ctk.CTkLabel(self.window,text='Crop this picture',font=ctk.CTkFont(size=19,weight='bold'),text_color='white').pack(anchor='w',padx=18,pady=(16,3))
        ctk.CTkLabel(self.window,text='Drag the lime corners to resize the crop. Drag inside the box to move it. The original is not changed.',text_color='#b9c7dc',wraplength=650,justify='left').pack(anchor='w',padx=18,pady=(0,10))
        self.canvas=tk.Canvas(self.window,width=self.width,height=self.height,bg='#111',highlightthickness=0,cursor='crosshair');self.canvas.pack(padx=18,pady=(0,10))
        self.image=ImageTk.PhotoImage(image,master=self.canvas)
        self.canvas.bind('<ButtonPress-1>',self.start);self.canvas.bind('<B1-Motion>',self.move);self.canvas.bind('<ButtonRelease-1>',self.end)
        row=ctk.CTkFrame(self.window,fg_color='transparent');row.pack(fill='x',padx=18,pady=(0,16))
        ctk.CTkButton(row,text='Reset crop',width=110,fg_color='#344663',command=self.reset).pack(side='left')
        ctk.CTkButton(row,text='Cancel',width=90,fg_color='#344663',command=self.window.destroy).pack(side='right')
        ctk.CTkButton(row,text='Apply crop',width=120,fg_color='#b9ff70',hover_color='#a9eb65',text_color='#142018',command=self.apply).pack(side='right',padx=(0,8))
        self.draw();self.window.grab_set();self.window.focus_set()
    def draw(self):
        c=self.canvas;w=self.width;h=self.height;l,t,r,b=self.crop;x1=l*w;y1=t*h;x2=r*w;y2=b*h
        c.delete('all');c.create_image(0,0,image=self.image,anchor='nw')
        # Stipple shades outside the chosen region while keeping the photo visible.
        for rect in ((0,0,w,y1),(0,y2,w,h),(0,y1,x1,y2),(x2,y1,w,y2)):
            c.create_rectangle(*rect,fill='#101522',stipple='gray50',outline='')
        c.create_rectangle(x1,y1,x2,y2,outline='#b9ff70',width=3)
        for x,y in ((x1,y1),(x2,y1),(x1,y2),(x2,y2)):
            c.create_rectangle(x-6,y-6,x+6,y+6,fill='#b9ff70',outline='#132018',width=1)
    def start(self,event):
        x=event.x/self.width;y=event.y/self.height;l,t,r,b=self.crop
        corners=[('tl',l,t),('tr',r,t),('bl',l,b),('br',r,b)]
        hit=min(corners,key=lambda v:((v[1]-x)*self.width)**2+((v[2]-y)*self.height)**2)
        dist=((hit[1]-x)*self.width)**2+((hit[2]-y)*self.height)**2
        mode=hit[0] if dist<=18**2 else ('move' if l<=x<=r and t<=y<=b else None)
        self.drag_state=(mode,x,y,list(self.crop)) if mode else None
    def move(self,event):
        if not self.drag_state:return
        mode,sx,sy,old=self.drag_state;x=max(0,min(1,event.x/self.width));y=max(0,min(1,event.y/self.height));l,t,r,b=old
        if mode=='move':
            dx=max(-l,min(1-r,x-sx));dy=max(-t,min(1-b,y-sy));self.crop=[l+dx,t+dy,r+dx,b+dy]
        else:
            # At least 1% of each dimension, making corners easy to grab and valid.
            if 'l' in mode:l=max(0,min(r-.01,x))
            if 'r' in mode:r=min(1,max(l+.01,x))
            if 't' in mode:t=max(0,min(b-.01,y))
            if 'b' in mode:b=min(1,max(t+.01,y))
            self.crop=[l,t,r,b]
        self.draw()
    def end(self,event):self.drag_state=None
    def reset(self):self.crop=[0,0,1,1];self.draw()
    def apply(self):
        self.on_apply(collage.validate_crop(self.crop));self.window.destroy()
