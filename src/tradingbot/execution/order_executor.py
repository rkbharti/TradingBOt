"""
Order Execution Engine

Bridges signal generation to MT5 order execution.

Responsibilities:
1. Apply challenge policy checks (block if rules violated)
2. Recalculate SL/TP using structural levels and liquidity
3. Validate risk-reward ratio
4. Calculate optimal position size
5. Build and send MT5 order request
6. Handle dry-run mode for backtesting

Flow:
    Signal (from signal engine)
        ↓
    Policy check (challenge_policy)
        ↓
    SL/TP recalculation (position_sizing)
        ↓
    RR validation (position_sizing)
        ↓
    Lot calculation (position_sizing)
        ↓
    MT5 order request build
        ↓
    Send to MT5 or DRY_RUN log
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Literal
from datetime import datetime

# Local imports (relative to this package)
from ..risk.position_sizing import PositionSizer, RiskRewardValidation
from ..risk.challenge_policy import ChallengePolicy

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

DRY_RUN = True  # Set to False for live trading
DRY_RUN_LOG_DIR = "logs/execution"


# ============================================================================
# RESULT DATACLASSES
# ============================================================================

@dataclass
class ExecutionResult:
    """
    Result of execute_signal() call.
    
    Fields:
        success: True if order was sent successfully
        ticket: MT5 ticket number (None if failed or dry-run)
        lot_size: Lot size used for order
        entry_price: Entry price used
        sl_price: Final stop-loss price (may differ from signal)
        tp_price: Final take-profit price (may differ from signal)
        rr_ratio: Actual risk-reward ratio achieved
        risk_amount: Dollar amount at risk
        rejection_reason: Human-readable rejection reason (if failed)
        timestamp: When order was attempted
    """

    success: bool
    ticket: Optional[int] = None
    lot_size: float = 0.0
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    rr_ratio: float = 0.0
    risk_amount: float = 0.0
    rejection_reason: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "success": self.success,
            "ticket": self.ticket,
            "lot_size": self.lot_size,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "rr_ratio": self.rr_ratio,
            "risk_amount": self.risk_amount,
            "rejection_reason": self.rejection_reason,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SignalResult:
    """Input signal from signal engine."""

    action: Literal["BUY", "SELL", "CLOSE"]
    direction: Literal["BULLISH", "BEARISH"]
    entry_price: float
    sl_price: float
    tp_price: float
    poi_zone: Dict[str, float]  # {"top": ..., "bottom": ..., "mt": ...}
    liquidity_levels: Dict[str, float]  # {"pdh": ..., "pdl": ..., "weekly_high": ..., "weekly_low": ...}


# ============================================================================
# ORDER EXECUTOR CLASS
# ============================================================================

class OrderExecutor:
    """
    Executes trading signals as MT5 orders.
    
    Applies policy, recalculates SL/TP, validates RR, calculates lot,
    and sends to MT5 (or logs in dry-run mode).
    
    Usage:
        executor = OrderExecutor(mt5_client, challenge_policy)
        
        result = executor.execute_signal(
            signal=signal_from_engine,
            account_balance=100000,
            peak_balance=100000
        )
        
        if result.success:
            print(f"Order sent: Ticket {result.ticket}")
        else:
            print(f"Order rejected: {result.rejection_reason}")
    """

    def __init__(
        self,
        mt5_client: Any,
        challenge_policy: ChallengePolicy,
        position_sizer: Optional[PositionSizer] = None,
        dry_run: bool = DRY_RUN,
    ):
        """
        Initialize OrderExecutor.
        
        Args:
            mt5_client: MT5Connection instance
            challenge_policy: ChallengePolicy instance
            position_sizer: PositionSizer instance (creates default if None)
            dry_run: If True, logs orders without sending to MT5
        """
        self.mt5_client = mt5_client
        self.challenge_policy = challenge_policy
        self.position_sizer = position_sizer or PositionSizer()
        self.dry_run = dry_run

        logger.info(
            f"OrderExecutor initialized:\n"
            f"  MT5 Symbol: {mt5_client.symbol}\n"
            f"  DRY_RUN: {self.dry_run}\n"
            f"  Risk/Trade: {challenge_policy.risk_per_trade_pct}%"
        )

    # =========================================================================
    # MAIN EXECUTION METHOD
    # =========================================================================

    def execute_signal(
        self,
        signal: SignalResult,
        account_balance: float,
        peak_balance: float,
        current_daily_pnl_pct: float = 0.0,
        trades_today: int = 0,
        consecutive_losses: int = 0,
        last_trade_time: Optional[datetime] = None,
    ) -> ExecutionResult:
        """
        Execute a trading signal through all policy and validation checks.
        
        Steps:
        1. Check ChallengePolicy.check_can_trade()
        2. Recalculate SL using structural levels
        3. Recalculate TP using liquidity levels
        4. Validate RR >= 2.5
        5. Calculate optimal lot size
        6. Build MT5 order request
        7. Send to MT5 (or log in dry-run)
        
        Args:
            signal: SignalResult from signal engine
            account_balance: Current account balance
            peak_balance: Highest balance reached (for drawdown)
            daily_pnl_pct: Today's PnL as % (optional)
            trades_today: Number of trades executed today (optional)
            consecutive_losses: Count of consecutive losing trades (optional)
            last_trade_time: Timestamp of last trade (optional)
        
        Returns:
            ExecutionResult with success status and order details
        
        Example:
            result = executor.execute_signal(
                signal=signal,
                account_balance=99800,
                peak_balance=100000,
                current_daily_pnl_pct=-0.2,
                trades_today=1,
                consecutive_losses=0
            )
            
            if result.success:
                print(f"✓ Ticket {result.ticket} | RR {result.rr_ratio:.2f}x | Lot {result.lot_size}")
            else:
                print(f"✗ {result.rejection_reason}")
        """
        try:
            timestamp = datetime.now()

            logger.info(
                f"\n{'='*70}\n"
                f"EXECUTING SIGNAL: {signal.direction} {signal.action}\n"
                f"Entry: {signal.entry_price} | SL: {signal.sl_price} | TP: {signal.tp_price}\n"
                f"{'='*70}"
            )

            # =========================================================================
            # STEP 1: POLICY CHECK
            # =========================================================================

            allowed, policy_reason = self.challenge_policy.check_can_trade(
                daily_pnl_pct=current_daily_pnl_pct,
                peak_balance=peak_balance,
                current_balance=account_balance,
                trades_today=trades_today,
                consecutive_losses=consecutive_losses,
                last_trade_time=last_trade_time,
            )

            if not allowed:
                logger.warning(f"Policy violation: {policy_reason}")
                return ExecutionResult(
                    success=False,
                    rejection_reason=policy_reason,
                    timestamp=timestamp,
                )

            # =========================================================================
            # STEP 2: RECALCULATE SL (STRUCTURAL)
            # =========================================================================

            try:
                structural_sl = self.position_sizer.get_structural_sl(
                    direction=signal.direction,
                    poi_zone=signal.poi_zone,
                    buffer_pips=2.0,
                )
                logger.info(f"Structural SL recalculated: {signal.sl_price:.2f} → {structural_sl:.2f}")
                final_sl = structural_sl

            except ValueError as e:
                logger.error(f"SL recalculation failed: {str(e)}")
                return ExecutionResult(
                    success=False,
                    rejection_reason=f"SL calculation error: {str(e)}",
                    timestamp=timestamp,
                )

            # =========================================================================
            # STEP 3: RECALCULATE TP (LIQUIDITY)
            # =========================================================================

            try:
                liquidity_tp = self.position_sizer.get_liquidity_tp(
                    direction=signal.direction,
                    liquidity_levels=signal.liquidity_levels,
                )
                logger.info(f"Liquidity TP recalculated: {signal.tp_price:.2f} → {liquidity_tp:.2f}")
                final_tp = liquidity_tp

            except ValueError as e:
                logger.error(f"TP recalculation failed: {str(e)}")
                return ExecutionResult(
                    success=False,
                    rejection_reason=f"TP calculation error: {str(e)}",
                    timestamp=timestamp,
                )

            # =========================================================================
            # STEP 4: VALIDATE RISK-REWARD RATIO
            # =========================================================================

            try:
                rr_validation: RiskRewardValidation = self.position_sizer.validate_rr(
                    entry_price=signal.entry_price,
                    sl_price=final_sl,
                    tp_price=final_tp,
                    min_rr=2.5,
                )

                if not rr_validation.is_valid:
                    reason = (
                        f"RR ratio {rr_validation.actual_rr:.2f}x below minimum "
                        f"{rr_validation.min_rr_required}x"
                    )
                    logger.warning(f"RR validation failed: {reason}")
                    return ExecutionResult(
                        success=False,
                        rejection_reason=reason,
                        timestamp=timestamp,
                    )

                logger.info(f"✓ RR validation passed: {rr_validation.actual_rr:.2f}x")

            except ValueError as e:
                logger.error(f"RR validation error: {str(e)}")
                return ExecutionResult(
                    success=False,
                    rejection_reason=f"RR validation error: {str(e)}",
                    timestamp=timestamp,
                )

            # =========================================================================
            # STEP 5: CALCULATE LOT SIZE
            # =========================================================================

            try:
                lot_calc = self.position_sizer.calculate_lot(
                    balance=account_balance,
                    risk_pct=self.challenge_policy.risk_per_trade_pct,
                    entry_price=signal.entry_price,
                    sl_price=final_sl,
                    symbol=self.mt5_client.symbol,
                )

                final_lot = lot_calc.lot_size
                logger.info(
                    f"Lot size calculated: {final_lot} | "
                    f"Risk: ${lot_calc.risk_amount:.2f}"
                )

            except ValueError as e:
                logger.error(f"Lot calculation error: {str(e)}")
                return ExecutionResult(
                    success=False,
                    rejection_reason=f"Lot calculation error: {str(e)}",
                    timestamp=timestamp,
                )

            # =========================================================================
            # STEP 6: BUILD MT5 ORDER REQUEST
            # =========================================================================

            try:
                order_request = self._build_order_request(
                    signal=signal,
                    lot=final_lot,
                    entry_price=signal.entry_price,
                    sl_price=final_sl,
                    tp_price=final_tp,
                )

                logger.debug(f"Order request built: {order_request}")

            except Exception as e:
                logger.error(f"Order request build error: {str(e)}")
                return ExecutionResult(
                    success=False,
                    rejection_reason=f"Order build error: {str(e)}",
                    timestamp=timestamp,
                )

            # =========================================================================
            # STEP 7: SEND TO MT5 (or DRY-RUN)
            # =========================================================================

            if self.dry_run:
                logger.info(
                    f"\n🔵 DRY-RUN MODE 🔵\n"
                    f"Order request logged (not sent to MT5):\n"
                    f"  Action: {signal.action}\n"
                    f"  Lot: {final_lot}\n"
                    f"  Entry: {order_request.get('price', signal.entry_price)}\n"
                    f"  SL: {final_sl}\n"
                    f"  TP: {final_tp}\n"
                    f"  RR: {rr_validation.actual_rr:.2f}x"
                )
                ticket = None

            else:
                # Send real order to MT5
                ticket = self.mt5_client.send_order(order_request)
                logger.info(f"Order sent to MT5: Ticket {ticket}")

            # =========================================================================
            # RETURN SUCCESS RESULT
            # =========================================================================

            result = ExecutionResult(
                success=True,
                ticket=ticket,
                lot_size=final_lot,
                entry_price=signal.entry_price,
                sl_price=final_sl,
                tp_price=final_tp,
                rr_ratio=rr_validation.actual_rr,
                risk_amount=lot_calc.risk_amount,
                timestamp=timestamp,
            )

            logger.info(f"✓ Execution successful:\n{result.to_dict()}")
            return result

        except Exception as e:
            logger.exception(f"Unexpected error in execute_signal: {str(e)}")
            return ExecutionResult(
                success=False,
                rejection_reason=f"Execution error: {str(e)}",
                timestamp=timestamp,
            )

    # =========================================================================
    # HELPER: BUILD ORDER REQUEST
    # =========================================================================

    def _build_order_request(
        self,
        signal: SignalResult,
        lot: float,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ) -> Dict[str, Any]:
        """
        Build MT5 order request dictionary.
        
        Returns dict ready for mt5_client.send_order().
        
        Args:
            signal: Signal containing action, direction, etc.
            lot: Lot size
            entry_price: Entry price
            sl_price: Stop-loss price
            tp_price: Take-profit price
        
        Returns:
            MT5 order request dict
        """
        try:
            # Lazy import MetaTrader5 constants (only needed for live trading)
            try:
                import MetaTrader5 as mt5
            except ImportError:
                # Fallback to integer constants if MT5 not available (for testing)
                # These match MetaTrader5 library definitions
                class mt5:
                    ORDER_TYPE_BUY = 0
                    ORDER_TYPE_SELL = 1
                    TRADE_ACTION_DEAL = 1
                    ORDER_TIME_GTC = 0
                    ORDER_FILLING_IOC = 1
            
            # Determine order type based on action
            if signal.action == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
            elif signal.action == "SELL":
                order_type = mt5.ORDER_TYPE_SELL
            else:
                raise ValueError(f"Unknown action: {signal.action}")

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.mt5_client.symbol,
                "volume": lot,
                "type": order_type,
                "price": entry_price,
                "sl": sl_price,
                "tp": tp_price,
                "deviation": 50,
                "magic": 12345,
                "comment": f"{signal.direction}_{signal.action}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            logger.debug(f"Order request: {request}")
            return request

        except Exception as e:
            logger.exception(f"Error building order request: {str(e)}")
            raise

    # =========================================================================
    # HELPER: SYNC POSITIONS
    # =========================================================================

    def sync_open_positions(self) -> list:
        """
        Query MT5 for all open positions.
        
        Used to prevent position deadlock bugs and verify state.
        
        Returns:
            List of active ticket numbers
        
        Example:
            tickets = executor.sync_open_positions()
            print(f"Open positions: {len(tickets)}")
            for ticket in tickets:
                print(f"  Ticket {ticket}")
        """
        try:
            # Use mocked MT5 client for testing, real MT5 in production
            positions = self.mt5_client.positions_get(symbol=self.mt5_client.symbol)

            if positions is None:
                logger.warning("MT5 positions_get() returned None")
                return []

            tickets = [pos.ticket for pos in positions]
            logger.info(f"Open positions synced: {len(tickets)} tickets")
            return tickets

        except Exception as e:
            logger.exception(f"Error syncing open positions: {str(e)}")
            return []
