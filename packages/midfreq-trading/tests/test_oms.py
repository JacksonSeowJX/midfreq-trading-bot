"""OrderManagementSystem: ids, risk and routing, the state machine and its races, duplicate fills,
the unfilled-order policy on a test clock, reconciliation, and the journal across restarts
(midfreq.trading.oms). A small fake venue stands in for an ExecutionClient; its events go to a
list, as a connector's go to the node's queue, and `pump` plays the node's loop."""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import pytest

from midfreq.core.common.clock import TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model import (
    AccountId, ClientOrderId, InstrumentId, Order, OrderAccepted, OrderCanceled, OrderCancelRejected, OrderDenied,
    OrderFactory, OrderFilled, OrderRejected, OrderSide, OrderStatus, OrderSubmitted, OrderType, StrategyId, TradeId,
    Venue,
)
from midfreq.core.model.data import QuoteTick
from midfreq.data.adapters.base import ExecutionClient, FillReport, OrderReport
from midfreq.trading.journal import OrderJournal
from midfreq.trading.oms import (
    TRANSITIONS, OrderManagementSystem, Outcome, UnfilledOrderPolicy, transition, venue_status,
)
from midfreq.trading.positions import PositionLedger
from midfreq.trading.risk import PreTradeRiskEngine, RiskLimits

AAPL = InstrumentId.from_str("AAPL.XNAS")
LRCX = InstrumentId.from_str("LRCX.XNAS")
TENCENT = InstrumentId.from_str("00700.XHKG")
XNAS = Venue("XNAS")
S1 = StrategyId("S1")
T0 = pd.Timestamp("2026-10-15 14:00", tz="UTC").value
SEC = 1_000_000_000


# ── a fake venue ──────────────────────────────────────────────────────────────────────────────

class _Connector:
    name = "FAKE"


class FakeVenue(ExecutionClient):
    """Records what the OMS sends; the test plays the venue's side. Events go to `queue`."""

    def __init__(self, clock: TestClock):
        super().__init__(_Connector())
        self.clock = clock
        self.queue: List = []
        self.set_event_handler(self.queue.append)
        self.submitted: List[Order] = []
        self.cancels: List[ClientOrderId] = []
        self.book: Dict[str, dict] = {}          # venue order id -> state
        self.trades: List[FillReport] = []
        self.fail_submit: Optional[Exception] = None
        self.fail_cancel: Optional[Exception] = None
        self.forget: set = set()                 # venue ids query_order no longer knows
        self.queries: List[ClientOrderId] = []
        self._n = 0
        self._t = 0

    @property
    def environment(self):
        return "sim"

    @property
    def account_id(self):
        return AccountId("FAKE-1")

    def connect(self):
        pass

    def disconnect(self):
        pass

    def submit_order(self, order):
        if self.fail_submit is not None:
            raise self.fail_submit
        self._n += 1
        voi = f"V{self._n}"
        order.tags["venue_order_id"] = voi
        self.submitted.append(order)
        self.book[voi] = {"order": order, "status": "SUBMITTED", "filled": 0.0, "px": None}

    def cancel_order(self, order):
        self.cancels.append(order.client_order_id)
        if self.fail_cancel is not None:
            raise self.fail_cancel

    # the venue's side
    def _ev(self, cls, order, **kw):
        return cls(order.client_order_id, order.strategy_id, order.instrument_id, self.clock.timestamp_ns(), **kw)

    def accept(self, order):
        self.book[order.tags["venue_order_id"]]["status"] = "SUBMITTED"
        self._emit(self._ev(OrderAccepted, order))

    def fill(self, order, qty, px, trade_id=None, push=True, commission=0.0):
        voi = order.tags["venue_order_id"]
        self._t += 1
        trade_id = trade_id or f"D{self._t}"
        state = self.book[voi]
        state["filled"] += qty
        state["px"] = px
        if state["filled"] >= order.quantity:
            state["status"] = "FILLED_ALL"
        elif not state["status"].startswith("CANCELLED"):
            state["status"] = "FILLED_PART"
        self.trades.append(FillReport(trade_id, voi, f"US.{order.instrument_id.symbol}", order.side.value, qty, px,
                                      pd.Timestamp(self.clock.timestamp_ns(), unit="ns", tz="UTC"),
                                      commission, "USD", str(order.client_order_id)))
        if push:
            self._emit(self._ev(OrderFilled, order, trade_id=TradeId(trade_id), side=order.side, last_qty=float(qty),
                                last_px=float(px), commission=commission, currency="USD",
                                tags={"venue_order_id": voi}))

    def cancel_ack(self, order, push=True):
        state = self.book[order.tags["venue_order_id"]]
        state["status"] = "CANCELLED_PART" if state["filled"] else "CANCELLED_ALL"
        if push:
            self._emit(self._ev(OrderCanceled, order, reason=state["status"]))

    def refuse_cancel(self, order, reason="cancel refused: the order is being matched"):
        self._emit(self._ev(OrderCancelRejected, order, reason=reason))

    def reject(self, order, reason="FAILED: insufficient buying power"):
        self.book[order.tags["venue_order_id"]]["status"] = "FAILED"
        self._emit(self._ev(OrderRejected, order, reason=reason))

    def _report(self, voi):
        s = self.book[voi]
        o = s["order"]
        return OrderReport(voi, f"US.{o.instrument_id.symbol}", o.side.value, o.quantity, s["filled"], o.price,
                           s["px"], s["status"], pd.Timestamp(o.ts_init, unit="ns", tz="UTC"),
                           client_order_id=str(o.client_order_id))

    def query_order(self, order):
        self.queries.append(order.client_order_id)
        voi = order.tags.get("venue_order_id")
        if voi is None or voi not in self.book or voi in self.forget:
            return None
        return self._report(voi)

    def open_orders(self):
        return [self._report(v) for v, s in self.book.items()
                if v not in self.forget and venue_status(s["status"]) is None]

    def fills(self, since):
        return [t for t in self.trades if t.ts >= since]


# ── the node's parts, wired as the node will wire them ────────────────────────────────────────

class Node:
    def __init__(self, tmp_path, policy=None, limits=None, capital=1_000_000.0, quotes=None, clock=None,
                 venue=None, lot_size=None, tick_size=None, **oms_kw):
        self.tmp_path = tmp_path
        self.clock = clock or TestClock(T0)
        self.bus = MessageBus()
        self.published = []                                  # order events, every strategy
        self.bus.subscribe("events.order.*", self.published.append)
        self.ledger = PositionLedger(self.clock, self.bus, capital={"S1": capital})
        self.quotes = {} if quotes is None else quotes
        self.risk = PreTradeRiskEngine(self.clock, self.bus, self.ledger, limits or RiskLimits(),
                                       reference_price=lambda iid: _last(self.quotes.get(iid)),
                                       lot_size=lambda iid: 100.0 if iid.venue.value == "XHKG" else 1.0)
        self.journal = OrderJournal(tmp_path / "journal",
                                    wall_clock=lambda: datetime(2026, 10, 15, 14, tzinfo=timezone.utc))
        self.venue = venue or FakeVenue(self.clock)
        self.policy = policy or UnfilledOrderPolicy()
        self.oms = OrderManagementSystem(self.clock, self.bus, self.risk, self.ledger, self.journal,
                                         {XNAS: self.venue}, self.policy, price_source=self.quotes.get,
                                         lot_size=lot_size, tick_size=tick_size, **oms_kw)
        self.factory = OrderFactory(S1, self.clock.timestamp_ns)

    def pump(self):
        while self.venue.queue:
            self.oms.handle_event(self.venue.queue.pop(0))

    def advance(self, seconds):
        self.clock.set_time(self.clock.timestamp_ns() + int(seconds * SEC))
        self.oms.on_timer(self.clock.timestamp_ns())
        self.pump()

    def limit(self, qty=100, price=150.0, side=OrderSide.BUY, iid=AAPL, **kw):
        order = self.factory.limit(iid, side, qty, price, **kw)
        self.oms.submit(order)
        self.pump()
        return order

    def market(self, qty=100, side=OrderSide.BUY, iid=AAPL):
        order = self.factory.market(iid, side, qty, reference_price=150.0)
        self.oms.submit(order)
        self.pump()
        return order

    def events_of(self, cls):
        return [m for m in self.published if isinstance(m, cls)]

    def records(self):
        lines = []
        for path in sorted((self.tmp_path / "journal").glob("orders_*.jsonl")):
            lines += [json.loads(line) for line in path.read_text().splitlines()]
        return lines


def _last(q):
    if q is None:
        return None
    if isinstance(q, (int, float)):
        return float(q)
    return (q.bid + q.ask) / 2


def quote(bid, ask, iid=AAPL):
    return QuoteTick(iid, bid, ask, 100.0, 100.0, T0, T0)


@pytest.fixture
def node(tmp_path):
    return Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)})


# ── ids, risk and routing ─────────────────────────────────────────────────────────────────────

def test_submit_assigns_an_id_journals_first_and_routes_by_venue(node):
    order = node.limit()
    assert order.client_order_id == ClientOrderId("S1-20261015-0001")
    assert order.tags["strategy_order_id"] == "O-S1-000001"
    assert order.status is OrderStatus.SUBMITTED and order.ts_init == T0
    assert node.venue.submitted == [order] and order.tags["venue_order_id"] == "V1"
    assert node.oms.order(order.client_order_id) is order
    assert node.oms.open_orders(S1) == [order] and node.oms.open_orders(StrategyId("other")) == []
    submitted = node.events_of(OrderSubmitted)
    assert len(submitted) == 1 and submitted[0].client_order_id == order.client_order_id
    kinds = [r["kind"] for r in node.records()]
    assert kinds[:3] == ["order", "event", "sent"]          # written before the venue had it
    assert node.records()[2]["venue_order_id"] == "V1"
    assert node.records()[0]["time"].startswith("2026-10-15T14:00:00")


def test_an_id_in_the_oms_format_is_kept_and_the_sequence_moves_past_it(node):
    own = Order(ClientOrderId("S1-20261015-0007"), S1, AAPL, OrderSide.BUY, OrderType.LIMIT, 1.0, 150.0)
    node.oms.submit(own)
    assert own.client_order_id == ClientOrderId("S1-20261015-0007") and "strategy_order_id" not in own.tags
    assert node.limit().client_order_id == ClientOrderId("S1-20261015-0008")
    assert node.oms.next_order_id(S1) == ClientOrderId("S1-20261015-0009")
    reused = Order(ClientOrderId("S1-20261015-0007"), S1, AAPL, OrderSide.BUY, OrderType.LIMIT, 1.0, 150.0)
    node.oms.submit(reused)                                  # already used: renamed
    assert reused.client_order_id == ClientOrderId("S1-20261015-0010")


def test_the_same_order_submitted_twice_is_sent_once(node, caplog):
    order = node.limit()
    node.oms.submit(order)
    assert len(node.venue.submitted) == 1 and "submitted twice" in caplog.text


def test_ids_never_repeat_across_restarts_and_open_orders_come_back(tmp_path):
    first = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)})
    a = first.limit()
    b = first.limit()
    first.venue.fill(b, 100, 150.0)
    first.pump()
    first.journal.close()

    second = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)})
    assert second.oms.next_order_id(S1) == ClientOrderId("S1-20261015-0003")
    restored = second.oms.order(a.client_order_id)
    assert restored is not None and restored.status is OrderStatus.SUBMITTED
    assert restored.tags["venue_order_id"] == "V1"
    assert [o.client_order_id for o in second.oms.open_orders()] == [a.client_order_id]
    assert second.oms.order(b.client_order_id).status is OrderStatus.FILLED
    # a repeated push of b's fill after the restart is recognised
    second.oms.handle_event(OrderFilled(b.client_order_id, S1, AAPL, T0, trade_id=TradeId("D1"),
                                        side=OrderSide.BUY, last_qty=100.0, last_px=150.0))
    assert second.oms.order(b.client_order_id).filled_qty == 100.0


def test_a_denied_order_is_published_journaled_and_never_sent(node):
    node.risk.halt("kill switch test")
    order = node.limit()
    assert order.status is OrderStatus.DENIED and order.reason == "trading halted: kill switch test"
    assert node.venue.submitted == []
    denied = node.events_of(OrderDenied)
    assert len(denied) == 1 and denied[0].reason == order.reason
    records = node.records()
    assert [r["kind"] for r in records] == ["order", "event"]
    assert records[1]["event"]["event"] == "OrderDenied" and records[1]["order"]["status"] == "DENIED"
    assert node.oms.open_orders() == []


def test_an_order_for_a_venue_with_no_client_is_denied(node):
    order = node.limit(qty=100, price=400.0, iid=TENCENT)
    assert order.status is OrderStatus.DENIED and "no execution client for venue XHKG" in order.reason


def test_a_failed_submit_call_rejects_and_a_later_fill_still_counts(node, caplog):
    node.venue.fail_submit = TimeoutError("gateway timeout")
    order = node.limit()
    assert order.status is OrderStatus.REJECTED and order.reason == "submit failed: gateway timeout"
    # the venue had it after all
    node.venue.fail_submit = None
    node.oms.handle_event(OrderFilled(order.client_order_id, S1, AAPL, T0, trade_id=TradeId("X1"),
                                      side=OrderSide.BUY, last_qty=100.0, last_px=150.0))
    assert order.filled_qty == 100.0 and order.status is OrderStatus.FILLED
    assert node.ledger.position(S1, AAPL) == 100.0
    assert "the cancel and the fill crossed" in caplog.text


# ── the state machine ─────────────────────────────────────────────────────────────────────────

def test_accept_partial_fill_and_fill(node):
    order = node.limit()
    node.venue.accept(order)
    node.pump()
    assert order.status is OrderStatus.ACCEPTED
    node.venue.fill(order, 40, 150.0, commission=0.5)
    node.pump()
    assert order.status is OrderStatus.PARTIALLY_FILLED and order.leaves_qty == 60
    node.venue.fill(order, 60, 151.0, commission=0.5)
    node.pump()
    assert order.status is OrderStatus.FILLED and order.avg_px == pytest.approx(150.6)
    assert order.commission == 1.0 and node.oms.open_orders() == []
    fills = node.events_of(OrderFilled)
    assert [f.last_qty for f in fills] == [40.0, 60.0]
    assert node.ledger.position(S1, AAPL) == 100.0
    assert node.ledger.cash(S1) == pytest.approx(1_000_000 - 40 * 150.0 - 60 * 151.0 - 1.0)


def test_events_reach_the_strategys_topic_and_fills_the_shared_one(node):
    seen = {"S1": [], "fills": []}
    node.bus.subscribe("events.order.S1", seen["S1"].append)
    node.bus.subscribe("events.fills", seen["fills"].append)
    order = node.limit()
    node.venue.fill(order, 100, 150.0)
    node.pump()
    assert [type(e).__name__ for e in seen["S1"]] == ["OrderSubmitted", "OrderFilled"]
    assert len(seen["fills"]) == 1


def test_a_repeated_fill_push_is_applied_once(node):
    order = node.limit()
    node.venue.fill(order, 40, 150.0, trade_id="D7")
    node.venue.queue.append(node.venue.queue[-1])            # the venue pushes it again
    node.pump()
    assert order.filled_qty == 40.0 and node.ledger.position(S1, AAPL) == 40.0
    assert len(node.events_of(OrderFilled)) == 1


def test_a_fill_found_by_venue_order_id_alone_is_applied_to_its_order(node):
    order = node.limit()
    node.oms.handle_event(OrderFilled(ClientOrderId("venue-made-up"), S1, AAPL, T0, trade_id=TradeId("D1"),
                                      side=OrderSide.BUY, last_qty=10.0, last_px=150.0,
                                      tags={"venue_order_id": "V1"}))
    assert order.filled_qty == 10.0
    assert node.events_of(OrderFilled)[0].client_order_id == order.client_order_id


def test_cancel_fill_race_applies_the_fill_after_the_cancel(node, caplog):
    order = node.limit()
    node.oms.cancel(order.client_order_id)
    assert node.venue.cancels == [order.client_order_id]
    node.venue.cancel_ack(order)
    node.pump()
    assert order.status is OrderStatus.CANCELED and order.reason == "cancelled by request; venue: CANCELLED_ALL"
    with caplog.at_level(logging.WARNING):
        node.venue.fill(order, 30, 150.0)                    # the deal push arrives after the cancel
        node.pump()
    assert order.filled_qty == 30.0 and order.status is OrderStatus.CANCELED
    assert node.ledger.position(S1, AAPL) == 30.0
    assert "fill after CANCELED" in caplog.text
    node.venue.fill(order, 70, 150.0)                        # the fill completed it at the venue
    node.pump()
    assert order.status is OrderStatus.FILLED


def test_illegal_transitions_are_logged_as_errors_and_ignored(node, caplog):
    order = node.limit()
    node.venue.fill(order, 100, 150.0)
    node.pump()
    with caplog.at_level(logging.ERROR):
        node.oms.handle_event(OrderFilled(order.client_order_id, S1, AAPL, T0, trade_id=TradeId("NEW"),
                                          side=OrderSide.BUY, last_qty=5.0, last_px=150.0))
        node.oms.handle_event(OrderRejected(order.client_order_id, S1, AAPL, T0, reason="late"))
        node.oms.handle_event(OrderCanceled(order.client_order_id, S1, AAPL, T0, reason="late"))
    assert order.status is OrderStatus.FILLED and order.filled_qty == 100.0
    assert node.ledger.position(S1, AAPL) == 100.0
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "illegal transition" in r.getMessage()]
    assert len(errors) == 3
    journaled = [r for r in node.records() if r["kind"] == "event" and r["outcome"] == "illegal"]
    assert len(journaled) == 3 and not any(r["applied"] for r in journaled)


def test_a_late_acknowledgement_changes_nothing(node, caplog):
    order = node.limit()
    node.venue.fill(order, 100, 150.0)
    node.venue.accept(order)                                 # overtaken by the fill push
    with caplog.at_level(logging.INFO):
        node.pump()
    assert order.status is OrderStatus.FILLED
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_event_for_an_unknown_order_is_ignored(node, caplog):
    node.oms.handle_event(OrderFilled(ClientOrderId("nobody"), S1, AAPL, T0, trade_id=TradeId("Z"),
                                      side=OrderSide.BUY, last_qty=5.0, last_px=150.0))
    assert node.ledger.position(S1, AAPL) == 0 and "unknown order" in caplog.text
    assert node.records()[-1]["outcome"] == "unknown"


def test_the_transition_table():
    S = OrderStatus
    assert transition(S.SUBMITTED, OrderFilled(None, S1, AAPL, 0)) is Outcome.APPLY
    assert transition(S.CANCELED, OrderFilled(None, S1, AAPL, 0)) is Outcome.RACE
    assert transition(S.FILLED, OrderFilled(None, S1, AAPL, 0)) is Outcome.ILLEGAL
    assert transition(S.PARTIALLY_FILLED, OrderAccepted(None, S1, AAPL, 0)) is Outcome.STALE
    assert transition(S.DENIED, OrderAccepted(None, S1, AAPL, 0)) is Outcome.ILLEGAL
    # a closed order accepts no fill, except a cancelled or rejected one: the cancel crossed the
    # fill, or the rejection was a guess and the venue had the order (the venue's fill is the truth)
    for status in (S.FILLED, S.DENIED):
        assert TRANSITIONS.get((status, OrderFilled), Outcome.ILLEGAL) is Outcome.ILLEGAL
    assert transition(S.REJECTED, OrderFilled(None, S1, AAPL, 0)) is Outcome.RACE


def test_venue_status_words():
    assert venue_status("FILLED_ALL") is OrderStatus.FILLED and venue_status("closed") is OrderStatus.FILLED
    assert venue_status("CANCELLED_PART") is OrderStatus.CANCELED and venue_status("canceled") is OrderStatus.CANCELED
    assert venue_status("CANCELLING_ALL") is None and venue_status("FILLED_PART") is None
    assert venue_status("SUBMIT_FAILED") is OrderStatus.REJECTED and venue_status("rejected") is OrderStatus.REJECTED
    assert venue_status("SUBMITTED") is None and venue_status("open") is None and venue_status(None) is None


# ── the unfilled-order policy ─────────────────────────────────────────────────────────────────

def test_an_unfilled_limit_is_repriced_from_the_ask_and_fills(tmp_path):
    """The Lam Research case: a buy left unfilled 30 s is re-priced, not just cancelled."""
    node = Node(tmp_path, quotes={LRCX: quote(81.10, 81.20, LRCX)})
    order = node.limit(qty=60, price=81.00, iid=LRCX)
    node.advance(29)
    assert node.venue.cancels == []
    node.advance(2)                                          # 31 s: cancel to re-price
    assert node.venue.cancels == [order.client_order_id]
    assert len(node.venue.submitted) == 1                    # never two live orders for one intent
    node.venue.cancel_ack(order)
    node.pump()
    assert order.status is OrderStatus.CANCELED and order.reason.startswith("re-price 1/2")
    child = node.venue.submitted[-1]
    assert child is not order and child.price == 81.20 and child.quantity == 60
    assert child.tags["parent"] == str(order.client_order_id) and child.tags["root"] == str(order.client_order_id)
    assert child.tags["attempt"] == 2 and child.tags["strategy_order_id"] == "O-S1-000001"
    assert child.client_order_id == ClientOrderId("S1-20261015-0002")
    node.venue.fill(child, 60, 81.20)
    node.pump()
    assert node.ledger.position(S1, LRCX) == 60.0
    assert [o.client_order_id for o in node.oms.family(order.client_order_id)] == [order.client_order_id,
                                                                                   child.client_order_id]
    steps = [r.get("step") for r in node.records() if r["kind"] == "policy"]
    assert steps == ["repricing", "child", "done"]
    node.advance(60)
    assert len(node.venue.submitted) == 2 and len(node.venue.cancels) == 1


def test_a_partly_filled_order_is_repriced_for_what_is_left(node):
    order = node.limit()
    node.venue.fill(order, 40, 150.0)
    node.pump()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    child = node.venue.submitted[-1]
    assert child.quantity == 60 and child.price == 150.1


def test_after_the_last_reprice_the_rest_is_cancelled_with_the_reason(node):
    order = node.limit()
    for _ in range(2):
        node.advance(31)
        node.venue.cancel_ack(node.venue.submitted[-1])
        node.pump()
    assert len(node.venue.submitted) == 3
    last = node.venue.submitted[-1]
    assert last.tags["attempt"] == 3
    node.advance(31)
    assert node.venue.cancels[-1] == last.client_order_id
    node.venue.cancel_ack(last)
    node.pump()
    assert last.status is OrderStatus.CANCELED
    assert last.reason == "unfilled after 3 attempts; venue: CANCELLED_ALL"
    node.advance(120)
    assert len(node.venue.submitted) == 3                    # no fourth order
    done = [r for r in node.records() if r["kind"] == "policy" and r.get("step") == "done"]
    assert len(done) == 1 and "unfilled after 3 attempts" in done[0]["summary"]
    assert order.status is OrderStatus.CANCELED


def test_a_sell_is_repriced_below_the_bid_and_falls_back_to_the_last_price(tmp_path):
    quotes = {AAPL: 150.0}
    node = Node(tmp_path, quotes=quotes, policy=UnfilledOrderPolicy(quote_offset_bps=10.0, last_offset_bps=100.0),
                tick_size=lambda iid: 0.01)
    node.ledger.restore("S1", 1_000_000.0, {AAPL: (500.0, 140.0)})
    order = node.limit(qty=100, price=151.0, side=OrderSide.SELL)
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    assert node.venue.submitted[-1].price == 148.5           # no quote: last x (1 - 1%)
    quotes[AAPL] = quote(149.995, 150.2)
    node.advance(31)
    node.venue.cancel_ack(node.venue.submitted[-1])
    node.pump()
    # bid x (1 - 10 bp) = 149.845, a sell rounds down to the tick
    assert node.venue.submitted[-1].price == 149.84


def test_with_no_price_to_reprice_the_order_is_cancelled(tmp_path):
    node = Node(tmp_path, quotes={})
    node.risk._reference_price = lambda iid: None
    order = node.limit()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    assert len(node.venue.submitted) == 1
    assert order.reason.startswith("unfilled after 1 attempt (no quote or last price")


def test_an_unfilled_market_order_is_cancelled_after_the_wait(node):
    order = node.market()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    assert order.reason == "unfilled after 1 attempt; venue: CANCELLED_ALL"
    assert len(node.venue.submitted) == 1


def test_the_policy_can_be_switched_off(tmp_path):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)}, policy=UnfilledOrderPolicy(enabled=False))
    node.limit()
    node.advance(600)
    assert node.venue.cancels == []


def test_a_fill_the_venue_did_not_push_is_found_before_the_child_is_sized(node):
    order = node.limit()
    node.advance(31)
    node.venue.fill(order, 40, 150.0, push=False)            # filled, but the push is lost
    node.venue.cancel_ack(order)
    node.pump()
    assert order.filled_qty == 40.0                          # found by asking the venue
    assert node.venue.submitted[-1].quantity == 60
    assert node.ledger.position(S1, AAPL) == 40.0


def test_a_late_fill_on_a_replaced_order_resizes_the_child(tmp_path, caplog):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)},
                policy=UnfilledOrderPolicy(confirm_before_reprice=False))
    order = node.limit()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    child = node.venue.submitted[-1]
    assert child.quantity == 100
    node.venue.fill(order, 40, 150.0)                        # the parent's fill arrives after its cancel
    node.pump()
    assert order.filled_qty == 40.0 and node.venue.cancels[-1] == child.client_order_id
    node.venue.cancel_ack(child)
    node.pump()
    resized = node.venue.submitted[-1]
    assert resized.quantity == 60 and resized.tags["parent"] == str(child.client_order_id)
    assert resized.tags["attempt"] == 2                      # a resize is not another attempt
    node.venue.fill(resized, 60, 150.1)
    node.pump()
    assert node.ledger.position(S1, AAPL) == 100.0


def test_a_late_fill_that_completes_the_family_cancels_the_child(tmp_path):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)},
                policy=UnfilledOrderPolicy(confirm_before_reprice=False))
    order = node.limit()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    child = node.venue.submitted[-1]
    node.venue.fill(order, 100, 150.0)
    node.pump()
    assert node.venue.cancels[-1] == child.client_order_id
    node.venue.cancel_ack(child)
    node.pump()
    assert len(node.venue.submitted) == 2 and node.ledger.position(S1, AAPL) == 100.0


def test_a_fill_racing_the_reprice_cancel_ends_the_family(node):
    order = node.limit()
    node.advance(31)
    node.venue.fill(order, 100, 150.0)                       # filled before the cancel landed
    node.pump()
    assert order.status is OrderStatus.FILLED
    node.advance(60)
    assert len(node.venue.submitted) == 1


def test_an_unconfirmed_cancel_is_checked_with_the_venue(node):
    order = node.limit()
    node.advance(31)
    node.venue.cancel_ack(order, push=False)                 # cancelled, but the push never comes
    node.advance(29)
    assert node.venue.queries == []
    node.advance(2)
    assert node.venue.queries == [order.client_order_id]
    assert order.status is OrderStatus.CANCELED and "venue status CANCELLED_ALL" in order.reason
    assert node.venue.submitted[-1].quantity == 100 and len(node.venue.submitted) == 2
    replies = [r for r in node.records() if r["kind"] == "venue_reply"]
    assert replies and replies[-1]["reply"]["status"] == "CANCELLED_ALL"


def test_a_refused_cancel_is_resent_then_left_to_reconciliation(tmp_path, caplog):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)}, policy=UnfilledOrderPolicy(max_cancel_retries=2))
    node.venue.fail_cancel = RuntimeError("order is being filled")
    order = node.limit()
    node.advance(31)
    for _ in range(3):
        node.advance(31)
    assert len(node.venue.cancels) == 3                      # the first and two retries
    assert "still not confirmed after 2 retries" in caplog.text
    node.advance(120)
    assert len(node.venue.cancels) == 3 and order.is_open
    assert "order is being filled" in caplog.text                # the venue's reply is in the log


def test_a_cancel_by_the_strategy_stops_the_policy(node):
    order = node.limit()
    node.advance(31)                                         # re-price cancel on its way
    node.oms.cancel(order.client_order_id)
    node.venue.cancel_ack(order)
    node.pump()
    assert len(node.venue.submitted) == 1
    assert order.reason.startswith("cancelled by request")


def test_cancelling_the_first_order_cancels_its_working_child(node):
    order = node.limit()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    child = node.venue.submitted[-1]
    node.oms.cancel(order.client_order_id)
    assert node.venue.cancels[-1] == child.client_order_id
    node.venue.cancel_ack(child)
    node.pump()
    node.advance(120)
    assert len(node.venue.submitted) == 2


def test_a_child_goes_through_risk_again(node):
    order = node.limit()
    node.advance(31)
    node.risk.halt("stop")
    node.venue.cancel_ack(order)
    node.pump()
    child = node.oms.family(order.client_order_id)[-1]
    assert child is not order and child.status is OrderStatus.DENIED
    assert len(node.venue.submitted) == 1
    node.advance(120)
    assert len(node.venue.submitted) == 1


def test_a_child_quantity_is_kept_to_whole_lots(tmp_path):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)}, lot_size=lambda iid: 10.0)
    order = node.limit()
    node.venue.fill(order, 35, 150.0)                        # odd remainder 65
    node.pump()
    node.advance(31)
    node.venue.cancel_ack(order)
    node.pump()
    assert node.venue.submitted[-1].quantity == 60


# ── reconciliation ────────────────────────────────────────────────────────────────────────────

def test_reconcile_repairs_our_side_and_reports_the_rest(tmp_path):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)}, policy=UnfilledOrderPolicy(enabled=False))
    filled_quietly = node.limit()
    gone = node.limit()
    cancelled_quietly = node.limit()
    resting = node.limit()
    node.venue.fill(filled_quietly, 100, 150.0, push=False)
    node.venue.forget.add(gone.tags["venue_order_id"])
    node.venue.cancel_ack(cancelled_quietly, push=False)
    node.venue.queue.clear()
    # one order and one fill at the venue that are not ours (another session, a manual trade)
    stranger = Order(ClientOrderId("manual-1"), StrategyId("other"), AAPL, OrderSide.SELL, OrderType.LIMIT,
                     5.0, 160.0, ts_init=T0)
    stranger.tags["venue_order_id"] = "M1"
    node.venue.book["M1"] = {"order": stranger, "status": "SUBMITTED", "filled": 0.0, "px": None}
    node.venue.trades.append(FillReport("MD1", "M9", "US.AAPL", "BUY", 3.0, 150.0,
                                        pd.Timestamp(T0, unit="ns", tz="UTC")))
    node.clock.set_time(T0 + 60 * SEC)

    report = node.oms.reconcile()
    assert report.source == "orders"
    assert filled_quietly.status is OrderStatus.FILLED and node.ledger.position(S1, AAPL) == 100.0
    assert gone.status is OrderStatus.CANCELED and "no longer knows" in gone.reason
    assert cancelled_quietly.status is OrderStatus.CANCELED and "CANCELLED_ALL" in cancelled_quietly.reason
    assert resting.status is OrderStatus.ACCEPTED            # listed open at the venue
    assert node.oms.open_orders() == [resting]
    assert len(report.adjustments) == 4
    assert len(report.unexplained) == 2
    assert any("not in the journal" in u for u in report.unexplained)
    assert any("is not one of ours" in u for u in report.unexplained)
    # a second pass finds everything in order
    again = node.oms.reconcile()
    assert again.adjustments == () and again.matched == 2 and len(again.unexplained) == 2


def test_reconcile_leaves_orders_just_sent_alone(node):
    order = node.limit()
    node.venue.forget.add(order.tags["venue_order_id"])
    report = node.oms.reconcile()
    assert order.is_open and report.adjustments == ()


def test_reconcile_survives_a_venue_that_cannot_answer(node):
    order = node.limit()

    def broken(*a, **k):
        raise ConnectionError("gateway down")
    node.venue.fills = broken
    node.venue.open_orders = broken
    report = node.oms.reconcile()
    assert order.is_open and len(report.unexplained) == 2 and "gateway down" in report.unexplained[0]


# ── refused cancels (OrderCancelRejected), the fill key, families ────────────────────────────

def test_a_refused_cancel_changes_nothing_on_a_closed_order(node):
    S = OrderStatus
    for status in (S.SUBMITTED, S.ACCEPTED, S.PARTIALLY_FILLED):
        assert transition(status, OrderCancelRejected(None, S1, AAPL, 0)) is Outcome.APPLY
    for status in (S.FILLED, S.CANCELED, S.REJECTED, S.DENIED):
        assert transition(status, OrderCancelRejected(None, S1, AAPL, 0)) is Outcome.STALE
    order = node.limit()
    node.venue.fill(order, 100, 150.0)
    node.venue.refuse_cancel(order, "already filled")
    node.pump()
    assert order.status is OrderStatus.FILLED and node.events_of(OrderCancelRejected) == []


def test_a_refused_reprice_cancel_keeps_the_order_working_then_tries_again(node):
    """The venue refuses the re-price's cancel and still has the order working: the OMS asks it
    (fills, then the order), keeps the order as it is, and tries again a full wait later."""
    order = node.limit()
    node.advance(31)                                          # re-price: cancel sent
    assert node.venue.cancels == [order.client_order_id]
    node.venue.refuse_cancel(order)
    node.pump()
    assert order.status is OrderStatus.ACCEPTED or order.is_open
    assert node.venue.queries == [order.client_order_id]     # asked the venue
    assert [type(e).__name__ for e in node.events_of(OrderCancelRejected)] == ["OrderCancelRejected"]
    node.advance(29)
    assert len(node.venue.cancels) == 1                       # no cancel storm: the wait starts again
    node.advance(2)
    assert len(node.venue.cancels) == 2                       # the next attempt
    node.venue.cancel_ack(order)
    node.pump()
    child = node.venue.submitted[-1]
    assert child is not order and child.tags["root"] == str(order.client_order_id)
    assert not node.oms.family_done(order.client_order_id)
    node.venue.fill(child, 100, 150.1)
    node.pump()
    assert node.oms.family_done(order.client_order_id) and node.oms.family_done(child.client_order_id)


def test_a_refused_cancel_of_an_order_that_filled_unseen_applies_the_fill(node):
    """Moomoo, 5 October: the cancel is refused because the order has filled, whose push never
    came. The OMS's query after the refusal finds the fill and the family is done."""
    order = node.limit()
    node.advance(31)
    node.venue.fill(order, 100, 150.05, push=False)
    node.venue.refuse_cancel(order, "cannot cancel: FILLED_ALL")
    node.pump()
    assert order.status is OrderStatus.FILLED and order.filled_qty == 100
    assert node.ledger.position(S1, AAPL) == 100
    assert node.oms.family_done(order.client_order_id)
    node.advance(120)
    assert len(node.venue.submitted) == 1


def test_repeated_refusals_stop_the_policy_and_leave_the_order_working(tmp_path, caplog):
    node = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)}, policy=UnfilledOrderPolicy(max_cancel_retries=1))
    order = node.limit()
    for _ in range(2):
        node.advance(31)
        node.venue.refuse_cancel(order)
        node.pump()
    assert "refused 2 cancels" in caplog.text
    node.advance(300)
    assert len(node.venue.cancels) == 2 and order.is_open    # left to the strategy and reconciliation


def test_a_strategy_cancel_that_is_refused_is_checked_and_sent_again(node):
    order = node.limit()
    node.oms.cancel(order.client_order_id)
    node.venue.refuse_cancel(order)
    node.pump()
    assert order.is_open and len(node.venue.cancels) == 1
    node.advance(31)                                          # the cancel timeout: checked, re-sent
    assert len(node.venue.cancels) == 2 and node.venue.queries.count(order.client_order_id) == 2
    node.venue.cancel_ack(order)
    node.pump()
    assert order.status is OrderStatus.CANCELED and len(node.venue.submitted) == 1


def test_the_same_trade_id_on_two_orders_is_two_fills(node):
    """OKX numbers trades per instrument: two of our orders can each have a trade 1. The fill key
    is (venue, venue order id, trade id), so both are applied, and a repeat of either is not."""
    a = node.limit(qty=10)
    b = node.limit(qty=10)
    node.venue.fill(a, 10, 150.0, trade_id="1")
    node.venue.fill(b, 10, 150.0, trade_id="1")
    node.pump()
    assert a.status is OrderStatus.FILLED and b.status is OrderStatus.FILLED
    assert node.ledger.position(S1, AAPL) == 20
    node.venue._emit(node.venue._ev(OrderFilled, a, trade_id=TradeId("1"), side=a.side, last_qty=10.0,
                                    last_px=150.0, tags={"venue_order_id": a.tags["venue_order_id"]}))
    node.pump()
    assert node.ledger.position(S1, AAPL) == 20
    assert node.oms.fill_key(a, "1") == ("XNAS", a.tags["venue_order_id"], "1")


def test_fill_keys_survive_a_restart(tmp_path):
    first = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)})
    a = first.limit(qty=10)
    first.venue.fill(a, 4, 150.0, trade_id="1")
    first.pump()
    second = Node(tmp_path, quotes={AAPL: quote(149.9, 150.1)}, venue=first.venue)
    second.venue.queue.clear()
    restored = second.oms.order(a.client_order_id)
    second.oms.handle_event(OrderFilled(a.client_order_id, S1, AAPL, T0, trade_id=TradeId("1"), side=OrderSide.BUY,
                                        last_qty=4.0, last_px=150.0, tags={"venue_order_id": a.tags["venue_order_id"]}))
    assert restored.filled_qty == 4.0                         # the repeat after the restart is ignored


def test_a_fill_report_without_a_fee_is_marked_commission_unknown(node):
    order = node.limit(qty=10)
    node.venue.fill(order, 10, 150.0, push=False)
    node.venue.trades[-1] = FillReport(node.venue.trades[-1].trade_id, order.tags["venue_order_id"], "US.AAPL",
                                       "BUY", 10.0, 150.0, node.venue.trades[-1].ts, None, "",
                                       str(order.client_order_id))
    node.oms.reconcile()
    fill = node.events_of(OrderFilled)[-1]
    assert fill.tags["commission_unknown"] is True and fill.tags["source"] == "reconcile"


def test_policy_presets_and_settings():
    assert UnfilledOrderPolicy.from_config("original_engine") == UnfilledOrderPolicy(max_reprices=0, max_cancel_retries=0)
    assert UnfilledOrderPolicy.from_config({"max_reprices": 2}).max_reprices == 2
    assert UnfilledOrderPolicy.from_config(None) == UnfilledOrderPolicy()
    assert UnfilledOrderPolicy(max_reprices=2).budget_s() == 120.0
    with pytest.raises(ValueError, match="reprice_attempts"):
        UnfilledOrderPolicy.from_config({"reprice_attempts": 2})
    with pytest.raises(ValueError, match="preset"):
        UnfilledOrderPolicy.from_config("orignal_engine")


def test_h6_reconciling_positions_skips_instruments_with_orders_in_flight(node):
    """H6: the broker already shows a sell's fill that has not reached us; shrinking the claim to
    the broker now would turn that fill, when it arrives, into a short. An instrument with an
    order working is left alone until the order is done."""
    from midfreq.data.adapters.base import PositionReport
    node.ledger.restore("S1", 1_000_000.0, {AAPL: (100.0, 150.0)})
    order = node.limit(qty=100, price=150.0, side=OrderSide.SELL)
    node.venue.fill(order, 100, 150.0, push=False)             # filled at the broker; no push yet
    report = node.ledger.reconcile(XNAS, [PositionReport("US.AAPL", 0.0, None, None, None, None, "AAPL.XNAS")])
    assert node.ledger.position(S1, AAPL) == 100 and report.adjustments == ()
    node.venue._emit(node.venue._ev(OrderFilled, order, trade_id=TradeId("D-late"), side=OrderSide.SELL,
                                    last_qty=100.0, last_px=150.0, tags={"venue_order_id": order.tags["venue_order_id"]}))
    node.pump()
    assert node.ledger.position(S1, AAPL) == 0                 # flat, not short
    node.ledger.reconcile(XNAS, [])
    assert node.ledger.position(S1, AAPL) == 0
