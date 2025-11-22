#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
failed_movements_viewer_fixed.py
- Affiche mouvements où mouvement_stock.obr_status = 0
- Colonnes : Mvt ID, Date, Code article, Désignation, Unité de mesure, Actions
- Une seule grille (grid) partagée entre l'en-tête et les lignes pour alignement
- Boutons par ligne : Voir + Réessayer
- DateEntry pour filtres Date de / Date à
- Pas de scrollbars
- Adapte la requête SQL pour éviter les colonnes calculées absentes dans la table
- L'apparence (style/colors/paddings) est conservée / adaptée pour cohérence
- Remplacez DB_PATH, API_OBR_URL et obtenir_token_auto/get_system_id par vos implémentations si nécessaire
"""
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from tkcalendar import DateEntry
from datetime import datetime
import json
import math
import logging
import requests
from database.connection import get_connection

# Remplacez par vos helpers d'auth si existants
try:
    from api.obr_client import obtenir_token_auto, get_system_id
except Exception:
    def obtenir_token_auto():
        return None
    def get_system_id():
        return "LOCAL_DEVICE"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_OBR_URL = "https://ebms.obr.gov.bi:9443/ebms_api/AddStockMovement/"
API_TIMEOUT = 30

# Visual constants (conservés / adaptés)
CONTENT_BG = "#f6f8fa"
CARD_BG = "#ffffff"
CONTOUR_BG = "#e6eef9"
TITLE_FG = "#0b3d91"
LABEL_FG = "#1f2937"
ROW_ALT = "#fbfdff"
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_LABEL = ("Segoe UI", 10)
FONT_CELL = ("Segoe UI", 9)

# Columns definition: (key, label, weight)
TABLE_COLS = [
    ("id", "Mvt ID", 6),
    ("item_movement_date", "Date", 18),
    ("item_code", "Code article", 8),
    ("item_designation", "Désignation", 36),
    ("item_measurement_unit", "Unité de mesure", 10)
]

PAGE_SIZE = 12
ACTIONS_MINWIDTH = 160

# ---------------- DB helpers ----------------

def query_mouvement_articles(obr_status=0, date_from=None, date_to=None):
    """
    Charge mouvements avec obr_status = ?.
    La requête évite les colonnes calculées qui peuvent ne pas exister.
    Retourne une liste de dicts (col_name -> value).
    """
    conn = get_connection()
    cur = conn.cursor()
    # Sélection prudente : utiliser les colonnes standard existantes.
    # Nous mappons item_sale_price -> item_price si present.
    q = """
      SELECT
        ms.id,
        ms.item_movement_date,
        ms.item_movement_invoice_ref,
        ms.item_movement_description,
        ms.item_movement_type,
        a.item_code,
        a.item_designation,
        a.item_quantity,
        a.item_measurement_unit,
        -- attempt common price columns but alias to item_price; use NULL fallback if absent
        COALESCE(a.item_sale_price, a.item_cost_price, NULL) AS item_price,
        ms.obr_status
      FROM mouvement_stock ms
      LEFT JOIN article_stock_local a ON ms.article_stock_id = a.id
      WHERE ms.obr_status = ?
    """
    params = [obr_status]
    if date_from:
        q += " AND ms.item_movement_date >= ?"; params.append(date_from)
    if date_to:
        q += " AND ms.item_movement_date <= ?"; params.append(date_to)
    q += " ORDER BY ms.item_movement_date DESC"
    cur.execute(q, params)
    rows = cur.fetchall()
    col_names = [d[0] for d in cur.description]
    conn.close()
    return [dict(zip(col_names, r)) for r in rows]

def mark_mouvement_status(mvt_id, status):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("UPDATE mouvement_stock SET obr_status=? WHERE id=?", (status, mvt_id))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        logger.exception("Erreur update obr_status: %s", e)
        return False

# ---------------- réseau / auth placeholders ----------------

def send_payload_to_obr(payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.post(API_OBR_URL, json=payload, headers=headers, timeout=API_TIMEOUT, verify=True)
        if 200 <= resp.status_code < 300:
            try: return True, resp.json()
            except Exception: return True, {"http_status": resp.status_code}
        else:
            try: return False, resp.json()
            except Exception: return False, {"http_status": resp.status_code, "text": resp.text}
    except requests.RequestException as e:
        logger.exception("Erreur réseau OBR")
        return False, {"error": str(e)}

# ---------------- utilitaires date / format ----------------

def parse_date_input(s):
    s = (s or "").strip()
    if not s: return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try: return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception: pass
    try: return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return None

def format_date_short(txt):
    if not txt: return ""
    try: return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except Exception: return str(txt)

# ---------------- GUI détail / retry ----------------

def _show_row_details(root, row):
    top = tk.Toplevel(root)
    top.title(f"Détail mouvement {row.get('id')}")
    top.geometry("760x460")
    top.resizable(False, False)

    outer = tk.Frame(top, bg=CONTOUR_BG, padx=8, pady=8); outer.pack(fill="both", expand=True)
    panel = tk.Frame(outer, bg=CARD_BG, bd=1, relief="solid", padx=12, pady=12); panel.pack(fill="both", expand=True)

    title = tk.Label(panel, text=f"Mouvement ID {row.get('id')}", font=("Segoe UI", 11, "bold"), bg=CARD_BG, fg=TITLE_FG)
    title.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,10))

    display_fields = [
        ("item_movement_date", "Date du mouvement"),
        ("item_movement_invoice_ref", "Référence facture"),
        ("item_movement_description", "Description"),
        ("item_movement_type", "Type"),
        ("item_code", "Code article"),
        ("item_designation", "Désignation"),
        ("item_quantity", "Quantité"),
        ("item_measurement_unit", "Unité"),
        ("item_price", "Prix unitaire (si disponible)"),
        ("obr_status", "OBR status")
    ]

    r = 1
    for idx, (key, label) in enumerate(display_fields):
        val = row.get(key, "")
        if key == "item_movement_date" and val:
            try: val = format_date_short(val)
            except Exception: pass
        lbl = tk.Label(panel, text=label + " :", font=("Segoe UI", 9, "bold"), bg=CARD_BG, fg=LABEL_FG, anchor="ne")
        lbl.grid(row=r, column=(idx % 2) * 2, sticky="ne", padx=(0,8), pady=6)
        val_lbl = tk.Label(panel, text=str(val), font=("Segoe UI", 9), bg=CARD_BG, fg=LABEL_FG,
                           anchor="w", justify="left", wraplength=320, bd=1, relief="solid", padx=6, pady=6)
        val_lbl.grid(row=r, column=(idx % 2) * 2 + 1, sticky="nsew", pady=6)
        if idx % 2 == 1: r += 1

    ttk.Button(panel, text="Fermer", command=top.destroy).grid(row=r+1, column=3, sticky="e", pady=(12,0))

def _retry_send_movement_confirm(parent, row, refresh_cb):
    if not messagebox.askyesno("Confirmer", f"Réessayer l'envoi du mouvement ID {row.get('id')} ?", parent=parent):
        return
    try:
        payload = {
            "system_or_device_id": get_system_id(),
            "item_code": row.get("item_code"),
            "item_designation": row.get("item_designation"),
            "item_quantity": str(row.get("item_quantity") or 0),
            "item_measurement_unit": row.get("item_measurement_unit"),
            "item_purchase_or_sale_price": str(row.get("item_price") or 0),
            "item_purchase_or_sale_currency": "BIF",
            "item_movement_type": row.get("item_movement_type") or "EN",
            "item_movement_invoice_ref": row.get("item_movement_invoice_ref") or "",
            "item_movement_description": row.get("item_movement_description") or "",
            "item_movement_date": row.get("item_movement_date") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception:
        payload = {"id": row.get("id")}
    token = obtenir_token_auto()
    ok, resp = send_payload_to_obr(payload, token)
    if ok:
        success = mark_mouvement_status(row.get("id"), 1)
        if success: messagebox.showinfo("Succès", f"Mouvement ID {row.get('id')} envoyé et marqué OBR=1.", parent=parent)
        else: messagebox.showwarning("Avertissement", f"Envoi réussi mais impossible de mettre à jour le statut local pour ID {row.get('id')}.", parent=parent)
        try: refresh_cb()
        except Exception: pass
    else:
        try: message = json.dumps(resp, ensure_ascii=False, indent=2)
        except Exception: message = str(resp)
        messagebox.showerror("Échec envoi", f"Envoi échoué : {message}", parent=parent)

# ---------------- UI principale (grid-based table) ----------------

def show_failed_articles(parent):
    for w in parent.winfo_children(): w.destroy()
    parent.configure(bg=CONTENT_BG)

    state = {"data": [], "page": 1, "page_size": PAGE_SIZE, "total_pages": 1, "date_from": None, "date_to": None}

    # Header area
    title_frame = tk.Frame(parent, bg=CONTENT_BG); title_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(8,6))
    title_frame.columnconfigure(0, weight=1)
    tk.Label(title_frame, text="Mouvements des articles déclarés à l'OBR non réuissis", font=FONT_TITLE, bg=CONTENT_BG, fg=TITLE_FG).grid(row=0, column=0, sticky="w")
    tk.Label(title_frame, text="Filtrer par date puis cliquez Recherche", font=FONT_LABEL, bg=CONTENT_BG, fg=LABEL_FG).grid(row=1, column=0, sticky="w", pady=(2,6))

    # Controls
    ctrl = tk.Frame(parent, bg=CONTENT_BG); ctrl.grid(row=1, column=0, sticky="ew", padx=12, pady=(0,8))
    for i in range(6): ctrl.columnconfigure(i, weight=0)
    tk.Label(ctrl, text="Date de :", font=FONT_LABEL, bg=CONTENT_BG, fg=LABEL_FG).grid(row=0, column=0, sticky="w", padx=(0,6))
    date_from_var = tk.StringVar()
    e_from = DateEntry(ctrl, textvariable=date_from_var, date_pattern="yyyy-mm-dd", width=12); e_from.grid(row=0, column=1, padx=(0,12))
    tk.Label(ctrl, text="Date à :", font=FONT_LABEL, bg=CONTENT_BG, fg=LABEL_FG).grid(row=0, column=2, sticky="w", padx=(0,6))
    date_to_var = tk.StringVar()
    e_to = DateEntry(ctrl, textvariable=date_to_var, date_pattern="yyyy-mm-dd", width=12); e_to.grid(row=0, column=3, padx=(0,12))
    btn_search = tk.Button(ctrl, text="Recherche", bg="#2563eb", fg="white", activebackground="#1e40af", padx=8); btn_search.grid(row=0, column=4, padx=(8,6))
    btn_refresh = tk.Button(ctrl, text="Rafraîchir", bg="#9ca3af", fg="white", activebackground="#6b7280", padx=8); btn_refresh.grid(row=0, column=5, padx=(8,6))

    # Table container using grid for header + rows
    table_frame = tk.Frame(parent, bg=CONTOUR_BG, bd=1, relief="solid"); table_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0,8))
    parent.rowconfigure(2, weight=1); parent.columnconfigure(0, weight=1)

    # Configure grid columns on table_frame according to weights
    total_weight = sum([w for _, _, w in TABLE_COLS])
    for c, (_k, _label, weight) in enumerate(TABLE_COLS):
        table_frame.grid_columnconfigure(c, weight=weight, minsize=weight * 6)
    # Actions column
    actions_col = len(TABLE_COLS)
    table_frame.grid_columnconfigure(actions_col, weight=0, minsize=ACTIONS_MINWIDTH)

    # Header row (row 0 inside table_frame)
    for c, (_k, label, _w) in enumerate(TABLE_COLS):
        hdr = tk.Label(table_frame, text=label, bg="#eef6ff", fg=LABEL_FG, font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=8, pady=6)
        hdr.grid(row=0, column=c, sticky="nsew")
    hdr_actions = tk.Label(table_frame, text="Actions", bg="#eef6ff", fg=LABEL_FG, font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=8, pady=6)
    hdr_actions.grid(row=0, column=actions_col, sticky="nsew")

    # Rows area start at row 1
    rows_start_row = 1
    row_widgets = []

    def load_data_and_refresh(page=1):
        df = parse_date_input(date_from_var.get()); dt = parse_date_input(date_to_var.get())
        state["date_from"] = df; state["date_to"] = dt
        try:
            state["data"] = query_mouvement_articles(obr_status=0, date_from=df, date_to=dt)
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de charger les données: {e}", parent=parent)
            state["data"] = []
        total = len(state["data"]); state["page_size"] = PAGE_SIZE
        state["total_pages"] = max(1, math.ceil(total / state["page_size"]))
        state["page"] = max(1, min(page, state["total_pages"]))
        refresh_table()

    def refresh_table():
        # destroy previous row widgets
        for widgets in row_widgets:
            for w in widgets:
                try: w.destroy()
                except Exception: pass
        row_widgets.clear()
        # remove any existing extra rows in table_frame
        for r in range(rows_start_row, rows_start_row + PAGE_SIZE + 5):
            for c in range(actions_col + 1):
                info = table_frame.grid_slaves(row=r, column=c)
                for w in info: w.destroy()

        data = state["data"]
        if not data:
            empty_lbl = tk.Label(table_frame, text="Aucun mouvement en échec pour les filtres choisis.", bg=CARD_BG, fg=LABEL_FG, font=FONT_CELL)
            empty_lbl.grid(row=rows_start_row, column=0, columnspan=actions_col+1, sticky="nsew", padx=6, pady=12)
            return

        page = state["page"]; size = state["page_size"]
        start = (page - 1) * size; end = start + size
        page_rows = data[start:end]

        for ri, row in enumerate(page_rows, start=rows_start_row):
            bg = ROW_ALT if (ri - rows_start_row + 1) % 2 == 0 else CARD_BG
            widgets_row = []
            for c, (key, _label, _w) in enumerate(TABLE_COLS):
                val = row.get(key, "")
                if key == "item_movement_date":
                    val = format_date_short(val)
                cell = tk.Label(table_frame, text=str(val), bg=bg, fg=LABEL_FG, font=FONT_CELL, bd=1, relief="solid", anchor="w", padx=8, pady=6)
                cell.grid(row=ri, column=c, sticky="nsew", padx=0, pady=0)
                widgets_row.append(cell)
            # Actions cell
            action_container = tk.Frame(table_frame, bg=bg)
            action_container.grid(row=ri, column=actions_col, sticky="nsew", padx=4, pady=4)
            def make_view_fn(rdata=row):
                def _open(): _show_row_details(parent, rdata)
                return _open
            def make_retry_fn(rdata=row):
                def _retry(): _retry_send_movement_confirm(parent, rdata, lambda: load_data_and_refresh(page=state.get("page",1)))
                return _retry
            view_btn = tk.Button(action_container, text="Voir", bg="#2563eb", fg="white", activebackground="#1e40af", command=make_view_fn(), padx=8)
            view_btn.pack(side="left", padx=(6,6))
            retry_btn = tk.Button(action_container, text="Réessayer", bg="#f59e0b", fg="white", activebackground="#d97706", command=make_retry_fn(), padx=8)
            retry_btn.pack(side="left", padx=(0,6))
            widgets_row.append(action_container)
            row_widgets.append(widgets_row)

        # update pager info (simple)
        total_pages = state.get("total_pages", 1)
        current = state.get("page", 1)
        # find/update pager label if exists
        for child in parent.winfo_children():
            # pager_frame placed after table_frame; we update its label by searching
            pass
        # keep previous behavior: no explicit pager label update other than disabling/enabling buttons if implemented externally

    # initial load
    btn_search.config(command=lambda: load_data_and_refresh(page=1))
    btn_refresh.config(command=lambda: load_data_and_refresh(page=state.get("page", 1)))
    load_data_and_refresh(page=1)

# Demo runner
if __name__ == "__main__":
    root = tk.Tk()
    root.title("Mouvements OBR en échec - Viewer")
    root.geometry("980x520")
    show_failed_articles(root)
    root.mainloop()
