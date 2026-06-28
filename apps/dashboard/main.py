# GUARDEER OS v4.1 — main.py [ENHANCED: timeframe support + overlays]
# Port: 8001 | VPS: Oracle Linux
#
# ENHANCEMENTS:
#   1. Added: current_timeframe in bot_state, default "M15"
#   2. Added: WebSocket "change_tf" updates bot_state and broadcasts
#   3. Added: Optional forwarding to trader's /set_timeframe (port 8000)
#   4. PRESERVED: All backend logic, routes, schemas
# ════════════════════════════════════════════════════════════════════

import sys
# Reconfigure stdout/stderr to UTF-8 on Windows to prevent UnicodeEncodeError in console
if hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
if hasattr(sys.stderr, "reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8")
    except Exception: pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import json
from datetime import datetime, date, timedelta
import hashlib
import traceback
import math
import os
import time as _time
import httpx
import psutil

# ─── Path resolution ────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR  = os.path.join(_HERE, "static")
_TEMPLATES_DIR = os.path.join(_HERE, "templates")

# ═══════════════════════════════════════════════════════════════════════════
# ClosedTradesTracker — Persist closed trades history
# ═══════════════════════════════════════════════════════════════════════════
class ClosedTradesTracker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.filename = os.path.join(_HERE, f"closed_trades_history_{symbol}.json")
        # For compatibility:
        if not os.path.exists(self.filename) and symbol == "XAUUSD":
            old_file = os.path.join(_HERE, "closed_trades_history.json")
            if os.path.exists(old_file):
                self.filename = old_file
        self.trades = []
        self.load_trades()

    def load_trades(self):
        try:
            if os.path.exists(self.filename):
                with open(self.filename, "r", encoding="utf-8") as f:
                    self.trades = json.load(f)
                print(f"✅ Loaded {len(self.trades)} historical closed trades for {self.symbol}")
        except Exception as e:
            print(f"⚠️ Failed to load closed trades history for {self.symbol}: {e}")

    def save_trades(self):
        try:
            # Keep only last 200 trades
            self.trades = self.trades[-200:]
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(self.trades, f, indent=4)
        except Exception as e:
            print(f"⚠️ Failed to save closed trades history for {self.symbol}: {e}")

    def add_trades(self, new_trades: list):
        added = False
        for t in new_trades:
            tid = t.get("id")
            if not any(existing.get("id") == tid for existing in self.trades):
                self.trades.append(t)
                added = True
        if added:
            self.save_trades()

    def get_all(self):
        return self.trades

import pytz

# ─── Globals ────────────────────────────────────────────────────────────
active_connections = []
bot_states = {} # mapping symbol -> state
closed_trades_trackers = {} # mapping symbol -> ClosedTradesTracker
pnl_trackers = {} # mapping symbol -> DailyPnLTracker
_SERVER_START_TIME = _time.time()
last_webhook_timestamp = None

def get_symbol_state(symbol: str) -> dict:
    if symbol not in bot_states:
        if symbol not in closed_trades_trackers:
            closed_trades_trackers[symbol] = ClosedTradesTracker(symbol)
        if symbol not in pnl_trackers:
            pnl_trackers[symbol] = DailyPnLTracker(symbol)
        bot_states[symbol] = {
            "symbol":            symbol,
            "current_timeframe": "M15",
            "closed_trades":     closed_trades_trackers[symbol].get_all()[-50:],
            "trading":           True,
            "equity":            0.0,
            "balance":           0.0,
            "pnl_daily":         0.0,
            "pnl_today":         0.0,
            "pnl_week":          0.0,
            "pnl_total":         0.0,
            "open_pnl":          0.0,
            "price":             0.0,
            "market_structure":  "NEUTRAL",
            "session":           "ASIAN",
            "trades":            [],
            "poi_overlays":      [],
            "chart_objects":     {},
            "chart_data":        [],
            "news_items":        [],
            "news_time":         "--",
            "signal_engine":     {
                "action":      "NO_TRADE",
                "direction":   "NEUTRAL",
                "confidence":  0,
                "reason":      "Scanning...",
                "gates":       {}
            }
        }
    return bot_states[symbol]

# ═══════════════════════════════════════════════════════════════════════════
# DailyPnLTracker — UNCHANGED
# ═══════════════════════════════════════════════════════════════════════════
class DailyPnLTracker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.realized_pnl = 0.0
        tz = pytz.timezone('Asia/Kolkata')
        self.last_reset_date = datetime.now(tz).date()
        self.history = {}
        self.total_realized = 0.0
        self.processed_ticket_ids = set()
        self.load_state()

    def load_state(self):
        try:
            state_file = os.path.join(_HERE, f"dashboard_state_{self.symbol}.json")
            if not os.path.exists(state_file) and self.symbol == "XAUUSD":
                old_file = os.path.join(_HERE, "dashboard_state.json")
                if os.path.exists(old_file):
                    state_file = old_file
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    data = json.load(f)
                    self.realized_pnl = float(data.get("realized_pnl", 0.0))
                    self.total_realized = float(data.get("total_realized", 0.0))
                    self.history = data.get("history", {})
                    self.processed_ticket_ids = set(data.get("processed_ticket_ids", []))
                    if "last_reset_date" in data:
                        self.last_reset_date = date.fromisoformat(data["last_reset_date"])
                print(f"✅ Dashboard Daily PnL state for {self.symbol} loaded from disk")
        except Exception as e:
            print(f"⚠️ Failed to load Daily PnL state for {self.symbol}: {e}")

    def save_state(self):
        try:
            state_file = os.path.join(_HERE, f"dashboard_state_{self.symbol}.json")
            with open(state_file, "w") as f:
                json.dump({
                    "realized_pnl": self.realized_pnl,
                    "total_realized": self.total_realized,
                    "last_reset_date": self.last_reset_date.isoformat(),
                    "history": self.history,
                    "processed_ticket_ids": list(self.processed_ticket_ids),
                }, f, indent=4)
        except Exception as e:
            print(f"⚠️ Failed to save Daily PnL state for {self.symbol}: {e}")

    def _ensure_today(self):
        tz = pytz.timezone('Asia/Kolkata')
        today = datetime.now(tz).date()
        if today > self.last_reset_date:
            self.last_reset_date = today
            self.realized_pnl = 0.0
            self.save_state()

    def add_closed_trade(self, pnl: float, when: datetime = None, ticket: str = None):
        if ticket:
            try:
                if ticket in self.processed_ticket_ids:
                    return
                self.processed_ticket_ids.add(ticket)
            except Exception:
                pass
        
        tz = pytz.timezone('Asia/Kolkata')
        if when is None:
            when = datetime.now(tz)
        else:
            if when.tzinfo is None:
                when = tz.localize(when)
            else:
                when = when.astimezone(tz)
                
        d = when.date()
        self._ensure_today()
        ds = d.isoformat()
        self.history[ds] = self.history.get(ds, 0.0) + float(pnl)
        
        today = datetime.now(tz).date()
        if d == today:
            self.realized_pnl += float(pnl)
        self.total_realized += float(pnl)
        self.save_state()

    def get_daily(self):
        self._ensure_today()
        return float(self.realized_pnl)

    def get_weekly(self):
        tz = pytz.timezone('Asia/Kolkata')
        today = datetime.now(tz).date()
        start = today - timedelta(days=today.weekday())
        total = 0.0
        d = start
        for i in range(7):
            ds = d.isoformat()
            total += float(self.history.get(ds, 0.0))
            d = d + timedelta(days=1)
        return float(total)

    def get_total(self):
        return float(self.total_realized)

# Initialize default symbol states on startup so tabs are visible immediately
get_symbol_state("XAUUSD")
get_symbol_state("BTCUSD")

# ═══════════════════════════════════════════════════════════════════════════
# broadcast_loop — sends bot_states to all WebSocket clients
# ═══════════════════════════════════════════════════════════════════════════
async def broadcast_loop():
    last_state_hash = None
    while True:
        if active_connections and bot_states:
            try:
                state_json = json.dumps(bot_states, sort_keys=True, default=str).replace("NaN", "null")
                state_hash = hashlib.sha256(state_json.encode()).hexdigest()
                if state_hash != last_state_hash:
                    payload = state_json
                    try:
                        print(f"📡 Broadcasting ({len(active_connections)} clients) {len(payload)}B")
                    except Exception:
                        pass
                    for conn in active_connections[:]:
                        try:
                            await conn.send_text(payload)
                        except Exception:
                            try: active_connections.remove(conn)
                            except Exception: pass
                    last_state_hash = state_hash
            except Exception as e:
                print(f"📡 Broadcast error: {e}")
        await asyncio.sleep(3.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()

# ═══════════════════════════════════════════════════════════════════════════
# FastAPI app setup
# ═══════════════════════════════════════════════════════════════════════════
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    print(f"✅ Static files: {_STATIC_DIR}")
else:
    print(f"⚠️  Static dir not found: {_STATIC_DIR}")

templates = Jinja2Templates(directory=_TEMPLATES_DIR) if os.path.isdir(_TEMPLATES_DIR) else None

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if templates is not None:
        idx = os.path.join(_TEMPLATES_DIR, "index.html")
        if os.path.exists(idx):
            return templates.TemplateResponse(request=request, name="index.html")
    return HTMLResponse(content=_get_inline_html())

# ═══════════════════════════════════════════════════════════════════════════
# Helper functions (unchanged)
# ═══════════════════════════════════════════════════════════════════════════
def get_val(obj, key, default=0.0):
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default

def parse_profit(x):
    if x is None:
        return 0.0
    try:
        if isinstance(x, (int, float)):
            val = float(x)
        else:
            s = str(x).strip()
            for ch in ("$", "€", ","):
                s = s.replace(ch, "")
            s = s.replace("(", "-").replace(")", "")
            val = float(s)
        if math.isnan(val):
            return 0.0
        return val
    except Exception:
        return 0.0

def normalize_webhook_payload(payload: dict) -> tuple:
    if "bot_instance" in payload and "analysis_data" in payload:
        bot_inst = payload["bot_instance"]
        analysis = payload["analysis_data"]
        # --- FIX: extract account fields from nested account dict ---
        if "account" in bot_inst and isinstance(bot_inst["account"], dict):
            account = bot_inst["account"]
            bot_inst["account_login"] = account.get("login", "--")
            bot_inst["account_server"] = account.get("server", "--")
            # ensure equity/balance are also present at root (if missing)
            if "equity" not in bot_inst:
                bot_inst["equity"] = account.get("equity", 0.0)
            if "balance" not in bot_inst:
                bot_inst["balance"] = account.get("balance", 0.0)
        # --- end of fix ---

        # --- Normalize price if it is a dictionary ---
        price_val = bot_inst.get("last_price", 0.0)
        if isinstance(price_val, dict):
            bot_inst["last_price"] = price_val.get("bid", price_val.get("ask", 0.0))

        return bot_inst, analysis

    if "bot" in payload and "analysis" in payload:
        bot_inst = payload["bot"]
        analysis = payload["analysis"]
        price_val = bot_inst.get("last_price", 0.0)
        if isinstance(price_val, dict):
            bot_inst["last_price"] = price_val.get("bid", price_val.get("ask", 0.0))
        return bot_inst, analysis

    bot_inst = {}
    analysis = {}
    try:
        account = payload.get("account", {})
        if isinstance(account, dict):
            bot_inst["account"] = account
            bot_inst["equity"] = account.get("equity", 0.0)
            bot_inst["balance"] = account.get("balance", 0.0)
            bot_inst["account_login"] = account.get("login", "--")
            bot_inst["account_server"] = account.get("server", "--")
        risk = payload.get("risk", {})
        if isinstance(risk, dict):
            bot_inst["risk"] = risk
        
        price_val = payload.get("price", 0.0)
        if isinstance(price_val, dict):
            bot_inst["last_price"] = price_val.get("bid", price_val.get("ask", 0.0))
        else:
            bot_inst["last_price"] = price_val

        bot_inst["current_session"] = payload.get("session", "ASIAN")
        
        # Positions normalization:
        positions_raw = payload.get("positions", None)
        if positions_raw is not None:
            if isinstance(positions_raw, dict):
                bot_pos = positions_raw.get("bot", [])
                manual_pos = positions_raw.get("manual", [])
                if not isinstance(bot_pos, list): bot_pos = []
                if not isinstance(manual_pos, list): manual_pos = []
                bot_inst["open_positions"] = bot_pos + manual_pos
            else:
                bot_inst["open_positions"] = positions_raw if isinstance(positions_raw, list) else []
        else:
            bot_inst["open_positions"] = None

        if "closed_trades" in payload:
            bot_inst["closed_trades"] = payload.get("closed_trades", [])
        else:
            bot_inst["closed_trades"] = None

        if "chart_data" in payload:
            bot_inst["chart_data"]  = payload.get("chart_data", [])[-300:]
        else:
            bot_inst["chart_data"] = None

        bias = payload.get("bias", {})
        if isinstance(bias, dict) and bias:
            analysis["market_structure"] = {
                "current_trend": bias.get("current", bias.get("d1", "NEUTRAL")),
                "d1_bias":       bias.get("d1", "NEUTRAL"),
                "h4_bias":       bias.get("h4", "NEUTRAL"),
            }
        elif "bias" in payload:
            analysis["market_structure"] = {
                "current_trend": "NEUTRAL", "d1_bias": "NEUTRAL", "h4_bias": "NEUTRAL"
            }
        else:
            analysis["market_structure"] = None

        signal = payload.get("signal", {})
        if isinstance(signal, dict) and signal:
            analysis["signal_engine"] = {
                "action":       signal.get("action", "NO_TRADE"),
                "direction":    signal.get("direction", "NEUTRAL"),
                "confidence":   signal.get("confidence", 0),
                "reason":       signal.get("reason", "--"),
                "reason_code":  signal.get("reason_code"),
                "entry_price":  signal.get("entry_price"),
                "sl_price":     signal.get("sl_price"),
                "tp_price":     signal.get("tp_price"),
                "gates":        signal.get("gates", {}),
            }
        elif "signal" in payload:
            analysis["signal_engine"] = {
                "action": "NO_TRADE", "direction": "NEUTRAL",
                "confidence": 0, "reason": "--",
                "reason_code": None, "gates": {},
            }
        else:
            analysis["signal_engine"] = None

        if "poi_overlays" in payload:
            analysis["poi_overlays"] = payload.get("poi_overlays", [])
        else:
            analysis["poi_overlays"] = None

        if "chart_objects" in payload:
            analysis["chart_objects"] = payload.get("chart_objects", {})
        else:
            analysis["chart_objects"] = None

        if "news_items" in payload:
            bot_inst["news_items"] = payload.get("news_items", [])
        else:
            bot_inst["news_items"] = None

        if "news_time" in payload:
            bot_inst["news_time"] = payload.get("news_time", "--")
        else:
            bot_inst["news_time"] = None

    except Exception as e:
        print(f"⚠️ Normalization error: {e}")
        traceback.print_exc()
    return bot_inst, analysis

def update_bot_state_v2(symbol, bot_instance, analysis_data):
    global bot_states, last_webhook_timestamp
    last_webhook_timestamp = datetime.now()
    state = get_symbol_state(symbol)
    pnl_tracker = pnl_trackers[symbol]
    closed_trades_tracker = closed_trades_trackers[symbol]
    try:
        incoming_equity = get_val(bot_instance, "equity", None)
        equity = float(incoming_equity) if incoming_equity is not None else state.get("equity", 0.0)

        incoming_balance = get_val(bot_instance, "balance", None)
        balance = float(incoming_balance) if incoming_balance is not None else state.get("balance", 0.0)

        incoming_login = get_val(bot_instance, "account_login", None)
        account_login = str(incoming_login) if incoming_login is not None else state.get("account_login", "--")

        incoming_server = get_val(bot_instance, "account_server", None)
        account_server = str(incoming_server) if incoming_server is not None else state.get("account_server", "--")

        incoming_price = get_val(bot_instance, "last_price", None)
        current_price = float(incoming_price) if incoming_price is not None else state.get("price", 0.0)

        if math.isnan(equity):        equity = state.get("equity", 0.0)
        if math.isnan(balance):       balance = state.get("balance", 0.0)
        if math.isnan(current_price): current_price = state.get("price", 0.0)
    except Exception as e:
        print(f"⚠️ Account parsing error for {symbol}: {e}")
        equity = state.get("equity", 0.0)
        balance = state.get("balance", 0.0)
        current_price = state.get("price", 0.0)
        account_login = state.get("account_login", "--")
        account_server = state.get("account_server", "--")

    open_pnl = 0.0
    formatted_trades = []
    try:
        positions = get_val(bot_instance, "open_positions", None)
        if positions is not None:
            if not isinstance(positions, list): positions = []
            for p in positions:
                profit_raw = get_val(p, "profit", None)
                if profit_raw is None: profit_raw = get_val(p, "pnl", None)
                profit = parse_profit(profit_raw)
                commission = parse_profit(get_val(p, "commission", 0.0))
                swap = parse_profit(get_val(p, "swap", 0.0))
                net_profit = profit + commission + swap

                entry  = parse_profit(get_val(p, "price", get_val(p, "entry_price", 0.0)))
                lot    = parse_profit(get_val(p, "lot_size", get_val(p, "volume", 0.0)))
                signal = get_val(p, "signal", get_val(p, "type", "N/A"))
                if (profit == 0.0) and (current_price > 0 and entry > 0 and lot > 0):
                    try:
                        contract_size = float(get_val(p, "contract_size", 100.0))
                        profit = (current_price - entry) * lot * contract_size if str(signal).upper() == "BUY" else (entry - current_price) * lot * contract_size
                        net_profit = profit + commission + swap
                    except Exception:
                        pass
                open_pnl += float(net_profit)
                formatted_trades.append({
                    "id":       str(get_val(p, "ticket", get_val(p, "id", "000")))[:12],
                    "symbol":   get_val(p, "symbol", symbol),
                    "type":     str(signal).upper(),
                    "lot_size": round(float(lot or 0.0), 3),
                    "volume":   round(float(lot or 0.0), 3),
                    "entry":    round(float(entry or 0.0), 5) if entry else 0.0,
                    "price":    round(float(entry or 0.0), 5) if entry else 0.0,
                    "pnl":      round(float(net_profit), 2),
                    "tp":       get_val(p, "tp", 0),
                    "sl":       get_val(p, "sl", 0),
                })
        else:
            formatted_trades = state.get("trades", [])
            open_pnl = state.get("open_pnl", 0.0)
    except Exception as e:
        print(f"⚠️ Positions parsing error for {symbol}: {e}")
        formatted_trades = state.get("trades", [])
        open_pnl = state.get("open_pnl", 0.0)

    formatted_closed = []
    closed = get_val(bot_instance, "closed_trades", None)
    if closed is not None:
        try:
            if not isinstance(closed, list): closed = []
            for ct in closed:
                profit_raw = None
                for k in ("profit", "pnl", "profit_usd", "deal_profit"):
                    profit_raw = get_val(ct, k, None)
                    if profit_raw is not None: break
                profit = parse_profit(profit_raw)
                commission = parse_profit(get_val(ct, "commission", 0.0))
                swap = parse_profit(get_val(ct, "swap", 0.0))
                net_profit = profit + commission + swap

                formatted_closed.append({
                    "id":     str(get_val(ct, "ticket", get_val(ct, "id", "000")))[:12],
                    "symbol": get_val(ct, "symbol", symbol),
                    "type":   str(get_val(ct, "signal", get_val(ct, "type", "N/A"))).upper(),
                    "entry":  round(float(get_val(ct, "entry_price", get_val(ct, "price", 0.0))), 5),
                    "exit":   round(float(get_val(ct, "close_price", get_val(ct, "exit", 0.0))), 5),
                    "pnl":    round(float(net_profit), 2),
                })
                if abs(net_profit) > 0.01:
                    when = None
                    ts = get_val(ct, "time", None) or get_val(ct, "close_time", None)
                    if ts:
                        try:
                            if isinstance(ts, str):
                                try:    when = datetime.fromisoformat(ts)
                                except:
                                    try: when = datetime.strptime(ts, "%Y.%m.%d %H:%M:%S")
                                    except: when = None
                            elif isinstance(ts, (int, float)):
                                when = datetime.fromtimestamp(float(ts)/1000.0 if ts > 1e12 else float(ts))
                        except Exception: when = None
                    try:
                        tid = get_val(ct, "ticket", get_val(ct, "id", None))
                        pnl_tracker.add_closed_trade(float(net_profit), when=when, ticket=str(tid) if tid else None)
                    except Exception: pass
        except Exception as e:
            print(f"⚠️ Closed trades error for {symbol}: {e}")

        try:
            closed_trades_tracker.add_trades(formatted_closed)
        except Exception as e:
            print(f"⚠️ Failed to track closed trades for {symbol}: {e}")

    try:
        market_struct = get_val(analysis_data, "market_structure", None)
        if market_struct is not None:
            market_structure_to_save = market_struct.get("current_trend", "NEUTRAL")
            d1_bias_to_save = market_struct.get("d1_bias", "NEUTRAL")
            h4_bias_to_save = market_struct.get("h4_bias", "NEUTRAL")
        else:
            market_structure_to_save = state.get("market_structure", "NEUTRAL")
            d1_bias_to_save = state.get("d1_bias", "NEUTRAL")
            h4_bias_to_save = state.get("h4_bias", "NEUTRAL")

        signal_eng = get_val(analysis_data, "signal_engine", None)
        if signal_eng is not None:
            signal_engine_to_save = {
                "action":       signal_eng.get("action", "NO_TRADE"),
                "direction":    signal_eng.get("direction", "NEUTRAL"),
                "confidence":   int(signal_eng.get("confidence", signal_eng.get("confidence_score", 0))),
                "reason":       signal_eng.get("reason", "--"),
                "reason_code":  signal_eng.get("reason_code"),
                "entry_price":  signal_eng.get("entry_price"),
                "sl_price":     signal_eng.get("sl_price"),
                "tp_price":     signal_eng.get("tp_price"),
                "gates":        signal_eng.get("gates", {}),
            }
        else:
            signal_engine_to_save = state.get("signal_engine", {
                "action": "NO_TRADE", "direction": "NEUTRAL",
                "confidence": 0, "reason": "--", "gates": {}
            })

        poi_overlays_incoming = get_val(analysis_data, "poi_overlays", None)
        if poi_overlays_incoming is not None:
            poi_overlays_to_save = poi_overlays_incoming
        else:
            poi_overlays_to_save = state.get("poi_overlays", [])

        chart_objects_incoming = get_val(analysis_data, "chart_objects", None)
        if chart_objects_incoming is not None:
            chart_objects_to_save = chart_objects_incoming
        else:
            chart_objects_to_save = state.get("chart_objects", {})

        chart_data_incoming = get_val(bot_instance, "chart_data", None)
        if chart_data_incoming is not None and len(chart_data_incoming) > 0:
            chart_data_to_save = chart_data_incoming[-300:]
        else:
            chart_data_to_save = state.get("chart_data", [])

        news_items_incoming = get_val(bot_instance, "news_items", None)
        if news_items_incoming is not None:
            news_items_to_save = news_items_incoming
        else:
            news_items_to_save = state.get("news_items", [])

        news_time_incoming = get_val(bot_instance, "news_time", None)
        if news_time_incoming is not None:
            news_time_to_save = news_time_incoming
        else:
            news_time_to_save = state.get("news_time", "--")

        current_tf = state.get("current_timeframe", "M15")

        state.update({
            "symbol":           symbol,
            "webhook_age_seconds": 0,
            "account_login":    account_login,
            "account_server":   account_server,
            "account_name":     get_val(bot_instance, "account_name", state.get("account_name", f"REAL-{symbol}")),
            "equity":           round(float(equity), 2),
            "balance":          round(float(balance), 2),
            "pnl_daily":        round(pnl_tracker.get_daily() + open_pnl, 2),
            "pnl_today":        round(pnl_tracker.get_daily() + open_pnl, 2),
            "pnl_week":         round(pnl_tracker.get_weekly() + open_pnl, 2),
            "pnl_total":        round(pnl_tracker.get_total() + open_pnl, 2),
            "open_pnl":         round(open_pnl, 2),
            "price":            current_price,
            "market_structure": market_structure_to_save,
            "session":          get_val(bot_instance, "current_session", state.get("session", "ASIAN")),
            "trades":           formatted_trades,
            "closed_trades":    closed_trades_tracker.get_all()[-50:],
            "chart_overlays":   {
                "levels": {
                    "pdh": poi_overlays_to_save,
                    "pdl": chart_objects_to_save,
                }
            },
            "poi_overlays":   poi_overlays_to_save,
            "chart_objects":  chart_objects_to_save,
            "chart_data":     chart_data_to_save,
            "trading":        state.get("trading", True),
            "d1_bias":        d1_bias_to_save,
            "h4_bias":        h4_bias_to_save,
            "news_items":     news_items_to_save,
            "news_time":      news_time_to_save,
            "signal_engine":  signal_engine_to_save,
            "current_timeframe": current_tf,
        })
    except Exception as e:
        print(f"⚠️ State update error for {symbol}: {e}")
        traceback.print_exc()

# ═══════════════════════════════════════════════════════════════════════════
# /webhook
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/webhook")
async def webhook(payload: dict = Body(...)):
    try:
        symbol = payload.get("symbol")
        if not symbol:
            bot_inst_data = payload.get("bot_instance") or payload.get("bot")
            if isinstance(bot_inst_data, dict):
                symbol = bot_inst_data.get("symbol")
        if not symbol:
            symbol = "XAUUSD"
            
        state = get_symbol_state(symbol)
        
        control_url = payload.get("control_url")
        if not control_url:
            bot_inst_data = payload.get("bot_instance") or payload.get("bot")
            if isinstance(bot_inst_data, dict):
                control_url = bot_inst_data.get("control_url")
        if control_url:
            state["control_url"] = control_url
            
        bot_inst, analysis = normalize_webhook_payload(payload)
        update_bot_state_v2(symbol, bot_inst, analysis)
        
        # ✅ Store smc_map in in-memory state dictionary
        state["smc_map"] = payload.get("smc_map", {})
        
        return {"status": "ok"}
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        traceback.print_exc()
        return {"status": "error", "reason": str(e)}

# ═══════════════════════════════════════════════════════════════════════════
# Bot control endpoints
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/bot/status")
async def bot_status(symbol: str = "XAUUSD"):
    state = get_symbol_state(symbol)
    return {"status": "PAUSED" if not state.get("trading", True) else "ACTIVE"}

@app.post("/bot/pause")
async def pause_bot(symbol: str = "XAUUSD"):
    state = get_symbol_state(symbol)
    state["trading"] = False
    control_url = state.get("control_url")
    if control_url:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{control_url}/control/pause")
            print(f"⏸️ Forwarded pause command to {symbol} bot at {control_url}/control/pause")
        except Exception as e:
            print(f"⚠️ Failed to forward pause to {symbol} bot: {e}")
    else:
        # Fallback to local
        for fallback_url in ("http://localhost:5000/control/pause", "http://localhost:8000/bot/pause"):
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    await client.post(fallback_url)
                print(f"⏸️ Forwarded pause command to fallback: {fallback_url}")
                break
            except Exception: pass
    print(f"⏸️  Bot {symbol} PAUSED via dashboard")
    return {"status": "PAUSED"}

@app.post("/bot/resume")
async def resume_bot(symbol: str = "XAUUSD"):
    state = get_symbol_state(symbol)
    state["trading"] = True
    control_url = state.get("control_url")
    if control_url:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{control_url}/control/resume")
            print(f"▶️ Forwarded resume command to {symbol} bot at {control_url}/control/resume")
        except Exception as e:
            print(f"⚠️ Failed to forward resume to {symbol} bot: {e}")
    else:
        # Fallback to local
        for fallback_url in ("http://localhost:5000/control/resume", "http://localhost:8000/bot/resume"):
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    await client.post(fallback_url)
                print(f"▶️ Forwarded resume command to fallback: {fallback_url}")
                break
            except Exception: pass
    print(f"▶️  Bot {symbol} RESUMED via dashboard")
    return {"status": "ACTIVE"}

@app.get('/bot/logs')
def get_logs():
    try:
        log_path = os.environ.get("BOT_LOG_PATH", "/var/log/tradingbot/bot.log")
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return {'logs': lines[-50:]}
    except Exception as e:
        return {'error': str(e), 'log_path': os.environ.get("BOT_LOG_PATH", "/var/log/tradingbot/bot.log")}

def is_trader_running():
    """
    Detect if trader/main.py process is actually alive.
    """
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            cmdline = proc.info.get('cmdline') or []

            joined = ' '.join(cmdline).lower()

            if 'trader/main.py' in joined:
                return True

            if 'apps.trader.main' in joined:
                return True

        return False

    except Exception:
        return False
# ═══════════════════════════════════════════════════════════════════════════
# /health — unchanged
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/health")
async def health_check(symbol: str = None):
    now = datetime.now()
    if last_webhook_timestamp is None:
        age = 99999
        mt5_status = "down"
    else:
        age = (now - last_webhook_timestamp).total_seconds()
        if age < 90:
            mt5_status = "ok"
        elif age < 180:
            mt5_status = "warning"
        else:
            mt5_status = "down"
            
    if not symbol:
        symbol = list(bot_states.keys())[0] if bot_states else "XAUUSD"
        
    state = bot_states.get(symbol, {})
    chart_len = len(state.get("chart_data", []) or [])
    se = state.get("signal_engine", {})
    trader_active = is_trader_running() or (age < 90)
    strategy_active = (se.get("action") is not None) and (mt5_status != "down")
    return {
        "mt5_connected":       mt5_status,
        "websocket_active":    len(active_connections) > 0,
        "active_ws_clients":   len(active_connections),
        "trader_alive":        trader_active,
        "strategy_engine":     strategy_active,
        "data_feed":           chart_len > 0,
        "data_feed_candles":   chart_len,
        "vps_uptime_seconds":  int(_time.time() - _SERVER_START_TIME),
        "webhook_age_seconds": round(age),
        "last_price":          state.get("price", 0.0),
        "open_positions":      len(state.get("trades", []) or []),
        "bot_paused":          not state.get("trading", True),
        "server_time":         now.isoformat(),
        "symbol":              symbol
    }

# ═══════════════════════════════════════════════════════════════════════════
# /api/state
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/api/state")
async def api_state(symbol: str = None):
    if not bot_states:
        return {"status": "no_data", "message": "No webhook received yet"}
    try:
        if symbol:
            state = bot_states.get(symbol)
            if not state:
                return {"status": "no_data", "message": f"No state for symbol {symbol}"}
            safe = json.loads(json.dumps(state, default=str).replace("NaN", "null"))
            safe["smc_map"] = state.get("smc_map", {})
        else:
            safe = json.loads(json.dumps(bot_states, default=str).replace("NaN", "null"))
            for k, v in safe.items():
                orig_state = bot_states.get(k, {})
                v["smc_map"] = orig_state.get("smc_map", {})
        safe["_source"]        = "http_snapshot"
        safe["_snapshot_time"] = datetime.now().isoformat()
        return safe
    except Exception as e:
        return {"status": "error", "reason": str(e)}

# ═══════════════════════════════════════════════════════════════════════════
# /ws WebSocket endpoint — ENHANCED: timeframe change handler
# ═══════════════════════════════════════════════════════════════════════════
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    client_info = "unknown"
    try:
        client_info = f"{websocket.client}" if hasattr(websocket, "client") else "unknown"
        print("🔌 WS CONNECTED:", client_info)

        if bot_states:
            try:
                safe_json = json.dumps(bot_states, default=str).replace("NaN", "null")
                await websocket.send_text(safe_json)
                print(f"📤 Initial states sent: {len(bot_states)} symbols")
            except Exception:
                pass

        while True:
            try:
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                    action = data.get("action")
                    symbol = data.get("symbol")
                    if not symbol:
                        symbol = "XAUUSD"

                    if action == "toggle_bot":
                        status = data.get("status", "ON")
                        state = get_symbol_state(symbol)
                        state["trading"] = (status == "ON")
                        print(f"🎛️  Bot {symbol} {'RESUMED' if state['trading'] else 'PAUSED'} via WS")
                        control_url = state.get("control_url")
                        if control_url:
                            try:
                                async with httpx.AsyncClient(timeout=2.0) as client:
                                    path = "/bot/resume" if status == "ON" else "/bot/pause"
                                    await client.post(f"{control_url}{path}")
                                print(f"✅ Forwarded {action} to {symbol} bot at {control_url}")
                            except Exception as e:
                                print(f"⚠️ Could not forward {action} to {symbol} bot: {e}")

                    elif action == "change_tf":
                        new_tf = data.get("tf", "M15")
                        print(f"📊 TF change requested for {symbol}: {new_tf}")
                        state = get_symbol_state(symbol)
                        state["current_timeframe"] = new_tf
                        control_url = state.get("control_url")
                        if control_url:
                            try:
                                async with httpx.AsyncClient(timeout=2.0) as client:
                                    await client.post(f"{control_url}/set_timeframe", json={"timeframe": new_tf})
                                print(f"✅ Forwarded timeframe {new_tf} to {symbol} bot at {control_url}")
                            except Exception as e:
                                print(f"⚠️ Could not forward timeframe to {symbol} bot: {e}")

                    elif action == "ping":
                        await websocket.send_text(
                            json.dumps({"action": "pong", "ts": datetime.now().isoformat()})
                        )
                except json.JSONDecodeError:
                    pass
            except WebSocketDisconnect:
                break
            except Exception:
                break

    finally:
        try:
            active_connections.remove(websocket)
        except Exception:
            pass
        print("🔌 WS DISCONNECTED:", client_info)

# ═══════════════════════════════════════════════════════════════════════════
# Inline HTML fallback (unchanged – kept for safety)
# ═══════════════════════════════════════════════════════════════════════════
def _get_inline_html() -> str:
    return html_content

# ═══════════════════════════════════════════════════════════════════════════════
# HTML/CSS/JS DASHBOARD CONTENT - GUARDEER OS v4.0 (RESPONSIVE + PATCHED JS)
# ═══════════════════════════════════════════════════════════════════════════════
html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>GUARDEER OS v4.0 - Institutional SMC Terminal</title>

    <script src="https://unpkg.com/lightweight-charts@4.0.0/dist/lightweight-charts.standalone.production.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">

    <style>
        :root {
            /* DARK THEME (default) */
            --bg-black: #000000;
            --bg-panel: #060607;
            --bg-panel-header: #0c0c0e;
            --border-color: #16161a;
            --text-main: #d1d1d6;
            --text-muted: #636366;
            --neon-green: #00ff88;
            --neon-red: #ff4d4d;
            --neon-amber: #ff9f0a;
            --neon-blue: #00d4ff;
            --terminal-gold: #c5a880;
        }

        body.light-theme {
            --bg-black: #eef4ec;
            --bg-panel: #f5f7f3;
            --bg-panel-header: #e8ede5;
            --border-color: #d0d8cd;
            --text-main: #1a1a1a;
            --text-muted: #6b7366;
            --neon-green: #008c3a;
            --neon-red: #c92c2c;
            --neon-amber: #b86800;
            --neon-blue: #0066cc;
            --terminal-gold: #8b6f47;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
        }

        body {
            background-color: var(--bg-black);
            color: var(--text-main);
            font-family: 'Rajdhani', sans-serif;
            font-size: 13px;
            letter-spacing: 0.5px;
            overflow-x: hidden;
            padding-bottom: 30px;
            position: relative;
            transition: background-color 0.3s ease, color 0.3s ease;
        }

        /* Subtle scanline overlay */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: repeating-linear-gradient(
                0deg,
                rgba(0, 0, 0, 0.015),
                rgba(0, 0, 0, 0.015) 1px,
                transparent 1px,
                transparent 2px
            );
            pointer-events: none;
            z-index: 9999;
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
        }

        /* Utility Classes */
        .txt-green { color: var(--neon-green) !important; }
        .txt-red { color: var(--neon-red) !important; }
        .txt-amber { color: var(--neon-amber) !important; }
        .txt-muted { color: var(--text-muted) !important; }
        .txt-gold { color: var(--terminal-gold) !important; }

        /* HEADER */
        .terminal-header {
            background-color: var(--bg-black);
            border-bottom: 1px solid var(--border-color);
            display: grid;
            grid-template-columns: 1fr auto 1fr;
            align-items: center;
            padding: 8px 16px;
            min-height: 65px;
            transition: all 0.3s ease;
        }

        @media (max-width: 1024px) {
            .terminal-header {
                grid-template-columns: 1fr;
                gap: 12px;
                padding: 8px 12px;
            }
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 24px;
            flex-wrap: wrap;
        }

        @media (max-width: 768px) {
            .header-left {
                gap: 12px;
            }
        }

        .timezone-block {
            display: flex;
            flex-direction: column;
        }
        .timezone-label {
            font-size: 10px;
            color: var(--text-muted);
            font-weight: 700;
            margin-bottom: 2px;
        }
        .time-row {
            display: flex;
            gap: 16px;
            font-size: 14px;
            font-weight: 600;
        }

        @media (max-width: 768px) {
            .time-row {
                gap: 8px;
                font-size: 12px;
            }
        }

        .header-center {
            text-align: center;
        }
        .header-center h1 {
            font-size: 24px;
            color: var(--neon-green);
            font-weight: 700;
            letter-spacing: 3px;
            line-height: 1.1;
            animation: glow-pulse 3s ease-in-out infinite;
        }

        @media (max-width: 768px) {
            .header-center h1 {
                font-size: 18px;
                letter-spacing: 2px;
            }
        }

        @keyframes glow-pulse {
            0%, 100% { text-shadow: 0 0 10px var(--neon-green), 0 0 20px var(--neon-green); }
            50% { text-shadow: 0 0 5px var(--neon-green); }
        }
        .header-center .subtitle {
            font-size: 10px;
            color: var(--text-main);
            letter-spacing: 1.5px;
            font-weight: 600;
        }

        @media (max-width: 768px) {
            .header-center .subtitle {
                font-size: 9px;
                letter-spacing: 1px;
            }
        }

        .header-right {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 20px;
            flex-wrap: wrap;
        }

        @media (max-width: 768px) {
            .header-right {
                gap: 12px;
                justify-content: flex-start;
            }
        }
        
        .theme-toggle {
            width: 40px;
            height: 24px;
            background: var(--border-color);
            border-radius: 12px;
            border: 1px solid var(--border-color);
            position: relative;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        .theme-toggle.dark::after {
            content: '🌙';
            position: absolute;
            left: 3px;
            top: 2px;
            font-size: 16px;
        }
        .theme-toggle.light::after {
            content: '☀️';
            position: absolute;
            right: 3px;
            top: 2px;
            font-size: 16px;
        }
        
        .control-panel {
            display: flex;
            align-items: center;
            gap: 8px;
            border: 1px solid var(--border-color);
            padding: 4px 8px;
            border-radius: 4px;
            background: #09090b;
            flex-wrap: wrap;
        }
        .control-panel.light-theme-mode {
            background: #f0f3ed;
        }
        .btn-toggle {
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 700;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            cursor: pointer;
            border-radius: 3px;
            transition: all 0.2s ease;
        }
        .btn-toggle.active-on {
            background: rgba(0, 255, 136, 0.1);
            border-color: var(--neon-green);
            color: var(--neon-green);
        }
        .btn-toggle.active-off {
            background: rgba(255, 77, 77, 0.1);
            border-color: var(--neon-red);
            color: var(--neon-red);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--neon-green);
            box-shadow: 0 0 8px var(--neon-green);
            animation: pulse-dot 2s ease-in-out infinite;
        }
        .status-dot.inactive {
            background: var(--neon-red) !important;
            box-shadow: 0 0 8px var(--neon-red) !important;
        }
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .meta-item {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
        }

        @media (max-width: 768px) {
            .meta-item {
                align-items: flex-start;
            }
        }

        .meta-label { font-size: 9px; color: var(--text-muted); font-weight: bold; }
        .meta-val { font-size: 12px; font-weight: 600; }

        /* DASHBOARD LAYOUT */
        .dashboard-container {
            display: flex;
            flex-direction: column;
            gap: 12px;
            padding: 12px;
        }

        .top-row-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 12px;
        }
        @media (max-width: 1400px) {
            .top-row-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        @media (max-width: 900px) {
            .top-row-grid {
                grid-template-columns: 1fr;
            }
        }

        /* PANELS */
        .terminal-panel {
            background-color: var(--bg-panel);
            border: 1px solid var(--border-color);
            border-radius: 4px;
            display: flex;
            flex-direction: column;
            transition: all 0.3s ease;
        }

        .panel-header {
            background-color: var(--bg-panel-header);
            border-bottom: 1px solid var(--border-color);
            padding: 6px 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .panel-title {
            font-size: 11px;
            font-weight: 700;
            color: var(--terminal-gold);
            text-transform: uppercase;
            letter-spacing: 1px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .panel-title::before {
            content: '';
            display: inline-block;
            width: 3px;
            height: 11px;
            background: var(--terminal-gold);
        }

        .panel-body {
            padding: 12px;
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        /* DATA ROWS & LISTS */
        .data-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }
        .data-label { color: var(--text-muted); font-weight: 500; font-size: 12px; }
        .data-value { font-weight: 600; font-size: 13px; }

        /* NEWS FILTER SPECIFIC */
        .filter-tabs {
            display: flex;
            gap: 6px;
            margin-bottom: 8px;
            font-size: 11px;
            align-items: center;
            flex-wrap: wrap;
        }
        .filter-tab {
            padding: 1px 6px;
            border: 1px solid transparent;
            color: var(--text-muted);
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .filter-tab.active {
            border: 1px solid var(--neon-red);
            color: var(--neon-red);
            background: rgba(255, 77, 77, 0.05);
        }

        /* NARRATIVE ENGINE SPECIFIC */
        .progress-container {
            display: flex;
            align-items: center;
            gap: 8px;
            width: 100%;
        }
        .progress-bar-bg {
            background: #141416;
            height: 8px;
            flex-grow: 1;
            border-radius: 2px;
            overflow: hidden;
        }
        .light-theme .progress-bar-bg {
            background: #ddd;
        }
        .progress-bar-fill {
            background: var(--neon-green);
            height: 100%;
            transition: width 0.4s ease;
        }
        .narrative-box {
            background: rgba(197, 168, 128, 0.04);
            border: 1px dashed rgba(197, 168, 128, 0.15);
            padding: 6px 8px;
            font-size: 11px;
            color: #ebdcb9;
            margin-top: 8px;
            line-height: 1.3;
        }

        /* SYSTEM STATUS SPECIFIC */
        .status-indicator-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            font-size: 12px;
        }
        .status-dot-group {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        /* CHART REGION */
        .chart-controls-bar {
            background: var(--bg-panel-header);
            border-bottom: 1px solid var(--border-color);
            padding: 6px 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }

        @media (max-width: 768px) {
            .chart-controls-bar {
                flex-direction: column;
                align-items: flex-start;
            }
        }

        .chart-meta-left {
            display: flex;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }

        @media (max-width: 768px) {
            .chart-meta-left {
                gap: 8px;
            }
        }

        .chart-symbol { font-size: 14px; font-weight: 700; color: var(--text-main); }

        @media (max-width: 768px) {
            .chart-symbol {
                font-size: 12px;
            }
        }

        .badge {
            font-size: 11px;
            padding: 1px 6px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .badge-green { background: rgba(0,255,136,0.1); border: 1px solid var(--neon-green); color: var(--neon-green); }
        .badge-gold { background: rgba(197,168,128,0.1); border: 1px solid var(--terminal-gold); color: var(--terminal-gold); }
        
        .timeframe-selector {
            display: flex;
            gap: 2px;
            flex-wrap: wrap;
        }
        .tf-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 2px 6px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.1s ease;
        }
        .tf-btn.active {
            background: #1c1c1f;
            color: #fff;
            border-radius: 2px;
        }
        .light-theme .tf-btn.active {
            background: #d0d8cd;
            color: var(--text-main);
        }

        .chart-container-shell {
            height: 380px;
            position: relative;
            background-color: #050505;
        }

        @media (max-width: 768px) {
            .chart-container-shell {
                height: 280px;
            }
        }

        .light-theme .chart-container-shell {
            background-color: #fafaf8;
        }

        /* TABLES WORKSPACE FOR LOWER TIERS */
        .bottom-row-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 12px;
        }
        @media (max-width: 1200px) {
            .bottom-row-grid {
                grid-template-columns: 1fr;
            }
        }

        .table-wrapper {
            width: 100%;
            overflow-x: auto;
        }
        .terminal-table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 12px;
        }
        .terminal-table th {
            background: var(--bg-panel-header);
            color: var(--text-muted);
            font-weight: 600;
            padding: 6px 8px;
            border-bottom: 1px solid var(--border-color);
            font-size: 10px;
            text-transform: uppercase;
        }
        .terminal-table td {
            padding: 6px 8px;
            border-bottom: 1px solid rgba(255,255,255,0.02);
            white-space: nowrap;
        }
        .light-theme .terminal-table td {
            border-bottom: 1px solid rgba(0,0,0,0.05);
        }
        .table-footer {
            padding: 6px 8px;
            text-align: right;
            border-top: 1px solid var(--border-color);
            font-weight: 700;
            font-size: 12px;
        }

        /* LOWER RIGHT SPLIT: ACTIONS & GATES */
        .gates-split-container {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            height: 100%;
        }
        @media (max-width: 600px) {
            .gates-split-container {
                grid-template-columns: 1fr;
            }
        }
        .gate-list-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            padding: 3px 0;
            border-bottom: 1px solid rgba(255,255,255,0.02);
        }

        /* FOOTER STATUS STRIP */
        .terminal-footer {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            background: #020202;
            border-top: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            padding: 4px 16px;
            font-size: 10px;
            color: var(--text-muted);
            font-weight: 600;
            z-index: 1000;
            flex-wrap: wrap;
            gap: 20px;
            transition: all 0.3s ease;
            overflow-x: auto;
        }

        @media (max-width: 768px) {
            .terminal-footer {
                padding: 4px 8px;
                gap: 8px;
                font-size: 9px;
            }
        }

        .light-theme .terminal-footer {
            background: #f5f7f3;
        }
        .footer-left-meta span, .footer-right-meta span {
            margin-right: 14px;
        }

        @media (max-width: 768px) {
            .footer-left-meta span, .footer-right-meta span {
                margin-right: 8px;
            }
        }
    </style>
</head>

<body x-data="DS()">

    <header class="terminal-header">
        <div class="header-left">
            <div class="timezone-block">
                <span class="timezone-label">TIMEZONE</span>
                <div class="time-row mono">
                    <div><span class="txt-amber">IST</span> <span id="ist-clock">--:--:--</span></div>
                    <div><span class="txt-muted">UTC</span> <span id="utc-clock" class="txt-main">--:--:--</span></div>
                </div>
            </div>
        </div>

        <div class="header-center">
            <h1>GUARDEER OS</h1>
            <div class="subtitle">INSTITUTIONAL SMC COMMAND TERMINAL</div>
        </div>

        <div class="header-right">
            <button class="theme-toggle" :class="theme_mode" @click="toggleTheme()" title="Toggle Dark/Light Mode"></button>
            
            <div class="control-panel" :class="theme_mode === 'light' ? 'light-theme-mode' : ''">
                <span class="meta-label" style="margin-right:4px;">CONTROL</span>
                <span style="font-size:11px; font-weight:700; color:var(--text-main)">BOT STATUS</span>
                <span class="status-dot" :class="bot_status ? '' : 'inactive'" style="margin: 0 4px;"></span>
                <span :class="bot_status ? 'txt-green' : 'txt-red'" style="font-size:11px; font-weight:700; margin-right:6px;" x-text="bot_status ? 'RUNNING' : 'STOPPED'">RUNNING</span>
                
                <button class="btn-toggle" :class="bot_status ? 'active-on' : ''" @click="toggleBot(true)">ON</button>
                <button class="btn-toggle" :class="!bot_status ? 'active-off' : ''" @click="toggleBot(false)">OFF</button>
            </div>
            <div class="meta-item">
                <span class="meta-label">SERVER</span>
                <span class="meta-val mono">VPS-01 <span class="txt-muted" style="font-size:10px;">v4.1</span></span>
            </div>
            <div class="meta-item">
                <span class="meta-label">ACCOUNT</span>
                <span class="meta-val mono txt-amber" x-text="account_name || 'REAL-01'">REAL-01</span>
            </div>
        </div>
    </header>

    <main class="dashboard-container">
        
        <section class="top-row-grid">
            
            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Account Overview</div>
                </div>
                <div class="panel-body">
                    <div class="data-row"><span class="data-label">LOGIN</span><span class="data-value mono" x-text="account_login || '--'">--</span></div>
                    <div class="data-row"><span class="data-label">SERVER</span><span class="data-value mono" x-text="account_server || '--'">--</span></div>
                    <div class="data-row"><span class="data-label">EQUITY</span><span class="data-value mono txt-green" x-text="fmt(equity)">$0.00</span></div>
                    <div class="data-row"><span class="data-label">BALANCE</span><span class="data-value mono txt-green" x-text="fmt(balance)">$0.00</span></div>
                    <div class="data-row">
                        <span class="data-label">DAILY P&L</span>
                        <span class="data-value mono" :class="pnl_today >= 0 ? 'txt-green' : 'txt-red'" x-text="fmt(pnl_today)">$0.00</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">DAILY %</span>
                        <span class="data-value mono" :class="pnl_today_pct >= 0 ? 'txt-green' : 'txt-red'" x-text="(pnl_today_pct >= 0 ? '+' : '') + pnl_today_pct.toFixed(2) + '%'">+0.00%</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">WEEKLY P&L</span>
                        <span class="data-value mono" :class="pnl_week >= 0 ? 'txt-green' : 'txt-red'" x-text="fmt(pnl_week)">$0.00</span>
                    </div>
                    <div class="data-row" style="margin-bottom:0;">
                        <span class="data-label">OVERALL P&L</span>
                        <span class="data-value mono" :class="pnl_total >= 0 ? 'txt-green' : 'txt-red'" x-text="fmt(pnl_total)">$0.00</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">News Filter</div>
                </div>
                <div class="panel-body">
                    <div class="filter-tabs">
                        <span class="txt-muted" style="font-size:10px; font-weight:700;">IMPACT:</span>
                        <span class="filter-tab" :class="news_filter === 'ALL' ? 'active' : ''" @click="news_filter='ALL'">ALL</span>
                        <span class="filter-tab" :class="news_filter === 'HIGH' ? 'active' : ''" @click="news_filter='HIGH'">HIGH</span>
                        <span class="filter-tab" :class="news_filter === 'MED' ? 'active' : ''" @click="news_filter='MED'">MED</span>
                        <span class="filter-tab" :class="news_filter === 'LOW' ? 'active' : ''" @click="news_filter='LOW'">LOW</span>
                    </div>
                    <div style="flex-grow:1; display:flex; flex-direction:column; gap:4px;">
                        <template x-for="item in getFilteredNews()">
                            <div class="data-row mono" style="font-size:12px; margin-bottom:2px;">
                                <span class="txt-muted" x-text="item.time">00:00</span>
                                <span :class="item.impact === 'HIGH' ? 'txt-red' : item.impact === 'MED' ? 'txt-amber' : 'txt-muted'" style="width:35px;font-weight:700" x-text="item.impact">HIGH</span>
                                <span class="txt-main" style="flex-grow:1; text-align:right" x-text="item.title">Event Loading...</span>
                            </div>
                        </template>
                        <div x-show="getFilteredNews().length === 0" class="txt-muted mono" style="font-size:11px; text-align:center; margin-top:20px;">
                            NO EVENTS (<span x-text="news_filter"></span>)
                        </div>
                    </div>
                    <div class="data-row mono" style="margin-top:6px; margin-bottom:0; font-size:11px;">
                        <span class="txt-muted">NEXT EVENT:</span>
                        <span class="txt-amber" x-text="news.time">COUNTING DOWN</span>
                    </div>
                </div>
            </div>
            
            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">System Status</div>
                </div>
                <div class="panel-body" style="justify-content: flex-start;">
                    <div class="status-indicator-row"><span>MT5 CONNECTED</span><div class="status-dot-group"><div class="status-dot" :class="(ws_connected && webhook_age_seconds < 60) ? '' : 'inactive'"></div><span class="mono" :class="(ws_connected && webhook_age_seconds < 60) ? 'txt-green' : 'txt-red'" x-text="(ws_connected && webhook_age_seconds < 60) ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row"><span>DATA FETCHING</span><div class="status-dot-group"><div class="status-dot" :class="(ws_connected && webhook_age_seconds < 60) ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row"><span>VPS RECEIVING DATA</span><div class="status-dot-group"><div class="status-dot" :class="(ws_connected && webhook_age_seconds < 60) ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row"><span>DASHBOARD ANALYSIS</span><div class="status-dot-group"><div class="status-dot" :class="(ws_connected && webhook_age_seconds < 60) ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'LIVE' : 'DOWN'">DOWN</span></div></div>
                    <div class="status-indicator-row" style="margin-bottom:8px;"><span>SYNC STATUS</span><div class="status-dot-group"><div class="status-dot" :class="(ws_connected && webhook_age_seconds < 60) ? '' : 'inactive'"></div><span class="mono" :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'WORKING' : 'HALTED'">HALTED</span></div></div>
                    <div class="data-row mono" style="margin-top:auto; margin-bottom:0; border-top:1px solid rgba(255,255,255,0.03); padding-top:4px; font-size:11px;">
                        <span class="txt-muted">LAST UPDATE:</span><span class="txt-main" id="last-update-ts">--:--:--</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Narrative Engine</div>
                </div>
                <div class="panel-body">
                    <div style="display:grid; grid-template-columns: 1fr 1.2fr; gap:12px; width:100%;">
                        <div>
                            <div class="data-row" style="margin-bottom:4px;">
                                <span class="data-label">D1 BIAS</span>
                                <span class="data-value" :class="d1_bias === 'BULLISH' ? 'txt-green' : d1_bias === 'BEARISH' ? 'txt-red' : 'txt-muted'" x-text="d1_bias">--</span>
                            </div>
                            <div class="data-row" style="margin-bottom:4px;">
                                <span class="data-label">H4 BIAS</span>
                                <span class="data-value" :class="h4_bias === 'BULLISH' ? 'txt-green' : h4_bias === 'BEARISH' ? 'txt-red' : 'txt-muted'" x-text="h4_bias">--</span>
                            </div>
                            <div class="data-row" style="margin-bottom:0;">
                                <span class="data-label">STRUCTURE</span>
                                <span class="data-value" :class="structure === 'BULLISH' ? 'txt-green' : structure === 'BEARISH' ? 'txt-red' : 'txt-muted'" x-text="structure">--</span>
                            </div>
                        </div>
                        <div style="display:flex; flex-direction:column; justify-content:center;">
                            <div class="data-row" style="margin-bottom:2px;">
                                <span class="data-label" style="font-size:10px;">SMC CONFIDENCE</span>
                                <span class="data-value mono txt-green" style="font-size:12px;" x-text="signal_engine.confidence + '%'">0%</span>
                            </div>
                            <div class="progress-container" style="margin-bottom:6px;">
                                <div class="progress-bar-bg">
                                    <div class="progress-bar-fill" :style="`width: ${signal_engine.confidence}%`" style="width: 0%;"></div>
                                </div>
                            </div>
                            <div class="data-row" style="margin-bottom:0;">
                                <span class="data-label" style="font-size:10px;">GATES PASSED</span>
                                <span class="data-value mono" style="font-size:12px;" x-text="getPassedGatesCount() + ' / 8'">0 / 8</span>
                            </div>
                        </div>
                    </div>
                    <div class="narrative-box">
                        <span class="txt-gold" style="font-weight:700">NARRATIVE:</span> 
                        <span x-text="signal_engine.reason">Parsing stream telemetry...</span>
                    </div>
                </div>
            </div>

            
        </section>

        <section class="terminal-panel">
            <div class="chart-controls-bar">
                <div class="chart-meta-left">
                    <span class="chart-symbol mono">XAUUSD <span class="txt-muted" style="font-size:11px;">•</span> <span x-text="current_tf">M15</span></span>
                    <div class="badge badge-green">LIVE</div>
                    <div class="badge badge-green" style="font-size:10px;" x-text="'STRUCTURE: ' + structure">STRUCTURE: --</div>
                    <div class="badge badge-gold" style="font-size:10px;" x-text="'SESSION: ' + session">SESSION: --</div>
                </div>
                
                <div class="timeframe-selector mono">
                    <template x-for="tf in ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']">
                        <button class="tf-btn" :class="current_tf === tf ? 'active' : ''" @click="changeTimeframe(tf)" x-text="tf"></button>
                    </template>
                </div>
                <div class="txt-muted mono" style="font-size:11px;">CHART CONTROLS</div>
            </div>
            <div class="chart-container-shell">
                <div id="chart-container" style="width: 100%; height: 100%;"></div>
            </div>
        </section>

        <section class="bottom-row-grid">
            
            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Open Positions (<span x-text="trades.length">0</span>)</div>
                </div>
                <div class="panel-body" style="padding:0;">
                    <div class="table-wrapper">
                        <table class="terminal-table mono">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>TYPE</th>
                                    <th>ENTRY</th>
                                    <th>SL</th>
                                    <th>TP</th>
                                    <th>VOL</th>
                                    <th>P&L</th>
                                    <th>STATUS</th>
                                </tr>
                            </thead>
                            <tbody>
                                <template x-for="t in trades">
                                    <tr>
                                        <td style="font-weight:700;" x-text="t.symbol || 'XAUUSD'">XAUUSD</td>
                                        <td :class="t.type === 'BUY' ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="t.type">BUY</td>
                                        <td x-text="t.entry">0.00</td>
                                        <td class="txt-muted" x-text="t.sl">0.00</td>
                                        <td class="txt-muted" x-text="t.tp">0.00</td>
                                        <td x-text="t.volume || t.lot_size || '0.10'">0.10</td>
                                        <td :class="t.pnl >= 0 ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="fmt(t.pnl)">$0.00</td>
                                        <td class="txt-green" style="font-size:11px;">OPEN</td>
                                    </tr>
                                </template>
                                <tr x-show="trades.length === 0">
                                    <td colspan="8" class="txt-muted" style="text-align:center; padding: 20px 0;">NO ACTIVE TRADES DETECTED</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="table-footer mono" :class="getOpenTotalPnl() >= 0 ? 'txt-green' : 'txt-red'">
                        TOTAL P&L: <span x-text="fmt(getOpenTotalPnl())">$0.00</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Closed Positions (<span x-text="closed_trades.length">0</span>)</div>
                </div>
                <div class="panel-body" style="padding:0;">
                    <div class="table-wrapper">
                        <table class="terminal-table mono">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>TYPE</th>
                                    <th>ENTRY</th>
                                    <th>EXIT</th>
                                    <th>P&L</th>
                                </tr>
                            </thead>
                            <tbody>
                                <template x-for="ct in closed_trades">
                                    <tr>
                                        <td style="font-weight:700;" x-text="ct.symbol">--</td>
                                        <td :class="ct.type === 'BUY' ? 'txt-green' : 'txt-red'" x-text="ct.type">--</td>
                                        <td x-text="ct.entry">0.00</td>
                                        <td x-text="ct.exit">0.00</td>
                                        <td :class="ct.pnl >= 0 ? 'txt-green' : 'txt-red'" style="font-weight:700;" x-text="fmt(ct.pnl)">$0.00</td>
                                    </tr>
                                </template>
                                <tr x-show="closed_trades.length === 0">
                                    <td colspan="5" class="txt-muted" style="text-align:center; padding: 20px 0;">HISTORY IS EMPTY</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="table-footer mono" :class="getClosedTotalPnl() >= 0 ? 'txt-green' : 'txt-red'">
                        TOTAL CLOSED P&L: <span x-text="fmt(getClosedTotalPnl())">$0.00</span>
                    </div>
                </div>
            </div>

            <div class="terminal-panel">
                <div class="panel-header">
                    <div class="panel-title">Signal Engine / Gateways</div>
                </div>
                <div class="panel-body" style="padding:10px;">
                    <div class="gates-split-container">
                        
                        <div style="display:flex; flex-direction:column; justify-content:space-between; background:rgba(255,255,255,0.01); padding:8px; border:1px solid var(--border-color)">
                            <div>
                                <div style="font-size:10px; color:var(--text-muted); font-weight:700; margin-bottom:4px;">SIGNAL ACTION</div>
                                <div class="mono">
                                    <div class="txt-muted" style="font-size:9px;">ACTION</div>
                                    <div style="font-size:16px; font-weight:700;" :class="signal_engine.action === 'BUY' ? 'txt-green' : signal_engine.action === 'SELL' ? 'txt-red' : 'txt-muted'" x-text="signal_engine.action">NO_TRADE</div>
                                </div>
                                <div class="mono" style="margin-top:4px;">
                                    <div class="txt-muted" style="font-size:9px;">DIRECTION</div>
                                    <div class="txt-gold" style="font-size:13px; font-weight:700;" x-text="signal_engine.direction || '--'">--</div>
                                </div>
                            </div>
                            <div class="mono" style="margin-top:auto;">
                                <div class="txt-muted" style="font-size:9px;">REASON</div>
                                <div class="txt-amber" style="font-size:11px; font-weight:600; line-height:1.2" x-text="signal_engine.reason_code || signal_engine.reason || 'SCANNING'">SCANNING</div>
                            </div>
                        </div>

                        <div class="mono" style="display:flex; flex-direction:column; justify-content:space-between;">
                            <div style="font-size:10px; color:var(--text-muted); font-weight:700; margin-bottom:2px;">SMC GATES (8 REQUIRED)</div>
                            <div style="flex-grow:1;">
                                <template x-for="(gate_info, idx) in getGatesArray()">
                                    <div class="gate-list-item">
                                        <span style="margin-right: 4px; color: var(--text-muted); font-size: 10px;" x-text="(idx+1) + '.'"></span>
                                        <span x-text="gate_info.name" style="flex-grow: 1;">Gate Name</span>
                                        <span :class="gate_info.passed ? 'txt-green' : 'txt-red'" style="font-weight:700; margin-left: 8px;" x-text="gate_info.passed ? '✔' : '✘'">✘</span>
                                    </div>
                                </template>
                                <div x-show="getGatesArray().length === 0" class="txt-muted" style="font-size:11px; padding-top:20px; text-align:center;">
                                    NO GATES REGISTERED
                                </div>
                            </div>
                            <div style="margin-top:4px;">
                                <div class="progress-bar-bg" style="height:4px; margin-bottom:2px;">
                                    <div class="progress-bar-fill" :style="`width: ${signal_engine.confidence}%`"></div>
                                </div>
                                <div style="font-size:9px; display:flex; justify-content:space-between" class="txt-muted">
                                    <span>MATCH: <span x-text="getPassedGatesCount()">0</span>/8</span>
                                    <span x-text="signal_engine.confidence + '%'">0%</span>
                                </div>
                            </div>
                        </div>

                    </div>
                </div>
            </div>

        </section>
    </main>

    <footer class="terminal-footer mono">
        <div class="footer-left-meta">
            <span>GUARDEER OS v4.1</span>
            <span>INSTITUTIONAL SMC COMMAND TERMINAL</span>
        </div>
        <div class="footer-right-meta">
            <span>VPS: <span :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'ONLINE' : 'OFFLINE'">OFFLINE</span></span>
            <span>WEBSOCKET: <span :class="ws_connected ? 'txt-green' : 'txt-red'" x-text="ws_connected ? 'ACTIVE' : 'DISCONNECTED'">DISCONNECTED</span></span>
            <span>PRICE: <span class="txt-gold mono" x-text="fmt_price(current_price)">$--</span></span>
            <span>SESSION: <span class="txt-amber" x-text="session || '--'">--</span></span>
        </div>
    </footer>

    <script>
        // Custom colour picker – overrides any palette
        const picker = document.getElementById("customColorPicker");
        if (picker) {
            picker.addEventListener("input", (e) => {
                const color = e.target.value;
                document.body.setAttribute("data-palette", "custom");
                document.body.style.setProperty("--accent", color);
                localStorage.setItem("gos_palette", "custom");
                localStorage.setItem("custom_accent", color);
                if (window.Alpine && Alpine.store("theme")) {
                    Alpine.store("theme").setCustomColor(color);
                }
            });
            const saved = localStorage.getItem("custom_accent");
            if (saved && localStorage.getItem("gos_palette") === "custom") {
                picker.value = saved;
                document.body.style.setProperty("--accent", saved);
            }
        }
    </script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 GUARDEER OS v4.1 [ENHANCED] starting on 0.0.0.0:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")