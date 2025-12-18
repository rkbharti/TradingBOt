import pandas as pd
import numpy as np

class MarketStructureDetector:
    """Detects market structure, trends, and significant levels"""
    
    def __init__(self, df):
        """
        Args:
            df: pandas DataFrame with OHLC + tick_volume columns
        """
        self.df = df
        self.lookback = 20
    
    def get_market_structure_analysis(self):
        """
        Returns market structure analysis:
        - current_trend: UPTREND, DOWNTREND, NEUTRAL
        - trend_valid: Boolean if trend is still valid
        - structure_shift: CHOCH_BULLISH, CHOCH_BEARISH, BOS_BULLISH, BOS_BEARISH, NONE
        - bos_level: Break of Structure level
        - choch_detected: Change of Character detected
        """
        try:
            if len(self.df) < self.lookback:
                return self._default_analysis()
            
            # Get recent price action
            recent = self.df.tail(self.lookback).copy()
            current_price = float(self.df['close'].iloc[-1])
            
            # Find swing highs and lows
            swing_highs = []
            swing_lows = []
            
            for i in range(1, len(recent) - 1):
                # Swing High: higher than neighbors
                if (recent['high'].iloc[i] > recent['high'].iloc[i-1] and 
                    recent['high'].iloc[i] > recent['high'].iloc[i+1]):
                    swing_highs.append({
                        'price': float(recent['high'].iloc[i]),
                        'bar': i
                    })
                
                # Swing Low: lower than neighbors
                if (recent['low'].iloc[i] < recent['low'].iloc[i-1] and 
                    recent['low'].iloc[i] < recent['low'].iloc[i+1]):
                    swing_lows.append({
                        'price': float(recent['low'].iloc[i]),
                        'bar': i
                    })
            
            if not swing_highs or not swing_lows:
                return self._default_analysis()
            
            # Determine trend from last 2 swings
            last_high = swing_highs[-1]['price'] if swing_highs else 0
            last_low = swing_lows[-1]['price'] if swing_lows else 0
            prev_high = swing_highs[-2]['price'] if len(swing_highs) > 1 else 0
            prev_low = swing_lows[-2]['price'] if len(swing_lows) > 1 else 0
            
            # Identify trend
            if last_high > prev_high and last_low > prev_low:
                current_trend = 'UPTREND'
            elif last_high < prev_high and last_low < prev_low:
                current_trend = 'DOWNTREND'
            else:
                current_trend = 'NEUTRAL'
            
            # Check if trend is still valid
            trend_valid = True
            structure_shift = 'NONE'
            bos_level = None
            choch_detected = False
            
            if current_trend == 'UPTREND':
                # Trend breaks if price closes below last swing low
                if current_price < last_low:
                    trend_valid = False
                    structure_shift = 'BOS_BEARISH'
                    bos_level = last_low
                    
                    # Check for CHOCH (higher low not holding)
                    if last_low <= prev_low:
                        structure_shift = 'CHOCH_BEARISH'
                        choch_detected = True
            
            elif current_trend == 'DOWNTREND':
                # Trend breaks if price closes above last swing high
                if current_price > last_high:
                    trend_valid = False
                    structure_shift = 'BOS_BULLISH'
                    bos_level = last_high
                    
                    # Check for CHOCH (lower high not holding)
                    if last_high >= prev_high:
                        structure_shift = 'CHOCH_BULLISH'
                        choch_detected = True
            
            return {
                'current_trend': current_trend,
                'trend_valid': trend_valid,
                'structure_shift': structure_shift,
                'bos_level': bos_level,
                'choch_detected': choch_detected,
                'last_swing_high': last_high,
                'last_swing_low': last_low,
                'prev_swing_high': prev_high,
                'prev_swing_low': prev_low,
            }
        
        except Exception as e:
            print(f"   ⚠️  Error in market structure analysis: {e}")
            return self._default_analysis()
    
    def _default_analysis(self):
        """Return safe defaults when analysis fails"""
        return {
            'current_trend': 'NEUTRAL',
            'trend_valid': True,
            'structure_shift': 'NONE',
            'bos_level': None,
            'choch_detected': False,
            'last_swing_high': 0,
            'last_swing_low': 0,
            'prev_swing_high': 0,
            'prev_swing_low': 0,
        }
