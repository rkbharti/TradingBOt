DRY_RUN = True

import time
import json
import pandas as pd
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from utils.mt5_connection import MT5Connection
from strategy.smc_strategy import SMCStrategy
from strategy.stoploss_calc import StopLossCalculator
from strategy.multi_timeframe_fractal import MultiTimeframeFractal
from strategy.market_structure import MarketStructureDetector
from strategy.smc_enhanced.zones import ZoneCalculator
from strategy.idea_memory import IdeaMemory
import MetaTrader5 as mt5
import threading
import sys
import os
import requests

import pytz
from utils.xauusd_filter import XAUUSDFilter
from utils.volume_analyzer_gold import GoldVolumeAnalyzer
from utils.smart_exits import SmartExitManager
from dotenv import load_dotenv
load_dotenv()
QUIET_LOGGING = True


# ============================================================
# MARKET HOURS & SESSION MANAGEMENT (Added Jan 12, 2026)
# ============================================================

def is_market_open():
    """
    Check if Forex market is open
    Prevents trading during weekends and validates MT5 connection
    """
    import MetaTrader5 as mt5

    now = datetime.now()
    day = now.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

    # Market closed on weekends
    if day == 5:  # Saturday
        print(f"⏸️  Market CLOSED - Saturday")
        return False

    if day == 6:  # Sunday
        print(f"⏸️  Market CLOSED - Sunday")
        return False

    # Friday closes at 02:00 IST Saturday morning
    if day == 4 and now.hour >= 2:
        print(f"⏸️  Market CLOSED - Friday close")
        return False

    # Market opens Monday 03:30 IST
    if day == 0 and now.hour < 3:
        print(f"⏸️  Market opens at 03:30 IST Monday")
        return False

    # Validate MT5 connection and tick data
    try:
        current_tick = mt5.symbol_info_tick("XAUUSD")

        if current_tick is None:
            print("⚠️  MT5 connection lost - cannot get tick data")
            return False

        # Check for zero spread (indicates market closed)
        spread = current_tick.ask - current_tick.bid
        if spread == 0:
            print("⚠️  Zero spread detected - market likely closed")
            return False

        # Check for frozen price (no movement for long time)
        # This is already handled by your existing logic, just validate here

        return True

    except Exception as e:
        print(f"❌ Error checking market status: {e}")
        return False


def is_trading_session():
    """
    SINGLE SOURCE OF TRUTH for session detection (UTC)
    Returns: (is_tradeable: bool, session_name: str)

    Session Times (UTC):
      - LONDON: 08:00 to 16:00 UTC
      - NEW_YORK: 13:00 to 21:00 UTC
      - NY_OVERLAP: 13:00 to 16:00 UTC
      - Outside these windows -> return False, "ASIAN"
    """
    # Use global UTC time to avoid local clock errors
    now = datetime.now(pytz.utc)
    hour = now.hour
    minute = now.minute
    t = hour + minute / 60.0

    # LONDON window (08:00 - 16:00 UTC)
    if 8.0 <= t < 16.0:
        # Overlap between London and New York (13:00-16:00 UTC)
        if 13.0 <= t < 16.0:
            return True, "NY_OVERLAP"
        return True, "LONDON"

    # New York session after London (16:00 - 21:00 UTC)
    if 16.0 <= t < 21.0:
        return True, "NY_SESSION"

    # Low-liquidity / Asian session
    return False, "ASIAN"


def get_mtf_signal_consensus(mtf_m15, mtf_m30, mtf_h1):
    """
    Multi-timeframe consensus using 2/3 majority rule
    More realistic than requiring all 3 timeframes to align

    Args:
        mtf_m15: M15 timeframe bias ("BULLISH", "BEARISH", or "NEUTRAL")
        mtf_m30: M30 timeframe bias
        mtf_h1: H1 timeframe bias

    Returns:
        str: "BUY", "SELL", or "HOLD"
    """
    mtf_signals = [mtf_m15, mtf_m30, mtf_h1]

    # Count votes
    bullish_count = mtf_signals.count("BULLISH")
    bearish_count = mtf_signals.count("BEARISH")

    # 2 out of 3 agreement
    if bullish_count >= 2:
        return "BUY"
    elif bearish_count >= 2:
        return "SELL"
    else:
        return "HOLD"


def log_trade_analysis(zone, zone_strength, mtf_m15, mtf_m30, mtf_h1,
                       mtf_signal, spread, final_signal, reason, price):
    """
    Detailed logging for forensic analysis
    Helps identify why trades are blocked
    """

    print(f"\n{'='*70}")
    print(f"📊 TRADE ANALYSIS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*70}")
    print(f"   💰 Price: ${price:.2f}")
    print(f"   📍 Zone: {zone} (Strength: {zone_strength:.1%})")
    print(f"   📈 MTF M15: {mtf_m15}")
    print(f"   📈 MTF M30: {mtf_m30}")
    print(f"   📈 MTF H1: {mtf_h1}")
    print(f"   🎯 MTF Consensus: {mtf_signal}")
    print(f"   💸 Spread: ${spread:.4f}")
    print(f"   🚦 SIGNAL: {final_signal}")
    print(f"   📝 Reason: {reason}")
    print(f"{'='*70}\n")

# ============================================================
# END OF NEW FUNCTIONS
# ============================================================



# ============================================================
# NEW: ATR CALCULATION UTILITY (FIX #ATR)
# ============================================================
def compute_atr_from_df(df: pd.DataFrame, period: int = 14) -> float:
    """
    Compute ATR (Average True Range) from a DataFrame with 'high','low','close' columns.
    Returns last ATR value (float). If insufficient data, returns the best-effort ATR.
    """
    try:
        if df is None or len(df) == 0:
            return 0.0

        # Ensure necessary columns exist
        for col in ['high', 'low', 'close']:
            if col not in df.columns:
                return 0.0

        # Work on a copy to avoid side effects
        d = df[['high', 'low', 'close']].copy().astype(float)

        # Previous close
        d['prev_close'] = d['close'].shift(1)

        # True range calculations
        d['tr1'] = d['high'] - d['low']
        d['tr2'] = (d['high'] - d['prev_close']).abs()
        d['tr3'] = (d['low'] - d['prev_close']).abs()

        d['tr'] = d[['tr1', 'tr2', 'tr3']].max(axis=1)

        # ATR using simple rolling mean (approx of Wilder)
        if len(d['tr'].dropna()) >= period:
            atr = d['tr'].rolling(window=period, min_periods=1).mean().iloc[-1]
        else:
            # fallback: use mean of available TR values
            atr = float(d['tr'].dropna().mean() or 0.0)

        # If ATR is zero (edge cases), fallback to recent high-low mean
        if atr <= 0:
            recent = d.tail(max(3, len(d)))
            atr = float((recent['high'] - recent['low']).abs().mean() or 0.0)

        return float(atr)
    except Exception as e:
        print(f"❌ compute_atr_from_df error: {e}")
        return 0.0

# ============================================================
# SESSION NAME NORMALIZER (Fix for filter mismatch)
# ============================================================
def map_session_for_filter(session_name: str) -> str:
    """
    Normalize different session names to the canonical session names used by filters:
      - "NY_OVERLAP" -> "OVERLAP"
      - "NY_SESSION" -> "NEW_YORK"
      - "LONDON" -> "LONDON"
      - "ASIAN" -> "ASIAN"
    This prevents mismatches between is_trading_session() and XAUUSDFilter expectations.
    """
    if session_name is None:
        return "ASIAN"
    s = session_name.upper()
    if s in ("NY_OVERLAP", "NY-OVERLAP", "NY_OVER", "OVERLAP"):
        return "OVERLAP"
    if s in ("NY_SESSION", "NY-SESSION", "NY", "NEW_YORK", "NYSESSION"):
        return "NEW_YORK"
    if "LONDON" in s:
        return "LONDON"
    if "ASIAN" in s:
        return "ASIAN"
    return session_name.upper()


# ========================================
# TELEGRAM NOTIFICATION SYSTEM
# ========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "True").lower() == "true"

def send_telegram(message, silent=False):
    """
    Send notification to Telegram

    Args:
        message (str): Message to send (supports HTML formatting)
        silent (bool): If True, sends without notification sound

    Returns:
        dict: Response from Telegram API
    """
    if not ENABLE_TELEGRAM:
        return None

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": silent
        }
        response = requests.post(url, data=data, timeout=30)

        if response.status_code == 200:
            print("   ✅ Telegram notification sent")
            return response.json()
        else:
            print(f"   ⚠️ Telegram error: {response.status_code}")
            return None

    except Exception as e:
        print(f"   ❌ Telegram error: {e}")
        return None

print("✅ Telegram notification system loaded!")


# ========================================
# ZONE VS BIAS CONFIGURATION
# ========================================

# Strong zone threshold for counter-trend trades
STRONG_ZONE_THRESHOLD = 30  # 70%+ zones can override bias
WEAK_ZONE_THRESHOLD = 15    # Below 30% = too weak to trade

# Allow strong zones to override conflicting bias
ENABLE_STRONG_ZONE_OVERRIDE = True

# Override mode for testing (bypass strict filters)
ENABLE_ZONE_OVERRIDE = True  # ✅ FIXED: Allow zone-based signal generation!

print(f"⚙️  Zone Configuration:")
print(f"   Strong Zone Threshold: {STRONG_ZONE_THRESHOLD}%")
print(f"   Weak Zone Threshold: {WEAK_ZONE_THRESHOLD}%")
print(f"   Strong Zone Override: {ENABLE_STRONG_ZONE_OVERRIDE}")
print(f"   Testing Override Mode: {ENABLE_ZONE_OVERRIDE}")


# ===== CRITICAL: FIXED CONFIGURATION LOADING =====
def load_config_with_safety(config_path="config.json"):
    """Load config with safety defaults for risk management"""
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)

        # Extract trading parameters with safety defaults
        trading_params = config.get("trading_parameters", {})

        # CRITICAL SAFETY VALUES - Override if missing
        risk_per_trade = trading_params.get("risk_per_trade", 0.25)  # Default 0.5% (safe)
        min_sl_distance = trading_params.get("min_sl_distance_pips", 35.0)  # Minimum 10 pips
        max_lot_size = trading_params.get("max_lot_size", 2.0)  # Cap at 2.0 lots
        max_positions = trading_params.get("max_positions", 3)  # Max 3 positions

        print(f"✅ Safety Parameters Loaded:")
        print(f"   Risk per Trade: {risk_per_trade}%")
        print(f"   Min SL Distance: {min_sl_distance} pips")
        print(f"   Max Lot Size: {max_lot_size} lots")
        print(f"   Max Positions: {max_positions}")

        return config, risk_per_trade, min_sl_distance, max_lot_size, max_positions
    except Exception as e:
        print(f"⚠️  Config load error: {e}, using safe defaults")
        return {}, 0.5, 10.0, 2.0, 3

# ===== RISK CALCULATOR IMPROVEMENTS =====
def calculate_lot_size_with_safety(account_balance, risk_percent, entry_price, stop_loss,
                                    max_lot=2.0, min_sl_pips=10.0):
    """
    Calculate lot size with CRITICAL safety caps
    - Ensures minimum 10 pip SL distance
    - Caps max lot size to prevent overleveraging
    - Returns adjusted SL if it was too tight
    """
    symbol_point = 0.01  # For XAUUSD
    min_sl_distance = min_sl_pips * symbol_point

    # ===== FIX #1: Ensure minimum SL distance =====
    original_sl = stop_loss
    if abs(entry_price - stop_loss) < min_sl_distance:
        if entry_price > stop_loss:  # BUY trade
            stop_loss = entry_price - (min_sl_pips * symbol_point)
        else:  # SELL trade
            stop_loss = entry_price + (min_sl_pips * symbol_point)
        print(f"   ⚠️  SL too tight ({abs(entry_price - original_sl) / symbol_point:.1f} pips)")
        print(f"   Adjusted from {original_sl:.2f} → {stop_loss:.2f}")

    # Calculate risk amount
    risk_amount = account_balance * (risk_percent / 100)

    # Calculate pips at risk
    pips_at_risk = abs(entry_price - stop_loss) / symbol_point

    # Calculate lot size
    if pips_at_risk > 0:
        # For XAUUSD: pip value = 10 per standard lot
        lot_size = risk_amount / (pips_at_risk * 10)
    else:
        lot_size = 0.1

    # ===== FIX #2: CAP THE LOT SIZE (THIS IS THE CRITICAL FIX) =====
    original_lot = lot_size
    lot_size = min(lot_size, max_lot)

    if original_lot > max_lot:
        print(f"   ⚠️  Lot size capped from {original_lot:.2f} → {lot_size:.2f} lots")

    # Round to nearest 0.01
    lot_size = round(lot_size, 2)

    # Ensure minimum lot
    lot_size = max(lot_size, 0.01)

    # FINAL ENFORCEMENT: Strictly enforce max_lot before returning
    lot_size = min(lot_size, max_lot)

    return lot_size, stop_loss


# ===== ENHANCED XAUUSD TRADING BOT =====
try:
    from strategy.smc_enhanced.liquidity import LiquidityDetector
    from strategy.smc_enhanced.poi import POIIdentifier
    from strategy.smc_enhanced.bias import BiasDetector
    from strategy.smc_enhanced.zones import ZoneCalculator
    from strategy.smc_enhanced.narrative import NarrativeAnalyzer
    SMC_ENHANCED_AVAILABLE = True
    print("✅ Guardeer 10-Video SMC Enhanced Modules Loaded Successfully!")
except ImportError as e:
    SMC_ENHANCED_AVAILABLE = False
    print(f"⚠️  Warning: SMC Enhanced modules not available: {e}")
    print("   Bot will run with standard SMC strategy only")

# ===== DASHBOARD SERVER IMPORT (FIXED) =====
import sys
import os

# 1. Ensure current directory is in Python path
sys.path.append(os.getcwd())

DASHBOARD_AVAILABLE = False
update_bot_state = None
update_bot_state_v2 = None
pnl_tracker = None

try:
    # Try to import server helper if available (in-process)
    import server as _server_module
    update_bot_state_v2 = getattr(_server_module, "update_bot_state_v2", None)
    pnl_tracker = getattr(_server_module, "pnl_tracker", None)
    if update_bot_state_v2:
        DASHBOARD_AVAILABLE = True
        print("✅ Dashboard integration loaded successfully (in-process)")
except Exception as e:
    # Server may be run as a separate process - we'll use HTTP POST fallback later
    update_bot_state_v2 = None
    pnl_tracker = None
    print(f"ℹ️ Dashboard not imported in-process: {e}")

# =====================================================
# 🛡️ PRODUCTION SAFETY CONFIGURATION
# =====================================================

# Risk Management Limits
MAX_POSITIONS_PER_DIRECTION = 2  # Only 2 BUYs OR 2 SELLs at once
MAX_TOTAL_POSITIONS = 3  # Never more than 3 trades total
DAILY_LOSS_LIMIT_PERCENT = 2.0  # Stop trading at -2% daily loss
MAX_CONSECUTIVE_LOSSES = 2  # Pause after 3 losses in a row
MIN_STOP_LOSS_PIPS = 35  # Minimum 20 pip SL (for M5)
MAX_STOP_LOSS_PIPS = 40  # Maximum 40 pip SL (prevent huge losses)

# Trend Filter Settings (H1 timeframe)
TREND_EMA_FAST = 20  # Fast EMA for trend
TREND_EMA_SLOW = 50  # Slow EMA for trend
USE_TREND_FILTER = True  # CRITICAL: Enable trend protection

# Session-Based Configuration
SESSION_CONFIG = {
    "LONDON": {"zone_threshold": 35, "rr_ratio": 2.5, "active": True},
    "NEW_YORK": {"zone_threshold": 40, "rr_ratio": 2.5, "active": True},
    "ASIAN": {"zone_threshold": 60, "rr_ratio": 2.0, "active": False},  # Avoid Asian (low liquidity)
    "OVERLAP": {"zone_threshold": 30, "rr_ratio": 3.0, "active": True}  # London+NY overlap
}


class XAUUSDTradingBot:
    """Enhanced trading bot with Guardeer's complete 10-video SMC strategy for XAUUSD"""

    def __init__(self, config_path="config.json", use_enhanced_smc=True):
        import logging
        import os
        from datetime import datetime

        self.ideamemory = IdeaMemory(expiry_minutes=30)
        print("✅ IdeaMemory initialized successfully")  # Add this line

        # ===== LOGGER SETUP (TERMINAL + FILE) =====
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(
            log_dir,
            f"tradingbot_{datetime.now().strftime('%Y-%m-%d')}.log"
        )

        self.logger = logging.getLogger("XAUUSDTradingBot")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        if not self.logger.handlers:
            # Console
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)

            # File
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)

            self.logger.addHandler(console_handler)
            self.logger.addHandler(file_handler)

        self.logger.info("🧠 Logger initialized (terminal + file)")

        self.config_path = config_path
        self.mtf_analyzer = MultiTimeframeFractal(symbol="XAUUSD")
        self.market_structure = None  # Will be initialized when data is available

        # ===== LOAD CONFIG WITH SAFETY =====
        self.config, self.risk_per_trade, self.min_sl_pips, self.max_lot_size, self.max_positions = \
            load_config_with_safety(config_path)

        self.mt5 = MT5Connection(config_path)
        # --- DAILY RISK BASELINE ---
        account_info = self.mt5.get_account_info()
        self.daily_start_balance = (
            float(account_info.balance) if account_info else 0.0
        )
        self.last_balance_reset_date = datetime.now().date()

        self.strategy = SMCStrategy()
        self.risk_calculator = None
        self.running = False
        self.trade_log = []
        self.open_positions = []

        # Production Safety Tracking
        self.daily_loss_limit_triggered = False
        self.consecutive_losses = 0
        self.consecutive_loss_pause = False
        self.consecutive_loss_pause_until = None
        self.last_trade_result = None  # 'WIN' or 'LOSS'

        self.last_signal = "HOLD"
        self.last_analysis = {}

        self.use_enhanced_smc = use_enhanced_smc and SMC_ENHANCED_AVAILABLE
        self.liquidity_detector = None
        self.poi_identifier = None
        self.bias_detector = None
        self.narrative_analyzer = None
        self.enhanced_analysis_data = {}

        # ===== FIX #3: COOLDOWN TRACKER FOR BUY/SELL SIGNALS =====
        self.last_buy_time = None
        self.last_sell_time = None
        self.COOLDOWN_SECONDS = 300  # 5 minutes between same-direction trades
        self.idea_memory = IdeaMemory(expiry_minutes=30)

        # ===== FIX #4: TRAILING STOPS TRACKER =====
        self.partial_profit_taken = {}  # Track which positions already took partial profit
        self.trailing_stop_levels = {}  # Track trailing stop levels

        # Hook to send Telegram from instance methods
        self.send_telegram = send_telegram

        # Dashboard update function (if imported at module level)
        # We copy module-level variable here to instance for easier usage
        self.update_bot_state_v2 = update_bot_state_v2
        self.pnl_tracker = pnl_tracker

        if self.use_enhanced_smc:
            print("✅ Using Guardeer 10-Video Enhanced SMC Strategy")
        else:
            print("⚠️  Using Standard SMC Strategy")

    def cleanup(self):
        """Safe shutdown - closes MT5, saves logs"""
        try:
            print("🧹 Cleaning up...")
            if hasattr(self, 'mt5'):
                # ✅ FIX: Use the correct method name 'shutdown'
                self.mt5.shutdown()
            print("✅ Bot shutdown complete")
        except Exception as e:
            print(f"⚠️ Cleanup warning: {e}")

    def sync_closed_positions(self):
        """Detect closed MT5 positions, evaluate outcome, update IdeaMemory, and clean open_positions."""
        try:
            # Safety check - ensure ideamemory exists
            if not hasattr(self, 'ideamemory') or self.ideamemory is None:
                print("⚠️  IdeaMemory not initialized - skipping closed position logging")
                # Still sync positions even if memory isn't available
                if not self.open_positions:
                    return

                mt5_positions = self.mt5.positions_get(symbol="XAUUSD")
                open_tickets = {p.ticket for p in mt5_positions} if mt5_positions else set()
                closed = [p for p in self.open_positions if p['ticket'] not in open_tickets]

                for position in closed:
                    self.open_positions.remove(position)
                    print(f"✅ Position {position['ticket']} closed")
                return

            # Original code continues...
            if not self.open_positions:
                return

            mt5_positions = self.mt5.positions_get(symbol="XAUUSD")
            open_tickets = {p.ticket for p in mt5_positions} if mt5_positions else set()
            closed = [p for p in self.open_positions if p['ticket'] not in open_tickets]

            for position in closed:
                ticket = position['ticket']
                deals = self.mt5.history_deals_get(ticket=ticket)
                profit = sum(d.profit for d in deals) if deals else 0.0
                outcome = "WIN" if profit > 0 else "LOSS"

                # Create idea_id from position metadata
                signal = position.get('signal', 'UNKNOWN')
                zone = position.get('zone', 'UNKNOWN')
                session = position.get('session', 'UNKNOWN')
                idea_id = f"{signal}|{zone}|{session}|{ticket}"

                # Create result dict with all the info mark_result expects
                result = {
                    'result': outcome.lower(),  # 'win' or 'loss' (lowercase)
                    'profit': profit,
                    'signal': signal,
                    'zone': zone,
                    'session': session,
                    'ticket': ticket
                }

                # Call mark_result with correct signature: (idea_id, result)
                try:
                    self.ideamemory.mark_result(idea_id, result)
                except Exception:
                    pass

                self.open_positions.remove(position)
                print(f"📊 TRADE CLOSED: {outcome} | {signal} {zone} {session} | PnL: ${profit:.2f}")

        except Exception as e:
            print(f"❌ sync_closed_positions error: {str(e)}")

    def check_trade_cooldown(self, signal_type):
        """
        Prevent multiple trades in same direction too quickly
        Cooldown: 5 minutes between BUY trades or SELL trades

        Returns:
            True = Allow trade (cooldown cleared)
            False = Block trade (cooldown active)
        """

        now = datetime.now()
        cooldown_seconds = 300  # 5 minutes = 300 seconds

        if signal_type == 'BUY':
            if self.last_buy_time is not None:
                time_since = (now - self.last_buy_time).total_seconds()

                if time_since < cooldown_seconds:
                    # COOLDOWN ACTIVE - BLOCK TRADE
                    remaining = cooldown_seconds - time_since
                    print(f"\n   ⏳ BUY cooldown active: {remaining:.0f}s remaining ({remaining/60:.1f} min)")
                    print(f"   🚫 TRADE BLOCKED - Last BUY was {time_since:.0f}s ago")
                    return False  # ← Block immediately after check

            # Cooldown cleared - update timestamp and allow
            print(f"\n   ✅ BUY cooldown cleared - Trade allowed")
            return True

        elif signal_type == 'SELL':
            if self.last_sell_time is not None:
                time_since = (now - self.last_sell_time).total_seconds()

                if time_since < cooldown_seconds:
                    # COOLDOWN ACTIVE - BLOCK TRADE
                    remaining = cooldown_seconds - time_since
                    print(f"\n   ⏳ SELL cooldown active: {remaining:.0f}s remaining ({remaining/60:.1f} min)")
                    print(f"   🚫 TRADE BLOCKED - Last SELL was {time_since:.0f}s ago")
                    return False  # ← Block immediately after check

            # Cooldown cleared - update timestamp and allow
            print(f"\n   ✅ SELL cooldown cleared - Trade allowed")
            return True

        # Unknown signal type - allow by default
        return True

    # ===== FIX #1: ZONE-BASED EXIT LOGIC =====
    def check_zone_based_exits(self, current_zone, current_price):
        """
        Smart Exit: Close trades if they reach the opposing zone, BUT ONLY IF PROFITABLE.
        """
        closed_trades = []
        # CONFIG: Minimum profit required to trigger a smart exit (in dollars)
        MIN_PROFIT_DOLLARS = 15.0

        if not self.open_positions:
            return closed_trades

        # Iterate over a COPY of the list [:] to safely modify the original during loop
        for pos in self.open_positions[:]:
            ticket = pos.get('ticket')
            pos_type = pos.get('signal') # 'BUY' or 'SELL'
            
            # Use tracked profit or calculate approximate
            profit = pos.get('profit', 0.0)
            if profit == 0.0:
                entry = pos.get('price', pos.get('entry_price', 0.0))
                lot = pos.get('lot_size', 0.01)
                if pos_type == 'BUY':
                    profit = (current_price - entry) * lot * 100
                else:
                    profit = (entry - current_price) * lot * 100

            should_close = False
            reason = ""

            # 1. Buy Logic (Close in PREMIUM)
            if pos_type == 'BUY' and current_zone == 'PREMIUM':
                if profit > MIN_PROFIT_DOLLARS:
                    should_close = True
                    reason = f"Taking Profit: BUY in PREMIUM zone (Profit: ${profit:.2f})"
                else:
                    # Optional: limit log spam
                    pass

            # 2. Sell Logic (Close in DISCOUNT)
            elif pos_type == 'SELL' and current_zone == 'DISCOUNT':
                if profit > MIN_PROFIT_DOLLARS:
                    should_close = True
                    reason = f"Taking Profit: SELL in DISCOUNT zone (Profit: ${profit:.2f})"

            # Execute Close
            if should_close:
                print(f"\n   🎯 SMART EXIT TRIGGERED: #{ticket}")
                print(f"      Type: {pos_type} | Zone: {current_zone}")
                print(f"      Profit: ${profit:.2f}")

                # ✅ FIX: Use bot's internal close method (handles MT5 logic)
                close_result = self.close_trade(ticket) 
                
                # ✅ FIX: Handle Boolean vs Dict return types safely
                success = False
                if isinstance(close_result, bool) and close_result:
                    success = True
                elif isinstance(close_result, dict) and close_result.get('retcode') == 10009:
                    success = True

                if success:
                    # Update Dashboard Logic
                    try:
                        # prefer instance-level pnl_tracker if available
                        tracker = self.pnl_tracker if hasattr(self, 'pnl_tracker') and self.pnl_tracker else None
                        if tracker:
                            tracker.add_closed_trade(profit)
                        else:
                            from server import pnl_tracker as server_pnl
                            server_pnl.add_closed_trade(profit)
                    except Exception:
                        pass

                    print(f"      ✅ Position closed successfully")
                    closed_trades.append({'ticket': ticket, 'profit': profit, 'type': pos_type})
                    
                    # Safe remove from list
                    if pos in self.open_positions:
                        self.open_positions.remove(pos)
                else:
                    print(f"      ❌ Close failed")

        return closed_trades

    # ===== FIX #4: PARTIAL PROFIT TAKING + TRAILING STOPS =====
    def check_partial_profit_targets(self, current_price):
        """
        Partial Profit: Closes 50% of trade if price moves > 500 pips (Reward 2R).
        Also moves Stop Loss to Breakeven.

        Note: For XAUUSD the bot uses 1 pip = 0.01. Therefore:
              (price - entry) * 100 -> gives pips,
              and a $5.00 move equals 500 pips (5.00 / 0.01 = 500).
        """
        try:
            for pos in self.open_positions:
                # Skip if already partially closed
                if pos.get('partial_taken', False):
                    continue

                ticket = pos['ticket']
                # Support both 'price' and 'entry_price' keys
                entry_price = pos.get('price', pos.get('entry_price', 0.0))
                lot_size = pos.get('lot_size', 0.0)
                
                # Only partial if lot size is large enough to split
                if lot_size <= 0.01:
                    continue

                # Calculate Pips (Approximate for Gold: 1.00 move = ~100 pips)
                if pos['signal'] == 'BUY':
                    pips = (current_price - entry_price) * 100 
                else:
                    pips = (entry_price - current_price) * 100

                # TRIGGER: 500 Pips (approx $5.00 price move on Gold)
                PARTIAL_TRIGGER_PIPS = 500 
                
                if pips >= PARTIAL_TRIGGER_PIPS:
                    print(f"\n   💰 PARTIAL PROFIT TRIGGER: Position #{ticket}")
                    print(f"      Entry: {entry_price:.2f} | Current: {current_price:.2f}")
                    print(f"      Profit: {pips:.0f} pips")
                    print(f"      Action: Close 50%, Move SL to breakeven")

                    # Close 50% of the lot
                    partial_lot = round(lot_size / 2, 2)
                    if partial_lot < 0.01: partial_lot = 0.01
                    
                    # ✅ FIX: Use close_trade which handles the API call
                    result = self.close_trade(ticket, partial_lot) 
                    
                    # ✅ FIX: Robust Boolean/Dict Check
                    success = False
                    if isinstance(result, bool) and result:
                        success = True
                    elif isinstance(result, dict) and result.get('retcode') == 10009:
                        success = True
                    
                    if success:
                        pos['partial_taken'] = True # Mark as done to prevent loops
                        
                        # Move SL to Breakeven (Entry +/- small buffer)
                        buffer = 0.10 # $0.10 buffer
                        be_price = entry_price + buffer if pos['signal']=='BUY' else entry_price - buffer
                        
                        # Modify Position Logic
                        if hasattr(self, 'modify_position'):
                            self.modify_position(ticket, sl=be_price, tp=pos.get('tp', 0.0))
                            print(f"      ✅ SL Moved to Breakeven: {be_price:.2f}")
                        
                        # Update local volume tracking
                        pos['lot_size'] = round(lot_size - partial_lot, 2)
                    else:
                        print(f"      ❌ Partial close failed")

        except Exception as e:
            print(f"   ❌ Partial profit check error: {e}")

    def update_trailing_stops(self, current_price, min_profit_pips=20):
        """
        SINGLE TRUTH VERSION
        Updates trailing stops on profitable positions.
        - safe against missing 'entry' keys
        - handles dictionary/float price inputs
        - moves SL only in favorable direction
        """
        updated_count = 0

        try:
            # 1. Normalize Price Data (Handle both dict and float)
            if isinstance(current_price, dict):
                bid = current_price.get('bid', 0)
                ask = current_price.get('ask', 0)
            else:
                # If a single float is passed, assume it's the bid for BUYs, 
                # but we need spread for accurate SELL logic. 
                # For safety, we'll use the float for both, but ideally pass a dict.
                bid = float(current_price)
                ask = float(current_price)

            # 2. Iterate through all open positions
            for pos in self.open_positions:
                ticket = pos.get('ticket')
                if not ticket:
                    continue

                # --- 🛡️ CRITICAL FIX: Auto-repair missing 'entry' key ---
                if 'entry' not in pos:
                    if 'entry_price' in pos:
                        pos['entry'] = pos['entry_price']
                    else:
                        # Skip if we absolutely can't find an entry price
                        continue
                # --------------------------------------------------------

                entry = float(pos['entry'])
                current_sl = float(pos.get('sl', 0))
                # Handle 'type' (from MT5) or 'signal' (from Bot)
                pos_type = pos.get('type', pos.get('signal', 'BUY'))

                # Safety check for valid entry
                if entry <= 0:
                    continue

                # --- BUY LOGIC ---
                if pos_type == 'BUY':
                    current_profit_pips = (bid - entry) * 100

                    if current_profit_pips >= min_profit_pips:
                        # Trail distance: 50% of profit
                        profit_distance = bid - entry
                        new_sl = entry + (profit_distance * 0.5)

                        # Only move SL UP
                        if new_sl > current_sl:
                            if self.mt5.modify_position(ticket, new_sl, pos.get('tp')):
                                print(f"📈 Trailing BUY #{ticket}: ${current_sl:.2f} → ${new_sl:.2f} (+{current_profit_pips:.1f}p)")
                                pos['sl'] = new_sl  # Update internal memory
                                updated_count += 1

                # --- SELL LOGIC ---
                elif pos_type == 'SELL':
                    current_profit_pips = (entry - ask) * 100

                    if current_profit_pips >= min_profit_pips:
                        profit_distance = entry - ask
                        new_sl = entry - (profit_distance * 0.5)

                        # Only move SL DOWN (or if SL is 0/undefined)
                        if new_sl < current_sl or current_sl == 0:
                            if self.mt5.modify_position(ticket, new_sl, pos.get('tp')):
                                print(f"📉 Trailing SELL #{ticket}: ${current_sl:.2f} → ${new_sl:.2f} (+{current_profit_pips:.1f}p)")
                                pos['sl'] = new_sl  # Update internal memory
                                updated_count += 1

            return updated_count

        except Exception as e:
            print(f"❌ Trailing stop error: {e}")
            return 0

    def sync_positions_with_mt5(self):
        """
        Two-Way Sync: 
        1. Remove internal positions that are closed in MT5.
        2. Adopt orphan positions from MT5 that are not in internal memory.
        """
        try:
            mt5_positions = self.mt5.get_open_positions()
            if mt5_positions is None:
                print("   ⚠️  Could not fetch MT5 positions for sync")
                return

            # Convert to dictionary for easy lookup if not already
            current_mt5_map = {}
            for pos in mt5_positions:
                # Handle both object (dot notation) and dict (bracket notation)
                ticket = pos['ticket'] if isinstance(pos, dict) else pos.ticket
                current_mt5_map[ticket] = pos

            # --- STEP 1: CLEANUP (Remove closed trades) ---
            before_count = len(self.open_positions)
            synced_positions = []

            for pos in self.open_positions:
                ticket = pos.get('ticket')
                if ticket in current_mt5_map:
                    synced_positions.append(pos)
                else:
                    signal = pos.get('signal', 'UNKNOWN')
                    # Use .get() safely for logging
                    entry_price = pos.get('entry_price', 0)
                    print(f"   ✅ Removed closed position: {signal} @ {entry_price:.2f} | Ticket: {ticket}")

            self.open_positions = synced_positions

            # --- STEP 2: ADOPTION (Import existing trades from MT5) ---
            tracked_tickets = {p['ticket'] for p in self.open_positions}

            for ticket, mt5_pos in current_mt5_map.items():
                if ticket not in tracked_tickets:
                    # Determine type (MT5 returns 0 for BUY, 1 for SELL)
                    raw_type = mt5_pos['type'] if isinstance(mt5_pos, dict) else mt5_pos.type

                    if raw_type == 0:
                        signal_str = "BUY"
                    elif raw_type == 1:
                        signal_str = "SELL"
                    else:
                        signal_str = str(raw_type)

                    # ✅ SAFE EXTRACTION: Handle both Dict and Object formats
                    if isinstance(mt5_pos, dict):
                        open_price = mt5_pos.get('price_open', mt5_pos.get('price', 0.0))
                        sl = mt5_pos.get('sl', 0.0)
                        tp = mt5_pos.get('tp', 0.0)
                        vol = mt5_pos.get('volume', 0.0)
                    else:
                        open_price = mt5_pos.price_open
                        sl = mt5_pos.sl
                        tp = mt5_pos.tp
                        vol = mt5_pos.volume

                    # ✅ CRITICAL FIX: Add 'entry' key (required by trailing stop logic)
                    new_pos = {
                        'ticket': ticket,
                        'signal': signal_str,
                        'type': signal_str,       # Added for compatibility
                        'entry': open_price,      # <-- FIX: The missing key causing the crash
                        'entry_price': open_price, # Keep for other logic
                        'sl': sl,
                        'stop_loss': sl,          # Dual keys for safety
                        'tp': tp,
                        'take_profit': tp,        # Dual keys for safety
                        'lot_size': vol,
                        'volume': vol,
                        'entry_time': datetime.now(),
                        'risk_percent': 0,        # Unknown for adopted trades
                        'atr': 0,
                        'zone': 'RECOVERED',
                        'market_structure': 'RECOVERED',
                        'session': getattr(self, 'current_session', 'UNKNOWN')
                    }

                    self.open_positions.append(new_pos)
                    print(f"   📥 ADOPTED ORPHAN TRADE: {signal_str} | Ticket: {ticket} | Lots: {vol}")

            after_count = len(self.open_positions)

            if before_count != after_count:
                print(f"📊 Position Sync Complete:")
                print(f"   Bot was tracking: {before_count}")
                print(f"   Now tracking: {after_count}")

        except Exception as e:
            print(f"❌ Error syncing positions: {e}")
            import traceback
            traceback.print_exc()

    def get_market_trend(self, timeframe='H1'):
        """
        🛡️ CRITICAL SAFETY: Detect market trend to prevent counter-trend trades
        Returns: ("BULLISH", "BEARISH", "RANGING")
        """
        try:
            import MetaTrader5 as mt5

            # Get H1 data for trend analysis
            if timeframe == 'H1':
                tf = mt5.TIMEFRAME_H1
            elif timeframe == 'H4':
                tf = mt5.TIMEFRAME_H4
            else:
                tf = mt5.TIMEFRAME_H1

            rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, 100)

            if rates is None or len(rates) < 50:
                print("⚠️ Trend filter: Not enough data, defaulting to RANGING")
                return "RANGING"

            closes = pd.Series([float(r['close']) for r in rates])

            # Calculate EMAs
            ema20 = closes.ewm(span=TREND_EMA_FAST, adjust=False).mean().iloc[-1]
            ema50 = closes.ewm(span=TREND_EMA_SLOW, adjust=False).mean().iloc[-1]
            current_price = closes.iloc[-1]

            # Calculate trend strength
            ema_diff_percent = abs(ema20 - ema50) / ema50 * 100

            # Determine trend
            if ema20 > ema50 and current_price > ema20:
                if ema_diff_percent > 0.15:  # Strong uptrend
                    return "BULLISH"
                else:
                    return "RANGING"  # Weak uptrend = ranging
            elif ema20 < ema50 and current_price < ema20:
                if ema_diff_percent > 0.15:  # Strong downtrend
                    return "BEARISH"
                else:
                    return "RANGING"
            else:
                return "RANGING"

        except Exception as e:
            print(f"⚠️ Trend filter error: {e}")
            return "RANGING"  # Default to RANGING on error (safe mode)

    def can_open_position(self, signal_type):
        """
        🛡️ Check if we can open another position (prevent over-trading)
        """
        # Check total position limit
        if len(self.open_positions) >= MAX_TOTAL_POSITIONS:
            print(f"🚫 Max total positions ({MAX_TOTAL_POSITIONS}) reached")
            return False

        # Check same-direction limit
        same_direction_count = sum(1 for pos in self.open_positions
                                   if pos['signal'] == signal_type)

        if same_direction_count >= MAX_POSITIONS_PER_DIRECTION:
            print(f"🚫 Max {signal_type} positions ({MAX_POSITIONS_PER_DIRECTION}) reached")
            return False

        return True

    def check_daily_loss_limit(self):
        """
        🛡️ CIRCUIT BREAKER: Stop trading if daily loss exceeds limit
        SAFE against None initialization
        """
        account = self.mt5.get_account_info()
        if not account:
            return False

        current_balance = float(account.balance)

        # ✅ HARD GUARD (this is what was missing)
        if self.daily_start_balance is None:
            self.daily_start_balance = current_balance
            self.daily_loss_limit_triggered = False
            return False

        daily_loss = self.daily_start_balance - current_balance
        if self.daily_start_balance == 0:
            return False
        daily_loss_percent = (daily_loss / self.daily_start_balance) * 100

        if daily_loss_percent >= DAILY_LOSS_LIMIT_PERCENT:
            if not self.daily_loss_limit_triggered:
                self.daily_loss_limit_triggered = True
                msg = (
                    f"🚨 CIRCUIT BREAKER ACTIVATED!\n\n"
                    f"Daily Loss: {daily_loss_percent:.2f}%\n"
                    f"Loss Amount: ${daily_loss:.2f}\n"
                    f"Start Balance: ${self.daily_start_balance:.2f}\n"
                    f"Current Balance: ${current_balance:.2f}\n\n"
                    f"🛑 Trading stopped for today."
                )
                self.logger.error(msg)
                try:
                    # Use module-level send_telegram via self.send_telegram
                    self.send_telegram(msg)
                except Exception:
                    pass
            return True

        return False

    def check_consecutive_losses(self):
        """
        🛡️ Pause trading after consecutive losses
        """
        if not hasattr(self, 'consecutive_losses'):
            self.consecutive_losses = 0
            self.consecutive_loss_pause = False

        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            if not self.consecutive_loss_pause:
                self.consecutive_loss_pause = True
                msg = (f"⚠️ CONSECUTIVE LOSS LIMIT\n\n"
                       f"Lost {self.consecutive_losses} trades in a row.\n"
                       f"Taking a break for 30 minutes...")
                print(f"\n{msg}\n")
                try:
                    self.send_telegram(msg)
                except Exception:
                    pass
                self.consecutive_loss_pause_until = datetime.now() + timedelta(minutes=30)
            return True

        # Check if pause period has expired
        if self.consecutive_loss_pause:
            if datetime.now() > self.consecutive_loss_pause_until:
                self.consecutive_loss_pause = False
                self.consecutive_losses = 0
                print("✅ Consecutive loss pause expired. Resuming trading...")
                return False
            return True

        return False

    def load_historical_trades(self, max_trades=50):
        """Load recent trades from tradelog.json for dashboard"""
        trades = []
        try:
            if os.path.exists('tradelog.json'):
                with open('tradelog.json', 'r') as f:
                    log_data = json.load(f)
                trades_to_add = log_data[-max_trades:] if len(log_data) > max_trades else log_data
                for entry in trades_to_add:
                    if entry.get('signal') in ['BUY', 'SELL']:
                        trades.append({
                            'id': len(trades) + 1,
                            'type': entry['signal'],
                            'entry_price': entry.get('entry_price'),
                            'time': entry.get('timestamp'),
                            'status': 'LOGGED',
                            'session': entry.get('session', 'UNKNOWN'),
                            'zone': entry.get('zone', 'UNKNOWN'),
                            'atr': entry.get('atr', 0),
                            'spread': entry.get('spread', 0),
                            'market_structure': entry.get('market_structure', 'UNKNOWN'),
                        })
            return trades
        except Exception as e:
            print(f"❌ Error loading historical trades: {e}")
            return []

    def calculate_current_pnl(self):
        """Calculate current P/L from open positions"""
        total_pnl = 0.0
        try:
            current_price = self.mt5.get_current_price()
            if not current_price:
                return 0.0

            current_bid = current_price['bid']
            current_ask = current_price['ask']

            for pos in self.open_positions:
                signal = pos['signal']
                entry = pos['entry_price']
                lot_size = pos['lot_size']

                if signal == 'SELL':
                    pnl = (entry - current_ask) * 100 * lot_size
                else:  # BUY
                    pnl = (current_bid - entry) * 100 * lot_size

                total_pnl += pnl

            return total_pnl
        except Exception as e:
            print(f"❌ P/L calculation error: {e}")
            return 0.0
        
    def fetch_recent_daily_high_low(self, lookback=5):
        """
        Fetch recent completed D1 candles (skip current forming candle) and compute PDH/PDL.
        Uses mt5.copy_rates_from_pos(..., pos=1, count=lookback) so we only get closed candles.
        Returns (pdh, pdl) as floats or (None, None) on failure.
        """
        try:
            import MetaTrader5 as mt5_lib
            # pos=1 means start from last closed candle (pos=0 would include current forming candle)
            cnt = max(lookback, 3)
            rates = None
            try:
                rates = mt5_lib.copy_rates_from_pos("XAUUSD", mt5_lib.TIMEFRAME_D1, 1, cnt)
            except Exception:
                # fallback: try copy_rates_from with a timestamp for yesterday
                try:
                    yesterday = datetime.now() - timedelta(days=1)
                    rates = mt5_lib.copy_rates_from("XAUUSD", mt5_lib.TIMEFRAME_D1, yesterday, cnt)
                except Exception as e:
                    print(f"⚠️ fetch_recent_daily_high_low - mt5 fetch failed: {e}")
                    rates = None

            if not rates or len(rates) == 0:
                return None, None

            # Convert to DataFrame for safety
            df = pd.DataFrame(rates)
            # Ensure numeric
            for c in ['high', 'low']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')

            # Compute pdh/pdl across available completed candles
            try:
                pdh = float(df['high'].max())
                pdl = float(df['low'].min())
            except Exception:
                return None, None

            # If high == low (bad data), try expanding lookback or use small epsilon
            if pdh == pdl:
                # try expand using more candles (up to 10)
                try:
                    rates2 = mt5_lib.copy_rates_from_pos("XAUUSD", mt5_lib.TIMEFRAME_D1, 1, max(lookback, 10))
                    if rates2 is not None and len(rates2) > 1:
                        df2 = pd.DataFrame(rates2)
                        df2['high'] = pd.to_numeric(df2['high'], errors='coerce')
                        df2['low'] = pd.to_numeric(df2['low'], errors='coerce')
                        maxh = float(df2['high'].max())
                        minl = float(df2['low'].min())
                        if maxh != minl:
                            pdh, pdl = maxh, minl
                        else:
                            # last resort: apply tiny expansion
                            pdh = pdh + 0.05
                            pdl = pdl - 0.05
                    else:
                        pdh = pdh + 0.05
                        pdl = pdl - 0.05
                except Exception:
                    # safe fallback: tiny expansion
                    pdh = pdh + 0.05
                    pdl = pdl - 0.05

            return pdh, pdl

        except Exception as e:
            print(f"⚠️ fetch_recent_daily_high_low error: {e}")
            return None, None

    def initialize(self):
        """Initialize the trading bot"""
        print("=" * 70)
        print("🤖 Initializing Enhanced XAUUSD Trading Bot...")
        print("=" * 70)

        # ===== NEW: MARKET HOURS CHECK =====
        def is_market_open_check():
            """Quick market check before full initialization"""
            now = datetime.now()
            day = now.weekday()  # 0=Monday, 6=Sunday

            if day == 5 or day == 6:  # Saturday or Sunday
                print(f"⏸️  Market CLOSED (Weekend - {now.strftime('%A')})")
                print(f"   Next open: Monday 03:30 IST")
                return False

            return True

        if not is_market_open_check():
            print("💤 Waiting for market to open...")
            return False
        # ===== END NEW CODE =====

        if not self.mt5.initialize_mt5():
            print("❌ Failed to initialize MT5 connection")
            return False

        # ← UNINDENT FROM HERE! (back to function level)
        account_info = self.mt5.get_account_info()
        balance = account_info.balance if account_info else 10000

        # ========================================
        # 📱 TELEGRAM: BOT STARTED NOTIFICATION
        # ========================================
        send_telegram(
            f"🤖 <b>Trading Bot Started!</b>\n\n"
            f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
            f"📊 Symbol: XAUUSD\n"
            f"💰 Balance: ${balance:,.2f}\n"
            f"💼 Strategy: {'Guardeer 10-Video Enhanced SMC' if self.use_enhanced_smc else 'Standard SMC'}\n"
            f"📈 Max Positions: {self.max_positions}\n"
            f"🛡️ Risk per Trade: {self.risk_per_trade}%"
        )

        # ===== CREATE RISK CALCULATOR WITH SAFETY VALUES =====
        self.risk_calculator = StopLossCalculator(
            account_balance=balance,
            risk_per_trade=self.risk_per_trade
        )
        self.risk_calculator.min_sl_distance_pips = self.min_sl_pips

        print(f"✅ Account Balance: ${balance:,.2f}")
        print(f"✅ Risk per Trade: {self.risk_per_trade}%")
        print(f"✅ Min SL Distance: {self.min_sl_pips} pips")
        print(f"✅ Time Zone: IST (UTC+5:30)")
        print(f"✅ SMC Strategy: {'Guardeer 10-Video Enhanced' if self.use_enhanced_smc else 'Standard'}")
        print("=" * 70)

        # Load historical trades
        historical_trades = self.load_historical_trades()
        print(f"📊 Loaded {len(historical_trades)} historical trades from tradelog.json")

        # Check position tracking
        print("📍 Checking position tracking on startup...")
        mt5_positions = self.mt5.get_open_positions()
        if mt5_positions and len(mt5_positions) > 0:
            print(f"✅ Found {len(mt5_positions)} open positions in MT5")
            for pos in mt5_positions:
                position = {
                    'ticket': pos['ticket'] if isinstance(pos, dict) else pos['ticket'],
                    'signal': pos['type'] if isinstance(pos, dict) else pos['type'],
                    'entry_price': pos['price_open'] if isinstance(pos, dict) else pos.price_open,
                    'stop_loss': pos['sl'] if isinstance(pos, dict) else pos.sl,
                    'take_profit': pos['tp'] if isinstance(pos, dict) else pos.tp,
                    'lot_size': pos['volume'] if isinstance(pos, dict) else pos['volume'],
                    'entry_time': datetime.now(),
                    'risk_percent': 0,
                    'atr': 0,
                    'zone': 'UNKNOWN',
                    'market_structure': 'UNKNOWN'
                }

                self.open_positions.append(position)
                try:
                    profit_val = pos['profit'] if isinstance(pos, dict) else pos.profit
                except Exception:
                    profit_val = 0.0
                print(f"   ✅ Imported {pos['type']} | Ticket: {pos['ticket']} | {pos['volume']} lots | P/L: ${profit_val:.2f}")

            print(f"📊 Imported {len(self.open_positions)} positions from MT5")
        else:
            print("   ℹ️  No open positions in MT5")

        self.sync_positions_with_mt5()
        print(f"📊 Now tracking {len(self.open_positions)} positions")
        print("✅ Position tracking initialized")
        return True

    def fetch_market_data(self):
        """Fetch current market data"""
        historical_data = self.mt5.get_historical_data(bars=300)
        if historical_data is None:
            print("❌ Could not fetch market data")
            return None

        if not isinstance(historical_data, pd.DataFrame):
            if not QUIET_LOGGING:
                print("   ℹ️  Converting market data to DataFrame...")
            if hasattr(historical_data, 'columns'):
                historical_data = pd.DataFrame(historical_data)
            else:
                # Convert numpy array to pandas DataFrame
                historical_data = pd.DataFrame(historical_data)

        # Ensure numeric types for columns used by ATR etc.
        for c in ['high','low','close','open']:
            if c in historical_data.columns:
                historical_data[c] = pd.to_numeric(historical_data[c], errors='coerce')

        current_price = self.mt5.get_current_price()
        if current_price is None:
            print("❌ Could not fetch current price")
            return None

        return historical_data, current_price

    def analyze_enhanced(self):
        """
        Complete analysis using ALL 10 Guardeer video concepts + Multi-TF
        PRODUCTION: Clean, optimized, no redundancy
        """
        global ZoneCalculator
        
        # ===== SESSION CHECK =====
        is_active, current_session = is_trading_session()
        # normalize session names for filters
        canonical_session = map_session_for_filter(current_session)
        self.current_session = canonical_session
        
        if not is_active:
            last_log = getattr(self, '_last_inactive_log_at', None)
            now = datetime.now()
            if last_log is None or (now - last_log).total_seconds() > 600:
                print(f"⏸️ Session '{canonical_session}' not active for trading.")
                self._last_inactive_log_at = now
            return

        try:
            self.sync_positions_with_mt5()
            
            # ===== FETCH MARKET DATA (SINGLE SOURCE OF TRUTH) =====
            market_data = self.fetch_market_data()
            if market_data is None:
                print("   ⚠️ Could not fetch market data - skipping analysis")
                return
            
            historical_data, current_price = market_data
            
            # ===== GOLD-SPECIFIC SESSION FILTER =====
            try:
                # Compute ATR robustly from candles (not from a missing column fallback)
                atr_value = None
                if 'atr' in historical_data.columns and pd.notna(historical_data['atr'].iloc[-1]):
                    try:
                        atr_value = float(historical_data['atr'].iloc[-1])
                    except Exception:
                        atr_value = None

                if atr_value is None or atr_value == 0:
                    # Compute ATR using our helper
                    atr_value = compute_atr_from_df(historical_data, period=14)

                spread_value = None
                # normalize spread extraction from current_price
                if isinstance(current_price, dict):
                    spread_value = current_price.get('spread')
                    # fallback to ask - bid if spread not present
                    if spread_value is None:
                        ask = current_price.get('ask', 0.0)
                        bid = current_price.get('bid', 0.0)
                        spread_value = abs(ask - bid)
                else:
                    spread_value = 0.0

                # Use canonical_session for filter
                is_tradeable, session_msg = XAUUSDFilter.is_tradeable_session(
                    session=self.current_session,
                    atr=atr_value,
                    spread=spread_value
                )
                
                # Improved logs for debugging
                print(f"   🌍 {session_msg} | Session={self.current_session} | ATR={atr_value:.2f} | Spread={spread_value}")
                
                if not is_tradeable:
                    print(f"   🚫 Session not tradeable - Skipping")
                    return
                
                if 'ASIAN' in self.current_session.upper():
                    asian_check = XAUUSDFilter.detect_asian_session_weakness(historical_data)
                    print(f"   📊 Asian Quality: {asian_check['score']}/100")
                    if asian_check['is_choppy']:
                        print(f"   🚫 {asian_check['recommendation']}")
                        return
                            
            except Exception as e:
                print(f"   ⚠️ Session filter error: {e}")
                return
            
            # ===== MULTI-TIMEFRAME ANALYSIS =====
            mtf_confluence = self.mtf_analyzer.get_multi_tf_confluence()
            
            # ===== MARKET STRUCTURE ANALYSIS =====
            try:
                from strategy.market_structure import MarketStructureDetector
                market_structure_detector = MarketStructureDetector(historical_data)
                structure_analysis = market_structure_detector.get_market_structure_analysis()
            except Exception:
                structure_analysis = {
                    'current_trend': 'NEUTRAL',
                    'trend_valid': True,
                    'structure_shift': 'NONE',
                    'bos_level': None,
                    'choch_detected': False
                }
            
            # ===== INITIALIZE DETECTOR MODULES =====
            if self.liquidity_detector is None:
                from strategy.smc_enhanced.inducement import InducementDetector
                from strategy.smc_enhanced.volume_analyzer import VolumeAnalyzer
                
                self.liquidity_detector = LiquidityDetector(historical_data)
                self.poi_identifier = POIIdentifier(historical_data)
                self.bias_detector = BiasDetector(historical_data)
                self.volume_analyzer = VolumeAnalyzer(historical_data)
                self.narrative_analyzer = NarrativeAnalyzer(
                    self.liquidity_detector,
                    self.poi_identifier,
                    self.bias_detector
                )
            else:
                self.liquidity_detector.df = historical_data
                self.poi_identifier.df = historical_data
                self.bias_detector.df = historical_data
                self.volume_analyzer.df = historical_data
            
            
            # ===== VIDEO 5: LIQUIDITY DETECTION =====
            try:
                pdh, pdl = self.liquidity_detector.get_previous_day_high_low()
                # If detector returned invalid/degenerate range, replace with robust fetch
                if pdh is None or pdl is None or pdh == pdl:
                    pdh2, pdl2 = self.fetch_recent_daily_high_low(lookback=5)
                    if pdh2 is not None and pdl2 is not None:
                        pdh, pdl = pdh2, pdl2
                    else:
                        # leave as-is but log
                        print(f"⚠️ Invalid PDH/PDL from detector; attempted fallback but got None")
            except Exception:
                pdh, pdl = self.fetch_recent_daily_high_low(lookback=5)
            
            try:
                swings = self.liquidity_detector.get_swing_high_low(lookback=20)
            except Exception:
                swings = {'highs': [], 'lows': []}
            
            try:
                liquidity_grabbed = self.liquidity_detector.check_liquidity_grab(current_price['bid'])
            except Exception:
                liquidity_grabbed = {'pdh_grabbed': False, 'pdl_grabbed': False}
            
            # ===== VIDEO 3: INDUCEMENT DETECTION =====
            try:
                liquidity_levels = {
                    'PDH': pdh,
                    'PDL': pdl,
                    'swing_highs': [s['price'] for s in swings.get('highs', [])],
                    'swing_lows': [s['price'] for s in swings.get('lows', [])]
                }
                from strategy.smc_enhanced.inducement import InducementDetector
                inducement_detector = InducementDetector(historical_data, liquidity_levels)
                self.inducement = inducement_detector.detect_latest_inducement(lookback=10)
                inducement = self.inducement
            except Exception:
                inducement = {'inducement': False}
            
            # ===== VIDEO 6: POI IDENTIFICATION =====
            try:
                order_blocks = self.poi_identifier.find_order_blocks(lookback=50)
            except Exception:
                order_blocks = {'bullish': [], 'bearish': []}
            
            try:
                fvgs = self.poi_identifier.find_fvg()
            except Exception:
                fvgs = {'bullish': [], 'bearish': []}
            
            # ===== VIDEO 8: VOLUME CONFIRMATION =====
            try:
                recent_bars = historical_data.tail(20)
                avg_volume = recent_bars['tick_volume'].iloc[:-1].mean()
                last_volume = recent_bars['tick_volume'].iloc[-1]
                volume_spike_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0
                volume_confirmation = {
                    'spike_detected': volume_spike_ratio > 1.5,
                    'ratio': volume_spike_ratio,
                    'threshold': 1.5
                }
            except Exception:
                volume_confirmation = {'spike_detected': False, 'ratio': 1.0, 'threshold': 1.5}
            
            # ===== MOMENTUM / RSI =====
            try:
                recent_prices = historical_data['close'].tail(5).values
                momentum = float(recent_prices[-1] - recent_prices[0]) if len(recent_prices) == 5 else 0.0
                
                if len(historical_data) >= 15:
                    delta = historical_data['close'].diff()
                    gain = delta.where(delta > 0, 0.0)
                    loss = -delta.where(delta < 0, 0.0)
                    avg_gain = gain.rolling(window=14).mean()
                    avg_loss = loss.rolling(window=14).mean()
                    last_gain = float(avg_gain.iloc[-1]) if not pd.isna(avg_gain.iloc[-1]) else 0.0
                    last_loss = float(avg_loss.iloc[-1]) if not pd.isna(avg_loss.iloc[-1]) else 0.0
                    
                    if last_loss == 0:
                        rsi = 50.0
                    else:
                        rs = last_gain / last_loss
                        rsi = 100.0 - (100.0 / (1.0 + rs))
                else:
                    rsi = 50.0
                
                momentum_data = {
                    "momentum": momentum,
                    "rsi": rsi,
                    "overbought": rsi > 70,
                    "oversold": rsi < 30
                }
            except Exception:
                momentum_data = {"momentum": 0.0, "rsi": 50.0, "overbought": False, "oversold": False}
            
            # ===== CLOSEST POI =====
            try:
                closest_poi = self.poi_identifier.get_closest_poi(current_price['bid'], direction="UP")
            except Exception:
                closest_poi = None
            
            # ===== VIDEO 9: BIAS DETECTION =====
            try:
                daily_bias = self.bias_detector.analyze_daily_pattern(historical_data.iloc[-1])
            except Exception:
                daily_bias = "NEUTRAL"
            
            try:
                intraday_bias = self.bias_detector.get_intraday_bias(lookback=20)
            except Exception:
                intraday_bias = "NEUTRAL"
            
            try:
                price_action_bias = self.bias_detector.get_price_action_bias()
            except Exception:
                price_action_bias = "NEUTRAL"
            
            try:
                combined_bias = self.bias_detector.get_combined_bias(
                    daily_bias,
                    intraday_bias,
                    price_action_bias
                )
            except Exception:
                combined_bias = "NEUTRAL"
            
            # ===== VIDEO 10a: ZONE ANALYSIS =====
            try:
                swing_highs = swings.get('highs', [])
                swing_lows = swings.get('lows', [])
                latest_swing_high = swing_highs[-1]['price'] if swing_highs else current_price['bid']
                latest_swing_low = swing_lows[-1]['price'] if swing_lows else current_price['bid']
                
                calc_high = max(latest_swing_high, latest_swing_low)
                calc_low = min(latest_swing_high, latest_swing_low)
                
                zones = ZoneCalculator.calculate_zones(calc_high, calc_low)
                current_zone = ZoneCalculator.classify_price_zone(current_price['bid'], zones)
                zone_summary = ZoneCalculator.get_zone_summary(current_price['bid'], zones)
            except Exception:
                zones = {}
                current_zone = "EQUILIBRIUM"
                zone_summary = None
            
            # ===== VIDEO 10b: NARRATIVE ANALYSIS =====
            try:
                inducement = getattr(self, 'inducement', {'inducement': False})
                market_state = {
                    'inducement': inducement.get('inducement', False),
                    'inducement_type': inducement.get('type', 'NONE'),
                    'inducement_direction': inducement.get('direction', 'NONE'),
                    'inducement_session': inducement.get('session', 'UNKNOWN'),
                    'inducement_reliability': inducement.get('session_reliability', 0.70),
                    'inducement_weighted_confidence': inducement.get('weighted_confidence', 'MEDIUM'),
                    'liquidity_grabbed': liquidity_grabbed.get('pdh_grabbed') or liquidity_grabbed.get('pdl_grabbed'),
                    'liquidity_type': 'PDH' if liquidity_grabbed.get('pdh_grabbed') else 'PDL' if liquidity_grabbed.get('pdl_grabbed') else 'NONE',
                    'fvg_tapped': len(fvgs.get('bullish', [])) > 0 or len(fvgs.get('bearish', [])) > 0,
                    'fvg_type': 'BULLISH' if len(fvgs.get('bullish', [])) > 0 else 'BEARISH' if len(fvgs.get('bearish', [])) > 0 else 'NONE',
                    'ob_hit': len(order_blocks.get('bullish', [])) > 0 or len(order_blocks.get('bearish', [])) > 0,
                    'ob_type': 'BULLISH' if len(order_blocks.get('bullish', [])) > 0 else 'BEARISH' if len(order_blocks.get('bearish', [])) > 0 else 'NONE',
                    'current_direction': daily_bias,
                    'current_bias': combined_bias,
                    'next_poi_target': closest_poi[0] if closest_poi else current_price['bid'],
                    'target_type': closest_poi[1] if closest_poi else 'NONE',
                    'target_distance': abs(closest_poi[0] - current_price['bid']) if closest_poi else 0,
                    'target_confidence': 'HIGH' if closest_poi else 'LOW',
                    'zone': current_zone,
                    'zone_strength': zone_summary.get('zone_strength') if zone_summary else 0,
                    'distance_from_equilibrium': abs(zone_summary.get('distance_from_equilibrium')) if zone_summary else 0,
                    'timeframe': '15min',
                    'price_action': 'NEUTRAL'
                }
                narrative = self.narrative_analyzer.analyze_market_story(market_state)
            except Exception:
                narrative = {'trade_signal': 'HOLD', 'confidence': 0, 'bias': 'NEUTRAL'}
            
            # ===== EXTRACT SIGNAL & ZONE STRENGTH =====
            final_signal = narrative.get('trade_signal', 'HOLD')
            zone_strength = zone_summary.get('zone_strength', 0) if zone_summary else 0
            
            # ===== SIGNAL OVERRIDE LOGIC =====
            if final_signal == 'HOLD' and zone_summary:
                if current_zone == 'DISCOUNT' and zone_strength >= 50:
                    final_signal = 'BUY'
                elif current_zone == 'PREMIUM' and zone_strength >= 50:
                    final_signal = 'SELL'
            
            # ===== SAFETY GUARDS =====
            MIN_ZONE_FLOOR = 15
            if zone_strength < MIN_ZONE_FLOOR and final_signal != 'HOLD':
                print(f"   🛡️ Zone Guard: Blocking {final_signal} (Strength {zone_strength}% < {MIN_ZONE_FLOOR}%)")
                final_signal = 'HOLD'
            
            # Check Cooldown
            if final_signal != 'HOLD' and not self.check_trade_cooldown(final_signal):
                print(f"   ⏳ Cooldown Active: Skipping {final_signal}")
                final_signal = 'HOLD'
            
            # Check IdeaMemory
            if final_signal != 'HOLD':
                if not self.idea_memory.is_allowed(final_signal, current_zone, self.current_session):
                    print(f"   🧠 IdeaMemory Blocked: {final_signal} in {current_zone}")
                    final_signal = 'HOLD'
            
            # ===== MULTI-TIMEFRAME CONFIDENCE FILTER =====
            MTF_MIN_CONFIDENCE = 35 if zone_strength > 70 else (30 if mtf_confluence['confidence'] >= 80 else 35)
            
            if mtf_confluence['confidence'] < MTF_MIN_CONFIDENCE:
                final_signal = 'HOLD'
            elif final_signal == 'BUY' and mtf_confluence['overall_bias'] == 'BEARISH':
                if not (inducement.get('inducement', False) and inducement.get('direction') == 'BULLISH'):
                    final_signal = 'HOLD'
            elif final_signal == 'SELL' and mtf_confluence['overall_bias'] == 'BULLISH':
                if not (inducement.get('inducement', False) and inducement.get('direction') == 'BEARISH'):
                    final_signal = 'HOLD'
            
            # ===== ZONE-BIAS VALIDATION =====
            zone_allows_trade = False
            
            if final_signal != 'HOLD':
                zone_str = zone_strength
                ENABLE_ZONE_OVERRIDE = True
                
                atr_value = None
                try:
                    if 'atr' in historical_data.columns:
                        atr_value = float(historical_data['atr'].iloc[-1])
                except Exception:
                    atr_value = None

                if not atr_value or atr_value == 0:
                    atr_value = compute_atr_from_df(historical_data, period=14)

                if atr_value and zone_summary:
                    try:
                        zone_str = ZoneCalculator.get_zone_strength_atr(current_price['bid'], zones, atr=atr_value)
                    except Exception:
                        zone_str = zone_strength
                
                is_strong_zone = zone_str >= 50
                
                # ATR Filter
                atr_filter_active = False
                try:
                    if atr_value is not None:
                        atr_filter_active = atr_value < 1.5
                except Exception:
                    atr_filter_active = True
                
                if final_signal == 'BUY':
                    bias_is_bullish = combined_bias in ['BULLISH', 'HIGHER_HIGH', 'NEUTRAL']
                    mtf_is_bullish = mtf_confluence.get('overall_bias') == 'BULLISH'
                    has_inducement = inducement.get('inducement', False) and inducement.get('direction') == 'BULLISH'
                    
                    if bias_is_bullish or mtf_is_bullish or has_inducement:
                        if not atr_filter_active and volume_confirmation.get('spike_detected', True) and momentum_data.get('rsi', 50) < 70:
                            zone_allows_trade = True
                    elif ENABLE_ZONE_OVERRIDE and is_strong_zone and current_zone == 'DISCOUNT':
                        zone_allows_trade = True
                
                elif final_signal == 'SELL':
                    bias_is_bearish = combined_bias in ['BEARISH', 'LOWER_LOW', 'NEUTRAL']
                    mtf_is_bearish = mtf_confluence.get('overall_bias') == 'BEARISH'
                    has_inducement = inducement.get('inducement', False) and inducement.get('direction') == 'BEARISH'
                    
                    if bias_is_bearish or mtf_is_bearish or has_inducement:
                        zone_allows_trade = True
                    elif ENABLE_ZONE_OVERRIDE and is_strong_zone and current_zone == 'PREMIUM':
                        zone_allows_trade = True
            
            # Final Safety Check
            if final_signal != 'HOLD' and not zone_allows_trade:
                print(f"   🚫 Trade Blocked: Zone/Bias Validation Failed")
                final_signal = 'HOLD'
            
            # ===== CALCULATE TECHNICAL INDICATORS =====
            atr_filter_active = False
            try:
                df = historical_data.copy()
                ema200 = float(df['close'].ewm(span=200, adjust=False).mean().iloc[-1]) if len(df) >= 200 else 0.0
                
                if len(df) >= 50:
                    ma20 = float(df['close'].rolling(window=20).mean().iloc[-1])
                    ma50 = float(df['close'].rolling(window=50).mean().iloc[-1])
                else:
                    ma20 = ma50 = 0.0
                
                recent_bars = min(50, len(df))
                support = float(df['low'].tail(recent_bars).min())
                resistance = float(df['high'].tail(recent_bars).max())
                
                # Compute ATR column locally for use later in analysis
                if len(df) >= 3:
                    df['high_low'] = df['high'] - df['low']
                    df['high_close'] = abs(df['high'] - df['close'].shift(1))
                    df['low_close'] = abs(df['low'] - df['close'].shift(1))
                    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
                    df['atr'] = df['tr'].rolling(window=14).mean()
                    atr = float(df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else compute_atr_from_df(df, 14))
                    atr_filter_active = atr < 1.5
                else:
                    atr = compute_atr_from_df(df, 14)
                    atr_filter_active = atr < 1.5
                    
            except Exception:
                ema200 = ma20 = ma50 = support = resistance = atr = 0.0
                atr_filter_active = True
            
            # ===== STORE ANALYSIS DATA =====
            self.enhanced_analysis_data = {
                'pdh': pdh, 'pdl': pdl, 'swings': swings,
                'order_blocks': order_blocks, 'fvgs': fvgs,
                'daily_bias': daily_bias, 'current_zone': current_zone,
                'narrative': narrative, 'zones': zones,
                'mtf_confluence': mtf_confluence,
                'market_structure': structure_analysis,
                'volume_confirmation': volume_confirmation,
                'momentum_data': momentum_data,
                'atr_filter_active': atr_filter_active,
            }
            
            self.last_analysis = {
                'smc_indicators': self.enhanced_analysis_data,
                'technical_levels': {
                    'ema200': ema200, 'ma20': ma20, 'ma50': ma50,
                    'support': support, 'resistance': resistance, 'atr': atr,
                },
                'zone': current_zone, 'bias': combined_bias
            }
            
            # ===== EXECUTION LOGIC =====
            self.check_zone_based_exits(current_zone, current_price['bid'])
            self.check_partial_profit_targets(current_price['bid'])
            self.update_trailing_stops(current_price)
            
            # ===== PREVENT STACKING & EXECUTE TRADE =====
            if len(self.open_positions) < self.max_positions and final_signal != 'HOLD':
                current_buys = len([p for p in self.open_positions if p['signal'] == 'BUY'])
                current_sells = len([p for p in self.open_positions if p['signal'] == 'SELL'])
                
                if final_signal == 'BUY' and current_buys > 0:
                    print("   🚫 Trade Skipped: Already have BUY open")
                    final_signal = 'HOLD'
                elif final_signal == 'SELL' and current_sells > 0:
                    print("   🚫 Trade Skipped: Already have SELL open")
                    final_signal = 'HOLD'
                
                if final_signal != 'HOLD' and self.check_trade_cooldown(final_signal):
                    success = self.execute_enhanced_trade(final_signal, current_price, historical_data, zones)
                    if success:
                        if final_signal == 'BUY':
                            self.last_buy_time = datetime.now()
                        elif final_signal == 'SELL':
                            self.last_sell_time = datetime.now()
            
            elif len(self.open_positions) >= self.max_positions and final_signal != 'HOLD':
                print(f"   🚫 Max Positions Reached")
                final_signal = 'HOLD'
            
            # ===== UPDATE DASHBOARD =====
            full_analysis_data = {
                'pdh': pdh, 'pdl': pdl, 'swings': swings,
                'inducement_data': inducement,
                'market_structure': structure_analysis,
                'order_blocks': order_blocks, 'fvgs': fvgs,
                'current_zone': current_zone,
                'zone_strength': zone_strength,
                'mtf_confluence': mtf_confluence,
                'narrative': narrative
            }
            
            try:
                self.log_trade_analysis(final_signal, 'Enhanced SMC Analysis', current_price, market_state)
            except Exception:
                pass
            
            try:
                self.update_dashboard_state()
            except Exception:
                pass
            
            # ===== PRINT SUMMARY =====
            reason_msg = "Waiting for setup"
            if zone_strength < 30:
                reason_msg = f"Zone Weak ({zone_strength}%)"
            elif structure_analysis.get('current_trend') == 'NEUTRAL':
                reason_msg = "Structure NEUTRAL"
            elif mtf_confluence['confidence'] < 50:
                reason_msg = "MTF Low"
            elif atr_filter_active:
                reason_msg = "Low Volatility"
            elif final_signal == 'HOLD':
                reason_msg = narrative.get('trade_signal_reason', 'Waiting for setup')
            
            try:
                self.print_smart_summary(
                    iteration=getattr(self, 'iteration_count', 0),
                    price=current_price['bid'],
                    analysis_data=full_analysis_data,
                    signal=final_signal,
                    reason=reason_msg,
                    positions=self.open_positions
                )
            except Exception:
                pass
        
        except Exception as e:
            print(f"❌ Error in enhanced analysis: {e}")
            import traceback
            traceback.print_exc()
            try:
                send_telegram(f"⚠️ <b>BOT ERROR!</b>\n\n<code>{str(e)[:200]}</code>")
            except Exception:
                pass


    def display_analysis(self, price, signal, reason, stats):
        """Display standard market analysis"""
        print(f"\n💰 XAUUSD Price: ${price['bid']:.2f} | Spread: ${price['spread']:.2f}")
        print(f"📊 Market Structure: {stats.get('market_structure', 'UNKNOWN')}")
        print(f"📍 Zone: {stats.get('zone', 'UNKNOWN')}")
        if stats.get('session', 'CLOSED') != 'CLOSED':
            print(f"🕐 Session: {stats.get('session', 'UNKNOWN')}")

        print("\n📈 Technical Levels:")
        print(f"   EMA200: ${stats.get('ema200', 0):.2f}")
        print(f"   MA20: ${stats.get('ma20', 0):.2f} | MA50: ${stats.get('ma50', 0):.2f}")
        print(f"   Support: ${stats.get('support', 0):.2f} | Resistance: ${stats.get('resistance', 0):.2f}")
        print(f"   ATR: ${stats.get('atr', 0):.2f}")

        print("\n🔧 SMC Indicators:")
        print(f"   FVG Bullish: {'✅' if stats.get('fvg_bullish') else '❌'}")
        print(f"   FVG Bearish: {'✅' if stats.get('fvg_bearish') else '❌'}")
        print(f"   Last BOS: {stats.get('bos', 'NONE')}")
        print(f"\n🎯 Signal: {signal}")
        print(f"📝 Reason: {reason}")
        print("-" * 70)

    def execute_trade(self, signal, price, historical_data, stats):
        """
        Legacy execution wrapper.
        Redirects all execution to execute_enhanced_trade
        to maintain a single execution authority.
        """
        try:
            self.logger.warning(
                "⚠️ Legacy execute_trade() called — redirecting to execute_enhanced_trade()"
            )

            # Forward stats as zones/context (safe fallback)
            zones = stats if isinstance(stats, dict) else {}

            return self.execute_enhanced_trade(
                signal=signal,
                price=price,
                historical_data=historical_data,
                zones=zones
            )

        except Exception as e:
            self.logger.error(f"❌ execute_trade wrapper failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def execute_enhanced_trade(self, signal, price, historical_data, zones):
        """
        SINGLE SOURCE OF TRUTH for trade execution.
        Human-like SMC execution with safety caps.
        """
        try:
            # --- ENTRY PRICE ---
            entry_price = price['ask'] if signal == 'BUY' else price['bid']

            # --- ATR ---
            atr = (
                historical_data['atr'].iloc[-1]
                if 'atr' in historical_data.columns
                else max(entry_price * 0.001, 1.0)
            )

            pip_value = 0.01  # XAUUSD

            # ===== SESSION-SPECIFIC SL (NEW) =====
            try:
                session_name = getattr(self, 'current_session', 'LONDON')
                MIN_SL_PIPS = XAUUSDFilter.get_min_sl_pips(session_name)
                print(f"⚙️ Session {session_name}: Min SL = {MIN_SL_PIPS} pips")
            except:
                MIN_SL_PIPS = 35  # Fallback


            # --- STOP LOSS / TAKE PROFIT ---
            if signal == 'BUY':
                stoploss = entry_price - max(atr * 3, MIN_SL_PIPS * pip_value)
                takeprofit = entry_price + (atr * 4)
            else:
                stoploss = entry_price + max(atr * 3, MIN_SL_PIPS * pip_value)
                takeprofit = entry_price - (atr * 4)

            # --- HARD VALIDATION (never skip) ---
            if signal == 'BUY' and stoploss >= entry_price:
                stoploss = entry_price - (atr * 2)
            if signal == 'SELL' and stoploss <= entry_price:
                stoploss = entry_price + (atr * 2)

            if signal == 'BUY' and takeprofit <= entry_price:
                takeprofit = entry_price + (atr * 3)
            if signal == 'SELL' and takeprofit >= entry_price:
                takeprofit = entry_price - (atr * 3)

            # --- LOT SIZE WITH SAFETY ---
            account_info = self.mt5.get_account_info()
            account_balance = float(account_info.balance) if account_info else 0.0

            lot_size, adjusted_sl = calculate_lot_size_with_safety(
                account_balance=account_balance,
                risk_percent=self.risk_per_trade,
                entry_price=entry_price,
                stop_loss=stoploss,
                max_lot=self.max_lot_size,
                min_sl_pips=self.min_sl_pips
            )
            stoploss = adjusted_sl

            # --- 🛡️ EMERGENCY CAP OVERRIDE ---
            env_cap = os.getenv("EMERGENCY_LOT_CAP")
            final_cap = float(env_cap) if env_cap else getattr(self, 'max_lot_size', 0.05)

            if lot_size > final_cap:
                print(f"   ⚠️ RISK ALERT: Lot size {lot_size:.2f} exceeded cap. Forcing {final_cap:.2f}")
                lot_size = final_cap
            
            lot_size = round(lot_size, 2)

            # --- FINAL LOG ---
            print("\n" + "=" * 70)
            print("✨ ENHANCED TRADE EXECUTION")
            print(f"Signal      : {signal}")
            print(f"Entry       : {entry_price:.2f}")
            print(f"Stop Loss   : {stoploss:.2f}")
            print(f"Take Profit : {takeprofit:.2f}")
            print(f"Lot Size    : {lot_size}")
            print(f"Risk %      : {self.risk_per_trade * 100:.1f}%")
            print(f"SL Distance : {abs(entry_price - stoploss) / pip_value:.1f} pips")
            print("=" * 70)

            # --- EXECUTE ---
            ticket = self.mt5.place_order(signal, lot_size, stoploss, takeprofit)
            if not ticket:
                print("❌ Order rejected by broker")
                return False

            # --- TRACK POSITION ---
            self.open_positions.append({
                "ticket": ticket,
                "signal": signal,
                "entry_price": entry_price,
                "stop_loss": stoploss,
                "take_profit": takeprofit,
                "lot_size": lot_size,
                "entry_time": datetime.now(),
                "zone": self.enhanced_analysis_data.get("current_zone", "UNKNOWN") if hasattr(self, 'enhanced_analysis_data') else "UNKNOWN",
                "session": getattr(self, 'current_session', 'UNKNOWN')
            })

            # --- NEW: LOG TRADE TO TRADELG.JSON ---
            trade_entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                "signal": signal,
                "price": float(entry_price),
                "stoploss": float(stoploss),
                "takeprofit": float(takeprofit),
                "lotsize": float(lot_size),
                "sl_distance_pips": abs(entry_price - stoploss) / pip_value,
                "risk_percent": self.risk_per_trade,
                "zone": self.enhanced_analysis_data.get("current_zone", "UNKNOWN") if hasattr(self, 'enhanced_analysis_data') else "UNKNOWN",
                "session": getattr(self, 'current_session', 'UNKNOWN'),
                "ticket": ticket,
                "status": "OPEN"
            }
            self.trade_log.append(trade_entry)

            # --- SAVE IMMEDIATELY ---
            self.save_trade_log("tradelog.json")

            print(f"✅ Trade executed | Ticket {ticket}")
            print(f"📝 Trade logged to tradelog.json")
            return True

        except Exception as e:
            print(f"❌ execute_enhanced_trade error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def log_trade_analysis(self, signal, reason, price, stats):
        """Log trade analysis"""
        try:
            log_entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S IST'),
                'signal': signal,
                'reason': reason,
                'price': price['bid'] if isinstance(price, dict) and 'bid' in price else float(price),
                'spread': price.get('spread', 0) if isinstance(price, dict) else 0,
                'zone': stats.get('zone', 'UNKNOWN') if isinstance(stats, dict) else 'UNKNOWN',
            }
            self.trade_log.append(log_entry)
        except Exception:
            pass

    def update_dashboard_state(self):
        """
        V3 Dashboard Updater: Force-feeds Account Data to Server
        - Sends: chart_data, equity/balance, open_positions, closed_trades (today)
        - Robust: calls update_bot_state_v2 using positional args first (payload, analysis)
        to avoid "missing required positional argument" errors across different server versions.
        """
        global update_bot_state_v2

        try:
            import MetaTrader5 as mt5_lib
            # 1. FETCH CHART DATA (100 Candles)
            try:
                rates = mt5_lib.copy_rates_from_pos("XAUUSD", mt5_lib.TIMEFRAME_M15, 0, 100)
                if rates is not None and len(rates) > 0:
                    self.chart_data = [
                        {'time': int(x['time']), 'open': float(x['open']), 'high': float(x['high']),
                        'low': float(x['low']), 'close': float(x['close'])}
                        for x in rates
                    ]
            except Exception as e:
                print(f"⚠️ chart_data fetch failed: {e}")

            # 2. FETCH ACCOUNT DATA
            try:
                account = mt5_lib.account_info()
                if account:
                    self.balance = float(account.balance)
                    self.equity = float(account.equity)
            except Exception as e:
                print(f"⚠️ account_info fetch failed: {e}")

            # 3. FETCH OPEN POSITIONS (live)
            formatted_positions = []
            try:
                positions = mt5_lib.positions_get()
                if positions is None:
                    positions = []
                for p in positions:
                    try:
                        ticket = getattr(p, "ticket", getattr(p, "order", None)) or getattr(p, "id", None) or None
                        typ = getattr(p, "type", None)
                        signal = "BUY" if typ == 0 else "SELL" if typ == 1 else getattr(p, "signal", "N/A")
                        volume = float(getattr(p, "volume", getattr(p, "lot", 0.0) or 0.0))
                        price_open = float(getattr(p, "price_open", getattr(p, "price", 0.0) or 0.0))
                        profit = float(getattr(p, "profit", 0.0) or 0.0)
                        tp = float(getattr(p, "tp", 0.0) or 0.0)
                        sl = float(getattr(p, "sl", 0.0) or 0.0)

                        formatted_positions.append({
                            "ticket": str(ticket),
                            "signal": str(signal).upper(),
                            "lot_size": round(volume, 3),
                            "price": round(price_open, 5),
                            "profit": round(profit, 2),
                            "tp": tp, "sl": sl
                        })
                    except Exception as e:
                        print(f"⚠️ position format error (ticket fallback): {e}")
            except Exception as e:
                print(f"⚠️ positions_get failed: {e}")

            # 4. FETCH TODAY's CLOSED DEALS (history) to feed realized PnL
            closed_deals_formatted = []
            try:
                from_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                to_dt = datetime.now()

                deals = mt5_lib.history_deals_get(from_dt, to_dt)
                if deals is None:
                    deals = []

                for d in deals:
                    try:
                        profit = getattr(d, "profit", None)
                        if profit is None:
                            profit = getattr(d, "profit_usd", None)
                        profit = float(profit or 0.0)

                        ts = getattr(d, "time", None) or getattr(d, "time_msc", None) or getattr(d, "time64", None)
                        when = None
                        if isinstance(ts, (int, float)):
                            if ts > 1e12:
                                when = datetime.fromtimestamp(ts / 1000.0)
                            elif ts > 1e9:
                                when = datetime.fromtimestamp(ts)
                            else:
                                when = datetime.fromtimestamp(ts)
                        elif isinstance(ts, str):
                            try:
                                when = datetime.fromisoformat(ts)
                            except:
                                when = None

                        ticket = getattr(d, "ticket", getattr(d, "order", None)) or getattr(d, "id", None)
                        closed_deals_formatted.append({
                            "ticket": str(ticket) if ticket is not None else None,
                            "profit": round(profit, 2),
                            "time": when.isoformat() if when else None,
                            "symbol": getattr(d, "symbol", None)
                        })
                    except Exception as e:
                        print(f"⚠️ history_deals item parse failed: {e}")
            except Exception as e:
                print(f"⚠️ history_deals_get failed: {e}")

            # 5. Build payload
            payload = {
                "equity": getattr(self, "equity", None),
                "balance": getattr(self, "balance", None),
                "last_price": getattr(self, "last_price", getattr(self, "price", None)),
                "open_positions": formatted_positions,
                "closed_trades": closed_deals_formatted,
                "chart_data": getattr(self, "chart_data", []) or []
            }

            analysis_obj = getattr(self, 'enhanced_analysis_data', {})

            # 6. SEND TO DASHBOARD (robust)
            try:
                if self.update_bot_state_v2:
                    # Call with positional args to satisfy both positional or keyword signatures:
                    try:
                        self.update_bot_state_v2(payload, analysis_obj)
                    except TypeError:
                        # fallback: pass a single dict if signature expects it
                        try:
                            self.update_bot_state_v2({"payload": payload, "analysis": analysis_obj, "timestamp": datetime.now().isoformat()})
                        except Exception as e:
                            print(f"⚠️ update_bot_state_v2 fallback failed: {e}")
                else:
                    # HTTP fallback
                    try:
                        url = "http://localhost:8000/api/bot_state"
                        data = {
                            "payload": payload,
                            "analysis": analysis_obj,
                            "timestamp": datetime.now().isoformat()
                        }
                        requests.post(url, json=data, timeout=5)
                    except Exception as e:
                        print(f"⚠️ HTTP dashboard POST failed: {e}")
            except Exception as e:
                print(f"⚠️ Dashboard send failed: {e}")

        except Exception as e:
            print(f"⚠️ Dashboard update failed: {e}")

    def print_smart_summary(self, iteration, price, analysis_data, signal, reason, positions):
        """
        Prints a clean, dashboard-style summary + Charting Assistant.
        FIXED: Calculates PnL locally to prevent AttributeError.
        """
        import os
        width = 75
        print("\n" + "═" * width)
        print(f"🤖 GUARDEER SMC BOT v3.0 | ⏱️ {datetime.now().strftime('%H:%M:%S')} | 🔄 Iteration {iteration}")
        print("═" * width)

        # 1. ACCOUNT & POSITIONS
        bal = 0.0
        eq = 0.0
        current_pnl = 0.0

        # Safe Account Info Fetch
        if hasattr(self, 'mt5') and self.mt5:
            acct = self.mt5.get_account_info()
            if acct:
                bal = float(acct.balance)
                eq = float(acct.equity)
                current_pnl = eq - bal  # ✅ Calculate PnL here instead of relying on self.current_pnl

        pnl_color = "🟢" if current_pnl >= 0 else "🔴"

        print(f"💰 Balance: ${bal:,.2f} | Equity: ${eq:,.2f}")
        print(f"📊 Positions: {len(positions)}/3 | Floating PnL: {pnl_color} ${current_pnl:.2f}")

        for pos in positions:
            # Handle potential missing keys safely
            ticket = pos.get('ticket', 'N/A')
            p_type = pos.get('signal', 'N/A')
            entry = pos.get('entry_price', 0.0)
            # Profit might not be in the tracking dict, display if available
            print(f"   👉 #{ticket} {p_type} @ {entry:.2f}")

        print("─" * width)

        # 2. 🎨 CHARTING ASSISTANT
        print(f"🎨 CHARTING ASSISTANT (Mark these on TradingView):")

        pdh = analysis_data.get('pdh', 0)
        pdl = analysis_data.get('pdl', 0)
        print(f"   💧 LIQUIDITY:")
        print(f"      • PDH (Previous Daily High) : ${pdh:.2f}")
        print(f"      • PDL (Previous Daily Low)  : ${pdl:.2f}")

        inducement = analysis_data.get('inducement_data', {})
        if inducement.get('inducement'):
            print(f"   🪤 INDUCEMENT:")
            print(f"      • Type  : {inducement.get('type')} ({inducement.get('direction')})")
            print(f"      • Level : ${inducement.get('level', 0):.2f}  <-- DRAW LINE HERE")
        else:
            print(f"   🪤 INDUCEMENT: None detected")

        struct = analysis_data.get('market_structure', {})
        print(f"   🏗️  STRUCTURE:")
        print(f"      • Trend : {struct.get('current_trend', 'NEUTRAL')}")
        if struct.get('bos_level'):
            print(f"      • BOS   : ${struct.get('bos_level'):.2f}  <-- MARK BOS")

        print(f"   🎯 POIs (Nearest to Price ${price:.2f}):")
        obs = analysis_data.get('order_blocks', {})
        fvgs = analysis_data.get('fvgs', {})

        # Find nearest OB
        nearest_ob = None
        min_dist = float('inf')
        all_obs = (obs.get('bullish', []) + obs.get('bearish', []))
        for ob in all_obs:
            dist = abs(ob['mean_threshold'] - price) if isinstance(ob, dict) else float('inf')
            if dist < min_dist:
                min_dist = dist
                nearest_ob = ob

        if nearest_ob:
            print(f"      • ORDER BLOCK : ${nearest_ob['mean_threshold']:.2f} ({nearest_ob.get('block_class', 'OB')})")
        else:
            print(f"      • ORDER BLOCK : None nearby")

        # Find nearest FVG
        nearest_fvg = None
        min_dist_fvg = float('inf')
        all_fvgs = (fvgs.get('bullish', []) + fvgs.get('bearish', []))
        for fvg in all_fvgs:
            fvg_level = fvg.get('top', 0) if isinstance(fvg, dict) else (fvg[0] if isinstance(fvg, (list, tuple)) and len(fvg) > 0 else 0)
            dist = abs(fvg_level - price)
            if dist < min_dist_fvg:
                min_dist_fvg = dist
                nearest_fvg = fvg

        if nearest_fvg:
            if isinstance(nearest_fvg, dict):
                top, bottom = nearest_fvg['top'], nearest_fvg['bottom']
            else:
                top, bottom = nearest_fvg[0], nearest_fvg[1]
            print(f"      • FVG ZONE    : ${bottom:.2f} - ${top:.2f}")

        print("─" * width)

        # 3. DECISION ENGINE
        zone_str = analysis_data.get('current_zone', 'UNKNOWN')
        zone_strength = analysis_data.get('zone_strength', 0)

        print(f"🧠 DECISION ENGINE:")
        print(f"   • Zone          : {zone_str} (Strength: {zone_strength:.0f}%)")
        print(f"   • MTF Bias      : {analysis_data.get('mtf_confluence', {}).get('overall_bias', 'N/A')}")

        print("─" * width)
        signal_color = "🟢" if signal == 'BUY' else "🔴" if signal == 'SELL' else "⚪"
        print(f"🚦 FINAL ACTION: {signal_color} {signal} {signal_color}")
        if signal == 'HOLD':
            print(f"   📝 Reason: {reason}")
        else:
            print(f"   🚀 Executing Trade on {price}...")

        print("═" * width + "\n")

    def save_trade_log(self, filename='tradelog.json'):
        """Save trade log to file (Fixes AttributeError)"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.trade_log, f, indent=2, default=str)
            print(f"✅ Trade log saved to {filename}")
        except Exception as e:
            print(f"❌ Error saving trade log: {e}")

    def run(self, interval_seconds=60):
        """Run the trading bot main loop"""
        if not self.initialize():
            return

        strategy_name = "Guardeer 10-Video Enhanced SMC" if self.use_enhanced_smc else "Standard SMC"

        print("=" * 70)
        print("🚀 XAUUSD Trading Bot Started")
        print(f"📊 Strategy: {strategy_name}")
        print(f"⏱️  Interval: Every {interval_seconds} seconds")
        print("✨ Features: FVG, BOS, Liquidity Sweeps, ATR Stops, Session Filters")
        print("📝 Trade logging enabled → tradelog.json")
        print("=" * 70)
        print("Press Ctrl+C to stop...\n")

        try:
            iteration = 0
            while self.running:
                # ===== SESSION DETECTION (SINGLE SOURCE OF TRUTH) =====
                self.current_session, is_active = self.strategy.get_current_session()

                if not is_active:
                    time.sleep(60)
                    continue

                # 🧠 REFLECTION FIRST (HUMAN BEHAVIOR)
                self.sync_closed_positions()

                # SAFETY CHECKS
                if self.check_daily_loss_limit():
                    time.sleep(300)
                    continue

                # MARKET ANALYSIS + EXECUTION
                if self.use_enhanced_smc:
                    self.analyze_enhanced()
                else:
                    self.analyze_and_trade()

                iteration += 1
                print("=" * 70)
                print(f"🔄 Iteration {iteration} | Positions {len(self.open_positions)}/{self.max_positions}")
                print("=" * 70)

                # === NEW: SAVE TRADELG EVERY 5 ITERATIONS ===
                if iteration % 5 == 0:
                    self.save_trade_log("tradelog.json")
                    print(f"💾 Saved tradelog.json ({len(self.trade_log)} entries)")

                # CLEAN TIMING (ONE sleep system only)
                for _ in range(interval_seconds):
                    if not self.running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            print("\n🛑 Bot stopped by user")
        finally:
            # === NEW: FINAL SAVE ON SHUTDOWN ===
            print("💾 Final tradelog save...")
            self.save_trade_log("tradelog.json")
            print(f"✅ Final save complete ({len(self.trade_log)} entries)")
            self.shutdown()

    def shutdown(self):
        """Shutdown the trading bot"""
        self.running = False
        self.save_trade_log()
        # ========================================
        # 📱 TELEGRAM: BOT STOPPED NOTIFICATION
        # ========================================
        try:
            account_info = self.mt5.get_account_info()
            balance_str = f"${account_info.balance:,.2f}" if account_info else "$0.00"
            send_telegram(
                f"🛑 <b>Trading Bot Stopped</b>\n\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
                f"📊 Total Iterations: {len(self.trade_log)}\n"
                f"💰 Final Balance: {balance_str}\n"
                f"📈 Open Positions: {len(self.open_positions)}"
            )
        except Exception:
            pass
        try:
            self.mt5.shutdown()
        except Exception:
            pass
        print("✅ Trading bot shutdown complete")
        print(f"📊 Total iterations: {len(self.trade_log)}")
        print(f"📈 Open positions: {len(self.open_positions)}")

    # ======= ADDED WRAPPERS TO FIX MISSING METHODS CALLED THROUGHOUT THE CLASS =====
    def close_trade(self, ticket, volume=None):
        """
        Wrapper to close a position safely. Tries several mt5 connection methods
        to be compatible with different MT5Connection implementations.
        Returns boolean or API response dict/object.
        """
        try:
            # Prefer mt5.close_position(ticket, volume)
            if hasattr(self.mt5, "close_position"):
                return self.mt5.close_position(ticket, volume) if volume else self.mt5.close_position(ticket)
            # Try close_trade
            if hasattr(self.mt5, "close_trade"):
                return self.mt5.close_trade(ticket, volume) if volume else self.mt5.close_trade(ticket)
            # Try close_order
            if hasattr(self.mt5, "close_order"):
                return self.mt5.close_order(ticket)
            # If none available, attempt to use place_order to hedge-close (not ideal)
            return False
        except Exception as e:
            print(f"❌ close_trade error: {e}")
            return False

    def modify_position(self, ticket, sl=None, tp=None):
        """
        Wrapper to modify position (stop loss / take profit).
        Returns True on success, False otherwise.
        """
        try:
            if hasattr(self.mt5, "modify_position"):
                return self.mt5.modify_position(ticket, sl, tp)
            if hasattr(self.mt5, "modify_trade"):
                return self.mt5.modify_trade(ticket, sl, tp)
            return False
        except Exception as e:
            print(f"❌ modify_position error: {e}")
            return False


# ===== DASHBOARD API SERVER =====
bot_instance = None

def execute_manual_trade(trade_type: str, lot_size: float):
    """Execute manual trade from dashboard"""
    global bot_instance
    if bot_instance is None:
        return False

    try:
        current_price = bot_instance.mt5.get_current_price()
        if not current_price:
            return False

        entry_price = current_price['ask'] if trade_type.upper() == 'BUY' else current_price['bid']
        pip_value = 0.01

        if trade_type.upper() == 'BUY':
            stoploss = entry_price - (50 * pip_value)
            takeprofit = entry_price + (100 * pip_value)
        else:
            stoploss = entry_price + (50 * pip_value)
            takeprofit = entry_price - (100 * pip_value)

        result = bot_instance.mt5.place_order(trade_type.upper(), lot_size, stoploss, takeprofit)
        print(f"✅ Manual {trade_type.upper()} trade executed from dashboard")
        return result

    except Exception as e:
        print(f"❌ Manual trade error: {e}")
        return False


def start_api_server():
    """Start the dashboard API server"""
    try:
        # CORRECTION: Import from root 'server.py', not 'api.server'
        from server import app
        import uvicorn
        import socket

        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)

        print("=" * 70)
        print("📱 DASHBOARD SERVER STARTING...")
        print("=" * 70)
        print(f"💻 Laptop Access: http://localhost:8000/dashboard")
        print(f"📱 Phone Access: http://{local_ip}:8000/dashboard")
        print(f"📚 API Docs: http://localhost:8000/docs")
        print("=" * 70)

        # Log level critical to keep terminal clean
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="critical")

    except Exception as e:
        print(f"❌ API Server error: {e}")


def main():
    """Main function to run the trading bot with dashboard"""
    global bot_instance

    print("=" * 70)
    print("🚀 XAUUSD SMC TRADING BOT v3.0")
    print("✨ Guardeer 10-Video Enhanced SMC")
    print("=" * 70)

    print("🚀 Starting dashboard server...")
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    time.sleep(3)

    # ===== USE ENHANCED SMC BY DEFAULT =====
    use_enhanced = True

    bot_instance = XAUUSDTradingBot(use_enhanced_smc=use_enhanced)

    # ✅ CRITICAL Fix: Initialize connection to MT5 before starting!
    if not bot_instance.initialize():
        print("❌ Bot initialization failed. Exiting...")
        return

    bot_instance.running = True

    # FIXED: Main trading loop replaces missing run()
    iteration_count = 1
    print("\n🚀 Bot Started - Press Ctrl+C to stop")
    print("⏱️  Analysis every 60 seconds")

    try:
        while bot_instance.running:
            print(f"\n{'='*70}")
            print(f"🔄 Iteration {iteration_count} | Positions {len(bot_instance.open_positions)}/3")
            print(f"{'='*70}")

            try:
                # 2. Update Trailing Stops (Needs current price)
                current_price_data = bot_instance.mt5.get_current_price()
                if current_price_data:
                    bid_price = current_price_data['bid'] if isinstance(current_price_data, dict) else current_price_data
                    bot_instance.update_trailing_stops(bid_price)

                # 3. Analyze Market & Trade
                if bot_instance.use_enhanced_smc:
                    bot_instance.analyze_enhanced()
                else:
                    bot_instance.analyze_and_trade()

                iteration_count += 1

                # Clean sleep loop to allow interrupt
                for _ in range(60):
                    if not bot_instance.running:
                        break
                    time.sleep(1)

            except Exception as e:
                print(f"⚠️ Iteration error (continuing): {e}")
                time.sleep(10)


    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    finally:
        if bot_instance:
            bot_instance.running = False
            bot_instance.cleanup()

if __name__ == "__main__":
    main()