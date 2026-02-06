from enum import Enum, auto


class NarrativeState(Enum):
    IDLE = auto()
    TRADING_RANGE_DEFINED = auto()
    EXTERNAL_LIQUIDITY_SWEPT = auto()
    HTF_POI_REACHED = auto()
    LTF_STRUCTURE_SHIFT = auto()
    LTF_POI_MITIGATED = auto()
    ENTRY_ALLOWED = auto()


class NarrativeAnalyzer:
    def __init__(self):
        self.state = NarrativeState.IDLE

    def reset(self, reason=""):
        self.state = NarrativeState.IDLE
        return {
            "state": self.state.name,
            "reset": True,
            "reason": reason
        }

    def update(self, market_state: dict):
        """
        Update narrative state based on market events.
        Allows multi-step advancement in a single update.
        """

        # --- GLOBAL INVALIDATION ---
        if market_state.get("htf_ob_invalidated"):
            return self.reset("HTF POI invalidated")

        if market_state.get("daily_structure_flipped"):
            return self.reset("HTF bias reset")

        # --- MULTI-STEP STATE ADVANCEMENT ---
        state_changed = True

        while state_changed:
            state_changed = False

            if self.state == NarrativeState.IDLE:
                if market_state.get("trading_range_defined"):
                    self.state = NarrativeState.TRADING_RANGE_DEFINED
                    state_changed = True

            elif self.state == NarrativeState.TRADING_RANGE_DEFINED:
                if market_state.get("external_liquidity_swept"):
                    self.state = NarrativeState.EXTERNAL_LIQUIDITY_SWEPT
                    state_changed = True

            elif self.state == NarrativeState.EXTERNAL_LIQUIDITY_SWEPT:
                if market_state.get("htf_poi_reached"):
                    self.state = NarrativeState.HTF_POI_REACHED
                    state_changed = True

            elif self.state == NarrativeState.HTF_POI_REACHED:
                if market_state.get("ltf_structure_shift"):
                    self.state = NarrativeState.LTF_STRUCTURE_SHIFT
                    state_changed = True

            elif self.state == NarrativeState.LTF_STRUCTURE_SHIFT:
                if market_state.get("ltf_poi_mitigated"):
                    self.state = NarrativeState.LTF_POI_MITIGATED
                    state_changed = True

            elif self.state == NarrativeState.LTF_POI_MITIGATED:
                if market_state.get("killzone_active"):
                    self.state = NarrativeState.ENTRY_ALLOWED
                    state_changed = True

        return self.snapshot()

    def snapshot(self):
        return {
            "state": self.state.name,
            "entry_allowed": self.state == NarrativeState.ENTRY_ALLOWED
        }
