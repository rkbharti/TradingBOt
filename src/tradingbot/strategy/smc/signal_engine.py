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

# Killzones in UTC — aligned with creator's NY-time windows (Lecture 10)
# Creator: London 02:00–05:00 NY = 07:00–10:00 UTC
# Creator: New York 07:00–12:00 NY = 12:00–17:00 UTC
# Asian session is hard-blocked (fake liquidity, wide spreads on XAUUSD)
# FIX #11: Widened London (was 07:00-09:00) and NY (was 13:00-15:30) to match
#          the creator's actual kill zone boundaries from Lecture 10.
KILLZONES_UTC: List[Tuple[time, time, str]] = [
    (time(0, 0),  time(3, 0),   "ASIAN"),
    (time(7, 0),  time(10, 0),  "LONDON"),
    (time(12, 0), time(17, 0),  "NEW_YORK"),
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
    # FIX #10: Entry module classification — identifies which of the creator's
    # 5 Entry Modules triggered. Used for confidence scoring and audit logging.
    # Values: IDM_SWEEP, IDM_ORDER_BLOCK, EXTREME_OB, BOS_SWEEP, CHOCH_SWEEP, GENERIC
    entry_module: str = "GENERIC"


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
    # ── Timeframe bias weights ─────────────────────────────────────────────────
    # FIX #6: Added w1_weight = 5. Creator: "Higher timeframe values are greater
    # than lower timeframe values." W1 is the boss — must outweigh D1 and H4.
    # Old code used getattr fallback of 1.0 which made W1 the WEAKEST TF. Wrong.
    w1_weight: int = 5
    d1_weight: int = 4
    h4_weight: int = 3

    external_swing_window: int = 50
    # ✅ FIX #1 — Pine Script internal_r_lookback defaults to 5 (iLen=5).
    # Was incorrectly set to 2, producing hyper-sensitive micro-pivots on every
    # minor wiggle and flooding CHoCH detection with false structure breaks.
    internal_swing_window: int = 5

    recent_sweep_bars: int = 80
    liquidity_lookback: int = 80

    atr_period: int = 14
    # FIX #3b (SL buffer): Reduced from 0.5 to 0.3 for refined OBs.
    # Creator says "give a little buffer" — 0.5x ATR was too wide on XAUUSD.
    # With wick-to-wick OB zones (FIX #4), the OB itself already includes the
    # wick, so the additional buffer only needs to cover minor noise.
    atr_sl_multiplier: float = 0.3

    sweep_atr_tolerance: float = 0.25
    min_atr_threshold: float = 0.05

    min_m5_candles: int = 50
    min_m15_candles: int = 50
    min_h4_candles: int = 200
    min_d1_candles: int = 120

    # FIX #1: rr_min raised from 2.0 → 3.0.
    # Creator: minimum 1:3. Targets 1:4, 1:5, 1:10+.
    # Creator explicitly calls 1:1.5 "low expectation" and 1:2 borderline.
    # This is the SINGLE SOURCE OF TRUTH for RR — backtest and executor
    # both read from this value. No more three different RR minimums.
    rr_min: float = 3.0

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
        w1_df: Optional[pd.DataFrame] = None,  # ✅ Added
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
        if current_atr is None or current_atr < self.config.min_atr_threshold:
            return _reject(gates, "LOW_VOLATILITY_REGIME")

        # ─── Step 1: HTF Bias ───────────────────────────────────────
        step1 = self._step_htf_bias(d1_df, h4_df, w1_df)  # ✅ w1_df passed
        gates["step_1_htf_bias"] = step1
        if not step1["passed"]:
            return _reject(gates, step1["reason"])

        direction: str = step1["direction"]

        # ─── Step 2: External Liquidity Sweep ───────────────────────
        step2, sweep = self._step_external_liquidity_sweep(m15_df, direction)
        gates["step_2_external_liquidity_sweep"] = step2
        if not step2["passed"] or sweep is None:
            return _reject(gates, step2["reason"], direction=direction)

        # ─── Step 3: CHOCH / MSS Body Close ─────────────────────────
        step3, structure_break = self._step_choch_mss_body_close(m5_df, sweep, m15_df)
        gates["step_3_choch_mss_body_close"] = step3
        if not step3["passed"] or structure_break is None:
            return _reject(gates, step3["reason"], direction=direction)

        # ─── Step 4: Valid POI ───────────────────────────────────────
        step4, poi_candidates = self._step_valid_poi(
            m15_df, m5_df, h4_df, d1_df, sweep, structure_break
        )
        gates["step_4_valid_poi"] = step4
        if not step4["passed"] or not poi_candidates:
            return _reject(gates, step4["reason"], direction=direction)

        # ─── Step 5: OB/FVG Confluence ──────────────────────────────
        step5, selected_poi, selected_fvg, entry_price = self._step_ob_fvg_confluence(
            m5_df, m15_df, direction, sweep, structure_break, poi_candidates
        )
        gates["step_5_ob_fvg_confluence"] = step5
        if not step5["passed"] or selected_poi is None or selected_fvg is None or entry_price is None:
            return _reject(gates, step5["reason"], direction=direction)

        # ─── Step 6: Dealing Range ───────────────────────────────────
        step6 = self._step_dealing_range(direction, entry_price, sweep)
        gates["step_6_dealing_range"] = step6
        if not step6["passed"]:
            return _reject(gates, step6["reason"], direction=direction)

        # ─── Step 7: Killzone ────────────────────────────────────────
        step7 = self._step_killzone(now_utc, m5_df)
        gates["step_7_killzone"] = step7
        if not step7["passed"]:
            return _reject(gates, step7["reason"], direction=direction)

        # ─── Step 7.5: News Filter ───────────────────────────────────
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

        # ─── Step 8: Risk/Reward ─────────────────────────────────────
        step8, sl_price, tp_price = self._step_rr(
            direction, entry_price, sweep, selected_poi, structure_break,
        )
        gates["step_8_risk_reward"] = step8
        if not step8["passed"]:
            return _reject(gates, step8["reason"], direction=direction)

        # ─── FIX #8/#9: IFSC detection — upgrade entry price if IFSC found ──
        # Creator: for sweep entries the IFSC close IS the entry price.
        # We scan M5 candles inside the selected POI zone for an IFSC candle.
        # If found, use its close as entry (more precise than OB/FVG overlap).
        # If not found, fall back to the existing overlap-based entry_price.
        ifsc_result = self._detect_ifsc(
            df=m5_df,
            direction=direction,
            zone_low=selected_poi.low,
            zone_high=selected_poi.high,
            scan_start=structure_break.candle_index,
            scan_end=min(len(m5_df) - 1, structure_break.candle_index + 50),
        )
        if ifsc_result is not None:
            entry_price = ifsc_result["entry_price"]
            gates["step_5_ob_fvg_confluence"]["ifsc_detected"] = True
            gates["step_5_ob_fvg_confluence"]["ifsc_entry"]    = entry_price
        else:
            gates["step_5_ob_fvg_confluence"]["ifsc_detected"] = False

        # ─── FIX #7: IDM detection — enrich gate data ────────────────
        # Detect IDM relative to the sweep candle (sweep acts as the BOS proxy
        # on M5 — the sweep itself is the structural move we're trading off).
        idm_result = self._detect_idm(
            df=m5_df,
            direction=direction,
            bos_candle_idx=structure_break.candle_index,
            lookback=60,
        )
        gates["step_3_choch_mss_body_close"]["idm_detected"] = idm_result is not None
        gates["step_3_choch_mss_body_close"]["idm_swept"]    = (
            idm_result.get("is_swept", False) if idm_result else False
        )
        gates["step_3_choch_mss_body_close"]["idm_level"]    = (
            idm_result.get("idm_level") if idm_result else None
        )

        # ─── FIX #10: Entry module classification + confidence score ─
        entry_module, confidence = self._classify_entry_module(
            poi=selected_poi,
            structure_break=structure_break,
            ifsc_result=ifsc_result,
            idm_result=idm_result,
        )

        # ─── All Gates Passed ────────────────────────────────────────
        return SignalResult(
            action="ENTER",
            direction=direction,
            entry_price=self._r(entry_price),
            sl_price=self._r(sl_price),
            tp_price=self._r(tp_price),
            gates=gates,
            reason="ALL_GATES_PASSED",
            confidence_score=confidence,
            entry_module=entry_module,
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
        w1_df: Optional[pd.DataFrame] = None,  # ✅ Weekly added
    ) -> Dict[str, Any]:

        # ✅ FIX 1: Three-tier hierarchy — W1 → D1 → H4
        w1_bias = (
            self._infer_bias(w1_df, self.config.external_swing_window)
            if w1_df is not None and len(w1_df) >= self.config.external_swing_window * 2 + 1
            else "NEUTRAL"
        )
        d1_bias = self._infer_bias(d1_df, self.config.external_swing_window)
        h4_bias = self._infer_bias(h4_df, self.config.external_swing_window)

        if self.config.time_column in d1_df.columns:
            ts = d1_df.iloc[-1][self.config.time_column]
        else:
            ts = d1_df.index[-1]

        def _log(direction: Optional[str], reason: str, is_pullback: bool = False):
            self._bias_debug_rows.append({
                "time":        ts,
                "w1_bias":     w1_bias,
                "d1_bias":     d1_bias,
                "h4_bias":     h4_bias,
                "direction":   direction,
                "reason":      reason,
                "is_pullback": is_pullback,
            })

        # ─────────────────────────────────────────────────────────────────
        # CASE 1 — Full alignment W1 + D1 + H4
        # Highest probability — all three agree
        # ─────────────────────────────────────────────────────────────────
        if (
            w1_bias == d1_bias == h4_bias
            and d1_bias in {"BULLISH", "BEARISH"}
        ):
            _log(d1_bias, "FULL_HTF_ALIGNED")
            return {
                "passed":          True,
                "direction":       d1_bias,
                "reason":          "FULL_HTF_ALIGNED",
                "w1_bias":         w1_bias,
                "d1_bias":         d1_bias,
                "h4_bias":         h4_bias,
                "is_pullback":     False,
                "agreement_score": (
                    self.config.d1_weight +
                    self.config.h4_weight +
                    self.config.w1_weight
                ),
            }

        # ─────────────────────────────────────────────────────────────────
        # CASE 2 — W1 dominant, D1/H4 conflict or neutral
        # W1 is the "boss" — lower TFs are pullbacks
        # ─────────────────────────────────────────────────────────────────
        if w1_bias in {"BULLISH", "BEARISH"}:
            # ✅ FIX 2: Conflict = pullback, not skip
            # D1 or H4 opposing = price seeking HTF POI before continuation
            is_pullback = (
                d1_bias not in {w1_bias, "NEUTRAL"} or
                h4_bias not in {w1_bias, "NEUTRAL"}
            )
            _log(w1_bias, "W1_DOMINANT", is_pullback=is_pullback)
            return {
                "passed":          True,
                "direction":       w1_bias,
                "reason":          "W1_DOMINANT",
                "w1_bias":         w1_bias,
                "d1_bias":         d1_bias,
                "h4_bias":         h4_bias,
                "is_pullback":     is_pullback,
                "agreement_score": self.config.w1_weight,
            }

        # ─────────────────────────────────────────────────────────────────
        # CASE 3 — W1 neutral, D1 dominant
        # D1 defines objective; H4 conflict = pullback to HTF POI
        # ─────────────────────────────────────────────────────────────────
        if d1_bias in {"BULLISH", "BEARISH"}:
            is_pullback = h4_bias not in {d1_bias, "NEUTRAL"}
            reason = "D1_DOMINANT_H4_PULLBACK" if is_pullback else "D1_DOMINANT"
            _log(d1_bias, reason, is_pullback=is_pullback)
            return {
                "passed":          True,
                "direction":       d1_bias,
                "reason":          reason,
                "w1_bias":         w1_bias,
                "d1_bias":         d1_bias,
                "h4_bias":         h4_bias,
                "is_pullback":     is_pullback,
                "agreement_score": self.config.d1_weight,
            }

        # ─────────────────────────────────────────────────────────────────
        # CASE 4 — W1 + D1 neutral, H4 has direction
        # Lowest confidence — H4 alone
        # ─────────────────────────────────────────────────────────────────
        if h4_bias in {"BULLISH", "BEARISH"}:
            _log(h4_bias, "H4_DOMINANT")
            return {
                "passed":          True,
                "direction":       h4_bias,
                "reason":          "H4_DOMINANT",
                "w1_bias":         w1_bias,
                "d1_bias":         d1_bias,
                "h4_bias":         h4_bias,
                "is_pullback":     False,
                "agreement_score": self.config.h4_weight,
            }

        # ─────────────────────────────────────────────────────────────────
        # CASE 5 — All neutral → genuine no-trade
        # Creator: "wait for market to complete its story"
        # ─────────────────────────────────────────────────────────────────
        _log(None, "NO_HTF_DIRECTION")
        return {
            "passed":          False,
            "direction":       None,
            "reason":          "NO_HTF_DIRECTION",
            "w1_bias":         w1_bias,
            "d1_bias":         d1_bias,
            "h4_bias":         h4_bias,
            "is_pullback":     False,
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

        if direction == "BULLISH":
            # Pre-build once — not inside per-candle loop
            prior_lows_all = [
                idx for idx in confirmed_lows
                if start_idx <= idx < (len(df) - 1)
            ]

            for i in range(len(df) - 1, start_idx - 1, -1):
                curr_low   = float(df["low"].iat[i])
                curr_close = float(df["close"].iat[i])

                atr = self._calc_atr(df, i, cfg.atr_period)
                if atr is None:
                    continue
                tolerance = atr * cfg.sweep_atr_tolerance

                # Only use swings that formed BEFORE this candle
                prior_lows = [idx for idx in prior_lows_all if idx < i]
                if not prior_lows:
                    continue

                # ✅ FIX 1: Check ALL unmitigated prior lows, newest first
                for ref_idx in reversed(prior_lows):
                    ref_level = float(df["low"].iat[ref_idx])

                    wick_break = curr_low  < (ref_level - tolerance)
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

        else:  # BEARISH
            prior_highs_all = [
                idx for idx in confirmed_highs
                if start_idx <= idx < (len(df) - 1)
            ]

            for i in range(len(df) - 1, start_idx - 1, -1):
                curr_high  = float(df["high"].iat[i])
                curr_close = float(df["close"].iat[i])

                atr = self._calc_atr(df, i, cfg.atr_period)
                if atr is None:
                    continue
                tolerance = atr * cfg.sweep_atr_tolerance

                prior_highs = [idx for idx in prior_highs_all if idx < i]
                if not prior_highs:
                    continue

                # ✅ FIX 1: All unmitigated prior highs, newest first
                for ref_idx in reversed(prior_highs):
                    ref_level = float(df["high"].iat[ref_idx])

                    wick_break = curr_high  > (ref_level + tolerance)
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

        sweep_time   = self._get_candle_time(m15_df, sweep.candle_index)
        m5_sweep_idx = self._find_bar_at_or_after(m5_df, sweep_time)

        if m5_sweep_idx is None:
            return {"passed": False, "reason": "SWEEP_MAPPING_FAILED"}, None

        structure_confirmation_window = 50
        pre_sweep_pivot_window        = 96

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

        def _has_displacement(start_idx: int, end_idx: int) -> bool:
            fvgs = self._find_fvgs(
                m5_df, sweep.direction,
                start=start_idx,
                end=min(len(m5_df) - 2, end_idx),
            )
            return len(fvgs) > 0

        for i in range(m5_sweep_idx + 1, end_idx):
            close_now  = float(m5_df["close"].iat[i])
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

                level   = up_p[0]
                crossed = close_prev <= level and close_now > level

                if crossed:
                    # ✅ FIX: Keep level — wait for fresh displacement
                    # Creator: slow move = lethargic PA = stay out, don't invalidate
                    if not _has_displacement(m5_sweep_idx, i):
                        continue  # level preserved — next candle may have displacement

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

                level   = dn_p[0]
                crossed = close_prev >= level and close_now < level

                if crossed:
                    # ✅ FIX: Keep level — wait for fresh displacement
                    if not _has_displacement(m5_sweep_idx, i):
                        continue  # level preserved — next candle may have displacement

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

        break_time    = self._get_candle_time(m5_df, structure_break.candle_index)
        break_m15_idx = self._find_bar_at_or_before(m15_df, break_time)
        if break_m15_idx is None:
            break_m15_idx = len(m15_df) - 1

        lookback = 36
        start = max(0, sweep.candle_index - lookback)
        end   = min(len(m15_df) - 1, break_m15_idx)

        if end < start:
            return {"passed": False, "reason": "NO_VALID_M15_POI"}, []

        segment = m15_df.iloc[start : end + 1]
        if segment.empty:
            return {"passed": False, "reason": "NO_VALID_M15_POI"}, []

        # ─── HTF POIs first ───────────────────────────────────────────────
        htf_pois = self._select_htf_institutional_pois(
            m15_df=m15_df,
            h4_df=h4_df,
            d1_df=d1_df,
            sweep=sweep,
            structure_break=structure_break,
        )
        if not htf_pois:
            return {"passed": False, "reason": "NO_HTF_POI"}, []

        # ─── Valid OB = last opposing candle before displacement ──────────
        def _find_valid_obs(
            df: pd.DataFrame,
            seg_start: int,
            seg_end: int,
            direction: str,
        ) -> List[int]:
            valid = []
            for idx in range(seg_end, seg_start - 1, -1):
                o = float(df["open"].iat[idx])
                c = float(df["close"].iat[idx])

                is_opposing = (
                    (direction == "BULLISH" and c < o) or
                    (direction == "BEARISH" and c > o)
                )
                if not is_opposing:
                    continue

                atr = self._calc_atr(df, idx, self.config.atr_period)
                if atr is None:
                    continue

                look_fwd = min(len(df) - 1, idx + 3)
                fvgs = self._find_fvgs(
                    df, direction,
                    start=idx + 1,
                    end=look_fwd,
                )
                if fvgs:
                    valid.append(idx)

            return valid

        seg_abs_start = int(m15_df.index.get_loc(segment.index[0]))
        seg_abs_end   = int(m15_df.index.get_loc(segment.index[-1]))

        ob_indices = _find_valid_obs(
            m15_df, seg_abs_start, seg_abs_end,
            sweep.direction,
        )

        if not ob_indices:
            return {"passed": False, "reason": "NO_VALID_M15_POI"}, []

        candidates = [
            self._build_poi(m15_df, "OB", idx)
            for idx in ob_indices
        ]
        candidates = self._dedupe_pois(candidates)

        # ─── HTF alignment + mitigation checks ───────────────────────────
        htf_aligned: List[Tuple[POI, POI]] = []

        for poi in candidates:

            if self.poi_mitigation.is_breached(poi):
                continue

            # ❌ MT segment check REMOVED — was self-invalidating
            # OB candle's own body sits inside segment and triggers
            # the check against itself. MT validation stays in
            # _step_ob_fvg_confluence retest scan only.

            poi_size = max(poi.high - poi.low, 1e-9)

            # ✅ 50% overlap threshold — not fully_inside
            for htf_poi in htf_pois:
                overlap_low  = max(poi.low,  htf_poi.low)
                overlap_high = min(poi.high, htf_poi.high)
                overlap_size = overlap_high - overlap_low

                sufficiently_inside = (
                    overlap_size > 0 and
                    (overlap_size / poi_size) >= 0.50
                )
                if sufficiently_inside:
                    htf_aligned.append((poi, htf_poi))
                    break

        if not htf_aligned:
            return {"passed": False, "reason": "NO_POI_IN_HTF_POI"}, []

        # ─── Priority: First OB after IDM > Extreme OB ───────────────────
        def _poi_priority(pair: Tuple[POI, POI]) -> int:
            t = pair[1].poi_type
            if "FIRST_OB" in t: return 0
            if "EXTREME"  in t: return 1
            return 2

        htf_aligned.sort(key=_poi_priority)

        valid_pois:   List[POI] = []
        matched_zone: Optional[Tuple[float, float]] = None
        matched_type: Optional[str] = None
        seen_types:   set = set()

        for m15_ob, htf_poi in htf_aligned:
            label = (
                "FIRST_OB_AFTER_IDM"
                if "FIRST_OB" in htf_poi.poi_type
                else "EXTREME_OB"
            )
            if label in seen_types:
                continue

            valid_pois.append(POI(
                poi_type=label,
                candle_index=m15_ob.candle_index,
                low=m15_ob.low,
                high=m15_ob.high,
            ))
            seen_types.add(label)

            if matched_zone is None:
                matched_zone = (htf_poi.low, htf_poi.high)
                matched_type = htf_poi.poi_type

        if not valid_pois:
            return {"passed": False, "reason": "NO_POI_IN_HTF_POI"}, []

        zone_low, zone_high = matched_zone

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

        htf_pois: List[POI] = []

        if not hasattr(self, "_htf_poi_debug_totals"):
            self._htf_poi_debug_totals = {
                "calls": 0, "timeframes_checked": 0,
                "too_short_df": 0, "not_enough_pivots": 0,
                "bad_anchor_order": 0, "empty_leg": 0,
                "first_no_shift_fvg": 0, "first_failed_displacement": 0,
                "extreme_no_shift_fvg": 0, "extreme_failed_displacement": 0,
                "pois_added": 0, "returned_empty": 0, "returned_nonempty": 0,
            }

        debug_counts = self._htf_poi_debug_totals
        debug_counts["calls"] += 1

        if len(d1_df) < 10 and len(h4_df) < 10:
            debug_counts["returned_empty"] += 1
            return htf_pois

        current_price    = float(m15_df["close"].iloc[-1])
        piv_window       = max(2, self.config.external_swing_window)
        immediate_window = 8

        # ✅ FIX 1: No proximity filter — creator considers all valid POIs
        # regardless of distance to current price

        # ✅ FIX 2: Only current dealing range leg — creator doctrine confirmed
        MAX_PAIRS_TO_SCAN = 1

        def _find_first_ob_after_idm(
            full_df: pd.DataFrame,
            idm_idx: int,
            protected_idx: int,
        ) -> Optional[int]:
            for scan_idx in range(idm_idx + 1, protected_idx + 1):
                fvg_end = min(len(full_df) - 2, protected_idx, scan_idx + immediate_window)
                immediate_fvgs = self._find_fvgs(
                    full_df, sweep.direction,
                    start=scan_idx + 1, end=fvg_end,
                )
                if immediate_fvgs:
                    return scan_idx

                debug_counts["first_no_shift_fvg"] += 1

                candidate = self._build_poi(
                    full_df, "HTF_FIRST_OB_ADVANCED", scan_idx,
                )
                if self.is_displacement_after_poi(candidate, full_df, sweep.direction):
                    return scan_idx

                debug_counts["first_failed_displacement"] += 1

            return None

        def _find_extreme_ob(
            full_df: pd.DataFrame,
            idm_idx: int,
            protected_idx: int,
        ) -> Optional[int]:
            if sweep.direction == "BULLISH":
                leg         = full_df.iloc[idm_idx: protected_idx + 1]
                extreme_rel = int(leg["low"].to_numpy().argmin())
            else:
                leg         = full_df.iloc[idm_idx: protected_idx + 1]
                extreme_rel = int(leg["high"].to_numpy().argmax())

            extreme_abs = idm_idx + extreme_rel

            for idx in range(extreme_abs, protected_idx + 1):
                fvg_end = min(len(full_df) - 2, protected_idx, idx + immediate_window)
                immediate_fvgs = self._find_fvgs(
                    full_df, sweep.direction,
                    start=idx + 1, end=fvg_end,
                )
                if immediate_fvgs:
                    return idx

            debug_counts["extreme_no_shift_fvg"] += 1
            return extreme_abs

        def _is_valid_leg(
            full_df: pd.DataFrame,
            idm_idx: int,
            protected_idx: int,
        ) -> bool:
            if protected_idx <= idm_idx:
                return False
            leg = full_df.iloc[idm_idx: protected_idx + 1]
            if len(leg) < 3:
                return False
            return True

        timeframe_sets = [("D1", d1_df), ("H4", h4_df)]

        for timeframe_name, full_df in timeframe_sets:
            debug_counts["timeframes_checked"] += 1

            if len(full_df) < max(5, piv_window * 2 + 1):
                debug_counts["too_short_df"] += 1
                continue

            ph, pl = self._find_pivots_debug(full_df, piv_window)
            pivot_list = pl if sweep.direction == "BULLISH" else ph

            if len(pivot_list) < 2:
                debug_counts["not_enough_pivots"] += 1
                continue

            leg_found   = False
            pairs_tried = 0

            for pair_end in range(len(pivot_list) - 1, 0, -1):
                if pairs_tried >= MAX_PAIRS_TO_SCAN:
                    break

                protected_idx = pivot_list[pair_end]
                idm_idx       = pivot_list[pair_end - 1]
                pairs_tried  += 1

                if not _is_valid_leg(full_df, idm_idx, protected_idx):
                    debug_counts["bad_anchor_order"] += 1
                    continue

                leg_df = full_df.iloc[idm_idx: protected_idx + 1]
                if leg_df.empty or len(leg_df) < 3:
                    debug_counts["empty_leg"] += 1
                    continue

                extreme_idx = _find_extreme_ob(full_df, idm_idx, protected_idx)
                if extreme_idx is not None:
                    extreme_poi = self._build_poi(
                        full_df,
                        f"{timeframe_name}_HTF_EXTREME_OB",
                        int(extreme_idx),
                    )
                    htf_pois.append(extreme_poi)
                    debug_counts["pois_added"] += 1
                else:
                    debug_counts["extreme_failed_displacement"] += 1

                first_idx = _find_first_ob_after_idm(full_df, idm_idx, protected_idx)
                if first_idx is not None:
                    first_poi = self._build_poi(
                        full_df,
                        f"{timeframe_name}_HTF_FIRST_OB_AFTER_IDM",
                        int(first_idx),
                    )
                    htf_pois.append(first_poi)
                    debug_counts["pois_added"] += 1

                if extreme_idx is not None or first_idx is not None:
                    leg_found = True
                    break

            if not leg_found:
                debug_counts["not_enough_pivots"] += 1

        htf_pois = self._dedupe_pois(htf_pois)

        # ✅ FIX 1: Proximity filter removed — all valid POIs returned
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
        fvg_end   = min(len(m5_df) - 2, structure_break.candle_index + 10)
        if fvg_end < fvg_start:
            fvg_end = len(m5_df) - 2

        fvg_list = self._find_fvgs(m5_df, direction, start=fvg_start, end=fvg_end)

        if not fvg_list:
            fvg_end_wide = min(len(m5_df) - 2, structure_break.candle_index + 20)
            fvg_list = self._find_fvgs(
                m5_df, direction,
                start=fvg_start,
                end=fvg_end_wide,
            )
        if not fvg_list:
            return {"passed": False, "reason": "FVG_NOT_FOUND"}, None, None, None

        # ATR computed once — used for proximity and wick tolerance
        atr     = self._calc_atr(m5_df, len(m5_df) - 1, self.config.atr_period)
        atr_tol = (atr * 0.1) if atr else 0.0  # ✅ wick tolerance

        best: Optional[Tuple[POI, FVG, float, float]] = None

        for poi in poi_candidates:
            poi_size = max(poi.high - poi.low, 1e-9)
            poi_mid  = (poi.low + poi.high) / 2.0

            if not self.is_displacement_after_poi(poi, m5_df, direction):
                continue

            for fvg in fvg_list:

                overlap_low  = max(poi.low,  fvg.low)
                overlap_high = min(poi.high, fvg.high)
                has_overlap  = overlap_high > overlap_low

                # ATR-based proximity fallback
                near_threshold = (atr * 0.25) if atr else (poi_size * 0.5)
                distance = min(
                    abs(poi.low  - fvg.high),
                    abs(poi.high - fvg.low),
                )
                is_near = distance <= near_threshold

                if not has_overlap and not is_near:
                    continue

                if has_overlap:
                    score = (overlap_high - overlap_low) / poi_size
                else:
                    score = 0.3 * (1.0 - min(distance / near_threshold, 1.0))

                is_advanced = "ADVANCED" in poi.poi_type

                if is_advanced:
                    entry = poi_mid
                else:
                    if direction == "BULLISH":
                        entry = overlap_high if has_overlap else poi.high
                    else:
                        entry = overlap_low  if has_overlap else poi.low

                # ─── Retest confirmation ──────────────────────────────────
                retest_found = False
                scan_start   = structure_break.candle_index
                # ✅ FIX: extended from 30 → 50 candles
                scan_end     = min(len(m5_df), scan_start + 50)

                zone_low  = overlap_low  if has_overlap else poi.low
                zone_high = overlap_high if has_overlap else poi.high
                zone_mid  = (zone_low + zone_high) / 2.0

                for j in range(scan_start, scan_end):
                    candle = m5_df.iloc[j]
                    high_  = float(candle["high"])
                    low_   = float(candle["low"])
                    open_  = float(candle["open"])
                    close_ = float(candle["close"])

                    body   = abs(close_ - open_)
                    range_ = max(high_ - low_, 1e-9)

                    # Body must not close through 50% MT — creator confirmed
                    if direction == "BULLISH":
                        body_through_mt = close_ < zone_mid
                    else:
                        body_through_mt = close_ > zone_mid

                    if body_through_mt:
                        continue

                    # Strong body required — creator confirmed displacement needed
                    if body <= 0.5 * range_:
                        continue

                    if direction == "BULLISH":
                        # ✅ FIX: ATR tolerance on wick lower bound
                        wick_in_zone = (
                            low_ <= zone_high and
                            low_ >= (zone_low - atr_tol)
                        )
                        # ✅ FIX: close back inside zone sufficient
                        # (not required to close fully above zone_high)
                        rejection = close_ > zone_low

                        if wick_in_zone and rejection:
                            retest_found = True
                            break
                    else:
                        # ✅ FIX: ATR tolerance on wick upper bound
                        wick_in_zone = (
                            high_ >= zone_low and
                            high_ <= (zone_high + atr_tol)
                        )
                        # ✅ FIX: close back inside zone sufficient
                        rejection = close_ < zone_high

                        if wick_in_zone and rejection:
                            retest_found = True
                            break

                if not retest_found:
                    continue

                if best is None or score > best[3]:
                    best = (poi, fvg, entry, score)

        if best is None:
            return {
                "passed": False,
                "reason": "OB_FVG_CONFLUENCE_MISSING",
            }, None, None, None

        poi, fvg, entry, score = best

        return {
            "passed": True,
            "reason": "OK",
            "poi_type": poi.poi_type,
            "poi_zone": (self._r(poi.low),  self._r(poi.high)),
            "fvg_zone": (self._r(fvg.low),  self._r(fvg.high)),
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

    # ── IDM Detection (FIX #7) ────────────────────────────────────────────────

    def _detect_idm(
        self,
        df: pd.DataFrame,
        direction: str,
        bos_candle_idx: int,
        lookback: int = 80,
    ) -> Optional[Dict[str, Any]]:
        """
        FIX #7 — Proper IDM (Inducement) detection per creator's Lecture 3.

        Creator's exact definition:
        "The first swing on the left side [after BOS] is our IDM."
        IDM = the FIRST internal swing formed AFTER a BOS, before the next
        structural high/low is confirmed.

        Rules implemented:
        1. Find the first confirmed swing low (bullish) or swing high (bearish)
           that formed AFTER the BOS candle index.
        2. Check if that swing has been swept:
           - Wick sweep is SUFFICIENT (body close NOT required).
           - Creator: "wick ban jaye to bhi chalega" (wick is enough).
        3. Validate it is NOT an inside bar (inside bars cannot be IDM).
           An inside bar is a candle whose high < prior candle high AND
           low > prior candle low — fully contained within the prior candle.
        4. The sweep candle must close back above (bullish) or below (bearish)
           the IDM level — this is the IFSC pattern.

        Returns dict with:
            idm_level: float — the IDM swing price
            idm_candle_idx: int — index of the IDM swing candle
            sweep_candle_idx: int — index of the candle that swept IDM
            is_swept: bool — whether IDM has been taken
            is_ifsc: bool — whether the sweep candle closed back (IFSC pattern)
        Returns None if no valid IDM found.
        """
        if len(df) < bos_candle_idx + 3:
            return None

        w = self.config.internal_swing_window
        raw_highs, raw_lows = self._find_m5_choch_pivots(df, w)

        scan_end = min(len(df), bos_candle_idx + lookback)

        def _is_inside_bar(df: pd.DataFrame, idx: int) -> bool:
            """Inside bar: fully contained within the prior candle. Invalid IDM."""
            if idx < 1:
                return False
            curr_high = float(df["high"].iat[idx])
            curr_low  = float(df["low"].iat[idx])
            prev_high = float(df["high"].iat[idx - 1])
            prev_low  = float(df["low"].iat[idx - 1])
            return curr_high < prev_high and curr_low > prev_low

        if direction == "BULLISH":
            # First swing LOW after BOS = IDM for bullish setups
            idm_candidates = [
                idx for idx in raw_lows
                if bos_candle_idx < idx < scan_end
                and not _is_inside_bar(df, idx)
            ]
            if not idm_candidates:
                return None

            idm_idx   = idm_candidates[0]  # FIRST swing low after BOS
            idm_level = float(df["low"].iat[idm_idx])

            # Check if IDM has been swept (wick below idm_level is enough)
            for sweep_idx in range(idm_idx + 1, scan_end):
                candle_low   = float(df["low"].iat[sweep_idx])
                candle_close = float(df["close"].iat[sweep_idx])

                wick_swept = candle_low < idm_level
                if wick_swept:
                    # IFSC: sweep candle closes BACK ABOVE the IDM level
                    is_ifsc = candle_close > idm_level
                    return {
                        "idm_level":       self._r(idm_level),
                        "idm_candle_idx":  idm_idx,
                        "sweep_candle_idx": sweep_idx,
                        "is_swept":        True,
                        "is_ifsc":         is_ifsc,
                        "direction":       "BULLISH",
                    }

            # IDM identified but not yet swept
            return {
                "idm_level":       self._r(idm_level),
                "idm_candle_idx":  idm_idx,
                "sweep_candle_idx": None,
                "is_swept":        False,
                "is_ifsc":         False,
                "direction":       "BULLISH",
            }

        else:  # BEARISH
            # First swing HIGH after BOS = IDM for bearish setups
            idm_candidates = [
                idx for idx in raw_highs
                if bos_candle_idx < idx < scan_end
                and not _is_inside_bar(df, idx)
            ]
            if not idm_candidates:
                return None

            idm_idx   = idm_candidates[0]  # FIRST swing high after BOS
            idm_level = float(df["high"].iat[idm_idx])

            for sweep_idx in range(idm_idx + 1, scan_end):
                candle_high  = float(df["high"].iat[sweep_idx])
                candle_close = float(df["close"].iat[sweep_idx])

                wick_swept = candle_high > idm_level
                if wick_swept:
                    is_ifsc = candle_close < idm_level
                    return {
                        "idm_level":       self._r(idm_level),
                        "idm_candle_idx":  idm_idx,
                        "sweep_candle_idx": sweep_idx,
                        "is_swept":        True,
                        "is_ifsc":         is_ifsc,
                        "direction":       "BEARISH",
                    }

            return {
                "idm_level":       self._r(idm_level),
                "idm_candle_idx":  idm_idx,
                "sweep_candle_idx": None,
                "is_swept":        False,
                "is_ifsc":         False,
                "direction":       "BEARISH",
            }

    # ── IFSC Detection (FIX #8) ───────────────────────────────────────────────

    def _detect_ifsc(
        self,
        df: pd.DataFrame,
        direction: str,
        zone_low: float,
        zone_high: float,
        scan_start: int,
        scan_end: int,
    ) -> Optional[Dict[str, Any]]:
        """
        FIX #8 — IFSC (Institutional Funding Smart Candle) detection.

        Creator's exact definition (Lecture IFSC):
        "The candle that sweeps or grabs any liquidity, swing, BOS, or CHoCH."
        "Body goes below/above the level and during the close, the body comes
         back and closes inside the range." — long wick, close back inside.

        IFSC rules implemented:
        1. Candle wicks INTO the OB zone (wick touches zone_low for bullish,
           zone_high for bearish).
        2. Candle sweeps a prior swing (wick goes THROUGH a prior high/low
           within the zone — the liquidity grab).
        3. Candle body CLOSES BACK inside the range:
           - Bullish: close > zone_low (closes back above the swept level)
           - Bearish: close < zone_high (closes back below the swept level)
        4. Long wick relative to body — body < 50% of total candle range.
           This distinguishes IFSC from a regular strong candle.

        Entry price = close of the IFSC candle (creator: "enter on IFSC close").

        Returns dict with:
            ifsc_candle_idx: int
            entry_price: float — IFSC close price
            swept_level: float — the prior swing that was grabbed
        Returns None if no IFSC found in the scan window.
        """
        if scan_end >= len(df):
            scan_end = len(df) - 1

        # Pre-compute prior swings within the zone for liquidity grab check
        w = self.config.internal_swing_window
        raw_highs, raw_lows = self._find_m5_choch_pivots(df, w)

        for i in range(scan_start, scan_end + 1):
            candle_high  = float(df["high"].iat[i])
            candle_low   = float(df["low"].iat[i])
            candle_open  = float(df["open"].iat[i])
            candle_close = float(df["close"].iat[i])

            candle_range = candle_high - candle_low
            if candle_range <= 0:
                continue

            body         = abs(candle_close - candle_open)
            body_ratio   = body / candle_range

            # IFSC has a long wick — body must be less than 50% of range
            if body_ratio >= 0.5:
                continue

            if direction == "BULLISH":
                # Wick must touch or enter the OB zone from below
                wick_in_zone = candle_low <= zone_high and candle_low >= (zone_low - zone_high * 0.001)

                if not wick_in_zone:
                    continue

                # Find a prior swing low within or near the zone that was swept
                swept_level = None
                for pl_idx in reversed(raw_lows):
                    if pl_idx >= i:
                        continue
                    pl_price = float(df["low"].iat[pl_idx])
                    # Swing low must be within the OB zone
                    if zone_low <= pl_price <= zone_high:
                        if candle_low < pl_price:  # wick swept below the swing
                            swept_level = pl_price
                            break

                if swept_level is None:
                    # Fallback: wick swept the zone_low itself
                    if candle_low < zone_low:
                        swept_level = zone_low

                if swept_level is None:
                    continue

                # Body closes back ABOVE the swept level (back inside range)
                closes_back = candle_close > swept_level

                if closes_back:
                    return {
                        "ifsc_candle_idx": i,
                        "entry_price":     self._r(candle_close),
                        "swept_level":     self._r(swept_level),
                        "body_ratio":      round(body_ratio, 3),
                        "direction":       "BULLISH",
                    }

            else:  # BEARISH
                wick_in_zone = candle_high >= zone_low and candle_high <= (zone_high + zone_high * 0.001)

                if not wick_in_zone:
                    continue

                swept_level = None
                for ph_idx in reversed(raw_highs):
                    if ph_idx >= i:
                        continue
                    ph_price = float(df["high"].iat[ph_idx])
                    if zone_low <= ph_price <= zone_high:
                        if candle_high > ph_price:
                            swept_level = ph_price
                            break

                if swept_level is None:
                    if candle_high > zone_high:
                        swept_level = zone_high

                if swept_level is None:
                    continue

                closes_back = candle_close < swept_level

                if closes_back:
                    return {
                        "ifsc_candle_idx": i,
                        "entry_price":     self._r(candle_close),
                        "swept_level":     self._r(swept_level),
                        "body_ratio":      round(body_ratio, 3),
                        "direction":       "BEARISH",
                    }

        return None

    # ── Entry Module Classification (FIX #10) ────────────────────────────────

    def _classify_entry_module(
        self,
        poi: "POI",
        structure_break: "StructureBreak",
        ifsc_result: Optional[Dict],
        idm_result: Optional[Dict],
    ) -> Tuple[str, int]:
        """
        FIX #10 — Classify which of the creator's 5 Entry Modules triggered.
        Returns (module_name, confidence_score).

        Creator's 5 modules and probabilities (Lecture IFSC):
        1. IDM_SWEEP       — IFSC sweeps IDM, closes back         → 95-100%
        2. IDM_ORDER_BLOCK — First valid OB above/below IDM        → 85%
        3. EXTREME_OB      — Last OB before CHoCH                  → 90%
        4. BOS_SWEEP       — BOS level swept by wick not body      → 85%
        5. CHOCH_SWEEP     — CHoCH level swept by wick             → 90%

        Priority: IDM_SWEEP > EXTREME_OB / CHOCH_SWEEP > IDM_ORDER_BLOCK > GENERIC
        """
        poi_type    = (poi.poi_type or "").upper()
        choch_label = (structure_break.choch_label or "").upper()

        # Module 1: IDM Sweep — IFSC present AND IDM was swept
        if (
            ifsc_result is not None
            and idm_result is not None
            and idm_result.get("is_swept")
            and idm_result.get("is_ifsc")
        ):
            return "IDM_SWEEP", 95

        # Module 5: CHoCH Sweep — CHoCH level was swept (CHoCH+ label)
        if "CHOCH+" in choch_label or "CHOCH_SWEEP" in poi_type:
            return "CHOCH_SWEEP", 90

        # Module 3: Extreme OB — last OB before CHoCH
        if "EXTREME" in poi_type:
            return "EXTREME_OB", 90

        # Module 4: BOS Sweep — BOS level swept
        if "BOS_SWEEP" in poi_type:
            return "BOS_SWEEP", 85

        # Module 2: IDM Order Block — first OB after IDM
        if "FIRST_OB" in poi_type or (idm_result is not None and idm_result.get("is_swept")):
            return "IDM_ORDER_BLOCK", 85

        # Fallback
        return "GENERIC", 75

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
        """
        Creator-aligned bias inference:
        Priority 1 — Liquidity sweep of key level + close back
        Priority 2 — Body BOS beyond last confirmed swing
        Priority 3 — Confirmed HH/HL or LL/LH sequence
        Priority 4 — Last swing leg direction (lowest confidence)
        """
        if len(df) < window + 5:
            return "NEUTRAL"

        lookback = min(len(df), max(window * 4, 60))
        recent   = df.iloc[-lookback:].reset_index(drop=True)

        ph, pl = self._find_pivots_debug(recent, max(2, window))

        # ── Helpers ───────────────────────────────────────────────────────

        def _body_high(idx: int) -> float:
            return max(
                float(recent["open"].iat[idx]),
                float(recent["close"].iat[idx]),
            )

        def _body_low(idx: int) -> float:
            return min(
                float(recent["open"].iat[idx]),
                float(recent["close"].iat[idx]),
            )

        last_idx   = len(recent) - 1
        last_close = float(recent["close"].iat[last_idx])
        last_open  = float(recent["open"].iat[last_idx])

        # ═══════════════════════════════════════════════════════════════
        # PRIORITY 1 — Liquidity Sweep of Key Level
        # Creator: PDL wick + close back above → bullish bias
        #          PDH wick + close back below → bearish bias
        # Use the most recent confirmed swing high/low as key level
        # ═══════════════════════════════════════════════════════════════
        if pl:
            key_low   = float(recent["low"].iat[pl[-1]])
            curr_low  = float(recent["low"].iat[last_idx])
            # Wick swept below key low AND body closed back above
            wick_swept_low  = curr_low  < key_low
            body_back_above = _body_low(last_idx) > key_low

            if wick_swept_low and body_back_above:
                return "BULLISH"

        if ph:
            key_high  = float(recent["high"].iat[ph[-1]])
            curr_high = float(recent["high"].iat[last_idx])
            # Wick swept above key high AND body closed back below
            wick_swept_high = curr_high > key_high
            body_back_below = _body_high(last_idx) < key_high

            if wick_swept_high and body_back_below:
                return "BEARISH"

        # ═══════════════════════════════════════════════════════════════
        # PRIORITY 2 — Body BOS beyond last confirmed swing
        # Creator: body close (not wick) beyond swing = valid BOS
        # ═══════════════════════════════════════════════════════════════
        if ph:
            last_swing_high = float(recent["high"].iat[ph[-1]])
            # ✅ FIX 2: body close required — not just any close
            if _body_high(last_idx) > last_swing_high:
                return "BULLISH"

        if pl:
            last_swing_low = float(recent["low"].iat[pl[-1]])
            if _body_low(last_idx) < last_swing_low:
                return "BEARISH"

        # ═══════════════════════════════════════════════════════════════
        # PRIORITY 3 — Confirmed HH/HL or LL/LH sequence
        # Creator: HH confirmed only after IDM taken
        #          Single CHoCH sufficient — no need for 3 pivots
        # ═══════════════════════════════════════════════════════════════
        if len(ph) >= 2 and len(pl) >= 2:
            hh_vals = [float(recent["high"].iat[i]) for i in ph[-2:]]
            hl_vals = [float(recent["low"].iat[i])  for i in pl[-2:]]
            ll_vals = [float(recent["low"].iat[i])  for i in pl[-2:]]
            lh_vals = [float(recent["high"].iat[i]) for i in ph[-2:]]

            # ✅ FIX 3/4: HH + HL both required (not just HH sequence)
            has_hh = hh_vals[1] > hh_vals[0]
            has_hl = hl_vals[1] > hl_vals[0]
            has_ll = ll_vals[1] < ll_vals[0]
            has_lh = lh_vals[1] < lh_vals[0]

            if has_hh and has_hl:
                return "BULLISH"
            if has_ll and has_lh:
                return "BEARISH"

            # Partial — one confirmed leg (single CHoCH sufficient)
            if has_hh and not has_lh:
                return "BULLISH"
            if has_ll and not has_hl:
                return "BEARISH"

        # ═══════════════════════════════════════════════════════════════
        # PRIORITY 4 — Last swing leg direction (lowest confidence)
        # Creator: temporary directional hint only
        # ═══════════════════════════════════════════════════════════════
        if ph and pl:
            last_high_idx = ph[-1]
            last_low_idx  = pl[-1]

            if last_low_idx < last_high_idx:
                return "BULLISH"
            if last_high_idx < last_low_idx:
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
            pass

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
        """
        Build a POI zone from a candle.

        FIX #4 — Creator uses FULL CANDLE (wick to wick) as the default OB zone.
        Old code used body only (open-to-close), which made zones too narrow and
        caused missed retests when price wicked into the real OB but not the body.

        Zone rules:
        - Default (OB): wick low → wick high (full candle range)
        - FVG / LIQUIDITY types: also wick-to-wick (unchanged)
        - SL placement automatically improves: selected_poi.low is now the wick
          low, matching the creator's "SL just below the OB low" rule.
        """
        h = float(df["high"].iat[idx])
        l = float(df["low"].iat[idx])

        # Always use full candle range (wick to wick) — creator confirmed
        low  = l
        high = h

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