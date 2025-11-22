# LoginView.py
"""
Login view adapté pour utiliser database.connection.get_connection()
au lieu d'appels directs à sqlite3.connect(...).
"""

import os
import hashlib
try:
    import bcrypt
    _HAS_BCRYPT = True
except Exception:
    bcrypt = None
    _HAS_BCRYPT = False

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

from utils import util_ventana as utl
from models.session import session as global_session

# Import centralisé de la connexion
try:
    from database.connection import get_connection
except Exception:
    # fallback minimal si database.connection introuvable
    import sqlite3
    def get_connection(path: str = None):
        p = path or "facturation_obr.db"
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        return conn

# ----------------- Password helpers -----------------

def _hash_password(pw: str) -> str:
    if not pw:
        return ""
    if _HAS_BCRYPT:
        hashed = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")
    else:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def verify_password(plain_pw: str, stored_hash: str) -> bool:
    if not plain_pw or not stored_hash:
        return False
    sh = str(stored_hash).strip()
    if not sh:
        return False
    if sh.startswith("$2a$") or sh.startswith("$2b$") or sh.startswith("$2y$"):
        if not _HAS_BCRYPT:
            return False
        try:
            return bcrypt.checkpw(plain_pw.encode("utf-8"), sh.encode("utf-8"))
        except Exception:
            return False
    else:
        try:
            return hashlib.sha256(plain_pw.encode("utf-8")).hexdigest() == sh
        except Exception:
            return False

# ----------------- Authentication -----------------

def verifier_utilisateur_local(username, password):
    """
    Vérifie identifiants contre utilisateur_societe.username.
    La colonne 'password' doit contenir le hash (bcrypt ou sha256 hex).
    Retourne sqlite3.Row si ok, sinon None.
    """
    if not username or not password:
        return None
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM utilisateur_societe WHERE username = ? LIMIT 1", (username,))
            user = cur.fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not user:
            return None

        stored_hash = None
        try:
            stored_hash = user["password"]
        except Exception:
            # fallback to common positions
            try:
                if len(user) > 2:
                    stored_hash = user[2]
                elif len(user) > 3:
                    stored_hash = user[3]
            except Exception:
                stored_hash = None

        if verify_password(password, stored_hash):
            # optional migration sha256->bcrypt: update stored hash if bcrypt available
            try:
                sh = str(stored_hash or "")
                if _HAS_BCRYPT and not (sh.startswith("$2a$") or sh.startswith("$2b$") or sh.startswith("$2y$")):
                    new_hash = _hash_password(password)
                    try:
                        conn2 = get_connection()
                        try:
                            cur2 = conn2.cursor()
                            cur2.execute("UPDATE utilisateur_societe SET password = ? WHERE username = ?", (new_hash, username))
                            conn2.commit()
                        finally:
                            try:
                                conn2.close()
                            except Exception:
                                pass
                    except Exception:
                        # ignore migration errors
                        pass
            except Exception:
                pass
            return user
        return None
    except Exception as e:
        messagebox.showerror("Erreur", f"Connexion à la base impossible : {e}")
        return None

# ----------------- LoginView -----------------

class LoginView(tk.Frame):
    WIDTH = 620
    HEIGHT = 440
    LEFT_WIDTH = 260
    MAX_FORM_WIDTH = 440

    def __init__(self, parent, controller, **kwargs):
        super().__init__(parent, bg="#f0f2f5")
        self.controller = controller
        self._build_ui()

    def _build_ui(self):
        root = self.controller
        root.title("Connexion à la plateforme")
        root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        root.config(bg="#f0f2f5")
        try:
            root.resizable(False, False)
        except Exception:
            pass
        try:
            utl.centrar_ventana(root, self.WIDTH, self.HEIGHT)
        except Exception:
            root.update_idletasks()
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            x, y = (sw - self.WIDTH) // 2, (sh - self.HEIGHT) // 2
            root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        COLOR_LEFT_BG = "#1e90ff"
        COLOR_RIGHT_BG = "#ffffff"
        COLOR_PAGE_BG = "#f0f2f5"

        logo = None
        assets_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "login.png")
        try:
            pil_img = Image.open(assets_path)
            pil_img = pil_img.resize((self.LEFT_WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)
            logo = ImageTk.PhotoImage(pil_img)
        except Exception:
            logo = None

        main_container = tk.Frame(self, bg=COLOR_PAGE_BG)
        main_container.pack(fill="both", expand=True)
        main_container.columnconfigure(0, weight=0)
        main_container.columnconfigure(1, weight=1)
        main_container.rowconfigure(0, weight=1)

        left_frame = tk.Frame(main_container, bd=0, width=self.LEFT_WIDTH, bg=COLOR_LEFT_BG)
        left_frame.grid(row=0, column=0, sticky="ns")
        left_frame.grid_propagate(False)
        if logo:
            lbl_logo = tk.Label(left_frame, image=logo, bg=COLOR_LEFT_BG)
            lbl_logo.image = logo
            lbl_logo.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            tk.Label(left_frame, text="Image non disponible", bg=COLOR_LEFT_BG, fg="white").pack(expand=True)

        right_outer = tk.Frame(main_container, bg=COLOR_PAGE_BG)
        right_outer.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        right_outer.columnconfigure(0, weight=1)
        right_outer.rowconfigure(0, weight=1)

        center_frame = tk.Frame(right_outer, bg=COLOR_PAGE_BG)
        center_frame.grid(row=0, column=0, sticky="nsew")
        center_frame.columnconfigure(0, weight=1)

        spacer_top = tk.Frame(center_frame, height=18, bg=COLOR_PAGE_BG)
        spacer_top.grid(row=0, column=0, sticky="ew")
        spacer_top.grid_propagate(False)

        form_container = tk.Frame(center_frame, bg=COLOR_PAGE_BG)
        form_container.grid(row=1, column=0, sticky="n", padx=6)
        form_container.columnconfigure(0, weight=1)

        def _adapt_width(event=None):
            w = center_frame.winfo_width() or (self.WIDTH - self.LEFT_WIDTH)
            new_w = min(self.MAX_FORM_WIDTH, max(280, w - 40))
            form_container.config(width=new_w)

        center_frame.bind("<Configure>", _adapt_width)

        form_card = tk.Frame(form_container, bg=COLOR_RIGHT_BG, bd=0, relief=tk.RIDGE)
        form_card.pack(fill="both", expand=False)
        form_card.columnconfigure(0, weight=1)

        titre = tk.Label(form_card, text="Connexion", font=("Times", 20, "bold"), fg="#333", bg=COLOR_RIGHT_BG, pady=6)
        titre.pack(fill="x", pady=(8, 6))

        cadre_champs = tk.Frame(form_card, bg=COLOR_RIGHT_BG)
        cadre_champs.pack(fill="both", expand=True, padx=14, pady=(6, 10))

        lbl_user = tk.Label(cadre_champs, text="Nom d'utilisateur", font=("Segoe UI", 11), fg="#444", bg=COLOR_RIGHT_BG, anchor="w")
        lbl_user.pack(fill="x", padx=6, pady=(8, 4))
        self.champ_utilisateur = ttk.Entry(cadre_champs, font=("Segoe UI", 12))
        self.champ_utilisateur.pack(fill="x", padx=6, pady=(0, 8))

        lbl_pass = tk.Label(cadre_champs, text="Mot de passe", font=("Segoe UI", 11), fg="#444", bg=COLOR_RIGHT_BG, anchor="w")
        lbl_pass.pack(fill="x", padx=6, pady=(6, 4))
        self.champ_mot_de_passe = ttk.Entry(cadre_champs, font=("Segoe UI", 12), show="*")
        self.champ_mot_de_passe.pack(fill="x", padx=6, pady=(0, 8))

        self.case_afficher = tk.BooleanVar(value=False)

        def toggle_password():
            self.champ_mot_de_passe.config(show="" if self.case_afficher.get() else "*")

        chk = ttk.Checkbutton(cadre_champs, text="Afficher le mot de passe", variable=self.case_afficher, command=toggle_password)
        chk.pack(anchor="w", padx=6, pady=(0, 8))

        self.barre_chargement = ttk.Progressbar(cadre_champs, mode="indeterminate")

        def se_connecter():
            username = self.champ_utilisateur.get().strip()
            password = self.champ_mot_de_passe.get().strip()
            if not username or not password:
                messagebox.showerror("Erreur", "Veuillez saisir vos identifiants.")
                return
            try:
                self.barre_chargement.pack(fill="x", padx=6, pady=(6, 6))
                self.barre_chargement.start()
            except Exception:
                pass
            self.after(200, lambda: self._verifier_et_lancer(username, password))

        btn_conn = tk.Button(cadre_champs, text="Se connecter", font=("Segoe UI", 13, "bold"),
                             bg="#1e90ff", fg="white", bd=0, activebackground="#1673d6", command=se_connecter)
        btn_conn.pack(fill="x", padx=6, pady=(8, 10))

        self.champ_utilisateur.bind("<Return>", lambda e: se_connecter())
        self.champ_mot_de_passe.bind("<Return>", lambda e: se_connecter())

        try:
            root.attributes("-alpha", 0.0)
            def fade_in():
                try:
                    a = root.attributes("-alpha")
                except Exception:
                    return
                if a < 1.0:
                    try:
                        root.attributes("-alpha", min(1.0, a + 0.06))
                    except Exception:
                        pass
                    root.after(18, fade_in)
            root.deiconify()
            fade_in()
        except Exception:
            root.deiconify()

    def _verifier_et_lancer(self, username, password):
        try:
            self.barre_chargement.stop()
            self.barre_chargement.pack_forget()
        except Exception:
            pass

        user = verifier_utilisateur_local(username, password)
        if not user:
            messagebox.showerror("Erreur", "Identifiants incorrects ❌")
            return

        # Extract role safely
        role = None
        try:
            if "role" in user.keys():
                role = user["role"]
            elif len(user) >= 4:
                role = user[3]
        except Exception:
            role = None

        # start session
        try:
            global_session.start_session(username, role)
        except Exception:
            try:
                global_session.username = username
                global_session.role = role
            except Exception:
                pass

        messagebox.showinfo("Succès", f"Bienvenue {username} ✅")

        # Special admin flow: show LicenseView only (LicenseView decides whether to open the .env editor)
        if username == "superuser" and (role == "superuser" or str(role).lower() == "superuser"):
            try:
                self.controller.show_view("LicenseView")
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible d'ouvrir LicenseView : {e}")
            return

        # Normal users -> open MainView with fade out
        root = self.controller

        def fade_out():
            try:
                alpha = root.attributes("-alpha")
            except Exception:
                alpha = 1.0
            if alpha > 0.0:
                try:
                    root.attributes("-alpha", max(0.0, alpha - 0.06))
                except Exception:
                    pass
                root.after(18, fade_out)
            else:
                try:
                    self.controller.show_view("MainView", on_logout=self._on_logout)
                except Exception as e:
                    messagebox.showerror("Erreur", f"Impossible d'ouvrir l'application principale : {e}")
                finally:
                    try:
                        root.attributes("-alpha", 1.0)
                    except Exception:
                        pass

        fade_out()

    def _on_logout(self):
        try:
            global_session.end_session()
        except Exception:
            pass
        try:
            self.controller.show_view("LoginView")
        except Exception:
            try:
                self.controller.destroy()
            except Exception:
                pass
