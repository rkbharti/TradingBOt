# strategy/smc_enhanced/bias.py

class BiasAnalyzer:
    """
    Institutional-style bias engine.

    Determines HTF directional bias based on
    confirmed structure and draw-on-liquidity.

    No scoring.
    No probability.
    No signal stacking.
    """

    def __init__(self):
        self.current_bias = "NEUTRAL"
        self.draw_on_liquidity = None

    # -------------------------------------------------
    # Core public method
    # -------------------------------------------------
    def get_bias(self, market_structure: dict, liquidity_map: dict = None):
        """
        Determine directional bias.

        Parameters:
        - market_structure: dict from MarketStructureDetector
        - liquidity_map: optional dict of liquidity levels

        Returns:
        {
            "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
            "draw_on_liquidity": price or None
        }
        """

        structure_bias = self._get_structure_bias(market_structure)
        liquidity_target = self._get_liquidity_target(
            structure_bias,
            liquidity_map
        )

        self.current_bias = structure_bias
        self.draw_on_liquidity = liquidity_target

        return {
            "bias": structure_bias,
            "draw_on_liquidity": liquidity_target
        }

    # -------------------------------------------------
    # Structure bias
    # -------------------------------------------------
    def _get_structure_bias(self, market_structure: dict):
        """
        Determine bias from confirmed structure.
        """

        if not market_structure:
            return "NEUTRAL"

        # Only trust confirmed structure
        if not market_structure.get("structure_confirmed", False):
            return "NEUTRAL"

        mss = market_structure.get("mss_or_choch", "")

        if "BULLISH" in mss:
            return "BULLISH"

        if "BEARISH" in mss:
            return "BEARISH"

        return "NEUTRAL"

    # -------------------------------------------------
    # Liquidity target
    # -------------------------------------------------
    def _get_liquidity_target(self, bias: str, liquidity_map: dict):
        """
        Determine draw on liquidity.
        """

        if not liquidity_map:
            return None

        buy_side = liquidity_map.get("buy_side", [])
        sell_side = liquidity_map.get("sell_side", [])

        if bias == "BULLISH" and sell_side:
            return max(sell_side)   # Target highest PDH (sell-side above)
        elif bias == "BEARISH" and buy_side:
            return min(buy_side)    # Target lowest PDL (buy-side below)

        return None
    
    

    # -------------------------------------------------
    # Reset logic
    # -------------------------------------------------
    def reset(self):
        """
        Reset bias when major structure flips.
        """
        self.current_bias = "NEUTRAL"
        self.draw_on_liquidity = None

    # Add to strategy/smc_enhanced/bias.py

def get_daily_ohlc_pattern(self, daily_candle: dict) -> str:
    """
    Guardeer Lecture 9 — Institutional daily pattern
    OHLC: Open near High → fake pump first → real dump (BEARISH)
    OLHC: Open near Low  → fake dump first → real pump (BULLISH)
    """
    o, h, l, c = daily_candle["open"], daily_candle["high"], daily_candle["low"], daily_candle["close"]
    total_range = h - l
    if total_range == 0:
        return "DOJI"
    
    open_position = (o - l) / total_range  # 0=near low, 1=near high
    
    if open_position > 0.7 and c < o:   # opened near high, closed below open
        return "OHLC_BEARISH"            # Fake high first, then real move down
    elif open_position < 0.3 and c > o: # opened near low, closed above open
        return "OLHC_BULLISH"            # Fake low first, then real move up
    else:
        return "EXPANSION"               # Trending day, trade with momentum

def get_institutional_bias(self, monthly_candle, weekly_candle, daily_candle) -> dict:
    """Top-down cascade — Monthly overrides Weekly overrides Daily"""
    monthly_pattern = self.get_daily_ohlc_pattern(monthly_candle)
    weekly_pattern  = self.get_daily_ohlc_pattern(weekly_candle)
    daily_pattern   = self.get_daily_ohlc_pattern(daily_candle)
    
    bias = "NEUTRAL"
    if "BULLISH" in monthly_pattern:
        bias = "BULLISH"
    elif "BEARISH" in monthly_pattern:
        bias = "BEARISH"
    
    return {
        "overall_bias": bias,
        "monthly": monthly_pattern,
        "weekly": weekly_pattern,
        "daily": daily_pattern,
        "confidence": 0.9 if monthly_pattern == weekly_pattern == daily_pattern else 0.6
    }