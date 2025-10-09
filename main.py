import time
import json
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
from utils.mt5_connection import MT5Connection
from strategy.smc_strategy import SMCStrategy
from strategy.stoploss_calc import StopLossCalculator

class XAUUSDTradingBot:
    """Main trading bot class for XAUUSD demo trading"""
    
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.mt5 = MT5Connection(config_path)
        self.strategy = SMCStrategy()
        self.risk_calculator = None
        self.running = False
        self.trade_log = []
        
    def initialize(self):
        """Initialize the trading bot"""
        print("🚀 Initializing XAUUSD Trading Bot...")
        print("=" * 50)
        
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
        print("=" * 50)
        
        return True
    
    def fetch_market_data(self):
        """Fetch current market data"""
        # Get historical data for analysis
        historical_data = self.mt5.get_historical_data(
            bars=200  # Get enough data for indicators
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
        """Main analysis and trading logic"""
        print(f"\n📊 Analyzing market at {datetime.now()}")
        
        # Fetch market data
        market_data = self.fetch_market_data()
        if market_data is None:
            return
        
        historical_data, current_price = market_data
        
        # Generate trading signal
        signal, reason = self.strategy.generate_signal(historical_data)
        
        # Get strategy statistics
        stats = self.strategy.get_strategy_stats(historical_data)
        
        # Display analysis
        self.display_analysis(current_price, signal, reason, stats)
        
        # Execute trade if signal is not HOLD
        if signal != "HOLD":
            self.execute_trade(signal, current_price, historical_data)
        
        # Log this analysis
        self.log_trade_analysis(signal, reason, current_price, stats)
    
    def display_analysis(self, price, signal, reason, stats):
        """Display current market analysis"""
        print(f"🎯 XAUUSD Current Price: ${price['bid']:.2f} (Spread: {price['spread']:.2f})")
        print(f"📈 Technical Levels:")
        print(f"   MA5: ${stats.get('ma5', 0):.2f} | MA20: ${stats.get('ma20', 0):.2f}")
        print(f"   Support: ${stats.get('support', 0):.2f} | Resistance: ${stats.get('resistance', 0):.2f}")
        print(f"💡 Signal: {signal} - {reason}")
        print("-" * 50)
    
    def execute_trade(self, signal, price, historical_data):
        """Execute a demo trade"""
        entry_price = price['ask'] if signal == "BUY" else price['bid']
        
        # Calculate stop loss and take profit
        stop_loss, take_profit = self.risk_calculator.calculate_stop_loss_take_profit(
            signal, entry_price
        )
        
        # Calculate position size
        lot_size = self.risk_calculator.calculate_position_size(
            entry_price, stop_loss
        )
        
        # Get risk metrics
        risk_metrics = self.risk_calculator.get_risk_metrics(
            entry_price, stop_loss, lot_size
        )
        
        print(f"💼 Trade Execution:")
        print(f"   Direction: {signal}")
        print(f"   Entry: ${entry_price:.2f}")
        print(f"   Stop Loss: ${stop_loss:.2f}")
        print(f"   Take Profit: ${take_profit:.2f}")
        print(f"   Lot Size: {lot_size}")
        print(f"   Risk: ${risk_metrics['risk_amount']:.2f} ({risk_metrics['risk_percent']:.1f}%)")
        
        # Place demo order
        self.mt5.place_demo_order(signal, lot_size, stop_loss, take_profit)
    
    def log_trade_analysis(self, signal, reason, price, stats):
        """Log trade analysis for later review"""
        log_entry = {
            'timestamp': datetime.now(),
            'signal': signal,
            'reason': reason,
            'price': price['bid'],
            'ma5': stats.get('ma5', 0),
            'ma20': stats.get('ma20', 0),
            'spread': price['spread']
        }
        self.trade_log.append(log_entry)
    
    def run(self, interval_seconds=60):
        """Run the trading bot main loop"""
        if not self.initialize():
            return
        
        self.running = True
        print(f"\n🤖 Trading Bot Started - Monitoring XAUUSD every {interval_seconds} seconds")
        print("Press Ctrl+C to stop...")
        
        try:
            while self.running:
                self.analyze_and_trade()
                
                # Wait for next iteration
                for _ in range(interval_seconds):
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
        self.mt5.shutdown()
        print("🔚 Trading bot shutdown complete")

def main():
    """Main function to run the trading bot"""
    bot = XAUUSDTradingBot()
    bot.run(interval_seconds=60)  # Check every minute

if __name__ == "__main__":
    main()
