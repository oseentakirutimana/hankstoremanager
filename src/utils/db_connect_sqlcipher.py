# utils/db_connect_sqlcipher.py
"""
Helper pour se connecter à une DB SQLCipher via pysqlcipher3/sqlcipher3.
Expose connect_sqlcipher(db_path, passphrase).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

def _get_sqlcipher_module():
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher
        return sqlcipher
    except Exception:
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
            return sqlcipher
        except Exception:
            return None

def connect_sqlcipher(db_path: str, passphrase: str, timeout: float = 5.0) -> Any:
    """
    Ouvre une connexion SQLCipher et applique PRAGMA key.
    Lève RuntimeError si la clé est invalide ou si l'import manque.
    """
    sqlcipher = _get_sqlcipher_module()
    if sqlcipher is None:
        raise RuntimeError("pysqlcipher3/sqlcipher3 non disponible")
    conn = sqlcipher.connect(db_path, timeout=timeout)
    cur = conn.cursor()
    cur.execute(f"PRAGMA key = '{passphrase}';")
    try:
        cur.execute("SELECT count(*) FROM sqlite_master;")
        _ = cur.fetchone()
    except Exception as e:
        conn.close()
        raise RuntimeError("Invalid SQLCipher key or corrupted DB") from e
    return conn
