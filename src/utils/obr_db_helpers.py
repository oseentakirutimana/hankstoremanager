import sqlite3
import os
import logging
from datetime import datetime
from database.connection import get_connection

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def get_client_data(tin: str):
    if not tin:
        return None
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM client WHERE customer_TIN = ? LIMIT 1", (tin,))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT * FROM client WHERE customer_name LIKE ? LIMIT 1", (f"%{tin}%",))
            row = cur.fetchone()
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return dict(row)
        if isinstance(row, dict):
            return row
        desc = [d[0] for d in cur.description] if cur.description else []
        if desc and isinstance(row, (list, tuple)):
            return {desc[i]: row[i] for i in range(min(len(desc), len(row)))}
        return dict(row)
    except Exception:
        logger.exception("Erreur get_client_data")
        return None
    finally:
        try:
            if conn: conn.close()
        except Exception:
            pass

def get_contribuable_data(contribuable_id: int):
    if not contribuable_id:
        return None
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM contribuable WHERE id = ? LIMIT 1", (contribuable_id,))
        row = cur.fetchone()
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return dict(row)
        if isinstance(row, dict):
            return row
        desc = [d[0] for d in cur.description] if cur.description else []
        if desc and isinstance(row, (list, tuple)):
            return {desc[i]: row[i] for i in range(min(len(desc), len(row)))}
        return dict(row)
    except Exception:
        logger.exception("Erreur get_contribuable_data")
        return None
    finally:
        try:
            if conn: conn.close()
        except Exception:
            pass

def get_next_invoice_number(prefix: str = "INV", width: int = 4) -> str:
    """
    Retourne le prochain numéro de facture basé sur le dernier enregistrement du jour.
    Format retourné : {prefix}_{YYYYMMDD}_{suffix} où suffix est zero-padded à `width` chiffres.
    Utilise une transaction SQLite (BEGIN IMMEDIATE) pour réduire les collisions concurrentes.
    """
    today = datetime.now().strftime("%Y%m%d")
    base = f"{prefix}_{today}_"
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        # Prendre un verrou immédiat pour minimiser les races (SQLite) puis lire le dernier suffix
        try:
            cur.execute("BEGIN IMMEDIATE")
        except Exception:
            # si le moteur/connexion ne supporte pas BEGIN IMMEDIATE, continuer sans verrou explicite
            pass

        # Tenter d'extraire le suffix numérique maximal directement en SQL
        try:
            cur.execute(
                """
                SELECT invoice_number,
                       CAST(
                         (   -- extraire la partie après le dernier underscore et caster en entier
                           SUBSTR(invoice_number, INSTR(invoice_number, '_', -1) + 1)
                         ) AS INTEGER
                       ) AS suffix_num
                FROM facture
                WHERE invoice_number LIKE ?
                ORDER BY suffix_num DESC
                LIMIT 1
                """,
                (base + "%",)
            )
            row = cur.fetchone()
        except Exception:
            # fallback général si la requête SQL spécifique échoue (ex: dialecte sqlite sans INSTR(..., -1))
            cur.execute("SELECT invoice_number FROM facture WHERE invoice_number LIKE ? ORDER BY id DESC LIMIT 1", (base + "%",))
            row = cur.fetchone()

        if row:
            # row peut être sqlite3.Row ou tuple
            last_inv = None
            try:
                last_inv = row["invoice_number"] if hasattr(row, "keys") else row[0]
            except Exception:
                last_inv = None
            if last_inv:
                # extraire le suffix numérique en Python de façon robuste
                try:
                    suffix_part = last_inv.rsplit("_", 1)[-1]
                    num = int(suffix_part)
                except Exception:
                    num = 1
            else:
                num = 1
        else:
            num = 1

        next_num = num + 1
        suffix = str(next_num).zfill(width)
        inv = f"{base}{suffix}"

        # Commit si on a commencé une transaction
        try:
            conn.commit()
        except Exception:
            pass

        return inv
    except Exception:
        logger.exception("Erreur get_next_invoice_number")
        # fallback propre si erreur inattendue
        return f"{base}{str(1).zfill(width)}"
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

def ensure_invoice_signature_columns():
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(facture)")
        rows = cur.fetchall()
        cols = []
        for r in rows:
            try:
                if isinstance(r, sqlite3.Row) or hasattr(r, "keys"):
                    cols.append(r["name"] if "name" in r.keys() else r[1])
                else:
                    cols.append(r[1])
            except Exception:
                try:
                    cols.append(r[1])
                except Exception:
                    pass
        if "invoice_signature" not in cols:
            cur.execute("ALTER TABLE facture ADD COLUMN invoice_signature TEXT")
        if "invoice_signature_date" not in cols:
            cur.execute("ALTER TABLE facture ADD COLUMN invoice_signature_date TEXT")
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass
        logger.exception("Erreur ensure_invoice_signature_columns")
    finally:
        try:
            if conn: conn.close()
        except Exception:
            pass

def validate_signature_date(date_str: str) -> bool:
    if not date_str:
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return True
    except Exception:
        return False


