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
            "displacement_detected": False,
            "reason_code": "INSUFFICIENT_DATA",
        }

    
    # ------------------------------------------------------------------
    # 3-bar swing logic
    # ------------------------------------------------------------------
    def _find_swings(self):
        swings = []
        if len(self.df) < 3:
            print("DEBUG SWINGS COUNT: 0 (not enough candles)")
            return swings

        for i in range(1, len(self.df) - 1):
            prev = self.df.iloc[i - 1]
            curr = self.df.iloc[i]
            nxt = self.df.iloc[i + 1]

            if curr['high'] > prev['high'] and curr['high'] > nxt['high']:
                swings.append({
                    'type': 'high',
                    'index': i,
                    'price': curr['high']
                })

            if curr['low'] < prev['low'] and curr['low'] < nxt['low']:
                swings.append({
                    'type': 'low',
                    'index': i,
                    'price': curr['low']
                })

        # 🔍 Debug output
        print("DEBUG SWINGS COUNT:", len(swings))
        if len(swings) > 0:
            print("DEBUG FIRST 5 SWINGS:", swings[:5])

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
            min_structure_bars = 20
            if (i - idm_bar) < min_structure_bars:
                continue

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
    # Helper: Real Break vs Liquidity Sweep
    # ------------------------------------------------------------------
    def check_liquidity_sweep(self, swing_price: float, index: int, direction: str):
        """
        Returns True if wick breaks swing but body closes inside; else False.
        Returns True, break index if sweep. If real break, also mark that.
        """
        if self.df is None or index >= len(self.df):
            return {"is_sweep": False, "break_index": None, "real_break": False}
        bar = self.df.iloc[index]
        result = {"is_sweep": False, "break_index": None, "real_break": False}
        if direction == "bullish":
            if bar["high"] > swing_price and bar["close"] < swing_price:
                result["is_sweep"] = True
                result["break_index"] = index
                # Check for real break next bar
                if index + 1 < len(self.df):
                    next_bar = self.df.iloc[index + 1]
                    if next_bar["close"] > bar["high"]:
                        result["real_break"] = True
        elif direction == "bearish":
            if bar["low"] < swing_price and bar["close"] > swing_price:
                result["is_sweep"] = True
                result["break_index"] = index
                if index + 1 < len(self.df):
                    next_bar = self.df.iloc[index + 1]
                    if next_bar["close"] < bar["low"]:
                        result["real_break"] = True
        return result
        # ------------------------------------------------------------------
    # Helper: FVG Detection
    # ------------------------------------------------------------------
    @staticmethod
    def detect_fvg(df: pd.DataFrame, start_index: int):
        """
        True FVG detection based on ICT/SMC rule.
        Returns True if 3-candle imbalance exists.
        """
        if df is None or len(df) < start_index + 3:
            return False

        c1 = df.iloc[start_index]
        c2 = df.iloc[start_index + 1]
        c3 = df.iloc[start_index + 2]

        # Bullish FVG
        if c1['high'] < c3['low']:
            return True

        # Bearish FVG
        if c1['low'] > c3['high']:
            return True

        return False

    # ------------------------------------------------------------------
    # Helper: Displacement Candle Logic
    # ------------------------------------------------------------------
    @staticmethod
    def detect_displacement(df: pd.DataFrame, start_index: int, window: int = 5):
        """
        - 3-candle sequence (start_index, start_index+1, start_index+2)
        - Candle 2 body must be larger than avg body size of last 'window' candles
        - Wick of candle1 and candle3 do NOT overlap → displacement exists
        Returns True/False
        """
        if df is None or len(df) < start_index + 3:
            return False
        c1 = df.iloc[start_index]
        c2 = df.iloc[start_index + 1]
        c3 = df.iloc[start_index + 2]
        # FVG check (true displacement condition)
        fvg_exists = MarketStructureDetector.detect_fvg(df, start_index)
        if not fvg_exists:
            return False

        # Body size check
        c2_body = abs(c2['close'] - c2['open'])
        if start_index - window + 1 < 0:
            avg_body = df.iloc[0:start_index + 1][['open', 'close']].apply(lambda row: abs(row['close'] - row['open']), axis=1).mean()
        else:
            avg_body = df.iloc[start_index - window + 1:start_index + 1][['open', 'close']].apply(lambda row: abs(row['close'] - row['open']), axis=1).mean()
        return c2_body > avg_body

    # ------------------------------------------------------------------
    # Helper: Inside Bar Detection
    # ------------------------------------------------------------------
    @staticmethod
    def detect_inside_bars(df: pd.DataFrame):
        """
        Returns indices of inside bars (fully inside previous bar range).
        """
        inside_indices = []
        for i in range(1, len(df)):
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            high_inside = curr['high'] <= prev['high'] and curr['high'] >= prev['low']
            low_inside = curr['low'] >= prev['low'] and curr['low'] <= prev['high']
            if high_inside and low_inside:
                inside_indices.append(i)
        return inside_indices

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
            "displacement_detected": False,
            "reason_code": "NO_STRUCTURE_BREAK",
        }

        if sweep_bar is None:
            return out
        print("DEBUG SWEEP BAR:", sweep_bar)

        # Find swings up to but not including sweep_bar
        swings = self._find_swings()
        highs = [s for s in swings if s["type"] == "high" and s["index"] < sweep_bar]
        lows = [s for s in swings if s["type"] == "low" and s["index"] < sweep_bar]
        if len(highs) < 2 or len(lows) < 2:
            print("DEBUG: Not enough swings for structure")
            print("High swings:", highs)
            print("Low swings:", lows)
            return out




        prev_high, last_high = highs[-2], highs[-1]
        prev_low, last_low = lows[-2], lows[-1]
        print("DEBUG STRUCTURE LEVELS:")
        print("prev_high:", prev_high)
        print("last_high:", last_high)
        print("prev_low:", prev_low)
        print("last_low:", last_low)


        # Trend determination
        is_bullish = last_high["price"] > prev_high["price"] and last_low["price"] > prev_low["price"]
        is_bearish = last_high["price"] < prev_high["price"] and last_low["price"] < prev_low["price"]

        # ====== REAL BREAK VS SWEEP LOGIC ======
        if idm_type == "bullish":
            for i in range(sweep_bar + 1, len(self.df)):
                bar = self.df.iloc[i]
                # Wick breaks structure, but close inside = sweep
                sweep = self.check_liquidity_sweep(prev_high["price"], i, "bullish")
                if sweep["is_sweep"]:
                    out["reason_code"] = "LIQUIDITY_SWEEP"
                    out["bos_or_sweep_occurred"] = True
                    # If real break confirmed, mark structure_confirmed
                    if sweep["real_break"] and i + 1 < len(self.df):
                        out.update({
                            "structure_confirmed": True,
                            "mss_or_choch": "MSS_BULLISH",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(prev_high["price"]),
                            "bos_bar_index": int(i + 1),
                            "reason_code": "STRUCTURE_CONFIRMED",
                        })
                        return out
                    return out
                # Confirm only if displacement present
                if bar["close"] > prev_high["price"]:
                    start_index = max(0, i - 1)
                    displacement = self.detect_displacement(self.df, start_index)
                    out["displacement_detected"] = bool(displacement)

                    if displacement:
                        out.update({
                            "structure_confirmed": True,
                            "mss_or_choch": "MSS_BULLISH",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(prev_high["price"]),
                            "bos_bar_index": int(i),
                            "reason_code": "STRUCTURE_CONFIRMED",
                        })
                    else:
                        out.update({
                            "structure_confirmed": False,
                            "mss_or_choch": "NONE",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(prev_high["price"]),
                            "bos_bar_index": int(i),
                            "reason_code": "NO_DISPLACEMENT",
                        })
                    return out

        elif idm_type == "bearish":
            for i in range(sweep_bar + 1, len(self.df)):
                bar = self.df.iloc[i]
                sweep = self.check_liquidity_sweep(prev_low["price"], i, "bearish")
                if sweep["is_sweep"]:
                    out["reason_code"] = "LIQUIDITY_SWEEP"
                    out["bos_or_sweep_occurred"] = True
                    if sweep["real_break"] and i + 1 < len(self.df):
                        out.update({
                            "structure_confirmed": True,
                            "mss_or_choch": "MSS_BEARISH",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(prev_low["price"]),
                            "bos_bar_index": int(i + 1),
                            "reason_code": "STRUCTURE_CONFIRMED",
                        })
                        return out
                    return out
                # Confirm only if displacement present
                if bar["close"] < prev_low["price"]:
                    start_index = max(0, i - 1)
                    displacement = self.detect_displacement(self.df, start_index)
                    out["displacement_detected"] = bool(displacement)

                    if displacement:
                        out.update({
                            "structure_confirmed": True,
                            "mss_or_choch": "MSS_BEARISH",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(prev_low["price"]),
                            "bos_bar_index": int(i),
                            "reason_code": "STRUCTURE_CONFIRMED",
                        })
                    else:
                        out.update({
                            "structure_confirmed": False,
                            "mss_or_choch": "NONE",
                            "bos_or_sweep_occurred": True,
                            "bos_level": float(prev_low["price"]),
                            "bos_bar_index": int(i),
                            "reason_code": "NO_DISPLACEMENT",
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
            out["displacement_detected"] = False
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

        # Add inside bar detection as additional output context (not affecting main logic)
        out["inside_bar_indices"] = self.detect_inside_bars(self.df)

        return out