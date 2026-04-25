"""
Comprehensive Edge Case Tests for Risk Engine + Execution Engine
Tests position_sizing, challenge_policy, and order_executor with mocked MT5 data.

Edge cases covered:
- Zero/negative balance scenarios
- Stop-loss too tight (insufficient pips)
- Risk-reward ratios below minimum
- Daily loss limits exceeded
- Drawdown limits exceeded
- Consecutive loss limits
- Trade gap violations
- Invalid structural zones
- MT5 connection failures (dry-run fallback)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# Lazy import MetaTrader5 (only needed for live mode tests)
try:
    import MetaTrader5 as mt5
except ImportError:
    # Create fallback mock for testing
    class mt5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 1
        ORDER_TIME_GTC = 0
        ORDER_FILLING_IOC = 1

from src.tradingbot.risk.position_sizing import (
    PositionSizer,
    LotCalculation,
    RiskRewardValidation,
)
from src.tradingbot.risk.challenge_policy import ChallengePolicy
from src.tradingbot.execution.order_executor import (
    OrderExecutor,
    ExecutionResult,
    SignalResult,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_mt5_client():
    """Create fully mocked MT5 client."""
    mock = Mock()
    mock.symbol = "XAUUSD"
    mock.send_order = Mock(return_value=12345)
    mock.positions_get = Mock(return_value=[])
    return mock


@pytest.fixture
def position_sizer():
    """Create PositionSizer instance with default params."""
    return PositionSizer(
        min_lot=0.01,
        max_lot=0.50,
        contract_size=100,
        pip_value=0.1,
        min_rr=2.5,
    )


@pytest.fixture
def challenge_policy():
    """Create ChallengePolicy with test params."""
    return ChallengePolicy(
        daily_loss_limit_pct=1.0,
        max_drawdown_pct=3.5,
        max_trades_per_day=2,
        max_consecutive_losses=2,
        min_trade_gap_minutes=90,
        risk_per_trade_pct=0.25,
    )


@pytest.fixture
def order_executor(mock_mt5_client, challenge_policy, position_sizer):
    """Create OrderExecutor with mocked dependencies."""
    return OrderExecutor(
        mt5_client=mock_mt5_client,
        challenge_policy=challenge_policy,
        position_sizer=position_sizer,
        dry_run=True,
    )


@pytest.fixture
def valid_signal():
    """Create a valid trading signal."""
    return SignalResult(
        action="BUY",
        direction="BULLISH",
        entry_price=2700.0,
        sl_price=2695.0,
        tp_price=2713.0,
        poi_zone={"top": 2705.0, "bottom": 2700.0, "mt": 2702.5},
        liquidity_levels={
            "pdh": 2705.0,
            "pdl": 2695.0,
            "weekly_high": 2708.0,
            "weekly_low": 2690.0,
        },
    )


# ============================================================================
# TEST SUITE 1: POSITION SIZING - EDGE CASES
# ============================================================================


class TestPositionSizingEdgeCases:
    """Test PositionSizer with edge case scenarios."""

    # ========================================================================
    # Zero / Negative Balance Tests
    # ========================================================================

    def test_calculate_lot_zero_balance(self, position_sizer):
        """Test lot calculation with zero balance → should raise ValueError."""
        with pytest.raises(ValueError, match="Balance must be > 0"):
            position_sizer.calculate_lot(
                balance=0,
                risk_pct=0.25,
                entry_price=2700.0,
                sl_price=2695.0,
            )

    def test_calculate_lot_negative_balance(self, position_sizer):
        """Test lot calculation with negative balance → should raise ValueError."""
        with pytest.raises(ValueError, match="Balance must be > 0"):
            position_sizer.calculate_lot(
                balance=-10000,
                risk_pct=0.25,
                entry_price=2700.0,
                sl_price=2695.0,
            )

    def test_calculate_lot_zero_risk_pct(self, position_sizer):
        """Test lot calculation with zero risk % → should raise ValueError."""
        with pytest.raises(ValueError, match="Risk percentage must be 0-100"):
            position_sizer.calculate_lot(
                balance=100000,
                risk_pct=0,
                entry_price=2700.0,
                sl_price=2695.0,
            )

    def test_calculate_lot_over_100_pct_risk(self, position_sizer):
        """Test lot calculation with >100% risk → should raise ValueError."""
        with pytest.raises(ValueError, match="Risk percentage must be 0-100"):
            position_sizer.calculate_lot(
                balance=100000,
                risk_pct=150,
                entry_price=2700.0,
                sl_price=2695.0,
            )

    # ========================================================================
    # Stop-Loss Too Tight Tests
    # ========================================================================

    def test_calculate_lot_entry_equals_sl(self, position_sizer):
        """Test lot calculation when entry = SL → should raise ValueError."""
        with pytest.raises(ValueError, match="Entry price cannot equal stop-loss"):
            position_sizer.calculate_lot(
                balance=100000,
                risk_pct=0.25,
                entry_price=2700.0,
                sl_price=2700.0,  # Same as entry!
            )

    def test_calculate_lot_sl_too_tight_one_pip(self, position_sizer):
        """Test with SL only 1 pip away (very tight stop)."""
        result = position_sizer.calculate_lot(
            balance=100000,
            risk_pct=0.25,
            entry_price=2700.0,
            sl_price=2699.0,  # Only 1 pip away
        )

        # SL distance: 1 pip
        # Risk: 100000 * 0.0025 = 250
        # Lot = 250 / (1 * 100) = 2.5 → clamped to MAX_LOT (0.5)
        assert result.lot_size == 0.5  # Should clamp to max
        assert "MAX_LOT" in result.reason
        print(f"✓ Tight SL (1 pip): Clamped to {result.lot_size} lot")

    def test_calculate_lot_zero_sl_distance(self, position_sizer):
        """Test when entry = SL (zero distance) → should raise ValueError."""
        with pytest.raises(ValueError, match="Entry price cannot equal stop-loss"):
            position_sizer.calculate_lot(
                balance=100000,
                risk_pct=0.5,
                entry_price=2700.0,
                sl_price=2700.0,
            )

    # ========================================================================
    # Risk-Reward Ratio Tests
    # ========================================================================

    def test_validate_rr_below_minimum(self, position_sizer):
        """Test RR validation when ratio < minimum."""
        result = position_sizer.validate_rr(
            entry_price=2700.0,
            sl_price=2695.0,
            tp_price=2708.0,  # Only 8 pips profit vs 5 pips risk = 1.6x
            min_rr=2.5,
        )

        assert result.is_valid is False
        assert result.actual_rr == pytest.approx(1.6, rel=0.01)
        assert result.min_rr_required == 2.5
        print(f"✗ Low RR: {result.actual_rr:.2f}x < {result.min_rr_required:.2f}x")

    def test_validate_rr_exactly_at_minimum(self, position_sizer):
        """Test RR validation when ratio exactly meets minimum."""
        result = position_sizer.validate_rr(
            entry_price=2700.0,
            sl_price=2695.0,
            tp_price=2712.5,  # Exactly 2.5x
            min_rr=2.5,
        )

        assert result.is_valid is True
        assert result.actual_rr == pytest.approx(2.5, rel=0.01)
        print(f"✓ RR at minimum: {result.actual_rr:.2f}x")

    def test_validate_rr_entry_equals_tp(self, position_sizer):
        """Test RR validation when entry = TP → should raise ValueError."""
        with pytest.raises(ValueError, match="Entry price cannot equal take-profit"):
            position_sizer.validate_rr(
                entry_price=2700.0,
                sl_price=2695.0,
                tp_price=2700.0,  # Same as entry!
            )

    def test_validate_rr_entry_equals_sl(self, position_sizer):
        """Test RR validation when entry = SL → should raise ValueError."""
        with pytest.raises(ValueError, match="Entry price cannot equal stop-loss"):
            position_sizer.validate_rr(
                entry_price=2700.0,
                sl_price=2700.0,  # Same as entry!
                tp_price=2710.0,
            )

    def test_validate_rr_negative_prices(self, position_sizer):
        """Test RR validation with negative prices → should raise ValueError."""
        with pytest.raises(ValueError, match="All prices must be > 0"):
            position_sizer.validate_rr(
                entry_price=-2700.0,
                sl_price=2695.0,
                tp_price=2710.0,
            )

    # ========================================================================
    # Structural SL Tests
    # ========================================================================

    def test_structural_sl_invalid_direction(self, position_sizer):
        """Test structural SL with invalid direction → should raise ValueError."""
        poi_zone = {"top": 2705.0, "bottom": 2700.0, "mt": 2702.5}

        with pytest.raises(ValueError, match="Direction must be BULLISH or BEARISH"):
            position_sizer.get_structural_sl(
                direction="INVALID",
                poi_zone=poi_zone,
            )

    def test_structural_sl_missing_zone_keys(self, position_sizer):
        """Test structural SL with missing zone keys → should raise ValueError."""
        incomplete_zone = {"top": 2705.0}  # Missing "bottom" and "mt"

        with pytest.raises(ValueError, match="poi_zone missing keys"):
            position_sizer.get_structural_sl(
                direction="BULLISH",
                poi_zone=incomplete_zone,
            )

    def test_structural_sl_zone_top_less_than_bottom(self, position_sizer):
        """Test structural SL with inverted zone (top < bottom) → should raise ValueError."""
        bad_zone = {"top": 2695.0, "bottom": 2705.0, "mt": 2700.0}

        with pytest.raises(ValueError, match="Zone top .* must be >= zone bottom"):
            position_sizer.get_structural_sl(
                direction="BULLISH",
                poi_zone=bad_zone,
            )

    def test_structural_sl_negative_buffer(self, position_sizer):
        """Test structural SL with negative buffer → should raise ValueError."""
        poi_zone = {"top": 2705.0, "bottom": 2700.0, "mt": 2702.5}

        with pytest.raises(ValueError, match="Buffer pips must be >= 0"):
            position_sizer.get_structural_sl(
                direction="BULLISH",
                poi_zone=poi_zone,
                buffer_pips=-1.0,
            )

    def test_structural_sl_zero_buffer(self, position_sizer):
        """Test structural SL with zero buffer (valid edge case)."""
        poi_zone = {"top": 2705.0, "bottom": 2700.0, "mt": 2702.5}

        sl = position_sizer.get_structural_sl(
            direction="BULLISH",
            poi_zone=poi_zone,
            buffer_pips=0.0,
        )

        assert sl == 2700.0  # zone_bottom - 0
        print(f"✓ Structural SL with zero buffer: {sl:.2f}")

    # ========================================================================
    # Liquidity TP Tests
    # ========================================================================

    def test_liquidity_tp_invalid_direction(self, position_sizer):
        """Test liquidity TP with invalid direction → should raise ValueError."""
        liquidity = {
            "pdh": 2705.0,
            "pdl": 2695.0,
            "weekly_high": 2708.0,
            "weekly_low": 2690.0,
        }

        with pytest.raises(ValueError, match="Direction must be BULLISH or BEARISH"):
            position_sizer.get_liquidity_tp(
                direction="INVALID",
                liquidity_levels=liquidity,
            )

    def test_liquidity_tp_missing_keys(self, position_sizer):
        """Test liquidity TP with missing keys → should raise ValueError."""
        incomplete_liquidity = {"pdh": 2705.0}  # Missing other keys

        with pytest.raises(ValueError, match="liquidity_levels missing keys"):
            position_sizer.get_liquidity_tp(
                direction="BULLISH",
                liquidity_levels=incomplete_liquidity,
            )

    def test_liquidity_tp_negative_values(self, position_sizer):
        """Test liquidity TP with negative values → should raise ValueError."""
        bad_liquidity = {
            "pdh": -2705.0,  # Negative!
            "pdl": 2695.0,
            "weekly_high": 2708.0,
            "weekly_low": 2690.0,
        }

        with pytest.raises(ValueError, match="pdh must be > 0"):
            position_sizer.get_liquidity_tp(
                direction="BULLISH",
                liquidity_levels=bad_liquidity,
            )

    def test_liquidity_tp_bullish_selects_nearest(self, position_sizer):
        """Test BULLISH TP selects nearest (lowest) sell-side liquidity."""
        liquidity = {
            "pdh": 2705.0,
            "pdl": 2695.0,
            "weekly_high": 2715.0,  # Further away
            "weekly_low": 2690.0,
        }

        tp = position_sizer.get_liquidity_tp(
            direction="BULLISH",
            liquidity_levels=liquidity,
        )

        assert tp == 2705.0  # pdh is nearest (min of sell-side)
        print(f"✓ BULLISH TP: {tp:.2f} (nearest sell-side)")

    def test_liquidity_tp_bearish_selects_nearest(self, position_sizer):
        """Test BEARISH TP selects nearest (highest) buy-side liquidity."""
        liquidity = {
            "pdh": 2705.0,
            "pdl": 2695.0,
            "weekly_high": 2715.0,
            "weekly_low": 2685.0,  # Further away
        }

        tp = position_sizer.get_liquidity_tp(
            direction="BEARISH",
            liquidity_levels=liquidity,
        )

        assert tp == 2695.0  # pdl is nearest (max of buy-side)
        print(f"✓ BEARISH TP: {tp:.2f} (nearest buy-side)")

    # ========================================================================
    # Lot Clamping Tests
    # ========================================================================

    def test_calculate_lot_large_sl_distance_clamps_to_min(self, position_sizer):
        """Test lot calculation with huge SL distance → clamps to MIN_LOT."""
        result = position_sizer.calculate_lot(
            balance=100000,
            risk_pct=0.1,
            entry_price=2700.0,
            sl_price=2000.0,  # 700 pips away (huge!)
        )

        # Risk: 100 | SL: 700 | Lot = 100 / (700 * 100) = 0.00142 → clamp to 0.01
        assert result.lot_size == 0.01
        assert "MIN_LOT" in result.reason
        print(f"✓ Huge SL distance: Clamped to MIN_LOT {result.lot_size}")

    def test_calculate_lot_tiny_sl_distance_clamps_to_max(self, position_sizer):
        """Test lot calculation with tiny SL distance → clamps to MAX_LOT."""
        result = position_sizer.calculate_lot(
            balance=100000,
            risk_pct=1.0,
            entry_price=2700.0,
            sl_price=2699.0,  # Only 1 pip
        )

        # Risk: 1000 | SL: 1 | Lot = 1000 / (1 * 100) = 10 → clamp to 0.5
        assert result.lot_size == 0.5
        assert "MAX_LOT" in result.reason
        print(f"✓ Tiny SL distance: Clamped to MAX_LOT {result.lot_size}")


# ============================================================================
# TEST SUITE 2: CHALLENGE POLICY - EDGE CASES
# ============================================================================


class TestChallengePolicyEdgeCases:
    """Test ChallengePolicy enforcement and edge cases."""

    # ========================================================================
    # Daily Loss Limit Tests
    # ========================================================================

    def test_daily_loss_limit_exactly_at_limit(self, challenge_policy):
        """Test daily loss exactly at limit threshold."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=-1.0,  # Exactly at limit
            peak_balance=100000,
            current_balance=99000,
            trades_today=0,
            consecutive_losses=0,
        )

        # Should be allowed (at limit, not exceeding)
        assert allowed is True
        assert reason is None
        print(f"✓ Daily loss at limit: Trade allowed")

    def test_daily_loss_limit_slightly_exceeded(self, challenge_policy):
        """Test daily loss slightly exceeding limit."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=-1.01,  # Just over limit
            peak_balance=100000,
            current_balance=98990,
            trades_today=0,
            consecutive_losses=0,
        )

        assert allowed is False
        assert "DAILY_LOSS_LIMIT" in reason
        print(f"✗ Daily loss exceeded: {reason}")

    def test_daily_loss_limit_major_loss(self, challenge_policy):
        """Test daily loss with massive drawdown."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=-5.0,  # Massive loss
            peak_balance=100000,
            current_balance=95000,
            trades_today=0,
            consecutive_losses=0,
        )

        assert allowed is False
        assert "DAILY_LOSS_LIMIT" in reason
        print(f"✗ Major daily loss: {reason}")

    # ========================================================================
    # Drawdown Limit Tests
    # ========================================================================

    def test_drawdown_exactly_at_limit(self, challenge_policy):
        """Test drawdown exactly at limit."""
        # Peak: 100000, Current: 96500 = 3.5% drawdown (exactly at limit)
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=96500,
            trades_today=0,
            consecutive_losses=0,
        )

        assert allowed is True
        assert reason is None
        print(f"✓ Drawdown at limit: Trade allowed")

    def test_drawdown_slightly_exceeded(self, challenge_policy):
        """Test drawdown slightly exceeding limit."""
        # Peak: 100000, Current: 96400 = 3.6% drawdown (exceeds 3.5%)
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=96400,
            trades_today=0,
            consecutive_losses=0,
        )

        assert allowed is False
        assert "MAX_DRAWDOWN" in reason
        print(f"✗ Drawdown exceeded: {reason}")

    def test_drawdown_with_zero_peak_balance(self, challenge_policy):
        """Test drawdown calculation with zero peak (edge case)."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=0,  # Invalid but should not crash
            current_balance=100000,
            trades_today=0,
            consecutive_losses=0,
        )

        # Should handle gracefully (skip drawdown check)
        assert allowed is True  # Other checks pass
        print(f"✓ Zero peak balance handled gracefully")

    # ========================================================================
    # Max Trades Per Day Tests
    # ========================================================================

    def test_max_trades_at_limit(self, challenge_policy):
        """Test when exactly at max trades limit."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=2,  # At limit (max is 2)
            consecutive_losses=0,
        )

        assert allowed is False
        assert "MAX_TRADES_PER_DAY" in reason
        print(f"✗ Max trades reached: {reason}")

    def test_max_trades_below_limit(self, challenge_policy):
        """Test when below max trades limit."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=1,  # Below limit
            consecutive_losses=0,
        )

        assert allowed is True
        assert reason is None
        print(f"✓ Below max trades: Trade allowed")

    # ========================================================================
    # Consecutive Loss Tests
    # ========================================================================

    def test_consecutive_losses_at_limit(self, challenge_policy):
        """Test when exactly at consecutive loss limit."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=1,
            consecutive_losses=2,  # At limit (max is 2)
        )

        assert allowed is False
        assert "MAX_CONSECUTIVE_LOSSES" in reason
        print(f"✗ Consecutive losses limit: {reason}")

    def test_consecutive_losses_below_limit(self, challenge_policy):
        """Test when below consecutive loss limit."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=1,
            consecutive_losses=1,  # Below limit
        )

        assert allowed is True
        assert reason is None
        print(f"✓ Consecutive losses below limit: Trade allowed")

    # ========================================================================
    # Min Trade Gap Tests
    # ========================================================================

    def test_min_trade_gap_too_soon(self, challenge_policy):
        """Test trade gap violation (too soon after last trade)."""
        last_trade = datetime.now() - timedelta(minutes=30)  # 30 min ago

        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=1,
            consecutive_losses=0,
            last_trade_time=last_trade,
        )

        assert allowed is False
        assert "MIN_TRADE_GAP" in reason
        print(f"✗ Trade gap too short: {reason}")

    def test_min_trade_gap_exactly_at_minimum(self, challenge_policy):
        """Test trade gap exactly at minimum."""
        last_trade = datetime.now() - timedelta(minutes=90)  # Exactly 90 min ago

        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=1,
            consecutive_losses=0,
            last_trade_time=last_trade,
        )

        assert allowed is True
        assert reason is None
        print(f"✓ Trade gap at minimum: Trade allowed")

    def test_min_trade_gap_plenty_of_time(self, challenge_policy):
        """Test with plenty of time since last trade."""
        last_trade = datetime.now() - timedelta(hours=3)  # 3 hours ago

        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=1,
            consecutive_losses=0,
            last_trade_time=last_trade,
        )

        assert allowed is True
        assert reason is None
        print(f"✓ Plenty of time since last trade: Trade allowed")

    def test_min_trade_gap_no_prior_trade(self, challenge_policy):
        """Test when no prior trade (first trade of day)."""
        allowed, reason = challenge_policy.check_can_trade(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=100000,
            trades_today=0,
            consecutive_losses=0,
            last_trade_time=None,  # No prior trade
        )

        assert allowed is True
        assert reason is None
        print(f"✓ First trade of day: Trade allowed (no gap requirement)")

    # ========================================================================
    # Trade Result Logging
    # ========================================================================

    def test_log_consecutive_losses_accumulate(self, challenge_policy):
        """Test consecutive loss counter accumulation."""
        assert challenge_policy.consecutive_losses == 0

        challenge_policy.log_trade_result(was_win=False, pnl=-50.0)
        assert challenge_policy.consecutive_losses == 1
        assert challenge_policy.daily_pnl == -50.0

        challenge_policy.log_trade_result(was_win=False, pnl=-45.0)
        assert challenge_policy.consecutive_losses == 2
        assert challenge_policy.daily_pnl == -95.0

        print(f"✓ Consecutive losses accumulated: {challenge_policy.consecutive_losses}")

    def test_log_win_resets_consecutive_losses(self, challenge_policy):
        """Test that win resets consecutive loss counter."""
        challenge_policy.log_trade_result(was_win=False, pnl=-50.0)
        challenge_policy.log_trade_result(was_win=False, pnl=-45.0)
        assert challenge_policy.consecutive_losses == 2

        challenge_policy.log_trade_result(was_win=True, pnl=100.0)
        assert challenge_policy.consecutive_losses == 0
        assert challenge_policy.daily_pnl == 5.0  # -50 - 45 + 100

        print(f"✓ Win reset consecutive losses")

    def test_reset_daily_state_clears_daily_counters(self, challenge_policy):
        """Test daily state reset."""
        challenge_policy.trades_today = 2
        challenge_policy.daily_pnl = 150.0
        challenge_policy.daily_pnl_pct = 0.15
        challenge_policy.consecutive_losses = 1

        challenge_policy.reset_daily_state()

        assert challenge_policy.trades_today == 0
        assert challenge_policy.daily_pnl == 0.0
        assert challenge_policy.daily_pnl_pct == 0.0
        assert challenge_policy.consecutive_losses == 1  # NOT reset (intentional)

        print(f"✓ Daily state reset (consecutive_losses preserved)")

    # ========================================================================
    # Lockdown Detection
    # ========================================================================

    def test_get_lockdown_reason_daily_loss(self, challenge_policy):
        """Test lockdown detection from daily loss."""
        reason = challenge_policy.get_lockdown_reason(
            daily_pnl_pct=-1.5,
            peak_balance=100000,
            current_balance=98500,
            consecutive_losses=0,
        )

        assert reason is not None
        assert "Daily loss limit" in reason
        print(f"✓ Lockdown detected: {reason}")

    def test_get_lockdown_reason_drawdown(self, challenge_policy):
        """Test lockdown detection from drawdown."""
        reason = challenge_policy.get_lockdown_reason(
            daily_pnl_pct=0.0,
            peak_balance=100000,
            current_balance=96200,  # 3.8% drawdown
            consecutive_losses=0,
        )

        assert reason is not None
        assert "drawdown" in reason.lower()
        print(f"✓ Lockdown detected: {reason}")

    def test_get_lockdown_reason_no_lockdown(self, challenge_policy):
        """Test when no lockdown reason exists."""
        reason = challenge_policy.get_lockdown_reason(
            daily_pnl_pct=-0.5,
            peak_balance=100000,
            current_balance=99800,
            consecutive_losses=0,
        )

        assert reason is None
        print(f"✓ No lockdown: Trading allowed")


# ============================================================================
# TEST SUITE 3: ORDER EXECUTOR - INTEGRATION EDGE CASES
# ============================================================================


class TestOrderExecutorEdgeCases:
    """Test OrderExecutor with integrated edge cases."""

    # ========================================================================
    # Policy Blocking Tests
    # ========================================================================

    def test_execute_signal_blocked_by_daily_loss(
        self, order_executor, valid_signal
    ):
        """Test signal blocked due to daily loss limit."""
        result = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=99000,
            peak_balance=100000,
            current_daily_pnl_pct=-1.5,  # Exceeds 1% limit
        )

        assert result.success is False
        assert "DAILY_LOSS_LIMIT" in result.rejection_reason
        print(f"✗ Signal blocked: {result.rejection_reason}")

    def test_execute_signal_blocked_by_consecutive_losses(
        self, order_executor, valid_signal
    ):
        """Test signal blocked due to consecutive loss limit."""
        result = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=100000,
            peak_balance=100000,
            consecutive_losses=2,  # At limit
        )

        assert result.success is False
        assert "MAX_CONSECUTIVE_LOSSES" in result.rejection_reason
        print(f"✗ Signal blocked: {result.rejection_reason}")

    def test_execute_signal_blocked_by_trade_gap(
        self, order_executor, valid_signal
    ):
        """Test signal blocked due to insufficient time gap."""
        last_trade = datetime.now() - timedelta(minutes=30)

        result = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=100000,
            peak_balance=100000,
            trades_today=1,
            consecutive_losses=0,
            last_trade_time=last_trade,
        )

        assert result.success is False
        assert "MIN_TRADE_GAP" in result.rejection_reason
        print(f"✗ Signal blocked: {result.rejection_reason}")

    # ========================================================================
    # RR Validation Tests
    # ========================================================================

    def test_execute_signal_rr_too_low(self, order_executor):
        """Test signal rejected due to low RR ratio."""
        bad_signal = SignalResult(
            action="BUY",
            direction="BULLISH",
            entry_price=2700.0,
            sl_price=2695.0,
            tp_price=2707.0,  # Only 7 pips profit vs 5 pips risk = 1.4x RR
            poi_zone={"top": 2705.0, "bottom": 2700.0, "mt": 2702.5},
            liquidity_levels={
                "pdh": 2704.0,  # recalculated TP=2704 → RR=(2704-2700)/(2700-2698)=4/2=2.0 < 2.5
                "pdl": 2695.0,
                "weekly_high": 2708.0,
                "weekly_low": 2690.0,
            },
        )

        result = order_executor.execute_signal(
            signal=bad_signal,
            account_balance=100000,
            peak_balance=100000,
        )

        assert result.success is False
        assert "RR ratio" in result.rejection_reason
        print(f"✗ Signal rejected: {result.rejection_reason}")

    # ========================================================================
    # Invalid Input Tests
    # ========================================================================

    def test_execute_signal_invalid_poi_zone(self, order_executor):
        """Test signal with invalid POI zone."""
        bad_signal = SignalResult(
            action="BUY",
            direction="BULLISH",
            entry_price=2700.0,
            sl_price=2695.0,
            tp_price=2713.0,
            poi_zone={"top": 2700.0},  # Missing "bottom" and "mt"
            liquidity_levels={
                "pdh": 2705.0,
                "pdl": 2695.0,
                "weekly_high": 2708.0,
                "weekly_low": 2690.0,
            },
        )

        result = order_executor.execute_signal(
            signal=bad_signal,
            account_balance=100000,
            peak_balance=100000,
        )

        assert result.success is False
        assert "SL calculation error" in result.rejection_reason
        print(f"✗ Invalid POI zone: {result.rejection_reason}")

    def test_execute_signal_invalid_liquidity_levels(self, order_executor):
        """Test signal with invalid liquidity levels."""
        bad_signal = SignalResult(
            action="BUY",
            direction="BULLISH",
            entry_price=2700.0,
            sl_price=2695.0,
            tp_price=2713.0,
            poi_zone={"top": 2705.0, "bottom": 2700.0, "mt": 2702.5},
            liquidity_levels={"pdh": 2705.0},  # Missing other keys
        )

        result = order_executor.execute_signal(
            signal=bad_signal,
            account_balance=100000,
            peak_balance=100000,
        )

        assert result.success is False
        assert "TP calculation error" in result.rejection_reason
        print(f"✗ Invalid liquidity levels: {result.rejection_reason}")

    # ========================================================================
    # Successful Execution Tests
    # ========================================================================

    def test_execute_signal_successful_dry_run(self, order_executor, valid_signal):
        """Test successful signal execution in dry-run mode."""
        result = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=100000,
            peak_balance=100000,
        )

        assert result.success is True
        assert result.ticket is None  # Dry-run, no ticket
        assert result.lot_size > 0
        assert result.rr_ratio >= 2.5
        assert result.rejection_reason is None
        print(f"✓ Dry-run successful: {result.lot_size} lot, RR {result.rr_ratio:.2f}x")

    def test_execute_signal_successful_live_mode(
        self, mock_mt5_client, challenge_policy, position_sizer, valid_signal
    ):
        """Test successful signal execution in live mode (with MT5 mock)."""
        mock_mt5_client.send_order = Mock(return_value=54321)

        executor = OrderExecutor(
            mt5_client=mock_mt5_client,
            challenge_policy=challenge_policy,
            position_sizer=position_sizer,
            dry_run=False,  # Live mode
        )

        result = executor.execute_signal(
            signal=valid_signal,
            account_balance=100000,
            peak_balance=100000,
        )

        assert result.success is True
        assert result.ticket == 54321
        mock_mt5_client.send_order.assert_called_once()
        print(f"✓ Live mode successful: Ticket {result.ticket}")

    # ========================================================================
    # Lot Sizing Edge Cases in Execution
    # ========================================================================

    def test_execute_signal_tiny_balance(self, order_executor, valid_signal):
        """Test execution with very small account balance."""
        result = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=1000,  # Very small
            peak_balance=1000,
        )

        assert result.success is True
        assert result.lot_size == 0.01  # Minimum lot
        print(f"✓ Tiny balance: Minimum lot {result.lot_size} used")

    def test_execute_signal_large_balance(self, order_executor, valid_signal):
        """Test execution with very large account balance."""
        result = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=1000000,  # Very large
            peak_balance=1000000,
        )

        assert result.success is True
        assert result.lot_size <= 0.5  # Maximum lot
        print(f"✓ Large balance: Lot {result.lot_size} (within max)")

    # ========================================================================
    # Position Sync Tests
    # ========================================================================

    def test_sync_open_positions_empty(self, order_executor):
        """Test syncing when no positions are open."""
        order_executor.mt5_client.positions_get = Mock(return_value=[])

        tickets = order_executor.sync_open_positions()

        assert tickets == []
        print(f"✓ No open positions synced")

    def test_sync_open_positions_multiple(self, order_executor):
        """Test syncing multiple open positions."""
        mock_pos1 = Mock()
        mock_pos1.ticket = 12345
        mock_pos2 = Mock()
        mock_pos2.ticket = 12346
        mock_pos3 = Mock()
        mock_pos3.ticket = 12347

        order_executor.mt5_client.positions_get = Mock(
            return_value=[mock_pos1, mock_pos2, mock_pos3]
        )

        tickets = order_executor.sync_open_positions()

        assert tickets == [12345, 12346, 12347]
        assert len(tickets) == 3
        print(f"✓ Synced {len(tickets)} open positions")

    def test_sync_open_positions_mt5_error(self, order_executor):
        """Test syncing when MT5 returns None."""
        order_executor.mt5_client.positions_get = Mock(return_value=None)

        tickets = order_executor.sync_open_positions()

        assert tickets == []
        print(f"✓ MT5 error handled gracefully")


# ============================================================================
# PARAMETERIZED TESTS - STRESS TESTING
# ============================================================================


class TestParameterized:
    """Parameterized tests for stress-testing edge cases."""

    @pytest.mark.parametrize(
        "balance,expected_result",
        [
            (0, ValueError),
            (-100, ValueError),
            (0.01, ValueError),
            (100, "valid"),
            (1000, "valid"),
            (1000000, "valid"),
        ],
    )
    def test_lot_calc_various_balances(self, position_sizer, balance, expected_result):
        """Test lot calculation with various balance values."""
        if expected_result == ValueError:
            with pytest.raises(ValueError):
                position_sizer.calculate_lot(
                    balance=balance,
                    risk_pct=0.25,
                    entry_price=2700.0,
                    sl_price=2695.0,
                )
        else:
            result = position_sizer.calculate_lot(
                balance=balance,
                risk_pct=0.25,
                entry_price=2700.0,
                sl_price=2695.0,
            )
            assert result.lot_size > 0

    @pytest.mark.parametrize(
        "entry,sl,tp,expected_valid",
        [
            (2700.0, 2695.0, 2712.5, True),  # 2.5x RR (at minimum)
            (2700.0, 2695.0, 2712.6, True),  # 2.52x RR (above minimum)
            (2700.0, 2695.0, 2712.0, False),  # 2.4x RR (below minimum)
            (2700.0, 2695.0, 2708.0, False),  # 1.6x RR (well below)
        ],
    )
    def test_rr_validation_various_ratios(
        self, position_sizer, entry, sl, tp, expected_valid
    ):
        """Test RR validation with various ratio scenarios."""
        result = position_sizer.validate_rr(entry, sl, tp)
        assert result.is_valid == expected_valid


# ============================================================================
# INTEGRATION TEST - FULL WORKFLOW
# ============================================================================


class TestFullWorkflow:
    """Integration test for complete trading workflow."""

    def test_full_workflow_from_signal_to_execution(
        self, order_executor, valid_signal
    ):
        """Test complete workflow: signal → policy check → sizing → execution."""
        # Simulate multiple trade scenario
        balance = 100000
        peak_balance = 100000
        trades_today = 0
        consecutive_losses = 0
        daily_pnl_pct = 0.0
        last_trade_time = None

        # Trade 1: Execute
        result1 = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=balance,
            peak_balance=peak_balance,
            trades_today=trades_today,
            consecutive_losses=consecutive_losses,
            current_daily_pnl_pct=daily_pnl_pct,
            last_trade_time=last_trade_time,
        )

        assert result1.success is True
        trades_today += 1
        last_trade_time = result1.timestamp

        # Log trade result (loss)
        order_executor.challenge_policy.log_trade_result(was_win=False, pnl=-50.0)
        consecutive_losses = order_executor.challenge_policy.consecutive_losses
        daily_pnl_pct = -0.05

        # Trade 2: Try to execute (should succeed - gap met)
        result2 = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=balance - 50,
            peak_balance=peak_balance,
            trades_today=trades_today,
            consecutive_losses=consecutive_losses,
            current_daily_pnl_pct=daily_pnl_pct,
            last_trade_time=datetime.now() - timedelta(minutes=91),  # Sufficient gap
        )

        assert result2.success is True
        trades_today += 1

        # Log another loss
        order_executor.challenge_policy.log_trade_result(was_win=False, pnl=-45.0)
        consecutive_losses = order_executor.challenge_policy.consecutive_losses
        daily_pnl_pct = -0.095

        # Trade 3: Try to execute (should be blocked - consecutive loss limit)
        result3 = order_executor.execute_signal(
            signal=valid_signal,
            account_balance=balance - 95,
            peak_balance=peak_balance,
            trades_today=trades_today,
            consecutive_losses=consecutive_losses,
            current_daily_pnl_pct=daily_pnl_pct,
            last_trade_time=datetime.now() - timedelta(minutes=91),
        )

        assert result3.success is False
        assert "MAX_CONSECUTIVE_LOSSES" in result3.rejection_reason

        print(
            f"✓ Full workflow: Trade 1 success, Trade 2 success, Trade 3 blocked "
            f"(as expected)"
        )


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
