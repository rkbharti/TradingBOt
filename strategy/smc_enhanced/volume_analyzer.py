"""
Volume Analysis Module
Guardeer Concept: Volume Confirmation for POI and Inducement

Key Concepts:
- Volume spike at order block = high probability reversal
- Volume divergence = trend exhaustion
- Volume confirmation for inducement sweeps
"""

import pandas as pd
import numpy as np


class VolumeAnalyzer:
    """
    Analyze volume patterns for trade confirmation.
    
    From Trading Best Practices:
    - Volume spike (>2x average) at POI = strong reversal signal
    - Decreasing volume in trend = exhaustion (divergence)
    - High volume on inducement sweep = institutional trap
    """
    
    def __init__(self, df):
        self.df = df
        self.volume_threshold = 2.0  # 2x average volume = spike
    
    def detect_volume_spike(self, lookback=20):
        """
        Detect if current volume is significantly higher than average.
        
        Returns:
            dict with spike info or {'spike': False}
        """
        try:
            if len(self.df) < lookback + 1:
                return {'spike': False, 'ratio': 0}
            
            recent = self.df.tail(lookback + 1)
            
            # Get current volume (last candle)
            try:
                current_volume = float(recent.iloc[-1]['tick_volume'])
            except (ValueError, TypeError, KeyError):
                return {'spike': False, 'ratio': 0}
            
            # Calculate average volume (excluding current)
            volumes = []
            for i in range(len(recent) - 1):
                try:
                    vol = float(recent.iloc[i]['tick_volume'])
                    if vol > 0:
                        volumes.append(vol)
                except (ValueError, TypeError, KeyError):
                    continue
            
            if not volumes:
                return {'spike': False, 'ratio': 0}
            
            avg_volume = np.mean(volumes)
            
            if avg_volume == 0:
                return {'spike': False, 'ratio': 0}
            
            volume_ratio = current_volume / avg_volume
            
            # Volume spike = current > threshold * average
            if volume_ratio >= self.volume_threshold:
                return {
                    'spike': True,
                    'ratio': volume_ratio,
                    'current_volume': current_volume,
                    'avg_volume': avg_volume,
                    'strength': 'STRONG' if volume_ratio >= 3.0 else 'MEDIUM'
                }
            
            return {
                'spike': False,
                'ratio': volume_ratio,
                'current_volume': current_volume,
                'avg_volume': avg_volume
            }
            
        except Exception as e:
            print(f"⚠️ Error detecting volume spike: {e}")
            return {'spike': False, 'ratio': 0}
    
    def detect_volume_divergence(self, lookback=10):
        """
        Detect volume divergence (price up, volume down = bearish; price down, volume up = bullish).
        
        Returns:
            'BULLISH_DIVERGENCE', 'BEARISH_DIVERGENCE', or 'NONE'
        """
        try:
            if len(self.df) < lookback:
                return 'NONE'
            
            recent = self.df.tail(lookback)
            
            # Calculate price trend
            try:
                first_close = float(recent.iloc[0]['close'])
                last_close = float(recent.iloc[-1]['close'])
                price_change = last_close - first_close
            except (ValueError, TypeError, KeyError):
                return 'NONE'
            
            # Calculate volume trend
            volumes = []
            for i in range(len(recent)):
                try:
                    vol = float(recent.iloc[i]['tick_volume'])
                    volumes.append(vol)
                except (ValueError, TypeError, KeyError):
                    continue
            
            if len(volumes) < 5:
                return 'NONE'
            
            # Simple volume trend: compare first half vs second half
            mid = len(volumes) // 2
            first_half_avg = np.mean(volumes[:mid])
            second_half_avg = np.mean(volumes[mid:])
            
            volume_decreasing = second_half_avg < first_half_avg * 0.8  # 20% decrease
            volume_increasing = second_half_avg > first_half_avg * 1.2  # 20% increase
            
            # Divergence detection
            if price_change > 0 and volume_decreasing:
                return 'BEARISH_DIVERGENCE'  # Price up, volume down = exhaustion
            elif price_change < 0 and volume_increasing:
                return 'BULLISH_DIVERGENCE'  # Price down, volume up = accumulation
            else:
                return 'NONE'
            
        except Exception as e:
            print(f"⚠️ Error detecting divergence: {e}")
            return 'NONE'
    
    def get_volume_confirmation(self, signal_type='BUY'):
        """
        Get volume confirmation for a trade signal.
        
        Args:
            signal_type: 'BUY' or 'SELL'
        
        Returns:
            dict with confirmation status and confidence boost
        """
        try:
            spike_data = self.detect_volume_spike(lookback=20)
            divergence = self.detect_volume_divergence(lookback=10)
            
            confirmed = False
            confidence_boost = 0
            reasons = []
            
            # Check volume spike
            if spike_data.get('spike'):
                confirmed = True
                confidence_boost += 15
                reasons.append(f"Volume spike ({spike_data.get('ratio', 0):.1f}x)")
            
            # Check divergence alignment
            if signal_type == 'BUY' and divergence == 'BULLISH_DIVERGENCE':
                confirmed = True
                confidence_boost += 10
                reasons.append("Bullish volume divergence")
            elif signal_type == 'SELL' and divergence == 'BEARISH_DIVERGENCE':
                confirmed = True
                confidence_boost += 10
                reasons.append("Bearish volume divergence")
            
            return {
                'confirmed': confirmed,
                'confidence_boost': confidence_boost,
                'reasons': reasons,
                'spike_data': spike_data,
                'divergence': divergence
            }
            
        except Exception as e:
            print(f"⚠️ Error getting volume confirmation: {e}")
            return {
                'confirmed': False,
                'confidence_boost': 0,
                'reasons': [],
                'spike_data': {'spike': False},
                'divergence': 'NONE'
            }
