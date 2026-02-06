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

        if bias == "BULLISH" and buy_side:
            return min(buy_side)

        if bias == "BEARISH" and sell_side:
            return max(sell_side)

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
