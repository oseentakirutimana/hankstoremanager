import tkinter as tk
from tkinter import ttk, messagebox
from database.connection import get_connection
from datetime import datetime
import re
import hashlib

CONTENT_BG      = "#f4f7f9"
FORM_BG         = "white"
LABEL_FG        = "#495057"
ENTRY_BG        = "white"
ENTRY_BORDER    = "#ced4da"
ENTRY_FOCUS     = "#80bdff"
BUTTON_BG       = "#007bff"
BUTTON_ACTIVE   = "#0056b3"
BUTTON_FG       = "white"
BTN_HOVER_BG    = "#0069d9"

ENTRY_WIDTH = 75

style = ttk.Style()
try:
    style.theme_use("clam")
    style.configure("Custom.TCombobox",
                    fieldbackground=ENTRY_BG,
                    background=ENTRY_BG,
                    foreground=LABEL_FG,
                    padding=(5, 5))
    style.map("Custom.TCombobox",
              fieldbackground=[('readonly', ENTRY_BG)],
              selectbackground=[('readonly', ENTRY_BG)],
              selectforeground=[('readonly', LABEL_FG)])
except Exception:
    pass

try:
    import bcrypt  # type: ignore
    _HAS_BCRYPT = True
except Exception:
    _HAS_BCRYPT = False

def _hash_password(pw: str) -> str:
    if not pw:
        return ""
    if _HAS_BCRYPT:
        hashed = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")
    else:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def _get_contribuables_list():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, tp_name FROM contribuable ORDER BY tp_name")
        rows = cur.fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        return []

def afficher_formulaire_utilisateur_societe(parent):
    for w in parent.winfo_children():
        w.destroy()
    try:
        parent.configure(bg=CONTENT_BG)
    except Exception:
        pass

    wrapper = tk.Frame(parent, bg=CONTENT_BG, padx=30, pady=25)
    wrapper.pack(fill="both", expand=True)

    inner_frame = tk.Frame(wrapper, bg=CONTENT_BG)
    inner_frame.pack(padx=20, pady=10)

    title = tk.Label(inner_frame, text="👥 Ajouter / Modifier utilisateur société",
                     font=("Segoe UI", 20, "bold"),
                     bg=CONTENT_BG, fg=LABEL_FG)
    title.pack(anchor="w", pady=(0,15))

    form = tk.Frame(inner_frame,
                    bg=FORM_BG,
                    bd=1,
                    relief="flat",
                    highlightbackground=ENTRY_BORDER,
                    highlightthickness=1,
                    padx=24, pady=24)
    form.pack(fill="x", padx=0, pady=10)

    form.grid_columnconfigure(0, weight=0)
    form.grid_columnconfigure(1, weight=1)

    labels = [
        ("Nom complet", "nom"),
        ("Nom d'utilisateur", "username"),
        ("Mot de passe", "password"),
        ("Confirmer mot de passe", "password_confirm"),
    ]
    vars_map = {}
    entries = {}

    ENTRY_STYLE_KW = {
        "bg": ENTRY_BG,
        "relief": "flat",
        "bd": 1,
        "highlightthickness": 1,
        "highlightbackground": ENTRY_BORDER,
        "highlightcolor": ENTRY_FOCUS,
        "insertbackground": LABEL_FG,
        "font": ("Segoe UI", 11),
        "fg": LABEL_FG
    }

    def on_focus_in(event):
        try:
            event.widget.config(highlightcolor=ENTRY_FOCUS)
        except Exception:
            pass

    def on_focus_out(event):
        try:
            event.widget.config(highlightcolor=ENTRY_BORDER)
        except Exception:
            pass

    for i, (lbl, key) in enumerate(labels):
        tk.Label(form,
                 text=lbl + " :",
                 font=("Segoe UI", 11, "bold"),
                 bg=FORM_BG, fg=LABEL_FG).grid(row=i, column=0, sticky="w", padx=10, pady=10)

        var = tk.StringVar()
        if "password" in key:
            ent = tk.Entry(form, textvariable=var, show="●", width=ENTRY_WIDTH, **ENTRY_STYLE_KW)
        else:
            ent = tk.Entry(form, textvariable=var, width=ENTRY_WIDTH, **ENTRY_STYLE_KW)

        ent.grid(row=i, column=1, sticky="ew", padx=10, pady=10)
        ent.bind("<FocusIn>", on_focus_in)
        ent.bind("<FocusOut>", on_focus_out)

        vars_map[key] = var
        entries[key] = ent

    # Show/Hide password control (affecte both password fields)
    def _toggle_password_visibility():
        nonlocal _pw_visible
        _pw_visible = not _pw_visible
        ch = "" if _pw_visible else "●"
        entries["password"].config(show=ch)
        entries["password_confirm"].config(show=ch)
        btn_show_pw.config(text="Masquer" if _pw_visible else "Afficher")

    _pw_visible = False
    btn_show_pw = tk.Button(form, text="Afficher", width=10, command=_toggle_password_visibility, bg="#e9ecef", fg=LABEL_FG)
    # place the button to the right of the confirm password entry
    btn_show_pw.grid(row=3, column=2, sticky="w", padx=(6,0), pady=10)

    tk.Label(form, text="Rôle :", font=("Segoe UI", 11, "bold"), bg=FORM_BG, fg=LABEL_FG).grid(row=4, column=0, sticky="w", padx=10, pady=10)
    role_var = tk.StringVar(value="manager")
    # nouveaux rôles demandés : manager, admin, agent
    role_cb = ttk.Combobox(form, textvariable=role_var, values=["manager", "admin", "agent"], state="readonly", width=ENTRY_WIDTH, style="Custom.TCombobox")
    role_cb.grid(row=4, column=1, sticky="ew", padx=10, pady=10)
    role_cb.bind("<FocusIn>", on_focus_in)
    role_cb.bind("<FocusOut>", on_focus_out)

    tk.Label(form, text="Contribuable (optionnel) :", font=("Segoe UI", 11, "bold"), bg=FORM_BG, fg=LABEL_FG).grid(row=5, column=0, sticky="w", padx=10, pady=10)
    contribuables = _get_contribuables_list()
    contrib_options_display = [""] + [f"{c[1]} (id:{c[0]})" for c in contribuables]
    contrib_map = {f"{c[1]} (id:{c[0]})": c[0] for c in contribuables}
    contrib_var = tk.StringVar(value="")
    contrib_cb = ttk.Combobox(form, textvariable=contrib_var, values=contrib_options_display, state="readonly", width=ENTRY_WIDTH, style="Custom.TCombobox")
    contrib_cb.grid(row=5, column=1, sticky="ew", padx=10, pady=10)
    contrib_cb.bind("<FocusIn>", on_focus_in)
    contrib_cb.bind("<FocusOut>", on_focus_out)

    note = tk.Label(form, text="*(laisser vide pour un utilisateur non lié à un contribuable)*", font=("Segoe UI", 9, "italic"), bg=FORM_BG, fg=LABEL_FG)
    note.grid(row=6, column=1, sticky="w", padx=10, pady=(0,10))

    btn_frame = tk.Frame(inner_frame, bg=CONTENT_BG)
    btn_frame.pack(fill="x", pady=(20,0))

    def on_enter(e):
        e.widget['background'] = BTN_HOVER_BG
    def on_leave(e):
        e.widget['background'] = BUTTON_BG

    save_btn = tk.Button(btn_frame,
                         text="💾 Enregistrer utilisateur",
                         bg=BUTTON_BG, fg=BUTTON_FG, activebackground=BUTTON_ACTIVE,
                         relief="flat", bd=0, cursor="hand2",
                         font=("Segoe UI", 12, "bold"), padx=15, pady=8)
    save_btn.pack(side="left")
    save_btn.bind("<Enter>", on_enter)
    save_btn.bind("<Leave>", on_leave)

    def _validate_inputs(data: dict):
        if not data["nom"]:
            return False, "Le nom complet est requis."
        if not data["username"]:
            return False, "Le nom d'utilisateur est requis."
        if not re.match(r"^[A-Za-z0-9_.-]{3,50}$", data["username"]):
            return False, "Username invalide : 3-50 chars (lettres, chiffres, _ . -)."
        if data.get("require_password", True):
            if not data["password"]:
                return False, "Le mot de passe est requis."
            if data["password"] != data["password_confirm"]:
                return False, "Les deux mots de passe ne correspondent pas."
            if len(data["password"]) < 6:
                return False, "Le mot de passe doit contenir au moins 6 caractères."
        else:
            if data["password"] or data["password_confirm"]:
                if data["password"] != data["password_confirm"]:
                    return False, "Les deux mots de passe ne correspondent pas."
                if len(data["password"]) < 6:
                    return False, "Le mot de passe doit contenir au moins 6 caractères."
        if data["role"] not in ("manager", "admin", "agent"):
            return False, "Rôle invalide."
        return True, ""

    def _insert_user(conn, data: dict):
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO utilisateur_societe (nom, username, password, role, contribuable_id)
            VALUES (?, ?, ?, ?, ?)
        """, (data["nom"], data["username"], data["password_hash"], data["role"], data["contribuable_id"]))
        conn.commit()
        return cur.lastrowid

    def _update_user(conn, user_id, data: dict):
        update_fields = ["nom = ?", "role = ?", "contribuable_id = ?"]
        update_params = [data["nom"], data["role"], data["contribuable_id"]]
        if data.get("password_hash"):
            update_fields.append("password = ?")
            update_params.append(data["password_hash"])
        update_params.append(user_id)
        cur = conn.cursor()
        query = f"UPDATE utilisateur_societe SET {', '.join(update_fields)} WHERE id = ?"
        cur.execute(query, tuple(update_params))
        conn.commit()

    def verifier_et_enregistrer():
        save_btn.config(state="disabled")
        try:
            payload = {
                "nom": vars_map["nom"].get().strip(),
                "username": vars_map["username"].get().strip(),
                "password": vars_map["password"].get(),
                "password_confirm": vars_map["password_confirm"].get(),
                "role": role_var.get(),
                "contrib_selection": contrib_var.get().strip()
            }
            payload["contribuable_id"] = contrib_map.get(payload["contrib_selection"]) if payload["contrib_selection"] else None

            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, nom FROM utilisateur_societe WHERE username = ? LIMIT 1", (payload["username"],))
            existing = cur.fetchone()

            is_update = bool(existing)
            payload["require_password"] = not is_update

            ok, msg = _validate_inputs(payload)
            if not ok:
                messagebox.showerror("Validation", msg)
                conn.close()
                return

            if payload["password"]:
                payload["password_hash"] = _hash_password(payload["password"])
            else:
                payload["password_hash"] = None

            if is_update:
                user_id = existing[0]
                if not messagebox.askyesno("Confirmation", f"Le nom d'utilisateur '{payload['username']}' existe déjà. Mettre à jour cet utilisateur ?\n(Le mot de passe sera conservé s'il n'est pas modifié.)"):
                    conn.close()
                    return
                _update_user(conn, user_id, payload)
                messagebox.showinfo("Succès", "Utilisateur mis à jour avec succès ✅")
            else:
                if not payload.get("password_hash"):
                    payload["password_hash"] = _hash_password(payload["password"])
                _insert_user(conn, payload)
                messagebox.showinfo("Succès", "Utilisateur créé avec succès ✅")

            conn.close()

            for v in vars_map.values():
                v.set("")
            role_var.set("manager")
            contrib_var.set("")

        except Exception as e:
            messagebox.showerror("Erreur", f"Échec enregistrement : {e}")
        finally:
            save_btn.config(state="normal")

    save_btn.config(command=verifier_et_enregistrer)

    try:
        entries["nom"].focus_set()
    except Exception:
        pass

    parent.update_idletasks()
