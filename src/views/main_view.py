# MainView.py
import os
import threading
import time
import socket
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import logging

from utils.util_images import charger_image
from models.session import session as global_session
from config import COULEUR_BARRE_SUPERIEURE, COULEUR_MENU_LATERAL, COULEUR_CORPS_PRINCIPAL, COULEUR_MENU_SURVOL

from gui.liste_clients import afficher_liste_clients
from gui.window_facture import afficher_formulaire_facture
from gui.tableau_de_Factures import afficher_liste_factures
from gui.tableau_articles_reuissi import show_obr_articles
from gui.tableau_articles_echec import show_failed_articles
from gui.window_articles_import import ImportStockBatchFrame
from gui.tableau_articles_import_echec import FailedImportsFrame
from gui.tableau_article_import_re import show_obr_articles_import
from gui.tableau_utilisateurs import afficher_tableau_utilisateurs
from gui.window_article_entre import formulaire_entree_et_declaration
from gui.window_facture_saisie import afficher_formulaire_facture_manual
from gui.window_utilisateurs import afficher_formulaire_utilisateur_societe

from database.connection import get_connection
from api.obr_client import obtenir_token_auto, get_system_id

# optional dashboard modules
try:
    from gui.dashboard_manager import build_metrics_panel
except Exception:
    build_metrics_panel = None

try:
    from gui.dashboard_agent import build_dashboard_overview
except Exception:
    build_dashboard_overview = None

try:
    from gui.form_graficas_design import FormulaireGraphiquesDesign
except Exception:
    FormulaireGraphiquesDesign = None

# optional theme helpers
try:
    from gui.theme import apply_tk_theme, apply_matplotlib_theme
except Exception:
    apply_tk_theme = None
    apply_matplotlib_theme = None

COULEUR_SCROLLBAR_FOND = COULEUR_MENU_LATERAL
COULEUR_SCROLLBAR_BOUTON = "#5c7c98"
COULEUR_SCROLLBAR_ACTIF = "#8ca3ba"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class MainView(tk.Frame):
    PERMISSIONS_PAR_ROLE = {"admin": set(), "manager": set(), "agent": set()}

    def __init__(self, parent, controller, on_logout=None, force_top_left=True, **kwargs):
        try:
            if apply_tk_theme and isinstance(parent, (tk.Tk, tk.Toplevel)):
                try:
                    apply_tk_theme(parent)
                except Exception:
                    pass
            if apply_matplotlib_theme:
                try:
                    apply_matplotlib_theme()
                except Exception:
                    pass
        except Exception:
            pass

        super().__init__(parent, bg=COULEUR_CORPS_PRINCIPAL)
        self.controller = controller
        self.on_logout = on_logout
        self._active_button = None
        self._menus = {}
        self._anim_lock = threading.Lock()
        self._sidebar_visible = True
        self._network_ok = None
        self._key_admin_instance = None
        self._all_permissions = set()
        self._current_metrics_refresh = None

        # preload a PIL/photo for the navbar logo (kept as reference to avoid GC)
        self._navbar_logo = None
        try:
            self._navbar_logo = charger_image("logo.jpg", (68, 68), circle=True)
        except Exception:
            self._navbar_logo = None

        # fonts
        self.title_font = ("Roboto", 15, "bold")
        self.menu_font = ("Segoe UI", 13)
        self.submenu_font = ("Segoe UI", 12)
        self.net_font = ("Segoe UI", 10, "bold")

        # try to set window title/icon/geometry
        try:
            if hasattr(self.controller, "title"):
                self.controller.title("Mon Application — Facturation Obr")
            ico = os.path.join(os.path.dirname(__file__), "..", "assets", "app.ico")
            ico = os.path.normpath(ico)
            if os.path.exists(ico) and hasattr(self.controller, "iconbitmap"):
                self.controller.iconbitmap(ico)
        except Exception:
            pass

        try:
            if hasattr(self.controller, "winfo_screenwidth"):
                sw = self.controller.winfo_screenwidth()
                sh = self.controller.winfo_screenheight()
                desired_w = min(1500, int(sw * 0.92))
                desired_h = min(820, int(sh * 0.86))
                x = (sw - desired_w) // 2
                y = (sh - desired_h) // 2
                self.controller.geometry(f"{desired_w}x{desired_h}+{x}+{y}")
                self.controller.update_idletasks()
        except Exception:
            pass

        self._build_ui()
        self._start_network_checker()

        # open the exclusive dashboard for the current role:
        # admin -> FormulaireGraphiquesDesign only
        # manager -> build_metrics_panel only
        # agent -> build_dashboard_overview only
        try:
            role = getattr(global_session, "role", None)
            chosen_role = str(role).lower() if role else None

            if chosen_role == "admin":
                if FormulaireGraphiquesDesign:
                    self._open_in_content(lambda: FormulaireGraphiquesDesign(self.content_inner))
                else:
                    # fallback: if admin graphic form missing, show an informative message only
                    self._show_dashboard_missing("admin")
                return

            if chosen_role == "manager":
                if build_metrics_panel:
                    self._open_in_content(lambda: self._open_metrics(build_metrics_panel))
                else:
                    # fallback: manager overview missing
                    self._show_dashboard_missing("manager")
                return

            # agent or no role -> agent dashboard
            if chosen_role == "agent" or chosen_role is None:
                if build_dashboard_overview:
                    self._open_in_content(lambda: build_dashboard_overview(self.content_inner, contrib_id=self._get_first_contrib_id(), low_threshold=5, role_filter="agent"))
                else:
                    # fallback: agent overview missing
                    self._show_dashboard_missing("agent")
                return

        except Exception:
            try:
                self.ouvrir_graphiques()
            except Exception:
                pass

    def _show_dashboard_missing(self, role):
        # Clear and display a single message explaining missing dashboard component
        try:
            for w in list(self.content_inner.winfo_children()):
                if w is self.error_frame:
                    continue
                try:
                    w.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            lbl = tk.Label(self.content_inner,
                           text=f"Le dashboard pour le rôle '{role}' n'est pas disponible (module manquant).",
                           bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font, wraplength=900, justify="left")
            lbl.grid(row=0, column=0, sticky="nw", padx=20, pady=20)
        except Exception:
            try:
                lbl.pack(padx=20, pady=20)
            except Exception:
                pass

    def _open_metrics(self, build_metrics_fn):
        try:
            res = build_metrics_fn(self.content_inner, contrib_id=self._get_first_contrib_id(), low_threshold=5)
            if isinstance(res, dict) and "refresh" in res and callable(res["refresh"]):
                self._current_metrics_refresh = res["refresh"]
        except Exception as e:
            try:
                tk.Label(self.content_inner, text=f"Erreur ouverture métriques: {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font).grid(row=0, column=0, sticky="nw", padx=20, pady=20)
            except Exception:
                try:
                    tk.Label(self.content_inner, text=f"Erreur ouverture métriques: {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font).pack(padx=20, pady=20)
                except Exception:
                    pass

    def _get_user_fields(self):
        s = global_session
        if not s or getattr(s, "username", None) is None:
            return ("Invité", "guest")
        try:
            conn = sqlite3.connect("facturation_obr.db")
            cur = conn.cursor()
            cur.execute("SELECT nom FROM utilisateur_societe WHERE username = ?", (s.username,))
            row = cur.fetchone()
            conn.close()
            display = row[0] if row and row[0] else s.username
        except Exception:
            display = s.username
        return (display, s.username)

    def _load_permissions_for_user(self, username):
        if not username:
            return set()
        try:
            conn = sqlite3.connect("facturation_obr.db")
            cur = conn.cursor()
            cur.execute("SELECT role FROM utilisateur_societe WHERE username = ?", (username,))
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                role = row[0]
                perms = self.PERMISSIONS_PAR_ROLE.get(role)
                if perms is not None:
                    return set(perms)
        except Exception:
            pass
        perms = self.PERMISSIONS_PAR_ROLE.get(username)
        return set(perms) if perms else set()

    def _a_permission(self, perm):
        uname = self._get_user_fields()[1]
        if not uname:
            return False
        if not hasattr(self, "_cached_permissions") or getattr(self, "_cached_permissions_user", None) != uname:
            self._cached_permissions_user = uname
            self._cached_permissions = self._load_permissions_for_user(uname)
        return perm in self._cached_permissions

    def _build_ui(self):
        self.barre_superieure = tk.Frame(self, bg=COULEUR_BARRE_SUPERIEURE, height=72)
        self.barre_superieure.pack(side="top", fill="x")

        center = tk.Frame(self, bg=COULEUR_CORPS_PRINCIPAL)
        center.pack(side="top", fill="both", expand=True)

        sidebar_w = 320
        self.sidebar_container = tk.Frame(center, bg=COULEUR_MENU_LATERAL, width=sidebar_w)
        self.sidebar_container.pack(side="left", fill="y")
        self.sidebar_container.pack_propagate(False)

        self.sidebar_canvas = tk.Canvas(self.sidebar_container, bg=COULEUR_MENU_LATERAL, highlightthickness=0)
        self.sidebar_canvas.pack(side="left", fill="both", expand=True)

        self.sidebar_scroll = tk.Scrollbar(
            self.sidebar_container,
            orient="vertical",
            command=self.sidebar_canvas.yview,
            width=6,
            troughcolor=COULEUR_SCROLLBAR_FOND,
            bg=COULEUR_SCROLLBAR_BOUTON,
            activebackground=COULEUR_SCROLLBAR_ACTIF
        )
        self.sidebar_scroll.pack(side="right", fill="y", padx=(0, 4))
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scroll.set)
        self.sidebar_inner = tk.Frame(self.sidebar_canvas, bg=COULEUR_MENU_LATERAL)
        self.sidebar_window = self.sidebar_canvas.create_window((0, 0), window=self.sidebar_inner, anchor="nw")

        self.sidebar_inner.bind("<Configure>", lambda e: self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all")))
        self.sidebar_canvas.bind("<Configure>", lambda e: self.sidebar_canvas.itemconfig(self.sidebar_window, width=e.width))

        self.content_container = tk.Frame(center, bg=COULEUR_CORPS_PRINCIPAL)
        self.content_container.pack(side="right", fill="both", expand=True)

        # content canvas + internal frame pattern (robuste)
        self.content_canvas = tk.Canvas(self.content_container, bg=COULEUR_CORPS_PRINCIPAL, highlightthickness=0)
        self.content_canvas.pack(side="left", fill="both", expand=True)

        self.content_scroll = tk.Scrollbar(
            self.content_container,
            orient="vertical",
            command=self.content_canvas.yview,
            width=12,
            troughcolor="#dddddd",
            bg="#bbbbbb",
            activebackground="#cccccc"
        )
        self.content_scroll.pack(side="right", fill="y", padx=(0, 4))
        self.content_canvas.configure(yscrollcommand=self.content_scroll.set)

        self.content_inner = tk.Frame(self.content_canvas, bg=COULEUR_CORPS_PRINCIPAL)
        self.content_window = self.content_canvas.create_window((0, 0), window=self.content_inner, anchor="nw")

        # keep scrollregion updated and force inner width to canvas width
        def _on_canvas_config(event):
            try:
                self.content_canvas.itemconfig(self.content_window, width=event.width)
                self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))
            except Exception:
                pass

        self.content_inner.bind("<Configure>", lambda e: self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all")))
        self.content_canvas.bind("<Configure>", _on_canvas_config)

        # mouse wheel handling for content area (bind only while pointer inside)
        def _on_mousewheel(event):
            try:
                if hasattr(event, "delta") and event.delta:
                    delta = -1 * int(event.delta / 120)
                    self.content_canvas.yview_scroll(delta, "units")
                else:
                    if event.num == 4:
                        self.content_canvas.yview_scroll(-1, "units")
                    elif event.num == 5:
                        self.content_canvas.yview_scroll(1, "units")
            except Exception:
                pass

        def _bind_mousewheel_to_canvas(w):
            w.bind("<Enter>", lambda e: w.bind_all("<MouseWheel>", _on_mousewheel))
            w.bind("<Leave>", lambda e: w.unbind_all("<MouseWheel>"))
            w.bind("<Enter>", lambda e: w.bind_all("<Button-4>", _on_mousewheel))
            w.bind("<Leave>", lambda e: w.unbind_all("<Button-4>"))
            w.bind("<Enter>", lambda e: w.bind_all("<Button-5>", _on_mousewheel))
            w.bind("<Leave>", lambda e: w.unbind_all("<Button-5>"))

        _bind_mousewheel_to_canvas(self.content_canvas)

        # dedicated error frame inside content_inner
        self.error_frame = tk.Frame(self.content_inner, bg=COULEUR_CORPS_PRINCIPAL)
        try:
            self.error_frame.grid(row=0, column=0, sticky="nw", padx=0, pady=0)
        except Exception:
            try:
                self.error_frame.pack(side="top", anchor="nw")
            except Exception:
                pass

        self._build_topbar_contents()
        self._build_sidebar_items()

    def _build_topbar_contents(self):
        logo_outer = tk.Frame(self.barre_superieure, bg=COULEUR_BARRE_SUPERIEURE)
        logo_outer.pack(side="left", padx=8, pady=8)

        size = 58
        canvas_w = size + 6
        canvas_h = size + 6
        c = tk.Canvas(logo_outer, width=canvas_w, height=canvas_h, bg=COULEUR_BARRE_SUPERIEURE, highlightthickness=0)
        c.pack()

        c.create_oval(4, 4, 4 + size, 4 + size, fill="#dddddd", outline="")
        c.create_oval(0, 0, size, size, fill="white", outline="#e6e6e6", width=1)

        img = self._navbar_logo
        if img:
            c.image = img
            cx = canvas_w // 2
            cy = canvas_h // 2
            c.create_image(cx, cy, image=img, anchor="center")

        btn_toggle = tk.Button(self.barre_superieure, text="☰", bg=COULEUR_BARRE_SUPERIEURE, fg="white", bd=0,
                               font=("Segoe UI", 14, "bold"), command=self.toggle_menu, padx=10, pady=6)
        btn_toggle.pack(side="left", padx=(6, 8), pady=8)

        display, uname = self._get_user_fields()
        self._user_label = tk.Label(self.barre_superieure, text=f"{display}  —  @{uname}", bg=COULEUR_BARRE_SUPERIEURE,
                                    fg="white", font=self.title_font)
        self._user_label.pack(side="left", padx=6)

        center_frame = tk.Frame(self.barre_superieure, bg=COULEUR_BARRE_SUPERIEURE)
        center_frame.pack(side="left", fill="both", expand=True)

        net_box = tk.Frame(center_frame, bg=COULEUR_BARRE_SUPERIEURE)
        net_box.pack(expand=True)
        self._network_label = tk.Label(net_box, text="Connexion...", font=self.net_font, bd=1, relief="groove",
                                       padx=12, pady=6)
        self._network_label.pack(expand=True)

        btn_deco = tk.Button(self.barre_superieure, text="Déconnexion", bg="#e53e3e", fg="white", bd=0,
                             font=("Segoe UI", 12, "bold"), command=self._on_logout_button, padx=12, pady=6)
        btn_deco.pack(side="right", padx=12, pady=8)

    def _build_sidebar_items(self):
        inner = self.sidebar_inner
        header = tk.Frame(inner, bg=COULEUR_MENU_LATERAL)
        header.pack(fill="x", pady=(12, 6))

        tk.Label(header,
                 text="Facturation Obr",
                 bg=COULEUR_MENU_LATERAL,
                 fg="#8ca3ba",
                 font=("Segoe UI", 14, "bold")
        ).pack(side="top", padx=12, anchor="w", pady=(0, 6))

        self._all_permissions = set()
        self._menus = {}

        def create_menu(title, icon, items):
            for _, _, perm in items:
                if perm:
                    self._all_permissions.add(perm)

            hdr = tk.Button(inner, text=f"{icon}   {title}", anchor="w", bd=0, bg=COULEUR_MENU_LATERAL, fg="white",
                             activebackground=COULEUR_MENU_SURVOL, font=self.menu_font)
            hdr.pack(fill="x", padx=12, pady=(8, 6))
            hdr.bind("<Enter>", lambda e: hdr.config(bg=COULEUR_MENU_SURVOL))
            hdr.bind("<Leave>", lambda e: hdr.config(bg=COULEUR_MENU_LATERAL))

            cont = tk.Frame(inner, bg=COULEUR_MENU_LATERAL)
            cont.pack_forget()

            def expand_with_animation(frame, expand=True, steps=6, delay=12):
                if not self._anim_lock.acquire(blocking=False):
                    if expand:
                        frame.pack(fill="x", padx=(28, 0))
                    else:
                        frame.pack_forget()
                    self.sidebar_canvas.config(scrollregion=self.sidebar_canvas.bbox("all"))
                    return

                def worker():
                    try:
                        if expand:
                            frame.pack(fill="x", padx=(28, 0))
                            for _ in range(steps):
                                time.sleep(delay / 1000.0)
                                try:
                                    self.sidebar_canvas.update_idletasks()
                                except Exception:
                                    pass
                        else:
                            for _ in range(steps):
                                time.sleep(delay / 1000.0)
                            try:
                                frame.pack_forget()
                            except Exception:
                                pass
                        self.sidebar_canvas.after(1, lambda: self.sidebar_canvas.config(scrollregion=self.sidebar_canvas.bbox("all")))
                    finally:
                        try:
                            self._anim_lock.release()
                        except Exception:
                            pass

                threading.Thread(target=worker, daemon=True).start()

            def on_header_click():
                for k, (frame, expanded) in list(self._menus.items()):
                    if k != title and expanded:
                        self._menus[k] = (frame, False)
                        expand_with_animation(frame, expand=False)
                frm, expanded = self._menus.get(title, (cont, False))
                if expanded:
                    self._menus[title] = (cont, False)
                    expand_with_animation(cont, expand=False)
                else:
                    self._menus[title] = (cont, True)
                    expand_with_animation(cont, expand=True)

            hdr.config(command=on_header_click)

            def _build_loader(callback):
                def loader():
                    try:
                        if callable(callback):
                            try:
                                res = callback(self.content_inner)
                            except TypeError:
                                res = callback()
                            if isinstance(res, tk.Widget):
                                try:
                                    res.pack(fill="both", expand=True)
                                except Exception:
                                    pass
                        else:
                            try:
                                if isinstance(callback, type) and issubclass(callback, tk.Widget):
                                    inst = callback(self.content_inner)
                                    try:
                                        inst.pack(fill="both", expand=True)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            for ch in list(self.error_frame.winfo_children()):
                                ch.destroy()
                        except Exception:
                            pass
                        try:
                            lbl = tk.Label(self.error_frame, text=f"Erreur ouverture vue : {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font, wraplength=800, justify="left")
                            lbl.grid(row=0, column=0, sticky="nw", padx=20, pady=20)
                        except Exception:
                            try:
                                tk.Label(self.content_inner, text=f"Erreur ouverture vue : {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font).pack(padx=20, pady=20)
                            except Exception:
                                pass
                return loader

            for txt, fn, perm in items:
                sub = tk.Button(cont, text=f"•   {txt}", anchor="w", bd=0, bg=COULEUR_MENU_LATERAL, fg="white",
                                 activebackground=COULEUR_MENU_SURVOL, font=self.submenu_font)
                sub.pack(fill="x", pady=6)
                sub.bind("<Enter>", lambda e, b=sub: b.config(bg=COULEUR_MENU_SURVOL))
                sub.bind("<Leave>", lambda e, b=sub: b.config(bg=COULEUR_MENU_LATERAL))

                sub._perm_key = perm
                loader_callable = _build_loader(fn)

                try:
                    if perm is None or self._a_permission(perm):
                        sub.config(command=lambda lc=loader_callable: self._open_in_content(lc), state="normal")
                    else:
                        sub.config(command=lambda lc=loader_callable: self._open_in_content(lc), state="disabled")
                except Exception:
                    sub.config(state="disabled")

            self._menus[title] = (cont, False)
            return hdr, cont

        current_role = getattr(global_session, "role", None)
        current_role = str(current_role).lower() if current_role else None

        def _open_dashboard_role(role):
            def loader():
                self._set_active(None)
                self.nettoyer_corps()
                try:
                    # exclusive mapping for role buttons as well:
                    if role == "admin":
                        if FormulaireGraphiquesDesign:
                            FormulaireGraphiquesDesign(self.content_inner)
                            return
                        self._show_dashboard_missing("admin")
                        return

                    if role == "manager":
                        if build_metrics_panel:
                            self._open_metrics(build_metrics_panel)
                            return
                        self._show_dashboard_missing("manager")
                        return

                    if role == "agent":
                        if build_dashboard_overview:
                            build_dashboard_overview(self.content_inner, contrib_id=self._get_first_contrib_id(), low_threshold=5, role_filter="agent")
                            return
                        self._show_dashboard_missing("agent")
                        return

                    self.ouvrir_graphiques()
                except Exception as e:
                    try:
                        for ch in list(self.error_frame.winfo_children()):
                            ch.destroy()
                    except Exception:
                        pass
                    try:
                        lbl = tk.Label(self.error_frame, text=f"Erreur dashboard {role}: {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font, wraplength=800, justify="left")
                        lbl.grid(row=0, column=0, sticky="nw", padx=20, pady=20)
                    except Exception:
                        try:
                            tk.Label(self.content_inner, text=f"Erreur dashboard {role}: {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font).pack(padx=20, pady=20)
                        except Exception:
                            pass
            return loader

        # show only the dashboard buttons relevant to user's role (keeps UI simple & avoids confusion)
        if current_role == "admin":
            btn_dash_admin = tk.Button(inner, text="🏛️ Dashboard Admin", anchor="w", bd=0, bg=COULEUR_MENU_LATERAL, fg="white",
                                       activebackground=COULEUR_MENU_SURVOL, font=("Segoe UI", 13, "bold"),
                                       command=lambda: self._open_in_content(_open_dashboard_role("admin")))
            btn_dash_admin.pack(fill="x", padx=12, pady=(8, 6))
            btn_dash_admin.bind("<Enter>", lambda e: btn_dash_admin.config(bg=COULEUR_MENU_SURVOL))
            btn_dash_admin.bind("<Leave>", lambda e: btn_dash_admin.config(bg=COULEUR_MENU_LATERAL))

        if current_role == "manager":
            btn_dash_manager = tk.Button(inner, text="🏢 Dashboard Manager", anchor="w", bd=0, bg=COULEUR_MENU_LATERAL, fg="white",
                                         activebackground=COULEUR_MENU_SURVOL, font=("Segoe UI", 13, "bold"),
                                         command=lambda: self._open_in_content(_open_dashboard_role("manager")))
            btn_dash_manager.pack(fill="x", padx=12, pady=(8, 6))
            btn_dash_manager.bind("<Enter>", lambda e: btn_dash_manager.config(bg=COULEUR_MENU_SURVOL))
            btn_dash_manager.bind("<Leave>", lambda e: btn_dash_manager.config(bg=COULEUR_MENU_LATERAL))

        if current_role == "agent" or current_role is None:
            btn_dash_agent = tk.Button(inner, text="👷 Dashboard Agent", anchor="w", bd=0, bg=COULEUR_MENU_LATERAL, fg="white",
                                       activebackground=COULEUR_MENU_SURVOL, font=("Segoe UI", 13, "bold"),
                                       command=lambda: self._open_in_content(_open_dashboard_role("agent")))
            btn_dash_agent.pack(fill="x", padx=12, pady=(8, 6))
            btn_dash_agent.bind("<Enter>", lambda e: btn_dash_agent.config(bg=COULEUR_MENU_SURVOL))
            btn_dash_agent.bind("<Leave>", lambda e: btn_dash_agent.config(bg=COULEUR_MENU_LATERAL))

        # menus (clients, factures, articles, etc.)
        create_menu("Clients", "👥", [
            ("Lister les clients", afficher_liste_clients, "clients_view"),
        ])

        create_menu("Factures", "📄", [
            ("Lister les factures", afficher_liste_factures, "factures_view"),
            ("Créer une facture avec stock ", afficher_formulaire_facture, "factures_declare_create"),
            ("Créer une facture sans stock ", afficher_formulaire_facture_manual, "factures_nondeclare_create"),
        ])

        create_menu("Articles", "📦", [
            ("Articles déclarés réuissis", show_obr_articles, "articles_reuissi_view"),
            ("Articles déclarés non réuissis", show_failed_articles, "articles_echec_view"),
            ("Entrée & déclaration", formulaire_entree_et_declaration, "articles_create"),
        ])

        create_menu("Articles Importés", "📥", [
            ("Articles Importes déclarés réuissis", show_obr_articles_import, "articles_create"),
            ("Articles Importes déclarés echec", FailedImportsFrame, "articles_create"),
            ("Déclarés Articles Importes", lambda parent=None: self._open_import_batch(parent), "articles_import_create"),
        ])

        create_menu("Utilisateurs", "👤", [
            ("Lister les utilisateurs", afficher_tableau_utilisateurs, "utilisateurs_view"),
            ("Créer un utilisateur", afficher_formulaire_utilisateur_societe, "utilisateurs_create"),
        ])

        # finalize permissions and set initial enable/disable
        try:
            all_perms = set(self._all_permissions)
            self.PERMISSIONS_PAR_ROLE["admin"] = set(all_perms)
            self.PERMISSIONS_PAR_ROLE["manager"] = set(all_perms)
            allowed_for_agent = set(p for p in all_perms if p.startswith("clients_") or p.startswith("factures_") or p in ("dashboard",))
            self.PERMISSIONS_PAR_ROLE["agent"] = allowed_for_agent

            try:
                if hasattr(self, "_cached_permissions_user"):
                    self._cached_permissions_user = None
                    self._cached_permissions = None
                for title, (frame, _) in self._menus.items():
                    for w in frame.winfo_children():
                        try:
                            perm = getattr(w, "_perm_key", None)
                            if perm:
                                if self._a_permission(perm):
                                    w.config(state="normal")
                                else:
                                    w.config(state="disabled")
                            else:
                                w.config(state="normal")
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            pass

    def _get_first_contrib_id(self):
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id FROM contribuable ORDER BY id LIMIT 1")
            r = cur.fetchone()
            conn.close()
            return r[0] if r else None
        except Exception:
            return None

    def _open_in_content(self, loader_callable):
        self._set_active(None)
        # clear previous content (except error_frame)
        try:
            for w in list(self.content_inner.winfo_children()):
                if w is self.error_frame:
                    continue
                try:
                    w.destroy()
                except Exception:
                    pass
        except Exception:
            pass

        # clear error_frame children
        try:
            for ch in list(self.error_frame.winfo_children()):
                try:
                    ch.destroy()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            loader_callable()
        except Exception as e:
            try:
                lbl_err = tk.Label(self.error_frame, text=f"Erreur ouverture vue : {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font, wraplength=800, justify="left")
                lbl_err.grid(row=0, column=0, sticky="nw", padx=20, pady=20)
            except Exception:
                try:
                    lbl_err = tk.Label(self.content_inner, text=f"Erreur ouverture vue : {e}", bg=COULEUR_CORPS_PRINCIPAL, fg="#900", font=self.submenu_font)
                    lbl_err.pack(padx=20, pady=20)
                except Exception:
                    pass
        try:
            self.content_canvas.yview_moveto(0)
        except Exception:
            pass

    def ouvrir_graphiques(self, parent=None):
        self._set_active(None)
        self.nettoyer_corps()
        try:
            try:
                from gui.form_graficas_design import FormulaireGraphiquesDesign as _FGD
            except Exception:
                _FGD = None

            if _FGD:
                _FGD(self.content_inner)
            else:
                lbl = tk.Label(self.error_frame, text="Dashboard (à implémenter)", bg=COULEUR_CORPS_PRINCIPAL, font=self.title_font)
                try:
                    lbl.grid(row=0, column=0, sticky="nw", padx=20, pady=20)
                except Exception:
                    lbl.pack(padx=20, pady=20)
        except Exception:
            try:
                lbl = tk.Label(self.error_frame, text="Graphiques indisponible", bg=COULEUR_CORPS_PRINCIPAL, font=self.title_font)
                lbl.grid(row=0, column=0, sticky="nw", padx=20, pady=20)
            except Exception:
                tk.Label(self.content_inner, text="Graphiques indisponible", bg=COULEUR_CORPS_PRINCIPAL, font=self.title_font).pack(padx=20, pady=20)
        try:
            self.content_canvas.yview_moveto(0)
        except Exception:
            pass

    def _open_import_batch(self, parent=None):
        parent = parent or self.content_inner
        self.nettoyer_corps()
        frame = ImportStockBatchFrame(parent,
                                      get_connection_fn=get_connection,
                                      obtenir_token_fn=obtenir_token_auto,
                                      get_system_id_fn=get_system_id,
                                      contribuable_id=self._get_first_contrib_id())
        frame.pack(fill="both", expand=True, padx=12, pady=12)

    def nettoyer_corps(self):
        for w in list(self.content_inner.winfo_children()):
            if w is self.error_frame:
                continue
            try:
                w.destroy()
            except Exception:
                pass

    def _set_active(self, btn):
        if self._active_button and isinstance(self._active_button, tk.Button):
            try:
                self._active_button.config(bg=COULEUR_MENU_LATERAL)
            except Exception:
                pass
        self._active_button = btn
        if btn and isinstance(btn, tk.Button):
            try:
                btn.config(bg=COULEUR_MENU_SURVOL)
            except Exception:
                pass

    def toggle_menu(self):
        if self._sidebar_visible:
            try:
                self.sidebar_container.forget()
            except Exception:
                try:
                    self.sidebar_container.pack_forget()
                except Exception:
                    pass
            try:
                self.content_container.pack_forget()
                self.content_container.pack(side="left", fill="both", expand=True)
            except Exception:
                pass
            self._sidebar_visible = False
        else:
            try:
                self.sidebar_container.pack(side="left", fill="y")
            except Exception:
                pass
            try:
                self.content_container.pack_forget()
                self.content_container.pack(side="right", fill="both", expand=True)
            except Exception:
                pass
            self._sidebar_visible = True
        try:
            if hasattr(self.controller, "update"):
                self.controller.update()
            else:
                self.update_idletasks()
        except Exception:
            pass

    def _on_logout_button(self):
        if not messagebox.askyesno("Déconnexion", "Êtes-vous sûr de vouloir vous déconnecter ?"):
            return
        try:
            if hasattr(global_session, "end_session"):
                global_session.end_session()
        except Exception:
            pass

        try:
            if hasattr(self.controller, "destroy_view"):
                self.controller.destroy_view("MainView")
        except Exception:
            pass

        if callable(self.on_logout):
            try:
                self.on_logout()
            except Exception:
                pass

        try:
            if hasattr(self.controller, "show_view"):
                self.controller.show_view("LoginView")
            else:
                try:
                    self.controller.destroy()
                except Exception:
                    pass
        except Exception:
            try:
                self.controller.destroy()
            except Exception:
                pass

    def _check_network_once(self, timeout=2) -> bool:
        try:
            sock = socket.create_connection(("8.8.8.8", 53), timeout=timeout)
            sock.close()
            return True
        except Exception:
            return False

    def _start_network_checker(self, interval=10):
        def worker():
            while True:
                try:
                    ok = self._check_network_once(timeout=2)
                    if ok != self._network_ok:
                        self._network_ok = ok
                        self.after(0, self._update_network_label)
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _update_network_label(self):
        if not hasattr(self, "_network_label") or self._network_label is None:
            return
        try:
            if self._network_ok:
                self._network_label.config(text="Connexion internet est disponible", fg="#ffffff", bg="#2e7d32")
                self._network_label.master.config(bg=COULEUR_BARRE_SUPERIEURE)
                self._network_label.config(relief="raised")
            else:
                self._network_label.config(text="Connexion internet est indisponible", fg="#3a2f00", bg="#ffca28")
                self._network_label.master.config(bg=COULEUR_BARRE_SUPERIEURE)
                self._network_label.config(relief="raised")
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    try:
        if apply_tk_theme:
            apply_tk_theme(root)
        if apply_matplotlib_theme:
            apply_matplotlib_theme()
    except Exception:
        pass

    mv = MainView(root, root)
    mv.pack(fill="both", expand=True)
    root.geometry("1200x760")
    root.mainloop()
