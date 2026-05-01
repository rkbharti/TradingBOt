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
from vps_reporter import ping_health, post_signal, post_trade_result

# ── Globals ───────────────────────────────────────────────────────────────────
DRY_RUN = True  # Set to False for live trading
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
    endpoint: str = "http://localhost:8000/webhook",
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

        self.signal_engine = SignalEngine(SignalEngineConfig())
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
        self.order_executor: Optional[OrderExecutor] = None  # created after MT5 init
        self.audit_logger = AuditLogger(
            log_path="logs/decisions/audit.jsonl",
            symbol="XAUUSD",
            timeframe="M5",
        )
        self.magic_number            = BOT_MAGIC_NUMBER  # ← wired here, used in order requests
        self._peak_balance: float    = 0.0
        self._daily_pnl_pct: float   = 0.0
        self._trades_today: int      = 0
        self._consecutive_losses: int = 0
        self._last_trade_time: Optional[datetime] = None
        self._session_date: Optional[date] = None

        # ── State ─────────────────────────────────────────────────────────────
        self.running                  = False
        self.trade_log: list          = []
        self.open_positions: list     = []
        self.manual_positions: list   = []
        self.max_positions            = 3
        self.max_lot_size             = 2.0
        self.risk_per_trade_percent   = 0.5
        self.current_session          = "UNKNOWN"
        self.dry_run                  = DRY_RUN
        self.waiting_for_confirmation = False

    # ── MT5 wrappers ──────────────────────────────────────────────────────────
    def mt5_initialize(self) -> bool:
        try:
            if hasattr(self.mt5, "initialize_mt5"): return self.mt5.initialize_mt5()
            if hasattr(self.mt5, "initialize"):      return self.mt5.initialize()
        except Exception as e:
            print(f"\u274c MT5 init error: {e}")
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
        try:
            if   hasattr(self.mt5, "positions_get"):     positions_raw = self.mt5.positions_get()
            elif hasattr(self.mt5, "get_positions"):      positions_raw = self.mt5.get_positions()
            elif hasattr(self.mt5, "get_open_positions"): positions_raw = self.mt5.get_open_positions()
            elif hasattr(self.mt5, "mt5") and hasattr(self.mt5.mt5, "positions_get"):
                positions_raw = self.mt5.mt5.positions_get()
        except Exception as e:
            print(f"\u26a0\ufe0f Error fetching live positions: {e}")
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
                    normalized.append({
                        "ticket":        int(p_dict.get("ticket", 0)),
                        "type":          int(p_dict.get("type", 0)),
                        "volume":        float(p_dict.get("volume", 0.0)),
                        "price_open":    float(p_dict.get("price_open", 0.0)),
                        "sl":            float(p_dict.get("sl", 0.0)),
                        "tp":            float(p_dict.get("tp", 0.0)),
                        "symbol":        str(p_dict.get("symbol", "")),
                        "price_current": float(p_dict.get("price_current", 0.0)),
                        "profit":        float(p_dict.get("profit", 0.0)),
                    })
        except Exception as e:
            print(f"\u26a0\ufe0f Error normalizing positions: {e}")
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

            # ── Determine PnL ────────────────────────────────────────────────
            # Best case: MT5 gave us a live profit field when we stored the pos.
            # Fallback: estimate from SL/TP distance (direction-aware).
            raw_pnl = pos.get("profit", None)

            if raw_pnl is None or raw_pnl == 0.0:
                # Estimate: did price reach TP or SL?
                # We don't know for certain so we skip was_win logic here —
                # better to not count it than to count it wrong.
                print(f"  ⚠️  Ticket {ticket}: no profit field — skipping policy update")
                continue

            pnl      = float(raw_pnl)
            was_win  = pnl > 0

            # ── Update challenge policy internal state ────────────────────────
            self.challenge_policy.log_trade_result(
                was_win=was_win,
                pnl=pnl,
                current_balance=current_balance,
            )

            # ── Sync bot-level shadow counters ────────────────────────────────
            self._consecutive_losses = self.challenge_policy.consecutive_losses
            self._peak_balance       = self.challenge_policy.peak_balance

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
                        max_drawdown=round(cp.max_daily_drawdown, 2),
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
        # ── Remote pause check ────────────────────────────────────────
        if not check_bot_active():
            print("⏸️ Bot paused via dashboard — skipping cycle")
            return

        self.sync_closed_positions()

        # ── Trailing stop / breakeven management ──────────────────────────────
        if self.open_positions:
            acct_trail  = self.mt5_get_account()
            price_trail = self.mt5_get_current_price()
            if price_trail is not None:
                bid_trail = float(price_trail.get("bid", 0.0)) if isinstance(price_trail, dict) else float(price_trail)
                if bid_trail > 0:
                    self._manage_open_positions_trailing(bid_trail)

        # ── Lockdown gate ─────────────────────────────────────────────────────
        _acct_early = self.mt5_get_account()
        _bal_early  = float(getattr(_acct_early, "balance", self._peak_balance or 100000.0)) if _acct_early else (self._peak_balance or 100000.0)
        lockdown_reason = self.challenge_policy.get_lockdown_reason(
            daily_pnl_pct=self._daily_pnl_pct,
            peak_balance=self._peak_balance or _bal_early,
            current_balance=_bal_early,
            consecutive_losses=self._consecutive_losses,
        )
        if lockdown_reason:
            print(f"\U0001f6d1 LOCKDOWN: {lockdown_reason} — skipping cycle (wait 60s)")
            try:
                self.audit_logger.log_lockdown(
                    lockdown_reason,
                    self._build_policy_state_snapshot(_bal_early),
                )
            except Exception as _e:
                print(f"\u26a0\ufe0f Audit lockdown log failed: {_e}")
            time.sleep(60)
            return

        is_active, session_name = is_trading_session()
        session_norm = map_session_for_filter(session_name)
        self.current_session = session_norm
        print(f"\n\U0001f552 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Session: {self.current_session}")

        stored_bias = self.htf_memory.get("htf_bias", "NEUTRAL")
        print("HTF MEMORY BIAS:", stored_bias)

        # ── Weekend / market closed ───────────────────────────────────────────
        if not is_active:
            print(f"\u23f8\ufe0f Market session '{session_norm}' not active — heartbeat only")
            acct    = self.mt5_get_account()
            equity  = float(getattr(acct, "equity",  0.0)) if acct else 0.0
            balance = float(getattr(acct, "balance", 0.0)) if acct else 0.0

            log_record = {
                "time":            datetime.now().isoformat(),
                "narrative_state": "MARKET_CLOSED",
                "entry_allowed":   False,
                "structure_state": {"current_trend": "MARKET CLOSED"},
                "bias":            "NEUTRAL",
                "reason":          f"Market session '{session_norm}' not active",
            }
            self.trade_log.append(log_record)
            self.save_trade_log()
            try:
                obs_logger.log_event("narrative_state", log_record)
            except Exception:
                pass

            send_to_dashboard(
                {
                    "equity": equity, "balance": balance, "last_price": 0,
                    "open_positions":   self.open_positions,
                    "manual_positions": self.manual_positions,
                    "closed_trades": [], "chart_data": [],
                    "current_session":  session_norm,
                },
                {
                    "market_structure": {"current_trend": "MARKET CLOSED"},
                    "zone_strength": 0, "current_zone": "CLOSED", "zones": {},
                },
            )
            return

        # ── Fetch market data ─────────────────────────────────────────────────
        market_data, current_price = self.fetch_and_prepare()
        if market_data is None or current_price is None:
            return

        bid    = float(current_price.get("bid", current_price))
        ask    = float(current_price.get("ask", bid))
        spread = abs(ask - bid)

        # ── Fetch all timeframe DataFrames ────────────────────────────────────
        m5_df  = self.mtf.fetch_data("M5")
        m15_df = self.mtf.fetch_data("M15")
        h4_df  = self.mtf.fetch_data("H4")
        d1_df  = self.mtf.fetch_data("D1")

        if any(df is None or len(df) == 0 for df in [m5_df, m15_df, h4_df, d1_df]):
            print("\u274c One or more timeframe DataFrames unavailable — skipping cycle")
            return

        # ── Manual trade observation ──────────────────────────────────────────
        self.detect_and_manage_manual_trades({
            "market_structure": {"current_trend": stored_bias},
            "current_zone":     "UNKNOWN",
        })

        # ── CANONICAL SIGNAL ENGINE ───────────────────────────────────────────
        print("\n\U0001f52c Running SMC Signal Engine (8-gate sequential check)...")
        try:
            result = self.signal_engine.evaluate(
                m5_df=m5_df,
                m15_df=m15_df,
                h4_df=h4_df,
                d1_df=d1_df,
                now_utc=datetime.now(timezone.utc),
            )
        except Exception as e:
            print(f"\u274c SignalEngine error: {e}")
            import traceback; traceback.print_exc()
            return

        # Update HTF memory from engine-confirmed direction
        if result.direction:
            self.htf_memory.update("htf_bias", result.direction)
            stored_bias = result.direction

        # ── Print gate results ────────────────────────────────────────────────
        print("\n\U0001f4ca SIGNAL ENGINE — GATE RESULTS")
        print("=" * 58)
        for gate_name, gate_data in result.gates.items():
            icon   = "\u2705" if gate_data.get("passed") else "\u274c"
            reason = gate_data.get("reason", "")
            print(f"  {icon}  {gate_name:<40} {reason}")
        print("=" * 58)
        print(f"  \U0001f3af ACTION:     {result.action}")
        print(f"  \U0001f4cc DIRECTION:  {result.direction}")
        print(f"  \U0001f511 REASON:     {result.reason}")
        print(f"  \U0001f4af CONFIDENCE: {result.confidence_score}%")
        if result.entry_price:
            print(f"  \U0001f4b0 ENTRY: {result.entry_price}  SL: {result.sl_price}  TP: {result.tp_price}")
        print("=" * 58 + "\n")

        # ── Log cycle ─────────────────────────────────────────────────────────
        log_record = {
            "time":             datetime.now().isoformat(),
            "narrative_state":  result.reason,
            "entry_allowed":    result.action == "ENTER",
            "action":           result.action,
            "direction":        result.direction,
            "entry_price":      result.entry_price,
            "sl_price":         result.sl_price,
            "tp_price":         result.tp_price,
            "confidence_score": result.confidence_score,
            "gates":            result.gates,
            "bias":             result.direction or "NEUTRAL",
            "session":          session_norm,
        }
        self.trade_log.append(log_record)
        self.save_trade_log()
        try:
            obs_logger.log_event("signal_result", log_record)
        except Exception:
            pass

        # ── Dashboard payload ─────────────────────────────────────────────────
        acct     = self.mt5_get_account()
        htf_gate = result.gates.get("step_1_htf_bias",       {})
        dr_gate  = result.gates.get("step_6_dealing_range",  {})
        rr_gate  = result.gates.get("step_8_risk_reward",    {})

        # Build chart_objects using existing module (keep dashboard overlay working)
        try:
            chart_objects = build_chart_objects({}, {}, {}, bid)
        except Exception:
            chart_objects = {}

        analysis_snapshot = {
            "market_structure": {
                "current_trend": result.direction or htf_gate.get("h4_bias", "NEUTRAL"),
                "d1_bias":       htf_gate.get("d1_bias", "NEUTRAL"),
                "h4_bias":       htf_gate.get("h4_bias", "NEUTRAL"),
            },
            "zone_strength": result.confidence_score,
            "current_zone":  dr_gate.get("zone", "UNKNOWN"),
            "zones": {
                "dealing_low":  dr_gate.get("dealing_low"),
                "dealing_high": dr_gate.get("dealing_high"),
                "equilibrium":  dr_gate.get("equilibrium"),
            },
            "pdh":           float(market_data["high"].max()),
            "pdl":           float(market_data["low"].min()),
            "ltf_pois":      {},
            "chart_objects": chart_objects,
            # Full engine output for dashboard drill-down panel
            "signal_engine": {
                "action":           result.action,
                "direction":        result.direction,
                "entry_price":      result.entry_price,
                "sl_price":         result.sl_price,
                "tp_price":         result.tp_price,
                "reason":           result.reason,
                "confidence_score": result.confidence_score,
                "rr":               rr_gate.get("rr"),
                "gates":            result.gates,
            },
        }

        send_to_dashboard(
            {
                "equity":           float(getattr(acct, "equity",  0.0)) if acct else 0.0,
                "balance":          float(getattr(acct, "balance", 0.0)) if acct else 0.0,
                "last_price":       bid,
                "open_positions":   self.open_positions,
                "manual_positions": self.manual_positions,
                "closed_trades":    [],
                "chart_data":       market_data.tail(300).to_dict(orient="records"),
                "current_session":  self.current_session,
            },
            analysis_snapshot,
        )

        # ── Execution gate ────────────────────────────────────────────────────
        if len(self.open_positions) > 0:
            print(f"\u26d4 BLOCKED: POSITION_ALREADY_OPEN ({len(self.open_positions)} open)")
            return

        if result.action != "ENTER":
            print(f"\u26d4 NO_TRADE: {result.reason}")
            return

        # ── All 8 gates passed — execute via OrderExecutor ───────────────────
        acct           = self.mt5_get_account()
        account_balance = float(getattr(acct, "balance", 100000.0)) if acct else 100000.0
        if account_balance > self._peak_balance:
            self._peak_balance = account_balance

        policy_snap = self._build_policy_state_snapshot(account_balance)

        # Build a minimal SignalResult-compatible dict for the executor
        # (OrderExecutor.execute_signal expects a SignalResult with poi_zone /
        #  liquidity_levels; when those are unavailable we pass safe defaults)
        try:
            executor_signal = ExecutorSignalResult(
                action=result.action or "BUY",
                direction=result.direction or "BULLISH",
                entry_price=float(result.entry_price or 0),
                sl_price=float(result.sl_price or 0),
                tp_price=float(result.tp_price or 0),
                poi_zone=getattr(result, "poi_zone", None) or {
                    "top":    float(result.entry_price or 0) + 5,
                    "bottom": float(result.entry_price or 0),
                    "mt":     float(result.entry_price or 0) + 2.5,
                },
                liquidity_levels=getattr(result, "liquidity_levels", None) or {
                    "pdh":         float(result.tp_price or 0),
                    "pdl":         float(result.sl_price or 0),
                    "weekly_high": float(result.tp_price or 0),
                    "weekly_low":  float(result.sl_price or 0),
                },
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
            print(f"\u274c OrderExecutor error: {e}")
            import traceback; traceback.print_exc()
            return

        # ── Audit log every evaluation ────────────────────────────────────────
        try:
            self.audit_logger.log_evaluation(result, exec_result, policy_snap)
        except Exception as e:
            print(f"\u26a0\ufe0f Audit log failed: {e}")

        if not exec_result.success:
            print(f"\u26d4 OrderExecutor rejected: {exec_result.rejection_reason}")
            return

        lot_size   = exec_result.lot_size
        trade_side = result.action  # "BUY" / "SELL"
        final_sl   = exec_result.sl_price
        final_tp   = exec_result.tp_price

        print(f"\U0001f680 ENTERING {trade_side} | Entry={result.entry_price} | SL={final_sl} | TP={final_tp} | RR={exec_result.rr_ratio:.2f}x | Lot={lot_size}")

        ticket = self.mt5_place_order(trade_side, lot_size, final_sl, final_tp)

        if ticket:
            print(f"\u2705 Order placed | Ticket: {ticket}")
            self._trades_today += 1
            self._last_trade_time = datetime.now()

            position_record = {
                "ticket":           ticket,
                "signal":           trade_side,
                "lot_size":         lot_size,
                "sl":               final_sl,
                "tp":               final_tp,
                "entry_price":      result.entry_price,
                "rr_ratio":         exec_result.rr_ratio,
                "risk_amount":      exec_result.risk_amount,
                "entry_time":       datetime.now().isoformat(),
                "status":           "OPEN",
                "source":           "SIGNAL_ENGINE",
                "confidence_score": result.confidence_score,
            }
            self.open_positions.append(position_record)
            self.trade_log.append({
                "timestamp": datetime.now().isoformat(),
                "action":    "ORDER_PLACED",
                **position_record,
            })
            self.save_trade_log()

            send_telegram(
                f"\U0001f680 <b>{trade_side}</b> entered @ {result.entry_price}\n"
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
            print("\u274c Order placement failed")

    # ── Diagnostics ───────────────────────────────────────────────────────────
    def signal_diagnostics(self) -> None:
        """
        Runs full 8-gate check against live market data and prints structured output.
        Replaces the old manual gate-by-gate diagnostic that called legacy modules.
        """
        print("\n" + "=" * 65)
        print("\U0001f52c  SMC SIGNAL ENGINE — LIVE DIAGNOSTICS")
        print("=" * 65)

        # GATE 0: MT5
        print("\n[GATE 0] MT5 Connection")
        try:
            if not self.mt5_initialize():
                print("  \u274c FAIL — MT5 could not initialize"); return
            print("  \u2705 PASS — MT5 connected")
        except Exception as e:
            print(f"  \u274c FAIL — {e}"); return

        # GATE 1: Market data
        print("\n[GATE 1] Market Data Fetch")
        try:
            market_data, current_price = self.fetch_and_prepare()
            if market_data is None or current_price is None:
                print("  \u274c FAIL — Could not fetch market data"); return
            bid = float(current_price.get("bid", current_price))
            print(f"  \u2705 PASS — {len(market_data)} bars | Price: {bid}")
        except Exception as e:
            print(f"  \u274c FAIL — {e}"); return

        # GATE 2: Session
        print("\n[GATE 2] Session Check")
        try:
            is_active, session_name = is_trading_session()
            session_norm = map_session_for_filter(session_name)
            allowed = session_norm in ["ASIAN_KZ", "LONDON_KZ", "NY_KZ"]
            status  = "\u2705 PASS" if allowed else "\u26a0\ufe0f  WARN (outside killzone)"
            print(f"  {status} — Session: {session_norm} | Active: {is_active}")
        except Exception as e:
            print(f"  \u274c FAIL — {e}")

        # GATE 3: Multi-TF DataFrames
        print("\n[GATE 3] Multi-Timeframe DataFrames")
        m5_df = m15_df = h4_df = d1_df = None
        try:
            m5_df  = self.mtf.fetch_data("M5")
            m15_df = self.mtf.fetch_data("M15")
            h4_df  = self.mtf.fetch_data("H4")
            d1_df  = self.mtf.fetch_data("D1")
            for name, df in [("M5", m5_df), ("M15", m15_df), ("H4", h4_df), ("D1", d1_df)]:
                if df is None or len(df) == 0:
                    print(f"  \u274c FAIL — {name} DataFrame empty or None"); return
                print(f"  \u2705  {name}: {len(df)} bars")
        except Exception as e:
            print(f"  \u274c FAIL — {e}"); return

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
                icon   = "  \u2705" if gate_data.get("passed") else "  \u274c"
                reason = gate_data.get("reason", "")
                detail = {k: v for k, v in gate_data.items() if k not in ("passed", "reason")}
                print(f"{icon}  {gate_name:<40} [{reason}]")
                if detail:
                    for dk, dv in detail.items():
                        print(f"         {dk}: {dv}")

            print("\n" + "-" * 65)
            print(f"  \U0001f3af DECISION:    {result.action}")
            print(f"  \U0001f4cc DIRECTION:   {result.direction}")
            print(f"  \U0001f511 BLOCKED BY:  {result.reason}")
            print(f"  \U0001f4af CONFIDENCE:  {result.confidence_score}%")
            if result.entry_price:
                print(f"  \U0001f4b0 ENTRY={result.entry_price}  SL={result.sl_price}  TP={result.tp_price}")

        except Exception as e:
            print(f"  \u274c FAIL — Signal Engine error: {e}")
            import traceback; traceback.print_exc()

        print("=" * 65 + "\n")

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
    bot = XAUUSDTradingBot()

    if not bot.initialize():
        print("\u274c Bot initialization failed — exiting")
        return

    bot.load_trade_log()

    print("\U0001f52c Running diagnostics...")
    bot.signal_diagnostics()

    bot.running = True
    print(f"\U0001f680 Bot started (DRY_RUN={bot.dry_run}). Press Ctrl+C to stop.")

    try:
        while bot.running:
            try:
                bot.analyze_once()
            except Exception as e:
                print(f"\u26a0\ufe0f Analysis exception (continuing): {e}")

            # 60-second interval with interrupt-aware sleep
            for _ in range(60):
                time.sleep(1)
                if not bot.running:
                    break

    except KeyboardInterrupt:
        print("\n\U0001f6d1 Stopped by user")
    finally:
        bot.running = False
        try:
            obs_logger.bot_stopped()
        except Exception:
            pass
        bot.cleanup()


if __name__ == "__main__":
    main()
