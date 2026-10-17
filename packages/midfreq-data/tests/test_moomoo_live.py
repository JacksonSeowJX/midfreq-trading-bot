"""
The Moomoo connector's live side against the fake OpenD in tests/support/fake_moomoo.py (no
network, no OpenD): the execution client (status mapping, fills, cancels, duplicate pushes, the
poll safety net, the remark round trip, paper unless configured) and the live streams (bar times,
quotes, quota, minimum subscription time, reconnects).
"""
import time

import pandas as pd
import pytest

from fake_moomoo import FakeMoomoo, ORDER_PUSH_COLUMNS, make_moomoo_connector, make_moomoo_execution_client
from midfreq.core.model.data import BarSpecification, BarType
from midfreq.core.model.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId
from midfreq.core.model.instruments import Equity
from midfreq.core.model.orders import Order
from midfreq.core.venues.spec import default_venues
from midfreq.data.adapters.base import ConnectorError, QuotaExceededError, UnsupportedIntervalError
from midfreq.data.adapters.moomoo.execution import ORDER_STATUS, MoomooExecutionClient, remark_for
from midfreq.data.adapters.moomoo.streaming import kline_close_ns

AAPL = InstrumentId.from_str("AAPL.XNAS")
MSFT = InstrumentId.from_str("MSFT.XNAS")
TENCENT = InstrumentId.from_str("00700.XHKG")
_SEQ = iter(range(1, 10**6))


@pytest.fixture(autouse=True)
def _paper_by_default(monkeypatch):
    """Whatever this machine's environment says, the tests start from connections.yaml (simulate)."""
    monkeypatch.delenv("MIDFREQ_MOOMOO_TRADING_ENV", raising=False)


def order(qty=10.0, price=100.0, side=OrderSide.BUY, iid=AAPL, order_type=OrderType.LIMIT,
          tif=TimeInForce.DAY, cid=None) -> Order:
    return Order(ClientOrderId(cid or f"O-TEST-{next(_SEQ):06d}"), StrategyId("TEST-1"), iid, side, order_type,
                 float(qty), price if order_type is OrderType.LIMIT else None, tif)


def setup(**options):
    client, venue = make_moomoo_execution_client(**options)
    events = []
    client.set_event_handler(events.append)
    return client, venue, events


def kinds(events):
    return [type(e).__name__ for e in events]


def ns(when: str) -> int:
    return int(pd.Timestamp(when, tz="UTC").value)


# ── the status table ─────────────────────────────────────────────────

FRESH = {   # what each OpenD status emits for an order not yet accepted
    "N/A": [], "UNSUBMITTED": [], "WAITING_SUBMIT": [], "SUBMITTING": [], "TIMEOUT": [],
    "SUBMIT_FAILED": ["OrderRejected"], "FAILED": ["OrderRejected"],
    "SUBMITTED": ["OrderAccepted"], "FILLED_PART": ["OrderAccepted"], "FILLED_ALL": ["OrderAccepted"],
    "CANCELLING_PART": ["OrderAccepted"], "CANCELLING_ALL": ["OrderAccepted"],
    "CANCELLED_PART": ["OrderAccepted", "OrderCanceled"], "CANCELLED_ALL": ["OrderAccepted", "OrderCanceled"],
    "DISABLED": ["OrderRejected"], "DELETED": ["OrderRejected"], "FILL_CANCELLED": ["OrderCanceled"],
}


def test_status_table_covers_every_sdk_status():
    moomoo = pytest.importorskip("moomoo")
    sdk_values = {v for k, v in vars(moomoo.OrderStatus).items() if k.isupper() and isinstance(v, str)}
    assert sdk_values == set(ORDER_STATUS) == set(FRESH)
    import fake_moomoo
    fake_values = {v for k, v in vars(fake_moomoo.OrderStatus).items() if k.isupper()}
    assert fake_values == sdk_values


@pytest.mark.parametrize("status", sorted(FRESH))
def test_each_status_emits_its_event(status):
    client, venue, events = setup()
    client.submit_order(order())
    venue.set_status(venue.last_order_id(), status, last_err_msg="exchange says so")
    assert kinds(events) == FRESH[status]
    terminal = [e for e in events if type(e).__name__ in ("OrderRejected", "OrderCanceled")]
    for e in terminal:
        assert e.reason.startswith(status) and "exchange says so" in e.reason


@pytest.mark.parametrize("status,expected", [
    ("DISABLED", "OrderCanceled"), ("DELETED", "OrderCanceled"), ("FAILED", "OrderCanceled"),
    ("CANCELLED_ALL", "OrderCanceled"), ("TIMEOUT", None), ("SUBMITTING", None),
])
def test_status_after_acceptance(status, expected):
    client, venue, events = setup()
    client.submit_order(order())
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.set_status(oid, status)
    assert kinds(events) == ["OrderAccepted"] + ([expected] if expected else [])


def test_reject_carries_the_venue_status_and_message():
    client, venue, events = setup()
    client.submit_order(order())
    venue.reject(venue.last_order_id(), "Insufficient buying power")
    (e,) = events
    assert kinds(events) == ["OrderRejected"] and e.reason == "FAILED: Insufficient buying power"


# ── submitting ───────────────────────────────────────────────────────

def test_a_limit_order_goes_out_as_given():
    client, venue, events = setup()
    o = order(qty=10, price=101.5)
    client.submit_order(o)
    (_, call), = [(m, kw) for m, kw in venue.calls if m == "place_order"]
    assert call == dict(price=101.5, qty=10.0, code="US.AAPL", trd_side="BUY", order_type="NORMAL",
                        trd_env="SIMULATE", acc_id=0, remark=str(o.client_order_id), time_in_force="DAY")
    assert o.tags["venue_order_id"] == venue.last_order_id()
    (sub,) = venue.submitted()
    assert (sub["symbol"], sub["side"], sub["quantity"], sub["price"], sub["order_type"], sub["client_order_id"]) == \
        ("US.AAPL", "BUY", 10.0, 101.5, "LIMIT", str(o.client_order_id))
    assert events == []   # SUBMITTING: nothing to say yet


def test_a_market_order_carries_no_price():
    client, venue, events = setup()
    client.submit_order(order(side=OrderSide.SELL, order_type=OrderType.MARKET, tif=TimeInForce.GTC))
    (sub,) = venue.submitted()
    assert (sub["side"], sub["order_type"], sub["venue_order_type"], sub["price"], sub["time_in_force"]) == \
        ("SELL", "MARKET", "MARKET", 0.0, "GTC")


@pytest.mark.parametrize("bad,why", [
    (dict(price=None), "limit order without a valid price"),
    (dict(qty=10.5), "not a whole positive number"),
    (dict(qty=0), "not a whole positive number"),
    (dict(tif=TimeInForce.IOC), "no IOC orders"),
    (dict(iid=TENCENT), "not on the US market"),
])
def test_orders_the_venue_cannot_take_are_refused_without_sending(bad, why):
    client, venue, events = setup()
    o = order(**bad)
    if "price" in bad:
        o.price = None
    client.submit_order(o)
    assert kinds(events) == ["OrderRejected"] and why in events[0].reason
    assert "place_order" not in venue.methods_called()


def test_hong_kong_quantities_must_be_whole_board_lots():
    client, venue, events = setup(market="HK")
    client.connector.instrument_provider._cache[TENCENT] = Equity(
        id=TENCENT, raw_symbol="00700", currency="HKD", lot_size=100.0)
    client.submit_order(order(qty=150, iid=TENCENT))
    assert kinds(events) == ["OrderRejected"] and "board lots (100)" in events[0].reason
    client.submit_order(order(qty=200, iid=TENCENT))
    assert venue.submitted()[-1]["symbol"] == "HK.00700" and venue.submitted()[-1]["quantity"] == 200


def test_place_order_errors():
    client, venue, events = setup()
    venue.fail_next_place("Insufficient buying power")
    client.submit_order(order())
    assert kinds(events) == ["OrderRejected"] and events[0].reason == "Insufficient buying power"
    venue.disconnect()
    client.submit_order(order())   # never left the process: cannot exist at the venue
    assert kinds(events)[-1] == "OrderRejected" and events[-1].reason == "not sent: Connect timeout"


def test_submitting_the_same_order_twice_is_an_error():
    client, venue, events = setup()
    o = order()
    client.submit_order(o)
    with pytest.raises(ConnectorError, match="already submitted"):
        client.submit_order(o)
    assert len(venue.submitted()) == 1


def test_not_connected_is_a_rejection():
    client, venue, events = setup(connect=False)
    client.submit_order(order())
    assert kinds(events) == ["OrderRejected"] and "not connected" in events[0].reason


def test_opend_down_refuses_to_connect():
    venue = FakeMoomoo()
    venue.opend_up = False
    with pytest.raises(ConnectorError, match="not reachable"):
        make_moomoo_execution_client(venue=venue)


# ── fills ────────────────────────────────────────────────────────────

def test_partial_fills_one_event_per_deal():
    client, venue, events = setup()
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.fill(oid, 4, 100.0, trade_id="D-1")
    venue.fill(oid, 6, 101.0, trade_id="D-2")
    assert kinds(events) == ["OrderAccepted", "OrderFilled", "OrderFilled"]
    f1, f2 = events[1:]
    assert (str(f1.trade_id), f1.last_qty, f1.last_px) == ("D-1", 4.0, 100.0)
    assert (str(f2.trade_id), f2.last_qty, f2.last_px) == ("D-2", 6.0, 101.0)
    for f in (f1, f2):
        assert f.tags["venue_order_id"] == oid and f.tags["commission_unknown"] is True
        assert f.commission == 0.0 and f.currency == "USD" and f.side is OrderSide.BUY
        assert f.ts_event == venue.clock()   # the deal's own time, read in New York time
    client.poll_now()   # a poll sees what the pushes said: nothing new
    assert len(events) == 3


def test_a_fill_without_an_acceptance_first_is_accepted_first():
    client, venue, events = setup()
    client.submit_order(order(qty=10))
    venue.fill(venue.last_order_id(), 10, 99.0)
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]


def test_order_update_before_its_deal():
    client, venue, events = setup()
    venue.deal_first = False
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.fill(oid, 10, 99.5, trade_id="D-9")
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]
    assert str(events[1].trade_id) == "D-9" and "synthetic_fill" not in events[1].tags


def test_repeated_pushes_are_not_repeated_events():
    client, venue, events = setup()
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.repeat_last_push()
    venue.fill(oid, 4, 100.0)
    venue.repeat_last_push()              # the order update again
    venue.deal_first = False
    venue.fill(oid, 6, 100.0)
    venue.repeat_last_push()              # the deal again
    venue.repeat_last_push()
    venue.cancel_ack(oid)                 # already filled: nothing
    assert kinds(events) == ["OrderAccepted", "OrderFilled", "OrderFilled"]


def test_a_cancelled_or_changed_deal_is_not_booked():
    client, venue, events = setup()
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.push_deal_raw({"trd_env": "SIMULATE", "code": "US.AAPL", "deal_id": "D-X", "order_id": oid, "qty": 10.0,
                         "price": 100.0, "trd_side": "BUY", "create_time": "2026-10-16 10:00:00", "status": "CANCELLED"})
    assert kinds(events) == ["OrderAccepted"]


# ── cancels ──────────────────────────────────────────────────────────

def test_cancel_is_final_only_when_the_venue_says_so():
    client, venue, events = setup()
    o = order()
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    client.cancel_order(o)
    assert venue.cancel_requested(oid) and kinds(events) == ["OrderAccepted"]
    venue.cancel_ack(oid)
    assert kinds(events) == ["OrderAccepted", "OrderCanceled"] and events[-1].reason == "CANCELLED_ALL"


def test_cancel_racing_a_fill():
    client, venue, events = setup()
    o = order(qty=10)
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    client.cancel_order(o)
    venue.fill(oid, 10, 100.0)   # the fill won
    venue.cancel_ack(oid)
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]


def test_partial_fill_then_cancel():
    client, venue, events = setup()
    o = order(qty=10)
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.fill(oid, 4, 100.0)
    client.cancel_order(o)
    venue.cancel_ack(oid)
    assert kinds(events) == ["OrderAccepted", "OrderFilled", "OrderCanceled"]
    assert events[-1].reason == "CANCELLED_PART"


def test_a_cancel_waits_for_the_fills_of_the_part_that_executed():
    client, venue, events = setup()
    venue.deal_push = False   # the deal push is late
    o = order(qty=10)
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.fill(oid, 4, 100.0, trade_id="D-LATE")
    client.cancel_order(o)
    venue.cancel_ack(oid)     # CANCELLED_PART, dealt 4, but no deal yet
    assert kinds(events) == ["OrderAccepted"]
    deal = dict(venue.deals[-1])
    venue.push_deal_raw(deal)
    assert kinds(events) == ["OrderAccepted", "OrderFilled", "OrderCanceled"]


def test_a_refused_cancel_is_followed_by_a_fresh_query():
    client, venue, events = setup()
    o = order(qty=10)
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.push_enabled = False
    venue.fill(oid, 10, 100.5, trade_id="D-7")   # filled at the broker; we heard nothing
    venue.refuse_cancel(oid, "This order cannot be cancelled: it has been filled")
    client.cancel_order(o)
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]
    assert (str(events[1].trade_id), events[1].last_qty, events[1].last_px) == ("D-7", 10.0, 100.5)
    calls = venue.methods_called()
    assert "order_list_query" in calls[calls.index("modify_order") + 1:]


def test_us_paper_status_lag_without_deals():
    """5 October: the status read SUBMITTED for 30 s after the broker had filled the order; the
    query after the refused cancel showed FILLED_ALL. Paper accounts without deal queries."""
    client, venue, events = setup(venue=FakeMoomoo(deals_supported=False))
    o = order(qty=10)
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.push_enabled = False
    venue.accept(oid)
    venue.lag_status(oid)                 # queries keep saying SUBMITTED
    venue.fill(oid, 10, 100.25)
    client.poll_now()
    assert kinds(events) == ["OrderAccepted"]
    venue.refuse_cancel(oid)
    client.cancel_order(o)
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]
    f = events[-1]
    assert (f.last_qty, f.last_px, str(f.trade_id)) == (10.0, 100.25, f"{oid}#10")
    assert f.tags["synthetic_fill"] is True


# ── the poll safety net ──────────────────────────────────────────────

def test_poll_finds_what_the_pushes_lost():
    client, venue, events = setup()
    venue.push_enabled = False
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.fill(oid, 10, 100.0, trade_id="D-P")
    assert events == []
    client.poll_now()
    assert kinds(events) == ["OrderAccepted", "OrderFilled"] and str(events[1].trade_id) == "D-P"
    venue.push_enabled = True
    venue.repeat_last_push()              # the push arrives after all: deduplicated
    client.poll_now()
    assert len(events) == 2
    assert ("deal_list_query", {"trd_env": "SIMULATE", "refresh_cache": True}) in venue.calls


def test_the_poll_thread_runs_while_orders_are_open():
    client, venue, events = setup(poll_interval=0.05)
    try:
        venue.push_enabled = False
        client.submit_order(order(qty=10))
        oid = venue.last_order_id()
        venue.accept(oid)
        venue.fill(oid, 10, 100.0)
        deadline = time.time() + 5
        while time.time() < deadline and "OrderFilled" not in kinds(events):
            time.sleep(0.02)
    finally:
        client.disconnect()
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]


def test_a_fill_known_only_from_status_is_booked_after_the_grace():
    client, venue, events = setup()
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.orders[oid].update(dealt_qty=10.0, dealt_avg_price=100.25)
    venue.set_status(oid, "FILLED_ALL")   # no deal anywhere
    client.poll_now()
    assert kinds(events) == ["OrderAccepted"]
    venue.clock.advance(6)
    client.poll_now()
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]
    f = events[-1]
    assert (f.last_qty, f.last_px, str(f.trade_id), f.tags["synthetic_fill"]) == (10.0, 100.25, f"{oid}#10", True)
    venue.push_deal_raw({"trd_env": "SIMULATE", "code": "US.AAPL", "deal_id": "D-LATE", "order_id": oid,
                         "qty": 10.0, "price": 100.25, "trd_side": "BUY", "create_time": "2026-10-16 10:00:00",
                         "status": "OK"})
    assert len(events) == 2               # the late deal is the same execution


# ── matching orders back by remark ───────────────────────────────────

def test_remark_fits_in_64_bytes_and_stays_unique():
    assert remark_for("O-XSReversal-000001") == "O-XSReversal-000001"
    a, b = "O-" + "LongStrategyName" * 6 + "-000001", "O-" + "LongStrategyName" * 6 + "-000002"
    ra, rb = remark_for(a), remark_for(b)
    assert len(ra.encode()) <= 64 and len(rb.encode()) <= 64 and ra != rb and ra == remark_for(a)
    u = "O-" + "策略" * 30
    assert len(remark_for(u).encode()) <= 64


def test_remark_round_trip_in_reports():
    client, venue, events = setup()
    long_id = "O-" + "LongStrategyName" * 6 + "-000001"
    client.submit_order(order(cid=long_id))
    oid = venue.last_order_id()
    assert venue.submitted()[-1]["client_order_id"] == remark_for(long_id)
    venue.accept(oid)
    (report,) = client.open_orders()
    assert (report.order_id, report.client_order_id, report.status) == (oid, long_id, "SUBMITTED")
    venue.fill(oid, 10, 100.0)
    since = pd.Timestamp(venue.clock(), unit="ns", tz="UTC") - pd.Timedelta(minutes=5)
    (fill,) = client.fills(since)
    assert fill.client_order_id == long_id and fill.order_id == oid


def test_an_order_whose_place_reply_was_lost_is_found_by_its_remark():
    client, venue, events = setup()
    venue.fail_next_place("PacketErr.Timeout", placed=True)
    o = order()
    client.submit_order(o)
    assert events == [] and "venue_order_id" not in o.tags
    venue.accept(venue.last_order_id())   # the push carries our remark
    assert kinds(events) == ["OrderAccepted"]


def test_a_push_that_overtakes_the_place_reply():
    client, venue, events = setup()
    venue.accept_during_place()
    o = order()
    client.submit_order(o)
    assert kinds(events) == ["OrderAccepted"] and o.tags["venue_order_id"] == venue.last_order_id()


def test_a_failed_place_reply_after_a_push_said_the_order_exists():
    client, venue, events = setup()
    venue.accept_during_place()
    venue.fail_next_place("PacketErr.Timeout", placed=True)
    o = order()
    client.submit_order(o)
    assert kinds(events) == ["OrderAccepted"] and o.tags["venue_order_id"] == venue.last_order_id()
    venue.clock.advance(60)
    client.poll_now()
    assert kinds(events) == ["OrderAccepted"]   # never rejected: it exists


def test_a_lost_place_reply_is_resolved_by_the_poll():
    client, venue, events = setup()
    venue.push_enabled = False
    venue.fail_next_place("PacketErr.Timeout", placed=True)
    o = order()
    client.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    client.poll_now()
    assert kinds(events) == ["OrderAccepted"]
    venue.push_enabled = True
    client.cancel_order(o)
    assert venue.cancel_requested(oid)


def test_a_place_that_never_arrived_is_rejected_after_the_grace():
    client, venue, events = setup()
    venue.fail_next_place("PacketErr.Timeout", placed=False)
    client.submit_order(order())
    client.poll_now()
    assert events == []
    venue.clock.advance(31)
    client.poll_now()
    assert kinds(events) == ["OrderRejected"] and "no order with remark" in events[0].reason


# ── paper unless configured ──────────────────────────────────────────

def test_paper_by_default():
    client, venue, events = setup()
    assert client.environment == "paper" and str(client.account_id) == "MOOMOO-US-PAPER"
    client.submit_order(order())
    assert venue.submitted()[-1]["trd_env"] == "SIMULATE"


def test_paper_spelled_paper():
    client, venue, events = setup(trading_env="paper")
    assert client.environment == "paper" and client.trd_env == "SIMULATE"


def test_an_unknown_trading_env_is_refused():
    with pytest.raises(ValueError, match="trading_env"):
        setup(trading_env="reall")


def test_live_only_when_the_connection_says_real(monkeypatch):
    monkeypatch.setenv("MIDFREQ_MOOMOO_TRADING_ENV", "real")
    client, venue, events = setup(venue=FakeMoomoo(real_account=True))
    assert client.environment == "live" and str(client.account_id) == "MOOMOO-US-LIVE"
    client.submit_order(order())
    assert venue.submitted()[-1]["trd_env"] == "REAL"


def test_live_without_a_real_account_does_not_start():
    with pytest.raises(ConnectorError, match="no REAL account"):
        setup(trading_env="real")


def test_unknown_options_are_refused():
    with pytest.raises(ValueError, match="trading_evn"):
        make_moomoo_execution_client(trading_evn="real")


# ── disconnects, reconciliation, adoption ────────────────────────────

def test_disconnect_mid_order_then_catch_up_on_reconnect():
    client, venue, events = setup()
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.disconnect()
    venue.fill(oid, 10, 100.0)            # the push is lost
    assert kinds(events) == ["OrderAccepted"]
    with pytest.raises(ConnectorError):
        client.open_orders()
    venue.reconnect()
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]
    assert client.open_orders() == []


def test_open_orders_query_order_and_fills():
    client, venue, events = setup()
    a, b = order(qty=10), order(qty=5, side=OrderSide.SELL, price=50.0)
    client.submit_order(a)
    client.submit_order(b)
    oid_a, oid_b = a.tags["venue_order_id"], b.tags["venue_order_id"]
    venue.accept(oid_a)
    venue.accept(oid_b)
    venue.fill(oid_a, 10, 100.0, trade_id="D-A")
    (open_b,) = client.open_orders()
    assert (open_b.order_id, open_b.side, open_b.quantity, open_b.price, open_b.client_order_id) == \
        (oid_b, "SELL", 5.0, 50.0, str(b.client_order_id))
    assert open_b.created == pd.Timestamp(venue.clock(), unit="ns", tz="UTC")
    report = client.query_order(a)
    assert (report.status, report.filled_quantity, report.average_fill_price) == ("FILLED_ALL", 10.0, 100.0)
    assert client.query_order(order()) is None
    since = pd.Timestamp(venue.clock(), unit="ns", tz="UTC") - pd.Timedelta(hours=1)
    (fill,) = client.fills(since)
    assert (fill.trade_id, fill.order_id, fill.symbol, fill.side, fill.quantity, fill.price) == \
        ("D-A", oid_a, "US.AAPL", "BUY", 10.0, 100.0)
    assert fill.instrument_id == "AAPL.XNAS" and fill.commission is None and fill.currency == "USD"
    assert fill.ts == pd.Timestamp(venue.clock(), unit="ns", tz="UTC")
    assert client.fills(since + pd.Timedelta(hours=2)) == []
    client.fills(since - pd.Timedelta(days=3))
    assert "history_deal_list_query" in venue.methods_called()


def test_fills_from_orders_where_paper_has_no_deals():
    client, venue, events = setup(venue=FakeMoomoo(deals_supported=False))
    client.submit_order(order(qty=10))
    oid = venue.last_order_id()
    venue.fill(oid, 10, 100.0)
    since = pd.Timestamp(venue.clock(), unit="ns", tz="UTC") - pd.Timedelta(hours=1)
    (fill,) = client.fills(since)
    assert (fill.trade_id, fill.quantity, fill.price) == (f"{oid}#10", 10.0, 100.0)
    assert [str(e.trade_id) for e in events if type(e).__name__ == "OrderFilled"] == [f"{oid}#10"]


def test_an_order_from_a_previous_session_is_adopted_on_cancel():
    first, venue, _ = setup()
    o = order(qty=10)
    first.submit_order(o)
    oid = venue.last_order_id()
    venue.accept(oid)
    venue.fill(oid, 4, 100.0)
    first.disconnect()
    o.status, o.filled_qty, o.avg_px = OrderStatus.PARTIALLY_FILLED, 4.0, 100.0   # the OMS's journal
    second, _, events = setup(venue=venue)
    second.cancel_order(o)
    assert venue.cancel_requested(oid)
    venue.fill(oid, 6, 101.0)             # the rest filled before the cancel landed
    assert [(type(e).__name__, getattr(e, "last_qty", None)) for e in events] == [("OrderFilled", 6.0)]


def test_venue_driver_protocol():
    venue = FakeMoomoo()
    for name in ("last_order_id", "submitted", "accept", "fill", "reject", "cancel_ack", "refuse_cancel",
                 "repeat_last_push", "disconnect", "reconnect"):
        assert callable(getattr(venue, name)), name


def test_the_real_sdk_classes_can_be_extended():
    moomoo = pytest.importorskip("moomoo")
    from midfreq.data.adapters.moomoo.execution import _trade_context_class, _trade_handlers
    from midfreq.data.adapters.moomoo.streaming import _quote_context_class, _quote_handlers
    assert issubclass(_trade_context_class(moomoo), moomoo.OpenSecTradeContext)
    assert issubclass(_quote_context_class(moomoo), moomoo.OpenQuoteContext)
    order_h, deal_h = _trade_handlers(moomoo, None)
    assert isinstance(order_h, moomoo.TradeOrderHandlerBase) and isinstance(deal_h, moomoo.TradeDealHandlerBase)
    kline_h, book_h = _quote_handlers(moomoo, None)
    assert isinstance(kline_h, moomoo.CurKlineHandlerBase) and isinstance(book_h, moomoo.OrderBookHandlerBase)


# ── live streams ─────────────────────────────────────────────────────

XNAS, XHKG = default_venues().get("XNAS"), default_venues().get("XHKG")
HOUR, DAY = BarSpecification.from_str("1-HOUR"), BarSpecification.from_str("1-DAY")


@pytest.mark.parametrize("time_key,spec,venue,utc", [
    ("2026-10-16 10:30:00", HOUR, XNAS, "2026-10-16 14:30"),    # first US hour (09:30-10:30 EDT)
    ("2026-10-16 16:00:00", HOUR, XNAS, "2026-10-16 20:00"),    # the half hour cut by the close
    ("2026-12-01 16:00:00", HOUR, XNAS, "2026-12-01 21:00"),    # EST
    ("2026-10-16 12:00:00", HOUR, XHKG, "2026-10-16 04:00"),    # HK morning ends at lunch
    ("2026-10-16 16:00:00", HOUR, XHKG, "2026-10-16 08:00"),
    ("2026-10-16 00:00:00", DAY, XNAS, "2026-10-16 20:00"),     # a daily K-line is the date
    ("2026-10-16 00:00:00", DAY, XHKG, "2026-10-16 08:00"),
])
def test_kline_time_key_is_the_bar_close(time_key, spec, venue, utc):
    assert kline_close_ns(time_key, spec, venue) == ns(utc)


def test_kline_in_a_dst_gap_has_no_time():
    assert kline_close_ns("2026-03-08 02:30:00", BarSpecification.from_str("1-MINUTE"), XNAS) is None


def streams(venue=None):
    venue = venue or FakeMoomoo()
    conn = make_moomoo_connector(venue)
    statuses = []
    conn.data_client.set_stream_status_handler(statuses.append)
    return conn, conn.data_client, venue, statuses


def test_bars_stream_with_close_times_in_utc():
    conn, dc, venue, statuses = streams()
    updates = []
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), updates.append)
    assert ("subscribe", {"codes": ["US.AAPL"], "subtypes": ["K_60M"], "first_push": True}) in venue.calls
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 100.0, 101.0, 99.5, 100.5, 12345)
    venue.push_kline("US.MSFT", "K_60M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)     # not subscribed
    venue.push_kline("US.AAPL", "K_1M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)      # another interval
    (u,) = updates
    assert u.bar.ts_event == ns("2026-10-16 14:30") and u.closed is False
    assert u.ts_received == venue.clock() == u.bar.ts_init
    assert (u.bar.open, u.bar.high, u.bar.low, u.bar.close, u.bar.volume) == (100.0, 101.0, 99.5, 100.5, 12345.0)
    assert u.bar.bar_type == BarType.of(AAPL, "1-HOUR")


def test_hong_kong_and_daily_bars():
    conn, dc, venue, statuses = streams()
    hk, daily = [], []
    dc.subscribe_bars(BarType.of(TENCENT, "1-HOUR"), hk.append)
    dc.subscribe_bars(BarType.of(AAPL, "1-DAY"), daily.append)
    venue.push_kline("HK.00700", "K_60M", "2026-10-16 12:00:00", 1, 1, 1, 1, 1)
    venue.push_kline("US.AAPL", "K_DAY", "2026-10-16 00:00:00", 1, 1, 1, 1, 1)
    assert hk[0].bar.ts_event == ns("2026-10-16 04:00") and daily[0].bar.ts_event == ns("2026-10-16 20:00")


def test_the_first_push_on_subscribing_is_delivered():
    conn, dc, venue, statuses = streams()
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)   # before anyone listens
    updates = []
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), updates.append)
    assert len(updates) == 1


def test_unsupported_interval():
    conn, dc, venue, statuses = streams()
    with pytest.raises(UnsupportedIntervalError):
        dc.subscribe_bars(BarType.of(AAPL, "4-HOUR"), print)


def test_quotes_are_the_top_of_the_order_book():
    conn, dc, venue, statuses = streams()
    quotes = []
    dc.subscribe_quotes(AAPL, quotes.append)
    assert ("subscribe", {"codes": ["US.AAPL"], "subtypes": ["ORDER_BOOK"], "first_push": True}) in venue.calls
    venue.push_order_book("US.AAPL", 99.9, 100.1, bid_size=300, ask_size=200)
    venue.push_order_book("US.AAPL", None, 100.1)   # one-sided: skipped
    (q,) = quotes
    assert (q.instrument_id, q.bid, q.ask, q.bid_size, q.ask_size) == (AAPL, 99.9, 100.1, 300.0, 200.0)
    assert q.ts_event == q.ts_init == venue.clock()


def test_subscription_limit_is_what_this_connection_may_use():
    conn, dc, venue, statuses = streams(FakeMoomoo(quota_total=100, quota_used_elsewhere=30))
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), print)
    dc.subscribe_quotes(AAPL, print)
    assert dc.subscription_limit() == 2 + (100 - 32)


def test_quota_exceeded():
    conn, dc, venue, statuses = streams(FakeMoomoo(quota_total=1))
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), print)
    with pytest.raises(QuotaExceededError):
        dc.subscribe_bars(BarType.of(MSFT, "1-HOUR"), print)
    assert dc.streams.subscriptions() == [("US.AAPL", "K_60M")]


def test_unsubscribe_waits_for_the_minimum_subscription_time():
    conn, dc, venue, statuses = streams()
    bt, updates = BarType.of(AAPL, "1-HOUR"), []
    dc.subscribe_bars(bt, updates.append)
    venue.clock.advance(10)
    dc.unsubscribe_bars(bt)
    assert "unsubscribe" not in venue.methods_called() and len(venue.timers) == 1
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)
    assert updates == []                  # no longer delivered
    venue.clock.advance(45)
    assert venue.run_timers() == 0
    venue.clock.advance(10)
    assert venue.run_timers() == 1
    assert "unsubscribe" in venue.methods_called() and venue.quote_contexts[0].server == {}
    dc.subscribe_bars(bt, updates.append)  # later, a fresh subscription
    venue.clock.advance(120)
    dc.unsubscribe_bars(bt)                # old enough: at once
    assert venue.methods_called().count("unsubscribe") == 2


def test_subscribing_again_within_the_minute_keeps_the_subscription():
    conn, dc, venue, statuses = streams()
    bt, first, second = BarType.of(AAPL, "1-HOUR"), [], []
    dc.subscribe_bars(bt, first.append)
    venue.clock.advance(10)
    dc.unsubscribe_bars(bt)
    dc.subscribe_bars(bt, second.append)
    assert venue.methods_called().count("subscribe") == 1
    venue.clock.advance(120)
    venue.run_timers()
    assert "unsubscribe" not in venue.methods_called()
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)
    assert first == [] and len(second) == 1


@pytest.mark.parametrize("sdk_resubscribes", [True, False])
def test_stream_status_and_resubscription_after_reconnect(sdk_resubscribes):
    conn, dc, venue, statuses = streams(FakeMoomoo(sdk_resubscribes=sdk_resubscribes))
    bars, quotes = [], []
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), bars.append)
    dc.subscribe_quotes(AAPL, quotes.append)
    assert [s.state for s in statuses] == ["connected"]
    venue.disconnect()
    assert [s.state for s in statuses] == ["connected", "disconnected"]
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)
    assert bars == []
    venue.reconnect()
    assert [s.state for s in statuses] == ["connected", "disconnected", "reconnected"]
    assert statuses[-1].detail == "2 subscriptions restored" and statuses[-1].ts == venue.clock()
    assert set(venue.quote_contexts[0].server) == {("US.AAPL", "K_60M"), ("US.AAPL", "ORDER_BOOK")}
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 11:30:00", 1, 1, 1, 1, 1)
    venue.push_order_book("US.AAPL", 1.0, 1.1)
    assert bars[-1].bar.ts_event == ns("2026-10-16 15:30") and len(quotes) >= 1


def test_a_failed_resubscription_is_an_error_status():
    conn, dc, venue, statuses = streams()
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), print)
    venue.query_errors["subscribe"] = "Subscription quota is insufficient"
    venue.disconnect()
    venue.reconnect()
    assert statuses[-1].state == "error" and "quota" in statuses[-1].detail


def test_a_failing_handler_does_not_stop_the_stream():
    conn, dc, venue, statuses = streams()
    calls = []

    def handler(update):
        calls.append(update)
        if len(calls) == 1:
            raise RuntimeError("strategy bug")
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), handler)
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 1, 1, 1, 1, 1)
    venue.push_kline("US.AAPL", "K_60M", "2026-10-16 10:30:00", 2, 2, 2, 2, 2)
    assert len(calls) == 2


def test_opend_down_is_an_error_status():
    venue = FakeMoomoo()
    venue.opend_up = False
    conn, dc, venue, statuses = streams(venue)
    with pytest.raises(ConnectorError, match="not reachable"):
        dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), print)
    assert [s.state for s in statuses] == ["error"]


# ── the connector ────────────────────────────────────────────────────

def test_connector_capabilities_and_close():
    conn, dc, venue, statuses = streams()
    assert conn.capabilities.live_data and conn.capabilities.execution
    client = conn.execution_client(market="HK", poll_interval=0)
    assert isinstance(client, MoomooExecutionClient) and client.environment == "paper"
    assert client.currency == "HKD" and client.timezone == "Asia/Hong_Kong"
    dc.subscribe_bars(BarType.of(AAPL, "1-HOUR"), print)
    conn.close()
    assert venue.quote_contexts[0].closed


def test_a_push_payload_the_sdk_cannot_parse_is_ignored():
    from fake_moomoo import PushError
    client, venue, events = setup()
    client.submit_order(order())
    venue.push_order_raw(PushError("bad packet"))
    venue.push_deal_raw(PushError("bad packet"))
    assert events == []
    assert set(ORDER_PUSH_COLUMNS) >= {"order_id", "order_status", "remark", "dealt_qty", "dealt_avg_price"}
