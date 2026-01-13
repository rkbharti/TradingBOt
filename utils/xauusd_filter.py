# utils/xauusd_filter.py
"""
Gold-Specific Trading Filters for XAUUSD
Handles unique characteristics of gold: volatility, spreads, session behavior
"""

import pandas as pd
import numpy as np


class XAUUSDFilter:
    """Professional gold trading filters"""
    
    # Session-specific ATR requirements (gold has different volatility per session)
    MINIMUM_ATR = {
        "ASIAN": 1.5,
        "LONDON": 2.0,
        "NY": 2.5,
        "NY_OVERLAP": 3.0,
        "DEFAULT": 2.0
    }
    
    # Stop loss distance (in pips) - adjusted per session
    MIN_SL_PIPS = {
        "ASIAN": 50,
        "LONDON": 35,
        "NY": 35,
        "NY_OVERLAP": 30,
        "DEFAULT": 35
    }
    
    # Spread threshold (points)
    MAX_SPREAD = {
        "ASIAN": 3.0,
        "LONDON": 2.0,
        "NY": 2.5,
        "NY_OVERLAP": 1.5,
        "DEFAULT": 2.0
    }
    
    # Risk per trade, session-adjusted
    RISK_PER_TRADE = {
        "ASIAN": 0.15,
        "LONDON": 0.25,
        "NY": 0.25,
        "NY_OVERLAP": 0.30,
        "DEFAULT": 0.25
    }

    @staticmethod
    def normalize_session_name(session):
        """Convert session names to standard format"""
        session = session.upper() if session else "DEFAULT"
        
        if "OVERLAP" in session or (session == "NY_OVERLAP"):
            return "NY_OVERLAP"
        elif "LONDON" in session:
            return "LONDON"
        elif "NEW YORK" in session or session == "NY":
            return "NY"
        elif "ASIAN" in session:
            return "ASIAN"
        else:
            return "DEFAULT"

    @staticmethod
    def is_tradeable_session(session, atr, spread):
        """
        Check if current session meets minimum trading requirements
        
        Returns:
            tuple: (is_tradeable: bool, reason: str)
        """
        session = XAUUSDFilter.normalize_session_name(session)
        
        min_atr = XAUUSDFilter.MINIMUM_ATR.get(session, XAUUSDFilter.MINIMUM_ATR["DEFAULT"])
        max_spread = XAUUSDFilter.MAX_SPREAD.get(session, XAUUSDFilter.MAX_SPREAD["DEFAULT"])
        
        if atr < min_atr:
            return False, f"Low volatility (ATR: {atr:.2f} < {min_atr})"
        
        if spread > max_spread:
            return False, f"Wide spread ({spread:.1f} > {max_spread})"
        
        return True, f"✅ {session} conditions met (ATR: {atr:.2f}, Spread: {spread:.1f})"

    @staticmethod
    def get_min_sl_pips(session):
        """Get session-appropriate stop loss distance"""
        session = XAUUSDFilter.normalize_session_name(session)
        return XAUUSDFilter.MIN_SL_PIPS.get(session, XAUUSDFilter.MIN_SL_PIPS["DEFAULT"])

    @staticmethod
    def get_max_spread(session):
        """Get max acceptable spread for session"""
        session = XAUUSDFilter.normalize_session_name(session)
        return XAUUSDFilter.MAX_SPREAD.get(session, XAUUSDFilter.MAX_SPREAD["DEFAULT"])

    @staticmethod
    def get_session_risk_percent(session):
        """Get recommended risk per trade for session"""
        session = XAUUSDFilter.normalize_session_name(session)
        return XAUUSDFilter.RISK_PER_TRADE.get(session, XAUUSDFilter.RISK_PER_TRADE["DEFAULT"])

    @staticmethod
    def detect_asian_session_weakness(historical_data):
        """
        Identify weak Asian session patterns that should be skipped
        """
        try:
            recent_bars = historical_data.tail(20)
            
            ranges = recent_bars['high'] - recent_bars['low']
            avg_range = ranges.mean()
            range_std = ranges.std()
            
            volatility_ratio = range_std / avg_range if avg_range > 0 else 0
            
            inside_bars = 0
            for i in range(1, len(recent_bars)):
                if (recent_bars.iloc[i]['high'] < recent_bars.iloc[i-1]['high'] and 
                    recent_bars.iloc[i]['low'] > recent_bars.iloc[i-1]['low']):
                    inside_bars += 1
            
            inside_bar_ratio = inside_bars / len(recent_bars)
            is_choppy = (volatility_ratio > 0.5) or (inside_bar_ratio > 0.4)
            
            return {
                'is_choppy': is_choppy,
                'volatility_ratio': round(volatility_ratio, 2),
                'inside_bar_ratio': round(inside_bar_ratio, 2),
                'avg_range': round(avg_range, 2),
                'recommendation': "🔴 SKIP - Choppy Asian session" if is_choppy else "🟢 OK to trade",
                'score': round((1 - volatility_ratio) * 100)
            }
        except Exception as e:
            return {
                'is_choppy': True,
                'recommendation': f"⚠️ UNABLE_TO_ANALYZE: {str(e)}",
                'score': 0
            }

    @staticmethod
    def detect_london_open_spike(historical_data):
        """
        London session opens with a spike - this is tradeable
        """
        try:
            recent_bars = historical_data.tail(10)
            latest_bar = recent_bars.iloc[-1]
            
            bar_range = latest_bar['high'] - latest_bar['low']
            avg_range_50 = (historical_data.tail(50)['high'] - historical_data.tail(50)['low']).mean()
            
            spike_ratio = bar_range / avg_range_50 if avg_range_50 > 0 else 1
            close_to_high = (latest_bar['close'] - latest_bar['low']) / bar_range if bar_range > 0 else 0.5
            is_directional = (close_to_high > 0.75) or (close_to_high < 0.25)
            
            is_spike = (spike_ratio > 1.8) and is_directional
            
            return {
                'london_spike': is_spike,
                'spike_ratio': round(spike_ratio, 2),
                'bar_range': round(bar_range, 2),
                'avg_range': round(avg_range_50, 2),
                'directionality': round(close_to_high, 2),
                'action': "🟢 ENTER_TREND" if is_spike else "⚠️ WAIT",
                'trend_direction': 'UP' if close_to_high > 0.75 else 'DOWN' if close_to_high < 0.25 else 'NEUTRAL'
            }
        except Exception as e:
            return {
                'london_spike': False,
                'action': f"⚠️ UNABLE_TO_ANALYZE: {str(e)}"
            }

    @staticmethod
    def get_session_quality_score(session, atr, spread, choppy_score=None):
        """
        Overall quality score for current trading session
        Returns 0-100 score where 100 = perfect conditions
        """
        session = XAUUSDFilter.normalize_session_name(session)
        score = 50
        
        min_atr = XAUUSDFilter.MINIMUM_ATR.get(session, 2.0)
        ideal_atr = min_atr * 1.5
        
        if atr >= ideal_atr:
            score += 25
        elif atr >= min_atr:
            score += 10
        else:
            score -= 25
        
        max_spread = XAUUSDFilter.MAX_SPREAD.get(session, 2.0)
        ideal_spread = max_spread * 0.5
        
        if spread <= ideal_spread:
            score += 20
        elif spread <= max_spread:
            score += 5
        else:
            score -= 20
        
        if session == "NY_OVERLAP":
            score += 15
        elif session in ["LONDON", "NY"]:
            score += 10
        elif session == "ASIAN":
            score -= 15
        
        if choppy_score is not None:
            score = score * 0.7 + choppy_score * 0.3
        
        return max(0, min(100, int(score)))
