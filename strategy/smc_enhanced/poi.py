"""
POI Identifier (Refactor Day-3 v2.1)
Corrective refactor implementing the Day-3 POI blueprint fixes (v2.1).

Notes:
- This file performs surgical corrections only (per instructions).
- Applied mandatory fixes:
  1) BOS matching logic uses structural validation (no equality/tolerance).
  2) Kill-zone default is permissive when session_detector is None.
  3) OB candidate generation requires displacement >= mean body size (last 20 bars).
- No public function signatures changed.
- No prints/logs. Default behavior remains BLOCK.
"""

from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import numpy as np

# Canonical reason codes (must match the contract)
REASON_OK = "OK"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

REASON_FVG_FOUND = "FVG_FOUND"
REASON_NO_FVG = "NO_FVG"
REASON_FVG_ASSOCIATED = "FVG_ASSOCIATED"
REASON_NO_FVG_ASSOCIATION = "NO_FVG_ASSOCIATION"

REASON_BOS_CONFIRMED = "BOS_CONFIRMED"
REASON_BOS_NOT_FOUND = "BOS_NOT_FOUND"
REASON_SWEEP_CONFIRMED = "SWEEP_CONFIRMED"
REASON_NO_SWEEP = "NO_SWEEP"
REASON_SWEEP_WRONG_DIRECTION = "SWEEP_WRONG_DIRECTION"

REASON_VALID_OB = "VALID_OB_WITH_FVG_AND_BOS"
REASON_INVALID_NO_FVG = "INVALID_NO_FVG"
REASON_INVALID_NO_BOS_NO_SWEEP = "INVALID_NO_BOS_NO_SWEEP"
REASON_NOT_IN_CORRECT_ARRAY = "NOT_IN_CORRECT_ARRAY"
REASON_OUTSIDE_KILL_ZONE = "OUTSIDE_KILL_ZONE"
REASON_BLOCKED_BY_PERMISSION_GATE = "BLOCKED_BY_PERMISSION_GATE"

# Block classes
BREAKER = "BREAKER_BLOCK"
MITIGATION = "MITIGATION_BLOCK"
RECLAIMED = "RECLAIMED_BLOCK"
WEAK = "WEAK_BLOCK"


class POIIdentifier:
    """Point of Interest Identifier per Day-3 contract (v2.1)."""

    def __init__(self, df: pd.DataFrame):
        """
        Args:
            df: pandas.DataFrame with columns ['time','open','high','low','close'] (contiguous)
        """
        self.df = df.copy() if df is not None else pd.DataFrame()
        # cached containers for backwards compatibility
        self.order_blocks: Dict[str, List[Dict[str, Any]]] = {"bullish": [], "bearish": []}
        self.fvgs: List[Dict[str, Any]] = []

    # -----------------------
    # FVG Detection
    # -----------------------
    def find_fvg(self, lookback: int = 200) -> List[Dict[str, Any]]:
        """
        Deterministic 3-bar FVG detection.
        Returns list of FVG objects conforming to contract schema.
        """
        fvgs: List[Dict[str, Any]] = []
        if self.df is None or len(self.df) < 3:
            return fvgs

        n = len(self.df)
        start = max(0, n - lookback)
        # Use deterministic scan: for i in [start .. n-3], compare bar i and i+2
        for i in range(start, n - 2):
            try:
                c1_high = float(self.df["high"].iloc[i])
                c1_low = float(self.df["low"].iloc[i])
                c3_high = float(self.df["high"].iloc[i + 2])
                c3_low = float(self.df["low"].iloc[i + 2])
            except Exception:
                continue

            # Bullish FVG: c1_high < c3_low -> gap between c1 high and c3 low
            if c1_high < c3_low:
                fvg = {
                    "id": f"FVG:{i+1}:BULL",             # deterministic id uses middle bar index
                    "type": "BULLISH",
                    "top": float(c1_high),
                    "bottom": float(c3_low),
                    "gap_size": float(c3_low - c1_high),
                    "created_by_bar": int(i + 1),
                    "created_time": pd.to_datetime(self.df["time"].iloc[i + 1]) if "time" in self.df.columns else None,
                    "mitigated": False,
                    "mitigated_at_bar": None,
                    "meta": {},
                }
                fvgs.append(fvg)

            # Bearish FVG: c1_low > c3_high
            if c1_low > c3_high:
                fvg = {
                    "id": f"FVG:{i+1}:BEAR",
                    "type": "BEARISH",
                    "top": float(c3_high),
                    "bottom": float(c1_low),
                    "gap_size": float(c1_low - c3_high),
                    "created_by_bar": int(i + 1),
                    "created_time": pd.to_datetime(self.df["time"].iloc[i + 1]) if "time" in self.df.columns else None,
                    "mitigated": False,
                    "mitigated_at_bar": None,
                    "meta": {},
                }
                fvgs.append(fvg)

        # store for compatibility
        self.fvgs = fvgs
        return fvgs

    # -----------------------
    # Order Block candidate detection (do NOT mark tradable)
    # -----------------------
    def find_order_blocks(self, lookback: int = 200) -> List[Dict[str, Any]]:
        """
        Find OB candidates deterministically. Does NOT enforce has_fvg or BOS here.
        Returns list of OB objects with partial fields filled; validation fields set later.

        v2.1 augmentation:
          - Adds displacement filter requiring abs(body) >= mean_body_size over last 20 candles
          - Keeps structural conditions:
              * Bullish: curr_close > curr_open AND next_high > curr_high
              * Bearish: curr_close < curr_open AND next_low < curr_low
        """
        ob_candidates: List[Dict[str, Any]] = []
        if self.df is None or len(self.df) < 3:
            return ob_candidates

        n = len(self.df)
        start = max(0, n - lookback)
        df_slice = self.df.iloc[start:n].reset_index(drop=True)
        base_index_offset = start  # to map local index to global bar_index

        # Compute deterministic mean body size over last 20 candles (or available)
        body_sample = self.df["close"] - self.df["open"] if ("close" in self.df.columns and "open" in self.df.columns) else None
        if body_sample is not None and len(body_sample) > 0:
            last_bodies = np.abs(body_sample.tail(min(20, len(body_sample))).values)
            mean_body_size = float(np.mean(last_bodies)) if len(last_bodies) > 0 else 0.0
        else:
            mean_body_size = 0.0

        # deterministic rule: examine candle i and i+1
        for local_i in range(0, len(df_slice) - 1):
            try:
                curr = df_slice.iloc[local_i]
                nxt = df_slice.iloc[local_i + 1]
                curr_open = float(curr["open"])
                curr_close = float(curr["close"])
                curr_high = float(curr["high"])
                curr_low = float(curr["low"])
                next_high = float(nxt["high"])
                next_low = float(nxt["low"])
            except Exception:
                continue

            bar_index = base_index_offset + local_i
            body_high = max(curr_open, curr_close)
            body_low = min(curr_open, curr_close)
            body_size = abs(curr_close - curr_open)
            mean_threshold = float((curr_high + curr_low) / 2.0)

            # Apply tightened candidate generation with displacement filter:

            # Bearish candidate: curr_close < curr_open AND next_low < curr_low AND body_size >= mean_body_size
            if (curr_close < curr_open) and (next_low < curr_low) and (abs(curr_close - curr_open) >= mean_body_size):
                ob = {
                    "id": f"OB:{bar_index}:BEARISH",
                    "type": "BEARISH",
                    "bar_index": int(bar_index),
                    "time": pd.to_datetime(curr["time"]) if "time" in curr else None,
                    "price_top": float(curr_high),
                    "price_bottom": float(curr_low),
                    "body_high": float(body_high),
                    "body_low": float(body_low),
                    "body_size": float(body_size),
                    "mean_threshold": float(mean_threshold),
                    "is_external": False,
                    "timeframe": None,
                    # FVG/structure fields to be filled later
                    "has_fvg": False,
                    "fvg_id": None,
                    "caused_bos": False,
                    "bos_level": None,
                    "sweep_evidence": None,
                    "times_tested": 0,
                    "tested_and_held": False,
                    "block_class": WEAK,
                    # validation & permission (to be set)
                    "is_valid_basic": False,
                    "is_valid_poi": False,
                    "permission_to_trade": False,
                    "reason_code": REASON_INSUFFICIENT_DATA,
                    "meta": {},
                }
                ob_candidates.append(ob)

            # Bullish candidate: curr_close > curr_open AND next_high > curr_high AND body_size >= mean_body_size
            if (curr_close > curr_open) and (next_high > curr_high) and (abs(curr_close - curr_open) >= mean_body_size):
                ob = {
                    "id": f"OB:{bar_index}:BULLISH",
                    "type": "BULLISH",
                    "bar_index": int(bar_index),
                    "time": pd.to_datetime(curr["time"]) if "time" in curr else None,
                    "price_top": float(curr_high),
                    "price_bottom": float(curr_low),
                    "body_high": float(body_high),
                    "body_low": float(body_low),
                    "body_size": float(body_size),
                    "mean_threshold": float(mean_threshold),
                    "is_external": False,
                    "timeframe": None,
                    "has_fvg": False,
                    "fvg_id": None,
                    "caused_bos": False,
                    "bos_level": None,
                    "sweep_evidence": None,
                    "times_tested": 0,
                    "tested_and_held": False,
                    "block_class": WEAK,
                    "is_valid_basic": False,
                    "is_valid_poi": False,
                    "permission_to_trade": False,
                    "reason_code": REASON_INSUFFICIENT_DATA,
                    "meta": {},
                }
                ob_candidates.append(ob)

        # store legacy structure for compatibility
        self.order_blocks = {"bullish": [o for o in ob_candidates if o["type"] == "BULLISH"],
                             "bearish": [o for o in ob_candidates if o["type"] == "BEARISH"]}
        return ob_candidates

    # -----------------------
    # Associate FVGs with OBs deterministically
    # -----------------------
    def associate_fvgs_with_obs(self, obs: List[Dict[str, Any]], fvgs: List[Dict[str, Any]], overlap_tolerance: float = 0.0) -> List[Dict[str, Any]]:
        """
        For each OB, deterministically mark has_fvg and fvg_id if any FVG intersects OB horizontal footprint.
        Overlap uses closed interval intersection with exact numeric checks plus optional tolerance.
        """
        # Sort fvgs deterministically by created_by_bar ascending
        fvgs_sorted = sorted(fvgs, key=lambda x: x.get("created_by_bar", 0))
        for ob in obs:
            ob_bottom = ob["price_bottom"]
            ob_top = ob["price_top"]
            ob["has_fvg"] = False
            ob["fvg_id"] = None
            for fvg in fvgs_sorted:
                fvg_bottom = float(fvg["bottom"])
                fvg_top = float(fvg["top"])
                # check intersection inclusive with tolerance
                if (ob_top + overlap_tolerance) >= fvg_bottom and (ob_bottom - overlap_tolerance) <= fvg_top:
                    ob["has_fvg"] = True
                    ob["fvg_id"] = fvg["id"]
                    break
            # update reason_code preliminarily
            ob["reason_code"] = REASON_FVG_ASSOCIATED if ob["has_fvg"] else REASON_NO_FVG_ASSOCIATION
        return obs

    # -----------------------
    # Classification of OB type (deterministic, strict comparisons)
    # -----------------------
    def classify_block_type(self, block: Dict[str, Any], df: pd.DataFrame, lookback_window: int = 50) -> str:
        """
        Deterministic classifier using exact counts in future window.
        Uses exact comparisons only (no percentage tolerances).
        Returns one of BREAKER, MITIGATION, RECLAIMED, WEAK.
        """
        try:
            block_high = float(block["price_top"])
            block_low = float(block["price_bottom"])
            block_index = int(block["bar_index"])

            n = len(df)
            start_idx = block_index + 2
            end_idx = min(block_index + lookback_window, n - 1)
            if start_idx >= end_idx:
                return WEAK

            touches = 0
            holds = 0
            breaks = 0

            # iterate exact rows in deterministic order
            for idx in range(start_idx, end_idx + 1):
                try:
                    row_high = float(df["high"].iloc[idx])
                    row_low = float(df["low"].iloc[idx])
                except Exception:
                    continue

                if (row_low <= block_high) and (row_high >= block_low):
                    touches += 1
                    # exact holds/breaks:
                    if block["type"] == "BEARISH":
                        if row_high <= block_high:
                            holds += 1
                        else:
                            breaks += 1
                    else:  # BULLISH
                        if row_low >= block_low:
                            holds += 1
                        else:
                            breaks += 1

            # classification
            if touches >= 2 and holds >= 1 and breaks == 0:
                return BREAKER
            if breaks >= 1 and holds >= 1:
                return RECLAIMED
            if touches >= 1 and breaks == 0:
                return MITIGATION
            return WEAK
        except Exception:
            return WEAK

    # -----------------------
    # Basic validation per contract (has_fvg AND (BOS OR sweep evidence))
    # -----------------------
    def validate_ob_basic(self,
                          block: Dict[str, Any],
                          liquidity_detector,
                          market_structure_detector,
                          look_forward: int = 12,
                          bos_match_tolerance: float = 1e-8
                          ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Returns (is_valid_basic, reason_code, evidence)
        Evidence includes 'bos_level' (float|None), 'bos_match' (bool), 'sweep' (the wick_sweep result or None)

        Uses market_structure_detector.get_idm_state() per Day-3 contract.

        v2.1 FIX: Structural BOS validation:
          - For BULLISH OB: bos_level > block.price_top
          - For BEARISH OB: bos_level < block.price_bottom
        """
        evidence: Dict[str, Any] = {"bos_level": None, "bos_match": False, "sweep": None}
        # 1) require has_fvg
        if not bool(block.get("has_fvg", False)):
            return False, REASON_INVALID_NO_FVG, evidence

        # 2) check market structure BOS match using get_idm_state
        try:
            ms = market_structure_detector.get_idm_state()
        except Exception:
            ms = {}

        bos_level = None
        bos_match = False
        if isinstance(ms, dict):
            # Valid BOS evidence only if structure_confirmed True AND bos_level present
            if bool(ms.get("structure_confirmed", False)) and (ms.get("bos_level") is not None):
                bos_level = ms.get("bos_level")
                evidence["bos_level"] = bos_level
                # structural check (no equality/tolerance)
                try:
                    if block["type"] == "BULLISH":
                        if float(bos_level) > float(block["price_top"]):
                            bos_match = True
                    else:  # BEARISH
                        if float(bos_level) < float(block["price_bottom"]):
                            bos_match = True
                except Exception:
                    bos_match = False

        if bos_match:
            block["caused_bos"] = True
            block["bos_level"] = float(bos_level)
            evidence["bos_match"] = True
            block["sweep_evidence"] = None
            block["is_valid_basic"] = True
            block["is_valid_poi"] = True
            block["reason_code"] = REASON_VALID_OB
            return True, REASON_VALID_OB, evidence

        # 3) If no BOS, check wick sweep evidence
        sweep = None
        try:
            sweep = liquidity_detector.wick_sweep_detector(level_price=float(block["mean_threshold"]), start_bar=int(block["bar_index"]), look_forward=look_forward)
        except Exception:
            sweep = None

        evidence["sweep"] = sweep
        if sweep and sweep.get("is_sweep", False):
            # check direction matches expected: bullish OB expects 'down' sweep, bearish expects 'up'
            desired_direction = "down" if block["type"] == "BULLISH" else "up"
            if sweep.get("sweep_direction") == desired_direction:
                # valid sweep evidence
                block["sweep_evidence"] = sweep
                block["caused_bos"] = False
                block["bos_level"] = None
                block["is_valid_basic"] = True
                block["is_valid_poi"] = True
                block["reason_code"] = REASON_VALID_OB
                return True, REASON_VALID_OB, evidence
            else:
                # sweep occurred but wrong direction
                return False, REASON_SWEEP_WRONG_DIRECTION, evidence

        # No BOS and no valid sweep
        return False, REASON_INVALID_NO_BOS_NO_SWEEP, evidence

    # -----------------------
    # Permission gate (zone + optional kill-zone check)
    # -----------------------
    def evaluate_permission(self,
                            block: Dict[str, Any],
                            zones: Any,                       # expected to be a callable: price -> zone_name per Day-3 fix
                            session_detector: Optional[Any] = None,
                            require_kill_zone: bool = True
                            ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Returns (permission_to_trade, reason_code, details)
        Deterministic logic per contract.

        v2.1 FIX: If session_detector is None, DO NOT block; set kill_zone_checked False and kill_zone_result None and continue.
        """
        details: Dict[str, Any] = {"zone_name": None, "kill_zone_checked": False, "kill_zone_result": None}

        # Must be basic valid
        if not bool(block.get("is_valid_basic", False)):
            return False, block.get("reason_code", REASON_BLOCKED_BY_PERMISSION_GATE), details

        # Determine zone via callable only (contract)
        zone_name = None
        if callable(zones):
            try:
                zone_name = zones(block["mean_threshold"])
            except Exception:
                zone_name = None
        else:
            # zones not callable -> cannot perform required classification -> block
            return False, REASON_NOT_IN_CORRECT_ARRAY, details

        details["zone_name"] = zone_name

        # Requirement mapping
        required_zone = "DISCOUNT" if block["type"] == "BULLISH" else "PREMIUM"
        if zone_name != required_zone:
            return False, REASON_NOT_IN_CORRECT_ARRAY, details

        # Kill-zone check if required
        if require_kill_zone:
            # v2.1: if session_detector None -> do not fail; mark checked False
            if session_detector is None:
                details["kill_zone_checked"] = False
                details["kill_zone_result"] = None
                # continue; do not block
            else:
                details["kill_zone_checked"] = True
                if hasattr(session_detector, "is_in_kill_zone"):
                    try:
                        kz = bool(session_detector.is_in_kill_zone(block["time"]))
                    except Exception:
                        kz = False
                    details["kill_zone_result"] = kz
                    if not kz:
                        return False, REASON_OUTSIDE_KILL_ZONE, details
                else:
                    # session_detector provided but no method -> treat as not in kill zone
                    details["kill_zone_result"] = None
                    return False, REASON_OUTSIDE_KILL_ZONE, details

        # All passed
        return True, REASON_OK, details

    # -----------------------
    # Orchestration: finalize_pois
    # -----------------------
    def finalize_pois(self,
                      lookback: int,
                      liquidity_detector,
                      market_structure_detector,
                      zone_calculator,
                      session_detector: Optional[Any] = None,
                      look_forward: int = 12) -> List[Dict[str, Any]]:
        """
        High-level method assembling POIs and applying the contract rules.

        Returns list of finalized OB objects conforming exactly to the OB schema.
        """
        finalized_obs: List[Dict[str, Any]] = []

        # Basic data sufficiency check
        if self.df is None or len(self.df) < 3:
            return finalized_obs

        # Step 1: find FVGs and OB candidates
        fvgs = self.find_fvg(lookback=lookback)
        obs = self.find_order_blocks(lookback=lookback)

        # Step 2: associate fvgs with obs deterministically
        obs = self.associate_fvgs_with_obs(obs, fvgs, overlap_tolerance=0.0)

        # Step 3: classify each block deterministically (strict comparisons)
        for ob in obs:
            ob["block_class"] = self.classify_block_type(ob, self.df, lookback_window=50)

        # Prepare zones callable per Day-3 fix:
        zones_callable = None
        try:
            df_for_zones = self.df.tail(min(100, len(self.df)))
            swing_high = float(df_for_zones["high"].max()) if "high" in df_for_zones.columns else None
            swing_low = float(df_for_zones["low"].min()) if "low" in df_for_zones.columns else None
            if swing_high is not None and swing_low is not None and zone_calculator is not None:
                zones_raw = zone_calculator.calculate_zones(swing_high=swing_high, swing_low=swing_low, df=df_for_zones)
                # zones_callable must call zone_calculator.classify_price_zone(price, zones_raw)
                if zones_raw is not None:
                    def _zone_callable(price, _zones=zones_raw, _zc=zone_calculator):
                        try:
                            return _zc.classify_price_zone(price, _zones)
                        except Exception:
                            return None
                    zones_callable = _zone_callable
                else:
                    # zones_raw not available -> provide callable that returns None
                    zones_callable = lambda price: None
            else:
                # zone_calculator missing or insufficient data -> callable returns None
                zones_callable = lambda price: None
        except Exception:
            zones_callable = lambda price: None

        # Step 4: For each OB, perform basic validation and permission gating
        for ob in obs:
            # basic validation using liquidity & market structure detectors
            is_valid_basic, basic_reason, evidence = self.validate_ob_basic(
                ob, liquidity_detector, market_structure_detector, look_forward=look_forward, bos_match_tolerance=1e-8
            )
            ob["is_valid_basic"] = bool(is_valid_basic)
            ob["is_valid_poi"] = bool(is_valid_basic)
            # attach evidence
            ob["sweep_evidence"] = evidence.get("sweep")
            ob["bos_level"] = evidence.get("bos_level")
            if evidence.get("bos_match"):
                ob["caused_bos"] = True
                ob["bos_level"] = evidence.get("bos_level")

            ob["reason_code"] = basic_reason

            # now run permission gate (zones_callable per Day-3 requirement)
            perm, perm_reason, perm_details = self.evaluate_permission(
                ob, zones_callable, session_detector=session_detector, require_kill_zone=True
            )
            ob["permission_to_trade"] = bool(perm)
            # if permission_to_trade False and basic_reason was OK/VALID then set reason accordingly
            if not ob["permission_to_trade"]:
                # if basic invalid reason, keep it; else override with permission reason
                if ob["is_valid_basic"]:
                    ob["reason_code"] = perm_reason
            else:
                ob["reason_code"] = REASON_OK

            # compute times_tested & tested_and_held deterministically for metadata (strict comparisons)
            times_tested = 0
            tested_and_held = False
            try:
                idx_start = ob["bar_index"] + 2
                idx_end = min(ob["bar_index"] + 50, len(self.df) - 1)
                for idx in range(idx_start, idx_end + 1):
                    try:
                        row_high = float(self.df["high"].iloc[idx])
                        row_low = float(self.df["low"].iloc[idx])
                    except Exception:
                        continue
                    if (row_low <= ob["price_top"]) and (row_high >= ob["price_bottom"]):
                        times_tested += 1
                        # hold test: exact comparisons
                        if ob["type"] == "BEARISH":
                            if row_high <= ob["price_top"]:
                                tested_and_held = True
                        else:
                            if row_low >= ob["price_bottom"]:
                                tested_and_held = True
            except Exception:
                times_tested = 0
                tested_and_held = False

            ob["times_tested"] = int(times_tested)
            ob["tested_and_held"] = bool(tested_and_held)

            # final normalization: ensure required keys exist exactly per schema
            canonical_ob = {
                "id": ob.get("id"),
                "type": ob.get("type"),
                "bar_index": int(ob.get("bar_index")),
                "time": ob.get("time"),
                "price_top": float(ob.get("price_top")),
                "price_bottom": float(ob.get("price_bottom")),
                "body_high": float(ob.get("body_high")),
                "body_low": float(ob.get("body_low")),
                "body_size": float(ob.get("body_size")),
                "mean_threshold": float(ob.get("mean_threshold")),
                "is_external": bool(ob.get("is_external", False)),
                "timeframe": ob.get("timeframe"),
                "has_fvg": bool(ob.get("has_fvg", False)),
                "fvg_id": ob.get("fvg_id"),
                "caused_bos": bool(ob.get("caused_bos", False)),
                "bos_level": ob.get("bos_level"),
                "sweep_evidence": ob.get("sweep_evidence"),
                "times_tested": int(ob.get("times_tested", 0)),
                "tested_and_held": bool(ob.get("tested_and_held", False)),
                "block_class": ob.get("block_class", WEAK),
                "is_valid_basic": bool(ob.get("is_valid_basic", False)),
                "is_valid_poi": bool(ob.get("is_valid_poi", False)),
                "permission_to_trade": bool(ob.get("permission_to_trade", False)),
                "reason_code": ob.get("reason_code", REASON_INSUFFICIENT_DATA),
                "meta": ob.get("meta", {}),
            }

            finalized_obs.append(canonical_ob)

        # store finalized order_blocks in legacy container grouped by polarity
        self.order_blocks = {
            "bullish": [o for o in finalized_obs if o["type"] == "BULLISH"],
            "bearish": [o for o in finalized_obs if o["type"] == "BEARISH"],
        }

        return finalized_obs