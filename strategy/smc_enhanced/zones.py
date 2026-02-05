"""
Zone Calculator Module — SMC / ICT CLEAN VERSION
Refactored after NotebookLLM + ICT doctrine audit

Purpose:
- Define Premium / Discount / Equilibrium zones
- Act as a HARD institutional filter
- NO probability, NO strength, NO ATR, NO scoring

Zones answer ONLY:
"Is price in institutional buying or selling territory?"
"""

class ZoneCalculator:

    @staticmethod
    def calculate_zones(swing_high, swing_low, buffer_percent=5):
        """
        Calculate Premium, Discount, and Equilibrium zones
        using ICT 0.5 equilibrium logic.
        """

        swing_high = float(swing_high)
        swing_low = float(swing_low)

        # Safety: ensure correct ordering
        if swing_high < swing_low:
            swing_high, swing_low = swing_low, swing_high

        range_size = swing_high - swing_low
        if range_size <= 0:
            return None

        equilibrium = (swing_high + swing_low) / 2
        buffer_size = range_size * (buffer_percent / 100)

        return {
            # Structure
            "swing_high": swing_high,
            "swing_low": swing_low,
            "range": range_size,

            # Equilibrium
            "equilibrium": equilibrium,
            "equilibrium_upper": equilibrium + buffer_size,
            "equilibrium_lower": equilibrium - buffer_size,

            # Premium / Discount
            "premium_start": equilibrium + buffer_size,
            "premium_end": swing_high,

            "discount_start": swing_low,
            "discount_end": equilibrium - buffer_size,
        }

    @staticmethod
    def classify_price_zone(price, zones):
        """
        Classify current price into Premium / Discount / Equilibrium
        """
        if not zones:
            return "UNKNOWN"

        price = float(price)

        if zones["equilibrium_lower"] <= price <= zones["equilibrium_upper"]:
            return "EQUILIBRIUM"

        if price > zones["equilibrium_upper"]:
            return "PREMIUM"

        if price < zones["equilibrium_lower"]:
            return "DISCOUNT"

        return "UNKNOWN"

    @staticmethod
    def can_execute_trade(signal, current_zone):
        """
        ICT Execution Rule:
        BUY  → Discount only
        SELL → Premium only
        """

        if signal == "BUY":
            return current_zone == "DISCOUNT"

        if signal == "SELL":
            return current_zone == "PREMIUM"

        return False

    @staticmethod
    def get_zone_context(price, zones):
        """
        Lightweight contextual summary.
        NO strength, NO scoring.
        """

        if not zones:
            return None

        price = float(price)
        zone = ZoneCalculator.classify_price_zone(price, zones)

        return {
            "current_zone": zone,
            "can_buy": zone == "DISCOUNT",
            "can_sell": zone == "PREMIUM",
            "equilibrium": zones["equilibrium"],
            "distance_from_equilibrium": price - zones["equilibrium"],
        }
