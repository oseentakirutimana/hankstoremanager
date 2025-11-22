# utils/sqlcipher_migrate.py
"""
Migration d'une DB SQLite non-chiffrée vers une DB chiffrée SQLCipher.
Utilise pysqlcipher3 (ou sqlcipher3) comme binding Python.
"""

from pathlib import Path
import logging
from typing import Optional

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

def migrate_plain_to_sqlcipher(plain_db: str, cipher_db: str, passphrase: str) -> bool:
    """
    Crée cipher_db chiffrée (SQLCipher) et copie schema + données depuis plain_db.
    Return True si succès.
    """
    sqlcipher = _get_sqlcipher_module()
    if sqlcipher is None:
        logger.error("Aucun binding SQLCipher disponible (pysqlcipher3/sqlcipher3).")
        return False

    plain = str(Path(plain_db).resolve())
    cipher = str(Path(cipher_db).resolve())

    try:
        conn = sqlcipher.connect(cipher)
        cur = conn.cursor()
        # Set key for the new encrypted DB
        cur.execute(f"PRAGMA key = '{passphrase}';")
        conn.commit()

        # Attach the plain DB and copy objects
        cur.execute(f"ATTACH DATABASE '{plain}' AS plain KEY '';")
        cur.execute(
            "SELECT type, name, sql FROM plain.sqlite_master "
            "WHERE sql NOT NULL AND type IN ('table','index','trigger','view') "
            "ORDER BY type='table' DESC"
        )
        rows = cur.fetchall()
        for typ, name, sql in rows:
            if not name or name.startswith("sqlite_"):
                continue
            try:
                cur.execute(sql)
            except Exception:
                # ignore create errors (indexes etc)
                pass
            if typ == "table":
                try:
                    cur.execute(f"INSERT INTO \"{name}\" SELECT * FROM plain.\"{name}\";")
                except Exception:
                    logger.exception("Could not copy data for table %s", name)
        conn.commit()
        cur.execute("DETACH DATABASE plain;")
        conn.close()
        logger.info("Migration to SQLCipher succeeded: %s", cipher)
        return True
    except Exception as e:
        logger.exception("Migration to SQLCipher failed: %s", e)
        try:
            conn.close()
        except Exception:
            pass
        return False
