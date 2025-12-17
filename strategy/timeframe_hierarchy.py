"""
Timeframe Hierarchy Filter
Ensures M5 signals RESPECT higher timeframe structure
Prevents counter-trend trades when H4/D1 are strongly opposed
"""

class TimeframeHierarchyFilter:
    """
    Validates M5 signals against H1, H4, D1 structure
    Prevents counter-trend trades when higher TFs are strongly opposed
    """
    
    def __init__(self):
        self.timeframe_weights = {
            'D1': 4.0,   # Highest weight
            'H4': 3.0,
            'H1': 2.0,
            'M15': 1.5,
            'M5': 1.0    # Lowest weight
        }
    
    def validate_m5_signal(self, signal: str, mtf_confluence: dict, 
                           structure_analysis: dict) -> dict:
        """
        Validate M5 signal against higher timeframes
        
        Args:
            signal: 'BUY' or 'SELL' from M5
            mtf_confluence: Multi-timeframe analysis results
            structure_analysis: Market structure from Video 3
            
        Returns:
            {
                'allowed': bool,
                'reason': str,
                'confidence_adjustment': float  # 0.0 to 1.0
            }
        """
        
        tf_signals = mtf_confluence.get('tf_signals', {})
        
        # Extract bias per timeframe
        d1_bias = tf_signals.get('D1', {}).get('bias', 'NEUTRAL')
        h4_bias = tf_signals.get('H4', {}).get('bias', 'NEUTRAL')
        h1_bias = tf_signals.get('H1', {}).get('bias', 'NEUTRAL')
        m15_bias = tf_signals.get('M15', {}).get('bias', 'NEUTRAL')
        
        # === RULE 1: D1 Dominance ===
        # If D1 is strongly bearish/bullish, M5 must have structure shift to trade against it
        if signal == 'BUY' and d1_bias == 'BEARISH':
            # Check if there's a CHOCH on D1 (structure shift)
            d1_choc = tf_signals.get('D1', {}).get('choc', {})
            if not d1_choc.get('bullish_choc', False):
                return {
                    'allowed': False,
                    'reason': 'D1 is BEARISH with no CHOCH - M5 BUY blocked',
                    'confidence_adjustment': 0.0
                }
        
        elif signal == 'SELL' and d1_bias == 'BULLISH':
            d1_choc = tf_signals.get('D1', {}).get('choc', {})
            if not d1_choc.get('bearish_choc', False):
                return {
                    'allowed': False,
                    'reason': 'D1 is BULLISH with no CHOCH - M5 SELL blocked',
                    'confidence_adjustment': 0.0
                }
        
        # === RULE 2: H4/H1 Alignment Check ===
        # Count how many higher timeframes oppose or support the M5 signal
        higher_tf_opposition_count = 0
        higher_tf_support_count = 0
        
        for tf, bias in [('H4', h4_bias), ('H1', h1_bias)]:
            if signal == 'BUY':
                if bias == 'BEARISH':
                    higher_tf_opposition_count += 1
                elif bias == 'BULLISH':
                    higher_tf_support_count += 1
            else:  # SELL
                if bias == 'BULLISH':
                    higher_tf_opposition_count += 1
                elif bias == 'BEARISH':
                    higher_tf_support_count += 1
        
        # If BOTH H4 and H1 oppose, block trade (too risky)
        if higher_tf_opposition_count >= 2:
            return {
                'allowed': False,
                'reason': f'H4 and H1 oppose M5 {signal} - Too risky',
                'confidence_adjustment': 0.0
            }
        
        # === RULE 3: Partial Alignment = Reduced Confidence ===
        if higher_tf_opposition_count == 1 and higher_tf_support_count == 0:
            return {
                'allowed': True,
                'reason': f'One HTF opposes - Reduced confidence',
                'confidence_adjustment': 0.6  # 40% penalty
            }
        
        # === RULE 4: Full Alignment = Boost Confidence ===
        if higher_tf_support_count >= 2:
            return {
                'allowed': True,
                'reason': f'HTFs support M5 {signal} - High confidence',
                'confidence_adjustment': 1.2  # 20% bonus
            }
        
        # === DEFAULT: Allow with standard confidence ===
        return {
            'allowed': True,
            'reason': 'No major conflicts detected',
            'confidence_adjustment': 1.0
        }
    
    
    def calculate_htf_alignment_score(self, signal: str, tf_signals: dict) -> float:
        """
        Calculate weighted alignment score (0-100)
        Higher score = better alignment with HTFs
        
        Args:
            signal: 'BUY' or 'SELL'
            tf_signals: Dictionary of timeframe analysis results
            
        Returns:
            float: Alignment score (0-100)
        """
        
        score = 0.0
        total_weight = 0.0
        
        for tf, analysis in tf_signals.items():
            if tf == 'M5':  # Skip M5 itself
                continue
                
            bias = analysis.get('bias', 'NEUTRAL')
            weight = self.timeframe_weights.get(tf, 1.0)
            
            if signal == 'BUY':
                if bias == 'BULLISH':
                    score += weight * 100
                elif bias == 'BEARISH':
                    score += weight * 0  # Negative alignment
                else:  # NEUTRAL
                    score += weight * 50  # Neutral doesn't help or hurt
            
            else:  # SELL
                if bias == 'BEARISH':
                    score += weight * 100
                elif bias == 'BULLISH':
                    score += weight * 0
                else:
                    score += weight * 50
            
            total_weight += weight
        
        return (score / total_weight) if total_weight > 0 else 50.0
    
    
    def get_dominant_timeframe(self, tf_signals: dict) -> dict:
        """
        Identify which timeframe has the strongest signal
        Used for debugging and analysis
        
        Returns:
            {
                'timeframe': str,
                'bias': str,
                'strength': float
            }
        """
        strongest_tf = None
        max_weight = 0.0
        strongest_bias = 'NEUTRAL'
        
        for tf, analysis in tf_signals.items():
            weight = self.timeframe_weights.get(tf, 1.0)
            bias = analysis.get('bias', 'NEUTRAL')
            
            # Only consider non-neutral biases
            if bias != 'NEUTRAL' and weight > max_weight:
                max_weight = weight
                strongest_tf = tf
                strongest_bias = bias
        
        return {
            'timeframe': strongest_tf if strongest_tf else 'NONE',
            'bias': strongest_bias,
            'strength': max_weight
        }
    
    
    def print_hierarchy_status(self, tf_signals: dict):
        """
        Print a visual representation of timeframe hierarchy
        Useful for debugging
        """
        print("\n   📊 Timeframe Hierarchy Status:")
        print("   " + "="*60)
        
        for tf in ['D1', 'H4', 'H1', 'M15', 'M5']:
            if tf not in tf_signals:
                continue
                
            analysis = tf_signals[tf]
            bias = analysis.get('bias', 'NEUTRAL')
            weight = self.timeframe_weights[tf]
            
            # Visual indicator
            if bias == 'BULLISH':
                indicator = '🟢 BULL'
            elif bias == 'BEARISH':
                indicator = '🔴 BEAR'
            else:
                indicator = '⚪ NEUTRAL'
            
            # Show BOS/CHOC if present
            bos = analysis.get('bos', {})
            choc = analysis.get('choc', {})
            
            extra = []
            if bos.get('bullish_bos') or bos.get('bearish_bos'):
                extra.append('BOS')
            if choc.get('bullish_choc') or choc.get('bearish_choc'):
                extra.append('CHOCH')
            
            extra_str = f" [{', '.join(extra)}]" if extra else ""
            
            print(f"   {tf:>4} (w={weight:.1f}): {indicator}{extra_str}")
        
        print("   " + "="*60)
        
    def validate_hierarchy(signal: str, mtf_confluence: dict, structure_analysis: dict) -> dict:
        """
        Standalone wrapper function for validating M5 signals
        """
        filter = TimeframeHierarchyFilter()
        return filter.validate_m5_signal(signal, mtf_confluence, structure_analysis)

