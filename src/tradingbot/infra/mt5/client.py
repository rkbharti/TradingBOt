import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

import logging
logger = logging.getLogger("tradingbot.mt5")
import MetaTrader5 as mt5
import json

# Fallback constants for symbol filling modes since they are missing from the MetaTrader5 python library
SYMBOL_FILLING_FOK = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
SYMBOL_FILLING_IOC = getattr(mt5, "SYMBOL_FILLING_IOC", 2)

from datetime import datetime
import pytz
from config.settings import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH


class MT5Connection:
    """
    Single authoritative MT5 interface.
    All trading, lifecycle, and sync logic must go through this class.
    """

    def __init__(self, config_path: str = "config.json"):
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
        login_value = int(MT5_LOGIN) if MT5_LOGIN else None
        init_kwargs = {}
        if MT5_PATH:
            init_kwargs["path"] = MT5_PATH
        if login_value is not None:
            init_kwargs["login"] = login_value
        if MT5_PASSWORD:
            init_kwargs["password"] = MT5_PASSWORD
        if MT5_SERVER:
            init_kwargs["server"] = MT5_SERVER

        if not mt5.initialize(**init_kwargs):
            logger.error(f"❌ MT5 init failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        if info is None:
            logger.error("❌ Cannot read account info")
            return False

        logger.info(f"✅ Connected to MT5 Account: {info.login}")
        logger.info(f"💰 Balance: ${info.balance}")
        logger.info(f"💼 Broker: {info.server}")
        return True

    def shutdown(self):
        mt5.shutdown()
        logger.info("🔌 MT5 connection closed")

    # -------------------------------------------------
    # MARKET DATA
    # -------------------------------------------------

    def get_account_info(self):
        return mt5.account_info()

    def get_symbol_info(self, symbol: str = None):
        """
        Wrapper used by OrderExecutor for spread checks.

        Returns MetaTrader5.symbol_info(symbol) result or None on error.
        """
        try:
            symbol = symbol or self.symbol
            info = mt5.symbol_info(symbol)
            if info is None:
                logger.error(f"❌ symbol_info() returned None for {symbol}")
            return info
        except Exception as e:
            logger.error(f"❌ get_symbol_info error for {symbol}: {e}")
            return None

    def get_current_price(self):
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return None
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": tick.ask - tick.bid,
        }

    def get_historical_data(self, bars: int = 300):
        return mt5.copy_rates_from_pos(
            self.symbol, self.timeframe, 1, bars
        )

    # -------------------------------------------------
    # 🔥 CRITICAL LIFECYCLE WRAPPERS
    # -------------------------------------------------

    def positions_get(self, symbol: str = None, ticket: int = None):
        """
        REQUIRED by sync_closed_positions() and OrderExecutor.sync_open_positions().
        """
        try:
            return mt5.positions_get(symbol=symbol, ticket=ticket)
        except Exception as e:
            logger.error(f"❌ positions_get error: {e}")
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
                result.append(
                    {
                        "ticket": pos.ticket,
                        "symbol": pos.symbol,
                        "type": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                        "volume": pos.volume,
                        "open_price": pos.price_open,
                        "sl": pos.sl,
                        "tp": pos.tp,
                    }
                )

            return result

        except Exception as e:
            logger.error(f"❌ get_open_positions error: {e}")
            return []

    def history_deals_get(self, ticket=None, from_date=None, to_date=None):
        """
        Used to evaluate closed trade PnL.
        Supports passing a datetime object as the first argument (fallback).
        """
        try:
            if isinstance(ticket, datetime):
                from_date = ticket
                ticket = None

            if ticket is not None:
                return mt5.history_deals_get(ticket=ticket)

            if from_date:
                if to_date is None:
                    to_date = datetime.now()
                return mt5.history_deals_get(from_date, to_date)

            return None
        except Exception as e:
            logger.error(f"❌ history_deals_get error: {e}")
            return None

    def history_deals_get_by_position(self, position_ticket: int, days_lookback: int = 5):
        """
        Fetch all deals associated with a position ticket within a lookback window.
        """
        try:
            from datetime import datetime, timedelta
            date_from = datetime.now() - timedelta(days=days_lookback)
            date_to = datetime.now() + timedelta(days=1)
            deals = mt5.history_deals_get(date_from, date_to, position=position_ticket)
            return deals
        except Exception as e:
            logger.error(f"❌ history_deals_get_by_position error: {e}")
            return None

    # -------------------------------------------------
    # ORDER EXECUTION
    # -------------------------------------------------

    def place_order(self, signal, lot_size, stop_loss, take_profit):
        symbol = self.symbol
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            logger.error("❌ Symbol not found")
            return None

        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error("❌ No tick data")
            return None

        order_type = (
            mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL
        )
        price = tick.ask if signal == "BUY" else tick.bid

        # Normalize volume
        lot_size = round(lot_size / symbol_info.volume_step) * symbol_info.volume_step
        lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))

        # Resolve dynamic filling mode based on broker capabilities
        filling_mode = symbol_info.filling_mode
        if filling_mode & SYMBOL_FILLING_FOK:
            type_filling = mt5.ORDER_FILLING_FOK
        elif filling_mode & SYMBOL_FILLING_IOC:
            type_filling = mt5.ORDER_FILLING_IOC
        else:
            type_filling = mt5.ORDER_FILLING_RETURN

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
            "type_filling": type_filling,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            return result.order

        logger.error(f"❌ Order failed: {result.comment if result else 'None'}")
        return None

    def send_order(self, order_request: dict):
        """
        Compatibility wrapper for OrderExecutor.
        Sends normalized live MT5 market order.
        """

        try:
            symbol = order_request["symbol"]

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise Exception(f"No tick data for {symbol}")

            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                raise Exception(f"No symbol info for {symbol}")

            # =========================================================
            # USE LIVE MARKET PRICE
            # =========================================================

            if order_request["type"] == mt5.ORDER_TYPE_BUY:
                live_price = tick.ask
            else:
                live_price = tick.bid

            digits = symbol_info.digits

            order_request["price"] = round(live_price, digits)
            order_request["sl"] = round(order_request["sl"], digits)
            order_request["tp"] = round(order_request["tp"], digits)

            # Resolve dynamic filling mode based on broker capabilities
            filling_mode = symbol_info.filling_mode
            if filling_mode & SYMBOL_FILLING_FOK:
                order_request["type_filling"] = mt5.ORDER_FILLING_FOK
            elif filling_mode & SYMBOL_FILLING_IOC:
                order_request["type_filling"] = mt5.ORDER_FILLING_IOC
            else:
                order_request["type_filling"] = mt5.ORDER_FILLING_RETURN

            # Final validation of stops relative to live price right before sending
            sl_val = order_request.get("sl", 0.0)
            tp_val = order_request.get("tp", 0.0)
            if order_request["type"] == mt5.ORDER_TYPE_BUY:
                # BUY: SL must be below BID, TP must be above ASK
                if sl_val > 0.0 and sl_val >= tick.bid:
                    raise Exception(f"Invalid BUY Stops: SL ({sl_val}) is above or equal to bid price ({tick.bid})")
                if tp_val > 0.0 and tp_val <= tick.ask:
                    raise Exception(f"Invalid BUY Stops: TP ({tp_val}) is below or equal to ask price ({tick.ask})")
            elif order_request["type"] == mt5.ORDER_TYPE_SELL:
                # SELL: SL must be above ASK, TP must be below BID
                if sl_val > 0.0 and sl_val <= tick.ask:
                    raise Exception(f"Invalid SELL Stops: SL ({sl_val}) is below or equal to ask price ({tick.ask})")
                if tp_val > 0.0 and tp_val >= tick.bid:
                    raise Exception(f"Invalid SELL Stops: TP ({tp_val}) is above or equal to bid price ({tick.bid})")

            # =========================================================
            # DEBUG LOG
            # =========================================================

            logger.info("📤 FINAL MT5 REQUEST")
            logger.info(order_request)

            # =========================================================
            # SEND ORDER
            # =========================================================

            result = mt5.order_send(order_request)

            if result is None:
                raise Exception("mt5.order_send() returned None")

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                raise Exception(
                    f"MT5 order failed | Retcode: {result.retcode} | "
                    f"Comment: {result.comment}"
                )

            logger.info(f"✅ Order executed successfully | Ticket: {result.order}")

            return result.order

        except Exception as e:
            logger.error(f"❌ send_order() error: {e}")
            raise

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
        return bool(result and result.retcode == mt5.TRADE_RETCODE_DONE)

    def modify_position(self, ticket, new_sl=None, new_tp=None):
        """Modify existing position's SL/TP"""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False

        position = position[0]

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl if new_sl is not None else position.sl,
            "tp": new_tp if new_tp is not None else position.tp,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"❌ Modify failed: {result.comment}")
            return False

        return True

    def close_position_partial(self, ticket, volume_to_close, comment: str = "Partial Close"):
        """Close a specific volume of an open position"""
        if not mt5.initialize():
            return {"success": False, "message": "No connection"}

        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return {"success": False, "message": "Position not found"}
        pos = pos[0]

        # Action is opposite of position type
        action_type = (
            mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        )
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return {"success": False, "message": "No tick data"}

        price = tick.bid if action_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": float(volume_to_close),
            "type": action_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": comment,
        }

        res = mt5.order_send(request)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"❌ Partial close failed: {res.comment}")
            return {"success": False}

        logger.info(f"✅ Closed {volume_to_close} lots")
        return {"success": True, "remaining_volume": pos.volume - volume_to_close}
