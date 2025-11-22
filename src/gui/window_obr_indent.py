# obr_env_editor.py
"""
Éditeur de credentials OBR (.env) pour production.

Modifications :
- écriture atomique + backup horodaté
- verrou simple par lockfile (empêche concurrence)
- permissions POSIX (chmod 600) et tentative d'ACL Windows (icacls)
- validation minimale des valeurs
- reload via python-dotenv et mise à jour config.FERNET_SECRET_KEY si nécessaire
- imports robustes et fallbacks pour usage dans build PyInstaller
"""

import os
import sys
import tempfile
import shutil
import logging
import time
import datetime
import base64
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Config import (retardé si nécessaire)
try:
    from config import OBR_ENV_PATH, get_user_data_dir  # type: ignore
except Exception:
    OBR_ENV_PATH = ".env"
    def get_user_data_dir(app_name: str = "facturation_obr") -> Path:
        return Path.cwd()

# Attempt to import load_dotenv (optional)
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# Optional view factories (import retardé)
_afficher_form_contribuable = None
try:
    from gui.form_contribuable import afficher_formulaire_contribuable  # type: ignore
    _afficher_form_contribuable = afficher_formulaire_contribuable
except Exception:
    _afficher_form_contribuable = None

# -----------------------
# Helpers: IO, locks, validation
# -----------------------

def _ensure_parent_dir(path: str):
    try:
        p = Path(path).resolve().parent
        p.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                p.chmod(0o700)
            except Exception:
                pass
    except Exception:
        logger.exception("Cannot ensure parent dir for %s", path)

def _read_lines(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except Exception as e:
        logger.exception("Error reading %s: %s", path, e)
        return []

def _timestamped_backup(path: str):
    try:
        if os.path.exists(path):
            ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            bak_path = f"{path}.bak.{ts}"
            try:
                shutil.copy2(path, bak_path)
            except Exception:
                logger.warning("Could not create .bak for %s", path)
    except Exception:
        logger.exception("Backup failed for %s", path)

def _write_lines_atomic(path: str, lines: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Write lines atomically to path, creating a timestamped .bak of previous file if exists.
    Returns (ok, error_message)
    """
    try:
        _ensure_parent_dir(path)
        _timestamped_backup(path)
        dirn = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".env_tmp_", dir=dirn, text=True)
        os.close(fd)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for l in lines:
                    f.write(l.rstrip("\n") + "\n")
            # atomic replace
            os.replace(tmp, path)
            # Restrict permissions POSIX
            try:
                if os.name != "nt":
                    os.chmod(path, 0o600)
            except Exception:
                logger.exception("Could not chmod %s", path)
            # Attempt Windows ACL (best-effort)
            try:
                if os.name == "nt":
                    user = os.getenv("USERNAME") or os.getlogin()
                    cmd = f'icacls "{path}" /inheritance:r /grant:r "{user}:(R,W)"'
                    os.system(cmd)
            except Exception:
                logger.exception("Could not apply Windows ACL to %s", path)
            return True, None
        except Exception as e:
            try:
                os.remove(tmp)
            except Exception:
                pass
            return False, str(e)
    except Exception as e:
        logger.exception("Atomic write failed for %s: %s", path, e)
        return False, str(e)

# Simple file-lock using excl create; best-effort cross-platform
def _acquire_lock(lock_path: str, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.12)
        except Exception:
            time.sleep(0.12)
    return False

def _release_lock(lock_path: str):
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass

def _build_updated_lines(orig_lines: List[str], mapping: dict) -> List[str]:
    found = {k: False for k in mapping.keys()}
    new_lines: List[str] = []
    for line in orig_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, _ = line.partition("=")
        key = key.strip()
        if key in mapping:
            new_lines.append(f"{key}={mapping[key]}")
            found[key] = True
        else:
            new_lines.append(line)
    for k, was_found in found.items():
        if not was_found:
            new_lines.append(f"{k}={mapping[k]}")
    return new_lines

def _reload_env(path: str):
    """Reload .env into environment using python-dotenv if available."""
    try:
        if load_dotenv:
            load_dotenv(dotenv_path=path, override=True)
            logger.info("Reloaded env from %s", path)
        else:
            try:
                for line in _read_lines(path):
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, _, v = s.partition("=")
                    os.environ[k.strip()] = v.strip()
                logger.info("Reloaded env (fallback) from %s", path)
            except Exception:
                logger.exception("Fallback env reload failed for %s", path)
        # If config is loaded, update config.FERNET_SECRET_KEY if present
        try:
            import config
            fkey = os.getenv("FACTURATION_OBR_FERNET_KEY")
            if fkey:
                try:
                    decoded = base64.urlsafe_b64decode(fkey.encode("utf-8"))
                    if len(decoded) == 32:
                        config.FERNET_SECRET_KEY = fkey.encode("utf-8")
                        logger.info("config.FERNET_SECRET_KEY updated from .env")
                except Exception:
                    logger.warning("FERNET key in .env not valid base64; skipping config update")
        except Exception:
            pass
    except Exception:
        logger.exception("Error reloading env from %s", path)

# Minimal validation for values to avoid injection/newline
def _validate_value(v: str) -> bool:
    if "\n" in v or "\r" in v:
        return False
    if len(v) > 2048:
        return False
    return True

# -----------------------
# GUI function
# -----------------------

def open_obr_inv_editor(parent, inv_path: Optional[str] = None):
    """
    Open a modal dialog to edit OBR credentials (.env).
    inv_path: explicit path to .env. If None uses OBR_ENV_PATH (from config) and,
              if that is relative, it's resolved inside get_user_data_dir().
    """
    # Resolve final inv_path
    try:
        if inv_path:
            final_path = inv_path
        else:
            final_path = OBR_ENV_PATH
        p = Path(final_path)
        if not p.is_absolute():
            try:
                user_dir = get_user_data_dir()
                final_path = str((Path(user_dir) / final_path).resolve())
            except Exception:
                final_path = str(p.resolve())
    except Exception:
        final_path = str(Path(OBR_ENV_PATH).resolve())

    orig_lines = _read_lines(final_path)

    dlg = tk.Toplevel(parent)
    dlg.transient(parent)
    dlg.grab_set()
    dlg.title("Édition OBR credentials (.env)")

    prefer_w, prefer_h = 520, 250
    try:
        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        x = max(0, (sw - prefer_w) // 2)
        y = max(0, (sh - prefer_h) // 2)
        dlg.geometry(f"{prefer_w}x{prefer_h}+{x}+{y}")
    except Exception:
        dlg.geometry(f"{prefer_w}x{prefer_h}")
    dlg.resizable(False, False)

    style = ttk.Style(dlg)
    try:
        style.theme_use(style.theme_use())
    except Exception:
        pass
    style.configure("Primary.TButton", foreground="white", background="#1e90ff", padding=(8, 6))
    style.map("Primary.TButton", background=[("active", "#1a78d1"), ("disabled", "#9dbfe8")])
    style.configure("Secondary.TButton", padding=(6, 5))

    frm = ttk.Frame(dlg, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Nom d'utilisateur (OBR_USERNAME):").grid(row=0, column=0, sticky="w", pady=(2, 4))
    user_var = tk.StringVar()
    user_ent = ttk.Entry(frm, textvariable=user_var, width=48)
    user_ent.grid(row=1, column=0, sticky="w")

    ttk.Label(frm, text="Mot de passe (OBR_PASSWORD):").grid(row=2, column=0, sticky="w", pady=(8, 4))
    pass_var = tk.StringVar()
    pass_ent = ttk.Entry(frm, textvariable=pass_var, show="*", width=40)
    pass_ent.grid(row=3, column=0, sticky="w")

    def _toggle_password():
        if pass_ent.cget("show") == "":
            pass_ent.config(show="*")
            btn_view.config(text="Voir")
        else:
            pass_ent.config(show="")
            btn_view.config(text="Masquer")

    btn_view = ttk.Button(frm, text="Voir", style="Secondary.TButton", command=_toggle_password)
    btn_view.grid(row=3, column=1, sticky="w", padx=(8, 0))

    ttk.Label(frm, text="System ID (OBR_SYSTEM_ID):").grid(row=4, column=0, sticky="w", pady=(8, 4))
    sysid_var = tk.StringVar()
    sysid_ent = ttk.Entry(frm, textvariable=sysid_var, width=48)
    sysid_ent.grid(row=5, column=0, sticky="w")

    # Pre-fill from orig_lines
    for line in orig_lines:
        s = line.strip()
        if not s or "=" not in s or s.startswith("#"):
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip()
        if k == "OBR_USERNAME" and not user_var.get():
            user_var.set(v)
        elif k == "OBR_PASSWORD" and not pass_var.get():
            pass_var.set(v)
        elif k == "OBR_SYSTEM_ID" and not sysid_var.get():
            sysid_var.set(v)

    def on_cancel():
        try:
            dlg.destroy()
        except Exception:
            pass

    def on_save():
        u = user_var.get().strip()
        p = pass_var.get().strip()
        s_id = sysid_var.get().strip()
        if not u or not p or not s_id:
            messagebox.showwarning("Validation", "Tous les champs sont requis.", parent=dlg)
            return

        mapping = {
            "OBR_USERNAME": u,
            "OBR_PASSWORD": p,
            "OBR_SYSTEM_ID": s_id
        }

        # Validate values
        for k, v in mapping.items():
            if not _validate_value(v):
                messagebox.showerror("Validation", f"Valeur invalide pour {k}", parent=dlg)
                return

        new_lines = _build_updated_lines(orig_lines, mapping)
        lockfile = final_path + ".lock"
        if not _acquire_lock(lockfile, timeout=5.0):
            messagebox.showerror("Erreur", "Le fichier est utilisé par une autre instance.", parent=dlg)
            return
        try:
            ok, err = _write_lines_atomic(final_path, new_lines)
        finally:
            _release_lock(lockfile)

        if ok:
            messagebox.showinfo("Succès", f"{os.path.basename(final_path)} mis à jour.", parent=dlg)
            _reload_env(final_path)
            try:
                dlg.destroy()
            except Exception:
                pass

            # Optional: open users/contribuables view after save (best-effort)
            try:
                from gui.tableau_utilisateurs import afficher_tableau_utilisateurs  # type: ignore
                from gui.window_utilisateurs import afficher_formulaire_utilisateur_societe  # type: ignore
            except Exception:
                afficher_tableau_utilisateurs = None
                afficher_formulaire_utilisateur_societe = None
            try:
                from gui.liste_contribuables import afficher_liste_contribuables  # type: ignore
            except Exception:
                afficher_liste_contribuables = None

            class _Scrollable(tk.Frame):
                def __init__(self, parent, bg=None, **kw):
                    super().__init__(parent, bg=bg)
                    self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, bg=bg)
                    self._vscroll = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
                    self._canvas.configure(yscrollcommand=self._vscroll.set)
                    self._vscroll.pack(side="right", fill="y")
                    self._canvas.pack(side="left", fill="both", expand=True)
                    self.inner = tk.Frame(self._canvas, bg=bg)
                    self._window = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
                    self.inner.bind("<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
                    self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(self._window, width=e.width))
                    self._canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
                    self._canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

                def _on_mousewheel(self, event):
                    try:
                        if hasattr(event, "delta") and event.delta:
                            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                        else:
                            if getattr(event, "num", None) == 4:
                                self._canvas.yview_scroll(-3, "units")
                            elif getattr(event, "num", None) == 5:
                                self._canvas.yview_scroll(3, "units")
                    except Exception:
                        pass

                def _bind_wheel(self, bind=True):
                    if bind:
                        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)
                        self._canvas.bind_all("<Button-4>", self._on_mousewheel)
                        self._canvas.bind_all("<Button-5>", self._on_mousewheel)
                    else:
                        try:
                            self._canvas.unbind_all("<MouseWheel>")
                            self._canvas.unbind_all("<Button-4>")
                            self._canvas.unbind_all("<Button-5>")
                        except Exception:
                            pass

            try:
                new_win = tk.Toplevel(parent)
                new_win.title("Utilisateurs et Contribuables")
                new_win.transient(parent)
                try:
                    ico = os.path.join(os.path.dirname(__file__), "..", "assets", "app.ico")
                    ico = os.path.normpath(ico)
                    if os.path.exists(ico):
                        new_win.iconbitmap(ico)
                except Exception:
                    pass
                try:
                    sw = parent.winfo_screenwidth()
                    sh = parent.winfo_screenheight()
                    w, h = 800, 700
                    x = max(0, (sw - w) // 2)
                    y = max(0, (sh - h) // 2)
                    new_win.geometry(f"{w}x{h}+{x}+{y}")
                except Exception:
                    pass

                menu_frame = tk.Frame(new_win, bg="#f3f4f6", height=44)
                menu_frame.pack(fill="x", side="top")
                content = _Scrollable(new_win, bg="white")
                content.pack(fill="both", expand=True)

                def _clear_content():
                    for w in content.inner.winfo_children():
                        try:
                            w.destroy()
                        except Exception:
                            pass

                def create_menu(title, icon, items):
                    lbl = tk.Label(menu_frame, text=f"{icon}  {title}", font=("Segoe UI", 11, "bold"), bg="#f3f4f6")
                    lbl.pack(side="left", padx=(12, 8), pady=8)
                    for txt, cb, _id in items:
                        def _make_cb(callback):
                            def _run():
                                try:
                                    _clear_content()
                                    if callable(callback):
                                        try:
                                            res = callback(content.inner)
                                            if isinstance(res, tk.Widget):
                                                try:
                                                    res.pack(fill="both", expand=True)
                                                except Exception:
                                                    pass
                                        except TypeError:
                                            try:
                                                res = callback()
                                                if isinstance(res, tk.Widget):
                                                    try:
                                                        res.pack(fill="both", expand=True)
                                                    except Exception:
                                                        pass
                                            except Exception:
                                                pass
                                except Exception as e:
                                    messagebox.showerror("Erreur ouverture", f"Impossible d'ouvrir {txt} : {e}", parent=new_win)
                            return _run
                        btn = ttk.Button(menu_frame, text=txt, command=_make_cb(cb), style="Default.TButton")
                        btn.pack(side="left", padx=6, pady=8)

                create_menu("Utilisateurs", "👤", [
                    ("Lister les utilisateurs", afficher_tableau_utilisateurs, "utilisateurs_view"),
                    ("Créer un utilisateur", afficher_formulaire_utilisateur_societe, "utilisateurs_create"),
                    ("Lister contribuables", afficher_liste_contribuables, "contribuables_view"),
                    ("Créer contribuable", _afficher_form_contribuable, "contribuables_create"),
                ])

                try:
                    _clear_content()
                    if callable(_afficher_form_contribuable):
                        res = _afficher_form_contribuable(content.inner)
                        if isinstance(res, tk.Widget):
                            try:
                                res.pack(fill="both", expand=True)
                            except Exception:
                                pass
                except Exception:
                    pass

            except Exception:
                try:
                    if callable(_afficher_form_contribuable):
                        _afficher_form_contribuable(parent)
                except Exception:
                    pass

            return

        else:
            messagebox.showerror("Erreur", f"Impossible d'écrire {final_path} : {err}", parent=dlg)

    btn_frame = ttk.Frame(frm)
    btn_frame.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(btn_frame, text="Annuler", style="Secondary.TButton", command=on_cancel).pack(side="right", padx=(0, 8))
    ttk.Button(btn_frame, text="Enregistrer", style="Primary.TButton", command=on_save).pack(side="right")

    user_ent.focus_set()
    return dlg
