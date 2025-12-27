import MetaTrader5 as mt5
import json
from datetime import datetime
import pytz
import time


class MT5Connection:
    """
    Single authoritative MT5 interface.
    All trading, lifecycle, and sync logic must go through this class.
    """

    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.config = self._load_config()

        self.symbol = self.config.get("symbol", "XAUUSD")

        timeframe_str = self.config.get("timeframe", "TIMEFRAME_M5")
        if not timeframe_str.startswith("TIMEFRAME_"):
            timeframe_str = f"TIMEFRAME_{timeframe_str}"
        self.timeframe = getattr(mt5, timeframe_str)

        self.timezone = pytz.timezone("Asia/Kolkata")

    # -------------------------------------------------
    # CONFIG
    # -------------------------------------------------

    def _load_config(self):
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except Exception:
            return {
                "symbol": "XAUUSD",
                "timeframe": "TIMEFRAME_M5",
                "risk_per_trade": 1.0,
                "max_positions": 3,
            }

    # -------------------------------------------------
    # CONNECTION
    # -------------------------------------------------

    def initialize_mt5(self) -> bool:
        if not mt5.initialize():
            print(f"❌ MT5 init failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        if info is None:
            print("❌ Cannot read account info")
            return False

        print(f"✅ Connected to MT5 Account: {info.login}")
        print(f"💰 Balance: ${info.balance}")
        print(f"💼 Broker: {info.server}")
        return True

    def shutdown(self):
        mt5.shutdown()
        print("🔌 MT5 connection closed")

    # -------------------------------------------------
    # MARKET DATA
    # -------------------------------------------------

    def get_account_info(self):
        return mt5.account_info()

    def get_current_price(self):
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return None
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": tick.ask - tick.bid,
        }

    def get_historical_data(self, bars=300):
        return mt5.copy_rates_from_pos(
            self.symbol, self.timeframe, 0, bars
        )

    # -------------------------------------------------
    # 🔥 CRITICAL LIFECYCLE WRAPPERS (FIXED)
    # -------------------------------------------------

    def positions_get(self, symbol: str = None, ticket: int = None):
        """
        REQUIRED by sync_closed_positions().
        This is the missing piece that caused your runtime error.
        """
        try:
            return mt5.positions_get(symbol=symbol, ticket=ticket)
        except Exception as e:
            print(f"❌ positions_get error: {e}")
            return None
        
    def get_open_positions(self):
        """
        High-level helper used by main.py and dashboard.
        Returns normalized open positions for the configured symbol.
        """
        try:
            positions = mt5.positions_get(symbol=self.symbol)
            if not positions:
                return []

            result = []
            for pos in positions:
                result.append({
                    "ticket": pos.ticket,
                    "type": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": pos.volume,
                    "price_open": pos.price_open,
                    "price_current": pos.price_current,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                    "comment": pos.comment,
                })

            return result

        except Exception as e:
            print(f"❌ get_open_positions error: {e}")
            return []


    def history_deals_get(self, ticket: int = None, from_date=None, to_date=None):
        """
        Used to evaluate closed trade PnL.
        """
        try:
            if ticket is not None:
                return mt5.history_deals_get(ticket=ticket)

            if from_date and to_date:
                return mt5.history_deals_get(from_date, to_date)

            return None
        except Exception as e:
            print(f"❌ history_deals_get error: {e}")
            return None

    # -------------------------------------------------
    # ORDER EXECUTION
    # -------------------------------------------------

    def place_order(self, signal, lot_size, stop_loss, take_profit):
        symbol = self.symbol
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            print("❌ Symbol not found")
            return None

        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print("❌ No tick data")
            return None

        order_type = (
            mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL
        )
        price = tick.ask if signal == "BUY" else tick.bid

        # Normalize volume
        lot_size = round(lot_size / symbol_info.volume_step) * symbol_info.volume_step
        lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot_size),
            "type": order_type,
            "price": price,
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "deviation": 20,
            "magic": 234000,
            "comment": "XAUUSD Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            return result.order

        print(f"❌ Order failed: {result.comment if result else 'None'}")
        return None

    # -------------------------------------------------
    # POSITION MANAGEMENT
    # -------------------------------------------------

    def close_position(self, ticket: int) -> bool:
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False

        pos = pos[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return False

        close_type = (
            mt5.ORDER_TYPE_SELL
            if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "Close by bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        return result and result.retcode == mt5.TRADE_RETCODE_DONE
