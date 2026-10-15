"""
ExecutionEngine: the route from a strategy's submit_order to a venue and back, after
NautilusTrader's `execution.engine`.

submit:  Order -> RiskEngine.check -> (denied | SimulatedExchange.process_order)
events:  venue event -> order state -> Portfolio (fills) -> MessageBus 'events.order.<strategy>'

Everything is synchronous, so by the time submit_order returns in a fill-at-close backtest the
order is already filled or rejected and the account reflects it.
"""
from __future__ import annotations

from typing import Dict

from midfreq.core.model.enums import OrderStatus
from midfreq.core.model.events import (
    OrderAccepted, OrderCanceled, OrderDenied, OrderEvent, OrderFilled, OrderRejected, OrderSubmitted,
)
from midfreq.core.model.identifiers import Venue
from midfreq.core.model.orders import Order


class ExecutionEngine:
    def __init__(self, cache, portfolio, risk_engine, msgbus, clock):
        self._cache = cache
        self._portfolio = portfolio
        self._risk = risk_engine
        self._msgbus = msgbus
        self._clock = clock
        self.exchanges: Dict[Venue, object] = {}
        risk_engine.execution = self

    def submit(self, order: Order) -> None:
        self._cache.add_order(order)
        if not order.ts_init:
            order.ts_init = self._clock.timestamp_ns()
        self.handle(OrderSubmitted(order.client_order_id, order.strategy_id, order.instrument_id,
                                   self._clock.timestamp_ns()))
        reason = self._risk.check(order)
        if reason is None and order.venue not in self.exchanges:
            reason = f"no venue {order.venue} in this engine"
        if reason is not None:
            self._risk.denied += 1
            self.handle(OrderDenied(order.client_order_id, order.strategy_id, order.instrument_id,
                                    self._clock.timestamp_ns(), reason=reason))
            return
        self.exchanges[order.venue].process_order(order)

    def cancel(self, order: Order) -> None:
        exchange = self.exchanges.get(order.venue)
        if exchange is not None and order.is_open:
            exchange.cancel_order(order)

    def handle(self, event: OrderEvent) -> None:
        order = self._cache.order(event.client_order_id)
        if order is not None:
            self._apply(order, event)
        if isinstance(event, OrderFilled):
            self._portfolio.apply_fill(event)
            self._risk.on_fill(event)
        self._msgbus.publish(f"events.order.{event.strategy_id}", event)

    @staticmethod
    def _apply(order: Order, event: OrderEvent) -> None:
        order.ts_last = event.ts_event
        if isinstance(event, OrderSubmitted):
            order.status = OrderStatus.SUBMITTED
        elif isinstance(event, OrderAccepted):
            # The remainder of a partly filled order starts resting: it stays PARTIALLY_FILLED.
            order.status = OrderStatus.PARTIALLY_FILLED if order.filled_qty > 0 else OrderStatus.ACCEPTED
        elif isinstance(event, OrderDenied):
            order.status, order.reason = OrderStatus.DENIED, event.reason
        elif isinstance(event, OrderRejected):
            order.status, order.reason = OrderStatus.REJECTED, event.reason
        elif isinstance(event, OrderCanceled):
            order.status, order.reason = OrderStatus.CANCELED, event.reason
        elif isinstance(event, OrderFilled):
            if order.filled_qty == 0:
                order.avg_px = event.last_px
            else:
                total = order.filled_qty + event.last_qty
                order.avg_px = (order.avg_px * order.filled_qty + event.last_px * event.last_qty) / total
            order.filled_qty += event.last_qty
            order.commission += event.commission
            order.status = (OrderStatus.FILLED if order.filled_qty >= order.quantity
                            else OrderStatus.PARTIALLY_FILLED)
