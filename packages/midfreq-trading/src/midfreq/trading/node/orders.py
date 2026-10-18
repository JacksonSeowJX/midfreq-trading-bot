"""
The node's orders: submitting (refused while trading is off) and cancelling through the OMS,
waiting on an order's outcome with the rest of the queue held back (`wait_until`), cancelling
what is still working at shutdown, and a strategy's fills as the order journal has them.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from midfreq.core.model.events import OrderDenied, OrderEvent
from midfreq.core.model.identifiers import ClientOrderId, StrategyId
from midfreq.core.model.orders import Order
from midfreq.trading.core.interfaces import TOPIC_ORDER_EVENTS
from midfreq.trading.node.clock import NANOS
from midfreq.trading.ops.logs import get_logger

log = get_logger("node")


class OrdersMixin:
    """TradingNode's orders. Relies on the node's `state`, `host`, `cache`, `msgbus`, `oms`,
    `journal`, `config`, `queue`, `clock`, `test_clock` and `_stop_flag`; `_held` keeps the items
    taken off the queue while waiting on an order, for the loop, and `_pumping` stops a wait inside
    a wait. Calls `now_ns` and the loop's `_dispatch`."""

    # ── orders ───────────────────────────────────────────────────────────────────────────────
    def _submit(self, order: Order) -> None:
        if self.state != "running" or not self.host.trading:
            # Never reached by the adapters (they stop creating orders first); a guard all the same.
            log.error("order %s refused: trading is off", order.client_order_id)
            self.msgbus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id),
                                OrderDenied(order.client_order_id, order.strategy_id, order.instrument_id,
                                            self.now_ns(), reason="trading is off"))
            return
        self.cache.add_order(order)
        self.oms.submit(order)

    def _cancel(self, client_order_id: ClientOrderId) -> None:
        self.oms.cancel(client_order_id)

    def wait_until(self, predicate: Callable[[], bool], timeout_s: float,
                   on_timeout: Optional[Callable[[], None]] = None) -> bool:
        """Process order events (and the OMS's timers) until `predicate()` holds, holding every
        other item for later. After `timeout_s` calls `on_timeout` (a cancel) and waits up to the
        same again for its outcome. True if the predicate held."""
        if predicate():
            return True
        if self._pumping:
            return predicate()
        self._pumping = True
        try:
            deadline = self.now_ns() + int(timeout_s * NANOS)
            gave_up = False
            last_timer = self.now_ns()
            while True:
                self._pump_order_events()
                if predicate():
                    return True
                now = self.now_ns()
                if self._stop_flag and not gave_up and on_timeout is not None:
                    # A stop was requested (SIGTERM): cancel what is left now, and wait only a
                    # few seconds for the cancel's outcome.
                    log.warning("stop requested (%s) while waiting for an order: cancelling it", self._stop_flag)
                    on_timeout()
                    gave_up = True
                    deadline = min(deadline, now + 5 * NANOS)
                    continue
                if now >= deadline:
                    if gave_up or on_timeout is None:
                        return predicate()
                    on_timeout()
                    gave_up = True
                    deadline = now + int(max(timeout_s / 2.0, 5.0) * NANOS)
                    continue
                if self.test_clock:
                    self.clock.set_time(min(deadline, now + NANOS))
                    self.oms.on_timer(self.now_ns())
                else:
                    item = self.queue.get(timeout=0.2)
                    if item is not None:
                        if isinstance(item, OrderEvent):
                            self._dispatch(item)
                        else:
                            self._held.append(item)
                    now = self.now_ns()
                    if now - last_timer >= NANOS:
                        self.oms.on_timer(now)
                        last_timer = now
        finally:
            self._pumping = False

    def _pump_order_events(self) -> None:
        while True:
            item = self.queue.get(timeout=0)
            if item is None:
                return
            if isinstance(item, OrderEvent):
                self._dispatch(item)
            else:
                self._held.append(item)

    def _cancel_open_orders(self) -> None:
        try:
            open_orders = list(self.oms.open_orders())
        except Exception as exc:
            log.warning("cannot list open orders at shutdown: %s", exc)
            return
        if not open_orders:
            return
        log.warning("cancelling %d open orders before shutdown", len(open_orders))
        for o in open_orders:
            try:
                self.oms.cancel(o.client_order_id)
            except Exception as exc:
                log.error("cancel of %s failed: %s", o.client_order_id, exc)
        wait = float(self.config.node.stop_order_wait_s)

        def none_open():
            try:
                return not self.oms.open_orders()
            except Exception:
                return True
        if not self.wait_until(none_open, wait):
            log.error("%d orders still open at shutdown", len(self.oms.open_orders()))

    def _journal_fills(self, strategy_id: StrategyId) -> List[Dict[str, Any]]:
        """The journal's applied fills of one strategy (for fills its book never received)."""
        if self.journal is None or not callable(getattr(self.journal, "load", None)):
            return []
        state = self.journal.load(int(self.config.oms.get("journal_lookback_days", 30)))
        return [f for f in getattr(state, "fills", []) if f.get("strategy_id") == str(strategy_id)]
