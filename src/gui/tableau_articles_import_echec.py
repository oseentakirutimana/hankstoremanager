# gestion_obr_failed_and_retry_full.py
# -*- coding: utf-8 -*-
"""
Interface Tkinter pour afficher les enregistrements de mouvement_stock_importe en échec (obr_status = 0)
et proposer un bouton Réessayer fonctionnel et robuste.

Caractéristiques principales :
- En-tête et contrôles de filtrage date (Date de / Date à) conformes à la maquette fournie.
- Lecture sécurisée de la table mouvement_stock_importe (PRAGMA) et insert/update dynamiques.
- Ne jamais écraser source_json par un objet d'erreur ; preservation du payload original.
- Reconstruction du payload à partir des colonnes DB si source_json est incomplet.
- Vérification des champs obligatoires avant envoi pour éviter 400 côté API.
- Gestion et log détaillé des requêtes HTTP / SQL (logger.debug).
- Mise à jour de obr_status (1=succès, 2=erreur client, 0=pending) et last_attempt_date.
- Bouton Voir pour inspecter source_json et bouton Réessayer pour tenter l'envoi.
- Adapte get_connection() et obtenir_token_auto() à ton projet réel si besoin.
"""
from __future__ import annotations
import json
import logging
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox
from tkcalendar import DateEntry

import requests

# ---------------- configuration / adaptateurs ----------------
DB_PATH = "facturation_obr.db"        # adapte si nécessaire
OBR_API_URL = "https://ebms.obr.gov.bi:9443/ebms_api/AddStockMovementImporters/"
REQUEST_TIMEOUT = 30

from database.connection import get_connection
from api.obr_client import obtenir_token_auto, get_system_id

# ---------------- logging ----------------
logger = logging.getLogger("gestion_obr")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(h)
logger.setLevel(logging.DEBUG)

# ---------------- UI constants ----------------
CONTENT_BG = "#f6f8fa"
CARD_BG = "#ffffff"
CONTOUR_BG = "#e6eef9"
TITLE_FG = "#0b3d91"
LABEL_FG = "#1f2937"
ROW_ALT = "#fbfdff"
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_LABEL = ("Segoe UI", 10)
FONT_CELL = ("Segoe UI", 9)

# ---------------- utilitaires ----------------
def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _to_float_safe(v, default=0.0):
    try:
        if v is None or str(v).strip() == "":
            return float(default)
        s = str(v).strip().replace("\u00A0", "").replace(" ", "")
        if s.count(",") > 0 and s.count(".") == 0:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return float(default)

# ---------------- DB helpers / schema safety ----------------
def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    logger.debug("PRAGMA table_info(%s) -> columns: %s", table, cols)
    return column in cols

def ensure_msi_columns(conn: sqlite3.Connection) -> None:
    """
    Ensure mouvement_stock_importe has runtime columns we use.
    Adds them if missing. Safe to call repeatedly.
    """
    cur = conn.cursor()
    changed = False
    if not _table_has_column(conn, "mouvement_stock_importe", "obr_status"):
        logger.debug("Adding column 'obr_status' to mouvement_stock_importe")
        cur.execute("ALTER TABLE mouvement_stock_importe ADD COLUMN obr_status INTEGER DEFAULT 0;")
        changed = True
    if not _table_has_column(conn, "mouvement_stock_importe", "last_attempt_date"):
        logger.debug("Adding column 'last_attempt_date' to mouvement_stock_importe")
        cur.execute("ALTER TABLE mouvement_stock_importe ADD COLUMN last_attempt_date TEXT;")
        changed = True
    if not _table_has_column(conn, "mouvement_stock_importe", "source_json"):
        logger.debug("Adding column 'source_json' to mouvement_stock_importe (if missing)")
        try:
            cur.execute("ALTER TABLE mouvement_stock_importe ADD COLUMN source_json TEXT;")
            changed = True
        except Exception:
            logger.debug("Could not add source_json (may already exist)")
    if changed:
        conn.commit()
        logger.debug("Schema changes applied to mouvement_stock_importe")

# ---------------- fetching / updating records ----------------
def fetch_failed_imports(date_from: Optional[str] = None, date_to: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retourne les enregistrements de mouvement_stock_importe en échec ou en erreur client.
    - obr_status IN (0, 2) (0 = pending/failed, 2 = erreur client)
    - filtres optionnels date_from / date_to (format attendu: 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM:SS')
    - utilise des paramètres SQL pour éviter l'injection
    - ferme correctement la connexion en cas d'exception
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        base_cols = [
            "id", "created_at", "item_movement_date", "item_code", "item_designation", "item_quantity",
            "obr_status", "source_json", "last_attempt_date",
            "reference_dmc", "rubrique_tarifaire", "nombre_par_paquet", "description_paquet",
            "item_measurement_unit", "item_cost_price", "item_movement_type"
        ]
        q = f"SELECT {', '.join(base_cols)} FROM mouvement_stock_importe WHERE obr_status IN (0, 2)"
        params: List[Any] = []

        if date_from:
            q += " AND item_movement_date >= ?"
            params.append(date_from)
        if date_to:
            q += " AND item_movement_date <= ?"
            params.append(date_to)

        q += " ORDER BY created_at DESC"

        logger.debug("Executing query to fetch failed imports: %s ; params=%s", q, params)
        cur.execute(q, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                logger.exception("Failed to close DB connection in fetch_failed_imports")


def update_import_result_preserve_source(local_id: int, response_obj: Dict[str, Any], new_status: int) -> None:
    """
    Update obr_status and last_attempt_date while preserving source_json.
    If you want to store response separately, add a column source_json_response and adapt here.
    """
    conn = get_connection()
    cur = conn.cursor()
    now = now_ts()
    # prefer to keep source_json unchanged; only update obr_status & last_attempt_date
    sql = "UPDATE mouvement_stock_importe SET obr_status = ?, last_attempt_date = ? WHERE id = ?"
    params = (new_status, now, local_id)
    logger.debug("Executing SQL UPDATE movement status (preserve source): %s ; params=%s", sql, params)
    cur.execute(sql, params)
    conn.commit()
    conn.close()

def update_import_result_with_response(local_id: int, response_obj: Dict[str, Any], new_status: int) -> None:
    """
    Update obr_status, last_attempt_date and write response_obj into source_json_response if present,
    otherwise overwrite source_json (fallback).
    """
    conn = get_connection()
    cur = conn.cursor()
    now = now_ts()
    # check if column source_json_response exists
    if _table_has_column(conn, "mouvement_stock_importe", "source_json_response"):
        sql = "UPDATE mouvement_stock_importe SET source_json_response = ?, obr_status = ?, last_attempt_date = ? WHERE id = ?"
        params = (json.dumps(response_obj, ensure_ascii=False), new_status, now, local_id)
    else:
        # fallback: update source_json with a wrapper containing both original payload and response (only if you accept overriding)
        # To avoid losing original payload, we try to preserve original payload into a wrapper
        cur.execute("SELECT source_json FROM mouvement_stock_importe WHERE id = ?", (local_id,))
        r = cur.fetchone()
        orig = r[0] if r and r[0] else None
        wrapper = {"original_source_json": None, "response": response_obj}
        try:
            wrapper["original_source_json"] = json.loads(orig) if orig else None
        except Exception:
            wrapper["original_source_json"] = orig
        sql = "UPDATE mouvement_stock_importe SET source_json = ?, obr_status = ?, last_attempt_date = ? WHERE id = ?"
        params = (json.dumps(wrapper, ensure_ascii=False), new_status, now, local_id)
    logger.debug("Executing SQL UPDATE mouvement_stock_importe (response): %s ; params=%s", sql, params)
    cur.execute(sql, params)
    conn.commit()
    conn.close()

# ---------------- réseau / envoi ----------------
def send_payload(payload: Dict[str, Any], token: Optional[str]) -> Tuple[bool, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        logger.debug("Sending payload to OBR: url=%s payload=%s headers=%s", OBR_API_URL, json.dumps(payload, ensure_ascii=False), headers)
        resp = requests.post(OBR_API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT, verify=True)
        logger.debug("Response status=%s text=%s", resp.status_code, resp.text[:2000])
        try:
            body = resp.json()
        except Exception:
            body = {"http_status": resp.status_code, "text": resp.text}
        ok = (resp.status_code == 200 and isinstance(body, dict) and (body.get("success") or body.get("status") in (1, "1") or body.get("code") in (0, 0.0)))
        return ok, {"http_status": resp.status_code, "body": body}
    except requests.RequestException as e:
        logger.exception("Network error when sending to OBR")
        return False, {"error": str(e)}

# ---------------- UI frame ----------------
class FailedImportsFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=CONTENT_BG)
        self.parent = parent
        self.pack(fill="both", expand=True)
        self.row_widgets: List[List[tk.Widget]] = []
        self._build_ui()
        self._load_data_async()

    def _build_ui(self):
        # Header area
        title_frame = tk.Frame(self, bg=CONTENT_BG)
        title_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(8,6))
        title_frame.columnconfigure(0, weight=1)
        tk.Label(title_frame, text="Mouvements des articles importés déclarés à l'OBR non réuissis", font=FONT_TITLE, bg=CONTENT_BG, fg=TITLE_FG).grid(row=0, column=0, sticky="w")
        tk.Label(title_frame, text="Filtrer par date puis cliquez Recherche", font=FONT_LABEL, bg=CONTENT_BG, fg=LABEL_FG).grid(row=1, column=0, sticky="w", pady=(2,6))

        # Controls
        ctrl = tk.Frame(self, bg=CONTENT_BG)
        ctrl.grid(row=1, column=0, sticky="ew", padx=12, pady=(0,8))
        for i in range(6):
            ctrl.columnconfigure(i, weight=0)
        tk.Label(ctrl, text="Date de :", font=FONT_LABEL, bg=CONTENT_BG, fg=LABEL_FG).grid(row=0, column=0, sticky="w", padx=(0,6))
        self.date_from_var = tk.StringVar()
        e_from = DateEntry(ctrl, textvariable=self.date_from_var, date_pattern="yyyy-mm-dd", width=12)
        e_from.grid(row=0, column=1, padx=(0,12))
        tk.Label(ctrl, text="Date à :", font=FONT_LABEL, bg=CONTENT_BG, fg=LABEL_FG).grid(row=0, column=2, sticky="w", padx=(0,6))
        self.date_to_var = tk.StringVar()
        e_to = DateEntry(ctrl, textvariable=self.date_to_var, date_pattern="yyyy-mm-dd", width=12)
        e_to.grid(row=0, column=3, padx=(0,12))
        btn_search = tk.Button(ctrl, text="Recherche", bg="#2563eb", fg="white", activebackground="#1e40af", padx=8)
        btn_search.grid(row=0, column=4, padx=(8,6))
        btn_refresh = tk.Button(ctrl, text="Rafraîchir", bg="#9ca3af", fg="white", activebackground="#6b7280", padx=8)
        btn_refresh.grid(row=0, column=5, padx=(8,6))

        # Table container
        self.table_frame = tk.Frame(self, bg=CONTOUR_BG, bd=1, relief="solid")
        self.table_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0,8))
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Define columns and header
        self.cols = [
            ("id", "Mvt ID", 6),
            ("item_movement_date", "Date", 18),
            ("item_code", "Code article", 10),
            ("item_designation", "Désignation", 36),
            ("item_quantity", "Qté", 8),
            ("last_attempt_date", "Dernière tentative", 18)
        ]
        total_cols = len(self.cols)
        for c, (_k, label, w) in enumerate(self.cols):
            hdr = tk.Label(self.table_frame, text=label, bg="#eef6ff", fg=LABEL_FG, font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=8, pady=6)
            hdr.grid(row=0, column=c, sticky="nsew")
            self.table_frame.grid_columnconfigure(c, weight=w, minsize=w * 6)
        hdr_actions = tk.Label(self.table_frame, text="Actions", bg="#eef6ff", fg=LABEL_FG, font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=8, pady=6)
        hdr_actions.grid(row=0, column=total_cols, sticky="nsew")
        self.table_frame.grid_columnconfigure(total_cols, weight=0, minsize=160)

        # Wire buttons
        btn_search.config(command=lambda: self._load_data_async())
        btn_refresh.config(command=lambda: self._load_data_async())

    def _clear_rows(self):
        for widgets in self.row_widgets:
            for w in widgets:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.row_widgets = []
        for child in list(self.table_frame.grid_slaves()):
            info = child.grid_info()
            if int(info.get("row", 0)) >= 1:
                try:
                    child.destroy()
                except Exception:
                    pass

    def _load_data_async(self):
        threading.Thread(target=self._load_data, daemon=True).start()

    def _load_data(self):
        df = parse_date_input(self.date_from_var.get())
        dt = parse_date_input(self.date_to_var.get())
        try:
            data = fetch_failed_imports(date_from=df, date_to=dt)
            self.after(0, lambda: self._render_rows(data))
        except Exception as e:
            logger.exception("Failed to load failed imports: %s", e)
            self.after(0, lambda: messagebox.showerror("Erreur", f"Impossible de charger les données: {e}", parent=self))

    def _render_rows(self, data: List[Dict[str, Any]]):
        self._clear_rows()
        if not data:
            lbl = tk.Label(self.table_frame, text="Aucun enregistrement en échec pour les filtres choisis.", bg=CARD_BG, fg=LABEL_FG, font=FONT_CELL, padx=8, pady=12)
            lbl.grid(row=1, column=0, columnspan=len(self.cols)+1, sticky="nsew")
            return

        for r_index, row in enumerate(data, start=1):
            widgets_row: List[tk.Widget] = []
            bg = ROW_ALT if (r_index % 2 == 0) else CARD_BG
            for c, (key, _label, _w) in enumerate(self.cols):
                val = row.get(key, "")
                if key in ("item_movement_date", "last_attempt_date") and val:
                    try:
                        val = str(val)[:19]
                    except Exception:
                        pass
                cell = tk.Label(self.table_frame, text=str(val), bg=bg, fg=LABEL_FG, font=FONT_CELL, bd=1, relief="solid", anchor="w", padx=8, pady=6)
                cell.grid(row=r_index, column=c, sticky="nsew")
                widgets_row.append(cell)
            # Actions
            actions_col = len(self.cols)
            action_container = tk.Frame(self.table_frame, bg=bg)
            action_container.grid(row=r_index, column=actions_col, sticky="nsew", padx=4, pady=4)
            view_btn = tk.Button(action_container, text="Voir", bg="#2563eb", fg="white", activebackground="#1e40af", command=lambda _r=row: self._on_view(_r), padx=8)
            view_btn.pack(side="left", padx=(6,6))
            retry_btn = tk.Button(action_container, text="Réessayer", bg="#f59e0b", fg="white", activebackground="#d97706", command=lambda _r=row: self._on_retry_async(_r), padx=8)
            retry_btn.pack(side="left", padx=(0,6))
            widgets_row.append(action_container)
            self.row_widgets.append(widgets_row)

    def _on_view(self, row: Dict[str, Any]):
        top = tk.Toplevel(self)
        top.title(f"Détail import id={row.get('id')}")
        top.geometry("720x420")
        panel = tk.Frame(top, padx=12, pady=12)
        panel.pack(fill="both", expand=True)
        txt = tk.Text(panel, wrap="word", height=20)
        txt.pack(fill="both", expand=True)
        try:
            pretty = json.dumps(json.loads(row.get("source_json") or "{}"), ensure_ascii=False, indent=2)
        except Exception:
            pretty = str(row.get("source_json"))
        txt.insert("1.0", pretty)
        txt.configure(state="disabled")
        tk.Button(panel, text="Fermer", command=top.destroy).pack(pady=(8,0))

    def _on_retry_async(self, row: Dict[str, Any]):
        threading.Thread(target=self._on_retry, args=(row,), daemon=True).start()

    def _on_retry(self, row: Dict[str, Any]):
        local_id = row.get("id")
        # relire payload + fallback fields depuis la DB
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("""
                SELECT source_json, item_code, item_designation, item_quantity, item_measurement_unit,
                       item_cost_price, item_movement_type, item_movement_date, reference_dmc, rubrique_tarifaire,
                       nombre_par_paquet, description_paquet
                FROM mouvement_stock_importe WHERE id = ?
            """, (local_id,))
            r = cur.fetchone()
            conn.close()
            if not r:
                self.after(0, lambda: messagebox.showerror("Erreur", f"Enregistrement introuvable id={local_id}", parent=self))
                return
            raw_source = r[0]
            db_fields = {
                "item_code": r[1],
                "item_designation": r[2],
                "item_quantity": r[3],
                "item_measurement_unit": r[4],
                "item_cost_price": r[5],
                "item_movement_type": r[6],
                "item_movement_date": r[7],
                "reference_dmc": r[8],
                "rubrique_tarifaire": r[9],
                "nombre_par_paquet": r[10],
                "description_paquet": r[11],
            }
        except Exception as e:
            logger.exception("Impossible de lire source_json depuis DB for id=%s: %s", local_id, e)
            self.after(0, lambda: messagebox.showerror("Erreur", f"Impossible de lire le payload en base: {e}", parent=self))
            return

        payload = None
        # Try to parse existing source_json only if it looks like a valid payload
        if raw_source:
            try:
                parsed = json.loads(raw_source)
                required = [
                    "system_or_device_id", "item_code", "item_designation", "item_quantity",
                    "item_measurement_unit", "item_cost_price", "item_movement_type",
                    "item_movement_date", "reference_dmc", "rubrique_tarifaire", "nombre_par_paquet", "description_paquet"
                ]
                has_required = all((k in parsed and parsed.get(k) not in (None, "")) for k in required)
                if has_required:
                    payload = parsed
                else:
                    payload = None
            except Exception:
                payload = None

        # If absent or incomplete, rebuild payload from DB columns
        if payload is None:
            payload = {
                "system_or_device_id": get_system_id() or "",
                "item_code": str(db_fields.get("item_code") or ""),
                "item_designation": str(db_fields.get("item_designation") or ""),
                "item_quantity": str(db_fields.get("item_quantity") or 0),
                "item_measurement_unit": str(db_fields.get("item_measurement_unit") or "UN"),
                "item_cost_price": str(db_fields.get("item_cost_price") or 0),
                "item_purchase_or_sale_price": str(db_fields.get("item_cost_price") or 0),  # some APIs expect this key
                "item_purchase_or_sale_currency": "BIF",
                "item_movement_type": str(db_fields.get("item_movement_type") or "EN"),
                "item_movement_date": str(db_fields.get("item_movement_date") or now_ts()),
                "item_movement_invoice_ref": "",
                "item_movement_description": str(db_fields.get("description_paquet") or ""),
                "reference_dmc": str(db_fields.get("reference_dmc") or ""),
                "rubrique_tarifaire": str(db_fields.get("rubrique_tarifaire") or ""),
                "nombre_par_paquet": str(db_fields.get("nombre_par_paquet") or 1),
                "description_paquet": str(db_fields.get("description_paquet") or ""),
            }

        # Final check for required fields
        missing = [k for k in (
            "system_or_device_id","item_code","item_designation","item_quantity","item_measurement_unit",
            "item_cost_price","item_movement_type","item_movement_date","reference_dmc","rubrique_tarifaire",
            "nombre_par_paquet","description_paquet"
        ) if not payload.get(k)]
        if missing:
            # mark as client error (2) to avoid retry storms and inform user
            resp_obj = {"error": "payload_incomplete", "missing": missing}
            update_import_result_with_response(local_id, resp_obj, 2)
            self.after(0, lambda: messagebox.showerror("Erreur payload", f"Payload incomplet pour id={local_id}. Champs manquants: {', '.join(missing)}", parent=self))
            return

        # Obtain token
        token = obtenir_token_auto()
        if not token:
            # Do not overwrite source_json; update only last_attempt_date / obr_status=0 (pending)
            try:
                update_import_result_preserve_source(local_id, {"error": "token_introuvable"}, 0)
            except Exception:
                logger.exception("Failed updating record when token missing for id=%s", local_id)
            self.after(0, lambda: messagebox.showwarning("Token manquant", "Impossible d'obtenir un token OBR. Vérifie la configuration d'auth.", parent=self))
            self._load_data_async()
            return

        # Send payload
        ok, resp_obj = send_payload(payload, token)
        if ok:
            # success: update record and optionally store response
            try:
                update_import_result_with_response(local_id, resp_obj, 1)
            except Exception:
                logger.exception("Failed updating success record for id=%s", local_id)
            self.after(0, lambda: messagebox.showinfo("Succès", f"Envoi réussi pour id={local_id}", parent=self))
        else:
            # if 400/403 -> mark 2 (client error), else keep pending (0)
            http_status = resp_obj.get("http_status") if isinstance(resp_obj, dict) else None
            status_to_set = 2 if http_status in (400, 403) else 0
            try:
                update_import_result_with_response(local_id, resp_obj, status_to_set)
            except Exception:
                logger.exception("Failed updating failure record for id=%s", local_id)
            short = json.dumps(resp_obj, ensure_ascii=False)[:1000]
            self.after(0, lambda: messagebox.showerror("Échec", f"Envoi échoué pour id={local_id}\n\n{short}", parent=self))

        # Refresh
        self._load_data_async()

# ---------------- utilities ----------------
def parse_date_input(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

# ---------------- runner ----------------
def main():
    root = tk.Tk()
    root.geometry("1100x620")
    app = FailedImportsFrame(root)
    root.title("Mouvements importés en échec - Réessayer")
    root.mainloop()

if __name__ == "__main__":
    main()
