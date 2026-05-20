from datetime import datetime, timezone
from typing import Any, Dict, List
from fastapi import APIRouter

router = APIRouter(tags=["signals"])

signal_events: List[Dict[str, Any]] = []
MAX_SIGNALS = 1000

@router.post("/signal")
async def receive_signal(payload: Dict[str, Any]):
    # Import websocket manager dynamically to prevent import recursion loops
    from apps.vps_server.routes.bot_control import manager

    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    signal_events.append(event)

    if len(signal_events) > MAX_SIGNALS:
        signal_events.pop(0)

    # Broadcast engine logic changes or chart candlestick packages straight to UI
    await manager.broadcast({
        "signal_engine": payload.get("signal_engine", event),
        "market_structure": payload.get("market_structure", payload.get("direction", "--")),
        "d1_bias": payload.get("d1_bias", "--"),
        "h4_bias": payload.get("h4_bias", "--"),
        "chart_data": payload.get("chart_data", []) # Feeds modern lightweight chart vectors directly
    })

    return {
        "ok": True,
        "message": "Signal received and broadcasted live",
        "count": len(signal_events),
    }

@router.get("/signals")
def get_signals():
    latest_signals = signal_events[-20:][::-1]
    return {"ok": True, "count": len(latest_signals), "signals": latest_signals}