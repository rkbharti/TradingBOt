import json
import os
from datetime import datetime, date
from typing import Dict, Any


class ObservationLogger:
    """
    Passive observer for live trading sessions.
    This class NEVER influences trading decisions.
    """

    def __init__(self, base_dir: str = "logs/observations"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

        self.today = date.today().isoformat()
        self.file_path = os.path.join(self.base_dir, f"{self.today}.json")

        self.data = self._load_or_init()

    # -------------------------
    # Lifecycle
    # -------------------------
    def bot_started(self):
        now = datetime.now().strftime("%H:%M:%S")
        if self.data["bot_start_time"] is None:
            self.data["bot_start_time"] = now
        else:
            self.data["restarts"] += 1
        self._save()

    def bot_stopped(self):
        self.data["bot_stop_time"] = datetime.now().strftime("%H:%M:%S")
        self._save()

    # -------------------------
    # Session context
    # -------------------------
    def update_session(self, session_name: str, active: bool):
        self.data["sessions"].setdefault(session_name, {})
        self.data["sessions"][session_name]["active"] = active
        self._save()

    def mark_liquidity_event(self, session: str, pdh_swept=False, pdl_swept=False):
        sess = self.data["sessions"].setdefault(session, {})
        sess["pdh_swept"] = pdh_swept
        sess["pdl_swept"] = pdl_swept
        self._save()

    # -------------------------
    # Events (OBs, Signals, Blocks)
    # -------------------------
    def log_event(self, event_type: str, details: Dict[str, Any]):
        event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "details": details
        }
        self.data["events"].append(event)
        self._save()

    # -------------------------
    # Internal helpers
    # -------------------------
    def _load_or_init(self) -> Dict[str, Any]:
        if os.path.exists(self.file_path):
            with open(self.file_path, "r") as f:
                return json.load(f)

        return {
            "date": self.today,
            "bot_start_time": None,
            "bot_stop_time": None,
            "restarts": 0,
            "sessions": {},
            "events": []
        }

    def _save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.data, f, indent=2)
