# models/key_manager_sqlite.py
"""
Key store SQLite.
- Utilise database.connection.get_connection() si disponible, sinon ouvre sqlite3 vers
  le chemin renvoyé par config.get_default_db_path().
- Gère chiffrement optionnel via Fernet (clé lue depuis config.FERNET_SECRET_KEY).
- Fournit génération de clés d'activation lisibles au format XXXX-XXXX-XXXX-XXXX (configurable).
"""

import sqlite3
import os
import datetime
import uuid
import hashlib
import secrets
import string
from typing import List, Optional, Dict, Any

# --- Config import (robuste) ---
try:
    from config import FERNET_SECRET_KEY, get_default_db_path  # type: ignore
except Exception:
    FERNET_SECRET_KEY = None
    def get_default_db_path() -> str:
        return os.path.abspath("facturation_obr.db")

# --- Prefer central connection helper ---
_USE_CENTRAL_CONN = True
try:
    from database.connection import get_connection as _central_get_connection  # type: ignore
except Exception:
    _central_get_connection = None
    _USE_CENTRAL_CONN = False

# --- Fernet init (optional) ---
_HAS_FERNET = False
_FERNET = None
if FERNET_SECRET_KEY:
    try:
        from cryptography.fernet import Fernet  # type: ignore
        _FERNET = Fernet(FERNET_SECRET_KEY)
        _HAS_FERNET = True
    except Exception:
        _HAS_FERNET = False
        _FERNET = None

# --- Utilities ---
def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat()

def _fingerprint(plain_key: str) -> str:
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()

def _ensure_dir_for_path(path: str):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    except Exception:
        pass

def _open_conn_explicit(path: str) -> sqlite3.Connection:
    _ensure_dir_for_path(path)
    conn = sqlite3.connect(path, timeout=30)
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

def _get_conn(path: Optional[str] = None) -> sqlite3.Connection:
    """
    Retourne une connexion SQLite.
    - Si path is None et database.connection.get_connection disponible -> l'utilise.
    - Sinon ouvre une connexion explicite vers path ou get_default_db_path().
    """
    if path is None and _USE_CENTRAL_CONN and _central_get_connection:
        return _central_get_connection()
    p = path or get_default_db_path()
    return _open_conn_explicit(p)

# --- Fernet helpers ---
def encrypt_key(plain_key: str) -> Optional[str]:
    if not _HAS_FERNET or not _FERNET:
        return None
    try:
        return _FERNET.encrypt(plain_key.encode("utf-8")).decode("utf-8")
    except Exception:
        return None

def decrypt_key(enc_key: str) -> Optional[str]:
    if not _HAS_FERNET or not _FERNET:
        return None
    try:
        return _FERNET.decrypt(enc_key.encode("utf-8")).decode("utf-8")
    except Exception:
        return None

# --- Activation key generation (readable format) ---
_ALPHABET_ACT = string.ascii_uppercase + string.digits

def _make_activation_piece(length: int = 5) -> str:
    return ''.join(secrets.choice(_ALPHABET_ACT) for _ in range(length))

def generate_activation_key(groups: int = 4, group_len: int = 5, prefix: Optional[str] = None) -> str:
    parts = [_make_activation_piece(group_len) for _ in range(groups)]
    key = '-'.join(parts)
    return f"{prefix}-{key}" if prefix else key

def generate_keys(n: int = 10, prefix: Optional[str] = None, groups: int = 4, group_len: int = 5) -> List[str]:
    return [generate_activation_key(groups=groups, group_len=group_len, prefix=prefix) for _ in range(n)]

# --- DB schema init ---
def init_db(path: Optional[str] = None):
    db_path = path or (None if _USE_CENTRAL_CONN else get_default_db_path())
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            fingerprint TEXT,
            is_encrypted INTEGER NOT NULL DEFAULT 0,
            used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            used_at TEXT,
            used_by TEXT,
            created_by TEXT,
            revoked INTEGER NOT NULL DEFAULT 0,
            revoked_reason TEXT,
            revoked_by TEXT
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_key ON keys(key);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_used ON keys(used);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_keys_fprint ON keys(fingerprint);")
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

# --- Key operations ---
def add_generated_keys(n: int = 10, prefix: Optional[str] = None, path: Optional[str] = None,
                       created_by: Optional[str] = None, groups: int = 4, group_len: int = 5) -> List[str]:
    init_db(path)
    conn = _get_conn(path)
    cur = conn.cursor()
    added: List[str] = []
    for _ in range(n):
        plain = generate_activation_key(groups=groups, group_len=group_len, prefix=prefix)
        fp = _fingerprint(plain)
        storable = plain
        is_enc = 0
        if _HAS_FERNET:
            enc = encrypt_key(plain)
            if enc:
                storable = enc
                is_enc = 1
        try:
            cur.execute("BEGIN")
            cur.execute(
                "INSERT INTO keys (key, fingerprint, is_encrypted, used, created_at, created_by) VALUES (?, ?, ?, 0, ?, ?)",
                (storable, fp, is_enc, _now_iso(), created_by)
            )
            cur.execute("COMMIT")
            added.append(plain)
        except sqlite3.IntegrityError:
            cur.execute("ROLLBACK")
            continue
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            continue
    try:
        conn.close()
    except Exception:
        pass
    return added

def list_keys(path: Optional[str] = None, decrypted: bool = True) -> List[Dict[str, Any]]:
    init_db(path)
    conn = _get_conn(path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM keys ORDER BY created_at DESC")
    rows = cur.fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        if rec.get("is_encrypted") and decrypted and _HAS_FERNET:
            try:
                rec["plain_key"] = decrypt_key(rec["key"])
            except Exception:
                rec["plain_key"] = None
        else:
            rec["plain_key"] = rec["key"] if not rec.get("is_encrypted") else None
        out.append(rec)
    try:
        conn.close()
    except Exception:
        pass
    return out

def _find_row_by_plain(plain_key: str, path: Optional[str] = None):
    fp = _fingerprint(plain_key)
    conn = _get_conn(path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM keys WHERE fingerprint = ? LIMIT 1", (fp,))
        r = cur.fetchone()
        if r:
            return r
        if _HAS_FERNET:
            cur.execute("SELECT * FROM keys WHERE is_encrypted = 1")
            rows = cur.fetchall()
            for row in rows:
                stored = row["key"]
                try:
                    dec = decrypt_key(stored)
                except Exception:
                    dec = None
                if dec and dec == plain_key:
                    return row
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return None

def validate_key_plain(plain_key: str, path: Optional[str] = None) -> bool:
    r = _find_row_by_plain(plain_key, path)
    if not r:
        return False
    if r["used"] or r["revoked"]:
        return False
    return True

def consume_key_plain(plain_key: str, used_by: Optional[str] = None, path: Optional[str] = None) -> bool:
    fp = _fingerprint(plain_key)
    conn = _get_conn(path)
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute("SELECT * FROM keys WHERE fingerprint = ? LIMIT 1", (fp,))
        r = cur.fetchone()
        if not r and _HAS_FERNET:
            cur.execute("SELECT * FROM keys WHERE is_encrypted = 1")
            rows = cur.fetchall()
            for row in rows:
                try:
                    dec = decrypt_key(row["key"])
                except Exception:
                    dec = None
                if dec and dec == plain_key:
                    r = row
                    break
        if not r:
            cur.execute("ROLLBACK")
            return False
        if r["used"] or r["revoked"]:
            cur.execute("ROLLBACK")
            return False
        cur.execute("UPDATE keys SET used = 1, used_at = ?, used_by = ? WHERE id = ?", (_now_iso(), used_by, r["id"]))
        conn.commit()
        return True
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

def validate_encrypted_input(encrypted_input: str, path: Optional[str] = None) -> bool:
    if not _HAS_FERNET:
        return False
    plain = decrypt_key(encrypted_input)
    if not plain:
        return False
    return validate_key_plain(plain, path=path)

def consume_encrypted_input(encrypted_input: str, used_by: Optional[str] = None, path: Optional[str] = None) -> bool:
    if not _HAS_FERNET:
        return False
    try:
        plain = decrypt_key(encrypted_input)
    except Exception:
        return False
    if not plain:
        return False
    return consume_key_plain(plain, used_by=used_by, path=path)

def revoke_key(plain_key: str, reason: Optional[str] = None, revoked_by: Optional[str] = None, path: Optional[str] = None) -> bool:
    conn = _get_conn(path)
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        r = _find_row_by_plain(plain_key, path=path)
        if not r:
            cur.execute("ROLLBACK")
            return False
        cur.execute("UPDATE keys SET revoked = 1, revoked_reason = ?, revoked_by = ? WHERE id = ?", (reason, revoked_by, r["id"]))
        cur.execute("COMMIT")
        return True
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

def reset_key_usage(plain_key: str, path: Optional[str] = None) -> bool:
    conn = _get_conn(path)
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        r = _find_row_by_plain(plain_key, path=path)
        if not r:
            cur.execute("ROLLBACK")
            return False
        cur.execute("UPDATE keys SET used = 0, used_at = NULL, used_by = NULL, revoked = 0, revoked_reason = NULL, revoked_by = NULL WHERE id = ?", (r["id"],))
        cur.execute("COMMIT")
        return True
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

def migrate_from_json_store(json_path: str, path: Optional[str] = None) -> int:
    import json
    if not os.path.exists(json_path):
        return 0
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    added = 0
    for k, info in data.items():
        used = bool(info.get("used"))
        created_at = info.get("created_at") or _now_iso()
        created_by = info.get("created_by")
        storable = k
        is_enc = 0
        fp = _fingerprint(k)
        if _HAS_FERNET:
            enc = encrypt_key(k)
            if enc:
                storable = enc
                is_enc = 1
        conn = _get_conn(path)
        cur = conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("INSERT OR IGNORE INTO keys (key, fingerprint, is_encrypted, used, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                        (storable, fp, is_enc, int(used), created_at, created_by))
            cur.execute("COMMIT")
            added += 1
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return added
