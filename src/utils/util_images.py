# utils/util_images.py
import os
from functools import lru_cache
import threading
import sys
import tkinter as tk

try:
    from PIL import Image, ImageOps, ImageTk, ImageDraw
except Exception:
    Image = None
    ImageOps = None
    ImageTk = None
    ImageDraw = None

from .resources import resource_path

_lock = threading.Lock()

@lru_cache(maxsize=128)
def _load_and_prepare(path: str, size: tuple, circle: bool):
    if Image is None:
        return None
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        return None

    if size:
        try:
            target_w = int(size[0])
            target_h = int(size[1])
            img.thumbnail((target_w, target_h), Image.LANCZOS)
            canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            x = (target_w - img.width) // 2
            y = (target_h - img.height) // 2
            canvas.paste(img, (x, y), mask=img if img.mode == "RGBA" else None)
            img = canvas
        except Exception:
            pass

    if circle:
        try:
            w, h = img.size
            mask = Image.new("L", (w, h), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, w - 1, h - 1), fill=255)
            img.putalpha(mask)
        except Exception:
            pass

    return img

def _candidate_paths(filename: str):
    if os.path.isabs(filename):
        yield filename
    else:
        # use resource_path to support PyInstaller
        yield resource_path(os.path.join("assets", filename))
        yield resource_path(filename)
        yield os.path.join(os.getcwd(), filename)

def charger_image(filename: str, size: tuple = None, circle: bool = False):
    """
    Retourne ImageTk.PhotoImage ou None.
    - Charge via Pillow si disponible, redimensionne, applique masque circulaire si demandé.
    - Crée ImageTk.PhotoImage dans un contexte protégé. ATTENTION: PhotoImage doit être créé sur le thread principal.
    """
    if not filename:
        return None

    pil_img = None
    found = None
    for p in _candidate_paths(filename):
        try:
            if os.path.exists(p):
                found = p
                pil_img = _load_and_prepare(p, size, circle)
                if pil_img is not None:
                    break
        except Exception:
            pass

    # fallback: no PIL or couldn't prepare
    if pil_img is None and Image is None:
        try:
            from tkinter import PhotoImage
            for p in _candidate_paths(filename):
                if os.path.exists(p):
                    try:
                        if size is None:
                            return PhotoImage(file=p)
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    if pil_img is None:
        return None

    if ImageTk is None:
        return None

    # Ensure PhotoImage creation happens on the main thread.
    # If current thread is main thread, create directly with lock.
    if threading.current_thread() is threading.main_thread():
        try:
            with _lock:
                photo = ImageTk.PhotoImage(pil_img)
            return photo
        except Exception:
            try:
                return ImageTk.PhotoImage(pil_img)
            except Exception:
                return None

    # If called from a background thread, schedule creation on main thread and wait briefly.
    result = {"photo": None, "done": False}
    def _make():
        try:
            with _lock:
                result["photo"] = ImageTk.PhotoImage(pil_img)
        except Exception:
            result["photo"] = None
        finally:
            result["done"] = True

    # Try to use default root; if not available, fail gracefully.
    try:
        root = tk._default_root
        if root is None:
            return None
        root.after(0, _make)
        # wait for short time for creation (non-blocking design recommended instead)
        waited = 0.0
        while not result["done"] and waited < 1.5:
            time_sleep = 0.02
            import time
            time.sleep(time_sleep)
            waited += time_sleep
        return result["photo"]
    except Exception:
        return None
