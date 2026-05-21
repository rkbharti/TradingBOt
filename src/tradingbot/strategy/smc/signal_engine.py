from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple
from tradingbot.infra.news.news_filter import NewsFilter

import numpy as np
import pandas as pd
from collections import deque

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

    external_swing_window: int = 3
    # ✅ FIX #1 — Pine Script internal_r_lookback defaults to 5 (iLen=5).
    # Was incorrectly set to 2, producing hyper-sensitive micro-pivots on every
    # minor wiggle and flooding CHoCH detection with false structure breaks.
    internal_swing_window: int = 5

    recent_sweep_bars: int = 80
    liquidity_lookback: int = 120

    atr_period: int = 14
    atr_sl_multiplier: float = 0.5

    sweep_atr_tolerance: float = 0.15
    min_atr_threshold: float = 0.05

    min_m5_candles: int = 50
    min_m15_candles: int = 50
    min_h4_candles: int = 20
    min_d1_candles: int = 20

    rr_min: float = 2.0

    time_column: str = "time"
    open_col: str = "open"
    high_col: str = "high"
    low_col: str = "low"
    close_col: str = "close"


# ─── POI Mitigation Tracker (FIX #2) ───────────────────────────────────────────
# Track breached POIs to prevent "Zombie POI" overtrading

@dataclass
class POIMitigation:
    """
    ✅ FIX #2 — State machine to track and invalidate breached POIs.
    Pine Script uses ob_mitigation triggers (Absolute or Middle cross).
    This prevents the same zone from generating multiple trades.
    """
    poi_id: str  # unique identifier: f"{poi_type}_{candle_index}_{low}_{high}"
    breached_at_index: Optional[int] = None
    is_active: bool = True

    def breach(self, current_index: int) -> None:
        """Mark this POI as breached at the given candle index."""
        self.breached_at_index = current_index
        self.is_active = False


class POIMitigationTracker:
    """Tracks POI state across multiple evaluate() calls."""
    def __init__(self, max_history: int = 500):
        self.breached_pois: Dict[str, POIMitigation] = {}
        self.max_history = max_history

    def add_breach(self, poi: POI, current_index: int) -> None:
        poi_id = self._poi_id(poi)
        if poi_id not in self.breached_pois:
            self.breached_pois[poi_id] = POIMitigation(poi_id=poi_id)
        self.breached_pois[poi_id].breach(current_index)

    def is_breached(self, poi: POI) -> bool:
        return self._poi_id(poi) in self.breached_pois and not self.breached_pois[self._poi_id(poi)].is_active

    def _poi_id(self, poi: POI) -> str:
        return f"{poi.poi_type}_{poi.candle_index}_{round(poi.low, 4)}_{round(poi.high, 4)}"

    def cleanup_old_breaches(self, current_candles: int) -> None:
        """Remove very old breach records to prevent memory bloat."""
        if len(self.breached_pois) > self.max_history:
            sorted_pois = sorted(
                self.breached_pois.items(),
                key=lambda x: x[1].breached_at_index or 0,
            )
            self.breached_pois = dict(sorted_pois[-self.max_history:])


# ─── Engine ───────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Canonical SMC signal engine for XAUUSD.
    Implements Guardeer's sequential checklist with critical parity fixes.
    """

    def __init__(self, config: Optional[SignalEngineConfig] = None) -> None:
        self.config = config or SignalEngineConfig()
        self.news_filter: Optional[NewsFilter] = None
        # ✅ FIX #4b — Stateful itrend tracking, matching Pine Script's `var int itrend = 0`.
        # Pine Script sets itrend := 1 after any bullish BOS/CHoCH, itrend := -1 after bearish.
        # This state PERSISTS across bars (Pine Script `var`) so we persist it across evaluate() calls.
        # Without this, every structure event is mislabeled "CHOCH" regardless of prior trend direction.
        self._itrend: int = 0   # 0=neutral, 1=bullish, -1=bearish
        self._bias_debug_rows: list[dict] = []
        # ✅ FIX #2 — POI Mitigation Tracker to prevent zombie POIs
        self.poi_mitigation: POIMitigationTracker = POIMitigationTracker()
        # ✅ FIX #4 — Track previous pivot lows/highs for CHoCH+ confirmation
        self._prev_highs: deque = deque(maxlen=10)
        self._prev_lows: deque = deque(maxlen=10)

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
        # print("🔥 EVALUATE METHOD RUNNING")

        current_atr = self._calc_atr(m5_df, len(m5_df) - 1, self.config.atr_period)

        # TEMP DEBUG — no formatting, safe against None
        # print(f"[ATR RAW] current_atr={current_atr} | type={type(current_atr)} | threshold={self.config.min_atr_threshold}")

        if current_atr is None or current_atr < self.config.min_atr_threshold:
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
        step4, poi_candidates = self._step_valid_poi(m15_df, m5_df, h4_df, d1_df, sweep, structure_break)
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

        # ─── Step 7.5: News Filter ───────────────────────────────────────────
        if self.news_filter is not None:
            blocked, news_reason = self.news_filter.is_news_blackout(now_utc)
            step7b = {
                "passed": not blocked,
                "reason": news_reason or "NO_HIGH_IMPACT_NEWS",
            }
        else:
            step7b = {"passed": True, "reason": "NEWS_FILTER_DISABLED"}
        gates["step_7b_news_filter"] = step7b
        if not step7b["passed"]:
            return _reject(gates, step7b["reason"], direction=direction)

        # ─── Step 8: Risk/Reward ─────────────────────────────────────────────
        step8, sl_price, tp_price = self._step_rr(
            direction,
            entry_price,
            sweep,
            selected_poi,
            structure_break,
        )
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

        if self.config.time_column in d1_df.columns:
            ts = d1_df.iloc[-1][self.config.time_column]
        else:
            ts = d1_df.index[-1]

        def _log(direction, reason):
            self._bias_debug_rows.append(
                {
                    "time": ts,
                    "d1_bias": d1_bias,
                    "h4_bias": h4_bias,
                    "direction": direction,
                    "reason": reason,
                }
            )

        # =========================
        # ✅ CASE 1 — PERFECT ALIGNMENT
        # =========================
        if d1_bias == h4_bias and d1_bias in {"BULLISH", "BEARISH"}:
            _log(d1_bias, "HTF_ALIGNED")

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
            _log(d1_bias, "D1_DOMINANT")

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
            _log(h4_bias, "H4_DOMINANT")

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
        _log(None, "NO_HTF_DIRECTION")

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

        confirmed_highs, confirmed_lows = self.detect_swing_points(
            df,
            cfg.external_swing_window
        )

        recent_window = 120
        start_idx = max(
            cfg.external_swing_window + 2,
            len(df) - recent_window
        )

        now_ts = None
        if "time" in df.columns:
            try:
                now_ts = pd.to_datetime(df["time"].iloc[-1])
            except Exception:
                now_ts = None

        max_sweep_age = 24
        max_sweep_age_minutes = 24 * 15  # M15 sweep logic = 6 hours max wall-clock age

        for i in range(len(df) - 1, start_idx - 1, -1):

            curr_high = float(df["high"].iat[i])
            curr_low = float(df["low"].iat[i])
            curr_close = float(df["close"].iat[i])

            atr = self._calc_atr(df, i, cfg.atr_period)
            if atr is None:
                continue
            tolerance = atr * cfg.sweep_atr_tolerance

            sweep_ts = None
            if now_ts is not None and "time" in df.columns:
                try:
                    sweep_ts = pd.to_datetime(df["time"].iat[i])
                except Exception:
                    sweep_ts = None

            candles_ago = (len(df) - 1) - i
            if candles_ago > max_sweep_age:
                continue

            if now_ts is not None and sweep_ts is not None:
                sweep_age_minutes = (now_ts - sweep_ts).total_seconds() / 60.0
                if sweep_age_minutes > max_sweep_age_minutes:
                    continue

            if direction == "BULLISH":

                prior_lows = [
                    idx for idx in confirmed_lows
                    if start_idx <= idx < i
                ]

                if not prior_lows:
                    continue

                for ref_idx in reversed(prior_lows[-2:]):

                    ref_level = float(df["low"].iat[ref_idx])

                    wick_break = curr_low < (ref_level - tolerance)
                    close_back = curr_close > ref_level

                    if wick_break and close_back:
                        left_highs = [
                            idx for idx in confirmed_highs
                            if start_idx <= idx < i
                        ]

                        tp = (
                            float(df["high"].iat[left_highs[-1]])
                            if left_highs
                            else float(
                                df["high"]
                                .iloc[max(start_idx, i - cfg.liquidity_lookback):i]
                                .max()
                            )
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

                prior_highs = [
                    idx for idx in confirmed_highs
                    if start_idx <= idx < i
                ]

                if not prior_highs:
                    continue

                for ref_idx in reversed(prior_highs[-2:]):

                    ref_level = float(df["high"].iat[ref_idx])

                    wick_break = curr_high > (ref_level + tolerance)
                    close_back = curr_close < ref_level

                    if wick_break and close_back:
                        left_lows = [
                            idx for idx in confirmed_lows
                            if start_idx <= idx < i
                        ]

                        tp = (
                            float(df["low"].iat[left_lows[-1]])
                            if left_lows
                            else float(
                                df["low"]
                                .iloc[max(start_idx, i - cfg.liquidity_lookback):i]
                                .min()
                            )
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

        sweep_time = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)

        if m5_sweep_idx is None:
            return {"passed": False, "reason": "SWEEP_MAPPING_FAILED"}, None

        structure_confirmation_window = 24  # ~2 hours on M5
        pre_sweep_pivot_window = 48         # ~4 hours of local pre-sweep structure only

        end_idx = min(
            len(m5_df),
            m5_sweep_idx + structure_confirmation_window
        )
        start_pivot_idx = max(0, m5_sweep_idx - pre_sweep_pivot_window)

        w = self.config.internal_swing_window

        if len(m5_df) < (w * 2 + 1):
            return {"passed": False, "reason": "INSUFFICIENT_STRUCTURE"}, None

        raw_highs, raw_lows = self._find_m5_choch_pivots(m5_df, w)

        up_p, up_n = [], []
        dn_p, dn_n = [], []

        pre_highs = sorted(
            [idx for idx in raw_highs if start_pivot_idx <= idx < m5_sweep_idx],
            reverse=True
        )
        pre_lows = sorted(
            [idx for idx in raw_lows if start_pivot_idx <= idx < m5_sweep_idx],
            reverse=True
        )

        for pivot_idx in pre_highs:
            up_p.append(float(m5_df["high"].iat[pivot_idx]))
            up_n.append(pivot_idx)

        for pivot_idx in pre_lows:
            dn_p.append(float(m5_df["low"].iat[pivot_idx]))
            dn_n.append(pivot_idx)

        if sweep.direction == "BULLISH":
            if not up_p:
                return {"passed": False, "reason": "INSUFFICIENT_STRUCTURE"}, None
        else:
            if not dn_p:
                return {"passed": False, "reason": "INSUFFICIENT_STRUCTURE"}, None

        for i in range(m5_sweep_idx + 1, end_idx):
            close_now = float(m5_df["close"].iat[i])
            close_prev = float(m5_df["close"].iat[i - 1])

            for pivot_idx in raw_highs:
                if m5_sweep_idx <= pivot_idx < i:
                    px = float(m5_df["high"].iat[pivot_idx])
                    if not up_n or pivot_idx > up_n[0]:
                        up_p.insert(0, px)
                        up_n.insert(0, pivot_idx)

            for pivot_idx in raw_lows:
                if m5_sweep_idx <= pivot_idx < i:
                    px = float(m5_df["low"].iat[pivot_idx])
                    if not dn_n or pivot_idx > dn_n[0]:
                        dn_p.insert(0, px)
                        dn_n.insert(0, pivot_idx)

            if sweep.direction == "BULLISH":
                if not up_p:
                    continue

                level = up_p[0]
                crossed = close_prev <= level and close_now > level

                if crossed:
                    choch_label = self._classify_structure_break("BULLISH")

                    if choch_label == "CHOCH" and len(dn_p) >= 2 and dn_p[0] > dn_p[1]:
                        choch_label = "CHOCH+"

                    sb = StructureBreak(
                        direction="BULLISH",
                        choch_label=choch_label,
                        level=level,
                        candle_index=i,
                        close_price=close_now,
                    )

                    up_p.clear()
                    up_n.clear()

                    return {
                        "passed": True,
                        "reason": f"VALID_BULLISH_{choch_label}",
                        "level": self._r(level),
                        "close_price": self._r(close_now),
                        "m5_candle_index": i,
                        "choch_label": choch_label,
                    }, sb

            else:
                if not dn_p:
                    continue

                level = dn_p[0]
                crossed = close_prev >= level and close_now < level

                if crossed:
                    choch_label = self._classify_structure_break("BEARISH")

                    if choch_label == "CHOCH" and len(up_p) >= 2 and up_p[0] < up_p[1]:
                        choch_label = "CHOCH+"

                    sb = StructureBreak(
                        direction="BEARISH",
                        choch_label=choch_label,
                        level=level,
                        candle_index=i,
                        close_price=close_now,
                    )

                    dn_p.clear()
                    dn_n.clear()

                    return {
                        "passed": True,
                        "reason": f"VALID_BEARISH_{choch_label}",
                        "level": self._r(level),
                        "close_price": self._r(close_now),
                        "m5_candle_index": i,
                        "choch_label": choch_label,
                    }, sb

        return {"passed": False, "reason": "NO_CHOCH"}, None

    def _step_valid_poi(
        self,
        m15_df: pd.DataFrame,
        m5_df: pd.DataFrame,
        h4_df: pd.DataFrame,
        d1_df: pd.DataFrame,
        sweep: SweepEvent,
        structure_break: StructureBreak,
    ) -> Tuple[Dict[str, Any], List[POI]]:

        break_time = self._get_candle_time(m5_df, structure_break.candle_index)
        break_m15_idx = self._find_bar_at_or_before(m15_df, break_time)
        if break_m15_idx is None:
            break_m15_idx = len(m15_df) - 1

        lookback = 36
        start = max(0, sweep.candle_index - lookback)
        end = min(len(m15_df) - 1, break_m15_idx)
        if end < start:
            return {"passed": False, "reason": "NO_VALID_M15_POI"}, []

        segment = m15_df.iloc[start:end + 1]
        if segment.empty:
            return {"passed": False, "reason": "NO_VALID_M15_POI"}, []

        candidates: List[POI] = []

        if sweep.direction == "BULLISH":
            candidate_indices = (
                segment["low"]
                .nsmallest(min(5, len(segment)))
                .index.tolist()
            )
        else:
            candidate_indices = (
                segment["high"]
                .nlargest(min(5, len(segment)))
                .index.tolist()
            )

        for idx_label in candidate_indices:
            abs_idx = m15_df.index.get_loc(idx_label)
            candidates.append(self._build_poi(m15_df, "OB", int(abs_idx)))

        candidates = self._dedupe_pois(candidates)
        if not candidates:
            return {"passed": False, "reason": "NO_VALID_M15_POI"}, []

        htf_pois = self._select_htf_institutional_pois(
            m15_df=m15_df,
            h4_df=h4_df,
            d1_df=d1_df,
            sweep=sweep,
            structure_break=structure_break,
        )

        if not htf_pois:
            return {"passed": False, "reason": "NO_HTF_POI"}, []

        htf_aligned: List[POI] = []
        matched_zone: Optional[Tuple[float, float]] = None
        matched_type: Optional[str] = None

        for poi in candidates:
            if self.poi_mitigation.is_breached(poi):
                continue

            for htf_poi in htf_pois:
                overlap = not (poi.high < htf_poi.low or poi.low > htf_poi.high)
                if overlap:
                    htf_aligned.append(poi)
                    matched_zone = (htf_poi.low, htf_poi.high)
                    matched_type = htf_poi.poi_type
                    break

        # print("\n[POI DEBUG] M15 candidates:")
        # for poi in candidates:
        #     print(f"  - {poi.poi_type} idx={poi.candle_index} low={poi.low} high={poi.high} breached={self.poi_mitigation.is_breached(poi)}")

        # print("[POI DEBUG] HTF POIs:")
        # for htf_poi in htf_pois:
        #     print(f"  - {htf_poi.poi_type} idx={htf_poi.candle_index} low={htf_poi.low} high={htf_poi.high}")

        # print("[POI DEBUG] Overlap checks:")
        # for poi in candidates:
        #     for htf_poi in htf_pois:
        #         overlap = not (poi.high < htf_poi.low or poi.low > htf_poi.high)
        #         print(f"  - M15 idx={poi.candle_index} vs {htf_poi.poi_type} idx={htf_poi.candle_index} => overlap={overlap}")

        if not htf_aligned:
            return {"passed": False, "reason": "NO_POI_IN_HTF_POI"}, []

        htf_aligned.sort(key=lambda p: p.candle_index)

        extreme_ob = htf_aligned[0]
        extreme_tagged = POI(
            poi_type="EXTREME_OB",
            candle_index=extreme_ob.candle_index,
            low=extreme_ob.low,
            high=extreme_ob.high,
        )

        if len(htf_aligned) > 1:
            first_ob = htf_aligned[-1]
            first_tagged = POI(
                poi_type="FIRST_OB_AFTER_IDM",
                candle_index=first_ob.candle_index,
                low=first_ob.low,
                high=first_ob.high,
            )
            valid_pois = [extreme_tagged, first_tagged]
        else:
            valid_pois = [extreme_tagged]

        zone_low, zone_high = matched_zone if matched_zone is not None else (extreme_tagged.low, extreme_tagged.high)

        return {
            "passed": True,
            "reason": "HTF_ALIGNED_POI",
            "htf_zone": (self._r(zone_low), self._r(zone_high)),
            "htf_poi_type": matched_type,
            "poi_count": len(valid_pois),
            "poi_types": [p.poi_type for p in valid_pois],
        }, valid_pois

    def _select_htf_institutional_pois(
        self,
        m15_df: pd.DataFrame,
        h4_df: pd.DataFrame,
        d1_df: pd.DataFrame,
        sweep: SweepEvent,
        structure_break: StructureBreak,
    ) -> List[POI]:
        """
        Phase B HTF institutional-origin selector.

        Doctrine-confirmed core:
        - scan the structural leg between IDM and Protected Extreme,
        - keep only the first OB after IDM and the Extreme OB,
        - require displacement and immediate FVG,
        - apply shift rule when the exact anchor candle lacks the valid immediate FVG.

        Engineering approximations:
        - derive IDM / Protected Extreme from pivot lists,
        - reuse current displacement helper,
        - keep optional near-price preference with fallback.

        DEBUG TOTALS VERSION:
        - preserves current behavior
        - accumulates totals across the whole backtest on self._htf_poi_debug_totals
        """
        htf_pois: List[POI] = []

        if not hasattr(self, "_htf_poi_debug_totals"):
            self._htf_poi_debug_totals = {
                "calls": 0,
                "timeframes_checked": 0,
                "too_short_df": 0,
                "not_enough_pivots": 0,
                "bad_anchor_order": 0,
                "empty_leg": 0,
                "first_no_shift_fvg": 0,
                "first_failed_displacement": 0,
                "extreme_no_shift_fvg": 0,
                "extreme_failed_displacement": 0,
                "pois_added": 0,
                "returned_empty": 0,
                "returned_nonempty": 0,
            }

        debug_counts = self._htf_poi_debug_totals
        debug_counts["calls"] += 1

        if len(d1_df) < 10 and len(h4_df) < 10:
            debug_counts["returned_empty"] += 1
            return htf_pois

        current_price = float(m15_df["close"].iloc[-1])
        piv_window = max(2, self.config.external_swing_window)
        immediate_window = 8

        def _apply_shift_rule_within_leg(
            full_df: pd.DataFrame,
            start_idx: int,
            leg_end_idx: int,
        ) -> Optional[int]:
            for idx in range(start_idx, leg_end_idx + 1):
                fvg_start = idx + 1
                fvg_end = min(len(full_df) - 2, leg_end_idx, idx + immediate_window)

                if fvg_end < fvg_start:
                    continue

                immediate_fvgs = self._find_fvgs(
                    full_df,
                    sweep.direction,
                    start=fvg_start,
                    end=fvg_end,
                )
                if immediate_fvgs:
                    return idx

            return None

        timeframe_sets = [
            ("D1", d1_df),
            ("H4", h4_df),
        ]

        for timeframe_name, full_df in timeframe_sets:
            debug_counts["timeframes_checked"] += 1

            if len(full_df) < max(5, piv_window * 2 + 1):
                debug_counts["too_short_df"] += 1
                continue

            ph, pl = self._find_pivots_debug(full_df, piv_window)

            if sweep.direction == "BULLISH":
                pivot_list = pl
            else:
                pivot_list = ph

            if len(pivot_list) < 2:
                debug_counts["not_enough_pivots"] += 1
                continue

            protected_idx = pivot_list[-1]
            idm_idx = pivot_list[-2]

            if protected_idx <= idm_idx:
                debug_counts["bad_anchor_order"] += 1
                continue

            leg_df = full_df.iloc[idm_idx : protected_idx + 1]
            if leg_df.empty or len(leg_df) < 3:
                debug_counts["empty_leg"] += 1
                continue

            if sweep.direction == "BULLISH":
                extreme_rel_idx = int(leg_df["low"].to_numpy().argmin())
            else:
                extreme_rel_idx = int(leg_df["high"].to_numpy().argmax())

            extreme_abs_idx = idm_idx + extreme_rel_idx

            first_after_idm_abs_idx: Optional[int] = None
            for scan_idx in range(idm_idx + 1, protected_idx + 1):
                shifted_idx = _apply_shift_rule_within_leg(full_df, scan_idx, protected_idx)
                if shifted_idx is None:
                    debug_counts["first_no_shift_fvg"] += 1
                    shifted_idx = scan_idx

                candidate_poi = self._build_poi(
                    full_df,
                    f"{timeframe_name}_HTF_FIRST_OB_AFTER_IDM",
                    int(shifted_idx),
                )

                if not self.is_displacement_after_poi(candidate_poi, full_df, sweep.direction):
                    debug_counts["first_failed_displacement"] += 1
                    continue

                first_after_idm_abs_idx = shifted_idx
                break

            shifted_extreme_idx = _apply_shift_rule_within_leg(full_df, extreme_abs_idx, protected_idx)
            if shifted_extreme_idx is None:
                debug_counts["extreme_no_shift_fvg"] += 1
                # NO fallback here — extreme OB must have an immediate FVG
            else:
                extreme_poi = self._build_poi(
                    full_df,
                    f"{timeframe_name}_HTF_EXTREME_OB",
                    int(shifted_extreme_idx),
                )
                if self.is_displacement_after_poi(extreme_poi, full_df, sweep.direction):
                    htf_pois.append(extreme_poi)
                    debug_counts["pois_added"] += 1
                else:
                    debug_counts["extreme_failed_displacement"] += 1

            if first_after_idm_abs_idx is not None:
                first_poi = self._build_poi(
                    full_df,
                    f"{timeframe_name}_HTF_FIRST_OB_AFTER_IDM",
                    int(first_after_idm_abs_idx),
                )
                htf_pois.append(first_poi)
                debug_counts["pois_added"] += 1

        htf_pois = self._dedupe_pois(htf_pois)

        if sweep.direction == "BULLISH":
            nearby_htf_pois = [
                poi for poi in htf_pois
                if poi.high >= current_price and (poi.high - current_price) <= 40.0
            ]
        else:
            nearby_htf_pois = [
                poi for poi in htf_pois
                if poi.low <= current_price and (current_price - poi.low) <= 40.0
            ]

        if nearby_htf_pois:
            htf_pois = nearby_htf_pois

        if htf_pois:
            debug_counts["returned_nonempty"] += 1
        else:
            debug_counts["returned_empty"] += 1

        return htf_pois

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
        # ⚠️ AUDIT CORRECTION — The prior audit claimed Pine Script uses "top/bottom 5% bands".
        # This is FALSE. The Pine Script dealing range display (`toplvl`, `midlvl`, `btmlvl`) shows
        # three horizontal lines at: swing HH (premium), midpoint EQ (equilibrium), swing LL (discount).
        # There is NO 5% multiplier (0.05) anywhere in the Guardeer Pine Script source.
        # The audit hallucinated this rule. The existing equilibrium midpoint check is the correct
        # approach — the only thing tightened here is using the adaptive tolerance correctly.

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

        # 🔴 XAUUSD: Asian session hard-blocked.
        # Broker spreads spike, liquidity is fake, sweeps are traps.
        if session == "ASIAN":
            return {
                "passed": False,
                "reason": "ASIAN_SESSION_BLOCKED",
                "timestamp_utc": ts.isoformat(),
                "session": "ASIAN",
                "killzone_active": False,
            }

        active = session is not None

        # 🔥 BLOCK TRADES OUTSIDE ALL KILLZONES
        if not active:
            return {
                "passed": False,
                "reason": "OUTSIDE_KILLZONE",
                "timestamp_utc": ts.isoformat(),
                "session": None,
                "killzone_active": False,
            }

        # ✅ ALLOW TRADE — London or NY only
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
        selected_poi: "POI",
        structure_break: StructureBreak,
    ) -> Tuple[Dict[str, Any], Optional[float], Optional[float]]:
        """
        Step 8 — Risk/Reward check.

        Implements mentor-style SL models:
        - POI entries: SL at refined OB high/low (+ ATR buffer).
        - Sweep entries: SL at sweep wick (+ ATR buffer).
        - Engineering-liquidity (CHoCH-based): SL at structure_break.level (+ ATR buffer).

        TP logic (for now):
        - Primary TP at sweep.target_external_liquidity (ERL-style).
        """

        # --- helpers ------------------------------------------------------
        def _infer_setup_type() -> str:
            """
            Infer what kind of setup we are in.
            For now:
            - If POI type hints sweep/IDM → treat as sweep setup.
            - If CHoCH/MSS label hints engineering-liquidity → ENGINEERING_LIQ.
            - Default → POI.
            """
            poi_type = (selected_poi.poi_type or "").upper()
            choch_label = (structure_break.choch_label or "").upper()

            # Very simple heuristics; you can tighten these later
            if "SWEEP" in poi_type or "IDM" in poi_type:
                return "SWEEP"

            if "CHOCH" in choch_label or "MSS" in choch_label:
                # You can refine this condition if you only want certain CHoCH types
                return "ENGINEERING_LIQ"

            return "POI"

        def _sl_buffer() -> float:
            return float(sweep.atr_at_sweep) * float(self.config.atr_sl_multiplier)

        def _compute_sl(setup_type: str, sl_buf: float) -> Tuple[float, str]:
            """
            Returns (sl_price, model_used)
            model_used is one of: 'POI_OB', 'SWEEP_WICK', 'CHOCH_LEVEL'
            """

            if direction == "BULLISH":
                if setup_type == "SWEEP":
                    # SL beyond the sweep wick
                    return float(sweep.sweep_price) - sl_buf, "SWEEP_WICK"
                if setup_type == "ENGINEERING_LIQ":
                    # SL at CHoCH level (Last Line of Defense)
                    return float(structure_break.level) - sl_buf, "CHOCH_LEVEL"
                # Default: refined OB low
                return float(selected_poi.low) - sl_buf, "POI_OB_LOW"

            else:  # BEARISH
                if setup_type == "SWEEP":
                    return float(sweep.sweep_price) + sl_buf, "SWEEP_WICK"
                if setup_type == "ENGINEERING_LIQ":
                    return float(structure_break.level) + sl_buf, "CHOCH_LEVEL"
                return float(selected_poi.high) + sl_buf, "POI_OB_HIGH"

        def _compute_tp_primary(sl: float, setup_type: str) -> float:
            """
            TP selection:
            - Counter-trend / engineering-liquidity style: keep conservative target at first ERL.
            - Trend-following: allow farther target if first ERL is too close.
            """
            tp_erl = float(sweep.target_external_liquidity)

            risk, reward_to_erl = _risk_reward(sl, tp_erl)
            rr_min = float(self.config.rr_min)

            # Minimum projected TP needed to satisfy RR threshold
            if direction == "BULLISH":
                tp_rr_min = float(entry_price + (risk * rr_min))
            else:
                tp_rr_min = float(entry_price - (risk * rr_min))

            # Conservative setups keep the nearest target
            if setup_type == "ENGINEERING_LIQ":
                return tp_erl

            # Trend / POI / sweep setups:
            # if ERL already satisfies RR, use it; otherwise stretch to minimum RR target
            if risk > 0 and reward_to_erl / risk >= rr_min:
                return tp_erl

            return tp_rr_min

        def _risk_reward(sl: float, tp: float) -> Tuple[float, float]:
            if direction == "BULLISH":
                risk = entry_price - sl
                reward = tp - entry_price
            else:
                risk = sl - entry_price
                reward = entry_price - tp
            return float(risk), float(reward)

        # --- decision logic ---------------------------------------------- 

        setup_type = _infer_setup_type()
        sl_buffer = _sl_buffer()
        sl, sl_model = _compute_sl(setup_type, sl_buffer)
        tp = _compute_tp_primary(sl, setup_type)

        # Geometry sanity
        risk, reward = _risk_reward(sl, tp)

        if risk <= 0 or reward <= 0:
            return {
                "passed": False,
                "reason": "INVALID_TRADE_GEOMETRY",
                "setup_type": setup_type,
                "sl_model": sl_model,
                "entry": self._r(entry_price),
                "sl": self._r(sl),
                "tp": self._r(tp),
                "risk_pts": self._r(risk),
                "reward_pts": self._r(reward),
                "sl_buffer": self._r(sl_buffer),
            }, None, None

        rr = reward / risk
        rr_min = float(self.config.rr_min)

        EPS = 1e-9
        if rr + EPS < rr_min:
            return {
                "passed": False,
                "reason": "RR_BELOW_MINIMUM",
                "rr": round(float(rr), 4),
                "rr_min": rr_min,
                "setup_type": setup_type,
                "sl_model": sl_model,
                "entry": self._r(entry_price),
                "sl": self._r(sl),
                "tp": self._r(tp),
                "risk_pts": self._r(risk),
                "reward_pts": self._r(reward),
                "sl_buffer": self._r(sl_buffer),
            }, None, None

        # --- final output (backwards compatible) ------------------------- 

        return {
            "passed": True,
            "reason": "OK",
            "rr": round(float(rr), 4),
            "rr_min": rr_min,
            "setup_type": setup_type,
            "sl_model": sl_model,
            "entry": self._r(entry_price),
            "sl": self._r(sl),
            "tp": self._r(tp),
            "risk_pts": self._r(risk),
            "reward_pts": self._r(abs(tp - entry_price)),
            "sl_buffer": self._r(sl_buffer),
        }, sl, tp

    def _classify_structure_break(self, sweep_direction: str) -> str:
        """
        ✅ FIX #4b — Implements Pine Script itrend-based CHoCH vs BOS classification.

        Pine Script logic (confirmed from guardeer.docx):
          if itrend < 0 and bullish crossover: CHoCH (reversal)
          if itrend >= 0 and bullish crossover: BOS (continuation)
          Then itrend := 1 (bullish event occurred)

          Symmetric for bearish.

        CHoCH+ is signaled when: after a CHoCH, the previous low was HIGHER than the one
        before it (bull) — i.e. dn.l.first() > dn.l.get(1) in Pine Script.
        We approximate this by checking if the _last_pivot_low_1 > _last_pivot_low_2
        (tracked via _prev_lows / _prev_highs).

        Returns: 'BOS', 'CHOCH', or 'CHOCPH'
        """
        if sweep_direction == "BULLISH":
            if self._itrend < 0:
                label = "CHOCH"
            else:
                label = "BOS"
            self._itrend = 1
        else:
            if self._itrend > 0:
                label = "CHOCH"
            else:
                label = "BOS"
            self._itrend = -1
        return label

    def _find_m5_choch_pivots(self, df: pd.DataFrame, window: int) -> Tuple[List[int], List[int]]:
        # ✅ FIX #3 — Pine Script ta.pivothigh(high, iLen, iLen) requires exactly iLen bars on BOTH
        # sides before a pivot is confirmed.  The previous implementation used:
        #   right_bars = min(effective_window, n - i - 1)
        # which would confirm a pivot with just 1 right-side bar near the current bar, creating phantom
        # pivots at the edge of data.  This loop now mirrors Pine Script exactly: a pivot at index i is
        # only eligible when i + window <= n - 1 (full right-side window fits within the array).
        n = len(df)
        effective_window = max(1, min(window, (n - 1) // 2))

        if n < (effective_window * 2 + 1):
            return [], []

        highs = df["high"].to_numpy(dtype=float)
        lows  = df["low"].to_numpy(dtype=float)
        ph: List[int] = []
        pl: List[int] = []

        # STRICT: require full window on both sides — no asymmetric right_bars shortcut
        for i in range(effective_window, n):
            if i + effective_window >= n:
                break
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

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def detect_swing_points(
        self,
        df: pd.DataFrame,
        window: int,
        use_m5_pivot_detector: bool = False,
    ) -> Tuple[List[int], List[int]]:
        if use_m5_pivot_detector:
            return self._find_m5_choch_pivots(df, window)

        return self._find_pivots_debug(df, window)

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
            is_strong_body  = body_ratio >= 0.50     # at least 50% body-to-range
            is_large_candle = body >= atr * 0.25    # at least 80% of ATR in body size

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
        # Require enough data to say anything
        if len(df) < window + 5:
            return "NEUTRAL"

        # Look back a reasonable window for HTF swings
        lookback = max(window * 6, 30)
        recent = df.iloc[-lookback:]

        # Use the same pivot logic as elsewhere
        ph, pl = self._find_pivots_debug(recent, max(2, window))

        # =========================
        # 1) STRONG TREND VIA 3 PIVOTS
        # =========================
        if len(ph) >= 3:
            vals = [float(recent["high"].iloc[i]) for i in ph[-3:]]
            if vals[0] < vals[1] < vals[2]:
                return "BULLISH"

        if len(pl) >= 3:
            vals = [float(recent["low"].iloc[i]) for i in pl[-3:]]
            if vals[0] > vals[1] > vals[2]:
                return "BEARISH"

        # =========================
        # 2) STRONG TREND VIA BREAK OF LAST SWING EXTREME
        # =========================
        last_close = float(recent["close"].iat[-1])

        if len(ph) >= 1:
            last_swing_high = float(recent["high"].iloc[ph[-1]])
            if last_close > last_swing_high:
                return "BULLISH"

        if len(pl) >= 1:
            last_swing_low = float(recent["low"].iloc[pl[-1]])
            if last_close < last_swing_low:
                return "BEARISH"

        # =========================
        # 3) FALLBACK — DIRECTION OF LAST SWING LEG
        # =========================
        # If we have at least one swing high and one swing low,
        # use the ordering of the last pivots to infer leg direction.
        if len(ph) >= 1 and len(pl) >= 1:
            last_high_idx = ph[-1]
            last_low_idx = pl[-1]

            if last_low_idx < last_high_idx:
                # Last completed leg is from a low to a later high → bullish context
                return "BULLISH"
            elif last_high_idx < last_low_idx:
                # Last completed leg is from a high to a later low → bearish context
                return "BEARISH"

        # =========================
        # 4) OTHERWISE — GENUINELY NEUTRAL / TOO LITTLE DATA
        # =========================
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
        o = float(df["open"].iat[idx]) if "open" in df.columns else float(df["low"].iat[idx])
        c = float(df["close"].iat[idx]) if "close" in df.columns else float(df["high"].iat[idx])
        h = float(df["high"].iat[idx])
        l = float(df["low"].iat[idx])

        body_low = min(o, c)
        body_high = max(o, c)

        if "FVG" in poi_type or "LIQUIDITY" in poi_type:
            low = min(l, h)
            high = max(l, h)
        else:
            low = body_low
            high = body_high

        return POI(
            poi_type=poi_type,
            candle_index=idx,
            low=min(low, high),
            high=max(low, high),
        )

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