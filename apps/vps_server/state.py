from dataclasses import dataclass, field
from datetime import datetime, timezone

from apps.vps_server.state_persistence import (
    save_state,
    load_state,
)

@dataclass
class BotState:
    # Core Controls
    trading_enabled: bool = True
    current_timeframe: str = "M15"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    # Financial Telemetry
    balance: float = 0.0
    equity: float = 0.0
    pnl_today: float = 0.0
    pnl_total: float = 0.0
    
    # Smart Money Concepts (SMC) Market States
    market_structure: str = "--"
    session: str = "ALL"
    d1_bias: str = "--"
    h4_bias: str = "--"

    def set_trading(self, enabled: bool) -> None:
        self.trading_enabled = enabled
        self.updated_at = datetime.now(timezone.utc).isoformat()

        # Persist updated state
        save_state(self)

    def update_metrics(self, data: dict) -> None:
        """Helper to quickly parse incoming telemetry payloads from the MT5 bot."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

        self.updated_at = datetime.now(timezone.utc).isoformat()

        # Persist telemetry updates
        save_state(self)

# Create runtime state
bot_state = BotState()

# Restore persisted state if available
saved_data = load_state()

if saved_data:
    for key, value in saved_data.items():
        if hasattr(bot_state, key):
            setattr(bot_state, key, value)

    print("✅ Bot state restored from disk")
else:
    print("ℹ️ No saved bot state found; using defaults")