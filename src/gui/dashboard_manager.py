# dashboard_manager.py
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
from datetime import date, timedelta
import sqlite3

# connection helper (fallback si database.connection absent)
try:
    from database.connection import get_connection
except Exception:
    def get_connection(path: str = None):
        p = path or "facturation_obr.db"
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        return conn

# -----------------------
# Data access
# -----------------------
def _default_period():
    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    return yesterday.isoformat(), tomorrow.isoformat()

def fetch_metrics(contribuable_id=None, low_threshold=5, period_from=None, period_to=None):
    """
    Retourne dict:
      total_items_count, total_stock_value,
      low_stock (list of rows dict),
      low_stock_count,
      total_transactions, total_factures,
      month_revenue, total_contribuables, total_utilisateurs,
      period_from, period_to
    Cette fonction est synchrone et peut être appelée depuis un thread worker.
    """
    if period_from is None or period_to is None:
        period_from, period_to = _default_period()

    conn = get_connection()
    cur = conn.cursor()
    try:
        # total items
        try:
            if contribuable_id:
                cur.execute("SELECT COUNT(1) AS total_items_count FROM article_stock_local WHERE COALESCE(is_manuel,0)=0 AND contribuable_id = ?", (contribuable_id,))
            else:
                cur.execute("SELECT COUNT(1) AS total_items_count FROM article_stock_local WHERE COALESCE(is_manuel,0)=0")
            total_items_count = int(cur.fetchone()["total_items_count"] or 0)
        except Exception:
            total_items_count = 0

        # total stock value
        try:
            if contribuable_id:
                cur.execute("SELECT COALESCE(SUM(item_quantity * COALESCE(item_sale_price,0.0)),0.0) AS total_stock_value FROM article_stock_local WHERE COALESCE(is_manuel,0)=0 AND contribuable_id = ?", (contribuable_id,))
            else:
                cur.execute("SELECT COALESCE(SUM(item_quantity * COALESCE(item_sale_price,0.0)),0.0) AS total_stock_value FROM article_stock_local WHERE COALESCE(is_manuel,0)=0")
            total_stock_value = float(cur.fetchone()["total_stock_value"] or 0.0)
        except Exception:
            total_stock_value = 0.0

        # low stock list
        try:
            if contribuable_id:
                cur.execute("SELECT id, item_code, item_designation, COALESCE(item_quantity,0) AS item_quantity, item_measurement_unit FROM article_stock_local WHERE contribuable_id = ? AND COALESCE(item_quantity,0) <= ? ORDER BY item_quantity ASC LIMIT 100", (contribuable_id, low_threshold))
            else:
                cur.execute("SELECT id, item_code, item_designation, COALESCE(item_quantity,0) AS item_quantity, item_measurement_unit FROM article_stock_local WHERE COALESCE(item_quantity,0) <= ? ORDER BY item_quantity ASC LIMIT 100", (low_threshold,))
            low_rows = [dict(r) for r in cur.fetchall()]
            low_stock_count = len(low_rows)
        except Exception:
            low_rows = []
            low_stock_count = 0

        # transactions
        try:
            if contribuable_id:
                cur.execute("SELECT COUNT(1) AS total_transactions FROM mouvement_stock WHERE contribuable_id = ? AND item_movement_date BETWEEN ? AND ?", (contribuable_id, period_from, period_to))
            else:
                cur.execute("SELECT COUNT(1) AS total_transactions FROM mouvement_stock WHERE item_movement_date BETWEEN ? AND ?", (period_from, period_to))
            total_transactions = int(cur.fetchone()["total_transactions"] or 0)
        except Exception:
            total_transactions = 0

        # factures
        try:
            if contribuable_id:
                cur.execute("SELECT COUNT(1) AS total_factures FROM facture WHERE contribuable_id = ? AND invoice_date BETWEEN ? AND ?", (contribuable_id, period_from, period_to))
            else:
                cur.execute("SELECT COUNT(1) AS total_factures FROM facture WHERE invoice_date BETWEEN ? AND ?", (period_from, period_to))
            total_factures = int(cur.fetchone()["total_factures"] or 0)
        except Exception:
            total_factures = 0

        # month revenue (from first day of month to period_to)
        try:
            today = date.today()
            first_day = today.replace(day=1).isoformat()
            if contribuable_id:
                cur.execute("SELECT COALESCE(SUM(total_amount),0.0) AS month_revenue FROM facture WHERE contribuable_id = ? AND invoice_date BETWEEN ? AND ?", (contribuable_id, first_day, period_to))
            else:
                cur.execute("SELECT COALESCE(SUM(total_amount),0.0) AS month_revenue FROM facture WHERE invoice_date BETWEEN ? AND ?", (first_day, period_to))
            month_revenue = float(cur.fetchone()["month_revenue"] or 0.0)
        except Exception:
            month_revenue = 0.0

        # totals
        try:
            cur.execute("SELECT COUNT(1) AS total_contribuables FROM contribuable")
            total_contribuables = int(cur.fetchone()["total_contribuables"] or 0)
        except Exception:
            total_contribuables = 0
        try:
            cur.execute("SELECT COUNT(1) AS total_utilisateurs FROM utilisateur_societe")
            total_utilisateurs = int(cur.fetchone()["total_utilisateurs"] or 0)
        except Exception:
            total_utilisateurs = 0

    finally:
        try: conn.close()
        except Exception: pass

    return {
        "total_items_count": total_items_count,
        "total_stock_value": total_stock_value,
        "low_stock": low_rows,
        "low_stock_count": low_stock_count,
        "total_transactions": total_transactions,
        "total_factures": total_factures,
        "month_revenue": month_revenue,
        "total_contribuables": total_contribuables,
        "total_utilisateurs": total_utilisateurs,
        "period_from": period_from,
        "period_to": period_to,
    }

# -----------------------
# UI: build_metrics_panel (manager) - non bloquant
# -----------------------
def _fetch_contrib_choices():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, tp_name FROM contribuable ORDER BY id")
        rows = cur.fetchall()
        conn.close()
        choices = [("", "Tous les contribuables")] + [(str(r["id"]), f'{r["id"]} — {r["tp_name"] or ""}') for r in rows]
        return choices
    except Exception:
        return [("", "Tous les contribuables")]

def build_metrics_panel(parent, contrib_id=None, low_threshold=5):
    """
    Construit le panel et retourne {'refresh': callable, 'start_auto': callable, 'stop_auto': callable}
    Le refresh est non bloquant : il lance la lecture DB dans un thread et met à jour l'UI via parent.after.
    """
    try:
        parent.configure(bg="#f6f8fa")
    except Exception:
        pass

    # clear parent
    for w in list(parent.winfo_children()):
        try: w.destroy()
        except Exception: pass

    # Header + controls
    header = tk.Frame(parent, bg="#f6f8fa")
    header.grid(row=0, column=0, sticky="ew", padx=8, pady=(6,4))
    header.columnconfigure(1, weight=1)
    tk.Label(header, text="📊 Tableau de bord — Manager", bg="#f6f8fa", fg="#0b3d91", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")

    controls = tk.Frame(header, bg="#f6f8fa")
    controls.grid(row=0, column=1, sticky="e")

    contrib_choices = _fetch_contrib_choices()
    contrib_key_to_label = {k: v for k, v in contrib_choices}
    contrib_var = tk.StringVar(value=str(contrib_id) if contrib_id else "")
    contrib_cb = ttk.Combobox(controls, values=[label for _, label in contrib_choices], state="readonly", width=28)
    try:
        if contrib_var.get():
            for i, (k, v) in enumerate(contrib_choices):
                if k == contrib_var.get():
                    contrib_cb.current(i); break
        else:
            contrib_cb.current(0)
    except Exception:
        try: contrib_cb.current(0)
        except Exception: pass

    tk.Label(controls, text="Filtrer : ", bg="#f6f8fa", font=("Segoe UI",9)).pack(side="left", padx=(0,6))
    contrib_cb.pack(side="left", padx=(0,6))

    btn_refresh = tk.Button(controls, text="Rafraîchir", bg="#2563eb", fg="white", padx=8, pady=3)
    btn_refresh.pack(side="left")

    # Metrics grid
    metrics_frame = tk.Frame(parent, bg="#f6f8fa")
    metrics_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4,6))
    parent.grid_rowconfigure(1, weight=0)
    parent.grid_columnconfigure(0, weight=1)
    for c in range(4):
        metrics_frame.columnconfigure(c, weight=1, uniform="cards")

    # Cards (display with horizontal layout: title left, value right on same row)
    cards = [
        ("total_items_count", "Articles totaux", "#2563eb"),
        ("low_stock_count", f"Alertes (≤ {low_threshold})", "#dc2626"),
        ("total_transactions", "Mouvements (période)", "#16a34a"),
        ("total_factures", "Factures (période)", "#0d9488"),
        ("total_stock_value", "Valeur totale du stock", "#0ea5a4"),
        ("month_revenue", "Chiffre mois en cours", "#06b6d4"),
        ("total_contribuables", "Contribuables", "#7c3aed"),
        ("total_utilisateurs", "Utilisateurs", "#f59e0b"),
    ]

    widgets = {}
    title_font = ("Segoe UI", 9, "bold")
    value_font = ("Segoe UI", 16, "bold")

    for idx, (key, label_text, color) in enumerate(cards):
        r = idx // 4
        c = idx % 4

        # container with horizontal layout: left = title, right = value
        cont = tk.Frame(metrics_frame, bg=color, padx=8, pady=6)
        cont.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
        cont.columnconfigure(0, weight=1)  # title area expands
        cont.columnconfigure(1, weight=0)  # value area minimal width

        # title (left) - allow wrapping but keep single-line look by not forcing wraplength
        lbl_title = tk.Label(cont, text=label_text, bg=color, fg="white", font=title_font, anchor="w", justify="left")
        lbl_title.grid(row=0, column=0, sticky="w")

        # value (right) - big, bold, horizontally aligned on same row
        lbl_value = tk.Label(cont, text="—", bg=color, fg="white", font=value_font, anchor="e", justify="right")
        lbl_value.grid(row=0, column=1, sticky="e", padx=(12,0))

        # store widget reference for updates
        widgets[key] = lbl_value

    # Low stock detailed list
    low_card = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid")
    low_card.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4,8))
    parent.grid_rowconfigure(2, weight=1)
    tk.Label(low_card, text="⚠️ Articles en seuil", bg="#ffffff", fg="#b91c1c", font=("Segoe UI",11,"bold")).grid(row=0, column=0, sticky="w", padx=8, pady=(8,6))

    table_frame = tk.Frame(low_card, bg="#ffffff")
    table_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)
    low_card.grid_rowconfigure(1, weight=1)
    low_card.grid_columnconfigure(0, weight=1)

    headers = ["ID", "Code", "Désignation", "Unité", "Qté", "Actions"]
    header_frame = tk.Frame(table_frame, bg="#ffffff")
    header_frame.grid(row=0, column=0, sticky="ew")
    for ci, h in enumerate(headers):
        lbl_h = tk.Label(header_frame, text=h, bg="#ffffff", fg="#0b3d91", font=("Segoe UI",9,"bold"))
        lbl_h.grid(row=0, column=ci, sticky="w", padx=6, pady=3)
        header_frame.grid_columnconfigure(ci, weight=(3 if ci==2 else 1))

    rows_container = tk.Frame(table_frame, bg="#ffffff")
    rows_container.grid(row=1, column=0, sticky="nsew")
    table_frame.grid_rowconfigure(1, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)

    canvas = tk.Canvas(rows_container, bg="#ffffff", highlightthickness=0)
    scrollbar = ttk.Scrollbar(rows_container, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg="#ffffff")
    inner_id = canvas.create_window((0,0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    rows_container.grid_rowconfigure(0, weight=1); rows_container.grid_columnconfigure(0, weight=1)

    def _on_inner_config(event):
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(inner_id, width=event.width)
        except Exception:
            pass
    inner.bind("<Configure>", _on_inner_config)

    # helpers for row population and UI updates (must run on UI thread)
    def _clear_inner():
        for ch in list(inner.winfo_children()):
            try: ch.destroy()
            except Exception: pass

    def _populate_rows(rows):
        _clear_inner()
        if not rows:
            tk.Label(inner, text="Aucun article en dessous du seuil.", bg="#ffffff", fg="#666", font=("Segoe UI",10)).grid(row=0, column=0, sticky="w", padx=6, pady=8)
            return
        for i, r in enumerate(rows):
            aid = r.get("id")
            code = r.get("item_code") or f"#{aid}"
            des = r.get("item_designation") or "-"
            unit = r.get("item_measurement_unit") or "-"
            qty = r.get("item_quantity") or 0
            row_frame = tk.Frame(inner, bg="#ffffff")
            row_frame.grid(row=i, column=0, sticky="nsew")
            tk.Label(row_frame, text=str(aid), bg="#ffffff", font=("Segoe UI",9)).grid(row=0, column=0, sticky="w", padx=4, pady=4)
            tk.Label(row_frame, text=str(code), bg="#ffffff", font=("Segoe UI",9)).grid(row=0, column=1, sticky="w", padx=4, pady=4)
            tk.Label(row_frame, text=str(des), bg="#ffffff", font=("Segoe UI",9), wraplength=200, justify="left").grid(row=0, column=2, sticky="w", padx=4, pady=4)
            tk.Label(row_frame, text=str(unit), bg="#ffffff", font=("Segoe UI",9)).grid(row=0, column=3, sticky="w", padx=4, pady=4)
            tk.Label(row_frame, text=str(qty), bg="#ffffff", font=("Segoe UI",9,"bold")).grid(row=0, column=4, sticky="e", padx=4, pady=4)
            act_frame = tk.Frame(row_frame, bg="#ffffff")
            btn_view = ttk.Button(act_frame, text="Voir", command=lambda _id=aid: _open_article_modal(parent, _id))
            btn_view.pack(side="left", padx=(0,4))
            act_frame.grid(row=0, column=5, sticky="e", padx=4, pady=4)
            row_frame.grid_columnconfigure(2, weight=3)
            row_frame.grid_columnconfigure(0, weight=0)
            row_frame.grid_columnconfigure(1, weight=1)
            row_frame.grid_columnconfigure(3, weight=1)
            row_frame.grid_columnconfigure(4, weight=0)
            row_frame.grid_columnconfigure(5, weight=0)

    def _safe_set(widget, val, is_money=False):
        try:
            if is_money:
                widget.config(text=f"{float(val):.2f}")
            else:
                widget.config(text=str(val))
        except Exception:
            try: widget.config(text=str(val))
            except Exception: pass

    # -----------------------
    # Non blocking refresh pattern
    # -----------------------
    # Worker: lit la DB (fetch_metrics) dans un thread, puis poste le résultat en UI thread via after.
    def _fetch_in_background(selected_id):
        try:
            data = fetch_metrics(contribuable_id=selected_id, low_threshold=low_threshold)
            return {"ok": True, "data": data}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _apply_metrics_result(result):
        # this runs on UI thread
        if not result.get("ok"):
            err = result.get("error", "Erreur inconnue")
            try: messagebox.showerror("Erreur", f"Impossible de lire les métriques: {err}")
            except Exception: pass
            return
        m = result["data"]
        _safe_set(widgets["total_items_count"], m.get("total_items_count", "—"))
        _safe_set(widgets["low_stock_count"], m.get("low_stock_count", "—"))
        _safe_set(widgets["total_transactions"], m.get("total_transactions", "—"))
        _safe_set(widgets["total_factures"], m.get("total_factures", "—"))
        _safe_set(widgets["total_stock_value"], m.get("total_stock_value", 0.0), is_money=True)
        _safe_set(widgets["month_revenue"], m.get("month_revenue", 0.0), is_money=True)
        _safe_set(widgets["total_contribuables"], m.get("total_contribuables", "—"))
        _safe_set(widgets["total_utilisateurs"], m.get("total_utilisateurs", "—"))
        _populate_rows(m.get("low_stock", []))
        try:
            _on_parent_config()
        except Exception:
            pass

    # Public refresh function (non blocking)
    def refresh_nonblocking():
        sel = contrib_cb.get() if contrib_cb.get() else ""
        selected_key = None
        for k, v in contrib_choices:
            if v == sel:
                selected_key = k; break
        try:
            selected_id = int(selected_key) if selected_key not in ("", None) else None
        except Exception:
            selected_id = None

        # Launch worker thread to fetch data
        def worker():
            res = _fetch_in_background(selected_id)
            # post result in UI thread
            try:
                parent.after(0, lambda r=res: _apply_metrics_result(r))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    # wire the refresh button to non-blocking refresh
    btn_refresh.config(command=refresh_nonblocking)
    # initial kick (small delay to let UI render)
    parent.after(50, refresh_nonblocking)

    # -----------------------
    # Auto refresh control (non blocking)
    # -----------------------
    _auto = {"t": None, "stop": False}

    def _auto_worker(poll_interval=5.0):
        while not _auto["stop"]:
            try:
                refresh_nonblocking()
            except Exception:
                pass
            # sleep in small chunks to react quickly to stop flag
            slept = 0.0
            while slept < poll_interval and not _auto["stop"]:
                time.sleep(0.2)
                slept += 0.2

    def start_auto(interval_seconds=5.0):
        if _auto["t"] and _auto["t"].is_alive():
            return
        _auto["stop"] = False
        t = threading.Thread(target=_auto_worker, args=(interval_seconds,), daemon=True)
        _auto["t"] = t
        t.start()

    def stop_auto():
        _auto["stop"] = True
        t = _auto.get("t")
        if t and t.is_alive():
            try: t.join(timeout=0.2)
            except Exception: pass

    return {"refresh": refresh_nonblocking, "start_auto": start_auto, "stop_auto": stop_auto}

# -----------------------
# Article access for modal view (reuse from original)
# -----------------------
def _fetch_article_by_id(article_id):
    if article_id is None:
        return None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM article_stock_local WHERE id = ? LIMIT 1", (article_id,))
        row = cur.fetchone()
        try: conn.close()
        except Exception: pass
        if not row:
            return None
        return dict(row)
    except Exception:
        try: conn.close()
        except Exception: pass
        return None

def _open_article_modal(parent, article_id):
    """
    Ouvre une fenêtre modale 'Voir' avec les détails de l'article.
    """
    article = _fetch_article_by_id(article_id)
    dlg = tk.Toplevel(parent)
    dlg.title(f"🔎 Voir article — #{article_id}")
    dlg.transient(parent)
    dlg.grab_set()

    try:
        dlg.configure(bg="#ffffff")
    except Exception:
        pass

    frame = tk.Frame(dlg, bg="#ffffff", padx=12, pady=12)
    frame.pack(fill="both", expand=True)

    if not article:
        tk.Label(frame, text="Article introuvable.", bg="#ffffff", fg="#900", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4,8))
        btn_close = tk.Button(frame, text="Fermer", command=dlg.destroy, bg="#2563eb", fg="white")
        btn_close.pack(anchor="e", pady=(8,0))
        dlg.update_idletasks()
        w = min(700, dlg.winfo_reqwidth())
        h = min(300, dlg.winfo_reqheight())
        dlg.geometry(f"{w}x{h}+{max(0,(dlg.winfo_screenwidth()-w)//2)}+{max(0,(dlg.winfo_screenheight()-h)//2)}")
        return

    display_fields = [
        ("ID", "id"),
        ("Code article", "item_code"),
        ("Désignation", "item_designation"),
        ("Unité de mesure", "item_measurement_unit"),
        ("Quantité", "item_quantity"),
        ("Prix vente", "item_sale_price"),
        ("Prix achat", "item_purchase_price"),
        ("Is manuel", "is_manuel"),
        ("Contribuable ID", "contribuable_id"),
    ]

    canvas = tk.Canvas(frame, bg="#ffffff", highlightthickness=0)
    sc = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg="#ffffff")
    inner_id = canvas.create_window((0,0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sc.set)
    canvas.pack(side="left", fill="both", expand=True)
    sc.pack(side="right", fill="y")

    def _on_inner_config(event=None):
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(inner_id, width=event.width)
        except Exception:
            pass
    inner.bind("<Configure>", _on_inner_config)

    for ri, (label_text, key) in enumerate(display_fields):
        val = article.get(key, "")
        lbl = tk.Label(inner, text=label_text + " :", bg="#ffffff", fg="#0b3d91", font=("Segoe UI", 9, "bold"))
        lbl.grid(row=ri, column=0, sticky="nw", padx=(4,8), pady=6)
        if key in ("item_sale_price", "item_purchase_price") and val is not None and val != "":
            try:
                val_s = f"{float(val):.2f}"
            except Exception:
                val_s = str(val)
        else:
            val_s = "" if val is None else str(val)
        ent = tk.Text(inner, height=1, width=60, wrap="word", bg="#f7f7f7", bd=0)
        ent.insert("1.0", val_s)
        ent.configure(state="disabled", font=("Segoe UI", 10))
        ent.grid(row=ri, column=1, sticky="nw", padx=(0,4), pady=6)

    desc = article.get("item_description") or article.get("item_movement_description") or ""
    if desc:
        r = len(display_fields)
        tk.Label(inner, text="Description :", bg="#ffffff", fg="#0b3d91", font=("Segoe UI", 9, "bold")).grid(row=r, column=0, sticky="nw", padx=(4,8), pady=6)
        txt = tk.Text(inner, height=6, width=60, wrap="word", bg="#f7f7f7", bd=0)
        txt.insert("1.0", desc)
        txt.configure(state="disabled", font=("Segoe UI", 10))
        txt.grid(row=r, column=1, sticky="nw", padx=(0,4), pady=6)

    btn_frame = tk.Frame(dlg, bg="#ffffff", pady=8)
    btn_close = tk.Button(btn_frame, text="Fermer", bg="#2563eb", fg="white", command=dlg.destroy, padx=10, pady=4)
    btn_close.pack(side="right", padx=8)
    btn_frame.pack(fill="x", side="bottom")

    dlg.update_idletasks()
    req_w = inner.winfo_reqwidth() + 60
    req_h = min(600, inner.winfo_reqheight() + btn_frame.winfo_reqheight() + 40)
    screen_w = dlg.winfo_screenwidth()
    screen_h = dlg.winfo_screenheight()
    w = min(req_w, int(screen_w * 0.9))
    h = min(req_h, int(screen_h * 0.85))
    x = max(0, (screen_w - w) // 2)
    y = max(0, (screen_h - h) // 2)
    dlg.geometry(f"{w}x{h}+{x}+{y}")
