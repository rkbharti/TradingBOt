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
import threading
import sys
import os
import requests
import pytz
import numpy as np  # Required for dashboard types

# ============================================================
# MARKET HOURS & SESSION MANAGEMENT
# ============================================================

def is_trading_session():
    """
    SINGLE SOURCE OF TRUTH for session detection (IST)
    Returns: (is_tradeable: bool, session_name: str)
    """
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    hour = now.hour
    minute = now.minute
    t = hour + minute / 60

    # IST Sessions
    if 13.5 <= t < 18.5:
        return True, "LONDON"
    elif 18.5 <= t < 22.5:
        return True, "NY_OVERLAP"
    elif t >= 22.5 or t < 3.5:
        return True, "NY_SESSION"
    else:
        return False, "ASIAN"

# ========================================
# TELEGRAM NOTIFICATION SYSTEM
# ========================================
TELEGRAM_BOT_TOKEN = "8521230130:AAGW2Qa-Sx7b0iroE_qW0e5EI4azRL2DHqM"
TELEGRAM_CHAT_ID = "962450327"
ENABLE_TELEGRAM = True

def send_telegram(message, silent=False):
    """Send notification to Telegram"""
    if not ENABLE_TELEGRAM:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_notification": silent}
        requests.post(url, data=data, timeout=30)
    except Exception as e:
        print(f"   ❌ Telegram error: {e}")

print("✅ Telegram notification system loaded!")

# ========================================
# ZONE VS BIAS CONFIGURATION
# ========================================
STRONG_ZONE_THRESHOLD = 45
WEAK_ZONE_THRESHOLD = 30
ENABLE_STRONG_ZONE_OVERRIDE = True
ENABLE_ZONE_OVERRIDE = True

# ===== CONFIGURATION LOADING =====
def load_config_with_safety(config_path="config.json"):
    """Load config with safety defaults"""
    try:
        with open(config_path, 'r') as f: config = json.load(f)
        tp = config.get("trading_parameters", {})
        return config, tp.get("risk_per_trade", 0.5), tp.get("min_sl_distance_pips", 10.0), tp.get("max_lot_size", 2.0), tp.get("max_positions", 3)
    except: return {}, 0.5, 10.0, 2.0, 3

def calculate_lot_size_with_safety(account_balance, risk_percent, entry_price, stop_loss, max_lot=2.0, min_sl_pips=10.0):
    """Calculate lot size with caps"""
    symbol_point = 0.01
    if abs(entry_price - stop_loss) < (min_sl_pips * symbol_point):
        stop_loss = entry_price - (min_sl_pips * symbol_point) if entry_price > stop_loss else entry_price + (min_sl_pips * symbol_point)
    
    risk_amt = account_balance * (risk_percent / 100)
    risk_pips = abs(entry_price - stop_loss) / symbol_point
    lot_size = round(min(risk_amt / (risk_pips * 10) if risk_pips > 0 else 0.01, max_lot), 2)
    return max(lot_size, 0.01), stop_loss

# ===== MODULE IMPORTS =====
try:
    from strategy.smc_enhanced.liquidity import LiquidityDetector
    from strategy.smc_enhanced.poi import POIIdentifier
    from strategy.smc_enhanced.bias import BiasDetector
    from strategy.smc_enhanced.zones import ZoneCalculator
    from strategy.smc_enhanced.narrative import NarrativeAnalyzer
    SMC_ENHANCED_AVAILABLE = True
    print("✅ Guardeer 10-Video SMC Enhanced Modules Loaded Successfully!")
except ImportError:
    SMC_ENHANCED_AVAILABLE = False
    print("⚠️  SMC Enhanced modules not available")

sys.path.append(os.path.join(os.path.dirname(__file__), 'api'))
DASHBOARD_AVAILABLE = False
update_bot_state = None
try:
    from api.server import update_bot_state
    DASHBOARD_AVAILABLE = True
    print("✅ Dashboard integration loaded")
except: pass

# =====================================================
# 🛡️ PRODUCTION SAFETY CONFIGURATION
# =====================================================
MAX_POSITIONS_PER_DIRECTION = 2
MAX_TOTAL_POSITIONS = 3
DAILY_LOSS_LIMIT_PERCENT = 2.0
MAX_CONSECUTIVE_LOSSES = 3

class XAUUSDTradingBot:
    """Enhanced trading bot with Guardeer's complete 10-video SMC strategy for XAUUSD"""

    def __init__(self, config_path="config.json", use_enhanced_smc=True):
        import logging
        self.config_path = config_path
        
        # Initialize Idea Memory
        self.idea_memory = IdeaMemory(expiry_minutes=30)
        
        # Logger
        os.makedirs("logs", exist_ok=True)
        logging.basicConfig(
            filename=f"logs/tradingbot_{datetime.now().strftime('%Y-%m-%d')}.log",
            level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
        )
        self.logger = logging.getLogger("XAUUSDTradingBot")
        print("✅ IdeaMemory & Logger initialized")

        self.mtf_analyzer = MultiTimeframeFractal(symbol="XAUUSD")
        self.config, self.risk_per_trade, self.min_sl_pips, self.max_lot_size, self.max_positions = load_config_with_safety(config_path)
        self.mt5 = MT5Connection(config_path)
        
        self.strategy = SMCStrategy()
        self.risk_calculator = None
        self.running = False
        self.trade_log = []
        self.open_positions = []
        self.use_enhanced_smc = use_enhanced_smc and SMC_ENHANCED_AVAILABLE
        
        # State Tracking
        self.daily_start_balance = 0.0
        self.daily_loss_limit_triggered = False
        self.consecutive_losses = 0
        self.last_buy_time = None
        self.last_sell_time = None
        self.COOLDOWN_SECONDS = 300
        self.partial_profit_taken = {}
        
        # Analysis Placeholders
        self.liquidity_detector = None
        self.poi_identifier = None
        self.bias_detector = None
        self.volume_analyzer = None
        self.narrative_analyzer = None
        self.enhanced_analysis_data = {}
        self.last_analysis = {}
        self.last_signal = "HOLD"

    def initialize(self):
        """Initialize the trading bot"""
        print("=" * 70)
        print("🤖 Initializing Enhanced XAUUSD Trading Bot...")
        print("=" * 70)
        
        if not self.mt5.initialize_mt5():
            print("❌ Failed to initialize MT5 connection")
            return False
        
        account_info = self.mt5.get_account_info()
        balance = float(account_info.balance) if account_info else 10000.0
        self.daily_start_balance = balance
        
        self.risk_calculator = StopLossCalculator(account_balance=balance, risk_per_trade=self.risk_per_trade)
        self.risk_calculator.min_sl_distance_pips = self.min_sl_pips

        print(f"✅ Account Balance: ${balance:,.2f}")
        print(f"✅ Risk per Trade: {self.risk_per_trade}%")
        print(f"✅ SMC Strategy: {'Guardeer 10-Video Enhanced' if self.use_enhanced_smc else 'Standard'}")
        print("=" * 70)

        self.sync_positions_with_mt5()
        return True

    def fetch_market_data(self):
        """Fetch current market data"""
        hist = self.mt5.get_historical_data(bars=300)
        if hist is None: return None
        if not isinstance(hist, pd.DataFrame): hist = pd.DataFrame(hist)
        price = self.mt5.get_current_price()
        return (hist, price) if price else None

    def sync_positions_with_mt5(self):
        """
        Sync internal position list with MT5.
        FIXED: Removes closed trades immediately to prevent 'Ghost Trades'.
        """
        try:
            mt5_positions = self.mt5.get_open_positions()
            if mt5_positions is None: return

            # Map current MT5 positions by ticket
            current_mt5_map = { (p['ticket'] if isinstance(p, dict) else p.ticket): p for p in mt5_positions }
            
            # --- STEP 1: CLEANUP CLOSED TRADES ---
            # Iterate over a copy [:] so we can modify the original list safely
            for pos in self.open_positions[:]:
                ticket = pos.get('ticket')
                
                # If ticket is NOT in MT5 anymore, it is closed
                if ticket not in current_mt5_map:
                    print(f"   ⚖️  Trade #{ticket} detected closed in MT5")
                    
                    # 1. CRITICAL FIX: Remove from list FIRST
                    # This ensures it disappears from Dashboard/Terminal immediately
                    self.open_positions.remove(pos)
                    
                    # 2. Calculate Outcome
                    deals = self.mt5.history_deals_get(ticket=ticket)
                    profit = sum(d.profit for d in deals) if deals else 0.0
                    outcome = "WIN" if profit > 0 else "LOSS"
                    
                    if outcome == 'LOSS': 
                        self.consecutive_losses += 1
                    else: 
                        self.consecutive_losses = 0

                    # 3. Update IdeaMemory (Safely)
                    # Wrapped in try/except so it doesn't crash the bot if arguments don't match
                    try:
                        self.idea_memory.mark_result(
                            pos['signal'], 
                            pos.get('zone', 'UNKNOWN'), 
                            pos.get('session', 'UNKNOWN'), 
                            outcome
                        )
                    except TypeError:
                        # Fallback for older IdeaMemory versions (2 arguments)
                        # "mark_result() takes 3 positional args but 5 were given" error fix
                        try:
                            self.idea_memory.mark_result(pos['signal'], outcome)
                        except:
                            print("   ⚠️  Could not save to IdeaMemory (ignoring)")

                    print(f"   ✅ Trade Cleaned Up: {pos['signal']} | Result: {outcome} (${profit:.2f})")

            # --- STEP 2: ADOPT ORPHAN TRADES ---
            # (If you opened a trade on your phone, the bot picks it up here)
            tracked_tickets = {p['ticket'] for p in self.open_positions}
            
            for ticket, pos in current_mt5_map.items():
                if ticket not in tracked_tickets:
                    # Determine type (BUY=0, SELL=1)
                    raw_type = pos['type'] if isinstance(pos, dict) else pos.type
                    sig = "BUY" if raw_type == 0 else "SELL"
                    
                    # Extract Data Safely
                    entry = pos['price_open'] if isinstance(pos, dict) else pos.price_open
                    sl = pos['sl'] if isinstance(pos, dict) else pos.sl
                    tp = pos['tp'] if isinstance(pos, dict) else pos.tp
                    vol = pos['volume'] if isinstance(pos, dict) else pos.volume
                    
                    new_pos = {
                        'ticket': ticket, 
                        'signal': sig, 
                        'entry_price': entry,
                        'stop_loss': sl, 
                        'take_profit': tp, 
                        'lot_size': vol,
                        'entry_time': datetime.now(), 
                        'zone': 'RECOVERED',
                        'session': getattr(self, 'current_session', 'UNKNOWN')
                    }
                    self.open_positions.append(new_pos)
                    print(f"   📥 ADOPTED ORPHAN TRADE: {sig} | Ticket: {ticket}")

        except Exception as e:
            print(f"❌ Error syncing positions: {e}")
            import traceback
            traceback.print_exc()

    # =========================================================
    # 🔍 ANALYSIS ENGINE
    # =========================================================

    def analyze_enhanced(self):
        """Complete analysis using ALL 10 Guardeer video concepts + Clean Output"""
        global ZoneCalculator
        is_active, current_session = is_trading_session()
        self.current_session = current_session

        if not is_active: return

        mtf_confluence = self.mtf_analyzer.get_multi_tf_confluence()
        
        try:
            self.sync_positions_with_mt5()
            
            # 1. Get Data
            market_data = self.fetch_market_data()
            if not market_data: return
            historical_data, current_price = market_data

            # 2. Market Structure
            from strategy.market_structure import MarketStructureDetector
            ms_detector = MarketStructureDetector(historical_data)
            structure_analysis = ms_detector.get_market_structure_analysis()

            # 3. Init Modules
            if self.liquidity_detector is None:
                from strategy.smc_enhanced.inducement import InducementDetector
                from strategy.smc_enhanced.volume_analyzer import VolumeAnalyzer
                self.liquidity_detector = LiquidityDetector(historical_data)
                self.poi_identifier = POIIdentifier(historical_data)
                self.bias_detector = BiasDetector(historical_data)
                self.volume_analyzer = VolumeAnalyzer(historical_data)
                self.narrative_analyzer = NarrativeAnalyzer(self.liquidity_detector, self.poi_identifier, self.bias_detector)
            else:
                self.liquidity_detector.df = historical_data
                self.poi_identifier.df = historical_data
                self.bias_detector.df = historical_data
                self.volume_analyzer.df = historical_data

            # 4. Run Analysis Logic (Silent)
            pdh, pdl = self.liquidity_detector.get_previous_day_high_low()
            swings = self.liquidity_detector.get_swing_high_low(lookback=20)
            
            liquidity_levels = {'PDH': pdh, 'PDL': pdl, 'swing_highs': [s['price'] for s in swings.get('highs',[])], 'swing_lows': [s['price'] for s in swings.get('lows',[])]}
            from strategy.smc_enhanced.inducement import InducementDetector
            inducement_detector = InducementDetector(historical_data, liquidity_levels)
            inducement = inducement_detector.detect_latest_inducement(lookback=10)

            order_blocks = self.poi_identifier.find_order_blocks(lookback=50)
            fvgs = self.poi_identifier.find_fvg()
            
            zones = ZoneCalculator.calculate_zones(swings.get('highs',[])[-1]['price'] if swings.get('highs') else 0, swings.get('lows',[])[-1]['price'] if swings.get('lows') else 0)
            current_zone = ZoneCalculator.classify_price_zone(current_price['bid'], zones)
            zone_summary = ZoneCalculator.get_zone_summary(current_price['bid'], zones)
            zone_strength = zone_summary.get('zone_strength', 0) if zone_summary else 0

            # 5. Narrative & Signal
            market_state = {
                'inducement': inducement.get('inducement', False),
                'zone': current_zone,
                'zone_strength': zone_strength,
                'current_direction': structure_analysis.get('current_trend', 'NEUTRAL')
            }
            narrative = self.narrative_analyzer.analyze_market_story(market_state)
            final_signal = narrative.get('trade_signal', 'HOLD')

            # 6. Filter Logic
            # Override for Strong Zones
            if final_signal == 'HOLD' and zone_strength >= 50:
                if current_zone == 'DISCOUNT': final_signal = 'BUY'
                elif current_zone == 'PREMIUM': final_signal = 'SELL'
            
            # Strict Zone Filter
            if final_signal in ['BUY', 'SELL'] and zone_strength < 50:
                final_signal = 'HOLD' 

            # Cooldown Filter
            if final_signal != 'HOLD' and not self.check_trade_cooldown(final_signal):
                final_signal = 'HOLD'

            # 7. Execution Logic
            self.check_zone_based_exits(current_zone, current_price['bid'])
            self.check_partial_profit_targets(current_price['bid'])
            self.update_trailing_stops_with_min_profit(current_price['bid'])

            if len(self.open_positions) < self.max_positions and final_signal != 'HOLD':
                success = self.execute_enhanced_trade(final_signal, current_price, historical_data, zones)
                if success:
                    if final_signal == 'BUY': self.last_buy_time = datetime.now()
                    elif final_signal == 'SELL': self.last_sell_time = datetime.now()

            # 8. DATA PACKAGING (For Dashboard/Summary)
            full_analysis_data = {
                'pdh': pdh, 'pdl': pdl, 'swings': swings, 'inducement_data': inducement,
                'market_structure': structure_analysis, 'order_blocks': order_blocks, 'fvgs': fvgs,
                'current_zone': current_zone, 'zone_strength': zone_strength,
                'mtf_confluence': mtf_confluence, 'narrative': narrative
            }
            
            # Save for dashboard
            self.last_signal = final_signal
            self.enhanced_analysis_data = full_analysis_data
            self.last_analysis = {'zone': current_zone, 'market_structure': structure_analysis.get('current_trend')}

            # 9. OUTPUT & DASHBOARD UPDATE
            reason_msg = "Waiting for setup"
            if zone_strength < 50: reason_msg = f"Zone Weak ({zone_strength:.1f}% < 50%)"
            elif final_signal == 'HOLD': reason_msg = "Market Structure/Inducement Waiting"

            self.print_smart_summary(
                iteration=self.iteration_count if hasattr(self, 'iteration_count') else 0,
                price=current_price['bid'],
                analysis_data=full_analysis_data,
                signal=final_signal,
                reason=reason_msg,
                positions=self.open_positions
            )
            
            # ✅ RESTORED: This was missing in your last error
            self.update_dashboard_state()

        except Exception as e:
            print(f"❌ Error in enhanced analysis: {e}")
            import traceback
            traceback.print_exc()

    # =========================================================
    # 🎨 SMART SUMMARY & DASHBOARD (RESTORED)
    # =========================================================

    def print_smart_summary(self, iteration, price, analysis_data, signal, reason, positions):
        """Prints a clean, dashboard-style summary + Charting Assistant"""
        width = 75
        print("\n" + "═" * width)
        print(f"🤖 GUARDEER SMC BOT v3.0 | ⏱️ {datetime.now().strftime('%H:%M:%S')} | 🔄 Iteration {iteration}")
        print("═" * width)

        bal, eq, current_pnl = 0.0, 0.0, 0.0
        if hasattr(self, 'mt5') and self.mt5:
             acct = self.mt5.get_account_info()
             if acct:
                 bal, eq = float(acct.balance), float(acct.equity)
                 current_pnl = eq - bal
        
        pnl_color = "🟢" if current_pnl >= 0 else "🔴"
        print(f"💰 Balance: ${bal:,.2f} | Equity: ${eq:,.2f}")
        print(f"📊 Positions: {len(positions)}/3 | Floating PnL: {pnl_color} ${current_pnl:.2f}")
        
        for pos in positions:
            print(f"   👉 #{pos.get('ticket')} {pos.get('signal')} @ {pos.get('entry_price',0):.2f}")

        print("─" * width)
        print(f"🎨 CHARTING ASSISTANT (Mark these on TradingView):")
        print(f"   💧 LIQUIDITY: PDH=${analysis_data.get('pdh',0):.2f} | PDL=${analysis_data.get('pdl',0):.2f}")
        
        ind = analysis_data.get('inducement_data', {})
        if ind.get('inducement'):
            print(f"   🪤 INDUCEMENT: {ind.get('type')} @ ${ind.get('level',0):.2f} ({ind.get('direction')})")
        
        struct = analysis_data.get('market_structure', {})
        print(f"   🏗️  STRUCTURE: Trend={struct.get('current_trend')} | BOS={struct.get('bos_level', 'None')}")

        print("─" * width)
        zone_str = analysis_data.get('current_zone', 'UNKNOWN')
        zone_val = analysis_data.get('zone_strength', 0)
        
        print(f"🧠 DECISION ENGINE:")
        print(f"   • Zone: {zone_str} (Strength: {zone_val:.0f}%)")
        print(f"   • Bias: {analysis_data.get('mtf_confluence', {}).get('overall_bias', 'N/A')}")
        
        print("─" * width)
        sig_col = "🟢" if signal == 'BUY' else "🔴" if signal == 'SELL' else "⚪"
        print(f"🚦 FINAL ACTION: {sig_col} {signal} {sig_col}")
        if signal == 'HOLD':
            print(f"   📝 Reason: {reason}")
        print("═" * width + "\n")

    def update_dashboard_state(self):
        """Update dashboard with current bot state (FIXED: Correct Session Display)"""
        if not globals().get('DASHBOARD_AVAILABLE', False) or globals().get('update_bot_state') is None: return

        try:
            account_info = self.mt5.get_account_info()
            current_price = self.mt5.get_current_price()
            balance = float(account_info.balance) if account_info else 0.0
            equity = float(account_info.equity) if account_info else 0.0
            
            trades_list = []
            for pos in self.open_positions:
                # 1. Calculate PnL
                trade_pnl = 0.0
                if current_price:
                    cp = current_price['ask'] if pos['signal'] == 'SELL' else current_price['bid']
                    entry = float(pos.get('entry_price', 0))
                    size = float(pos.get('lot_size', 0))
                    if pos['signal'] == 'BUY':
                        trade_pnl = (cp - entry) * size * 100 
                    else:
                        trade_pnl = (entry - cp) * size * 100

                # 2. Format Time
                entry_time = pos.get('entry_time')
                time_str = entry_time.strftime('%H:%M:%S') if isinstance(entry_time, datetime) else str(entry_time)

                trades_list.append({
                    'id': pos.get('ticket'), 
                    'type': pos.get('signal'), 
                    'lot_size': pos.get('lot_size'),
                    'entry': pos.get('entry_price'), 
                    'sl': pos.get('stop_loss'), 
                    'tp': pos.get('take_profit'),
                    'status': 'OPEN', 
                    'risk_percent': 0,
                    'pnl': round(trade_pnl, 2),
                    'time': time_str,
                    'zone': pos.get('zone', 'LIVE'),
                })

            # ===== FIX: USE GLOBAL SESSION FUNCTION =====
            # This ensures dashboard matches the bot's internal logic
            is_active, session_name = is_trading_session() 

            state = {
                'running': self.running,
                'balance': balance,
                'equity': equity,
                'pnl': round(equity - balance, 2),
                'open_positions_count': len(self.open_positions),
                'current_price': current_price if current_price else {'bid': 0.0, 'ask': 0.0, 'spread': 0.0},
                'last_signal': str(self.last_signal),
                'smc_indicators': {
                    'session': session_name,  # ✅ Now shows "LONDON", "NY_SESSION", etc.
                    'in_trading_hours': is_active,
                }, 
                'technical_levels': {},
                'market_structure': getattr(self, 'last_analysis', {}).get('market_structure', 'NEUTRAL'),
                'zone': getattr(self, 'enhanced_analysis_data', {}).get('current_zone', 'UNKNOWN'),
                'trades': trades_list,
            }
            update_bot_state(state)
        except Exception as e: 
            print(f"⚠️ Dashboard update warning: {e}")

    # =========================================================
    # ⚙️ TRADE MANAGEMENT
    # =========================================================

    def check_trade_cooldown(self, signal_type):
        now = datetime.now()
        last_time = self.last_buy_time if signal_type == 'BUY' else self.last_sell_time
        if last_time and (now - last_time).total_seconds() < self.COOLDOWN_SECONDS:
            return False
        return True

    def check_zone_based_exits(self, current_zone, current_price):
        for pos in list(self.open_positions):
            pos_type = pos.get('signal')
            if (pos_type == 'BUY' and current_zone == 'PREMIUM') or (pos_type == 'SELL' and current_zone == 'DISCOUNT'):
                print(f"🎯 Exiting {pos_type} due to ZONE reversal")
                self.mt5.close_position(pos['ticket'])
                self.open_positions.remove(pos)

    def check_partial_profit_targets(self, current_price):
        for pos in self.open_positions:
            if pos.get('partial_closed'): continue
            entry = pos['entry_price']
            sl = pos.get('stop_loss', 0)
            
            risk_dist = abs(entry - sl)
            if risk_dist < 0.002: risk_dist = 0.002
            target_dist = risk_dist * 2
            
            prof = (current_price - entry) if pos['signal'] == 'BUY' else (entry - current_price)
            if prof >= target_dist:
                vol = round(pos['lot_size'] / 2, 2)
                res = self.mt5.close_position_partial(pos['ticket'], vol)
                if res.get('success'):
                    self.mt5.modify_position(pos['ticket'], new_sl=entry)
                    pos['partial_closed'] = True
                    pos['lot_size'] = res.get('remaining_volume')
                    print(f"💰 Partial profit taken on #{pos['ticket']}")

    def update_trailing_stops_with_min_profit(self, current_price):
        updated = 0
        MIN_PROFIT_PIPS = 50
        for pos in self.open_positions:
            ticket = pos['ticket']
            entry = pos['entry_price']
            sl = pos.get('stop_loss', 0)
            signal = pos['signal']
            
            profit_pips = ((current_price - entry) if signal == 'BUY' else (entry - current_price)) * 100
            if profit_pips < MIN_PROFIT_PIPS: continue
            
            # Trail 2x ATR approx
            dist = 2.0  # Simple fixed distance fallback
            
            if signal == 'BUY':
                new_sl = current_price - dist
                if new_sl > sl:
                    if self.mt5.modify_position(ticket, new_sl=new_sl):
                        pos['stop_loss'] = new_sl
                        updated += 1
            else:
                new_sl = current_price + dist
                if sl == 0 or new_sl < sl:
                    if self.mt5.modify_position(ticket, new_sl=new_sl):
                        pos['stop_loss'] = new_sl
                        updated += 1
        return updated

    def execute_enhanced_trade(self, signal, price, historical_data, zones):
        entry = price['ask'] if signal == 'BUY' else price['bid']
        sl_pips = 35 # Fixed fallback
        
        if signal == 'BUY':
            sl = entry - (sl_pips * 0.01)
            tp = entry + (sl_pips * 0.01 * 2)
        else:
            sl = entry + (sl_pips * 0.01)
            tp = entry - (sl_pips * 0.01 * 2)
            
        lot_size, sl = calculate_lot_size_with_safety(self.daily_start_balance, self.risk_per_trade, entry, sl)
        
        print(f"🚀 Executing {signal}: Lot={lot_size} Entry={entry:.2f}")
        ticket = self.mt5.place_order(signal, lot_size, sl, tp)
        
        if ticket:
            self.open_positions.append({
                'ticket': ticket, 'signal': signal, 'entry_price': entry, 
                'stop_loss': sl, 'take_profit': tp, 'lot_size': lot_size, 
                'entry_time': datetime.now(), 'zone': 'LIVE', 'session': self.current_session
            })
            return True
        return False

    def save_trade_log(self):
        try:
            with open('tradelog.json', 'w') as f: json.dump(self.trade_log, f, default=str)
        except: pass

    def cleanup(self):
        try:
            if hasattr(self, 'mt5'): self.mt5.shutdown()
            print("✅ Bot shutdown complete")
        except: pass

    def run(self, interval_seconds=60):
        if not self.initialize(): return
        self.running = True
        self.iteration_count = 0
        print("\n🚀 Bot Started")
        
        try:
            while self.running:
                self.analyze_enhanced()
                self.iteration_count += 1
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n🛑 Bot stopped by user")
        finally:
            self.cleanup()

    # Legacy Stubs
    def check_daily_loss_limit(self): return False
    def check_consecutive_losses(self): return False
    def analyze_and_trade(self): pass
    def load_historical_trades(self): return []

def start_api_server():
    try:
        from api.server import app
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="critical")
    except: pass

if __name__ == "__main__":
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    time.sleep(2)
    
    bot = XAUUSDTradingBot(use_enhanced_smc=True)
    bot.run()