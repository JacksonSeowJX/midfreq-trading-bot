"""
The Strategy API, after NautilusTrader's `trading.strategy`.

A strategy reacts to data and decides; it does not touch cash or positions. It subscribes to the
bars (or slices, or signals) it needs, and expresses decisions as orders built by its
`order_factory` and sent with `submit_order`. What happened to an order comes back as events
(`on_order_filled`, `on_order_rejected`, ...). The `cache` and `portfolio` it sees are read-only
views, so a strategy cannot, for example, give itself cash the way a core strategy could by
writing to `portfolio.cash`.

    class Momentum(Strategy):
        def on_start(self):
            self.subscribe_bars(self.bar_type)
        def on_bar(self, bar):
            if <entry rule> and self.portfolio.is_flat(bar.instrument_id):
                self.submit_order(self.order_factory.market(bar.instrument_id, OrderSide.BUY, 100))

For decisions that need every instrument at the same time (cross-sectional ranking), subscribe to
slices: `on_slice` runs once all bars with the same ts_event have been published, and an order
submitted there fills at that slice's closes. (A slice is LEAN's term; Nautilus leaves this to
user code.)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from mft.accounting import PortfolioView
from mft.cache import CacheView
from mft.model.data import Bar, BarType
from mft.model.enums import OrderSide
from mft.model.events import (
    OrderAccepted, OrderCanceled, OrderDenied, OrderEvent, OrderFilled, OrderRejected,
)
from mft.model.identifiers import InstrumentId, StrategyId, as_instrument_id
from mft.model.orders import Order, OrderFactory
from mft.model.signals import Signal


@dataclass
class StrategyConfig:
    strategy_id: Optional[str] = None     # default '<ClassName>-<order_id_tag>'
    order_id_tag: str = "001"


class Strategy:
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self.id = StrategyId(self.config.strategy_id or f"{type(self).__name__}-{self.config.order_id_tag}")
        self.log = logging.getLogger(f"mft.strategy.{self.id}")
        self._clock = None
        self._msgbus = None
        self._execution = None
        self._cache: Optional[CacheView] = None
        self._portfolio: Optional[PortfolioView] = None
        self.order_factory: Optional[OrderFactory] = None

    # ── wiring (called by the engine) ────────────────────────────────

    def register(self, *, clock, msgbus, cache, portfolio, execution) -> None:
        if self._msgbus is not None:
            raise RuntimeError(f"strategy {self.id} is already registered with an engine")
        self._clock = clock
        self._msgbus = msgbus
        self._execution = execution
        self._cache = CacheView(cache)
        self._portfolio = PortfolioView(portfolio)
        self.order_factory = OrderFactory(self.id, clock.timestamp_ns)
        msgbus.subscribe(f"events.order.{self.id}", self._handle_order_event)

    @property
    def clock(self):
        return self._clock

    @property
    def cache(self) -> CacheView:
        return self._cache

    @property
    def portfolio(self) -> PortfolioView:
        return self._portfolio

    # ── handlers to override ─────────────────────────────────────────

    def on_start(self) -> None:
        """Before the first bar: subscribe to data here."""

    def on_stop(self) -> None:
        """After the last bar."""

    def on_bar(self, bar: Bar) -> None:
        ...

    def on_slice(self, time_slice) -> None:
        """All bars with one ts_event (see mft.backtest.data_engine.TimeSlice)."""

    def on_signal(self, signal: Signal) -> None:
        ...

    def on_order_accepted(self, event: OrderAccepted) -> None:
        ...

    def on_order_filled(self, event: OrderFilled) -> None:
        ...

    def on_order_rejected(self, event: OrderRejected) -> None:
        """The venue refused the order (cash, lot size, minimum notional, position)."""

    def on_order_denied(self, event: OrderDenied) -> None:
        """The RiskEngine stopped the order before it reached the venue."""

    def on_order_canceled(self, event: OrderCanceled) -> None:
        ...

    def on_event(self, event: OrderEvent) -> None:
        """Every order event, after its specific handler."""

    # ── subscriptions and publishing ─────────────────────────────────

    def subscribe_bars(self, bar_type: "BarType | str") -> None:
        bt = bar_type if isinstance(bar_type, BarType) else BarType.from_str(bar_type)
        self._msgbus.subscribe(f"data.bars.{bt}", self._handle_bar)

    def subscribe_slices(self) -> None:
        self._msgbus.subscribe("data.slices", self._handle_slice)

    def subscribe_signals(self, name: str = "*") -> None:
        self._msgbus.subscribe(f"data.signals.{name}", self._handle_signal)

    def publish_signal(self, name: str, value: Any, instrument_id: Optional[InstrumentId] = None,
                       ts_event: Optional[int] = None) -> Signal:
        now = self._clock.timestamp_ns()
        signal = Signal(name, value, ts_event if ts_event is not None else now, now, instrument_id, str(self.id))
        self._msgbus.publish(signal.topic, signal)
        return signal

    # ── orders ───────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> Order:
        if order.strategy_id != self.id:
            raise ValueError(f"{self.id} cannot submit an order created for {order.strategy_id}")
        self._execution.submit(order)
        return order

    def cancel_order(self, order: Order) -> None:
        self._execution.cancel(order)

    def close_position(self, instrument_id: "InstrumentId | str", **tags) -> Optional[Order]:
        """Sell the whole long position in `instrument_id`, if there is one."""
        iid = as_instrument_id(instrument_id)
        qty = self.portfolio.net_qty(iid)
        if qty <= 0:
            return None
        return self.submit_order(self.order_factory.market(iid, OrderSide.SELL, qty, tags=tags))

    # ── dispatch ─────────────────────────────────────────────────────

    def _handle_bar(self, bar: Bar) -> None:
        self.on_bar(bar)

    def _handle_slice(self, time_slice) -> None:
        self.on_slice(time_slice)

    def _handle_signal(self, signal: Signal) -> None:
        self.on_signal(signal)

    def _handle_order_event(self, event: OrderEvent) -> None:
        if isinstance(event, OrderFilled):
            self.on_order_filled(event)
        elif isinstance(event, OrderRejected):
            self.on_order_rejected(event)
        elif isinstance(event, OrderDenied):
            self.on_order_denied(event)
        elif isinstance(event, OrderCanceled):
            self.on_order_canceled(event)
        elif isinstance(event, OrderAccepted):
            self.on_order_accepted(event)
        self.on_event(event)
