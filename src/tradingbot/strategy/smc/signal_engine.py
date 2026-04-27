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

    recent_sweep_bars: int = 30
    liquidity_lookback: int = 120

    atr_period: int = 14
    atr_sl_multiplier: float = 0.5

    # === UPDATED: Liquidity Sweep Logic ===
    # ATR multiplier used as tolerance buffer for sweep detection
    sweep_atr_tolerance: float = 0.1  # 10% of ATR used as buffer

    # === UPDATED: DATA WINDOW FIX ===
    # Minimum candle requirements per timeframe
    # Engine logs warnings but does NOT block execution if below these thresholds
    min_m5_candles: int = 50
    min_m15_candles: int = 50
    min_h4_candles: int = 20
    min_d1_candles: int = 20

    rr_min: float = 2.5

    time_column: str = "time"
    open_col: str = "open"
    high_col: str = "high"
    low_col: str = "low"
    close_col: str = "close"


# ─── Engine ───────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Canonical SMC signal engine for XAUUSD.
    Implements Guardeer\'s sequential checklist.
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
        m5_df = self._normalize_ohlc(m5_df, "m5_df")
        m15_df = self._normalize_ohlc(m15_df, "m15_df")
        h4_df = self._normalize_ohlc(h4_df, "h4_df")
        d1_df = self._normalize_ohlc(d1_df, "d1_df")

        gates = self._init_gates()

        # === UPDATED: DATA WINDOW FIX ===
        # Log all dataframe lengths for debugging — DO NOT stop execution
        print(f"[DATA] m5_len: {len(m5_df)}")
        print(f"[DATA] m15_len: {len(m15_df)}")
        print(f"[DATA] h4_len: {len(h4_df)}")
        print(f"[DATA] d1_len: {len(d1_df)}")

        cfg = self.config
        if len(m5_df) < cfg.min_m5_candles:
            print(f"⚠️ [DATA] m5_df has {len(m5_df)} candles — recommended minimum is {cfg.min_m5_candles}. Swing detection may be limited.")
        if len(m15_df) < cfg.min_m15_candles:
            print(f"⚠️ [DATA] m15_df has {len(m15_df)} candles — recommended minimum is {cfg.min_m15_candles}. Sweep detection may fail.")
        if len(h4_df) < cfg.min_h4_candles:
            print(f"⚠️ [DATA] h4_df has {len(h4_df)} candles — recommended minimum is {cfg.min_h4_candles}.")
        if len(d1_df) < cfg.min_d1_candles:
            print(f"⚠️ [DATA] d1_df has {len(d1_df)} candles — recommended minimum is {cfg.min_d1_candles}.")

        # 🔥 SOFT CHECK — DO NOT STOP ENGINE
        if (
            m5_df.empty
            or m15_df.empty
            or h4_df.empty
            or d1_df.empty
        ):
            print("⚠️ One of the dataframes is empty — continuing cautiously")

        if (
            len(m5_df) < 15
            or len(m15_df) < 15
            or len(h4_df) < 15
            or len(d1_df) < 15
        ):
            print(
                f"⚠️ LOW DATA → m5:{len(m5_df)}, m15:{len(m15_df)}, h4:{len(h4_df)}, d1:{len(d1_df)}"
            )

        # 🚀 DO NOT RETURN — continue execution

        step1 = self._step_htf_bias(d1_df, h4_df)
        gates["step_1_htf_bias"] = step1
        if not step1["passed"]:
            return self._no_trade(gates, step1["reason"])

        direction: str = step1["direction"]

        step2, sweep = self._step_external_liquidity_sweep(m15_df, direction)
        gates["step_2_external_liquidity_sweep"] = step2
        if not step2["passed"] or sweep is None:
            return self._no_trade(gates, step2["reason"], direction=direction)

        step3, structure_break = self._step_choch_mss_body_close(m5_df, sweep, m15_df)
        gates["step_3_choch_mss_body_close"] = step3
        if not step3["passed"] or structure_break is None:
            return self._no_trade(gates, step3["reason"], direction=direction)

        step4, poi_candidates = self._step_valid_poi(m15_df, m5_df, sweep, structure_break)
        gates["step_4_valid_poi"] = step4
        if not step4["passed"] or not poi_candidates:
            return self._no_trade(gates, step4["reason"], direction=direction)

        step5, selected_poi, selected_fvg, entry_price = self._step_ob_fvg_confluence(
            m5_df, m15_df, direction, sweep, structure_break, poi_candidates
        )
        gates["step_5_ob_fvg_confluence"] = step5
        if not step5["passed"] or selected_poi is None or selected_fvg is None or entry_price is None:
            return self._no_trade(gates, step5["reason"], direction=direction)

        step6 = self._step_dealing_range(direction, entry_price, sweep)
        gates["step_6_dealing_range"] = step6
        if not step6["passed"]:
            return self._no_trade(gates, step6["reason"], direction=direction)

        step7 = self._step_killzone(now_utc, m5_df)
        gates["step_7_killzone"] = step7
        if not step7["passed"]:
            return self._no_trade(gates, step7["reason"], direction=direction)

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
        # === UPDATED: Liquidity Sweep Logic ===
        # Use structure-based swing detection via _find_pivots (confirmed pivots only)
        # HTF bias filters which side to scan — no simultaneous dual-side checks
        ext_highs, ext_lows = self._find_pivots(df, self.config.external_swing_window)

        # === UPDATED: SWING FALLBACK ===
        # If no structure-based pivots found (insufficient data), fall back to
        # simple argmax/argmin over the entire available window.
        # This ensures sweep detection can still run with smaller datasets.
        if not ext_highs:
            fallback_high_idx = int(np.argmax(df["high"].to_numpy(dtype=float)))
            ext_highs = [fallback_high_idx]
            print(f"⚠️ [SWING] No structure highs found — using fallback high idx={fallback_high_idx} level={df['high'].iat[fallback_high_idx]:.2f}")
        if not ext_lows:
            fallback_low_idx = int(np.argmin(df["low"].to_numpy(dtype=float)))
            ext_lows = [fallback_low_idx]
            print(f"⚠️ [SWING] No structure lows found — using fallback low idx={fallback_low_idx} level={df['low'].iat[fallback_low_idx]:.2f}")

        print(f"[SWING] highs: {ext_highs}")
        print(f"[SWING] lows: {ext_lows}")
        print(f"[SWEEP] HTF direction={direction} | ext_highs={len(ext_highs)} | ext_lows={len(ext_lows)}")

        if direction == "BULLISH":
            # Bullish bias → only check SELL-SIDE sweep (low sweep)
            event = self._find_bullish_sweep(df, ext_highs, ext_lows)
        else:
            # Bearish bias → only check BUY-SIDE sweep (high sweep)
            event = self._find_bearish_sweep(df, ext_highs, ext_lows)

        if event is None:
            print(f"[SWEEP] ❌ No valid sweep found for direction={direction}")
            return {"passed": False, "reason": "NO_LIQUIDITY_SWEEP"}, None

        print(
            f"[SWEEP] ✅ Sweep detected | side={event.sweep_side} | "
            f"ref_level={event.reference_level:.2f} | sweep_price={event.sweep_price:.2f} | "
            f"close_back={event.close_back_inside:.2f}"
        )

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
        int_highs, int_lows = self._find_pivots(m15_df, self.config.internal_swing_window)

        sweep_time = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)
        if m5_sweep_idx is None:
            m5_sweep_idx = 0

        if sweep.direction == "BULLISH":
            candidate_levels = [i for i in int_highs if i < sweep.candle_index]
            if not candidate_levels:
                return {"passed": False, "reason": "NO_CHOCH"}, None
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
                return {"passed": False, "reason": "NO_CHOCH"}, None
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

        return {"passed": False, "reason": "NO_CHOCH"}, None

    def _step_valid_poi(
        self,
        m15_df: pd.DataFrame,
        m5_df: pd.DataFrame,
        sweep: SweepEvent,
        structure_break: StructureBreak,
    ) -> Tuple[Dict[str, Any], List[POI]]:
        break_time = self._get_candle_time(m5_df, structure_break.candle_index)
        break_m15_idx = self._find_bar_at_or_before(m15_df, break_time)
        if break_m15_idx is None:
            break_m15_idx = len(m15_df) - 1

        start = max(0, sweep.candle_index)
        end = min(len(m15_df) - 1, break_m15_idx)
        if end < start:
            end = len(m15_df) - 1

        segment = m15_df.iloc[start : end + 1]

        if sweep.direction == "BULLISH":
            opposite_mask = (segment["close"] < segment["open"]).to_numpy()
        else:
            opposite_mask = (segment["close"] > segment["open"]).to_numpy()

        opp_abs = [start + int(i) for i in np.where(opposite_mask)[0]]
        if not opp_abs:
            return {"passed": False, "reason": "NO_VALID_POI"}, []

        candidates: List[POI] = []

        if sweep.candle_index in opp_abs:
            candidates.append(self._build_poi(m15_df, "IDM_SWEEP", sweep.candle_index))

        post_sweep = [i for i in opp_abs if i > sweep.candle_index]
        if post_sweep:
            candidates.append(self._build_poi(m15_df, "FIRST_OB_AFTER_IDM", post_sweep[0]))

        pre_choch = [i for i in opp_abs if i < break_m15_idx]
        if pre_choch:
            candidates.append(self._build_poi(m15_df, "EXTREME_OB", pre_choch[-1]))
            candidates.append(self._build_poi(m15_df, "BOS_SWEEP", pre_choch[-1]))

        if opp_abs:
            candidates.append(self._build_poi(m15_df, "CHOCH_SWEEP", opp_abs[-1]))

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

        fvg_start = max(1, structure_break.candle_index)
        fvg_end = min(len(m5_df) - 2, m5_sweep_idx if m5_sweep_idx >= fvg_start else len(m5_df) - 2)
        if fvg_end < fvg_start:
            fvg_end = len(m5_df) - 2

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
        dealing_low = min(sweep.sweep_price, sweep.target_external_liquidity)
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
        atr_buf = sweep.atr_at_sweep * self.config.atr_sl_multiplier

        if direction == "BULLISH":
            sl = sweep.sweep_price - atr_buf
            tp = sweep.target_external_liquidity
            risk = entry_price - sl
            reward = tp - entry_price
        else:
            sl = sweep.sweep_price + atr_buf
            tp = sweep.target_external_liquidity
            risk = sl - entry_price
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

    # === UPDATED: Liquidity Sweep Logic ===
    def detect_swing_points(
        self,
        df: pd.DataFrame,
        window: int,
    ) -> Tuple[List[int], List[int]]:
        """
        Helper: Return confirmed structure-based swing highs and lows.
        A swing high is confirmed only if a lower pivot follows (IDM taken).
        A swing low is confirmed only if a higher pivot follows (IDM taken).
        This mirrors Guardeer\'s IDM confirmation rule from Lecture 3.
        """
        raw_highs, raw_lows = self._find_pivots(df, window)

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

    def _classify_sweep_strength(
        self,
        curr_low_or_high: float,
        ref_level: float,
        atr: float,
        is_bullish_sweep: bool,
    ) -> str:
        """
        Classify sweep strength based on wick penetration distance.
        strong: wick > 50% of ATR beyond level
        weak:   wick <= 50% of ATR beyond level
        """
        penetration = abs(curr_low_or_high - ref_level)
        threshold = atr * 0.5
        return "strong" if penetration >= threshold else "weak"

    def _find_bullish_sweep(
        self,
        df: pd.DataFrame,
        ext_highs: List[int],
        ext_lows: List[int],
    ) -> Optional[SweepEvent]:
        """
        === UPDATED: Liquidity Sweep Logic ===
        BULLISH sweep = sell-side liquidity grab
        Rule:
          - current.low < previous confirmed swing LOW (wick below)
          - current.close > previous confirmed swing LOW (closes back inside)
        ATR-based tolerance buffer applied to avoid exact-equality rejections.
        HTF bias = BULLISH → only scan for SELL-SIDE (low) sweeps.
        """
        confirmed_highs, confirmed_lows = self.detect_swing_points(df, self.config.external_swing_window)

        # === UPDATED: SWING FALLBACK ===
        # If no confirmed swing lows, fall back to all ext_lows; if still empty,
        # synthesize from argmin over lookback window
        if not confirmed_lows:
            confirmed_lows = list(ext_lows)
        if not confirmed_lows:
            fallback_idx = int(np.argmin(df["low"].to_numpy(dtype=float)))
            confirmed_lows = [fallback_idx]
            print(f"⚠️ [SWING FALLBACK] Bullish sweep using synthetic low idx={fallback_idx}")

        start_idx = max(self.config.external_swing_window + 1, len(df) - self.config.recent_sweep_bars)

        for i in range(len(df) - 1, start_idx - 1, -1):
            prior_lows = [idx for idx in confirmed_lows if idx < i]
            if not prior_lows:
                continue

            ref_idx = prior_lows[-1]
            ref_level = float(df["low"].iat[ref_idx])

            atr = self._calc_atr(df, i, self.config.atr_period)
            tolerance = atr * self.config.sweep_atr_tolerance

            curr_low = float(df["low"].iat[i])
            curr_close = float(df["close"].iat[i])
            curr_high = float(df["high"].iat[i])
            curr_open = float(df["open"].iat[i])

            print(
                f"[SWEEP BULLISH] i={i} | ref_level={ref_level:.2f} | "
                f"curr_low={curr_low:.2f} | curr_close={curr_close:.2f} | "
                f"tolerance={tolerance:.4f} | "
                f"wick_below={curr_low < ref_level + tolerance} | "
                f"close_inside={curr_close > ref_level - tolerance}"
            )

            wick_grabs_below = curr_low < (ref_level + tolerance)
            close_back_inside = curr_close > (ref_level - tolerance)

            if not (wick_grabs_below and close_back_inside):
                continue

            future_highs = [idx for idx in confirmed_highs if idx > i]
            if not future_highs:
                future_highs = [idx for idx in ext_highs if idx > i]

            if future_highs:
                tp_level = float(df["high"].iat[future_highs[0]])
            else:
                w_start = max(0, i - self.config.liquidity_lookback)
                tp_level = float(df["high"].iloc[w_start:i].max())

            if np.isnan(tp_level):
                continue

            strength = self._classify_sweep_strength(curr_low, ref_level, atr, is_bullish_sweep=True)
            print(f"[SWEEP BULLISH] ✅ Valid sweep at i={i} | strength={strength} | tp={tp_level:.2f}")

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

    def _find_bearish_sweep(
        self,
        df: pd.DataFrame,
        ext_highs: List[int],
        ext_lows: List[int],
    ) -> Optional[SweepEvent]:
        """
        === UPDATED: Liquidity Sweep Logic ===
        BEARISH sweep = buy-side liquidity grab
        Rule:
          - current.high > previous confirmed swing HIGH (wick above)
          - current.close < previous confirmed swing HIGH (closes back inside)
        ATR-based tolerance buffer applied to avoid exact-equality rejections.
        HTF bias = BEARISH → only scan for BUY-SIDE (high) sweeps.
        """
        confirmed_highs, confirmed_lows = self.detect_swing_points(df, self.config.external_swing_window)

        # === UPDATED: SWING FALLBACK ===
        # If no confirmed swing highs, fall back to all ext_highs; if still empty,
        # synthesize from argmax over lookback window
        if not confirmed_highs:
            confirmed_highs = list(ext_highs)
        if not confirmed_highs:
            fallback_idx = int(np.argmax(df["high"].to_numpy(dtype=float)))
            confirmed_highs = [fallback_idx]
            print(f"⚠️ [SWING FALLBACK] Bearish sweep using synthetic high idx={fallback_idx}")

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

            print(
                f"[SWEEP BEARISH] i={i} | ref_level={ref_level:.2f} | "
                f"curr_high={curr_high:.2f} | curr_close={curr_close:.2f} | "
                f"tolerance={tolerance:.4f} | "
                f"wick_above={curr_high > ref_level - tolerance} | "
                f"close_inside={curr_close < ref_level + tolerance}"
            )

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
            print(f"[SWEEP BEARISH] ✅ Valid sweep at i={i} | strength={strength} | tp={tp_level:.2f}")

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
        if len(df) < poi.candle_index + 3:
            return False

        for i in range(poi.candle_index + 1, min(len(df), poi.candle_index + 4)):
            open_p = float(df["open"].iat[i])
            close_p = float(df["close"].iat[i])
            body = abs(close_p - open_p)

            atr = self._calc_atr(df, i, 14)
            if body > atr * 1.5:
                if direction == "BULLISH" and close_p > open_p:
                    return True
                if direction == "BEARISH" and close_p < open_p:
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
        pivot_highs, pivot_lows = self._find_pivots(df, window)
        last_break: Optional[Tuple[int, str]] = None

        for i in range(1, len(df)):
            ph = [idx for idx in pivot_highs if idx < i]
            pl = [idx for idx in pivot_lows if idx < i]
            close = float(df["close"].iat[i])

            if ph and close > float(df["high"].iat[ph[-1]]):
                last_break = (i, "BULLISH")
            if pl and close < float(df["low"].iat[pl[-1]]):
                last_break = (i, "BEARISH")

        return last_break[1] if last_break else "NEUTRAL"

    def _find_pivots(self, df: pd.DataFrame, window: int) -> Tuple[List[int], List[int]]:
        # === UPDATED: DATA WINDOW FIX ===
        # Dynamically reduce window if dataframe is too small to produce any pivots.
        # This prevents empty pivot lists from killing sweep detection on short datasets.
        n = len(df)
        effective_window = window
        # Minimum required: window * 2 + 1 candles for at least one pivot
        while effective_window > 1 and n < (effective_window * 2 + 1):
            effective_window -= 1

        if effective_window < 1:
            print(f"⚠️ [SWING] DataFrame too small (n={n}) even for window=1 — returning empty pivots")
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
            if h > highs[i - effective_window : i].max() and h > highs[i + 1 : i + effective_window + 1].max():
                if lows[i + 1 : i + effective_window + 1].min() < lows[i]:
                    ph.append(i)
            if l < lows[i - effective_window : i].min() and l < lows[i + 1 : i + effective_window + 1].min():
                if highs[i + 1 : i + effective_window + 1].max() > highs[i]:
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
            print(f"DEBUG {name} length:", 0)
            return pd.DataFrame()

        out = df.copy()
        out.columns = [str(c).lower().strip() for c in out.columns]

        cfg = self.config
        required = {cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col}
        missing = required.difference(out.columns)
        if missing:
            print(f"{name} missing columns: {sorted(missing)}")
            print(f"DEBUG {name} length:", 0)
            return pd.DataFrame()

        for col in [cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col]:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        before = len(out)
        out = out.dropna(subset=[cfg.open_col, cfg.high_col, cfg.low_col, cfg.close_col])
        out = out.reset_index(drop=True)
        dropped = before - len(out)
        if dropped > 0:
            print(f"{name} dropped {dropped} rows with invalid OHLC values")

        # 🔥 FORCE TIME ALWAYS
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

        print(f"DEBUG {name} length:", len(out))
        print(f"{name} columns:", out.columns.tolist())

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