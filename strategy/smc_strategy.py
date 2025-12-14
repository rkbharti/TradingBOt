import pandas as pd
import numpy as np
from datetime import datetime, time


class SMCStrategy:
    """
    Enhanced Smart Money Concept (SMC) Strategy for XAUUSD
    Implements: FVG, BOS, Liquidity Sweeps, Premium/Discount Zones, Session Filters
    """
    
    def __init__(self):
        self.signals = []
        self.last_signal = "HOLD"
        self.market_structure = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
        self.last_bos = None
    
    def convert_mt5_data_to_dataframe(self, data):
        """Convert MT5 numpy array to pandas DataFrame with proper columns"""
        df = pd.DataFrame(data)
        
        # MT5 returns: time, open, high, low, close, tick_volume, spread, real_volume
        if 'close' not in df.columns and len(df.columns) >= 5:
            df.columns = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']
        
        return df
        
    def calculate_atr(self, df, period=14):
        """Calculate Average True Range for volatility measurement"""
        df['h-l'] = df['high'] - df['low']
        df['h-pc'] = abs(df['high'] - df['close'].shift(1))
        df['l-pc'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)
        df['atr'] = df['tr'].rolling(window=period).mean()
        df.drop(['h-l', 'h-pc', 'l-pc', 'tr'], axis=1, inplace=True)
        return df
    
    def calculate_indicators(self, df):
        """Calculate technical indicators for SMC analysis"""
        # Moving Averages
        df['MA5'] = df['close'].rolling(window=5).mean()
        df['MA20'] = df['close'].rolling(window=20).mean()
        df['MA50'] = df['close'].rolling(window=50).mean()
        df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        # ATR for dynamic stops
        df = self.calculate_atr(df, period=14)
        
        # Support and Resistance levels
        df['resistance'] = df['high'].rolling(window=20).max()
        df['support'] = df['low'].rolling(window=20).min()
        
        # Swing points for structure
        df['swing_high'] = df['high'].rolling(window=5, center=True).max()
        df['swing_low'] = df['low'].rolling(window=5, center=True).min()
        df['is_swing_high'] = df['high'] == df['swing_high']
        df['is_swing_low'] = df['low'] == df['swing_low']
        
        return df
    
    def detect_fair_value_gaps(self, df):
        """Detect Fair Value Gaps (FVG) - key SMC concept"""
        df['fvg_bullish'] = False
        df['fvg_bearish'] = False
        df['fvg_top'] = np.nan
        df['fvg_bottom'] = np.nan
        
        for i in range(2, len(df)):
            # Bullish FVG: Current low > Previous high (2 candles ago)
            if df['low'].iloc[i] > df['high'].iloc[i-2]:
                df.loc[df.index[i], 'fvg_bullish'] = True
                df.loc[df.index[i], 'fvg_top'] = df['low'].iloc[i]
                df.loc[df.index[i], 'fvg_bottom'] = df['high'].iloc[i-2]
            
            # Bearish FVG: Current high < Previous low (2 candles ago)
            elif df['high'].iloc[i] < df['low'].iloc[i-2]:
                df.loc[df.index[i], 'fvg_bearish'] = True
                df.loc[df.index[i], 'fvg_top'] = df['low'].iloc[i-2]
                df.loc[df.index[i], 'fvg_bottom'] = df['high'].iloc[i]
        
        return df
    
    def detect_order_blocks(self, df):
        """Enhanced order block detection with volume consideration"""
        df['order_block_bullish'] = False
        df['order_block_bearish'] = False
        df['ob_strength'] = 0
        
        for i in range(3, len(df)):
            # Bullish OB: Strong bearish candle followed by reversal
            candle_range = abs(df['close'].iloc[i-1] - df['open'].iloc[i-1])
            avg_range = df['high'].iloc[i-10:i].sub(df['low'].iloc[i-10:i]).mean()
            
            if candle_range > avg_range * 1.5:  # Strong candle
                # Bullish setup
                if (df['close'].iloc[i-1] < df['open'].iloc[i-1] and  # Bearish candle
                    df['close'].iloc[i] > df['open'].iloc[i] and      # Bullish reversal
                    df['close'].iloc[i] > df['high'].iloc[i-1]):      # Break high
                    df.loc[df.index[i], 'order_block_bullish'] = True
                    df.loc[df.index[i], 'ob_strength'] = candle_range / avg_range
                
                # Bearish setup
                elif (df['close'].iloc[i-1] > df['open'].iloc[i-1] and  # Bullish candle
                      df['close'].iloc[i] < df['open'].iloc[i] and      # Bearish reversal
                      df['close'].iloc[i] < df['low'].iloc[i-1]):       # Break low
                    df.loc[df.index[i], 'order_block_bearish'] = True
                    df.loc[df.index[i], 'ob_strength'] = candle_range / avg_range
        
        return df
    
    def detect_break_of_structure(self, df):
        """Detect Break of Structure (BOS) - critical SMC concept"""
        df['bos_bullish'] = False
        df['bos_bearish'] = False
        
        swing_highs = df[df['is_swing_high'] == True]['high'].values
        swing_lows = df[df['is_swing_low'] == True]['low'].values
        
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            # Bullish BOS: Price breaks above recent swing high
            recent_high = swing_highs[-2] if len(swing_highs) >= 2 else swing_highs[-1]
            if df['close'].iloc[-1] > recent_high:
                df.loc[df.index[-1], 'bos_bullish'] = True
                self.market_structure = "BULLISH"
                self.last_bos = "BULLISH"
            
            # Bearish BOS: Price breaks below recent swing low
            recent_low = swing_lows[-2] if len(swing_lows) >= 2 else swing_lows[-1]
            if df['close'].iloc[-1] < recent_low:
                df.loc[df.index[-1], 'bos_bearish'] = True
                self.market_structure = "BEARISH"
                self.last_bos = "BEARISH"
        
        return df
    
    def detect_liquidity_sweeps(self, df):
        """Detect liquidity grabs at swing highs/lows"""
        df['liquidity_sweep_high'] = False
        df['liquidity_sweep_low'] = False
        
        lookback = 10
        for i in range(lookback, len(df)):
            # High sweep: Wick above recent high then closes below
            recent_high = df['high'].iloc[i-lookback:i].max()
            if (df['high'].iloc[i] > recent_high and 
                df['close'].iloc[i] < recent_high):
                df.loc[df.index[i], 'liquidity_sweep_high'] = True
            
            # Low sweep: Wick below recent low then closes above
            recent_low = df['low'].iloc[i-lookback:i].min()
            if (df['low'].iloc[i] < recent_low and 
                df['close'].iloc[i] > recent_low):
                df.loc[df.index[i], 'liquidity_sweep_low'] = True
        
        return df
    
    def calculate_premium_discount_zones(self, df):
        """Calculate premium and discount zones using Fibonacci"""
        lookback = 50
        recent_high = df['high'].iloc[-lookback:].max()
        recent_low = df['low'].iloc[-lookback:].min()
        
        # Fibonacci levels
        fib_range = recent_high - recent_low
        df['fib_0.5'] = recent_low + (fib_range * 0.5)  # Equilibrium
        df['fib_0.618'] = recent_low + (fib_range * 0.618)  # Discount/Premium boundary
        df['fib_0.382'] = recent_low + (fib_range * 0.382)
        
        # Classify current price
        current_price = df['close'].iloc[-1]
        if current_price < df['fib_0.382'].iloc[-1]:
            df['zone'] = 'DEEP_DISCOUNT'
        elif current_price < df['fib_0.5'].iloc[-1]:
            df['zone'] = 'DISCOUNT'
        elif current_price > df['fib_0.618'].iloc[-1]:
            df['zone'] = 'PREMIUM'
        else:
            df['zone'] = 'EQUILIBRIUM'
        
        return df
    
    def get_current_session(self):
        """
        Check if current time is within trading sessions
        Returns: (session_name, is_active)
        """
        from datetime import datetime, time
        
        now = datetime.now()
        current_time = now.time()
        weekday = now.weekday()  # Monday=0, Sunday=6
        
        # ========================================
        # WEEKEND CHECK (Critical!)
        # ========================================
        if weekday == 5:  # Saturday - CLOSED ALL DAY
            return "WEEKEND (Saturday)", False
        elif weekday == 6:  # Sunday - CLOSED ALL DAY
            return "WEEKEND (Sunday)", False
        elif weekday == 0 and current_time < time(3, 30):  # Monday before 3:30 AM IST
            # Market opens Monday 3:30 AM IST (Sunday 5 PM EST)
            return "PRE-MARKET (Monday)", False
        elif weekday == 4 and current_time >= time(3, 30):  # Friday after 3:30 AM IST
            # Market closes Friday 5 PM EST = Saturday 3:30 AM IST
            return "POST-MARKET (Friday)", False
        
        # ========================================
        # SESSION TIMES (IST = UTC+5:30)
        # ========================================
        sessions = {
            'TOKYO': (time(6, 0), time(12, 30)),
            'LONDON': (time(13, 30), time(22, 0)),
            'NEW_YORK': (time(19, 0), time(2, 0)),
            'LONDON/NEW_YORK': (time(19, 0), time(22, 0))  # Overlap
        }
        
        # ========================================
        # CHECK ACTIVE SESSION
        # ========================================
        for session_name, (start, end) in sessions.items():
            # Handle overnight sessions (NY session crosses midnight)
            if start > end:  # Overnight session
                if current_time >= start or current_time <= end:
                    return session_name, True
            else:  # Normal session
                if start <= current_time <= end:
                    return session_name, True
        
        # ========================================
        # NO SESSION ACTIVE
        # ========================================
        return "CLOSED", False


    
    def check_trading_session(self):
        """Check if current time is within optimal trading sessions (IST)"""
        session_name, is_active = self.get_current_session()
        
        # Prefer London and New York sessions for gold trading
        # Asian session has lower volume
        if session_name in ["LONDON", "LONDON/NEW_YORK", "NEW_YORK"]:
            return True, session_name
        else:
            return False, session_name
    
    def generate_signal(self, data):
        """Generate enhanced SMC-based trading signals"""
        if data is None or len(data) < 200:
            return "HOLD", "Insufficient data for analysis"
        
        # Convert MT5 numpy array to DataFrame with proper columns
        df = self.convert_mt5_data_to_dataframe(data)
        
        # Apply all indicators
        df = self.calculate_indicators(df)
        df = self.detect_fair_value_gaps(df)
        df = self.detect_order_blocks(df)
        df = self.detect_break_of_structure(df)
        df = self.detect_liquidity_sweeps(df)
        df = self.calculate_premium_discount_zones(df)
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Check trading session
        in_session, session_name = self.check_trading_session()
        
        signal = "HOLD"
        reason = "No clear SMC setup"
        confidence = 0
        
        # === BULLISH SIGNAL CONDITIONS ===
        bullish_score = 0
        bullish_reasons = []
        
        if self.market_structure == "BULLISH" or current.get('bos_bullish', False):
            bullish_score += 2
            bullish_reasons.append("Bullish BOS")
        
        if current.get('fvg_bullish', False):
            bullish_score += 2
            bullish_reasons.append("Bullish FVG")
        
        if current.get('order_block_bullish', False):
            bullish_score += 1.5 * current.get('ob_strength', 1)
            bullish_reasons.append("Bullish OB")
        
        if current.get('liquidity_sweep_low', False):
            bullish_score += 2
            bullish_reasons.append("Liquidity sweep low")
        
        if current.get('zone') == 'DISCOUNT' or current.get('zone') == 'DEEP_DISCOUNT':
            bullish_score += 1.5
            bullish_reasons.append("Discount zone")
        
        if current['close'] > current['EMA200']:
            bullish_score += 1
            bullish_reasons.append("Above EMA200")
        
        if current['MA5'] > current['MA20'] > current['MA50']:
            bullish_score += 1
            bullish_reasons.append("MA alignment")
        
        # === BEARISH SIGNAL CONDITIONS ===
        bearish_score = 0
        bearish_reasons = []
        
        if self.market_structure == "BEARISH" or current.get('bos_bearish', False):
            bearish_score += 2
            bearish_reasons.append("Bearish BOS")
        
        if current.get('fvg_bearish', False):
            bearish_score += 2
            bearish_reasons.append("Bearish FVG")
        
        if current.get('order_block_bearish', False):
            bearish_score += 1.5 * current.get('ob_strength', 1)
            bearish_reasons.append("Bearish OB")
        
        if current.get('liquidity_sweep_high', False):
            bearish_score += 2
            bearish_reasons.append("Liquidity sweep high")
        
        if current.get('zone') == 'PREMIUM':
            bearish_score += 1.5
            bearish_reasons.append("Premium zone")
        
        if current['close'] < current['EMA200']:
            bearish_score += 1
            bearish_reasons.append("Below EMA200")
        
        if current['MA5'] < current['MA20'] < current['MA50']:
            bearish_score += 1
            bearish_reasons.append("MA alignment")
        
        # === DECISION LOGIC ===
        min_score = 4.0  # Require strong confirmation
        
        if bullish_score >= min_score and bullish_score > bearish_score:
            signal = "BUY"
            confidence = min(bullish_score / 8 * 100, 100)  # Convert to percentage
            reason = f"Bullish SMC: {', '.join(bullish_reasons[:3])}"
            
            # ✅ ZONE FILTER: Only BUY in DISCOUNT zones
            if current.get('zone') not in ['DISCOUNT', 'DEEP_DISCOUNT']:
                signal = "HOLD"
                reason = f"[FILTERED] {reason} - Not in discount zone (current: {current.get('zone')})"
            
            if not in_session:
                signal = "HOLD"
                reason += f" [Outside trading session - {session_name}]"
                
        elif bearish_score >= min_score and bearish_score > bullish_score:
            signal = "SELL"
            confidence = min(bearish_score / 8 * 100, 100)
            reason = f"Bearish SMC: {', '.join(bearish_reasons[:3])}"
            
            # ✅ ZONE FILTER: Only SELL in PREMIUM zones
            if current.get('zone') != 'PREMIUM':
                signal = "HOLD"
                reason = f"[FILTERED] {reason} - Not in premium zone (current: {current.get('zone')})"
            
            if not in_session:
                signal = "HOLD"
                reason += f" [Outside trading session - {session_name}]"
        
        # Add session info to reason
        if signal != "HOLD" and in_session:
            reason += f" [{session_name} session]"
        
        self.last_signal = signal
        return signal, reason
    
    def get_strategy_stats(self, data):
        """Return enhanced strategy statistics"""
        if data is None or len(data) < 2:
            return {}
        
        # Convert MT5 data to DataFrame
        df = self.convert_mt5_data_to_dataframe(data)
        df = self.calculate_indicators(df)
        df = self.detect_fair_value_gaps(df)
        df = self.detect_break_of_structure(df)
        df = self.calculate_premium_discount_zones(df)
        
        current = df.iloc[-1]
        in_session, session_name = self.get_current_session()
        
        # ===== FIXED SESSION DISPLAY =====
        return {
            'current_price': current['close'],
            'ma20': current.get('MA20', 0),
            'ma50': current.get('MA50', 0),
            'ema200': current.get('EMA200', 0),
            'support': current.get('support', 0),
            'resistance': current.get('resistance', 0),
            'atr': current.get('atr', 0),
            'market_structure': self.market_structure,
            'zone': current.get('zone', 'UNKNOWN'),
            'last_signal': self.last_signal,
            'session': session_name,  # ✅ NOW SHOWS CORRECT SESSION
            'in_trading_hours': in_session,
            'fvg_bullish': current.get('fvg_bullish', False),
            'fvg_bearish': current.get('fvg_bearish', False),
            'bos': self.last_bos
        }