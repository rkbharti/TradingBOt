import pandas as pd
from typing import Dict, Optional


class MarketStructureDetector:
    """
    Candle-based SMC Market Structure Engine (Video-aligned).

    Implements rules:
    1. Valid pullback = candle sweeps previous candle high/low.
    2. Inside bar = ignored.
    3. IDM = first valid pullback.
    4. Wick sweep of IDM confirms HH/LL.
    5. Body close confirms BOS or CHOCH.
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy() if df is not None else pd.DataFrame()

    # ------------------------------------------------------------------
    # Default output
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
            "reason_code": "INSUFFICIENT_DATA",
        }

    # ------------------------------------------------------------------
    # Candle logic helpers
    # ------------------------------------------------------------------
    def _is_inside_bar(self, prev, curr) -> bool:
        return curr["high"] <= prev["high"] and curr["low"] >= prev["low"]

    def _valid_pullback(self, prev, curr) -> Optional[str]:
        """
        Returns:
            'bullish' → swept previous low
            'bearish' → swept previous high
            None → invalid or inside bar
        """
        if self._is_inside_bar(prev, curr):
            return None

        if curr["low"] < prev["low"]:
            return "bullish"

        if curr["high"] > prev["high"]:
            return "bearish"

        return None

    # ------------------------------------------------------------------
    # IDM detection
    # ------------------------------------------------------------------
    def detect_idm(self) -> Dict:
        """
        Scan candles to find first valid pullback.
        """
        if self.df is None or len(self.df) < 3:
            return self._default_analysis()

        for i in range(1, len(self.df)):
            prev = self.df.iloc[i - 1]
            curr = self.df.iloc[i]

            pullback_type = self._valid_pullback(prev, curr)

            if pullback_type:
                out = self._default_analysis()
                out.update(
                    {
                        "is_idm_present": True,
                        "idm_bar_index": i,
                        "idm_price": float(curr["low"] if pullback_type == "bullish" else curr["high"]),
                        "idm_type": pullback_type,
                        "reason_code": "IDM_PRESENT",
                    }
                )
                return out

        out = self._default_analysis()
        out["reason_code"] = "NO_IDM"
        return out

    # ------------------------------------------------------------------
    # IDM sweep
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

        for i in range(idm_bar + 1, len(self.df)):
            bar = self.df.iloc[i]

            if idm_type == "bullish":
                if bar["low"] < idm_price:
                    out.update(
                        {
                            "is_idm_swept": True,
                            "idm_sweep_bar_index": i,
                            "idm_sweep_price": float(bar["low"]),
                            "reason_code": "IDM_WICK_SWEEP",
                        }
                    )
                    return out

            elif idm_type == "bearish":
                if bar["high"] > idm_price:
                    out.update(
                        {
                            "is_idm_swept": True,
                            "idm_sweep_bar_index": i,
                            "idm_sweep_price": float(bar["high"]),
                            "reason_code": "IDM_WICK_SWEEP",
                        }
                    )
                    return out

        return out

    # ------------------------------------------------------------------
    # Structure confirmation
    # ------------------------------------------------------------------
    def confirm_structure(self, idm_type: str, sweep_bar: int) -> Dict:
        out = {
            "structure_confirmed": False,
            "mss_or_choch": "NONE",
            "bos_or_sweep_occurred": False,
            "bos_level": None,
            "reason_code": "NO_STRUCTURE_BREAK",
        }

        if sweep_bar is None:
            return out

        # Determine structural reference
        ref_high = self.df["high"].iloc[:sweep_bar].max()
        ref_low = self.df["low"].iloc[:sweep_bar].min()

        for i in range(sweep_bar + 1, len(self.df)):
            bar = self.df.iloc[i]

            if idm_type == "bullish":
                if bar["close"] > ref_high:
                    out.update(
                        {
                            "structure_confirmed": True,
                            "mss_or_choch": "MSS_BULLISH",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(ref_high),
                            "reason_code": "STRUCTURE_CONFIRMED",
                        }
                    )
                    return out

            elif idm_type == "bearish":
                if bar["close"] < ref_low:
                    out.update(
                        {
                            "structure_confirmed": True,
                            "mss_or_choch": "MSS_BEARISH",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(ref_low),
                            "reason_code": "STRUCTURE_CONFIRMED",
                        }
                    )
                    return out

        return out

    # ------------------------------------------------------------------
    # Main public API
    # ------------------------------------------------------------------
    def get_idm_state(self) -> Dict:
        if self.df is None or len(self.df) < 3:
            return self._default_analysis()

        # Step 1: detect IDM
        idm = self.detect_idm()
        if not idm["is_idm_present"]:
            return idm

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
            return out

        # Step 3: structure
        structure = self.confirm_structure(
            idm_type=idm["idm_type"],
            sweep_bar=sweep["idm_sweep_bar_index"],
        )

        out.update(structure)
        return out
