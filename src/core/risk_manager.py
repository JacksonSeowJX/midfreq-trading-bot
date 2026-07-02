"""
Risk Management Module
======================
Provides position sizing, stop-losses, take-profits, and portfolio-level
circuit breakers for the quantitative trading framework.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple
from enum import Enum
import math


# ─── Position Sizing ───────────────────────────────────────────────

class SizingMethod(str, Enum):
    FIXED_QUANTITY = "fixed_quantity"
    FIXED_FRACTIONAL = "fixed_fractional"
    KELLY = "kelly"


class PositionSizer(ABC):
    """Abstract base class for position sizing algorithms."""

    @abstractmethod
    def calculate_qty(self, equity: float, entry_price: float,
                      stop_price: Optional[float] = None,
                      win_rate: Optional[float] = None,
                      avg_win: Optional[float] = None,
                      avg_loss: Optional[float] = None) -> int:
        """
        Calculate the number of shares to trade.

        Args:
            equity: Current total portfolio equity
            entry_price: Expected entry price
            stop_price: Stop-loss price (if applicable)
            win_rate: Historical win rate (for Kelly)
            avg_win: Average winning trade P&L (for Kelly)
            avg_loss: Average losing trade P&L (for Kelly)

        Returns:
            Integer number of shares to buy
        """
        pass


class FixedQuantitySizer(PositionSizer):
    """Always trade a fixed number of shares (legacy behavior)."""

    def __init__(self, qty: int = 100):
        self.qty = qty

    def calculate_qty(self, equity: float, entry_price: float, **kwargs) -> int:
        # Ensure we can afford the position
        max_affordable = int(equity / entry_price) if entry_price > 0 else 0
        return min(self.qty, max_affordable)


class FixedFractionalSizer(PositionSizer):
    """
    Risk a fixed percentage of equity per trade.

    The position size is determined by how much capital you're willing
    to lose if the stop-loss is hit:
        qty = (equity * risk_pct) / (entry_price - stop_price)

    If no stop price is provided, falls back to risking risk_pct of
    equity as the total position value.
    """

    def __init__(self, risk_pct: float = 0.02):
        """
        Args:
            risk_pct: Fraction of equity to risk per trade (e.g., 0.02 = 2%)
        """
        self.risk_pct = risk_pct

    def calculate_qty(self, equity: float, entry_price: float,
                      stop_price: Optional[float] = None, **kwargs) -> int:
        if entry_price <= 0:
            return 0

        risk_amount = equity * self.risk_pct

        if stop_price is not None and stop_price < entry_price:
            risk_per_share = entry_price - stop_price
            if risk_per_share > 0:
                qty = int(risk_amount / risk_per_share)
            else:
                qty = int(risk_amount / entry_price)
        else:
            # No stop → size based on total risk as position value
            qty = int(risk_amount / entry_price)

        # Never exceed what we can afford
        max_affordable = int(equity / entry_price)
        return min(qty, max_affordable)


class KellyCriterionSizer(PositionSizer):
    """
    Kelly Criterion position sizing.

    Optimal fraction f* = (W * R - L) / R
    where:
        W = win rate
        L = loss rate (1 - W)
        R = avg_win / avg_loss (payoff ratio)

    We apply a fractional Kelly (default 0.5) to reduce volatility,
    as full Kelly is extremely aggressive.
    """

    def __init__(self, fraction: float = 0.5, max_pct: float = 0.25):
        """
        Args:
            fraction: Fraction of full Kelly to use (0.5 = half-Kelly)
            max_pct: Maximum % of equity in a single position (safety cap)
        """
        self.fraction = fraction
        self.max_pct = max_pct

    def calculate_qty(self, equity: float, entry_price: float,
                      win_rate: Optional[float] = None,
                      avg_win: Optional[float] = None,
                      avg_loss: Optional[float] = None, **kwargs) -> int:
        if entry_price <= 0 or equity <= 0:
            return 0

        # Default to conservative sizing if we don't have enough trade history
        if win_rate is None or avg_win is None or avg_loss is None or avg_loss == 0:
            # Fall back to 2% fixed fractional
            return FixedFractionalSizer(0.02).calculate_qty(equity, entry_price)

        # Calculate Kelly fraction
        payoff_ratio = avg_win / avg_loss
        loss_rate = 1.0 - win_rate
        kelly_f = (win_rate * payoff_ratio - loss_rate) / payoff_ratio

        # Apply fractional Kelly and safety bounds
        kelly_f = max(0.0, kelly_f) * self.fraction
        kelly_f = min(kelly_f, self.max_pct)

        position_value = equity * kelly_f
        qty = int(position_value / entry_price)

        max_affordable = int(equity / entry_price)
        return min(qty, max_affordable)


# ─── Stop-Loss Types ───────────────────────────────────────────────

class StopLossType(str, Enum):
    NONE = "none"
    FIXED_PCT = "fixed_pct"
    TRAILING = "trailing"


# ─── Risk Manager ──────────────────────────────────────────────────

class RiskManager:
    """
    Central risk management engine.

    Manages stop-losses, take-profits, position sizing, and portfolio-level
    circuit breakers. Integrates with Portfolio and Strategy.
    """

    def __init__(
        self,
        stop_loss_pct: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        max_drawdown_pct: float = 0.10,
        position_sizer: Optional[PositionSizer] = None,
    ):
        """
        Args:
            stop_loss_pct: Fixed stop-loss as decimal (e.g., 0.03 = 3% below entry)
            trailing_stop_pct: Trailing stop as decimal (e.g., 0.05 = 5% below peak)
            take_profit_pct: Take-profit as decimal (e.g., 0.05 = 5% above entry)
            max_drawdown_pct: Portfolio drawdown circuit breaker (e.g., 0.10 = 10%)
            position_sizer: Position sizing algorithm (defaults to FixedQuantity(100))
        """
        self.stop_loss_pct = stop_loss_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.take_profit_pct = take_profit_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.position_sizer = position_sizer or FixedQuantitySizer(100)

        # Track the highest price since entry for trailing stops
        # Format: { 'HK.00700': highest_price_float }
        self._peak_prices: Dict[str, float] = {}

        # Circuit breaker state
        self._trading_halted: bool = False

    def update_peak_price(self, symbol: str, current_price: float):
        """Update the peak price tracker for trailing stops."""
        if symbol in self._peak_prices:
            self._peak_prices[symbol] = max(self._peak_prices[symbol], current_price)
        else:
            self._peak_prices[symbol] = current_price

    def register_entry(self, symbol: str, entry_price: float):
        """Called when a new position is opened. Initializes peak tracking."""
        self._peak_prices[symbol] = entry_price

    def clear_position(self, symbol: str):
        """Called when a position is fully closed. Cleans up tracking."""
        self._peak_prices.pop(symbol, None)

    def check_exit_signals(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
    ) -> Optional[str]:
        """
        Check if any risk-based exit signal is triggered.

        Args:
            symbol: Stock symbol
            current_price: Current market price
            entry_price: Average entry price of the position

        Returns:
            Exit reason string ('stop_loss', 'trailing_stop', 'take_profit')
            or None if no exit signal.
        """
        if entry_price <= 0:
            return None

        # 1. Fixed stop-loss
        if self.stop_loss_pct is not None:
            stop_price = entry_price * (1.0 - self.stop_loss_pct)
            if current_price <= stop_price:
                return "stop_loss"

        # 2. Trailing stop
        if self.trailing_stop_pct is not None:
            peak = self._peak_prices.get(symbol, entry_price)
            trail_price = peak * (1.0 - self.trailing_stop_pct)
            if current_price <= trail_price:
                return "trailing_stop"

        # 3. Take-profit
        if self.take_profit_pct is not None:
            target_price = entry_price * (1.0 + self.take_profit_pct)
            if current_price >= target_price:
                return "take_profit"

        return None

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        win_rate: Optional[float] = None,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
    ) -> int:
        """
        Calculate position size using the configured sizing method.

        Also computes the stop price for fixed-fractional sizing.
        """
        stop_price = None
        if self.stop_loss_pct is not None:
            stop_price = entry_price * (1.0 - self.stop_loss_pct)

        return self.position_sizer.calculate_qty(
            equity=equity,
            entry_price=entry_price,
            stop_price=stop_price,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
        )

    def is_trading_halted(self, current_equity: float, initial_equity: float) -> bool:
        """
        Portfolio-level circuit breaker.

        Returns True if the portfolio has drawn down beyond the max allowed
        percentage from its initial value.
        """
        if initial_equity <= 0:
            return False

        drawdown = (initial_equity - current_equity) / initial_equity

        if drawdown >= self.max_drawdown_pct:
            if not self._trading_halted:
                self._trading_halted = True
            return True

        # Reset if we recover (optional — remove this block for permanent halt)
        self._trading_halted = False
        return False

    def get_stop_price(self, entry_price: float) -> Optional[float]:
        """Get the fixed stop-loss price for a given entry."""
        if self.stop_loss_pct is not None:
            return entry_price * (1.0 - self.stop_loss_pct)
        return None

    def get_take_profit_price(self, entry_price: float) -> Optional[float]:
        """Get the take-profit price for a given entry."""
        if self.take_profit_pct is not None:
            return entry_price * (1.0 + self.take_profit_pct)
        return None


# ─── Factory ───────────────────────────────────────────────────────

SIZER_REGISTRY = {
    SizingMethod.FIXED_QUANTITY: FixedQuantitySizer,
    SizingMethod.FIXED_FRACTIONAL: FixedFractionalSizer,
    SizingMethod.KELLY: KellyCriterionSizer,
}


def create_position_sizer(method: SizingMethod, **kwargs) -> PositionSizer:
    """Factory function to create a position sizer from config."""
    sizer_class = SIZER_REGISTRY[method]
    return sizer_class(**kwargs)
