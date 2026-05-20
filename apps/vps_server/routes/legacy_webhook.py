from datetime import datetime, timezone
from fastapi import APIRouter, Request

router = APIRouter(tags=["legacy-compat"])

@router.post("/webhook")
async def legacy_webhook(request: Request):
    payload = await request.json()
    print(f"[{datetime.now(timezone.utc).isoformat()}] legacy /webhook hit")
    
    # Hook into the WebSocket manager to update the live dashboard 24/7
    try:
        from apps.vps_server.routes.bot_control import manager
        
        # Broadcast the incoming data immediately
        await manager.broadcast({
            "signal_engine": {
                "action": payload.get("action", payload.get("direction", "SIGNAL")),
                "confidence": payload.get("confidence", 100),
                "reason": payload.get("reason", "Legacy webhook trigger"),
                "entry": payload.get("entry"),
                "sl": payload.get("sl"),
                "tp": payload.get("tp")
            },
            "market_structure": payload.get("market_structure", "--"),
            "session": payload.get("session", "ALL")
        })
        print("✅ Legacy webhook broadcasted live to dashboard")
    except Exception as e:
        print(f"⚠️ Legacy webhook failed to broadcast: {e}")

    return {
        "status": "accepted",
        "note": "legacy webhook compatibility"
    }