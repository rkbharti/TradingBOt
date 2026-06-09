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
    
    def fetch_data(self, timeframe_name: str, bars=300, debug=False):
        """Fetch recent OHLC data for a specific timeframe using CLOSED candles only."""
        try:
            tf = self.timeframes[timeframe_name]

            if not mt5.symbol_select(self.symbol, True):
                return {
                    "df": None,
                    "is_stale": False,
                    "error": f"symbol_select failed for {self.symbol}",
                    "latest_closed_time": None,
                    "latest_visible_time": None,
                }

            tick = mt5.symbol_info_tick(self.symbol)
            if tick is None:
                return {
                    "df": None,
                    "is_stale": False,
                    "error": "symbol_info_tick returned None",
                    "latest_closed_time": None,
                    "latest_visible_time": None,
                }

            rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, bars + 3)
            if rates is None or len(rates) == 0:
                return {
                    "df": None,
                    "is_stale": False,
                    "error": f"copy_rates_from_pos returned no data for {timeframe_name}",
                    "latest_closed_time": None,
                    "latest_visible_time": None,
                }

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(None)
            df = df.drop_duplicates(subset=["time"]).sort_values("time", ascending=True).reset_index(drop=True)

            if len(df) < 2:
                return {
                    "df": None,
                    "is_stale": False,
                    "error": f"not enough bars for {timeframe_name}",
                    "latest_closed_time": None,
                    "latest_visible_time": None,
                }

            tick_time = pd.to_datetime(tick.time, unit="s", utc=True).tz_convert(None) if hasattr(tick, "time") else None

            latest_visible = df.iloc[-1]
            closed_df = df.iloc[:-1].copy().reset_index(drop=True)

            if closed_df.empty:
                return {
                    "df": None,
                    "is_stale": False,
                    "error": f"closed_df empty for {timeframe_name}",
                    "latest_closed_time": None,
                    "latest_visible_time": latest_visible["time"],
                }

            latest_closed = closed_df.iloc[-1]

            tf_minutes = {
                "M1": 1,
                "M5": 5,
                "M15": 15,
                "M30": 30,
                "H1": 60,
                "H4": 240,
                "D1": 1440,
            }.get(timeframe_name)

            is_stale = False
            if tf_minutes is not None:
                now_utc = pd.Timestamp.utcnow().tz_localize(None)
                max_allowed_age = pd.Timedelta(minutes=tf_minutes * 2)
                
                # Check if a weekend (Saturday/Sunday) falls between latest_closed["time"] and now_utc
                delta_days = (now_utc - latest_closed["time"]).days
                has_weekend = False
                for day_offset in range(delta_days + 1):
                    check_day = (latest_closed["time"] + pd.Timedelta(days=day_offset)).weekday()
                    if check_day in [5, 6]:  # Saturday or Sunday
                        has_weekend = True
                        break
                        
                if has_weekend:
                    max_allowed_age += pd.Timedelta(hours=48)
                    
                is_stale = (now_utc - latest_closed["time"]) > max_allowed_age

            if debug:
                print(f"\n[MT5 DATA CHECK] {self.symbol} {timeframe_name}")
                print(f"  Tick time              : {tick_time}")
                print("  Recent MT5 bars        :")
                for _, row in df.tail(3).iterrows():
                    print(
                        f"    {row['time']} | "
                        f"O={row['open']} H={row['high']} L={row['low']} C={row['close']}"
                    )

                print(
                    f"  DF latest visible row  : {latest_visible['time']} | "
                    f"O={latest_visible['open']} H={latest_visible['high']} "
                    f"L={latest_visible['low']} C={latest_visible['close']}"
                )

                print(
                    f"  DF latest closed row   : {latest_closed['time']} | "
                    f"O={latest_closed['open']} H={latest_closed['high']} "
                    f"L={latest_closed['low']} C={latest_closed['close']}"
                )

                if is_stale:
                    print(
                        f"   ⚠️ Stale {timeframe_name} data for {self.symbol}: "
                        f"last_closed_bar={latest_closed['time']}"
                    )

            return {
                "df": closed_df.tail(bars).reset_index(drop=True),
                "is_stale": is_stale,
                "error": None,
                "latest_closed_time": latest_closed["time"],
                "latest_visible_time": latest_visible["time"],
            }

        except Exception as e:
            return {
                "df": None,
                "is_stale": False,
                "error": str(e),
                "latest_closed_time": None,
                "latest_visible_time": None,
            }
    
    
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
            if current_price >= most_recent_high * 0.9998:  # 0.02% tolerance
                result['bullish_bos'] = True
                result['bos_price'] = most_recent_high
                result['strength'] = 'STRONG'
        
        if swing_lows:
            most_recent_low = df.iloc[swing_lows[-1]]['low']
            if current_price <= most_recent_low * 1.0002:   # 0.02% tolerance
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
    
    
    def analyze_timeframe(self, timeframe: str, bars=300) -> dict:
        """
        Analyze a single timeframe for BOS, CHoCH bias.

        NOTE: detect_idm() was removed from this class (Fix #12).
        The old implementation used wick ratios and doji counts to detect IDM —
        this is completely wrong. IDM is NOT a candle pattern; it is the first
        internal swing after a BOS, as defined by the creator in Lecture 3.
        Proper IDM detection now lives in SignalEngine._detect_idm() which
        correctly identifies the first swing low/high after a BOS and checks
        if it has been swept by a wick (body close not required).
        """
        raw = self.fetch_data(timeframe, bars=bars)
        # fetch_data returns a dict — extract the DataFrame
        if isinstance(raw, dict):
            df = raw.get("df")
        else:
            df = raw
        if df is None or len(df) == 0:
            return None
        print(f"{timeframe} candles loaded:", len(df))
        
        # Timeframe-aware sensitivity
        tf_sensitivity = {
            'D1': 1,
            'H4': 1,
            'H1': 2,
            'M15': 2,
            'M5': 3
        }
        sens = tf_sensitivity.get(timeframe, 3)
        swing_highs, swing_lows = self.detect_swing_points(df, sensitivity=sens)
        bos = self.detect_bos(df, swing_highs, swing_lows)
        choc = self.detect_choc(df, swing_highs, swing_lows)
        # FIX #12: detect_idm() call removed — method was deleted because it
        # used wick ratios / doji counts which is NOT how IDM works.
        # IDM detection is now in SignalEngine._detect_idm().

        bias_score = 0
        if bos['bullish_bos']:
            bias_score += 2
        if bos['bearish_bos']:
            bias_score -= 2
        if choc['bullish_choc']:
            bias_score += 1
        if choc['bearish_choc']:
            bias_score -= 1

        if bias_score > 0:
            bias = 'BULLISH'
        elif bias_score < 0:
            bias = 'BEARISH'
        elif choc['bullish_choc']:
            bias = 'BULLISH'
        elif choc['bearish_choc']:
            bias = 'BEARISH'
        else:
            bias = 'NEUTRAL'

        return {
            'timeframe':    timeframe,
            'bias':         bias,
            'bos':          bos,
            'choc':         choc,
            'swing_highs':  len(swing_highs),
            'swing_lows':   len(swing_lows),
        }
    #day 7
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
