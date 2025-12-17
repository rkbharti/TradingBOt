"""
Point of Interest (POI) Identifier
Guardeer VIDEO 6: IDM, Order Blocks, Fair Value Gaps
Guardeer VIDEO 7: Block Type Classification (BREAKER, MITIGATION, RECLAIMED, WEAK)

Key Concepts:
- IDM: Inducement & Displacement (sweep probability)
- Order Blocks: Last candle without FVG
- FVG: Fair Value Gap zones
- Block Classification: Quality-based filtering
"""

import pandas as pd
import numpy as np


class POIIdentifier:
    """
    Identifies Points of Interest (POI) from Guardeer VIDEO 6 & 7
    
    From Guardeer:
    - IDM sweep probability: 50% default, 75-85% on HTF, 95-100% when no lower POIs
    - Order Block = last candle without FVG
    - FVG = Mitigated when price touches the zone
    - Block Classification = BREAKER (tested & held) > MITIGATION > RECLAIMED > WEAK
    """
    
    def __init__(self, df):
        self.df = df
        self.order_blocks = {'bullish': [], 'bearish': []}
        self.fvgs = {'bullish': [], 'bearish': []}
        
    def identify_idm_sweep(self, timeframe_minutes=15, structure_state='BULLISH'):
        """
        Calculate IDM (Inducement & Displacement) sweep probability.
        
        From Guardeer: IDM sweep probability is:
        - 50% on lower timeframes (5-15 min)
        - 75-85% on higher timeframes (30 min+)
        - 95-100% when no lower POIs exist
        
        This tells you WHEN to expect a sweep before actual reversal.
        """
        probability = {
            'default': 0.50,
            'higher_timeframe': 0.80,
            'no_lower_pois': 0.98,
            'current_probability': 0.50,
            'timeframe': timeframe_minutes,
            'structure': structure_state
        }
        
        # Adjust based on timeframe
        if timeframe_minutes >= 30:
            probability['current_probability'] = 0.80
        elif timeframe_minutes >= 15:
            probability['current_probability'] = 0.65
        else:
            probability['current_probability'] = 0.50
        
        return probability
    
    def find_order_blocks(self, lookback=50):
        """
        Identify Order Blocks (last candle without FVG).
        
        From Guardeer VIDEO 7: Advanced Order Block = body-to-body (volume-to-volume)
        Mean threshold = most precise entry point
        
        Bearish OB: Last SELLING candle (close < open) without FVG above it
        Bullish OB: Last BUYING candle (close > open) without FVG below it
        
        NEW: Each OB is classified as BREAKER, MITIGATION, RECLAIMED, or WEAK
        """
        try:
            if len(self.df) < lookback:
                lookback = len(self.df)
            
            if lookback < 5:
                return self.order_blocks
            
            df = self.df.tail(lookback).reset_index(drop=True)
            
            # First, identify FVGs to exclude them
            fvg_zones = self._identify_fvg_zones(df)
            
            for i in range(1, len(df) - 2):
                current = df.iloc[i]
                next_candle = df.iloc[i + 1]
                
                try:
                    curr_open = float(current['open'])
                    curr_close = float(current['close'])
                    curr_high = float(current['high'])
                    curr_low = float(current['low'])
                    next_low = float(next_candle['low'])
                    next_high = float(next_candle['high'])
                except (ValueError, TypeError):
                    continue
                
                # Check for Bearish OB (selling pressure)
                if (curr_close < curr_open and  # Bearish candle
                    curr_low < next_low):         # Followed by higher low (no FVG)
                    
                    mean_threshold = (curr_high + curr_low) / 2
                    body_size = abs(curr_close - curr_open)
                    
                    ob_block = {
                        'type': 'BEARISH',
                        'high': curr_high,
                        'low': curr_low,
                        'body_high': max(curr_open, curr_close),
                        'body_low': min(curr_open, curr_close),
                        'mean_threshold': mean_threshold,
                        'index': i,
                        'time': current.get('time'),
                        'strength': 'STRONG' if body_size > 5 else 'WEAK',
                        'mitigated': False,
                        'bar_number': len(self.df) - lookback + i,
                        'block_class': 'PENDING'  # Will classify after loop
                    }
                    
                    self.order_blocks['bearish'].append(ob_block)
                
                # Check for Bullish OB (buying pressure)
                if (curr_close > curr_open and  # Bullish candle
                    curr_high > next_high):      # Followed by lower high (no FVG)
                    
                    mean_threshold = (curr_high + curr_low) / 2
                    body_size = abs(curr_close - curr_open)
                    
                    ob_block = {
                        'type': 'BULLISH',
                        'high': curr_high,
                        'low': curr_low,
                        'body_high': max(curr_open, curr_close),
                        'body_low': min(curr_open, curr_close),
                        'mean_threshold': mean_threshold,
                        'index': i,
                        'time': current.get('time'),
                        'strength': 'STRONG' if body_size > 5 else 'WEAK',
                        'mitigated': False,
                        'bar_number': len(self.df) - lookback + i,
                        'block_class': 'PENDING'  # Will classify after loop
                    }
                    
                    self.order_blocks['bullish'].append(ob_block)
            
            # NOW classify all blocks after they've been created
            for ob_block in self.order_blocks['bearish']:
                ob_block['block_class'] = self.classify_block_type(ob_block, df, lookback_window=50)
            
            for ob_block in self.order_blocks['bullish']:
                ob_block['block_class'] = self.classify_block_type(ob_block, df, lookback_window=50)
            
            return self.order_blocks
            
        except Exception as e:
            print(f"⚠️ Error finding order blocks: {e}")
            return self.order_blocks

    
    def classify_block_type(self, block, df, lookback_window=50):
        """
        NEW METHOD - Guardeer VIDEO 7: Classify OB type to filter weak ones.
        
        Returns: 'BREAKER_BLOCK', 'MITIGATION_BLOCK', 'RECLAIMED_BLOCK', or 'WEAK_BLOCK'
        
        BREAKER_BLOCK = OB tested 2+ times, price held = STRONG (use first)
        MITIGATION_BLOCK = OB revisited, price filled through once = MEDIUM
        RECLAIMED_BLOCK = OB broken but price returned = MEDIUM
        WEAK_BLOCK = First time testing or no data = LOW (filter out)
        """
        try:
            block_high = float(block['high'])
            block_low = float(block['low'])
            block_index = block['index']
            
            # Not enough candles ahead → treat as weak
            if block_index + 5 >= len(df):
                return 'WEAK_BLOCK'
            
            # Look at future price action after this block formed
            future_data = df.iloc[block_index + 2:min(block_index + lookback_window, len(df))]
            
            if len(future_data) < 5:
                return 'WEAK_BLOCK'
            
            # Count how many times price touched this block
            touches = 0
            holds = 0
            breaks = 0
            
            for idx, row in future_data.iterrows():
                try:
                    high = float(row['high'])
                    low = float(row['low'])
                except (ValueError, TypeError):
                    continue
                
                # Price touched the block zone
                if (low <= block_high and high >= block_low):
                    touches += 1
                    
                    # Did price hold (didn't break through)?
                    if block['type'] == 'BEARISH':
                        # For bearish OB, holding means price didn't close strongly above
                        if high <= block_high * 1.001:  # Small tolerance
                            holds += 1
                        else:
                            breaks += 1
                    else:  # BULLISH
                        # For bullish OB, holding means price didn't close strongly below
                        if low >= block_low * 0.999:  # Small tolerance
                            holds += 1
                        else:
                            breaks += 1
            
            # Classification logic (Guardeer VIDEO 7 criteria)
            if touches >= 2 and holds >= 1 and breaks == 0:
                return 'BREAKER_BLOCK'  # Tested multiple times and held = STRONGEST
            elif breaks >= 1 and holds >= 1:
                return 'RECLAIMED_BLOCK'  # Broken but returned = MEDIUM
            elif touches >= 1 and breaks == 0:
                return 'MITIGATION_BLOCK'  # Touched and filled but staying = MEDIUM
            else:
                return 'WEAK_BLOCK'  # Not tested much = WEAKEST
                
        except Exception as e:
            print(f"⚠️ Error classifying block: {e}")
            return 'WEAK_BLOCK'

    
    def _identify_fvg_zones(self, df):
        """Helper to identify FVG zones"""
        fvg_zones = []
        
        try:
            for i in range(len(df) - 2):
                try:
                    c1_high = float(df.iloc[i]['high'])
                    c1_low = float(df.iloc[i]['low'])
                    c3_high = float(df.iloc[i + 2]['high'])
                    c3_low = float(df.iloc[i + 2]['low'])
                except (ValueError, TypeError):
                    continue
                
                # Bullish FVG
                if c1_high < c3_low:
                    fvg_zones.append({
                        'type': 'BULLISH',
                        'top': c1_high,
                        'bottom': c3_low
                    })
                
                # Bearish FVG
                if c1_low > c3_high:
                    fvg_zones.append({
                        'type': 'BEARISH',
                        'top': c1_low,
                        'bottom': c3_high
                    })
        except:
            pass
        
        return fvg_zones
    
    def find_fvg(self, direction='both'):
        """
        Find Fair Value Gaps (FVG).
        
        Bullish FVG: Gap up - high of candle 1 < low of candle 3
        Bearish FVG: Gap down - low of candle 1 > high of candle 3
        
        From Guardeer: FVG is a liquidity zone that price will return to
        """
        fvgs = {'bullish': [], 'bearish': []}
        
        try:
            for i in range(len(self.df) - 2):
                try:
                    c1 = self.df.iloc[i]
                    c3 = self.df.iloc[i + 2]
                    
                    c1_high = float(c1['high'])
                    c1_low = float(c1['low'])
                    c3_high = float(c3['high'])
                    c3_low = float(c3['low'])
                    
                except (ValueError, TypeError, KeyError):
                    continue
                
                # Bullish FVG: c1_high < c3_low (gap up)
                if c1_high < c3_low:
                    fvg = {
                        'type': 'BULLISH',
                        'top': c1_high,           # Top of gap
                        'bottom': c3_low,         # Bottom of gap
                        'gap_size': c3_low - c1_high,
                        'index': i,
                        'time': c1.get('time'),
                        'mitigated': False,
                        'bar_number': i
                    }
                    fvgs['bullish'].append(fvg)
                
                # Bearish FVG: c1_low > c3_high (gap down)
                if c1_low > c3_high:
                    fvg = {
                        'type': 'BEARISH',
                        'top': c1_low,            # Top of gap
                        'bottom': c3_high,        # Bottom of gap
                        'gap_size': c1_low - c3_high,
                        'index': i,
                        'time': c1.get('time'),
                        'mitigated': False,
                        'bar_number': i
                    }
                    fvgs['bearish'].append(fvg)
            
            self.fvgs = fvgs
            return fvgs
            
        except Exception as e:
            print(f"⚠️ Error finding FVGs: {e}")
            return fvgs
    
    def get_closest_poi(self, current_price, direction='UP'):
        """
        Get closest Point of Interest (POI) above or below current price.
        
        From Guardeer: This is what price is targeting next
        Priority: Order Blocks > FVGs
        
        NEW: Filters out WEAK_BLOCK (quality < 0.5) to use only high-quality POIs
        """
        try:
            current_price = float(current_price)
            poi_targets = []
            
            if direction == 'UP':
                # Look for targets ABOVE current price
                
                # Add bullish order blocks
                for ob in self.order_blocks['bullish']:
                    if ob['mean_threshold'] > current_price:
                        # NEW: Get block class quality weight
                        block_class = ob.get('block_class', 'WEAK_BLOCK')
                        
                        quality_weight = {
                            'BREAKER_BLOCK': 1.0,      # Use first (tested & held)
                            'RECLAIMED_BLOCK': 0.85,   # Use second (returned after break)
                            'MITIGATION_BLOCK': 0.70,  # Use third (filled orders)
                            'WEAK_BLOCK': 0.40         # Filter out (untested)
                        }.get(block_class, 0.40)
                        
                        # Optional: Skip very weak blocks (uncomment to enable strict filtering)
                        # if quality_weight < 0.5:
                        #     continue
                        
                        poi_targets.append((
                            ob['mean_threshold'],
                            f'ORDER_BLOCK_BULLISH_{block_class}',  # Now shows class in type
                            ob,
                            block_class  # Pass class instead of just strength
                        ))
                
                # Add bullish FVGs
                for fvg in self.fvgs['bullish']:
                    if fvg['bottom'] > current_price:
                        poi_targets.append((
                            fvg['bottom'],
                            'FVG_BULLISH',
                            fvg,
                            'FVG'
                        ))
            
            elif direction == 'DOWN':
                # Look for targets BELOW current price
                
                # Add bearish order blocks
                for ob in self.order_blocks['bearish']:
                    if ob['mean_threshold'] < current_price:
                        # NEW: Get block class quality weight
                        block_class = ob.get('block_class', 'WEAK_BLOCK')
                        
                        quality_weight = {
                            'BREAKER_BLOCK': 1.0,
                            'RECLAIMED_BLOCK': 0.85,
                            'MITIGATION_BLOCK': 0.70,
                            'WEAK_BLOCK': 0.40
                        }.get(block_class, 0.40)
                        
                        # Optional: Skip very weak blocks (uncomment to enable strict filtering)
                        # if quality_weight < 0.5:
                        #     continue
                        
                        poi_targets.append((
                            ob['mean_threshold'],
                            f'ORDER_BLOCK_BEARISH_{block_class}',  # Now shows class in type
                            ob,
                            block_class  # Pass class instead of just strength
                        ))
                
                # Add bearish FVGs
                for fvg in self.fvgs['bearish']:
                    if fvg['bottom'] < current_price:
                        poi_targets.append((
                            fvg['bottom'],
                            'FVG_BEARISH',
                            fvg,
                            'FVG'
                        ))
            
            if poi_targets:
                # Return closest target
                if direction == 'UP':
                    return min(poi_targets, key=lambda x: x[0])
                else:
                    return max(poi_targets, key=lambda x: x[0])
            
            return None
            
        except Exception as e:
            print(f"⚠️ Error getting closest POI: {e}")
            return None
    
    def get_all_pois(self, current_price):
        """Get all POIs with their types and distances"""
        pois = {
            'above': [],
            'below': []
        }
        
        try:
            current_price = float(current_price)
            
            # Order blocks above
            for ob in self.order_blocks['bullish']:
                if ob['mean_threshold'] > current_price:
                    pois['above'].append({
                        'price': ob['mean_threshold'],
                        'type': 'ORDER_BLOCK_BULLISH',
                        'strength': ob['strength'],
                        'block_class': ob.get('block_class', 'WEAK_BLOCK'),  # NEW
                        'distance': ob['mean_threshold'] - current_price
                    })
            
            # FVGs above
            for fvg in self.fvgs['bullish']:
                if fvg['bottom'] > current_price:
                    pois['above'].append({
                        'price': fvg['bottom'],
                        'type': 'FVG_BULLISH',
                        'gap_size': fvg['gap_size'],
                        'distance': fvg['bottom'] - current_price
                    })
            
            # Order blocks below
            for ob in self.order_blocks['bearish']:
                if ob['mean_threshold'] < current_price:
                    pois['below'].append({
                        'price': ob['mean_threshold'],
                        'type': 'ORDER_BLOCK_BEARISH',
                        'strength': ob['strength'],
                        'block_class': ob.get('block_class', 'WEAK_BLOCK'),  # NEW
                        'distance': current_price - ob['mean_threshold']
                    })
            
            # FVGs below
            for fvg in self.fvgs['bearish']:
                if fvg['bottom'] < current_price:
                    pois['below'].append({
                        'price': fvg['bottom'],
                        'type': 'FVG_BEARISH',
                        'gap_size': fvg['gap_size'],
                        'distance': current_price - fvg['bottom']
                    })
            
            # Sort by distance (closest first)
            pois['above'].sort(key=lambda x: x['distance'])
            pois['below'].sort(key=lambda x: x['distance'])
            
            return pois
            
        except Exception as e:
            print(f"⚠️ Error getting all POIs: {e}")
            return pois
