# database/connection.py
"""
Simple wrapper pour gérer un chemin DB global et fournir get_connection().
- Si SQLCipher est utilisé, ton code devra appeler utils/db_connect_sqlcipher.connect_sqlcipher
  directement en lui passant la passphrase récupérée (ce wrapper reste pour plain SQLite).
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

_DB_PATH_ENV = "FACTURATION_OBR_DB_PATH"
_current_db_path: Optional[str] = None

def set_db_path(path: Optional[str]):
    global _current_db_path
    if path is None:
        return
    _current_db_path = str(Path(path).resolve())
    os.environ[_DB_PATH_ENV] = _current_db_path

def get_db_path() -> str:
    global _current_db_path
    if _current_db_path:
        return _current_db_path
    env = os.getenv(_DB_PATH_ENV)
    if env:
        _current_db_path = str(Path(env).resolve())
        return _current_db_path
    # fallback: current working directory DB
    _current_db_path = str(Path.cwd() / "facturation_obr.db")
    return _current_db_path

def get_connection(timeout: float = 30.0) -> sqlite3.Connection:
    """
    Retourne une connexion sqlite3 standard (non SQLCipher).
    Si tu utilises SQLCipher, utilise utils/db_connect_sqlcipher.connect_sqlcipher
    avec la passphrase appropriée.
    """
    dbp = get_db_path()
    path = Path(dbp)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dbp, timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return conn
