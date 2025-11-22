from datetime import datetime

LOG_FILE = "ebms_log.txt"

def log_verification_TIN(tin, status, message):
    """
    Enregistre une ligne de log dans ebms_log.txt avec :
    - Horodatage
    - TIN vérifié
    - Statut (Valide, Invalide, Erreur HTTP, Erreur Réseau, etc.)
    - Message retourné par l’API OBR ou le système
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ligne = f"[{timestamp}] TIN: {tin} | Statut: {status} | Message: {message}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(ligne)
    except Exception as e:
        from utils.logger import log_erreur
        log_erreur(f"Erreur lors de l’écriture dans ebms_log.txt : {e}")
