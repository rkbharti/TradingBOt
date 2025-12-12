"""
Bias Detection Module
Guardeer VIDEO 9: Bias = Market Direction

Key Concepts:
- OHLC pattern (Bearish): Open → High → Low → Close
- OLHC pattern (Bullish): Open → Low → High → Close
- From Guardeer: "Bias is the most critical concept"
"""

import pandas as pd
import numpy as np


class BiasDetector:
    """
    Bias Detection from Guardeer VIDEO 9
    
    Bias = Direction the market is heading
    
    OHLC pattern (Bearish): Open → High → Low → Close
    OLHC pattern (Bullish): Open → Low → High → Close
    
    From Guardeer: "Bias is the most critical concept - defines what you can do"
    """
    
    def __init__(self, df):
        self.df = df
        self.daily_bias = None
        self.weekly_bias = None
        self.monthly_bias = None
    
    def analyze_daily_pattern(self, daily_candle):
        """
        Analyze daily candle OHLC pattern.
        
        Returns:
        - 'BULLISH' if OLHC (Open → Low → High → Close)
        - 'BEARISH' if OHLC (Open → High → Low → Close)
        - 'NEUTRAL' otherwise
        """
        try:
            open_price = float(daily_candle.get('open'))
            high = float(daily_candle.get('high'))
            low = float(daily_candle.get('low'))
            close = float(daily_candle.get('close'))
            
        except (ValueError, TypeError):
            return 'NEUTRAL'
        
        # OLHC = Bullish (price went down first, then recovered strongly)
        # Sequence: Open → Low → High → Close
        # Indicator: Close in upper half + opened near high + closed above open
        if open_price > low and low < high and close > open_price:
            # Confirm it's OLHC by checking if O > L and C > O
            if open_price > low and close > open_price:
                # Extra confirmation: close should be near high
                range_size = high - low
                close_from_low = close - low
                if range_size > 0 and close_from_low > range_size * 0.65:
                    return 'BULLISH'
        
        # OHLC = Bearish (price went up first, then sold off)
        # Sequence: Open → High → Low → Close  
        # Indicator: Close in lower half + opened near low + closed below open
        if open_price < high and high > low and close < open_price:
            # Confirm it's OHLC by checking if O < H and C < O
            if open_price < high and close < open_price:
                # Extra confirmation: close should be near low
                range_size = high - low
                close_from_low = close - low
                if range_size > 0 and close_from_low < range_size * 0.35:
                    return 'BEARISH'
        
        # Check close position relative to range as fallback
        range_size = high - low
        if range_size == 0:
            return 'NEUTRAL'
        
        close_position = (close - low) / range_size
        
        # Bullish: Close in upper 65% of range + close > open
        if close_position > 0.65 and close > open_price:
            return 'BULLISH'
        
        # Bearish: Close in lower 35% of range + close < open
        elif close_position < 0.35 and close < open_price:
            return 'BEARISH'
        
        return 'NEUTRAL'
    
    def get_ohlc_sequence(self, candle):
        """
        Get exact OHLC sequence of a candle.
        
        Returns order of: Open, High, Low, Close
        Example: 'OLHC' means Open→Low→High→Close
        """
        try:
            o = float(candle.get('open'))
            h = float(candle.get('high'))
            l = float(candle.get('low'))
            c = float(candle.get('close'))
        except (ValueError, TypeError):
            return 'UNKNOWN'
        
        # Create ordered list with labels
        prices = [('O', o), ('H', h), ('L', l), ('C', c)]
        
        # Sort by price value
        sorted_prices = sorted(prices, key=lambda x: x[1])
        
        # Get sequence
        sequence = ''.join([p[0] for p in sorted_prices])
        
        return sequence
    
    def get_htf_bias(self, df_daily, lookback=3):
        """
        Get Higher Timeframe bias for direction confirmation.
        
        From Guardeer: ALWAYS check HTF bias FIRST
        Never trade against higher timeframe direction
        
        Analyzes last 3 daily candles for trend confirmation
        """
        try:
            if len(df_daily) < lookback:
                return 'NEUTRAL'
            
            recent_candles = df_daily.tail(lookback)
            
            # Count bullish vs bearish
            bullish_count = 0
            bearish_count = 0
            neutral_count = 0
            
            for _, candle in recent_candles.iterrows():
                bias = self.analyze_daily_pattern(candle)
                if bias == 'BULLISH':
                    bullish_count += 1
                elif bias == 'BEARISH':
                    bearish_count += 1
                else:
                    neutral_count += 1
            
            # Majority determines bias
            if bullish_count > bearish_count:
                return 'BULLISH'
            elif bearish_count > bullish_count:
                return 'BEARISH'
            else:
                return 'NEUTRAL'
            
        except Exception as e:
            print(f"⚠️ Error getting HTF bias: {e}")
            return 'NEUTRAL'
    
    def get_bias_alignment(self, daily_bias, entry_direction):
        """
        Check if entry direction aligns with daily bias.
        
        From Guardeer: Trade WITH bias, never AGAINST it
        
        Returns: True if aligned, False if not
        """
        if daily_bias == 'BULLISH' and entry_direction == 'BUY':
            return True
        elif daily_bias == 'BEARISH' and entry_direction == 'SELL':
            return True
        elif daily_bias == 'NEUTRAL':
            return True  # Can trade either direction in neutral
        else:
            return False
    
    def get_intraday_bias(self, lookback=20):
        """
        Get intraday bias (15-min or lower timeframe).
        
        Looks at recent price action pattern
        """
        try:
            if len(self.df) < lookback:
                return 'NEUTRAL'
            
            recent = self.df.tail(lookback)
            
            # Count bullish vs bearish candles
            bullish = 0
            bearish = 0
            
            for _, candle in recent.iterrows():
                try:
                    open_p = float(candle['open'])
                    close_p = float(candle['close'])
                    if close_p > open_p:
                        bullish += 1
                    elif close_p < open_p:
                        bearish += 1
                except:
                    pass
            
            if bullish > bearish:
                return 'BULLISH'
            elif bearish > bullish:
                return 'BEARISH'
            else:
                return 'NEUTRAL'
        
        except Exception as e:
            print(f"⚠️ Error getting intraday bias: {e}")
            return 'NEUTRAL'
    
    def get_price_action_bias(self):
        """Analyze price action bias from recent candles"""
        try:
            # ✅ Ensure we're working with DataFrame, not tuple
            if not isinstance(self.df, pd.DataFrame):
                print("   ⚠️  df is not a DataFrame")
                return "NEUTRAL"
            
            if len(self.df) < 5:
                return "NEUTRAL"
            
            # Get last candle as Series (not tuple)
            latest = self.df.iloc[-1]
            
            # Convert to dict if it's a Series
            if isinstance(latest, pd.Series):
                latest = latest.to_dict()
            
            # Now safely access as dict
            close = latest.get('close', 0)
            open_price = latest.get('open', 0)
            high = latest.get('high', 0)
            low = latest.get('low', 0)
            
            body_size = abs(close - open_price)
            total_range = high - low
            
            if total_range == 0:
                return "NEUTRAL"
            
            # Strong bullish candle
            if close > open_price and body_size / total_range > 0.7:
                return "BULLISH"
            
            # Strong bearish candle
            elif close < open_price and body_size / total_range > 0.7:
                return "BEARISH"
            
            else:
                return "NEUTRAL"
                
        except Exception as e:
            print(f"   ⚠️  Price action bias error: {e}")
            return "NEUTRAL"

    
    def get_combined_bias(self, daily_bias=None, intraday_bias=None, price_action_bias=None):
        """
        Combine multiple biases into final recommendation.
        
        Voting system: Which direction appears most?
        """
        if daily_bias is None:
            daily_bias = self.daily_bias or 'NEUTRAL'
        if intraday_bias is None:
            intraday_bias = self.get_intraday_bias()
        if price_action_bias is None:
            price_action_bias = self.get_price_action_bias()
        
        bullish_votes = 0
        bearish_votes = 0
        
        # Vote
        for bias in [daily_bias, intraday_bias, price_action_bias]:
            if bias == 'BULLISH':
                bullish_votes += 1
            elif bias == 'BEARISH':
                bearish_votes += 1
        
        # Determine combined bias
        if bullish_votes > bearish_votes:
            return 'BULLISH'
        elif bearish_votes > bullish_votes:
            return 'BEARISH'
        else:
            return 'NEUTRAL'
