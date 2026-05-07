from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

from apps.vps_server.telegram_utils import send_telegram

router = APIRouter(tags=["trade-results"])

# In-memory store (bounded)
trade_result_events: List[Dict[str, Any]] = []
MAX_TRADE_RESULTS = 1000


def build_trade_result_message(event: Dict[str, Any]) -> str:
    symbol = event.get("symbol", "UNKNOWN")
    direction = event.get("direction", "UNKNOWN")
    result = event.get("result", "UNKNOWN")

    pnl = event.get("pnl", 0)
    rr = event.get("rr", "N/A")

    entry = event.get("entry_price", "N/A")
    exit_price = event.get("exit_price", "N/A")

    reason = event.get("reason", "N/A")

    emoji = "✅" if str(result).upper() in ["WIN", "TP"] else "❌"

    return (
        f"{emoji} <b>Trade Closed</b>\n"
        f"Symbol: {symbol}\n"
        f"Direction: {direction}\n"
        f"Result: {result}\n"
        f"PnL: ${pnl}\n"
        f"RR: {rr}\n"
        f"Entry: {entry}\n"
        f"Exit: {exit_price}\n"
        f"Reason: {reason}"
    )


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

    # Telegram alert
    try:
        send_telegram(build_trade_result_message(event))
    except Exception as e:
        print(f"❌ Trade-result Telegram error: {e}")

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