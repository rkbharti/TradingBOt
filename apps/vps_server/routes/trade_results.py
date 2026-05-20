from datetime import datetime, timezone
from typing import Any, Dict, List
from fastapi import APIRouter
from apps.vps_server.telegram_utils import send_telegram

# 1. This must be initialized BEFORE decorators use it
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
async def receive_trade_result(payload: Dict[str, Any]):
    from apps.vps_server.routes.bot_control import manager
    from apps.vps_server.state import bot_state

    event = {
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    trade_result_events.append(event)

    if len(trade_result_events) > MAX_TRADE_RESULTS:
        trade_result_events.pop(0)

    # Update and accumulate metrics directly inside our central state cache
    trade_pnl = float(payload.get("pnl", 0))
    bot_state.pnl_today = round(getattr(bot_state, "pnl_today", 0.0) + trade_pnl, 2)
    bot_state.pnl_total = round(getattr(bot_state, "pnl_total", 0.0) + trade_pnl, 2)
    bot_state.balance = float(payload.get("balance", bot_state.balance))
    bot_state.equity = float(payload.get("equity", bot_state.equity))

    try:
        send_telegram(build_trade_result_message(event))
    except Exception as e:
        print(f"❌ Trade-result Telegram error: {e}")

    # Broadcast from the central state instead of the raw payload keys
    await manager.broadcast({
        "closed_trades": trade_result_events[-20:][::-1],
        "equity": bot_state.equity,
        "balance": bot_state.balance,
        "pnl_today": bot_state.pnl_today,
        "pnl_total": bot_state.pnl_total
    })

    return {
        "ok": True,
        "message": "Trade result captured and updated live",
        "count": len(trade_result_events),
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