"""
Challenge/Funded Account Policy Enforcer

Implements strict rules for funded account trading:
- Daily loss limits
- Maximum drawdown limits
- Max trades per day
- Consecutive loss limits
- Minimum trade gap (prevents scalping)
- Risk per trade caps

These rules are designed to prevent account blowups and comply with
funded trading challenge requirements.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS - Default Challenge Rules
# ============================================================================

DEFAULT_DAILY_LOSS_LIMIT_PCT = 1.0  # Lose more than 1% in a day = lockdown
DEFAULT_MAX_DRAWDOWN_PCT = 3.5  # Max peak-to-current drawdown = lockdown
DEFAULT_MAX_TRADES_PER_DAY = 2  # Only 2 quality trades per day
DEFAULT_MAX_CONSECUTIVE_LOSSES = 2  # Max 2 losses in a row
DEFAULT_MIN_TRADE_GAP_MINUTES = 90  # Wait 90 min between trades
DEFAULT_RISK_PER_TRADE_PCT = 0.25  # Risk 0.25% of balance per trade


@dataclass
class ChallengePolicy:
    """
    Policy enforcement for funded account trading.
    
    Tracks:
    - Daily PnL and daily loss limits
    - Peak-to-current drawdown
    - Consecutive losing trades
    - Trades per day count
    - Last trade timestamp
    
    Methods return (allowed: bool, reason: str | None) tuples where:
    - (True, None) = trade allowed
    - (False, reason) = trade blocked with explanation
    """

    # =========================================================================
    # CONFIGURATION (all configurable)
    # =========================================================================

    daily_loss_limit_pct: float = DEFAULT_DAILY_LOSS_LIMIT_PCT
    max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN_PCT
    max_trades_per_day: int = DEFAULT_MAX_TRADES_PER_DAY
    max_consecutive_losses: int = DEFAULT_MAX_CONSECUTIVE_LOSSES
    min_trade_gap_minutes: int = DEFAULT_MIN_TRADE_GAP_MINUTES
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT
    starting_balance: float = 100000.0

    # =========================================================================
    # RUNTIME STATE (updated after each trade)
    # =========================================================================

    consecutive_losses: int = field(default=0)
    trades_today: int = field(default=0)
    daily_pnl: float = field(default=0.0)
    daily_pnl_pct: float = field(default=0.0)
    last_trade_time: Optional[datetime] = field(default=None)

    def __post_init__(self):
        """Initialize logger and internal peak balance tracker."""
        # Internal high-water-mark — owned by the policy, not the caller
        self.peak_balance: float = self.starting_balance

        logger.info(
            f"ChallengePolicy initialized:\n"
            f"  Daily Loss Limit: {self.daily_loss_limit_pct}%\n"
            f"  Max Drawdown: {self.max_drawdown_pct}%\n"
            f"  Max Trades/Day: {self.max_trades_per_day}\n"
            f"  Max Consecutive Losses: {self.max_consecutive_losses}\n"
            f"  Min Gap: {self.min_trade_gap_minutes} min\n"
            f"  Risk/Trade: {self.risk_per_trade_pct}%\n"
            f"  Peak Balance: ${self.peak_balance:.2f}"
        )

    # =========================================================================
    # PRIMARY METHOD: Can Trade?
    # =========================================================================

    def check_can_trade(
        self,
        daily_pnl_pct: float,
        peak_balance: float,
        current_balance: float,
        trades_today: int,
        consecutive_losses: int,
        last_trade_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a trade is allowed under challenge rules.
        
        Checks in order:
        1. Daily loss limit
        2. Maximum drawdown
        3. Max trades per day
        4. Consecutive losses
        5. Minimum trade gap
        
        Returns immediately on first violation.
        
        Args:
            daily_pnl_pct: Today's profit/loss as % of account (can be negative)
            peak_balance: Highest balance reached (for drawdown calc)
            current_balance: Current account balance
            trades_today: Number of trades executed so far today
            consecutive_losses: Number of consecutive losing trades
            last_trade_time: Timestamp of last executed trade (optional)
        
        Returns:
            (True, None) if trade is allowed
            (False, reason_string) if trade is blocked
        
        Example:
            allowed, reason = policy.check_can_trade(
                daily_pnl_pct=-0.8,
                peak_balance=100000,
                current_balance=99800,
                trades_today=1,
                consecutive_losses=1,
                last_trade_time=datetime.now()
            )
            if not allowed:
                print(f"Trade blocked: {reason}")
        """
        try:
            # =========================================================================
            # RULE 1: Daily Loss Limit
            # =========================================================================
            if daily_pnl_pct < -self.daily_loss_limit_pct:
                reason = (
                    f"DAILY_LOSS_LIMIT breached: {daily_pnl_pct:.2f}% loss exceeds "
                    f"limit of {self.daily_loss_limit_pct}%"
                )
                logger.warning(f"Trade BLOCKED: {reason}")
                return (False, reason)

            # =========================================================================
            # RULE 2: Maximum Drawdown
            # =========================================================================
            if peak_balance > 0 and current_balance > 0:
                drawdown_pct = round(((peak_balance - current_balance) / peak_balance) * 100, 8)
                if drawdown_pct > self.max_drawdown_pct:
                    reason = (
                        f"MAX_DRAWDOWN breached: {drawdown_pct:.2f}% drawdown "
                        f"exceeds limit of {self.max_drawdown_pct}%"
                    )
                    logger.warning(f"Trade BLOCKED: {reason}")
                    return (False, reason)

            # =========================================================================
            # RULE 3: Consecutive Losses (checked before max trades per day)
            # =========================================================================
            if consecutive_losses >= self.max_consecutive_losses:
                reason = (
                    f"MAX_CONSECUTIVE_LOSSES reached: {consecutive_losses} losses in a row, "
                    f"limit is {self.max_consecutive_losses}"
                )
                logger.warning(f"Trade BLOCKED: {reason}")
                return (False, reason)

            # =========================================================================
            # RULE 4: Max Trades Per Day
            # =========================================================================
            if trades_today >= self.max_trades_per_day:
                reason = (
                    f"MAX_TRADES_PER_DAY reached: {trades_today} trades executed, "
                    f"limit is {self.max_trades_per_day}"
                )
                logger.warning(f"Trade BLOCKED: {reason}")
                return (False, reason)

            # =========================================================================
            # RULE 5: Minimum Trade Gap
            # =========================================================================
            if last_trade_time is not None:
                time_since_last_trade = datetime.now() - last_trade_time
                minutes_elapsed = time_since_last_trade.total_seconds() / 60

                if minutes_elapsed < self.min_trade_gap_minutes:
                    reason = (
                        f"MIN_TRADE_GAP not met: {minutes_elapsed:.1f} minutes since last trade, "
                        f"minimum is {self.min_trade_gap_minutes} minutes"
                    )
                    logger.warning(f"Trade BLOCKED: {reason}")
                    return (False, reason)

            # =========================================================================
            # ALL RULES PASSED
            # =========================================================================
            logger.info(
                f"Trade ALLOWED | Daily PnL: {daily_pnl_pct:.2f}% | "
                f"Trades today: {trades_today}/{self.max_trades_per_day} | "
                f"Consecutive losses: {consecutive_losses}/{self.max_consecutive_losses}"
            )
            return (True, None)

        except Exception as e:
            logger.exception(f"Error in check_can_trade: {str(e)}")
            # Fail safe: block trade on error
            return (False, f"Policy check error: {str(e)}")

    # =========================================================================
    # HELPER: Get Lockdown Reason
    # =========================================================================

    def get_lockdown_reason(
        self,
        daily_pnl_pct: float,
        peak_balance: float,
        current_balance: float,
        consecutive_losses: int,
    ) -> Optional[str]:
        """
        Get human-readable lockdown reason without checking trade gap.
        
        Used for logging why account is in lockdown (separate from
        trade-by-trade blocking).
        
        Returns:
            Lockdown reason string, or None if not in lockdown
        
        Example:
            reason = policy.get_lockdown_reason(
                daily_pnl_pct=-1.2,
                peak_balance=100000,
                current_balance=98800,
                consecutive_losses=3
            )
            if reason:
                print(f"Account locked: {reason}")
        """
        try:
            # Check daily loss
            if daily_pnl_pct < -self.daily_loss_limit_pct:
                return (
                    f"Daily loss limit breached ({daily_pnl_pct:.2f}% < "
                    f"-{self.daily_loss_limit_pct}%)"
                )

            # Check drawdown
            if peak_balance > 0 and current_balance > 0:
                drawdown_pct = ((peak_balance - current_balance) / peak_balance) * 100
                if drawdown_pct > self.max_drawdown_pct:
                    return (
                        f"Max drawdown exceeded ({drawdown_pct:.2f}% > "
                        f"{self.max_drawdown_pct}%)"
                    )

            # Check consecutive losses
            if consecutive_losses >= self.max_consecutive_losses:
                return (
                    f"Max consecutive losses reached ({consecutive_losses} >= "
                    f"{self.max_consecutive_losses})"
                )

            return None

        except Exception as e:
            logger.exception(f"Error in get_lockdown_reason: {str(e)}")
            return f"Error checking lockdown: {str(e)}"

    # =========================================================================
    # TRADE LOGGING
    # =========================================================================

    def log_trade_result(
        self,
        was_win: bool,
        pnl: float,
        current_balance: float = 0.0,
    ) -> None:
        """
        Update internal state after a trade closes.

        Call this method every time a trade completes (win or loss).
        It updates:
        - consecutive_losses counter
        - daily_pnl total
        - daily_pnl_pct
        - peak_balance high-water-mark (if current_balance provided)

        Args:
            was_win: True if trade was profitable, False if loss
            pnl: Profit/loss amount in account currency (can be negative)
            current_balance: Current account balance after trade closes.
                            Used to update internal peak_balance tracker.
                            Pass this every time — omitting it means
                            drawdown protection uses a stale peak.

        Example:
            policy.log_trade_result(was_win=False, pnl=-50.0, current_balance=99950.0)
            policy.log_trade_result(was_win=True, pnl=120.0, current_balance=100070.0)
        """
        try:
            self.daily_pnl += pnl

            if self.starting_balance > 0:
                self.daily_pnl_pct = (self.daily_pnl / self.starting_balance) * 100

            # Update internal high-water-mark — this is the fix.
            # peak_balance must NEVER decrease; only move up on new equity highs.
            if current_balance > 0:
                self.peak_balance = max(self.peak_balance, current_balance)

            if was_win:
                self.consecutive_losses = 0
                logger.info(
                    f"Trade WIN: +${pnl:.2f} | Consecutive losses reset to 0 | "
                    f"Peak balance: ${self.peak_balance:.2f}"
                )
            else:
                self.consecutive_losses += 1
                logger.info(
                    f"Trade LOSS: -${abs(pnl):.2f} | "
                    f"Consecutive losses: {self.consecutive_losses} | "
                    f"Peak balance: ${self.peak_balance:.2f}"
                )

            logger.debug(f"Daily PnL total: ${self.daily_pnl:.2f}")

        except Exception as e:
            logger.exception(f"Error in log_trade_result: {str(e)}")

    # =========================================================================
    # RESET
    # =========================================================================

    def reset_daily_state(self) -> None:
        """
        Reset daily counters at start of new trading day.
        
        Call this at market open or start of new trading session.
        Resets:
        - trades_today to 0
        - daily_pnl to 0
        - daily_pnl_pct to 0
        - consecutive_losses is NOT reset (can carry over)
        
        Note: consecutive_losses persists across days to track overall
        trading discipline. It should be reset separately if needed.
        
        Example:
            # At 00:00 UTC or start of new trading day
            policy.reset_daily_state()
        """
        try:
            logger.info(
                f"Daily state reset:\n"
                f"  trades_today: {self.trades_today} → 0\n"
                f"  daily_pnl: ${self.daily_pnl:.2f} → $0.00\n"
                f"  daily_pnl_pct: {self.daily_pnl_pct:.2f}% → 0.00%"
            )

            self.trades_today = 0
            self.daily_pnl = 0.0
            self.daily_pnl_pct = 0.0
            # Note: consecutive_losses is intentionally NOT reset

        except Exception as e:
            logger.exception(f"Error in reset_daily_state: {str(e)}")
