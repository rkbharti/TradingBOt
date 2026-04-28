from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─── Constants ────────────────────────────────────────────────────────────────

VALID_POI_TYPES = {
    "IDM_SWEEP",
    "FIRST_OB_AFTER_IDM",
    "EXTREME_OB",
    "BOS_SWEEP",
    "CHOCH_SWEEP",
}

# Killzones in UTC (Guardeer Lecture 10, converted from IST)
# London Open KZ: 12:30 PM - 2:30 PM IST -> 07:00-09:00 UTC
# New York Open KZ: 6:30 PM - 9:00 PM IST -> 13:00-15:30 UTC
KILLZONES_UTC: List[Tuple[time, time, str]] = [
    (time(7, 0), time(9, 0), "LONDON"),
    (time(13, 0), time(15, 30), "NEW_YORK"),
]

# ─── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalResult:
    action: str
    direction: Optional[str]
    entry_price: Optional[float]
    sl_price: Optional[float]
    tp_price: Optional[float]
    gates: Dict[str, Dict[str, Any]]
    reason: str
    confidence_score: int


@dataclass(frozen=True)
class SweepEvent:
    direction: str
    sweep_side: str
    reference_index: int
    reference_level: float
    candle_index: int
    sweep_price: float
    close_back_inside: float
    target_external_liquidity: float
    atr_at_sweep: float


@dataclass(frozen=True)
class StructureBreak:
    direction: str
    choch_label: str
    level: float
    candle_index: int
    close_price: float


@dataclass(frozen=True)
class POI:
    poi_type: str
    candle_index: int
    low: float
    high: float


@dataclass(frozen=True)
class FVG:
    direction: str
    candle_index: int
    low: float
    high: float


@dataclass
class SignalEngineConfig:
    d1_weight: int = 4
    h4_weight: int = 3

    external_swing_window: int = 5
    internal_swing_window: int = 2

    recent_sweep_bars: int = 80
    liquidity_lookback: int = 120

    atr_period: int = 14
    atr_sl_multiplier: float = 0.5

    sweep_atr_tolerance: float = 0.1

    min_m5_candles: int = 50
    min_m15_candles: int = 50
    min_h4_candles: int = 20
    min_d1_candles: int = 20

    rr_min: float = 1.3

    time_column: str = "time"
    open_col: str = "open"
    high_col: str = "high"
    low_col: str = "low"
    close_col: str = "close"


# ─── Engine ───────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Canonical SMC signal engine for XAUUSD.
    Implements Guardeer's sequential checklist.
    """

    def __init__(self, config: Optional[SignalEngineConfig] = None) -> None:
        self.config = config or SignalEngineConfig()

    # ── Public Entry Point ────────────────────────────────────────────────────

    def evaluate(
        self,
        m5_df: pd.DataFrame,
        m15_df: pd.DataFrame,
        h4_df: pd.DataFrame,
        d1_df: pd.DataFrame,
        now_utc: Optional[datetime] = None,
    ) -> SignalResult:
        # m5_df = self._normalize_ohlc(m5_df, "m5_df")
        # m15_df = self._normalize_ohlc(m15_df, "m15_df")
        # h4_df = self._normalize_ohlc(h4_df, "h4_df")
        # d1_df = self._normalize_ohlc(d1_df, "d1_df")

        gates = self._init_gates()

        # === DATA QUALITY CHECKS ===
        cfg = self.config
        if len(m5_df) < cfg.min_m5_candles:
            print(f"⚠️ [DATA] m5_df has {len(m5_df)} candles — recommended minimum is {cfg.min_m5_candles}.")
        if len(m15_df) < cfg.min_m15_candles:
            print(f"⚠️ [DATA] m15_df has {len(m15_df)} candles — recommended minimum is {cfg.min_m15_candles}.")
        if len(h4_df) < cfg.min_h4_candles:
            print(f"⚠️ [DATA] h4_df has {len(h4_df)} candles — recommended minimum is {cfg.min_h4_candles}.")
        if len(d1_df) < cfg.min_d1_candles:
            print(f"⚠️ [DATA] d1_df has {len(d1_df)} candles — recommended minimum is {cfg.min_d1_candles}.")

        if any(df.empty for df in [m5_df, m15_df, h4_df, d1_df]):
            print("⚠️ One or more dataframes are empty — continuing cautiously")

        if any(len(df) < 15 for df in [m5_df, m15_df, h4_df, d1_df]):
            print(f"⚠️ LOW DATA → m5:{len(m5_df)}, m15:{len(m15_df)}, h4:{len(h4_df)}, d1:{len(d1_df)}")

        # ─── Step 1: HTF Bias ───────────────────────────────────────────────

        step1 = self._step_htf_bias(d1_df, h4_df)
        gates["step_1_htf_bias"] = step1
        if not step1["passed"]:
            return self._no_trade(gates, step1["reason"])

        direction: str = step1["direction"]

        # ─── Step 2: External Liquidity Sweep ────────────────────────────────

        step2, sweep = self._step_external_liquidity_sweep(m15_df, direction)
        gates["step_2_external_liquidity_sweep"] = step2
        if not step2["passed"] or sweep is None:
            return self._no_trade(gates, step2["reason"], direction=direction)

        # ─── Step 3: CHOCH / MSS Body Close ─────────────────────────────────

        step3, structure_break = self._step_choch_mss_body_close(m5_df, sweep, m15_df)
        gates["step_3_choch_mss_body_close"] = step3
        if not step3["passed"] or structure_break is None:
            return self._no_trade(gates, step3["reason"], direction=direction)

        # ─── Step 4: Valid POI ─────────────────────────────────────────────��

        step4, poi_candidates = self._step_valid_poi(
            m15_df,
            m5_df,
            h4_df,   # 🔥 ADD THIS
            sweep,
            structure_break
        )
        gates["step_4_valid_poi"] = step4
        if not step4["passed"] or not poi_candidates:
            return self._no_trade(gates, step4["reason"], direction=direction)

        # ─── Step 5: OB/FVG Confluence ──────────────────────────────────────

        step5, selected_poi, selected_fvg, entry_price = self._step_ob_fvg_confluence(
            m5_df, m15_df, direction, sweep, structure_break, poi_candidates
        )
        gates["step_5_ob_fvg_confluence"] = step5
        if not step5["passed"] or selected_poi is None or selected_fvg is None or entry_price is None:
            return self._no_trade(gates, step5["reason"], direction=direction)

        # ─── Step 6: Dealing Range ──────────────────────────────────────────

        step6 = self._step_dealing_range(direction, entry_price, sweep)
        gates["step_6_dealing_range"] = step6
        if not step6["passed"]:
            return self._no_trade(gates, step6["reason"], direction=direction)

        # # ─── Step 7: Killzone ───────────────────────────────────────────────

        step7 = self._step_killzone(now_utc, m5_df)
        gates["step_7_killzone"] = step7
        if not step7["passed"]:
            return self._no_trade(gates, step7["reason"], direction=direction)

        # ─── Step 8: Risk/Reward ────────────────────────────────────────────

        step8, sl_price, tp_price = self._step_rr(direction, entry_price, sweep)
        gates["step_8_risk_reward"] = step8
        if not step8["passed"]:
            return self._no_trade(gates, step8["reason"], direction=direction)

        # ─── All Gates Passed ───────────────────────────────────────────────

        print(f"[TRADE] {direction} | ENTRY: {self._r(entry_price)} | SL: {self._r(sl_price)} | TP: {self._r(tp_price)}")

        return SignalResult(
            action="ENTER",
            direction=direction,
            entry_price=self._r(entry_price),
            sl_price=self._r(sl_price),
            tp_price=self._r(tp_price),
            gates=gates,
            reason="ALL_GATES_PASSED",
            confidence_score=100,
        )

    def evaluate_from_context(self, ctx: Dict[str, Any]) -> SignalResult:
        return self.evaluate(
            m5_df=ctx["m5_df"],
            m15_df=ctx["m15_df"],
            h4_df=ctx["h4_df"],
            d1_df=ctx["d1_df"],
            now_utc=ctx.get("now_utc"),
        )

    # ── Gate Implementations ──────────────────────────────────────────────────

    def _step_htf_bias(
        self,
        d1_df: pd.DataFrame,
        h4_df: pd.DataFrame,
    ) -> Dict[str, Any]:

        d1_bias = self._infer_bias(d1_df, self.config.external_swing_window)
        h4_bias = self._infer_bias(h4_df, self.config.external_swing_window)

        # =========================
        # ✅ CASE 1 — PERFECT ALIGNMENT
        # =========================
        if d1_bias == h4_bias and d1_bias in {"BULLISH", "BEARISH"}:
            return {
                "passed": True,
                "direction": d1_bias,
                "reason": "HTF_ALIGNED",
                "d1_bias": d1_bias,
                "h4_bias": h4_bias,
                "agreement_score": self.config.d1_weight + self.config.h4_weight,
            }

        # =========================
        # 🔥 CASE 2 — D1 NEUTRAL → TRUST H4
        # =========================
        if d1_bias == "NEUTRAL" and h4_bias in {"BULLISH", "BEARISH"}:
            return {
                "passed": True,
                "direction": h4_bias,
                "reason": "H4_DOMINANT",
                "d1_bias": d1_bias,
                "h4_bias": h4_bias,
                "agreement_score": self.config.h4_weight,
            }

        # =========================
        # ❌ CASE 3 — BOTH NEUTRAL
        # =========================
        if d1_bias == "NEUTRAL" and h4_bias == "NEUTRAL":
            return {
                "passed": False,
                "direction": None,
                "reason": "NO_HTF_DIRECTION",
                "d1_bias": d1_bias,
                "h4_bias": h4_bias,
                "agreement_score": 0,
            }

        # =========================
        # ❌ CASE 4 — CONFLICT (OPPOSITE)
        # =========================
        return {
            "passed": False,
            "direction": None,
            "reason": "HTF_CONFLICT",
            "d1_bias": d1_bias,
            "h4_bias": h4_bias,
            "agreement_score": 0,
        }

    def _step_external_liquidity_sweep(
        self,
        df: pd.DataFrame,
        direction: str,
    ) -> Tuple[Dict[str, Any], Optional[SweepEvent]]:

        cfg = self.config

        if len(df) < 50:
            return {"passed": False, "reason": "INSUFFICIENT_DATA"}, None

        # 🔥 Scan last N candles (NOT just last one)
        start_idx = max(cfg.external_swing_window + 2, len(df) - cfg.recent_sweep_bars)

        for i in range(len(df) - 1, start_idx - 1, -1):

            # -------------------------
            # STEP 1: Get reference swings
            # -------------------------
            window_start = max(0, i - 20)
            segment = df.iloc[window_start:i]

            if len(segment) < 10:
                continue

            ref_high = float(segment["high"].max())
            ref_low = float(segment["low"].min())

            # -------------------------
            # STEP 2: Current candle
            # -------------------------
            curr_high = float(df["high"].iat[i])
            curr_low = float(df["low"].iat[i])
            curr_close = float(df["close"].iat[i])

            atr = self._calc_atr(df, i, cfg.atr_period)
            tolerance = atr * cfg.sweep_atr_tolerance

            # =========================
            # ✅ BULLISH SWEEP (sell-side taken)
            # =========================
            if direction == "BULLISH":

                wick_break = curr_low < (ref_low - tolerance)
                close_back = curr_close > ref_low

                if wick_break and close_back:

                    tp = float(segment["high"].max())

                    event = SweepEvent(
                        direction="BULLISH",
                        sweep_side="SELL_SIDE",
                        reference_index=i,
                        reference_level=ref_low,
                        candle_index=i,
                        sweep_price=curr_low,
                        close_back_inside=curr_close,
                        target_external_liquidity=tp,
                        atr_at_sweep=atr,
                    )

                    return {
                        "passed": True,
                        "reason": "VALID_BULLISH_SWEEP",
                        "reference_level": self._r(ref_low),
                        "sweep_price": self._r(curr_low),
                        "target_external_liquidity": self._r(tp),
                        "candle_index": i,
                    }, event

            # =========================
            # ✅ BEARISH SWEEP (buy-side taken)
            # =========================
            else:

                wick_break = curr_high > (ref_high + tolerance)
                close_back = curr_close < ref_high

                if wick_break and close_back:

                    tp = float(segment["low"].min())

                    event = SweepEvent(
                        direction="BEARISH",
                        sweep_side="BUY_SIDE",
                        reference_index=i,
                        reference_level=ref_high,
                        candle_index=i,
                        sweep_price=curr_high,
                        close_back_inside=curr_close,
                        target_external_liquidity=tp,
                        atr_at_sweep=atr,
                    )

                    return {
                        "passed": True,
                        "reason": "VALID_BEARISH_SWEEP",
                        "reference_level": self._r(ref_high),
                        "sweep_price": self._r(curr_high),
                        "target_external_liquidity": self._r(tp),
                        "candle_index": i,
                    }, event

        return {"passed": False, "reason": "NO_VALID_SWEEP"}, None

    def _step_choch_mss_body_close(
        self,
        m5_df: pd.DataFrame,
        sweep: SweepEvent,
        m15_df: pd.DataFrame,
    ) -> Tuple[Dict[str, Any], Optional[StructureBreak]]:

        # -------------------------
        # STEP 1: Map sweep → M5
        # -------------------------
        sweep_time = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)

        if m5_sweep_idx is None:
            return {"passed": False, "reason": "SWEEP_MAPPING_FAILED"}, None

        end_idx = min(len(m5_df), m5_sweep_idx + 25)

        # -------------------------
        # STEP 2: Find REAL swing (LH / HL)
        # -------------------------
        lookback = 30
        start = max(0, m5_sweep_idx - lookback)

        segment = m5_df.iloc[start:m5_sweep_idx]

        if len(segment) < 10:
            return {"passed": False, "reason": "INSUFFICIENT_STRUCTURE"}, None

        highs = segment["high"].to_numpy()
        lows = segment["low"].to_numpy()

        # 🔥 Detect pivots (simple but effective)
        pivot_highs = []
        pivot_lows = []

        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                pivot_highs.append((start + i, highs[i]))

            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                pivot_lows.append((start + i, lows[i]))

        if not pivot_highs or not pivot_lows:
            return {"passed": False, "reason": "NO_PIVOTS_FOUND"}, None

        # -------------------------
        # STEP 3: Define CHOCH level
        # -------------------------
        if sweep.direction == "BULLISH":
            # 🔥 last LOWER HIGH
            choch_level = pivot_highs[-1][1]

        else:
            # 🔥 last HIGHER LOW
            choch_level = pivot_lows[-1][1]

        # -------------------------
        # STEP 4: Scan for BODY BREAK
        # -------------------------
        for i in range(m5_sweep_idx + 1, end_idx):

            close_price = float(m5_df["close"].iat[i])

            # =========================
            # ✅ BULLISH CHOCH
            # =========================
            if sweep.direction == "BULLISH":

                if close_price > choch_level:

                    sb = StructureBreak(
                        direction="BULLISH",
                        choch_label="CHOCH",
                        level=choch_level,
                        candle_index=i,
                        close_price=close_price,
                    )

                    return {
                        "passed": True,
                        "reason": "VALID_BULLISH_CHOCH",
                        "level": self._r(choch_level),
                        "close_price": self._r(close_price),
                        "m5_candle_index": i,
                    }, sb

            # =========================
            # ✅ BEARISH CHOCH
            # =========================
            else:

                if close_price < choch_level:

                    sb = StructureBreak(
                        direction="BEARISH",
                        choch_label="CHOCH",
                        level=choch_level,
                        candle_index=i,
                        close_price=close_price,
                    )

                    return {
                        "passed": True,
                        "reason": "VALID_BEARISH_CHOCH",
                        "level": self._r(choch_level),
                        "close_price": self._r(close_price),
                        "m5_candle_index": i,
                    }, sb

        return {"passed": False, "reason": "NO_CHOCH"}, None

    def _step_valid_poi(
        self,
        m15_df: pd.DataFrame,
        m5_df: pd.DataFrame,
        h4_df: pd.DataFrame,  # 🔥 NEW
        sweep: SweepEvent,
        structure_break: StructureBreak,
    ) -> Tuple[Dict[str, Any], List[POI]]:

        # -------------------------
        # STEP 1: Build M15 POIs (same as before)
        # -------------------------
        break_time = self._get_candle_time(m5_df, structure_break.candle_index)
        break_m15_idx = self._find_bar_at_or_before(m15_df, break_time)
        if break_m15_idx is None:
            break_m15_idx = len(m15_df) - 1

        lookback = 20
        start = max(0, sweep.candle_index - lookback)
        end = min(len(m15_df) - 1, break_m15_idx)

        segment = m15_df.iloc[start:end + 1]

        if sweep.direction == "BULLISH":
            opposite_mask = (segment["close"] < segment["open"]).to_numpy()
        else:
            opposite_mask = (segment["close"] > segment["open"]).to_numpy()

        opp_abs = [start + int(i) for i in np.where(opposite_mask)[0]]
        if not opp_abs:
            return {"passed": False, "reason": "NO_VALID_POI"}, []

        candidates: List[POI] = []

        for idx in opp_abs:
            candidates.append(self._build_poi(m15_df, "OB", idx))

        # -------------------------
        # STEP 2: Detect H4 OB (VERY IMPORTANT)
        # -------------------------
        if len(h4_df) < 10:
            return {"passed": False, "reason": "NO_H4_DATA"}, []

        h4_segment = h4_df.iloc[-20:]

        if sweep.direction == "BULLISH":
            # last bearish candle = OB
            mask = h4_segment["close"] < h4_segment["open"]
        else:
            mask = h4_segment["close"] > h4_segment["open"]

        h4_candidates = h4_segment[mask]

        if h4_candidates.empty:
            return {"passed": False, "reason": "NO_HTF_OB"}, []

        last_h4_idx = h4_candidates.index[-1]

        h4_low = float(h4_df.loc[last_h4_idx]["low"])
        h4_high = float(h4_df.loc[last_h4_idx]["high"])

        # -------------------------
        # STEP 3: FILTER M15 POIs INSIDE H4 OB
        # -------------------------
        valid_pois = []

        for poi in candidates:

            overlap = not (poi.high < h4_low or poi.low > h4_high)

            if overlap:
                valid_pois.append(poi)

        if not valid_pois:
            return {"passed": False, "reason": "NO_POI_IN_HTF_OB"}, []

        # -------------------------
        # STEP 4: RETURN
        # -------------------------
        return {
            "passed": True,
            "reason": "HTF_ALIGNED_POI",
            "h4_zone": (self._r(h4_low), self._r(h4_high)),
            "poi_count": len(valid_pois),
        }, valid_pois

    def _step_ob_fvg_confluence(
        self,
        m5_df: pd.DataFrame,
        m15_df: pd.DataFrame,
        direction: str,
        sweep: SweepEvent,
        structure_break: StructureBreak,
        poi_candidates: List[POI],
    ) -> Tuple[Dict[str, Any], Optional[POI], Optional[FVG], Optional[float]]:

        sweep_time = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)
        if m5_sweep_idx is None:
            m5_sweep_idx = len(m5_df) - 1

        fvg_start = structure_break.candle_index
        fvg_end = min(len(m5_df) - 2, structure_break.candle_index + 20)

        if fvg_end < fvg_start:
            fvg_end = len(m5_df) - 2

        fvg_list = self._find_fvgs(m5_df, direction, start=fvg_start, end=fvg_end)

        if not fvg_list:
            return {"passed": False, "reason": "FVG_NOT_FOUND"}, None, None, None

        best: Optional[Tuple[POI, FVG, float, float]] = None

        for poi in poi_candidates:
            poi_size = max(poi.high - poi.low, 1e-9)

            for fvg in fvg_list:

                overlap_low = max(poi.low, fvg.low)
                overlap_high = min(poi.high, fvg.high)

                has_overlap = overlap_high > overlap_low

                distance = min(
                    abs(poi.low - fvg.high),
                    abs(poi.high - fvg.low)
                )

                is_near = distance <= poi_size

                if not has_overlap and not is_near:
                    continue

                if has_overlap:
                    score = (overlap_high - overlap_low) / poi_size
                else:
                    score = 1.0 - min(distance / poi_size, 1.0)

                if has_overlap:
                    entry = (overlap_low + overlap_high) / 2.0
                else:
                    entry = (fvg.low + fvg.high) / 2.0

                if best is None or score > best[3]:
                    best = (poi, fvg, entry, score)

        if best is None:
            return {"passed": False, "reason": "OB_FVG_CONFLUENCE_MISSING"}, None, None, None

        poi, fvg, entry, score = best

        displacement_valid = self.is_displacement_after_poi(poi, m5_df, direction)

        if not displacement_valid:
            if best is not None:
                _, _, _, safe_ratio = best
            else:
                safe_ratio = 0

            if safe_ratio > 0.6:
                return {
                    "passed": True,
                    "reason": "RELAXED_NO_DISPLACEMENT",
                    "poi_type": poi.poi_type,
                    "poi_zone": (self._r(poi.low), self._r(poi.high)),
                    "fvg_zone": (self._r(fvg.low), self._r(fvg.high)),
                    "entry_price": self._r(entry),
                    "overlap_ratio": round(float(safe_ratio), 4),
                }, poi, fvg, entry

            return {"passed": False, "reason": "NO_DISPLACEMENT_AFTER_POI"}, None, None, None

        return {
            "passed": True,
            "reason": "OK",
            "poi_type": poi.poi_type,
            "poi_zone": (self._r(poi.low), self._r(poi.high)),
            "fvg_zone": (self._r(fvg.low), self._r(fvg.high)),
            "entry_price": self._r(entry),
            "confluence_score": round(float(score), 4),
        }, poi, fvg, entry

    def _step_dealing_range(
        self,
        direction: str,
        entry_price: float,
        sweep: SweepEvent,
    ) -> Dict[str, Any]:

        dealing_low = min(sweep.sweep_price, sweep.target_external_liquidity)
        dealing_high = max(sweep.sweep_price, sweep.target_external_liquidity)

        eq = (dealing_low + dealing_high) / 2.0
        range_size = dealing_high - dealing_low

        # 🔥 SMART TOLERANCE
        tol_range = range_size * 0.02
        tol_price = eq * 0.001  # 0.1% price-based
        tol = max(tol_range, tol_price)  # adaptive floor

        if direction == "BULLISH":
            passed = entry_price <= (eq + tol)
            reason = "OK" if passed else "NOT_IN_DISCOUNT_ZONE"
        else:
            passed = entry_price >= (eq - tol)
            reason = "OK" if passed else "NOT_IN_PREMIUM_ZONE"

        return {
            "passed": passed,
            "reason": reason,
            "dealing_low": self._r(dealing_low),
            "dealing_high": self._r(dealing_high),
            "equilibrium": self._r(eq),
            "entry_price": self._r(entry_price),
            "tolerance": self._r(tol),
            "zone": (
                "DISCOUNT"
                if entry_price < eq
                else ("PREMIUM" if entry_price > eq else "EQUILIBRIUM")
            ),
        }

    def _step_killzone(
        self,
        now_utc: Optional[datetime],
        m5_df: pd.DataFrame,
    ) -> Dict[str, Any]:

        ts = self._resolve_now_utc(now_utc, m5_df)
        t = ts.time()

        session: Optional[str] = None

        for (kz_start, kz_end, kz_name) in KILLZONES_UTC:
            if kz_start <= t < kz_end:
                session = kz_name
                break

        active = session is not None

        # 🔥 BLOCK TRADES OUTSIDE KILLZONE
        if not active:
            return {
                "passed": False,  # ❌ BLOCK TRADE
                "reason": "OUTSIDE_KILLZONE",
                "timestamp_utc": ts.isoformat(),
                "session": None,
                "killzone_active": False,
            }

        # ✅ ALLOW TRADE
        return {
            "passed": True,
            "reason": f"INSIDE_{session}_KILLZONE",
            "timestamp_utc": ts.isoformat(),
            "session": session,
            "killzone_active": True,
        }

    def _step_rr(
        self,
        direction: str,
        entry_price: float,
        sweep: SweepEvent,
    ) -> Tuple[Dict[str, Any], Optional[float], Optional[float]]:

        atr_buf = sweep.atr_at_sweep * self.config.atr_sl_multiplier

        # === SL / TP BASE ===
        if direction == "BULLISH":
            sl = sweep.sweep_price - atr_buf
            tp_main = sweep.target_external_liquidity
            risk = entry_price - sl
            reward = tp_main - entry_price
        else:
            sl = sweep.sweep_price + atr_buf
            tp_main = sweep.target_external_liquidity
            risk = sl - entry_price
            reward = entry_price - tp_main

        # === SAFETY CHECK ===
        if risk <= 0 or reward <= 0:
            return {"passed": False, "reason": "INVALID_TRADE_GEOMETRY"}, None, None

        rr = reward / risk

        # === DYNAMIC RR THRESHOLD ===
        rr_min = self.config.rr_min

        # 🔥 Relax RR slightly if structure is strong
        if sweep.atr_at_sweep > 0:
            rr_min = max(1.2, rr_min - 0.2)

        # === FALLBACK TP LOGIC ===
        tp = tp_main

        if rr < rr_min:
            # 🔥 Try closer TP (partial liquidity / mid target)
            if direction == "BULLISH":
                tp_alt = entry_price + (risk * rr_min)
            else:
                tp_alt = entry_price - (risk * rr_min)

            alt_reward = abs(tp_alt - entry_price)
            alt_rr = alt_reward / risk

            # ✅ Accept if adjusted RR works
            if alt_rr >= rr_min:
                tp = tp_alt
                rr = alt_rr
            else:
                return {
                    "passed": False,
                    "reason": "RR_BELOW_MINIMUM",
                    "rr": round(float(rr), 4),
                    "rr_min": rr_min,
                }, None, None

        # === FINAL OUTPUT ===
        return {
            "passed": True,
            "reason": "OK",
            "rr": round(float(rr), 4),
            "rr_min": rr_min,
            "entry": self._r(entry_price),
            "sl": self._r(sl),
            "tp": self._r(tp),
            "risk_pts": self._r(risk),
            "reward_pts": self._r(abs(tp - entry_price)),
            "atr_buffer": self._r(atr_buf),
        }, sl, tp

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def detect_swing_points(
        self,
        df: pd.DataFrame,
        window: int,
    ) -> Tuple[List[int], List[int]]:
        """
        Helper: Return confirmed structure-based swing highs and lows.
        A swing high is confirmed only if a lower pivot follows (IDM taken).
        A swing low is confirmed only if a higher pivot follows (IDM taken).
        This mirrors Guardeer's IDM confirmation rule from Lecture 3.
        """
        # raw_highs, raw_lows = self._find_pivots(df, window)

        confirmed_highs: List[int] = []
        confirmed_lows: List[int] = []

        for sh_idx in raw_highs:
            following_lows = [sl for sl in raw_lows if sl > sh_idx]
            if following_lows:
                confirmed_highs.append(sh_idx)

        for sl_idx in raw_lows:
            following_highs = [sh for sh in raw_highs if sh > sl_idx]
            if following_highs:
                confirmed_lows.append(sl_idx)

        return confirmed_highs, confirmed_lows

    # def _classify_sweep_strength(
    #     self,
    #     curr_low_or_high: float,
    #     ref_level: float,
    #     atr: float,
    #     is_bullish_sweep: bool,
    # ) -> str:
    #     """
    #     Classify sweep strength based on wick penetration distance.
    #     strong: wick > 50% of ATR beyond level
    #     weak:   wick <= 50% of ATR beyond level
    #     """
    #     penetration = abs(curr_low_or_high - ref_level)
    #     threshold = atr * 0.5
    #     return "strong" if penetration >= threshold else "weak"

    # def _find_bullish_sweep(
    #     self,
    #     df: pd.DataFrame,
    #     ext_highs: List[int],
    #     ext_lows: List[int],
    # ) -> Optional[SweepEvent]:
    #     """
    #     BULLISH sweep = sell-side liquidity grab
    #     Rule:
    #       - current.low < previous confirmed swing LOW (wick below)
    #       - current.close > previous confirmed swing LOW (closes back inside)
    #     ATR-based tolerance buffer applied to avoid exact-equality rejections.
    #     HTF bias = BULLISH → only scan for SELL-SIDE (low) sweeps.
    #     """
    #     confirmed_highs, confirmed_lows = self.detect_swing_points(df, self.config.external_swing_window)

    #     if not confirmed_lows:
    #         confirmed_lows = list(ext_lows)
    #     if not confirmed_lows:
    #         fallback_idx = int(np.argmin(df["low"].to_numpy(dtype=float)))
    #         confirmed_lows = [fallback_idx]

    #     start_idx = max(self.config.external_swing_window + 1, len(df) - self.config.recent_sweep_bars)

    #     for i in range(len(df) - 1, start_idx - 1, -1):
    #         prior_lows = [idx for idx in confirmed_lows if idx < i]
    #         if not prior_lows:
    #             continue

    #         ref_idx = prior_lows[-1]
    #         ref_level = float(df["low"].iat[ref_idx])

    #         atr = self._calc_atr(df, i, self.config.atr_period)
    #         tolerance = atr * self.config.sweep_atr_tolerance

    #         curr_low = float(df["low"].iat[i])
    #         curr_close = float(df["close"].iat[i])
    #         curr_high = float(df["high"].iat[i])
    #         curr_open = float(df["open"].iat[i])

    #         wick_grabs_below = curr_low < (ref_level + tolerance)
    #         close_back_inside = curr_close > (ref_level - tolerance)

    #         if not (wick_grabs_below and close_back_inside):
    #             continue

    #         future_highs = [idx for idx in confirmed_highs if idx > i]
    #         if not future_highs:
    #             future_highs = [idx for idx in ext_highs if idx > i]

    #         if future_highs:
    #             tp_level = float(df["high"].iat[future_highs[0]])
    #         else:
    #             w_start = max(0, i - self.config.liquidity_lookback)
    #             tp_level = float(df["high"].iloc[w_start:i].max())

    #         if np.isnan(tp_level):
    #             continue

    #         strength = self._classify_sweep_strength(curr_low, ref_level, atr, is_bullish_sweep=True)

    #         return SweepEvent(
    #             direction="BULLISH",
    #             sweep_side="SELL_SIDE",
    #             reference_index=ref_idx,
    #             reference_level=ref_level,
    #             candle_index=i,
    #             sweep_price=curr_low,
    #             close_back_inside=curr_close,
    #             target_external_liquidity=tp_level,
    #             atr_at_sweep=atr,
    #         )

    #     return None

    # def _find_bearish_sweep(
    #     self,
    #     df: pd.DataFrame,
    #     ext_highs: List[int],
    #     ext_lows: List[int],
    # ) -> Optional[SweepEvent]:
        """
        BEARISH sweep = buy-side liquidity grab
        Rule:
          - current.high > previous confirmed swing HIGH (wick above)
          - current.close < previous confirmed swing HIGH (closes back inside)
        ATR-based tolerance buffer applied to avoid exact-equality rejections.
        HTF bias = BEARISH → only scan for BUY-SIDE (high) sweeps.
        """
        confirmed_highs, confirmed_lows = self.detect_swing_points(df, self.config.external_swing_window)

        if not confirmed_highs:
            confirmed_highs = list(ext_highs)
        if not confirmed_highs:
            fallback_idx = int(np.argmax(df["high"].to_numpy(dtype=float)))
            confirmed_highs = [fallback_idx]

        start_idx = max(self.config.external_swing_window + 1, len(df) - self.config.recent_sweep_bars)

        for i in range(len(df) - 1, start_idx - 1, -1):
            prior_highs = [idx for idx in confirmed_highs if idx < i]
            if not prior_highs:
                continue

            ref_idx = prior_highs[-1]
            ref_level = float(df["high"].iat[ref_idx])

            atr = self._calc_atr(df, i, self.config.atr_period)
            tolerance = atr * self.config.sweep_atr_tolerance

            curr_high = float(df["high"].iat[i])
            curr_close = float(df["close"].iat[i])
            curr_low = float(df["low"].iat[i])
            curr_open = float(df["open"].iat[i])

            wick_grabs_above = curr_high > (ref_level - tolerance)
            close_back_inside = curr_close < (ref_level + tolerance)

            if not (wick_grabs_above and close_back_inside):
                continue

            future_lows = [idx for idx in confirmed_lows if idx > i]
            if not future_lows:
                future_lows = [idx for idx in ext_lows if idx > i]

            if future_lows:
                tp_level = float(df["low"].iat[future_lows[0]])
            else:
                w_start = max(0, i - self.config.liquidity_lookback)
                tp_level = float(df["low"].iloc[w_start:i].min())

            if np.isnan(tp_level):
                continue

            strength = self._classify_sweep_strength(curr_high, ref_level, atr, is_bullish_sweep=False)

            return SweepEvent(
                direction="BEARISH",
                sweep_side="BUY_SIDE",
                reference_index=ref_idx,
                reference_level=ref_level,
                candle_index=i,
                sweep_price=curr_high,
                close_back_inside=curr_close,
                target_external_liquidity=tp_level,
                atr_at_sweep=atr,
            )

        return None

    def is_displacement_after_poi(self, poi: POI, df: pd.DataFrame, direction: str) -> bool:

        poi_time = self._get_candle_time(df, poi.candle_index)
        m5_idx = self._find_bar_at_or_after(df, poi_time)

        if m5_idx is None:
            return False

        if len(df) < m5_idx + 5:
            return False

        end_idx = min(len(df), m5_idx + 10)

        for i in range(m5_idx + 1, end_idx):

            open_p = float(df["open"].iat[i])
            close_p = float(df["close"].iat[i])
            high_p = float(df["high"].iat[i])
            low_p = float(df["low"].iat[i])

            body = abs(close_p - open_p)
            range_ = high_p - low_p

            if range_ == 0:
                continue

            body_ratio = body / range_
            atr = self._calc_atr(df, i, 14)

            start_idx = max(0, i - 3)

            recent_high_slice = df["high"].iloc[start_idx:i]
            recent_low_slice = df["low"].iloc[start_idx:i]

            if len(recent_high_slice) == 0 or len(recent_low_slice) == 0:
                continue

            recent_high = recent_high_slice.max()
            recent_low = recent_low_slice.min()

            if direction == "BULLISH":
                if (
                    close_p > open_p
                    or body_ratio > 0.30
                    or close_p >= recent_high * 0.999

                    or close_p > df["close"].iloc[max(0, i-2):i].max()
                ):
                    return True

            else:
                if (
                    close_p < open_p
                    or body_ratio > 0.30
                    or close_p < recent_low
                    or close_p < df["close"].iloc[max(0, i-2):i].min()
                ):
                    return True

        return False

    def _calc_atr(self, df: pd.DataFrame, end_idx: int, period: int) -> float:
        start = max(1, end_idx - period + 1)
        highs = df["high"].to_numpy(dtype=float)[start : end_idx + 1]
        lows = df["low"].to_numpy(dtype=float)[start : end_idx + 1]
        closes = df["close"].to_numpy(dtype=float)[start - 1 : end_idx]
        if len(highs) < 2:
            return float(highs[-1] - lows[-1]) if len(highs) == 1 else 1.0
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        return float(np.mean(tr))

    def _infer_bias(self, df: pd.DataFrame, window: int) -> str:
        if len(df) < window + 5:
            return "NEUTRAL"

        lookback = max(window * 6, 30)
        recent = df.iloc[-lookback:]
        ph, pl = self._find_pivots_debug(recent, max(2, min(3, window)))

        if len(ph) >= 3:
            vals = [float(recent["high"].iloc[i]) for i in ph[-3:]]
            if vals[0] < vals[1] < vals[2]:
                return "BULLISH"

        if len(pl) >= 3:
            vals = [float(recent["low"].iloc[i]) for i in pl[-3:]]
            if vals[0] > vals[1] > vals[2]:
                return "BEARISH"

        last_close = float(recent["close"].iat[-1])
        if len(ph) >= 1 and last_close > float(recent["high"].iloc[ph[-1]]):
            return "BULLISH"
        if len(pl) >= 1 and last_close < float(recent["low"].iloc[pl[-1]]):
            return "BEARISH"

        return "NEUTRAL"

    def _find_pivots_debug(self, df: pd.DataFrame, window: int) -> Tuple[List[int], List[int]]:
        n = len(df)
        effective_window = window
        while effective_window > 1 and n < (effective_window * 2 + 1):
            effective_window -= 1

        if effective_window < 1:
            return [], []

        if effective_window != window:
            print(f"⚠️ [SWING] Reduced pivot window {window} → {effective_window} due to small dataset (n={n})")

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        ph: List[int] = []
        pl: List[int] = []

        for i in range(effective_window, n - effective_window):
            h = highs[i]
            l = lows[i]

            if (
                h > highs[i - effective_window : i].max()
                and h > highs[i + 1 : i + effective_window + 1].max()
            ):
                ph.append(i)

            if (
                l < lows[i - effective_window : i].min()
                and l < lows[i + 1 : i + effective_window + 1].min()
            ):
                pl.append(i)

        return ph, pl

    def _find_fvgs(
        self,
        df: pd.DataFrame,
        direction: str,
        start: int,
        end: int,
    ) -> List[FVG]:

        fvgs: List[FVG] = []
        end = min(end, len(df) - 2)

        for i in range(max(2, start), end + 1):

            c1h = float(df["high"].iat[i - 1])
            c1l = float(df["low"].iat[i - 1])

            c2h = float(df["high"].iat[i])
            c2l = float(df["low"].iat[i])
            c2o = float(df["open"].iat[i])
            c2c = float(df["close"].iat[i])

            c3h = float(df["high"].iat[i + 1])
            c3l = float(df["low"].iat[i + 1])

            body = abs(c2c - c2o)
            range_ = c2h - c2l

            if range_ == 0:
                continue

            body_ratio = body / range_

            # STRICT FVG
            if direction == "BULLISH" and c1h < c3l:
                fvgs.append(FVG("BULLISH", i, low=c1h, high=c3l))
                continue

            if direction == "BEARISH" and c1l > c3h:
                fvgs.append(FVG("BEARISH", i, low=c3h, high=c1l))
                continue

            # RELAXED IMBALANCE
            if direction == "BULLISH":
                gap = c3l - c1h
                if gap > 0 or body_ratio > 0.5:
                    fvgs.append(FVG("BULLISH", i, low=c1h, high=c3l))

            else:
                gap = c1l - c3h
                if gap > 0 or body_ratio > 0.5:
                    fvgs.append(FVG("BEARISH", i, low=c3h, high=c1l))

            # DISPLACEMENT FALLBACK
            if direction == "BULLISH" and c2c > df["high"].iloc[i-3:i].max():
                fvgs.append(FVG("BULLISH", i, low=c2l, high=c2h))

            if direction == "BEARISH" and c2c < df["low"].iloc[i-3:i].min():
                fvgs.append(FVG("BEARISH", i, low=c2l, high=c2h))

        return fvgs

    def _build_poi(self, df: pd.DataFrame, poi_type: str, idx: int) -> POI:
        h = float(df["high"].iat[idx])
        l = float(df["low"].iat[idx])
        return POI(poi_type=poi_type, candle_index=idx, low=min(l, h), high=max(l, h))

    def _dedupe_pois(self, pois: List[POI]) -> List[POI]:
        seen: set = set()
        out: List[POI] = []
        for p in pois:
            key = (p.poi_type, p.candle_index, round(p.low, 5), round(p.high, 5))
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    def _get_candle_time(self, df: pd.DataFrame, idx: int) -> Optional[datetime]:
        if idx < 0 or idx >= len(df):
            return None

        if self.config.time_column in df.columns:
            val = df[self.config.time_column].iat[idx]
            return self._normalize_datetime(val)

        if isinstance(df.index, pd.DatetimeIndex):
            return self._normalize_datetime(df.index[idx])

        return None

    def _find_bar_at_or_after(
        self,
        df: pd.DataFrame,
        target_time: Optional[datetime],
    ) -> Optional[int]:
        if target_time is None:
            return None

        target = self._normalize_datetime(target_time)
        if target is None:
            return None

        times = self._time_values(df)
        if not times:
            return None

        for idx, value in enumerate(times):
            current = self._normalize_datetime(value)
            if current is None:
                continue
            if current >= target:
                return idx
        return None

    def _find_bar_at_or_before(
        self,
        df: pd.DataFrame,
        target_time: Optional[datetime],
    ) -> Optional[int]:
        if target_time is None:
            return None

        target = self._normalize_datetime(target_time)
        if target is None:
            return None

        times = self._time_values(df)
        if not times:
            return None

        last_idx: Optional[int] = None
        for idx, value in enumerate(times):
            current = self._normalize_datetime(value)
            if current is None:
                continue
            if current <= target:
                last_idx = idx
            else:
                break
        return last_idx

    def _time_values(self, df: pd.DataFrame) -> List[Any]:
        if self.config.time_column in df.columns:
            return list(df[self.config.time_column].values)
        if isinstance(df.index, pd.DatetimeIndex):
            return list(df.index)
        return []

    def _normalize_datetime(self, value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, pd.Timestamp):
            dt = value.to_pydatetime()
        elif isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float, np.integer, np.floating)):
            try:
                dt = datetime.fromtimestamp(int(value), tz=timezone.utc)
            except Exception:
                return None
        elif hasattr(value, "to_pydatetime"):
            try:
                dt = value.to_pydatetime()
            except Exception:
                return None
        else:
            try:
                ts = pd.Timestamp(value)
                dt = ts.to_pydatetime()
            except Exception:
                return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _normalize_ohlc(self, df: pd.DataFrame, name: str) -> pd.DataFrame:
        if not isinstance(df, pd.DataFrame):
            print(f"{name} invalid input type")
            return pd.DataFrame()

        out = df.copy()
        out.columns = [str(c).lower().strip() for c in out.columns]

        cfg = self.config
        required = {cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col}
        missing = required.difference(out.columns)
        if missing:
            print(f"{name} missing columns: {sorted(missing)}")
            return pd.DataFrame()

        cols = ["open", "high", "low", "close"]

        out[cols] = out[cols].apply(pd.to_numeric, errors="coerce")

        before = len(out)
        out = out.dropna(subset=[cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col])
        out = out.reset_index(drop=True)
        dropped = before - len(out)
        if dropped > 0:
            print(f"{name} dropped {dropped} rows with invalid OHLC values")

        if isinstance(out.index, pd.DatetimeIndex):
            out[cfg.time_column] = out.index
        else:
            out[cfg.time_column] = pd.RangeIndex(len(out))

        if not pd.api.types.is_datetime64_any_dtype(out[cfg.time_column]):
            sample = out[cfg.time_column].dropna()
            if len(sample) > 0:
                first_val = sample.iloc[0]
                if isinstance(first_val, (int, float, np.integer, np.floating)):
                    out[cfg.time_column] = pd.to_datetime(out[cfg.time_column], unit="s", utc=True)
                else:
                    try:
                        out[cfg.time_column] = pd.to_datetime(out[cfg.time_column], utc=True)
                    except Exception:
                        pass

        out = out.sort_values(cfg.time_column, kind="stable")
        out = out.reset_index(drop=True)

        if len(out) == 0:
            print(f"{name} empty after normalization")
            return pd.DataFrame()

        return out

    def _resolve_now_utc(
        self,
        now_utc: Optional[datetime],
        df: pd.DataFrame,
    ) -> datetime:
        if now_utc is not None:
            normalized = self._normalize_datetime(now_utc)
            if normalized is not None:
                return normalized
            return datetime.now(timezone.utc)

        if len(df) > 0:
            ts = self._get_candle_time(df, len(df) - 1)
            if ts is not None:
                return ts

        return datetime.now(timezone.utc)

    def _init_gates(self) -> Dict[str, Dict[str, Any]]:
        return {
            g: {"passed": False, "reason": "NOT_EVALUATED"}
            for g in [
                "step_1_htf_bias",
                "step_2_external_liquidity_sweep",
                "step_3_choch_mss_body_close",
                "step_4_valid_poi",
                "step_5_ob_fvg_confluence",
                "step_6_dealing_range",
                "step_7_killzone",
                "step_8_risk_reward",
            ]
        }

    def _no_trade(
        self,
        gates: Dict[str, Dict[str, Any]],
        reason: str,
        direction: Optional[str] = None,
    ) -> SignalResult:
        passed_count = sum(1 for g in gates.values() if g.get("passed") is True)
        return SignalResult(
            action="NO_TRADE",
            direction=direction,
            entry_price=None,
            sl_price=None,
            tp_price=None,
            gates=gates,
            reason=reason,
            confidence_score=int(round(passed_count / 8.0 * 100)),
        )

    @staticmethod
    def _r(v: Optional[float]) -> Optional[float]:
        return round(float(v), 2) if v is not None else None