class StopLossCalculator:
    """Calculate position sizing and stop loss levels"""
    
    def __init__(self, account_balance, risk_per_trade=1.0):
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade  # Percentage of account to risk per trade
    
    def calculate_position_size(self, entry_price, stop_loss_price):
        """Calculate lot size based on risk management"""
        # Calculate risk in price units
        price_risk = abs(entry_price - stop_loss_price)
        
        if price_risk == 0:
            return 0.01  # Default minimum lot size
        
        # Calculate risk in account currency (USD for XAUUSD)
        risk_amount = self.account_balance * (self.risk_per_trade / 100)
        
        # Calculate lot size (simplified - in real trading use proper pip value calculation)
        # For gold, 1 lot = 100 oz, but we're trading mini lots (0.01 = 1 oz)
        lot_size = risk_amount / price_risk
        
        # Apply limits
        lot_size = max(0.01, min(lot_size, 1.0))  # Between 0.01 and 1.0 lots
        lot_size = round(lot_size, 2)  # Round to 2 decimal places
        
        return lot_size
    
    def calculate_stop_loss_take_profit(self, signal, entry_price, atr=None):
        """Calculate SL and TP levels based on SMC principles"""
        if atr is None:
            # Default to 1% risk for demo
            if signal == "BUY":
                stop_loss = entry_price * 0.99  # 1% below entry
                take_profit = entry_price * 1.02  # 2% above entry
            else:  # SELL
                stop_loss = entry_price * 1.01  # 1% above entry
                take_profit = entry_price * 0.98  # 2% below entry
        else:
            # Use ATR for dynamic levels
            if signal == "BUY":
                stop_loss = entry_price - (atr * 1.5)
                take_profit = entry_price + (atr * 2.5)
            else:  # SELL
                stop_loss = entry_price + (atr * 1.5)
                take_profit = entry_price - (atr * 2.5)
        
        return stop_loss, take_profit
    
    def get_risk_metrics(self, entry_price, stop_loss, lot_size):
        """Calculate risk metrics for the trade"""
        risk_per_trade = abs(entry_price - stop_loss) * lot_size * 100  # Simplified
        risk_percent = (risk_per_trade / self.account_balance) * 100
        
        return {
            'risk_amount': risk_per_trade,
            'risk_percent': risk_percent,
            'lot_size': lot_size,
            'reward_ratio': 2.0  # Fixed 1:2 risk-reward for demo
        }
