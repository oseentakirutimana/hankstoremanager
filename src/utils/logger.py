import logging
from datetime import datetime

# Configuration du logger principal
LOG_FILE = "app.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()  # Affiche aussi en console
    ]
)

def log_info(message):
    logging.info(message)

def log_debug(message):
    logging.debug(message)

def log_erreur(message):
    logging.error(message)
