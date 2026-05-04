from datetime import datetime, timezone
from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/webhook")
async def legacy_webhook(request: Request):
    payload = await request.json()
    print(f"[{datetime.now(timezone.utc).isoformat()}] legacy /webhook hit")
    return {
        "status": "accepted",
        "note": "legacy webhook compatibility"
    }