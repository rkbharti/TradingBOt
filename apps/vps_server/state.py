from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BotState:
    trading_enabled: bool = True
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def set_trading(self, enabled: bool) -> None:
        self.trading_enabled = enabled
        self.updated_at = datetime.now(timezone.utc).isoformat()


bot_state = BotState()