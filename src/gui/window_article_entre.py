#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gestion_stock_fr_final_v2.py
- Retires le bouton collé à Désignation
- Combobox large (alignée aux entrées)
- Retour à l'état initial quand on choisit un type Entrée
- Readonly visuel (fond gris) et restauration
- Rafraîchir codes global en pied de page (pas par carte)
- Toutes les autres fonctionnalités précédentes conservées
Remplace get_connection, obtenir_token_auto, get_system_id par tes implémentations.
"""
import threading
import json
import time
import requests
from decimal import Decimal, ROUND_HALF_UP
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

from database.connection import get_connection
from api.obr_client import obtenir_token_auto, get_system_id

# Styles / constantes
CONTENT_BG = "white"
FORM_BG = "#f8f9fa"
LABEL_FG = "#343a40"
ENTRY_BG = "white"
READONLY_BG = "#e9ecef"
BUTTON_BG = "#28a745"
BUTTON_FG = "white"
LABEL_FONT = ("Segoe UI", 11)
TITLE_FONT = ("Segoe UI", 18, "bold")
INPUT_FONT = ("Segoe UI", 11)

style = ttk.Style()
try:
    style.theme_use("default")
except Exception:
    pass
style.configure("Form.TEntry", fieldbackground=ENTRY_BG, background=ENTRY_BG, padding=6, font=INPUT_FONT)
style.configure("ReadOnly.TEntry", fieldbackground=READONLY_BG, background=READONLY_BG, padding=6, font=INPUT_FONT)
style.configure("Form.TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG, padding=6, font=INPUT_FONT)
style.configure("ReadOnly.TCombobox", fieldbackground=READONLY_BG, background=READONLY_BG, padding=6, font=INPUT_FONT)

MOVEMENT_CHOICES = [
    ("Entrée normale (EN)", "EN"), ("Entrée retour (ER)", "ER"), ("Entrée initiale (EI)", "EI"),
    ("Entrée ajustement (EAJ)", "EAJ"), ("Entrée transfert (ET)", "ET"), ("Entrée autre unité (EAU)", "EAU"),
    ("Sortie normale (SN)", "SN"), ("Sortie perte (SP)", "SP"), ("Vente (SV)", "SV"),
    ("Sortie don (SD)", "SD"), ("Sortie consommation (SC)", "SC"), ("Sortie ajustement (SAJ)", "SAJ"),
    ("Sortie transfert (ST)", "ST"), ("Sortie autre unité (SAU)", "SAU"),
]

PRICING_CHOICES = [
    ("Fixe", "fixed"), ("Marge en %", "markup_percent"), ("Marge en montant", "markup_amount"), ("Dernier coût", "last_cost"),
]

def D(v):
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")

def quantize_money(x):
    return (D(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

# DB helpers
def fetch_all_item_codes():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT item_code FROM article_stock_local ORDER BY item_code;")
        rows = [r[0] for r in cur.fetchall() if r and r[0]]
        conn.close()
        return rows
    except Exception:
        return []

def fetch_item_by_code(code):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT item_designation, item_measurement_unit, item_cost_price, item_sale_price,
                   taux_tva, item_ct, item_tl, item_tsce_tax, item_ott_tax, item_quantity, pricing_strategy, markup_percent
            FROM article_stock_local WHERE item_code = ?
        """, (code,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        keys = ["designation","unit","cost","sale","tva","ct","tl","tsce","ott","quantity","pricing_strategy","markup_percent"]
        return dict(zip(keys, r))
    except Exception:
        return None

def get_first_contribuable_vat_flag():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, tp_name, vat_taxpayer FROM contribuable ORDER BY id LIMIT 1;")
        r = cur.fetchone()
        conn.close()
        if r:
            return {"id": r[0], "tp_name": r[1], "vat_taxpayer": bool(r[2])}
    except Exception:
        pass
    return None

# ---------- ArticleCard ----------
class ArticleCard(tk.Frame):
    def __init__(self, parent, idx, show_tax_fields=True, is_output_mode=False, remove_callback=None, *args, **kwargs):
        super().__init__(parent, bg="white", bd=1, relief="solid", padx=8, pady=8, *args, **kwargs)
        self.idx = idx
        self.remove_callback = remove_callback
        self.show_tax_fields = show_tax_fields
        self.is_output_mode = is_output_mode
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        tk.Label(self, text=f"Article #{self.idx+1}", font=("Segoe UI", 12, "bold"), bg="white", fg=LABEL_FG).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,8))

        self.vars = {}
        def mk(r, c, label, default="", readonly=False):
            lbl = tk.Label(self, text=label, anchor="w", bg="white", fg=LABEL_FG, font=LABEL_FONT)
            lbl.grid(row=r, column=c*2, sticky="w", padx=(0,6))
            v = tk.StringVar(value=default)
            style_name = "ReadOnly.TEntry" if readonly else "Form.TEntry"
            ent = ttk.Entry(self, textvariable=v, font=INPUT_FONT, width=30, style=style_name)
            ent.grid(row=r, column=c*2+1, sticky="ew", padx=(0,6), pady=(0,6))
            if readonly:
                try:
                    ent.state(["readonly"])
                except Exception:
                    ent.configure(state="readonly")
                    ent.configure(background=READONLY_BG)
            self.vars[label] = v
            return ent

        # Code article: entry + combo (combo larger, style like entries)
        self.code_entry = mk(1,0,"Code article")
        self.code_combo_var = tk.StringVar()
        self.code_combo = ttk.Combobox(self, textvariable=self.code_combo_var, values=[], width=30, state="readonly", style="Form.TCombobox")
        self.code_combo.grid(row=1, column=0*2+1, sticky="ew", padx=(0,6), pady=(0,6))
        self.code_combo.grid_remove()

        # no per-card refresh button (removed as requested)

        self.ent_design = mk(1,1,"Désignation")
        self.ent_qty = mk(2,0,"Quantité","0")
        self.ent_unit = mk(2,1,"Unité de mesure","unité")
        self.ent_cost = mk(3,0,"PA unitaire","0.00")

        # Prix de vente readonly
        tk.Label(self, text="Prix de vente unitaire (HT)", anchor="w", bg="white", fg=LABEL_FG, font=LABEL_FONT).grid(row=3, column=1*2, sticky="w", padx=(0,6))
        self.sale_var = tk.StringVar(value="0.00")
        self.ent_sale_readonly = ttk.Entry(self, textvariable=self.sale_var, font=INPUT_FONT, width=30, style="ReadOnly.TEntry")
        self.ent_sale_readonly.grid(row=3, column=1*2+1, sticky="ew", padx=(0,6), pady=(0,6))

        # Pricing strategy, markup
        tk.Label(self, text="Stratégie prix", anchor="w", bg="white", fg=LABEL_FG, font=LABEL_FONT).grid(row=4, column=0*2, sticky="w", padx=(0,6))
        self.pricing_strategy_var = tk.StringVar(value=PRICING_CHOICES[1][0])
        self.strat_cb = ttk.Combobox(self, textvariable=self.pricing_strategy_var, values=[d for d,_ in PRICING_CHOICES], state="readonly", width=30)
        self.strat_cb.grid(row=4, column=0*2+1, sticky="w", padx=(0,6), pady=(0,6))

        tk.Label(self, text="Valeur marge", anchor="w", bg="white", fg=LABEL_FG, font=LABEL_FONT).grid(row=4, column=1*2, sticky="w", padx=(0,6))
        self.markup_var = tk.StringVar(value="25.0")
        self.markup_entry = ttk.Entry(self, textvariable=self.markup_var, width=30, style="Form.TEntry")
        self.markup_entry.grid(row=4, column=1*2+1, sticky="ew", padx=(0,6), pady=(0,6))

        # compute sale readonly based on cost/strategy
        def compute_and_update_sale(*_):
            try:
                cost = D(self.ent_cost.get() or "0")
                strat_disp = self.pricing_strategy_var.get()
                strat_code = next((c for d,c in PRICING_CHOICES if d == strat_disp), "markup_percent")
                markup_value = D(self.markup_var.get() or "25.0")
                if strat_code == "fixed":
                    final = quantize_money(cost)
                elif strat_code == "markup_percent":
                    final = quantize_money(cost * (1 + (markup_value / D("100"))))
                elif strat_code == "markup_amount":
                    final = quantize_money(cost + markup_value)
                elif strat_code == "last_cost":
                    final = quantize_money(cost * (1 + D("0.25")))
                else:
                    final = quantize_money(cost * (1 + (markup_value / D("100"))))
            except Exception:
                final = D("0")
            self.sale_var.set(str(quantize_money(final)))

        self.ent_cost.bind("<KeyRelease>", compute_and_update_sale)
        self.pricing_strategy_var.trace_add("write", lambda *a: compute_and_update_sale())
        self.markup_var.trace_add("write", lambda *a: compute_and_update_sale())
        compute_and_update_sale()

        # Tax fields
        if self.show_tax_fields:
            self.ent_tva = mk(5,0,"TVA (%)","18")
            mk(5,1,"Taxe communale (CT)","0")
            mk(6,0,"Taxe licence (TL)","0")
            mk(6,1,"Taxe spécifique (TSCE)","0")
            mk(7,0,"Autres taxes (OTT)","0")
        else:
            self.vars["TVA (%)"] = tk.StringVar(value="0")
            self.vars["Taxe communale (CT)"] = tk.StringVar(value="0")
            self.vars["Taxe licence (TL)"] = tk.StringVar(value="0")
            self.vars["Taxe spécifique (TSCE)"] = tk.StringVar(value="0")
            self.vars["Autres taxes (OTT)"] = tk.StringVar(value="0")

        mk(7,1,"Référence facture fournisseur","")
        tk.Label(self, text="Description", anchor="w", bg="white", fg=LABEL_FG, font=LABEL_FONT).grid(row=8, column=0, sticky="w", padx=(0,6))
        desc = tk.StringVar()
        ttk.Entry(self, textvariable=desc, font=INPUT_FONT, width=10, style="Form.TEntry").grid(row=8, column=1, columnspan=3, sticky="ew", padx=(0,6))
        self.vars["Description du mouvement"] = desc

        btn_remove = tk.Button(self, text="Supprimer", bg="#dc3545", fg="white", command=self._on_remove)
        btn_remove.grid(row=0, column=3, sticky="e")

        # Combobox handler
        self.code_combo.bind("<<ComboboxSelected>>", lambda e: self._on_code_selected())

    def _set_entry_readonly_visual(self, entry_widget):
        try:
            entry_widget.configure(style="ReadOnly.TEntry")
            entry_widget.state(["readonly"])
        except Exception:
            try:
                entry_widget.configure(state="readonly", background=READONLY_BG)
            except Exception:
                pass

    def _set_entry_editable_visual(self, entry_widget):
        try:
            entry_widget.configure(style="Form.TEntry")
            try:
                entry_widget.state(["!readonly"])
            except Exception:
                entry_widget.configure(state="normal", background=ENTRY_BG)
        except Exception:
            pass

    def switch_to_combo_mode(self, codes):
        # show combo, hide entry, populate codes
        self.code_entry.grid_remove()
        self.code_combo['values'] = codes
        self.code_combo.set('')
        self.code_combo.configure(width=30, style="Form.TCombobox")
        self.code_combo.grid()

    def switch_to_entry_mode(self):
        # hide combo, show entry and restore editability
        self.code_combo.grid_remove()
        self.code_entry.grid()
        # restore editability of fields (design, unit, cost, markup, strat)
        for child in self.grid_slaves():
            try:
                if isinstance(child, ttk.Entry) or isinstance(child, tk.Entry):
                    # try to set to editable style
                    try:
                        child.configure(style="Form.TEntry")
                        child.state(["!readonly"])
                    except Exception:
                        try:
                            child.configure(state="normal", background=ENTRY_BG)
                        except Exception:
                            pass
            except Exception:
                pass
        # ensure strat and markup are editable
        try:
            self.strat_cb.configure(state="readonly")
        except Exception:
            pass
        try:
            self.markup_entry.configure(state="normal")
        except Exception:
            pass

    def _on_code_selected(self):
        code = self.code_combo_var.get()
        if not code:
            return
        row = fetch_item_by_code(code)
        if not row:
            return

        # fill values and mark readonly visually for relevant entries except Quantité and Réf facture
        mapping = {
            "Désignation": row.get("designation"),
            "Unité de mesure": row.get("unit"),
            "Coût unitaire (achat)": row.get("cost"),
            "TVA (%)": row.get("tva"),
            "Taxe communale (CT)": row.get("ct"),
            "Taxe licence (TL)": row.get("tl"),
            "Taxe spécifique (TSCE)": row.get("tsce"),
            "Autres taxes (OTT)": row.get("ott")
        }
        # set variables and readonly visuals
        for label, val in mapping.items():
            var = self.vars.get(label)
            if var is None:
                continue
            var.set("" if val is None else str(val))
            # find associated Entry and set readonly style
            for child in self.grid_slaves():
                try:
                    tv = child.cget("textvariable")
                    if tv and str(tv) == str(var):
                        self._set_entry_readonly_visual(child)
                except Exception:
                    pass

        # sale value
        sale_val = row.get("sale") if row.get("sale") is not None else row.get("cost")
        self.sale_var.set(str(quantize_money(sale_val or 0)))
        self._set_entry_readonly_visual(self.ent_sale_readonly)

        # pricing strategy + markup: set and lock
        ps = row.get("pricing_strategy")
        mp = row.get("markup_percent")
        if ps:
            disp = next((d for d,c in PRICING_CHOICES if c == ps), None)
            if disp:
                try:
                    self.strat_cb.set(disp)
                    self.strat_cb.configure(state="disabled")
                except Exception:
                    pass
        if mp is not None:
            try:
                self.markup_var.set(str(mp))
                self.markup_entry.configure(state="readonly", background=READONLY_BG)
            except Exception:
                pass

        # leave Quantité and Réf facture editable (they are entries in vars)

    def _on_remove(self):
        if self.remove_callback:
            self.remove_callback(self)

    def get_values(self):
        out = {k: v.get().strip() for k, v in self.vars.items()}
        if self.code_combo.winfo_ismapped():
            out["Code article"] = self.code_combo_var.get().strip()
        else:
            out["Code article"] = self.vars.get("Code article","").get().strip()
        out["final_sale_from_field"] = self.sale_var.get().strip()
        out["pricing_strategy_display"] = self.pricing_strategy_var.get()
        out["markup_value"] = self.markup_var.get().strip()
        return out

# ---------- formulaire_entree_et_declaration ----------
class formulaire_entree_et_declaration(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=FORM_BG)
        self.pack(fill="both", expand=True)
        self.cards = []
        self.first_contrib = get_first_contribuable_vat_flag()
        self.show_tax_fields = bool(self.first_contrib and self.first_contrib.get("vat_taxpayer", False))
        self._build()

    def _build(self):
        header = tk.Frame(self, bg=FORM_BG)
        header.pack(fill="x", padx=16, pady=(12,6))
        title_text = "📥 Saisie des entrées et sorties en stock"
        if self.first_contrib:
            title_text += f" — {self.first_contrib.get('tp_name')}"
        tk.Label(header, text=title_text, font=TITLE_FONT, bg=FORM_BG, fg=LABEL_FG).pack(side="left")

        ctrl = tk.Frame(self, bg=FORM_BG)
        ctrl.pack(fill="x", padx=16, pady=(6,6))
        tk.Label(ctrl, text="Type de mouvement:", bg=FORM_BG, fg=LABEL_FG, font=LABEL_FONT).pack(side="left", padx=(0,8))
        self.type_display_var = tk.StringVar(value=MOVEMENT_CHOICES[0][0])
        self.cmb_type = ttk.Combobox(ctrl, values=[d for d,_ in MOVEMENT_CHOICES], textvariable=self.type_display_var, width=40, state="readonly")
        self.cmb_type.pack(side="left", padx=(0,12))
        self.cmb_type.bind("<<ComboboxSelected>>", lambda e: self._on_type_changed())

        tk.Label(ctrl, text="Date:", bg=FORM_BG, fg=LABEL_FG, font=LABEL_FONT).pack(side="left")
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        date_entry = ttk.Entry(ctrl, textvariable=self.date_var, width=20, style="Form.TEntry")
        date_entry.pack(side="left", padx=(8,12))
        try:
            date_entry.state(["readonly"])
        except Exception:
            date_entry.configure(state="readonly")

        vat_text = "Assujetti à la TVA: Oui" if (self.first_contrib and self.first_contrib.get("vat_taxpayer")) else "Assujetti à la TVA: Non"
        self.vat_status_label = tk.Label(ctrl, text=vat_text, bg=FORM_BG, fg=LABEL_FG, font=LABEL_FONT)
        self.vat_status_label.pack(side="left", padx=(8,0))

        self.cards_container = tk.Frame(self, bg=FORM_BG)
        self.cards_container.pack(fill="both", expand=True, padx=16, pady=(8,0))

        footer = tk.Frame(self, bg=FORM_BG)
        footer.pack(fill="x", padx=16, pady=(8,12))
        self.btn_add = tk.Button(footer, text="Ajouter un article", bg="#007bff", fg="white", command=self.add_card)
        self.btn_add.pack(side="left")
        self.btn_refresh_all = tk.Button(footer, text="Rafraîchir codes", bg="#6c757d", fg="white", command=self.refresh_all_codes)
        self.btn_refresh_all.pack(side="left", padx=(8,0))
        self.btn_preview = tk.Button(footer, text="Aperçu", command=self.preview_all)
        self.btn_preview.pack(side="right", padx=(0,8))
        self.btn_save = tk.Button(footer, text="📥 Enregistrer & Déclarer", bg=BUTTON_BG, fg=BUTTON_FG, command=self._on_save_clicked)
        self.btn_save.pack(side="right", padx=(0,8))

        self.add_card()
        self._on_type_changed()

    def add_card(self):
        idx = len(self.cards)
        is_output_mode = self._current_type_is_output()
        card = ArticleCard(self.cards_container, idx, show_tax_fields=self.show_tax_fields, is_output_mode=is_output_mode, remove_callback=self.remove_card)
        card.pack(fill="x", pady=8, padx=8)
        if is_output_mode:
            codes = fetch_all_item_codes()
            card.switch_to_combo_mode(codes)
        else:
            card.switch_to_entry_mode()
        self.cards.append(card)
        self._refresh_titles()

    def remove_card(self, card):
        card.destroy()
        self.cards.remove(card)
        self._refresh_titles()

    def _refresh_titles(self):
        for i, c in enumerate(self.cards):
            c.idx = i
            try:
                lbl = c.grid_slaves(row=0, column=0)[0]
                lbl.config(text=f"Article #{i+1}")
            except Exception:
                pass

    def _current_type_is_output(self):
        display = self.type_display_var.get()
        return display.startswith("Sortie") or "(SV)" in display or display.startswith("Vente")

    def _on_type_changed(self):
        is_output = self._current_type_is_output()
        codes = fetch_all_item_codes() if is_output else []
        for card in self.cards:
            if is_output:
                card.switch_to_combo_mode(codes)
            else:
                # when returning to entry mode restore editable visuals and clear selection
                card.switch_to_entry_mode()
                # clear readonly visuals and values that may have been filled by selection (except user-filled ones)
                for label, var in card.vars.items():
                    if label in ("Désignation","Unité de mesure","Coût unitaire (achat)","TVA (%)","Taxe communale (CT)","Taxe licence (TL)","Taxe spécifique (TSCE)","Autres taxes (OTT)"):
                        var.set("")  # clear prefilled readonly fields
                card.sale_var.set("0.00")
                # unlock strat and markup
                try:
                    card.strat_cb.configure(state="readonly")
                except Exception:
                    pass
                try:
                    card.markup_entry.configure(state="normal")
                    card.markup_entry.configure(background=ENTRY_BG)
                except Exception:
                    pass

    def refresh_all_codes(self):
        codes = fetch_all_item_codes()
        for card in self.cards:
            if card.code_combo.winfo_ismapped():
                card.code_combo['values'] = codes
        messagebox.showinfo("Rafraîchir codes", "Liste des codes mise à jour.")

    def preview_all(self):
        if not self.cards:
            messagebox.showinfo("Aperçu", "Aucun article")
            return
        txt = ""
        for i, c in enumerate(self.cards, start=1):
            txt += f"--- Article {i} ---\n"
            for k, v in c.get_values().items():
                txt += f"{k}: {v}\n"
        display = self.type_display_var.get()
        code = next((code for disp, code in MOVEMENT_CHOICES if disp == display), "EN")
        txt += f"\nType mouvement (affiché): {display}\nType envoyé: {code}\nDate: {self.date_var.get()}\nVAT: {self.vat_status_label.cget('text')}"
        messagebox.showinfo("Aperçu", txt)

    def _show_preloader(self, message="Vérification connexion..."):
        self._preloader = tk.Toplevel(self)
        self._preloader.transient(self.winfo_toplevel())
        self._preloader.grab_set()
        self._preloader.title("")
        self._preloader.geometry("360x90")
        try:
            self._preloader.resizable(False, False)
        except Exception:
            pass
        lbl = tk.Label(self._preloader, text=message, font=LABEL_FONT)
        lbl.pack(expand=True, fill="both", padx=12, pady=12)

    def _hide_preloader(self):
        try:
            if hasattr(self, "_preloader") and self._preloader:
                self._preloader.grab_release()
                self._preloader.destroy()
                self._preloader = None
        except Exception:
            pass

    def _on_save_clicked(self):
        entries = [c.get_values() for c in self.cards]
        for i, data in enumerate(entries, start=1):
            if not data.get("Code article") or not data.get("Désignation") or not data.get("Quantité"):
                messagebox.showwarning("Champ manquant", f"Article #{i} : remplir Code, Désignation et Quantité")
                return

        # stock sufficiency check for outputs
        display = self.type_display_var.get()
        type_code = next((code for disp, code in MOVEMENT_CHOICES if disp == display), "EN")
        if type_code.startswith("S"):
            for i, data in enumerate(entries, start=1):
                code = data.get("Code article")
                qty = D(data.get("Quantité") or "0")
                row = fetch_item_by_code(code)
                available = D(row.get("quantity") or "0") if row else D("0")
                if qty > available:
                    messagebox.showerror("Stock insuffisant", f"Article #{i} ({code}) : quantité demandée {qty} supérieure au stock disponible {available}.")
                    return

        # preloader + token check
        self._show_preloader("Vérification de la connexion OBR...")
        def check_and_proceed():
            try:
                token = obtenir_token_auto()
                if not token:
                    raise RuntimeError("Impossible d'obtenir le token OBR.")
                self.after(0, self._hide_preloader)
                self.after(0, lambda: self.save_all())
            except Exception as e:
                self.after(0, self._hide_preloader)
                self.after(0, lambda: messagebox.showerror("Erreur connexion", str(e)))
        threading.Thread(target=check_and_proceed, daemon=True).start()

    def save_all(self):
        self.btn_save.config(state="disabled")
        self.btn_add.config(state="disabled")
        self.btn_refresh_all.config(state="disabled")

        entries = [c.get_values() for c in self.cards]
        display = self.type_display_var.get()
        type_code = next((code for disp, code in MOVEMENT_CHOICES if disp == display), "EN")
        date_now = self.date_var.get().strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def worker():
            try:
                conn = get_connection()
                cur = conn.cursor()
                mouvements_local = []
                for data in entries:
                    qty = D(data.get("Quantité") or "0")
                    cost_price = D(data.get("Coût unitaire (achat)") or "0")
                    final_sale = D(data.get("final_sale_from_field") or data.get("final_sale_from_field") or data.get("Prix de vente unitaire (HT)") or "0")
                    vat_rate = D(data.get("TVA (%)") or "0") if self.show_tax_fields else D("0")
                    ct = D(data.get("Taxe communale (CT)") or "0") if self.show_tax_fields else D("0")
                    tl = D(data.get("Taxe licence (TL)") or "0") if self.show_tax_fields else D("0")
                    tsce = D(data.get("Taxe spécifique (TSCE)") or "0") if self.show_tax_fields else D("0")
                    ott = D(data.get("Autres taxes (OTT)") or "0") if self.show_tax_fields else D("0")

                    code = data.get("Code article")
                    designation = data.get("Désignation")
                    unit = data.get("Unité de mesure") or "unité"
                    ref_fact = data.get("Référence facture fournisseur") or ""
                    desc = data.get("Description du mouvement") or ""

                    strat_disp = data.get("pricing_strategy_display") or PRICING_CHOICES[1][0]
                    strat_code = next((c for d,c in PRICING_CHOICES if d == strat_disp), "markup_percent")
                    markup_value = D(data.get("markup_value") or "25.0")

                    cur.execute("SELECT id, item_quantity, item_cost_price, item_sale_price FROM article_stock_local WHERE item_code = ?", (code,))
                    row = cur.fetchone()
                    is_output = type_code.startswith("S")
                    qty_effect = -qty if is_output else qty

                    if row:
                        stock_id, old_qty, old_cost, old_sale = row[0], D(row[1] or "0"), D(row[2] or "0"), D(row[3] or "0")
                        new_qty = old_qty + qty_effect
                        if qty_effect > 0:
                            total_qty_for_cost = old_qty + qty_effect
                            new_cost = ((old_qty * old_cost) + (qty_effect * cost_price)) / total_qty_for_cost if total_qty_for_cost > 0 else cost_price
                        else:
                            new_cost = old_cost
                        cur.execute("""
                            UPDATE article_stock_local
                            SET item_quantity = ?, item_cost_price = ?, item_sale_price = ?, pricing_strategy = ?, markup_percent = ?, taux_tva = ?, item_ct = ?, item_tl = ?, item_tsce_tax = ?, item_ott_tax = ?, last_purchase_date = ?, is_manuel = 0
                            WHERE id = ?
                        """, (float(new_qty), float(new_cost), float(final_sale), strat_code, float(markup_value), float(vat_rate), float(ct), float(tl), float(tsce), float(ott), date_now, stock_id))
                    else:
                        initial_qty = qty_effect
                        cur.execute("""
                            INSERT INTO article_stock_local(
                                item_code, item_designation, item_quantity, item_measurement_unit,
                                item_cost_price, item_sale_price, pricing_strategy, markup_percent,
                                taux_tva, item_ct, item_tl, item_tsce_tax, item_ott_tax, last_purchase_date, is_manuel
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (code, designation, float(initial_qty), unit, float(cost_price), float(final_sale), strat_code, float(markup_value),
                              float(vat_rate), float(ct), float(tl), float(tsce), float(ott), date_now, 0))
                        stock_id = cur.lastrowid

                    payload_for_local = {
                        "system_or_device_id": get_system_id(),
                        "item_code": code,
                        "item_designation": designation,
                        "item_quantity": str(qty),
                        "item_measurement_unit": unit,
                        "item_cost_price": str(cost_price),
                        "item_price": str(final_sale),
                        "item_purchase_or_sale_price": str(cost_price),
                        "item_purchase_or_sale_currency": "BIF",
                        "item_movement_type": type_code,
                        "item_movement_invoice_ref": ref_fact,
                        "item_movement_description": desc,
                        "item_movement_date": date_now
                    }
                    cur.execute("""
                        INSERT INTO mouvement_stock (
                            contribuable_id, system_or_device_id, item_code, item_designation, item_quantity,
                            item_measurement_unit, item_purchase_or_sale_price, item_purchase_or_sale_currency,
                            item_movement_type, item_movement_date, item_movement_invoice_ref, item_movement_description,
                            article_stock_id, obr_status, source_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (self.first_contrib["id"] if self.first_contrib else None, get_system_id(), code, designation, float(qty),
                          unit, float(cost_price), "BIF", type_code, date_now, ref_fact, desc,
                          stock_id, 0, json.dumps(payload_for_local, ensure_ascii=False), date_now))
                    mouvement_id = cur.lastrowid
                    conn.commit()
                    mouvements_local.append((mouvement_id, payload_for_local))

                conn.close()

                token = obtenir_token_auto()
                if not token:
                    raise RuntimeError("Token OBR introuvable. Mouvements enregistrés localement pour retry.")

                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                for mouvement_id, mo_payload in mouvements_local:
                    status = 0
                    attempt = 0
                    max_attempts = 3
                    backoff = 0.5
                    mv_json = {}
                    while attempt < max_attempts:
                        attempt += 1
                        try:
                            resp_mv = requests.post(
                                "https://ebms.obr.gov.bi:9443/ebms_api/AddStockMovement/",
                                json=mo_payload,
                                headers=headers,
                                timeout=30,
                                verify=True
                            )
                            try:
                                mv_json = resp_mv.json()
                            except Exception:
                                mv_json = {"raw": resp_mv.text}
                            if resp_mv.status_code == 200 and (mv_json.get("success") or mv_json.get("status") in (1, "1") or mv_json.get("code") == 0):
                                status = 1
                                break
                            if resp_mv.status_code == 400:
                                status = 0
                                break
                        except Exception:
                            mv_json = {"error": "network"}
                        if attempt < max_attempts:
                            time.sleep(backoff)
                            backoff *= 2

                    conn2 = get_connection()
                    try:
                        cur2 = conn2.cursor()
                        cur2.execute("UPDATE mouvement_stock SET obr_status = ?, source_json = ? WHERE id = ?", (status, json.dumps(mv_json, ensure_ascii=False), mouvement_id))
                        conn2.commit()
                    except Exception:
                        try:
                            conn2.rollback()
                        except:
                            pass
                    finally:
                        conn2.close()

                # reset UI
                self.after(0, lambda: messagebox.showinfo("Terminé", "Toutes les lignes traitées et tentatives OBR effectuées."))
                self.after(0, self._reset_forms)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Erreur", str(e)))
            finally:
                self.after(0, lambda: self.btn_save.config(state="normal"))
                self.after(0, lambda: self.btn_add.config(state="normal"))
                self.after(0, lambda: self.btn_refresh_all.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_forms(self):
        for c in list(self.cards):
            try:
                c.destroy()
            except Exception:
                pass
        self.cards = []
        self.add_card()
        self.first_contrib = get_first_contribuable_vat_flag()
        self.show_tax_fields = bool(self.first_contrib and self.first_contrib.get("vat_taxpayer", False))
        vat_text = "Assujetti à la TVA: Oui" if (self.first_contrib and self.first_contrib.get("vat_taxpayer")) else "Assujetti à la TVA: Non"
        try:
            self.vat_status_label.config(text=vat_text)
        except Exception:
            pass
        self._on_type_changed()

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Gestion du stock (FR) - Final V2")
    root.geometry("1280x820")
    app = formulaire_entree_et_declaration(root)
    root.mainloop()
