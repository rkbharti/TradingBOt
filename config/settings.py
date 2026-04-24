import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

MT5_LOGIN    = os.getenv("MT5_LOGIN")
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER   = os.getenv("MT5_SERVER")
MT5_PATH     = os.getenv("MT5_PATH")

DRY_RUN      = os.getenv("DRY_RUN", "true").lower() == "true"
SYMBOL       = os.getenv("SYMBOL", "XAUUSD")
TIMEFRAME    = os.getenv("TIMEFRAME", "M5")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_TELEGRAM    = os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"
