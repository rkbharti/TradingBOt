from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["daily-summary"])

daily_summary_events: List[Dict[str, Any]] = []


@router.post("/daily-summary")
def receive_daily_summary(payload: Dict[str, Any]):
    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    daily_summary_events.append(event)

    return {
        "ok": True,
        "message": "Daily summary received",
        "count": len(daily_summary_events),
        "event": event,
    }