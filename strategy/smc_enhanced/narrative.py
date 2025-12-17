"""
Narrative Analyzer Module
Guardeer VIDEO 10: Market Narrative (3Bs Framework)
Guardeer VIDEO 3: Inducement Integration


FIXED VERSION - December 2024:
- Reduced inducement weight (25% → 15%)
- Added volume requirement for inducement
- Inducement no longer overrides zone requirement
- STEP 4: Added session weighting for inducement


Key Concepts:
B1: Based on what market did RECENTLY (now includes inducement WITH SESSION DATA)
B2: Based on CURRENT framework (which liquidity targeted?)
B3: Based on CURRENT dealing range (premium/discount)


From Guardeer: "The 3Bs tells the complete story the market is telling"
"""



class NarrativeAnalyzer:
    """
    Market Narrative Analysis (3Bs Framework) from Guardeer VIDEO 10
    
    B1: Based on what market did recently (WITH INDUCEMENT + SESSION WEIGHTING)
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
        
        - Inducement detected? (HIGHEST PRIORITY WITH SESSION DATA)
        - Liquidity grabbed?
        - FVG tapped?
        - Order block hit?
        - Direction of recent price action?
        """
        try:
            b1 = {
                'inducement': market_state.get('inducement', False),
                'inducement_type': market_state.get('inducement_type', 'NONE'),
                'inducement_direction': market_state.get('inducement_direction', 'NONE'),
                # STEP 4: Session weighting fields
                'inducement_session': market_state.get('inducement_session', 'UNKNOWN'),
                'inducement_reliability': market_state.get('inducement_reliability', 0.70),
                'inducement_weighted_confidence': market_state.get('inducement_weighted_confidence', 'MEDIUM'),
                # Volume fields
                'volume_spike': market_state.get('volume_spike', False),
                'volume_ratio': market_state.get('volume_ratio', 0),
                # Other recent action
                'liquidity_grabbed': market_state.get('liquidity_grabbed', False),
                'liquidity_type': market_state.get('liquidity_type', 'NONE'),
                'fvg_tapped': market_state.get('fvg_tapped', False),
                'fvg_type': market_state.get('fvg_type', 'NONE'),
                'ob_hit': market_state.get('ob_hit', False),
                'ob_type': market_state.get('ob_type', 'NONE'),
                'price_action': market_state.get('price_action', 'NEUTRAL'),
                'narrative': self._build_b1_narrative(
                    market_state.get('inducement', False),
                    market_state.get('volume_spike', False),
                    market_state.get('liquidity_grabbed', False),
                    market_state.get('fvg_tapped', False),
                    market_state.get('ob_hit', False)
                )
            }
            
            return b1
            
        except Exception as e:
            print(f"⚠️ Error in B1 analysis: {e}")
            return {}
    
    def _build_b1_narrative(self, inducement, volume_spike, liquidity_grabbed, fvg_tapped, ob_hit):
        """
        Build narrative string from B1 conditions.
        
        FIXED: Inducement gets highest priority BUT volume matters
        """
        if inducement and volume_spike:
            return "🚨 INDUCEMENT + VOLUME SPIKE detected - High probability reversal"
        elif inducement:
            return "🚨 Market created INDUCEMENT sweep (awaiting volume confirmation)"
        elif liquidity_grabbed:
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
                'current_direction': market_state.get('current_direction', 'NEUTRAL'),
                'current_bias': market_state.get('current_bias', 'NEUTRAL'),
                'next_poi_target': market_state.get('next_poi_target'),
                'target_type': market_state.get('target_type'),
                'target_distance': market_state.get('target_distance'),
                'target_confidence': market_state.get('target_confidence', 'MEDIUM'),
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
                'zone': market_state.get('zone', 'EQUILIBRIUM'),
                'zone_strength': market_state.get('zone_strength', 0),
                'distance_from_equilibrium': market_state.get('distance_from_equilibrium', 0),
                'can_buy': market_state.get('zone') in ['DISCOUNT', 'DEEP_DISCOUNT'],
                'can_sell': market_state.get('zone') == 'PREMIUM',
                'equilibrium_breach_risk': market_state.get('zone_strength', 0) > 80,
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
        
        FIXED: Inducement with volume = HIGH urgency
        """
        urgency_score = 0
        
        # B1: Inducement + Volume = VERY urgent (FIXED)
        if b1.get('inducement') and b1.get('volume_spike'):
            urgency_score += 3
        elif b1.get('inducement'):
            urgency_score += 1  # REDUCED from 3
        
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
        
        FIXED: Inducement gets 2 votes ONLY with volume confirmation
        """
        bullish_votes = 0
        bearish_votes = 0
        
        # B1: Inducement direction (FIXED - volume matters)
        if b1.get('inducement'):
            if b1.get('volume_spike'):
                # With volume = 2 votes
                if b1.get('inducement_direction') == 'BULLISH':
                    bullish_votes += 2
                elif b1.get('inducement_direction') == 'BEARISH':
                    bearish_votes += 2
            else:
                # Without volume = 1 vote only
                if b1.get('inducement_direction') == 'BULLISH':
                    bullish_votes += 1
                elif b1.get('inducement_direction') == 'BEARISH':
                    bearish_votes += 1
        
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
        
        FIXED: Inducement SUGGESTS direction but still requires:
        1. Zone confirmation (40%+ strength)
        2. Volume confirmation (spike present)
        3. Bias alignment
        """
        # Inducement detected = suggests direction but needs confirmation
        if b1.get('inducement'):
            # RULE 1: Require minimum zone strength
            if b3.get('zone_strength', 0) < 40:
                return 'HOLD'  # Zone too weak
            
            # RULE 2: Require volume confirmation
            if not b1.get('volume_spike'):
                return 'HOLD'  # No volume = no trade
            
            # RULE 3: Check direction alignment with bias
            if b1.get('inducement_direction') == 'BULLISH' and bias == 'BULLISH':
                # RULE 4: Must be in correct zone
                if b3.get('can_buy'):
                    return 'BUY'
            elif b1.get('inducement_direction') == 'BEARISH' and bias == 'BEARISH':
                # RULE 4: Must be in correct zone
                if b3.get('can_sell'):
                    return 'SELL'
            
            # If any rule fails, return HOLD
            return 'HOLD'
        
        # Original logic (no inducement)
        # Must be in correct zone
        if not (b3.get('can_buy') or b3.get('can_sell')):
            return 'HOLD'
        
        # Must have direction confirmation
        if b2.get('current_direction') == 'NEUTRAL':
            return 'HOLD'
        
        # Apply logic
        if b3.get('can_buy') and b2.get('current_direction') == 'BULLISH' and bias == 'BULLISH':
            return 'BUY'
        elif b3.get('can_sell') and b2.get('current_direction') == 'BEARISH' and bias == 'BEARISH':
            return 'SELL'
        else:
            return 'HOLD'
    
    def calculate_confidence(self, b1, b2, b3):
        """
        Calculate confidence level in the narrative (0-100%).
        
        STEP 4 ENHANCED: Session weighting for inducement
        """
        confidence = 50  # Base confidence
        
        # B1: Inducement with session weighting
        if b1.get('inducement'):
            base_boost = 10  # Start conservative
            
            # STEP 4: Apply session reliability multiplier
            session_reliability = b1.get('inducement_reliability', 0.70)
            session_boost = int(base_boost * (session_reliability / 0.70))  # Scale by reliability
            
            # Volume confirmation adds extra boost
            if b1.get('volume_spike'):
                session_boost += 10  # Extra 10% for volume
            
            confidence += session_boost
            
            # Debug output with session name
            session = b1.get('inducement_session', 'UNKNOWN')
            print(f"   📊 Inducement confidence: +{session_boost}% ({session} session)")
        
        # B1: Recent action confirmation
        if b1.get('liquidity_grabbed') or b1.get('ob_hit'):
            confidence += 15  # REDUCED from 20
        
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
        if b1.get('inducement'):
            print(f"   🚨 INDUCEMENT: {b1.get('inducement_type')} - {b1.get('inducement_direction')}")
            print(f"   📍 Session: {b1.get('inducement_session')} ({b1.get('inducement_reliability', 0)*100:.0f}% reliability)")
            print(f"   📊 Volume: {'✅ SPIKE' if b1.get('volume_spike') else '❌ NO SPIKE'}")
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
