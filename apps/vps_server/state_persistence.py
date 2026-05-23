import json
import os
from dataclasses import asdict

# Always saves next to this file, regardless of where server is launched from
STATE_FILE = os.path.join(os.path.dirname(__file__), "bot_state.json")

def save_state(bot_state):
    """
    Persist current bot state to disk.
    """
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(asdict(bot_state), f, indent=4)
        return True
    except Exception as e:
        print(f"❌ Failed to save bot state: {e}")
        return False


def load_state():
    """
    Load bot state from disk if available.
    """
    try:
        if not os.path.exists(STATE_FILE):
            return None

        with open(STATE_FILE, "r") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"❌ Failed to load bot state: {e}")
        return None