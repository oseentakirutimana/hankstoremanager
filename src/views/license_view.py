# window_obr_license.py
import sys
import os
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

# Assurez-vous que ces chemins sont corrects dans votre projet
try:
    from config import OBR_ENV_PATH
    from utils.util_ventana import centrar_ventana
    from gui.window_obr_indent import open_obr_inv_editor
except Exception:
    # Fallbacks légers pour tests locaux
    OBR_ENV_PATH = "obr.env"

    def centrar_ventana(ctrl, w, h):
        try:
            ctrl.geometry(f"{w}x{h}")
        except Exception:
            pass

    def open_obr_inv_editor(ctrl, inv_path):
        messagebox.showinfo("Simu", f"Ouverture éditeur OBR pour {inv_path}")

# Import des fonctions de gestion de clés
try:
    from models.key_manager_sqlite import (
        init_db,
        consume_encrypted_input,
        decrypt_key,
        consume_key_plain,
    )
except Exception:
    # fallback minimal pour test sans DB
    def init_db(path: Optional[str] = None):
        pass

    def consume_encrypted_input(token: str, used_by: Optional[str] = None, path: Optional[str] = None) -> bool:
        return False

    def decrypt_key(token: str) -> Optional[str]:
        return None

    def consume_key_plain(key: str, used_by: Optional[str] = None, path: Optional[str] = None) -> bool:
        return False

# Initialiser la DB si nécessaire
try:
    init_db()
except Exception:
    pass

# Coordonnées de contact
_SUPPORT_WHATSAPP = "+257 61 366 672"
_SUPPORT_EMAIL = "cichahayoosee@gmail.com"


class LicenseView(tk.Frame):
    """
    Vue d'activation :
    - Accepte tokens Fernet (commencent par 'gAAAA...' / 'GAAAA...')
    - Accepte clés lisibles en clair (ex: KEY-AB3DF-K9T2P-4MZQ1-7VN6R)
    - Auto-prefixe 'KEY-' si l'utilisateur omet le préfixe
    - Normalise la saisie (sans espaces, majuscules) et limite la longueur pour les clés lisibles
    """

    # Configuration du format d'activation lisible
    ACTIVATION_PREFIX = "KEY"  # chaîne préfixe (mettez None si pas de préfixe)
    ACTIVATION_GROUPS = 4  # ex: 4 groupes
    ACTIVATION_GROUP_LEN = 5  # ex: 5 caractères par groupe

    def __init__(self, parent, controller, **kwargs):
        super().__init__(parent, bg="#f0f2f5")
        self.controller = controller
        self._success_anim_id = None
        self._anim_bar = None
        self._entry_var = tk.StringVar(value="")

        # calculer longueur max pour clé lisible (avec tirets et préfixe)
        groups = max(1, int(self.ACTIVATION_GROUPS))
        glen = max(1, int(self.ACTIVATION_GROUP_LEN))
        hyphens = groups - 1 if groups > 1 else 0
        base_len = groups * glen + hyphens
        prefix = (self.ACTIVATION_PREFIX or "").strip()
        prefix_len = (len(prefix) + 1) if prefix else 0  # +1 pour le '-'
        self._max_activation_len = base_len + prefix_len

        self._build_ui()

    def _build_ui(self):
        centrar_ventana(self.controller, 620, 240)
        card = tk.Frame(self, bg="white", padx=20, pady=16)
        card.place(relx=0.5, rely=0.5, anchor="c")

        tk.Label(card, text="Activation de l'application", font=("Segoe UI", 14), bg="white").pack(pady=(0, 8))

        self.entry = ttk.Entry(card, font=("Segoe UI", 12), width=60, textvariable=self._entry_var)
        self.entry.pack(pady=(0, 8))
        # trace_add disponible sur Tk 8.6+, fallback to trace if necessary
        try:
            self._entry_var.trace_add("write", self._on_entry_change)
        except Exception:
            try:
                self._entry_var.trace("w", lambda *args: self._on_entry_change())
            except Exception:
                pass

        self._status_label = tk.Label(card, text="", bg="white", fg="#333", font=("Segoe UI", 10))
        self._status_label.pack(pady=(0, 8))

        btn = tk.Button(
            card,
            text="Valider",
            bg="#1e90ff",
            fg="white",
            bd=0,
            command=self._on_valider,
            font=("Segoe UI", 11, "bold"),
        )
        btn.pack(fill="x")
        self.entry.bind("<Return>", lambda e: self._on_valider())

        # Hint sur le format attendu
        prefix = f"{self.ACTIVATION_PREFIX + '-'}" if self.ACTIVATION_PREFIX else ""
        hint_parts = ["X" * int(self.ACTIVATION_GROUP_LEN) for _ in range(int(self.ACTIVATION_GROUPS))]
        hint = f"Format attendu: {prefix}{'-'.join(hint_parts)}"
        tk.Label(card, text=hint, bg="white", fg="#666", font=("Segoe UI", 9)).pack(pady=(6, 0))

    def _on_entry_change(self, *_):
        """
        Normalisation et limitation de l'entrée :
        - supprime espaces
        - met en majuscule
        - si token Fernet probable (commence par GAAAA) : n'applique pas de troncature
        - sinon tronque à self._max_activation_len
        """
        try:
            val = self._entry_var.get()
            if not isinstance(val, str):
                return
            normalized = val.replace(" ", "").upper()

            # If likely a Fernet token, allow long input
            if normalized.startswith("GAAAA"):
                new = normalized
            else:
                if len(normalized) > self._max_activation_len:
                    new = normalized[: self._max_activation_len]
                else:
                    new = normalized

            # set only if changed to avoid recursion
            if new != val:
                # try to preserve cursor position by resetting value
                self._entry_var.set(new)
        except Exception:
            pass

    def _on_valider(self):
        raw = self._entry_var.get().strip()
        if not raw:
            messagebox.showerror("Erreur", "Veuillez saisir votre clé d'activation.")
            return

        # Normalize input
        inp = raw.replace(" ", "").upper()

        # audit user (fallback)
        try:
            used_by = getattr(__import__("models.session", fromlist=["session"]).session, "username", "admin_Hosea_Ntaki")
        except Exception:
            used_by = "admin_Hosea_Ntaki"

        ok = False

        try:
            # Case 1: Fernet token (starts with gAAAA / GAAAA)
            if inp.startswith("GAAAA"):
                # verify decryptable
                plain = decrypt_key(inp)
                if not plain:
                    self._show_contact_error()
                    return
                ok = consume_encrypted_input(inp, used_by=used_by)

            # Case 2: Plain activation key
            else:
                prefix = (self.ACTIVATION_PREFIX or "").upper()
                if prefix:
                    if not inp.startswith(prefix + "-"):
                        inp_prefixed = f"{prefix}-{inp}"
                    else:
                        inp_prefixed = inp
                else:
                    inp_prefixed = inp

                # Reject overly long plain keys
                if len(inp_prefixed) > self._max_activation_len:
                    self._show_contact_error()
                    return

                ok = consume_key_plain(inp_prefixed, used_by=used_by)

        except Exception as e:
            # debug print; en prod utilise logging
            try:
                print(f"[LicenseView] erreur consommation clé: {e}")
            except Exception:
                pass
            ok = False

        if ok:
            self._show_success_animation(then=self._on_success_next)
        else:
            self._show_contact_error()

    def _show_contact_error(self):
        msg = (
            "Clé saisie invalide, déjà utilisée ou expirée.\n\n"
            "Veuillez nous contacter pour assistance :\n"
            f"WhatsApp : {_SUPPORT_WHATSAPP}\n"
            f"E-mail : {_SUPPORT_EMAIL}\n\n"
            "Merci."
        )
        messagebox.showerror("Erreur d'activation", msg)

    def _show_success_animation(self, then=None):
        try:
            self._status_label.config(text="Validation réussie — ouverture en cours...", fg="#0b6623", font=("Segoe UI", 10, "bold"))
            if self._anim_bar:
                try:
                    self._anim_bar.destroy()
                except Exception:
                    pass

            # place progressbar inside a small frame anchored to this widget
            try:
                container = self
                self._anim_bar = ttk.Progressbar(container, mode="determinate", maximum=100)
                # place under status label (approximate placement)
                self._anim_bar.place(relx=0.5, rely=0.75, anchor="c", width=360, height=12)
                self._anim_bar["value"] = 0
            except Exception:
                self._anim_bar = None

            steps = 20
            delay = 45  # ms between steps

            def step(i=0):
                try:
                    val = int((i / steps) * 100)
                    if self._anim_bar:
                        self._anim_bar["value"] = val
                    if i < steps:
                        self._success_anim_id = self.after(delay, lambda: step(i + 1))
                    else:
                        if self._anim_bar:
                            try:
                                self._anim_bar.destroy()
                            except Exception:
                                pass
                        self._status_label.config(text="Prêt", fg="#075e3b", font=("Segoe UI", 10, "bold"))
                        if callable(then):
                            then()
                except Exception:
                    if callable(then):
                        then()

            step(0)
        except Exception:
            if callable(then):
                then()

    def _on_success_next(self):
        # open editor then return to LoginView
        try:
            open_obr_inv_editor(self.controller, inv_path=OBR_ENV_PATH)
        except Exception:
            pass
        try:
            if hasattr(self.controller, "show_view"):
                self.controller.show_view("LoginView")
            else:
                if hasattr(self.controller, "destroy"):
                    self.controller.destroy()
        except Exception:
            try:
                if hasattr(self.controller, "destroy"):
                    self.controller.destroy()
            except Exception:
                pass

    def destroy(self):
        try:
            if self._success_anim_id:
                self.after_cancel(self._success_anim_id)
        except Exception:
            pass
        try:
            if self._anim_bar:
                self._anim_bar.destroy()
        except Exception:
            pass
        return super().destroy()
