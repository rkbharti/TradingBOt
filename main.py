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
    """Enhanced trading bot with advanced SMC strategy for XAUUSD"""
    
    def __init__(self, config_path="config.json"):
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
    
    def sync_positions_with_mt5(self):
        """
        ✅ DAY 2 FIX: Sync internal position tracking with actual MT5 positions.
        Removes any positions from self.open_positions that are no longer in MT5.
        
        This prevents desync issues where bot thinks positions are open when they're closed.
        Called at the start of every trading cycle for accuracy.
        """
        try:
            # Get actual positions from MT5
            mt5_positions = self.mt5.get_open_positions()
            
            if mt5_positions is None:
                print("⚠️ Could not fetch MT5 positions for sync")
                return
            
            # Extract ticket numbers from MT5 positions
            mt5_tickets = {pos['ticket'] for pos in mt5_positions}
            
            # Count before sync
            before_count = len(self.open_positions)
            
            # Keep only positions that still exist in MT5
            # Filter by ticket number
            synced_positions = []
            for pos in self.open_positions:
                ticket = pos.get('ticket')
                if ticket and ticket in mt5_tickets:
                    synced_positions.append(pos)
                else:
                    # Position closed - log it
                    signal = pos.get('signal', 'UNKNOWN')
                    entry_price = pos.get('entry_price', 0)
                    print(f"   🔄 Removed closed position: {signal} @ ${entry_price:.2f} (Ticket: {ticket})")
            
            # Update tracking list
            self.open_positions = synced_positions
            
            # Count after sync
            after_count = len(self.open_positions)
            
            # Log sync results if positions were removed
            if before_count != after_count:
                removed = before_count - after_count
                print(f"\n🔄 Position Sync Complete:")
                print(f"   Bot was tracking: {before_count} positions")
                print(f"   MT5 has open: {len(mt5_tickets)} positions")
                print(f"   Removed: {removed} closed position(s)")
                print(f"   Now tracking: {after_count} positions")
            
        except Exception as e:
            print(f"⚠️ Error syncing positions: {e}")
            import traceback
            traceback.print_exc()
    
    def load_historical_trades(self, max_trades=50):
        """Load recent trades from trade_log.json for dashboard"""
        try:
            if os.path.exists("trade_log.json"):
                with open("trade_log.json", 'r') as f:
                    log_data = json.load(f)
                
                # Get only BUY/SELL signals (not HOLD)
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
            # Get current price
            current_price = self.mt5.get_current_price()
            if not current_price:
                return 0.0
            
            current_bid = current_price['bid']
            current_ask = current_price['ask']
            
            # Calculate P&L for each open position
            for pos in self.open_positions:
                signal = pos['signal']
                entry = pos['entry_price']
                lot_size = pos['lot_size']
                
                if signal == "SELL":
                    # SELL: Profit when price goes DOWN
                    # P&L = (Entry - Current) * 100 * Lot Size
                    pnl = (entry - current_ask) * 100 * lot_size
                else:  # BUY
                    # BUY: Profit when price goes UP
                    # P&L = (Current - Entry) * 100 * Lot Size
                    pnl = (current_bid - entry) * 100 * lot_size
                
                total_pnl += pnl
            
            return total_pnl
            
        except Exception as e:
            print(f"⚠️ P&L calculation error: {e}")
            return 0.0
        
    def initialize(self):
        """Initialize the trading bot"""
        print("🚀 Initializing Enhanced XAUUSD Trading Bot...")
        print("=" * 60)
        
        # Connect to MT5
        if not self.mt5.initialize_mt5():
            print("❌ Failed to initialize MT5 connection")
            return False
        
        # Initialize risk calculator with account balance
        account_info = self.mt5.get_account_info()
        if account_info:
            balance = account_info.balance
        else:
            balance = 10000  # Default demo balance
        
        self.risk_calculator = StopLossCalculator(
            account_balance=balance,
            risk_per_trade=self.mt5.config.get("risk_per_trade", 1.0)
        )
        
        print(f"💰 Account Balance: ${balance:,.2f}")
        print(f"🎯 Risk per Trade: {self.mt5.config.get('risk_per_trade', 1.0)}%")
        print(f"🌍 Time Zone: IST (UTC+5:30)")
        print("=" * 60)
        
        # Load historical trades for dashboard
        historical_trades = self.load_historical_trades()
        print(f"📜 Loaded {len(historical_trades)} historical trades from trade_log.json")
        
        # ✅ DAY 2 FIX: Check for stale position tracking on startup
        print("\n🔄 Checking position tracking on startup...")
        print(f"   Bot was tracking: {len(self.open_positions)} positions")
        
        # Sync with MT5 to clean up any stale tracking
        self.sync_positions_with_mt5()
        
        print(f"   After sync: {len(self.open_positions)} positions")
        print("✅ Position tracking initialized")
        
        # Initial dashboard update
        self.update_dashboard_state()
        
        return True
    
    def fetch_market_data(self):
        """Fetch current market data"""
        # Get historical data for analysis (increased for better indicators)
        historical_data = self.mt5.get_historical_data(
            bars=300  # More data for enhanced indicators
        )
        
        if historical_data is None:
            print("❌ Could not fetch market data")
            return None
        
        # Get current price
        current_price = self.mt5.get_current_price()
        if current_price is None:
            print("❌ Could not fetch current price")
            return None
        
        return historical_data, current_price
    
    def analyze_and_trade(self):
        """Main analysis and trading logic with enhanced SMC"""
        try:
            # ✅ DAY 2 FIX: Sync positions with MT5 FIRST (before any checks)
            self.sync_positions_with_mt5()
            
            # Check if we've reached max positions
            if len(self.open_positions) >= self.max_positions:
                print(f"⚠️ Max positions ({self.max_positions}) reached. Waiting...")
                print(f"   Currently tracking: {len(self.open_positions)} positions")
                return
            
            print(f"\n📊 Analyzing market at {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
            
            # Fetch market data
            market_data = self.fetch_market_data()
            if market_data is None:
                return
            
            historical_data, current_price = market_data
            
            # Generate trading signal
            signal, reason = self.strategy.generate_signal(historical_data)
            
            # Get enhanced strategy statistics
            stats = self.strategy.get_strategy_stats(historical_data)
            
            # Store for dashboard
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
            
            # Display enhanced analysis
            self.display_enhanced_analysis(current_price, signal, reason, stats)
            
            # Check risk limits before trading
            total_risk = sum([pos.get('risk_percent', 0) for pos in self.open_positions])
            can_trade, risk_msg = self.risk_calculator.check_risk_limits(
                self.open_positions, total_risk
            )
            
            # Execute trade if signal is not HOLD and risk limits allow
            if signal != "HOLD":
                if can_trade:
                    self.execute_enhanced_trade(signal, current_price, historical_data, stats)
                else:
                    print(f"⚠️  Trade blocked: {risk_msg}")
            
            # Log this analysis
            self.log_trade_analysis(signal, reason, current_price, stats)
            
            # Update dashboard after analysis
            self.update_dashboard_state()
            
        except Exception as e:
            print(f"❌ Error in analyze_and_trade: {e}")
            import traceback
            traceback.print_exc()
    
    def display_enhanced_analysis(self, price, signal, reason, stats):
        """Display enhanced market analysis with SMC indicators"""
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
        print("-" * 60)
    
    def execute_enhanced_trade(self, signal, price, historical_data, stats):
        """Execute trade with enhanced SMC-based parameters"""
        entry_price = price['ask'] if signal == "BUY" else price['bid']
        atr = stats.get('atr', entry_price * 0.01)
        zone = stats.get('zone', 'EQUILIBRIUM')
        market_structure = stats.get('market_structure', 'NEUTRAL')
        
        # Calculate stop loss and take profit with ATR
        stop_loss, take_profit = self.risk_calculator.calculate_stop_loss_take_profit(
            signal, entry_price, atr, zone, market_structure
        )
        
        # Calculate position size
        lot_size = self.risk_calculator.calculate_position_size(
            entry_price, stop_loss
        )
        
        # Get comprehensive risk metrics
        risk_metrics = self.risk_calculator.get_risk_metrics(
            entry_price, stop_loss, lot_size, take_profit
        )
        
        print(f"\n💼 Trade Execution Details:")
        print(f"   Direction: {signal}")
        print(f"   Entry: ${entry_price:.2f}")
        print(f"   Stop Loss: ${stop_loss:.2f} ({risk_metrics['stop_loss_pips']:.2f} pips)")
        print(f"   Take Profit: ${take_profit:.2f} ({risk_metrics['take_profit_pips']:.2f} pips)")
        print(f"   Lot Size: {lot_size}")
        print(f"   Position Value: ${risk_metrics['position_value']:,.2f}")
        print(f"   Risk: ${risk_metrics['risk_amount']:.2f} ({risk_metrics['risk_percent']:.2f}%)")
        print(f"   Potential Reward: ${risk_metrics['reward_amount']:.2f}")
        print(f"   R:R Ratio: 1:{risk_metrics['reward_ratio']:.1f}")
        print(f"   ATR: ${atr:.2f}")
        print(f"   Zone: {zone} | Structure: {market_structure}")
        
        # ✅ DAY 1 FIX: Place REAL order and capture ticket number
        ticket = self.mt5.place_order(signal, lot_size, stop_loss, take_profit)

        if ticket:
            # ✅ Track position with ticket number
            position = {
                'ticket': ticket,  # ← Store ticket for tracking
                'signal': signal,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'lot_size': lot_size,
                'entry_time': datetime.now(),
                'risk_percent': risk_metrics['risk_percent'],
                'atr': atr,
                'zone': zone,
                'market_structure': market_structure
            }
            self.open_positions.append(position)
            
            print(f"\n✅ Position added to tracking")
            print(f"   Ticket: {ticket}")
            print(f"   Open Positions: {len(self.open_positions)}")
        else:
            print(f"\n❌ Order placement failed - position not tracked")
    
    def log_trade_analysis(self, signal, reason, price, stats):
        """Log enhanced trade analysis for review"""
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S IST'),
            'signal': signal,
            'reason': reason,
            'price': price['bid'],
            'spread': price['spread'],
            'ma20': stats.get('ma20', 0),
            'ema200': stats.get('ema200', 0),
            'atr': stats.get('atr', 0),
            'market_structure': stats.get('market_structure', 'UNKNOWN'),
            'zone': stats.get('zone', 'UNKNOWN'),
            'session': stats.get('session', 'UNKNOWN'),
            'in_trading_hours': stats.get('in_trading_hours', False),
            'fvg_bullish': stats.get('fvg_bullish', False),
            'fvg_bearish': stats.get('fvg_bearish', False)
        }
        self.trade_log.append(log_entry)
    
    def update_dashboard_state(self):
        """Update dashboard with current bot state"""
        if not DASHBOARD_AVAILABLE or update_bot_state is None:
            return
        
        try:
            # Get current data
            account_info = self.mt5.get_account_info()
            current_price = self.mt5.get_current_price()
            
            # Helper function to convert numpy types to Python types
            def convert_value(val):
                """Convert numpy/pandas types to native Python types"""
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
            
            # Get SMC indicators with type conversion
            smc = self.last_analysis.get('smc_indicators', {})
            tech = self.last_analysis.get('technical_levels', {})
            
            # Format open positions for dashboard WITH P&L
            trades_list = []
            
            # Part 1: Add OPEN positions (actual trades)
            for idx, pos in enumerate(self.open_positions):
                # Calculate individual P&L
                pnl = 0.0
                if current_price:
                    entry = pos['entry_price']
                    lot_size = pos['lot_size']
                    
                    if pos['signal'] == "SELL":
                        pnl = (entry - current_price['ask']) * 100 * lot_size
                    else:  # BUY
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
                    "atr": pos.get('atr', 0),
                    "zone": pos.get('zone', 'UNKNOWN'),
                    "market_structure": pos.get('market_structure', 'UNKNOWN')
                })
            
            # Part 2: Add recent SIGNALS (not executed, just logged)
            # Only add signals that are NOT already in open_positions
            open_times = [pos['entry_time'].strftime("%Y-%m-%d %H:%M:%S IST") for pos in self.open_positions]
            
            signal_count = 0
            for entry in reversed(self.trade_log):  # Most recent first
                if signal_count >= 5:  # Limit to 5 signals
                    break
                    
                if entry.get('signal') in ['BUY', 'SELL']:
                    timestamp = entry['timestamp']
                    
                    # Skip if this signal became an open position
                    if timestamp in open_times:
                        continue
                    
                    trades_list.append({
                        "id": len(trades_list) + 1,
                        "type": entry['signal'],
                        "lot_size": 0.0,  # SIGNAL trades don't have lot_size
                        "entry": entry['price'],
                        "time": timestamp,
                        "status": "SIGNAL",
                        "pnl": 0.0,
                        "session": entry.get('session', 'UNKNOWN'),
                        "zone": entry.get('zone', 'UNKNOWN'),
                        "spread": entry.get('spread', 0)
                    })
                    signal_count += 1
            
            # Calculate current P&L
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
            print(f"📊 Dashboard updated - Balance: ${current_balance:,.2f} | P&L: ${current_pnl:,.2f} | Trades: {len(trades_list)}")
            
        except Exception as e:
            print(f"⚠️ Dashboard update error: {e}")
    
    def save_trade_log(self, filename="trade_log.json"):
        """Save trade log to file"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.trade_log, f, indent=2, default=str)
            print(f"\n💾 Trade log saved to {filename}")
        except Exception as e:
            print(f"\n❌ Error saving trade log: {e}")
    
    def run(self, interval_seconds=60):
        """Run the enhanced trading bot main loop"""
        if not self.initialize():
            return
        
        self.running = True
        print(f"\n🤖 Enhanced SMC Trading Bot Started")
        print(f"⏱️  Monitoring XAUUSD every {interval_seconds} seconds")
        print(f"🛡️  Features: FVG, BOS, Liquidity Sweeps, ATR Stops, Session Filters")
        print("\nPress Ctrl+C to stop...\n")
        
        try:
            iteration = 0
            while self.running:
                iteration += 1
                print(f"\n{'='*60}")
                print(f"Iteration #{iteration}")
                print(f"{'='*60}")
                
                # ✅ DAY 2 FIX: Show tracking status
                print(f"📊 Position Tracking: {len(self.open_positions)}/{self.max_positions}")
                print()
                
                self.analyze_and_trade()
                
                # Save log every 10 iterations
                if iteration % 10 == 0:
                    self.save_trade_log()
                
                # Wait for next iteration
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


# Global bot instance for API access
bot_instance = None


def execute_manual_trade(trade_type: str, lot_size: float):
    """Execute manual trade from dashboard"""
    global bot_instance
    
    if bot_instance is None:
        return False
    
    try:
        # Get current price
        current_price = bot_instance.mt5.get_current_price()
        if not current_price:
            return False
        
        # Simple execution for manual trades
        entry_price = current_price['ask'] if trade_type.upper() == "BUY" else current_price['bid']
        
        # Use basic stop loss (50 pips) for manual trades
        pip_value = 0.01
        if trade_type.upper() == "BUY":
            stop_loss = entry_price - (50 * pip_value)
            take_profit = entry_price + (100 * pip_value)
        else:
            stop_loss = entry_price + (50 * pip_value)
            take_profit = entry_price - (100 * pip_value)
        
        # Place order
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
        
        print("\n" + "="*60)
        print("📱 DASHBOARD SERVER STARTING...")
        print("="*60)
        print(f"🖥️  Laptop Access:  http://localhost:8000/dashboard")
        print(f"📱 Phone Access:    http://{local_ip}:8000/dashboard")
        print(f"📊 API Docs:        http://localhost:8000/docs")
        print("="*60)
        print("✨ Copy the Phone Access URL to use on your mobile")
        print("🌐 Make sure phone is on the same WiFi network")
        print("="*60 + "\n")
        
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
    except Exception as e:
        print(f"❌ API Server error: {e}")


def main():
    """Main function to run the trading bot with dashboard"""
    global bot_instance
    
    print("\n" + "="*60)
    print(" " * 10 + "XAUUSD SMC TRADING BOT v2.0")
    print(" " * 15 + "Enhanced Algorithm")
    print("="*60 + "\n")
    
    print("⏳ Starting dashboard server...")
    
    # Start API server in background thread
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    
    # Give API server time to start
    time.sleep(3)
    
    # Create and run bot
    bot_instance = XAUUSDTradingBot()
    bot_instance.run(interval_seconds=60)  # Check every minute


if __name__ == "__main__":
    main()
