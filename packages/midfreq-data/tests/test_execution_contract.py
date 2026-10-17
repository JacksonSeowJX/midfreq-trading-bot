"""
The execution contract: every ExecutionClient must pass these, each against a fake of its venue
played through the VenueDriver of tests/support/venue_driver.py (no network, no gateway).

Factories, one per venue (tests/support):
    make_moomoo_execution_client   fake_moomoo.py   (skipped until that module exists)
    make_ccxt_execution_client     fake_ccxt.py     (OKX; run twice: websocket updates and polling)
    make_sim_execution_client      fake_sim.py      (the simulated venue, midfreq.trading.testing)

Scenarios: accept then full fill; partial fills summing to the order; rejection with the venue's
reason; cancel acknowledged; cancel racing a fill (the fill first, then the cancel of the rest);
a refused cancel; a repeated push producing no second event; a disconnect mid-order, after which
open_orders() and fills(since) reconcile the order and the stream works again; the venue's order id
recorded; paper (or sim) by default and live refused without explicit configuration; events
reaching the handler only, the order itself left to the OMS.

Events may arrive on the client's own threads, so the suite waits for them (WAIT) and, to show
that nothing more comes, waits for a quiet spell (QUIET).

Conventions a factory must follow: it returns an unconnected client (the suite sets the handler
and connects) and accepts `environment='live'`, which must then fail at construction or connect.
"""
from __future__ import annotations

import importlib
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Tuple

import pandas as pd
import pytest

from midfreq.core.model.enums import OrderSide, OrderStatus
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderEvent, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import InstrumentId, StrategyId
from midfreq.core.model.orders import Order, OrderFactory
from midfreq.data.adapters.base import ConnectorError, ExecutionClient

WAIT = 5.0                 # seconds an expected event may take
QUIET = 0.25               # seconds without events that count as "nothing more comes"
SINCE = pd.Timestamp("2000-01-01", tz="UTC")
REFUSALS = (ConnectorError, ValueError, PermissionError, RuntimeError)


# ── the venues ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Market:
    instrument: str
    quantity: float
    parts: Tuple[float, float]          # two partial fills summing to quantity (exact in binary)
    price: float


US = Market("AAPL.XNAS", 10.0, (4.0, 6.0), 190.0)
HK = Market("00700.XHKG", 300.0, (100.0, 200.0), 400.0)
OKX = Market("BTC-USDT.OKX", 0.5, (0.125, 0.375), 60000.0)


def _moomoo():
    module = pytest.importorskip("fake_moomoo", reason="tests/support/fake_moomoo.py not merged yet")
    return module.make_moomoo_execution_client, {}


def _ccxt(updates: str):
    def load():
        pytest.importorskip("ccxt")
        return importlib.import_module("fake_ccxt").make_ccxt_execution_client, {"updates": updates}
    return load


def _sim():
    return importlib.import_module("fake_sim").make_sim_execution_client, {}


FACTORIES = {"moomoo": _moomoo, "ccxt-watch": _ccxt("watch"), "ccxt-poll": _ccxt("poll"), "sim": _sim}


def _market(name: str, client: ExecutionClient) -> Market:
    if name.startswith("ccxt"):
        return OKX
    if name == "moomoo":
        return HK if str(client.options.get("market", "US")).upper() == "HK" else US
    return US


# ── recording what the client emits ─────────────────────────────────

class Recorder:
    """The event handler: thread-safe, with waits."""

    def __init__(self):
        self.events: List[OrderEvent] = []
        self._cv = threading.Condition()
        self._last_ns = time.monotonic_ns()

    def __call__(self, event: OrderEvent) -> None:
        with self._cv:
            self.events.append(event)
            self._last_ns = time.monotonic_ns()
            self._cv.notify_all()

    def snapshot(self) -> List[OrderEvent]:
        with self._cv:
            return list(self.events)

    def of(self, *types) -> List[OrderEvent]:
        return [e for e in self.snapshot() if isinstance(e, types)]

    def wait_for(self, predicate: Callable[[List[OrderEvent]], bool], what: str, timeout: float = WAIT) -> None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while not predicate(self.events):
                left = deadline - time.monotonic()
                if left <= 0:
                    pytest.fail(f"timed out waiting for {what}; events so far: {self.events}")
                self._cv.wait(left)

    def wait_count(self, cls, n: int, what: str = "") -> None:
        self.wait_for(lambda ev: sum(isinstance(e, cls) for e in ev) >= n, what or f"{n} {cls.__name__}")

    def settle(self, quiet: float = QUIET, timeout: float = WAIT) -> None:
        """Wait until no event has arrived for `quiet` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cv:
                idle = (time.monotonic_ns() - self._last_ns) / 1e9
            if idle >= quiet:
                return
            time.sleep(quiet - idle)
        pytest.fail(f"events kept coming for {timeout}s: {self.snapshot()}")


@dataclass
class Venue:
    name: str
    client: ExecutionClient
    driver: object
    rec: Recorder
    market: Market
    factory: OrderFactory = field(default_factory=lambda: OrderFactory(StrategyId("CONTRACT-1")))

    def limit(self, side: OrderSide = OrderSide.BUY) -> Order:
        m = self.market
        return self.factory.limit(InstrumentId.from_str(m.instrument), side, m.quantity, m.price)

    def submit(self, order: Order) -> str:
        assert self.client.submit_order(order) is None
        return self.driver.last_order_id()

    def submit_accepted(self, side: OrderSide = OrderSide.BUY) -> Tuple[Order, str]:
        order = self.limit(side)
        accepted = len(self.rec.of(OrderAccepted))
        vid = self.submit(order)
        self.driver.accept(vid)
        self.rec.wait_count(OrderAccepted, accepted + 1, "OrderAccepted")
        return order, vid


@pytest.fixture(params=list(FACTORIES))
def venue(request):
    make, options = FACTORIES[request.param]()
    client, driver = make(**options)
    rec = Recorder()
    client.set_event_handler(rec)
    client.connect()
    try:
        yield Venue(request.param, client, driver, rec, _market(request.param, client))
    finally:
        client.disconnect()


def _about(order: Order, events: List[OrderEvent]) -> None:
    for e in events:
        assert e.client_order_id == order.client_order_id
        assert e.strategy_id == order.strategy_id
        assert e.instrument_id == order.instrument_id
        assert isinstance(e.ts_event, int) and e.ts_event > 0


def _kinds(events: List[OrderEvent]) -> List[str]:
    return [type(e).__name__ for e in events]


def _retry(fn, timeout: float = WAIT):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return fn()
        except ConnectorError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


# ── scenarios ────────────────────────────────────────────────────────

def test_accept_then_full_fill(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted()
    v.driver.fill(vid, m.quantity, m.price, "T-FULL-1")
    v.rec.wait_count(OrderFilled, 1)
    v.rec.settle()
    events = v.rec.snapshot()
    assert _kinds(events) == ["OrderAccepted", "OrderFilled"]
    _about(order, events)
    fill = events[1]
    assert str(fill.trade_id) == "T-FULL-1"
    assert fill.side is OrderSide.BUY
    assert fill.last_qty == pytest.approx(m.quantity) and fill.last_px == pytest.approx(m.price)
    assert fill.tags["venue_order_id"] == vid


def test_partial_fills_sum_to_the_order(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted(OrderSide.SELL)
    v.driver.fill(vid, m.parts[0], m.price, "T-PART-1")
    v.rec.wait_count(OrderFilled, 1)
    v.driver.fill(vid, m.parts[1], m.price + 1, "T-PART-2")
    v.rec.wait_count(OrderFilled, 2)
    v.rec.settle()
    events = v.rec.snapshot()
    assert _kinds(events) == ["OrderAccepted", "OrderFilled", "OrderFilled"]
    _about(order, events)
    fills = v.rec.of(OrderFilled)
    assert [str(f.trade_id) for f in fills] == ["T-PART-1", "T-PART-2"]
    assert [f.last_qty for f in fills] == pytest.approx(list(m.parts))
    assert sum(f.last_qty for f in fills) == pytest.approx(m.quantity)
    assert [f.last_px for f in fills] == pytest.approx([m.price, m.price + 1])
    assert all(f.side is OrderSide.SELL for f in fills)


def test_rejection_carries_the_venue_reason(venue):
    v = venue
    order = v.limit()
    vid = v.submit(order)
    v.driver.reject(vid, "insufficient buying power")
    v.rec.wait_count(OrderRejected, 1)
    v.rec.settle()
    events = v.rec.snapshot()
    assert _kinds(events) == ["OrderRejected"]
    _about(order, events)
    assert "insufficient buying power" in events[0].reason


def test_cancel_is_acknowledged(venue):
    v = venue
    order, vid = v.submit_accepted()
    assert v.client.cancel_order(order) is None
    v.driver.cancel_ack(vid)
    v.rec.wait_count(OrderCanceled, 1)
    v.rec.settle()
    events = v.rec.snapshot()
    assert _kinds(events) == ["OrderAccepted", "OrderCanceled"]
    _about(order, events)
    assert events[1].reason


def test_cancel_racing_a_fill_reports_the_fill_then_the_cancel(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted()
    v.client.cancel_order(order)
    v.driver.fill(vid, m.parts[0], m.price, "T-RACE-1")      # the fill reaches the venue first
    v.driver.cancel_ack(vid)                                 # then the cancel takes what is left
    v.rec.wait_count(OrderCanceled, 1)
    v.rec.settle()
    events = v.rec.snapshot()
    assert _kinds(events) == ["OrderAccepted", "OrderFilled", "OrderCanceled"]
    _about(order, events)
    assert str(events[1].trade_id) == "T-RACE-1" and events[1].last_qty == pytest.approx(m.parts[0])


def test_refused_cancel_leaves_the_order_working(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted()
    v.driver.refuse_cancel(vid, "order is being matched")
    assert v.client.cancel_order(order) is None              # no exception: the outcome is events
    v.rec.settle()
    assert not v.rec.of(OrderCanceled, OrderRejected)
    v.driver.fill(vid, m.quantity, m.price, "T-AFTER-REFUSAL")
    v.rec.wait_count(OrderFilled, 1)
    v.rec.settle()
    assert _kinds(v.rec.snapshot()) == ["OrderAccepted", "OrderFilled"]


def test_repeated_push_produces_no_duplicate_event(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted()
    v.driver.repeat_last_push()
    v.rec.settle()
    assert len(v.rec.of(OrderAccepted)) == 1
    v.driver.fill(vid, m.parts[0], m.price, "T-DUP-1")
    v.rec.wait_count(OrderFilled, 1)
    v.driver.repeat_last_push()
    v.driver.repeat_last_push()
    v.rec.settle()
    v.driver.fill(vid, m.parts[1], m.price, "T-DUP-2")
    v.rec.wait_count(OrderFilled, 2)
    v.driver.repeat_last_push()
    v.rec.settle()
    events = v.rec.snapshot()
    assert _kinds(events) == ["OrderAccepted", "OrderFilled", "OrderFilled"]
    assert [str(f.trade_id) for f in v.rec.of(OrderFilled)] == ["T-DUP-1", "T-DUP-2"]


def test_disconnect_mid_order_then_reconcile_after_reconnect(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted()
    v.driver.disconnect()
    v.driver.fill(vid, m.parts[0], m.price, "T-DARK-1")     # its push is lost
    time.sleep(0.05)
    v.driver.reconnect()

    mine = [r for r in _retry(v.client.open_orders) if r.order_id == vid]
    assert len(mine) == 1, "the order is still open at the venue"
    report = mine[0]
    assert order.tags["venue_order_id"] == report.order_id
    assert report.client_order_id in (None, str(order.client_order_id))
    assert report.filled_quantity == pytest.approx(m.parts[0])
    assert report.quantity == pytest.approx(m.quantity)

    fills = [f for f in _retry(lambda: v.client.fills(SINCE)) if f.order_id == vid]
    assert [f.trade_id for f in fills] == ["T-DARK-1"]
    assert fills[0].quantity == pytest.approx(m.parts[0]) and fills[0].price == pytest.approx(m.price)
    assert fills[0].client_order_id in (None, str(order.client_order_id))

    v.driver.fill(vid, m.parts[1], m.price, "T-LIGHT-2")    # the stream works again
    v.rec.wait_for(lambda ev: any(isinstance(e, OrderFilled) and str(e.trade_id) == "T-LIGHT-2" for e in ev),
                   "the fill after the reconnect")
    v.rec.settle()
    trade_ids = [str(f.trade_id) for f in v.rec.of(OrderFilled)]
    assert len(trade_ids) == len(set(trade_ids)), "a fill was reported twice"
    assert set(trade_ids) <= {"T-DARK-1", "T-LIGHT-2"} and "T-LIGHT-2" in trade_ids
    assert not v.rec.of(OrderCanceled, OrderRejected)
    assert not [r for r in v.client.open_orders() if r.order_id == vid], "filled orders are not open"


def test_venue_order_id_is_recorded(venue):
    v, m = venue, venue.market
    order = v.limit()
    vid = v.submit(order)
    assert order.tags.get("venue_order_id") == vid
    sent = v.driver.submitted()[-1]
    assert sent["symbol"]
    assert str(sent["side"]).upper() == "BUY"
    assert float(sent["quantity"]) == pytest.approx(m.quantity)
    assert float(sent["price"]) == pytest.approx(m.price)
    assert str(sent.get("order_type", sent.get("type"))).upper() == "LIMIT"
    v.driver.accept(vid)
    v.driver.fill(vid, m.quantity, m.price, "T-ID-1")
    v.rec.wait_count(OrderFilled, 1)
    assert v.rec.of(OrderFilled)[0].tags["venue_order_id"] == vid


def test_events_reach_the_handler_and_the_order_is_left_to_the_oms(venue):
    v, m = venue, venue.market
    order, vid = v.submit_accepted()
    v.driver.fill(vid, m.quantity, m.price, "T-H-1")
    v.rec.wait_count(OrderFilled, 1)
    v.rec.settle()
    # the client reports; applying events to the order is the OMS's job
    assert order.status is OrderStatus.INITIALIZED
    assert order.filled_qty == 0 and order.avg_px is None and order.commission == 0

    second = Recorder()
    v.client.set_event_handler(second)
    before = len(v.rec.snapshot())
    order2 = v.limit()
    vid2 = v.submit(order2)
    v.driver.accept(vid2)
    second.wait_count(OrderAccepted, 1)
    assert v.client.cancel_order(order2) is None
    v.driver.cancel_ack(vid2)
    second.wait_count(OrderCanceled, 1)
    second.settle()
    assert len(v.rec.snapshot()) == before, "events went to the replaced handler"
    assert _kinds(second.snapshot()) == ["OrderAccepted", "OrderCanceled"]
    _about(order2, second.snapshot())


def test_paper_by_default(venue):
    assert venue.client.environment in ("paper", "sim")
    assert venue.client.account_id is not None


@pytest.mark.parametrize("name", list(FACTORIES))
def test_live_is_refused_without_explicit_configuration(name, tmp_path, monkeypatch):
    cfg = tmp_path / "connections.yaml"
    cfg.write_text("moomoo:\n  trading_env: simulate\nccxt:\n  trading_env: paper\n")
    monkeypatch.setenv("MIDFREQ_CONNECTIONS", str(cfg))
    for var in [k for k in os.environ if k.startswith("MIDFREQ_") and k.endswith("_TRADING_ENV")]:
        monkeypatch.delenv(var)
    make, options = FACTORIES[name]()
    with pytest.raises(REFUSALS):
        client, _ = make(**options, environment="live")
        try:
            client.connect()
        finally:
            client.disconnect()
