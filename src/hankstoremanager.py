# hankstoremanager.py
"""
Launcher principal.
- Prépare fichiers utilisateur (ensure_user_files)
- Charge clé Fernet depuis user_dir/app.inv si présente (avant import modules qui en dépendent)
- Résout passphrase SQLCipher (keyring / FERNET fallback) si migration faite
- Configure database.connection via set_db_path et démarre AppController
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("hankstoremanager")

try:
    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)
except Exception:
    project_root = os.path.abspath(os.path.dirname(__file__))

# prepare user files (copy bundled resources)
try:
    from utils.ensure_user_files import ensure_user_files
except Exception:
    ensure_user_files = None
    logger.warning("utils.ensure_user_files introuvable")

# key retrieval helpers
try:
    from utils.key_store import retrieve_passphrase_keyring, retrieve_passphrase_local_encrypted  # type: ignore
except Exception:
    retrieve_passphrase_keyring = None
    retrieve_passphrase_local_encrypted = None

# DB connection setter
try:
    from database.connection import set_db_path, get_connection  # type: ignore
except Exception:
    def set_db_path(path: str):
        os.environ["FACTURATION_OBR_DB_PATH"] = path or ""
    def get_connection():
        raise RuntimeError("database.connection.get_connection not available")

# other app pieces (lazy import after env/fernet ready)
AppController = None
init_db = None
try:
    # don't import AppController here; will import after env ready
    from models.key_manager_sqlite import init_db as _maybe_init_db  # type: ignore
    init_db = _maybe_init_db
except Exception:
    init_db = None

def _load_fernet_from_inv(user_dir: str, inv_filename: str = "app.inv") -> bool:
    import base64, logging
    logger_local = logging.getLogger("hankstoremanager._load_fernet")
    try:
        p = Path(user_dir) / inv_filename
        if not p.exists():
            logger_local.debug("app.inv not found at %s", p)
            return False
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            logger_local.warning("app.inv empty")
            return False
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
        except Exception:
            logger_local.exception("app.inv not base64 urlsafe")
            return False
        if len(decoded) != 32:
            logger_local.warning("app.inv decoded len %d (expected 32)", len(decoded))
            return False
        # set env var used by config on import or update config if already imported
        os.environ["FACTURATION_OBR_FERNET_KEY"] = raw
        try:
            import config
            config.FERNET_SECRET_KEY = raw.encode("utf-8")
            logger_local.info("FERNET key loaded from app.inv and applied to config")
        except Exception:
            logger_local.info("config not importable now; environment var set")
        return True
    except Exception:
        logger_local.exception("Error reading app.inv")
        return False

def prepare_user_files_and_db() -> dict:
    import config
    results = {}
    try:
        if ensure_user_files:
            results = ensure_user_files(app_name=config.APP_NAME,
                                       db_name=Path(config.KEY_STORE_DB_PATH).name,
                                       inv_name="app.inv",
                                       env_example_name=".env.example")
        else:
            user_dir = config.get_user_data_dir()
            os.makedirs(user_dir, exist_ok=True)
            results = {
                Path(config.KEY_STORE_DB_PATH).name: str(Path(user_dir) / Path(config.KEY_STORE_DB_PATH).name),
                "app.inv": str(Path(user_dir) / "app.inv"),
                ".env": str(Path(user_dir) / ".env"),
            }
    except Exception:
        logger.exception("prepare_user_files failed")
    return results

def resolve_sqlcipher_passphrase(user_dir: str) -> Optional[str]:
    # Try keyring first
    try:
        if retrieve_passphrase_keyring:
            p = retrieve_passphrase_keyring()
            if p:
                return p
    except Exception:
        pass
    # fallback: local encrypted file with FERNET
    try:
        import config
        if retrieve_passphrase_local_encrypted := getattr(__import__("utils.key_store", fromlist=["retrieve_passphrase_local_encrypted"]), "retrieve_passphrase_local_encrypted", None):
            p = retrieve_passphrase_local_encrypted(getattr(config, "FERNET_SECRET_KEY", None), user_dir)
            if p:
                return p
    except Exception:
        pass
    return None

def start_app(db_path=None, config_path=None, inv_path=None):
    global AppController
    try:
        from controllers.app_controller import AppController as _AppController  # type: ignore
        AppController = _AppController
    except Exception:
        logger.exception("controllers.app_controller.AppController introuvable")
        AppController = None

    # initialize DB schema if a module provides it
    if init_db:
        try:
            if db_path:
                try:
                    init_db(db_path)
                    logger.info("init_db executed with db_path=%s", db_path)
                except TypeError:
                    init_db()
                    logger.info("init_db executed without param")
            else:
                init_db()
                logger.info("init_db executed without param")
        except Exception:
            logger.exception("Error during init_db()")

    # set db path for other modules
    if db_path:
        try:
            set_db_path(db_path)
            logger.info("set_db_path called with %s", db_path)
        except Exception:
            logger.exception("set_db_path failed")

    # start UI controller
    if AppController is None:
        logger.error("AppController not available")
        raise RuntimeError("AppController not available")

    try:
        try:
            app = AppController(db_path=db_path)
            logger.info("AppController initialized with db_path")
        except TypeError:
            app = AppController()
            logger.info("AppController initialized without db_path")
    except Exception:
        logger.exception("Failed to initialize AppController")
        raise

    try:
        if hasattr(app, "mainloop") and callable(app.mainloop):
            app.mainloop()
        elif hasattr(app, "run") and callable(app.run):
            app.run()
        else:
            logger.error("AppController exposes neither mainloop nor run")
            raise RuntimeError("AppController not runnable")
    except Exception:
        logger.exception("Error during app execution")
        raise

def main():
    try:
        import config
        user_files = prepare_user_files_and_db()
        logger.info("User files resolved: %s", user_files)

        user_dir = str(config.get_user_data_dir())
        # load Fernet key from app.inv early (before modules import that depend on it)
        _load_fernet_from_inv(user_dir, inv_filename="app.inv")

        db_name = Path(config.KEY_STORE_DB_PATH).name
        db_path = user_files.get(db_name) or config.get_default_db_path()
        inv_path = user_files.get("app.inv") or None

        logger.info("Resolved DB path: %s", db_path)
        logger.info("Resolved INV path: %s", inv_path)
        logger.info("Using env path: %s", getattr(config, "OBR_ENV_PATH", "unknown"))

        # attempt to get SQLCipher passphrase
        passphrase = resolve_sqlcipher_passphrase(str(config.get_user_data_dir()))
        if passphrase:
            logger.info("SQLCipher passphrase found; using encrypted DB.")
            set_db_path(db_path)
        else:
            logger.info("No SQLCipher passphrase found; using plain DB path.")
            set_db_path(db_path)

        start_app(db_path=db_path, config_path=None, inv_path=inv_path)

    except Exception as e:
        logger.exception("Fatal startup error: %s", e)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("Erreur", f"Erreur au démarrage : {e}")
            root.destroy()
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
