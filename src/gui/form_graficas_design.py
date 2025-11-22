# gui/form_graficas_design.py
import tkinter as tk
from tkinter import ttk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from datetime import date, timedelta
import threading
import sqlite3
import logging
import math

logger = logging.getLogger(__name__)

# try to reuse your app connection helper (fallback included)
try:
    from database.connection import get_connection
except Exception:
    def get_connection(path: str = None):
        p = path or "facturation_obr.db"
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        return conn

class FormulaireGraphiquesDesign:
    """
    Widget Matplotlib intégré.
    Usage:
        FormulaireGraphiquesDesign(parent_frame, days=30, lowstock_top=8, contrib_id=None)
    Fournit méthode refresh() pour forcer la mise à jour.
    """

    def __init__(self, panel_principal, days=30, lowstock_top=8, contrib_id=None):
        self.parent = panel_principal
        self.days = max(7, int(days))
        self.lowstock_top = max(3, int(lowstock_top))
        self.contrib_id = contrib_id

        # container
        self.container = tk.Frame(self.parent, bg="#f6f8fa")
        self.container.pack(fill="both", expand=True)

        # header
        hdr = tk.Frame(self.container, bg="#f6f8fa")
        hdr.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(hdr, text="📈 Dashboard Graphiques", bg="#f6f8fa", fg="#0b3d91", font=("Segoe UI", 14, "bold")).pack(side="left", padx=(4,6))
        btn_refresh = ttk.Button(hdr, text="Rafraîchir", command=self._debounced_refresh)
        btn_refresh.pack(side="right", padx=6)
        self._status_lbl = tk.Label(hdr, text="", bg="#f6f8fa", fg="#333", font=("Segoe UI", 9))
        self._status_lbl.pack(side="right", padx=(0,12))

        # Figure + axes (created once)
        self.fig = Figure(figsize=(10,7), dpi=110)
        self.ax_tx = self.fig.add_subplot(211)
        self.ax_low = self.fig.add_subplot(212)
        self.fig.subplots_adjust(hspace=0.45, left=0.12, right=0.95, top=0.95, bottom=0.12)

        # Canvas + toolbar
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.container)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill="both", expand=True)
        try:
            toolbar = NavigationToolbar2Tk(self.canvas, self.container)
            toolbar.update()
            toolbar.pack(fill="x", side="bottom")
        except Exception:
            pass

        # threading state
        self._lock = threading.Lock()
        self._pending_timer = None
        self._last_data_hash = None

        # initial draw
        self.refresh()

    # debounce refresh
    def _debounced_refresh(self, delay_ms=250):
        try:
            if self._pending_timer:
                self.parent.after_cancel(self._pending_timer)
        except Exception:
            pass
        self._pending_timer = self.parent.after(delay_ms, lambda: threading.Thread(target=self.refresh, daemon=True).start())

    def refresh(self):
        with self._lock:
            self._set_status("Chargement...")
            try:
                tx_labels, tx_values = self._fetch_transactions_last_n_days(self.days, self.contrib_id)
                low_labels, low_qtys = self._fetch_lowstock_top(self.lowstock_top, self.contrib_id)
            except Exception as e:
                logger.exception("Erreur fetch pour graphiques: %s", e)
                self.parent.after(0, lambda: self._set_status(f"Erreur: {e}"))
                return

            try:
                data_hash = (tuple(tx_values), tuple(low_qtys))
                if data_hash == self._last_data_hash:
                    self.parent.after(0, lambda: self._set_status("À jour"))
                    return
                self._last_data_hash = data_hash
            except Exception:
                pass

            self.parent.after(0, lambda: self._update_plots(tx_labels, tx_values, low_labels, low_qtys))
            self.parent.after(0, lambda: self._set_status("À jour"))

    def _set_status(self, text):
        try:
            self._status_lbl.config(text=text)
        except Exception:
            pass

    # ---- data fetchers ----
    def _fetch_transactions_last_n_days(self, n_days: int, contrib_id=None):
        end = date.today()
        start = end - timedelta(days=n_days - 1)
        day_list = [(start + timedelta(days=i)).isoformat() for i in range(n_days)]
        counts = {d: 0 for d in day_list}

        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            if contrib_id:
                q = ("SELECT item_movement_date AS d, COUNT(1) AS c FROM mouvement_stock "
                     "WHERE contribuable_id = ? AND item_movement_date BETWEEN ? AND ? "
                     "GROUP BY item_movement_date")
                cur.execute(q, (contrib_id, start.isoformat(), end.isoformat()))
            else:
                q = ("SELECT item_movement_date AS d, COUNT(1) AS c FROM mouvement_stock "
                     "WHERE item_movement_date BETWEEN ? AND ? GROUP BY item_movement_date")
                cur.execute(q, (start.isoformat(), end.isoformat()))
            for row in cur.fetchall():
                d = row["d"]
                if isinstance(d, str) and len(d) > 10:
                    d = d[:10]
                if d in counts:
                    counts[d] = int(row["c"] or 0)
        finally:
            conn.close()

        labels = day_list
        values = [counts[d] for d in labels]
        return labels, values

    def _fetch_lowstock_top(self, top_n: int, contrib_id=None):
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            if contrib_id:
                cur.execute(
                    "SELECT item_code, item_designation, item_quantity FROM article_stock_local "
                    "WHERE contribuable_id = ? ORDER BY item_quantity ASC LIMIT ?",
                    (contrib_id, top_n)
                )
            else:
                cur.execute(
                    "SELECT item_code, item_designation, item_quantity FROM article_stock_local "
                    "ORDER BY item_quantity ASC LIMIT ?",
                    (top_n,)
                )
            rows = cur.fetchall()
        finally:
            conn.close()

        labels = []
        qtys = []
        for r in rows:
            code = (r["item_code"] or "").strip()
            name = (r["item_designation"] or "").strip()
            qty = float(r["item_quantity"] or 0.0)
            lab = f"{code} — {name}" if code else name
            if len(lab) > 48:
                lab = lab[:45] + "..."
            labels.append(lab)
            qtys.append(qty)
        return labels, qtys

    # ---- plotting (UI thread) ----
    def _update_plots(self, tx_labels, tx_values, low_labels, low_qtys):
        # Transactions (top)
        ax = self.ax_tx
        ax.clear()
        total = len(tx_labels)
        if total == 0 or all(v == 0 for v in tx_values):
            ax.text(0.5, 0.5, "Aucune transaction récente", ha="center", va="center", transform=ax.transAxes, fontsize=11)
        else:
            x = list(range(total))
            colors = ["#16a34a" if v > 0 else "#c7e6d1" for v in tx_values]
            ax.bar(x, tx_values, color=colors, alpha=0.95, linewidth=0)
            step = max(1, math.ceil(total / 12))
            xticks = x[::step]
            xlabels = [tx_labels[i][5:] if tx_labels[i].startswith(str(date.today().year)) else tx_labels[i] for i in xticks]
            ax.set_xticks(xticks)
            ax.set_xticklabels(xlabels, rotation=45, ha="right", fontsize=8)
            ax.set_ylim(0, max(1, max(tx_values) * 1.15))
            ax.set_ylabel("Mouvements")
            ax.set_title(f"Transactions par jour (dernier{'s' if total!=1 else ''} {total} jours)")
            ax.grid(axis="y", linestyle="--", alpha=0.45)
            if total <= 20:
                for i, v in enumerate(tx_values):
                    if v:
                        ax.text(i, v + max(0.02 * max(tx_values), 0.1), str(int(v)), ha="center", va="bottom", fontsize=8)

        # Low stock (bottom)
        ax2 = self.ax_low
        ax2.clear()
        if not low_labels:
            ax2.text(0.5, 0.5, "Aucun article en rupture", ha="center", va="center", transform=ax2.transAxes, fontsize=11)
        else:
            y_pos = list(range(len(low_labels)))
            ax2.barh(y_pos, low_qtys, color="#dc2626", alpha=0.9)
            ax2.set_yticks(y_pos)
            ax2.set_yticklabels(low_labels, fontsize=9)
            ax2.invert_yaxis()
            ax2.set_xlabel("Quantité")
            ax2.set_title(f"Top {len(low_labels)} articles en stock le plus faible")
            max_qty = max(low_qtys) if low_qtys else 1
            ax2.set_xlim(0, max_qty * 1.2 if max_qty > 0 else 1)
            for i, v in enumerate(low_qtys):
                ax2.text(v + max(0.02 * max_qty, 0.1), i, f"{v:.2f}", va="center", fontsize=9)

        try:
            self.canvas.draw_idle()
        except Exception:
            try:
                self.canvas.draw()
            except Exception:
                pass

    # cleanup
    def destroy(self):
        try:
            self.canvas.get_tk_widget().destroy()
        except Exception:
            pass
        try:
            self.container.destroy()
        except Exception:
            pass
