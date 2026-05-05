from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["signals"])

# In-memory store (bounded)
signal_events: List[Dict[str, Any]] = []
MAX_SIGNALS = 1000


@router.post("/signal")
def receive_signal(payload: Dict[str, Any]):
    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    signal_events.append(event)

    # Memory control (production safety)
    if len(signal_events) > MAX_SIGNALS:
        signal_events.pop(0)

    return {
        "ok": True,
        "message": "Signal received",
        "count": len(signal_events),
        "event": event,
    }


@router.get("/signals")
def get_signals():
    # Last 20 signals (latest first)
    latest_signals = signal_events[-20:][::-1]

    return {
        "ok": True,
        "count": len(latest_signals),
        "signals": latest_signals,
    }