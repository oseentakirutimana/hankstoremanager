# utils/ensure_user_files.py
"""
Prépare et sécurise les fichiers utilisateur au premier lancement.
- copie facturation_obr.db (embarquée) -> %APPDATA%/... si absent
- copie .env.example -> .env si absent
- copie app.inv -> user_dir/app.inv (et restreint permissions)
- si possible effectue migration vers SQLCipher et stocke passphrase via keyring/FERNET fallback
"""

import os
import sys
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Late imports of project config / helpers to avoid import-time cycles
try:
    from config import get_user_data_dir, get_resource_path, get_default_db_path, FERNET_SECRET_KEY  # type: ignore
except Exception:
    # fallback implementations
    def get_user_data_dir(app_name: str = "hankstoremanager") -> Path:
        if sys.platform.startswith("win"):
            return Path(os.getenv("APPDATA") or Path.home()) / app_name
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / app_name
        return Path.home() / ".local" / "share" / app_name

    def get_resource_path(relative: str) -> str:
        try:
            base = Path(sys._MEIPASS)  # type: ignore
        except Exception:
            base = Path(__file__).resolve().parent.parent
        return str((base / relative).resolve())

    def get_default_db_path() -> str:
        return str((get_user_data_dir() / "facturation_obr.db").resolve())

    FERNET_SECRET_KEY = None

# optional modules
try:
    from utils.sqlcipher_migrate import migrate_plain_to_sqlcipher  # type: ignore
except Exception:
    migrate_plain_to_sqlcipher = None

try:
    from utils.key_store import store_passphrase_keyring, store_passphrase_local_encrypted  # type: ignore
except Exception:
    store_passphrase_keyring = None
    store_passphrase_local_encrypted = None

def _chmod_restrict(path: Path):
    try:
        if os.name != "nt":
            path.chmod(0o600)
    except Exception:
        logger.exception("chmod failed for %s", path)

def _apply_windows_acl(path: Path, username: Optional[str] = None):
    if os.name != "nt":
        return
    try:
        user = username or os.getenv("USERNAME") or os.getlogin()
        cmd = f'icacls "{path}" /inheritance:r /grant:r "{user}:(F)"'
        rc = os.system(cmd)
        if rc != 0:
            logger.warning("icacls returned %s for %s", rc, path)
    except Exception:
        logger.exception("Windows ACL failed for %s", path)

def _copy_embedded_if_missing(src: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    try:
        if os.path.exists(src):
            shutil.copy2(src, str(dest))
            logger.info("Copied embedded %s -> %s", src, dest)
            _chmod_restrict(dest)
            if os.name == "nt":
                _apply_windows_acl(dest)
            return dest
    except Exception:
        logger.exception("Copy failed %s -> %s", src, dest)
    try:
        dest.touch(exist_ok=True)
        _chmod_restrict(dest)
        return dest
    except Exception:
        logger.exception("Fallback create failed for %s", dest)
        return dest

def ensure_user_files(app_name: str = "hankstoremanager", 
                      db_name: str = "facturation_obr.db",
                      inv_name: str = "app.inv",
                      env_example_name: str = ".env.example") -> Dict[str, str]:
    """
    Crée le dossier utilisateur, copie les fichiers embarqués si nécessaire,
    effectue migration SQLCipher si possible et stocke la passphrase.
    Retourne mapping {name: absolute_path}.
    """
    user_dir = get_user_data_dir(app_name)
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.exception("Could not create user dir %s", user_dir)

    results: Dict[str, str] = {}

    # 1) copy plain DB (starting point)
    try:
        bundled_db = get_resource_path(db_name)
    except Exception:
        bundled_db = db_name
    target_plain = Path(user_dir) / db_name
    _copy_embedded_if_missing(bundled_db, target_plain)
    results[db_name] = str(target_plain)

    # 2) copy inv file (app.inv)
    try:
        bundled_inv = get_resource_path(inv_name)
    except Exception:
        bundled_inv = inv_name
    target_inv = Path(user_dir) / inv_name
    _copy_embedded_if_missing(bundled_inv, target_inv)
    results[inv_name] = str(target_inv)

    # 3) copy .env.example -> .env
    try:
        bundled_env_example = get_resource_path(env_example_name)
    except Exception:
        bundled_env_example = env_example_name
    target_env = Path(user_dir) / ".env"
    if not target_env.exists():
        if os.path.exists(bundled_env_example):
            try:
                shutil.copy2(bundled_env_example, str(target_env))
                _chmod_restrict(target_env)
                results[".env"] = str(target_env)
            except Exception:
                logger.exception("Could not copy env example")
                results[".env"] = str(target_env)
        else:
            try:
                target_env.touch(exist_ok=True)
                _chmod_restrict(target_env)
                results[".env"] = str(target_env)
            except Exception:
                logger.exception("Could not create env file")
                results[".env"] = str(target_env)
    else:
        results[".env"] = str(target_env)

    # 4) SQLCipher migration (if available)
    cipher_db = Path(user_dir) / db_name
    plain_db = target_plain

    migrated = False
    try:
        # only attempt migration if module available
        sqlcipher_available = False
        try:
            from pysqlcipher3 import dbapi2 as _  # type: ignore
            sqlcipher_available = True
        except Exception:
            try:
                from sqlcipher3 import dbapi2 as _  # type: ignore
                sqlcipher_available = True
            except Exception:
                sqlcipher_available = False

        if migrate_plain_to_sqlcipher and sqlcipher_available:
            # if cipher file already exists and non-empty, consider it migrated
            if cipher_db.exists() and cipher_db.stat().st_size > 0:
                migrated = True
                results[db_name] = str(cipher_db)
            else:
                # generate strong passphrase
                import os, base64
                raw = os.urandom(32)
                passphrase = base64.urlsafe_b64encode(raw).decode("utf-8")
                ok = migrate_plain_to_sqlcipher(str(plain_db), str(cipher_db), passphrase)
                if ok:
                    migrated = True
                    results[db_name] = str(cipher_db)
                    # store passphrase: prefer keyring
                    stored = False
                    try:
                        if store_passphrase_keyring:
                            stored = store_passphrase_keyring(passphrase)
                    except Exception:
                        stored = False
                    if not stored:
                        try:
                            if store_passphrase_local_encrypted:
                                store_passphrase_local_encrypted(passphrase, FERNET_SECRET_KEY, str(user_dir))
                        except Exception:
                            logger.exception("Storing passphrase local fallback failed")
                    # move plain db to backup
                    try:
                        bak = plain_db.with_suffix(".bak")
                        shutil.move(str(plain_db), str(bak))
                        logger.info("Moved plain DB to backup %s", bak)
                    except Exception:
                        logger.exception("Could not move plain DB to backup")
    except Exception:
        logger.exception("SQLCipher migration attempt failed")

    if not migrated:
        results[db_name] = str(target_plain)

    return results
