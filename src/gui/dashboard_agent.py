# dashboard_overview.py
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import date, timedelta
import sqlite3
import math

# Utilise ta fonction get_connection déjà existante si présente
try:
    from database.connection import get_connection
except Exception:
    def get_connection(path: str = None):
        p = path or "facturation_obr.db"
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        return conn


def fetch_overview_metrics(contribuable_id=None, lowstock_threshold=5, lowstock_limit=100, period_from=None, period_to=None):
    """
    Récupère les métriques :
    - total_items_count : COUNT(1) des enregistrements dans article_stock_local WHERE is_manuel = 0
    - total_stock_value : somme(item_quantity * item_sale_price) pour is_manuel = 0
    - low_stock : liste des articles avec item_quantity <= lowstock_threshold
    - total_transactions : count mouvements entre period_from et period_to
    - total_factures : count factures entre period_from et period_to
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # total count d'articles (is_manuel = 0) en utilisant COUNT(1)
        try:
            if contribuable_id:
                cur.execute(
                    "SELECT COUNT(1) AS total_items_count "
                    "FROM article_stock_local WHERE COALESCE(is_manuel,0)=0 AND contribuable_id = ?",
                    (contribuable_id,)
                )
            else:
                cur.execute(
                    "SELECT COUNT(1) AS total_items_count "
                    "FROM article_stock_local WHERE COALESCE(is_manuel,0)=0"
                )
            total_items_count = cur.fetchone()["total_items_count"] or 0
        except Exception:
            total_items_count = 0

        # valeur totale du stock (is_manuel = 0)
        try:
            if contribuable_id:
                cur.execute(
                    "SELECT COALESCE(SUM(item_quantity * COALESCE(item_sale_price,0.0)),0.0) AS total_stock_value "
                    "FROM article_stock_local WHERE COALESCE(is_manuel,0)=0 AND contribuable_id = ?",
                    (contribuable_id,)
                )
            else:
                cur.execute(
                    "SELECT COALESCE(SUM(item_quantity * COALESCE(item_sale_price,0.0)),0.0) AS total_stock_value "
                    "FROM article_stock_local WHERE COALESCE(is_manuel,0)=0"
                )
            total_stock_value = cur.fetchone()["total_stock_value"] or 0.0
        except Exception:
            total_stock_value = 0.0

        # low stock : item_quantity <= lowstock_threshold
        try:
            if contribuable_id:
                cur.execute(
                    "SELECT id, item_code, item_designation, item_quantity, item_measurement_unit FROM article_stock_local "
                    "WHERE contribuable_id = ? AND COALESCE(item_quantity,0) <= ? ORDER BY item_quantity ASC LIMIT ?",
                    (contribuable_id, lowstock_threshold, lowstock_limit)
                )
            else:
                cur.execute(
                    "SELECT id, item_code, item_designation, item_quantity, item_measurement_unit FROM article_stock_local "
                    "WHERE COALESCE(item_quantity,0) <= ? ORDER BY item_quantity ASC LIMIT ?",
                    (lowstock_threshold, lowstock_limit)
                )
            low_rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            low_rows = []

        # période : défaut hier -> demain
        if period_from is None or period_to is None:
            today = date.today()
            yesterday = today - timedelta(days=1)
            tomorrow = today + timedelta(days=1)
            period_from = yesterday.isoformat()
            period_to = tomorrow.isoformat()

        # transactions (mouvement_stock)
        try:
            if contribuable_id:
                cur.execute(
                    "SELECT COUNT(1) AS total_transactions FROM mouvement_stock "
                    "WHERE contribuable_id = ? AND item_movement_date BETWEEN ? AND ?",
                    (contribuable_id, period_from, period_to)
                )
            else:
                cur.execute(
                    "SELECT COUNT(1) AS total_transactions FROM mouvement_stock "
                    "WHERE item_movement_date BETWEEN ? AND ?",
                    (period_from, period_to)
                )
            total_transactions = cur.fetchone()["total_transactions"] or 0
        except Exception:
            total_transactions = 0

        # factures (invoice_date)
        try:
            if contribuable_id:
                cur.execute(
                    "SELECT COUNT(1) AS total_factures FROM facture "
                    "WHERE contribuable_id = ? AND invoice_date BETWEEN ? AND ?",
                    (contribuable_id, period_from, period_to)
                )
            else:
                cur.execute(
                    "SELECT COUNT(1) AS total_factures FROM facture WHERE invoice_date BETWEEN ? AND ?",
                    (period_from, period_to)
                )
            total_factures = cur.fetchone()["total_factures"] or 0
        except Exception:
            total_factures = 0

    finally:
        conn.close()

    return {
        "total_items_count": int(total_items_count),
        "total_stock_value": float(total_stock_value),
        "low_stock": low_rows,
        "total_transactions": int(total_transactions),
        "total_factures": int(total_factures),
    }


def build_dashboard_overview(parent, contrib_id=None, low_threshold=5, role_filter=None, page=1, page_size=10):
    """
    Construit le tableau de bord dans `parent` (interface en français).
    - contrib_id: filtre facultatif par contribuable
    - low_threshold: seuil d'alerte pour stock faible (par ex. 5)
    - role_filter: "admin" | "manager" | "agent" ou None
    - page / page_size: pagination simple pour la grille
    Retourne dict { "refresh": callable }.
    """
    # Défensif : configurer le fond du parent si possible
    try:
        parent.configure(bg="#f6f8fa")
    except Exception:
        pass

    # nettoyage initial du parent
    for w in list(parent.winfo_children()):
        try:
            w.destroy()
        except Exception:
            pass

    # En-tête
    header = tk.Frame(parent, bg="#f6f8fa")
    header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
    tk.Label(header, text="📊 Aperçu du tableau de bord", bg="#f6f8fa", fg="#0b3d91", font=("Segoe UI", 14, "bold")).pack(anchor="w")

    # Cartes métriques
    metrics_frame = tk.Frame(parent, bg="#f6f8fa")
    metrics_frame.grid(row=1, column=0, sticky="ew", padx=12)
    for c in range(4):
        metrics_frame.columnconfigure(c, weight=1)

    cartes = [
        ("Articles totaux (count)", "#2563eb"),
        (f"Alertes stock ≤ {low_threshold}", "#dc2626"),
        ("Mouvements (période)", "#16a34a"),
        ("Factures (période)", "#0d9488"),
    ]
    metrics_labels = {}
    for idx, (titre, couleur) in enumerate(cartes):
        cont = tk.Frame(metrics_frame, bg=couleur, padx=12, pady=10)
        cont.grid(row=0, column=idx, sticky="nsew", padx=6, pady=4)
        tk.Label(cont, text=titre, bg=couleur, fg="white", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        lbl_val = tk.Label(cont, text="0", bg=couleur, fg="white", font=("Segoe UI", 20, "bold"))
        lbl_val.pack(anchor="w", pady=(6,0))
        metrics_labels[titre] = lbl_val

    # Carte tableau : Alertes stock faible
    low_card = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid")
    low_card.grid(row=2, column=0, sticky="nsew", padx=12, pady=(12,12))
    parent.grid_rowconfigure(2, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    tk.Label(low_card, text="⚠️ Alertes de stock faible", bg="#ffffff", fg="#b91c1c", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(8,6))

    table_frame = tk.Frame(low_card, bg="#ffffff")
    table_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
    low_card.grid_rowconfigure(1, weight=1)
    low_card.grid_columnconfigure(0, weight=1)

    headers = ["ID article", "Désignation", "Unité", "Quantité", "Actions"]
    for c, h in enumerate(headers):
        lbl_h = tk.Label(table_frame, text=h, bg="#ffffff", fg="#0b3d91", font=("Segoe UI", 10, "bold"), anchor="w")
        lbl_h.grid(row=0, column=c, sticky="ew", padx=4, pady=4)
        table_frame.grid_columnconfigure(c, weight=(3 if c == 1 else 1))

    # Scrollable container pour les lignes
    rows_container = tk.Frame(table_frame, bg="#ffffff")
    rows_container.grid(row=1, column=0, columnspan=len(headers), sticky="nsew")
    table_frame.grid_rowconfigure(1, weight=1)

    canvas = tk.Canvas(rows_container, bg="#ffffff", highlightthickness=0)
    scrollbar = ttk.Scrollbar(rows_container, orient="vertical", command=canvas.yview)
    inner_rows = tk.Frame(canvas, bg="#ffffff")
    inner_id = canvas.create_window((0,0), window=inner_rows, anchor="nw")
    inner_rows.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    rows_container.grid_rowconfigure(0, weight=1)
    rows_container.grid_columnconfigure(0, weight=1)

    # pagination controls
    pag_frame = tk.Frame(low_card, bg="#ffffff")
    pag_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(6,12))
    pag_frame.columnconfigure(1, weight=1)
    lbl_page_info = tk.Label(pag_frame, text="", bg="#ffffff", font=("Segoe UI", 9))
    lbl_page_info.grid(row=0, column=1, sticky="e")
    btn_prev = ttk.Button(pag_frame, text="← Précédent")
    btn_next = ttk.Button(pag_frame, text="Suivant →")
    btn_prev.grid(row=0, column=0, sticky="w")
    btn_next.grid(row=0, column=2, sticky="e")

    lbl_empty = tk.Label(inner_rows, text="Aucun article en dessous du seuil.", bg="#ffffff", fg="#666", font=("Segoe UI", 10))

    # ---------- Modal "Voir article" ----------
    def _voir_article(article_id):
        """Ouvre une modale de lecture seule pour afficher les détails d'un article."""
        try:
            # fenêtre modale
            modal = tk.Toplevel(parent)
            modal.transient(parent)
            modal.grab_set()
            modal.title(f"Voir article — #{article_id}")
            try: modal.configure(bg="#ffffff")
            except Exception: pass

            # conteneur interne
            body = tk.Frame(modal, bg="#ffffff", padx=12, pady=12)
            body.pack(fill="both", expand=True)

            # helper pour ajouter une ligne label / valeur
            def _row(label_text, value_text, row):
                tk.Label(body, text=label_text, bg="#ffffff", fg="#0b3d91", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(6,2))
                val = tk.Label(body, text=value_text, bg="#ffffff", fg="#111", font=("Segoe UI", 10), anchor="w", wraplength=420, justify="left")
                val.grid(row=row, column=1, sticky="w", padx=(8,0), pady=(6,2))
                return val

            # lecture en base des informations détaillées (défensif)
            details = {
                "item_code": "",
                "item_designation": "",
                "item_measurement_unit": "",
                "item_quantity": "",
                "item_sale_price": "",
                "item_description": ""
            }
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT item_code, item_designation, item_measurement_unit, COALESCE(item_quantity,0) AS item_quantity, "
                    "COALESCE(item_sale_price,0.0) AS item_sale_price, item_description "
                    "FROM article_stock_local WHERE id = ?",
                    (article_id,)
                )
                row = cur.fetchone()
                if row:
                    details["item_code"] = row["item_code"] or f"#{article_id}"
                    details["item_designation"] = row["item_designation"] or "-"
                    details["item_measurement_unit"] = row["item_measurement_unit"] or "-"
                    details["item_quantity"] = str(row["item_quantity"])
                    try:
                        details["item_sale_price"] = f"{float(row['item_sale_price']):.2f}"
                    except Exception:
                        details["item_sale_price"] = str(row["item_sale_price"])
                    details["item_description"] = row["item_description"] or ""
                try:
                    conn.close()
                except Exception:
                    pass
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass

            # afficher les champs (lecture seule)
            _row("Code article :", details["item_code"], 0)
            _row("Désignation :", details["item_designation"], 1)
            _row("Unité :", details["item_measurement_unit"], 2)
            _row("Quantité :", details["item_quantity"], 3)
            _row("Prix vente :", details["item_sale_price"], 4)
            _row("Description :", details["item_description"], 5)

            # zone d'actions (seulement Fermer)
            actions = tk.Frame(modal, bg="#ffffff", pady=8)
            actions.pack(fill="x", padx=12, pady=(0,12))
            def _close():
                try:
                    modal.grab_release()
                except Exception:
                    pass
                try:
                    modal.destroy()
                except Exception:
                    pass

            btn_close = ttk.Button(actions, text="Fermer", command=_close)
            btn_close.pack(side="right")

            # rendre la taille minimale et centrer la modale par rapport au parent (défensif)
            modal.update_idletasks()
            try:
                w = modal.winfo_reqwidth()
                h = modal.winfo_reqheight()
                px = parent.winfo_rootx()
                py = parent.winfo_rooty()
                pw = parent.winfo_width()
                ph = parent.winfo_height()
                x = px + max(0, (pw - w) // 2)
                y = py + max(0, (ph - h) // 2)
                modal.geometry(f"+{x}+{y}")
            except Exception:
                pass

        except Exception:
            try:
                messagebox.showinfo("Voir article", f"Voir article id={article_id}")
            except Exception:
                pass

    # remove commander button usage: no-op placeholder kept for compatibility
    def _commander_article(article_id):
        # Commander button intentionally ignored per request
        return

    # refresh implementation
    def _refresh(p=page):
        # récupérer métriques (période par défaut : hier -> demain)
        try:
            metrics = fetch_overview_metrics(
                contribuable_id=contrib_id,
                lowstock_threshold=low_threshold,
                lowstock_limit=page_size * 100,
                period_from=None,
                period_to=None
            )
        except Exception as e:
            try:
                err_lbl = tk.Label(parent, text=f"Erreur lecture métriques : {e}", bg="#f6f8fa", fg="#900", font=("Segoe UI", 10))
                err_lbl.grid(row=3, column=0, sticky="nw", padx=12, pady=8)
            except Exception:
                pass
            return

        # update metrics (manager sees all)
        try:
            metrics_labels["Articles totaux (count)"].config(text=str(metrics["total_items_count"]))
            metrics_labels[f"Alertes stock ≤ {low_threshold}"].config(text=str(len(metrics["low_stock"])))
            metrics_labels["Mouvements (période)"].config(text=str(metrics["total_transactions"]))
            metrics_labels["Factures (période)"].config(text=str(metrics["total_factures"]))
        except Exception:
            pass

        rows = metrics.get("low_stock", []) or []
        total_rows = len(rows)
        total_pages = max(1, math.ceil(total_rows / page_size))
        current_page = max(1, min(p, total_pages))

        start = (current_page - 1) * page_size
        end = start + page_size
        page_slice = rows[start:end]

        # clear inner_rows
        try:
            for child in list(inner_rows.winfo_children()):
                try:
                    child.destroy()
                except Exception:
                    pass
        except Exception:
            pass

        if not page_slice:
            try:
                lbl_empty.grid(row=0, column=0, sticky="w", padx=6, pady=8)
            except Exception:
                try:
                    lbl = tk.Label(inner_rows, text="Aucun article en dessous du seuil.", bg="#ffffff", fg="#666", font=("Segoe UI", 10))
                    lbl.grid(row=0, column=0, sticky="w", padx=6, pady=8)
                except Exception:
                    pass
        else:
            for r_idx, r in enumerate(page_slice):
                aid = r.get("id")
                code = r.get("item_code") or f"#{aid}"
                designation = r.get("item_designation") or "-"
                unite = r.get("item_measurement_unit") or "-"
                qty = r.get("item_quantity") or 0

                lbl0 = tk.Label(inner_rows, text=str(code), bg="#ffffff", anchor="w", font=("Segoe UI", 10))
                lbl1 = tk.Label(inner_rows, text=str(designation), bg="#ffffff", anchor="w", font=("Segoe UI", 10))
                lbl2 = tk.Label(inner_rows, text=str(unite), bg="#ffffff", anchor="w", font=("Segoe UI", 10))
                lbl3 = tk.Label(inner_rows, text=str(qty), bg="#ffffff", anchor="e", font=("Segoe UI", 10, "bold"))

                btn_frame = tk.Frame(inner_rows, bg="#ffffff")
                btn_voir = ttk.Button(btn_frame, text="Voir", command=lambda _id=aid: _voir_article(_id))
                btn_voir.pack(side="left", padx=(0,6))
                # Commander button intentionally not created

                lbl0.grid(row=r_idx, column=0, sticky="ew", padx=4, pady=6)
                lbl1.grid(row=r_idx, column=1, sticky="ew", padx=4, pady=6)
                lbl2.grid(row=r_idx, column=2, sticky="ew", padx=4, pady=6)
                lbl3.grid(row=r_idx, column=3, sticky="ew", padx=4, pady=6)
                btn_frame.grid(row=r_idx, column=4, sticky="e", padx=4, pady=6)

                inner_rows.grid_columnconfigure(0, weight=1)
                inner_rows.grid_columnconfigure(1, weight=3)
                inner_rows.grid_columnconfigure(2, weight=1)
                inner_rows.grid_columnconfigure(3, weight=1)
                inner_rows.grid_columnconfigure(4, weight=1)

        # pagination info
        try:
            lbl_page_info.config(text=f"Page {current_page} / {total_pages} — {total_rows} résultat(s)")
        except Exception:
            pass

        def _set_btn_state(btn, enabled):
            try:
                if enabled:
                    btn.state(["!disabled"])
                else:
                    btn.state(["disabled"])
            except Exception:
                try:
                    btn.config(state="normal" if enabled else "disabled")
                except Exception:
                    pass

        _set_btn_state(btn_prev, current_page > 1)
        _set_btn_state(btn_next, current_page < total_pages)

        def _go_prev():
            new_p = max(1, current_page - 1)
            _refresh(new_p)
        def _go_next():
            new_p = min(total_pages, current_page + 1)
            _refresh(new_p)

        btn_prev.config(command=_go_prev)
        btn_next.config(command=_go_next)

    # initial load
    _refresh(page)

    return {"refresh": _refresh}
