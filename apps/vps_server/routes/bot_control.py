from datetime import datetime, timezone

from fastapi import APIRouter

from apps.vps_server.state import bot_state

router = APIRouter(tags=["bot-control"])


@router.get("/bot/status")
def get_bot_status():
    return {
        "trading": bot_state.trading_enabled,
        "updated_at": bot_state.updated_at,
    }


@router.post("/bot/pause")
def pause_bot():
    bot_state.set_trading(False)
    return {
        "ok": True,
        "message": "Bot paused",
        "trading": bot_state.trading_enabled,
        "updated_at": bot_state.updated_at,
    }


@router.post("/bot/resume")
def resume_bot():
    bot_state.set_trading(True)
    return {
        "ok": True,
        "message": "Bot resumed",
        "trading": bot_state.trading_enabled,
        "updated_at": bot_state.updated_at,
    }