# controllers/app_controller.py
import tkinter as tk
import importlib
import os
import sys

def _data_path(*parts):
    """
    Retourne un chemin absolu vers une ressource incluse dans le bundle PyInstaller
    (sys._MEIPASS) ou vers le fichier dans l'arborescence source en développement.
    Usage: _data_path('assets', 'app.ico')
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, *parts)
    here = os.path.dirname(os.path.abspath(__file__))
    # __file__ est src/controllers/app_controller.py -> remonte d'un niveau pour atteindre src/
    return os.path.normpath(os.path.join(here, "..", *parts))

class AppController(tk.Tk):
    """
    Contrôleur central : une seule instance Tk.
    - show_view(view_key, **kwargs) : affiche une vue (frame) réutilisable.
    - destroy_view(view_key) : détruit la vue et la retire du cache.
    """

    def __init__(self):
        super().__init__()
        # titre général (peut être remplacé par la vue)
        self.title("Mon Application")

        # charger icône si disponible (utilise helper _data_path pour bundle/dev)
        try:
            ico_path = _data_path('assets', 'app.ico')
            if os.path.exists(ico_path):
                try:
                    self.iconbitmap(ico_path)
                except Exception:
                    # fallback: sur certaines plateformes, iconbitmap peut planter pour des formats inattendus
                    try:
                        self.iconphoto(False, tk.PhotoImage(file=ico_path))
                    except Exception:
                        pass
        except Exception:
            pass

        # taille et positionnement : centrée par défaut (ajustable par les vues)
        default_w, default_h = 1280, 800
        self.geometry(f"{default_w}x{default_h}")
        self._center_window(default_w, default_h)
        self.resizable(True, True)

        # conteneur pour les vues
        self.container = tk.Frame(self)
        self.container.pack(fill="both", expand=True)

        # cache des instances
        self._views = {}

        # mapping pour import paresseux
        # Note: garde cohérence avec packaging (si src est package, utilises "src.views.*")
        # L'import resolver essaiera d'abord la variante avec "src." puis sans.
        self._mapping = {
            "LicenseView": ("views.license_view", "LicenseView"),
            "LoginView": ("views.login_view", "LoginView"),
            "MainView": ("views.main_view", "MainView"),
        }

        # démarrer sur la vue login
        try:
            self.show_view("LoginView")
        except Exception:
            # en cas d'erreur lors du premier affichage, log minimal et continuer
            try:
                import logging
                logging.getLogger("hankstoremanager").exception("Échec lors de l'affichage initial de LoginView")
            except Exception:
                pass

    def _center_window(self, w, h):
        try:
            self.update_idletasks()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _import_view_class(self, view_key):
        """
        Importe la classe de vue de façon résiliente :
        - tente d'abord "src.<module>" si possible,
        - puis le module tel quel (ex: "views.foo"),
        - lève ImportError clair si aucun import n'a marché.
        """
        if view_key not in self._mapping:
            raise ValueError(f"Vue inconnue: {view_key}")
        module_name, class_name = self._mapping[view_key]

        candidates = []
        # si le module n'est pas déjà préfixé, tester la variante package-aware
        if not module_name.startswith("src."):
            candidates = [f"src.{module_name}", module_name]
        else:
            candidates = [module_name]

        last_exc = None
        for mod_name in candidates:
            try:
                module = importlib.import_module(mod_name)
                cls = getattr(module, class_name, None)
                if cls is None:
                    raise ImportError(f"Module {mod_name} importé mais n'expose pas {class_name}")
                return cls
            except Exception as e:
                last_exc = e
                continue

        # si pas de succès, remonter l'erreur initiale pour faciliter le debug
        raise ImportError(f"Impossible d'importer {class_name} depuis {module_name}") from last_exc

    def show_view(self, view_key, **kwargs):
        """
        Affiche une vue unique. Toutes les autres vues existantes sont détruites.
        kwargs sont passés au constructeur de la vue (ex : on_logout).
        """
        # Détruire toutes les vues existantes pour garantir qu'une seule soit visible
        for key, inst in list(self._views.items()):
            try:
                inst.pack_forget()
                inst.destroy()
            except Exception:
                pass
            self._views.pop(key, None)

        ViewClass = self._import_view_class(view_key)
        # Construire l'instance en passant controller=self
        instance = ViewClass(self.container, controller=self, **kwargs)
        self._views[view_key] = instance
        instance.pack(fill="both", expand=True)

    def destroy_view(self, view_key):
        """
        Détruit et retire du cache la vue identifiée par view_key.
        """
        inst = self._views.get(view_key)
        if inst:
            try:
                inst.pack_forget()
                inst.destroy()
            except Exception:
                pass
            self._views.pop(view_key, None)
