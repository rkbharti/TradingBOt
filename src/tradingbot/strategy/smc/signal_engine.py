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
    action: str                        # "ENTER" | "NO_TRADE"
    direction: Optional[str]           # "BULLISH" | "BEARISH" | None
    entry_price: Optional[float]
    sl_price: Optional[float]
    tp_price: Optional[float]
    gates: Dict[str, Dict[str, Any]]   # per-step pass/fail evidence
    reason: str                        # first failed gate reason_code
    confidence_score: int              # 0–100, count of passed gates


@dataclass(frozen=True)
class SweepEvent:
    direction: str
    sweep_side: str
    reference_index: int
    reference_level: float
    candle_index: int
    sweep_price: float
    close_back_inside: float
    target_external_liquidity: float   # nearest opposite swing (TP target)
    atr_at_sweep: float                # ATR of the sweep candle (for SL)


@dataclass(frozen=True)
class StructureBreak:
    direction: str
    choch_label: str                   # "CHOCH" | "MSS"
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
    # HTF weights (Lecture 9: D1=macro, H4=intermediate)
    d1_weight: int = 4
    h4_weight: int = 3

    # Pivot detection windows per TF
    external_swing_window: int = 5    # for HTF bias + liquidity sweep detection
    internal_swing_window: int = 2    # for CHoCH/MSS detection on M5/M15

    # Sweep search
    recent_sweep_bars: int = 30       # how far back to look for a valid sweep
    liquidity_lookback: int = 120     # bars to find opposite external liquidity (TP)

    # ATR config for SL buffer
    atr_period: int = 14
    atr_sl_multiplier: float = 0.5   # SL = sweep_wick ± (ATR * multiplier)

    # Risk/Reward
    rr_min: float = 2.5

    # Column contract (MT5 standard output)
    time_column: str = "time"
    open_col: str = "open"
    high_col: str = "high"
    low_col: str = "low"
    close_col: str = "close"


# ─── Engine ───────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Canonical SMC signal engine for XAUUSD.
    Implements Guardeer's 8-gate sequential checklist.
    Zero imports from legacy strategy modules.
    Only pandas + numpy.
    """

    def __init__(self, config: Optional[SignalEngineConfig] = None) -> None:
        self.config = config or SignalEngineConfig()

    # ── Public Entry Point ────────────────────────────────────────────────────

    def evaluate(
        self,
        m5_df: pd.DataFrame,           # execution timeframe (M5)
        m15_df: pd.DataFrame,          # structure confirmation (M15)
        h4_df: pd.DataFrame,           # HTF intermediate
        d1_df: pd.DataFrame,           # HTF macro
        now_utc: Optional[datetime] = None,
    ) -> SignalResult:
        m5_df  = self._normalize_ohlc(m5_df,  "m5_df")
        m15_df = self._normalize_ohlc(m15_df, "m15_df")
        h4_df  = self._normalize_ohlc(h4_df,  "h4_df")
        d1_df  = self._normalize_ohlc(d1_df,  "d1_df")

        gates = self._init_gates()

        # ── Step 1: HTF Bias ──────────────────────────────────────────────────
        step1 = self._step_htf_bias(d1_df, h4_df)
        gates["step_1_htf_bias"] = step1
        if not step1["passed"]:
            return self._no_trade(gates, step1["reason"])

        direction: str = step1["direction"]

        # ── Step 2: External Liquidity Sweep ─────────────────────────────────
        step2, sweep = self._step_external_liquidity_sweep(m15_df, direction)
        gates["step_2_external_liquidity_sweep"] = step2
        if not step2["passed"] or sweep is None:
            return self._no_trade(gates, step2["reason"], direction=direction)

        # ── Step 3: CHoCH/MSS Body Close (M5 for precision) ──────────────────
        step3, structure_break = self._step_choch_mss_body_close(m5_df, sweep, m15_df)
        gates["step_3_choch_mss_body_close"] = step3
        if not step3["passed"] or structure_break is None:
            return self._no_trade(gates, step3["reason"], direction=direction)

        # ── Step 4: Valid POI Type ────────────────────────────────────────────
        step4, poi_candidates = self._step_valid_poi(m15_df, sweep, structure_break)
        gates["step_4_valid_poi"] = step4
        if not step4["passed"] or not poi_candidates:
            return self._no_trade(gates, step4["reason"], direction=direction)

        # ── Step 5: OB + FVG Confluence ───────────────────────────────────────
        step5, selected_poi, selected_fvg, entry_price = self._step_ob_fvg_confluence(
            m5_df, direction, sweep, structure_break, poi_candidates
        )
        gates["step_5_ob_fvg_confluence"] = step5
        if not step5["passed"] or selected_poi is None or selected_fvg is None or entry_price is None:
            return self._no_trade(gates, step5["reason"], direction=direction)

        # ── Step 6: Dealing Range ─────────────────────────────────────────────
        step6 = self._step_dealing_range(direction, entry_price, sweep)
        gates["step_6_dealing_range"] = step6
        if not step6["passed"]:
            return self._no_trade(gates, step6["reason"], direction=direction)

        # ── Step 7: Killzone ──────────────────────────────────────────────────
        step7 = self._step_killzone(now_utc, m5_df)
        gates["step_7_killzone"] = step7
        if not step7["passed"]:
            return self._no_trade(gates, step7["reason"], direction=direction)

        # ── Step 8: RR >= 2.5 ─────────────────────────────────────────────────
        step8, sl_price, tp_price = self._step_rr(direction, entry_price, sweep)
        gates["step_8_risk_reward"] = step8
        if not step8["passed"]:
            return self._no_trade(gates, step8["reason"], direction=direction)

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
        """
        Guardeer Lecture 9: Monthly → Weekly → Daily → 4H top-down.
        D1 and H4 must agree. D1 weight=4, H4 weight=3.
        NEUTRAL on either → NO_TRADE (not enough directional conviction).
        """
        d1_bias = self._infer_bias(d1_df, self.config.external_swing_window)
        h4_bias = self._infer_bias(h4_df, self.config.external_swing_window)

        passed = (d1_bias == h4_bias) and (d1_bias in {"BULLISH", "BEARISH"})

        return {
            "passed": passed,
            "reason": "OK" if passed else "HTF_BIAS_MISMATCH",
            "direction": d1_bias if passed else None,
            "d1_bias": d1_bias,
            "h4_bias": h4_bias,
            "d1_weight": self.config.d1_weight,
            "h4_weight": self.config.h4_weight,
            "agreement_score": (self.config.d1_weight + self.config.h4_weight) if passed else 0,
        }

    def _step_external_liquidity_sweep(
        self,
        df: pd.DataFrame,
        direction: str,
    ) -> Tuple[Dict[str, Any], Optional[SweepEvent]]:
        """
        Guardeer Lecture 5: External liquidity = PDH/PDL or Swing H/L.
        A valid sweep requires: wick beyond level + body CLOSES BACK INSIDE.
        Wick-only = not a confirmed sweep.
        TP target = nearest opposite external swing (Lecture 5 rule).
        """
        ext_highs, ext_lows = self._find_pivots(df, self.config.external_swing_window)

        if direction == "BULLISH":
            event = self._find_bullish_sweep(df, ext_highs, ext_lows)
        else:
            event = self._find_bearish_sweep(df, ext_highs, ext_lows)

        if event is None:
            return {"passed": False, "reason": "EXTERNAL_LIQUIDITY_NOT_SWEPT"}, None

        return {
            "passed": True,
            "reason": "OK",
            "sweep_side": event.sweep_side,
            "reference_level": self._r(event.reference_level),
            "sweep_price": self._r(event.sweep_price),
            "close_back_inside": self._r(event.close_back_inside),
            "target_external_liquidity": self._r(event.target_external_liquidity),
            "atr_at_sweep": self._r(event.atr_at_sweep),
            "candle_index": event.candle_index,
        }, event

    def _step_choch_mss_body_close(
        self,
        m5_df: pd.DataFrame,
        sweep: SweepEvent,
        m15_df: pd.DataFrame,
    ) -> Tuple[Dict[str, Any], Optional[StructureBreak]]:
        """
        Guardeer Lecture 3: CHoCH is ONLY valid when body (not wick) closes
        beyond the internal swing level. Wick touch = wait for body close.
        Use M5 for precision, validate against M15 internal structure.
        """
        int_highs, int_lows = self._find_pivots(m15_df, self.config.internal_swing_window)

        # Map M15 sweep candle time to M5 index
        sweep_time = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)
        if m5_sweep_idx is None:
            return {"passed": False, "reason": "SWEEP_TIME_NOT_FOUND_IN_M5"}, None

        if sweep.direction == "BULLISH":
            candidate_levels = [i for i in int_highs if i < sweep.candle_index]
            if not candidate_levels:
                return {"passed": False, "reason": "CHOCH_LEVEL_NOT_FOUND"}, None
            choch_level = float(m15_df["high"].iat[candidate_levels[-1]])

            for i in range(m5_sweep_idx + 1, len(m5_df)):
                close_price = float(m5_df["close"].iat[i])
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
                        "reason": "OK",
                        "label": "CHOCH",
                        "level": self._r(choch_level),
                        "close_price": self._r(close_price),
                        "m5_candle_index": i,
                        "validation": "BODY_CLOSE",
                    }, sb
        else:
            candidate_levels = [i for i in int_lows if i < sweep.candle_index]
            if not candidate_levels:
                return {"passed": False, "reason": "CHOCH_LEVEL_NOT_FOUND"}, None
            choch_level = float(m15_df["low"].iat[candidate_levels[-1]])

            for i in range(m5_sweep_idx + 1, len(m5_df)):
                close_price = float(m5_df["close"].iat[i])
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
                        "reason": "OK",
                        "label": "CHOCH",
                        "level": self._r(choch_level),
                        "close_price": self._r(close_price),
                        "m5_candle_index": i,
                        "validation": "BODY_CLOSE",
                    }, sb

        return {"passed": False, "reason": "CHOCH_BODY_CLOSE_NOT_CONFIRMED"}, None

    def _step_valid_poi(
        self,
        df: pd.DataFrame,
        sweep: SweepEvent,
        structure_break: StructureBreak,
    ) -> Tuple[Dict[str, Any], List[POI]]:
        """
        Guardeer Lecture 6: Only 5 valid POIs — IDM_SWEEP, FIRST_OB_AFTER_IDM,
        EXTREME_OB, BOS_SWEEP, CHOCH_SWEEP.
        Middle OBs between IDM and CHoCH are retail traps — skip them.
        """
        start = max(0, sweep.candle_index)
        end = min(len(df) - 1, structure_break.candle_index)
        segment = df.iloc[start : end + 1]

        if sweep.direction == "BULLISH":
            opposite_mask = (segment["close"] < segment["open"]).to_numpy()
        else:
            opposite_mask = (segment["close"] > segment["open"]).to_numpy()

        opp_abs = [start + int(i) for i in np.where(opposite_mask)[0]]
        if not opp_abs:
            return {"passed": False, "reason": "NO_VALID_POI"}, []

        candidates: List[POI] = []

        # POI 1: IDM_SWEEP — sweep candle itself is opposite-colored
        if sweep.candle_index in opp_abs:
            candidates.append(self._build_poi(df, "IDM_SWEEP", sweep.candle_index))

        # POI 2: FIRST_OB_AFTER_IDM
        post_sweep = [i for i in opp_abs if i > sweep.candle_index]
        if post_sweep:
            candidates.append(self._build_poi(df, "FIRST_OB_AFTER_IDM", post_sweep[0]))

        # POI 3: EXTREME_OB — closest OB to CHoCH level (Lecture 6: highest prob)
        pre_choch = [i for i in opp_abs if i < structure_break.candle_index]
        if pre_choch:
            candidates.append(self._build_poi(df, "EXTREME_OB", pre_choch[-1]))

        # POI 4: BOS_SWEEP
        if pre_choch:
            candidates.append(self._build_poi(df, "BOS_SWEEP", pre_choch[-1]))

        # POI 5: CHOCH_SWEEP
        if opp_abs:
            candidates.append(self._build_poi(df, "CHOCH_SWEEP", opp_abs[-1]))

        deduped = self._dedupe_pois(candidates)
        valid = [p for p in deduped if p.poi_type in VALID_POI_TYPES]

        if not valid:
            return {"passed": False, "reason": "NO_VALID_POI_TYPE"}, []

        return {
            "passed": True,
            "reason": "OK",
            "poi_types": [p.poi_type for p in valid],
            "count": len(valid),
        }, valid

    def _step_ob_fvg_confluence(
        self,
        m5_df: pd.DataFrame,
        direction: str,
        sweep: SweepEvent,
        structure_break: StructureBreak,
        poi_candidates: List[POI],
    ) -> Tuple[Dict[str, Any], Optional[POI], Optional[FVG], Optional[float]]:
        """
        Guardeer Lecture 4: OB is only valid if there is an FVG AT or ABOVE
        it (bullish) or AT or BELOW it (bearish). No FVG = invalid OB.
        Entry = center of OB∩FVG overlap zone.
        """
        # Detect FVGs on M5 for precision (Lecture 6: LTF for entry)
        # FVG location: ONLY between previous structure and IDM sweep
        fvg_start = max(1, structure_break.candle_index)
        fvg_end = min(len(m5_df) - 2, sweep.candle_index if sweep.candle_index > fvg_start else len(m5_df) - 2)
        fvg_list = self._find_fvgs(m5_df, direction, start=fvg_start, end=fvg_end)

        if not fvg_list:
            return {"passed": False, "reason": "FVG_NOT_FOUND"}, None, None, None

        best: Optional[Tuple[POI, FVG, float, float]] = None

        for poi in poi_candidates:
            for fvg in fvg_list:
                overlap_low = max(poi.low, fvg.low)
                overlap_high = min(poi.high, fvg.high)
                if overlap_high <= overlap_low:
                    continue
                ratio = (overlap_high - overlap_low) / max(poi.high - poi.low, 1e-9)
                if best is None or ratio > best[3]:
                    entry = (overlap_low + overlap_high) / 2.0
                    best = (poi, fvg, entry, ratio)

        if best is None:
            return {"passed": False, "reason": "OB_FVG_CONFLUENCE_MISSING"}, None, None, None

        poi, fvg, entry, ratio = best
        
        # Guardeer Lecture 12: Displacement check
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
            "overlap_ratio": round(float(ratio), 4),
        }, poi, fvg, entry

    def _step_dealing_range(
        self,
        direction: str,
        entry_price: float,
        sweep: SweepEvent,
    ) -> Dict[str, Any]:
        """
        Guardeer Lecture 9 & 11: Dealing Range = last significant H to L.
        Buy only from DISCOUNT (below EQ=50%), Sell only from PREMIUM (above EQ).
        Entering at EQ → wait for confirmation.
        """
        dealing_low  = min(sweep.sweep_price, sweep.target_external_liquidity)
        dealing_high = max(sweep.sweep_price, sweep.target_external_liquidity)
        eq = (dealing_low + dealing_high) / 2.0

        if direction == "BULLISH":
            passed = entry_price < eq
            reason = "OK" if passed else "NOT_IN_DISCOUNT_ZONE"
        else:
            passed = entry_price > eq
            reason = "OK" if passed else "NOT_IN_PREMIUM_ZONE"

        return {
            "passed": passed,
            "reason": reason,
            "dealing_low": self._r(dealing_low),
            "dealing_high": self._r(dealing_high),
            "equilibrium": self._r(eq),
            "entry_price": self._r(entry_price),
            "zone": "DISCOUNT" if entry_price < eq else ("PREMIUM" if entry_price > eq else "EQUILIBRIUM"),
        }

    def _step_killzone(
        self,
        now_utc: Optional[datetime],
        m5_df: pd.DataFrame,
    ) -> Dict[str, Any]:
        """
        Guardeer Lecture 10: Only trade during active killzones.
        London: 08:00–11:00 UTC | NY: 13:00–16:00 UTC.
        Outside these windows → probability drops significantly.
        """
        ts = self._resolve_now_utc(now_utc, m5_df)
        t = ts.time()

        session: Optional[str] = None
        for (kz_start, kz_end, kz_name) in KILLZONES_UTC:
            if kz_start <= t < kz_end:
                session = kz_name
                break

        active = session is not None

        return {
            "passed": active,
            "reason": "OK" if active else "KILLZONE_INACTIVE",
            "timestamp_utc": ts.isoformat(),
            "session": session,
            "killzones_utc": [(str(s), str(e), n) for s, e, n in KILLZONES_UTC],
        }

    def _step_rr(
        self,
        direction: str,
        entry_price: float,
        sweep: SweepEvent,
    ) -> Tuple[Dict[str, Any], Optional[float], Optional[float]]:
        """
        Guardeer Lecture 6 + 10: SL = below sweep wick ± ATR buffer (structural).
        TP = nearest opposite external liquidity (NOT max of lookback).
        RR must be >= 2.5.
        """
        atr_buf = sweep.atr_at_sweep * self.config.atr_sl_multiplier

        if direction == "BULLISH":
            sl = sweep.sweep_price - atr_buf
            tp = sweep.target_external_liquidity
            risk   = entry_price - sl
            reward = tp - entry_price
        else:
            sl = sweep.sweep_price + atr_buf
            tp = sweep.target_external_liquidity
            risk   = sl - entry_price
            reward = entry_price - tp

        if risk <= 0 or reward <= 0:
            return {"passed": False, "reason": "INVALID_TRADE_GEOMETRY"}, None, None

        rr = reward / risk
        passed = rr >= self.config.rr_min

        return {
            "passed": passed,
            "reason": "OK" if passed else "RR_BELOW_MINIMUM",
            "rr": round(float(rr), 4),
            "rr_min": self.config.rr_min,
            "entry": self._r(entry_price),
            "sl": self._r(sl),
            "tp": self._r(tp),
            "risk_pts": self._r(risk),
            "reward_pts": self._r(reward),
            "atr_buffer": self._r(atr_buf),
        }, sl, tp

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _find_bullish_sweep(
        self,
        df: pd.DataFrame,
        ext_highs: List[int],
        ext_lows: List[int],
    ) -> Optional[SweepEvent]:
        """
        Bullish sweep: wick takes out a prior swing low, body closes back above it.
        Target (TP) = nearest swing HIGH to the right of the sweep.
        """
        start_idx = max(self.config.external_swing_window + 1, len(df) - self.config.recent_sweep_bars)

        for i in range(len(df) - 1, start_idx - 1, -1):
            prior_lows = [idx for idx in ext_lows if idx < i]
            if not prior_lows:
                continue

            ref_idx    = prior_lows[-1]
            ref_level  = float(df["low"].iat[ref_idx])
            curr_low   = float(df["low"].iat[i])
            curr_close = float(df["close"].iat[i])

            if curr_low < ref_level and curr_close > ref_level:
                # TP = nearest opposite external high AFTER this sweep
                future_highs = [idx for idx in ext_highs if idx > i]
                if future_highs:
                    tp_level = float(df["high"].iat[future_highs[0]])
                else:
                    # fallback: highest high in lookback window
                    w_start = max(0, i - self.config.liquidity_lookback)
                    tp_level = float(df["high"].iloc[w_start:i].max())

                if np.isnan(tp_level):
                    continue

                atr = self._calc_atr(df, i, self.config.atr_period)

                return SweepEvent(
                    direction="BULLISH",
                    sweep_side="SELL_SIDE",
                    reference_index=ref_idx,
                    reference_level=ref_level,
                    candle_index=i,
                    sweep_price=curr_low,
                    close_back_inside=curr_close,
                    target_external_liquidity=tp_level,
                    atr_at_sweep=atr,
                )
        return None

    def is_displacement_after_poi(self, poi: POI, df: pd.DataFrame, direction: str) -> bool:
        """
        Guardeer Lecture 12: After POI mitigation, MUST see displacement (large fast candle).
        No displacement after POI = entry invalid.
        """
        if len(df) < poi.candle_index + 3:
            return False
            
        # Check the next 3 candles after POI for a strong displacement body
        for i in range(poi.candle_index + 1, min(len(df), poi.candle_index + 4)):
            open_p = float(df["open"].iat[i])
            close_p = float(df["close"].iat[i])
            body = abs(close_p - open_p)
            
            # Rough proxy for displacement: body > ATR
            atr = self._calc_atr(df, i, 14)
            if body > atr * 1.5:
                if direction == "BULLISH" and close_p > open_p:
                    return True
                if direction == "BEARISH" and close_p < open_p:
                    return True
        return False

    def _find_bearish_sweep(
        self,
        df: pd.DataFrame,
        ext_highs: List[int],
        ext_lows: List[int],
    ) -> Optional[SweepEvent]:
        """
        Bearish sweep: wick takes out a prior swing high, body closes back below it.
        Target (TP) = nearest swing LOW to the right of the sweep.
        """
        start_idx = max(self.config.external_swing_window + 1, len(df) - self.config.recent_sweep_bars)

        for i in range(len(df) - 1, start_idx - 1, -1):
            prior_highs = [idx for idx in ext_highs if idx < i]
            if not prior_highs:
                continue

            ref_idx    = prior_highs[-1]
            ref_level  = float(df["high"].iat[ref_idx])
            curr_high  = float(df["high"].iat[i])
            curr_close = float(df["close"].iat[i])

            if curr_high > ref_level and curr_close < ref_level:
                future_lows = [idx for idx in ext_lows if idx > i]
                if future_lows:
                    tp_level = float(df["low"].iat[future_lows[0]])
                else:
                    w_start = max(0, i - self.config.liquidity_lookback)
                    tp_level = float(df["low"].iloc[w_start:i].min())

                if np.isnan(tp_level):
                    continue

                atr = self._calc_atr(df, i, self.config.atr_period)

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

    def _calc_atr(self, df: pd.DataFrame, end_idx: int, period: int) -> float:
        start = max(1, end_idx - period + 1)
        highs  = df["high"].to_numpy(dtype=float)[start : end_idx + 1]
        lows   = df["low"].to_numpy(dtype=float)[start : end_idx + 1]
        closes = df["close"].to_numpy(dtype=float)[start - 1 : end_idx]
        if len(highs) < 2:
            return float(highs[-1] - lows[-1]) if len(highs) == 1 else 1.0
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:]  - closes[:-1]),
            ),
        )
        return float(np.mean(tr))

    def _infer_bias(self, df: pd.DataFrame, window: int) -> str:
        """
        Guardeer Lecture 3: Bias = last confirmed BOS/CHoCH body close direction.
        NEUTRAL if no confirmed break found.
        """
        pivot_highs, pivot_lows = self._find_pivots(df, window)
        last_break: Optional[Tuple[int, str]] = None

        for i in range(1, len(df)):
            ph = [idx for idx in pivot_highs if idx < i]
            pl = [idx for idx in pivot_lows  if idx < i]
            close = float(df["close"].iat[i])

            if ph and close > float(df["high"].iat[ph[-1]]):
                last_break = (i, "BULLISH")
            if pl and close < float(df["low"].iat[pl[-1]]):
                last_break = (i, "BEARISH")

        return last_break[1] if last_break else "NEUTRAL"

    def _find_pivots(self, df: pd.DataFrame, window: int) -> Tuple[List[int], List[int]]:
        highs = df["high"].to_numpy(dtype=float)
        lows  = df["low"].to_numpy(dtype=float)
        n = len(df)
        ph: List[int] = []
        pl: List[int] = []

        for i in range(window, n - window):
            h = highs[i]
            l = lows[i]
            if h > highs[i - window : i].max() and h > highs[i + 1 : i + window + 1].max():
                # Guardeer Lecture 3: IDM must be swept to confirm HH
                # We proxy IDM confirmation by ensuring a minor pullback happens after the high
                if lows[i + 1 : i + window + 1].min() < lows[i]:
                    ph.append(i)
            if l < lows[i - window : i].min() and l < lows[i + 1 : i + window + 1].min():
                # Guardeer Lecture 3: IDM must be swept to confirm LL
                if highs[i + 1 : i + window + 1].max() > highs[i]:
                    pl.append(i)
        return ph, pl

    def _find_fvgs(
        self,
        df: pd.DataFrame,
        direction: str,
        start: int,
        end: int,
    ) -> List[FVG]:
        """
        Guardeer Lecture 4: FVG = gap between C1 high and C3 low (bullish),
        or C1 low and C3 high (bearish). They must NOT overlap.
        """
        fvgs: List[FVG] = []
        end = min(end, len(df) - 2)

        for i in range(max(1, start), end + 1):
            c1h = float(df["high"].iat[i - 1])
            c1l = float(df["low"].iat[i - 1])
            c3h = float(df["high"].iat[i + 1])
            c3l = float(df["low"].iat[i + 1])

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

    def _is_bos_sweep(self, df: pd.DataFrame, sb: StructureBreak) -> bool:
        close = float(df["close"].iat[sb.candle_index])
        return close > sb.level if sb.direction == "BULLISH" else close < sb.level

    def _get_candle_time(self, df: pd.DataFrame, idx: int) -> Optional[pd.Timestamp]:
        if self.config.time_column in df.columns:
            val = df[self.config.time_column].iat[idx]
            return pd.Timestamp(val) if not pd.isna(val) else None
        if isinstance(df.index, pd.DatetimeIndex):
            return df.index[idx]
        return None

    def _find_bar_at_or_after(
        self,
        df: pd.DataFrame,
        target_time: Optional[pd.Timestamp],
    ) -> Optional[int]:
        if target_time is None:
            return None
        if self.config.time_column in df.columns:
            times = pd.to_datetime(df[self.config.time_column])
        elif isinstance(df.index, pd.DatetimeIndex):
            times = pd.Series(df.index, index=range(len(df)))
        else:
            return None
        if target_time.tzinfo is not None:
            times = pd.to_datetime(times).dt.tz_localize("UTC") if times.dt.tz is None else times.dt.tz_convert("UTC")
        candidates = times[times >= target_time]
        if candidates.empty:
            return None
        return int(candidates.index[0])

    def _normalize_ohlc(self, df: pd.DataFrame, name: str) -> pd.DataFrame:
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"{name} must be a pandas DataFrame")
        out = df.copy()
        out.columns = [str(c).lower() for c in out.columns]

        cfg = self.config
        required = {cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col}
        missing = required.difference(out.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")

        for col in [cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col]:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        if out[[cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col]].isna().any().any():
            raise ValueError(f"{name} contains NaN OHLC values after coercion")

        if cfg.time_column in out.columns:
            out[cfg.time_column] = pd.to_datetime(out[cfg.time_column], utc=True, errors="coerce")
            out = out.sort_values(cfg.time_column).reset_index(drop=True)

        min_bars = self.config.external_swing_window * 2 + 5
        if len(out) < min_bars:
            raise ValueError(f"{name} needs at least {min_bars} bars, got {len(out)}")

        return out

    def _resolve_now_utc(
        self,
        now_utc: Optional[datetime],
        df: pd.DataFrame,
    ) -> datetime:
        if now_utc is not None:
            if now_utc.tzinfo is None:
                return now_utc.replace(tzinfo=timezone.utc)
            return now_utc.astimezone(timezone.utc)
        # Fallback: last candle time from M5 df
        if self.config.time_column in df.columns and len(df) > 0:
            ts = df[self.config.time_column].iat[-1]
            ts = pd.Timestamp(ts)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return ts.to_pydatetime()
        return datetime.now(timezone.utc)

    def _init_gates(self) -> Dict[str, Dict[str, Any]]:
        return {g: {"passed": False, "reason": "NOT_EVALUATED"} for g in [
            "step_1_htf_bias",
            "step_2_external_liquidity_sweep",
            "step_3_choch_mss_body_close",
            "step_4_valid_poi",
            "step_5_ob_fvg_confluence",
            "step_6_dealing_range",
            "step_7_killzone",
            "step_8_risk_reward",
        ]}

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