import numpy as np

class StopLossCalculator:
    """Enhanced position sizing and stop loss with ATR-based dynamic levels"""
    
    def __init__(self, account_balance, risk_per_trade=1.0, max_risk_per_trade=2.0):
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade  # Default risk percentage
        self.max_risk_per_trade = max_risk_per_trade  # Maximum allowed risk
        self.min_lot_size = 0.01
        self.max_lot_size = 1.0
        self.gold_pip_value = 0.01  # XAUUSD pip value
    
    def update_balance(self, new_balance):
        """Update account balance for dynamic position sizing"""
        self.account_balance = new_balance
    
    def calculate_position_size(self, entry_price, stop_loss_price, custom_risk=None):
        """Calculate lot size based on ATR-aware risk management"""
        # Use custom risk or default
        risk_percent = custom_risk if custom_risk else self.risk_per_trade
        risk_percent = min(risk_percent, self.max_risk_per_trade)  # Cap at max
        
        # Calculate risk in price units
        price_risk = abs(entry_price - stop_loss_price)
        
        if price_risk == 0 or price_risk < 0.1:  # Minimum $0.10 risk per oz
            return self.min_lot_size
        
        # Calculate risk amount in account currency
        risk_amount = self.account_balance * (risk_percent / 100)
        
        # For XAUUSD: 1 lot = 100 oz, 0.01 lot = 1 oz
        # Risk per lot = price_risk * 100 (for 1 lot)
        risk_per_standard_lot = price_risk * 100
        
        # Calculate lot size
        lot_size = risk_amount / risk_per_standard_lot
        
        # Apply limits and round
        lot_size = max(self.min_lot_size, min(lot_size, self.max_lot_size))
        lot_size = round(lot_size, 2)  # Round to 2 decimal places
        
        return lot_size
    
    def calculate_stop_loss_take_profit(self, signal, entry_price, atr=None, 
                                       zone="EQUILIBRIUM", market_structure="NEUTRAL"):
        """Calculate SL and TP levels using ATR and SMC principles"""
        
        if atr is None or atr == 0:
            # Fallback to percentage-based if ATR not available
            atr = entry_price * 0.01  # 1% as fallback
        
        # ATR multipliers based on market conditions
        if market_structure == "BULLISH" and signal == "BUY":
            # Tighter stops in trending markets
            sl_multiplier = 1.5
            tp_multiplier = 3.0  # 1:2 reward ratio
        elif market_structure == "BEARISH" and signal == "SELL":
            sl_multiplier = 1.5
            tp_multiplier = 3.0
        else:
            # Wider stops in choppy/neutral markets
            sl_multiplier = 2.0
            tp_multiplier = 3.5
        
        # Adjust for premium/discount zones
        if zone == "DEEP_DISCOUNT" and signal == "BUY":
            tp_multiplier = 4.0  # More room to run
        elif zone == "PREMIUM" and signal == "SELL":
            tp_multiplier = 4.0
        
        # Calculate levels
        if signal == "BUY":
            stop_loss = entry_price - (atr * sl_multiplier)
            take_profit = entry_price + (atr * tp_multiplier)
        else:  # SELL
            stop_loss = entry_price + (atr * sl_multiplier)
            take_profit = entry_price - (atr * tp_multiplier)
        
        # Ensure minimum distance
        min_distance = entry_price * 0.005  # 0.5% minimum
        if abs(stop_loss - entry_price) < min_distance:
            stop_loss = entry_price - min_distance if signal == "BUY" else entry_price + min_distance
        
        return round(stop_loss, 2), round(take_profit, 2)
    
    def calculate_trailing_stop(self, signal, entry_price, current_price, atr, 
                               initial_stop_loss):
        """Calculate trailing stop loss based on ATR"""
        trailing_distance = atr * 1.5
        
        if signal == "BUY":
            # For long positions
            profit = current_price - entry_price
            if profit > atr * 2:  # Start trailing after 2x ATR profit
                new_stop = current_price - trailing_distance
                # Only move stop up, never down
                return max(new_stop, initial_stop_loss)
        else:  # SELL
            # For short positions
            profit = entry_price - current_price
            if profit > atr * 2:
                new_stop = current_price + trailing_distance
                # Only move stop down, never up
                return min(new_stop, initial_stop_loss)
        
        return initial_stop_loss
    
    def get_risk_metrics(self, entry_price, stop_loss, lot_size, take_profit=None):
        """Calculate comprehensive risk metrics for the trade"""
        # Risk calculation
        price_risk = abs(entry_price - stop_loss)
        risk_per_trade = price_risk * lot_size * 100  # For XAUUSD
        risk_percent = (risk_per_trade / self.account_balance) * 100
        
        # Reward calculation
        if take_profit:
            price_reward = abs(take_profit - entry_price)
            reward_per_trade = price_reward * lot_size * 100
            reward_ratio = price_reward / price_risk if price_risk > 0 else 0
        else:
            reward_per_trade = 0
            reward_ratio = 0
        
        # Position value
        position_value = entry_price * lot_size * 100
        
        return {
            'risk_amount': round(risk_per_trade, 2),
            'risk_percent': round(risk_percent, 2),
            'reward_amount': round(reward_per_trade, 2),
            'reward_ratio': round(reward_ratio, 2),
            'lot_size': lot_size,
            'position_value': round(position_value, 2),
            'stop_loss_pips': round(price_risk, 2),
            'take_profit_pips': round(price_reward, 2) if take_profit else 0
        }
    
    def apply_kelly_criterion(self, win_rate, avg_win, avg_loss):
        """Calculate optimal position size using Kelly Criterion"""
        if avg_loss == 0:
            return self.risk_per_trade
        
        win_loss_ratio = avg_win / avg_loss
        kelly_percent = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
        
        # Use half-Kelly for safety
        kelly_percent = kelly_percent * 0.5
        
        # Cap at max risk
        kelly_percent = max(0.5, min(kelly_percent * 100, self.max_risk_per_trade))
        
        return kelly_percent
    
    def check_risk_limits(self, open_positions, total_risk_percent):
        """Check if adding new position exceeds risk limits"""
        max_total_risk = 5.0  # Maximum 5% total account risk
        max_positions = 3  # Maximum 3 concurrent positions
        
        if len(open_positions) >= max_positions:
            return False, f"Maximum {max_positions} positions already open"
        
        if total_risk_percent + self.risk_per_trade > max_total_risk:
            return False, f"Total risk would exceed {max_total_risk}%"
        
        return True, "Risk limits OK"
