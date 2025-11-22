# -*- coding: utf-8 -*-
"""
afficher_formulaire_facture_manual_adapte_schema.py
Formulaire de saisie manuelle adapté au schéma facturation_obr.db.
Modifications :
 - Par défaut TVA = 18.00 si checkbox Assujetti TVA cochée, sinon 0.00
 - L'utilisateur peut modifier le Taux TVA par ligne
 - Calculs defensifs et écriture des champs item_price_nvat, vat_amount, item_price_wvat
 - Mise à jour client.vat_customer_payer = 1 si vat_var coché (à l'insertion ou update ciblé)
"""
from __future__ import annotations
import hashlib
import logging
import time
import unicodedata
import requests
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import sqlite3
import _tkinter

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

            contribuable_id = None
            try:
                cur.execute("SELECT contribuable_id FROM facture WHERE id=? LIMIT 1", (facture_id,))
                r = cur.fetchone()
                if r:
                    contribuable_id = r["contribuable_id"] if hasattr(r, "keys") else r[0]
            except Exception:
                logger.exception("Impossible de récupérer contribuable_id pour mouvement_stock")

            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            for l in lignes:
                item_code = l.get("item_code","")
                mo_payload = {
                    "system_or_device_id": get_system_id(),
                    "item_code": item_code,
                    "item_designation": l.get("item_designation",""),
                    "item_quantity": str(l.get("item_quantity","0")),
                    "item_measurement_unit": l.get("item_measurement_unit",""),
                    "item_cost_price": str(l.get("item_cost_price",0.0)),
                    "item_purchase_or_sale_price": str(l.get("item_sale_price", l.get("item_price", 0.0))),
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
                            break
                        if resp_mv.status_code == 400:
                            status = 0
                            break
                    except Exception as ex:
                        logger.exception("Erreur réseau AddStockMovement tentative %d pour %s", attempt, item_code)
                    if attempt < max_attempts:
                        try:
                            time.sleep(backoff_seconds)
                            backoff_seconds *= 2
                        except Exception:
                            pass

                stock_id = None
                try:
                    c2 = conn.cursor()
                    c2.execute("SELECT id FROM article_stock_local WHERE item_code=? LIMIT 1", (item_code,))
                    rr = c2.fetchone()
                    if rr:
                        stock_id = rr["id"] if hasattr(rr, "keys") else rr[0]
                    try: c2.close()
                    except Exception: pass
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
                                    float(l.get("item_quantity",0) or 0),
                                    mo_payload.get("item_measurement_unit",""),
                                    float(mo_payload.get("item_purchase_or_sale_price") or 0.0),
                                    mo_payload.get("item_purchase_or_sale_currency", ""),
                                    mo_payload.get("item_movement_type","SV"),
                                    mo_payload.get("item_movement_date"),
                                    mo_payload.get("item_movement_invoice_ref"),
                                    mo_payload.get("item_movement_description"),
                                    stock_id,
                                    int(status),
                                    json.dumps({"request": mo_payload, "response": mv_json}, ensure_ascii=False),
                                    date_now
                                ))
                    conn.commit()
                except Exception as sql_ex:
                    try: conn.rollback()
                    except Exception: pass
                    logger.exception("Erreur insertion mouvement local invoice_ref=%s: %s", inv_num, sql_ex)
            return True, "Facture envoyée et mouvements traités"
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

def afficher_formulaire_facture_manual(parent):

    for w in parent.winfo_children(): w.destroy()
    try: parent.configure(bg=CONTENT_BG)
    except Exception: pass

    wrapper = tk.Frame(parent, bg=CONTENT_BG, padx=CARD_PAD, pady=CARD_PAD)
    wrapper.pack(fill="both", expand=True)
    tk.Label(wrapper, text="Nouvelle facture (saisie manuelle)", font=TITLE_FONT, bg=CONTENT_BG, fg=LABEL_FG).pack(anchor="w", pady=(0,12))

    # Contribuable caché et variables
    contrib_frame = tk.Frame(wrapper, bg=FORM_BG)
    contrib_frame.pack_forget()
    tp_vars = {
        "tp_type": tk.StringVar(value=""), "tp_name": tk.StringVar(value=""), "tp_TIN": tk.StringVar(value=""),
        "tp_trade_number": tk.StringVar(value=""), "tp_postal_number": tk.StringVar(value=""),
        "tp_phone_number": tk.StringVar(value=""), "tp_address_province": tk.StringVar(value=""),
        "tp_address_commune": tk.StringVar(value=""), "tp_address_quartier": tk.StringVar(value=""),
        "tp_address_avenue": tk.StringVar(value=""), "tp_address_rue": tk.StringVar(value=""),
        "tp_address_number": tk.StringVar(value=""), "tp_fiscal_center": tk.StringVar(value=""),
        "tp_legal_form": tk.StringVar(value=""), "tp_activity_sector": tk.StringVar(value=""),
        "vat_taxpayer": tk.StringVar(value=""), "ct_taxpayer": tk.StringVar(value=""), "tl_taxpayer": tk.StringVar(value="")
    }

    first_tp_id = None
    try:
        conn_tmp = get_connection(); cur_tmp = conn_tmp.cursor()
        cur_tmp.execute("SELECT id FROM contribuable ORDER BY tp_name LIMIT 1")
        r = cur_tmp.fetchone()
        first_tp_id = r["id"] if r and hasattr(r, "keys") else (r[0] if r else None)
        conn_tmp.close()
        if first_tp_id:
            tp_data = get_contribuable_data(first_tp_id) or {}
            for k in tp_vars:
                if k in tp_data and tp_data[k] is not None:
                    tp_vars[k].set(tp_data[k])
    except Exception:
        logger.exception("Erreur pré-remplissage contribuable")

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

    # NIF verification
    nif_frame = tk.Frame(client_frame, bg=FORM_BG)
    nif_frame.grid(row=4, column=0, columnspan=4, sticky="ew", padx=6, pady=(8,0))
    nif_frame.grid_columnconfigure(0, weight=0); nif_frame.grid_columnconfigure(1, weight=1); nif_frame.grid_columnconfigure(2, weight=0)
    tk.Label(nif_frame, text="NIF :", font=DEFAULT_FONT, bg=FORM_BG, fg=LABEL_FG).grid(row=0, column=0, sticky="w", padx=6)
    nif_var = tk.StringVar()
    nif_entry = ttk.Entry(nif_frame, textvariable=nif_var, width=36, font=DEFAULT_FONT, style="Form.TEntry")
    nif_entry.grid(row=0, column=1, sticky="w", padx=6)
    btn_check_nif = tk.Button(nif_frame, text="Vérifier NIF", bg=BUTTON_ACTION_BG, activebackground=BUTTON_ACTION_ACTIVE, fg=BUTTON_FG, relief="raised", bd=1, padx=8, pady=4)
    btn_check_nif.grid(row=0, column=2, sticky="w", padx=6)
    vat_var = tk.BooleanVar(value=False)
    chk_vat = ttk.Checkbutton(nif_frame, text="Assujetti TVA", variable=vat_var)
    chk_vat.grid(row=0, column=3, sticky="w", padx=(12,6))

    # Handler: when vat_var changes, update existing lines' TVA default shown and recalc totals
    def _on_vat_toggle(*_):
        try:
            forced = "18.00" if vat_var.get() else "0.00"
            for rd in manual_rows:
                try:
                    tvv = rd.get("tva_var")
                    if tvv is not None:
                        # do not override if user intentionally changed? requirement set default but allow edit:
                        # we update only if current value equals previous default OR empty
                        try:
                            cur_val = (tvv.get() or "").strip()
                        except Exception:
                            cur_val = ""
                        # If current value equals the other default, replace; else leave user edit intact.
                        other_default = "0.00" if vat_var.get() else "18.00"
                        if cur_val == "" or cur_val == other_default:
                            try:
                                tvv.set(forced)
                            except Exception:
                                ent = rd.get("ent_tva")
                                if ent and getattr(ent, "winfo_exists", lambda: False)():
                                    try: ent.delete(0, "end"); ent.insert(0, forced)
                                    except Exception: pass
                except Exception:
                    pass
            for i in range(len(manual_rows)):
                try:
                    _calc_row_total(i)
                except Exception:
                    pass
            _recalc_totals()
        except Exception:
            logger.exception("Erreur lors du toggle Assujetti TVA")

    try:
        vat_var.trace_add("write", _on_vat_toggle)
    except Exception:
        try:
            vat_var.trace("w", _on_vat_toggle)
        except Exception:
            pass

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
                vat_var.set((local.get("vat_customer_payer") in (1, "1", "True", True)))
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

    # Détails facture
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
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            tin = tp_vars.get("tp_TIN").get() or nif_var.get().strip() or "UNKNOWN"
            ident_var.set(f"{tin}/{get_system_id()}/{ts}/{num_var.get()}")
        except Exception:
            logger.exception("Erreur maj_ident")
    try:
        num_var.trace_add("write", maj_ident)
    except Exception:
        try: num_var.trace("w", maj_ident)
        except Exception: logger.exception("Impossible d'attacher trace à num_var")
    maj_ident()

    sig_text_init, sig_date_init, elec_init = _build_obr_invoice_signature({k: v.get() for k, v in tp_vars.items()}, num_var.get().strip() or get_next_invoice_number(), invoice_signature_date_var.get().strip())
    invoice_signature_var.set(sig_text_init)
    invoice_signature_date_var.set(sig_date_init)

    def _refresh_signature(*_):
        try:
            tp_curr = {k: tp_vars[k].get() for k in tp_vars}
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

    # Tableau lignes (canvas + scrollbars)
    table_border = tk.Frame(wrapper, bg="#1f1f1f", bd=2, relief="solid")
    table_border.pack(fill="both", expand=False, pady=(0,12))
    table_border.configure(padx=1, pady=1)

    table_canvas = tk.Canvas(table_border, bg=FORM_BG, highlightthickness=0)
    hscroll = ttk.Scrollbar(table_border, orient="horizontal", command=table_canvas.xview)
    vscroll = ttk.Scrollbar(table_border, orient="vertical", command=table_canvas.yview)
    table_canvas.configure(xscrollcommand=hscroll.set, yscrollcommand=vscroll.set)
    table_canvas.grid(row=0, column=0, sticky="nsew")
    vscroll.grid(row=0, column=1, sticky="ns")
    hscroll.grid(row=1, column=0, sticky="ew", columnspan=2)
    table_border.grid_rowconfigure(0, weight=1)
    table_border.grid_columnconfigure(0, weight=1)

    lines_frame = tk.Frame(table_canvas, bg=FORM_BG)
    lines_frame_id = table_canvas.create_window((0,0), window=lines_frame, anchor="nw")

    def _on_lines_config(event):
        try:
            table_canvas.configure(scrollregion=table_canvas.bbox("all"))
            inner_w = max(lines_frame.winfo_reqwidth(), event.width)
            table_canvas.itemconfig(lines_frame_id, width=inner_w)
        except Exception:
            pass

    table_canvas.bind("<Configure>", _on_lines_config)

    headers = [
        ("#", 4), ("Code", 12), ("Désignation", 22), ("Unité", 8),
        ("Qté", 8), ("PU (vente)", 12), ("PU (achat)", 12), ("Taux TVA %", 10), ("Total TTC", 12), ("Action", 10)
    ]
    for c, (h, w) in enumerate(headers):
        lbl = tk.Label(lines_frame, text=h, bg=FORM_BG, fg=LABEL_FG, font=("Segoe UI", 9, "bold"),
                       width=w, anchor="w" if c==2 else "center", bd=1, relief="solid")
        lbl.grid(row=0, column=c, padx=0, pady=0, sticky="nsew")

    manual_rows = []
    total_label_var = tk.StringVar(value="0.00")

    def _recalc_totals():
        s = 0.0
        for rd in manual_rows:
            try:
                s += float(rd.get("cached_total", 0.0) or 0.0)
            except Exception:
                pass
        try:
            total_label_var.set(f"{s:.2f}")
        except Exception:
            pass

    def _calc_row_total(idx):
        try:
            if idx < 0 or idx >= len(manual_rows):
                return
            row = manual_rows[idx]
            try:
                qty = float(row['qty_var'].get() or 0.0)
            except Exception:
                qty = 0.0
            try:
                pu = float(row['pu_var'].get() or 0.0)
            except Exception:
                pu = 0.0
            try:
                taux = float(row['tva_var'].get() or 0.0)
            except Exception:
                taux = 0.0
            ht = pu * qty
            tva = ht * (taux / 100.0)
            total = ht + tva
            row['cached_total'] = total
            lbl = row.get('lbl_total')
            if lbl is not None:
                try:
                    if getattr(lbl, "winfo_exists", lambda: False)():
                        lbl.config(text=f"{total:.2f}")
                except _tkinter.TclError:
                    pass
        except Exception:
            logger.exception("Erreur calcul total ligne")

    def _remove_this(idx):
        try:
            if idx < 0 or idx >= len(manual_rows):
                return
            rdict = manual_rows[idx]
            for var_name in ("qty_var", "pu_var", "tva_var"):
                try:
                    var = rdict.get(var_name)
                    if var is not None:
                        try:
                            tinfo = var.trace_info() or []
                            for trace in list(tinfo):
                                try:
                                    mode = trace[0]
                                    cbname = trace[1]
                                    var.trace_remove(mode, cbname)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
            for wkey in ("lbl_idx","ent_code","ent_desig","ent_unit","ent_qty","ent_pu","ent_cost","ent_tva","lbl_total","btn_remove"):
                widget = rdict.get(wkey)
                if widget is None:
                    continue
                try:
                    if getattr(widget, "winfo_exists", lambda: False)():
                        widget.destroy()
                except _tkinter.TclError:
                    pass
                except Exception:
                    pass
            manual_rows.pop(idx)
            for i, rd in enumerate(manual_rows, start=1):
                try:
                    lbl = rd.get("lbl_idx")
                    if lbl and getattr(lbl, "winfo_exists", lambda: False)():
                        lbl.config(text=str(i))
                        lbl.grid_configure(row=i)
                    for key in ("ent_code","ent_desig","ent_unit","ent_qty","ent_pu","ent_cost","ent_tva","lbl_total","btn_remove"):
                        w = rd.get(key)
                        if w and getattr(w, "winfo_exists", lambda: False)():
                            w.grid_configure(row=i)
                except Exception:
                    logger.exception("Erreur réindexation ligne")
            _recalc_totals()
        except Exception:
            logger.exception("Erreur suppression ligne (wrapper)")

    def _add_manual_row(prefill: dict = None):
        row_index = len(manual_rows) + 1
        r = row_index

        lbl_idx = tk.Label(lines_frame, text=str(row_index), bg=FORM_BG, fg=LABEL_FG, width=4, bd=1, relief="solid")
        ent_code = tk.Entry(lines_frame, width=12, bd=1, relief="solid")
        ent_desig = tk.Entry(lines_frame, width=22, bd=1, relief="solid")
        ent_unit = tk.Entry(lines_frame, width=8, bd=1, relief="solid")
        qty_var = tk.StringVar(value=str(prefill.get("item_quantity", "0")) if prefill else "0")
        ent_qty = tk.Entry(lines_frame, textvariable=qty_var, width=8, bd=1, relief="solid")
        pu_var = tk.StringVar(value=f"{prefill.get('item_price', '')}" if prefill else "0.00")
        ent_pu = tk.Entry(lines_frame, textvariable=pu_var, width=12, bd=1, relief="solid")
        cost_var = tk.StringVar(value=f"{prefill.get('item_cost_price', '')}" if prefill else "0.00")
        ent_cost = tk.Entry(lines_frame, textvariable=cost_var, width=12, bd=1, relief="solid")
        # default TVA per vat_var, but allow user edit
        default_tva = "18.00" if vat_var.get() else "0.00"
        tva_var = tk.StringVar(value=str(prefill.get('taux_tva', default_tva)) if prefill else default_tva)
        ent_tva = tk.Entry(lines_frame, textvariable=tva_var, width=10, bd=1, relief="solid")
        lbl_total = tk.Label(lines_frame, text="0.00", bg=FORM_BG, fg=LABEL_FG, width=12, anchor="e", bd=1, relief="solid")
        btn_remove = tk.Button(lines_frame, text="Suppr", bg="#dc3545", activebackground="#c82333", fg="white", padx=6, pady=2, bd=1, relief="raised")

        lbl_idx.grid(row=r, column=0, padx=0, pady=0, sticky="nsew")
        ent_code.grid(row=r, column=1, padx=0, pady=0, sticky="nsew")
        ent_desig.grid(row=r, column=2, padx=0, pady=0, sticky="nsew")
        ent_unit.grid(row=r, column=3, padx=0, pady=0, sticky="nsew")
        ent_qty.grid(row=r, column=4, padx=0, pady=0, sticky="nsew")
        ent_pu.grid(row=r, column=5, padx=0, pady=0, sticky="nsew")
        ent_cost.grid(row=r, column=6, padx=0, pady=0, sticky="nsew")
        ent_tva.grid(row=r, column=7, padx=0, pady=0, sticky="nsew")
        lbl_total.grid(row=r, column=8, padx=0, pady=0, sticky="nsew")
        btn_remove.grid(row=r, column=9, padx=0, pady=0, sticky="nsew")

        if prefill:
            ent_code.insert(0, str(prefill.get("item_code", "")))
            ent_desig.insert(0, str(prefill.get("item_designation", "")))
            ent_unit.insert(0, str(prefill.get("item_measurement_unit", "")))

        row_dict = {
            "lbl_idx": lbl_idx, "ent_code": ent_code, "ent_desig": ent_desig, "ent_unit": ent_unit,
            "qty_var": qty_var, "ent_qty": ent_qty, "pu_var": pu_var, "ent_pu": ent_pu,
            "cost_var": cost_var, "ent_cost": ent_cost, "tva_var": tva_var, "ent_tva": ent_tva,
            "lbl_total": lbl_total, "btn_remove": btn_remove, "cached_total": 0.0
        }
        manual_rows.append(row_dict)

        def _on_change(*a, idx=len(manual_rows)-1):
            _calc_row_total(idx)
            _recalc_totals()
        try:
            qty_var.trace_add("write", _on_change)
            pu_var.trace_add("write", _on_change)
            tva_var.trace_add("write", _on_change)
        except Exception:
            try:
                qty_var.trace("w", _on_change)
                pu_var.trace("w", _on_change)
                tva_var.trace("w", _on_change)
            except Exception:
                pass

        def _btn_remove_command(local_idx=len(manual_rows)-1):
            _remove_this(local_idx)

        btn_remove.config(command=_btn_remove_command)

        _calc_row_total(len(manual_rows)-1)
        _recalc_totals()

    _add_manual_row()

    controls = tk.Frame(wrapper, bg=FORM_BG)
    controls.pack(fill="x", pady=(6,12)); controls.configure(padx=6, pady=4)
    btn_add = tk.Button(controls, text="Ajouter ligne", bg="#198754", activebackground="#157347", fg="white", padx=10, pady=6)
    btn_add.pack(side="left", padx=6)
    tk.Label(controls, text="Total facture TTC :", bg=FORM_BG, fg=LABEL_FG, font=("Segoe UI", 11, "bold")).pack(side="left", padx=(12,6))
    tk.Label(controls, textvariable=total_label_var, bg=FORM_BG, fg=LABEL_FG, font=("Segoe UI", 11, "bold")).pack(side="left")

    btn_add.config(command=lambda: (_add_manual_row(), _recalc_totals()))

    def _rows_periodic_check():
        try:
            for i in range(len(manual_rows)):
                try:
                    rd = manual_rows[i]
                except IndexError:
                    break
                try:
                    lbl = rd.get("lbl_total")
                    if lbl is None or not getattr(lbl, "winfo_exists", lambda: False)():
                        continue
                    _calc_row_total(i)
                except Exception:
                    pass
            _recalc_totals()
        except Exception:
            logger.exception("Erreur periodic check")
        parent.after(600, _rows_periodic_check)
    parent.after(600, _rows_periodic_check)

    action_frame = tk.Frame(wrapper, bg=FORM_BG)
    action_frame.pack(fill="x", pady=(6,0)); action_frame.configure(padx=6, pady=6)

    btn_save = tk.Button(action_frame, text="Sauvegarder & Envoyer", bg=BUTTON_SAVE_BG, activebackground=BUTTON_SAVE_ACTIVE, fg=BUTTON_FG, padx=12, pady=6)
    btn_save.pack(side="left", padx=6)
    btn_cancel = tk.Button(action_frame, text="Annuler", bg=BUTTON_WARN_BG, activebackground=BUTTON_WARN_ACTIVE, fg=BUTTON_WARN_FG, padx=12, pady=6)
    btn_cancel.pack(side="right", padx=6)

    def _collect_manual_lines():
        lignes = []
        for rd in manual_rows:
            try:
                code = rd["ent_code"].get().strip()
                des = rd["ent_desig"].get().strip()
                unit = rd["ent_unit"].get().strip()
                try:
                    qty_raw = rd["qty_var"].get()
                    qty = float(qty_raw) if str(qty_raw).strip() else 0.0
                except Exception:
                    qty = 0.0
                try:
                    pu = float(rd["pu_var"].get()) if rd["pu_var"].get().strip() else 0.0
                except Exception:
                    pu = 0.0
                try:
                    cost = float(rd["cost_var"].get()) if rd["cost_var"].get().strip() else 0.0
                except Exception:
                    cost = 0.0
                try:
                    taux = float(rd["tva_var"].get()) if rd["tva_var"].get().strip() else (18.0 if vat_var.get() else 0.0)
                except Exception:
                    taux = 18.0 if vat_var.get() else 0.0
                if qty <= 0:
                    messagebox.showwarning("Quantité", f"Quantité invalide pour '{des or code}'")
                    return None
                ht = pu * qty
                tva_amount = ht * (taux/100.0)
                total_ttc = ht + tva_amount
                # Normaliser qty en entier si valeur entière, sinon float
                if abs(qty - int(qty)) < 1e-9:
                    qty_to_store = int(qty)
                else:
                    qty_to_store = round(qty, 6)
                lignes.append({
                    "item_code": code or f"MAN_{int(time.time()*1000)%100000}",
                    "item_designation": des or "Article manuel",
                    "item_measurement_unit": unit or "",
                    "item_quantity": qty_to_store,
                    "item_price": round(pu, 2),
                    "item_sale_price": round(pu, 2),
                    "item_cost_price": round(cost, 2),
                    "taux_tva": round(taux, 2),
                    "vat": round(taux, 2),
                    "vat_amount": round(tva_amount, 2),
                    "item_price_nvat": round(ht, 2),
                    "item_price_wvat": round(ht + tva_amount, 2),
                    "item_total_amount": round(total_ttc, 2)
                })
            except Exception:
                logger.exception("Erreur lecture ligne manuelle")
                messagebox.showerror("Erreur", "Erreur lecture d'une ligne d'article")
                return None
        return lignes

    def _on_cancel():
        try:
            for rd in manual_rows[:]:
                try:
                    for key in ("lbl_idx","ent_code","ent_desig","ent_unit","ent_qty","ent_pu","ent_cost","ent_tva","lbl_total","btn_remove"):
                        w = rd.get(key)
                        try:
                            if w and getattr(w, "winfo_exists", lambda: False)():
                                w.destroy()
                        except Exception:
                            pass
                except Exception:
                    pass
            manual_rows.clear()
            _add_manual_row()
            _recalc_totals()
        except Exception:
            logger.exception("Erreur annulation formulaire manuel")

    def _save_and_send_manual():
        lignes = _collect_manual_lines()
        if lignes is None or not lignes:
            return

        tp_snapshot = {k: v.get() for k, v in tp_vars.items()}

        sig_text, sig_date_field, electronic_sig = _build_obr_invoice_signature(tp_snapshot or {"tp_TIN": nif_var.get()}, num_var.get(), invoice_signature_date_var.get())
        if not validate_signature_date(sig_date_field):
            messagebox.showerror("Date signature", "Date signature invalide")
            return

        facture_id = None
        conn = None
        try:
            conn = get_connection(); cur = conn.cursor()

            # --- Déterminer ou créer client minimal (client_id non null requis) ---
            client_id = None
            client_table_used = None
            try:
                tin_val = nif_var.get().strip()
                possible_tables = ["client", "clients", "client_local", "client_tbl"]

                # 1) Si on a un NIF non vide : chercher par NIF strict (ne touche qu'au matching)
                if tin_val:
                    for tbl in possible_tables:
                        try:
                            cur.execute(f"SELECT id, vat_customer_payer FROM {tbl} WHERE customer_TIN=? LIMIT 1", (tin_val,))
                            row = cur.fetchone()
                            if row:
                                client_id = row["id"] if hasattr(row, "keys") else row[0]
                                client_table_used = tbl
                                break
                        except Exception:
                            pass

                # 2) Si pas trouvé par NIF : chercher par nom (si présent)
                if not client_id:
                    cname = cl_vars.get("customer_name").get().strip() if cl_vars.get("customer_name") else ""
                    if cname:
                        for tbl in possible_tables:
                            try:
                                cur.execute(f"SELECT id, customer_TIN, vat_customer_payer FROM {tbl} WHERE customer_name=? LIMIT 1", (cname,))
                                row = cur.fetchone()
                                if row:
                                    client_id = row["id"] if hasattr(row, "keys") else row[0]
                                    client_table_used = tbl
                                    break
                            except Exception:
                                pass

                # 3) Si toujours pas trouvé : insérer nouveau record dans la première table disponible
                if not client_id:
                    inserted = False
                    for tbl in possible_tables:
                        try:
                            cur.execute(
                                f"INSERT INTO {tbl} (customer_name, customer_TIN, customer_address, customer_phone_number, customer_email, vat_customer_payer) VALUES (?,?,?,?,?,?)",
                                (
                                    cl_vars.get("customer_name").get() if cl_vars.get("customer_name") else "",
                                    tin_val if tin_val else None,
                                    cl_vars.get("customer_address").get() if cl_vars.get("customer_address") else "",
                                    cl_vars.get("customer_phone_number").get() if cl_vars.get("customer_phone_number") else "",
                                    cl_vars.get("customer_email").get() if cl_vars.get("customer_email") else "",
                                    1 if vat_var.get() else 0
                                )
                            )
                            client_id = cur.lastrowid
                            client_table_used = tbl
                            inserted = True
                            break
                        except Exception:
                            try: conn.rollback()
                            except Exception: pass
                            continue

                    if not inserted and client_id is None:
                        try:
                            cur.execute("""CREATE TABLE IF NOT EXISTS client (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            customer_name TEXT,
                                            customer_TIN TEXT,
                                            customer_address TEXT,
                                            customer_phone_number TEXT,
                                            customer_email TEXT,
                                            vat_customer_payer INTEGER DEFAULT 0
                                        )""")
                            cur.execute(
                                "INSERT INTO client (customer_name, customer_TIN, customer_address, customer_phone_number, customer_email, vat_customer_payer) VALUES (?,?,?,?,?,?)",
                                (
                                    cl_vars.get("customer_name").get() if cl_vars.get("customer_name") else "",
                                    tin_val if tin_val else None,
                                    cl_vars.get("customer_address").get() if cl_vars.get("customer_address") else "",
                                    cl_vars.get("customer_phone_number").get() if cl_vars.get("customer_phone_number") else "",
                                    cl_vars.get("customer_email").get() if cl_vars.get("customer_email") else "",
                                    1 if vat_var.get() else 0
                                )
                            )
                            client_id = cur.lastrowid
                            client_table_used = "client"
                        except Exception:
                            try: conn.rollback()
                            except Exception: pass
                            client_id = None

                # 4) Mise à jour ponctuelle : n'exécutez UPDATE que si on a trouvé un client_id
                if client_id and client_table_used:
                    try:
                        update_values = [
                            cl_vars.get("customer_name").get() if cl_vars.get("customer_name") else "",
                            cl_vars.get("customer_address").get() if cl_vars.get("customer_address") else "",
                            cl_vars.get("customer_phone_number").get() if cl_vars.get("customer_phone_number") else "",
                            cl_vars.get("customer_email").get() if cl_vars.get("customer_email") else ""
                        ]
                        if tin_val:
                            cur.execute(f"""
                                UPDATE {client_table_used}
                                SET customer_name = ?, customer_TIN = ?, customer_address = ?, customer_phone_number = ?, customer_email = ?, vat_customer_payer = ?
                                WHERE id = ?
                            """, (update_values[0], tin_val, update_values[1], update_values[2], update_values[3], 1 if vat_var.get() else 0, client_id))
                        else:
                            if vat_var.get():
                                cur.execute(f"""
                                    UPDATE {client_table_used}
                                    SET customer_name = ?, customer_address = ?, customer_phone_number = ?, customer_email = ?, vat_customer_payer = ?
                                    WHERE id = ?
                                """, (update_values[0], update_values[1], update_values[2], update_values[3], 1, client_id))
                            else:
                                cur.execute(f"""
                                    UPDATE {client_table_used}
                                    SET customer_name = ?, customer_address = ?, customer_phone_number = ?, customer_email = ?
                                    WHERE id = ?
                                """, (update_values[0], update_values[1], update_values[2], update_values[3], client_id))
                    except Exception:
                        logger.exception("Impossible de mettre à jour le client avec les valeurs saisies")
                try:
                    conn.commit()
                except Exception:
                    pass
            except Exception:
                logger.exception("Erreur détermination/création client")
                try: conn.rollback()
                except Exception: pass
                client_id = None

            if not client_id:
                messagebox.showerror("Client manquant", "Impossible de créer ou retrouver un client valide; la facture nécessite un client.")
                try:
                    if conn: conn.close()
                except Exception:
                    pass
                return

            # Compute total_amount for facture
            total_amount = 0.0
            for l in lignes:
                try:
                    total_amount += float(l.get("item_total_amount", 0.0))
                except Exception:
                    pass

            # --- Insertion facture ---
            cur.execute("""INSERT INTO facture (
                                invoice_number, invoice_date, invoice_type, invoice_identifier,
                                payment_type, currency, invoice_signature, invoice_signature_date,
                                facture_statut, contribuable_id, client_id, total_amount
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (num_var.get(), date_var.get(), type_var_invoice.get().split(" -")[0], ident_var.get(),
                         pay_var.get(), curr_var.get(), sig_text, sig_date_field, "non_envoyé", first_tp_id, client_id, float(total_amount)))
            facture_id = cur.lastrowid

            # --- Insertion articles et gestion stock local/mouvements ---
            for l in lignes:
                qty_to_store = l.get("item_quantity", 0)
                if isinstance(qty_to_store, float) and abs(qty_to_store - int(qty_to_store)) < 1e-9:
                    qty_to_store_db = int(qty_to_store)
                else:
                    qty_to_store_db = qty_to_store

                # Read per-line TVA if provided, otherwise default based on vat_var
                try:
                    taux_from_line = float(l.get("taux_tva", l.get("vat", None))) if (l.get("taux_tva", None) is not None) else None
                except Exception:
                    taux_from_line = None
                taux_tva_to_store = float(taux_from_line) if taux_from_line is not None else (18.0 if vat_var.get() else 0.0)

                # Defensive calculations for HT/TVA/TTC
                try:
                    unit_ht = float(l.get("item_price", l.get("item_sale_price", 0.0)) or 0.0)
                except Exception:
                    unit_ht = 0.0
                try:
                    qty_local = float(l.get("item_quantity", qty_to_store_db) or 0.0)
                except Exception:
                    qty_local = float(qty_to_store_db or 0.0)
                total_ht = round(unit_ht * qty_local, 2)
                vat_amount = round(total_ht * (taux_tva_to_store / 100.0), 2)
                total_wvat = round(total_ht + vat_amount, 2)
                line_total = round(l.get("item_total_amount", total_wvat), 2)

                # Insert into article with updated VAT fields
                cur.execute("""INSERT INTO article (
                    facture_id, item_code, item_designation, quantity, unit_price_used,
                    unit_price_nvat, vat_amount, unit_price_wvat, line_total_amount, tax_rate, pricing_source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                facture_id,
                                l["item_code"],
                                l["item_designation"],
                                qty_to_store_db,
                                round(l.get("item_sale_price", l.get("item_price", 0.0)), 2),
                                round(total_ht, 2),
                                round(vat_amount, 2),
                                round(total_wvat, 2),
                                line_total,
                                taux_tva_to_store,
                                "manual"
                            ))
                article_row_id = cur.lastrowid

                # update or insert article_stock_local
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
                        new_qty = current_qty_val - float(l.get("item_quantity", 0))
                        try:
                            cur.execute("UPDATE article_stock_local SET item_quantity=?, item_sale_price=?, item_cost_price=?, taux_tva=?, item_price_nvat=?, item_price_wvat=? WHERE id=?",
                                        (new_qty, l.get("item_sale_price", l.get("item_price", 0.0)), l.get("item_cost_price", 0.0), (taux_tva_to_store), round(total_ht,2), round(total_wvat,2), stock_id))
                        except Exception:
                            try:
                                cur.execute("UPDATE article_stock_local SET item_quantity=?, taux_tva=?, item_price_nvat=?, item_price_wvat=? WHERE id=?", (new_qty, (taux_tva_to_store), round(total_ht,2), round(total_wvat,2), stock_id))
                            except Exception:
                                logger.exception("Impossible mettre à jour article_stock_local (quantity fallback)")
                    else:
                        try:
                            cur.execute("""INSERT INTO article_stock_local (
                                                item_code, item_designation, item_measurement_unit, item_sale_price,
                                                item_quantity, item_cost_price, taux_tva, item_price_nvat, item_price_wvat, date_enregistrement, is_manuel
                                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                                        (l["item_code"], l["item_designation"], l.get("item_measurement_unit",""), l.get("item_sale_price", l.get("item_price", 0.0)),
                                         float(l.get("item_quantity", 0)), l.get("item_cost_price", 0.0), (taux_tva_to_store), round(total_ht,2), round(total_wvat,2), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 1))
                        except Exception:
                            try:
                                cur.execute("""INSERT INTO article_stock_local (
                                                    item_code, item_designation, item_measurement_unit, item_sale_price,
                                                    item_quantity, item_cost_price, taux_tva, item_price_nvat, item_price_wvat, date_enregistrement
                                                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                            (l["item_code"], l["item_designation"], l.get("item_measurement_unit",""), l.get("item_sale_price", l.get("item_price", 0.0)),
                                             float(l.get("item_quantity", 0)), l.get("item_cost_price", 0.0), (taux_tva_to_store), round(total_ht,2), round(total_wvat,2), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                                try:
                                    cur.execute("UPDATE article_stock_local SET is_manuel = 1 WHERE item_code = ?", (l["item_code"],))
                                except Exception:
                                    pass
                            except Exception:
                                logger.exception("Impossible d'insérer record article_stock_local")
                except Exception:
                    logger.exception("Erreur gestion stock local")
            conn.commit()

        except sqlite3.IntegrityError as ie:
            if conn:
                try: conn.rollback()
                except Exception: pass
            logger.exception("Erreur sauvegarde facture manuelle: %s", ie)
            messagebox.showerror("Erreur BD", f"Échec sauvegarde (contrainte BD): {ie}")
            try:
                if conn: conn.close()
            except Exception:
                pass
            return
        except Exception as ex:
            if conn:
                try: conn.rollback()
                except Exception: pass
            logger.exception("Erreur sauvegarde facture manuelle: %s", ex)
            messagebox.showerror("Erreur", f"Échec sauvegarde: {ex}")
            try:
                if conn: conn.close()
            except Exception:
                pass
            return
        finally:
            try:
                if conn: conn.close()
            except Exception:
                pass

        # Envoi automatique OBR (payload complet)
        inv_num = num_var.get()
        sig_text, sig_date_field, electronic_sig = _build_obr_invoice_signature(tp_snapshot or {"tp_TIN": nif_var.get()}, inv_num, invoice_signature_date_var.get())

        vat_total = 0.0
        invoice_items_payload = []
        for l in lignes:
            try:
                try:
                    unit_ht = float(l.get("item_price", l.get("item_sale_price", 0.0)) or 0.0)
                except Exception:
                    unit_ht = 0.0
                try:
                    qty_local = float(l.get("item_quantity", 0) or 0.0)
                except Exception:
                    qty_local = 0.0
                taux_local = float(l.get("taux_tva", l.get("vat", None))) if (l.get("taux_tva", None) is not None) else (18.0 if vat_var.get() else 0.0)
                total_ht = round(unit_ht * qty_local, 2)
                vat_amount = round(total_ht * (taux_local / 100.0), 2)
                total_wvat = round(total_ht + vat_amount, 2)

                item_payload = {
                    "item_code": l.get("item_code", ""),
                    "item_designation": l.get("item_designation", ""),
                    "item_quantity": l.get("item_quantity", 0),
                    "item_price": round(l.get("item_price", 0.0), 2),
                    "item_purchase_or_sale_price": round(l.get("item_sale_price", l.get("item_price", 0.0)), 2),
                    "item_price_nvat": round(total_ht, 2),
                    "item_price_wvat": round(total_wvat, 2),
                    "vat": round(vat_amount, 2),
                    "item_total_amount": round(l.get("item_total_amount", total_wvat), 2),
                    "item_cost_price": round(l.get("item_cost_price", 0.0), 2),
                    "item_measurement_unit": l.get("item_measurement_unit", "")
                }
                invoice_items_payload.append(item_payload)
                vat_total += float(item_payload.get("vat", 0.0))
            except Exception:
                logger.exception("Erreur construction item payload")

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
            "customer_name": cl_vars["customer_name"].get() if cl_vars.get("customer_name") else "",
            "customer_TIN": nif_var.get().strip(),
            "customer_address": cl_vars["customer_address"].get() if cl_vars.get("customer_address") else "",
            "customer_phone_number": cl_vars["customer_phone_number"].get() if cl_vars.get("customer_phone_number") else "",
            "vat_customer_payer": "1" if vat_var.get() else "",
            "payment_type": pay_var.get(),
            "invoice_ref": inv_num,
            "invoice_currency": curr_var.get(),
            "invoice_items": invoice_items_payload,
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

        # Reset UI après succès
        try:
            for rd in manual_rows[:]:
                try:
                    for key in ("lbl_idx","ent_code","ent_desig","ent_unit","ent_qty","ent_pu","ent_cost","ent_tva","lbl_total","btn_remove"):
                        w = rd.get(key)
                        try:
                            if w and getattr(w, "winfo_exists", lambda: False)():
                                w.destroy()
                        except Exception:
                            pass
                except Exception:
                    pass
            manual_rows.clear()
            _add_manual_row()
            _recalc_totals()
            num_var.set(get_next_invoice_number())
            date_var.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            try:
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                tin = tp_vars.get("tp_TIN").get() or nif_var.get().strip() or "UNKNOWN"
                ident_var.set(f"{tin}/{get_system_id()}/{ts}/{num_var.get()}")
            except Exception:
                pass
        except Exception:
            logger.exception("Erreur reset formulaire après sauvegarde")

        # Redirection vers la liste des factures
        try:
            try:
                from gui.tableau_de_Factures import afficher_liste_factures as _aff_liste
                _aff_liste(parent)
            except Exception:
                try:
                    from gui.tableau_de_Factures import afficher_liste_factures
                    afficher_liste_factures(parent)
                except Exception:
                    logger.exception("Impossible d'appeler afficher_liste_factures — vérifie son emplacement/import")
        except Exception:
            logger.exception("Erreur redirection afficher_liste_factures")

    btn_save.config(command=_save_and_send_manual)
    btn_cancel.config(command=_on_cancel)

    parent.after(200, lambda: (manual_rows[0]["ent_code"].focus_set() if manual_rows else None))

    return {"add_line": _add_manual_row, "save": _save_and_send_manual, "cancel": _on_cancel}
