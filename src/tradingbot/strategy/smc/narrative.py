from enum import Enum, auto
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# BAR BUDGET PER STATE — how long each state may persist before timeout-reset
# ─────────────────────────────────────────────────────────────────────────────
MAX_BARS_PER_STATE = {
    "IDLE":                      None,  # never times out
    "TRADING_RANGE_DEFINED":       50,  # ~8 days on 4H
    "EXTERNAL_LIQUIDITY_SWEPT":    20,  # 20 bars to reach HTF POI
    "HTF_POI_REACHED":             15,  # 15 bars waiting for displacement
    "LTF_DISPLACEMENT_CONFIRMED":  10,  # 10 bars for LTF CHoCH
    "LTF_STRUCTURE_SHIFT":          8,  # 8 bars for LTF POI mitigation
    "LTF_POI_MITIGATED":            6,  # 6 bars waiting for killzone
    "ENTRY_ALLOWED":                4,  # 4 bars max to trigger entry
}


# ─────────────────────────────────────────────────────────────────────────────
# STATE MACHINE ENUM
# ─────────────────────────────────────────────────────────────────────────────
class NarrativeState(Enum):
    IDLE                      = auto()
    TRADING_RANGE_DEFINED     = auto()
    EXTERNAL_LIQUIDITY_SWEPT  = auto()
    HTF_POI_REACHED           = auto()
    LTF_DISPLACEMENT_CONFIRMED = auto()
    LTF_STRUCTURE_SHIFT       = auto()
    LTF_POI_MITIGATED         = auto()
    ENTRY_ALLOWED             = auto()


# ─────────────────────────────────────────────────────────────────────────────
# NARRATIVE ANALYZER
# ─────────────────────────────────────────────────────────────────────────────
class NarrativeAnalyzer:
    """
    Dual-layer narrative engine:
      Layer 1 — state machine (update / snapshot) — original Engine 4 logic.
      Layer 2 — 3Bs Auto-Narrative (build_narrative) — Engine 5 / Guardeer L10.
    """

    def __init__(self):
        self.state = NarrativeState.IDLE
        self._bars_in_state = 0
        self._last_state    = NarrativeState.IDLE
        self._total_bars    = 0
        self._timeout_log   = []

        # Shared market-state dict written to by build_narrative
        # and readable by the state machine or any upstream consumer.
        self.market_state: dict = {
            "liquidity_swept":    False,
            "poi_tapped":         False,
            "structure_confirmed": False,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API — Layer 1 (State Machine)
    # ─────────────────────────────────────────────────────────────────────────

    def reset(self, reason: str = "") -> dict:
        """Hard reset to IDLE. Clears all counters."""
        prev_state  = self.state.name
        self.state  = NarrativeState.IDLE
        self._bars_in_state = 0
        self._last_state    = NarrativeState.IDLE
        return {
            "state":          self.state.name,
            "reset":          True,
            "reason":         reason,
            "previous_state": prev_state,
        }

    def update(self, market_state: dict) -> dict:
        """
        Update narrative state based on market events.
        Allows multi-step advancement in a single update.

        Args:
            market_state: dict of boolean signal flags from upstream engines.
                Expected keys (all optional / default False):
                    trading_range_defined, external_liquidity_swept,
                    htf_poi_reached, displacement_detected,
                    ltf_structure_shift, ltf_poi_mitigated,
                    killzone_active, htf_ob_invalidated,
                    daily_structure_flipped

        Returns:
            snapshot dict — see snapshot() for shape.
        """
        self._total_bars += 1

        # STEP 1: Track bar count in current state
        if self.state != self._last_state:
            self._bars_in_state = 0
            self._last_state    = self.state
        else:
            self._bars_in_state += 1

        # STEP 2: Global invalidation (highest priority)
        if market_state.get("htf_ob_invalidated"):
            return self.reset("HTF POI invalidated")
        if market_state.get("daily_structure_flipped"):
            return self.reset("HTF bias reset")

        # STEP 3: Timeout check
        timeout_result = self._check_timeout()
        if timeout_result:
            return timeout_result

        # STEP 4: Rollback logic
        if self.state == NarrativeState.LTF_STRUCTURE_SHIFT:
            if not market_state.get("ltf_structure_shift"):
                self.state = NarrativeState.LTF_DISPLACEMENT_CONFIRMED
                self._bars_in_state = 0
                self._last_state    = self.state

        if self.state == NarrativeState.LTF_DISPLACEMENT_CONFIRMED:
            if not market_state.get("displacement_detected"):
                self.state = NarrativeState.HTF_POI_REACHED
                self._bars_in_state = 0
                self._last_state    = self.state

        if self.state == NarrativeState.LTF_POI_MITIGATED:
            if not market_state.get("ltf_poi_mitigated"):
                self.state = NarrativeState.LTF_STRUCTURE_SHIFT
                self._bars_in_state = 0
                self._last_state    = self.state

        if self.state == NarrativeState.ENTRY_ALLOWED:
            if not market_state.get("killzone_active"):
                self.state = NarrativeState.LTF_POI_MITIGATED
                self._bars_in_state = 0
                self._last_state    = self.state

        # STEP 5: Multi-step forward advancement
        state_changed = True
        while state_changed:
            state_changed = False

            if self.state == NarrativeState.IDLE:
                if market_state.get("trading_range_defined"):
                    self.state    = NarrativeState.TRADING_RANGE_DEFINED
                    state_changed = True

            elif self.state == NarrativeState.TRADING_RANGE_DEFINED:
                if market_state.get("external_liquidity_swept"):
                    self.state    = NarrativeState.EXTERNAL_LIQUIDITY_SWEPT
                    state_changed = True

            elif self.state == NarrativeState.EXTERNAL_LIQUIDITY_SWEPT:
                if market_state.get("htf_poi_reached"):
                    self.state    = NarrativeState.HTF_POI_REACHED
                    state_changed = True

            elif self.state == NarrativeState.HTF_POI_REACHED:
                if market_state.get("displacement_detected"):
                    self.state    = NarrativeState.LTF_DISPLACEMENT_CONFIRMED
                    state_changed = True

            elif self.state == NarrativeState.LTF_DISPLACEMENT_CONFIRMED:
                if market_state.get("ltf_structure_shift"):
                    self.state    = NarrativeState.LTF_STRUCTURE_SHIFT
                    state_changed = True

            elif self.state == NarrativeState.LTF_STRUCTURE_SHIFT:
                if market_state.get("ltf_poi_mitigated"):
                    self.state    = NarrativeState.LTF_POI_MITIGATED
                    state_changed = True

            elif self.state == NarrativeState.LTF_POI_MITIGATED:
                if market_state.get("killzone_active"):
                    self.state    = NarrativeState.ENTRY_ALLOWED
                    state_changed = True

            if state_changed:
                self._bars_in_state = 0
                self._last_state    = self.state

        return self.snapshot()

    def snapshot(self) -> dict:
        """Returns current state snapshot for upstream consumers and dashboard."""
        budget         = MAX_BARS_PER_STATE.get(self.state.name)
        bars_remaining = (budget - self._bars_in_state) if budget else None
        return {
            "state":          self.state.name,
            "entry_allowed":  self.state == NarrativeState.ENTRY_ALLOWED,
            "bars_in_state":  self._bars_in_state,
            "timeout_budget": budget,
            "bars_remaining": bars_remaining,
            "total_bars":     self._total_bars,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API — Layer 2 (3Bs Auto-Narrative — Engine 5 / Guardeer L10)
    # ─────────────────────────────────────────────────────────────────────────

    def build_narrative(
        self,
        liquidity_engine,
        poi_engine,
        structure_engine,
        current_price: float,
    ) -> dict:
        """
        Guardeer Lecture 10 — Answer the 3 Bs automatically.

        B1: What has price DONE?      (past   — liquidity sweep)
        B2: What IS the condition?    (present — POI / OB tap)
        B3: What is PREDICTED next?   (future  — MSS / CHoCH)

        Args:
            liquidity_engine:  must expose  get_recently_swept_pools() -> list[dict]
                               each pool dict: {"type": str, "level": float}
            poi_engine:        must expose  get_active_pois()           -> list[dict]
                               each poi  dict: {"type": str, "level": float, "tapped": bool}
            structure_engine:  must expose  get_latest_mss()            -> dict | None
                               mss dict: {"direction": str}
            current_price:     latest market price (float)

        Returns:
            {
                "B1":              str,
                "B2":              str,
                "B3":              str,
                "trade_permission": bool,
                "current_price":   float,
            }

        Populates self.market_state so the Layer-1 state machine stays in sync:
            liquidity_swept    → bool
            poi_tapped         → bool
            structure_confirmed → bool
        """
        narrative: dict = {
            "B1":              None,
            "B2":              None,
            "B3":              "WAITING_FOR_MSS: LTF CHoCH not yet confirmed",
            "trade_permission": False,
            "current_price":   current_price,
        }

        # Reset 3B flags before each evaluation
        self.market_state["liquidity_swept"]     = False
        self.market_state["poi_tapped"]          = False
        self.market_state["structure_confirmed"] = False

        # ── B1: Has price grabbed a liquidity pool? ───────────────────────────
        swept_pools = liquidity_engine.get_recently_swept_pools()
        if swept_pools:
            last_pool = swept_pools[-1]
            narrative["B1"] = (
                f"LIQUIDITY_SWEPT: {last_pool['type']} at {last_pool['level']:.2f}"
            )
            self.market_state["liquidity_swept"] = True
        else:
            narrative["B1"] = "ACCUMULATING: No liquidity swept yet"
            # Cannot advance without B1 — return early
            return narrative

        # ── B2: Has price rebalanced an FVG or tapped an OB? ─────────────────
        active_pois = poi_engine.get_active_pois()
        tapped_ob   = next((p for p in active_pois if p.get("tapped")), None)
        if tapped_ob:
            narrative["B2"] = (
                f"POI_TAPPED: {tapped_ob['type']} at {tapped_ob['level']:.2f}"
            )
            self.market_state["poi_tapped"] = True
        else:
            narrative["B2"] = "RETRACING: Price moving toward POI"
            # Cannot advance without B2 — return early
            return narrative

        # ── B3: Has an MSS / CHoCH confirmed direction? ───────────────────────
        mss = structure_engine.get_latest_mss()
        if mss:
            narrative["B3"]              = (
                f"MSS_CONFIRMED: {mss['direction']} — Entry permitted"
            )
            self.market_state["structure_confirmed"] = True
            narrative["trade_permission"]            = True

        return narrative

    # ─────────────────────────────────────────────────────────────────────────
    # PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _check_timeout(self) -> Optional[dict]:
        """
        Returns a reset dict if the current state exceeded its bar budget.
        Returns None if still within budget.
        """
        budget = MAX_BARS_PER_STATE.get(self.state.name)
        if budget is None:
            return None

        if self._bars_in_state >= budget:
            reason = (
                f"TIMEOUT: {self.state.name} exceeded {budget}-bar limit "
                f"({self._bars_in_state} bars elapsed)"
            )
            self._timeout_log.append({
                "bar":    self._total_bars,
                "state":  self.state.name,
                "bars":   self._bars_in_state,
                "budget": budget,
            })
            if len(self._timeout_log) > 10:
                self._timeout_log.pop(0)
            return self.reset(reason)

        return None

    @property
    def timeout_log(self) -> list:
        """
        Returns the last 10 timeout events — useful for debugging.
        If TRADING_RANGE_DEFINED times out 20x in a row, your upstream
        liquidity sweep detection is broken.
        """
        return list(self._timeout_log)
