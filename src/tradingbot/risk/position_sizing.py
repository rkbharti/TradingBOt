"""
Position Sizing Engine for XAUUSD Trading Bot

Calculates optimal lot sizes, validates risk-reward ratios, and determines
structural stop-losses and liquidity-based take-profits.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Tuple, Literal
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

# XAUUSD Specifications
MIN_LOT = 0.01
MAX_LOT = 0.50
DEFAULT_CONTRACT_SIZE = 100  # Standard lot for XAUUSD
DEFAULT_PIP_VALUE = 0.1  # XAUUSD pip value
MIN_RISK_RATIO = 2.5

# Structural levels buffer
DEFAULT_BUFFER_PIPS = 2.0


@dataclass
class LotCalculation:
    """Result of lot size calculation."""
    lot_size: float
    risk_amount: float
    sl_distance: float
    reason: str = "OK"


@dataclass
class RiskRewardValidation:
    """Result of RR validation."""
    is_valid: bool
    actual_rr: float
    entry_price: float
    sl_price: float
    tp_price: float
    min_rr_required: float


class PositionSizer:
    """
    Calculates position sizing parameters for XAUUSD trades.
    
    Ensures:
    - Lot sizes respect risk parameters and account balance
    - Stop-losses are placed at structural support/resistance
    - Take-profits are placed at liquidity levels
    - Risk-reward ratios meet minimum thresholds
    """

    def __init__(
        self,
        min_lot: float = MIN_LOT,
        max_lot: float = MAX_LOT,
        contract_size: float = DEFAULT_CONTRACT_SIZE,
        pip_value: float = DEFAULT_PIP_VALUE,
        min_rr: float = MIN_RISK_RATIO,
    ):
        """
        Initialize PositionSizer.
        
        Args:
            min_lot: Minimum lot size allowed (default: 0.01)
            max_lot: Maximum lot size allowed (default: 0.50)
            contract_size: Contract size for symbol (default: 100 for XAUUSD)
            pip_value: Pip value in account currency (default: 0.1 for XAUUSD)
            min_rr: Minimum risk-reward ratio required (default: 2.5)
        """
        self.min_lot = min_lot
        self.max_lot = max_lot
        self.contract_size = contract_size
        self.pip_value = pip_value
        self.min_rr = min_rr

        logger.info(
            f"PositionSizer initialized: min_lot={min_lot}, max_lot={max_lot}, "
            f"contract_size={contract_size}, pip_value={pip_value}, min_rr={min_rr}"
        )

    # =========================================================================
    # LOT CALCULATION
    # =========================================================================

    def calculate_lot(
        self,
        balance: float,
        risk_pct: float,
        entry_price: float,
        sl_price: float,
        symbol: str = "XAUUSD",
    ) -> LotCalculation:
        """
        Calculate optimal lot size based on account risk parameters.
        
        Formula:
            risk_amount = balance * (risk_pct / 100)
            sl_distance = abs(entry_price - sl_price)
            lot = risk_amount / (sl_distance * contract_size)
            lot = clamp(lot, min_lot, max_lot)
        
        Args:
            balance: Current account balance in account currency
            risk_pct: Risk percentage per trade (0-100)
            entry_price: Trade entry price
            sl_price: Stop-loss price
            symbol: Trading symbol (default: "XAUUSD")
        
        Returns:
            LotCalculation dataclass with lot_size, risk_amount, sl_distance
        
        Raises:
            ValueError: If inputs are invalid
        """
        try:
            # Validate inputs
            if balance < 1.0:
                raise ValueError(f"Balance must be > 0, got {balance}")
            if not (0 < risk_pct <= 100):
                raise ValueError(f"Risk percentage must be 0-100, got {risk_pct}")
            if entry_price <= 0 or sl_price <= 0:
                raise ValueError(f"Prices must be > 0: entry={entry_price}, sl={sl_price}")
            if entry_price == sl_price:
                raise ValueError("Entry price cannot equal stop-loss price")

            # Calculate risk amount
            risk_amount = balance * (risk_pct / 100)
            logger.debug(f"Risk amount: ${risk_amount:.2f} ({risk_pct}% of ${balance:.2f})")

            # Calculate stop-loss distance in pips
            sl_distance = abs(entry_price - sl_price)
            logger.debug(f"SL distance: {sl_distance:.2f} pips")

            # Calculate raw lot size
            # lot = risk_amount / (sl_distance_in_pips * contract_size)
            denominator = sl_distance * self.contract_size
            if denominator == 0:
                raise ValueError("SL distance cannot be zero")

            raw_lot = risk_amount / denominator
            logger.debug(f"Raw lot size before clamping: {raw_lot:.4f}")

            # Clamp to min/max range
            clamped_lot = max(self.min_lot, min(raw_lot, self.max_lot))
            
            # Round to 2 decimal places for MT5 compatibility
            final_lot = round(clamped_lot, 2)

            reason = "OK"
            if final_lot < raw_lot:
                reason = f"Clamped to MAX_LOT ({self.max_lot})"
            elif final_lot > raw_lot:
                reason = f"Increased to MIN_LOT ({self.min_lot})"

            logger.info(
                f"Lot calculation: {final_lot} lot | Risk: ${risk_amount:.2f} | "
                f"SL Distance: {sl_distance:.2f} pips | {reason}"
            )

            return LotCalculation(
                lot_size=final_lot,
                risk_amount=risk_amount,
                sl_distance=sl_distance,
                reason=reason,
            )

        except ValueError as e:
            logger.error(f"Lot calculation validation error: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in calculate_lot: {str(e)}")
            raise ValueError(f"Lot calculation failed: {str(e)}")

    # =========================================================================
    # RISK-REWARD VALIDATION
    # =========================================================================

    def validate_rr(
        self,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        min_rr: float | None = None,
    ) -> RiskRewardValidation:
        """
        Validate that risk-reward ratio meets minimum threshold.
        
        RR Ratio = Profit Distance / Risk Distance
        
        Example:
            Entry: 2700.0
            SL: 2695.0 (risk = 5 pips)
            TP: 2713.0 (profit = 13 pips)
            RR = 13 / 5 = 2.6x ✓
        
        Args:
            entry_price: Trade entry price
            sl_price: Stop-loss price
            tp_price: Take-profit price
            min_rr: Minimum RR required (overrides self.min_rr if provided)
        
        Returns:
            RiskRewardValidation dataclass
        
        Raises:
            ValueError: If inputs are invalid
        """
        try:
            # Use instance min_rr if not overridden
            required_min_rr = min_rr if min_rr is not None else self.min_rr

            # Validate inputs
            if entry_price <= 0 or sl_price <= 0 or tp_price <= 0:
                raise ValueError(
                    f"All prices must be > 0: entry={entry_price}, sl={sl_price}, tp={tp_price}"
                )

            if entry_price == sl_price:
                raise ValueError("Entry price cannot equal stop-loss price")

            if entry_price == tp_price:
                raise ValueError("Entry price cannot equal take-profit price")

            if required_min_rr <= 0:
                raise ValueError(f"Min RR must be > 0, got {required_min_rr}")

            # Calculate risk and reward distances
            risk_distance = abs(entry_price - sl_price)
            reward_distance = abs(tp_price - entry_price)

            # Calculate RR ratio
            if risk_distance == 0:
                raise ValueError("Risk distance cannot be zero")

            actual_rr = reward_distance / risk_distance

            # Determine validity
            is_valid = actual_rr >= required_min_rr

            logger.info(
                f"RR Validation: {actual_rr:.2f}x (required: {required_min_rr}x) | "
                f"Risk: {risk_distance:.2f} | Reward: {reward_distance:.2f} | "
                f"Status: {'✓ VALID' if is_valid else '✗ INVALID'}"
            )

            return RiskRewardValidation(
                is_valid=is_valid,
                actual_rr=actual_rr,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                min_rr_required=required_min_rr,
            )

        except ValueError as e:
            logger.error(f"RR validation error: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in validate_rr: {str(e)}")
            raise ValueError(f"RR validation failed: {str(e)}")

    # =========================================================================
    # STRUCTURAL STOP-LOSS PLACEMENT
    # =========================================================================

    def get_structural_sl(
        self,
        direction: Literal["BULLISH", "BEARISH"],
        poi_zone: Dict[str, float],
        buffer_pips: float = DEFAULT_BUFFER_PIPS,
    ) -> float:
        """
        Calculate stop-loss price based on structural support/resistance.
        
        For BULLISH trades (long):
            SL = zone bottom - buffer
        
        For BEARISH trades (short):
            SL = zone top + buffer
        
        Args:
            direction: Trade direction ("BULLISH" for long, "BEARISH" for short)
            poi_zone: Dict with keys "top", "bottom", "mt" (mid-point)
                Example: {"top": 2702.5, "bottom": 2700.0, "mt": 2701.25}
            buffer_pips: Buffer distance below/above zone (default: 2.0 pips)
        
        Returns:
            Stop-loss price
        
        Raises:
            ValueError: If inputs are invalid
        """
        try:
            # Validate direction
            if direction not in ("BULLISH", "BEARISH"):
                raise ValueError(f"Direction must be BULLISH or BEARISH, got {direction}")

            # Validate poi_zone
            required_keys = {"top", "bottom", "mt"}
            if not isinstance(poi_zone, dict):
                raise ValueError(f"poi_zone must be a dict, got {type(poi_zone)}")

            missing_keys = required_keys - set(poi_zone.keys())
            if missing_keys:
                raise ValueError(f"poi_zone missing keys: {missing_keys}")

            # Validate buffer
            if buffer_pips < 0:
                raise ValueError(f"Buffer pips must be >= 0, got {buffer_pips}")

            # Validate zone values
            zone_top = poi_zone["top"]
            zone_bottom = poi_zone["bottom"]
            
            if zone_top <= 0 or zone_bottom <= 0:
                raise ValueError(f"Zone prices must be > 0: top={zone_top}, bottom={zone_bottom}")
            
            if zone_top < zone_bottom:
                raise ValueError(
                    f"Zone top ({zone_top}) must be >= zone bottom ({zone_bottom})"
                )

            # Calculate SL based on direction
            if direction == "BULLISH":
                # Long: SL below support
                sl_price = zone_bottom - buffer_pips
            else:  # BEARISH
                # Short: SL above resistance
                sl_price = zone_top + buffer_pips

            logger.info(
                f"Structural SL ({direction}): {sl_price:.2f} | "
                f"Zone: [{zone_bottom:.2f} - {zone_top:.2f}] | Buffer: {buffer_pips} pips"
            )

            return sl_price

        except ValueError as e:
            logger.error(f"Structural SL calculation error: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in get_structural_sl: {str(e)}")
            raise ValueError(f"Structural SL calculation failed: {str(e)}")

    # =========================================================================
    # LIQUIDITY-BASED TAKE-PROFIT
    # =========================================================================

    def get_liquidity_tp(
        self,
        direction: Literal["BULLISH", "BEARISH"],
        liquidity_levels: Dict[str, float],
    ) -> float:
        """
        Calculate take-profit price based on nearest liquidity level.
        
        For BULLISH trades (long):
            TP = nearest sell-side liquidity above entry
            Candidates: pdh (previous day high), weekly_high
        
        For BEARISH trades (short):
            TP = nearest buy-side liquidity below entry
            Candidates: pdl (previous day low), weekly_low
        
        Args:
            direction: Trade direction ("BULLISH" for long, "BEARISH" for short)
            liquidity_levels: Dict with keys "pdh", "pdl", "weekly_high", "weekly_low"
                Example: {
                    "pdh": 2705.0,
                    "pdl": 2695.0,
                    "weekly_high": 2708.0,
                    "weekly_low": 2690.0
                }
        
        Returns:
            Take-profit price (nearest liquidity level)
        
        Raises:
            ValueError: If inputs are invalid or no valid liquidity levels exist
        """
        try:
            # Validate direction
            if direction not in ("BULLISH", "BEARISH"):
                raise ValueError(f"Direction must be BULLISH or BEARISH, got {direction}")

            # Validate liquidity_levels
            required_keys = {"pdh", "pdl", "weekly_high", "weekly_low"}
            if not isinstance(liquidity_levels, dict):
                raise ValueError(f"liquidity_levels must be a dict, got {type(liquidity_levels)}")

            missing_keys = required_keys - set(liquidity_levels.keys())
            if missing_keys:
                raise ValueError(f"liquidity_levels missing keys: {missing_keys}")

            # Validate all values are positive
            for key, value in liquidity_levels.items():
                if value <= 0:
                    raise ValueError(f"{key} must be > 0, got {value}")

            # Get appropriate liquidity levels
            if direction == "BULLISH":
                # Long: look for sell-side liquidity above
                candidates = [
                    ("pdh", liquidity_levels["pdh"]),
                    ("weekly_high", liquidity_levels["weekly_high"]),
                ]
                tp_price = min(cand[1] for cand in candidates)
                used_level = [name for name, price in candidates if price == tp_price][0]
                
            else:  # BEARISH
                # Short: look for buy-side liquidity below
                candidates = [
                    ("pdl", liquidity_levels["pdl"]),
                    ("weekly_low", liquidity_levels["weekly_low"]),
                ]
                tp_price = max(cand[1] for cand in candidates)
                used_level = [name for name, price in candidates if price == tp_price][0]

            logger.info(
                f"Liquidity TP ({direction}): {tp_price:.2f} "
                f"(nearest: {used_level})"
            )

            return tp_price

        except ValueError as e:
            logger.error(f"Liquidity TP calculation error: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in get_liquidity_tp: {str(e)}")
            raise ValueError(f"Liquidity TP calculation failed: {str(e)}")
