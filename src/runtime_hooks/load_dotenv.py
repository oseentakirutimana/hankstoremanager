# src/runtime_hooks/load_dotenv.py
"""
PyInstaller runtime hook to load environment variables from a .env file early at app startup.

Behaviour:
- cherche dans cet ordre : fichier .env à la racine du bundle/current workdir,
  puis .env dans le dossier contenant l'exécutable, puis .env.example.
- n'écrase pas les variables d'environnement déjà présentes (override=False).
- silencieux par défaut ; en debug (ENV_LOAD_DOTENV=1) affiche les chemins essayés.
"""
from __future__ import annotations
import os
import sys

def _debug(msg: str) -> None:
    if os.environ.get("ENV_LOAD_DOTENV") == "1":
        try:
            # éviter d'exposer les valeurs, juste informer des actions
            sys.stderr.write(f"[load_dotenv hook] {msg}\n")
        except Exception:
            pass

def _possible_paths() -> list[str]:
    paths: list[str] = []

    # 1) working directory where process was started
    try:
        paths.append(os.path.join(os.getcwd(), ".env"))
        paths.append(os.path.join(os.getcwd(), ".env.example"))
    except Exception:
        pass

    # 2) directory of the frozen executable (PyInstaller _MEIPASS or exe dir)
    try:
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            base = os.path.dirname(sys.executable) or os.path.dirname(__file__)
        paths.append(os.path.join(base, ".env"))
        paths.append(os.path.join(base, ".env.example"))
    except Exception:
        pass

    # 3) repo layout when running from source: project root (one level up from src/)
    try:
        here = os.path.dirname(__file__)
        repo_root = os.path.abspath(os.path.join(here, "..", ".."))  # src/runtime_hooks -> repo root
        paths.append(os.path.join(repo_root, ".env"))
        paths.append(os.path.join(repo_root, ".env.example"))
    except Exception:
        pass

    # deduplicate while preserving order
    seen = set()
    ordered = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered

def _load_dotenv():
    try:
        from dotenv import load_dotenv
    except Exception:
        _debug("python-dotenv not installed; skipping .env load")
        return

    for path in _possible_paths():
        if os.path.isfile(path):
            # load without overriding existing env vars
            try:
                load_dotenv(dotenv_path=path, override=False)
                _debug(f"Loaded env from: {path}")
            except Exception:
                _debug(f"Failed to load env from: {path}")
            # stop after first successful load
            return
    _debug("No .env or .env.example file found; nothing loaded")

# Execute at import time (PyInstaller will import runtime_hooks early)
try:
    _load_dotenv()
except Exception:
    # never crash the application because of env-loading issues
    _debug("Exception occurred in load_dotenv hook, continuing without loading .env")
