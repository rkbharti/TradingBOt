from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["trade-results"])

trade_result_events: List[Dict[str, Any]] = []


@router.post("/trade-result")
def receive_trade_result(payload: Dict[str, Any]):
    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    trade_result_events.append(event)

    return {
        "ok": True,
        "message": "Trade result received",
        "count": len(trade_result_events),
        "event": event,
    }