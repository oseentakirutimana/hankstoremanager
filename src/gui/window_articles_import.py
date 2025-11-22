# gestion_obr_import_batch_frame_final_fixed.py
# -*- coding: utf-8 -*-
"""
ImportStockBatchFrame — version complète et corrigée.

Corrections appliquées :
- Injection forcée de system_or_device_id dans tous les payloads avant envoi à l'OBR
  (send_worker et _bg_send_local_row).
- Conserve le comportement précédent pour la création/validation locale et les updates.
- Construction dynamique de l'INSERT dans mouvement_stock_importe (évite mismatches colonnes/valeurs)
- Ajout automatique des colonnes manquantes (obr_status, last_attempt_date, source_json) si nécessaire
- Logging détaillé (logger.debug) pour SQL + params
- Mise à jour/création article_stock_local
- Envois OBR en threads, agrégation des résultats et résumé
- UI Tkinter embeddable (ImportStockBatchFrame)
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Tuple

import sqlite3
import logging

import tkinter as tk
from tkinter import ttk, messagebox

import requests

# Application-specific imports (adapte les chemins à ton projet)
from database.connection import get_connection
from api.obr_client import obtenir_token_auto, get_system_id

# ---------- Logging ----------
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# ---------- Constantes et utilitaires ----------
BG = "#f6f9fb"
PANEL_BG = "#ffffff"
PRIMARY = "#0b5ed7"
SUCCESS = "#198754"
DANGER = "#dc3545"
LABEL_FG = "#1f2d33"
LABEL_FONT = ("Segoe UI", 10)
INPUT_FONT = ("Segoe UI", 10)
TITLE_FONT = ("Segoe UI", 14, "bold")

IMPORT_MOVE_TYPES = ["EN", "ER", "EI", "EAJ", "ET", "EAU"]

PRICING_CHOICES = [
    ("Fixe", "fixed"),
    ("Marge en %", "markup_percent"),
    ("Marge en montant", "markup_amount"),
    ("Dernier coût", "last_cost"),
]

def _D(v):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")

def _quantize(v):
    return float(_D(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

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

def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ttk styles
_style_init = False
def _ensure_style():
    global _style_init
    if _style_init:
        return
    s = ttk.Style()
    try:
        s.theme_use("clam")
    except Exception:
        pass
    s.configure("Title.TLabel", font=TITLE_FONT, foreground=LABEL_FG, background=BG)
    s.configure("Label.TLabel", font=LABEL_FONT, foreground=LABEL_FG, background=BG)
    s.configure("Panel.TFrame", background=PANEL_BG)
    s.configure("Form.TEntry", padding=6)
    s.configure("ReadOnly.TEntry", padding=6)
    s.configure("Primary.TButton", foreground="white", background=PRIMARY)
    s.configure("Success.TButton", foreground="white", background=SUCCESS)
    s.configure("Danger.TButton", foreground="white", background=DANGER)
    _style_init = True

# ---------- Helpers DB pour compatibilité des schémas ----------
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

# ---------------- ImportLine (une ligne d'article) ----------------
class ImportLine(tk.Frame):
    def __init__(self, parent, idx: int, initial: Optional[Dict[str, Any]] = None, remove_cb=None):
        super().__init__(parent, bg=BG)
        _ensure_style()
        self.idx = idx
        self.remove_cb = remove_cb
        self.initial = initial or {}
        self.vars: Dict[str, tk.StringVar] = {}
        self.sale_var = tk.StringVar(value=str(self.initial.get("item_sale_price", "0.00")))
        self.pricing_strategy_var = tk.StringVar(value=self.initial.get("pricing_strategy_display", PRICING_CHOICES[1][0]))
        self.markup_var = tk.StringVar(value=str(self.initial.get("markup_value", "25.0")))
        self.mt_var = tk.StringVar(value=self.initial.get("item_movement_type", IMPORT_MOVE_TYPES[0]))
        self._build_ui()
        self._wire_events()
        self._compute_sale()

    def _build_ui(self):
        panel = ttk.Frame(self, style="Panel.TFrame", padding=(12,10))
        panel.pack(fill="x", expand=True, padx=8, pady=6)

        # Row 0
        self._mk(panel, "Code article", "item_code", row=0, col=0, width=18)
        self._mk(panel, "Désignation", "item_designation", row=0, col=2, width=24, default="")

        # Row 1
        self._mk(panel, "Quantité", "item_quantity", row=1, col=0, width=12, default="0")
        self._mk(panel, "Unité", "item_measurement_unit", row=1, col=2, width=12, default="unité")

        # Row 2
        self._mk(panel, "Coût unitaire (achat)", "item_cost_price", row=2, col=0, width=18, default="0.00")
        self._mk(panel, "Devise coût", "item_cost_price_currency", row=2, col=2, width=8, default=self.initial.get("item_cost_price_currency", "BIF"))

        # Row 3 - sale readonly + pricing strategy
        lbl_sale = ttk.Label(panel, text="Prix de vente unitaire (HT)", style="Label.TLabel")
        lbl_sale.grid(row=3, column=0, sticky="w", padx=(0,6), pady=6)
        ent_sale = ttk.Entry(panel, textvariable=self.sale_var, width=18, font=INPUT_FONT, style="ReadOnly.TEntry")
        ent_sale.grid(row=3, column=1, sticky="w", padx=(0,8), pady=6)
        try:
            ent_sale.state(["readonly"])
        except Exception:
            try:
                ent_sale.configure(state="readonly")
            except Exception:
                pass

        lbl_strat = ttk.Label(panel, text="Stratégie prix", style="Label.TLabel")
        lbl_strat.grid(row=3, column=2, sticky="w", padx=(6,6), pady=6)
        self.strat_cb = ttk.Combobox(panel, textvariable=self.pricing_strategy_var, values=[d for d,_ in PRICING_CHOICES], state="readonly", width=18)
        self.strat_cb.grid(row=3, column=3, sticky="w", padx=(0,8), pady=6)

        # Row 4 - markup + movement type
        lbl_markup = ttk.Label(panel, text="Valeur marge", style="Label.TLabel")
        lbl_markup.grid(row=4, column=0, sticky="w", padx=(0,6), pady=6)
        self.markup_entry = ttk.Entry(panel, textvariable=self.markup_var, width=12, font=INPUT_FONT, style="Form.TEntry")
        self.markup_entry.grid(row=4, column=1, sticky="w", padx=(0,8), pady=6)

        lbl_mt = ttk.Label(panel, text="Type mouvement", style="Label.TLabel")
        lbl_mt.grid(row=4, column=2, sticky="w", padx=(6,6), pady=6)
        self.mt_cb = ttk.Combobox(panel, textvariable=self.mt_var, values=IMPORT_MOVE_TYPES, state="readonly", width=8)
        self.mt_cb.grid(row=4, column=3, sticky="w", padx=(0,8), pady=6)

        # Row 5
        self._mk(panel, "Date (YYYY-MM-DD HH:MM:SS)", "item_movement_date", row=5, col=0, width=20, default=now_ts())
        self._mk(panel, "Réf DMC", "reference_dmc", row=5, col=2, width=24, default="")

        # Row 6
        self._mk(panel, "Rubrique tarifaire", "rubrique_tarifaire", row=6, col=0, width=24, default="")
        self._mk(panel, "Nbre par paquet", "nombre_par_paquet", row=6, col=2, width=10, default="1")

        # Row 7
        self._mk(panel, "TVA (%)", "taux_tva", row=7, col=0, width=8, default="18")
        self._mk(panel, "Taxe communale (CT)", "item_ct", row=7, col=2, width=8, default="0")

        # Row 8
        self._mk(panel, "Taxe licence (TL)", "item_tl", row=8, col=0, width=8, default="0")
        self._mk(panel, "Taxe spécifique (TSCE)", "item_tsce_tax", row=8, col=2, width=8, default="0")

        # Row 9 - Autres taxes (OTT) and Description paquet on same logical area
        self._mk(panel, "Autres taxes (OTT)", "item_ott_tax", row=9, col=0, width=8, default="0")
        self._mk(panel, "Description paquet", "description_paquet", row=9, col=2, width=24, default="")

        # bottom remove button (visible)
        btn = ttk.Button(panel, text="Supprimer", style="Danger.TButton", command=self._on_remove)
        btn.grid(row=10, column=3, sticky="e", padx=(0,8), pady=(8,0))

        # Ensure cost var exists
        if "item_cost_price" not in self.vars:
            self.vars["item_cost_price"] = tk.StringVar(value=str(self.initial.get("item_cost_price", "0.00")))

    def _mk(self, parent, label_text, key, row, col, width=24, default=""):
        lbl = ttk.Label(parent, text=label_text, style="Label.TLabel")
        lbl.grid(row=row, column=col, sticky="w", padx=(0,6), pady=6)
        val = str(self.initial.get(key, default)) if self.initial.get(key, default) is not None else ""
        var = tk.StringVar(value=val)
        ent = ttk.Entry(parent, textvariable=var, width=width, font=INPUT_FONT, style="Form.TEntry")
        ent.grid(row=row, column=col+1, sticky="w", padx=(0,8), pady=6)
        self.vars[key] = var
        return ent

    def _wire_events(self):
        try:
            cost_var = self.vars.get("item_cost_price")
            if cost_var:
                cost_var.trace_add("write", lambda *a: self._compute_sale())
        except Exception:
            pass
        try:
            self.pricing_strategy_var.trace_add("write", lambda *a: self._compute_sale())
        except Exception:
            pass
        try:
            self.markup_var.trace_add("write", lambda *a: self._compute_sale())
        except Exception:
            pass

    def _compute_sale(self):
        try:
            cost = _D(str(self.vars.get("item_cost_price").get() or "0"))
            strat_disp = self.pricing_strategy_var.get()
            strat_map = {d: c for d, c in PRICING_CHOICES}
            strat_code = strat_map.get(strat_disp, "markup_percent")
            markup_value = _D(str(self.markup_var.get() or "25.0"))
            if strat_code == "fixed":
                final = cost
            elif strat_code == "markup_percent":
                final = cost * (1 + (markup_value / _D("100")))
            elif strat_code == "markup_amount":
                final = cost + markup_value
            elif strat_code == "last_cost":
                final = cost * (1 + _D("0.25"))
            else:
                final = cost * (1 + (markup_value / _D("100")))
        except Exception:
            final = _D("0")
        self.sale_var.set(str(_quantize(final)))

    def _on_remove(self):
        if callable(self.remove_cb):
            self.remove_cb(self)

    def get_row(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        for k, var in self.vars.items():
            v = var.get().strip()
            if k in ("item_quantity", "item_cost_price", "taux_tva", "item_ct", "item_tl", "item_tsce_tax", "item_ott_tax", "nombre_par_paquet"):
                row[k] = _to_float_safe(v, 0.0)
            else:
                row[k] = v or None
        row["description_paquet"] = row.get("description_paquet") or ""
        row["item_sale_price"] = _to_float_safe(self.sale_var.get() or "0.00", 0.0)
        row["pricing_strategy_display"] = self.pricing_strategy_var.get()
        row["markup_value"] = _to_float_safe(self.markup_var.get() or "25.0", 25.0)
        row["item_movement_type"] = self.mt_var.get() or IMPORT_MOVE_TYPES[0]
        return row

# ---------------- ImportStockBatchFrame (embeddable) ----------------
class ImportStockBatchFrame(tk.Frame):
    def __init__(self,
                 parent,
                 get_connection_fn=get_connection,
                 obtenir_token_fn=obtenir_token_auto,
                 get_system_id_fn=get_system_id,
                 contribuable_id: Optional[int] = None,
                 *args, **kwargs):
        _ensure_style()
        super().__init__(parent, bg=BG, *args, **kwargs)
        self.get_connection = get_connection_fn
        self.obtenir_token_auto = obtenir_token_fn
        self.get_system_id = get_system_id_fn
        self.contribuable_id = contribuable_id
        self.lines: List[ImportLine] = []
        self._build_ui()

    def _build_ui(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=12, pady=(12,6))
        ttk.Label(header, text="📦 Import Articles (Batch) — Déclaration OBR", style="Title.TLabel").pack(side="left")
        btn_frame = tk.Frame(header, bg=BG)
        btn_frame.pack(side="right")
        ttk.Button(btn_frame, text="Ajouter ligne", command=self._add_line).pack(side="right", padx=6)
        ttk.Button(btn_frame, text="Enregistrer & Envoyer", command=lambda: self._on_save(send=True), style="Success.TButton").pack(side="right", padx=6)

        # container without scrollability
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True, padx=12, pady=(6,12))

        self.status_var = tk.StringVar(value="Prêt")
        ttk.Label(self, textvariable=self.status_var, style="Label.TLabel").pack(fill="x", padx=12, pady=(0,8))

        # initial line
        self._add_line()

    def _add_line(self, initial: Optional[Dict[str, Any]] = None):
        line = ImportLine(self.container, idx=len(self.lines), initial=initial, remove_cb=self._remove_line)
        line.pack(fill="x", pady=6, padx=6)
        self.lines.append(line)

    def _remove_line(self, line: ImportLine):
        if line in self.lines:
            line.destroy()
            self.lines.remove(line)
            for i, l in enumerate(self.lines):
                l.idx = i

    def _validate_all(self) -> Tuple[bool, Optional[str]]:
        if not self.lines:
            return False, "Aucune ligne à traiter"
        for i, ln in enumerate(self.lines, start=1):
            row = ln.get_row()
            if not row.get("item_code") or not row.get("item_designation") or not row.get("item_quantity"):
                return False, f"Ligne {i} : code, désignation et quantité requis"
            try:
                if row.get("item_movement_date"):
                    datetime.strptime(str(row.get("item_movement_date")), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return False, f"Ligne {i} : format date invalide (YYYY-MM-DD HH:MM:SS)"
            if row.get("item_movement_type") not in IMPORT_MOVE_TYPES:
                return False, f"Ligne {i} : type mouvement invalide (doit être {', '.join(IMPORT_MOVE_TYPES)})"
        return True, None

    def _set_status(self, text: str):
        self.status_var.set(text)

    def _on_save(self, send: bool):
        ok, err = self._validate_all()
        if not ok:
            messagebox.showwarning("Validation", err, parent=self)
            return
        rows = [ln.get_row() for ln in self.lines]
        threading.Thread(target=self._worker_save, args=(rows, send), daemon=True).start()
        self._set_status("Traitement en cours...")

    def _worker_save(self, rows: List[Dict[str, Any]], send: bool):
        results: List[Tuple[Optional[int], bool, Optional[str]]] = []
        # étape 1 : insertions locales
        for row in rows:
            try:
                row["system_id"] = self.get_system_id()
            except Exception:
                row["system_id"] = None
            try:
                local_id, stock_id = self._insert_local_and_sync_stock(row)
                results.append((local_id, True, None))
            except Exception as e:
                logger.exception("Local insert failed for row %s: %s", row.get("item_code"), e)
                results.append((None, False, str(e)))

        local_success = sum(1 for _, ok, _ in results if ok)
        local_fail = sum(1 for _, ok, _ in results if not ok)

        obr_success = 0
        obr_fail = 0
        obr_details: List[str] = []

        if send:
            send_tasks: List[Tuple[Dict[str, Any], int]] = []
            for local_id, ok_flag, _ in results:
                if ok_flag and local_id:
                    try:
                        conn = self.get_connection()
                        cur = conn.cursor()
                        cur.execute("SELECT source_json FROM mouvement_stock_importe WHERE id = ?", (local_id,))
                        r = cur.fetchone()
                        conn.close()
                        payload = json.loads(r[0]) if r and r[0] else None
                    except Exception as e:
                        payload = None
                        obr_details.append(f"id local {local_id}: impossible de lire payload depuis DB: {e}")
                    if payload is not None:
                        send_tasks.append((payload, local_id))

            threads: List[threading.Thread] = []
            send_results_lock = threading.Lock()
            send_results: List[Tuple[int, bool, str]] = []

            def send_worker(payload: Dict[str, Any], local_id: int):
                nonlocal send_results
                try:
                    # Ensure payload contains system_or_device_id expected by OBR
                    try:
                        sid = self.get_system_id() if callable(self.get_system_id) else None
                    except Exception:
                        sid = None
                    if sid:
                        payload["system_or_device_id"] = sid
                    elif "system_or_device_id" not in payload and "system_id" in payload:
                        payload["system_or_device_id"] = payload.get("system_id")

                    token = self.obtenir_token_auto()
                    if not token:
                        msg = "token introuvable"
                        try:
                            self._update_local_response(local_id, {"error": msg}, obr_status=0)
                        except Exception:
                            pass
                        with send_results_lock:
                            send_results.append((local_id, False, msg))
                        return

                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    url = "https://ebms.obr.gov.bi:9443/ebms_api/AddStockMovementImporters/"

                    attempt = 0
                    max_attempts = 3
                    backoff = 0.6
                    last_resp = None
                    while attempt < max_attempts:
                        attempt += 1
                        try:
                            logger.debug("OBR POST attempt %s for local_id %s to %s payload=%s", attempt, local_id, url, json.dumps(payload, ensure_ascii=False))
                            resp = requests.post(url, json=payload, headers=headers, timeout=30, verify=True)
                            try:
                                jr = resp.json()
                            except Exception:
                                jr = {"raw_text": resp.text}
                            last_resp = {"http_status": resp.status_code, "body": jr}
                            logger.debug("OBR response for local_id %s: %s", local_id, last_resp)
                            if resp.status_code == 200 and isinstance(jr, dict) and (jr.get("success") or jr.get("status") in (1, "1") or jr.get("code") == 0):
                                try:
                                    self._update_local_response(local_id, last_resp, obr_status=1)
                                except Exception:
                                    pass
                                with send_results_lock:
                                    send_results.append((local_id, True, f"HTTP 200 - {jr}"))
                                return
                            if resp.status_code in (400, 403):
                                try:
                                    self._update_local_response(local_id, last_resp, obr_status=2)
                                except Exception:
                                    pass
                                with send_results_lock:
                                    send_results.append((local_id, False, f"HTTP {resp.status_code} - {jr}"))
                                return
                        except Exception as e:
                            last_resp = {"error": str(e)}
                            logger.debug("Exception during OBR POST for local_id %s: %s", local_id, e)
                        time.sleep(backoff)
                        backoff *= 2

                    try:
                        self._update_local_response(local_id, last_resp, obr_status=0)
                    except Exception:
                        pass
                    with send_results_lock:
                        send_results.append((local_id, False, f"exhausted_retries - {last_resp}"))
                except Exception as e:
                    try:
                        self._update_local_response(local_id, {"error": str(e)}, obr_status=0)
                    except Exception:
                        pass
                    with send_results_lock:
                        send_results.append((local_id, False, f"exception: {e}"))

            for payload, local_id in send_tasks:
                t = threading.Thread(target=send_worker, args=(payload, local_id), daemon=True)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            with send_results_lock:
                for local_id, ok, msg in send_results:
                    if ok:
                        obr_success += 1
                    else:
                        obr_fail += 1
                        obr_details.append(f"id local {local_id}: {msg}")

        summary_text = (
            f"Insertions locales: {local_success} réussies, {local_fail} échouées\n"
            f"Envois OBR: {obr_success} réussis, {obr_fail} échoués"
        )

        self.after(0, lambda: self._set_status(f"Terminé: {local_success} locales, {obr_success} OBR réussis; {local_fail} locales échecs, {obr_fail} OBR échecs"))

        def _show_summary():
            details = "\n".join(obr_details[:10])
            if len(obr_details) > 10:
                details += f"\n... ({len(obr_details)-10} autres erreurs)"
            messagebox.showinfo("Résumé", f"{summary_text}\n\nDétails (quelques lignes) :\n{details}")

        self.after(0, _show_summary)

    def _insert_local_and_sync_stock(self, row: Dict[str, Any]) -> Tuple[int, Optional[int]]:
        """
        Version robuste qui :
        - s'assure des colonnes nécessaires,
        - construit l'INSERT dynamiquement en fonction des colonnes réelles,
        - aligne les valeurs correctement,
        - logue SQL et params.
        """
        conn = None
        try:
            conn = self.get_connection()
            ensure_msi_columns(conn)

            cur = conn.cursor()
            now = now_ts()
            source_json = json.dumps(row, ensure_ascii=False)

            # lire colonnes actuelles
            cur.execute("PRAGMA table_info(mouvement_stock_importe)")
            cols_info = cur.fetchall()
            existing_cols = [c[1] for c in cols_info]
            logger.debug("Existing columns for mouvement_stock_importe: %s", existing_cols)

            candidate_map = {
                "contribuable_id": self.contribuable_id,
                "system_id": row.get("system_id") or self.get_system_id(),
                "item_code": row.get("item_code"),
                "item_designation": row.get("item_designation"),
                "item_quantity": float(row.get("item_quantity") or 0),
                "item_measurement_unit": row.get("item_measurement_unit"),
                "item_cost_price": float(row.get("item_cost_price") or 0),
                "item_cost_price_currency": row.get("item_cost_price_currency") or "BIF",
                "item_movement_type": row.get("item_movement_type"),
                "item_movement_invoice_ref": row.get("item_movement_invoice_ref") or "",
                "item_movement_description": row.get("item_movement_description") or "",
                "item_movement_date": row.get("item_movement_date") or now,
                "reference_dmc": row.get("reference_dmc") or "",
                "rubrique_tarifaire": row.get("rubrique_tarifaire") or "",
                "nombre_par_paquet": float(row.get("nombre_par_paquet") or 1),
                "description_paquet": row.get("description_paquet") or "",
                "taux_tva": float(row.get("taux_tva") or 18),
                "item_ct": float(row.get("item_ct") or 0),
                "item_tl": float(row.get("item_tl") or 0),
                "item_tsce_tax": float(row.get("item_tsce_tax") or 0),
                "item_ott_tax": float(row.get("item_ott_tax") or 0),
                "source_json": source_json,
                "created_at": now,
                "obr_status": 0,
                "last_attempt_date": None,
                # payload-named fields often expected by OBR API — kept here so they can be stored
                "system_or_device_id": row.get("system_or_device_id") or self.get_system_id(),
                "item_purchase_or_sale_price": float(row.get("item_purchase_or_sale_price") or row.get("item_sale_price") or row.get("item_cost_price") or 0),
                "item_purchase_or_sale_currency": row.get("item_purchase_or_sale_currency") or row.get("item_cost_price_currency") or "BIF",
            }

            insert_cols = []
            insert_vals = []
            for col in existing_cols:
                if col in candidate_map:
                    insert_cols.append(col)
                    insert_vals.append(candidate_map[col])

            if not insert_cols:
                raise RuntimeError("Aucune colonne valide trouvée pour l'INSERT dans mouvement_stock_importe")

            placeholders = ", ".join(["?"] * len(insert_cols))
            cols_sql = ", ".join(insert_cols)
            sql = f"INSERT INTO mouvement_stock_importe ({cols_sql}) VALUES ({placeholders})"

            logger.debug("Executing dynamic INSERT: %s ; params=%s", sql, tuple(insert_vals))
            cur.execute(sql, tuple(insert_vals))
            local_msi_id = cur.lastrowid
            logger.debug("Inserted mouvement_stock_importe id=%s", local_msi_id)

            # update/create article_stock_local
            sel_sql = "SELECT id, item_quantity, item_cost_price, item_sale_price FROM article_stock_local WHERE item_code = ? LIMIT 1"
            logger.debug("Executing SQL SELECT: %s ; params=(%s,)", sel_sql, row.get("item_code"))
            cur.execute(sel_sql, (row.get("item_code"),))
            found = cur.fetchone()
            qty_change = float(row.get("item_quantity") or 0)
            cost_price = float(row.get("item_cost_price") or 0)
            if found:
                stock_id, old_qty, old_cost, old_sale = found[0], float(found[1] or 0), float(found[2] or 0), float(found[3] or 0)
                new_qty = old_qty + qty_change
                if new_qty > 0 and qty_change > 0:
                    try:
                        new_cost = ((old_qty * old_cost) + (qty_change * cost_price)) / new_qty
                    except Exception:
                        new_cost = cost_price
                else:
                    new_cost = old_cost if old_cost else cost_price
                new_sale = old_sale if old_sale not in (None, 0) else float(row.get("item_sale_price") or new_cost)
                upd_sql = """
                    UPDATE article_stock_local
                    SET item_quantity = ?, item_cost_price = ?, item_sale_price = ?, taux_tva = ?, item_ct = ?, item_tl = ?, item_tsce_tax = ?, item_ott_tax = ?
                    WHERE id = ?
                """
                upd_params = (float(new_qty), float(_quantize(new_cost)), float(_quantize(new_sale)), float(row.get("taux_tva") or 18), float(row.get("item_ct") or 0), float(row.get("item_tl") or 0), float(row.get("item_tsce_tax") or 0), float(row.get("item_ott_tax") or 0), stock_id)
                logger.debug("Executing SQL UPDATE article_stock_local: %s ; params=%s", upd_sql.strip(), upd_params)
                cur.execute(upd_sql, upd_params)
                updated_stock_id = stock_id
            else:
                ins_sql = """
                    INSERT INTO article_stock_local (
                        contribuable_id, item_code, item_designation, item_quantity,
                        item_measurement_unit, item_cost_price, item_cost_price_currency,
                        item_sale_price, pricing_strategy, markup_percent, taux_tva, item_ct, item_tl, item_tsce_tax, item_ott_tax, is_manuel, last_purchase_date, date_enregistrement
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                ins_params = (
                    self.contribuable_id,
                    row.get("item_code"),
                    row.get("item_designation"),
                    float(qty_change),
                    row.get("item_measurement_unit"),
                    float(_quantize(cost_price)),
                    row.get("item_cost_price_currency") or "BIF",
                    float(_quantize(row.get("item_sale_price") or cost_price)),
                    "markup_percent",
                    float(row.get("markup_value") or 25.0),
                    float(row.get("taux_tva") or 18),
                    float(row.get("item_ct") or 0),
                    float(row.get("item_tl") or 0),
                    float(row.get("item_tsce_tax") or 0),
                    float(row.get("item_ott_tax") or 0),
                    0,
                    row.get("item_movement_date"),
                    now_ts()
                )
                logger.debug("Executing SQL INSERT article_stock_local: %s ; params=%s", ins_sql.strip(), ins_params)
                cur.execute(ins_sql, ins_params)
                updated_stock_id = cur.lastrowid
                logger.debug("Inserted article_stock_local id=%s", updated_stock_id)

            conn.commit()
            logger.debug("Committed transaction for local_msi_id=%s", local_msi_id)
            return local_msi_id, updated_stock_id
        except Exception:
            if conn:
                try:
                    conn.rollback()
                    logger.debug("Rolled back transaction due to exception")
                except Exception:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _bg_send_local_row(self, row_payload: Dict[str, Any], local_id: int):
        try:
            # ensure system_or_device_id present in payload
            try:
                sid = self.get_system_id() if callable(self.get_system_id) else None
            except Exception:
                sid = None
            if sid:
                row_payload["system_or_device_id"] = sid
            elif "system_or_device_id" not in row_payload and "system_id" in row_payload:
                row_payload["system_or_device_id"] = row_payload.get("system_id")

            token = self.obtenir_token_auto()
            if not token:
                self._update_local_response(local_id, {"error": "no_token"}, obr_status=0)
                self.after(0, lambda: messagebox.showwarning("OBR", f"Envoi OBR impossible (id local {local_id}) : token introuvable.", parent=self))
                return

            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            url = "https://ebms.obr.gov.bi:9443/ebms_api/AddStockMovementImporters/"

            attempt = 0
            max_attempts = 3
            backoff = 0.6
            last_resp = None
            while attempt < max_attempts:
                attempt += 1
                try:
                    logger.debug("OBR POST attempt %s for local_id %s to %s payload=%s", attempt, local_id, url, json.dumps(row_payload, ensure_ascii=False))
                    resp = requests.post(url, json=row_payload, headers=headers, timeout=30, verify=True)
                    try:
                        jr = resp.json()
                    except Exception:
                        jr = {"raw_text": resp.text}
                    last_resp = {"http_status": resp.status_code, "body": jr}
                    logger.debug("OBR response for local_id %s: %s", local_id, last_resp)
                    if resp.status_code == 200 and isinstance(jr, dict) and (jr.get("success") or jr.get("status") in (1, "1") or jr.get("code") == 0):
                        self._update_local_response(local_id, last_resp, obr_status=1)
                        self.after(0, lambda: messagebox.showinfo("OBR — Succès", f"Envoi OBR réussi (id local {local_id}). Réponse: {jr}", parent=self))
                        return
                    if resp.status_code in (400, 403):
                        self._update_local_response(local_id, last_resp, obr_status=2)
                        self.after(0, lambda: messagebox.showerror("OBR — Erreur", f"Envoi OBR échoué (id local {local_id}). Code {resp.status_code}. Détails: {jr}", parent=self))
                        return
                except Exception as e:
                    last_resp = {"error": str(e)}
                    logger.debug("Exception during _bg_send_local_row for local_id %s: %s", local_id, e)
                time.sleep(backoff)
                backoff *= 2
            self._update_local_response(local_id, last_resp, obr_status=0)
            self.after(0, lambda: messagebox.showwarning("OBR — Échec", f"Échec envoi OBR après {max_attempts} tentatives (id local {local_id}). Dernière erreur: {last_resp}", parent=self))
        except Exception as e:
            try:
                self._update_local_response(local_id, {"error": str(e)}, obr_status=0)
            except Exception:
                pass
            self.after(0, lambda: messagebox.showwarning("OBR", f"Erreur interne lors de l'envoi OBR (id local {local_id}): {e}", parent=self))

    def _update_local_response(self, local_id: int, response_obj: Dict[str, Any], obr_status: Optional[int] = None):
        conn = None
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            now = now_ts()
            if obr_status is None:
                http_status = response_obj.get("http_status") if isinstance(response_obj, dict) else None
                body = response_obj.get("body") if isinstance(response_obj, dict) else None
                if http_status == 200 and isinstance(body, dict) and (body.get("success") or body.get("status") in (1, "1") or body.get("code") == 0):
                    obr_status = 1
                elif http_status in (400, 403):
                    obr_status = 2
                else:
                    obr_status = 0
            sql = """
                UPDATE mouvement_stock_importe
                SET source_json = ?, obr_status = ?, last_attempt_date = ?
                WHERE id = ?
            """
            params = (json.dumps(response_obj, ensure_ascii=False), obr_status, now, local_id)
            logger.debug("Executing SQL UPDATE mouvement_stock_importe: %s ; params=%s", sql.strip(), params)
            cur.execute(sql, params)
            conn.commit()
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

# Optional: simple test runner when file executed directly
def main_test_ui():
    root = tk.Tk()
    root.title("Test ImportStockBatchFrame")
    frame = ImportStockBatchFrame(root, get_connection_fn=get_connection, obtenir_token_fn=obtenir_token_auto, get_system_id_fn=get_system_id, contribuable_id=1)
    frame.pack(fill="both", expand=True)
    root.geometry("1000x700")
    root.mainloop()

if __name__ == "__main__":
    main_test_ui()
