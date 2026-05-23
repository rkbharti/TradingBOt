import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import os
import time
import json
import threading
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import pandas as pd
import pytz
import requests
import signal as _signal  # renamed: avoids clash with local 'signal' trade variable

# ── Infra ─────────────────────────────────────────────────────────────────────
from tradingbot.infra.mt5.client import MT5Connection
from tradingbot.data.timeframe_aggregator import MultiTimeframeFractal
from tradingbot.infra.storage.json_store import IdeaMemory
from tradingbot.infra.storage.state_repository import HTFMemory
from tradingbot.observability.logger import ObservationLogger
from tradingbot.observability.decision_audit import OBObservationLogger, AuditLogger
#import for oracle Clous vpos for daily summary or toggle for bot ON/OFF
from apps.trader.vps_reporter import post_daily_summary, check_bot_active  

# ── Phase 4: Risk engine + audit ──────────────────────────────────────────────
from tradingbot.risk.challenge_policy import ChallengePolicy
from tradingbot.execution.order_executor import (
    OrderExecutor,
    SignalResult as ExecutorSignalResult,
)
from tradingbot.observability.chart_objects import build_chart_objects

# ── Phase 2: Canonical Signal Engine (replaces all legacy strategy modules) ───
from tradingbot.strategy.smc.signal_engine import SignalEngine, SignalEngineConfig

# ── RETIRED strategy modules — do NOT re-enable, logic is inside signal_engine ─
# from tradingbot.strategy.smc.market_structure_detector import MarketStructureDetector
# from tradingbot.strategy.smc.zones import ZoneCalculator
# from tradingbot.strategy.smc.liquidity import LiquidityDetector
# from tradingbot.strategy.smc.narrative import NarrativeAnalyzer
# from tradingbot.strategy.smc.poi import POIIdentifier
# from tradingbot.strategy.smc.bias import BiasAnalyzer

# ── Config ────────────────────────────────────────────────────────────────────
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM
from apps.trader.vps_reporter import ping_health, post_signal, post_trade_result
# ── Control server (for dashboard commands) ────────────────────────────────
from flask import Flask, jsonify
import threading

control_app = Flask(__name__)
bot_instance_ref = None  # Will be set after bot initialization

@control_app.route('/control/status', methods=['GET'])
def control_status():
    """Dashboard polls this to check if bot is paused/running"""
    global bot_instance_ref
    if bot_instance_ref is None:
        return jsonify({"status": "UNKNOWN", "paused": False}), 500
    
    is_paused = getattr(bot_instance_ref, 'paused', False)
    return jsonify({
        "status": "PAUSED" if is_paused else "RUNNING",
        "paused": is_paused,
        "timestamp": datetime.now().isoformat(),
    }), 200

@control_app.route('/control/pause', methods=['POST'])
def control_pause():
    """Dashboard calls this to pause trading"""
    global bot_instance_ref
    if bot_instance_ref is None:
        return jsonify({"error": "Bot not initialized"}), 500
    
    bot_instance_ref.paused = True
    print("⏸️  Bot PAUSED via dashboard control")
    return jsonify({"status": "PAUSED", "timestamp": datetime.now().isoformat()}), 200

@control_app.route('/control/resume', methods=['POST'])
def control_resume():
    """Dashboard calls this to resume trading"""
    global bot_instance_ref
    if bot_instance_ref is None:
        return jsonify({"error": "Bot not initialized"}), 500
    
    bot_instance_ref.paused = False
    print("▶️  Bot RESUMED via dashboard control")
    return jsonify({"status": "RUNNING", "timestamp": datetime.now().isoformat()}), 200

def start_control_server():
    """Run Flask control server in background thread"""
    try:
        print("🌐 Starting bot control server on port 5000...")
        control_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"❌ Control server error: {e}")

from tradingbot.risk.position_sizing import (
    PositionSizer,
    MIN_LOT,
    MAX_LOT,
    DEFAULT_CONTRACT_SIZE,
    DEFAULT_PIP_VALUE,
)

# ── Globals ───────────────────────────────────────────────────────────────────
DRY_RUN = False  # Set to False for live trading
BOT_MAGIC_NUMBER = 20250101

obs_logger = ObservationLogger()
obs_logger.bot_started()
ping_health()

ob_obs_logger = OBObservationLogger()
try:
    ob_obs_logger.log({"event": "LOGGER_INIT", "timestamp": datetime.now().isoformat()})
except Exception as e:
    print(f"\u26a0\ufe0f OBObservationLogger error: {e}")


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message: str, silent: bool = False):
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        print(f"\u274c Telegram error: {e}")
        return None


# ── Utilities ─────────────────────────────────────────────────────────────────
def compute_atr_from_df(df: pd.DataFrame, period: int = 14) -> float:
    try:
        if df is None or len(df) == 0:
            return 0.0
        for c in ("high", "low", "close"):
            if c not in df.columns:
                return 0.0
        d = df[["high", "low", "close"]].astype(float).copy()
        d["prev_close"] = d["close"].shift(1)
        d["tr1"] = d["high"] - d["low"]
        d["tr2"] = (d["high"] - d["prev_close"]).abs()
        d["tr3"] = (d["low"]  - d["prev_close"]).abs()
        d["tr"]  = d[["tr1", "tr2", "tr3"]].max(axis=1)
        if len(d["tr"].dropna()) >= period:
            atr = d["tr"].rolling(window=period, min_periods=1).mean().iloc[-1]
        else:
            atr = float(d["tr"].dropna().mean() or 0.0)
        if not atr or atr <= 0:
            recent = d.tail(max(3, len(d)))
            atr = float((recent["high"] - recent["low"]).abs().mean() or 0.0)
        return float(atr)
    except Exception as e:
        print(f"\u274c compute_atr_from_df error: {e}")
        return 0.0


def is_trading_session():
    """
    ICT Killzone-based session model using New York time.
    - ASIAN_KZ:           20:00-00:00 NY
    - LONDON_KZ:          02:00-05:00 NY
    - NY_KZ:              07:00-12:00 NY
    - SESSION_DEAD_ZONE:  12:00-14:00 NY  (no trading)
    - CBDR_ANALYSIS_ONLY: 14:00-20:00 NY  (analysis only)
    - OFF_KILLZONE:       all other times
    - WEEKEND_MARKET_CLOSED: Sat + Sun
    """
    now_utc = datetime.now(pytz.utc)
    if now_utc.weekday() >= 5:
        return False, "WEEKEND_MARKET_CLOSED"

    ny_tz  = pytz.timezone("America/New_York")
    now_ny = now_utc.astimezone(ny_tz)
    t = now_ny.hour + now_ny.minute / 60.0

    if t >= 20.0:              return True,  "ASIAN_KZ"
    if 2.0  <= t < 5.0:       return True,  "LONDON_KZ"
    if 7.0  <= t < 12.0:      return True,  "NY_KZ"
    if 12.0 <= t < 14.0:      return False, "SESSION_DEAD_ZONE"
    if 14.0 <= t < 20.0:      return True,  "CBDR_ANALYSIS_ONLY"
    return False, "OFF_KILLZONE"


def map_session_for_filter(session_name: str) -> str:
    if session_name is None:
        return "OFF_KILLZONE"
    s = session_name.upper()
    if "ASIAN"    in s: return "ASIAN_KZ"
    if "LONDON"   in s: return "LONDON_KZ"
    if "NY"       in s: return "NY_KZ"
    if "DEAD"     in s: return "SESSION_DEAD_ZONE"
    if "CBDR"     in s: return "CBDR_ANALYSIS_ONLY"
    if "OFF"      in s or "KILLZONE" in s: return "OFF_KILLZONE"
    if "WEEKEND"  in s: return "WEEKEND_MARKET_CLOSED"
    return s


# ── Dashboard webhook ─────────────────────────────────────────────────────────
def send_to_dashboard(
    bot_data: dict,
    analysis: dict,
    endpoint: str = "http://68.233.99.145:8001/webhook",
    timeout: float = 3.0,
) -> bool:
    """
    Send JSON snapshot to dashboard /webhook.
    Safe: catches all exceptions, returns False on failure.
    Replaces NaN tokens before posting.
    """
    poi_overlays = []
    try:
        ltf_pois = analysis.get("ltf_pois") or {}
        if ltf_pois.get("extreme_poi"):
            poi_overlays.append({
                "type":   "extreme",
                "top":    ltf_pois["extreme_poi"]["top"],
                "bottom": ltf_pois["extreme_poi"]["bottom"],
            })
        if ltf_pois.get("idm_poi"):
            poi_overlays.append({
                "type":   "idm",
                "top":    ltf_pois["idm_poi"]["top"],
                "bottom": ltf_pois["idm_poi"]["bottom"],
            })
        for mp in ltf_pois.get("median_pois", []):
            poi_overlays.append({"type": "median", "top": mp["top"], "bottom": mp["bottom"]})
    except Exception as e:
        print(f"\u26a0\ufe0f POI overlay build failed: {e}")

    try:
        analysis_with_overlays = {**analysis, "chart_overlays": {"poi_zones": poi_overlays}}
        payload = {"bot_instance": bot_data, "analysis_data": analysis_with_overlays}

        try:
            json_payload = json.dumps(payload, default=str)
            if "NaN" in json_payload:
                json_payload = json_payload.replace("NaN", "null")
            resp = requests.post(
                endpoint,
                data=json_payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
        except TypeError:
            resp = requests.post(endpoint, json=payload, timeout=timeout)

        if resp.status_code == 200:
            return True
        print(f"\u26a0\ufe0f Dashboard POST returned {resp.status_code}")
        return False

    except requests.exceptions.RequestException as e:
        print(f"\u26a0\ufe0f Dashboard POST failed: {e}")
        return False
    except Exception as e:
        print(f"\u274c Dashboard unexpected error: {e}")
        return False


# ── Shutdown ──────────────────────────────────────────────────────────────────
def graceful_shutdown(signum=None, frame=None):
    print("\U0001f6d1 Graceful shutdown initiated")
    try:
        obs_logger.bot_stopped()
    except Exception as e:
        print(f"\u26a0\ufe0f Failed to log bot stop: {e}")
    sys.exit(0)


_signal.signal(_signal.SIGINT,  graceful_shutdown)  # Ctrl+C
_signal.signal(_signal.SIGTERM, graceful_shutdown)  # kill / systemd stop


# ─────────────────────────────────────────────────────────────────────────────
# BOT
# ─────────────────────────────────────────────────────────────────────────────
class XAUUSDTradingBot:

    def __init__(self, config_path: str = "config.json") -> None:
        self.config_path = config_path

        # ── Infrastructure ────────────────────────────────────────────────────
        self.mt5         = MT5Connection(config_path)
        self.mtf         = MultiTimeframeFractal(symbol="XAUUSD")
        self.idea_memory = IdeaMemory(expiry_minutes=30)
        self.htf_memory  = HTFMemory()

        # ── Phase 2: canonical signal engine ─────────────────────────────────
        from tradingbot.infra.news.news_filter import NewsFilter
        from config.settings import FINNHUB_API_KEY

        self.signal_engine_config = SignalEngineConfig()
        self.signal_engine = SignalEngine(self.signal_engine_config)
        self.signal_engine.news_filter = NewsFilter(api_key=FINNHUB_API_KEY)

        # ── Phase 4: Risk + Audit ─────────────────────────────────────────────
        self.challenge_policy = ChallengePolicy(
            daily_loss_limit_pct=1.0,
            max_drawdown_pct=3.5,
            max_trades_per_day=2,
            max_consecutive_losses=2,
            min_trade_gap_minutes=90,
            risk_per_trade_pct=0.25,
        )

        self.position_sizer = PositionSizer(
            min_lot=MIN_LOT,
            max_lot=MAX_LOT,
            contract_size=DEFAULT_CONTRACT_SIZE,
            pip_value=DEFAULT_PIP_VALUE,
            min_rr=self.signal_engine.config.rr_min,
        )

        self.order_executor: Optional[OrderExecutor] = None

        self.audit_logger = AuditLogger(
            log_path="logs/decisions/audit.jsonl",
            symbol="XAUUSD",
            timeframe="M5",
        )

        self.magic_number              = BOT_MAGIC_NUMBER
        self._peak_balance: float      = 0.0
        self._daily_pnl_pct: float     = 0.0
        self._trades_today: int        = 0
        self._consecutive_losses: int  = 0
        self._last_trade_time: Optional[datetime] = None
        self._session_date: Optional[date] = None

        # ── State ─────────────────────────────────────────────────────────────
        self.running                   = False
        self.paused                    = False
        self.trade_log: list           = []
        self.open_positions: list      = []
        self.manual_positions: list    = []
        self.closed_trades: list       = []
        self.max_positions             = 3
        self.max_lot_size              = 2.0
        self.risk_per_trade_percent    = 0.5
        self.current_session           = "UNKNOWN"
        self.dry_run                   = DRY_RUN
        self.waiting_for_confirmation  = False

        # ── Cycle summary collector (populated during analyze_once) ───────────
        self._cycle_data: dict         = {}

    # ── Cycle summary printer ─────────────────────────────────────────────────
    def _print_cycle_summary(self) -> None:
        """
        Prints one clean, readable block per analysis cycle.
        Reads from self._cycle_data which is populated in analyze_once().
        Also emits a JSON snapshot to stdout for any log aggregator.
        """
        d = self._cycle_data
        W = 66  # box width

        def row(label: str, value: str) -> str:
            content = f"  {label:<18} {value}"
            return f"║{content:<{W}}║"

        def divider(char: str = "─") -> str:
            return f"╠{char * W}╣"

        def section(title: str) -> str:
            t = f"  {title}"
            return f"╠{t:<{W}}╣"

        def blank() -> str:
            return f"║{' ' * W}║"

        lines = []
        # ── Header ────────────────────────────────────────────────────────────
        ts    = d.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        sess  = d.get("session", "UNKNOWN")
        mode  = "🔴 LIVE" if not self.dry_run else "🟡 DRY-RUN"
        title = f"  XAUUSD  │  {ts}  │  {sess}  │  {mode}"
        lines.append(f"╔{'═' * W}╗")
        lines.append(f"║{title:<{W}}║")

        # ── Account ───────────────────────────────────────────────────────────
        lines.append(divider())
        lines.append(f"║{'  💼 ACCOUNT':<{W}}║")
        acct = d.get("account", {})
        bal  = acct.get("balance", 0.0)
        eq   = acct.get("equity", 0.0)
        lg   = acct.get("login", "—")
        srv  = acct.get("server", "—")
        dd   = bal - eq if bal and eq else 0.0
        lines.append(row("Balance:",    f"${bal:>12,.2f}"))
        lines.append(row("Equity:",     f"${eq:>12,.2f}   (Drawdown: ${abs(dd):,.2f})"))
        lines.append(row("Account:",    f"Login: {lg}   Server: {srv}"))
        lines.append(row("Daily PnL:",  f"{d.get('daily_pnl_pct', 0.0):+.2f}%   Trades today: {d.get('trades_today', 0)}   Consec. losses: {d.get('consecutive_losses', 0)}"))

        # ── Live price ────────────────────────────────────────────────────────
        lines.append(divider())
        lines.append(f"║{'  📈 LIVE PRICE  ─  XAUUSD':<{W}}║")
        candle = d.get("candle", {})
        lines.append(row("Bid / Ask:",  f"{d.get('bid', 0):.2f}  /  {d.get('ask', 0):.2f}   Spread: {d.get('spread', 0):.2f} pts"))
        lines.append(row("M5 Candle:",  f"O:{candle.get('open','─')}  H:{candle.get('high','─')}  L:{candle.get('low','─')}  C:{candle.get('close','─')}"))
        lines.append(row("Δ vs Bid:",   f"{d.get('close_bid_delta', 0):.2f} pts"))

        # ── Bias ──────────────────────────────────────────────────────────────
        lines.append(divider())
        lines.append(f"║{'  🧭 MARKET BIAS':<{W}}║")
        prev_b = d.get("prev_bias", "—")
        live_b = d.get("current_bias", "—")
        arrow  = "→" if prev_b == live_b else "⟳"
        lines.append(row("HTF Bias:",   f"Prev: {prev_b}  {arrow}  Live: {live_b}"))
        lines.append(row("D1 / H4:",    f"{d.get('d1_bias', '—')}  /  {d.get('h4_bias', '—')}"))

        # ── Gates ─────────────────────────────────────────────────────────────
        lines.append(divider())
        lines.append(f"║{'  🔬 SMC SIGNAL ENGINE  ─  8-Gate Sequential':<{W}}║")
        gate_labels = {
            "step_1_htf_bias":             "G1  HTF Bias",
            "step_2_external_liquidity_sweep": "G2  Liquidity Sweep",
            "step_3_choch_mss_body_close": "G3  CHoCH / MSS",
            "step_4_valid_poi":            "G4  Valid POI",
            "step_5_ob_fvg_confluence":    "G5  OB / FVG Confluence",
            "step_6_dealing_range":        "G6  Dealing Range",
            "step_7_killzone":             "G7  Killzone",
            "step_8_risk_reward":          "G8  Risk / Reward",
        }
        gates       = d.get("gates", {})
        first_fail  = None
        for key, label in gate_labels.items():
            gdata  = gates.get(key, {})
            passed = gdata.get("passed", False)
            reason = gdata.get("reason", "NOT_EVALUATED")
            if reason == "NOT_EVALUATED":
                icon   = "  ─"
                marker = ""
            elif passed:
                icon   = "  ✅"
                marker = ""
            else:
                icon   = "  ❌"
                marker = "  ← STOPPED"
                if first_fail is None:
                    first_fail = key
            content = f"{icon}  {label:<28} {reason}{marker}"
            lines.append(f"║{content:<{W+2}}║")

        # ── Decision ──────────────────────────────────────────────────────────
        lines.append(divider())
        lines.append(f"║{'  🎯 DECISION':<{W}}║")
        action = d.get("action", "NO_TRADE")
        conf   = d.get("confidence", 0)
        reason = d.get("reason", "—")
        action_icon = "🚀 ENTER" if action == "ENTER" else "⛔ NO_TRADE"
        lines.append(row("Action:",     f"{action_icon}   Confidence: {conf}%"))
        lines.append(row("Blocked by:", reason))
        if d.get("entry_price"):
            lines.append(row("Entry / SL / TP:", f"{d['entry_price']}  /  {d.get('sl_price','—')}  /  {d.get('tp_price','—')}"))

        # ── Positions ─────────────────────────────────────────────────────────
        lines.append(divider())
        bot_pos  = [p for p in self.open_positions if p.get("source") != "MANUAL"]
        man_pos  = self.manual_positions
        lines.append(f"║{'  📂 POSITIONS  ─  Bot: ' + str(len(bot_pos)) + '   Manual: ' + str(len(man_pos)):<{W}}║")
        all_pos = bot_pos + man_pos
        if not all_pos:
            lines.append(f"║{'  — No open positions —':<{W}}║")
        else:
            for p in all_pos:
                side   = p.get("signal", "?")
                ticket = p.get("ticket", "?")
                entry  = p.get("entry_price", p.get("price_open", 0.0))
                sl_p   = p.get("sl", "—")
                tp_p   = p.get("tp", "—")
                pnl    = p.get("profit", 0.0)
                src    = "MANUAL" if p.get("source") == "MANUAL" or p.get("origin") == "MANUAL" else "BOT"
                pnl_s  = f"${float(pnl):+.2f}" if pnl is not None else "—"
                content = f"  [{src}] #{ticket}  {side}  Entry:{entry}  SL:{sl_p}  TP:{tp_p}  PnL:{pnl_s}"
                lines.append(f"║{content:<{W}}║")

        # ── Footer ────────────────────────────────────────────────────────────
        lines.append(f"╚{'═' * W}╝")

        print("\n" + "\n".join(lines))

        # ── Compact JSON snapshot for WebSocket / log aggregator ──────────────
        snapshot = {
            "type":       "cycle_update",
            "timestamp":  d.get("timestamp"),
            "session":    d.get("session"),
            "dry_run":    self.dry_run,
            "account":    d.get("account", {}),
            "risk": {
                "daily_pnl_pct":       d.get("daily_pnl_pct", 0.0),
                "trades_today":        d.get("trades_today", 0),
                "consecutive_losses":  d.get("consecutive_losses", 0),
                "peak_balance":        self._peak_balance,
            },
            "price": {
                "bid":             d.get("bid"),
                "ask":             d.get("ask"),
                "spread":          d.get("spread"),
                "close_bid_delta": d.get("close_bid_delta"),
                "candle":          d.get("candle", {}),
            },
            "bias": {
                "previous":   d.get("prev_bias"),
                "current":    d.get("current_bias"),
                "d1":         d.get("d1_bias"),
                "h4":         d.get("h4_bias"),
            },
            "signal": {
                "action":      d.get("action"),
                "direction":   d.get("direction"),
                "confidence":  d.get("confidence"),
                "reason":      d.get("reason"),
                "entry_price": d.get("entry_price"),
                "sl_price":    d.get("sl_price"),
                "tp_price":    d.get("tp_price"),
                "gates":       d.get("gates", {}),
            },
            "positions": {
                "bot":    [p for p in self.open_positions if p.get("source") != "MANUAL"],
                "manual": self.manual_positions,
            },
        }
        try:
            print("__CYCLE_JSON__:" + json.dumps(snapshot, default=str))
        except Exception:
            pass

    # ── MT5 wrappers ──────────────────────────────────────────────────────────
    MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

    def mt5_initialize(self) -> bool:
        try:
            if hasattr(self.mt5, "initialize_mt5"):
                return self.mt5.initialize_mt5()
            if hasattr(self.mt5, "initialize"):
                return self.mt5.initialize(path=MT5_PATH)  # ← add path here
        except Exception as e:
            print(f"❌ MT5 init error: {e}")
        return False

    def mt5_get_account(self):
        try:
            if hasattr(self.mt5, "get_account_info"): return self.mt5.get_account_info()
            if hasattr(self.mt5, "account_info"):      return self.mt5.account_info()
        except Exception:
            pass
        return None

    def mt5_get_current_price(self):
        try:
            if hasattr(self.mt5, "get_current_price"): return self.mt5.get_current_price()
            if hasattr(self.mt5, "get_price"):          return self.mt5.get_price()
        except Exception:
            pass
        return None

    def mt5_get_historical(self, bars: int = 300):
        try:
            if hasattr(self.mt5, "get_historical_data"): return self.mt5.get_historical_data(bars=bars)
            if hasattr(self.mt5, "history"):              return self.mt5.history(bars)
        except Exception:
            pass
        return None

    def mt5_get_all_positions(self) -> list:
        """
        Robust, unified accessor for live MT5 positions.
        Returns a list of normalised dicts.
        """
        positions_raw = None

        accessors = []
        if hasattr(self.mt5, "positions_get"):
            accessors.append(("self.mt5.positions_get", lambda: self.mt5.positions_get()))
        if hasattr(self.mt5, "get_positions"):
            accessors.append(("self.mt5.get_positions", lambda: self.mt5.get_positions()))
        if hasattr(self.mt5, "get_open_positions"):
            accessors.append(("self.mt5.get_open_positions", lambda: self.mt5.get_open_positions()))
        if hasattr(self.mt5, "mt5") and hasattr(self.mt5.mt5, "positions_get"):
            accessors.append(("self.mt5.mt5.positions_get", lambda: self.mt5.mt5.positions_get()))

        try:
            for accessor_name, accessor_call in accessors:
                try:
                    candidate = accessor_call()
                    if candidate is not None:
                        positions_raw = candidate
                        break
                except Exception as inner_e:
                    pass  # try next accessor silently
        except Exception as e:
            print(f"⚠️ Error fetching live positions: {e}")
            return []

        if positions_raw is None:
            return []

        normalized = []
        try:
            iterable = positions_raw if isinstance(positions_raw, (list, tuple)) else [positions_raw]
            for p in iterable:
                if isinstance(p, dict):
                    p_dict = p
                elif hasattr(p, "_asdict"):
                    p_dict = p._asdict()
                else:
                    p_dict = {
                        "ticket":        getattr(p, "ticket", 0),
                        "type":          getattr(p, "type", 0),
                        "volume":        getattr(p, "volume", 0.0),
                        "price_open":    getattr(p, "price_open", 0.0),
                        "sl":            getattr(p, "sl", 0.0),
                        "tp":            getattr(p, "tp", 0.0),
                        "symbol":        getattr(p, "symbol", ""),
                        "price_current": getattr(p, "price_current", 0.0),
                        "profit":        getattr(p, "profit", 0.0),
                    }

                if p_dict.get("ticket"):
                    raw_type = p_dict.get("type", 0)
                    if isinstance(raw_type, str):
                        if raw_type.upper() == "BUY":
                            type_code = 0
                        elif raw_type.upper() == "SELL":
                            type_code = 1
                        else:
                            type_code = 0
                    else:
                        type_code = int(raw_type or 0)

                    normalized.append({
                        "ticket":        int(p_dict.get("ticket", 0)),
                        "type":          type_code,
                        "volume":        float(p_dict.get("volume", 0.0)),
                        "price_open":    float(p_dict.get("price_open", 0.0)),
                        "sl":            float(p_dict.get("sl", 0.0)),
                        "tp":            float(p_dict.get("tp", 0.0)),
                        "symbol":        str(p_dict.get("symbol", "")),
                        "price_current": float(p_dict.get("price_current", 0.0)),
                        "profit":        float(p_dict.get("profit", 0.0)),
                    })
        except Exception as e:
            print(f"⚠️ Error normalizing positions: {e}")
            return []

        return normalized

    def mt5_place_order(self, side: str, lots: float, sl: float, tp: float):
        try:
            if self.dry_run:
                print(f"\u26a0\ufe0f DRY_RUN — simulated order: {side} {lots} lots SL={sl} TP={tp}")
                return f"DRY-{int(time.time())}"
            if hasattr(self.mt5, "place_order"):  return self.mt5.place_order(side, lots, sl, tp)
            if hasattr(self.mt5, "order_send"):   return self.mt5.order_send(side, lots, sl, tp)
        except Exception as e:
            print(f"\u274c place_order error: {e}")
        return None

    def mt5_close_position(self, ticket, volume=None):
        try:
            if hasattr(self.mt5, "close_position"):
                return self.mt5.close_position(ticket, volume) if volume else self.mt5.close_position(ticket)
            if hasattr(self.mt5, "close_trade"):
                return self.mt5.close_trade(ticket, volume) if volume else self.mt5.close_trade(ticket)
        except Exception as e:
            print(f"\u274c close_position error: {e}")
        return False

    def mt5_modify_position(self, ticket, sl=None, tp=None):
        try:
            if hasattr(self.mt5, "modify_position"): return self.mt5.modify_position(ticket, sl, tp)
            if hasattr(self.mt5, "modify_trade"):     return self.mt5.modify_trade(ticket, sl, tp)
        except Exception as e:
            print(f"\u274c modify_position error: {e}")
        return False

    # ── Initialization ────────────────────────────────────────────────────────
    def initialize(self) -> bool:
        print("=== Initializing XAUUSDTradingBot ===")
        if not self.mt5_initialize():
            print("\u274c MT5 initialization failed")
            return False

        acct = self.mt5_get_account()
        if acct:
            try:
                bal = float(acct.balance)
                self._peak_balance = bal
                self.risk_per_trade_percent = (
                    getattr(acct, "risk_per_trade", self.risk_per_trade_percent)
                    or self.risk_per_trade_percent
                )
                print(f"\u2705 Account balance: ${bal:,.2f}")
            except Exception:
                print("\u2139\ufe0f Could not read account balance cleanly")
        else:
            print("\u2139\ufe0f No account info available (continuing in read-only/test mode)")

        # Wire OrderExecutor now that MT5 client is ready
        self.order_executor = OrderExecutor(
            mt5_client=self.mt5,
            position_sizer=self.position_sizer,
            challenge_policy=self.challenge_policy,
            dry_run=self.dry_run,
        )
        print("\u2705 OrderExecutor wired")
        print("\u2705 Initialization complete")
        return True

    # ── Data fetch ────────────────────────────────────────────────────────────
    def fetch_and_prepare(self):
        market_data = self.mt5_get_historical(bars=300)
        if market_data is None:
            print("\u274c Could not fetch historical data")
            return None, None
        if not isinstance(market_data, pd.DataFrame):
            try:
                market_data = pd.DataFrame(market_data)
            except Exception:
                print("\u274c Historical data conversion failed")
                return None, None
        for c in ("high", "low", "close", "open", "tick_volume"):
            if c in market_data.columns:
                market_data[c] = pd.to_numeric(market_data[c], errors="coerce")

        current_price = self.mt5_get_current_price()
        if current_price is None:
            print("\u274c Could not fetch current price")
            return market_data, None
        return market_data, current_price

    def sync_closed_positions(self) -> None:
        """
        Detect positions that have closed since last cycle.
        For each closed position:
        - Calls challenge_policy.log_trade_result() to update
            consecutive_losses, daily_pnl, peak_balance.
        - Updates bot-level counters (_consecutive_losses, _daily_pnl_pct).
        """
        live_tickets = {p["ticket"] for p in self.mt5_get_all_positions()}
        before = len(self.open_positions)

        still_open = []
        closed = []

        for p in self.open_positions:
            if p.get("ticket") in live_tickets:
                still_open.append(p)
            else:
                closed.append(p)

        self.open_positions = still_open
        removed = len(closed)

        if removed == 0:
            return

        print(f"\U0001f504 Synced: removed {removed} closed position(s)")

        # ── Fetch current account balance for peak tracking ──────────────────
        acct = self.mt5_get_account()
        current_balance = float(getattr(acct, "balance", 0.0)) if acct else 0.0

        for pos in closed:
            ticket  = pos.get("ticket")
            entry   = float(pos.get("entry_price", 0.0))
            sl      = float(pos.get("sl", 0.0))
            tp      = float(pos.get("tp", 0.0))
            side    = pos.get("signal", "BUY")

            raw_pnl = pos.get("profit", None)

            if not any(ct.get("ticket") == ticket for ct in self.closed_trades):
                closed_record = dict(pos)
                closed_record["status"] = "CLOSED"
                closed_record["closed_time"] = datetime.now().isoformat()
                if raw_pnl is not None:
                    closed_record["profit"] = float(raw_pnl)
                self.closed_trades.append(closed_record)

            if raw_pnl is None or raw_pnl == 0.0:
                print(f"  ⚠️  Ticket {ticket}: no profit field — skipping policy update")
                continue

            pnl = float(raw_pnl)
            was_win = pnl > 0

            self.challenge_policy.log_trade_result(
                was_win=was_win,
                pnl=pnl,
                current_balance=current_balance,
            )

            self._consecutive_losses = self.challenge_policy.consecutive_losses
            self._peak_balance = self.challenge_policy.peak_balance

            if current_balance > 0 and self.challenge_policy.starting_balance > 0:
                self._daily_pnl_pct = (
                    self.challenge_policy.daily_pnl
                    / self.challenge_policy.starting_balance
                ) * 100

            result_icon = "✅ WIN" if was_win else "❌ LOSS"
            print(
                f"  {result_icon} Ticket {ticket} | PnL: ${pnl:+.2f} | "
                f"Consecutive losses: {self._consecutive_losses} | "
                f"Peak: ${self._peak_balance:,.2f}"
            )
            post_trade_result(
                symbol="XAUUSD",
                direction=side,
                result="win" if was_win else "loss",
                pnl=round(pnl, 2),
                note=f"Ticket {ticket} | Session: {self.current_session}",
            )

    # ── Manual trade observation ──────────────────────────────────────────────
    def detect_and_manage_manual_trades(self, analysis_context: dict) -> None:
        """
        Detects MT5 positions not tracked in self.open_positions → flags as MANUAL.
        Applies SMC context for advisory output.
        """
        try:
            live_pos_list = self.mt5_get_all_positions() or []
            if live_pos_list:
                print(f"\U0001f50e DEBUG: MT5 reports {len(live_pos_list)} open positions.")

            bot_tickets          = [int(p.get("ticket", 0)) for p in self.open_positions]
            current_manual_tickets = []

            for pos in live_pos_list:
                ticket    = int(pos.get("ticket", 0))
                symbol    = pos.get("symbol", "")
                sym_upper = symbol.upper()

                if "XAU" not in sym_upper and "GOLD" not in sym_upper:
                    print(f"\u26a0\ufe0f DEBUG: Skipping {ticket} (symbol={symbol})")
                    continue

                if ticket not in bot_tickets:
                    known_bot_trade = any(
                        int(log.get("ticket", 0)) == ticket and log.get("action") == "ORDER_PLACED"
                        for log in self.trade_log
                        if isinstance(log, dict)
                    )

                    if known_bot_trade:
                        existing_open = next((x for x in self.open_positions if x.get("ticket") == ticket), None)
                        if not existing_open:
                            type_code = pos.get("type", 0)
                            trade_type = "BUY" if type_code == 0 else "SELL"
                            restored_open = {
                                "ticket": ticket,
                                "signal": trade_type,
                                "lot_size": pos.get("volume"),
                                "sl": pos.get("sl"),
                                "tp": pos.get("tp"),
                                "entry_price": float(pos.get("price_open", 0.0)),
                                "entry_time": datetime.now().isoformat(),
                                "status": "OPEN",
                                "source": "SIGNAL_ENGINE",
                                "type": type_code,
                                "profit": pos.get("profit", 0.0),
                                "price_current": pos.get("price_current", 0.0),
                                "symbol": symbol,
                            }
                            self.open_positions.append(restored_open)
                            print(f"♻️ Restored bot trade from MT5: Ticket {ticket} | {trade_type}")
                        continue

                    current_manual_tickets.append(ticket)
                    existing = next((x for x in self.manual_positions if x["ticket"] == ticket), None)

                    if not existing:
                        type_code   = pos.get("type", 0)
                        trade_type  = "BUY" if type_code == 0 else "SELL"
                        entry_price = float(pos.get("price_open", 0.0))

                        trend = analysis_context.get("market_structure", {}).get("current_trend", "NEUTRAL")
                        zone  = analysis_context.get("current_zone", "UNKNOWN")

                        rationale = []
                        if trade_type == "BUY":
                            if trend == "BULLISH": rationale.append("Aligned with Bullish Trend")
                            elif trend == "BEARISH": rationale.append("Counter-trend (High Risk)")
                        else:
                            if trend == "BEARISH": rationale.append("Aligned with Bearish Trend")
                            elif trend == "BULLISH": rationale.append("Counter-trend (High Risk)")

                        if zone == "DISCOUNT" and trade_type == "BUY":  rationale.append("Buying in Discount (Good)")
                        if zone == "PREMIUM"  and trade_type == "BUY":  rationale.append("Buying in Premium (Risk)")
                        if zone == "PREMIUM"  and trade_type == "SELL": rationale.append("Selling in Premium (Good)")
                        if zone == "DISCOUNT" and trade_type == "SELL": rationale.append("Selling in Discount (Risk)")

                        advisory_str = "; ".join(rationale) if rationale else "Neutral structure"
                        print(f"\U0001f440 MANUAL TRADE DETECTED: Ticket {ticket} | {trade_type} @ {entry_price}")
                        print(f"   \U0001f916 SMC Advisory: {advisory_str}")

                        new_manual = {
                            "ticket":        ticket,
                            "origin":        "MANUAL",
                            "signal":        trade_type,
                            "entry_price":   entry_price,
                            "volume":        pos.get("volume"),
                            "sl":            pos.get("sl"),
                            "tp":            pos.get("tp"),
                            "entry_time":    datetime.now().isoformat(),
                            "advisory":      advisory_str,
                            "status":        "OPEN",
                            "symbol":        symbol,
                            "type":          type_code,
                            "profit":        pos.get("profit", 0.0),
                            "price_open":    entry_price,
                            "price_current": pos.get("price_current", 0.0),
                            "source":        "MANUAL",
                        }
                        self.manual_positions.append(new_manual)
                        self.trade_log.append({
                            "timestamp": datetime.now().isoformat(),
                            "action":    "MANUAL_DETECTED",
                            "ticket":    ticket,
                            "details":   new_manual,
                        })
                        self.save_trade_log()
                    else:
                        existing["sl"]            = pos.get("sl")
                        existing["tp"]            = pos.get("tp")
                        existing["current_price"] = pos.get("price_current", 0.0)

            # Cleanup closed manual trades
            active_manual = set(current_manual_tickets)
            for m in list(self.manual_positions):
                if m["ticket"] not in active_manual:
                    print(f"\U0001f3c1 Manual Trade {m['ticket']} Closed/Removed")
                    self.manual_positions.remove(m)
                    self.trade_log.append({
                        "timestamp": datetime.now().isoformat(),
                        "action":    "MANUAL_CLOSED",
                        "ticket":    m["ticket"],
                    })
                    self.save_trade_log()

            # Inject manual trades into open_positions for dashboard visibility
            for mp in self.manual_positions:
                if not any(op.get("ticket", 0) == mp["ticket"] for op in self.open_positions):
                    self.open_positions.append(mp)

        except Exception as e:
            print(f"\u26a0\ufe0f Manual trade sync error: {e}")

    # ── Main analysis cycle ───────────────────────────────────────────────────

    def _maybe_reset_daily_state(self) -> None:
        """
        Reset daily counters at 08:00 IST on a new calendar day.

        Persists _session_date to disk so bot restarts mid-session
        do NOT trigger a false daily reset.
        """
        try:
            import pytz
            import json
            from pathlib import Path

            STATE_FILE = Path("logs/session_state.json")

            ist_tz  = pytz.timezone("Asia/Kolkata")
            now_ist = datetime.now(pytz.utc).astimezone(ist_tz)
            today   = now_ist.date()

            # ── Load persisted session date on first call ─────────────────────
            if self._session_date is None:
                if STATE_FILE.exists():
                    try:
                        saved = json.loads(STATE_FILE.read_text())
                        saved_date_str = saved.get("session_date", "")
                        if saved_date_str:
                            from datetime import date
                            self._session_date = date.fromisoformat(saved_date_str)
                            print(f"📂 Restored session_date from disk: {self._session_date}")
                    except Exception as load_err:
                        print(f"⚠️ Could not load session state: {load_err}")

            # ── Only reset if it's genuinely a new calendar day AND past 08:00 IST
            if self._session_date != today and now_ist.hour >= 8:
                print(f"🔄 New trading day ({today}) — resetting daily counters")

                # ── Fire daily summary BEFORE resetting counters ──────────────
                try:
                    cp = self.challenge_policy
                    total = cp.daily_wins + cp.daily_losses
                    post_daily_summary(
                        total_trades=total,
                        wins=cp.daily_wins,
                        losses=cp.daily_losses,
                        net_pnl=round(self._daily_pnl_pct, 2),
                        max_drawdown=round(cp.max_drawdown_pct, 2),
                        session=str(self._session_date),
                    )
                except Exception as e:
                    print(f"⚠️ Daily summary post failed: {e}")

                self.challenge_policy.reset_daily_state() 
                self._trades_today       = 0
                self._daily_pnl_pct      = 0.0
                self._consecutive_losses = self.challenge_policy.consecutive_losses
                self._session_date       = today

                # ── Persist new session date so restarts don't re-trigger ─────
                try:
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(
                        json.dumps({
                            "session_date": today.isoformat(),
                            "reset_at_ist": now_ist.isoformat(),
                        })
                    )
                    print(f"💾 session_date persisted: {today}")
                except Exception as save_err:
                    print(f"⚠️ Could not persist session state: {save_err}")

        except Exception as e:
            print(f"⚠️ Daily reset error: {e}")

    def _build_policy_state_snapshot(self, account_balance: float) -> dict:
        """Return a serialisable policy-state dict for audit records."""
        return {
            "daily_pnl_pct":          self._daily_pnl_pct,
            "consecutive_losses":     self._consecutive_losses,
            "trades_today":           self._trades_today,
            "peak_balance":           self._peak_balance,
            "current_balance":        account_balance,
            "max_drawdown_pct":       self.challenge_policy.max_drawdown_pct,
            "daily_loss_limit_pct":   self.challenge_policy.daily_loss_limit_pct,
            "max_consecutive_losses": self.challenge_policy.max_consecutive_losses,
            "max_trades_per_day":     self.challenge_policy.max_trades_per_day,
        }

    def _manage_open_positions_trailing(self, current_bid: float) -> None:
        """
        Breakeven + trailing stop logic. Called every cycle.

        Rules (SMC-aligned, funded challenge safe):
        - BREAKEVEN: When price moves 1.0× risk in our favour,
        move SL to entry + 2 pips (locks 0 loss).
        - TRAILING:  When price moves 2.0× risk in our favour,
        trail SL at 50% of the current profit distance
        (locks partial profit, lets winners run).

        All SL modifications go through mt5_modify_position().
        Dry-run: prints the action but does not call MT5.
        """
        if not self.open_positions:
            return

        for pos in self.open_positions:
            try:
                ticket     = pos.get("ticket")
                entry      = float(pos.get("entry_price", 0.0))
                current_sl = float(pos.get("sl", 0.0))
                tp         = float(pos.get("tp", 0.0))
                side       = pos.get("signal", "BUY")

                if not entry or not current_sl or not tp:
                    continue

                # ── Risk distance at entry ────────────────────────────────────
                risk_pips = abs(entry - current_sl)
                if risk_pips <= 0:
                    continue

                if side == "BUY":
                    profit_pips = current_bid - entry
                else:
                    profit_pips = entry - current_bid

                if profit_pips <= 0:
                    continue  # still in drawdown — do nothing

                # ── BREAKEVEN: 1R in profit → SL to entry + 2 pips ───────────
                breakeven_sl = (entry + 2.0) if side == "BUY" else (entry - 2.0)
                at_1r        = profit_pips >= risk_pips * 1.0

                if at_1r and (
                    (side == "BUY"  and current_sl < breakeven_sl) or
                    (side == "SELL" and current_sl > breakeven_sl)
                ):
                    new_sl = round(breakeven_sl, 2)
                    print(f"  🔒 BREAKEVEN Ticket {ticket}: SL {current_sl} → {new_sl} (entry+2)")
                    if not self.dry_run:
                        self.mt5_modify_position(ticket, sl=new_sl)
                    pos["sl"] = new_sl
                    continue  # don't also trail in same cycle

                # ── TRAILING: 2R in profit → trail at 50% profit distance ─────
                at_2r = profit_pips >= risk_pips * 2.0

                if at_2r:
                    trail_sl = (
                        round(current_bid - (profit_pips * 0.5), 2)
                        if side == "BUY"
                        else round(current_bid + (profit_pips * 0.5), 2)
                    )
                    sl_improved = (
                        (side == "BUY"  and trail_sl > current_sl) or
                        (side == "SELL" and trail_sl < current_sl)
                    )
                    if sl_improved:
                        print(f"  📈 TRAILING Ticket {ticket}: SL {current_sl} → {trail_sl}")
                        if not self.dry_run:
                            self.mt5_modify_position(ticket, sl=trail_sl)
                        pos["sl"] = trail_sl

            except Exception as e:
                print(f"  ⚠️ Trailing stop error for ticket {pos.get('ticket')}: {e}")

    def analyze_once(self) -> None:
        # ── Daily reset (08:00 IST) ───────────────────────────────────────────
        self._maybe_reset_daily_state()
        
        # ── Pause flag check (from dashboard control) ──────────────────────────
        if self.paused:
            print("⏸️ Bot paused via dashboard control — skipping cycle")
            return
        
        # ── Remote pause check ────────────────────────────────────────
        if not check_bot_active():
            print("⏸️ Bot paused via remote — skipping cycle")
            return

        self.sync_closed_positions()

        # ── Trailing stop / breakeven management ──────────────────────────────
        if self.open_positions:
            acct_trail = self.mt5_get_account()
            price_trail = self.mt5_get_current_price()
            if price_trail is not None:
                bid_trail = float(price_trail.get("bid", 0.0)) if isinstance(price_trail, dict) else float(price_trail)
                if bid_trail > 0:
                    self._manage_open_positions_trailing(bid_trail)

        # ── Lockdown gate ─────────────────────────────────────────────────────
        _acct_early = self.mt5_get_account()
        _bal_early = float(getattr(_acct_early, "balance", self._peak_balance or 100000.0)) if _acct_early else (self._peak_balance or 100000.0)
        lockdown_reason = self.challenge_policy.get_lockdown_reason(
            daily_pnl_pct=self._daily_pnl_pct,
            peak_balance=self._peak_balance or _bal_early,
            current_balance=_bal_early,
            consecutive_losses=self._consecutive_losses,
        )
        if lockdown_reason:
            print(f"🛑 LOCKDOWN: {lockdown_reason} — skipping cycle (wait 60s)")
            try:
                self.audit_logger.log_lockdown(
                    lockdown_reason,
                    self._build_policy_state_snapshot(_bal_early),
                )
            except Exception as _e:
                print(f"⚠️ Audit lockdown log failed: {_e}")
            time.sleep(60)
            return

        is_active, session_name = is_trading_session()
        session_norm = map_session_for_filter(session_name)
        self.current_session = session_norm

        # ── Initialise cycle_data collector ───────────────────────────────────
        self._cycle_data = {
            "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session":    session_norm,
        }

        previous_bias = self.htf_memory.get("htf_bias", "NEUTRAL")
        self._cycle_data["prev_bias"] = previous_bias

        # ── Weekend / market closed ───────────────────────────────────────────
        if not is_active:
            print(f"⏸️ Market session '{session_norm}' not active — heartbeat only")
            self.detect_and_manage_manual_trades({
                "market_structure": {"current_trend": previous_bias},
                "current_zone": "UNKNOWN",
            })
            acct = self.mt5_get_account()
            equity = float(getattr(acct, "equity", 0.0)) if acct else 0.0
            balance = float(getattr(acct, "balance", 0.0)) if acct else 0.0

            log_record = {
                "time": datetime.now().isoformat(),
                "narrative_state": "MARKET_CLOSED",
                "entry_allowed": False,
                "structure_state": {"current_trend": "MARKET CLOSED"},
                "bias": "NEUTRAL",
                "reason": f"Market session '{session_norm}' not active",
            }
            self.trade_log.append(log_record)
            self.save_trade_log()
            try:
                obs_logger.log_event("narrative_state", log_record)
            except Exception:
                pass

            dashboard_ok = send_to_dashboard(
                {
                    "equity": equity, "balance": balance, "last_price": 0,
                    "open_positions": self.open_positions,
                    "manual_positions": self.manual_positions,
                    "closed_trades": self.closed_trades, "chart_data": [],  # ← Gap 1 fix
                    "current_session": session_norm,
                },
                {
                    "market_structure": {"current_trend": "MARKET CLOSED"},
                    "zone_strength": 0, "current_zone": "CLOSED", "zones": {},
                },
            )
            print(f"🔎 Weekend dashboard POST success: {dashboard_ok}")
            return  # ← Gap 3 fix: stop here, do not fall through to active-market code

        # ── Fetch market data ─────────────────────────────────────────────────
        market_data, current_price = self.fetch_and_prepare()
        if market_data is None or current_price is None:
            return
        latest = market_data.iloc[-1]
        bid = float(current_price.get("bid", current_price))
        ask = float(current_price.get("ask", bid))
        spread = abs(ask - bid)

        # ── LIVE CANDLE SYNC VERIFICATION ─────────────────────────────────────
        try:
            _vc = market_data.iloc[-1]
            _close_bid_delta = round(abs(float(_vc['close']) - bid), 2)
            self._cycle_data.update({
                "bid":             bid,
                "ask":             ask,
                "spread":          round(spread, 2),
                "close_bid_delta": _close_bid_delta,
                "candle": {
                    "time":  str(_vc.get('time', _vc.name)),
                    "open":  _vc['open'],
                    "high":  _vc['high'],
                    "low":   _vc['low'],
                    "close": _vc['close'],
                },
            })
        except Exception as _ve:
            print(f"⚠️ Candle sync check failed: {_ve}")

        # ── Fetch all timeframe DataFrames ────────────────────────────────────
        m5_raw = self.mtf.fetch_data("M5")
        m15_raw = self.mtf.fetch_data("M15")
        h4_raw = self.mtf.fetch_data("H4")
        d1_raw = self.mtf.fetch_data("D1")

        m5_df = m5_raw.get("df") if isinstance(m5_raw, dict) else m5_raw
        m15_df = m15_raw.get("df") if isinstance(m15_raw, dict) else m15_raw
        h4_df = h4_raw.get("df") if isinstance(h4_raw, dict) else h4_raw
        d1_df = d1_raw.get("df") if isinstance(d1_raw, dict) else d1_raw

        latest = m5_df.iloc[-1] if m5_df is not None and len(m5_df) > 0 else None

        if any(df is None or len(df) == 0 for df in [m5_df, m15_df, h4_df, d1_df]):
            print("❌ One or more timeframe DataFrames unavailable — skipping cycle")
            return

        # ── CANONICAL SIGNAL ENGINE ───────────────────────────────────────────
        try:
            result = self.signal_engine.evaluate(
                m5_df=m5_df,
                m15_df=m15_df,
                h4_df=h4_df,
                d1_df=d1_df,
                now_utc=datetime.now(timezone.utc),
            )
        except Exception as e:
            print(f"❌ SignalEngine error: {e}")
            import traceback; traceback.print_exc()
            return

        current_bias = result.direction or previous_bias or "NEUTRAL"

        if result.direction:
            self.htf_memory.update("htf_bias", result.direction)

        # ── Populate bias + gate data into cycle_data ─────────────────────────
        htf_gate_early = result.gates.get("step_1_htf_bias", {})
        self._cycle_data.update({
            "current_bias": current_bias,
            "d1_bias":      htf_gate_early.get("d1_bias", "NEUTRAL"),
            "h4_bias":      htf_gate_early.get("h4_bias", "NEUTRAL"),
            "action":       result.action,
            "direction":    result.direction,
            "confidence":   result.confidence_score,
            "reason":       result.reason,
            "entry_price":  result.entry_price,
            "sl_price":     result.sl_price,
            "tp_price":     result.tp_price,
            "gates":        result.gates,
        })

        # ── Manual trade observation ──────────────────────────────────────────
        self.detect_and_manage_manual_trades({
            "market_structure": {"current_trend": current_bias},
            "current_zone": "UNKNOWN",
        })

        # ── Collect account info for summary ─────────────────────────────────
        acct_summary = self.mt5_get_account()
        self._cycle_data.update({
            "account": {
                "login":   getattr(acct_summary, "login", None) if acct_summary else None,
                "server":  getattr(acct_summary, "server", None) if acct_summary else None,
                "balance": float(getattr(acct_summary, "balance", 0.0)) if acct_summary else 0.0,
                "equity":  float(getattr(acct_summary, "equity", 0.0)) if acct_summary else 0.0,
            },
            "daily_pnl_pct":      self._daily_pnl_pct,
            "trades_today":       self._trades_today,
            "consecutive_losses": self._consecutive_losses,
        })

        # ── Print clean cycle summary (terminal + JSON line) ─────────────────
        self._print_cycle_summary()

        # ── Log cycle ─────────────────────────────────────────────────────────
        log_record = {
            "time": datetime.now().isoformat(),
            "narrative_state": result.reason,
            "entry_allowed": result.action == "ENTER",
            "action": result.action,
            "direction": result.direction,
            "entry_price": result.entry_price,
            "sl_price": result.sl_price,
            "tp_price": result.tp_price,
            "confidence_score": result.confidence_score,
            "gates": result.gates,
            "bias": current_bias,
            "session": session_norm,
        }
        self.trade_log.append(log_record)
        self.save_trade_log()
        try:
            obs_logger.log_event("signal_result", log_record)
        except Exception:
            pass

        # ── Gap 2 fix: refresh floating P&L on open positions from MT5 ─────────
        # Pulls the latest price_current and profit for every open position
        # so the dashboard shows live floating P&L, not stale entry-time values.
        try:
            live_pos_map = {
                p["ticket"]: p
                for p in self.mt5_get_all_positions()
                if p.get("ticket")
            }
            for op in self.open_positions:
                ticket = op.get("ticket")
                if ticket and ticket in live_pos_map:
                    live = live_pos_map[ticket]
                    op["price_current"] = live.get("price_current", op.get("price_current", 0.0))
                    op["profit"]        = live.get("profit",        op.get("profit", 0.0))
        except Exception as _pnl_err:
            print(f"⚠️ Live P&L refresh failed: {_pnl_err}")

        # ── Dashboard payload ─────────────────────────────────────────────────
        acct = acct_summary  # already fetched above for cycle summary
        htf_gate = result.gates.get("step_1_htf_bias", {})
        dr_gate = result.gates.get("step_6_dealing_range", {})
        rr_gate = result.gates.get("step_8_risk_reward", {})

        try:
            chart_objects = build_chart_objects({}, {}, {}, bid)
        except Exception:
            chart_objects = {}

        analysis_snapshot = {
            "market_structure": {
                "current_trend": current_bias,
                "d1_bias": htf_gate.get("d1_bias", "NEUTRAL"),
                "h4_bias": htf_gate.get("h4_bias", "NEUTRAL"),
            },
            "zone_strength": result.confidence_score,
            "current_zone": dr_gate.get("zone", "UNKNOWN"),
            "zones": {
                "dealing_low": dr_gate.get("dealing_low"),
                "dealing_high": dr_gate.get("dealing_high"),
                "equilibrium": dr_gate.get("equilibrium"),
            },
            "pdh": float(market_data["high"].max()),
            "pdl": float(market_data["low"].min()),
            "ltf_pois": {},
            "chart_objects": chart_objects,
            "signal_engine": {
                "action": result.action,
                "direction": result.direction,
                "entry_price": result.entry_price,
                "sl_price": result.sl_price,
                "tp_price": result.tp_price,
                "reason": result.reason,
                "confidence_score": result.confidence_score,
                "rr": rr_gate.get("rr"),
                "gates": result.gates,
            },
        }

        send_to_dashboard(
            {
                "equity": float(getattr(acct, "equity", 0.0)) if acct else 0.0,
                "balance": float(getattr(acct, "balance", 0.0)) if acct else 0.0,
                "last_price": bid,
                "open_positions": self.open_positions,
                "manual_positions": self.manual_positions,
                "closed_trades": self.closed_trades,          # ← Gap 1 fix: was hardcoded []
                "chart_data": market_data.tail(300).to_dict(orient="records"),
                "current_session": self.current_session,
                "account": {
                    "login": getattr(acct, "login", None) if acct else None,
                    "server": getattr(acct, "server", None) if acct else None,
                    "balance": float(getattr(acct, "balance", 0.0)) if acct else 0.0,
                    "equity": float(getattr(acct, "equity", 0.0)) if acct else 0.0,
                }
            },
            analysis_snapshot,
        )

        # ── Execution gate ────────────────────────────────────────────────────
        if len(self.open_positions) > 0:
            return  # summary already shows open position count

        if result.action != "ENTER":
            return  # summary already shows reason

        # ── All 8 gates passed — execute via OrderExecutor ───────────────────
        acct = self.mt5_get_account()
        account_balance = float(getattr(acct, "balance", 100000.0)) if acct else 100000.0
        if account_balance > self._peak_balance:
            self._peak_balance = account_balance

        policy_snap = self._build_policy_state_snapshot(account_balance)

        try:
            action = result.action or "ENTER"
            direction = result.direction or "BULLISH"

            if action == "ENTER":
                action = "BUY" if direction == "BULLISH" else "SELL"

            entry_price = float(result.entry_price) if result.entry_price is not None else 0.0
            sl_price = float(result.sl_price) if result.sl_price is not None else None
            tp_price = float(result.tp_price) if result.tp_price is not None else None

            poi_zone = getattr(result, "poi_zone", None)
            liquidity_levels = getattr(result, "liquidity_levels", None)

            if poi_zone is None:
                poi_zone = {
                    "top": entry_price + 5.0,
                    "bottom": entry_price,
                    "mt": entry_price + 2.5,
                }

            if liquidity_levels is None:
                liquidity_levels = {}

                if tp_price is not None and tp_price > 0:
                    liquidity_levels["pdh"] = tp_price
                    liquidity_levels["weekly_high"] = tp_price

                if sl_price is not None and sl_price > 0:
                    liquidity_levels["pdl"] = sl_price
                    liquidity_levels["weekly_low"] = sl_price

            executor_signal = ExecutorSignalResult(
                action=action,
                direction=direction,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                poi_zone=poi_zone,
                liquidity_levels=liquidity_levels,
            )

            exec_result = self.order_executor.execute_signal(
                signal=executor_signal,
                account_balance=account_balance,
                peak_balance=self._peak_balance,
                current_daily_pnl_pct=self._daily_pnl_pct,
                trades_today=self._trades_today,
                consecutive_losses=self._consecutive_losses,
                last_trade_time=self._last_trade_time,
            )

        except Exception as e:
            print(f"❌ OrderExecutor error: {e}")
            import traceback
            traceback.print_exc()
            return

        try:
            self.audit_logger.log_evaluation(result, exec_result, policy_snap)
        except Exception as e:
            print(f"⚠️ Audit log failed: {e}")

        if not exec_result.success:
            print(f"⛔ OrderExecutor rejected: {exec_result.rejection_reason}")
            return

        lot_size = exec_result.lot_size
        trade_side = action
        final_sl = exec_result.sl_price
        final_tp = exec_result.tp_price

        print(f"🚀 ENTERING {trade_side} | Entry={result.entry_price} | SL={final_sl} | TP={final_tp} | RR={exec_result.rr_ratio:.2f}x | Lot={lot_size}")

        ticket = exec_result.ticket

        if ticket:
            print(f"✅ Order placed | Ticket: {ticket}")
            self._trades_today += 1
            self._last_trade_time = datetime.now()

            position_record = {
                "ticket": ticket,
                "signal": trade_side,
                "lot_size": lot_size,
                "sl": final_sl,
                "tp": final_tp,
                "entry_price": result.entry_price,
                "rr_ratio": exec_result.rr_ratio,
                "risk_amount": exec_result.risk_amount,
                "entry_time": datetime.now().isoformat(),
                "status": "OPEN",
                "source": "SIGNAL_ENGINE",
                "confidence_score": result.confidence_score,
            }
            self.open_positions.append(position_record)
            self.trade_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "ORDER_PLACED",
                **position_record,
            })
            self.save_trade_log()
            # ── ADD THIS BLOCK ─────────────────────────────────────
            # Post-trade dashboard sync: send updated open_positions AFTER append
            acct_post = self.mt5_get_account()
            send_to_dashboard(
                {
                    "equity":           float(getattr(acct_post, "equity", 0.0)) if acct_post else 0.0,
                    "balance":          float(getattr(acct_post, "balance", 0.0)) if acct_post else 0.0,
                    "last_price":       bid,
                    "open_positions":   self.open_positions,       # ← now contains new position
                    "manual_positions": self.manual_positions,
                    "closed_trades":    [],
                    "chart_data":       [],                        # omit heavy chart data on this call
                    "current_session":  self.current_session,
                },
                analysis_snapshot,                                 # reuse snapshot from this cycle
            )

            send_telegram(
                f"🚀 <b>{trade_side}</b> entered @ {result.entry_price}\n"
                f"SL: {final_sl} | TP: {final_tp}\n"
                f"RR: {exec_result.rr_ratio:.2f}x | Lot: {lot_size} | Risk: ${exec_result.risk_amount:.2f}\n"
                f"Confidence: {result.confidence_score}% | Session: {self.current_session}"
            )
            post_signal(
                symbol="XAUUSD",
                direction=trade_side,
                entry=float(result.entry_price or 0.0),
                sl=float(final_sl or 0.0),
                tp=float(final_tp or 0.0),
                gate_summary=str(getattr(result, "gate_summary", "") or ""),
            )
        else:
            print("❌ Order placement failed")

    # ── Diagnostics ───────────────────────────────────────────────────────────
    def signal_diagnostics(self):
        """
        Runs full live diagnostic flow against MT5 + closed-candle MTF data.

        Behavior:
        - Active session + missing TF data -> FAIL
        - Active session + stale TF data   -> FAIL
        - Inactive session + stale TF data -> WARN + heartbeat skip
        - Inactive session + fetched TFs   -> INFO/WARN only, no forced failure
        """
        print("\n" + "=" * 65)
        print("🔬  SMC SIGNAL ENGINE — LIVE DIAGNOSTICS")
        print("=" * 65)

        result = None

        # GATE 0: MT5
        print("\n[GATE 0] MT5 Connection")
        try:
            if not self.mt5_initialize():
                print("  ❌ FAIL — MT5 could not initialize")
                return None
            print("  ✅ PASS — MT5 connected")
        except Exception as e:
            print(f"  ❌ FAIL — {e}")
            return None

        # GATE 1: Market data
        print("\n[GATE 1] Market Data Fetch")
        try:
            market_data, current_price = self.fetch_and_prepare()
            if market_data is None or current_price is None:
                print("  ❌ FAIL — Could not fetch market data")
                return None
            bid = float(current_price.get("bid", current_price))
            print(f"  ✅ PASS — {len(market_data)} bars | Price: {bid}")
        except Exception as e:
            print(f"  ❌ FAIL — {e}")
            return None

        # GATE 2: Session
        print("\n[GATE 2] Session Check")
        session_norm = "UNKNOWN"
        is_active = False
        try:
            is_active, session_name = is_trading_session()
            session_norm = map_session_for_filter(session_name)
            allowed = session_norm in ["ASIAN_KZ", "LONDON_KZ", "NY_KZ"]
            status = "✅ PASS" if allowed else "⚠️  WARN (outside killzone)"
            print(f"  {status} — Session: {session_norm} | Active: {is_active}")
        except Exception as e:
            print(f"  ❌ FAIL — {e}")
            return None

        # GATE 3: Multi-TF DataFrames
        print("\n[GATE 3] Multi-Timeframe DataFrames")
        try:
            mtf_map = {
                "M5": self.mtf.fetch_data("M5", debug=False),
                "M15": self.mtf.fetch_data("M15", debug=False),
                "H4": self.mtf.fetch_data("H4", debug=False),
                "D1": self.mtf.fetch_data("D1", debug=False),
            }

            missing = []
            stale = []
            valid = []

            for name, info in mtf_map.items():
                df = info.get("df")
                is_stale = info.get("is_stale", False)
                error = info.get("error")
                latest_closed_time = info.get("latest_closed_time")
                latest_visible_time = info.get("latest_visible_time")

                closed_str = str(latest_closed_time)[:16] if latest_closed_time is not None else "N/A"
                visible_str = str(latest_visible_time)[:16] if latest_visible_time is not None else "N/A"

                if df is None:
                    missing.append(name)
                    reason = error or "unknown fetch failure"
                    print(f"  ❌ {name} | fetch failed | reason={reason}")
                    continue

                if is_stale:
                    stale.append(name)
                    print(f"  ⚠️ {name} | closed={closed_str} | visible={visible_str} | stale")
                else:
                    valid.append(name)
                    print(f"  ✅ {name} | closed={closed_str} | visible={visible_str} | fresh")

            # Inactive session: stale is expected, but true fetch failures still matter
            if not is_active:
                if missing:
                    print(f"  ⚠️ WARN — Missing TFs while market inactive: {', '.join(missing)}")
                if stale:
                    print(f"  ⚠️ WARN — Stale TFs while market inactive: {', '.join(stale)}")
                print("  ⏸️ Heartbeat mode — skipping signal-engine evaluation because session is inactive")
                print("=" * 65 + "\n")
                return None

            # Active session: missing or stale is a hard failure
            if missing:
                print(f"  ❌ FAIL — Missing TFs during active session: {', '.join(missing)}")
                return None

            if stale:
                print(f"  ❌ FAIL — Stale TFs during active session: {', '.join(stale)}")
                return None

            m5_df = mtf_map["M5"]["df"]
            m15_df = mtf_map["M15"]["df"]
            h4_df = mtf_map["H4"]["df"]
            d1_df = mtf_map["D1"]["df"]

        except Exception as e:
            print(f"  ❌ FAIL — {e}")
            return None

        # GATES 4-11: Signal Engine evaluation
        print("\n[GATES 4-11] Signal Engine — 8-Gate Sequential Check")
        print("-" * 65)
        try:
            result = self.signal_engine.evaluate(
                m5_df=m5_df,
                m15_df=m15_df,
                h4_df=h4_df,
                d1_df=d1_df,
                now_utc=datetime.now(timezone.utc),
            )

            for gate_name, gate_data in result.gates.items():
                icon = "  ✅" if gate_data.get("passed") else "  ❌"
                reason = gate_data.get("reason", "")
                detail = {k: v for k, v in gate_data.items() if k not in ("passed", "reason")}
                print(f"{icon}  {gate_name:<40} [{reason}]")
                if detail:
                    for dk, dv in detail.items():
                        print(f"         {dk}: {dv}")

            print("\n" + "-" * 65)
            print(f"  🎯 DECISION:    {result.action}")
            print(f"  📌 DIRECTION:   {result.direction}")
            print(f"  🔑 BLOCKED BY:  {result.reason}")
            print(f"  💯 CONFIDENCE:  {result.confidence_score}%")
            if result.entry_price:
                print(f"  💰 ENTRY={result.entry_price}  SL={result.sl_price}  TP={result.tp_price}")

            if result.direction:
                self.htf_memory.update("htf_bias", result.direction)

        except Exception as e:
            print(f"  ❌ FAIL — Signal Engine error: {e}")
            import traceback
            traceback.print_exc()
            return None

        print("=" * 65 + "\n")
        return result

    # ── Persistence ───────────────────────────────────────────────────────────
    def save_trade_log(self, filename: str = "tradelog.json") -> None:
        try:
            with open(filename, "w") as f:
                json.dump(self.trade_log, f, indent=2, default=str)
        except Exception as e:
            print(f"\u274c Error saving trade log: {e}")

    def load_trade_log(self, filename: str = "tradelog.json") -> None:
        try:
            if os.path.exists(filename):
                with open(filename, "r") as f:
                    self.trade_log = json.load(f)
                print(f"\u2705 Loaded {len(self.trade_log)} log entries")
        except Exception as e:
            print(f"\u274c Error loading trade log: {e}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def cleanup(self) -> None:
        try:
            if   hasattr(self.mt5, "shutdown"):    self.mt5.shutdown()
            elif hasattr(self.mt5, "disconnect"):  self.mt5.disconnect()
        except Exception as e:
            print(f"\u26a0\ufe0f Error shutting down MT5: {e}")
        self.save_trade_log()
        print("\u2705 Bot cleaned up")


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    global bot_instance_ref
    
    bot = XAUUSDTradingBot()

    if not bot.initialize():
        print("❌ Bot initialization failed — exiting")
        return

    bot_instance_ref = bot  # Make bot accessible to control server
    
    # ── Start control server in background ─────────────────────────────────
    control_thread = threading.Thread(target=start_control_server, daemon=True)
    control_thread.start()
    print("✅ Control server thread started on port 5000")
    time.sleep(1)  # Give server time to start
    
    bot.load_trade_log()

    print("🔬 Running diagnostics...")
    diag_result = bot.signal_diagnostics()

    try:
        if diag_result is not None and getattr(diag_result, "direction", None):
            bot.htf_memory.update("htf_bias", diag_result.direction)
            print(f"🧭 Synced HTF memory from diagnostics: {diag_result.direction}")
    except Exception as e:
        print(f"⚠️ Failed to sync diagnostic HTF bias into runtime memory: {e}")

    bot.running = True
    print(f"🚀 Bot started (DRY_RUN={bot.dry_run}). Press Ctrl+C to stop.")

    try:
        while bot.running:
            try:
                bot.analyze_once()
            except Exception as e:
                print(f"⚠️ Analysis exception (continuing): {e}")

            for _ in range(60):
                time.sleep(1)
                if not bot.running:
                    break

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    finally:
        bot.running = False
        try:
            obs_logger.bot_stopped()
        except Exception:
            pass
        bot.cleanup()


if __name__ == "__main__":
    main()
