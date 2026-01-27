# utils/ob_observation_logger.py
# fix
import json
import os
from datetime import datetime, timezone
from threading import Lock





class OBObservationLogger:
    def __init__(self, path="research/ob_observations.json"):
        self.path = path
        self._lock = Lock()

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        # Ensure file exists
        if not os.path.exists(self.path):
            with open(self.path, "w") as f:
                json.dump([], f)

    def log(self, payload: dict):
        payload = dict(payload)
        payload["logged_at"] = datetime.now(timezone.utc).isoformat()

        with self._lock:
            try:
                with open(self.path, "r") as f:
                    data = json.load(f)
            except Exception:
                data = []

            data.append(payload)

            with open(self.path, "w") as f:
                json.dump(data, f, indent=2)
    