# utils/key_store.py
"""
Gestion du stockage sécurisé de la passphrase SQLCipher.
- Priorité : python-keyring
- Fallback : fichier chiffré localement avec la clé Fernet (config.FERNET_SECRET_KEY)
"""

import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

try:
    import keyring
except Exception:
    keyring = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None
    InvalidToken = Exception

APP_KEY_NAME = "facturation_obr_sqlcipher"
LOCAL_ENC_FILENAME = "sqlcipher_pass.enc"

def store_passphrase_keyring(passphrase: str, app_name: str = APP_KEY_NAME) -> bool:
    if keyring is None:
        return False
    try:
        keyring.set_password(app_name, "sqlcipher_pass", passphrase)
        return True
    except Exception:
        logger.exception("keyring.set_password failed")
        return False

def retrieve_passphrase_keyring(app_name: str = APP_KEY_NAME) -> Optional[str]:
    if keyring is None:
        return None
    try:
        return keyring.get_password(app_name, "sqlcipher_pass")
    except Exception:
        logger.exception("keyring.get_password failed")
        return None

def store_passphrase_local_encrypted(passphrase: str, fernet_key: Optional[bytes], user_dir: str) -> bool:
    try:
        if Fernet is None:
            logger.error("cryptography.Fernet non disponible")
            return False
        if not fernet_key:
            logger.error("FERNET key absent, impossible de chiffrer localement")
            return False
        f = Fernet(fernet_key)
        token = f.encrypt(passphrase.encode("utf-8"))
        out = Path(user_dir) / LOCAL_ENC_FILENAME
        out.write_bytes(token)
        try:
            if os.name != "nt":
                out.chmod(0o600)
        except Exception:
            pass
        return True
    except Exception:
        logger.exception("store_passphrase_local_encrypted failed")
        return False

def retrieve_passphrase_local_encrypted(fernet_key: Optional[bytes], user_dir: str) -> Optional[str]:
    try:
        if Fernet is None:
            return None
        if not fernet_key:
            return None
        p = Path(user_dir) / LOCAL_ENC_FILENAME
        if not p.exists():
            return None
        token = p.read_bytes()
        f = Fernet(fernet_key)
        try:
            dec = f.decrypt(token)
            return dec.decode("utf-8")
        except InvalidToken:
            logger.exception("Fernet decrypt failed: invalid token")
            return None
    except Exception:
        logger.exception("retrieve_passphrase_local_encrypted failed")
        return None
