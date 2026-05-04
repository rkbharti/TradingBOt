from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["signals"])

signal_events: List[Dict[str, Any]] = []


@router.post("/signal")
def receive_signal(payload: Dict[str, Any]):
    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    signal_events.append(event)

    return {
        "ok": True,
        "message": "Signal received",
        "count": len(signal_events),
        "event": event,
    }