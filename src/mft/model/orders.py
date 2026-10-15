"""
Orders and the OrderFactory, after NautilusTrader's `model.orders`.

In the original framework a strategy "traded" by calling `portfolio.execute_trade(...)`, which
decided and filled in one step. Here a strategy only creates an Order and submits it; the
RiskEngine may deny it, the venue may reject it, and fills arrive later as events. That separation
is what lets the same strategy run against a simulated or (later) a live venue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from mft.model.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from mft.model.identifiers import ClientOrderId, InstrumentId, StrategyId


@dataclass(slots=True)
class Order:
    client_order_id: ClientOrderId
    strategy_id: StrategyId
    instrument_id: InstrumentId
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None               # limit price (LIMIT orders only)
    time_in_force: TimeInForce = TimeInForce.GTC
    # The price the strategy decided at. Ignored by the default fill models; the legacy fill model
    # fills at it, because the original strategies passed their own price to execute_trade.
    reference_price: Optional[float] = None
    tags: Dict[str, Any] = field(default_factory=dict)
    ts_init: int = 0

    status: OrderStatus = OrderStatus.INITIALIZED
    filled_qty: float = 0.0
    avg_px: Optional[float] = None
    commission: float = 0.0
    reason: Optional[str] = None                # why it was denied, rejected or cancelled
    ts_last: int = 0

    @property
    def is_buy(self) -> bool:
        return self.side is OrderSide.BUY

    @property
    def venue(self):
        return self.instrument_id.venue

    @property
    def is_open(self) -> bool:
        return not self.status.is_closed

    @property
    def is_closed(self) -> bool:
        return self.status.is_closed

    @property
    def leaves_qty(self) -> float:
        return self.quantity - self.filled_qty

    def __repr__(self) -> str:
        px = f" @ {self.price}" if self.price is not None else ""
        return (f"Order({self.client_order_id}, {self.side.value} {self.quantity:g} {self.instrument_id}"
                f" {self.order_type.value}{px} {self.time_in_force.value}, {self.status.value})")


class OrderFactory:
    """Creates orders for one strategy with unique, readable ids ('O-XSReversal-000001')."""

    def __init__(self, strategy_id: StrategyId, clock: Callable[[], int] = lambda: 0):
        self.strategy_id = strategy_id
        self._clock = clock
        self._count = 0

    def _next_id(self) -> ClientOrderId:
        self._count += 1
        return ClientOrderId(f"O-{self.strategy_id}-{self._count:06d}")

    def market(self, instrument_id: InstrumentId, side: OrderSide, quantity: float,
               time_in_force: TimeInForce = TimeInForce.GTC,
               reference_price: Optional[float] = None,
               tags: Optional[Dict[str, Any]] = None) -> Order:
        return Order(self._next_id(), self.strategy_id, instrument_id, side, OrderType.MARKET,
                     float(quantity), None, time_in_force, reference_price, dict(tags or {}),
                     self._clock())

    def limit(self, instrument_id: InstrumentId, side: OrderSide, quantity: float, price: float,
              time_in_force: TimeInForce = TimeInForce.GTC,
              tags: Optional[Dict[str, Any]] = None) -> Order:
        return Order(self._next_id(), self.strategy_id, instrument_id, side, OrderType.LIMIT,
                     float(quantity), float(price), time_in_force, None, dict(tags or {}),
                     self._clock())
