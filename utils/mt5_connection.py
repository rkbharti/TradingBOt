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
            login_result = mt5.login(
                login=self.config["login"],
                password=self.config["password"],
                server=self.config["server"]
            )
            
            if not login_result:
                print("❌ MT5 login failed")
                return False
            
            # Get account info after successful login
            account_info = mt5.account_info()
            if account_info is None:
                print("❌ Could not retrieve account info")
                return False
            
            self.connected = True
            print(f"✅ Connected to MT5 Demo Account: {self.config['login']}")
            print(f"💰 Balance: ${account_info.balance}")
            print(f"💼 Broker: {self.config['server']}")
            return True
            
        except Exception as e:
            print(f"❌ MT5 connection error: {e}")
            return False
    
    def get_account_info(self):
        """Get account information"""
        if not self.connected:
            print("⚠️ MT5 not connected")
            return None
        return mt5.account_info()
    
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
    
    def place_order(self, signal, lot_size, stop_loss, take_profit):
        """Place a REAL order in MT5"""
        try:
            symbol = self.symbol
            
            # Get current price
            price = self.get_current_price()
            if not price:
                print("❌ Cannot get current price")
                return False
            
            # Determine order type and price
            if signal == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
                price_value = price['ask']
            else:  # SELL
                order_type = mt5.ORDER_TYPE_SELL
                price_value = price['bid']
            
            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price_value,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": 20,
                "magic": 234000,
                "comment": "XAUUSD Bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Send order
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"❌ Order failed: {result.comment} (Code: {result.retcode})")
                return False
            
            print(f"✅ REAL ORDER PLACED:")
            print(f"   Order: {result.order}")
            print(f"   Volume: {result.volume}")
            print(f"   Price: {result.price}")
            print(f"   Deal: {result.deal}")
            
            return True
            
        except Exception as e:
            print(f"❌ Order placement error: {e}")
            return False

    
    def shutdown(self):
        """Shutdown MT5 connection"""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            print("🔌 MT5 connection closed")
