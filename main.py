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
import threading
import sys
import os
import requests


# ========================================
# TELEGRAM NOTIFICATION SYSTEM
# ========================================
TELEGRAM_BOT_TOKEN = "8537260766:AAHUp5kb8WP2GDxDD4SE8Y0nIIvE1IyIp1g"
TELEGRAM_CHAT_ID = "962450327"
ENABLE_TELEGRAM = True  # Set to False to disable notifications

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
STRONG_ZONE_THRESHOLD = 70  # 70%+ zones can override bias
WEAK_ZONE_THRESHOLD = 30    # Below 30% = too weak to trade

# Allow strong zones to override conflicting bias
ENABLE_STRONG_ZONE_OVERRIDE = True

# Override mode for testing (bypass strict filters)
ENABLE_ZONE_OVERRIDE = True  # Set to False after testing

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
        risk_per_trade = trading_params.get("risk_per_trade", 0.5)  # Default 0.5% (safe)
        min_sl_distance = trading_params.get("min_sl_distance_pips", 10.0)  # Minimum 10 pips
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

sys.path.append(os.path.join(os.path.dirname(__file__), 'api'))

DASHBOARD_AVAILABLE = False
update_bot_state = None
try:
    from api.server import update_bot_state as update_bot_state
    update_bot_state = update_bot_state
    DASHBOARD_AVAILABLE = True
    print("✅ Dashboard integration loaded")
except ImportError as e:
    print(f"⚠️  Dashboard not available: {e}")


class XAUUSDTradingBot:
    """Enhanced trading bot with Guardeer's complete 10-video SMC strategy for XAUUSD"""

    def __init__(self, config_path="config.json", use_enhanced_smc=True):
        self.config_path = config_path
        self.mtf_analyzer = MultiTimeframeFractal(symbol="XAUUSD")
        self.market_structure = None  # Will be initialized when data is available


        
        
        # ===== LOAD CONFIG WITH SAFETY =====
        self.config, self.risk_per_trade, self.min_sl_pips, self.max_lot_size, self.max_positions = \
            load_config_with_safety(config_path)
        
        self.mt5 = MT5Connection(config_path)
        self.strategy = SMCStrategy()
        self.risk_calculator = None
        self.running = False
        self.trade_log = []
        self.open_positions = []

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

        # ===== FIX #4: TRAILING STOPS TRACKER =====
        self.partial_profit_taken = {}  # Track which positions already took partial profit
        self.trailing_stop_levels = {}  # Track trailing stop levels

        if self.use_enhanced_smc:
            print("✅ Using Guardeer 10-Video Enhanced SMC Strategy")
        else:
            print("⚠️  Using Standard SMC Strategy")

    # ===== FIX #3: COOLDOWN FILTER FUNCTION =====
    def check_trade_cooldown(self, signal_type):
        """
        Prevent multiple trades in same direction too quickly
        Cooldown: 5 minutes between BUY trades or SELL trades
        This prevents "catching falling knife" scenarios
        """
        now = datetime.now()
        
        if signal_type == 'BUY':
            if self.last_buy_time:
                time_since_last = (now - self.last_buy_time).total_seconds()
                if time_since_last < self.COOLDOWN_SECONDS:
                    remaining = self.COOLDOWN_SECONDS - time_since_last
                    print(f"   ⏳ BUY cooldown active ({remaining:.0f}s remaining) - SIGNAL BLOCKED")
                    return False
            self.last_buy_time = now
            print(f"   ✅ BUY cooldown cleared - Signal allowed")
            return True
        
        elif signal_type == 'SELL':
            if self.last_sell_time:
                time_since_last = (now - self.last_sell_time).total_seconds()
                if time_since_last < self.COOLDOWN_SECONDS:
                    remaining = self.COOLDOWN_SECONDS - time_since_last
                    print(f"   ⏳ SELL cooldown active ({remaining:.0f}s remaining) - SIGNAL BLOCKED")
                    return False
            self.last_sell_time = now
            print(f"   ✅ SELL cooldown cleared - Signal allowed")
            return True
        
        return True

    # ===== FIX #1: ZONE-BASED EXIT LOGIC =====
    def check_zone_based_exits(self, current_zone, current_price):
        """
        Close BUY trades in PREMIUM zone (take profits!)
        Close SELL trades in DISCOUNT zone (take profits!)
        This is the most critical fix - prevents holding winners too long
        """
        closed_trades = []
        
        try:
            for pos in list(self.open_positions):
                ticket = pos.get('ticket')
                pos_type = pos.get('signal', 'BUY')
                entry_price = pos.get('entry_price', 0)
                profit = 0.0
                
                # Calculate current profit
                if pos_type == 'BUY':
                    profit = (current_price - entry_price) * pos.get('lot_size', 0) * 100
                else:  # SELL
                    profit = (entry_price - current_price) * pos.get('lot_size', 0) * 100
                
                # ===== ZONE-BASED EXIT RULES =====
                should_close = False
                reason = ""
                
                # Close BUY trades in PREMIUM zone
                if pos_type == 'BUY' and current_zone == 'PREMIUM':
                    should_close = True
                    reason = f"BUY in PREMIUM zone (take profits!)"
                
                # Close SELL trades in DISCOUNT zone
                elif pos_type == 'SELL' and current_zone == 'DISCOUNT':
                    should_close = True
                    reason = f"SELL in DISCOUNT zone (take profits!)"
                
                if should_close:
                    print(f"\n   🎯 EXIT SIGNAL: Closing {pos_type} #{ticket}")
                    print(f"      Entry: ${entry_price:.2f} | Current: ${current_price:.2f}")
                    print(f"      Profit: ${profit:.2f}")
                    print(f"      Reason: {reason}")
                    
                    # Close position in MT5
                    close_result = self.mt5.close_position(ticket)
                    
                    if close_result:
                        print(f"      ✅ Position closed successfully")
                        closed_trades.append({
                            'ticket': ticket,
                            'type': pos_type,
                            'profit': profit
                        })
                        self.open_positions.remove(pos)
                    else:
                        print(f"      ❌ Close failed")
            
            # Log results
            if closed_trades:
                total_profit = sum(t['profit'] for t in closed_trades)
                print(f"\n   📊 Zone-Based Exits Complete:")
                print(f"      Closed: {len(closed_trades)} positions")
                print(f"      Total Profit: ${total_profit:.2f}")
            
            return closed_trades
            
        except Exception as e:
            print(f"   ❌ Zone exit check error: {e}")
            return []

    # ===== FIX #4: PARTIAL PROFIT TAKING + TRAILING STOPS =====
    def check_partial_profit_targets(self, current_price: float):
        """
        Check if any positions have reached partial profit targets (2R)
        and take 50% profit while moving SL to breakeven
        """
        try:
            for pos in self.open_positions:
                # Skip if already partially closed
                if pos.get('partial_closed', False):
                    continue
                
                entry = pos['entry_price']
                sl = pos.get('stop_loss', 0)  # ✅ Correct key
                tp = pos['tp']
                original_volume = pos['volume']
                
                # Calculate risk (R)
                if pos['type'] == 'BUY':
                    risk = entry - sl
                    profit_pips = (current_price - entry) * 100
                    target_pips = risk * 2 * 100  # 2R target
                else:  # SELL
                    risk = sl - entry
                    profit_pips = (entry - current_price) * 100
                    target_pips = risk * 2 * 100  # 2R target
                
                # Check if 2R reached
                if profit_pips >= target_pips:
                    print(f"\n   💰 PARTIAL PROFIT TRIGGER: Position #{pos['ticket']}")
                    print(f"      Entry: ${entry:.2f} | Current: ${current_price:.2f}")
                    print(f"      Profit: {profit_pips:.0f} pips (≥ 2R target: {target_pips:.0f})")
                    print(f"      Action: Close 50%, Move SL to breakeven")
                    
                    # Close 50% of position
                    volume_to_close = round(original_volume / 2, 2)
                    
                    result = self.mt5.close_position_partial(
                        ticket=pos['ticket'],
                        volume_to_close=volume_to_close,
                        comment="Partial Profit @ 2R"
                    )
                    
                    if result['success']:
                        print(f"      ✅ Closed {volume_to_close} lots successfully")
                        
                        # Move SL to breakeven
                        modify_result = self.mt5.modify_position(
                            ticket=pos['ticket'],
                            new_sl=entry,
                            comment="SL to Breakeven"
                        )
                        
                        if modify_result['success']:
                            print(f"      ✅ Stop Loss moved to breakeven: ${entry:.2f}")
                            
                            # Update position tracking
                            pos['partial_closed'] = True
                            pos['volume'] = result['remaining_volume']
                            pos['sl'] = entry
                        else:
                            print(f"      ❌ Failed to move SL: {modify_result['message']}")
                    else:
                        print(f"      ❌ Partial close failed: {result['message']}")
                        
        except Exception as e:
            print(f"   ❌ Partial profit check error: {str(e)}")


    # ===== FIX #5: TRAILING STOP IMPLEMENTATION =====
    def update_trailing_stops(self, current_price, min_profit_pips=20):
        """
        Update trailing stops on profitable positions
        Moves SL up by 50% of distance above entry for BUY
        Prevents locking in losses while protecting gains
        """
        updated_count = 0
        
        try:
            for pos in self.open_positions:
                ticket = pos.get('ticket')
                entry = pos.get('entry_price', 0)
                current_sl = pos.get('stop_loss', 0)
                pos_type = pos.get('signal', 'BUY')
                
                if pos_type == 'BUY':
                    current_profit_pips = (current_price - entry) / 0.01
                    
                    # Only trail if profitable beyond min threshold
                    if current_profit_pips >= min_profit_pips:
                        # New SL: entry + 50% of current profit
                        new_sl = entry + ((current_price - entry) * 0.5)
                        
                        # Only move SL upward (never downward)
                        if new_sl > current_sl:
                            old_sl = current_sl
                            self.mt5.modify_position(ticket, new_sl, pos.get('take_profit'))
                            print(f"   📈 Trailing Stop Updated: #{ticket}")
                            print(f"      Old SL: ${old_sl:.2f} → New SL: ${new_sl:.2f}")
                            print(f"      Profit: {current_profit_pips:.1f} pips")
                            updated_count += 1
                
                else:  # SELL
                    current_profit_pips = (entry - current_price) / 0.01
                    
                    if current_profit_pips >= min_profit_pips:
                        # New SL: entry - 50% of current profit
                        new_sl = entry - ((entry - current_price) * 0.5)
                        
                        # Only move SL downward (never upward)
                        if new_sl < current_sl:
                            old_sl = current_sl
                            self.mt5.modify_position(ticket, new_sl, pos.get('take_profit'))
                            print(f"   📉 Trailing Stop Updated: #{ticket}")
                            print(f"      Old SL: ${old_sl:.2f} → New SL: ${new_sl:.2f}")
                            print(f"      Profit: {current_profit_pips:.1f} pips")
                            updated_count += 1
            
            return updated_count
            
        except Exception as e:
            print(f"   ❌ Trailing stop error: {e}")
            return 0

    def sync_positions_with_mt5(self):
        """Sync internal position tracking with actual MT5 positions"""
        try:
            mt5_positions = self.mt5.get_open_positions()
            if mt5_positions is None:
                print("   ⚠️  Could not fetch MT5 positions for sync")
                return

            mt5_tickets = [pos['ticket'] for pos in mt5_positions]
            before_count = len(self.open_positions)
            synced_positions = []

            for pos in self.open_positions:
                ticket = pos.get('ticket')
                if ticket and ticket in mt5_tickets:
                    synced_positions.append(pos)
                else:
                    signal = pos.get('signal', 'UNKNOWN')
                    entry_price = pos.get('entry_price', 0)
                    print(f"   ✅ Removed closed position: {signal} @ {entry_price:.2f} | Ticket: {ticket}")

            self.open_positions = synced_positions
            after_count = len(self.open_positions)

            if before_count != after_count:
                removed = before_count - after_count
                print(f"📊 Position Sync Complete:")
                print(f"   Bot was tracking: {before_count} positions")
                print(f"   MT5 has open: {len(mt5_tickets)} positions")
                print(f"   Removed: {removed} closed positions")
                print(f"   Now tracking: {after_count} positions")

        except Exception as e:
            print(f"❌ Error syncing positions: {e}")

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

     
    def initialize(self):
        """Initialize the trading bot"""
        print("=" * 70)
        print("🤖 Initializing Enhanced XAUUSD Trading Bot...")
        print("=" * 70)

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
                print(f"   ✅ Imported {pos['type']} | Ticket: {pos['ticket']} | {pos['volume']} lots | P/L: ${pos['profit']:.2f}")

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
            print("   ℹ️  Converting market data to DataFrame...")
            if hasattr(historical_data, 'columns'):
                historical_data = pd.DataFrame(historical_data)
            else:
                # Convert numpy array to pandas DataFrame
                historical_data = pd.DataFrame(historical_data)

        current_price = self.mt5.get_current_price()
        if current_price is None:
            print("❌ Could not fetch current price")
            return None

        return historical_data, current_price

    def analyze_enhanced(self):
        global ZoneCalculator
        """Complete analysis using ALL 10 Guardeer video concepts + Multi-TF"""
        
        # ===== STEP 1: RUN MULTI-TIMEFRAME FRACTAL ANALYSIS =====
        mtf_confluence = self.mtf_analyzer.get_multi_tf_confluence()
        
        try:
            self.sync_positions_with_mt5()
            print("📊 Running Enhanced SMC Analysis (Guardeer 10-Videos)...")
            
            # ===== STEP 1.5: MARKET STRUCTURE ANALYSIS (VIDEO 3) =====
            print("📈 VIDEO 3 - MARKET STRUCTURE ANALYSIS")
            print("="*70)

            try:
                # ===== FIX #2: PROPER DATAFRAME PASSING =====
                from strategy.market_structure import MarketStructureDetector
                
                # Get market data FIRST (before structure analysis)
                market_data = self.fetch_market_data()
                if market_data is None:
                    print("   ⚠️  Could not fetch market data for structure analysis")
                    raise Exception("No market data")
                
                historical_data, current_price = market_data
                
                # Now pass the DataFrame correctly - FIXED METHOD!
                market_structure_detector = MarketStructureDetector(historical_data)
                structure_analysis = market_structure_detector.get_market_structure_analysis()  # ✅ FIXED!
                
                print(f"   ✅ Trend: {structure_analysis.get('current_trend', 'NEUTRAL')}")
                print(f"   🔄 Structure Shift: {structure_analysis.get('structure_shift', 'NONE')}")
                print(f"   📊 BOS Level: {structure_analysis.get('bos_level', 'None')}")
                print(f"   📊 CHOCH Detected: {structure_analysis.get('choch_detected', False)}")
                print(f"   📊 Trend Valid: {structure_analysis.get('trend_valid', True)}")
                
            except ImportError as e:
                print(f"   ❌ Module import error: {e}")
                print(f"   ℹ️  Market structure module not found. Checking if file exists...")
                
                import os
                if not os.path.exists('strategy/market_structure.py'):
                    print(f"   ⚠️  market_structure.py not found. Creating basic version...")
                    structure_analysis = {
                        'current_trend': 'NEUTRAL',
                        'trend_valid': True,
                        'structure_shift': 'NONE'
                    }
                else:
                    print(f"   ⚠️  Import failed despite file existing. Check syntax.")
                    structure_analysis = {
                        'current_trend': 'NEUTRAL',
                        'trend_valid': True,
                        'structure_shift': 'NONE'
                    }
                    
            except Exception as e:
                print(f"   ❌ Error in market structure analysis: {str(e)[:100]}")
                print(f"   ℹ️  Using defaults (Video 3 will be skipped this iteration)")
                
                structure_analysis = {
                    'current_trend': 'NEUTRAL',
                    'trend_valid': True,
                    'structure_shift': 'NONE',
                    'bos_level': None,
                    'choch_detected': False
                }






            print("="*70)




            # Initialize modules on first run
            if self.liquidity_detector is None:
                self.liquidity_detector = LiquidityDetector(historical_data)
                self.poi_identifier = POIIdentifier(historical_data)
                self.bias_detector = BiasDetector(historical_data)
                self.narrative_analyzer = NarrativeAnalyzer(self.liquidity_detector, self.poi_identifier, self.bias_detector)
                print("   ✅ Enhanced SMC modules initialized")
            else:
                self.liquidity_detector.df = historical_data
                self.poi_identifier.df = historical_data
                self.bias_detector.df = historical_data

            # VIDEO 5 - LIQUIDITY DETECTION
            print("📋 VIDEO 5 - LIQUIDITY DETECTION")
            try:
                pdh, pdl = self.liquidity_detector.get_previous_day_high_low()
                print(f"   📍 PDH=${pdh:.2f} | PDL=${pdl:.2f}" if pdh and pdl else "   ❌ PDH/PDL Not available")
            except Exception as e:
                print(f"   ❌ Error getting PDH/PDL: {e}")
                pdh, pdl = None, None

            try:
                swings = self.liquidity_detector.get_swing_high_low(lookback=20)
                print(f"   📍 Swing Highs: {len(swings.get('highs', []))} | Swing Lows: {len(swings.get('lows', []))}")
            except Exception as e:
                print(f"   ❌ Error identifying swings: {e}")
                swings = {'highs': [], 'lows': []}

            try:
                liquidity_zones = self.liquidity_detector.get_liquidity_zones()
                liquidity_grabbed = self.liquidity_detector.check_liquidity_grab(current_price['bid'])
                print(f"   ✅ Liquidity Grabbed: {liquidity_grabbed.get('pdh_grabbed') or liquidity_grabbed.get('pdl_grabbed')}")
            except Exception as e:
                print(f"   ❌ Error checking liquidity: {e}")
                liquidity_grabbed = {'pdh_grabbed': False, 'pdl_grabbed': False}

            # VIDEO 6 - POI IDENTIFICATION
            print("🎯 VIDEO 6 - POI IDENTIFICATION")
            try:
                order_blocks = self.poi_identifier.find_order_blocks(lookback=50)
                print(f"   📍 Bullish OBs: {len(order_blocks.get('bullish', []))} | Bearish OBs: {len(order_blocks.get('bearish', []))}")
            except Exception as e:
                print(f"   ❌ Error finding order blocks: {e}")
                order_blocks = {'bullish': [], 'bearish': []}

            try:
                fvgs = self.poi_identifier.find_fvg()
                print(f"   📍 Bullish FVGs: {len(fvgs.get('bullish', []))} | Bearish FVGs: {len(fvgs.get('bearish', []))}")
            except Exception as e:
                print(f"   ❌ Error finding FVGs: {e}")
                fvgs = {'bullish': [], 'bearish': []}
            
            # ===== FIX #5A: VOLUME SPIKE DETECTION =====
            try:
                # Check if FVG fill happened with volume spike
                recent_bars = historical_data.tail(20)
                avg_volume = recent_bars['tick_volume'].iloc[:-1].mean()
                last_volume = recent_bars['tick_volume'].iloc[-1]
                
                volume_spike_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0
                fvg_volume_spike = volume_spike_ratio > 1.5  # 50% above average
                
                if fvg_volume_spike:
                    print(f"   📊 Volume Spike: {volume_spike_ratio:.2f}x (above average) - FVG confirmation strong! ✅")
                else:
                    print(f"   📊 Volume: {volume_spike_ratio:.2f}x (normal) - No spike signal")
                
                volume_confirmation = {
                    'spike_detected': fvg_volume_spike,
                    'ratio': volume_spike_ratio,
                    'threshold': 1.5
                }
            except Exception as e:
                print(f"   ⚠️  Error analyzing volume: {e}")
                volume_confirmation = {'spike_detected': False, 'ratio': 1.0, 'threshold': 1.5}
                
                # ===== FIX #5C: MOMENTUM / RSI CHECK =====
            try:
                # Simple 5‑bar momentum
                recent_prices = historical_data['close'].tail(5).values
                if len(recent_prices) == 5:
                    momentum = float(recent_prices[-1] - recent_prices[0])
                else:
                    momentum = 0.0

                # Basic RSI(14)
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

                print(f"   📊 Momentum (5 bars): {momentum:.2f} | RSI(14): {rsi:.0f}")

                momentum_data = {
                    "momentum": momentum,
                    "rsi": rsi,
                    "overbought": rsi > 70,
                    "oversold": rsi < 30,
                }
            except Exception as e:
                print(f"   ⚠️  Error calculating momentum/RSI: {e}")
                momentum_data = {
                    "momentum": 0.0,
                    "rsi": 50.0,
                    "overbought": False,
                    "oversold": False,
                }



            try:
                idm_probability = self.poi_identifier.identify_idm_sweep(timeframe_minutes=15)
                print(f"   📊 IDM Probability: {idm_probability.get('current_probability', 0) * 100:.0f}%")
            except Exception as e:
                print(f"   ❌ Error calculating IDM: {e}")
                idm_probability = {'current_probability': 0.65}

            try:
                closest_poi = self.poi_identifier.get_closest_poi(current_price['bid'], direction="UP")
                print(f"   📍 Closest POI: ${closest_poi[0]:.2f} ({closest_poi[1]})" if closest_poi else "   ℹ️  No POI found")
            except Exception as e:
                print(f"   ❌ Error finding closest POI: {e}")
                closest_poi = None

            # VIDEO 9 - BIAS DETECTION
            print("🧠 VIDEO 9 - BIAS DETECTION")
            try:
                daily_bias = self.bias_detector.analyze_daily_pattern(historical_data.iloc[-1])
                print(f"   📊 Daily Bias: {daily_bias}")
            except Exception as e:
                print(f"   ❌ Error analyzing daily pattern: {e}")
                daily_bias = "NEUTRAL"

            try:
                intraday_bias = self.bias_detector.get_intraday_bias(lookback=20)
                print(f"   📊 Intraday Bias: {intraday_bias}")
            except Exception as e:
                print(f"   ❌ Error getting intraday bias: {e}")
                intraday_bias = "NEUTRAL"

            try:
                price_action_bias = self.bias_detector.get_price_action_bias()
                print(f"   📊 Price Action: {price_action_bias}")
            except Exception as e:
                print(f"   ❌ Error getting price action bias: {e}")
                price_action_bias = "NEUTRAL"

            try:
                combined_bias = self.bias_detector.get_combined_bias(daily_bias, intraday_bias, price_action_bias)
                print(f"   📊 Combined Bias: {combined_bias}")
            except Exception as e:
                print(f"   ❌ Error combining bias: {e}")
                combined_bias = "NEUTRAL"

            # VIDEO 10a - ZONE ANALYSIS
            print("📦 VIDEO 10a - ZONE ANALYSIS")
            try:
                swing_highs = swings.get('highs', [])
                swing_lows = swings.get('lows', [])
                latest_swing_high = swing_highs[-1]['price'] if swing_highs else current_price['bid']
                latest_swing_low = swing_lows[-1]['price'] if swing_lows else current_price['bid']

                zones = ZoneCalculator.calculate_zones(latest_swing_high, latest_swing_low)
                current_zone = ZoneCalculator.classify_price_zone(current_price['bid'], zones)
                zone_summary = ZoneCalculator.get_zone_summary(current_price['bid'], zones)

                print(f"   📍 Current Zone: {current_zone}")
                if zone_summary:
                    print(f"   📊 Zone Strength: {zone_summary.get('zone_strength', 0):.0f}%")
                    print(f"   ✅ Can BUY: {zone_summary.get('can_buy')} | Can SELL: {zone_summary.get('can_sell')}")
                    if zone_summary.get('next_target'):
                        print(f"   📈 Next Target: ${zone_summary.get('next_target'):.2f}")
            except Exception as e:
                print(f"   ❌ Error analyzing zones: {e}")
                zones = {}
                current_zone = "EQUILIBRIUM"
                zone_summary = None

            # VIDEO 10b - NARRATIVE 3Bs FRAMEWORK
            print("📖 VIDEO 10b - NARRATIVE 3Bs")
            try:
                market_state = {
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
                print(f"   📌 B1 (Recent Action): {narrative.get('b1', {}).get('narrative', 'N/A')}")
                print(f"   📌 B2 (Current Framework): {narrative.get('b2', {}).get('narrative', 'N/A')}")
                print(f"   📌 B3 (Dealing Range): {narrative.get('b3', {}).get('narrative', 'N/A')}")
                print(f"   🎯 Trade Signal: {narrative.get('trade_signal', 'HOLD')}")
                print(f"   📊 Confidence: {narrative.get('confidence', 0):.0f}%")
                print(f"   🧭 Bias: {narrative.get('bias', 'NEUTRAL')}")
            except Exception as e:
                print(f"   ❌ Error in narrative analysis: {e}")
                narrative = {'trade_signal': 'HOLD', 'confidence': 0, 'bias': 'NEUTRAL'}

            final_signal = narrative.get('trade_signal', 'HOLD')
            
            # ===== FIX #6: NARRATIVE OVERRIDE FOR MISSING SIGNALS =====
            print("\n🔧 FIX #6: NARRATIVE OVERRIDE CHECK")

            if zone_summary:
                print(f"   📊 Zone: {current_zone} | Strength: {zone_summary.get('zone_strength', 0):.0f}% | Signal: {final_signal}")
            else:
                print(f"   ⚠️  No zone_summary available!")

            # Override logic with LOWER threshold (50% instead of 70%)
            if final_signal == 'HOLD' and current_zone == 'DISCOUNT' and zone_summary:
                zone_str = zone_summary.get('zone_strength', 0)
                if zone_str > 50:
                    print(f"   🎯 OVERRIDE: DISCOUNT ({zone_str:.0f}%) → Forcing BUY Signal")
                    final_signal = 'BUY'
                    print(f"   ✅ Signal changed to: {final_signal}")
                else:
                    print(f"   ⚠️  Zone strength too weak ({zone_str:.0f}% < 50%) - No override")
                    
            elif final_signal == 'HOLD' and current_zone == 'PREMIUM' and zone_summary:
                zone_str = zone_summary.get('zone_strength', 0)
                if zone_str > 50:
                    print(f"   🎯 OVERRIDE: PREMIUM ({zone_str:.0f}%) → Forcing SELL Signal")
                    final_signal = 'SELL'
                    print(f"   ✅ Signal changed to: {final_signal}")
                else:
                    print(f"   ⚠️  Zone strength too weak ({zone_str:.0f}% < 50%) - No override")
                    
            else:
                print(f"   ℹ️  No override needed. Current signal: {final_signal}")
                
            # ===== MARKET STRUCTURE FILTER (VIDEO 3) =====
            print("\n🔍 MARKET STRUCTURE VALIDATION")
            print("="*70)

            # Structure validation rules
            STRUCTURE_FILTER_ENABLED = True
            TREND_BREAKAGE_BLOCKS_ENTRY = True

            if STRUCTURE_FILTER_ENABLED:
                # Check if trend is valid
                if not structure_analysis['trend_valid']:
                    print(f"   ⚠️  Trend is NO LONGER VALID - BOS detected")
                    print(f"   🚫 Signal blocked due to trend invalidation")
                    final_signal = 'HOLD'
                
                # Check for trend alignment
                elif final_signal == 'BUY' and structure_analysis['current_trend'] == 'DOWNTREND':
                    print(f"   📊 BUY signal but trend is DOWNTREND")
                    print(f"   ⚠️  Structure doesn't support BUY")
                    if structure_analysis['structure_shift'] not in ['CHOCH_BULLISH', 'BOS_BULLISH']:
                        print(f"   🚫 No CHOCH/BOS confirmation - Signal BLOCKED")
                        final_signal = 'HOLD'
                    else:
                        print(f"   ✅ But {structure_analysis['structure_shift']} detected - Signal allowed")
                
                elif final_signal == 'SELL' and structure_analysis['current_trend'] == 'UPTREND':
                    print(f"   📊 SELL signal but trend is UPTREND")
                    print(f"   ⚠️  Structure doesn't support SELL")
                    if structure_analysis['structure_shift'] not in ['CHOCH_BEARISH', 'BOS_BEARISH']:
                        print(f"   🚫 No CHOCH/BOS confirmation - Signal BLOCKED")
                        final_signal = 'HOLD'
                    else:
                        print(f"   ✅ But {structure_analysis['structure_shift']} detected - Signal allowed")
                
                else:
                    print(f"   ✅ Signal aligned with market structure")
                    print(f"   📊 Trend: {structure_analysis['current_trend']}")
                    print(f"   🔄 Shift: {structure_analysis['structure_shift']}")

            print("="*70)


            # ===== NEW: MULTI-TIMEFRAME CONFIDENCE FILTER =====
            print("\n🔍 MULTI-TIMEFRAME CONFLUENCE FILTER")
            print("="*70)

            # ===== FIX #1: DYNAMIC THRESHOLD LOGIC =====
            MTF_BASE_CONFIDENCE = 60
            zone_strength = zone_summary.get('zone_strength', 0) if zone_summary else 0

            # Calculate dynamic threshold
            if zone_strength > 70:
                MTF_MIN_CONFIDENCE = 50
                print(f"   📊 Zone is STRONG ({zone_strength:.0f}%) → Lowering MTF threshold to 50%")
            elif mtf_confluence['confidence'] >= 80:
                MTF_MIN_CONFIDENCE = 40
                print(f"   📊 MTF Confidence is VERY HIGH ({mtf_confluence['confidence']}%) → Lowering threshold to 40%")
            else:
                MTF_MIN_CONFIDENCE = MTF_BASE_CONFIDENCE
                print(f"   📊 Using default MTF threshold: {MTF_MIN_CONFIDENCE}%")

            print(f"   📊 Current MTF Confidence: {mtf_confluence['confidence']}% (Required: {MTF_MIN_CONFIDENCE}%)")
            print(f"   📊 MTF Bias: {mtf_confluence['overall_bias']}")

            if mtf_confluence['confidence'] < MTF_MIN_CONFIDENCE:
                print(f"   ⚠️  MTF Confidence BELOW threshold")
                print(f"   🚫 {final_signal} signal → Changed to HOLD")
                final_signal = 'HOLD'
            elif final_signal == 'BUY' and mtf_confluence['overall_bias'] == 'BEARISH':
                print(f"   📊 BUY conflicts with BEARISH MTF bias")
                print(f"   🚫 BUY signal → Changed to HOLD")
                final_signal = 'HOLD'
            elif final_signal == 'SELL' and mtf_confluence['overall_bias'] == 'BULLISH':
                print(f"   📊 SELL conflicts with BULLISH MTF bias")
                print(f"   🚫 SELL signal → Changed to HOLD")
                final_signal = 'HOLD'
            else:
                print(f"   ✅ Signal {final_signal} CONFIRMED by MTF analysis")


            print("="*70)



            # ===== ZONE FILTER VALIDATION =====
            print("\n🔍 ZONE FILTER VALIDATION")
            
            zone_str = zone_summary.get('zone_strength', 0) if zone_summary else 0
            
            ENABLE_ZONE_OVERRIDE = True
            zone_allows_trade = False
            
            if ENABLE_ZONE_OVERRIDE:
                print("   🚨 OVERRIDE MODE ACTIVE - Zone filter relaxed for testing")
                
                # ===== FIX #3: LOWERED THRESHOLDS FOR M5 TIMEFRAME =====
                # Original: 70% was too high for M5 (zones rarely reached 70%)
                # New: 40% is more realistic for M5 intraday trading
                STRONG_ZONE_THRESHOLD = 40  # Lowered from 70%
                WEAK_ZONE_THRESHOLD = 20    # Lowered from 30%
                ENABLE_STRONG_ZONE_OVERRIDE = True

                print(f"   🎚️  Zone Thresholds: Strong={STRONG_ZONE_THRESHOLD}% | Weak={WEAK_ZONE_THRESHOLD}%")
                print(f"   📊 Current Strength: {zone_str:.0f}%")

                
                # ===== FIX #3: RECALIBRATE ZONE STRENGTH =====
                

                # Add ATR-based adjustment if available
                atr_value = historical_data.get('atr', {}).iloc[-1] if 'atr' in historical_data.columns else None

                if atr_value and zone_summary:
                    from strategy.smc_enhanced.zones import ZoneCalculator
                    zone_str_atr = ZoneCalculator.get_zone_strength_atr(
                        current_price['bid'],
                        zones,
                        atr=atr_value
                    )
                    print(f"   📊 Zone Strength (Base): {zone_str:.0f}% → (ATR-Adjusted): {zone_str_atr:.0f}%")
                    zone_str = zone_str_atr
                else:
                    print(f"   📊 Zone Strength: {zone_str:.0f}%")

                is_strong_zone = zone_str >= STRONG_ZONE_THRESHOLD
                is_weak_zone = zone_str <= WEAK_ZONE_THRESHOLD
                
                if final_signal == 'BUY':
                    if combined_bias in ['BULLISH', 'HIGHER_HIGH', 'NEUTRAL']:
                        # ===== FIX #5: APPLY ALL FILTERS =====
                        # Initialize atr_filter_active if not set yet
                        if 'atr_filter_active' not in locals():
                            atr_filter_active = False
                        filter_checks = {
                            'atr_ok': not atr_filter_active,
                            'volume_ok': volume_confirmation.get('spike_detected', True),
                            'momentum_ok': momentum_data.get('rsi', 50) < 70  # Not overbought
                        }
                        
                        if not filter_checks['atr_ok']:
                            print(f"   ⚠️  BUY blocked - ATR filter (volatility too low)")
                            zone_allows_trade = False
                        elif not filter_checks['volume_ok']:
                            print(f"   ⚠️  BUY blocked - Volume filter (no spike confirmation)")
                            zone_allows_trade = False
                        elif not filter_checks['momentum_ok']:
                            print(f"   ⚠️  BUY blocked - Momentum filter (RSI overbought)")
                            zone_allows_trade = False
                        else:
                            print(f"   ✅ BUY signal allowed - All filters passed!")
                            zone_allows_trade = True

                        
                        send_telegram(
                            f"🟢 <b>BUY SIGNAL DETECTED!</b>\n\n"
                            f"💰 Price: ${current_price['bid']:.2f}\n"
                            f"📊 Zone: {current_zone}\n"
                            f"💪 Zone Strength: {zone_str:.0f}%\n"
                            f"🎯 MTF Bias: {mtf_confluence['overall_bias']} ({mtf_confluence['confidence']}%)\n"
                            f"🎯 Daily Bias: {daily_bias}\n"
                            f"🎯 Combined: {combined_bias}\n"
                            f"📈 Confidence: {narrative.get('confidence', 0):.0f}%\n"
                            f"⏰ {datetime.now().strftime('%H:%M:%S IST')}"
                        )
                    
                    elif ENABLE_STRONG_ZONE_OVERRIDE and is_strong_zone and current_zone == 'DISCOUNT':
                        # ===== FIX #3: LOWERED THRESHOLD ALLOWS MORE OVERRIDES =====
                        print(f"   🎯 BUY signal allowed - STRONG DISCOUNT zone ({zone_str:.0f}% > {STRONG_ZONE_THRESHOLD}%) overrides {combined_bias} bias")
                        print(f"   💡 Counter-trend reversal setup detected")
                        print(f"   ℹ️  Narrative forcing trade despite conflicting bias")
                        zone_allows_trade = True
                    
                    elif is_weak_zone:
                        print(f"   ⚠️  BUY signal BLOCKED - Zone too weak ({zone_str:.0f}% < {WEAK_ZONE_THRESHOLD}%)")
                        zone_allows_trade = False
                    
                    else:
                        print(f"   ⚠️  BUY signal BLOCKED - Zone not strong enough ({zone_str:.0f}% < {STRONG_ZONE_THRESHOLD}%)")
                        zone_allows_trade = False
                
                elif final_signal == 'SELL':
                    if combined_bias in ['BEARISH', 'LOWER_LOW', 'NEUTRAL']:
                        print(f"   ✅ SELL signal allowed - Bias confirms ({combined_bias})")
                        zone_allows_trade = True
                        
                        send_telegram(
                            f"🔴 <b>SELL SIGNAL DETECTED!</b>\n\n"
                            f"💰 Price: ${current_price['bid']:.2f}\n"
                            f"📊 Zone: {current_zone}\n"
                            f"💪 Zone Strength: {zone_str:.0f}%\n"
                            f"🎯 MTF Bias: {mtf_confluence['overall_bias']} ({mtf_confluence['confidence']}%)\n"
                            f"🎯 Daily Bias: {daily_bias}\n"
                            f"🎯 Combined: {combined_bias}\n"
                            f"📈 Confidence: {narrative.get('confidence', 0):.0f}%\n"
                            f"⏰ {datetime.now().strftime('%H:%M:%S IST')}"
                        )
                    
                    elif ENABLE_STRONG_ZONE_OVERRIDE and is_strong_zone and current_zone == 'PREMIUM':
                        # ===== FIX #3: LOWERED THRESHOLD ALLOWS MORE OVERRIDES =====
                        print(f"   🎯 SELL signal allowed - STRONG PREMIUM zone ({zone_str:.0f}% > {STRONG_ZONE_THRESHOLD}%) overrides {combined_bias} bias")
                        print(f"   💡 Counter-trend reversal setup detected")
                        print(f"   ℹ️  Narrative forcing trade despite conflicting bias")
                        zone_allows_trade = True

                    
                    elif is_weak_zone:
                        print(f"   ⚠️  SELL signal BLOCKED - Zone too weak ({zone_str:.0f}% < {WEAK_ZONE_THRESHOLD}%)")
                        zone_allows_trade = False
                    
                    else:
                        print(f"   ⚠️  SELL signal BLOCKED - Zone not strong enough ({zone_str:.0f}% < {STRONG_ZONE_THRESHOLD}%)")
                        zone_allows_trade = False
                
                else:
                    print(f"   ℹ️  Signal is HOLD - No trade decision needed")
                    zone_allows_trade = False
                
                print(f"\n   📊 Filter Analysis:")
                print(f"      • Current Zone: {current_zone}")
                print(f"      • Zone Strength: {zone_str:.0f}%")
                print(f"      • MTF Confidence: {mtf_confluence['confidence']}%")
                print(f"      • MTF Bias: {mtf_confluence['overall_bias']}")
                print(f"      • Combined Bias: {combined_bias}")
                print(f"      • Signal: {final_signal}")
                print(f"      • Trade Allowed?: {zone_allows_trade}")

            else:
                print("   🔒 STRICT MODE - No zone overrides allowed")
                
                if final_signal == 'BUY' and combined_bias in ['BULLISH', 'NEUTRAL']:
                    zone_allows_trade = True
                elif final_signal == 'SELL' and combined_bias in ['BEARISH', 'NEUTRAL']:
                    zone_allows_trade = True
                else:
                    zone_allows_trade = False

            


            
            # Calculate technical indicators
            atr_filter_active = False  # <-- ensure defined for all paths

            try:
                df = historical_data.copy()
                
                if len(df) >= 200:
                    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
                    ema200 = float(df['ema200'].iloc[-1])
                else:
                    ema200 = 0.0

                if len(df) >= 50:
                    df['ma20'] = df['close'].rolling(window=20).mean()
                    df['ma50'] = df['close'].rolling(window=50).mean()
                    ma20 = float(df['ma20'].iloc[-1])
                    ma50 = float(df['ma50'].iloc[-1])
                else:
                    ma20 = 0.0
                    ma50 = 0.0

                recent_bars = min(50, len(df))
                support = float(df['low'].tail(recent_bars).min())
                resistance = float(df['high'].tail(recent_bars).max())

                if len(df) >= 14:
                    df['high_low'] = df['high'] - df['low']
                    df['high_close'] = abs(df['high'] - df['close'].shift(1))
                    df['low_close'] = abs(df['low'] - df['close'].shift(1))
                    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
                    df['atr'] = df['tr'].rolling(window=14).mean()
                    atr = float(df['atr'].iloc[-1])
                else:
                    atr = 0.0

                # ===== FIX #5B: ATR VOLATILITY FILTER =====
                MIN_ATR_XAUUSD = 1.5  # tune as needed

                if atr < MIN_ATR_XAUUSD:
                    print(f"   ⚠️  ATR too low ({atr:.2f} < {MIN_ATR_XAUUSD}) - Pausing entries")
                    atr_filter_active = True
                else:
                    print(f"   ✅ ATR healthy ({atr:.2f}) - Trading allowed")
                    atr_filter_active = False

            except Exception as e:
                print(f"   ⚠️  Error calculating technical levels: {e}")
                ema200 = ma20 = ma50 = support = resistance = atr = 0.0
                atr_filter_active = True # treat error as: block entries
            
            # Store analysis data
            self.enhanced_analysis_data = {
                'pdh': pdh,
                'pdl': pdl,
                'swings': swings,
                'order_blocks': order_blocks,
                'fvgs': fvgs,
                'daily_bias': daily_bias,
                'current_zone': current_zone,
                'narrative': narrative,
                'zones': zones,
                'mtf_confluence': mtf_confluence,
                'market_structure': structure_analysis,
                'volume_confirmation': volume_confirmation,
                'momentum_data': momentum_data,
                'atr_filter_active': atr_filter_active,    # ADD THIS LINE
            }

            self.last_analysis = {
                'smc_indicators': self.enhanced_analysis_data,
                'technical_levels': {
                    'ema200': ema200,
                    'ma20': ma20,
                    'ma50': ma50,
                    'support': support,
                    'resistance': resistance,
                    'atr': atr,
                },
                'zone': current_zone,
                'bias': combined_bias
            }

            # ===== FIX #1: ZONE-BASED EXITS =====
            print("\n🔴 FIX #1: ZONE-BASED EXITS")
            self.check_zone_based_exits(current_zone, current_price['bid'])

            # ===== FIX #4: PARTIAL PROFIT TAKING =====
            print("\n💰 FIX #4: PARTIAL PROFIT TAKING")
            self.check_partial_profit_targets(current_price['bid'])

            # ===== FIX #5: UPDATE TRAILING STOPS =====
            print("\n📈 FIX #5: TRAILING STOPS")
            trailing_count = self.update_trailing_stops(current_price['bid'])
            if trailing_count == 0:
                print("   ℹ️  No positions ready for trailing stops")

            # Execute trade if conditions met
            at_max_positions = len(self.open_positions) >= self.max_positions
            
            # ===== FIX #7: SESSION FILTER CHECK =====
            print("\n⏰ FIX #7: SESSION FILTER")
            session_name, is_active = self.strategy.get_current_session()

            if is_active:
                print(f"   ✅ Session: {session_name} is OPEN - Trading allowed")
            else:
                print(f"   ⏸️  Session: {session_name} is CLOSED - Trading blocked")
                if final_signal != 'HOLD':
                    print(f"   🚫 {final_signal} signal suppressed due to closed session")
                    final_signal = 'HOLD'

            if not at_max_positions and final_signal != 'HOLD' and zone_allows_trade:
                # ===== FIX #3: CHECK COOLDOWN BEFORE TRADING =====
                print("\n🎯 FIX #3: COOLDOWN FILTER")
                if self.check_trade_cooldown(final_signal):
                    print(f"✅ Executing {final_signal} trade...")
                    self.execute_enhanced_trade(final_signal, current_price, historical_data, zones)
                else:
                    print(f"   🚫 Trade blocked by cooldown")
            elif at_max_positions and final_signal != 'HOLD':
                print(f"⚠️  Signal {final_signal} detected but max positions ({self.max_positions}) reached")

            self.log_trade_analysis(final_signal, 'Enhanced SMC Analysis', current_price, market_state)
            self.update_dashboard_state()

        except Exception as e:
            print(f"❌ Error in enhanced analysis: {e}")
            import traceback
            traceback.print_exc()
            
            send_telegram(
                f"⚠️ <b>BOT ERROR!</b>\n\n"
                f"<code>{str(e)[:200]}</code>\n\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n\n"
                f"🔧 Bot is still running and monitoring..."
            )



    def analyze_and_trade(self):
        """Standard SMC analysis (used when enhanced mode disabled)"""
        try:
            self.sync_positions_with_mt5()

            at_max_positions = len(self.open_positions) >= self.max_positions
            if at_max_positions:
                print(f"⚠️  Max positions ({self.max_positions}) reached. Monitoring only...")
                print(f"   Currently tracking {len(self.open_positions)} positions")
                return

            print(f"📊 Analyzing market at {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")

            market_data = self.fetch_market_data()
            if market_data is None:
                return

            historical_data, current_price = market_data

            signal, reason = self.strategy.generate_signal(historical_data)
            stats = self.strategy.get_strategy_stats(historical_data)

            self.last_signal = signal
            self.last_analysis = {
                'smc_indicators': {
                    'fvg_bullish': stats.get('fvg_bullish', False),
                    'fvg_bearish': stats.get('fvg_bearish', False),
                    'bos': str(stats.get('bos')) if stats.get('bos') else None,
                    'session': stats.get('session', 'CLOSED'),
                },
                'technical_levels': {
                    'ma20': stats.get('ma20', 0),
                    'ma50': stats.get('ma50', 0),
                    'ema200': stats.get('ema200', 0),
                    'support': stats.get('support', 0),
                    'resistance': stats.get('resistance', 0),
                    'atr': stats.get('atr', 0),
                },
                'market_structure': stats.get('market_structure', 'NEUTRAL'),
                'zone': stats.get('zone', 'EQUILIBRIUM'),
            }

            self.display_analysis(current_price, signal, reason, stats)

            if not at_max_positions and signal != 'HOLD':
                total_risk = sum(pos.get('risk_percent', 0) for pos in self.open_positions)
                can_trade, risk_msg = self.risk_calculator.check_risk_limits(self.open_positions, total_risk)

                if can_trade:
                    self.execute_trade(signal, current_price, historical_data, stats)
                else:
                    print(f"⚠️  Trade blocked: {risk_msg}")
            elif at_max_positions and signal != 'HOLD':
                print(f"⚠️  Signal {signal} detected, but max positions reached. Skipping trade.")

            self.log_trade_analysis(signal, reason, current_price, stats)
            self.update_dashboard_state()

        except Exception as e:
            print(f"❌ Error in analyze_and_trade: {e}")
            import traceback
            traceback.print_exc()

    def display_analysis(self, price, signal, reason, stats):
        """Display standard market analysis"""
        print(f"\n💰 XAUUSD Price: ${price['bid']:.2f} | Spread: {price['spread']:.2f}")
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
        """Execute trade with standard parameters + SAFETY CAPS"""
        entry_price = price['ask'] if signal == 'BUY' else price['bid']
        atr = stats.get('atr', entry_price * 0.01)
        market_structure = stats.get('market_structure', 'NEUTRAL')

        stoploss, takeprofit = self.risk_calculator.calculate_stop_loss_takeprofit(
            signal, entry_price, atr, stats.get('zone', 'EQUILIBRIUM'), market_structure
        )

        # ===== FIX #3: USE SAFE LOT SIZE CALCULATION =====
        lot_size, adjusted_stoploss = calculate_lot_size_with_safety(
            account_balance=self.mt5.get_account_info().balance if self.mt5.get_account_info() else 50000,
            risk_percent=self.risk_per_trade,
            entry_price=entry_price,
            stop_loss=stoploss,
            max_lot=self.max_lot_size,
            min_sl_pips=self.min_sl_pips
        )
        
        stoploss = adjusted_stoploss

        risk_metrics = self.risk_calculator.get_risk_metrics(entry_price, stoploss, lot_size, takeprofit)

        print(f"\n{'='*70}")
        print(f"📋 Trade Execution Details")
        print(f"{'='*70}")
        print(f"   Direction: {signal}")
        print(f"   Entry: ${entry_price:.2f}")
        print(f"   Stop Loss: ${stoploss:.2f} ({risk_metrics['stoploss_pips']:.2f} pips)")
        print(f"   Take Profit: ${takeprofit:.2f} ({risk_metrics['takeprofit_pips']:.2f} pips)")
        print(f"   Lot Size: {lot_size} lots")
        print(f"   Risk: ${risk_metrics['risk_amount']:.2f} ({risk_metrics['risk_percent']:.2f}%)")
        print(f"   R:R Ratio: 1:{risk_metrics['reward_ratio']:.1f}")
        print(f"{'='*70}\n")

        ticket = self.mt5.place_order(signal, lot_size, stoploss, takeprofit)

        if ticket:
            position = {
                'ticket': ticket,
                'signal': signal,
                'entry_price': entry_price,
                'stop_loss': stoploss,
                'take_profit': takeprofit,
                'lot_size': lot_size,
                'entry_time': datetime.now(),
                'risk_percent': risk_metrics['risk_percent'],
                'atr': atr,
                'zone': stats.get('zone', 'EQUILIBRIUM'),
                'market_structure': market_structure
            }
            self.open_positions.append(position)
            print(f"✅ Position opened successfully")
            print(f"   Ticket: {ticket}")
            print(f"   Open Positions: {len(self.open_positions)}/{self.max_positions}")
            return True
        else:
            print(f"❌ Order placement failed")
            return False

    def execute_enhanced_trade(self, signal, price, historical_data, zones):
        """Execute trade with enhanced SMC-based parameters + SAFETY CAPS"""
        try:
            entry_price = price['ask'] if signal == 'BUY' else price['bid']
            atr = historical_data['atr'].iloc[-1] if 'atr' in historical_data.columns else 1.0

            # Calculate SL and TP based on zones with CORRECT direction + MINIMUM SAFETY
            MIN_SL_PIPS = 20  # Minimum 20 pips stop loss
            pip_value = 0.01  # For XAUUSD

            if signal == 'BUY':
                # For BUY: SL below entry, TP above entry
                if zones:
                    stoploss = min(
                        zones.get('discount_start', entry_price - max(atr * 3, MIN_SL_PIPS * pip_value)),
                        entry_price - max(atr * 3, MIN_SL_PIPS * pip_value)
                    )
                    takeprofit = max(
                        zones.get('swing_high', entry_price + atr * 4),
                        entry_price + (atr * 4)
                    )
                else:
                    stoploss = entry_price - max(atr * 3, MIN_SL_PIPS * pip_value)
                    takeprofit = entry_price + (atr * 4)
            else:  # SELL
                # For SELL: SL above entry, TP below entry
                if zones:
                    stoploss = max(
                        zones.get('premium_end', entry_price + max(atr * 3, MIN_SL_PIPS * pip_value)),
                        entry_price + max(atr * 3, MIN_SL_PIPS * pip_value)
                    )
                    takeprofit = min(
                        zones.get('swing_low', entry_price - atr * 4),
                        entry_price - (atr * 4)
                    )
                else:
                    stoploss = entry_price + max(atr * 3, MIN_SL_PIPS * pip_value)
                    takeprofit = entry_price - (atr * 4)


            # CRITICAL: Validate stop levels
            if signal == 'BUY':
                if stoploss >= entry_price:
                    print(f"   ⚠️  BUY SL was above entry! Correcting...")
                    stoploss = entry_price - (atr * 2)
                if takeprofit <= entry_price:
                    print(f"   ⚠️  BUY TP was below entry! Correcting...")
                    takeprofit = entry_price + (atr * 3)
            else:  # SELL
                if stoploss <= entry_price:
                    print(f"   ⚠️  SELL SL was below entry! Correcting...")
                    stoploss = entry_price + (atr * 2)
                if takeprofit >= entry_price:
                    print(f"   ⚠️  SELL TP was above entry! Correcting...")
                    takeprofit = entry_price - (atr * 3)

            print(f"   🔧 Final Levels: Entry=${entry_price:.2f}, SL=${stoploss:.2f}, TP=${takeprofit:.2f}")

            # ===== FIX #3: USE SAFE LOT SIZE CALCULATION FOR ENHANCED TRADES =====
            lot_size, adjusted_stoploss = calculate_lot_size_with_safety(
                account_balance=self.mt5.get_account_info().balance if self.mt5.get_account_info() else 50000,
                risk_percent=self.risk_per_trade,
                entry_price=entry_price,
                stop_loss=stoploss,
                max_lot=self.max_lot_size,
                min_sl_pips=self.min_sl_pips
            )
            
            stoploss = adjusted_stoploss

            risk_metrics = self.risk_calculator.get_risk_metrics(entry_price, stoploss, lot_size, takeprofit)

            # Calculate pip distances manually
            pip_value = 0.01  # For XAUUSD
            sl_pips = abs(entry_price - stoploss) / pip_value
            tp_pips = abs(takeprofit - entry_price) / pip_value
            risk_amount = abs(entry_price - stoploss) * lot_size * 100
            reward_amount = abs(takeprofit - entry_price) * lot_size * 100
            rr_ratio = reward_amount / risk_amount if risk_amount > 0 else 0

            print(f"\n{'='*70}")
            print(f"✨ Enhanced Trade Execution")
            print(f"{'='*70}")
            print(f"   Direction: {signal}")
            print(f"   Entry: ${entry_price:.2f}")
            print(f"   Stop Loss: ${stoploss:.2f} ({sl_pips:.1f} pips)")
            print(f"   Take Profit: ${takeprofit:.2f} ({tp_pips:.1f} pips)")
            print(f"   Lot Size: {lot_size} lots")
            print(f"   Risk: ${risk_amount:.2f}")
            print(f"   Reward: ${reward_amount:.2f}")
            print(f"   R:R Ratio: 1:{rr_ratio:.2f}")
            print(f"{'='*70}\n")

            ticket = self.mt5.place_order(signal, lot_size, stoploss, takeprofit)

            if ticket:
                position = {
                    'ticket': ticket,
                    'signal': signal,
                    'entry_price': entry_price,
                    'stop_loss': stoploss,
                    'take_profit': takeprofit,
                    'lot_size': lot_size,
                    'entry_time': datetime.now(),
                    'risk_percent': risk_metrics['risk_percent'],
                    'atr': atr,
                    'zone': self.enhanced_analysis_data.get('current_zone', 'UNKNOWN'),
                    'market_structure': 'ENHANCED_SMC'
                }
                self.open_positions.append(position)
                print(f"✅ Enhanced trade executed")
                print(f"   Ticket: {ticket}")
                return True
            else:
                print(f"❌ Order placement failed")
                return False

        except Exception as e:
            print(f"❌ Error executing enhanced trade: {e}")
            import traceback
            traceback.print_exc()
            return False

    def log_trade_analysis(self, signal, reason, price, stats):
        """Log trade analysis"""
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S IST'),
            'signal': signal,
            'reason': reason,
            'price': price['bid'],
            'spread': price['spread'],
            'zone': stats.get('zone', 'UNKNOWN') if isinstance(stats, dict) else 'UNKNOWN',
        }
        self.trade_log.append(log_entry)

    def update_dashboard_state(self):
        """Update dashboard with current bot state"""
        if not DASHBOARD_AVAILABLE or update_bot_state is None:
            return

        try:
            account_info = self.mt5.get_account_info()
            current_price = self.mt5.get_current_price()

            def convert_value(val):
                import numpy as np
                if isinstance(val, (np.bool_, bool)) or str(type(val).__name__) == 'bool':
                    return bool(val)
                elif isinstance(val, (np.integer, np.int64, np.int32)):
                    return int(val)
                elif isinstance(val, (np.floating, np.float64, np.float32)):
                    return float(val)
                elif val is None:
                    return None
                return val

            smc = self.last_analysis.get('smc_indicators', {})
            tech = self.last_analysis.get('technical_levels', {})

            trades_list = []
            for idx, pos in enumerate(self.open_positions):
                pnl = 0.0
                if current_price:
                    entry = pos['entry_price']
                    lot_size = pos['lot_size']
                    if pos['signal'] == 'SELL':
                        pnl = (entry - current_price['ask']) * 100 * lot_size
                    else:
                        pnl = (current_price['bid'] - entry) * 100 * lot_size

                trades_list.append({
                    'id': idx + 1,
                    'type': pos['signal'],
                    'lot_size': pos['lot_size'],
                    'entry': pos['entry_price'],
                    'sl': pos['stop_loss'],
                    'tp': pos['take_profit'],
                    'time': pos['entry_time'].strftime('%Y-%m-%d %H:%M:%S IST'),
                    'status': 'OPEN',
                    'pnl': round(pnl, 2),
                    'risk_percent': pos.get('risk_percent', 0),
                    'zone': pos.get('zone', 'UNKNOWN'),
                    'market_structure': pos.get('market_structure', 'UNKNOWN'),
                })

            current_pnl = self.calculate_current_pnl()
            initial_balance = float(account_info.balance) if account_info else 100000.0
            current_balance = initial_balance + current_pnl

            # ===== FIXED SESSION DETECTION =====
            session_name, is_active = self.strategy.get_current_session()

            state = {
                'running': bool(self.running),
                'balance': current_balance,
                'initial_balance': initial_balance,
                'pnl': round(current_pnl, 2),
                'open_positions_count': len(self.open_positions),
                'current_price': current_price if current_price else {'bid': 0.0, 'ask': 0.0, 'spread': 0.0},
                'last_signal': str(self.last_signal),
                'smc_indicators': {
                    'fvg_bullish': bool(convert_value(smc.get('fvg_bullish', False))),
                    'fvg_bearish': bool(convert_value(smc.get('fvg_bearish', False))),
                    'bos': str(smc.get('bos')) if smc.get('bos') else None,
                    'session': session_name,  # ✅ NOW SHOWS CORRECT SESSION
                    'in_trading_hours': is_active,
                },
                'technical_levels': {
                    'ma20': float(convert_value(tech.get('ma20', 0))),
                    'ma50': float(convert_value(tech.get('ma50', 0))),
                    'ema200': float(convert_value(tech.get('ema200', 0))),
                    'support': float(convert_value(tech.get('support', 0))),
                    'resistance': float(convert_value(tech.get('resistance', 0))),
                    'atr': float(convert_value(tech.get('atr', 0))),
                },
                'market_structure': str(self.last_analysis.get('market_structure', 'NEUTRAL')),
                'zone': str(self.last_analysis.get('zone', 'EQUILIBRIUM')),
                'trades': trades_list,
            }

            update_bot_state(state)
            print(f"   📊 Dashboard updated - Balance: ${current_balance:,.2f} | P/L: ${current_pnl:,.2f}")

        except Exception as e:
            print(f"⚠️  Dashboard update error: {e}")

    def save_trade_log(self, filename='tradelog.json'):
        """Save trade log to file"""
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
        print("=" * 70)
        print("Press Ctrl+C to stop...\n")

        try:
            iteration = 0
            while self.running:
                iteration += 1
                print("=" * 70)
                print(f"🔄 Iteration {iteration} | Positions {len(self.open_positions)}/{self.max_positions}")
                print("=" * 70)

                if self.use_enhanced_smc:
                    self.analyze_enhanced()
                else:
                    self.analyze_and_trade()

                # Save log every 10 iterations
                if iteration % 10 == 0:
                    self.save_trade_log()

                # Wait for next interval
                for _ in range(int(interval_seconds)):
                    if not self.running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            print("\n🛑 Bot stopped by user")
        finally:
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
            send_telegram(
                f"🛑 <b>Trading Bot Stopped</b>\n\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
                f"📊 Total Iterations: {len(self.trade_log)}\n"
                f"💰 Final Balance: ${account_info.balance:,.2f if account_info else 0}\n"
                f"📈 Open Positions: {len(self.open_positions)}"
            )
        except:
            pass
        self.mt5.shutdown()
        print("✅ Trading bot shutdown complete")
        print(f"📊 Total iterations: {len(self.trade_log)}")
        print(f"📈 Open positions: {len(self.open_positions)}")


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
        from api.server import app
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
        print("✅ Copy the Phone Access URL to use on your mobile")
        print("✅ Make sure phone is on the same WiFi network")
        print("=" * 70)

        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

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
    use_enhanced = True  # Change to False to use standard SMC

    bot_instance = XAUUSDTradingBot(use_enhanced_smc=use_enhanced)
    bot_instance.running = True
    bot_instance.run(interval_seconds=60)


if __name__ == "__main__":
    main()