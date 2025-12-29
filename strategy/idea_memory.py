# strategy/idea_memory.py

from datetime import datetime, timedelta
from enum import Enum


class IdeaState(Enum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"


class IdeaMemory:
    """
    Human-like idea memory.
    Remembers failed ideas and blocks retrying them
    until new market information appears.
    """

    def __init__(self, expiry_minutes=30):
        self.ideas = {}
        self.expiry = timedelta(minutes=expiry_minutes)
        self.results = {} 

    def _now(self):
        return datetime.now()

    def _idea_key(self, direction, zone, zone_bucket, session):
        """
        Uniquely identifies a trading idea
        """
        return f"{direction}|{zone}|{zone_bucket}|{session}"

    def is_allowed(self, direction, zone, zone_bucket, session):
        """
        Check if this idea is allowed to trade
        """
        key = self._idea_key(direction, zone, zone_bucket, session)

        idea = self.ideas.get(key)
        if not idea:
            return True  # New idea, allowed

        # Expired idea → allow again
        if self._now() - idea["last_update"] > self.expiry:
            self.ideas.pop(key, None)
            return True

        if idea["state"] == IdeaState.FAILED:
            return False

        return True

    def mark_active(self, direction, zone, zone_bucket, session):
        """
        Mark idea as currently being traded
        """
        key = self._idea_key(direction, zone, zone_bucket, session)
        self.ideas[key] = {
            "state": IdeaState.ACTIVE,
            "last_update": self._now()
        }

    def mark_failed(self, direction, zone, zone_bucket, session, reason=""):
        """
        Mark idea as failed (human: 'this didn't work')
        """
        key = self._idea_key(direction, zone, zone_bucket, session)
        self.ideas[key] = {
            "state": IdeaState.FAILED,
            "last_update": self._now(),
            "reason": reason
        }
    def mark_result(self, idea_id, result):
        """Track trade outcome for learning"""
        self.results[idea_id] = {
            'result': result,  # 'win' or 'loss'
            'timestamp': datetime.now(),
            'profit': result.get('profit', 0)
        }

    def reset_all(self, reason="context_change"):
        """
        Clear idea memory when market context changes
        (new BOS, CHOCH, session change, etc.)
        """
        self.ideas.clear()
