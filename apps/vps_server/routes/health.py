from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "vps_receiver",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }