# utils/resources.py
import os, sys

def resource_path(rel_path: str) -> str:
    """
    Renvoie le chemin absolu vers une ressource.
    Fonctionne en dev et quand l'app est packagée par PyInstaller.
    Usage: resource_path('assets/logo.png') ou resource_path('facturation_obr.db')
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base, rel_path)
