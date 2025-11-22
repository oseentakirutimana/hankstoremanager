import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from api.obr_client import checkTIN
from database.connection import get_connectio

def afficher_formulaire_contribuable(parent):
    for widget in parent.winfo_children():
        widget.destroy()

    wrapper = tk.Frame(parent, bg="white")
    wrapper.pack(fill="both", expand=True, padx=20, pady=20)

    # Conteneur central avec marge interne et fond clair
    form_frame = tk.Frame(wrapper, bg="#f8f9fa", bd=1, relief="solid")
    form_frame.pack(fill="both", expand=True, padx=24, pady=12)

    tk.Label(form_frame, text="➕  Ajouter un contribuable", font=("Segoe UI", 18), bg="#f8f9fa", fg="#343a40").pack(pady=(12, 18))

    # Grid container pour les champs (3 colonnes)
    grid = tk.Frame(form_frame, bg="#f8f9fa")
    grid.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    # Définir 3 colonnes qui se partagent la largeur disponible
    for i in range(3):
        grid.columnconfigure(i, weight=1, uniform="col")

    champs = [
        ("Nom complet", "tp_name"),
        ("NIF", "tp_TIN"),
        ("Numéro de registre", "tp_trade_number"),
        ("Numéro postal", "tp_postal_number"),
        ("Téléphone", "tp_phone_number"),
        ("Province", "tp_address_province"),
        ("Commune", "tp_address_commune"),
        ("Quartier", "tp_address_quartier"),
        ("Avenue", "tp_address_avenue"),
        ("Rue", "tp_address_rue"),
        ("Numéro de porte", "tp_address_number"),
        ("Centre fiscal", "tp_fiscal_center"),
        ("Forme juridique", "tp_legal_form"),
        ("Secteur d'activité", "tp_activity_sector"),
    ]

    entrees = {}
    # placer les champs 3 par ligne
    row = 0
    col = 0
    for label_text, key in champs:
        # Label
        lbl = tk.Label(grid, text=label_text, font=("Segoe UI", 11), bg="#f8f9fa", fg="#343a40")
        lbl.grid(row=row*2, column=col, sticky="w", padx=(8, 12), pady=(6, 2))

        # Widget (combobox pour centre fiscal, sinon Entry) — utiliser sticky="ew" pour s'étirer
        if key == "tp_fiscal_center":
            combo = ttk.Combobox(grid, font=("Segoe UI", 11), state="readonly")
            combo["values"] = ["DGC", "DMC", "DPMC"]
            combo.current(0)
            combo.grid(row=row*2 + 1, column=col, sticky="ew", padx=(8, 12), pady=(0, 8))
            entrees[key] = combo
        else:
            entry = tk.Entry(grid, font=("Segoe UI", 11), bg="white", bd=1, relief="solid")
            entry.grid(row=row*2 + 1, column=col, sticky="ew", padx=(8, 12), pady=(0, 8))
            entrees[key] = entry

        col += 1
        if col >= 3:
            col = 0
            row += 1

    # Si la dernière ligne n'est pas complète, on ajoute des colonnes vides pour l'alignement (optionnel)
    # (inutile si grid.columnconfigure avec weights est en place)

    # Type de contribuable (étiré sur toute la largeur)
    type_frame = tk.Frame(form_frame, bg="#f8f9fa")
    type_frame.pack(fill="x", padx=12, pady=(6, 6))
    tk.Label(type_frame, text="Type de contribuable :", font=("Segoe UI", 11), bg="#f8f9fa", fg="#343a40").pack(anchor="w")
    tp_type_var = tk.StringVar(value="1")
    rbf = tk.Frame(type_frame, bg="#f8f9fa")
    rbf.pack(anchor="w", pady=(6, 0))
    tk.Radiobutton(rbf, text="Personne physique", variable=tp_type_var, value="1", bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=8)
    tk.Radiobutton(rbf, text="Personne morale", variable=tp_type_var, value="2", bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=8)

    # Régimes fiscaux (étiré)
    regime_frame = tk.Frame(form_frame, bg="#f8f9fa")
    regime_frame.pack(fill="x", padx=12, pady=(6, 12))
    tk.Label(regime_frame, text="Régimes fiscaux :", font=("Segoe UI", 11), bg="#f8f9fa", fg="#343a40").pack(anchor="w")
    var_vat = tk.BooleanVar()
    var_ct = tk.BooleanVar()
    var_tl = tk.BooleanVar()
    chkf = tk.Frame(regime_frame, bg="#f8f9fa")
    chkf.pack(anchor="w", pady=(6,0))
    tk.Checkbutton(chkf, text="Assujetti à la TVA", variable=var_vat, bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=8)
    tk.Checkbutton(chkf, text="Assujetti à la taxe de consommation", variable=var_ct, bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=8)
    tk.Checkbutton(chkf, text="Assujetti au prélèvement forfaitaire libératoire", variable=var_tl, bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=8)

    # Loader label (initialement caché)
    loader_frame = tk.Frame(form_frame, bg="#f8f9fa")
    loader_frame.pack(fill="x", padx=12)
    loader_label = tk.Label(loader_frame, text="", font=("Segoe UI", 10), bg="#f8f9fa", fg="#343a40")
    loader_label.pack(anchor="w")

    # Boutons
    btn_frame = tk.Frame(form_frame, bg="#f8f9fa")
    btn_frame.pack(fill="x", padx=12, pady=(10, 12))
    submit_btn = tk.Button(btn_frame, text="💾 Enregistrer", font=("Segoe UI", 11), bg="#28a745", fg="white", activebackground="#34c759")
    submit_btn.pack(side="right")

    # Animation loader: points animés
    def start_loader(text="Vérification OBR"):
        stop_event.clear()
        def animate():
            dots = 0
            while not stop_event.is_set():
                display = f"{text}{'.' * dots}"
                loader_label.after(0, loader_label.config, {"text": display})
                dots = (dots + 1) % 4
                time.sleep(0.5)
            loader_label.after(0, loader_label.config, {"text": ""})
        t = threading.Thread(target=animate, daemon=True)
        t.start()

    def stop_loader():
        stop_event.set()

    stop_event = threading.Event()

    # Fonction d'enregistrement (identique à la tienne, adaptée au nouvel agencement)
    def enregistrer_contribuable_async():
        data = {}
        for key, widget in entrees.items():
            try:
                val = widget.get()
            except Exception:
                val = ""
            data[key] = val.strip() if isinstance(val, str) else val

        data.update({
            "tp_type": tp_type_var.get(),
            "vat_taxpayer": 1 if var_vat.get() else 0,
            "ct_taxpayer": 1 if var_ct.get() else 0,
            "tl_taxpayer": 1 if var_tl.get() else 0
        })

        if not data.get("tp_name") or not data.get("tp_TIN"):
            messagebox.showerror("Erreur", "Nom et NIF sont obligatoires.")
            return

        submit_btn.config(state="disabled")
        start_loader("Vérification OBR")

        def worker():
            try:
                tin_clean = data["tp_TIN"].replace(" ", "")
                tin_resp = checkTIN(tin_clean)
            except Exception as e:
                def on_error():
                    stop_loader()
                    submit_btn.config(state="normal")
                    messagebox.showerror("Erreur API", f"Échec de la vérification OBR : {e}")
                loader_label.after(0, on_error)
                return

            def on_result():
                stop_loader()
                submit_btn.config(state="normal")

                if not isinstance(tin_resp, dict) or not tin_resp.get("valid", False):
                    msg_obr = tin_resp.get("message", "NIF invalide ou réponse inattendue") if isinstance(tin_resp, dict) else "Réponse OBR inattendue"
                    messagebox.showerror("NIF invalide", msg_obr)
                    return

                tp_name_from_obr = tin_resp.get("tp_name")
                if tp_name_from_obr:
                    w = entrees.get("tp_name")
                    if hasattr(w, "delete"):
                        w.delete(0, tk.END)
                        w.insert(0, tp_name_from_obr)
                    else:
                        try:
                            w.set(tp_name_from_obr)
                        except Exception:
                            pass
                    messagebox.showinfo("Contribuable reconnu", f"NIF valide : {tp_name_from_obr}")

                # Enregistrement en base
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO contribuable (
                            tp_name, tp_TIN, tp_trade_number, tp_postal_number, tp_phone_number,
                            tp_address_province, tp_address_commune, tp_address_quartier,
                            tp_address_avenue, tp_address_rue, tp_address_number, tp_fiscal_center,
                            tp_legal_form, tp_activity_sector, tp_type, vat_taxpayer, ct_taxpayer, tl_taxpayer
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data.get("tp_name"),
                        data.get("tp_TIN"),
                        data.get("tp_trade_number"),
                        data.get("tp_postal_number"),
                        data.get("tp_phone_number"),
                        data.get("tp_address_province"),
                        data.get("tp_address_commune"),
                        data.get("tp_address_quartier"),
                        data.get("tp_address_avenue"),
                        data.get("tp_address_rue"),
                        data.get("tp_address_number"),
                        data.get("tp_fiscal_center"),
                        data.get("tp_legal_form"),
                        data.get("tp_activity_sector"),
                        data.get("tp_type"),
                        data.get("vat_taxpayer"),
                        data.get("ct_taxpayer"),
                        data.get("tl_taxpayer")
                    ))
                    conn.commit()
                    messagebox.showinfo("Succès", "Contribuable ajouté ✅")
                except Exception as err:
                    print("DB error:", err)
                    if conn:
                        conn.rollback()
                    if "UNIQUE constraint failed" in str(err):
                        messagebox.showerror("Erreur", "Un contribuable avec ce NIF existe déjà.")
                    else:
                        messagebox.showerror("Erreur", f"Échec d'enregistrement : {err}")
                finally:
                    if conn:
                        conn.close()

                # Reset UI fields
                for key, widget in entrees.items():
                    if isinstance(widget, ttk.Combobox):
                        try:
                            widget.current(0)
                        except Exception:
                            widget.set("")
                    else:
                        try:
                            widget.delete(0, tk.END)
                        except Exception:
                            pass
                tp_type_var.set("1")
                var_vat.set(False)
                var_ct.set(False)
                var_tl.set(False)

            loader_label.after(0, on_result)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    submit_btn.config(command=enregistrer_contribuable_async)
    parent.update_idletasks()
