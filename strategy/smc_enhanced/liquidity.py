"""
Liquidity Detection Module (Refactor for Day-2)

Replaces the previous retail-style implementation with a deterministic, SMC-oriented
liquidity engine. Key capabilities:

- PDH / PDL extraction (Previous Day High / Low) as HTF external liquidity targets
- Classic 5-bar fractal detection (swing highs / swing lows)
- Equal Highs / Equal Lows (EQH / EQL) detection
- Wick-based sweep detection (canonical sweep detector)
- Structured, deterministic return schemas and reason_code enums
- Backwards-compatible wrappers for existing callers:
  - get_previous_day_high_low(), get_swing_high_low(), check_liquidity_grab(),
    get_nearest_liquidity_above(), get_nearest_liquidity_below(), get_liquidity_zones()

Notes:
- All methods operate on pandas.DataFrame self.df with columns:
  ['time','open','high','low','close'] and expect contiguous candles.
- Default sweep look-forward window = 12 bars (configurable)
- This module returns structured dicts (no printing). Reason codes are strings.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

try:
    import pytz
except Exception:
    pytz = None  # timezone handling optional; functions will gracefully handle naive datetimes


# Canonical reason codes (strings used in return dicts)
REASON_OK = "OK"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REASON_PDH_PDL_FOUND = "PDH_PDL_FOUND"
REASON_NO_DAILY_DATA = "NO_DAILY_DATA"
REASON_PDH_PDL_DEGENERATE = "PDH_PDL_DEGENERATE"
REASON_FRACTALS_FOUND = "FRACTALS_FOUND"
REASON_INSUFFICIENT_BARS = "INSUFFICIENT_BARS"
REASON_EQ_FOUND = "EQ_FOUND"
REASON_NO_EQ = "NO_EQ"
REASON_SWEEP_DETECTED = "SWEEP_DETECTED"
REASON_NO_SWEEP = "NO_SWEEP"
REASON_SWEEP_WRONG_DIRECTION = "SWEEP_WRONG_DIRECTION"
REASON_LIQUIDITY_EVALUATED = "LIQUIDITY_EVALUATED"
REASON_ZONES_COMPILED = "ZONES_COMPILED"


class LiquidityDetector:
    """LiquidityDetector: HTF/IT liquidity primitives and sweep detection."""

    def __init__(self, df: pd.DataFrame, tz: str = "UTC"):
        """
        Args:
            df: pandas DataFrame with OHLC columns and a 'time' column
            tz: timezone string used when interpreting 'time' (default 'UTC')
        """
        self.df = df.copy() if df is not None else pd.DataFrame()
        self.tz = tz
        self.pdh: Optional[float] = None
        self.pdl: Optional[float] = None
        self.pdh_bar: Optional[int] = None
        self.pdl_bar: Optional[int] = None
        # cached swings (format kept for backwards compatibility)
        self.swings: Dict[str, List[Dict[str, Any]]] = {"highs": [], "lows": []}
        # default look-forward for sweep detection
        self.default_look_forward = 12

    # --------------------------
    # Utility / Default helpers
    # --------------------------
    def _ensure_time_index(self) -> None:
        """Ensure self.df['time'] is pandas.Timestamp and timezone-aware if possible."""
        if "time" not in self.df.columns:
            return
        if not pd.api.types.is_datetime64_any_dtype(self.df["time"]):
            if pd.api.types.is_numeric_dtype(self.df["time"]):
                # assume unix timestamp seconds
                self.df["time"] = pd.to_datetime(self.df["time"], unit="s")
            else:
                self.df["time"] = pd.to_datetime(self.df["time"], errors="coerce")
        # Make timezone-aware if pytz available and tz specified
        if pytz is not None and self.tz:
            try:
                if self.df["time"].dt.tz is None:
                    self.df["time"] = self.df["time"].dt.tz_localize("UTC").dt.tz_convert(self.tz)
                else:
                    self.df["time"] = self.df["time"].dt.tz_convert(self.tz)
            except Exception:
                # fallback: leave as-is (naive timestamps)
                pass

    def _default_pdh_pdl(self) -> Dict[str, Any]:
        return {
            "pdh": None,
            "pdh_bar": None,
            "pdl": None,
            "pdl_bar": None,
            "source_start": None,
            "source_end": None,
            "reason_code": REASON_NO_DAILY_DATA,
        }

    # --------------------------
    # PDH / PDL Extraction
    # --------------------------
    def get_previous_day_high_low(self, lookback_days: int = 1) -> Dict[str, Any]:
        """
        Compute Previous Day High (PDH) and Previous Day Low (PDL) using completed D1 candles.

        Returns:
            {
                'pdh': float|None,
                'pdh_bar': int|None,
                'pdl': float|None,
                'pdl_bar': int|None,
                'source_start': Timestamp|None,
                'source_end': Timestamp|None,
                'reason_code': str
            }

        Notes:
            - Excludes current forming D1 candle; uses only closed daily candles.
            - If exact yesterday range not available, falls back to the last 24h window.
        """
        out = self._default_pdh_pdl()
        if self.df is None or len(self.df) < 1:
            out["reason_code"] = REASON_NO_DAILY_DATA
            return out

        # Ensure time column is datetime
        self._ensure_time_index()
        if "time" not in self.df.columns or self.df["time"].isnull().all():
            out["reason_code"] = REASON_NO_DAILY_DATA
            return out

        try:
            now = datetime.now(pytz.timezone(self.tz)) if pytz is not None else datetime.utcnow()
            # Determine yesterday range in tz
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_start = today_start - timedelta(days=lookback_days)
            yesterday_end = today_start

            df_time = self.df.copy()
            # Filter closed candles for the previous day
            df_yesterday = df_time[(df_time["time"] >= yesterday_start) & (df_time["time"] < yesterday_end)]

            if df_yesterday.empty:
                # fallback to last 24 hours
                cutoff = now - timedelta(hours=24)
                df_yesterday = df_time[df_time["time"] >= cutoff]
                if df_yesterday.empty:
                    out["reason_code"] = REASON_NO_DAILY_DATA
                    return out

            # Compute PDH / PDL
            pdh_val = float(df_yesterday["high"].max())
            pdl_val = float(df_yesterday["low"].min())

            # find first bar (global index) where those extremes occur (prefer earliest)
            # use exact equality; tolerances not applied to PD by design
            # Map back to global df indices
            pdh_bar = None
            pdl_bar = None
            # locate first occurrence in the original df
            highs = self.df["high"].values
            lows = self.df["low"].values
            # using np.isclose is safer for floating rounding issues
            for i in range(len(self.df)):
                if pdh_bar is None and np.isclose(highs[i], pdh_val, atol=1e-8, rtol=0):
                    pdh_bar = int(i)
                if pdl_bar is None and np.isclose(lows[i], pdl_val, atol=1e-8, rtol=0):
                    pdl_bar = int(i)
                if pdh_bar is not None and pdl_bar is not None:
                    break

            out.update(
                {
                    "pdh": pdh_val,
                    "pdh_bar": pdh_bar,
                    "pdl": pdl_val,
                    "pdl_bar": pdl_bar,
                    "source_start": pd.to_datetime(df_yesterday["time"].iloc[0]) if len(df_yesterday) > 0 else None,
                    "source_end": pd.to_datetime(df_yesterday["time"].iloc[-1]) if len(df_yesterday) > 0 else None,
                    "reason_code": REASON_PDH_PDL_FOUND,
                }
            )
            # Degenerate case (pdh == pdl) is unlikely but handle
            if np.isclose(pdh_val, pdl_val):
                out["reason_code"] = REASON_PDH_PDL_DEGENERATE
            # store cached PDH/PDL for quick access
            self.pdh = out["pdh"]
            self.pdl = out["pdl"]
            self.pdh_bar = out["pdh_bar"]
            self.pdl_bar = out["pdl_bar"]
            return out
        except Exception:
            out["reason_code"] = REASON_NO_DAILY_DATA
            return out

    # --------------------------
    # Fractals (5-bar) detection
    # --------------------------
    def detect_fractals(self) -> Dict[str, Any]:
        """
        Detect classic 5-bar fractals (center bar extreme vs two bars each side).

        Returns:
            {
                'swing_highs': [ {'bar': int, 'price': float, 'time': Timestamp, 'fractal_type': '5bar', 'classification': 'unknown'}, ... ],
                'swing_lows' : [ {...}, ... ],
                'reason_code': str
            }
        """
        out = {"swing_highs": [], "swing_lows": [], "reason_code": REASON_INSUFFICIENT_BARS}
        if self.df is None or len(self.df) < 5:
            return out

        highs = self.df["high"].values
        lows = self.df["low"].values
        times = self.df["time"].values if "time" in self.df.columns else [None] * len(self.df)

        swing_highs: List[Dict[str, Any]] = []
        swing_lows: List[Dict[str, Any]] = []

        # 5-bar fractal: index i is fractal center if it's > highs of i-2,i-1,i+1,i+2
        for i in range(2, len(self.df) - 2):
            try:
                if (
                    highs[i] > highs[i - 1]
                    and highs[i] > highs[i - 2]
                    and highs[i] > highs[i + 1]
                    and highs[i] > highs[i + 2]
                ):
                    swing_highs.append(
                        {
                            "bar": int(i),
                            "price": float(highs[i]),
                            "time": pd.to_datetime(times[i]) if times is not None else None,
                            "fractal_type": "5bar",
                            "classification": "unknown",
                            "meta": {},
                        }
                    )
                if (
                    lows[i] < lows[i - 1]
                    and lows[i] < lows[i - 2]
                    and lows[i] < lows[i + 1]
                    and lows[i] < lows[i + 2]
                ):
                    swing_lows.append(
                        {
                            "bar": int(i),
                            "price": float(lows[i]),
                            "time": pd.to_datetime(times[i]) if times is not None else None,
                            "fractal_type": "5bar",
                            "classification": "unknown",
                            "meta": {},
                        }
                    )
            except Exception:
                # skip any problematic index
                continue

        out["swing_highs"] = swing_highs
        out["swing_lows"] = swing_lows
        out["reason_code"] = REASON_FRACTALS_FOUND if (swing_highs or swing_lows) else REASON_INSUFFICIENT_BARS

        # Update backwards-compatible self.swings (old format)
        # old fields: {'price','index','time','bar_number'}
        highs_compat = []
        lows_compat = []
        for s in swing_highs:
            highs_compat.append(
                {
                    "price": s["price"],
                    "index": None,
                    "time": s["time"],
                    "bar_number": s["bar"],
                }
            )
        for s in swing_lows:
            lows_compat.append(
                {"price": s["price"], "index": None, "time": s["time"], "bar_number": s["bar"]}
            )
        self.swings = {"highs": highs_compat, "lows": lows_compat}
        return out

    # Backwards-compatible wrapper used by main.py and other modules
    def get_swing_high_low(self, lookback: int = 20, min_candles_between: int = 2) -> Dict[str, Any]:
        """
        Compatibility wrapper that returns swings in the legacy format expected elsewhere.

        Returns:
            {'highs': [{'price': float, 'index': int, 'time': Timestamp, 'bar_number': int}, ...],
             'lows':  [ ... ] }
        """
        fractals = self.detect_fractals()
        # Convert to legacy structure with lookback trimming semantics
        highs = fractals.get("swing_highs", [])
        lows = fractals.get("swing_lows", [])
        # Only return up to lookback if requested (map bar_number to legacy 'index' relative to tail)
        n = len(self.df)
        recent_threshold = max(0, n - lookback)
        highs_legacy = []
        lows_legacy = []
        for h in highs:
            if h["bar"] >= recent_threshold:
                highs_legacy.append({"price": h["price"], "index": None, "time": h["time"], "bar_number": h["bar"]})
        for l in lows:
            if l["bar"] >= recent_threshold:
                lows_legacy.append({"price": l["price"], "index": None, "time": l["time"], "bar_number": l["bar"]})
        # update internal cache
        self.swings = {"highs": highs_legacy, "lows": lows_legacy}
        return {"highs": highs_legacy, "lows": lows_legacy}

    # --------------------------
    # Equal High / Equal Low detection
    # --------------------------
    def detect_equal_extremes(self, lookback: int = 100, tol: float = 1e-6) -> Dict[str, Any]:
        """
        Detect Equal Highs (EQH) and Equal Lows (EQL) in the recent lookback.

        Args:
            lookback: number of bars from the tail to inspect
            tol: absolute tolerance for comparing prices

        Returns:
            {
                'eq_highs': [{'price': float, 'count': int, 'bars': [int,...], 'first_bar': int, 'last_bar': int}, ...],
                'eq_lows':  [...],
                'reason_code': str
            }
        """
        out = {"eq_highs": [], "eq_lows": [], "reason_code": REASON_NO_EQ}
        if self.df is None or len(self.df) < 2:
            return out
        n = len(self.df)
        start = max(0, n - lookback)
        highs = self.df["high"].iloc[start:].values
        lows = self.df["low"].iloc[start:].values
        # Build clusters by scanning unique values with isclose grouping
        # For simplicity, round to reasonably small decimal places determined by tol
        # but use isclose for membership tests
        eq_highs = []
        used = set()
        for i in range(len(highs)):
            if i in used:
                continue
            price_i = float(highs[i])
            bars = [start + i]
            used.add(i)
            for j in range(i + 1, len(highs)):
                if np.isclose(price_i, highs[j], atol=tol, rtol=0):
                    bars.append(start + j)
                    used.add(j)
            if len(bars) >= 2:
                eq_highs.append({"price": price_i, "count": len(bars), "bars": bars, "first_bar": bars[0], "last_bar": bars[-1]})
        eq_lows = []
        used = set()
        for i in range(len(lows)):
            if i in used:
                continue
            price_i = float(lows[i])
            bars = [start + i]
            used.add(i)
            for j in range(i + 1, len(lows)):
                if np.isclose(price_i, lows[j], atol=tol, rtol=0):
                    bars.append(start + j)
                    used.add(j)
            if len(bars) >= 2:
                eq_lows.append({"price": price_i, "count": len(bars), "bars": bars, "first_bar": bars[0], "last_bar": bars[-1]})

        out["eq_highs"] = eq_highs
        out["eq_lows"] = eq_lows
        out["reason_code"] = REASON_EQ_FOUND if (eq_highs or eq_lows) else REASON_NO_EQ
        return out

    # --------------------------
    # Wick-based sweep detector (core primitive)
    # --------------------------
    def wick_sweep_detector(self, level_price: float, start_bar: int, look_forward: Optional[int] = None) -> Dict[str, Any]:
        """
        Detect earliest wick sweep beyond level_price after start_bar.

        Args:
            level_price: price level to test (e.g., PDH / a swing high or PDL / a swing low)
            start_bar: integer bar index in self.df from which to start searching (inclusive)
            look_forward: number of bars to scan forward (defaults to self.default_look_forward)

        Returns:
            {
                'is_sweep': bool,
                'sweep_bar_index': int|None,
                'sweep_price': float|None,
                'sweep_wick_type': 'upper'|'lower'|None,
                'sweep_direction': 'up'|'down'|None,
                'look_forward': int
            }
        """
        res = {
            "is_sweep": False,
            "sweep_bar_index": None,
            "sweep_price": None,
            "sweep_wick_type": None,
            "sweep_direction": None,
            "look_forward": look_forward or self.default_look_forward,
        }
        if self.df is None or len(self.df) == 0:
            res["look_forward"] = 0
            return res
        n = len(self.df)
        if start_bar is None or start_bar < 0:
            res["look_forward"] = 0
            return res
        if look_forward is None:
            look_forward = self.default_look_forward
        end = min(n, start_bar + 1 + look_forward)
        highs = self.df["high"].values
        lows = self.df["low"].values
        # scan forward earliest-first
        for idx in range(start_bar + 1, end):
            # detect upper wick piercing
            if highs[idx] > level_price:
                res.update(
                    {
                        "is_sweep": True,
                        "sweep_bar_index": int(idx),
                        "sweep_price": float(highs[idx]),
                        "sweep_wick_type": "upper",
                        "sweep_direction": "up",
                        "look_forward": look_forward,
                    }
                )
                return res
            # detect lower wick piercing
            if lows[idx] < level_price:
                res.update(
                    {
                        "is_sweep": True,
                        "sweep_bar_index": int(idx),
                        "sweep_price": float(lows[idx]),
                        "sweep_wick_type": "lower",
                        "sweep_direction": "down",
                        "look_forward": look_forward,
                    }
                )
                return res
        return res

    # --------------------------
    # PD sweep wrapper
    # --------------------------
    def detect_pd_sweeps(self, look_forward: Optional[int] = None) -> Dict[str, Any]:
        """
        Check PDH / PDL for wick sweeps.

        Returns:
            {
                'pdh': float|None,
                'pdh_bar': int|None,
                'pdh_sweep': { ... wick_sweep_detector schema ... } | None,
                'pdl': float|None,
                'pdl_bar': int|None,
                'pdl_sweep': { ... } | None,
                'reason_code': str
            }
        """
        pd = self.get_previous_day_high_low()
        output = {
            "pdh": pd.get("pdh"),
            "pdh_bar": pd.get("pdh_bar"),
            "pdh_sweep": None,
            "pdl": pd.get("pdl"),
            "pdl_bar": pd.get("pdl_bar"),
            "pdl_sweep": None,
            "reason_code": REASON_LIQUIDITY_EVALUATED,
        }
        if pd.get("reason_code") != REASON_PDH_PDL_FOUND:
            output["reason_code"] = pd.get("reason_code", REASON_NO_DAILY_DATA)
            return output

        if output["pdh"] is not None and output["pdh_bar"] is not None:
            output["pdh_sweep"] = self.wick_sweep_detector(level_price=output["pdh"], start_bar=output["pdh_bar"], look_forward=look_forward)
        if output["pdl"] is not None and output["pdl_bar"] is not None:
            output["pdl_sweep"] = self.wick_sweep_detector(level_price=output["pdl"], start_bar=output["pdl_bar"], look_forward=look_forward)
        return output

    # --------------------------
    # Backwards-compatible liquidity grab checker (replaces old boolean logic)
    # --------------------------
    def check_liquidity_grab(self, current_price: float, look_forward: Optional[int] = None) -> Dict[str, Any]:
        """
        High-level wrapper used by main.py to check whether PDH/PDL or recent swings were 'grabbed' (swept).

        Returns structured dict:
            {
                'pdh_grabbed': bool,
                'pdh_grab_details': {..} | None,
                'pdl_grabbed': bool,
                'pdl_grab_details': {..} | None,
                'swing_highs_grabbed': [...],
                'swing_lows_grabbed': [...],
                'nearest_above': {...} | None,
                'nearest_below': {...} | None,
                'reason_code': str
            }

        Important: This method uses wick-based sweep detection anchored on the bar where the level occurred.
        """
        out = {
            "pdh_grabbed": False,
            "pdh_grab_details": None,
            "pdl_grabbed": False,
            "pdl_grab_details": None,
            "swing_highs_grabbed": [],
            "swing_lows_grabbed": [],
            "nearest_above": None,
            "nearest_below": None,
            "reason_code": REASON_LIQUIDITY_EVALUATED,
        }

        # Ensure PDH/PDL known
        pd_info = self.get_previous_day_high_low()
        pdh = pd_info.get("pdh")
        pdl = pd_info.get("pdl")
        pdh_bar = pd_info.get("pdh_bar")
        pdl_bar = pd_info.get("pdl_bar")

        # Use fractals to populate swings
        fractals = self.detect_fractals()
        swing_highs = fractals.get("swing_highs", [])
        swing_lows = fractals.get("swing_lows", [])

        # PDH sweep check
        if pdh is not None and pdh_bar is not None:
            pdh_sweep = self.wick_sweep_detector(level_price=pdh, start_bar=pdh_bar, look_forward=look_forward)
            out["pdh_grabbed"] = bool(pdh_sweep.get("is_sweep", False))
            out["pdh_grab_details"] = pdh_sweep if out["pdh_grabbed"] else None

        # PDL sweep check
        if pdl is not None and pdl_bar is not None:
            pdl_sweep = self.wick_sweep_detector(level_price=pdl, start_bar=pdl_bar, look_forward=look_forward)
            out["pdl_grabbed"] = bool(pdl_sweep.get("is_sweep", False))
            out["pdl_grab_details"] = pdl_sweep if out["pdl_grabbed"] else None

        # Swing sweeps: iterate recent swings and check if sweeps occurred after their bar
        for s in swing_highs:
            detail = self.wick_sweep_detector(level_price=s["price"], start_bar=s["bar"], look_forward=look_forward)
            if detail.get("is_sweep", False):
                out["swing_highs_grabbed"].append({"bar": s["bar"], "price": s["price"], "grabbed": True, "detail": detail})

        for s in swing_lows:
            detail = self.wick_sweep_detector(level_price=s["price"], start_bar=s["bar"], look_forward=look_forward)
            if detail.get("is_sweep", False):
                out["swing_lows_grabbed"].append({"bar": s["bar"], "price": s["price"], "grabbed": True, "detail": detail})

        # Nearest liquidity targets (compatibility)
        nearest = self.get_nearest_liquidity(current_price=current_price)
        out["nearest_above"] = nearest.get("targets_above", [None])[0] if nearest.get("targets_above") else None
        out["nearest_below"] = nearest.get("targets_below", [None])[-1] if nearest.get("targets_below") else None

        return out

    # --------------------------
    # Nearest liquidity utilities
    # --------------------------
    def get_nearest_liquidity(self, current_price: float, max_candidates: int = 5) -> Dict[str, Any]:
        """
        Return nearest liquidity targets above and below current_price.

        Returns:
            {
                'targets_above': [ {'type': str,'price': float,'bar': int,'distance': float,'meta': {...}}, ... ],
                'targets_below': [ ... ],
                'reason_code': str
            }
        """
        out = {"targets_above": [], "targets_below": [], "reason_code": REASON_OK}
        if self.df is None or len(self.df) == 0:
            out["reason_code"] = REASON_INSUFFICIENT_DATA
            return out

        zones = []

        # PDH / PDL
        pd = self.get_previous_day_high_low()
        if pd.get("pdh") is not None:
            zones.append({"type": "PDH", "price": pd["pdh"], "bar": pd["pdh_bar"], "is_external": True})
        if pd.get("pdl") is not None:
            zones.append({"type": "PDL", "price": pd["pdl"], "bar": pd["pdl_bar"], "is_external": True})

        # Fractals
        fr = self.detect_fractals()
        for h in fr.get("swing_highs", []):
            zones.append({"type": "SWING_HIGH", "price": h["price"], "bar": h["bar"], "is_external": False})
        for l in fr.get("swing_lows", []):
            zones.append({"type": "SWING_LOW", "price": l["price"], "bar": l["bar"], "is_external": False})

        # EQs
        eq = self.detect_equal_extremes()
        for e in eq.get("eq_highs", []):
            zones.append({"type": "EQH", "price": e["price"], "bar": e["first_bar"], "is_external": False, "meta": {"count": e["count"], "bars": e["bars"]}})
        for e in eq.get("eq_lows", []):
            zones.append({"type": "EQL", "price": e["price"], "bar": e["first_bar"], "is_external": False, "meta": {"count": e["count"], "bars": e["bars"]}})

        # Partition into above / below
        above = [z for z in zones if z["price"] > current_price]
        below = [z for z in zones if z["price"] < current_price]
        # sort by distance
        above_sorted = sorted(above, key=lambda z: z["price"])[:max_candidates]
        below_sorted = sorted(below, key=lambda z: -z["price"])[:max_candidates]

        # add distance field
        for z in above_sorted:
            z["distance"] = float(z["price"] - current_price)
            z.setdefault("meta", {})
        for z in below_sorted:
            z["distance"] = float(current_price - z["price"])
            z.setdefault("meta", {})

        out["targets_above"] = above_sorted
        out["targets_below"] = below_sorted
        out["reason_code"] = REASON_OK
        return out

    # Backwards-compatible wrappers
    def get_nearest_liquidity_above(self, current_price: float):
        res = self.get_nearest_liquidity(current_price=current_price, max_candidates=1)
        return res["targets_above"][0] if res.get("targets_above") else None

    def get_nearest_liquidity_below(self, current_price: float):
        res = self.get_nearest_liquidity(current_price=current_price, max_candidates=1)
        return res["targets_below"][0] if res.get("targets_below") else None

    # --------------------------
    # Aggregate zones for POI engine
    # --------------------------
    def get_liquidity_zones(self) -> Dict[str, Any]:
        """
        Compile canonical liquidity zones for the current data set.

        Returns:
            {
                'previous_day_high': {...} | None,
                'previous_day_low': {...} | None,
                'eq_highs': [...],
                'eq_lows': [...],
                'swing_highs': [...],
                'swing_lows': [...],
                'reason_code': str
            }

        Each zone includes: price, bar, time (if available), is_external flag and meta placeholder.
        """
        out: Dict[str, Any] = {
            "previous_day_high": None,
            "previous_day_low": None,
            "eq_highs": [],
            "eq_lows": [],
            "swing_highs": [],
            "swing_lows": [],
            "reason_code": REASON_ZONES_COMPILED,
        }

        pd = self.get_previous_day_high_low()
        if pd.get("pdh") is not None:
            out["previous_day_high"] = {
                "price": pd["pdh"],
                "bar": pd["pdh_bar"],
                "time": pd.get("source_end"),
                "is_external": True,
                "meta": {},
            }
        if pd.get("pdl") is not None:
            out["previous_day_low"] = {
                "price": pd["pdl"],
                "bar": pd["pdl_bar"],
                "time": pd.get("source_end"),
                "is_external": True,
                "meta": {},
            }
 # impprove liquidty code from detecting fake brekaout to true finding liquidity
        fr = self.detect_fractals()
        for h in fr.get("swing_highs", []):
            out["swing_highs"].append({"price": h["price"], "bar": h["bar"], "time": h["time"], "is_external": False, "meta": h.get("meta", {})})
        for l in fr.get("swing_lows", []):
            out["swing_lows"].append({"price": l["price"], "bar": l["bar"], "time": l["time"], "is_external": False, "meta": l.get("meta", {})})

        eq = self.detect_equal_extremes()
        out["eq_highs"] = eq.get("eq_highs", [])
        out["eq_lows"] = eq.get("eq_lows", [])
        out["reason_code"] = REASON_ZONES_COMPILED
        return out