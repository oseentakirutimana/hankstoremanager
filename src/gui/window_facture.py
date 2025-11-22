#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
afficher_formulaire_facture.py
Formulaire de création et d'envoi de facture (sélection d'articles)
Adapté au schéma de la base fournie (create_or_reset_facturation_obr_no_sample_with_flags.py).
"""
from __future__ import annotations
import sqlite3
import logging
import hashlib
import unicodedata
import requests
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import time

from api.obr_client import obtenir_token_auto, get_system_id, checkTIN
from utils.obr_db_helpers import (
    get_client_data, get_contribuable_data,
    get_next_invoice_number, validate_signature_date )

from database.connection import get_connection

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s | %(levelname)s | %(message)s")

# UI constants
CONTENT_BG = "white"
FORM_BG = "#f8f9fa"
LABEL_FG = "#343a40"
ENTRY_BG = "white"
CARD_PAD = 12
TITLE_FONT = ("Segoe UI", 18, "bold")
INPUT_FONT = ("Segoe UI", 11)
DEFAULT_FONT = ("Segoe UI", 10)

BUTTON_SAVE_BG       = "#28a745"
BUTTON_SAVE_ACTIVE   = "#34c759"
BUTTON_ACTION_BG     = "#0d6efd"
BUTTON_ACTION_ACTIVE = "#0b5ed7"
BUTTON_FG            = "white"
BUTTON_WARN_BG       = "#ffc107"
BUTTON_WARN_ACTIVE   = "#e0a800"
BUTTON_WARN_FG       = "black"

style = ttk.Style()
try:
    style.theme_use("default")
    style.configure("Form.TEntry", fieldbackground=ENTRY_BG, background=ENTRY_BG, padding=6, font=INPUT_FONT)
    style.configure("Custom.TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG, padding=6, font=INPUT_FONT)
except Exception:
    pass

def _sha1_hex_normalized(text: str) -> str:
    # OBR expects SHA-256 of the normalized invoice signature (document indicates SHA256)
    try:
        s = unicodedata.normalize("NFKD", (text or "").strip()).encode("utf-8")
        return hashlib.sha256(s).hexdigest()
    except Exception:
        return ""

def _build_obr_invoice_signature(tp: dict, invoice_number: str, sig_dt_ui: str = None):
    now_dt = datetime.now()
    try:
        if sig_dt_ui:
            dt_parsed = datetime.strptime(sig_dt_ui.strip(), "%Y-%m-%d %H:%M:%S")
            sig_date_field = dt_parsed.strftime("%Y-%m-%d %H:%M:%S")
            timestamp_compact = dt_parsed.strftime("%Y%m%d%H%M%S")
        else:
            sig_date_field = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            timestamp_compact = now_dt.strftime("%Y%m%d%H%M%S")
    except Exception:
        sig_date_field = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        timestamp_compact = now_dt.strftime("%Y%m%d%H%M%S")
    nif = (tp.get("tp_TIN") or "").strip() or "UNKNOWN"
    system_id = str(get_system_id() or "").strip() or "UNKNOWN"
    inv_num = (invoice_number or "").strip() or get_next_invoice_number()
    invoice_signature = f"{nif}/{system_id}/{timestamp_compact}/{inv_num}"
    electronic_signature = _sha1_hex_normalized(invoice_signature)
    return invoice_signature, sig_date_field, electronic_signature

def _traiter_reponse_obr_et_declarer_mouvements(resp, conn, cur, facture_id, lignes, inv_num, curr_currency, token):
    import json
    try:
        status_code = getattr(resp, "status_code", None)
        try:
            payload = resp.json() if resp is not None else {}
        except Exception:
            payload = {}
        if status_code == 200 and payload.get("success"):
            data = payload.get("result") or payload or {}
            reg_number = data.get("invoice_registered_number") or ""
            reg_date = data.get("invoice_registered_date") or ""
            electronic_sig = data.get("electronic_signature") or ""
            try:
                cur.execute("UPDATE facture SET facture_statut=? WHERE id=?", ("envoyé", facture_id))
                cur.execute(
                    "INSERT INTO accuse_reception (invoice_registered_number,invoice_registered_date,electronic_signature,facture_id) VALUES (?,?,?,?)",
                    (reg_number or "", reg_date or "", electronic_sig or "", facture_id)
                )
                conn.commit()
            except Exception:
                try: conn.rollback()
                except Exception: pass
                logger.exception("Erreur MAJ local après envoi OBR")

            # try to resolve contribuable_id from facture if available
            contribuable_id = None
            try:
                cur.execute("SELECT contribuable_id FROM facture WHERE id=? LIMIT 1", (facture_id,))
                r = cur.fetchone()
                if r:
                    contribuable_id = r["contribuable_id"] if hasattr(r, "keys") else r[0]
            except Exception:
                logger.exception("Impossible de récupérer contribuable_id pour mouvement_stock")

            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            mouvements_local = []
            for l in lignes:
                item_code = l.get("item_code","")
                mo_payload = {
                    "system_or_device_id": get_system_id(),
                    "item_code": item_code,
                    "item_designation": l.get("item_designation",""),
                    "item_quantity": str(l.get("item_quantity", 0)),
                    "item_measurement_unit": l.get("item_measurement_unit",""),
                    "item_cost_price": str(l.get("item_cost_price", 0)),
                    "item_purchase_or_sale_price": str(l.get("item_sale_price", l.get("item_price", 0))),
                    "item_purchase_or_sale_currency": curr_currency,
                    "item_movement_type": "SV",
                    "item_movement_invoice_ref": inv_num,
                    "item_movement_description": "Vente",
                    "item_movement_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                status = 0
                attempt = 0
                max_attempts = 3
                backoff_seconds = 0.5
                short_msg = ""
                mv_json = {}
                while attempt < max_attempts:
                    attempt += 1
                    try:
                        resp_mv = requests.post(
                            "https://ebms.obr.gov.bi:9443/ebms_api/AddStockMovement/",
                            json=mo_payload,
                            headers=headers,
                            timeout=30
                        )
                        try:
                            mv_json = resp_mv.json()
                        except Exception:
                            mv_json = {}
                        if resp_mv.status_code == 200 and mv_json.get("success"):
                            status = 1
                            short_msg = mv_json.get("msg") or mv_json.get("message") or ""
                            logger.debug("AddStockMovement OK for %s qty=%s", item_code, mo_payload["item_quantity"])
                            break
                        if resp_mv.status_code == 400:
                            status = 0
                            short_msg = mv_json.get("msg") or mv_json.get("message") or getattr(resp_mv, "text", "")
                            logger.warning("AddStockMovement 400 pour %s: %s -- payload: %s", item_code, short_msg, mo_payload)
                            break
                        short_msg = mv_json.get("msg") or mv_json.get("message") or getattr(resp_mv, "text", "")
                        logger.warning("AddStockMovement tentative %d échouée (HTTP %s) pour %s: %s", attempt, resp_mv.status_code, item_code, short_msg)
                    except Exception as ex:
                        short_msg = f"Erreur réseau: {ex}"
                        logger.exception("Erreur réseau lors AddStockMovement tentative %d pour %s", attempt, item_code)
                    if attempt < max_attempts:
                        try:
                            time.sleep(backoff_seconds)
                            backoff_seconds *= 2
                        except Exception:
                            pass

                payload_for_local = {
                    "request": mo_payload,
                    "response_summary": {
                        "http_status": getattr(resp_mv, "status_code", None) if 'resp_mv' in locals() else None,
                        "obr_success": bool(mv_json.get("success")) if isinstance(mv_json, dict) else False,
                        "message": mv_json.get("msg") or mv_json.get("message") or short_msg
                    }
                }

                stock_id = None
                try:
                    c2 = conn.cursor()
                    c2.execute("SELECT id FROM article_stock_local WHERE item_code=? LIMIT 1", (item_code,))
                    rr = c2.fetchone()
                    if rr:
                        stock_id = rr["id"] if hasattr(rr, "keys") else rr[0]
                    try:
                        c2.close()
                    except Exception:
                        pass
                except Exception:
                    logger.exception("Erreur recherche article_stock_local pour insertion mouvement")

                try:
                    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    cur.execute("""INSERT INTO mouvement_stock (
                        contribuable_id, system_or_device_id, item_code, item_designation, item_quantity,
                        item_measurement_unit, item_purchase_or_sale_price, item_purchase_or_sale_currency,
                        item_movement_type, item_movement_date, item_movement_invoice_ref, item_movement_description,
                        article_stock_id, obr_status, source_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    contribuable_id,
                                    get_system_id(),
                                    item_code,
                                    mo_payload.get("item_designation",""),
                                    float(l.get("item_quantity", 0) or 0),
                                    mo_payload.get("item_measurement_unit",""),
                                    float(mo_payload.get("item_purchase_or_sale_price", 0)) if mo_payload.get("item_purchase_or_sale_price") not in (None, "") else 0.0,
                                    mo_payload.get("item_purchase_or_sale_currency", curr_currency),
                                    mo_payload.get("item_movement_type", "SV"),
                                    mo_payload.get("item_movement_date"),
                                    mo_payload.get("item_movement_invoice_ref"),
                                    mo_payload.get("item_movement_description"),
                                    stock_id,
                                    int(status),
                                    json.dumps(payload_for_local, ensure_ascii=False),
                                    date_now
                                ))
                    mouvement_id = cur.lastrowid
                    conn.commit()
                    mouvements_local.append((mouvement_id, payload_for_local))
                except Exception as sql_ex:
                    try: conn.rollback()
                    except Exception: pass
                    logger.exception("Erreur insertion mouvement local invoice_ref=%s: %s", inv_num, sql_ex)

            return True, "Facture envoyée et mouvements traités séquentiellement"
        else:
            if status_code == 400:
                msg = payload.get("msg") or "Veuillez fournir tous les champs obligatoires"
            elif status_code == 403:
                msg = payload.get("msg") or "Accès refusé ou paramètre manquant"
            else:
                msg = payload.get("msg") if isinstance(payload, dict) else str(payload or getattr(resp,"text",f"HTTP {status_code}"))
            logger.error("OBR addInvoice erreur [%s]: %s", status_code, msg)
            return False, msg
    except Exception:
        logger.exception("Erreur traitement réponse OBR")
        return False, "Impossible d'analyser la réponse OBR"

def afficher_formulaire_facture(parent):

    for w in parent.winfo_children(): w.destroy()
    try: parent.configure(bg=CONTENT_BG)
    except Exception: pass

    wrapper = tk.Frame(parent, bg=CONTENT_BG, padx=CARD_PAD, pady=CARD_PAD)
    wrapper.pack(fill="both", expand=True)
    tk.Label(wrapper, text="📄 Nouvelle facture", font=TITLE_FONT, bg=CONTENT_BG, fg=LABEL_FG).pack(anchor="w", pady=(0,12))

    # contrib keys and tp_vars
    tp_keys = [
        "tp_type","tp_name","tp_TIN","tp_trade_number","tp_postal_number","tp_phone_number",
        "tp_address_province","tp_address_commune","tp_address_quartier","tp_address_avenue",
        "tp_address_rue","tp_address_number","tp_fiscal_center","tp_legal_form","tp_activity_sector",
        "vat_taxpayer","ct_taxpayer","tl_taxpayer"
    ]
    tp_vars = {k: tk.StringVar(value="") for k in tp_keys}
    first_tp_id = None
    try:
        conn_tmp = get_connection(); cur_tmp = conn_tmp.cursor()
        cur_tmp.execute("SELECT id FROM contribuable ORDER BY tp_name LIMIT 1")
        row = cur_tmp.fetchone()
        if row:
            first_tp_id = row["id"] if hasattr(row, "keys") else row[0]
        conn_tmp.close()
    except Exception:
        logger.exception("Erreur récupération contribuable")
    if first_tp_id:
        try:
            data = get_contribuable_data(first_tp_id) or {}
            for k, v in data.items():
                if k in tp_vars and v is not None: tp_vars[k].set(v)
        except Exception:
            logger.exception("Erreur remplir contribuable")

    # Client UI
    client_frame = tk.LabelFrame(wrapper, text="Détails Client", bg=FORM_BG, fg=LABEL_FG, bd=1, relief="solid")
    client_frame.pack(fill="x", pady=(0,12)); client_frame.configure(padx=10, pady=10)
    for col in range(4): client_frame.grid_columnconfigure(col, weight=1, uniform="c")

    client_champs = [
        ("Nom", "customer_name"), ("Adresse", "customer_address"), ("Téléphone", "customer_phone_number"),
        ("N° postal", "customer_postal_number"), ("Email", "customer_email"), ("Secteur", "customer_sector"),
    ]
    cl_vars = {}
    for i, (label_text, key) in enumerate(client_champs):
        row = i // 2; col = (i % 2) * 2
        tk.Label(client_frame, text=label_text + " :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=row, column=col, sticky="w", padx=6, pady=6)
        var = tk.StringVar()
        ent = ttk.Entry(client_frame, textvariable=var, width=48, font=DEFAULT_FONT, style="Form.TEntry")
        ent.grid(row=row, column=col+1, sticky="ew", padx=6, pady=6)
        cl_vars[key] = var

    try:
        tp_name_val = tp_vars.get("tp_name").get() if tp_vars.get("tp_name") else ""
        if tp_name_val and cl_vars.get("customer_name"):
            cl_vars["customer_name"].set(f"client, {tp_name_val}")
    except Exception:
        pass

    # NIF verify area
    nif_frame = tk.Frame(client_frame, bg=FORM_BG)
    nif_frame.grid(row=4, column=0, columnspan=4, sticky="ew", padx=6, pady=(8,0))
    nif_frame.grid_columnconfigure(0, weight=0); nif_frame.grid_columnconfigure(1, weight=1); nif_frame.grid_columnconfigure(2, weight=0)
    tk.Label(nif_frame, text="NIF :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=0, column=0, sticky="w", padx=6)
    nif_var = tk.StringVar()
    nif_entry = ttk.Entry(nif_frame, textvariable=nif_var, width=36, font=DEFAULT_FONT, style="Form.TEntry")
    nif_entry.grid(row=0, column=1, sticky="w", padx=6)
    btn_check_nif = tk.Button(nif_frame, text="Vérifier NIF", bg=BUTTON_ACTION_BG, activebackground=BUTTON_ACTION_ACTIVE, fg=BUTTON_FG, relief="raised", bd=1, padx=8, pady=4)
    btn_check_nif.grid(row=0, column=2, sticky="w", padx=6)
    # Assujetti TVA is checked by default
    vat_var = tk.BooleanVar(value=True)
    chk_vat = ttk.Checkbutton(nif_frame, text="Assujetti TVA", variable=vat_var)
    chk_vat.grid(row=0, column=3, sticky="w", padx=(12,6))

    def _on_check_nif():
        try:
            val = nif_var.get().strip()
            if val == "":
                messagebox.showwarning("NIF", "Saisissez un NIF"); return
            if not val.isdigit():
                messagebox.showerror("NIF invalide", "Le NIF doit être numérique"); return
            try:
                local = get_client_data(tin=val)
            except Exception:
                local = None
            if local:
                if cl_vars.get("customer_name"): cl_vars["customer_name"].set(local.get("customer_name",""))
                if cl_vars.get("customer_address"): cl_vars["customer_address"].set(local.get("customer_address",""))
                if cl_vars.get("customer_phone_number"): cl_vars["customer_phone_number"].set(local.get("customer_phone_number",""))
                if cl_vars.get("customer_postal_number"): cl_vars["customer_postal_number"].set(local.get("customer_postal_number",""))
                if cl_vars.get("customer_email"): cl_vars["customer_email"].set(local.get("customer_email",""))
                if cl_vars.get("customer_sector"): cl_vars["customer_sector"].set(local.get("customer_sector",""))
                vat_var.set(local.get("vat_customer_payer",0) == 1)
                messagebox.showinfo("NIF", "Client local trouvé"); return
            try:
                res = checkTIN(val)
            except Exception:
                messagebox.showerror("Erreur", "Impossible d'interroger l'API TIN"); return
            if not res.get("valid"):
                messagebox.showinfo("Vérification TIN", res.get("message","Invalide")); return
            tp = res.get("data", {})
            if cl_vars.get("customer_name"): cl_vars["customer_name"].set(tp.get("tp_name") or tp.get("name",""))
            addr_parts = [tp.get(k,"") for k in ("tp_address_province","tp_address_commune","tp_address_quartier","tp_address_avenue","tp_address_rue")]
            if cl_vars.get("customer_address"): cl_vars["customer_address"].set(" ".join([p for p in addr_parts if p]) or tp.get("tp_address",""))
            if cl_vars.get("customer_phone_number"): cl_vars["customer_phone_number"].set(tp.get("tp_phone_number","") or tp.get("phone",""))
            if cl_vars.get("customer_sector"): cl_vars["customer_sector"].set(tp.get("tp_activity_sector",""))
            vat_var.set(str(tp.get("vat_taxpayer","")) in ("1","True","true"))
            messagebox.showinfo("Vérification TIN", res.get("message","Client OBR trouvé"))
        except Exception:
            logger.exception("Erreur _on_check_nif")

    btn_check_nif.config(command=_on_check_nif)

    # Meta facture
    meta = tk.LabelFrame(wrapper, text="Détails de la facture", bg=FORM_BG, fg=LABEL_FG, bd=1, relief="solid")
    meta.pack(fill="x", pady=(0,12)); meta.configure(padx=10, pady=10)
    for col in range(4): meta.grid_columnconfigure(col, weight=1)

    num_var = tk.StringVar(value=get_next_invoice_number())
    date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    type_var_invoice = tk.StringVar(value="FN - Facture normale")
    curr_var = tk.StringVar(value="BIF")
    pay_var = tk.StringVar(value="1")
    invoice_signature_var = tk.StringVar(value="")
    invoice_signature_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ident_var = tk.StringVar()

    tk.Label(meta, text="Numéro :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=0, column=0, sticky="w", padx=6, pady=6)
    ent_num = ttk.Entry(meta, textvariable=num_var, style="Form.TEntry", width=28, state="readonly")
    ent_num.grid(row=1, column=0, padx=6, pady=4)
    tk.Label(meta, text="Date :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=0, column=1, sticky="w", padx=6, pady=6)
    ent_date = ttk.Entry(meta, textvariable=date_var, style="Form.TEntry", width=28, state="readonly")
    ent_date.grid(row=1, column=1, padx=6, pady=4)
    tk.Label(meta, text="Type :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=0, column=2, sticky="w", padx=6, pady=6)
    ttk.Combobox(meta, textvariable=type_var_invoice, style="Custom.TCombobox", width=26, values=["FN - Facture normale","FA - Avoir","RC - RC","RHF - Recu hors taxe"]).grid(row=1, column=2, padx=6, pady=4)
    tk.Label(meta, text="Devise :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=0, column=3, sticky="w", padx=6, pady=6)
    ttk.Combobox(meta, textvariable=curr_var, style="Custom.TCombobox", width=12, values=["BIF","USD","EUR"]).grid(row=1, column=3, padx=6, pady=4)

    tk.Label(meta, text="Signature (automatique) :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=2, column=0, sticky="w", padx=6, pady=(12,4))
    sig_entry = ttk.Entry(meta, textvariable=invoice_signature_var, style="Form.TEntry", width=40, state="readonly")
    sig_entry.grid(row=3, column=0, padx=6, pady=4)
    tk.Label(meta, text="Date signature :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=2, column=1, sticky="w", padx=6, pady=(12,4))
    sig_date_entry = ttk.Entry(meta, textvariable=invoice_signature_date_var, style="Form.TEntry", width=24, state="readonly")
    sig_date_entry.grid(row=3, column=1, padx=6, pady=4)
    tk.Label(meta, text="Identifiant :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=2, column=2, sticky="w", padx=6, pady=(12,4))
    ident_entry = ttk.Entry(meta, textvariable=ident_var, style="Form.TEntry", width=40, state="readonly")
    ident_entry.grid(row=3, column=2, columnspan=2, padx=6, pady=4)

    def maj_ident(*_):
        nonlocal first_tp_id
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            if first_tp_id:
                tp = get_contribuable_data(first_tp_id) or {}
                tin = tp.get("tp_TIN", "UNKNOWN")
                ident_var.set(f"{tin}/{get_system_id()}/{ts}/{num_var.get()}")
            else:
                ident_var.set(f"UNKNOWN/{get_system_id()}/{ts}/{num_var.get()}")
        except Exception:
            logger.exception("Erreur maj_ident")
    try:
        num_var.trace_add("write", maj_ident)
    except Exception:
        try: num_var.trace("w", maj_ident)
        except Exception: logger.exception("Impossible d'attacher trace à num_var")
    maj_ident()

    sig_text_init, sig_date_init, elec_init = _build_obr_invoice_signature({k: "" for k in tp_keys}, num_var.get().strip() or get_next_invoice_number(), invoice_signature_date_var.get().strip())
    invoice_signature_var.set(sig_text_init)
    invoice_signature_date_var.set(sig_date_init)

    def _refresh_signature(*_):
        try:
            tp_curr = {k: tp_vars[k].get() for k in tp_keys}
            inv = num_var.get().strip() or get_next_invoice_number()
            sig_text, sig_date, elec = _build_obr_invoice_signature(tp_curr, inv, invoice_signature_date_var.get().strip())
            invoice_signature_var.set(sig_text)
            invoice_signature_date_var.set(sig_date)
            return sig_text, sig_date, elec
        except Exception:
            logger.exception("Erreur normalisation signature")
            return invoice_signature_var.get().strip(), invoice_signature_date_var.get().strip(), ""

    try:
        invoice_signature_date_var.trace_add("write", lambda *a: _refresh_signature())
    except Exception:
        try: invoice_signature_date_var.trace("w", lambda *a: _refresh_signature())
        except Exception: pass

    # ---------- Articles area ----------
    art_frame = tk.LabelFrame(wrapper, text="Articles (sélectionner)", bg=FORM_BG, fg=LABEL_FG, bd=1, relief="solid")
    art_frame.pack(fill="both", expand=True, pady=(0,12)); art_frame.configure(padx=6, pady=6)

    search_var = tk.StringVar()
    tk.Label(art_frame, text="Recherche :", bg=FORM_BG, fg=LABEL_FG, font=DEFAULT_FONT).grid(row=0, column=0, sticky="w", padx=(6,4), pady=(0,6))
    search_entry = ttk.Entry(art_frame, textvariable=search_var, style="Form.TEntry", width=28)
    search_entry.grid(row=0, column=1, sticky="w", padx=(0,6), pady=(0,6))
    art_frame.grid_columnconfigure(0, weight=0); art_frame.grid_columnconfigure(1, weight=1)

    art_canvas = tk.Canvas(art_frame, bg=FORM_BG, highlightthickness=0, height=320)
    vscroll = ttk.Scrollbar(art_frame, orient="vertical", command=art_canvas.yview)
    art_canvas.configure(yscrollcommand=vscroll.set)
    art_canvas.grid(row=1, column=0, columnspan=2, sticky="nsew")
    vscroll.grid(row=1, column=2, sticky="ns", padx=(4,6))
    art_frame.grid_rowconfigure(1, weight=1)

    art_inner = tk.Frame(art_canvas, bg=FORM_BG)
    art_window = art_canvas.create_window((0,0), window=art_inner, anchor="nw")
    def _on_art_config(e):
        try:
            art_canvas.configure(scrollregion=art_canvas.bbox("all"))
            if art_window is not None:
                art_canvas.itemconfig(art_window, width=e.width)
        except Exception:
            pass
    art_inner.bind("<Configure>", _on_art_config)

    hdr_font = ("Segoe UI", 10, "bold")
    # added Taux TVA column after PU (vente)
    cols = [("Sel",4), ("Code article",8), ("Désignation",8), ("Stock",7), ("PU (vente)",12), ("Taux TVA",8), ("Qté",4), ("Total TTC",10)]
    for col, (txt, w) in enumerate(cols):
        tk.Label(art_inner, text=txt, font=hdr_font, bg=FORM_BG, fg=LABEL_FG, width=w, anchor="w" if col==2 else "center").grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

    rows = []

    def _load_raw_articles():
        try:
            conn = get_connection()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    id,
                    item_code,
                    item_designation,
                    item_quantity,
                    item_sale_price,
                    item_ott_tax,
                    item_tsce_tax,
                    item_tl,
                    item_ct,
                    item_measurement_unit,
                    item_cost_price,
                    taux_tva
                FROM article_stock_local
                WHERE is_manuel = 0
                ORDER BY item_designation
            """)
            rows_db = cur.fetchall()
            conn.close()

            enriched = []
            for r in rows_db:
                item_code = (r["item_code"] or "").strip() if "item_code" in r.keys() else ""
                designation = (r["item_designation"] or "").strip() if "item_designation" in r.keys() else ""
                try:
                    qty = float(r["item_quantity"]) if "item_quantity" in r.keys() and r["item_quantity"] is not None else 0.0
                except Exception:
                    qty = 0.0
                try:
                    sale_price = float(r["item_sale_price"]) if "item_sale_price" in r.keys() and r["item_sale_price"] is not None else 0.0
                except Exception:
                    sale_price = 0.0
                try:
                    ott = float(r["item_ott_tax"]) if "item_ott_tax" in r.keys() and r["item_ott_tax"] is not None else 0.0
                except Exception:
                    ott = 0.0
                try:
                    tsce = float(r["item_tsce_tax"]) if "item_tsce_tax" in r.keys() and r["item_tsce_tax"] is not None else 0.0
                except Exception:
                    tsce = 0.0
                try:
                    tl = float(r["item_tl"]) if "item_tl" in r.keys() and r["item_tl"] is not None else 0.0
                except Exception:
                    tl = 0.0
                try:
                    ct = float(r["item_ct"]) if "item_ct" in r.keys() and r["item_ct"] is not None else 0.0
                except Exception:
                    ct = 0.0
                unit = (r["item_measurement_unit"] or "").strip() if "item_measurement_unit" in r.keys() else ""
                try:
                    cost = float(r["item_cost_price"]) if "item_cost_price" in r.keys() and r["item_cost_price"] is not None else sale_price
                except Exception:
                    cost = sale_price

                # Determine taux default: preserve DB value if present, otherwise use VAT checkbox default
                try:
                    raw_taux = float(r["taux_tva"]) if "taux_tva" in r.keys() and r["taux_tva"] is not None else None
                except Exception:
                    raw_taux = None
                try:
                    default_taux = 18.0 if vat_var.get() else 0.0
                except Exception:
                    default_taux = 0.0
                taux = raw_taux if (raw_taux is not None) else default_taux

                enriched.append({
                    "id": r["id"] if "id" in r.keys() else None,
                    "item_code": item_code,
                    "item_designation": designation,
                    "item_quantity": qty,
                    "item_sale_price": sale_price,
                    "item_ott_tax": ott,
                    "item_tsce_tax": tsce,
                    "item_tl": tl,
                    "item_ct": ct,
                    "item_measurement_unit": unit,
                    "item_cost_price": cost,
                    "taux_tva": taux
                })
            return enriched
        except Exception:
            logger.exception("Erreur chargement articles")
            return []

    raw_articles = _load_raw_articles()

    def _build_rows(filtered):
        for child in list(art_inner.winfo_children()):
            try:
                r = int(child.grid_info().get("row", -1))
            except Exception:
                r = -1
            if r > 0:
                child.destroy()
        rows.clear()
        for idx, art in enumerate(filtered, start=1):
            code = str(art.get("item_code","") or "")
            desig = str(art.get("item_designation","") or "")
            unit = str(art.get("item_measurement_unit","") or "")
            stock = art.get("item_quantity", 0) or 0
            pu = art.get("item_sale_price", 0) or 0.0
            taux_tva = art.get("taux_tva", 0) or 0.0
            ott = art.get("item_ott_tax", 0) or 0.0
            tsce = art.get("item_tsce_tax", 0) or 0.0
            tl_val = art.get("item_tl", 0) or 0.0
            ct_val = art.get("item_ct", 0) or 0.0
            cost = art.get("item_cost_price", 0.0) or 0.0

            sel = tk.BooleanVar(value=False)
            qty_var = tk.StringVar(value="0")
            pu_var = tk.StringVar(value=f"{float(pu):.2f}" if pu is not None else "0.00")
            widgets = {"chk": None, "ent_pu": None, "ent_qty": None, "lbl_total": None, "ent_tva": None}
            chk = tk.Checkbutton(art_inner, variable=sel, bg=FORM_BG, selectcolor=FORM_BG)
            chk.grid(row=idx, column=0, padx=6, pady=4, sticky="w"); widgets['chk'] = chk

            tk.Label(art_inner, text=code, bg=FORM_BG, fg=LABEL_FG, width=10, anchor="w").grid(row=idx, column=1, padx=4, pady=4, sticky="w")
            display_desig = f"{desig} ({unit})" if unit else desig
            tk.Label(art_inner, text=display_desig, bg=FORM_BG, fg=LABEL_FG, width=28, anchor="w").grid(row=idx, column=2, padx=4, pady=4, sticky="w")
            tk.Label(art_inner, text=f"{float(stock):.2f}" if isinstance(stock, (int, float)) else str(stock), bg=FORM_BG, fg=LABEL_FG, width=10, anchor="center").grid(row=idx, column=3, padx=4, pady=4)

            # PU rendered readonly so user cannot edit; code may call pu_var.set(...) to update display
            ent_pu = ttk.Entry(art_inner, textvariable=pu_var, style="Form.TEntry", width=12, state="readonly")
            ent_pu.grid(row=idx, column=4, padx=4, pady=4)
            widgets['ent_pu'] = ent_pu

            # insert editable Taux TVA column (user can change per-line)
            # Use DB taux if present; otherwise use vat_var state: 18.00 when assujetti checked, else 0.00
            try:
                initial_tva_default = float(taux_tva) if taux_tva is not None else (18.0 if vat_var.get() else 0.0)
            except Exception:
                initial_tva_default = 18.0 if vat_var.get() else 0.0
            tva_var = tk.StringVar(value=f"{float(initial_tva_default):.2f}")
            ent_tva = ttk.Entry(art_inner, textvariable=tva_var, style="Form.TEntry", width=8)
            ent_tva.grid(row=idx, column=5, padx=4, pady=4)
            widgets['ent_tva'] = ent_tva

            # quantity column shifted to col 6
            ent_qty = ttk.Entry(art_inner, textvariable=qty_var, style="Form.TEntry", width=8)
            ent_qty.grid(row=idx, column=6, padx=4, pady=4)
            widgets['ent_qty'] = ent_qty

            # total column shifted to col 7
            lbl_total = tk.Label(art_inner, text="0.00", bg=FORM_BG, fg=LABEL_FG, width=10, anchor="e")
            lbl_total.grid(row=idx, column=7, padx=4, pady=4)
            widgets['lbl_total'] = lbl_total

            fd = {
                "taux_tva": taux_tva, "ott_tax": ott, "tsce_tax": tsce,
                "tl": tl_val, "ct": ct_val, "item_measurement_unit": unit, "item_cost_price": cost
            }
            # expose per-line editable TVA variable so maj_ligne/_collect_lignes can read the current value
            try:
                fd['tva_var'] = tva_var
            except Exception:
                fd['tva_var'] = None

            stock_val = stock
            rows.append((sel, qty_var, pu_var, code, desig, unit, stock_val, fd, widgets))

            def _mk_cb(*a):
                try: maj_ligne()
                except Exception: logger.exception("Erreur maj_ligne dans trace")
            try:
                qty_var.trace_add("write", _mk_cb); pu_var.trace_add("write", _mk_cb); tva_var.trace_add("write", _mk_cb)
            except Exception:
                try: qty_var.trace("w", _mk_cb); pu_var.trace("w", _mk_cb); tva_var.trace("w", _mk_cb)
                except Exception: pass

    _build_rows(raw_articles)

    def _apply_search(*_):
        q = search_var.get().strip().lower()
        if not q:
            filtered = raw_articles
        else:
            filtered = []
            for a in raw_articles:
                s = a.get("item_designation","")
                code = a.get("item_code","")
                if q in str(s).lower() or q in str(code).lower():
                    filtered.append(a)
        _build_rows(filtered)
        try: maj_ligne()
        except Exception: pass

    try:
        search_var.trace_add("write", _apply_search)
    except Exception:
        try: search_var.trace("w", _apply_search)
        except Exception: pass

    total_var = tk.StringVar(value="0.00")
    def maj_ligne(*_):
        total_fact = 0.0
        for sel, qty_var, pu_var, code, des, unit, stock_val, fd, widgets in rows:
            try:
                q = float(qty_var.get().strip()) if qty_var.get().strip() else 0.0
            except Exception:
                q = 0.0
            try:
                puv = float(pu_var.get().strip()) if pu_var.get().strip() else 0.0
            except Exception:
                puv = 0.0
            try:
                stock_num = float(stock_val) if (isinstance(stock_val, (int, float)) or (isinstance(stock_val, str) and stock_val.replace('.','',1).isdigit())) else 0.0
            except Exception:
                stock_num = 0.0
            if hasattr(sel, "get") and sel.get():
                if q > stock_num:
                    try:
                        messagebox.showwarning("Qty > stock", f"Quantité demandée ({q}) > stock ({stock_num}) pour '{des}'.")
                    except Exception: pass
                    try:
                        qty_var.set(str(int(stock_num) if float(stock_num).is_integer() else stock_num))
                    except Exception:
                        qty_var.set(str(stock_num))
                    q = stock_num
                # HT line = unit_price * qty + unit fixed taxes (ott/tsce/ct)
                ht = puv * q
                ht += (fd.get('ott_tax', 0) or 0) + (fd.get('tsce_tax', 0) or 0) + (fd.get('ct', 0) or 0)
                # read dynamic taux from tva_var if present
                try:
                    if fd.get('tva_var') is not None:
                        taux = float(fd['tva_var'].get().strip()) if fd['tva_var'].get().strip() else 0.0
                    else:
                        taux = float(fd.get('taux_tva', 0) or 0)
                except Exception:
                    taux = float(fd.get('taux_tva', 0) or 0)
                tva = ht * (taux / 100.0)
                wv = ht + tva
                wvt = wv + (fd.get('tl', 0) or 0)
                fd['quantity'] = q; fd['unit_price'] = puv; fd['price_nvat'] = round(ht, 2); fd['vat_amount'] = round(tva, 2); fd['price_wvat'] = round(wv, 2); fd['total'] = round(wvt, 2)
                try: widgets['lbl_total'].config(text=f"{wvt:.2f}")
                except Exception: pass
                total_fact += wvt
            else:
                try: widgets['lbl_total'].config(text="0.00")
                except Exception: pass
        try: total_var.set(f"{total_fact:.2f}")
        except Exception: pass

    maj_ligne()

    # When vat_var toggles, update per-line tva entries default (user can still change)
    def _on_vat_toggle(*_):
        try:
            new_def = "18.00" if vat_var.get() else "0.00"
            for sel, qty_var, pu_var, code, des, unit, stock_val, fd, widgets in rows:
                try:
                    tvv = fd.get('tva_var')
                    if tvv is not None:
                        # set default for all lines; user may override
                        tvv.set(new_def)
                except Exception:
                    pass
            maj_ligne()
        except Exception:
            logger.exception("Erreur lors du changement de vat_var")
    try:
        vat_var.trace_add("write", _on_vat_toggle)
    except Exception:
        try: vat_var.trace("w", _on_vat_toggle)
        except Exception: pass

    # totals UI
    total_frame = tk.Frame(wrapper, bg=FORM_BG); total_frame.pack(fill="x", pady=(0,12)); total_frame.configure(padx=10, pady=6)
    tk.Label(total_frame, text="Total facture TTC :", font=("Segoe UI", 12, "bold"), bg=FORM_BG, fg=LABEL_FG).pack(side="left")
    tk.Label(total_frame, textvariable=total_var, font=("Segoe UI", 12, "bold"), bg=FORM_BG, fg=LABEL_FG).pack(side="left", padx=(8,0))

    # buttons
    btn_frame = tk.Frame(wrapper, bg=FORM_BG); btn_frame.pack(fill="x", pady=(8,12))

    btn_save = tk.Button(btn_frame, text="Sauvegarder & Envoyer", bg=BUTTON_SAVE_BG, activebackground=BUTTON_SAVE_ACTIVE, fg=BUTTON_FG, relief="raised", bd=1, padx=10, pady=6)
    btn_save.pack(side="left", padx=6)

    btn_refresh = tk.Button(btn_frame, text="Rafraichir", bg=BUTTON_ACTION_BG, activebackground=BUTTON_ACTION_ACTIVE, fg=BUTTON_FG, relief="raised", bd=1, padx=10, pady=6)
    btn_refresh.pack(side="left", padx=6)

    btn_cancel = tk.Button(btn_frame, text="Annuler", bg=BUTTON_WARN_BG, activebackground=BUTTON_WARN_ACTIVE, fg=BUTTON_WARN_FG, relief="raised", bd=1, padx=10, pady=6)
    btn_cancel.pack(side="right", padx=6)

    def _collect_lignes():
        collected = []
        for sel, qty_var, pu_var, code, des, unit, stock_val, fd, widgets in rows:
            if hasattr(sel, "get") and sel.get():
                try: q = float(qty_var.get().strip()) if qty_var.get().strip() else 0.0
                except Exception: q = 0.0
                try: puv = float(pu_var.get().strip()) if pu_var.get().strip() else 0.0
                except Exception: puv = 0.0
                # read dynamic taux
                try:
                    if fd.get('tva_var') is not None:
                        taux = float(fd['tva_var'].get().strip()) if fd['tva_var'].get().strip() else 0.0
                    else:
                        taux = float(fd.get('taux_tva', 0) or 0)
                except Exception:
                    taux = float(fd.get('taux_tva', 0) or 0)
                if q <= 0:
                    try: messagebox.showwarning("Quantité", f"Quantité invalide pour '{des}'")
                    except Exception: pass
                    return None
                # Calculate derived fields
                unit_price_nvat = round(puv * q, 2)
                extra_fixed = (fd.get('ott_tax', 0) or 0) + (fd.get('tsce_tax', 0) or 0) + (fd.get('ct', 0) or 0)
                ht_with_extras = unit_price_nvat + extra_fixed
                vat_amount = round(ht_with_extras * (taux / 100.0), 2)
                unit_price_wvat = round(ht_with_extras + vat_amount, 2)
                line_total_amount = round(unit_price_wvat + (fd.get('tl', 0) or 0), 2)
                item = {
                    "item_code": code or "",
                    "item_designation": des or "",
                    "item_quantity": q,
                    "item_price": round(puv, 2),
                    "item_sale_price": round(puv, 2),
                    "vat": vat_amount,
                    "item_price_nvat": unit_price_nvat,
                    "vat_amount": vat_amount,
                    "item_price_wvat": unit_price_wvat,
                    "item_total_amount": line_total_amount,
                    "item_measurement_unit": fd.get("item_measurement_unit","") if fd else unit,
                    "item_cost_price": fd.get("item_cost_price", 0.0),
                    # include explicit tax rate so backend can use it if expected
                    "tax_rate": round(taux, 2)
                }
                collected.append(item)
        return collected

    def refresh_all_forms():
        nonlocal raw_articles, first_tp_id
        try:
            raw_articles = _load_raw_articles()
            _apply_search()
            try:
                c = get_connection(); cur = c.cursor()
                cur.execute("SELECT id FROM contribuable ORDER BY tp_name LIMIT 1")
                r = cur.fetchone(); c.close()
                first_tp_id = r["id"] if r and hasattr(r,"keys") else (r[0] if r else None)
            except Exception:
                logger.exception("Erreur refresh contribuable")
            if first_tp_id:
                try:
                    data = get_contribuable_data(first_tp_id) or {}
                    for k, v in data.items():
                        if k in tp_vars and v is not None: tp_vars[k].set(v)
                except Exception:
                    logger.exception("Erreur remplir contribuable (refresh)")
            maj_ident()
            _refresh_signature()
            maj_ligne()
        except Exception:
            logger.exception("Erreur refresh_all_forms")

    def _save_and_send():
        nonlocal first_tp_id
        tp = {k: tp_vars[k].get() for k in tp_keys}
        inv_num = num_var.get().strip() or get_next_invoice_number()
        sig_text, sig_date_field, electronic_sig = _build_obr_invoice_signature(tp, inv_num, invoice_signature_date_var.get().strip())
        invoice_signature_var.set(sig_text); invoice_signature_date_var.set(sig_date_field)
        if not sig_text:
            messagebox.showerror("Signature requise", "La signature structurée est requise."); return
        if not validate_signature_date(sig_date_field):
            messagebox.showerror("Date signature invalide", "La date de signature doit être au format YYYY-MM-DD HH:MM:SS"); return

        lignes = _collect_lignes()
        if lignes is None: return
        if not lignes:
            messagebox.showwarning("Aucune sélection", "Sélectionnez au moins un article."); return

        client_id = None; conn = None
        try:
            conn = get_connection(); cur = conn.cursor()
            tin_val = nif_var.get().strip()
            if tin_val:
                cur.execute("SELECT id FROM client WHERE customer_TIN = ? LIMIT 1", (tin_val,))
                r = cur.fetchone()
                if r:
                    client_id = r["id"] if hasattr(r, "keys") else r[0]
                    cur.execute("""UPDATE client SET customer_name=?, customer_address=?, customer_phone_number=?, customer_postal_number=?, customer_email=?, customer_sector=?, vat_customer_payer=? WHERE id=?""",
                                (cl_vars["customer_name"].get(), cl_vars["customer_address"].get(), cl_vars["customer_phone_number"].get(),
                                 cl_vars["customer_postal_number"].get(), cl_vars["customer_email"].get(), cl_vars["customer_sector"].get(), 1 if vat_var.get() else 0, client_id))
                else:
                    cur.execute("""INSERT INTO client (customer_name, customer_address, customer_phone_number, customer_postal_number, customer_email, customer_sector, customer_TIN, vat_customer_payer) VALUES (?,?,?,?,?,?,?,?)""",
                                (cl_vars["customer_name"].get(), cl_vars["customer_address"].get(), cl_vars["customer_phone_number"].get(),
                                 cl_vars["customer_postal_number"].get(), cl_vars["customer_email"].get(), cl_vars["customer_sector"].get(), tin_val, 1 if vat_var.get() else 0))
                    client_id = cur.lastrowid
            else:
                name_val = cl_vars["customer_name"].get().strip() or "Client"
                cur.execute("SELECT id FROM client WHERE customer_name = ? LIMIT 1", (name_val,))
                r = cur.fetchone()
                if r:
                    client_id = r["id"] if hasattr(r, "keys") else r[0]
                else:
                    cur.execute("""INSERT INTO client (customer_name, customer_address, customer_phone_number, customer_postal_number, customer_email, customer_sector, customer_TIN, vat_customer_payer) VALUES (?,?,?,?,?,?,?,?)""",
                                (cl_vars["customer_name"].get(), cl_vars["customer_address"].get(), cl_vars["customer_phone_number"].get(),
                                 cl_vars["customer_postal_number"].get(), cl_vars["customer_email"].get(), cl_vars["customer_sector"].get(), "", 1 if vat_var.get() else 0))
                    client_id = cur.lastrowid
            conn.commit()
        except Exception as ex:
            if conn:
                try: conn.rollback()
                except Exception: pass
            logger.exception("Erreur enregistrement client local: %s", ex)
            messagebox.showerror("Erreur", f"Échec sauvegarde client local: {ex}")
            try: conn.close()
            except Exception: pass
            return
        finally:
            try:
                if conn: conn.close()
            except Exception: pass

        # Save facture + articles atomically
        conn = None; facture_id = None
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("""INSERT INTO facture (invoice_number,invoice_date,invoice_type,invoice_identifier,payment_type,currency,invoice_signature,invoice_signature_date,facture_statut,contribuable_id,client_id,total_amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (inv_num, date_var.get(), type_var_invoice.get().split(" -")[0], ident_var.get(), pay_var.get(), curr_var.get(), invoice_signature_var.get(), invoice_signature_date_var.get(), "non_envoyé", first_tp_id, client_id, float(total_var.get() or 0.0)))
            facture_id = cur.lastrowid
            for l in lignes:
                qty_to_store = l.get("item_quantity", 0)
                if isinstance(qty_to_store, float) and abs(qty_to_store - int(qty_to_store)) < 1e-9:
                    qty_to_store_db = int(qty_to_store)
                else:
                    qty_to_store_db = qty_to_store

                cur.execute("""INSERT INTO article (
                    facture_id, item_code, item_designation, quantity, unit_price_used,
                    unit_price_nvat, vat_amount, unit_price_wvat, line_total_amount, tax_rate, pricing_source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (facture_id, l["item_code"], l["item_designation"], qty_to_store_db, l.get("item_sale_price", l.get("item_price", 0)),
                             l["item_price_nvat"], l["vat_amount"], l["item_price_wvat"], l["item_total_amount"], l.get("tax_rate", 0.0), "default"))
                article_row_id = cur.lastrowid

                try:
                    cur.execute("SELECT id, item_quantity FROM article_stock_local WHERE item_code=? LIMIT 1", (l["item_code"],))
                    row = cur.fetchone()
                    if row:
                        stock_id = row["id"] if hasattr(row, "keys") else row[0]
                        current_qty = row["item_quantity"] if hasattr(row, "keys") else row[1]
                        try:
                            current_qty_val = float(current_qty) if current_qty is not None else 0.0
                        except Exception:
                            current_qty_val = 0.0
                        new_qty = current_qty_val - float(l["item_quantity"])
                        try:
                            cur.execute("UPDATE article_stock_local SET item_quantity=?, item_sale_price=?, item_cost_price=?, taux_tva=? WHERE id=?", (new_qty, l.get("item_sale_price", l.get("item_price", 0)), l.get("item_cost_price", 0.0), l.get("tax_rate", 0), stock_id))
                        except Exception:
                            try:
                                cur.execute("UPDATE article_stock_local SET item_quantity=? WHERE id=?", (new_qty, stock_id))
                            except Exception:
                                logger.exception("Impossible mettre à jour article_stock_local (quantity fallback)")
                    else:
                        try:
                            cur.execute("""INSERT INTO article_stock_local (
                                item_code, item_designation, item_measurement_unit, item_sale_price,
                                item_quantity, item_cost_price, taux_tva, date_enregistrement, is_manuel
                            ) VALUES (?,?,?,?,?,?,?,?,?)""",
                                        (l["item_code"], l["item_designation"], l.get("item_measurement_unit",""), l.get("item_sale_price", l.get("item_price", 0)),
                                         float(l["item_quantity"]), l.get("item_cost_price", 0.0), l.get("tax_rate", 0.0), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 1))
                        except Exception:
                            logger.exception("Impossible d'insérer article_stock_local pour %s", l.get("item_code"))
                except Exception:
                    logger.exception("Erreur insertion/mise à jour article_stock_local")
            conn.commit()
        except Exception as ex:
            if conn:
                try: conn.rollback()
                except Exception: pass
            logger.exception("Erreur enregistrement local: %s", ex)
            messagebox.showerror("Erreur", f"Échec enregistrement local: {ex}")
            try: conn.close()
            except Exception: pass
            return
        finally:
            try:
                if conn: conn.close()
            except Exception: pass

        # Prepare payload and send invoice to OBR
        tp_snapshot = {k: tp_vars[k].get() for k in tp_keys}
        sig_text, sig_date_field, electronic_sig = _refresh_signature()
        invoice_payload = {
            "invoice_number": inv_num,
            "invoice_date": date_var.get(),
            "invoice_identifier": ident_var.get(),
            "invoice_type": type_var_invoice.get().split(" -")[0],
            "tp_type": tp_snapshot.get("tp_type", ""),
            "tp_name": tp_snapshot.get("tp_name", ""),
            "tp_TIN": tp_snapshot.get("tp_TIN", ""),
            "tp_trade_number": tp_snapshot.get("tp_trade_number", ""),
            "tp_phone_number": tp_snapshot.get("tp_phone_number", ""),
            "tp_address_province": tp_snapshot.get("tp_address_province", ""),
            "tp_address_commune": tp_snapshot.get("tp_address_commune", ""),
            "tp_address_quartier": tp_snapshot.get("tp_address_quartier", ""),
            "tp_address_avenue": tp_snapshot.get("tp_address_avenue", ""),
            "tp_address_number": tp_snapshot.get("tp_address_number", ""),
            "tp_fiscal_center": tp_snapshot.get("tp_fiscal_center", ""),
            "tp_legal_form": tp_snapshot.get("tp_legal_form", ""),
            "tp_activity_sector": tp_snapshot.get("tp_activity_sector", ""),
            "vat_taxpayer": tp_snapshot.get("vat_taxpayer", ""),
            "ct_taxpayer": tp_snapshot.get("ct_taxpayer", ""),
            "tl_taxpayer": tp_snapshot.get("tl_taxpayer", ""),
            "customer_name": cl_vars["customer_name"].get(),
            "customer_TIN": nif_var.get().strip(),
            "customer_address": cl_vars["customer_address"].get(),
            "customer_phone_number": cl_vars["customer_phone_number"].get(),
            "vat_customer_payer": "1" if vat_var.get() else "",
            "payment_type": pay_var.get(),
            "invoice_ref": inv_num,
            "invoice_currency": curr_var.get(),
            "invoice_items": lignes,
            "invoice_signature": sig_text,
            "invoice_signature_date": sig_date_field,
            "electronic_signature": electronic_sig,
            "identifiant": ident_var.get(),
            "system_or_device_id": get_system_id()
        }

        try:
            token = obtenir_token_auto()
            if not token:
                messagebox.showerror("Erreur", "Impossible d'obtenir token OBR"); return
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            resp = requests.post("https://ebms.obr.gov.bi:9443/ebms_api/addInvoice/", json=invoice_payload, headers=headers, timeout=30)
        except Exception as ex:
            logger.exception("Erreur réseau OBR: %s", ex)
            messagebox.showerror("Erreur réseau", f"Échec envoi OBR: {ex}")
            return

        try:
            conn2 = get_connection(); cur2 = conn2.cursor()
            ok, msg = _traiter_reponse_obr_et_declarer_mouvements(resp, conn2, cur2, facture_id, lignes, invoice_payload["invoice_ref"], curr_var.get(), token)
            try: conn2.close()
            except Exception: pass
            if not ok:
                messagebox.showerror("Erreur OBR", msg)
            else:
                messagebox.showinfo("Succès OBR", msg)
        except Exception:
            logger.exception("Erreur après envoi OBR")
            messagebox.showerror("Erreur", "Erreur traitement réponse OBR")
            try:
                if conn2: conn2.close()
            except Exception: pass

    def _on_refresh():
        try:
            refresh_all_forms()
        except Exception:
            logger.exception("Erreur _on_refresh")

    def _on_cancel():
        try:
            for sel, qty_var, pu_var, code, des, unit, stock_val, fd, widgets in rows:
                try:
                    if hasattr(sel, "set"): sel.set(False)
                except Exception: pass
                try: qty_var.set("0")
                except Exception: pass
                try: pu_var.set("0.00")
                except Exception: pass
                try: widgets.get("lbl_total").config(text="0.00")
                except Exception: pass
            total_var.set("0.00")
        except Exception:
            logger.exception("Erreur annulation")

    def _save_and_redirect():
        try:
            _save_and_send()
        except Exception:
            logger.exception("Erreur dans _save_and_send lors du wrapper")
        try:
            from gui.tableau_de_Factures import afficher_liste_factures as _aff_liste
            _aff_liste(parent)
        except Exception:
            try:
                from gui.tableau_de_Factures import afficher_liste_factures
                afficher_liste_factures(parent)
            except Exception:
                logger.exception("Impossible d'appeler afficher_liste_factures — vérifie son emplacement/import")

    btn_save.config(command=_save_and_redirect)
    btn_refresh.config(command=_on_refresh)
    btn_cancel.config(command=_on_cancel)

    try: parent.update_idletasks()
    except Exception: pass
    try: nif_entry.focus_set()
    except Exception: pass

    # --- IMPORTANT: ensure signature is refreshed at load by default ---
    try:
        _refresh_signature()
    except Exception:
        logger.exception("Erreur initial refresh signature")

    return {"refresh": refresh_all_forms, "cancel": _on_cancel, "save_and_send": _save_and_send}
