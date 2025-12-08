import MetaTrader5 as mt5
import json
from datetime import datetime
import pytz

class MT5Connection:
    """Enhanced MT5 connection with real order placement"""
    
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.symbol = self.config.get("symbol", "XAUUSD")
        
        # Handle both "M5" and "TIMEFRAME_M5" formats
        timeframe_str = self.config.get("timeframe", "TIMEFRAME_M5")
        if not timeframe_str.startswith("TIMEFRAME_"):
            timeframe_str = f"TIMEFRAME_{timeframe_str}"
        self.timeframe = getattr(mt5, timeframe_str)
        
        self.timezone = pytz.timezone("Asia/Kolkata")

    
    def load_config(self):
        """Load configuration from JSON file"""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"⚠️ Config file not found: {self.config_path}")
            return self.get_default_config()
        except json.JSONDecodeError:
            print(f"⚠️ Invalid JSON in config file")
            return self.get_default_config()
    
    def get_default_config(self):
        """Return default configuration"""
        return {
            "symbol": "XAUUSD",
            "timeframe": "TIMEFRAME_M5",
            "risk_per_trade": 1.0,
            "max_positions": 3
        }
    
    def initialize_mt5(self):
        """Initialize MT5 connection"""
        if not mt5.initialize():
            print(f"❌ MT5 initialization failed: {mt5.last_error()}")
            return False
        
        # Get account info
        account_info = mt5.account_info()
        if account_info is None:
            print("❌ Failed to get account info")
            return False
        
        print(f"✅ Connected to MT5 Demo Account: {account_info.login}")
        print(f"💰 Balance: ${account_info.balance}")
        print(f"💼 Broker: {account_info.server}")
        
        return True
    
    def get_account_info(self):
        """Get current account information"""
        return mt5.account_info()
    
    def get_historical_data(self, bars=300):
        """Fetch historical price data"""
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 0, bars)
            
            if rates is None or len(rates) == 0:
                print(f"❌ No data received for {self.symbol}")
                return None
            
            print(f"📊 Fetched {len(rates)} bars of {self.symbol} M5 data")
            return rates
            
        except Exception as e:
            print(f"❌ Error fetching data: {e}")
            return None
    
    def get_current_price(self):
        """Get current bid/ask prices"""
        try:
            tick = mt5.symbol_info_tick(self.symbol)
            if tick is None:
                return None
            
            return {
                'bid': tick.bid,
                'ask': tick.ask,
                'spread': tick.ask - tick.bid
            }
        except Exception as e:
            print(f"❌ Error getting price: {e}")
            return None
    
    def place_order(self, signal, lot_size, stop_loss, take_profit):
        """Place a REAL order in MT5 (Demo or Live)"""
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
            
            # Get symbol info for volume constraints
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                print(f"❌ Symbol {symbol} not found")
                return False
            
            # Check if symbol is available for trading
            if not symbol_info.visible:
                print(f"❌ Symbol {symbol} is not visible")
                if not mt5.symbol_select(symbol, True):
                    print(f"❌ Failed to select {symbol}")
                    return False
            
            # Round lot size to valid volume step
            lot_size = round(lot_size / symbol_info.volume_step) * symbol_info.volume_step
            lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))
            
            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(lot_size),
                "type": order_type,
                "price": price_value,
                "sl": float(stop_loss),
                "tp": float(take_profit),
                "deviation": 20,
                "magic": 234000,
                "comment": "XAUUSD Bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            print(f"\n📋 Sending {signal} order to MT5...")
            print(f"   Symbol: {symbol}")
            print(f"   Volume: {lot_size}")
            print(f"   Price: {price_value:.2f}")
            print(f"   SL: {stop_loss:.2f}")
            print(f"   TP: {take_profit:.2f}")
            
            # Send order
            result = mt5.order_send(request)
            
            if result is None:
                print(f"❌ Order send returned None")
                return False
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"❌ Order failed!")
                print(f"   Error code: {result.retcode}")
                print(f"   Comment: {result.comment}")
                return False
            
            print(f"\n✅ REAL ORDER PLACED IN MT5!")
            print(f"   Order ID: {result.order}")
            print(f"   Deal ID: {result.deal}")
            print(f"   Volume: {result.volume}")
            print(f"   Price: {result.price:.2f}")
            print(f"   Type: {signal}")
            
            return True
            
        except Exception as e:
            print(f"❌ Order placement error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def place_demo_order(self, signal, lot_size, stop_loss, take_profit):
        """DEPRECATED: Use place_order() instead for real orders"""
        print(f"\n📋 DEMO ORDER: {signal} {lot_size} lots | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        print(f"💡 Note: This is paper trading - no real order placed")
        print(f"⚠️  To place real orders, bot now uses place_order() method")
        return True
    
    def get_open_positions(self):
        """Get all open positions for the symbol"""
        try:
            positions = mt5.positions_get(symbol=self.symbol)
            if positions is None:
                return []
            
            position_list = []
            for pos in positions:
                position_list.append({
                    'ticket': pos.ticket,
                    'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                    'volume': pos.volume,
                    'price_open': pos.price_open,
                    'sl': pos.sl,
                    'tp': pos.tp,
                    'price_current': pos.price_current,
                    'profit': pos.profit,
                    'comment': pos.comment
                })
            
            return position_list
            
        except Exception as e:
            print(f"❌ Error getting positions: {e}")
            return []
    
    def close_position(self, ticket):
        """Close a specific position by ticket"""
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                print(f"❌ Position {ticket} not found")
                return False
            
            position = position[0]
            symbol = position.symbol
            
            # Determine close order type (opposite of open)
            if position.type == mt5.ORDER_TYPE_BUY:
                order_type = mt5.ORDER_TYPE_SELL
                price = mt5.symbol_info_tick(symbol).bid
            else:
                order_type = mt5.ORDER_TYPE_BUY
                price = mt5.symbol_info_tick(symbol).ask
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": order_type,
                "position": ticket,
                "price": price,
                "deviation": 20,
                "magic": 234000,
                "comment": "Close by bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"❌ Failed to close position {ticket}: {result.comment}")
                return False
            
            print(f"✅ Position {ticket} closed successfully")
            return True
            
        except Exception as e:
            print(f"❌ Error closing position: {e}")
            return False
    
    def shutdown(self):
        """Shutdown MT5 connection"""
        mt5.shutdown()
        print("🔌 MT5 connection closed")
