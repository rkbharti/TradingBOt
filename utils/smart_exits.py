# utils/smart_exits.py
"""
Professional Exit Strategies for XAUUSD Trading Bot
Implements multi-level exits, partial profit taking, and trailing logic
"""

import pandas as pd
import numpy as np
from datetime import datetime


class SmartExitManager:
    """Advanced exit strategies for maximum profitability"""

    @staticmethod
    def calculate_partial_targets(entry_price, stop_loss, signal_type='BUY', lot_size=1.0):
        """
        3-level exit strategy:
        Level 1: Close 50% at 1R
        Level 2: Close 25% at 2R
        Level 3: Trail remaining 25%
        """
        risk = abs(entry_price - stop_loss)
        
        if signal_type == 'BUY':
            target_1r = entry_price + risk
            target_2r = entry_price + (risk * 2)
            target_3r = entry_price + (risk * 3)
        else:
            target_1r = entry_price - risk
            target_2r = entry_price - (risk * 2)
            target_3r = entry_price - (risk * 3)
        
        targets = {
            'level_1': {
                'price': round(target_1r, 2),
                'volume_close': round(lot_size * 0.50, 2),
                'percent_of_position': 50,
                'description': '1R - Secure 50% profit',
                'trailing_stop': entry_price,
                'pips_to_target': abs(target_1r - entry_price) / 0.01
            },
            'level_2': {
                'price': round(target_2r, 2),
                'volume_close': round(lot_size * 0.25, 2),
                'percent_of_position': 25,
                'description': '2R - Secure 25% profit',
                'trailing_stop': target_1r,
                'pips_to_target': abs(target_2r - entry_price) / 0.01
            },
            'level_3': {
                'price': target_3r,
                'volume_close': round(lot_size * 0.25, 2),
                'percent_of_position': 25,
                'description': '3R+ - Trail with breakeven stop',
                'trailing_stop': target_2r,
                'pips_to_target': abs(target_3r - entry_price) / 0.01
            },
            'summary': {
                'total_risk': risk,
                'total_potential_reward': risk * 3,
                'reward_risk_ratio': 3.0,
                'entry': entry_price,
                'stop_loss': stop_loss
            }
        }
        
        return targets

    @staticmethod
    def should_close_partial(current_price, entry_price, stop_loss, signal_type='BUY', level=1):
        """Check if current price has reached a partial profit level"""
        targets = SmartExitManager.calculate_partial_targets(entry_price, stop_loss, signal_type)
        
        if level == 1:
            target = targets['level_1']['price']
        elif level == 2:
            target = targets['level_2']['price']
        else:
            target = targets['level_3']['price']
        
        if signal_type == 'BUY':
            return current_price >= target
        else:
            return current_price <= target

    @staticmethod
    def detect_divergence_exit(df, signal_type='BUY', lookback=20):
        """
        Exit when price makes new high but momentum doesn't (divergence)
        """
        try:
            recent = df.tail(lookback)
            
            delta = recent['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            
            avg_gain = gain.rolling(window=14).mean()
            avg_loss = loss.rolling(window=14).mean()
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            current_price = recent['close'].iloc[-1]
            current_rsi = rsi.iloc[-1]
            
            divergence_detected = False
            divergence_type = 'NONE'
            strength = 0
            
            if signal_type == 'BUY':
                price_high_idx = recent['close'].idxmax()
                rsi_high_idx = rsi.idxmax()
                
                price_high_bar = recent.index.get_loc(price_high_idx) if price_high_idx in recent.index else len(recent) - 1
                rsi_high_bar = rsi.index.get_loc(rsi_high_idx) if rsi_high_idx in rsi.index else len(rsi) - 1
                
                if price_high_bar > rsi_high_bar:
                    divergence_detected = True
                    divergence_type = 'BEARISH'
                    strength = min(100, int((100 - current_rsi)))
            
            else:
                price_low_idx = recent['close'].idxmin()
                rsi_low_idx = rsi.idxmin()
                
                price_low_bar = recent.index.get_loc(price_low_idx) if price_low_idx in recent.index else len(recent) - 1
                rsi_low_bar = rsi.index.get_loc(rsi_low_idx) if rsi_low_idx in rsi.index else len(rsi) - 1
                
                if price_low_bar > rsi_low_bar:
                    divergence_detected = True
                    divergence_type = 'BULLISH'
                    strength = min(100, int(current_rsi - 30))
            
            return {
                'divergence_detected': divergence_detected,
                'type': divergence_type,
                'strength': max(0, min(100, strength)),
                'current_rsi': round(current_rsi, 2) if not pd.isna(current_rsi) else 50,
                'rsi_overbought': current_rsi > 70 if not pd.isna(current_rsi) else False,
                'rsi_oversold': current_rsi < 30 if not pd.isna(current_rsi) else False,
                'action': f'✅ EXIT SIGNAL' if divergence_detected else '⏸️ No divergence',
                'confidence': 'HIGH' if strength > 70 else 'MEDIUM' if strength > 50 else 'LOW'
            }
        except Exception as e:
            return {
                'divergence_detected': False,
                'error': str(e)
            }

    @staticmethod
    def detect_support_resistance_exit(current_price, entry_price, support, resistance, tolerance=0.20):
        """Exit if price hits support/resistance"""
        hit_support = abs(current_price - support) < tolerance
        hit_resistance = abs(current_price - resistance) < tolerance
        
        if current_price > entry_price:
            exit_reason = 'At Resistance'
        else:
            exit_reason = 'At Support'
        
        should_exit = hit_support or hit_resistance
        
        return {
            'hit_support': hit_support,
            'hit_resistance': hit_resistance,
            'exit_signal': should_exit,
            'exit_reason': exit_reason if should_exit else 'None',
            'distance_to_support': abs(current_price - support),
            'distance_to_resistance': abs(current_price - resistance)
        }

    @staticmethod
    def calculate_trailing_stop(current_price, entry_price, signal_type='BUY', trail_percent=0.5):
        """Calculates trailing stop level"""
        if signal_type == 'BUY':
            profit = current_price - entry_price
            trailing_stop = current_price - (profit * trail_percent)
        else:
            profit = entry_price - current_price
            trailing_stop = current_price + (profit * trail_percent)
        
        return {
            'trailing_stop': round(trailing_stop, 2),
            'current_price': current_price,
            'profit': round(abs(current_price - entry_price), 2),
            'trail_amount': round(abs(profit * trail_percent), 2),
            'description': f'Trail {int(trail_percent*100)}% of gains'
        }
