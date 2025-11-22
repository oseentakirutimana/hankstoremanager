import time
import json
import os

class Session:
    def __init__(self):
        self.username = None
        self.role = None
        self.login_time = None
        self.token_eBMS = None
        self.token_time = None
        self.session_file = os.path.join(os.path.dirname(__file__), "session_data.json")
        self.load_session()

    def start_session(self, username, role=None):
        self.username = username
        self.role = role
        self.login_time = time.time()
        self.save_session()

    def is_session_active(self):
        if self.login_time is None:
            return False
        return (time.time() - self.login_time) < 3600  # 1h

    def end_session(self):
        self.username = None
        self.role = None
        self.login_time = None
        self.token_eBMS = None
        self.token_time = None
        self.save_session()

    def save_session(self):
        data = {
            "username": self.username,
            "role": self.role,
            "login_time": self.login_time
        }
        try:
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            print("Erreur sauvegarde session :", e)

    def load_session(self):
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.username = data.get("username")
                self.role = data.get("role")
                self.login_time = data.get("login_time")
            except Exception as e:
                print("Erreur chargement session :", e)

    def set_token_eBMS(self, token):
        self.token_eBMS = token
        self.token_time = time.time()

    def get_token_eBMS(self):
        if self.token_eBMS and (time.time() - self.token_time) < 60:
            return self.token_eBMS
        return None

    def get_remaining_time(self):
        if self.token_time:
            remaining = 60 - (time.time() - self.token_time)
            return max(0, int(remaining))
        return 0

# Instance globale
session = Session()
