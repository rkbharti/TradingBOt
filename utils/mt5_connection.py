import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
import time
import json
import os

class MT5Connection:
    def __init__(self, config_path="config.json"):
        """Initialize MT5 connection with config file"""
        self.config = self.load_config(config_path)
        self.connected = False
        
    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            print(f"❌ Config file {config_path} not found!")
            raise
    
    def initialize_mt5(self):
        """Initialize connection to MT5 terminal"""
        try:
            if not mt5.initialize():
                print("❌ MT5 initialization failed")
                return False
            
            # Login to demo account
            account_info = mt5.login(
                login=self.config["login"],
                password=self.config["password"],
                server=self.config["server"]
            )
            
            if account_info is None:
                print("❌ MT5 login failed")
                return False
            
            self.connected = True
            print(f"✅ Connected to MT5 Demo Account: {self.config['login']}")
            print(f"💰 Balance: ${account_info.balance}")
            print(f"💼 Broker: {self.config['server']}")
            return True
            
        except Exception as e:
            print(f"❌ MT5 connection error: {e}")
            return False
    
    def get_current_price(self, symbol=None):
        """Get current bid/ask price for symbol"""
        if not self.connected:
            print("⚠️ MT5 not connected")
            return None
            
        symbol = symbol or self.config["symbol"]
        tick = mt5.symbol_info_tick(symbol)
        
        if tick is None:
            print(f"❌ Could not get tick data for {symbol}")
            return None
            
        return {
            'bid': tick.bid,
            'ask': tick.ask,
            'spread': tick.ask - tick.bid,
            'time': tick.time
        }
    
    def get_historical_data(self, symbol=None, timeframe=None, bars=1000):
        """Fetch historical price data"""
        if not self.connected:
            print("⚠️ MT5 not connected")
            return None
            
        symbol = symbol or self.config["symbol"]
        timeframe = timeframe or self.config["timeframe"]
        
        # Convert timeframe string to MT5 constant
        tf_mapping = {
            'M1': mt5.TIMEFRAME_M1,
            'M5': mt5.TIMEFRAME_M5,
            'M15': mt5.TIMEFRAME_M15,
            'H1': mt5.TIMEFRAME_H1,
            'H4': mt5.TIMEFRAME_H4,
            'D1': mt5.TIMEFRAME_D1
        }
        
        tf = tf_mapping.get(timeframe, mt5.TIMEFRAME_M5)
        
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        
        if rates is None:
            print(f"❌ Could not fetch historical data for {symbol}")
            return None
        
        # Convert to DataFrame
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        
        print(f"📊 Fetched {len(df)} bars of {symbol} {timeframe} data")
        return df
    
    def place_demo_order(self, order_type, lot_size, stop_loss, take_profit):
        """Place a demo trade order (paper trading only)"""
        if not self.connected:
            print("⚠️ MT5 not connected")
            return False
            
        symbol = self.config["symbol"]
        price = self.get_current_price(symbol)
        
        if price is None:
            return False
        
        # Calculate order parameters
        if order_type.upper() == "BUY":
            order_type_mt5 = mt5.ORDER_TYPE_BUY
            price_exec = price['ask']
        elif order_type.upper() == "SELL":
            order_type_mt5 = mt5.ORDER_TYPE_SELL
            price_exec = price['bid']
        else:
            print("❌ Invalid order type")
            return False
        
        # Prepare order request
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type_mt5,
            "price": price_exec,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": 10,
            "magic": 12345,
            "comment": "SMC Bot Demo",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        # In demo mode, we'll just log the order instead of actually placing it
        print(f"📋 DEMO ORDER: {order_type} {lot_size} lots | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        print(f"💡 Note: This is paper trading - no real order placed")
        
        return True
    
    def shutdown(self):
        """Shutdown MT5 connection"""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            print("🔌 MT5 connection closed")
