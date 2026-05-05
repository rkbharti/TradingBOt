from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["trade-results"])

# In-memory store (bounded)
trade_result_events: List[Dict[str, Any]] = []
MAX_TRADE_RESULTS = 1000


@router.post("/trade-result")
def receive_trade_result(payload: Dict[str, Any]):
    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    trade_result_events.append(event)

    # Memory control (production safety)
    if len(trade_result_events) > MAX_TRADE_RESULTS:
        trade_result_events.pop(0)

    return {
        "ok": True,
        "message": "Trade result received",
        "count": len(trade_result_events),
        "event": event,
    }


@router.get("/trade-results")
def get_trade_results():
    # Last 20 trade results (latest first)
    latest_results = trade_result_events[-20:][::-1]

    return {
        "ok": True,
        "count": len(latest_results),
        "trade_results": latest_results,
    }