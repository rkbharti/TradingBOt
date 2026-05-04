from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.vps_server.routes.bot_control import router as bot_control_router
from apps.vps_server.routes.daily_summary import router as daily_summary_router
from apps.vps_server.routes.health import router as health_router
from apps.vps_server.routes.signals import router as signals_router
from apps.vps_server.routes.trade_results import router as trade_results_router

app = FastAPI(
    title="TradingBOt VPS Receiver",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(bot_control_router)
app.include_router(signals_router)
app.include_router(trade_results_router)
app.include_router(daily_summary_router)


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "vps_receiver",
    }