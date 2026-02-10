"""
Guardeer Video 2: Multi-Timeframe Fractal Analysis
Integrated with existing analyze_enhanced() method
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime


class MultiTimeframeFractal:
    """
    Multi-timeframe fractal analysis for BOS, CHOC, and IDM detection
    Works alongside your existing analyze_enhanced() method
    """
    
    def __init__(self, symbol="XAUUSD"):
        self.symbol = symbol
        self.timeframes = {
            'M5': mt5.TIMEFRAME_M5,
            'M15': mt5.TIMEFRAME_M15,
            'H1': mt5.TIMEFRAME_H1,
            'H4': mt5.TIMEFRAME_H4,
            'D1': mt5.TIMEFRAME_D1
        }
    
    def fetch_data(self, timeframe_name: str, bars=300) -> pd.DataFrame:
        """Fetch OHLC data for a specific timeframe"""
        try:
            tf = self.timeframes[timeframe_name]
            rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, bars)
            
            if rates is None or len(rates) == 0:
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            return df
            
        except Exception as e:
            print(f"   ❌ Error fetching {timeframe_name}: {e}")
            return None
    
    
    def detect_swing_points(self, df: pd.DataFrame, sensitivity=5) -> tuple:
        if len(df) < sensitivity * 2 + 1:
            return [], []
        
        swing_highs = []
        swing_lows = []
        
        for i in range(sensitivity, len(df) - sensitivity):
            is_high = all(df.iloc[i]['high'] >= df.iloc[j]['high'] 
                         for j in range(i - sensitivity, i + sensitivity + 1))
            if is_high:
                swing_highs.append(i)
            
            is_low = all(df.iloc[i]['low'] <= df.iloc[j]['low'] 
                        for j in range(i - sensitivity, i + sensitivity + 1))
            if is_low:
                swing_lows.append(i)
        
        return swing_highs, swing_lows
    
    
    def detect_bos(self, df: pd.DataFrame, swing_highs: list, swing_lows: list) -> dict:
        result = {
            'bullish_bos': False,
            'bearish_bos': False,
            'bos_price': None,
            'strength': 'WEAK'
        }
        
        if len(df) < 5:
            return result
        
        current_price = df.iloc[-1]['close']
        
        if swing_highs:
            most_recent_high = df.iloc[swing_highs[-1]]['high']
            if current_price > most_recent_high:
                result['bullish_bos'] = True
                result['bos_price'] = most_recent_high
                result['strength'] = 'STRONG'
        
        if swing_lows:
            most_recent_low = df.iloc[swing_lows[-1]]['low']
            if current_price < most_recent_low:
                result['bearish_bos'] = True
                result['bos_price'] = most_recent_low
                result['strength'] = 'STRONG'
        
        return result
    
    
    def detect_choc(self, df: pd.DataFrame, swing_highs: list, swing_lows: list) -> dict:
        result = {
            'bullish_choc': False,
            'bearish_choc': False,
            'description': 'No CHOC'
        }
        
        if len(swing_highs) < 3 or len(swing_lows) < 3:
            return result
        
        last_3_highs = [df.iloc[i]['high'] for i in swing_highs[-3:]]
        last_3_lows = [df.iloc[i]['low'] for i in swing_lows[-3:]]
        
        if last_3_lows[0] > last_3_lows[1] > last_3_lows[2]:
            if last_3_highs[-1] > last_3_highs[-2]:
                result['bullish_choc'] = True
                result['description'] = 'Downtrend broken, higher high formed'
        
        if last_3_highs[0] < last_3_highs[1] < last_3_highs[2]:
            if last_3_lows[-1] < last_3_lows[-2]:
                result['bearish_choc'] = True
                result['description'] = 'Uptrend broken, lower low formed'
        
        return result
    
    
    def detect_idm(self, df: pd.DataFrame) -> dict:
        result = {
            'idm_detected': False,
            'idm_type': 'NONE',
            'probability': 0
        }
        
        if len(df) < 5:
            return result
        
        recent = df.iloc[-5:].copy()
        recent['body'] = abs(recent['close'] - recent['open'])
        recent['range'] = recent['high'] - recent['low']
        
        large_wick_count = 0
        small_body_count = 0
        
        for idx, row in recent.iterrows():
            body_ratio = row['body'] / row['range'] if row['range'] > 0 else 0
            
            if body_ratio < 0.3:
                small_body_count += 1
            
            upper_wick = row['high'] - max(row['open'], row['close'])
            if row['range'] > 0 and upper_wick > row['range'] * 0.6:
                large_wick_count += 1
        
        if large_wick_count >= 3 and small_body_count >= 3:
            result['idm_detected'] = True
            result['idm_type'] = 'BEARISH_IDM'
            result['probability'] = min(100, (large_wick_count / 5) * 100)
        
        return result
    
    
    def analyze_timeframe(self, timeframe: str, bars=300) -> dict:
        df = self.fetch_data(timeframe, bars=bars)
        if df is None:
            return None
        print(f"{timeframe} candles loaded:", len(df))
        
        swing_highs, swing_lows = self.detect_swing_points(df, sensitivity=3)
        bos = self.detect_bos(df, swing_highs, swing_lows)
        choc = self.detect_choc(df, swing_highs, swing_lows)
        idm = self.detect_idm(df)
        
        bias_score = 0
        if bos['bullish_bos']:
            bias_score += 2
        if bos['bearish_bos']:
            bias_score -= 2
        if choc['bullish_choc']:
            bias_score += 1
        if choc['bearish_choc']:
            bias_score -= 1
        
        bias = 'BULLISH' if bias_score > 0 else ('BEARISH' if bias_score < 0 else 'NEUTRAL')
        
        return {
            'timeframe': timeframe,
            'bias': bias,
            'bos': bos,
            'choc': choc,
            'idm': idm,
            'swing_highs': len(swing_highs),
            'swing_lows': len(swing_lows)
        }

    # =========================================================
    # NEW METHOD — DOES NOT MODIFY OLD LOGIC
    # =========================================================
    def get_multi_tf_confluence(self) -> dict:
        """
        Aggregates bias from all timeframes.
        This method is required by main.py.
        """
        results = {}
        
        for tf in ['D1', 'H4', 'H1', 'M15', 'M5']:
            analysis = self.analyze_timeframe(tf, bars=300)
            if analysis:
                results[tf] = analysis
        
        bull = 0
        bear = 0
        
        for tf_data in results.values():
            if tf_data["bias"] == "BULLISH":
                bull += 1
            elif tf_data["bias"] == "BEARISH":
                bear += 1
        
        if bull > bear:
            overall = "BULLISH"
        elif bear > bull:
            overall = "BEARISH"
        else:
            overall = "NEUTRAL"
        
        total = bull + bear
        confidence = int((max(bull, bear) / total) * 100) if total > 0 else 0
        
        return {
            "overall_bias": overall,
            "confidence": confidence,
            "tf_signals": results
        }
