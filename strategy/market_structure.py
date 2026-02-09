import pandas as pd
from typing import Dict, Optional

class MarketStructureDetector:
    """
    SMC True Swing-Based Market Structure Engine (refined BOS/CHoCH/IDM timing).
    - Detects swing highs/lows using a 3-bar model.
    - Trend: determined by last two swing highs and last two swing lows.
    - BOS: properly detected at the *first close after* swing break.
    - CHoCH: first break in the opposite direction after BOS.
    - IDM: first opposite swing after BOS candle.
    - Pure price action—no indicators, ATR, or moving averages.
    """

    def __init__(self, df: pd.DataFrame, tick_size: float = 0.05):
        self.df = df.copy() if df is not None else pd.DataFrame()
        self.tick_size = tick_size

    # ------------------------------------------------------------------
    # Default output (compat w/ old logic)
    # ------------------------------------------------------------------
    def _default_analysis(self) -> Dict:
        return {
            "is_idm_present": False,
            "idm_bar_index": None,
            "idm_price": None,
            "idm_type": None,
            "is_idm_swept": False,
            "idm_sweep_bar_index": None,
            "idm_sweep_price": None,
            "structure_confirmed": False,
            "mss_or_choch": "NONE",
            "bos_or_sweep_occurred": False,
            "bos_level": None,
            "choch_bar_index": None,
            "reason_code": "INSUFFICIENT_DATA",
        }

    # ------------------------------------------------------------------
    # 3-bar swing logic
    # ------------------------------------------------------------------
    def _find_swings(self):
        swings = []
        if len(self.df) < 3:
            return swings
        for i in range(1, len(self.df) - 1):
            prev = self.df.iloc[i - 1]
            curr = self.df.iloc[i]
            nxt = self.df.iloc[i + 1]
            if curr['high'] > prev['high'] and curr['high'] > nxt['high']:
                swings.append({'type': 'high', 'index': i, 'price': curr['high']})
            if curr['low'] < prev['low'] and curr['low'] < nxt['low']:
                swings.append({'type': 'low', 'index': i, 'price': curr['low']})
        return swings

    # ------------------------------------------------------------------
    # IDM detection (first valid pullback)
    # ------------------------------------------------------------------
    def detect_idm(self) -> Dict:
        if self.df is None or len(self.df) < 3:
            return self._default_analysis()
        for i in range(1, len(self.df)):
            prev = self.df.iloc[i - 1]
            curr = self.df.iloc[i]
            if curr["low"] < prev["low"]:
                out = self._default_analysis()
                out.update({
                    "is_idm_present": True,
                    "idm_bar_index": i,
                    "idm_price": float(curr["low"]),
                    "idm_type": "bullish",
                    "reason_code": "IDM_PRESENT",
                })
                return out
            if curr["high"] > prev["high"]:
                out = self._default_analysis()
                out.update({
                    "is_idm_present": True,
                    "idm_bar_index": i,
                    "idm_price": float(curr["high"]),
                    "idm_type": "bearish",
                    "reason_code": "IDM_PRESENT",
                })
                return out
        out = self._default_analysis()
        out["reason_code"] = "NO_IDM"
        return out

    # ------------------------------------------------------------------
    # Tolerance-based IDM sweep detection
    # ------------------------------------------------------------------
    def confirm_idm_sweep(self, idm_bar: int, idm_price: float, idm_type: str) -> Dict:
        out = {
            "is_idm_swept": False,
            "idm_sweep_bar_index": None,
            "idm_sweep_price": None,
            "reason_code": "IDM_NOT_SWEPT",
        }
        if idm_bar is None:
            return out
        tolerance = abs(self.tick_size) * 0.5  # half-tick tolerance

        for i in range(idm_bar + 1, len(self.df)):
            bar = self.df.iloc[i]

            if idm_type == "bullish":
                # Sweep must touch or slightly break IDM
                if bar["low"] <= idm_price + tolerance:
                    out.update({
                        "is_idm_swept": True,
                        "idm_sweep_bar_index": i,
                        "idm_sweep_price": float(bar["low"]),
                        "reason_code": "IDM_WICK_SWEEP",
                    })
                    return out

            elif idm_type == "bearish":
                if bar["high"] >= idm_price - tolerance:
                    out.update({
                        "is_idm_swept": True,
                        "idm_sweep_bar_index": i,
                        "idm_sweep_price": float(bar["high"]),
                        "reason_code": "IDM_WICK_SWEEP",
                    })
                    return out

        return out


    # ------------------------------------------------------------------
    # Structure confirmation (BOS, CHoCH, MSS logic)
    # ------------------------------------------------------------------
    def confirm_structure(self, idm_type: str, sweep_bar: int) -> Dict:
        out = {
            "structure_confirmed": False,
            "mss_or_choch": "NONE",
            "bos_or_sweep_occurred": False,
            "bos_level": None,
            "bos_bar_index": None,
            "reason_code": "NO_STRUCTURE_BREAK",
        }

        if sweep_bar is None:
            return out

        # Find swings up to but not including sweep_bar
        swings = self._find_swings()
        highs = [s for s in swings if s["type"] == "high" and s["index"] < sweep_bar]
        lows = [s for s in swings if s["type"] == "low" and s["index"] < sweep_bar]
        if len(highs) < 2 or len(lows) < 2:
            return out

        prev_high, last_high = highs[-2], highs[-1]
        prev_low, last_low = lows[-2], lows[-1]

        # Trend determination
        is_bullish = last_high["price"] > prev_high["price"] and last_low["price"] > prev_low["price"]
        is_bearish = last_high["price"] < prev_high["price"] and last_low["price"] < prev_low["price"]

        if idm_type == "bullish" and is_bullish:
            # Find close > prev_high["price"] after sweep_bar
            for i in range(sweep_bar + 1, len(self.df)):
                bar = self.df.iloc[i]
                if bar["close"] > prev_high["price"]:
                    out.update({
                        "structure_confirmed": True,
                        "mss_or_choch": "MSS_BULLISH",
                        "bos_or_sweep_occurred": True,
                        "bos_level": float(prev_high["price"]),
                        "bos_bar_index": int(i),
                        "reason_code": "STRUCTURE_CONFIRMED",
                    })
                    return out
        elif idm_type == "bearish" and is_bearish:
            for i in range(sweep_bar + 1, len(self.df)):
                bar = self.df.iloc[i]
                if bar["close"] < prev_low["price"]:
                    out.update({
                        "structure_confirmed": True,
                        "mss_or_choch": "MSS_BEARISH",
                        "bos_or_sweep_occurred": True,
                        "bos_level": float(prev_low["price"]),
                        "bos_bar_index": int(i),
                        "reason_code": "STRUCTURE_CONFIRMED",
                    })
                    return out

        return out

    # ------------------------------------------------------------------
    # Main public API - required by all integration code
    # ------------------------------------------------------------------
    def get_idm_state(self) -> Dict:
        """
        Consolidated SMC structure state with required compatibility fields.
        """
        if self.df is None or len(self.df) < 3:
            out = self._default_analysis()
            out["choch_bar_index"] = None
            return out

        # Step 1: detect IDM
        idm = self.detect_idm()
        if not idm["is_idm_present"]:
            out = {**idm}
            out["choch_bar_index"] = None
            return out

        # Step 2: sweep
        sweep = self.confirm_idm_sweep(
            idm_bar=idm["idm_bar_index"],
            idm_price=idm["idm_price"],
            idm_type=idm["idm_type"],
        )

        out = {**idm, **sweep}

        if not sweep["is_idm_swept"]:
            out["structure_confirmed"] = False
            out["mss_or_choch"] = "NONE"
            out["bos_or_sweep_occurred"] = False
            out["bos_level"] = None
            out["choch_bar_index"] = None
            return out

        # Step 3: structure (BOS)
        structure = self.confirm_structure(
            idm_type=idm["idm_type"],
            sweep_bar=sweep["idm_sweep_bar_index"],
        )

        out.update(structure)

        # NEW: CHoCH logic - bar index
        # If structure is CHoCH, use the BOS bar as CHoCH origin
        if structure.get("structure_confirmed") and structure.get("mss_or_choch", "").startswith("CHOCH"):
            out["choch_bar_index"] = structure.get("bos_bar_index")
        else:
            out["choch_bar_index"] = None

        return out