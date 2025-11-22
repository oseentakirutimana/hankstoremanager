import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from api.obr_client import checkTIN
from database.connection import get_connection

def afficher_formulaire_contribuable(parent):
    for widget in parent.winfo_children():
        widget.destroy()

    wrapper = tk.Frame(parent, bg="white")
    wrapper.pack(fill="both", expand=True, padx=20, pady=20)

    form_frame = tk.Frame(wrapper, bg="#f8f9fa", bd=1, relief="solid")
    form_frame.pack(pady=20, padx=50)

    tk.Label(form_frame, text="➕ Ajouter un contribuable", font=("Segoe UI", 18), bg="#f8f9fa", fg="#343a40").pack(pady=(10, 20))

    champs = {
        "Nom complet": "tp_name",
        "NIF": "tp_TIN",
        "Numéro de registre": "tp_trade_number",
        "Numéro postal": "tp_postal_number",
        "Téléphone": "tp_phone_number",
        "Province": "tp_address_province",
        "Commune": "tp_address_commune",
        "Quartier": "tp_address_quartier",
        "Avenue": "tp_address_avenue",
        "Rue": "tp_address_rue",
        "Numéro de porte": "tp_address_number",
        "Centre fiscal": "tp_fiscal_center",
        "Forme juridique": "tp_legal_form",
        "Secteur d'activité": "tp_activity_sector"
    }

    entrees = {}
    for label_text, key in champs.items():
        tk.Label(form_frame, text=label_text, font=("Segoe UI", 11), bg="#f8f9fa", fg="#343a40").pack(anchor="w", padx=20)
        if key == "tp_fiscal_center":
            combo = ttk.Combobox(form_frame, font=("Segoe UI", 11), width=37, state="readonly")
            combo["values"] = ["DGC", "DMC", "DPMC"]
            combo.current(0)
            combo.pack(pady=5, padx=20)
            entrees[key] = combo
        else:
            entry = tk.Entry(form_frame, font=("Segoe UI", 11), width=40, bg="white", bd=1, relief="solid")
            entry.pack(pady=5, padx=20)
            entrees[key] = entry

    # Type
    tk.Label(form_frame, text="Type de contribuable :", font=("Segoe UI", 11), bg="#f8f9fa", fg="#343a40").pack(anchor="w", padx=20, pady=(10, 5))
    tp_type_var = tk.StringVar(value="1")
    type_frame = tk.Frame(form_frame, bg="#f8f9fa")
    type_frame.pack(padx=20, pady=(0, 10), anchor="w")
    tk.Radiobutton(type_frame, text="Personne physique", variable=tp_type_var, value="1", bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=10)
    tk.Radiobutton(type_frame, text="Personne morale", variable=tp_type_var, value="2", bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(side="left", padx=10)

    # Regimes
    regime_frame = tk.Frame(form_frame, bg="#f8f9fa")
    regime_frame.pack(pady=15, padx=20, fill="x")
    tk.Label(regime_frame, text="Régimes fiscaux :", font=("Segoe UI", 11), bg="#f8f9fa", fg="#343a40").pack(anchor="w")
    var_vat = tk.BooleanVar()
    var_ct = tk.BooleanVar()
    var_tl = tk.BooleanVar()
    for text, var in [("Assujetti à la TVA", var_vat), ("Assujetti à la taxe de consommation", var_ct), ("Assujetti au prélèvement forfaitaire libératoire", var_tl)]:
        tk.Checkbutton(regime_frame, text=text, variable=var, bg="#f8f9fa", fg="#343a40", selectcolor="#f8f9fa").pack(anchor="w", pady=2)

    # Loader label (initialement caché)
    loader_frame = tk.Frame(form_frame, bg="#f8f9fa")
    loader_frame.pack(fill="x", padx=20)
    loader_label = tk.Label(loader_frame, text="", font=("Segoe UI", 10), bg="#f8f9fa", fg="#343a40")
    loader_label.pack(anchor="w")

    # Bouton en bas
    btn_frame = tk.Frame(form_frame, bg="#f8f9fa")
    btn_frame.pack(pady=(10, 20))
    submit_btn = tk.Button(btn_frame, text="💾 Enregistrer", font=("Segoe UI", 11), bg="#28a745", fg="white", activebackground="#34c759", width=20)
    submit_btn.pack()

    # Animation loader: points animés
    def start_loader(text="Vérification OBR"):
        stop_event.clear()
        def animate():
            dots = 0
            while not stop_event.is_set():
                display = f"{text}{'.' * dots}"
                # mettre à jour UI via after
                loader_label.after(0, loader_label.config, {"text": display})
                dots = (dots + 1) % 4
                time.sleep(0.5)
            # nettoyer affichage
            loader_label.after(0, loader_label.config, {"text": ""})
        t = threading.Thread(target=animate, daemon=True)
        t.start()

    def stop_loader():
        stop_event.set()

    stop_event = threading.Event()

    # Fonction d'enregistrement qui lance checkTIN de façon asynchrone
    def enregistrer_contribuable_async():
        # Récupérer et normaliser les valeurs
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

        # Désactiver le bouton et démarrer loader
        submit_btn.config(state="disabled")
        start_loader("Vérification OBR")

        # Thread worker pour appel réseau + insertion DB
        def worker():
            try:
                tin_clean = data["tp_TIN"].replace(" ", "")
                tin_resp = checkTIN(tin_clean)
            except Exception as e:
                # Erreur réseau / timeouts
                def on_error():
                    stop_loader()
                    submit_btn.config(state="normal")
                    messagebox.showerror("Erreur API", f"Échec de la vérification OBR : {e}")
                loader_label.after(0, on_error)
                return

            # Traiter la réponse OBR dans le thread UI via after
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
