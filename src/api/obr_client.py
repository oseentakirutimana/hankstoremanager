# ebms_client.py
import os
from pathlib import Path
import requests
from dotenv import load_dotenv
from utils.logger import log_info, log_erreur, log_debug
from utils.ebms_logger import log_verification_TIN

# --- Résolution du .env en production via config ---
try:
    # config.get_user_data_dir() doit renvoyer le dossier utilisateur (ex: %APPDATA%/facturation_obr)
    from config import get_user_data_dir, OBR_ENV_PATH  # type: ignore
except Exception:
    # fallback si config absent
    def get_user_data_dir(app_name: str = "facturation_obr") -> Path:
        if os.name == "nt":
            return Path(os.getenv("APPDATA") or Path.home()) / app_name
        if os.sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / app_name
        return Path.home() / ".local" / "share" / app_name
    OBR_ENV_PATH = ".env"

def _resolve_env_path(env_path: str | None = None) -> Path:
    p = Path(env_path or OBR_ENV_PATH)
    if not p.is_absolute():
        user_dir = get_user_data_dir()
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        p = (user_dir / p).resolve()
    return p

# Charger le .env utilisateur (override pour forcer les valeurs du fichier)
_env_file = _resolve_env_path()
try:
    if _env_file.exists():
        load_dotenv(dotenv_path=str(_env_file), override=True)
        log_debug(f".env chargé depuis {_env_file}")
    else:
        log_debug(f".env introuvable en {_env_file}, utilisation des variables d'environnement système si présentes")
except Exception as e:
    log_erreur(f"Impossible de charger .env depuis {_env_file}: {e}")

# Lecture sûre des variables d'environnement
def _get_env(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v if v else None

OBR_USERNAME = _get_env("OBR_USERNAME")
OBR_PASSWORD = _get_env("OBR_PASSWORD")
OBR_SYSTEM_ID = _get_env("OBR_SYSTEM_ID")

# Endpoints
BASE_URL = "https://ebms.obr.gov.bi:9443/ebms_api"
AUTH_ENDPOINT = "/login/"
CHECK_TIN_ENDPOINT = "/checkTIN/"
_REQUEST_TIMEOUT = 30  # seconds

def get_system_id():
    return OBR_SYSTEM_ID

def obtenir_token_auto():
    # 🔐 Identifiants intégrés (ne pas logger le mot de passe)
    username = OBR_USERNAME
    password = OBR_PASSWORD

    if not username or not password:
        log_erreur("OBR credentials manquantes (OBR_USERNAME/OBR_PASSWORD).")
        return None

    url = BASE_URL.rstrip("/") + AUTH_ENDPOINT
    payload = {"username": username, "password": password}

    log_info(f"Tentative de connexion automatique avec username: {username}")
    try:
        response = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT)
        log_debug(f"Code HTTP: {response.status_code}")
        log_debug(f"Réponse brute: {response.text}")
        response.raise_for_status()

        # Extraction sûre du token
        j = {}
        try:
            j = response.json()
        except Exception:
            log_erreur("Réponse non-JSON reçue lors de l'authentification.")
            return None

        token = None
        # Plusieurs API renvoient token différemment ; tenter les chemins usuels
        if isinstance(j, dict):
            token = j.get("result", {}).get("token") or j.get("token") or j.get("access_token")
        if not token:
            log_erreur("Token non trouvé dans la réponse d'authentification.")
            return None

        log_info("Token reçu avec succès.")
        return token

    except requests.exceptions.RequestException as e:
        log_erreur(f"Erreur d'authentification: {e}")
        return None

def checkTIN(tin):
    tin = (tin or "").strip()
    if not tin:
        return {"valid": False, "message": "Le champ TIN est vide."}

    token = obtenir_token_auto()
    if not token:
        try:
            log_verification_TIN(tin, "Erreur Jeton", "Impossible d'obtenir le jeton eBMS.")
        except Exception:
            pass
        return {"valid": False, "message": "Impossible d'obtenir le jeton eBMS."}

    url = BASE_URL.rstrip("/") + CHECK_TIN_ENDPOINT
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"tp_TIN": tin}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()

        try:
            json_data = response.json()
        except Exception:
            try:
                log_verification_TIN(tin, "Erreur", "Réponse non-JSON de l'API OBR")
            except Exception:
                pass
            return {"valid": False, "message": "Réponse inattendue de l'API OBR."}

        message = json_data.get("msg") if isinstance(json_data, dict) else None
        if not message:
            message = "Réponse OBR absente."

        taxpayer_list = []
        if isinstance(json_data, dict):
            taxpayer_list = json_data.get("result", {}).get("taxpayer", []) or []

        if json_data.get("success") and taxpayer_list:
            tp_data = taxpayer_list[0]
            try:
                log_verification_TIN(tin, "Valide", message)
            except Exception:
                pass
            return {"valid": True, "data": tp_data, "message": message}
        else:
            try:
                log_verification_TIN(tin, "Invalide", message)
            except Exception:
                pass
            return {"valid": False, "message": message}

    except requests.exceptions.HTTPError as e:
        msg = None
        try:
            msg = e.response.text
        except Exception:
            msg = str(e)
        try:
            log_verification_TIN(tin, "Erreur HTTP", msg)
        except Exception:
            pass
        return {"valid": False, "message": f"Erreur HTTP OBR : {msg}"}

    except requests.exceptions.RequestException as e:
        try:
            log_verification_TIN(tin, "Erreur Réseau", str(e))
        except Exception:
            pass
        return {"valid": False, "message": f"Connexion impossible à l'API OBR : {e}"}
