"""
Narrative Analyzer Module
Guardeer VIDEO 10: Market Narrative (3Bs Framework)

Key Concepts:
B1: Based on what market did RECENTLY
B2: Based on CURRENT framework (which liquidity targeted?)
B3: Based on CURRENT dealing range (premium/discount)

From Guardeer: "The 3Bs tells the complete story the market is telling"
"""


class NarrativeAnalyzer:
    """
    Market Narrative Analysis (3Bs Framework) from Guardeer VIDEO 10
    
    B1: Based on what market did recently
    B2: Based on current framework (which liquidity targeted?)
    B3: Based on current dealing range (premium/discount)
    
    The 3Bs tells you the complete story the market is telling and what to do next.
    """
    
    def __init__(self, liquidity_detector, poi_identifier, bias_detector, zone_calculator=None):
        self.liquidity = liquidity_detector
        self.poi = poi_identifier
        self.bias = bias_detector
        self.zones = zone_calculator
    
    def analyze_market_story(self, market_state):
        """
        Analyze the 3-part market narrative (3Bs).
        
        Args:
            market_state: dict with current market information
        
        Returns:
            dict with complete narrative analysis
        """
        try:
            b1 = self.get_b1_recent_action(market_state)
            b2 = self.get_b2_current_framework(market_state)
            b3 = self.get_b3_dealing_range(market_state)
            
            complete_story = self.combine_narrative(b1, b2, b3)
            combined_bias = self.get_combined_bias(b1, b2, b3)
            
            narrative = {
                'b1': b1,
                'b2': b2,
                'b3': b3,
                'complete_story': complete_story,
                'bias': combined_bias,
                'trade_signal': self.derive_signal(b1, b2, b3, combined_bias),
                'confidence': self.calculate_confidence(b1, b2, b3)
            }
            
            return narrative
            
        except Exception as e:
            print(f"⚠️ Error analyzing market story: {e}")
            return {}
    
    def get_b1_recent_action(self, market_state):
        """
        B1: What has market done RECENTLY?
        
        - Liquidity grabbed?
        - FVG tapped?
        - Order block hit?
        - Direction of recent price action?
        """
        try:
            b1 = {
                'liquidity_grabbed': market_state.get('liquidity_grabbed', False),
                'liquidity_type': market_state.get('liquidity_type', 'NONE'),  # 'PDH', 'PDL', 'SWING_HIGH', etc
                'fvg_tapped': market_state.get('fvg_tapped', False),
                'fvg_type': market_state.get('fvg_type', 'NONE'),
                'ob_hit': market_state.get('ob_hit', False),
                'ob_type': market_state.get('ob_type', 'NONE'),  # 'BULLISH', 'BEARISH'
                'price_action': market_state.get('price_action', 'NEUTRAL'),  # 'STRONG_UP', 'STRONG_DOWN', etc
                'narrative': self._build_b1_narrative(
                    market_state.get('liquidity_grabbed', False),
                    market_state.get('fvg_tapped', False),
                    market_state.get('ob_hit', False)
                )
            }
            
            return b1
            
        except Exception as e:
            print(f"⚠️ Error in B1 analysis: {e}")
            return {}
    
    def _build_b1_narrative(self, liquidity_grabbed, fvg_tapped, ob_hit):
        """
        Build narrative string from B1 conditions.
        """
        if liquidity_grabbed:
            return "Market recently GRABBED liquidity and is likely reversing"
        elif fvg_tapped:
            return "Market recently FILLED a fair value gap"
        elif ob_hit:
            return "Market recently HIT an order block"
        else:
            return "Market is consolidating, no major structure hit yet"
    
    def get_b2_current_framework(self, market_state):
        """
        B2: Based on CURRENT framework, which liquidity is market targeting?
        
        - What POI is next target?
        - Higher or lower timeframe?
        - What's the pullback pattern?
        - Direction of current structure?
        """
        try:
            b2 = {
                'current_direction': market_state.get('current_direction', 'NEUTRAL'),  # 'BULLISH', 'BEARISH', 'NEUTRAL'
                'current_bias': market_state.get('current_bias', 'NEUTRAL'),
                'next_poi_target': market_state.get('next_poi_target'),  # Price level
                'target_type': market_state.get('target_type'),  # 'ORDER_BLOCK', 'FVG', 'SWING', etc
                'target_distance': market_state.get('target_distance'),  # Pips away
                'target_confidence': market_state.get('target_confidence', 'MEDIUM'),  # 'LOW', 'MEDIUM', 'HIGH'
                'pullback_pattern': market_state.get('pullback_pattern', 'UNKNOWN'),
                'timeframe': market_state.get('timeframe', '15min'),
                'narrative': self._build_b2_narrative(
                    market_state.get('current_direction', 'NEUTRAL'),
                    market_state.get('target_type'),
                    market_state.get('target_distance')
                )
            }
            
            return b2
            
        except Exception as e:
            print(f"⚠️ Error in B2 analysis: {e}")
            return {}
    
    def _build_b2_narrative(self, direction, target_type, distance):
        """
        Build narrative string from B2 conditions.
        """
        if target_type is None or distance is None:
            return "No clear POI target identified yet"
        
        direction_text = "UP" if direction == 'BULLISH' else "DOWN" if direction == 'BEARISH' else "?"
        distance_text = f"{distance:.1f}" if distance else "unknown distance"
        
        return f"Market is heading {direction_text} to {target_type} at {distance_text} pips away"
    
    def get_b3_dealing_range(self, market_state):
        """
        B3: Where is price in CURRENT dealing range?
        
        - Premium, Discount, or Equilibrium?
        - Distance from equilibrium?
        - Zone strength?
        """
        try:
            b3 = {
                'zone': market_state.get('zone', 'EQUILIBRIUM'),  # 'PREMIUM', 'DISCOUNT', 'EQUILIBRIUM'
                'zone_strength': market_state.get('zone_strength', 0),  # 0-100%
                'distance_from_equilibrium': market_state.get('distance_from_equilibrium', 0),
                'can_buy': market_state.get('zone') in ['DISCOUNT', 'DEEP_DISCOUNT'],
                'can_sell': market_state.get('zone') == 'PREMIUM',
                'equilibrium_breach_risk': market_state.get('zone_strength', 0) > 80,  # High strength = breach risk
                'narrative': self._build_b3_narrative(
                    market_state.get('zone', 'EQUILIBRIUM'),
                    market_state.get('zone_strength', 0)
                )
            }
            
            return b3
            
        except Exception as e:
            print(f"⚠️ Error in B3 analysis: {e}")
            return {}
    
    def _build_b3_narrative(self, zone, zone_strength):
        """
        Build narrative string from B3 conditions.
        """
        if zone == 'PREMIUM':
            return f"Price is in PREMIUM zone ({zone_strength:.0f}% deep) - SELL zone"
        elif zone == 'DISCOUNT':
            return f"Price is in DISCOUNT zone ({zone_strength:.0f}% deep) - BUY zone"
        else:
            return f"Price is at EQUILIBRIUM - AVOID trading"
    
    def combine_narrative(self, b1, b2, b3):
        """
        Combine all 3 Bs into a complete market story.
        """
        try:
            story = {
                'what_happened': b1.get('narrative', 'Unknown'),
                'what_happens_next': b2.get('narrative', 'No target'),
                'where_is_price': b3.get('narrative', 'Unknown'),
                'trade_allowed': b3.get('can_buy') or b3.get('can_sell'),
                'trade_direction': 'BUY' if b3.get('can_buy') and b2.get('current_direction') == 'UP' 
                                  else 'SELL' if b3.get('can_sell') and b2.get('current_direction') == 'DOWN' 
                                  else 'HOLD',
                'urgency': self._calculate_urgency(b1, b2, b3)
            }
            
            return story
            
        except Exception as e:
            print(f"⚠️ Error combining narrative: {e}")
            return {}
    
    def _calculate_urgency(self, b1, b2, b3):
        """
        Calculate how urgent the trade setup is.
        
        Returns: 'LOW', 'MEDIUM', 'HIGH'
        """
        urgency_score = 0
        
        # B1: Liquidity grabbed = urgent reversal expected
        if b1.get('liquidity_grabbed'):
            urgency_score += 2
        
        # B2: Close to target = urgent entry
        if b2.get('target_distance') and b2.get('target_distance') < 10:
            urgency_score += 2
        
        # B3: Deep in zone = urgent, high zone strength
        if b3.get('zone_strength', 0) > 70:
            urgency_score += 1
        
        if urgency_score >= 4:
            return 'HIGH'
        elif urgency_score >= 2:
            return 'MEDIUM'
        else:
            return 'LOW'
    
    def get_combined_bias(self, b1, b2, b3):
        """
        Get final bias recommendation from all 3 Bs.
        
        Voting system: Which direction appears most?
        """
        bullish_votes = 0
        bearish_votes = 0
        
        # B2 direction
        if b2.get('current_direction') == 'BULLISH':
            bullish_votes += 1
        elif b2.get('current_direction') == 'BEARISH':
            bearish_votes += 1
        
        # B3 zone
        if b3.get('can_buy'):
            bullish_votes += 1
        if b3.get('can_sell'):
            bearish_votes += 1
        
        # B1 action
        if b1.get('price_action') == 'STRONG_UP':
            bullish_votes += 1
        elif b1.get('price_action') == 'STRONG_DOWN':
            bearish_votes += 1
        
        if bullish_votes > bearish_votes:
            return 'BULLISH'
        elif bearish_votes > bullish_votes:
            return 'BEARISH'
        else:
            return 'NEUTRAL'
    
    def derive_signal(self, b1, b2, b3, bias):
        """
        Derive final trading signal from all components.
        
        Signal: 'BUY', 'SELL', 'HOLD'
        """
        # Must be in correct zone first
        if not (b3.get('can_buy') or b3.get('can_sell')):
            return 'HOLD'  # Wrong zone
        
        # Must have direction confirmation
        if b2.get('current_direction') == 'NEUTRAL':
            return 'HOLD'  # No direction
        
        # Now apply logic
        if b3.get('can_buy') and b2.get('current_direction') == 'BULLISH' and bias == 'BULLISH':
            return 'BUY'
        elif b3.get('can_sell') and b2.get('current_direction') == 'BEARISH' and bias == 'BEARISH':
            return 'SELL'
        else:
            return 'HOLD'
    
    def calculate_confidence(self, b1, b2, b3):
        """
        Calculate confidence level in the narrative (0-100%).
        
        Based on how much evidence supports the story.
        """
        confidence = 50  # Base confidence
        
        # B1: Recent action confirmation
        if b1.get('liquidity_grabbed') or b1.get('ob_hit'):
            confidence += 20
        
        # B2: Clear target
        if b2.get('target_type') and b2.get('target_confidence') == 'HIGH':
            confidence += 15
        
        # B3: Strong zone
        if b3.get('zone_strength', 0) > 60:
            confidence += 15
        
        return min(100, confidence)
    
    def print_narrative(self, narrative):
        """
        Pretty print the market narrative.
        """
        if not narrative:
            print("⚠️ No narrative data")
            return
        
        print("\n" + "="*70)
        print("📖 MARKET NARRATIVE (3Bs FRAMEWORK)")
        print("="*70)
        
        b1 = narrative.get('b1', {})
        b2 = narrative.get('b2', {})
        b3 = narrative.get('b3', {})
        story = narrative.get('complete_story', {})
        
        print(f"\n① B1 (What Happened Recently):")
        print(f"   {b1.get('narrative', 'N/A')}")
        print(f"   - Liquidity Grabbed: {b1.get('liquidity_grabbed')}")
        print(f"   - FVG Tapped: {b1.get('fvg_tapped')}")
        print(f"   - OB Hit: {b1.get('ob_hit')}")
        
        print(f"\n② B2 (Current Framework):")
        print(f"   {b2.get('narrative', 'N/A')}")
        print(f"   - Current Direction: {b2.get('current_direction')}")
        print(f"   - Target Distance: {b2.get('target_distance')} pips")
        
        print(f"\n③ B3 (Dealing Range):")
        print(f"   {b3.get('narrative', 'N/A')}")
        print(f"   - Zone Strength: {b3.get('zone_strength', 0):.0f}%")
        
        print(f"\n🎯 FINAL STORY:")
        print(f"   Signal: {story.get('trade_direction')} | Urgency: {story.get('urgency')}")
        print(f"   Bias: {narrative.get('bias')} | Confidence: {narrative.get('confidence', 0):.0f}%")
        print(f"   Trade Allowed: {story.get('trade_allowed')}")
        
        print("="*70 + "\n")
