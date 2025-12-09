import MetaTrader5 as mt5


class StopLossCalculator:
    """Enhanced stop loss and position sizing calculator with broker validation"""
    
    def __init__(self, account_balance, risk_per_trade=1.0, max_risk_per_day=5.0):
        """
        Initialize calculator with account parameters
        
        Args:
            account_balance (float): Current account balance
            risk_per_trade (float): Risk percentage per trade (default 1%)
            max_risk_per_day (float): Maximum risk per day (default 5%)
        """
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        self.max_risk_per_day = max_risk_per_day
        self.symbol = "XAUUSD"
        
        # ✅ DAY 3 FIX: Get broker specifications
        self.min_stop_distance = self._get_min_stop_distance()
        self.symbol_info = mt5.symbol_info(self.symbol)
        
        print(f"🔧 StopLoss Calculator initialized:")
        print(f"   Broker min stop distance: ${self.min_stop_distance:.2f}")
        if self.symbol_info:
            print(f"   Min lot: {self.symbol_info.volume_min}")
            print(f"   Max lot: {self.symbol_info.volume_max}")
            print(f"   Lot step: {self.symbol_info.volume_step}")
    
    def _get_min_stop_distance(self):
        """
        ✅ DAY 3 FIX: Get minimum stop distance from broker
        
        Returns:
            float: Minimum stop distance in price points
        """
        try:
            symbol_info = mt5.symbol_info(self.symbol)
            if symbol_info:
                # Broker's minimum stop level in points
                stop_level_points = symbol_info.trade_stops_level
                
                # Convert to price distance
                point = symbol_info.point
                min_distance = stop_level_points * point
                
                # Add safety buffer (10 points = $0.10)
                buffer = 10 * point
                
                total_min = min_distance + buffer
                
                # Ensure at least $0.50 minimum
                return max(total_min, 0.50)
            else:
                # Default fallback: 0.50 USD
                return 0.50
        except Exception as e:
            print(f"⚠️ Could not get broker stop distance: {e}")
            return 0.50  # Safe default
    
    def calculate_stop_loss_take_profit(self, signal, entry_price, atr, zone="EQUILIBRIUM", market_structure="NEUTRAL"):
        """
        ✅ DAY 3 FIX: Calculate SL/TP with broker validation and spread consideration
        
        Args:
            signal (str): "BUY" or "SELL"
            entry_price (float): Entry price
            atr (float): Current ATR value
            zone (str): Premium/Discount zone
            market_structure (str): Current market structure
            
        Returns:
            tuple: (stop_loss, take_profit) prices
        """
        # Get current spread
        try:
            tick = mt5.symbol_info_tick(self.symbol)
            spread = tick.ask - tick.bid if tick else 0.17  # Default spread
        except:
            spread = 0.17  # Fallback spread
        
        # Base ATR multipliers (conservative)
        base_sl_multiplier = 2.0
        base_tp_multiplier = 4.0
        
        # ✅ Adjust for zone and structure
        if zone == "PREMIUM":
            if signal == "SELL":
                # Strong SELL setup in premium - tighter stops
                base_sl_multiplier = 1.8
                base_tp_multiplier = 4.5
            else:
                # Weaker BUY in premium - wider stops
                base_sl_multiplier = 2.2
                base_tp_multiplier = 3.5
        
        elif zone == "DISCOUNT" or zone == "DEEPDISCOUNT":
            if signal == "BUY":
                # Strong BUY setup in discount - tighter stops
                base_sl_multiplier = 1.8
                base_tp_multiplier = 4.5
            else:
                # Weaker SELL in discount - wider stops
                base_sl_multiplier = 2.2
                base_tp_multiplier = 3.5
        
        # Calculate initial SL/TP distances
        sl_distance = atr * base_sl_multiplier
        tp_distance = atr * base_tp_multiplier
        
        # ✅ DAY 3 FIX: Ensure distances meet broker minimums
        # Add spread to minimum distance for safety
        min_distance_with_spread = self.min_stop_distance + spread
        
        if sl_distance < min_distance_with_spread:
            print(f"🔧 SL distance too small (${sl_distance:.2f}), adjusting to ${min_distance_with_spread:.2f}")
            sl_distance = min_distance_with_spread
        
        if tp_distance < min_distance_with_spread:
            print(f"🔧 TP distance too small (${tp_distance:.2f}), adjusting to ${min_distance_with_spread:.2f}")
            tp_distance = min_distance_with_spread
        
        # Calculate actual SL/TP prices
        if signal == "BUY":
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SELL
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
        
        # ✅ DAY 3 FIX: Final validation
        actual_sl_distance = abs(entry_price - stop_loss)
        actual_tp_distance = abs(take_profit - entry_price)
        
        if actual_sl_distance < self.min_stop_distance:
            print(f"🔧 Final SL adjustment needed (${actual_sl_distance:.2f} < ${self.min_stop_distance:.2f})")
            if signal == "BUY":
                stop_loss = entry_price - self.min_stop_distance - spread
            else:
                stop_loss = entry_price + self.min_stop_distance + spread
        
        if actual_tp_distance < self.min_stop_distance:
            print(f"🔧 Final TP adjustment needed (${actual_tp_distance:.2f} < ${self.min_stop_distance:.2f})")
            if signal == "BUY":
                take_profit = entry_price + self.min_stop_distance + spread
            else:
                take_profit = entry_price - self.min_stop_distance - spread
        
        return stop_loss, take_profit
    
    def calculate_position_size(self, entry_price, stop_loss):
        """
        ✅ DAY 3 FIX: Calculate position size with margin validation
        
        Args:
            entry_price (float): Entry price
            stop_loss (float): Stop loss price
            
        Returns:
            float: Lot size (validated against margin)
        """
        # Calculate risk amount in USD
        risk_amount = self.account_balance * (self.risk_per_trade / 100)
        
        # Calculate stop loss distance in price
        sl_distance = abs(entry_price - stop_loss)
        
        # XAUUSD: 1 lot = 100 oz, 1 pip = $1 for 0.01 lot
        # P&L per lot = price_change * 100
        # So: risk_amount = sl_distance * 100 * lot_size
        lot_size = risk_amount / (sl_distance * 100)
        
        # ✅ DAY 3 FIX: Get symbol specifications
        if self.symbol_info:
            # Round to valid lot step
            lot_step = self.symbol_info.volume_step
            lot_size = round(lot_size / lot_step) * lot_step
            
            # Enforce broker limits
            min_lot = self.symbol_info.volume_min
            max_lot = self.symbol_info.volume_max
            
            lot_size = max(min_lot, min(lot_size, max_lot))
            
            # ✅ DAY 3 FIX: Check margin requirement
            required_margin = self._calculate_required_margin(entry_price, lot_size)
            free_margin = self._get_free_margin()
            
            if required_margin > free_margin * 0.8:  # Use only 80% of free margin
                # Reduce lot size to fit available margin
                safe_lot_size = (free_margin * 0.8 / required_margin) * lot_size
                safe_lot_size = round(safe_lot_size / lot_step) * lot_step
                safe_lot_size = max(min_lot, safe_lot_size)
                
                print(f"⚠️ Margin insufficient for {lot_size:.2f} lots")
                print(f"   Required margin: ${required_margin:.2f}")
                print(f"   Free margin: ${free_margin:.2f}")
                print(f"   Reducing to {safe_lot_size:.2f} lots (margin-safe)")
                
                lot_size = safe_lot_size
        else:
            # Fallback: basic rounding
            lot_size = round(lot_size, 2)
            lot_size = max(0.01, min(lot_size, 10.0))
        
        return lot_size
    
    def _calculate_required_margin(self, entry_price, lot_size):
        """
        ✅ DAY 3 FIX: Calculate required margin for position
        
        Args:
            entry_price (float): Entry price
            lot_size (float): Position size in lots
            
        Returns:
            float: Required margin in account currency
        """
        try:
            if self.symbol_info:
                # Contract size (100 oz for XAUUSD)
                contract_size = self.symbol_info.trade_contract_size
                
                # Margin requirement per lot
                # For XAUUSD: margin = (price * contract_size * lot_size) / leverage
                leverage = 100  # Typical forex leverage
                
                required_margin = (entry_price * contract_size * lot_size) / leverage
                
                return required_margin
            else:
                # Fallback estimate
                return entry_price * 100 * lot_size / 100
        except Exception as e:
            print(f"⚠️ Margin calculation error: {e}")
            return entry_price * 100 * lot_size / 100
    
    def _get_free_margin(self):
        """
        ✅ DAY 3 FIX: Get available free margin
        
        Returns:
            float: Free margin available
        """
        try:
            account_info = mt5.account_info()
            if account_info:
                return account_info.margin_free
            else:
                # Fallback: assume 80% of balance is free
                return self.account_balance * 0.8
        except Exception as e:
            print(f"⚠️ Free margin check error: {e}")
            return self.account_balance * 0.8
    
    def get_risk_metrics(self, entry_price, stop_loss, lot_size, take_profit=None):
        """
        ✅ DAY 3 FIX: Get comprehensive risk metrics with validation
        
        Args:
            entry_price (float): Entry price
            stop_loss (float): Stop loss price
            lot_size (float): Position size
            take_profit (float): Take profit price (optional)
            
        Returns:
            dict: Risk metrics
        """
        # Calculate distances
        sl_distance = abs(entry_price - stop_loss)
        sl_pips = sl_distance / 0.01  # Convert to pips
        
        # Calculate risk amount
        # P&L = price_change * 100 * lot_size
        risk_amount = sl_distance * 100 * lot_size
        risk_percent = (risk_amount / self.account_balance) * 100
        
        # Position value
        position_value = entry_price * 100 * lot_size  # Contract size = 100 oz
        
        # Calculate reward if TP provided
        reward_amount = 0
        reward_ratio = 0
        tp_pips = 0
        
        if take_profit:
            tp_distance = abs(take_profit - entry_price)
            tp_pips = tp_distance / 0.01
            reward_amount = tp_distance * 100 * lot_size
            
            if risk_amount > 0:
                reward_ratio = reward_amount / risk_amount
        
        # ✅ DAY 3 FIX: Add margin info
        required_margin = self._calculate_required_margin(entry_price, lot_size)
        free_margin = self._get_free_margin()
        margin_level = (self.account_balance / required_margin * 100) if required_margin > 0 else 0
        
        return {
            'stop_loss_pips': round(sl_pips, 2),
            'take_profit_pips': round(tp_pips, 2),
            'risk_amount': round(risk_amount, 2),
            'risk_percent': round(risk_percent, 2),
            'reward_amount': round(reward_amount, 2),
            'reward_ratio': round(reward_ratio, 2),
            'position_value': round(position_value, 2),
            'required_margin': round(required_margin, 2),
            'free_margin': round(free_margin, 2),
            'margin_level': round(margin_level, 2)
        }
    
    def check_risk_limits(self, open_positions, total_risk_percent):
        """
        Check if risk limits allow new trade
        
        Args:
            open_positions (list): List of current open positions
            total_risk_percent (float): Total risk from open positions
            
        Returns:
            tuple: (can_trade: bool, message: str)
        """
        # Check daily risk limit
        if total_risk_percent >= self.max_risk_per_day:
            return False, f"Daily risk limit reached ({total_risk_percent:.1f}%/{self.max_risk_per_day}%)"
        
        # Check number of positions (max 3)
        if len(open_positions) >= 3:
            return False, "Maximum 3 positions already open"
        
        # ✅ DAY 3 FIX: Check margin availability
        free_margin = self._get_free_margin()
        if free_margin < 100:  # Minimum $100 free margin required
            return False, f"Insufficient free margin (${free_margin:.2f})"
        
        return True, "Risk limits OK"
