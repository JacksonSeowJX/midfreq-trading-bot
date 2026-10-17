"""Order events, after NautilusTrader's `model.events`. Events are immutable records of what
happened; the Order object holds the current state they produce."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from midfreq.core.model.enums import LiquiditySide, OrderSide
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId, TradeId


@dataclass(frozen=True, slots=True)
class OrderEvent:
    client_order_id: ClientOrderId
    strategy_id: StrategyId
    instrument_id: InstrumentId
    ts_event: int


@dataclass(frozen=True, slots=True)
class OrderSubmitted(OrderEvent):
    pass


@dataclass(frozen=True, slots=True)
class OrderAccepted(OrderEvent):
    """The venue accepted a resting order (a limit, or a market order waiting for the next bar)."""


@dataclass(frozen=True, slots=True)
class OrderDenied(OrderEvent):
    """The RiskEngine stopped the order before it reached the venue."""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OrderRejected(OrderEvent):
    """The venue refused the order: insufficient cash or position, below one lot, no market."""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OrderCanceled(OrderEvent):
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OrderCancelRejected(OrderEvent):
    """The venue refused a cancel (the order had already filled or ended, or is being matched).
    The order's state does not change: it is whatever the venue says next, or reports when asked."""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OrderFilled(OrderEvent):
    trade_id: TradeId = None
    side: OrderSide = OrderSide.BUY
    last_qty: float = 0.0
    last_px: float = 0.0
    commission: float = 0.0
    currency: str = ""
    liquidity_side: LiquiditySide = LiquiditySide.TAKER
    tags: Dict[str, Any] = field(default_factory=dict)

    @property
    def notional(self) -> float:
        return self.last_qty * self.last_px
