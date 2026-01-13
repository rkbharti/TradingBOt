# utils/volume_analyzer_gold.py
"""
Gold-Specific Volume Analysis
Detects smart money accumulation, volume spikes, and confirmation patterns
"""

import pandas as pd
import numpy as np


class GoldVolumeAnalyzer:
    """Advanced volume analysis specifically for XAUUSD trading"""
    
    @staticmethod
    def detect_accumulation_distribution(df, lookback=20):
        """
        Detects when smart money is accumulating
        - Buy signal: A/D rising, price falling (accumulation)
        - Sell signal: A/D falling, price rising (distribution)
        """
        try:
            df = df.copy()
            
            if 'tick_volume' in df.columns:
                df['volume'] = df['tick_volume']
            elif 'real_volume' in df.columns:
                df['volume'] = df['real_volume']
            else:
                df['volume'] = 1
            
            high_low = df['high'] - df['low']
            close_low = df['close'] - df['low']
            high_close = df['high'] - df['close']
            
            df['mfm'] = (close_low - high_close) / high_low
            df['mfm'] = df['mfm'].replace([np.inf, -np.inf], 0).fillna(0)
            df['mfv'] = df['mfm'] * df['volume']
            df['ad_line'] = df['mfv'].cumsum()
            
            recent = df.tail(lookback)
            recent_ad_idx = len(recent) - 1
            past_ad_idx = max(0, len(recent) - 6)
            
            latest_ad_trend = recent['ad_line'].iloc[-1] - recent['ad_line'].iloc[past_ad_idx]
            latest_price = recent['close'].iloc[-1]
            past_price = recent['close'].iloc[past_ad_idx]
            latest_price_trend = latest_price - past_price
            
            price_change_pct = (latest_price_trend / past_price * 100) if past_price != 0 else 0
            
            signal = 'HOLD'
            confidence = 0
            
            if latest_ad_trend > 0 and latest_price_trend < 0:
                signal = 'BUY'
                divergence_strength = abs(latest_ad_trend) / (abs(latest_ad_trend) + abs(latest_price_trend))
                confidence = min(100, int(divergence_strength * 150))
            
            elif latest_ad_trend < 0 and latest_price_trend > 0:
                signal = 'SELL'
                divergence_strength = abs(latest_ad_trend) / (abs(latest_ad_trend) + abs(latest_price_trend))
                confidence = min(100, int(divergence_strength * 150))
            
            else:
                if latest_ad_trend > 0:
                    signal = 'BUY'
                    confidence = min(50, int(latest_ad_trend / 1000))
                elif latest_ad_trend < 0:
                    signal = 'SELL'
                    confidence = min(50, int(abs(latest_ad_trend) / 1000))
            
            return {
                'signal': signal,
                'confidence': confidence,
                'ad_line': float(recent['ad_line'].iloc[-1]),
                'ad_trend': float(latest_ad_trend),
                'price_trend': float(latest_price_trend),
                'price_change_pct': round(price_change_pct, 2),
                'divergence_detected': signal != 'HOLD',
                'type': 'ACCUMULATION' if latest_ad_trend > 0 else 'DISTRIBUTION' if latest_ad_trend < 0 else 'NEUTRAL'
            }
        
        except Exception as e:
            return {
                'signal': 'HOLD',
                'confidence': 0,
                'error': str(e)
            }

    @staticmethod
    def detect_volume_breakout(df, lookback=20, threshold_multiplier=2.0):
        """
        Detects volume breakouts (volume >> average)
        Indicates smart money entering positions with force
        """
        try:
            recent = df.tail(lookback)
            
            if 'tick_volume' in df.columns:
                volume_col = 'tick_volume'
            elif 'real_volume' in df.columns:
                volume_col = 'real_volume'
            else:
                return {'breakout': False, 'ratio': 0, 'signal': 'HOLD'}
            
            current_volume = float(recent[volume_col].iloc[-1])
            avg_volume = float(recent[volume_col].iloc[:-1].mean())
            
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            is_breakout = volume_ratio > threshold_multiplier
            
            current_close = df['close'].iloc[-1]
            previous_close = df['close'].iloc[-2] if len(df) > 1 else current_close
            
            direction = 'UP' if current_close > previous_close else 'DOWN' if current_close < previous_close else 'NEUTRAL'
            
            if volume_ratio > 3.0:
                confidence = 'VERY_STRONG'
                signal_weight = 1.0
            elif volume_ratio > 2.5:
                confidence = 'STRONG'
                signal_weight = 0.8
            elif volume_ratio > threshold_multiplier:
                confidence = 'MODERATE'
                signal_weight = 0.6
            else:
                confidence = 'NONE'
                signal_weight = 0
            
            return {
                'breakout': is_breakout,
                'ratio': round(volume_ratio, 2),
                'current': int(current_volume),
                'average': int(avg_volume),
                'direction': direction,
                'confidence': confidence,
                'signal': direction if is_breakout else 'HOLD',
                'signal_weight': signal_weight,
                'interpretation': f"Volume {int(volume_ratio)}x normal - {direction} breakout"
            }
        except Exception as e:
            return {
                'breakout': False,
                'ratio': 0,
                'signal': 'HOLD',
                'error': str(e)
            }

    @staticmethod
    def confirm_fvg_with_volume(df, lookback=5, volume_threshold=1.5):
        """
        FVGs are more reliable if confirmed by volume spike
        """
        try:
            recent = df.tail(lookback)
            
            if 'tick_volume' in df.columns:
                volume_col = 'tick_volume'
            elif 'real_volume' in df.columns:
                volume_col = 'real_volume'
            else:
                return {'fvg_confirmed': False, 'volume_confirmation': 'UNKNOWN'}
            
            recent_volumes = recent[volume_col].values
            avg_volume_past = df[volume_col].iloc[:-lookback].mean() if len(df) > lookback else recent_volumes.mean()
            
            fvg_volume = recent_volumes[-1]
            volume_ratio = fvg_volume / avg_volume_past if avg_volume_past > 0 else 1
            
            is_confirmed = volume_ratio > volume_threshold
            
            return {
                'fvg_confirmed': is_confirmed,
                'volume_ratio': round(volume_ratio, 2),
                'volume_confirmation': "✅ YES" if is_confirmed else "❌ WEAK",
                'strength': "HIGH" if volume_ratio > 2.0 else "MEDIUM" if is_confirmed else "LOW",
                'trade_weight': min(1.0, volume_ratio / 2.0)
            }
        except Exception as e:
            return {
                'fvg_confirmed': False,
                'volume_confirmation': 'ERROR',
                'error': str(e)
            }

    @staticmethod
    def detect_volume_dry_up(df, lookback=10):
        """
        Detects when volume dries up (market losing interest)
        Useful for: Exit signals, trend exhaustion detection
        """
        try:
            recent = df.tail(lookback)
            
            if 'tick_volume' in df.columns:
                volume_col = 'tick_volume'
            elif 'real_volume' in df.columns:
                volume_col = 'real_volume'
            else:
                return {'dry_up': False}
            
            volumes = recent[volume_col].values
            
            declining_bars = 0
            for i in range(len(volumes)-1, 0, -1):
                if volumes[i] < volumes[i-1]:
                    declining_bars += 1
                else:
                    break
            
            is_drying_up = declining_bars >= 3
            
            recent_avg_vol = np.mean(volumes[-5:])
            historical_avg_vol = df[volume_col].iloc[:-lookback].mean() if len(df) > lookback else recent_avg_vol
            
            volume_decline = (historical_avg_vol - recent_avg_vol) / historical_avg_vol if historical_avg_vol > 0 else 0
            is_significantly_lower = volume_decline > 0.3
            
            return {
                'dry_up': is_drying_up or is_significantly_lower,
                'declining_bars': declining_bars,
                'volume_decline_percent': round(volume_decline * 100, 1),
                'action': '⚠️ TREND EXHAUSTION - Consider Exit' if (is_drying_up or is_significantly_lower) else '✅ Volume Healthy'
            }
        except:
            return {'dry_up': False}
