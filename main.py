import time
import json
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
from utils.mt5_connection import MT5Connection
from strategy.smc_strategy import SMCStrategy
from strategy.stoploss_calc import StopLossCalculator

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
        
        # Place demo order
        order_placed = self.mt5.place_demo_order(signal, lot_size, stop_loss, take_profit)
        
        if order_placed:
            # Track position
            position = {
                'signal': signal,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'lot_size': lot_size,
                'entry_time': datetime.now(),
                'risk_percent': risk_metrics['risk_percent'],
                'atr': atr
            }
            self.open_positions.append(position)
            print(f"\n✅ Order placed successfully!")
        else:
            print(f"\n❌ Order placement failed")
    
    def log_trade_analysis(self, signal, reason, price, stats):
        """Log enhanced trade analysis for review"""
        log_entry = {
            'timestamp': datetime.now(),
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

def main():
    """Main function to run the trading bot"""
    print("\n" + "="*60)
    print(" " * 10 + "XAUUSD SMC TRADING BOT v2.0")
    print(" " * 15 + "Enhanced Algorithm")
    print("="*60 + "\n")
    
    bot = XAUUSDTradingBot()
    bot.run(interval_seconds=60)  # Check every minute

if __name__ == "__main__":
    main()
