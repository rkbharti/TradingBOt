from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from apps.vps_server.state import bot_state
from apps.vps_server.telegram_utils import send_telegram
import json

router = APIRouter(tags=["bot-control"])

# --- WEBSOCKET CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Immediately push current snapshot state to wipe out mock HTML entries on load
        await self.send_initial_snapshot(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

    async def send_initial_snapshot(self, websocket: WebSocket):
        # Imports here inside method prevents potential circular inheritance issues
        from apps.vps_server.routes.signals import signal_events
        from apps.vps_server.routes.trade_results import trade_result_events

        # Dynamically verify if tracking timeframe exist on current state configuration
        current_tf = getattr(bot_state, "current_timeframe", "M15")

        snapshot = {
            "bot_status": bot_state.trading_enabled,
            "current_tf": current_tf,
            "equity": getattr(bot_state, "equity", 0.0),
            "balance": getattr(bot_state, "balance", 0.0),
            "pnl_today": getattr(bot_state, "pnl_today", 0.0),
            "market_structure": getattr(bot_state, "market_structure", "--"),
            "session": getattr(bot_state, "session", "--"),
            "d1_bias": getattr(bot_state, "d1_bias", "--"),
            "h4_bias": getattr(bot_state, "h4_bias", "--"),
            "trades": [], # Populated if tracking live open positions
            "closed_trades": trade_result_events[-10:][::-1],
            "signal_engine": signal_events[-1] if signal_events else {
                "action": "NO_TRADE", "confidence": 0, "reason": "Scanning telemetry..."
            }
        }
        try:
            await websocket.send_json(snapshot)
        except Exception:
            pass

manager = ConnectionManager()

# --- NEW WEBSOCKET LIVE ROUTE ---
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Listen to JSON interaction strings sent from your UI dashboard buttons
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            action = data.get("action")

            # B) FIX: Handles dashboard execution switch toggle intents
            if action == "toggle_bot":
                status_input = data.get("status")
                is_active = status_input == "ON" or status_input is True
                bot_state.set_trading(is_active)
                send_telegram(f"⚙️ <b>Dashboard Command:</b> Bot set to {'RUNNING' if is_active else 'PAUSED'}")
                await manager.broadcast({"bot_status": is_active})

            # C) FIX: Handles dashboard structural timeframe changes
            elif action == "change_timeframe":
                selected_tf = data.get("timeframe", "M15")
                bot_state.current_timeframe = selected_tf  # Attached to state for MT5 tracking
                send_telegram(f"📊 <b>Dashboard Command:</b> Timeframe switched to <b>{selected_tf}</b>")
                await manager.broadcast({"current_tf": selected_tf})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# --- REVISED EXTENDED REST API ENDPOINTS ---
@router.get("/bot/status")
def get_bot_status():
    return {
        "trading": bot_state.trading_enabled,
        "timeframe": getattr(bot_state, "current_timeframe", "M15"), # MT5 fetches structural shifts here
        "updated_at": bot_state.updated_at,
    }

@router.post("/bot/pause")
async def pause_bot():
    bot_state.set_trading(False)
    send_telegram("⏸️ <b>Trading Bot Paused</b>")
    await manager.broadcast({"bot_status": False})
    return {"ok": True, "trading": False}

@router.post("/bot/resume")
async def resume_bot():
    bot_state.set_trading(True)
    send_telegram("▶️ <b>Trading Bot Resumed</b>")
    await manager.broadcast({"bot_status": True})
    return {"ok": True, "trading": True}