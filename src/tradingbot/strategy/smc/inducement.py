"""
Inducement Detection Module
Guardeer VIDEO 3: Inducement Sweeps & Liquidity Traps

Key Concepts:
- Inducement = Price wicks into liquidity (PDH/PDL/Swing), then reverses
- Sweep = False breakout designed to trap traders
- High probability reversal setup after inducement detected

STEP 4 ENHANCEMENT: Session-based weighting for inducement reliability
"""

import pandas as pd
import numpy as np


class InducementDetector:
    """
    Detect inducement sweeps into liquidity zones.
    
    From Guardeer VIDEO 3:
    - Price creates a wick beyond liquidity level (PDH, PDL, Swing High/Low)
    - Price closes BACK inside the previous range
    - This is a trap for retail traders (inducement)
    - Often followed by sharp reversal in opposite direction
    
    STEP 4 ADDED: Session detector integration for reliability weighting
    """
    
    def __init__(self, df, liquidity_levels):
        """
        Args:
            df: DataFrame with OHLC data
            liquidity_levels: dict with {
                'PDH': price,
                'PDL': price,
                'swing_highs': [prices],
                'swing_lows': [prices]
            }
        """
        self.df = df
        self.levels = liquidity_levels
        
        # STEP 4: Initialize session detector
        try:
            from .session_detector import SessionDetector
            self.session_detector = SessionDetector()
            self.session_enabled = True
        except ImportError:
            print("⚠️ SessionDetector not available - using default weighting")
            self.session_detector = None
            self.session_enabled = False
    
    def detect_latest_inducement(self, lookback=10, wick_threshold=0.3):
        """
        Detect if latest candles show inducement sweep.
        
        Args:
            lookback: How many recent candles to check
            wick_threshold: Minimum wick size relative to body (0.3 = 30%)
        
        Returns:
            dict with inducement info or {'inducement': False}
        """
        try:
            if len(self.df) < lookback + 2:
                return {'inducement': False}
            
            recent = self.df.tail(lookback).reset_index(drop=True)
            
            # Check each recent candle for sweep pattern
            for i in range(len(recent) - 1, -1, -1):
                candle = recent.iloc[i]
                
                try:
                    high = float(candle['high'])
                    low = float(candle['low'])
                    open_price = float(candle['open'])
                    close = float(candle['close'])
                except (ValueError, TypeError):
                    continue
                
                body_size = abs(close - open_price)
                upper_wick = high - max(open_price, close)
                lower_wick = min(open_price, close) - low
                
                # Check for sweep above (bearish inducement)
                sweep_above = self._check_sweep_above(high, close, upper_wick, body_size, wick_threshold)
                if sweep_above:
                    # STEP 4: Apply session weighting
                    return self._apply_session_weighting(sweep_above)
                
                # Check for sweep below (bullish inducement)
                sweep_below = self._check_sweep_below(low, close, lower_wick, body_size, wick_threshold)
                if sweep_below:
                    # STEP 4: Apply session weighting
                    return self._apply_session_weighting(sweep_below)
            
            return {'inducement': False}
            
        except Exception as e:
            print(f"⚠️ Error detecting inducement: {e}")
            return {'inducement': False}
    
    def _check_sweep_above(self, high, close, upper_wick, body_size, wick_threshold):
        """
        Check if candle swept above a liquidity level (bearish inducement).
        
        Pattern:
        - High wicked above PDH/Swing High
        - Close back below the level
        - Upper wick is significant (> threshold)
        """
        # PDH sweep
        pdh = self.levels.get('PDH')
        if pdh and high > pdh and close < pdh:
            # Check wick is significant
            if body_size > 0 and upper_wick / body_size >= wick_threshold:
                wick_pips = upper_wick / 0.01  # Convert to pips for XAUUSD
                return {
                    'inducement': True,
                    'type': 'SWEEP_ABOVE_PDH',
                    'direction': 'BEARISH',
                    'level': pdh,
                    'high': high,
                    'close': close,
                    'wick_size': wick_pips,
                    'confidence': 'HIGH'
                }
        
        # Swing High sweep
        swing_highs = self.levels.get('swing_highs', [])
        for swing in swing_highs:
            try:
                swing_price = float(swing)
                if high > swing_price and close < swing_price:
                    if body_size > 0 and upper_wick / body_size >= wick_threshold:
                        wick_pips = upper_wick / 0.01
                        return {
                            'inducement': True,
                            'type': 'SWEEP_ABOVE_SWING',
                            'direction': 'BEARISH',
                            'level': swing_price,
                            'high': high,
                            'close': close,
                            'wick_size': wick_pips,
                            'confidence': 'MEDIUM'
                        }
            except (ValueError, TypeError):
                continue
        
        return None
    
    def _check_sweep_below(self, low, close, lower_wick, body_size, wick_threshold):
        """
        Check if candle swept below a liquidity level (bullish inducement).
        
        Pattern:
        - Low wicked below PDL/Swing Low
        - Close back above the level
        - Lower wick is significant (> threshold)
        """
        # PDL sweep
        pdl = self.levels.get('PDL')
        if pdl and low < pdl and close > pdl:
            # Check wick is significant
            if body_size > 0 and lower_wick / body_size >= wick_threshold:
                wick_pips = lower_wick / 0.01  # Convert to pips for XAUUSD
                return {
                    'inducement': True,
                    'type': 'SWEEP_BELOW_PDL',
                    'direction': 'BULLISH',
                    'level': pdl,
                    'low': low,
                    'close': close,
                    'wick_size': wick_pips,
                    'confidence': 'HIGH'
                }
        
        # Swing Low sweep
        swing_lows = self.levels.get('swing_lows', [])
        for swing in swing_lows:
            try:
                swing_price = float(swing)
                if low < swing_price and close > swing_price:
                    if body_size > 0 and lower_wick / body_size >= wick_threshold:
                        wick_pips = lower_wick / 0.01
                        return {
                            'inducement': True,
                            'type': 'SWEEP_BELOW_SWING',
                            'direction': 'BULLISH',
                            'level': swing_price,
                            'low': low,
                            'close': close,
                            'wick_size': wick_pips,
                            'confidence': 'MEDIUM'
                        }
            except (ValueError, TypeError):
                continue
        
        return None
    
    def _apply_session_weighting(self, inducement_data):
        """
        STEP 4: Apply session-based weighting to inducement detection.
        
        London/NY overlap = 95% reliability (VERY_HIGH)
        London session = 85% reliability (HIGH)
        NY session = 80% reliability (HIGH)
        Asian session = 60% reliability (LOW)
        """
        if not self.session_enabled or self.session_detector is None:
            # No session detector - return original data
            return inducement_data
        
        try:
            # Get current session
            session_info = self.session_detector.get_current_session()
            
            # Apply session weighting
            weighted = self.session_detector.weight_inducement(
                inducement_data.copy(),
                current_time=None  # Uses current time
            )
            
            return weighted
            
        except Exception as e:
            print(f"⚠️ Error applying session weighting: {e}")
            return inducement_data
    
    def get_inducement_summary(self, inducement_data):
        """
        Get human-readable summary of inducement detection.
        
        STEP 4 ENHANCED: Now includes session reliability
        """
        if not inducement_data.get('inducement'):
            return "No inducement detected"
        
        ind_type = inducement_data.get('type', 'UNKNOWN')
        direction = inducement_data.get('direction', 'UNKNOWN')
        level = inducement_data.get('level', 0)
        confidence = inducement_data.get('confidence', 'MEDIUM')
        wick_size = inducement_data.get('wick_size', 0)
        
        # STEP 4: Add session info if available
        session = inducement_data.get('session', 'UNKNOWN')
        session_reliability = inducement_data.get('session_reliability', 0)
        weighted_confidence = inducement_data.get('weighted_confidence', confidence)
        
        summary = f"🚨 INDUCEMENT DETECTED!\n"
        summary += f"   Type: {ind_type}\n"
        summary += f"   Direction: {direction}\n"
        summary += f"   Level swept: ${level:.2f}\n"
        summary += f"   Wick size: {wick_size:.2f} pips\n"
        summary += f"   Confidence: {confidence}"
        
        # Add session weighting if available
        if session != 'UNKNOWN':
            summary += f"\n   Session: {session} ({session_reliability*100:.0f}% reliability)\n"
            summary += f"   Weighted Confidence: {weighted_confidence}"
        
        return summary
