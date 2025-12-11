import time
import json
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
from utils.mt5_connection import MT5Connection
from strategy.smc_strategy import SMCStrategy
from strategy.stoploss_calc import StopLossCalculator
import threading
import sys
import os

# ============================================================================
# ✨ NEW: Import Guardeer 10-Video SMC Enhanced Modules
# ============================================================================
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
    print(f"⚠️ Warning: SMC Enhanced modules not available: {e}")
    print("   Bot will run with standard SMC strategy only")

# Add API path for dashboard integration
sys.path.append(os.path.join(os.path.dirname(__file__), 'api'))

# Import dashboard functions at module level
DASHBOARD_AVAILABLE = False
update_bot_state = None

try:
    from api.server import update_bot_state as _update_bot_state
    update_bot_state = _update_bot_state
    DASHBOARD_AVAILABLE = True
    print("✅ Dashboard integration loaded")
except ImportError as e:
    print(f"⚠️ Dashboard not available: {e}")


class XAUUSDTradingBot:
    """Enhanced trading bot with Guardeer's complete 10-video SMC strategy for XAUUSD"""
    
    def __init__(self, config_path="config.json", use_enhanced_smc=True):
        self.config_path = config_path
        self.mt5 = MT5Connection(config_path)
        self.strategy = SMCStrategy()
        self.risk_calculator = None
        self.running = False
        self.trade_log = []
        self.open_positions = []
        
        # Dashboard state tracking
        self.last_signal = "HOLD"
        self.last_analysis = {}
        
        # Get max positions from config
        self.max_positions = self.mt5.config.get("max_positions", 3)
        
        # ============================================================================
        # ✨ NEW: Initialize Guardeer SMC Enhanced Modules
        # ============================================================================
        self.use_enhanced_smc = use_enhanced_smc and SMC_ENHANCED_AVAILABLE
        self.liquidity_detector = None
        self.poi_identifier = None
        self.bias_detector = None
        self.narrative_analyzer = None
        self.enhanced_analysis_data = {}
        
        if self.use_enhanced_smc:
            print("🎯 Using Guardeer 10-Video Enhanced SMC Strategy")
        else:
            print("📊 Using Standard SMC Strategy")
    
    def sync_positions_with_mt5(self):
        """Sync internal position tracking with actual MT5 positions"""
        try:
            mt5_positions = self.mt5.get_open_positions()
            
            if mt5_positions is None:
                print("⚠️ Could not fetch MT5 positions for sync")
                return
            
            mt5_tickets = {pos['ticket'] for pos in mt5_positions}
            before_count = len(self.open_positions)
            
            synced_positions = []
            for pos in self.open_positions:
                ticket = pos.get('ticket')
                if ticket and ticket in mt5_tickets:
                    synced_positions.append(pos)
                else:
                    signal = pos.get('signal', 'UNKNOWN')
                    entry_price = pos.get('entry_price', 0)
                    print(f"   🔄 Removed closed position: {signal} @ ${entry_price:.2f} (Ticket: {ticket})")
            
            self.open_positions = synced_positions
            after_count = len(self.open_positions)
            
            if before_count != after_count:
                removed = before_count - after_count
                print(f"\n🔄 Position Sync Complete:")
                print(f"   Bot was tracking: {before_count} positions")
                print(f"   MT5 has open: {len(mt5_tickets)} positions")
                print(f"   Removed: {removed} closed position(s)")
                print(f"   Now tracking: {after_count} positions")
            
        except Exception as e:
            print(f"⚠️ Error syncing positions: {e}")
    
    def load_historical_trades(self, max_trades=50):
        """Load recent trades from trade_log.json for dashboard"""
        try:
            if os.path.exists("trade_log.json"):
                with open("trade_log.json", 'r') as f:
                    log_data = json.load(f)
                
                trades = []
                for entry in log_data[-max_trades:]:
                    if entry.get('signal') in ['BUY', 'SELL']:
                        trades.append({
                            "id": len(trades) + 1,
                            "type": entry['signal'],
                            "entry": entry['price'],
                            "time": entry['timestamp'],
                            "status": "LOGGED",
                            "session": entry.get('session', 'UNKNOWN'),
                            "zone": entry.get('zone', 'UNKNOWN'),
                            "atr": entry.get('atr', 0),
                            "spread": entry.get('spread', 0),
                            "market_structure": entry.get('market_structure', 'UNKNOWN')
                        })
                
                return trades
        except Exception as e:
            print(f"⚠️ Error loading historical trades: {e}")
        
        return []
    
    def calculate_current_pnl(self):
        """Calculate current P&L from open positions"""
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
                
                if signal == "SELL":
                    pnl = (entry - current_ask) * 100 * lot_size
                else:
                    pnl = (current_bid - entry) * 100 * lot_size
                
                total_pnl += pnl
            
            return total_pnl
            
        except Exception as e:
            print(f"⚠️ P&L calculation error: {e}")
            return 0.0
        
    def initialize(self):
        """Initialize the trading bot"""
        print("\n" + "="*70)
        print("🚀 Initializing Enhanced XAUUSD Trading Bot...")
        print("="*70)
        
        if not self.mt5.initialize_mt5():
            print("❌ Failed to initialize MT5 connection")
            return False
        
        account_info = self.mt5.get_account_info()
        if account_info:
            balance = account_info.balance
        else:
            balance = 10000
        
        self.risk_calculator = StopLossCalculator(
            account_balance=balance,
            risk_per_trade=self.mt5.config.get("risk_per_trade", 1.0)
        )
        
        print(f"💰 Account Balance: ${balance:,.2f}")
        print(f"🎯 Risk per Trade: {self.mt5.config.get('risk_per_trade', 1.0)}%")
        print(f"🌍 Time Zone: IST (UTC+5:30)")
        print(f"🎬 SMC Strategy: {'Guardeer 10-Video Enhanced' if self.use_enhanced_smc else 'Standard'}")
        print("="*70)
        
        historical_trades = self.load_historical_trades()
        print(f"📜 Loaded {len(historical_trades)} historical trades from trade_log.json")
        
        print("\n🔄 Checking position tracking on startup...")
        print(f"   Bot was tracking: {len(self.open_positions)} positions")
        
        mt5_positions = self.mt5.get_open_positions()
        if mt5_positions and len(mt5_positions) > 0:
            print(f"   Found {len(mt5_positions)} open positions in MT5")
            
            for pos in mt5_positions:
                position = {
                    'ticket': pos['ticket'],
                    'signal': pos['type'],
                    'entry_price': pos['price_open'],
                    'stop_loss': pos['sl'],
                    'take_profit': pos['tp'],
                    'lot_size': pos['volume'],
                    'entry_time': datetime.now(),
                    'risk_percent': 0,
                    'atr': 0,
                    'zone': 'UNKNOWN',
                    'market_structure': 'UNKNOWN'
                }
                self.open_positions.append(position)
                print(f"   ✅ Imported: {pos['type']} | Ticket: {pos['ticket']} | {pos['volume']} lots | P&L: ${pos['profit']:.2f}")
            
            print(f"   Imported {len(self.open_positions)} positions from MT5")
        else:
            print(f"   No open positions in MT5")
        
        self.sync_positions_with_mt5()
        
        print(f"   Now tracking: {len(self.open_positions)} positions")
        print("✅ Position tracking initialized")
        
        self.update_dashboard_state()
        
        return True
    
    def fetch_market_data(self):
        """Fetch current market data"""
        historical_data = self.mt5.get_historical_data(bars=300)
        
        if historical_data is None:
            print("❌ Could not fetch market data")
            return None
        
        # ✅ FIX: Convert numpy array to pandas DataFrame if needed
        if not isinstance(historical_data, pd.DataFrame):
            print("   Converting market data to DataFrame...")
            if hasattr(historical_data, 'columns'):
                historical_data = pd.DataFrame(historical_data)
            else:
                # If it's a structured numpy array
                historical_data = pd.DataFrame(historical_data)
        
        current_price = self.mt5.get_current_price()
        if current_price is None:
            print("❌ Could not fetch current price")
            return None
        
        return historical_data, current_price
    
    # ============================================================================
    # ✨ NEW: Guardeer 10-Video Enhanced Analysis Method
    # ============================================================================
    def analyze_enhanced(self):
        """Complete analysis using ALL 10 Guardeer video concepts"""
        try:
            self.sync_positions_with_mt5()
            
            print(f"\n📊 Running Enhanced SMC Analysis (Guardeer 10-Videos)...")
            
            market_data = self.fetch_market_data()
            if market_data is None:
                return
            
            historical_data, current_price = market_data
            
            print(f"📊 Fetched {len(historical_data)} bars of XAUUSD M5 data")
            
            # =========================================================================
            # VIDEO 5: LIQUIDITY ANALYSIS
            # =========================================================================
            if self.liquidity_detector is None:
                self.liquidity_detector = LiquidityDetector(historical_data)
                self.poi_identifier = POIIdentifier(historical_data)
                self.bias_detector = BiasDetector(historical_data)
                self.narrative_analyzer = NarrativeAnalyzer(
                    self.liquidity_detector,
                    self.poi_identifier,
                    self.bias_detector
                )
                print("   ✅ Enhanced SMC modules initialized")
            else:
                self.liquidity_detector.df = historical_data
                self.poi_identifier.df = historical_data
                self.bias_detector.df = historical_data
            
            print("\n📍 VIDEO 5 - LIQUIDITY DETECTION:")
            try:
                pdh, pdl = self.liquidity_detector.get_previous_day_high_low()
                print(f"   PDH: ${pdh:.2f} | PDL: ${pdl:.2f}" if pdh and pdl else "   PDH/PDL: Not available")
            except Exception as e:
                print(f"⚠️ Error getting PDH/PDL: {e}")
                pdh, pdl = None, None
            
            try:
                swings = self.liquidity_detector.get_swing_high_low(lookback=20)
                print(f"   Swing Highs: {len(swings.get('highs', []))} | Swing Lows: {len(swings.get('lows', []))}") 
            except Exception as e:
                print(f"⚠️ Error identifying swings: {e}")
                swings = {'highs': [], 'lows': []}
            
            try:
                liquidity_zones = self.liquidity_detector.get_liquidity_zones()
                liquidity_grabbed = self.liquidity_detector.check_liquidity_grab(current_price['bid'])
                print(f"   Liquidity Grabbed: {liquidity_grabbed.get('pdh_grabbed') or liquidity_grabbed.get('pdl_grabbed')}")
            except Exception as e:
                print(f"⚠️ Error checking liquidity: {e}")
                liquidity_grabbed = {'pdh_grabbed': False, 'pdl_grabbed': False}
            
            # =========================================================================
            # VIDEO 6: POI IDENTIFICATION
            # =========================================================================
            print("\n🎯 VIDEO 6 - POI IDENTIFICATION:")
            try:
                order_blocks = self.poi_identifier.find_order_blocks(lookback=50)
                print(f"   Bullish OBs: {len(order_blocks.get('bullish', []))} | Bearish OBs: {len(order_blocks.get('bearish', []))}")
            except Exception as e:
                print(f"⚠️ Error finding order blocks: {e}")
                order_blocks = {'bullish': [], 'bearish': []}
            
            try:
                fvgs = self.poi_identifier.find_fvg()
                print(f"   Bullish FVGs: {len(fvgs.get('bullish', []))} | Bearish FVGs: {len(fvgs.get('bearish', []))}")
            except Exception as e:
                print(f"⚠️ Error finding FVGs: {e}")
                fvgs = {'bullish': [], 'bearish': []}
            
            try:
                idm_probability = self.poi_identifier.identify_idm_sweep(timeframe_minutes=15)
                print(f"   IDM Probability: {idm_probability.get('current_probability', 0)*100:.0f}%")
            except Exception as e:
                print(f"⚠️ Error calculating IDM: {e}")
                idm_probability = {'current_probability': 0.65}
            
            try:
                closest_poi = self.poi_identifier.get_closest_poi(current_price['bid'], direction='UP')
                if closest_poi:
                    print(f"   Closest POI: ${closest_poi[0]:.2f} ({closest_poi[1]})")
            except Exception as e:
                print(f"⚠️ Error finding closest POI: {e}")
                closest_poi = None
            
            # =========================================================================
            # VIDEO 9: BIAS DETECTION
            # =========================================================================
            print("\n🧭 VIDEO 9 - BIAS DETECTION:")
            try:
                daily_bias = self.bias_detector.analyze_daily_pattern(historical_data.iloc[-1])
                print(f"   Daily Bias: {daily_bias}")
            except Exception as e:
                print(f"⚠️ Error analyzing daily pattern: {e}")
                daily_bias = "NEUTRAL"
            
            try:
                intraday_bias = self.bias_detector.get_intraday_bias(lookback=20)
                print(f"   Intraday Bias: {intraday_bias}")
            except Exception as e:
                print(f"⚠️ Error getting intraday bias: {e}")
                intraday_bias = "NEUTRAL"
            
            try:
                price_action_bias = self.bias_detector.get_price_action_bias()
                print(f"   Price Action: {price_action_bias}")
            except Exception as e:
                print(f"⚠️ Error getting price action bias: {e}")
                price_action_bias = "NEUTRAL"
            
            try:
                combined_bias = self.bias_detector.get_combined_bias(daily_bias, intraday_bias, price_action_bias)
                print(f"   Combined Bias: {combined_bias}")
            except Exception as e:
                print(f"⚠️ Error combining bias: {e}")
                combined_bias = "NEUTRAL"
            
            # =========================================================================
            # VIDEO 10: ZONE ANALYSIS
            # =========================================================================
            print("\n📦 VIDEO 10a - ZONE ANALYSIS:")
            try:
                latest_swing_high = swings['highs'][-1]['price'] if swings['highs'] else current_price['bid']
                latest_swing_low = swings['lows'][-1]['price'] if swings['lows'] else current_price['bid']
                
                zones = ZoneCalculator.calculate_zones(latest_swing_high, latest_swing_low)
                current_zone = ZoneCalculator.classify_price_zone(current_price['bid'], zones)
                zone_summary = ZoneCalculator.get_zone_summary(current_price['bid'], zones)
                
                print(f"   Current Zone: {current_zone}")
                if zone_summary:
                    print(f"   Zone Strength: {zone_summary['zone_strength']:.0f}%")
                    print(f"   Can BUY: {zone_summary['can_buy']} | Can SELL: {zone_summary['can_sell']}")
                    print(f"   Next Target: {zone_summary['next_target']['target'] if zone_summary['next_target'] else 'N/A'}")
            except Exception as e:
                print(f"⚠️ Error analyzing zones: {e}")
                zones = {}
                current_zone = "EQUILIBRIUM"
                zone_summary = None
            
            # =========================================================================
            # VIDEO 10b: NARRATIVE ANALYSIS (3Bs)
            # =========================================================================
            print("\n📖 VIDEO 10b - NARRATIVE (3Bs FRAMEWORK):")
            try:
                market_state = {
                    'liquidity_grabbed': liquidity_grabbed['pdh_grabbed'] or liquidity_grabbed['pdl_grabbed'],
                    'liquidity_type': 'PDH' if liquidity_grabbed['pdh_grabbed'] else 'PDL' if liquidity_grabbed['pdl_grabbed'] else 'NONE',
                    'fvg_tapped': len(fvgs['bullish']) > 0 or len(fvgs['bearish']) > 0,
                    'fvg_type': 'BULLISH' if len(fvgs['bullish']) > 0 else 'BEARISH' if len(fvgs['bearish']) > 0 else 'NONE',
                    'ob_hit': len(order_blocks['bullish']) > 0 or len(order_blocks['bearish']) > 0,
                    'ob_type': 'BULLISH' if len(order_blocks['bullish']) > 0 else 'BEARISH' if len(order_blocks['bearish']) > 0 else 'NONE',
                    'current_direction': daily_bias,
                    'current_bias': combined_bias,
                    'next_poi_target': closest_poi[0] if closest_poi else current_price['bid'],
                    'target_type': closest_poi[1] if closest_poi else 'NONE',
                    'target_distance': abs(closest_poi[0] - current_price['bid']) if closest_poi else 0,
                    'target_confidence': 'HIGH' if closest_poi else 'LOW',
                    'zone': current_zone,
                    'zone_strength': zone_summary['zone_strength'] if zone_summary else 0,
                    'distance_from_equilibrium': abs(zone_summary['distance_from_equilibrium']) if zone_summary else 0,
                    'timeframe': '15min',
                    'price_action': 'NEUTRAL'
                }
                
                narrative = self.narrative_analyzer.analyze_market_story(market_state)
                
                print(f"\n   B1 (Recent Action): {narrative.get('b1', {}).get('narrative', 'N/A')}")
                print(f"   B2 (Current Framework): {narrative.get('b2', {}).get('narrative', 'N/A')}")
                print(f"   B3 (Dealing Range): {narrative.get('b3', {}).get('narrative', 'N/A')}")
                print(f"\n   Trade Signal: {narrative.get('trade_signal', 'HOLD')}")
                print(f"   Confidence: {narrative.get('confidence', 0):.0f}%")
                print(f"   Bias: {narrative.get('bias', 'NEUTRAL')}")
            except Exception as e:
                print(f"⚠️ Error in narrative analysis: {e}")
                narrative = {'trade_signal': 'HOLD', 'confidence': 0, 'bias': 'NEUTRAL'}
            
            # =========================================================================
            # DECISION MAKING
            # =========================================================================
            final_signal = narrative.get('trade_signal', 'HOLD')
            
            # =========================================================================
            # ZONE FILTER (CRITICAL)
            # =========================================================================
            print("\n🔍 ZONE FILTER VALIDATION:")
            zone_allows_trade = False
            
            if final_signal == 'BUY' and current_zone == 'DISCOUNT':
                print(f"   ✅ {final_signal} signal allowed in {current_zone} zone")
                zone_allows_trade = True
            elif final_signal == 'SELL' and current_zone == 'PREMIUM':
                print(f"   ✅ {final_signal} signal allowed in {current_zone} zone")
                zone_allows_trade = True
            elif final_signal != 'HOLD':
                print(f"   ❌ {final_signal} signal BLOCKED: Current zone is {current_zone}")
                zone_allows_trade = False
                final_signal = 'HOLD'
            
            # Store for display
            self.enhanced_analysis_data = {
                'pdh': pdh,
                'pdl': pdl,
                'swings': swings,
                'order_blocks': order_blocks,
                'fvgs': fvgs,
                'daily_bias': daily_bias,
                'current_zone': current_zone,
                'narrative': narrative,
                'zones': zones
            }
            
            self.last_signal = final_signal
            self.last_analysis = {
                'enhanced_smc': self.enhanced_analysis_data,
                'zone': current_zone,
                'bias': combined_bias
            }
            
            # =========================================================================
            # EXECUTE TRADE
            # =========================================================================
            at_max_positions = len(self.open_positions) >= self.max_positions
            
            if not at_max_positions and final_signal != 'HOLD' and zone_allows_trade:
                print(f"\n✅ Executing {final_signal} trade...")
                self.execute_enhanced_trade(final_signal, current_price, historical_data, zones)
            elif at_max_positions and final_signal != 'HOLD':
                print(f"\n⚠️ Signal {final_signal} detected but max positions ({self.max_positions}) reached")
            
            # Log analysis
            self.log_trade_analysis(final_signal, "Enhanced SMC Analysis", current_price, market_state)
            self.update_dashboard_state()
            
        except Exception as e:
            print(f"\n❌ Error in enhanced analysis: {e}")
            import traceback
            traceback.print_exc()
    
    # ============================================================================
    # KEEP EXISTING: Standard analyze_and_trade() method for fallback
    # ============================================================================
    def analyze_and_trade(self):
        """Standard SMC analysis (used when enhanced mode disabled)"""
        try:
            self.sync_positions_with_mt5()
            
            at_max_positions = len(self.open_positions) >= self.max_positions
            
            if at_max_positions:
                print(f"⚠️ Max positions ({self.max_positions}) reached. Monitoring only...")
                print(f"   Currently tracking: {len(self.open_positions)} positions")
            
            print(f"\n📊 Analyzing market at {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
            
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
                    'bos': str(stats.get('bos', 'None')) if stats.get('bos') else 'None',
                    'session': stats.get('session', 'CLOSED')
                },
                'technical_levels': {
                    'ma20': stats.get('ma20', 0),
                    'ma50': stats.get('ma50', 0),
                    'ema200': stats.get('ema200', 0),
                    'support': stats.get('support', 0),
                    'resistance': stats.get('resistance', 0),
                    'atr': stats.get('atr', 0)
                },
                'market_structure': stats.get('market_structure', 'NEUTRAL'),
                'zone': stats.get('zone', 'EQUILIBRIUM')
            }
            
            self.display_analysis(current_price, signal, reason, stats)
            
            if not at_max_positions and signal != "HOLD":
                total_risk = sum([pos.get('risk_percent', 0) for pos in self.open_positions])
                can_trade, risk_msg = self.risk_calculator.check_risk_limits(
                    self.open_positions, total_risk
                )
                
                if can_trade:
                    self.execute_trade(signal, current_price, historical_data, stats)
                else:
                    print(f"⚠️ Trade blocked: {risk_msg}")
            elif at_max_positions and signal != "HOLD":
                print(f"🔔 Signal: {signal} detected, but max positions reached. Skipping trade.")
            
            self.log_trade_analysis(signal, reason, current_price, stats)
            self.update_dashboard_state()
            
        except Exception as e:
            print(f"❌ Error in analyze_and_trade: {e}")
            import traceback
            traceback.print_exc()
    
    def display_analysis(self, price, signal, reason, stats):
        """Display standard market analysis"""
        print(f"🎯 XAUUSD Price: ${price['bid']:.2f} (Spread: ${price['spread']:.2f})")
        print(f"\n📈 Market Structure: {stats.get('market_structure', 'UNKNOWN')}")
        print(f"🎯 Zone: {stats.get('zone', 'UNKNOWN')}")
        print(f"⏰ Session: {stats.get('session', 'UNKNOWN')} {'✅' if stats.get('in_trading_hours') else '⛔'}")
        
        print(f"\n📊 Technical Levels:")
        print(f"   EMA200: ${stats.get('ema200', 0):.2f}")
        print(f"   MA20: ${stats.get('ma20', 0):.2f} | MA50: ${stats.get('ma50', 0):.2f}")
        print(f"   Support: ${stats.get('support', 0):.2f} | Resistance: ${stats.get('resistance', 0):.2f}")
        print(f"   ATR: ${stats.get('atr', 0):.2f}")
        
        print(f"\n💡 SMC Indicators:")
        print(f"   FVG Bullish: {'✅' if stats.get('fvg_bullish') else '❌'}")
        print(f"   FVG Bearish: {'✅' if stats.get('fvg_bearish') else '❌'}")
        print(f"   Last BOS: {stats.get('bos', 'NONE')}")
        
        print(f"\n🔔 Signal: {signal}")
        print(f"   Reason: {reason}")
        print("-" * 70)
    
    def execute_trade(self, signal, price, historical_data, stats):
        """Execute trade with standard parameters"""
        entry_price = price['ask'] if signal == "BUY" else price['bid']
        atr = stats.get('atr', entry_price * 0.01)
        market_structure = stats.get('market_structure', 'NEUTRAL')
        
        stop_loss, take_profit = self.risk_calculator.calculate_stop_loss_take_profit(
            signal, entry_price, atr, stats.get('zone', 'EQUILIBRIUM'), market_structure
        )
        
        lot_size = self.risk_calculator.calculate_position_size(
            entry_price, stop_loss
        )
        
        risk_metrics = self.risk_calculator.get_risk_metrics(
            entry_price, stop_loss, lot_size, take_profit
        )
        
        print(f"\n💼 Trade Execution Details:")
        print(f"   Direction: {signal}")
        print(f"   Entry: ${entry_price:.2f}")
        print(f"   Stop Loss: ${stop_loss:.2f} ({risk_metrics['stop_loss_pips']:.2f} pips)")
        print(f"   Take Profit: ${take_profit:.2f} ({risk_metrics['take_profit_pips']:.2f} pips)")
        print(f"   Lot Size: {lot_size}")
        print(f"   Risk: ${risk_metrics['risk_amount']:.2f} ({risk_metrics['risk_percent']:.2f}%)")
        print(f"   R:R Ratio: 1:{risk_metrics['reward_ratio']:.1f}")
        
        ticket = self.mt5.place_order(signal, lot_size, stop_loss, take_profit)

        if ticket:
            position = {
                'ticket': ticket,
                'signal': signal,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'lot_size': lot_size,
                'entry_time': datetime.now(),
                'risk_percent': risk_metrics['risk_percent'],
                'atr': atr,
                'zone': stats.get('zone', 'EQUILIBRIUM'),
                'market_structure': market_structure
            }
            self.open_positions.append(position)
            
            print(f"\n✅ Position opened successfully")
            print(f"   Ticket: {ticket}")
            print(f"   Open Positions: {len(self.open_positions)}")
            return True
        else:
            print(f"\n❌ Order placement failed")
            return False
    
    # ============================================================================
    # ✨ NEW: Enhanced Trade Execution with Zone Validation
    # ============================================================================
    def execute_enhanced_trade(self, signal, price, historical_data, zones):
        """Execute trade with enhanced SMC-based parameters"""
        try:
            print(f"\n🎯 Enhanced Trade Execution:")
            
            entry_price = price['ask'] if signal == "BUY" else price['bid']
            atr = historical_data['atr'].iloc[-1] if 'atr' in historical_data.columns else 1.0
            
            # Use zone-based stops
            if zones:
                if signal == "BUY":
                    stop_loss = zones['discount_start']
                    take_profit = zones['swing_high']
                else:
                    stop_loss = zones['premium_end']
                    take_profit = zones['swing_low']
            else:
                stop_loss = entry_price - atr * 2 if signal == "BUY" else entry_price + atr * 2
                take_profit = entry_price + atr * 4 if signal == "BUY" else entry_price - atr * 4
            
            lot_size = self.risk_calculator.calculate_position_size(entry_price, stop_loss)
            risk_metrics = self.risk_calculator.get_risk_metrics(entry_price, stop_loss, lot_size, take_profit)
            
            print(f"   Entry: ${entry_price:.2f}")
            print(f"   Stop Loss: ${stop_loss:.2f} ({risk_metrics['stop_loss_pips']:.2f} pips)")
            print(f"   Take Profit: ${take_profit:.2f} ({risk_metrics['take_profit_pips']:.2f} pips)")
            print(f"   Lot Size: {lot_size}")
            print(f"   Risk: ${risk_metrics['risk_amount']:.2f}")
            
            ticket = self.mt5.place_order(signal, lot_size, stop_loss, take_profit)

            if ticket:
                position = {
                    'ticket': ticket,
                    'signal': signal,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'lot_size': lot_size,
                    'entry_time': datetime.now(),
                    'risk_percent': risk_metrics['risk_percent'],
                    'atr': atr,
                    'zone': self.enhanced_analysis_data.get('current_zone', 'UNKNOWN'),
                    'market_structure': 'ENHANCED_SMC'
                }
                self.open_positions.append(position)
                print(f"\n✅ Enhanced trade executed | Ticket: {ticket}")
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
                if isinstance(val, (np.bool_, bool)) or str(type(val).__name__) == 'bool_':
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
                    
                    if pos['signal'] == "SELL":
                        pnl = (entry - current_price['ask']) * 100 * lot_size
                    else:
                        pnl = (current_price['bid'] - entry) * 100 * lot_size
                
                trades_list.append({
                    "id": idx + 1,
                    "type": pos['signal'],
                    "lot_size": pos['lot_size'],
                    "entry": pos['entry_price'],
                    "sl": pos['stop_loss'],
                    "tp": pos['take_profit'],
                    "time": pos['entry_time'].strftime("%Y-%m-%d %H:%M:%S IST"),
                    "status": "OPEN",
                    "pnl": round(pnl, 2),
                    "risk_percent": pos.get('risk_percent', 0),
                    "zone": pos.get('zone', 'UNKNOWN'),
                    "market_structure": pos.get('market_structure', 'UNKNOWN')
                })
            
            current_pnl = self.calculate_current_pnl()
            initial_balance = float(account_info.balance) if account_info else 100000.0
            current_balance = initial_balance + current_pnl
            
            state = {
                "running": bool(self.running),
                "balance": current_balance,
                "initial_balance": initial_balance,
                "pnl": round(current_pnl, 2),
                "open_positions_count": len(self.open_positions),
                "current_price": current_price if current_price else {"bid": 0.0, "ask": 0.0, "spread": 0.0},
                "last_signal": str(self.last_signal),
                "smc_indicators": {
                    "fvg_bullish": bool(convert_value(smc.get('fvg_bullish', False))),
                    "fvg_bearish": bool(convert_value(smc.get('fvg_bearish', False))),
                    "bos": str(smc.get('bos', 'None')) if smc.get('bos') else 'None',
                    "session": str(smc.get('session', 'CLOSED'))
                },
                "technical_levels": {
                    "ma20": float(convert_value(tech.get('ma20', 0))),
                    "ma50": float(convert_value(tech.get('ma50', 0))),
                    "ema200": float(convert_value(tech.get('ema200', 0))),
                    "support": float(convert_value(tech.get('support', 0))),
                    "resistance": float(convert_value(tech.get('resistance', 0))),
                    "atr": float(convert_value(tech.get('atr', 0)))
                },
                "market_structure": str(self.last_analysis.get('market_structure', 'NEUTRAL')),
                "zone": str(self.last_analysis.get('zone', 'EQUILIBRIUM')),
                "trades": trades_list
            }
            
            update_bot_state(state)
            print(f"📊 Dashboard updated - Balance: ${current_balance:,.2f} | P&L: ${current_pnl:,.2f}")
            
        except Exception as e:
            print(f"⚠️ Dashboard update error: {e}")
    
    def save_trade_log(self, filename="trade_log.json"):
        """Save trade log to file"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.trade_log, f, indent=2, default=str)
            print(f"💾 Trade log saved to {filename}")
        except Exception as e:
            print(f"❌ Error saving trade log: {e}")
    
    def run(self, interval_seconds=60):
        """Run the trading bot main loop"""
        if not self.initialize():
            return
        
        self.running = True
        strategy_name = "Guardeer 10-Video Enhanced SMC" if self.use_enhanced_smc else "Standard SMC"
        
        print(f"\n{'='*70}")
        print(f"🤖 XAUUSD Trading Bot Started")
        print(f"📊 Strategy: {strategy_name}")
        print(f"⏱️  Interval: Every {interval_seconds} seconds")
        print(f"🛡️  Features: FVG, BOS, Liquidity Sweeps, ATR Stops, Session Filters")
        print(f"{'='*70}")
        print("\nPress Ctrl+C to stop...\n")
        
        try:
            iteration = 0
            while self.running:
                iteration += 1
                print(f"\n{'='*70}")
                print(f"Iteration #{iteration} | Positions: {len(self.open_positions)}/{self.max_positions}")
                print(f"{'='*70}")
                
                # Use enhanced analysis if available
                if self.use_enhanced_smc:
                    self.analyze_enhanced()
                else:
                    self.analyze_and_trade()
                
                if iteration % 10 == 0:
                    self.save_trade_log()
                
                for _ in range(interval_seconds):
                    if not self.running:
                        break
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            print("\n\n🛑 Bot stopped by user")
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Shutdown the trading bot"""
        self.running = False
        self.save_trade_log()
        self.mt5.shutdown()
        print("\n🔚 Trading bot shutdown complete")
        print(f"Total iterations: {len(self.trade_log)}")
        print(f"Open positions: {len(self.open_positions)}")


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
        
        entry_price = current_price['ask'] if trade_type.upper() == "BUY" else current_price['bid']
        
        pip_value = 0.01
        if trade_type.upper() == "BUY":
            stop_loss = entry_price - (50 * pip_value)
            take_profit = entry_price + (100 * pip_value)
        else:
            stop_loss = entry_price + (50 * pip_value)
            take_profit = entry_price - (100 * pip_value)
        
        result = bot_instance.mt5.place_order(
            trade_type.upper(),
            lot_size,
            stop_loss,
            take_profit
        )
        
        print(f"📝 Manual {trade_type.upper()} trade executed from dashboard")
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
        
        print("\n" + "="*70)
        print("📱 DASHBOARD SERVER STARTING...")
        print("="*70)
        print(f"🖥️  Laptop Access:  http://localhost:8000/dashboard")
        print(f"📱 Phone Access:    http://{local_ip}:8000/dashboard")
        print(f"📊 API Docs:        http://localhost:8000/docs")
        print("="*70)
        print("✨ Copy the Phone Access URL to use on your mobile")
        print("🌐 Make sure phone is on the same WiFi network")
        print("="*70 + "\n")
        
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
    except Exception as e:
        print(f"❌ API Server error: {e}")

def main():
    """Main function to run the trading bot with dashboard"""
    global bot_instance
    
    print("\n" + "="*70)
    print(" " * 12 + "🎯 XAUUSD SMC TRADING BOT v3.0")
    print(" " * 8 + "✨ Guardeer 10-Video Enhanced SMC")
    print("="*70 + "\n")
    
    # ✨ NEW: Use Enhanced SMC by default (disable with use_enhanced_smc=False)
    use_enhanced = True  # Change to False to use standard SMC
    
    print("⏳ Starting dashboard server...")
    
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    
    time.sleep(3)
    
    bot_instance = XAUUSDTradingBot(use_enhanced_smc=use_enhanced)
    bot_instance.run(interval_seconds=60)

if __name__ == "__main__":
    main()
