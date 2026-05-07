from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

from apps.vps_server.telegram_utils import send_telegram
from apps.vps_server.routes.trade_results import trade_result_events
from apps.vps_server.routes.signals import signal_events
from apps.vps_server.state import bot_state

router = APIRouter(tags=["daily-summary"])

daily_summary_events: List[Dict[str, Any]] = []

IST = ZoneInfo("Asia/Kolkata")

scheduler = BackgroundScheduler(timezone=IST)


def generate_daily_summary() -> str:
    today = datetime.now(IST).strftime("%Y-%m-%d")

    total_trades = len(trade_result_events)

    wins = 0
    losses = 0
    total_pnl = 0.0

    for trade in trade_result_events:
        result = str(trade.get("result", "")).lower()
        pnl = float(trade.get("pnl", 0))

        total_pnl += pnl

        if result in ["win", "tp"]:
            wins += 1
        elif result in ["loss", "sl"]:
            losses += 1

    win_rate = 0.0

    if total_trades > 0:
        win_rate = (wins / total_trades) * 100

    latest_signals = len(signal_events)

    bot_status = "ACTIVE ✅" if bot_state.trading_enabled else "PAUSED ⏸️"

    summary = (
        f"📊 <b>Daily Trading Summary</b>\n\n"
        f"Date: {today}\n\n"
        f"Bot Status: {bot_status}\n\n"
        f"Signals Generated: {latest_signals}\n"
        f"Trades Closed: {total_trades}\n\n"
        f"✅ Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"📈 Win Rate: {win_rate:.1f}%\n\n"
        f"💰 Net PnL: ${total_pnl:.2f}\n"
    )

    return summary


def send_daily_summary():
    try:
        summary = generate_daily_summary()

        send_telegram(summary)

        event = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }

        daily_summary_events.append(event)

        if len(daily_summary_events) > 100:
            daily_summary_events.pop(0)

        print("✅ Daily summary sent")

    except Exception as e:
        print(f"❌ Daily summary error: {e}")


# Every day at 6:30 AM IST
scheduler.add_job(
    send_daily_summary,
    trigger="cron",
    hour=6,
    minute=30,
)

scheduler.start()


@router.post("/daily-summary/send")
def manual_send_daily_summary():
    send_daily_summary()

    return {
        "ok": True,
        "message": "Daily summary sent manually",
        "count": len(daily_summary_events),
    }


@router.get("/daily-summary/history")
def get_daily_summary_history():
    latest = daily_summary_events[-10:][::-1]

    return {
        "ok": True,
        "count": len(latest),
        "history": latest,
    }