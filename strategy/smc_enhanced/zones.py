"""
Zone Calculator Module
Guardeer VIDEO 10: Premium/Discount/Equilibrium Zones

Key Concepts:
- Premium zone: Above 0.5 Fib (SELL only)
- Discount zone: Below 0.5 Fib (BUY only)
- Equilibrium: At 0.5 Fib (AVOID)

From Guardeer: "Premium/Discount concept separates retail from institutional traders"
"""

import pandas as pd
import numpy as np


class ZoneCalculator:
    """
    Premium/Discount/Equilibrium Zone Calculation
    
    From Guardeer VIDEO 10:
    - Premium zone: Above 0.5 Fib (SELL only)
    - Discount zone: Below 0.5 Fib (BUY only)
    - Equilibrium: At 0.5 Fib (AVOID)
    
    This is what separates institutional traders from retail traders.
    """
    
    @staticmethod
    def calculate_zones(swing_high, swing_low, buffer_percent=2):
        """
        Calculate Premium, Discount, and Equilibrium zones.
        
        Using Fibonacci 0.5 as the midpoint divider.
        
        Args:
            swing_high: Highest price in the swing
            swing_low: Lowest price in the swing
            buffer_percent: Buffer around equilibrium to avoid (default 2%)
        
        Returns:
            dict with zone information
        """
        try:
            swing_high = float(swing_high)
            swing_low = float(swing_low)
            
            range_size = swing_high - swing_low
            
            if range_size <= 0:
                return None
            
            # Equilibrium point (Fibonacci 0.5)
            equilibrium = (swing_high + swing_low) / 2
            equilibrium_buffer = range_size * (buffer_percent / 100)
            
            zones = {
                'swing_high': swing_high,
                'swing_low': swing_low,
                'range': range_size,
                'equilibrium': equilibrium,
                'equilibrium_buffer': equilibrium_buffer,
                'equilibrium_upper': equilibrium + equilibrium_buffer,
                'equilibrium_lower': equilibrium - equilibrium_buffer,
                
                # Premium zone: Above 0.5
                'premium_start': equilibrium + equilibrium_buffer,
                'premium_end': swing_high,
                'premium_range': swing_high - (equilibrium + equilibrium_buffer),
                'premium_mid': (swing_high + (equilibrium + equilibrium_buffer)) / 2,
                
                # Discount zone: Below 0.5
                'discount_start': swing_low,
                'discount_end': equilibrium - equilibrium_buffer,
                'discount_range': (equilibrium - equilibrium_buffer) - swing_low,
                'discount_mid': ((equilibrium - equilibrium_buffer) + swing_low) / 2,
                
                # Fibonacci levels (extended)
                'fib_0_382': swing_low + (range_size * 0.382),
                'fib_0_5': equilibrium,
                'fib_0_618': swing_low + (range_size * 0.618),
                'fib_0_786': swing_low + (range_size * 0.786),
            }
            
            return zones
            
        except Exception as e:
            print(f"⚠️ Error calculating zones: {e}")
            return None
    
    @staticmethod
    def classify_price_zone(current_price, zones):
        """
        Classify current price into zone.
        
        Returns: 'PREMIUM', 'DISCOUNT', 'EQUILIBRIUM', or 'UNKNOWN'
        """
        if zones is None:
            return 'UNKNOWN'
        
        try:
            current_price = float(current_price)
            
            eq_upper = zones['equilibrium_upper']
            eq_lower = zones['equilibrium_lower']
            premium_start = zones['premium_start']
            discount_end = zones['discount_end']
            
            # Within equilibrium buffer
            if eq_lower <= current_price <= eq_upper:
                return 'EQUILIBRIUM'
            
            # Above equilibrium = Premium
            elif current_price > eq_upper:
                return 'PREMIUM'
            
            # Below equilibrium = Discount
            elif current_price < eq_lower:
                return 'DISCOUNT'
            
            return 'UNKNOWN'
            
        except Exception as e:
            print(f"⚠️ Error classifying price zone: {e}")
            return 'UNKNOWN'
    
    @staticmethod
    def get_distance_from_zone(current_price, zones, zone_type='equilibrium'):
        """
        Get distance of price from specific zone.
        
        Returns: distance in pips (positive = above zone, negative = below)
        """
        if zones is None:
            return 0
        
        try:
            current_price = float(current_price)
            
            if zone_type == 'equilibrium':
                return current_price - zones['equilibrium']
            elif zone_type == 'premium_mid':
                return current_price - zones['premium_mid']
            elif zone_type == 'discount_mid':
                return current_price - zones['discount_mid']
            
            return 0
            
        except:
            return 0
    
    @staticmethod
    def can_execute_trade(signal, current_zone):
        """
        Determine if trade should execute based on zone alignment.
        
        From Guardeer: This is the FILTER that separates winners from losers
        
        Args:
            signal: 'BUY' or 'SELL'
            current_zone: 'PREMIUM', 'DISCOUNT', or 'EQUILIBRIUM'
        
        Returns:
            bool: True if trade should execute
        """
        if signal == 'BUY':
            # BUY only in DISCOUNT zone
            # Avoid EQUILIBRIUM and PREMIUM
            return current_zone == 'DISCOUNT'
        
        elif signal == 'SELL':
            # SELL only in PREMIUM zone
            # Avoid EQUILIBRIUM and DISCOUNT
            return current_zone == 'PREMIUM'
        
        return False
    
    @staticmethod
    def get_zone_strength(current_price, zones):
        """
        Get how strong the current zone is (how deep in the zone).
        
        Returns: percentage (0-100) of how deep in zone
        """
        if zones is None:
            return 0
        
        try:
            current_price = float(current_price)
            
            # If in premium
            if current_price > zones['premium_start']:
                premium_depth = current_price - zones['premium_start']
                premium_range = zones['premium_range']
                if premium_range > 0:
                    strength = min(100, (premium_depth / premium_range) * 100)
                    return strength
            
            # If in discount
            elif current_price < zones['discount_end']:
                discount_depth = zones['discount_end'] - current_price
                discount_range = zones['discount_range']
                if discount_range > 0:
                    strength = min(100, (discount_depth / discount_range) * 100)
                    return strength
            
            return 0
            
        except:
            return 0
    
    @staticmethod
    def get_next_zone_target(current_price, zones, direction='UP'):
        """
        Get next zone target price based on direction.
        
        Args:
            current_price: Current price
            zones: Zone information
            direction: 'UP' or 'DOWN'
        
        Returns:
            dict with target information
        """
        if zones is None:
            return None
        
        try:
            current_price = float(current_price)
            
            if direction == 'UP':
                # Next targets upward
                targets = []
                
                if current_price < zones['fib_0_382']:
                    targets.append(('FIB_0.382', zones['fib_0_382']))
                if current_price < zones['fib_0_5']:
                    targets.append(('EQUILIBRIUM', zones['fib_0_5']))
                if current_price < zones['fib_0_618']:
                    targets.append(('FIB_0.618', zones['fib_0_618']))
                if current_price < zones['fib_0_786']:
                    targets.append(('FIB_0.786', zones['fib_0_786']))
                if current_price < zones['swing_high']:
                    targets.append(('SWING_HIGH', zones['swing_high']))
                
                if targets:
                    next_target = min(targets, key=lambda x: x[1])
                    return {
                        'target': next_target[0],
                        'price': next_target[1],
                        'distance': next_target[1] - current_price
                    }
            
            elif direction == 'DOWN':
                # Next targets downward
                targets = []
                
                if current_price > zones['fib_0_786']:
                    targets.append(('FIB_0.786', zones['fib_0_786']))
                if current_price > zones['fib_0_618']:
                    targets.append(('FIB_0.618', zones['fib_0_618']))
                if current_price > zones['fib_0_5']:
                    targets.append(('EQUILIBRIUM', zones['fib_0_5']))
                if current_price > zones['fib_0_382']:
                    targets.append(('FIB_0.382', zones['fib_0_382']))
                if current_price > zones['swing_low']:
                    targets.append(('SWING_LOW', zones['swing_low']))
                
                if targets:
                    next_target = max(targets, key=lambda x: x[1])
                    return {
                        'target': next_target[0],
                        'price': next_target[1],
                        'distance': current_price - next_target[1]
                    }
            
            return None
            
        except Exception as e:
            print(f"⚠️ Error getting next zone target: {e}")
            return None
    
    @staticmethod
    def get_zone_summary(current_price, zones):
        """Get comprehensive zone analysis with safe formatting"""
        try:
            if not zones or not isinstance(zones, dict):
                return None
            
            # Safely get zone values with defaults
            eq = zones.get('equilibrium', current_price)
            premium_start = zones.get('premium_start', eq)
            discount_end = zones.get('discount_end', eq)
            swing_high = zones.get('swing_high', premium_start)
            swing_low = zones.get('swing_low', discount_end)
            
            # Calculate distance from equilibrium
            distance_from_eq = current_price - eq
            eq_range = swing_high - swing_low
            
            if eq_range == 0:
                zone_strength = 0
            else:
                zone_strength = abs(distance_from_eq / eq_range) * 100
            
            # Determine current zone
            if current_price > premium_start:
                current_zone = "PREMIUM"
                can_buy = False
                can_sell = True
                next_target = discount_end
            elif current_price < discount_end:
                current_zone = "DISCOUNT"
                can_buy = True
                can_sell = False
                next_target = premium_start
            else:
                current_zone = "EQUILIBRIUM"
                can_buy = False
                can_sell = False
                next_target = eq
            
            return {
                'current_zone': current_zone,
                'zone_strength': min(zone_strength, 100),  # Cap at 100%
                'distance_from_equilibrium': distance_from_eq,
                'can_buy': can_buy,
                'can_sell': can_sell,
                'next_target': float(next_target),  # ✅ Ensure it's a float
                'equilibrium': float(eq)
            }
            
        except Exception as e:
            print(f"   ⚠️  Zone summary error: {e}")
            return None
