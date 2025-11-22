# gui/theme.py
import tkinter as tk
from tkinter import ttk
import matplotlib as mpl

# Paramètres de style
GLOBAL_FONT_FAMILY = "Segoe UI"
GLOBAL_FONT_SIZE = 10
GLOBAL_TITLE_FONT = ("Segoe UI", 14, "bold")
GLOBAL_UI_BG = "#f6f8fa"
GLOBAL_ACCENT = "#0b3d91"

def apply_tk_theme(root: tk.Tk):
    """
    Applique réglages de police et couleurs aux widgets Tk/ttk.
    Appelle ceci une seule fois au démarrage (avant de construire l'UI).
    """
    try:
        root.option_add("*Font", f"{GLOBAL_FONT_FAMILY} {GLOBAL_FONT_SIZE}")
        root.option_add("*Background", GLOBAL_UI_BG)
        root.option_add("*Foreground", "#111827")
        root.option_add("*Button.Font", f"{GLOBAL_FONT_FAMILY} {GLOBAL_FONT_SIZE}")
        root.option_add("*Label.Font", f"{GLOBAL_FONT_FAMILY} {GLOBAL_FONT_SIZE}")
        root.option_add("*Entry.Font", f"{GLOBAL_FONT_FAMILY} {GLOBAL_FONT_SIZE}")
        root.option_add("*Listbox.Font", f"{GLOBAL_FONT_FAMILY} {GLOBAL_FONT_SIZE}")
        root.option_add("*Text.Font", f"{GLOBAL_FONT_FAMILY} {GLOBAL_FONT_SIZE}")
    except Exception:
        pass

    try:
        style = ttk.Style()
        for t in ("clam", "alt", "default", "vista"):
            try:
                style.theme_use(t)
                break
            except Exception:
                pass

        style.configure(".", background=GLOBAL_UI_BG, foreground="#111827", font=(GLOBAL_FONT_FAMILY, GLOBAL_FONT_SIZE))
        style.configure("TLabel", background=GLOBAL_UI_BG, font=(GLOBAL_FONT_FAMILY, GLOBAL_FONT_SIZE))
        style.configure("TButton", font=(GLOBAL_FONT_FAMILY, GLOBAL_FONT_SIZE), padding=6)
        style.configure("Treeview", font=(GLOBAL_FONT_FAMILY, GLOBAL_FONT_SIZE), rowheight=int(GLOBAL_FONT_SIZE * 2.8))
        style.configure("Treeview.Heading", font=(GLOBAL_FONT_FAMILY, GLOBAL_FONT_SIZE, "bold"))
        style.configure("Accent.TLabel", foreground=GLOBAL_ACCENT, background=GLOBAL_UI_BG, font=(GLOBAL_FONT_FAMILY, GLOBAL_FONT_SIZE, "bold"))
    except Exception:
        pass

def apply_matplotlib_theme():
    """
    Applique les paramètres Matplotlib pour correspondre au thème Tk.
    Appelle ceci avant l'instanciation de Figures.
    """
    try:
        mpl.rcParams["font.family"] = GLOBAL_FONT_FAMILY
        mpl.rcParams["font.size"] = GLOBAL_FONT_SIZE
        mpl.rcParams["axes.titlesize"] = GLOBAL_FONT_SIZE + 4
        mpl.rcParams["axes.titleweight"] = "bold"
        mpl.rcParams["axes.labelsize"] = GLOBAL_FONT_SIZE
        mpl.rcParams["xtick.labelsize"] = max(8, GLOBAL_FONT_SIZE - 1)
        mpl.rcParams["ytick.labelsize"] = max(8, GLOBAL_FONT_SIZE - 1)
        mpl.rcParams["legend.fontsize"] = max(8, GLOBAL_FONT_SIZE - 1)
        mpl.rcParams["figure.titlesize"] = GLOBAL_FONT_SIZE + 6
        mpl.rcParams["axes.edgecolor"] = "#e6eef9"
        mpl.rcParams["axes.grid"] = True
        mpl.rcParams["grid.linestyle"] = "--"
        mpl.rcParams["grid.color"] = "#e5e7eb"
    except Exception:
        pass
