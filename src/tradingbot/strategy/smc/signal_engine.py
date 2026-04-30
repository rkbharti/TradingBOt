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
    (time(0, 0),  time(3, 0),   "ASIAN"),
    (time(7, 0),  time(9, 0),   "LONDON"),
    (time(13, 0), time(15, 30), "NEW_YORK"),
    (time(16, 0), time(18, 0),  "LONDON_CLOSE"),
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

    sweep_atr_tolerance: float = 0.05
    min_atr_threshold: float = 3.0

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

        if not hasattr(self, 'rejection_counts'):
            self.rejection_counts = {}

        def _reject(gates, reason, direction=None):
            self.rejection_counts[reason] = self.rejection_counts.get(reason, 0) + 1
            return self._no_trade(gates, reason, direction=direction)

        gates = self._init_gates()

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
        
        # ─── Step 0: ATR Regime Filter ──────────────────────────────
        current_atr = self._calc_atr(m5_df, len(m5_df) - 1, self.config.atr_period)
        if current_atr < self.config.min_atr_threshold:
            return _reject(gates, "LOW_VOLATILITY_REGIME")

        # ─── Step 1: HTF Bias ───────────────────────────────────────────────
        step1 = self._step_htf_bias(d1_df, h4_df)
        gates["step_1_htf_bias"] = step1
        if not step1["passed"]:
            return _reject(gates, step1["reason"])

        direction: str = step1["direction"]

        # ─── Step 2: External Liquidity Sweep ───────────────────────────────
        step2, sweep = self._step_external_liquidity_sweep(m15_df, direction)
        gates["step_2_external_liquidity_sweep"] = step2
        if not step2["passed"] or sweep is None:
            return _reject(gates, step2["reason"], direction=direction)

        # ─── Step 3: CHOCH / MSS Body Close ─────────────────────────────────
        step3, structure_break = self._step_choch_mss_body_close(m5_df, sweep, m15_df)
        gates["step_3_choch_mss_body_close"] = step3
        if not step3["passed"] or structure_break is None:
            return _reject(gates, step3["reason"], direction=direction)

        # ─── Step 4: Valid POI ───────────────────────────────────────────────
        step4, poi_candidates = self._step_valid_poi(m15_df, m5_df, h4_df, sweep, structure_break)
        gates["step_4_valid_poi"] = step4
        if not step4["passed"] or not poi_candidates:
            return _reject(gates, step4["reason"], direction=direction)

        # ─── Step 5: OB/FVG Confluence ──────────────────────────────────────
        step5, selected_poi, selected_fvg, entry_price = self._step_ob_fvg_confluence(
            m5_df, m15_df, direction, sweep, structure_break, poi_candidates
        )
        gates["step_5_ob_fvg_confluence"] = step5
        if not step5["passed"] or selected_poi is None or selected_fvg is None or entry_price is None:
            return _reject(gates, step5["reason"], direction=direction)

        # ─── Step 6: Dealing Range ───────────────────────────────────────────
        step6 = self._step_dealing_range(direction, entry_price, sweep)
        gates["step_6_dealing_range"] = step6
        if not step6["passed"]:
            return _reject(gates, step6["reason"], direction=direction)

        # ─── Step 7: Killzone ────────────────────────────────────────────────
        step7 = self._step_killzone(now_utc, m5_df)
        gates["step_7_killzone"] = step7
        if not step7["passed"]:
            return _reject(gates, step7["reason"], direction=direction)

        # ─── Step 8: Risk/Reward ─────────────────────────────────────────────
        step8, sl_price, tp_price = self._step_rr(direction, entry_price, sweep, selected_poi)
        gates["step_8_risk_reward"] = step8
        if not step8["passed"]:
            return _reject(gates, step8["reason"], direction=direction)

        # ─── All Gates Passed ────────────────────────────────────────────────
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

    def print_gate_summary(self):
        if not hasattr(self, 'rejection_counts') or not self.rejection_counts:
            print("No rejection data recorded.")
            return
        total = sum(self.rejection_counts.values())
        print("\n📊 GATE REJECTION SUMMARY:")
        print("-" * 45)
        for reason, count in sorted(self.rejection_counts.items(), key=lambda x: -x[1]):
            pct = (count / total) * 100
            print(f"  {reason:<30} → {count:>6} ({pct:.1f}%)")
        print(f"  {'TOTAL REJECTIONS':<30} → {total:>6}")
        print("-" * 45)

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
        # ✅ CASE 2 — D1 DOMINANT (H4 conflict or H4 neutral)
        # =========================
        if d1_bias in {"BULLISH", "BEARISH"}:
            return {
                "passed": True,
                "direction": d1_bias,
                "reason": "D1_DOMINANT",
                "d1_bias": d1_bias,
                "h4_bias": h4_bias,
                "agreement_score": self.config.d1_weight,
            }

        # =========================
        # ✅ CASE 3 — D1 NEUTRAL → TRUST H4
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
        # ❌ CASE 4 — BOTH NEUTRAL (genuine no-trade)
        # =========================
        return {
            "passed": False,
            "direction": None,
            "reason": "NO_HTF_DIRECTION",
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

        confirmed_highs, confirmed_lows = self.detect_swing_points(df, cfg.external_swing_window)

        start_idx = cfg.external_swing_window + 2

        for i in range(len(df) - 1, start_idx - 1, -1):
            curr_high  = float(df["high"].iat[i])
            curr_low   = float(df["low"].iat[i])
            curr_close = float(df["close"].iat[i])
            atr        = self._calc_atr(df, i, cfg.atr_period)
            tolerance  = atr * cfg.sweep_atr_tolerance

            if direction == "BULLISH":
                prior_lows = [idx for idx in confirmed_lows if idx < i]
                if not prior_lows:
                    continue

                for ref_idx in reversed(prior_lows[-2:]):
                    ref_level = float(df["low"].iat[ref_idx])

                    wick_break = curr_low   < (ref_level - tolerance)
                    close_back = curr_close > ref_level

                    if wick_break and close_back:
                        left_highs = [idx for idx in confirmed_highs if idx < i]
                        tp = (
                            float(df["high"].iat[left_highs[-1]])
                            if left_highs
                            else float(df["high"].iloc[max(0, i - cfg.liquidity_lookback):i].max())
                        )
                        return {
                            "passed": True,
                            "reason": "VALID_BULLISH_SWEEP",
                            "reference_level": self._r(ref_level),
                            "sweep_price": self._r(curr_low),
                            "target_external_liquidity": self._r(tp),
                            "candle_index": i,
                        }, SweepEvent(
                            direction="BULLISH",
                            sweep_side="SELL_SIDE",
                            reference_index=ref_idx,
                            reference_level=ref_level,
                            candle_index=i,
                            sweep_price=curr_low,
                            close_back_inside=curr_close,
                            target_external_liquidity=tp,
                            atr_at_sweep=atr,
                        )

            else:
                prior_highs = [idx for idx in confirmed_highs if idx < i]
                if not prior_highs:
                    continue

                for ref_idx in reversed(prior_highs[-2:]):
                    ref_level = float(df["high"].iat[ref_idx])

                    wick_break = curr_high  > (ref_level + tolerance)
                    close_back = curr_close < ref_level

                    if wick_break and close_back:
                        left_lows = [idx for idx in confirmed_lows if idx < i]
                        tp = (
                            float(df["low"].iat[left_lows[-1]])
                            if left_lows
                            else float(df["low"].iloc[max(0, i - cfg.liquidity_lookback):i].min())
                        )
                        return {
                            "passed": True,
                            "reason": "VALID_BEARISH_SWEEP",
                            "reference_level": self._r(ref_level),
                            "sweep_price": self._r(curr_high),
                            "target_external_liquidity": self._r(tp),
                            "candle_index": i,
                        }, SweepEvent(
                            direction="BEARISH",
                            sweep_side="BUY_SIDE",
                            reference_index=ref_idx,
                            reference_level=ref_level,
                            candle_index=i,
                            sweep_price=curr_high,
                            close_back_inside=curr_close,
                            target_external_liquidity=tp,
                            atr_at_sweep=atr,
                        )

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
        sweep_time   = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)

        if m5_sweep_idx is None:
            return {"passed": False, "reason": "SWEEP_MAPPING_FAILED"}, None

        # Extended scan: 200 bars (~16.5 hrs) catches cross-session CHoCH
        end_idx = min(len(m5_df), m5_sweep_idx + 200)

        # -------------------------
        # STEP 2: IDM-confirmed swings in the pre-sweep window
        # -------------------------
        lookback = max(30, self.config.internal_swing_window * 4)
        start    = max(0, m5_sweep_idx - lookback)

        # Lowered from 10 → 3: sliced windows can be shallow near start
        if (m5_sweep_idx - start) < 3:
            return {"passed": False, "reason": "INSUFFICIENT_STRUCTURE"}, None

        confirmed_highs_rel, confirmed_lows_rel = self.detect_swing_points(
            m5_df.iloc[start:m5_sweep_idx],
            self.config.internal_swing_window,
            use_m5_pivot_detector=True
        )

        confirmed_highs_abs = [start + i for i in confirmed_highs_rel]
        confirmed_lows_abs  = [start + i for i in confirmed_lows_rel]

        # -------------------------
        # STEP 3: LEFT-SIDE swing rule
        # -------------------------
        if sweep.direction == "BULLISH":
            lh_candidates = [idx for idx in confirmed_highs_abs if idx < m5_sweep_idx]

            # Fallback: use raw high from pre-sweep window if IDM finds nothing
            if not lh_candidates:
                fallback_window = m5_df.iloc[start:m5_sweep_idx]
                if fallback_window.empty:
                    return {"passed": False, "reason": "NO_LEFT_SIDE_LH_FOUND"}, None
                choch_idx   = start + int(fallback_window["high"].values.argmax())
                choch_level = float(fallback_window["high"].max())
            else:
                choch_idx   = lh_candidates[-1]
                choch_level = float(m5_df["high"].iat[choch_idx])

        else:
            hl_candidates = [idx for idx in confirmed_lows_abs if idx < m5_sweep_idx]

            # Fallback: use raw low from pre-sweep window if IDM finds nothing
            if not hl_candidates:
                fallback_window = m5_df.iloc[start:m5_sweep_idx]
                if fallback_window.empty:
                    return {"passed": False, "reason": "NO_LEFT_SIDE_HL_FOUND"}, None
                choch_idx   = start + int(fallback_window["low"].values.argmin())
                choch_level = float(fallback_window["low"].min())
            else:
                choch_idx   = hl_candidates[-1]
                choch_level = float(m5_df["low"].iat[choch_idx])

        # -------------------------
        # STEP 4: Scan for BODY CLOSE break
        # -------------------------
        for i in range(m5_sweep_idx + 1, end_idx):

            close_price = float(m5_df["close"].iat[i])

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
        h4_df: pd.DataFrame,
        sweep: SweepEvent,
        structure_break: StructureBreak,
    ) -> Tuple[Dict[str, Any], List[POI]]:

        # -------------------------
        # STEP 1: Build M15 POI candidates
        # -------------------------
        break_time    = self._get_candle_time(m5_df, structure_break.candle_index)
        break_m15_idx = self._find_bar_at_or_before(m15_df, break_time)
        if break_m15_idx is None:
            break_m15_idx = len(m15_df) - 1

        lookback = 20
        start    = max(0, sweep.candle_index - lookback)
        end      = min(len(m15_df) - 1, break_m15_idx)
        segment  = m15_df.iloc[start:end + 1]

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
        # STEP 2: H4 OB detection
        # -------------------------
        if len(h4_df) < 10:
            return {"passed": False, "reason": "NO_H4_DATA"}, []

        h4_segment = h4_df.iloc[-20:]

        if sweep.direction == "BULLISH":
            mask = h4_segment["close"] < h4_segment["open"]
        else:
            mask = h4_segment["close"] > h4_segment["open"]

        h4_candidates = h4_segment[mask]
        if h4_candidates.empty:
            return {"passed": False, "reason": "NO_HTF_OB"}, []

        last_h4_idx = h4_candidates.index[-1]
        h4_low      = float(h4_df.loc[last_h4_idx]["low"])
        h4_high     = float(h4_df.loc[last_h4_idx]["high"])

        # -------------------------
        # STEP 3: Filter M15 POIs inside H4 OB
        # -------------------------
        htf_aligned: List[POI] = []
        for poi in candidates:
            overlap = not (poi.high < h4_low or poi.low > h4_high)
            if overlap:
                htf_aligned.append(poi)

        if not htf_aligned:
            return {"passed": False, "reason": "NO_POI_IN_HTF_OB"}, []

        # -------------------------
        # STEP 4: Extreme OB rule — filter out middle OBs (HIGH-9)
        # Sort by candle_index ascending = closest to CHoCH origin first
        # -------------------------
        htf_aligned.sort(key=lambda p: p.candle_index)

        extreme_ob = htf_aligned[0]   # closest to CHoCH/sweep origin

        extreme_tagged = POI(
            poi_type="EXTREME_OB",
            candle_index=extreme_ob.candle_index,
            low=extreme_ob.low,
            high=extreme_ob.high,
        )

        if len(htf_aligned) > 1:
            first_ob = htf_aligned[-1]   # furthest from origin = First OB after sweep
            first_tagged = POI(
                poi_type="FIRST_OB_AFTER_IDM",
                candle_index=first_ob.candle_index,
                low=first_ob.low,
                high=first_ob.high,
            )
            valid_pois = [extreme_tagged, first_tagged]
        else:
            valid_pois = [extreme_tagged]

        return {
            "passed": True,
            "reason": "HTF_ALIGNED_POI",
            "h4_zone": (self._r(h4_low), self._r(h4_high)),
            "poi_count": len(valid_pois),
            "poi_types": [p.poi_type for p in valid_pois],
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

        sweep_time   = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)
        if m5_sweep_idx is None:
            m5_sweep_idx = len(m5_df) - 1

        fvg_start = structure_break.candle_index
        fvg_end   = min(len(m5_df) - 2, structure_break.candle_index + 20)
        if fvg_end < fvg_start:
            fvg_end = len(m5_df) - 2

        fvg_list = self._find_fvgs(m5_df, direction, start=fvg_start, end=fvg_end)
        if not fvg_list:
            return {"passed": False, "reason": "FVG_NOT_FOUND"}, None, None, None

        best: Optional[Tuple[POI, FVG, float, float]] = None

        for poi in poi_candidates:
            poi_size = max(poi.high - poi.low, 1e-9)

            for fvg in fvg_list:

                overlap_low  = max(poi.low, fvg.low)
                overlap_high = min(poi.high, fvg.high)
                has_overlap  = overlap_high > overlap_low

                distance = min(
                    abs(poi.low - fvg.high),
                    abs(poi.high - fvg.low),
                )
                is_near = distance <= poi_size

                if not has_overlap and not is_near:
                    continue

                if has_overlap:
                    score = (overlap_high - overlap_low) / poi_size
                else:
                    score = 1.0 - min(distance / poi_size, 1.0)

                # -------------------------------------------------------
                # HIGH-10 FIX: entry = zone boundary, NOT candle close
                # -------------------------------------------------------
                if direction == "BULLISH":
                    zone_high = poi.high
                    zone_low  = poi.low
                    entry     = zone_high    # limit buy at TOP of OB/FVG zone
                else:
                    zone_high = poi.high
                    zone_low  = poi.low
                    entry     = zone_low     # limit sell at BOTTOM of OB/FVG zone

                retest_found = False
                scan_start   = structure_break.candle_index
                scan_end     = min(len(m5_df), scan_start + 25)

                for j in range(scan_start, scan_end):
                    candle = m5_df.iloc[j]
                    high   = candle["high"]
                    low    = candle["low"]
                    open_  = candle["open"]
                    close  = candle["close"]

                    body       = abs(close - open_)
                    range_     = max(high - low, 1e-9)
                    strong_body = body > 0.5 * range_

                    if not strong_body:
                        continue

                    if direction == "BULLISH":
                        wick_touch = (low >= zone_low) and (low <= zone_high)
                        rejection  = close > zone_high

                        if wick_touch and rejection:
                            # entry stays at zone_high — DO NOT override with close
                            retest_found = True
                            break

                    else:
                        wick_touch = (high <= zone_high) and (high >= zone_low)
                        rejection  = close < zone_low

                        if wick_touch and rejection:
                            # entry stays at zone_low — DO NOT override with close
                            retest_found = True
                            break

                if not retest_found:
                    continue

                if best is None or score > best[3]:
                    best = (poi, fvg, entry, score)

        if best is None:
            return {"passed": False, "reason": "OB_FVG_CONFLUENCE_MISSING"}, None, None, None

        poi, fvg, entry, score = best

        # Displacement is a hard gate — no relaxed bypass (removed RELAXED_NO_DISPLACEMENT)
        displacement_valid = self.is_displacement_after_poi(poi, m5_df, direction)
        if not displacement_valid:
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
        selected_poi: "POI",                        # HIGH-4: anchor SL to OB, not sweep wick
    ) -> Tuple[Dict[str, Any], Optional[float], Optional[float]]:

        sl_buffer = sweep.atr_at_sweep * self.config.atr_sl_multiplier

        # === SL anchored to OB zone boundary (HIGH-4) ===
        if direction == "BULLISH":
            sl        = selected_poi.low - sl_buffer   # OB low, not sweep wick
            tp        = sweep.target_external_liquidity
            risk      = entry_price - sl
            reward    = tp - entry_price
        else:
            sl        = selected_poi.high + sl_buffer  # OB high, not sweep wick
            tp        = sweep.target_external_liquidity
            risk      = sl - entry_price
            reward    = entry_price - tp

        # === SAFETY CHECK ===
        if risk <= 0 or reward <= 0:
            return {"passed": False, "reason": "INVALID_TRADE_GEOMETRY"}, None, None

        rr = reward / risk

        # === FIXED RR THRESHOLD — no dynamic reduction (HIGH-8) ===
        rr_min = self.config.rr_min   # always use configured value; ATR reduction block removed

        if rr < rr_min:
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
            "sl_buffer": self._r(sl_buffer),
        }, sl, tp

    def _find_m5_choch_pivots(self, df: pd.DataFrame, window: int) -> Tuple[List[int], List[int]]:
        n = len(df)
        effective_window = max(1, min(window, (n - 1) // 2))

        if n < 3:
            return [], []

        highs = df["high"].to_numpy(dtype=float)
        lows  = df["low"].to_numpy(dtype=float)
        ph: List[int] = []
        pl: List[int] = []

        for i in range(effective_window, n):
            right_bars = min(effective_window, n - i - 1)
            if right_bars < 1:
                continue

            h = highs[i]
            l = lows[i]

            if (
                h > highs[i - effective_window : i].max()
                and h > highs[i + 1 : i + right_bars + 1].max()
            ):
                ph.append(i)

            if (
                l < lows[i - effective_window : i].min()
                and l < lows[i + 1 : i + right_bars + 1].min()
            ):
                pl.append(i)

        return ph, pl

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def detect_swing_points(
        self,
        df: pd.DataFrame,
        window: int,
        use_m5_pivot_detector: bool = False,
    ) -> Tuple[List[int], List[int]]:

        if use_m5_pivot_detector:
            raw_highs, raw_lows = self._find_m5_choch_pivots(df, window)
        else:
            raw_highs, raw_lows = self._find_pivots_debug(df, window)

        confirmed_highs: List[int] = []
        confirmed_lows:  List[int] = []

        last_raw_high = raw_highs[-1] if raw_highs else None
        last_raw_low  = raw_lows[-1]  if raw_lows  else None

        for sh_idx in raw_highs:
            following_lows = [sl for sl in raw_lows if sl > sh_idx]
            if following_lows or sh_idx == last_raw_high:
                confirmed_highs.append(sh_idx)

        for sl_idx in raw_lows:
            following_highs = [sh for sh in raw_highs if sh > sl_idx]
            if following_highs or sl_idx == last_raw_low:
                confirmed_lows.append(sl_idx)

        return confirmed_highs, confirmed_lows

    def is_displacement_after_poi(self, poi: POI, df: pd.DataFrame, direction: str) -> bool:

        poi_time = self._get_candle_time(df, poi.candle_index)
        m5_idx   = self._find_bar_at_or_after(df, poi_time)

        if m5_idx is None or len(df) < m5_idx + 3:
            return False

        end_idx = min(len(df), m5_idx + 10)
        atr     = self._calc_atr(df, m5_idx, 14)

        if atr == 0:
            return False

        for i in range(m5_idx + 1, end_idx):
            open_p  = float(df["open"].iat[i])
            close_p = float(df["close"].iat[i])
            high_p  = float(df["high"].iat[i])
            low_p   = float(df["low"].iat[i])

            body         = abs(close_p - open_p)
            candle_range = high_p - low_p

            if candle_range == 0:
                continue

            body_ratio = body / candle_range

            # STRICT: strong directional body AND size relative to ATR
            is_bullish_body = close_p > open_p
            is_bearish_body = close_p < open_p
            is_strong_body  = body_ratio >= 0.60     # at least 60% body-to-range
            is_large_candle = body >= atr * 0.40     # at least 40% of ATR in body size

            if direction == "BULLISH" and is_bullish_body and is_strong_body and is_large_candle:
                return True

            if direction == "BEARISH" and is_bearish_body and is_strong_body and is_large_candle:
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
            c3h = float(df["high"].iat[i + 1])
            c3l = float(df["low"].iat[i + 1])

            # STRICT RULE ONLY — Candle 1 and Candle 3 must NOT overlap
            if direction == "BULLISH" and c1h < c3l:
                fvgs.append(FVG("BULLISH", i, low=c1h, high=c3l))

            elif direction == "BEARISH" and c1l > c3h:
                fvgs.append(FVG("BEARISH", i, low=c3h, high=c1l))

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