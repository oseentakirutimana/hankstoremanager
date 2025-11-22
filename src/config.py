# config.py
"""
Configuration centrale.
- get_resource_path() compatible PyInstaller
- load_user_env() : copie .env.example -> %APPDATA%/.env au premier lancement puis charge .env utilisateur
- validation de FACTURATION_OBR_FERNET_KEY (base64 urlsafe -> bytes pour Fernet)
- get_default_db_path() : chemin DB utilisateur (writable)
"""

import os
import sys
import base64
import shutil
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

APP_NAME = "hankstoremanager"
DEFAULT_DB_FILENAME = "facturation_obr.db"
_PROJECT_ENV = Path(__file__).resolve().parent / ".env"
_PROJECT_ENV_EXAMPLE = Path(__file__).resolve().parent / ".env.example"

# UI colours
COULEUR_BARRE_SUPERIEURE = "#1f2329"
COULEUR_MENU_LATERAL = "#2a3138"
COULEUR_CORPS_PRINCIPAL = "#f1faff"
COULEUR_MENU_SURVOL = "#2f88c5"

def get_user_data_dir(app_name: str = APP_NAME) -> Path:
    try:
        if sys.platform.startswith("win"):
            base = Path(os.getenv("APPDATA") or Path.home())
            return (base / app_name).expanduser().resolve()
        if sys.platform == "darwin":
            return (Path.home() / "Library" / "Application Support" / app_name).resolve()
        return (Path.home() / ".local" / "share" / app_name).resolve()
    except Exception:
        return Path.cwd().resolve()

def get_resource_path(relative_path: str) -> str:
    try:
        base = Path(sys._MEIPASS)  # type: ignore
    except Exception:
        base = Path(__file__).resolve().parent
    return str((base / relative_path).resolve())

def load_user_env(env_name: str = ".env", env_example_name: str = ".env.example", override: bool = True) -> Path:
    user_dir = get_user_data_dir()
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    env_path = user_dir / env_name

    if not env_path.exists():
        try:
            candidate = Path(get_resource_path(env_example_name))
            if candidate.exists():
                shutil.copy2(str(candidate), str(env_path))
            else:
                if _PROJECT_ENV_EXAMPLE.exists():
                    shutil.copy2(str(_PROJECT_ENV_EXAMPLE), str(env_path))
        except Exception:
            pass

    try:
        load_dotenv(dotenv_path=str(env_path), override=override)
    except Exception:
        try:
            load_dotenv(dotenv_path=str(env_path), override=False)
        except Exception:
            pass

    return env_path

# load .env project then user .env
try:
    if _PROJECT_ENV.exists():
        load_dotenv(dotenv_path=str(_PROJECT_ENV), override=False)
except Exception:
    pass

_USER_ENV_PATH = load_user_env()
OBR_ENV_PATH = str(_USER_ENV_PATH.resolve())

def _validate_fernet_key(raw: str) -> Optional[bytes]:
    if not raw:
        return None
    try:
        s = raw.strip().strip('"').strip("'")
        key_bytes = s.encode("utf-8")
        decoded = base64.urlsafe_b64decode(key_bytes)
        if len(decoded) != 32:
            return None
        return key_bytes
    except Exception:
        return None

def _read_fernet_key_from_env() -> Optional[bytes]:
    v = os.getenv("FACTURATION_OBR_FERNET_KEY")
    return _validate_fernet_key(v) if v else None

FERNET_SECRET_KEY = _read_fernet_key_from_env()

def get_default_db_path() -> str:
    env = os.getenv("FACTURATION_OBR_DB_PATH")
    if env:
        try:
            return str(Path(env).expanduser().resolve())
        except Exception:
            pass
    user_dir = get_user_data_dir()
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str((user_dir / DEFAULT_DB_FILENAME).resolve())

KEY_STORE_DB_PATH = get_default_db_path()
DEFAULT_ENV_PATH = OBR_ENV_PATH
