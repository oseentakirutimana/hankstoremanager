# tableau_utilisateurs.py
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Dict, Tuple, List
from database.connection import get_connection
import hashlib
import re
import sqlite3

# UI constants
CONTENT_BG = "#f6f8fa"
CARD_BG = "#ffffff"
CONTOUR_BG = "#e6eef9"
HEADER_BG = "#eef6ff"
HEADER_FG = "#0b3d91"
LABEL_FG = "#1f2937"
ROW_BG_1 = CARD_BG
ROW_BG_2 = "#fbfdff"
ENTRY_BG = "white"
ENTRY_BORDER = "#ced4da"
BTN_FG = "white"
FONT_CELL = ("Segoe UI", 9)
FONT_LABEL = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI", 14, "bold")

BTN_VIEW_BG = "#6c757d"
BTN_EDIT_BG = "#007bff"
BTN_DELETE_BG = "#dc3545"

ROLES = ["manager", "admin", "agent"]

COLUMNS = [
    ("id", "ID", 40),
    ("nom", "Nom complet", 200),
    ("username", "Nom utilisateur", 150),
    ("role", "Rôle", 80),
    ("contribuable_id", "Contribuable ID", 120),
]

ROW_HEIGHT = 32
PAGE_SIZES = [10, 15, 20, 50]
default_page_size = 15

try:
    import bcrypt  # type: ignore
    _HAS_BCRYPT = True
except Exception:
    _HAS_BCRYPT = False

def _hash_password(pw: str) -> str:
    if not pw:
        return ""
    if _HAS_BCRYPT:
        return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def _center_window(win: tk.Toplevel, parent_widget: tk.Widget, prefer_w: Optional[int] = None, prefer_h: Optional[int] = None):
    try:
        parent_widget.update_idletasks()
        win.update_idletasks()

        px = parent_widget.winfo_rootx()
        py = parent_widget.winfo_rooty()
        pw = parent_widget.winfo_width()
        ph = parent_widget.winfo_height()

        req_w = win.winfo_reqwidth() or 0
        req_h = win.winfo_reqheight() or 0

        default_view_w, default_view_h = 1100, 700
        default_edit_w, default_edit_h = 1280, 900

        title = (win.title() or "").lower()
        if title.startswith("voir") or "voir" in title:
            ww = prefer_w or req_w or default_view_w
            wh = prefer_h or req_h or default_view_h
        else:
            ww = prefer_w or req_w or default_edit_w
            wh = prefer_h or req_h or default_edit_h

        screen_w = parent_widget.winfo_screenwidth()
        screen_h = parent_widget.winfo_screenheight()
        ww = min(max(300, ww), screen_w - 40)
        wh = min(max(200, wh), screen_h - 40)

        try:
            max_w = max(300, pw - 40)
            max_h = max(200, ph - 40)
            if ww > max_w:
                ww = max_w
            if wh > max_h:
                wh = max_h
        except Exception:
            pass

        x = px + max(0, (pw - ww) // 2)
        y = py + max(0, (ph - wh) // 2)

        win.geometry(f"{ww}x{wh}+{x}+{y}")
    except Exception:
        try:
            sw = parent_widget.winfo_screenwidth()
            sh = parent_widget.winfo_screenheight()
            fw = prefer_w or 900
            fh = prefer_h or 600
            fx = max(0, (sw - fw) // 2)
            fy = max(0, (sh - fh) // 2)
            win.geometry(f"{fw}x{fh}+{fx}+{fy}")
        except Exception:
            pass

# ----- DB helpers -----
def _fetch_users_page(filter_text=None, page=1, page_size=15):
    conn = get_connection()
    cur = conn.cursor()
    cols = [c[0] for c in COLUMNS]
    sql_cols = ", ".join(cols)
    where_clauses = []
    params = []

    # n'afficher que les rôles souhaités et exclure 'superuser'
    allowed_roles = ("admin", "manager", "agent")
    where_clauses.append("role IN ({roles})".format(roles=",".join("?" for _ in allowed_roles)))
    params.extend(allowed_roles)
    where_clauses.append("LOWER(COALESCE(role,'')) != 'superuser'")

    # filtre texte sur nom ou username si fourni
    if filter_text:
        where_clauses.append("(nom LIKE ? OR username LIKE ?)")
        q = f"%{filter_text}%"
        params.extend([q, q])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # total
    cur.execute(f"SELECT COUNT(1) FROM utilisateur_societe {where_sql}", tuple(params))
    total = cur.fetchone()[0]

    offset = (page - 1) * page_size

    # récupérer page
    cur.execute(
        f"SELECT {sql_cols} FROM utilisateur_societe {where_sql} ORDER BY nom LIMIT ? OFFSET ?",
        tuple(params) + (page_size, offset)
    )
    rows = cur.fetchall()

    try:
        desc = [d[0] for d in cur.description]
        data = [dict(zip(desc, row)) for row in rows]
    except Exception:
        data = rows

    conn.close()
    return data, total


def _fetch_user_by_id(user_id: int) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, nom, username, password, role, contribuable_id FROM utilisateur_societe WHERE id = ? LIMIT 1", (user_id,))
    row = cur.fetchone()
    try: conn.close()
    except Exception: pass
    if not row:
        return None
    try:
        cols = [c[0] for c in cur.description]
        return {cols[i]: row[i] for i in range(len(cols))}
    except Exception:
        return {"id": row[0], "nom": row[1], "username": row[2], "password": row[3], "role": row[4], "contribuable_id": row[5]}

def _update_user_db(user_id: int, nom: str, password_hash: Optional[str], role: str, contribuable_id: Optional[int]):
    conn = get_connection()
    cur = conn.cursor()
    fields = ["nom = ?", "role = ?", "contribuable_id = ?"]
    params = [nom, role, contribuable_id]
    if password_hash:
        fields.append("password = ?")
        params.append(password_hash)
    params.append(user_id)
    sql = f"UPDATE utilisateur_societe SET {', '.join(fields)} WHERE id = ?"
    cur.execute(sql, tuple(params))
    conn.commit()
    try: conn.close()
    except Exception: pass

def _delete_user_db(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM utilisateur_societe WHERE id = ?", (user_id,))
    conn.commit()
    try: conn.close()
    except Exception: pass

def _fetch_all_contribuables_ids() -> List[str]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM contribuable ORDER BY id")
        rows = cur.fetchall()
        return [str(r[0]) for r in rows if r and r[0] is not None]
    except Exception:
        return [str(i) for i in [1, 2, 3, 4, 5, 101, 999]]
    finally:
        try: conn.close()
        except Exception: pass

# ----- Modal view (lecture seule) -----
def _open_view_modal(center_widget: tk.Widget, user: Dict):
    dlg = tk.Toplevel(center_widget)
    dlg.title(f"🔍 Voir utilisateur — {user.get('username','')}")
    dlg.transient(center_widget)
    dlg.grab_set()

    root_frame = tk.Frame(dlg, bg=CARD_BG)
    root_frame.pack(fill="both", expand=True)

    canvas = tk.Canvas(root_frame, bg=CARD_BG, highlightthickness=0)
    sc = ttk.Scrollbar(root_frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=CARD_BG, padx=14, pady=14)

    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sc.set)

    canvas.pack(side="left", fill="both", expand=True)
    sc.pack(side="right", fill="y")

    fields = [("Nom complet", "nom"), ("Nom d'utilisateur", "username"), ("Rôle", "role"), ("Contribuable (id)", "contribuable_id")]
    for i, (label_text, key) in enumerate(fields):
        tk.Label(inner, text=label_text + " :", bg=CARD_BG, fg=LABEL_FG, font=("Segoe UI", 10, "bold")).grid(row=i, column=0, sticky="w", pady=8)
        val = user.get(key) if user else ""
        ent = tk.Entry(inner, width=80, state="readonly", readonlybackground="#f0f0f0", font=("Segoe UI", 10))
        ent.grid(row=i, column=1, sticky="w", pady=8, padx=(8,0))
        ent.configure(state="normal")
        ent.delete(0, "end")
        ent.insert(0, "" if val is None else str(val))
        ent.configure(state="readonly")

    btn_frame = tk.Frame(dlg, bg=CARD_BG, pady=8)
    btn_close = tk.Button(btn_frame, text="Fermer", bg=BTN_EDIT_BG, fg=BTN_FG, command=dlg.destroy)
    btn_close.pack(side="right", padx=8)
    btn_frame.pack(fill="x", side="bottom")

    def _on_config(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(inner_id, width=event.width)
    inner.bind("<Configure>", _on_config)

    dlg.update_idletasks()
    content_h = inner.winfo_reqheight() + btn_frame.winfo_reqheight() + 40
    content_w = max(700, inner.winfo_reqwidth() + 40)
    screen_h = center_widget.winfo_screenheight()
    screen_w = center_widget.winfo_screenwidth()

    min_h = 240
    max_h = int(screen_h * 0.85)
    final_h = min(max(content_h, min_h), max_h)
    final_w = min(max(content_w, 600), int(screen_w * 0.9))

    _center_window(dlg, center_widget, prefer_w=final_w, prefer_h=final_h)

# ----- Modal edit (prérempli) -----
def _open_edit_modal(center_widget: tk.Widget, user_id: int, on_saved_callback=None):
    user = _fetch_user_by_id(user_id)
    if not user:
        messagebox.showerror("Erreur", "Utilisateur introuvable", parent=center_widget)
        return

    dlg = tk.Toplevel(center_widget)
    dlg.title(f"✏️ Éditer utilisateur — {user.get('username','')}")
    dlg.transient(center_widget)
    dlg.grab_set()

    root_frame = tk.Frame(dlg, bg=CARD_BG)
    root_frame.pack(fill="both", expand=True)

    canvas = tk.Canvas(root_frame, bg=CARD_BG, highlightthickness=0)
    sc = ttk.Scrollbar(root_frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=CARD_BG, padx=14, pady=14)

    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sc.set)

    canvas.pack(side="left", fill="both", expand=True)
    sc.pack(side="right", fill="y")

    nom_var = tk.StringVar(value=user.get("nom") or "")
    username_var = tk.StringVar(value=user.get("username") or "")
    role_var = tk.StringVar(value=user.get("role") or ROLES[0])
    contrib_id_val = user.get("contribuable_id")
    contrib_var = tk.StringVar(value=str(contrib_id_val) if contrib_id_val else "")
    pw_var = tk.StringVar(value="")
    pw_confirm_var = tk.StringVar(value="")
    pw_visible = {"val": False}

    contrib_values = [""] + _fetch_all_contribuables_ids()
    if contrib_var.get() and contrib_var.get() not in contrib_values:
         contrib_values.append(contrib_var.get())

    labels = [
        ("Nom complet", nom_var, "entry"),
        ("Nom d'utilisateur", username_var, "entry_readonly"),
        ("Rôle", role_var, "role_combo"),
        ("Contribuable (id)", contrib_var, "contrib_combo"),
        ("Nouveau mot de passe", pw_var, "password"),
        ("Confirmer mot de passe", pw_confirm_var, "password_confirm")
    ]

    for i, (label_text, var, input_type) in enumerate(labels):
        tk.Label(inner, text=label_text + " :", bg=CARD_BG, fg=LABEL_FG, font=("Segoe UI", 10, "bold")).grid(row=i, column=0, sticky="w", pady=8)

        if input_type == "role_combo":
            cb = ttk.Combobox(inner, textvariable=var, values=ROLES, state="readonly", width=64, font=FONT_LABEL)
            cb.grid(row=i, column=1, sticky="w", pady=8)
        elif input_type == "contrib_combo":
            cb = ttk.Combobox(inner, textvariable=var, values=contrib_values, state="normal", width=64, font=FONT_LABEL)
            cb.grid(row=i, column=1, sticky="w", pady=8)
        elif input_type in ("password", "password_confirm"):
            ent = tk.Entry(inner, textvariable=var, show="●", width=64, font=FONT_LABEL, bg=ENTRY_BG, bd=1, relief="flat", highlightbackground=ENTRY_BORDER, highlightthickness=1)
            ent.grid(row=i, column=1, sticky="w", pady=8)
            if input_type == "password": ent_pw = ent
            else: ent_pw_confirm = ent
        else:
            ent = tk.Entry(inner, textvariable=var, width=64, font=FONT_LABEL, bg=ENTRY_BG, bd=1, relief="flat", highlightbackground=ENTRY_BORDER, highlightthickness=1)
            ent.grid(row=i, column=1, sticky="w", pady=8)
            if input_type == "entry_readonly":
                ent.configure(state="readonly", readonlybackground="#f0f0f0")

    def _toggle_pw():
        pw_visible["val"] = not pw_visible["val"]
        ch = "" if pw_visible["val"] else "●"
        try:
            ent_pw.config(show=ch)
            ent_pw_confirm.config(show=ch)
            btn_toggle_pw.config(text="Masquer" if pw_visible["val"] else "Afficher")
        except Exception:
            pass

    btn_toggle_pw = tk.Button(inner, text="Afficher", command=_toggle_pw, bg="#e9ecef", font=("Segoe UI", 9))
    btn_toggle_pw.grid(row=4, column=2, padx=(8,0), sticky="w")

    btn_frame = tk.Frame(dlg, bg=CARD_BG, pady=8)
    btn_frame.pack(fill="x", side="bottom")

    def _validate_edit_inputs() -> Tuple[bool, str]:
        nom = nom_var.get().strip()
        role = role_var.get().strip()
        pw = pw_var.get()
        pwc = pw_confirm_var.get()
        contrib_display = contrib_var.get().strip()
        if not nom:
            return False, "Le nom complet est requis"
        if role not in ROLES:
            return False, "Rôle invalide"
        if pw or pwc:
            if pw != pwc:
                return False, "Les mots de passe ne correspondent pas"
            if len(pw) < 6:
                return False, "Le mot de passe doit contenir au moins 6 caractères"
        if contrib_display:
            if re.fullmatch(r"\d+", contrib_display) is None:
                return False, "Contribuable id doit être un nombre entier ou vide"
        return True, ""

    def _on_save():
        ok, msg = _validate_edit_inputs()
        if not ok:
            messagebox.showwarning("Validation", msg, parent=dlg)
            return
        nom = nom_var.get().strip()
        role = role_var.get().strip()
        contrib_display = contrib_var.get().strip()
        if contrib_display == "":
            contrib_id = None
        else:
            try:
                contrib_id = int(contrib_display)
            except Exception:
                contrib_id = None
        password_hash = _hash_password(pw_var.get()) if pw_var.get() else None
        try:
            _update_user_db(user_id, nom, password_hash, role, contrib_id)
            messagebox.showinfo("Succès", "Utilisateur mis à jour", parent=dlg)
            dlg.destroy()
            if callable(on_saved_callback):
                on_saved_callback()
        except Exception as e:
            messagebox.showerror("Erreur", f"Échec mise à jour: {e}", parent=dlg)

    def _on_delete():
        if not messagebox.askyesno("Confirmation", "Supprimer cet utilisateur ? Cette action est irréversible.", parent=dlg):
            return
        try:
            _delete_user_db(user_id)
            messagebox.showinfo("Supprimé", "Utilisateur supprimé", parent=dlg)
            dlg.destroy()
            if callable(on_saved_callback): on_saved_callback()
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de supprimer: {e}", parent=dlg)

    btn_save = tk.Button(btn_frame, text="Enregistrer", bg=BTN_EDIT_BG, fg=BTN_FG, command=_on_save)
    btn_save.pack(side="right", padx=6)
    btn_delete = tk.Button(btn_frame, text="Supprimer", bg=BTN_DELETE_BG, fg="white", command=_on_delete)
    btn_delete.pack(side="right", padx=6)
    btn_cancel = tk.Button(btn_frame, text="Annuler", command=dlg.destroy)
    btn_cancel.pack(side="right", padx=6)

    def _on_config(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(inner_id, width=event.width)
    inner.bind("<Configure>", _on_config)

    dlg.update_idletasks()
    content_h = inner.winfo_reqheight() + btn_frame.winfo_reqheight() + 50
    content_w = max(800, inner.winfo_reqwidth() + 40)
    screen_h = center_widget.winfo_screenheight()
    screen_w = center_widget.winfo_screenwidth()
    min_h = 360
    max_h = int(screen_h * 0.9)
    final_h = min(max(content_h, min_h), max_h)
    final_w = min(max(content_w, 700), int(screen_w * 0.95))

    _center_window(dlg, center_widget, prefer_w=final_w, prefer_h=final_h)

# ----- Tableau principal ----- 
def afficher_tableau_utilisateurs(parent):
    for w in parent.winfo_children():
        try: w.destroy()
        except Exception: pass

    parent.grid_columnconfigure(0, weight=1)
    parent.grid_rowconfigure(1, weight=1)
    parent.configure(bg=CONTENT_BG)

    current_page = {"n": 1}
    pagination_info = {"total": 0}

    header = tk.Frame(parent, bg=CONTENT_BG)
    header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
    header.grid_columnconfigure(0, weight=1)

    tk.Label(header, text="👥 Gestion des utilisateurs", font=FONT_TITLE, bg=CONTENT_BG, fg=HEADER_FG).grid(row=0, column=0, sticky="w")

    right = tk.Frame(header, bg=CONTENT_BG)
    right.grid(row=0, column=1, sticky="e")

    search_var = tk.StringVar()
    tk.Label(right, text="Recherche :", bg=CONTENT_BG, fg=LABEL_FG, font=FONT_LABEL).pack(side="left", padx=(0,6))
    search_entry = tk.Entry(right, textvariable=search_var, font=FONT_LABEL, width=22,
                             bg=ENTRY_BG, highlightthickness=1, highlightbackground=ENTRY_BORDER, relief="flat")
    search_entry.pack(side="left", padx=(0,8))

    page_size_var = tk.IntVar(value=default_page_size)
    tk.Label(right, text="Taille page:", bg=CONTENT_BG, fg=LABEL_FG, font=FONT_LABEL).pack(side="left", padx=(6,4))
    page_size_cb = ttk.Combobox(right, values=PAGE_SIZES, textvariable=page_size_var, width=4, state="readonly", font=FONT_LABEL)
    page_size_cb.pack(side="left", padx=(0,8))
    page_size_cb.set(default_page_size)

    card = tk.Frame(parent, bg=CARD_BG)
    card.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
    card.grid_columnconfigure(0, weight=1)
    card.grid_rowconfigure(0, weight=1)

    inner_outer = tk.Frame(card, bg=CONTOUR_BG, bd=1, relief="solid")
    inner_outer.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    inner_outer.grid_rowconfigure(0, weight=1)
    inner_outer.grid_columnconfigure(0, weight=1)
    inner_outer.grid_columnconfigure(1, weight=1)
    inner_outer.grid_columnconfigure(2, weight=1)

    inner_grid = tk.Frame(inner_outer, bg=CARD_BG)
    inner_grid.grid(row=0, column=1, sticky="nsew", padx=0, pady=8)
    inner_grid.grid_rowconfigure(0, weight=0)

    for ci, (dbcol, label, minw) in enumerate(COLUMNS):
        h = tk.Label(inner_grid, text=label, bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 9, "bold"),
                      anchor="w", padx=6, bd=1, relief="solid", pady=8)
        h.grid(row=0, column=ci, sticky="nsew", padx=0, pady=0)
        if dbcol == "nom":
            inner_grid.grid_columnconfigure(ci, weight=1, minsize=minw)
        else:
            inner_grid.grid_columnconfigure(ci, weight=0, minsize=minw)

    act_col = len(COLUMNS)
    ha = tk.Label(inner_grid, text="Actions", bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 9, "bold"),
                   anchor="center", padx=6, bd=1, relief="solid", pady=8)
    ha.grid(row=0, column=act_col, sticky="nsew", padx=0, pady=0)
    inner_grid.grid_columnconfigure(act_col, weight=0, minsize=200)

    created_row_widgets = []

    def clear_rows():
        for child in inner_grid.grid_slaves():
            info = child.grid_info()
            if info.get("row", 0) >= 1:
                try: child.destroy()
                except Exception: pass
        created_row_widgets.clear()

    def refresh():
        render_rows(search_var.get().strip())

    def render_rows(filter_text=None):
        page = current_page["n"]
        page_size = page_size_var.get()

        try:
            rows, total = _fetch_users_page(filter_text=filter_text, page=page, page_size=page_size)
            pagination_info["total"] = total
        except sqlite3.OperationalError as oe:
            clear_rows()
            lbl = tk.Label(inner_grid, text=f"Erreur base donnée: {oe}", bg=CARD_BG, fg="#900", font=("Segoe UI", 10, "bold"), anchor="w")
            lbl.grid(row=1, column=0, columnspan=len(COLUMNS)+1, sticky="ew", padx=8, pady=8)
            created_row_widgets.append([lbl])
            _update_pager(0, 0, 0)
            return
        except Exception as e:
            clear_rows()
            lbl = tk.Label(inner_grid, text=f"Erreur: {e}", bg=CARD_BG, fg="#900", font=("Segoe UI", 10, "bold"), anchor="w")
            lbl.grid(row=1, column=0, columnspan=len(COLUMNS)+1, sticky="ew", padx=8, pady=8)
            created_row_widgets.append([lbl])
            _update_pager(0, 0, 0)
            return

        clear_rows()

        if total == 0:
            lbl = tk.Label(inner_grid, text="Aucun utilisateur trouvé.", bg=CARD_BG, fg=LABEL_FG, font=("Segoe UI", 10), anchor="w")
            lbl.grid(row=1, column=0, columnspan=len(COLUMNS)+1, sticky="ew", padx=8, pady=8)
            created_row_widgets.append([lbl])
            _update_pager(0, 0, 0)
            return

        total_pages = max(1, (total + page_size - 1) // page_size)

        if current_page["n"] > total_pages:
            current_page["n"] = total_pages
            page = current_page["n"]
            rows, total = _fetch_users_page(filter_text=filter_text, page=page, page_size=page_size)
            pagination_info["total"] = total

        for ri, row in enumerate(rows, start=1):
            bg = ROW_BG_1 if (ri % 2 == 1) else ROW_BG_2
            widgets = []

            for ci, (dbcol, _, _) in enumerate(COLUMNS):
                txt = row.get(dbcol, "") if isinstance(row, dict) else row[ci]
                if dbcol == "role": txt = str(txt).capitalize()
                lbl = tk.Label(inner_grid, text=str(txt) if txt is not None else "", anchor="w", bg=bg, fg=LABEL_FG, font=FONT_CELL,
                                 padx=6, pady=6, bd=1, relief="solid", wraplength=150)
                lbl.grid(row=ri, column=ci, sticky="nsew", padx=0, pady=0)
                inner_grid.grid_rowconfigure(ri, minsize=ROW_HEIGHT)
                widgets.append(lbl)

            user_id = row.get("id") if isinstance(row, dict) else row[0]
            act_frame = tk.Frame(inner_grid, bg=bg, bd=1, relief="solid")
            act_frame.grid(row=ri, column=len(COLUMNS), sticky="nsew", padx=0, pady=0)

            btn_v = tk.Button(act_frame, text="🔍 Voir", bg=BTN_VIEW_BG, fg="white", activebackground="#5a6268", padx=6, pady=2, font=("Segoe UI", 8),
                              command=lambda uid=user_id: _open_view_modal(inner_grid, _fetch_user_by_id(uid)))
            btn_e = tk.Button(act_frame, text="✏️ Editer", bg=BTN_EDIT_BG, fg="white", activebackground="#0056b3", padx=6, pady=2, font=("Segoe UI", 8),
                              command=lambda uid=user_id: _open_edit_modal(inner_grid, uid, refresh))
            btn_v.pack(side="left", padx=(4,0), pady=4)
            btn_e.pack(side="left", padx=4, pady=4)

            widgets.append(act_frame)
            created_row_widgets.append(widgets)

        _update_pager(current_page['n'], total_pages, total)

    def _update_pager(page, total_pages, total):
        pager_text = f"Page {page} / {total_pages} — {total} utilisateur(s)"
        try:
            lbl = getattr(parent, "_pager_label", None)
            if lbl and getattr(lbl, "winfo_exists", lambda: False)() :
                lbl.config(text=pager_text)
            else:
                parent._pager_label = tk.Label(parent, text=pager_text, bg=CONTENT_BG, fg=LABEL_FG, font=("Segoe UI", 9))
                parent._pager_label.grid(row=2, column=0, sticky="w", padx=12, pady=(4,0))
        except Exception:
            pass

        try:
            btn_prev.config(state="normal" if page > 1 else "disabled")
            btn_next.config(state="normal" if page < total_pages else "disabled")
        except Exception:
            pass

    pager_frame = tk.Frame(parent, bg=CONTENT_BG)
    pager_frame.grid(row=3, column=0, sticky="e", padx=12, pady=(6,12))
    btn_prev = tk.Button(pager_frame, text="◀ Préc", bg="#6c757d", fg=BTN_FG, width=8, activebackground="#5a6268")
    btn_next = tk.Button(pager_frame, text="Suiv ▶", bg="#6c757d", fg=BTN_FG, width=8, activebackground="#5a6268")
    btn_prev.pack(side="left", padx=6); btn_next.pack(side="left")

    def on_prev():
        if current_page["n"] > 1:
            current_page["n"] -= 1
            refresh()

    def on_next():
        page_size = page_size_var.get()
        total = pagination_info.get("total", 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if current_page["n"] < total_pages:
            current_page["n"] += 1
            refresh()

    btn_prev.config(command=on_prev)
    btn_next.config(command=on_next)

    def on_page_size_change(e=None):
        current_page["n"] = 1
        refresh()

    page_size_cb.bind("<<ComboboxSelected>>", on_page_size_change)

    def _on_search(*_):
        current_page["n"] = 1
        refresh()

    try:
        search_var.trace_add("write", _on_search)
    except Exception:
        try: search_var.trace("w", _on_search)
        except Exception: pass

    refresh()

    return {"refresh": refresh,
            "search_var": search_var,
            "page_size_var": page_size_var}
