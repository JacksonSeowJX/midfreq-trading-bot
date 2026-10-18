"""
Event-driven strategies on the node: `NativeStrategyRunner` runs one
midfreq.strategies.trading.Strategy with the node's clock, bus and cache, a portfolio of its own
fed by its fills, and the OMS as its execution. `LiveTimeSlice` is the cross-section it receives
on data.slices.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from midfreq.core.accounting import Portfolio as AccountingPortfolio
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.events import OrderDenied, OrderEvent, OrderFilled
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.model.orders import Order
from midfreq.trading.config.settings import StrategySettings
from midfreq.trading.core.interfaces import TOPIC_BAR, TOPIC_ORDER_EVENTS
from midfreq.trading.strategy.services import NodeServices


# ── event-driven strategies ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiveTimeSlice:
    """Every bar of one cross-section, for Strategy.on_slice (the live counterpart of
    midfreq.backtest.data_engine.TimeSlice)."""
    ts_event: int
    bars: Tuple[Bar, ...]
    data: Tuple[object, ...] = ()
    complete: bool = True

    def __iter__(self):
        return iter(self.bars)

    def __len__(self) -> int:
        return len(self.bars)

    def get(self, instrument_id: InstrumentId) -> Optional[Bar]:
        for bar in self.bars:
            if bar.bar_type.instrument_id == instrument_id:
                return bar
        return None

    @property
    def instrument_ids(self) -> List[InstrumentId]:
        return [b.bar_type.instrument_id for b in self.bars]


class _NativeExecution:
    """What a native Strategy calls submit/cancel on: the OMS, while trading is on."""

    def __init__(self, runner: "NativeStrategyRunner"):
        self.runner = runner

    def submit(self, order: Order) -> None:
        r = self.runner
        if not r.trading:
            event = OrderDenied(order.client_order_id, order.strategy_id, order.instrument_id,
                                r.services.clock.timestamp_ns(), reason="trading is off (warm-up or shutdown)")
            r.services.msgbus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id), event)
            return
        r.orders_sent += 1
        r.services.submit(order)

    def cancel(self, order: Order) -> None:
        self.runner.services.cancel(order.client_order_id)


class NativeStrategyRunner:
    """Runs one midfreq.strategies.trading.Strategy: registered with the node's clock, bus and
    cache, a portfolio of its own fed by its fills, and the OMS as its execution."""

    kind = "native"

    def __init__(self, strategy: Any, settings: StrategySettings, services: NodeServices,
                 instrument_ids: Sequence[InstrumentId], bar_spec: BarSpecification, currency: str):
        self.strategy = strategy
        self.settings = settings
        self.services = services
        self.instrument_ids = list(instrument_ids)
        self.bar_spec = bar_spec
        self.currency = currency
        self.id = strategy.id
        self.portfolio = AccountingPortfolio(services.cache)
        if settings.capital:
            self.portfolio.fund(currency, settings.capital)
        self.trading = False
        self.orders_sent = 0
        self.phase = "created"
        self.log = logging.getLogger(f"midfreq.strategy.{self.id}")

    def register(self) -> None:
        bus = self.services.msgbus
        # The portfolio first, so the strategy sees its fill already applied.
        bus.subscribe(TOPIC_ORDER_EVENTS.format(strategy_id=self.id), self._on_order_event)
        self.strategy.register(clock=self.services.clock, msgbus=bus, cache=self.services.cache,
                               portfolio=self.portfolio, execution=_NativeExecution(self))

    def _on_order_event(self, event: OrderEvent) -> None:
        if isinstance(event, OrderFilled):
            fill = event
            if not fill.currency:
                import dataclasses
                fill = dataclasses.replace(fill, currency=self.currency)
            self.portfolio.apply_fill(fill)

    def start(self) -> None:
        self.strategy.on_start()

    def warm_up(self, count: int) -> int:
        """Publish the stored bars on the bar-type topics the strategy subscribed to, with
        trading off."""
        self.phase = "warmup"
        fed = 0
        bars: List[Bar] = []
        for iid in self.instrument_ids:
            try:
                bars += self.services.warm_up(BarType(iid, self.bar_spec), count) if count > 0 else []
            except Exception as exc:
                self.log.warning("[warmup] %s: no history (%s)", iid, exc)
        for bar in sorted(bars, key=lambda b: b.ts_event):
            self.services.cache.add_bar(bar)
            self.services.msgbus.publish(TOPIC_BAR.format(bar_type=bar.bar_type), bar)
            fed += 1
        self.phase = "live"
        return fed

    def trading_on(self) -> None:
        self.trading = True

    def trading_off(self) -> None:
        self.trading = False
        self.phase = "stopping"

    def stop(self) -> None:
        try:
            self.strategy.on_stop()
        finally:
            self.phase = "stopped"
