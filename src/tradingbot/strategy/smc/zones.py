class ZoneCalculator:

    @staticmethod
    def calculate_zones(swing_high, swing_low, buffer_percent=5):
        """
        Calculate Premium, Discount, and Equilibrium zones
        using ICT 0.5 equilibrium logic.
        """

        swing_high = float(swing_high)
        swing_low  = float(swing_low)

        # Safety: ensure correct ordering
        if swing_high < swing_low:
            swing_high, swing_low = swing_low, swing_high

        range_size = swing_high - swing_low
        if range_size <= 0:
            return None

        equilibrium  = (swing_high + swing_low) / 2
        buffer_size  = range_size * (buffer_percent / 100)

        return {
            # Structure
            "swing_high": swing_high,
            "swing_low":  swing_low,
            "range":      range_size,

            # Equilibrium
            "equilibrium":       equilibrium,
            "equilibrium_upper": equilibrium + buffer_size,
            "equilibrium_lower": equilibrium - buffer_size,

            # Premium / Discount
            "premium_start": equilibrium + buffer_size,
            "premium_end":   swing_high,

            "discount_start": swing_low,
            "discount_end":   equilibrium - buffer_size,
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
        zone  = ZoneCalculator.classify_price_zone(price, zones)

        return {
            "current_zone":               zone,
            "can_buy":                    zone == "DISCOUNT",
            "can_sell":                   zone == "PREMIUM",
            "equilibrium":                zones["equilibrium"],
            "distance_from_equilibrium":  price - zones["equilibrium"],
        }


# ─────────────────────────────────────────────────────────────────────────────
# NEW CLASS — DealingRangeAnalyzer
# Guardeer Lectures 9 & 11 — Fibonacci Premium/Discount Engine
#
# WHAT THIS ADDS (not in ZoneCalculator above):
#   1. Fibonacci-based price position (0.0–1.0 grid, not just midpoint)
#      → tells you "price is at 82% of the range = deep Premium"
#   2. get_price_position() returns exact Fib % + zone label
#   3. is_valid_entry_zone() takes bias + price → hard True/False gate
#   4. get_nested_range() for LTF discount inside HTF discount setups
#      → highest probability entries (nested discount = institutional confluence)
#   5. get_fib_levels() exposes 0.236 / 0.382 / 0.5 / 0.618 / 0.786 / 0.886
#      for OTE and internal reference levels
#
# KEY GUARDEER RULES ENCODED:
#   - Only BUY from Discount zone in uptrend (fib < 0.5)
#   - Only SELL from Premium zone in downtrend (fib > 0.5)
#   - At Equilibrium (fib ~0.5) → wait for confirmation, never enter blind
#   - Never enter at midpoint without strong LTF CHoCH confirmation
# ─────────────────────────────────────────────────────────────────────────────

class DealingRangeAnalyzer:
    """
    Fibonacci-based Dealing Range engine.

    A dealing range is defined by two anchor points:
      - htf_high: most recent significant HTF swing high
      - htf_low:  most recent significant HTF swing low

    The 0–1 Fibonacci grid is then applied:
      0.0          = htf_low  (bottom of range)
      0.0 – 0.5    = DISCOUNT zone (institutional buy territory)
      0.5          = EQUILIBRIUM (50% — wait for confirmation here)
      0.5 – 1.0    = PREMIUM zone (institutional sell territory)
      1.0          = htf_high (top of range)

    Key internal Fibonacci levels also tracked:
      0.236, 0.382, 0.500, 0.618, 0.705, 0.786, 0.886
    """

    # EQ buffer — prices within this fib distance of 0.5 are considered
    # "at equilibrium" and require extra confirmation before entry.
    EQ_BUFFER = 0.03   # ±3% either side of the 0.5 level

    # Fibonacci reference levels used for OTE and internal analysis
    FIB_LEVELS = {
        "0.236": 0.236,
        "0.382": 0.382,
        "0.500": 0.500,   # Equilibrium
        "0.618": 0.618,   # OTE top (retracement from high)
        "0.705": 0.705,   # Mid OTE
        "0.786": 0.786,   # OTE bottom
        "0.886": 0.886,   # Deep OTE / last resort entry
    }

    # ── Public API ────────────────────────────────────────────────────────────

    def get_dealing_range(self, htf_high: float, htf_low: float) -> dict:
        """
        Build the full dealing range dict from a HTF high and low anchor.

        Returns:
            {
                "htf_high": float,
                "htf_low":  float,
                "range_size": float,
                "equilibrium": float,           ← exact 50% price
                "eq_upper": float,              ← EQ upper buffer
                "eq_lower": float,              ← EQ lower buffer
                "premium_zone": (float, float), ← (eq_upper, htf_high)
                "discount_zone": (float, float),← (htf_low, eq_lower)
                "fib_levels": dict,             ← all key Fib prices
            }
        """
        htf_high   = float(htf_high)
        htf_low    = float(htf_low)

        if htf_high < htf_low:
            htf_high, htf_low = htf_low, htf_high

        range_size  = htf_high - htf_low
        if range_size <= 0:
            return {}

        equilibrium = htf_low + range_size * 0.5
        eq_upper    = htf_low + range_size * (0.5 + self.EQ_BUFFER)
        eq_lower    = htf_low + range_size * (0.5 - self.EQ_BUFFER)

        # Pre-calculate all Fibonacci price levels
        fib_prices = {
            label: round(htf_low + range_size * fib, 5)
            for label, fib in self.FIB_LEVELS.items()
        }

        return {
            "htf_high":      htf_high,
            "htf_low":       htf_low,
            "range_size":    range_size,
            "equilibrium":   equilibrium,
            "eq_upper":      eq_upper,
            "eq_lower":      eq_lower,
            "premium_zone":  (eq_upper, htf_high),
            "discount_zone": (htf_low, eq_lower),
            "fib_levels":    fib_prices,
        }

    def get_price_position(self, current_price: float,
                            htf_high: float, htf_low: float) -> dict:
        """
        Get the exact Fibonacci position of current price within the range.

        Returns:
            {
                "zone":      "PREMIUM" | "DISCOUNT" | "EQUILIBRIUM",
                "fib_pct":   float,    ← 0.0–1.0 (e.g. 0.82 = 82% = deep Premium)
                "pct_label": str,      ← human-readable e.g. "82% Premium"
                "at_eq":     bool,     ← True if within EQ buffer
            }
        """
        htf_high = float(htf_high)
        htf_low  = float(htf_low)

        range_size = htf_high - htf_low
        if range_size <= 0:
            return {"zone": "UNKNOWN", "fib_pct": 0.0, "pct_label": "N/A", "at_eq": False}

        fib_pct = (float(current_price) - htf_low) / range_size
        # Clamp to 0–1 in case price has broken out of the range
        fib_pct = max(0.0, min(1.0, fib_pct))

        at_eq = abs(fib_pct - 0.5) <= self.EQ_BUFFER

        if at_eq:
            zone = "EQUILIBRIUM"
        elif fib_pct > 0.5:
            zone = "PREMIUM"
        else:
            zone = "DISCOUNT"

        pct_label = f"{fib_pct * 100:.1f}% {zone}"

        return {
            "zone":      zone,
            "fib_pct":   round(fib_pct, 4),
            "pct_label": pct_label,
            "at_eq":     at_eq,
        }

    def is_valid_entry_zone(self, bias: str, current_price: float,
                             htf_high: float, htf_low: float) -> bool:
        """
        Hard gate — returns True ONLY when price is in the institutionally
        correct zone for the given bias direction.

        Guardeer Rule (Lecture 9):
          BULLISH bias → price must be in DISCOUNT (fib < 0.5 - buffer)
          BEARISH bias → price must be in PREMIUM  (fib > 0.5 + buffer)
          EQUILIBRIUM  → always False (wait for confirmation)

        Args:
            bias:          "BULLISH" | "BEARISH" | "NEUTRAL"
            current_price: current market price
            htf_high:      most recent HTF swing high
            htf_low:       most recent HTF swing low

        Returns:
            True  → safe to look for entry in this zone
            False → wrong zone, DO NOT enter regardless of OB quality
        """
        position = self.get_price_position(current_price, htf_high, htf_low)

        if position["zone"] == "EQUILIBRIUM":
            return False    # Never trade at EQ without extra confirmation

        if bias == "BULLISH" and position["zone"] == "DISCOUNT":
            return True

        if bias == "BEARISH" and position["zone"] == "PREMIUM":
            return True

        return False

    def get_nested_range(self, ltf_high: float, ltf_low: float,
                          htf_high: float, htf_low: float) -> dict:
        """
        Resolve a nested dealing range — LTF discount inside HTF discount.
        This is the HIGHEST probability setup in the Guardeer framework.

        Logic:
          1. Confirm the LTF range sits inside the HTF discount zone
          2. Build the LTF dealing range
          3. Return confluence flag + both ranges

        Returns:
            {
                "htf_range":        dict,   ← full HTF dealing range
                "ltf_range":        dict,   ← full LTF dealing range
                "nested_confirmed": bool,   ← True = LTF range is inside HTF discount
                "confluence_type":  str,    ← "DISCOUNT_IN_DISCOUNT" | "PREMIUM_IN_PREMIUM" | "NONE"
            }
        """
        htf_range = self.get_dealing_range(htf_high, htf_low)
        ltf_range = self.get_dealing_range(ltf_high, ltf_low)

        if not htf_range or not ltf_range:
            return {"htf_range": htf_range, "ltf_range": ltf_range,
                    "nested_confirmed": False, "confluence_type": "NONE"}

        # Check if the entire LTF range sits within the HTF discount zone
        htf_discount_low, htf_discount_high = htf_range["discount_zone"]
        ltf_nested_in_htf_discount = (
            ltf_low  >= htf_discount_low and
            ltf_high <= htf_discount_high
        )

        # Check if the entire LTF range sits within the HTF premium zone
        htf_premium_low, htf_premium_high = htf_range["premium_zone"]
        ltf_nested_in_htf_premium = (
            ltf_low  >= htf_premium_low and
            ltf_high <= htf_premium_high
        )

        if ltf_nested_in_htf_discount:
            confluence_type = "DISCOUNT_IN_DISCOUNT"   # Buy setup — highest probability
        elif ltf_nested_in_htf_premium:
            confluence_type = "PREMIUM_IN_PREMIUM"     # Sell setup — highest probability
        else:
            confluence_type = "NONE"

        return {
            "htf_range":        htf_range,
            "ltf_range":        ltf_range,
            "nested_confirmed": confluence_type != "NONE",
            "confluence_type":  confluence_type,
        }

    def get_fib_levels(self, htf_high: float, htf_low: float) -> dict:
        """
        Return all key Fibonacci price levels for the given range.
        Useful for plotting on chart or cross-referencing with OTE calculator.

        Returns dict of {label: price} e.g.:
            {"0.236": 2312.5, "0.382": 2298.1, "0.500": 2280.0, ...}
        """
        dr = self.get_dealing_range(htf_high, htf_low)
        return dr.get("fib_levels", {})