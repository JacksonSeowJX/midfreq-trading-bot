"""
A simulated venue for the trading node: SimulatedExecutionClient and SimulatedDataClient.

Deterministic: time is the clock's (a TestClock in tests and replays), ids are counters, and
everything a real venue does later (latency, a later partial fill, the delivery of a recorded bar)
is a callback given to a scheduler, `schedule(at_ns, callback)`. SimScheduler is one, driven by a
TestClock: `advance_to(ns)` runs every callback due by `ns` in time order, moving the clock to each.

SimulatedExecutionClient (environment 'sim') fills
- a market order at its reference price (the decision candle's close), or the price source's
  price when it has none, moved by `slippage_bps` against the order (buy higher, sell lower);
- a limit order that is marketable when it arrives at the price source's price (its limit or
  better); one that is not rests and fills at its limit once the price reaches it (match(),
  on_bar() and on_quote() re-check resting orders);
- in slices when `partial_fills` gives a pattern (fractions of the order's quantity, the last slice
  taking what is left), `partial_fill_interval_ns` apart.
It rejects what `check_lot(order)` or `check_cash(order, price)` objects to (each returns a reason
or None), and a market order with no price at all. A cancel arriving after the order filled is
refused (the fill won) with OrderCancelRejected, as is one the venue was told to refuse
(venue_refuse_cancel); what a cancel arrives in time for is cancelled. venue_disconnect and
venue_reconnect are reported to the status handler ('disconnected', 'reconnected').

`auto=False` turns the matching off: the venue then only does what its venue-side methods
(venue_accept, venue_fill, venue_reject, venue_cancel, venue_refuse_cancel, ...) tell it, which is
how the execution contract suite plays the venue (tests/support/fake_sim.py).

Every event the venue produces is a push, and a push repeated (repeat_last_push) produces no
second event. While the link is down (venue_disconnect) pushes do not arrive, but open_orders()
and fills(since) still report the venue's state; when it returns (venue_reconnect) the client
catches up on what it missed, as the ccxt client's REST resync does, each event still only once.

SimulatedDataClient replays recorded bars (or BarUpdates, or QuoteTicks) to subscribers on the
scheduler, each at its own time: a Bar at its close plus `delay_ns`, a BarUpdate at its
ts_received, a QuoteTick at its ts_event. Listeners added with add_listener (a
SimulatedExecutionClient's on_bar / on_quote) see each item before the subscribers do, as the
backtest's exchange does.
"""
from __future__ import annotations

import heapq
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd

from midfreq.core.common.clock import Clock, TestClock
from midfreq.core.common.timestamps import ns_to_timestamp, to_utc_timestamp
from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.enums import LiquiditySide, OrderSide, OrderType
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderCancelRejected, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import AccountId, ClientId, InstrumentId, TradeId
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import (
    BarUpdate, ConnectorError, DataClient, ExecutionClient, FillReport, OrderReport,
)

log = logging.getLogger(__name__)

Scheduler = Callable[[int, Callable[[], None]], None]     # (due time, UTC ns; callback)
PriceSource = Callable[[InstrumentId], Optional[float]]

_EPS = 1e-9


class SimScheduler:
    """Callbacks at simulated times on a TestClock. Callbacks may schedule more callbacks."""

    def __init__(self, clock: TestClock):
        self.clock = clock
        self._heap: List[Tuple[int, int, Callable[[], None]]] = []
        self._seq = itertools.count()

    def __call__(self, at_ns: int, callback: Callable[[], None]) -> None:
        heapq.heappush(self._heap, (max(int(at_ns), self.clock.timestamp_ns()), next(self._seq), callback))

    schedule = __call__

    def next_time(self) -> Optional[int]:
        return self._heap[0][0] if self._heap else None

    def advance_to(self, ns: int) -> int:
        """Run every callback due by `ns` (time order, then the order scheduled), then leave the
        clock at `ns`. Returns how many ran."""
        ran = 0
        while self._heap and self._heap[0][0] <= ns:
            at, _, callback = heapq.heappop(self._heap)
            if at > self.clock.timestamp_ns():
                self.clock.set_time(at)
            callback()
            ran += 1
        if ns > self.clock.timestamp_ns():
            self.clock.set_time(ns)
        return ran

    def run(self, max_callbacks: int = 1_000_000) -> int:
        """Run everything scheduled, moving the clock along."""
        ran = 0
        while self._heap and ran < max_callbacks:
            ran += self.advance_to(self._heap[0][0])
        return ran

    def __len__(self) -> int:
        return len(self._heap)


def _immediate(clock: Clock) -> Scheduler:
    """A scheduler for zero latency: runs a callback at once if it is due, else refuses."""
    def schedule(at_ns: int, callback: Callable[[], None]) -> None:
        if at_ns > clock.timestamp_ns():
            raise ValueError("a delayed callback needs a scheduler (SimScheduler)")
        callback()
    return schedule


# ── the simulated execution venue ────────────────────────────────────────────────────────────

ACCEPTED, PARTIAL, FILLED, CANCELED, REJECTED, PENDING = (
    "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "PENDING")
_CLOSED = (FILLED, CANCELED, REJECTED)


@dataclass(eq=False)
class SimOrder:
    """The venue's record of one order."""
    order: Order
    venue_order_id: str
    created_ns: int
    status: str = PENDING           # PENDING until the venue has it and has accepted it
    arrived: bool = False
    filled: float = 0.0
    cost: float = 0.0
    updated_ns: int = 0
    rested: bool = False            # it did not fill on arrival: fills at its limit from now on
    filling: bool = False           # slices of a fill are scheduled
    cancel_requested: bool = False
    refuse_cancel: Optional[str] = None

    @property
    def leaves(self) -> float:
        return self.order.quantity - self.filled

    @property
    def is_open(self) -> bool:
        return self.status not in _CLOSED

    @property
    def avg_px(self) -> Optional[float]:
        return self.cost / self.filled if self.filled > 0 else None


@dataclass
class _Push:
    kind: str                       # accepted | filled | canceled | rejected | cancel_rejected
    venue_order_id: str
    data: Dict[str, Any] = field(default_factory=dict)


class SimulatedExecutionClient(ExecutionClient):
    """See the module docstring."""

    def __init__(self, clock: Clock, price_source: Optional[PriceSource] = None, *,
                 schedule: Optional[Scheduler] = None, latency_ns: int = 0, slippage_bps: float = 0.0,
                 partial_fills: Sequence[float] = (), partial_fill_interval_ns: int = 0,
                 quantity_step: Optional[Callable[[InstrumentId], float]] = None,
                 check_cash: Optional[Callable[[Order, float], Optional[str]]] = None,
                 check_lot: Optional[Callable[[Order], Optional[str]]] = None,
                 commission: Optional[Callable[[Order, float, float], float]] = None,
                 currency: Union[str, Callable[[InstrumentId], str]] = "",
                 auto: bool = True, account_id: str = "SIM-001", environment: str = "sim",
                 options: Optional[Dict[str, Any]] = None):
        super().__init__(None, options)
        if str(environment).lower() != "sim":
            raise ConnectorError(f"the simulated venue only runs in 'sim', not {environment!r}")
        if latency_ns < 0 or partial_fill_interval_ns < 0:
            raise ValueError("latencies cannot be negative")
        if any(f <= 0 for f in partial_fills):
            raise ValueError(f"partial-fill fractions must be positive: {list(partial_fills)}")
        if schedule is None and (latency_ns > 0 or (partial_fills and partial_fill_interval_ns > 0)):
            raise ValueError("latency or spaced partial fills need a scheduler (e.g. SimScheduler(clock))")
        self.clock = clock
        self._prices: Dict[InstrumentId, float] = {}
        self._price_source = price_source or self._prices.get
        self._schedule = schedule if schedule is not None else _immediate(clock)
        self.latency_ns = int(latency_ns)
        self.slippage_bps = float(slippage_bps)
        self.partial_fills = tuple(float(f) for f in partial_fills)
        self.partial_fill_interval_ns = int(partial_fill_interval_ns)
        self._quantity_step = quantity_step
        self._check_cash = check_cash
        self._check_lot = check_lot
        self._commission = commission
        self._currency = currency
        self.auto = bool(auto)
        self._account_id = AccountId(account_id)
        self._connected = False
        self._linked = True             # the push channel: False drops pushes (a disconnect)
        self._orders: Dict[str, SimOrder] = {}
        self._by_client_id: Dict[str, SimOrder] = {}
        self._fills: List[FillReport] = []
        self._order_ids = itertools.count(1)
        self._trade_ids = itertools.count(1)
        self._submitted: List[dict] = []
        self._last_push: Optional[_Push] = None
        self._missed: List[_Push] = []
        self._accepted_sent: set = set()
        self._ended_sent: set = set()
        self._trades_sent: set = set()
        self._refusal_ids = itertools.count(1)
        self._refusals_sent: set = set()

    # ── identity and session ─────────────────────────────────────────

    @property
    def client_id(self) -> ClientId:
        return ClientId("SIM")

    @property
    def environment(self) -> str:
        return "sim"

    @property
    def account_id(self) -> AccountId:
        return self._account_id

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    # ── the client side (ExecutionClient) ────────────────────────────

    def submit_order(self, order: Order) -> None:
        if not self._connected:
            raise ConnectorError("the simulated venue is not connected")
        if str(order.client_order_id) in self._by_client_id:
            log.warning("sim: %s was already sent; not sending it again", order.client_order_id)
            return
        venue_id = f"SIM-{next(self._order_ids)}"
        so = SimOrder(order, venue_id, self.clock.timestamp_ns(), updated_ns=self.clock.timestamp_ns())
        self._orders[venue_id] = so
        self._by_client_id[str(order.client_order_id)] = so
        order.tags["venue_order_id"] = venue_id
        self._at(self.clock.timestamp_ns() + self.latency_ns, lambda: self._arrive(so))

    def cancel_order(self, order: Order) -> None:
        if not self._connected:
            raise ConnectorError("the simulated venue is not connected")
        so = self._by_client_id.get(str(order.client_order_id))
        if so is None:
            log.warning("sim: cancel of unknown order %s ignored", order.client_order_id)
            return
        self._at(self.clock.timestamp_ns() + self.latency_ns, lambda: self._cancel_arrives(so))

    def query_order(self, order: Order) -> Optional[OrderReport]:
        so = self._by_client_id.get(str(order.client_order_id))
        return self._report(so) if so is not None and so.arrived else None

    def open_orders(self) -> List[OrderReport]:
        return [self._report(so) for so in self._orders.values() if so.arrived and so.is_open]

    def fills(self, since: pd.Timestamp) -> List[FillReport]:
        cutoff = to_utc_timestamp(since)
        return [f for f in self._fills if f.ts >= cutoff]

    # ── market data in (for matching) ────────────────────────────────

    def set_price(self, instrument_id: InstrumentId, price: float) -> None:
        """The internal price source's price (used when no price_source was given)."""
        self._prices[instrument_id] = float(price)

    def on_bar(self, bar: Bar) -> None:
        self.set_price(bar.instrument_id, bar.close)
        self.match(bar.instrument_id)

    def on_quote(self, quote: QuoteTick) -> None:
        self.set_price(quote.instrument_id, quote.mid)
        self.match(quote.instrument_id)

    def on_item(self, item: Any) -> None:
        """Listener for SimulatedDataClient: bars and quotes."""
        if isinstance(item, Bar):
            self.on_bar(item)
        elif isinstance(item, QuoteTick):
            self.on_quote(item)

    def match(self, instrument_id: Optional[InstrumentId] = None) -> None:
        """Offer every resting limit order (of one instrument) to the current price."""
        if not self.auto:
            return
        for so in list(self._orders.values()):
            if so.arrived and so.is_open and (instrument_id is None or so.order.instrument_id == instrument_id):
                self._match(so)

    # ── the venue's own logic ────────────────────────────────────────

    def _at(self, at_ns: int, callback: Callable[[], None]) -> None:
        """Run `callback` now if it is due (zero latency), else at `at_ns` on the scheduler."""
        if at_ns <= self.clock.timestamp_ns():
            callback()
        else:
            self._schedule(at_ns, callback)

    def _arrive(self, so: SimOrder) -> None:
        order = so.order
        so.arrived = True
        so.updated_ns = self.clock.timestamp_ns()
        self._submitted.append({
            "symbol": str(order.instrument_id), "side": order.side.value, "quantity": order.quantity,
            "price": order.price, "order_type": order.order_type.value,
            "client_order_id": str(order.client_order_id), "venue_order_id": so.venue_order_id})
        if not self.auto:
            return
        reason = self._check_lot(order) if self._check_lot is not None else None
        if reason is None and order.quantity <= 0:
            reason = f"quantity {order.quantity:g} is not positive"
        price = None
        if reason is None:
            price = self._fill_price_on_arrival(order)
            if order.order_type is OrderType.MARKET and price is None:
                reason = f"no price for {order.instrument_id}"
        if reason is None and self._check_cash is not None:
            estimate = price if order.order_type is OrderType.MARKET else order.price
            reason = self._check_cash(order, estimate)
        if reason is not None:
            return self.venue_reject(so.venue_order_id, reason)
        self.venue_accept(so.venue_order_id)
        if so.cancel_requested:
            return
        self._match(so, arriving=True)

    def _fill_price_on_arrival(self, order: Order) -> Optional[float]:
        if order.order_type is OrderType.MARKET:
            ref = order.reference_price if order.reference_price is not None else self._price_source(order.instrument_id)
            if ref is None:
                return None
            move = self.slippage_bps / 10_000.0
            return ref * (1 + move) if order.side is OrderSide.BUY else ref * (1 - move)
        return self._price_source(order.instrument_id)

    def _match(self, so: SimOrder, arriving: bool = False) -> None:
        if not so.is_open or so.status == PENDING or so.filling:
            return
        order = so.order
        if order.order_type is OrderType.MARKET:
            price = self._fill_price_on_arrival(order)
            if price is None:
                return
        else:
            market = self._price_source(order.instrument_id)
            if market is None:
                so.rested = True
                return
            crosses = market <= order.price + _EPS if order.side is OrderSide.BUY else market >= order.price - _EPS
            if not crosses:
                so.rested = True
                return
            price = order.price if so.rested and not arriving else market
            if order.side is OrderSide.BUY:
                price = min(price, order.price)
            else:
                price = max(price, order.price)
        self._fill_in_slices(so, price)

    def _slices(self, so: SimOrder) -> List[float]:
        leaves = so.leaves
        if not self.partial_fills:
            return [leaves]
        step = self._quantity_step(so.order.instrument_id) if self._quantity_step is not None else None
        out, left = [], leaves
        for fraction in self.partial_fills[:-1]:
            qty = so.order.quantity * fraction
            if step:
                qty = (int(qty / step + _EPS)) * step
            qty = min(qty, left)
            if qty > _EPS:
                out.append(qty)
                left -= qty
            if left <= _EPS:
                break
        if left > _EPS:
            out.append(left)
        return out

    def _fill_in_slices(self, so: SimOrder, price: float) -> None:
        slices = self._slices(so)
        so.filling = True             # one schedule of slices per order at a time
        now = self.clock.timestamp_ns()
        for i, qty in enumerate(slices):
            last = i == len(slices) - 1

            def fill(qty=qty, last=last):
                if so.is_open:
                    self.venue_fill(so.venue_order_id, min(qty, so.leaves), price)
                if last:
                    so.filling = False
            self._at(now + i * self.partial_fill_interval_ns, fill)

    def _cancel_arrives(self, so: SimOrder) -> None:
        if not so.is_open:
            log.info("sim: cancel of %s refused: already %s", so.order.client_order_id, so.status.lower())
            self._push(_Push("cancel_rejected", so.venue_order_id, {"reason": f"already {so.status.lower()}",
                                                                    "seq": next(self._refusal_ids)}))
            return
        if so.refuse_cancel is not None:
            log.info("sim: cancel of %s refused: %s", so.order.client_order_id, so.refuse_cancel)
            reason, so.refuse_cancel = so.refuse_cancel, None
            self._push(_Push("cancel_rejected", so.venue_order_id, {"reason": reason, "seq": next(self._refusal_ids)}))
            return
        so.cancel_requested = True
        if self.auto:
            self.venue_cancel(so.venue_order_id, "canceled by the client")

    # ── venue-side controls (the contract suite's driver, and tests) ─

    def venue_order(self, venue_order_id: str) -> SimOrder:
        return self._orders[venue_order_id]

    def last_order_id(self) -> str:
        return next(reversed(self._orders))

    def submitted(self) -> List[dict]:
        return [dict(s) for s in self._submitted]

    def venue_accept(self, venue_order_id: str) -> None:
        so = self._orders[venue_order_id]
        if so.status != PENDING:
            return
        so.status = ACCEPTED
        so.updated_ns = self.clock.timestamp_ns()
        self._push(_Push("accepted", venue_order_id))

    def venue_fill(self, venue_order_id: str, quantity: float, price: float, trade_id: Optional[str] = None,
                   liquidity: LiquiditySide = LiquiditySide.TAKER) -> None:
        so = self._orders[venue_order_id]
        if not so.is_open:
            raise ValueError(f"{venue_order_id} is {so.status}; it cannot fill")
        quantity = float(quantity)
        if quantity <= 0 or quantity > so.leaves + _EPS:
            raise ValueError(f"fill of {quantity:g} for {venue_order_id}, which has {so.leaves:g} left")
        order = so.order
        trade_id = trade_id or f"SIM-T{next(self._trade_ids)}"
        commission = float(self._commission(order, quantity, price)) if self._commission is not None else 0.0
        currency = self._currency(order.instrument_id) if callable(self._currency) else self._currency
        so.filled += quantity
        so.cost += quantity * price
        so.status = FILLED if so.leaves <= _EPS * max(1.0, order.quantity) else PARTIAL
        so.updated_ns = now = self.clock.timestamp_ns()
        self._fills.append(FillReport(
            trade_id=trade_id, order_id=venue_order_id, symbol=str(order.instrument_id), side=order.side.value,
            quantity=quantity, price=float(price), ts=ns_to_timestamp(now), commission=commission,
            currency=currency, client_order_id=str(order.client_order_id), instrument_id=str(order.instrument_id)))
        self._push(_Push("filled", venue_order_id, {
            "trade_id": trade_id, "quantity": quantity, "price": float(price), "commission": commission,
            "currency": currency, "liquidity": liquidity, "ts": now}))

    def venue_reject(self, venue_order_id: str, reason: str = "") -> None:
        so = self._orders[venue_order_id]
        if not so.is_open:
            return
        so.status = REJECTED
        so.updated_ns = self.clock.timestamp_ns()
        self._push(_Push("rejected", venue_order_id, {"reason": reason or "rejected"}))

    def venue_cancel(self, venue_order_id: str, reason: str = "canceled") -> None:
        """The venue cancels what is left: confirming the client's cancel, or on its own."""
        so = self._orders[venue_order_id]
        if not so.is_open:
            return
        so.status = CANCELED
        so.updated_ns = self.clock.timestamp_ns()
        self._push(_Push("canceled", venue_order_id, {"reason": reason or "canceled"}))

    def venue_refuse_cancel(self, venue_order_id: str, reason: str = "") -> None:
        self._orders[venue_order_id].refuse_cancel = reason or "cancel refused"

    def repeat_last_push(self) -> None:
        if self._last_push is not None:
            self._push(self._last_push)

    def venue_disconnect(self) -> None:
        self._linked = False
        self._status("disconnected", "sim: link to the venue lost", self.clock.timestamp_ns())

    def venue_reconnect(self) -> None:
        """The link returns; the client catches up on the pushes it missed, then reports
        'reconnected'."""
        self._linked = True
        missed, self._missed = self._missed, []
        for push in missed:
            self._deliver(push)
        self._status("reconnected", f"sim: link restored, {len(missed)} missed push(es) delivered",
                     self.clock.timestamp_ns())

    # ── pushes -> events ─────────────────────────────────────────────

    def _push(self, push: _Push) -> None:
        if not self._linked:
            self._missed.append(push)     # not delivered now; recovered at venue_reconnect
            return
        self._last_push = push
        self._deliver(push)

    def _deliver(self, push: _Push) -> None:
        so = self._orders[push.venue_order_id]
        order = so.order
        now = self.clock.timestamp_ns()
        key = push.venue_order_id
        if push.kind == "accepted":
            if key in self._accepted_sent or key in self._ended_sent:
                return
            self._accepted_sent.add(key)
            self._emit(OrderAccepted(order.client_order_id, order.strategy_id, order.instrument_id, now))
        elif push.kind == "filled":
            d = push.data
            if d["trade_id"] in self._trades_sent:
                return
            self._trades_sent.add(d["trade_id"])
            self._emit(OrderFilled(
                order.client_order_id, order.strategy_id, order.instrument_id, d["ts"],
                trade_id=TradeId(d["trade_id"]), side=order.side, last_qty=d["quantity"], last_px=d["price"],
                commission=d["commission"], currency=d["currency"], liquidity_side=d["liquidity"],
                tags={"venue_order_id": key}))
        elif push.kind == "cancel_rejected":
            if push.data["seq"] in self._refusals_sent:
                return
            self._refusals_sent.add(push.data["seq"])
            self._emit(OrderCancelRejected(order.client_order_id, order.strategy_id, order.instrument_id, now,
                                           reason=push.data.get("reason", "cancel refused")))
        else:
            if key in self._ended_sent:
                return
            self._ended_sent.add(key)
            cls = OrderRejected if push.kind == "rejected" else OrderCanceled
            self._emit(cls(order.client_order_id, order.strategy_id, order.instrument_id, now,
                           reason=push.data.get("reason", push.kind)))

    def _report(self, so: SimOrder) -> OrderReport:
        order = so.order
        return OrderReport(
            order_id=so.venue_order_id, symbol=str(order.instrument_id), side=order.side.value,
            quantity=order.quantity, filled_quantity=so.filled, price=order.price, average_fill_price=so.avg_px,
            status=so.status, created=ns_to_timestamp(so.created_ns), updated=ns_to_timestamp(so.updated_ns),
            client_order_id=str(order.client_order_id))


# ── the simulated data feed ──────────────────────────────────────────────────────────────────

Recorded = Union[Bar, BarUpdate, QuoteTick]


class SimulatedDataClient(DataClient):
    """Replays recorded bars and quotes to subscribers on a scheduler (see the module docstring)."""

    def __init__(self, items: Iterable[Recorded], clock: Clock, schedule: Optional[Scheduler] = None, *,
                 delay_ns: int = 0, closed: bool = True, history: Iterable[Bar] = ()):
        """`items` are what the session received (Bars, BarUpdates, QuoteTicks); `history` the
        bars before it, for warm-up through request_bars. Without `schedule` the client makes a
        SimScheduler on `clock` (a TestClock) and exposes it as `scheduler`."""
        super().__init__(None, now_ns=clock.timestamp_ns)
        self.clock = clock
        if schedule is None:
            if not isinstance(clock, TestClock):
                raise ValueError("without a scheduler the simulated data client needs a TestClock")
            schedule = SimScheduler(clock)
        self.scheduler = schedule
        self.delay_ns = int(delay_ns)
        self.closed = bool(closed)
        self._items: List[Recorded] = sorted(items, key=self._due)
        self._history: List[Bar] = list(history)
        self._bar_handlers: Dict[BarType, Callable[[BarUpdate], None]] = {}
        self._quote_handlers: Dict[InstrumentId, Callable[[QuoteTick], None]] = {}
        self._listeners: List[Callable[[Any], None]] = []
        self._last: Dict[InstrumentId, float] = {}
        self._delivered: List[Bar] = []
        self._scheduled: set = set()

    @property
    def client_id(self) -> ClientId:
        return ClientId("SIM")

    def _due(self, item: Recorded) -> int:
        if isinstance(item, BarUpdate):
            return item.ts_received
        if isinstance(item, Bar):
            return item.ts_event + self.delay_ns
        return item.ts_event + self.delay_ns

    def add_listener(self, listener: Callable[[Any], None]) -> None:
        """Called with every delivered Bar or QuoteTick before the subscribers (a simulated
        venue's on_item, so resting orders fill on the new price first)."""
        self._listeners.append(listener)

    def last_price(self, instrument_id: InstrumentId) -> Optional[float]:
        """The last delivered close (or quote mid): a price source for SimulatedExecutionClient."""
        return self._last.get(instrument_id)

    # ── live streams ─────────────────────────────────────────────────

    def subscribe_bars(self, bar_type: BarType, handler: Callable[[BarUpdate], None]) -> None:
        self._bar_handlers[bar_type] = handler
        self._schedule_matching(lambda item: _bar_of(item) is not None and _bar_of(item).bar_type == bar_type)
        self._stream_status("connected", f"{bar_type}")

    def unsubscribe_bars(self, bar_type: BarType) -> None:
        self._bar_handlers.pop(bar_type, None)

    def subscribe_quotes(self, instrument_id: InstrumentId, handler: Callable[[QuoteTick], None]) -> None:
        self._quote_handlers[instrument_id] = handler
        self._schedule_matching(lambda item: isinstance(item, QuoteTick) and item.instrument_id == instrument_id)
        self._stream_status("connected", f"{instrument_id}")

    def unsubscribe_quotes(self, instrument_id: InstrumentId) -> None:
        self._quote_handlers.pop(instrument_id, None)

    def subscription_limit(self) -> Optional[int]:
        return None

    def _schedule_matching(self, wanted: Callable[[Recorded], bool]) -> None:
        now = self.clock.timestamp_ns()
        for index, item in enumerate(self._items):
            due = self._due(item)
            if index in self._scheduled or due < now or not wanted(item):
                continue
            self._scheduled.add(index)
            self.scheduler(due, lambda item=item: self._deliver(item))

    def _deliver(self, item: Recorded) -> None:
        now = self.clock.timestamp_ns()
        if isinstance(item, QuoteTick):
            self._last[item.instrument_id] = item.mid
            for listener in self._listeners:
                listener(item)
            handler = self._quote_handlers.get(item.instrument_id)
            if handler is not None:
                handler(item)
            return
        update = item if isinstance(item, BarUpdate) else BarUpdate(item, self.closed, now)
        bar = update.bar
        self._last[bar.instrument_id] = bar.close
        if update.closed:
            self._delivered.append(bar)
        for listener in self._listeners:
            listener(bar)
        handler = self._bar_handlers.get(bar.bar_type)
        if handler is not None:
            handler(update)

    # ── history ──────────────────────────────────────────────────────

    def request_bars(self, bar_type: BarType, start, end=None) -> List[Bar]:
        """Closed bars of `bar_type` from the history and those delivered so far, by close time,
        in [start, end] and not after the clock."""
        start_ns = to_utc_timestamp(start).value
        end_ns = to_utc_timestamp(end).value if end is not None else self.clock.timestamp_ns()
        bars = [b for b in self._history + self._delivered if b.bar_type == bar_type]
        return self._normalise(bars, start_ns, end_ns)

    def _request_bars(self, bar_type, venue, start, end) -> List[Bar]:
        return self.request_bars(bar_type, start, end)


def _bar_of(item: Recorded) -> Optional[Bar]:
    if isinstance(item, BarUpdate):
        return item.bar
    return item if isinstance(item, Bar) else None
