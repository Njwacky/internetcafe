"""Review a WIA scan before using it in the session's image or PDF tools."""
from pathlib import Path
from tempfile import NamedTemporaryFile
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from crop_editor import CropEditor
from services import open_safe_image


def render(image, turns, grayscale):
    result = image.rotate(-90 * turns, expand=True) if turns else image
    return ImageOps.grayscale(result) if grayscale else result


def save_edits(path, turns, grayscale, crop):
    # Replace only after a successful write; both session lists keep the same path.
    with open_safe_image(path) as source:
        image = render(source.convert('RGB'), turns, grayscale)
        w, h = image.size
        l, t, r, b = (round(v * dim) for v, dim in zip(crop, (w, h, w, h)))
        l, t = min(l, w-1), min(t, h-1)
        image = image.crop((l, t, max(l+1, r), max(t+1, b)))
    with NamedTemporaryFile(dir=Path(path).parent, delete=False) as tmp:
        staged = Path(tmp.name)
    try:
        image.save(staged, format='BMP')
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


class ScanEditor(CropEditor):
    def __init__(self, parent, path, on_saved):
        self.path, self.on_saved = path, on_saved
        self.turns, self.grayscale = 0, False
        with open_safe_image(path) as source:
            self.original = source.convert('RGB')
        self.original.thumbnail((680, 440), Image.Resampling.LANCZOS)
        super().__init__(parent, {}, None, title='Review scan', heading='Review scanned page',
                         apply_text='Apply edits', preview=self.original)

    def extra_controls(self):
        row = ctk.CTkFrame(self.window, fg_color='transparent')
        row.pack(pady=(0, 10))
        ctk.CTkButton(row, text='Rotate right 90°', command=self.rotate).pack(side='left', padx=4)
        ctk.CTkSegmentedButton(row, values=['Color', 'B/W'], command=self.set_mode,
                               variable=ctk.StringVar(value='Color')).pack(side='left', padx=4)

    def rotate(self):
        self.turns = (self.turns + 1) % 4
        l, t, r, b = self.crop
        self.crop = [1-b, l, 1-t, r]
        self.refresh()

    def set_mode(self, mode):
        self.grayscale = mode == 'B/W'
        self.refresh()

    def refresh(self):
        image = render(self.original, self.turns, self.grayscale)
        image.thumbnail((680, 440), Image.Resampling.LANCZOS)
        self.width, self.height = image.size
        self.canvas.configure(width=self.width, height=self.height)
        self.image = ImageTk.PhotoImage(image, master=self.canvas)
        self.draw()

    def apply(self):
        try:
            if self.turns or self.grayscale or self.crop != [0, 0, 1, 1]:
                save_edits(self.path, self.turns, self.grayscale, self.crop)
        except Exception as exc:
            messagebox.showerror('Could not apply scan edits', str(exc), parent=self.window)
            return
        self.window.destroy()
        self.on_saved()
