DRY_RUN = True

import os
import time
import json
import threading
from datetime import datetime, timedelta
import pandas as pd
import pytz
import requests
import signal
import sys
from utils.observation_logger import ObservationLogger
obs_logger = ObservationLogger()
obs_logger.bot_started()

# OBSERVATION ONLY — passive OB logger
from utils.ob_observation_logger import OBObservationLogger
ob_obs_logger = OBObservationLogger()
# OBSERVATION ONLY — sanity check log
try:
    ob_obs_logger.log({"event": "LOGGER_INIT", "timestamp": datetime.now().isoformat()})
except Exception as e:
    print(f"⚠️ OBObservationLogger error: {e}")

# Local modules (must exist per your directory structure)
from utils.mt5_connection import MT5Connection
from strategy.multi_timeframe_fractal import MultiTimeframeFractal
from strategy.market_structure import MarketStructureDetector
from strategy.smc_enhanced.zones import ZoneCalculator
from strategy.idea_memory import IdeaMemory
from strategy.smc_enhanced.liquidity import LiquidityDetector
from strategy.smc_enhanced.narrative import NarrativeAnalyzer
from strategy.smc_enhanced.poi import POIIdentifier
from strategy.chart_objects import build_chart_objects
from utils.htf_memory import HTFMemory  # TASK 1 STEP 1

# Note: we no longer rely on direct in-process server imports.
# Communication to the dashboard is done via webhook POST to the server.
# (This keeps bot and server processes decoupled and robust.)

# Telegram config (optional)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "False").lower() == "true"

def send_telegram(message, silent=False):
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": silent
        }, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        print(f" ❌ Telegram error: {e}")
        return None

# ---------- Utilities ----------

def compute_atr_from_df(df: pd.DataFrame, period: int = 14) -> float:
    try:
        if df is None or len(df) == 0:
            return 0.0
        for c in ("high", "low", "close"):
            if c not in df.columns:
                return 0.0
        d = df[['high', 'low', 'close']].astype(float).copy()
        d['prev_close'] = d['close'].shift(1)
        d['tr1'] = d['high'] - d['low']
        d['tr2'] = (d['high'] - d['prev_close']).abs()
        d['tr3'] = (d['low'] - d['prev_close']).abs()
        d['tr'] = d[['tr1', 'tr2', 'tr3']].max(axis=1)
        if len(d['tr'].dropna()) >= period:
            atr = d['tr'].rolling(window=period, min_periods=1).mean().iloc[-1]
        else:
            atr = float(d['tr'].dropna().mean() or 0.0)
        if not atr or atr <= 0:
            recent = d.tail(max(3, len(d)))
            atr = float((recent['high'] - recent['low']).abs().mean() or 0.0)
        return float(atr)
    except Exception as e:
        print(f"❌ compute_atr_from_df error: {e}")
        return 0.0

def is_trading_session():
    """
    ICT Killzone-based session model using New York time.
    
    Sessions:
    - LONDON_KZ: 02:00-05:00 NY time
    - NY_KZ: 07:00-12:00 NY time
    - OFF_KILLZONE: All other times (analysis only, no execution)
    - WEEKEND_MARKET_CLOSED: Saturday and Sunday
    """
    now_utc = datetime.now(pytz.utc)

    # Weekend check (Saturday=5, Sunday=6)
    if now_utc.weekday() >= 5:
        return False, "WEEKEND_MARKET_CLOSED"

    # Convert to New York time
    ny_tz = pytz.timezone("America/New_York")
    now_ny = now_utc.astimezone(ny_tz)

    t = now_ny.hour + now_ny.minute / 60.0

    # London Killzone: 02:00–05:00 NY time
    if 2.0 <= t < 5.0:
        return True, "LONDON_KZ"

    # New York Killzone: 07:00–12:00 NY time
    if 7.0 <= t < 12.0:
        return True, "NY_KZ"

    # Otherwise: analysis only
    return True, "OFF_KILLZONE"

def map_session_for_filter(session_name: str) -> str:
    if session_name is None:
        return "OFF_KILLZONE"
    s = session_name.upper()
    if "LONDON" in s:
        return "LONDON_KZ"
    if "NY" in s:
        return "NY_KZ"
    if "OFF" in s or "KILLZONE" in s:
        return "OFF_KILLZONE"
    if "WEEKEND" in s:
        return "WEEKEND_MARKET_CLOSED"
    return s

# ---------- Dashboard webhook sender ----------
def send_to_dashboard(bot_data: dict, analysis: dict, endpoint: str = "http://localhost:8000/webhook", timeout: float = 3.0):
    """
    Send a JSON snapshot to the dashboard server's /webhook endpoint.
    - Safe: catches exceptions and returns False on failure.
    - Replaces NaN values in serializable data by converting via json.dumps -> replace.
    """

    # --------------------------------------------------
    # PHASE-7A: POI VISUAL OVERLAY FEED
    # --------------------------------------------------
    poi_overlays = []
    print("DEBUG overlays:", poi_overlays)

    try:
        ltf_pois = analysis.get("ltf_pois")

        if ltf_pois:
            if ltf_pois.get("extreme_poi"):
                poi_overlays.append({
                    "type": "extreme",
                    "top": ltf_pois["extreme_poi"]["top"],
                    "bottom": ltf_pois["extreme_poi"]["bottom"]
                })

            if ltf_pois.get("idm_poi"):
                poi_overlays.append({
                    "type": "idm",
                    "top": ltf_pois["idm_poi"]["top"],
                    "bottom": ltf_pois["idm_poi"]["bottom"]
                })

            for mp in ltf_pois.get("median_pois", []):
                poi_overlays.append({
                    "type": "median",
                    "top": mp["top"],
                    "bottom": mp["bottom"]
                })

    except Exception as e:
        print(f"⚠️ POI overlay build failed: {e}")

    try:
        # attach overlays into analysis_data
        analysis_with_overlays = analysis.copy()

        analysis_with_overlays["chart_overlays"] = {
            "poi_zones": poi_overlays
        }

        payload = {
            "bot_instance": bot_data,
            "analysis_data": analysis_with_overlays
        }

        # Attempt to serialize and replace NaN tokens
        try:
            json_payload = json.dumps(payload, default=str)
            if "NaN" in json_payload:
                json_payload = json_payload.replace("NaN", "null")
            resp = requests.post(endpoint, data=json_payload, headers={"Content-Type": "application/json"}, timeout=timeout)
        except TypeError:
            # fallback
            resp = requests.post(endpoint, json=payload, timeout=timeout)

        if resp.status_code == 200:
            return True
        else:
            print(f"   ⚠️ Dashboard POST returned status {resp.status_code}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"   ⚠️ Dashboard POST failed: {e}")
        return False
    except Exception as e:
        print(f"   ❌ Dashboard unexpected error: {e}")
        return False


def graceful_shutdown(signum=None, frame=None):
    print("🛑 Graceful shutdown initiated")
    try:
        obs_logger.bot_stopped()
    except Exception as e:
        print(f"⚠️ Failed to log bot stop: {e}")
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)   # Ctrl+C
signal.signal(signal.SIGTERM, graceful_shutdown)  # Kill / stop


# ---------- Bot ----------

class XAUUSDTradingBot:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.mt5 = MT5Connection(config_path)
        self.mtf = MultiTimeframeFractal(symbol="XAUUSD")
        self.idea_memory = IdeaMemory(expiry_minutes=30)
        # === SMC NARRATIVE STATE MACHINE (PHASE-3B-1) ===
        self.narrative = NarrativeAnalyzer()
        self.zone_calculator = ZoneCalculator
        self.zone_calculator = ZoneCalculator
        self.poi_identifier = None   # initialized later with fresh data

        self.running = False
        self.trade_log = []

        # Internal tracking for BOT initiated positions
        self.open_positions = []

        # === MANUAL TRADE OBSERVATION (ADDED) ===
        # Separate list to track positions found in MT5 that are NOT in self.open_positions
        self.manual_positions = []

        self.max_positions = 3
        self.max_lot_size = 2.0
        self.risk_per_trade_percent = 0.5
        self.current_session = "UNKNOWN"
        self.dry_run = DRY_RUN
        # Reaction logic: waiting for confirmation after HTF POI hit
        self.waiting_for_confirmation = False

        # TASK 1 STEP 2: Add HTFMemory
        self.htf_memory = HTFMemory()

    # MT5 wrappers
    def mt5_initialize(self):
        try:
            if hasattr(self.mt5, "initialize_mt5"):
                return self.mt5.initialize_mt5()
            if hasattr(self.mt5, "initialize"):
                return self.mt5.initialize()
        except Exception as e:
            print(f"❌ MT5 init error: {e}")
        return False

    def mt5_get_account(self):
        try:
            if hasattr(self.mt5, "get_account_info"):
                return self.mt5.get_account_info()
            if hasattr(self.mt5, "account_info"):
                return self.mt5.account_info()
        except Exception:
            pass
        return None

    def mt5_get_current_price(self):
        try:
            if hasattr(self.mt5, "get_current_price"):
                return self.mt5.get_current_price()
            if hasattr(self.mt5, "get_price"):
                return self.mt5.get_price()
        except Exception:
            pass
        return None

    def mt5_get_historical(self, bars=300):
        try:
            if hasattr(self.mt5, "get_historical_data"):
                return self.mt5.get_historical_data(bars=bars)
            if hasattr(self.mt5, "history"):
                return self.mt5.history(bars)
        except Exception:
            pass
        return None

    # === MANUAL TRADE OBSERVATION (ADDED) ===
    def mt5_get_all_positions(self):
        """
        Robust, unified accessor for live MT5 positions.
        Handles various wrapper implementations and return types safely.
        Returns a list of standardized dictionaries.
        """
        positions_raw = None

        # 1. Try various method names common in wrappers
        try:
            if hasattr(self.mt5, "positions_get"):
                positions_raw = self.mt5.positions_get()
            elif hasattr(self.mt5, "get_positions"):
                positions_raw = self.mt5.get_positions()
            elif hasattr(self.mt5, "get_open_positions"):
                positions_raw = self.mt5.get_open_positions()
            # 2. Try direct MT5 access if wrapper exposes it
            elif hasattr(self.mt5, "mt5") and hasattr(self.mt5.mt5, "positions_get"):
                positions_raw = self.mt5.mt5.positions_get()
        except Exception as e:
            print(f"⚠️ Error fetching live positions: {e}")
            return []

        if positions_raw is None:
            return []

        # 3. Normalize Result
        normalized = []
        try:
            # Handle Tuple/List inputs
            iterable = positions_raw
            if not isinstance(positions_raw, (list, tuple)):
                iterable = [positions_raw] # Single object case

            for p in iterable:
                p_dict = {}
                # Extract data based on type
                if isinstance(p, dict):
                    p_dict = p
                elif hasattr(p, "_asdict"):
                    p_dict = p._asdict()
                else:
                    # Generic Object access
                    p_dict = {
                        "ticket": getattr(p, "ticket", 0),
                        "type": getattr(p, "type", 0),
                        "volume": getattr(p, "volume", 0.0),
                        "price_open": getattr(p, "price_open", 0.0),
                        "sl": getattr(p, "sl", 0.0),
                        "tp": getattr(p, "tp", 0.0),
                        "symbol": getattr(p, "symbol", ""),
                        "price_current": getattr(p, "price_current", 0.0),
                        "profit": getattr(p, "profit", 0.0)
                    }

                # Ensure critical keys exist and are typed correctly
                if p_dict.get("ticket"):
                    normalized.append({
                        "ticket": int(p_dict.get("ticket", 0)),
                        "type": int(p_dict.get("type", 0)), # 0=Buy, 1=Sell usually
                        "volume": float(p_dict.get("volume", 0.0)),
                        "price_open": float(p_dict.get("price_open", 0.0)),
                        "sl": float(p_dict.get("sl", 0.0)),
                        "tp": float(p_dict.get("tp", 0.0)),
                        "symbol": str(p_dict.get("symbol", "")),
                        "price_current": float(p_dict.get("price_current", 0.0)),
                        "profit": float(p_dict.get("profit", 0.0))
                    })
        except Exception as e:
            print(f"⚠️ Error normalizing positions: {e}")
            return []

        return normalized
    # ========================================

    def mt5_place_order(self, side, lots, sl, tp):
        try:
            if self.dry_run:
                print(f" ⚠️ DRY_RUN enabled — simulated order: {side} {lots} lots SL={sl} TP={tp}")
                return f"DRY-{int(time.time())}"
            if hasattr(self.mt5, "place_order"):
                return self.mt5.place_order(side, lots, sl, tp)
            if hasattr(self.mt5, "order_send"):
                return self.mt5.order_send(side, lots, sl, tp)
        except Exception as e:
            print(f"❌ place_order error: {e}")
        return None

    def mt5_close_position(self, ticket, volume=None):
        try:
            if hasattr(self.mt5, "close_position"):
                return self.mt5.close_position(ticket, volume) if volume else self.mt5.close_position(ticket)
            if hasattr(self.mt5, "close_trade"):
                return self.mt5.close_trade(ticket, volume) if volume else self.mt5.close_trade(ticket)
        except Exception as e:
            print(f"❌ close position error: {e}")
        return False

    def mt5_modify_position(self, ticket, sl=None, tp=None):
        try:
            if hasattr(self.mt5, "modify_position"):
                return self.mt5.modify_position(ticket, sl, tp)
            if hasattr(self.mt5, "modify_trade"):
                return self.mt5.modify_trade(ticket, sl, tp)
        except Exception as e:
            print(f"❌ modify position error: {e}")
        return False

    def initialize(self):
        print("=== Initializing XAUUSDTradingBot ===")
        if not self.mt5_initialize():
            print("❌ MT5 initialization failed")
            return False

        acct = self.mt5_get_account()
        if acct:
            try:
                bal = float(acct.balance)
                self.risk_per_trade_percent = getattr(acct, "risk_per_trade", self.risk_per_trade_percent) or self.risk_per_trade_percent
                print(f"✅ Account balance: ${bal:,.2f}")
            except Exception:
                print("ℹ️ Could not read account balance cleanly")
        else:
            print("ℹ️ No account info available (continuing in read-only/test mode)")

        print("✅ Initialization complete")
        return True

    def fetch_and_prepare(self):
        market_data = self.mt5_get_historical(bars=300)
        if market_data is None:
            print("❌ Could not fetch historical data")
            return None, None
        if not isinstance(market_data, pd.DataFrame):
            try:
                market_data = pd.DataFrame(market_data)
            except Exception:
                print("❌ Historical data conversion failed")
                return None, None
        for c in ("high", "low", "close", "open", "tick_volume"):
            if c in market_data.columns:
                market_data[c] = pd.to_numeric(market_data[c], errors="coerce")

        current_price = self.mt5_get_current_price()
        if current_price is None:
            print("❌ Could not fetch current price")
            return market_data, None
        return market_data, current_price

    # === MANUAL TRADE OBSERVATION (ADDED) ===
    def detect_and_manage_manual_trades(self, analysis_context):
        """
        Detects trades that exist in MT5 but are not tracked in self.open_positions.
        These are flagged as MANUAL.
        It then applies SMC analysis to generate advisory logs.
        """
        try:
            # 1. Fetch all live positions from MT5 (Normalized)
            live_pos_list = self.mt5_get_all_positions()
            if live_pos_list is None:
                live_pos_list = []

            # DEBUG: Print found positions to ensure connectivity
            if len(live_pos_list) > 0:
                print(f"🔎 DEBUG: MT5 reports {len(live_pos_list)} open positions.")

            # 2. Identify Bot Ticket IDs
            bot_tickets = [int(p.get("ticket", 0)) for p in self.open_positions]

            # 3. Sync Logic
            current_manual_tickets = []

            for pos in live_pos_list:
                ticket = int(pos.get("ticket", 0))
                symbol = pos.get("symbol", "")

                # Filter for XAUUSD (or current symbol) only - CASE INSENSITIVE FIX
                sym_upper = symbol.upper()
                if "XAU" not in sym_upper and "GOLD" not in sym_upper:
                    # DEBUG: Print why we are skipping
                    print(f"⚠️ DEBUG: Skipping position {ticket} (Symbol: {symbol} not XAU/GOLD)")
                    continue

                # TASK 2: Manual trade detection
                # If this ticket is NOT in bot_tickets, it is MANUAL
                if ticket not in bot_tickets:
                    current_manual_tickets.append(ticket)

                    # Check if we are already tracking this manual trade
                    existing_manual = next((item for item in self.manual_positions if item["ticket"] == ticket), None)

                    if not existing_manual:
                        # === NEW MANUAL TRADE DETECTED ===
                        trade_type_code = pos.get("type", 0)
                        trade_type = "BUY" if trade_type_code == 0 else "SELL"
                        entry_price = float(pos.get("price_open", 0.0))

                        # Apply SMC Intelligence
                        advisory = "HOLD"
                        rationale = []

                        trend = analysis_context.get("market_structure", {}).get("current_trend", "NEUTRAL")
                        zone = analysis_context.get("current_zone", "UNKNOWN")

                        # Trend Alignment
                        if trade_type == "BUY":
                            if trend == "BULLISH": rationale.append("Aligned with Bullish Trend")
                            elif trend == "BEARISH": rationale.append("Counter-trend (High Risk)")
                        else: # SELL
                            if trend == "BEARISH": rationale.append("Aligned with Bearish Trend")
                            elif trend == "BULLISH": rationale.append("Counter-trend (High Risk)")

                        # Zone Alignment
                        if zone == "DISCOUNT" and trade_type == "BUY": rationale.append("Buying in Discount (Good)")
                        if zone == "PREMIUM" and trade_type == "BUY": rationale.append("Buying in Premium (Risk)")
                        if zone == "PREMIUM" and trade_type == "SELL": rationale.append("Selling in Premium (Good)")
                        if zone == "DISCOUNT" and trade_type == "SELL": rationale.append("Selling in Discount (Risk)")

                        advisory_str = "; ".join(rationale) if rationale else "Neutral structure"

                        print(f"👀 MANUAL TRADE DETECTED: Ticket {ticket} | {trade_type} @ {entry_price}")
                        print(f"   🤖 SMC Analysis: {advisory_str}")

                        # Register
                        new_manual = {
                            "ticket": ticket,
                            "origin": "MANUAL",
                            "signal": trade_type,
                            "entry_price": entry_price,
                            "volume": pos.get("volume"),
                            "sl": pos.get("sl"),
                            "tp": pos.get("tp"),
                            "entry_time": datetime.now().isoformat(),
                            "advisory": advisory_str,
                            "status": "OPEN",
                            "symbol": symbol,
                            "type": trade_type_code,
                            "profit": pos.get("profit", 0.0),
                            "price_open": entry_price,
                            "price_current": pos.get("price_current", 0.0),
                            "source": "MANUAL"  # TASK 2 (Add 'source': 'MANUAL')
                        }
                        self.manual_positions.append(new_manual)

                        # Log to persistent log
                        self.trade_log.append({
                            "timestamp": datetime.now().isoformat(),
                            "action": "MANUAL_DETECTED",
                            "ticket": ticket,
                            "details": new_manual
                        })
                        self.save_trade_log()
                    else:
                        # Update dynamic fields (SL/TP could change manually)
                        existing_manual["sl"] = pos.get("sl")
                        existing_manual["tp"] = pos.get("tp")
                        existing_manual["current_price"] = pos.get("price_current", 0.0) # If available

            # 4. Cleanup Closed Manual Trades
            # Remove trades from self.manual_positions that are no longer in live_positions (tickets)
            active_manual_tickets = set(current_manual_tickets)
            for m_pos in list(self.manual_positions):
                if m_pos["ticket"] not in active_manual_tickets:
                    print(f"🏁 Manual Trade {m_pos['ticket']} Closed/Removed")
                    self.manual_positions.remove(m_pos)
                    self.trade_log.append({
                        "timestamp": datetime.now().isoformat(),
                        "action": "MANUAL_CLOSED",
                        "ticket": m_pos["ticket"]
                    })
                    self.save_trade_log()

            # TASK 2: Ensure manual trades are visible in open_positions
            # This block guarantees that manual trades appear in the open_positions list and are passed to dashboard
            manual_tickets_set = set([pm["ticket"] for pm in self.manual_positions])
            for manual_pos in self.manual_positions:
                # Check if not already injected in open_positions
                if not any(op.get("ticket", 0) == manual_pos["ticket"] for op in self.open_positions):
                    self.open_positions.append(manual_pos)

        except Exception as e:
            print(f"⚠️ Manual trade sync error: {e}")
    # ========================================

    def analyze_once(self):
        is_active, session_name = is_trading_session()
        session_norm = map_session_for_filter(session_name)
        self.current_session = session_norm
        print(f"\n🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Session: {self.current_session}")

        # TASK 1 STEP 4: Print stored HTF bias on startup / first cycle
        stored_bias = self.htf_memory.get("h4_bias", "NEUTRAL")
        print("HTF MEMORY BIAS:", stored_bias)

        # ============================================================================== 
        # MARKET CLOSED (WEEKEND ONLY)
        # ============================================================================== 
        if not is_active:
            print(f"⏸️ Market session '{session_norm}' not active — heartbeat only")
            acct = self.mt5_get_account()
            equity = float(getattr(acct, 'equity', 0.0)) if acct else 0.0
            balance = float(getattr(acct, 'balance', 0.0)) if acct else 0.0

            # Narrative state logging (even when market closed)
            narrative_state = "MARKET_CLOSED"
            entry_allowed = False
            structure_state = {"current_trend": "MARKET CLOSED"}
            bias = "NEUTRAL"
            log_record = {
                "time": datetime.now().isoformat(),
                "narrative_state": narrative_state,
                "entry_allowed": entry_allowed,
                "structure_state": structure_state,
                "bias": bias,
                "reason": f"Market session '{session_norm}' not active"
            }
            self.trade_log.append(log_record)
            self.save_trade_log()
            try:
                obs_logger.log_event("narrative_state", log_record)
            except Exception:
                pass

            send_to_dashboard(
                {
                    "equity": equity,
                    "balance": balance,
                    "last_price": 0,
                    "open_positions": self.open_positions,
                    "manual_positions": self.manual_positions,
                    "closed_trades": [],
                    "chart_data": [],
                    "current_session": session_norm
                },
                {
                    "market_structure": {"current_trend": "MARKET CLOSED"},
                    "zone_strength": 0,
                    "current_zone": "CLOSED",
                    "zones": {}
                }
            )
            return

        # ============================================================================== 
        # ACTIVE MARKET (Analysis runs 24/5)
        # ============================================================================== 
        market_data, current_price = self.fetch_and_prepare()
        if market_data is None or current_price is None:
            return

        bid = float(current_price.get("bid", current_price))
        ask = float(current_price.get("ask", bid))
        spread = abs(ask - bid)
        price_for_zones = bid
        #day 8
        # --- MTF Bias --- 
        try:
            print("DEBUG: Running MTF analysis")
            mtf_conf = self.mtf.get_multi_tf_confluence()

            print("\n📊 MULTI-TIMEFRAME FRACTAL ANALYSIS (DEBUG)")
            print("=================================================")
            print(f"Overall Bias: {mtf_conf.get('overall_bias')}")
            print(f"Confidence: {mtf_conf.get('confidence')}")

            tf_signals = mtf_conf.get("tf_signals", {})

            if isinstance(tf_signals, dict):
                for tf_name, tf_entry in tf_signals.items():
                    bias = tf_entry.get("bias", "UNKNOWN")

                    bos_block = tf_entry.get("bos", {})
                    choc_block = tf_entry.get("choc", {})

                    bos = bos_block.get("bullish_bos") or bos_block.get("bearish_bos")
                    choch = choc_block.get("bullish_choc") or choc_block.get("bearish_choc")

                    print(f"{tf_name}: bias={bias} | BOS={bool(bos)} | CHoCH={bool(choch)}")

            print("=================================================\n")

        except Exception as e:
            print("❌ MTF ERROR:", str(e))
            mtf_conf = {"overall_bias": "NEUTRAL", "confidence": 0}


        # --- H4 bias calculation (TASK 1 STEP 3) ---
        try:
            # Placeholder: Assume H4 bias is calculated here, e.g. from an H4 analysis module or similar logic.
            h4_bias = mtf_conf.get("H4", {}).get("bias", "NEUTRAL")
        except Exception:
            h4_bias = "NEUTRAL"
        print("H4 bias detected:", h4_bias) 
        self.htf_memory.update("h4_bias", h4_bias)  # TASK 1 STEP 3

        # --- Market Structure --- 
        try:
            ms_detector = MarketStructureDetector(market_data)
            smc_state = ms_detector.get_idm_state()
            if not isinstance(smc_state, dict): smc_state = {}

            if smc_state.get("idm_type") == "bullish": ms = {"current_trend": "BULLISH"}
            elif smc_state.get("idm_type") == "bearish": ms = {"current_trend": "BEARISH"}
            else: ms = {"current_trend": "NEUTRAL"}
        except Exception as e:
            print("❌ Market structure error:", e)
            ms = {"current_trend": "NEUTRAL"}
            smc_state = {}

        # --- Zones --- 
        try:
            latest_high = float(market_data['high'].max())
            latest_low = float(market_data['low'].min())
            zones = ZoneCalculator.calculate_zones(latest_high, latest_low)
            current_zone = ZoneCalculator.classify_price_zone(price_for_zones, zones)
            zone_strength = 0
        except Exception:
            current_zone = "UNKNOWN"; zone_strength = 0; zones = {}

        # --- Manual trade observation --- 
        self.detect_and_manage_manual_trades({
            "market_structure": ms, "current_zone": current_zone,
            "zone_strength": zone_strength, "mtf_bias": mtf_conf
        })

        # --- Liquidity Detection --- 
        external_sweep = False
        try:
            liquidity_detector = LiquidityDetector(market_data)
            liq_result = liquidity_detector.check_liquidity_grab(current_price=bid)
            external_sweep = liq_result.get("pdh_grabbed", False) or liq_result.get("pdl_grabbed", False)
        except Exception as e:
            print(f"⚠️ Liquidity detection error: {e}")

        # ======================================================================
        # 🔥 PHASE-6/7 — LTF POI DETECTION & OVERLAYS
        # ======================================================================
        poi_mitigated = False
        displacement_confirmed = False
        ltf_pois = {} # <--- PHASE-7: Store for visual dashboard

        try:
            ltf_df = self.mtf.fetch_data("M5")
            if ltf_df is not None and len(ltf_df) > 10:
                poi_identifier = POIIdentifier(ltf_df)
                idm_type = smc_state.get("idm_type")

                if idm_type == "bullish":
                    direction = "bullish"
                elif idm_type == "bearish":
                    direction = "bearish"
                else:
                    direction = None

                if direction:
                    shift_start = max(0, len(ltf_df) - 50)   # temporary safe window
                    shift_end = len(ltf_df) - 1

                    ltf_pois = poi_identifier.detect_ltf_pois(
                        shift_start=shift_start,
                        shift_end=shift_end,
                        direction=direction,
                        df=ltf_df
                    )
                else:
                    ltf_pois = {}

                extreme_poi = ltf_pois.get("extreme_poi")
                if extreme_poi:
                    poi_mitigated = poi_identifier.is_poi_mitigated(extreme_poi, ltf_df)
                    if poi_mitigated:
                        displacement_confirmed = poi_identifier.is_displacement_after_poi(
                            extreme_poi, direction, ltf_df
                        )
        except Exception as e:
            print(f"⚠️ Phase-6/7 POI logic failed: {e}")

        # ============================================================================== 
        # 🔥 PHASE-3B-1 — NARRATIVE ENGINE
        # ============================================================================== 
        market_state = {
            "trading_range_defined": True,
            "external_liquidity_swept": external_sweep,
            "idm_taken": smc_state.get("is_idm_swept", False),
            "htf_poi_reached": external_sweep,

            # FIXED LINE
            "ltf_structure_shift": smc_state.get("structure_confirmed", False),

            "ltf_poi_mitigated": poi_mitigated,
            "killzone_active": self.current_session in ["LONDON_KZ", "NY_KZ"],
            "htf_ob_invalidated": False,
            "daily_structure_flipped": False
        }

        
        print("NARRATIVE CHECK:")
        print("ltf_structure_shift:", market_state.get("ltf_structure_shift"))
        print("ltf_poi_mitigated:", market_state.get("ltf_poi_mitigated"))
        print("killzone_active:", market_state.get("killzone_active"))

        narrative_snapshot = self.narrative.update(market_state)

        # Narrative state logging (required: every cycle)
        bias = None
        try:
            from strategy.smc_enhanced.bias import BiasAnalyzer
            bias_analyzer = BiasAnalyzer()
            bias_result = bias_analyzer.get_bias(smc_state)
            bias = bias_result.get("bias", "NEUTRAL") if isinstance(bias_result, dict) else "NEUTRAL"
        except Exception:
            bias = "NEUTRAL"

        log_record = {
            "time": datetime.now().isoformat(),
            "narrative_state": narrative_snapshot.get("state"),
            "entry_allowed": narrative_snapshot.get("entry_allowed"),
            "structure_state": smc_state,
            "bias": bias,
            "reason": narrative_snapshot.get("state") if not narrative_snapshot.get("entry_allowed") else "Entry permitted"
        }
        self.trade_log.append(log_record)
        self.save_trade_log()
        try:
            obs_logger.log_event("narrative_state", log_record)
        except Exception:
            pass

        # TASK 3: Narrative explanation print
        narrative_state = narrative_snapshot.get("state", "UNKNOWN")
        print(f"🧠 Market Narrative: {narrative_state} — structure context for this minute")

        # ====================================================================== 
        # 📊 PHASE-7 — SEND TO DASHBOARD (ALWAYS)
        # ====================================================================== 
        acct = self.mt5_get_account()
        dash_payload = {
            "equity": float(getattr(acct, 'equity', 0.0)) if acct else 0.0,
            "balance": float(getattr(acct, 'balance', 0.0)) if acct else 0.0,
            "last_price": bid,
            "open_positions": self.open_positions,
            "manual_positions": self.manual_positions,
            "closed_trades": [],
            "chart_data": market_data.tail(300).to_dict(orient="records"),
            "current_session": self.current_session
        }

        # === 🟦 Build chart objects module integration ===

        # Debug: check what SMC engine is producing
        print("SMC STATE:", smc_state)
        print("LTF POIS:", ltf_pois)

        chart_objects = build_chart_objects(
            smc_state, zones, ltf_pois, bid
        )

        # Debug: check final chart objects
        print("CHART OBJECTS:", chart_objects)

        analysis_snapshot = {
            # Core structure info
            "market_structure": ms,
            "zone_strength": zone_strength,
            "current_zone": current_zone,

            # HTF zones
            "zones": zones,

            # Daily levels
            "pdh": float(market_data['high'].max()),
            "pdl": float(market_data['low'].min()),

            # LTF POIs (legacy overlay system)
            "ltf_pois": ltf_pois,

            # New unified chart objects
            "chart_objects": chart_objects
        }

        # Send to dashboard
        send_to_dashboard(dash_payload, analysis_snapshot)

        # ============================================================================== 
        # 🚀 EXECUTION GATE (Pre-filtered Institutional Model)
        # ============================================================================== 
        if not narrative_snapshot.get("entry_allowed", False):
            print(f"⏸ No trade — Narrative: {narrative_snapshot.get('state')}")
            return

        # ================================================================
        # INSTITUTIONAL PRE-FILTER GATES (All must pass before signal generation)
        # ================================================================
        try:
            execution_allowed = True
            gate_reason = None
            
            # Gate 1: Session must be killzone
            if self.current_session not in ["LONDON_KZ", "NY_KZ"]:
                execution_allowed = False
                gate_reason = "Session not in killzone"
            
            # Gate 2: Liquidity sweep must be true
            if execution_allowed and not external_sweep:
                execution_allowed = False
                gate_reason = "No liquidity sweep"
            
            # Gate 3: Position limit (no open positions)
            if execution_allowed and len(self.open_positions) > 0:
                execution_allowed = False
                gate_reason = "Position already open"
            
            # If any gate fails, exit execution block
            if not execution_allowed:
                print(f"⛔ Execution blocked: {gate_reason}")
                return
            
            # ================================================================
            # SIGNAL GENERATION (Only after all pre-filter gates pass)
            # ================================================================
            trend = ms.get("current_trend", "NEUTRAL")
            signal = None
            
            if trend == "BULLISH":
                # Check BUY-specific gates
                if current_zone == "DISCOUNT" and stored_bias == "BULLISH":
                    signal = "BUY"
                else:
                    # Explain why BUY was not generated
                    reasons = []
                    if current_zone != "DISCOUNT":
                        reasons.append(f"zone is {current_zone}, need DISCOUNT")
                    if stored_bias != "BULLISH":
                        reasons.append(f"HTF bias is {stored_bias}, need BULLISH")
                    print(f"⛔ BUY signal rejected: {'; '.join(reasons)}")
                    return
            
            elif trend == "BEARISH":
                # Check SELL-specific gates
                if current_zone == "PREMIUM" and stored_bias == "BEARISH":
                    signal = "SELL"
                else:
                    # Explain why SELL was not generated
                    reasons = []
                    if current_zone != "PREMIUM":
                        reasons.append(f"zone is {current_zone}, need PREMIUM")
                    if stored_bias != "BEARISH":
                        reasons.append(f"HTF bias is {stored_bias}, need BEARISH")
                    print(f"⛔ SELL signal rejected: {'; '.join(reasons)}")
                    return
            
            else:
                print(f"⏸️ No clear trend for execution (Trend: {trend})")
                return
            
            # ================================================================
            # STRUCTURAL SL & LIQUIDITY-BASED TP (Only if signal generated)
            # ================================================================
            if signal:
                # Calculate recent swing high/low (last 20 bars)
                recent_high = float(market_data['high'].tail(20).max())
                recent_low = float(market_data['low'].tail(20).min())
                
                # Define buffer
                buffer = 0.3
                
                if signal == "BUY":
                    # SL = recent swing low - buffer
                    sl = recent_low - buffer
                    # TP = recent swing high (structural liquidity)
                    tp = recent_high
                    print(f"📈 BUY Signal generated | SL = {sl:.2f} (Recent Low: {recent_low:.2f}) | TP = {tp:.2f} (Recent High: {recent_high:.2f})")
                
                elif signal == "SELL":
                    # SL = recent swing high + buffer
                    sl = recent_high + buffer
                    # TP = recent swing low (structural liquidity)
                    tp = recent_low
                    print(f"📉 SELL Signal generated | SL = {sl:.2f} (Recent High: {recent_high:.2f}) | TP = {tp:.2f} (Recent Low: {recent_low:.2f})")
                
                # Place order
                lot_size = 0.01  # Fixed small lot size
                
                print(f"🎯 Placing {signal} order: {lot_size} lots | SL={sl:.2f} | TP={tp:.2f}")
                ticket = self.mt5_place_order(signal, lot_size, sl, tp)
                
                if ticket:
                    print(f"✅ Order placed successfully | Ticket: {ticket}")
                    # Log order placement
                    self.open_positions.append({
                        "ticket": ticket,
                        "signal": signal,
                        "lot_size": lot_size,
                        "sl": sl,
                        "tp": tp,
                        "entry_price": bid,
                        "entry_time": datetime.now().isoformat(),
                        "status": "OPEN"
                    })
                    self.trade_log.append({
                        "timestamp": datetime.now().isoformat(),
                        "action": "ORDER_PLACED",
                        "ticket": ticket,
                        "signal": signal,
                        "lot_size": lot_size,
                        "sl": sl,
                        "tp": tp,
                        "entry_price": bid
                    })
                    self.save_trade_log()
                else:
                    print(f"❌ Order placement failed")
        
        except Exception as e:
            print(f"❌ Execution logic error: {e}")

    # Persistence
    def save_trade_log(self, filename="tradelog.json"):
        try:
            with open(filename, "w") as f:
                json.dump(self.trade_log, f, indent=2, default=str)
        except Exception as e:
            print(f"❌ Error saving trade log: {e}")

    def load_trade_log(self, filename="tradelog.json"):
        try:
            if os.path.exists(filename):
                with open(filename, "r") as f:
                    self.trade_log = json.load(f)
                print(f"✅ Loaded {len(self.trade_log)} log entries")
        except Exception as e:
            print(f"❌ Error loading trade log: {e}")

    def cleanup(self):
        try:
            if hasattr(self.mt5, "shutdown"):
                self.mt5.shutdown()
            elif hasattr(self.mt5, "disconnect"):
                self.mt5.disconnect()
        except Exception as e:
            print(f"⚠️ Error shutting down MT5 connection: {e}")
        self.save_trade_log()
        print("✅ Bot cleaned up")

# CLI main
def main():
    bot = XAUUSDTradingBot()
    if not bot.initialize():
        print("❌ Bot initialization failed — exiting")
        return

    bot.load_trade_log()
    bot.running = True
    print("🚀 Bot started (DRY_RUN=%s). Press Ctrl+C to stop." % bot.dry_run)

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