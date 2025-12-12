"""
Liquidity Detection Module
Guardeer VIDEO 5: Liquidity Zones, PDH/PDL, Swing Highs/Lows

Key Concepts:
- External Liquidity: Previous Day High/Low (PDH/PDL)
- Internal Liquidity: Swing Highs/Lows
- Liquidity Grabs signal reversal
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class LiquidityDetector:
    """
    Detects liquidity zones from Guardeer VIDEO 5
    - Previous Day High/Low (PDH/PDL)
    - Swing Highs and Lows
    - Liquidity grabs and sweeps
    
    From Guardeer: "When price grabs liquidity, it's going opposite direction"
    """
    
    def __init__(self, df):
        self.df = df  # Pandas dataframe with OHLCV
        self.pdh = None
        self.pdl = None
        self.swings = {'highs': [], 'lows': []}
        
    def get_previous_day_high_low(self):
        """Get previous day high/low with proper DataFrame time handling"""
        try:
            import pytz
            from datetime import datetime, timedelta
            
            # Check if 'time' column exists
            if 'time' not in self.df.columns:
                print(f"   ⚠️  'time' column not found in DataFrame. Columns: {list(self.df.columns)}")
                return None, None
            
            # ✅ CRITICAL: Convert time column to datetime if it's not already
            if not pd.api.types.is_datetime64_any_dtype(self.df['time']):
                print(f"   🔧 Converting time column from {self.df['time'].dtype} to datetime...")
                # Handle both Unix timestamps (int) and datetime strings
                if pd.api.types.is_numeric_dtype(self.df['time']):
                    # Unix timestamp (seconds)
                    self.df['time'] = pd.to_datetime(self.df['time'], unit='s')
                else:
                    # String datetime
                    self.df['time'] = pd.to_datetime(self.df['time'])
            
            # Set IST timezone
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)
            
            # Make DataFrame timezone-aware if not already
            if self.df['time'].dt.tz is None:
                self.df['time'] = self.df['time'].dt.tz_localize('UTC').dt.tz_convert(ist)
            else:
                self.df['time'] = self.df['time'].dt.tz_convert(ist)
            
            # Get today's date at midnight IST
            today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get yesterday's date range
            yesterday_end = today_start
            yesterday_start = yesterday_end - timedelta(days=1)
            
            print(f"   🔍 Looking for PDH/PDL between {yesterday_start.strftime('%Y-%m-%d %H:%M')} and {yesterday_end.strftime('%Y-%m-%d %H:%M')}")
            
            # Filter DataFrame for yesterday's data
            df_yesterday = self.df[
                (self.df['time'] >= yesterday_start) & 
                (self.df['time'] < yesterday_end)
            ]
            
            if len(df_yesterday) == 0:
                # Try last 24 hours if no exact yesterday match
                cutoff_time = now_ist - timedelta(hours=24)
                df_yesterday = self.df[self.df['time'] >= cutoff_time]
                
                if len(df_yesterday) == 0:
                    print(f"   ⚠️  No data available for PDH/PDL calculation")
                    print(f"   📊 DataFrame time range: {self.df['time'].min()} to {self.df['time'].max()}")
                    return None, None
                
                print(f"   ℹ️  Using last 24 hours data ({len(df_yesterday)} bars)")
            else:
                print(f"   ✅ Found {len(df_yesterday)} bars for {yesterday_start.date()}")
            
            pdh = float(df_yesterday['high'].max())
            pdl = float(df_yesterday['low'].min())
            
            return pdh, pdl
            
        except Exception as e:
            print(f"   ❌ Error calculating PDH/PDL: {e}")
            import traceback
            traceback.print_exc()
            return None, None


    
    def get_swing_high_low(self, lookback=20, min_candles_between=2):
        """
        Identify Swing Highs and Swing Lows.
        
        A swing high: price makes higher high, then retreats
        A swing low: price makes lower low, then bounces
        
        From Guardeer: "Swings are foundation of identifying liquidity zones"
        """
        swings = {'highs': [], 'lows': []}
        
        try:
            if len(self.df) < lookback + 5:
                return swings
            
            # Use only recent data
            df_recent = self.df.tail(lookback).reset_index(drop=True)
            
            # Convert to numeric
            highs = pd.to_numeric(df_recent['high'], errors='coerce')
            lows = pd.to_numeric(df_recent['low'], errors='coerce')
            times = df_recent['time'] if 'time' in df_recent.columns else None
            
            # Identify swing highs (local maxima)
            for i in range(min_candles_between + 1, len(df_recent) - min_candles_between - 1):
                # Check if current high is higher than surrounding candles
                is_swing_high = True
                
                for j in range(1, min_candles_between + 1):
                    if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                        is_swing_high = False
                        break
                
                if is_swing_high:
                    swing_entry = {
                        'price': float(highs[i]),
                        'index': i,
                        'time': times[i] if times is not None else None,
                        'bar_number': len(self.df) - lookback + i
                    }
                    swings['highs'].append(swing_entry)
            
            # Identify swing lows (local minima)
            for i in range(min_candles_between + 1, len(df_recent) - min_candles_between - 1):
                # Check if current low is lower than surrounding candles
                is_swing_low = True
                
                for j in range(1, min_candles_between + 1):
                    if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                        is_swing_low = False
                        break
                
                if is_swing_low:
                    swing_entry = {
                        'price': float(lows[i]),
                        'index': i,
                        'time': times[i] if times is not None else None,
                        'bar_number': len(self.df) - lookback + i
                    }
                    swings['lows'].append(swing_entry)
            
            self.swings = swings
            return swings
            
        except Exception as e:
            print(f"⚠️ Error identifying swings: {e}")
            return swings
    
    def check_liquidity_grab(self, current_price, direction='both'):
        """
        Check if current price has grabbed/swept a liquidity zone.
        
        From Guardeer: "When price grabs liquidity, signal a reversal coming"
        
        direction: 'up' (grabbed PDH), 'down' (grabbed PDL), 'both'
        """
        grabbed = {
            'pdh_grabbed': False,
            'pdl_grabbed': False,
            'swing_highs_grabbed': [],
            'swing_lows_grabbed': []
        }
        
        try:
            if self.pdh is None:
                self.get_previous_day_high_low()
            
            current_price = float(current_price)
            
            # Check PDH grab
            if self.pdh and current_price > self.pdh:
                grabbed['pdh_grabbed'] = True
            
            # Check PDL grab
            if self.pdl and current_price < self.pdl:
                grabbed['pdl_grabbed'] = True
            
            # Check swing highs grabbed
            for swing in self.swings.get('highs', []):
                if current_price > swing['price']:
                    grabbed['swing_highs_grabbed'].append(swing)
            
            # Check swing lows grabbed
            for swing in self.swings.get('lows', []):
                if current_price < swing['price']:
                    grabbed['swing_lows_grabbed'].append(swing)
            
            return grabbed
            
        except Exception as e:
            print(f"⚠️ Error checking liquidity grab: {e}")
            return grabbed
    
    def get_liquidity_zones(self):
        """Get all important liquidity zones for the day"""
        zones = {
            'previous_day_high': self.pdh,
            'previous_day_low': self.pdl,
            'swing_highs': self.swings.get('highs', []),
            'swing_lows': self.swings.get('lows', [])
        }
        return zones
    
    def get_nearest_liquidity_above(self, current_price):
        """Find nearest liquidity zone ABOVE current price"""
        zones = []
        
        if self.pdh and self.pdh > current_price:
            zones.append(('PDH', self.pdh))
        
        for swing in self.swings.get('highs', []):
            if swing['price'] > current_price:
                zones.append(('SWING_HIGH', swing['price']))
        
        if zones:
            return min(zones, key=lambda x: x[1])
        return None
    
    def get_nearest_liquidity_below(self, current_price):
        """Find nearest liquidity zone BELOW current price"""
        zones = []
        
        if self.pdl and self.pdl < current_price:
            zones.append(('PDL', self.pdl))
        
        for swing in self.swings.get('lows', []):
            if swing['price'] < current_price:
                zones.append(('SWING_LOW', swing['price']))
        
        if zones:
            return max(zones, key=lambda x: x[1])
        return None
