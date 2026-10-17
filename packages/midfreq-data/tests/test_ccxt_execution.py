"""
The ccxt connector's live side, beyond the execution contract (test_execution_contract.py):
environments and credentials, client order ids and precision, the status mapping, orders whose
reply is lost, exchanges without trade data, the reconciliation reads, and the live streams.
All against tests/support/fake_ccxt.py; nothing reaches a real exchange.
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

ccxt = pytest.importorskip("ccxt")

from midfreq.core.model.data import BarType
from midfreq.core.model.enums import LiquiditySide, OrderSide, TimeInForce
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import InstrumentId, StrategyId
from midfreq.core.model.orders import OrderFactory
from midfreq.data.adapters.base import BarUpdate, ConnectorError, StreamStatus
from midfreq.data.adapters.ccxt.connector import CcxtConnector
from midfreq.data.adapters.ccxt.execution import (
    CcxtExecutionClient, client_id_param, resolve_environment, venue_client_order_id,
)
from midfreq.data.adapters.ccxt.streaming import bar_update

from fake_ccxt import BASE_MS, FakeCcxtDriver, FakeCcxtPro, FakeCcxtRest, FakeCcxtVenue, make_ccxt_execution_client

BTC = InstrumentId.from_str("BTC-USDT.OKX")
WAIT = 5.0


class Events(list):
    def __init__(self):
        super().__init__()
        self._cv = threading.Condition()

    def __call__(self, event):
        with self._cv:
            self.append(event)
            self._cv.notify_all()

    def of(self, cls):
        with self._cv:
            return [e for e in self if isinstance(e, cls)]

    def wait(self, cls, n=1, timeout=WAIT):
        deadline = time.monotonic() + timeout
        with self._cv:
            while sum(isinstance(e, cls) for e in self) < n:
                left = deadline - time.monotonic()
                if left <= 0:
                    pytest.fail(f"no {n} {cls.__name__} within {timeout}s: {list(self)}")
                self._cv.wait(left)
        return self.of(cls)


def quiet(seconds=0.15):
    time.sleep(seconds)


@pytest.fixture
def paper_config(tmp_path, monkeypatch):
    """connections.yaml saying paper, with no environment override."""
    cfg = tmp_path / "connections.yaml"
    cfg.write_text("ccxt:\n  trading_env: paper\n")
    monkeypatch.setenv("MIDFREQ_CONNECTIONS", str(cfg))
    monkeypatch.delenv("MIDFREQ_CCXT_TRADING_ENV", raising=False)
    return cfg


def connected(**options):
    client, driver = make_ccxt_execution_client(**options)
    events = Events()
    client.set_event_handler(events)
    client.connect()
    return client, driver, events


def orders(prefix="O"):
    return OrderFactory(StrategyId("XSReversal-001"), prefix=prefix)


@pytest.fixture
def live(request):
    made = []

    def make(**options):
        client, driver, events = connected(**options)
        made.append(client)
        return client, driver, events
    yield make
    for client in made:
        client.disconnect()


# ── environment, credentials, sandbox ────────────────────────────────

def test_environment_is_paper_unless_connections_yaml_says_live():
    assert resolve_environment(None, None) == "paper"
    assert resolve_environment(None, "paper") == "paper"
    assert resolve_environment("demo", "live") == "paper"
    assert resolve_environment(None, "live") == "live"
    assert resolve_environment("real", "live") == "live"
    with pytest.raises(ConnectorError, match="trading_env: live"):
        resolve_environment("live", "paper")
    with pytest.raises(ValueError):
        resolve_environment("prod-ish", "paper")


def test_live_needs_the_connections_setting(paper_config, monkeypatch):
    with pytest.raises(ConnectorError):
        make_ccxt_execution_client(environment="live")
    monkeypatch.setenv("MIDFREQ_CCXT_TRADING_ENV", "live")
    client, _ = make_ccxt_execution_client(environment="live")
    assert client.environment == "live" and str(client.account_id) == "OKX-LIVE"
    client.connect()
    try:
        assert client.fake_rest.sandbox is False and client.fake_pro.sandbox is False
    finally:
        client.disconnect()


def test_paper_puts_both_exchange_objects_in_the_sandbox(paper_config, live):
    client, _, _ = live()
    assert client.environment == "paper" and str(client.account_id) == "OKX-PAPER"
    assert client.fake_rest.sandbox and client.fake_pro.sandbox
    assert client.update_mode == "watch"


def test_unknown_options_are_refused():
    with pytest.raises(ValueError, match="enviroment"):
        make_ccxt_execution_client(enviroment="paper")


def test_credentials_come_from_the_environment_under_paper_names(paper_config):
    seen = {}

    def factory(exchange_id, config):
        seen.update(config, exchange_id=exchange_id)
        return FakeCcxtRest(FakeCcxtVenue())

    secrets = {"OKX_PAPER_API_KEY": "k", "OKX_PAPER_API_SECRET": "s", "OKX_PAPER_API_PASSPHRASE": "p"}
    client = CcxtExecutionClient(CcxtConnector(), {"exchange": "okx", "updates": "poll"},
                                 exchange_factory=factory, get_secret=secrets.get)
    client.connect()
    try:
        assert seen["exchange_id"] == "okx"
        assert (seen["apiKey"], seen["secret"], seen["password"]) == ("k", "s", "p")
        assert seen["enableRateLimit"] is True
        assert client._ex.sandbox is True
    finally:
        client.disconnect()


def test_missing_credentials_name_the_variables(paper_config):
    client = CcxtExecutionClient(CcxtConnector(), {"exchange": "okx"},
                                 exchange_factory=lambda *_: pytest.fail("built without a key"),
                                 get_secret=lambda var: None)
    with pytest.raises(ConnectorError, match="OKX_PAPER_API_KEY"):
        client.connect()


def test_an_exchange_without_a_sandbox_cannot_paper_trade(paper_config):
    rest = FakeCcxtRest(FakeCcxtVenue())

    def no_sandbox(enabled):
        raise ccxt.NotSupported("kraken does not have a sandbox URL")
    rest.set_sandbox_mode = no_sandbox
    client = CcxtExecutionClient(CcxtConnector(), {"updates": "poll"}, exchange=rest)
    with pytest.raises(ConnectorError, match="cannot paper trade"):
        client.connect()


def test_watch_needs_a_stream_exchange(paper_config):
    venue = FakeCcxtVenue()
    client = CcxtExecutionClient(CcxtConnector(), {"updates": "watch"}, exchange=FakeCcxtRest(venue),
                                 stream_exchange=object())
    with pytest.raises(ConnectorError, match="watch_orders"):
        client.connect()


def test_submit_before_connect_is_an_error(paper_config):
    client, _ = make_ccxt_execution_client()
    with pytest.raises(ConnectorError, match="not connected"):
        client.submit_order(orders().limit(BTC, OrderSide.BUY, 0.1, 60000.0))


# ── client order ids and precision ───────────────────────────────────

def test_client_order_ids_fit_each_exchange():
    cid = "O-XSReversal-001-000001"
    okx = venue_client_order_id(cid, "okx")
    assert okx.isalnum() and len(okx) <= 32 and okx.startswith("OXSReversal001000001")
    assert okx == venue_client_order_id(cid, "okx")                       # recomputable
    assert okx != venue_client_order_id("OXSReversal-001-000001", "okx")   # punctuation still distinguishes
    assert len(venue_client_order_id("O-" + "x" * 60, "okx")) == 32
    assert venue_client_order_id(cid, "binance") == cid                    # Binance takes dashes
    assert venue_client_order_id("abc123", "okx") == "abc123"
    assert (client_id_param("okx"), client_id_param("binance"), client_id_param("kraken")) == \
        ("clOrdId", "newClientOrderId", "clientOrderId")


def test_submit_sends_the_client_id_and_rounds_to_the_market(paper_config, live):
    client, driver, events = live()
    order = orders().limit(BTC, OrderSide.BUY, 0.123456789, 60000.04)
    client.submit_order(order)
    sent = driver.submitted()[-1]
    assert sent["symbol"] == "BTC/USDT" and sent["side"] == "buy" and sent["order_type"] == "limit"
    assert sent["quantity"] == pytest.approx(0.12345678) and sent["price"] == pytest.approx(60000.0)
    assert sent["params"] == {"clOrdId": order.tags["venue_client_order_id"]}
    assert order.tags["venue_client_order_id"] == venue_client_order_id(str(order.client_order_id), "okx")
    assert order.quantity == pytest.approx(0.12345678) and order.tags["requested_qty"] == 0.123456789
    assert order.tags["venue_price"] == pytest.approx(60000.0)
    assert order.tags["venue_order_id"] == driver.last_order_id()
    quiet()
    assert not events


def test_ioc_and_market_orders(paper_config, live):
    client, driver, _ = live()
    client.submit_order(orders().limit(BTC, OrderSide.SELL, 0.1, 61000.0, time_in_force=TimeInForce.IOC))
    assert driver.submitted()[-1]["params"]["timeInForce"] == "IOC"
    client.submit_order(orders("M").market(BTC, OrderSide.BUY, 0.1))
    sent = driver.submitted()[-1]
    assert sent["order_type"] == "market" and sent["price"] is None


def test_unsendable_orders_are_rejected_without_reaching_the_exchange(paper_config, live):
    client, driver, events = live()
    f = orders()
    client.submit_order(f.limit(BTC, OrderSide.BUY, 1e-12, 60000.0))           # below the amount precision
    client.submit_order(f.limit(InstrumentId.from_str("BTCUSDT.BINANCE"), OrderSide.BUY, 0.1, 60000.0))
    client.submit_order(f.limit(InstrumentId.from_str("DOGE-USDT.OKX"), OrderSide.BUY, 1, 0.1))   # no market
    rejected = events.wait(OrderRejected, 3)
    assert all(e.reason.startswith("not sent") for e in rejected)
    assert "precision" in rejected[0].reason
    assert "binance" in rejected[1].reason
    assert "DOGE/USDT" in rejected[2].reason
    assert driver.submitted() == []


def test_an_exchange_refusal_at_submit_is_a_rejection(paper_config, live):
    client, driver, events = live()
    client.fake_venue.create_errors.append(ccxt.InsufficientFunds('okx {"sCode":"51008","sMsg":"Insufficient USDT"}'))
    order = orders().limit(BTC, OrderSide.BUY, 1.0, 60000.0)
    client.submit_order(order)
    (rejected,) = events.wait(OrderRejected)
    assert "InsufficientFunds" in rejected.reason and "Insufficient USDT" in rejected.reason
    assert rejected.client_order_id == order.client_order_id


def test_the_same_order_is_never_sent_twice(paper_config, live):
    client, driver, _ = live()
    order = orders().limit(BTC, OrderSide.BUY, 0.1, 60000.0)
    client.submit_order(order)
    client.submit_order(order)
    assert len(driver.submitted()) == 1


# ── status mapping ───────────────────────────────────────────────────

@pytest.mark.parametrize("status,event,word", [
    ("canceled", OrderCanceled, "canceled"), ("cancelled", OrderCanceled, "canceled"),
    ("expired", OrderCanceled, "expired"), ("rejected", OrderRejected, "rejected"),
])
def test_terminal_statuses(paper_config, live, status, event, word):
    client, driver, events = live()
    client.submit_order(orders().limit(BTC, OrderSide.BUY, 0.1, 60000.0))
    vid = driver.last_order_id()
    driver.accept(vid)
    events.wait(OrderAccepted)
    client.fake_venue.update(vid, status, {"sMsg": "by the exchange"})
    (ended,) = events.wait(event)
    assert ended.reason == f"{word}: by the exchange"
    quiet()
    assert len(events) == 2


@pytest.mark.parametrize("updates", ["watch", "poll"])
def test_a_close_before_its_trade_arrives_fetches_the_trade_first(paper_config, live, updates):
    """The order's 'closed' push can beat its trade: the fill is fetched, then not repeated."""
    client, driver, events = live(updates=updates)
    order = orders().limit(BTC, OrderSide.BUY, 0.5, 60000.0)
    client.submit_order(order)
    vid = driver.last_order_id()
    driver.accept(vid)
    events.wait(OrderAccepted)
    client.fake_venue.streaming = False      # this fill's pushes are lost ...
    driver.fill(vid, 0.2, 60000.0, "T1")
    client.fake_venue.streaming = updates == "watch"
    driver.cancel_ack(vid)                   # ... and the cancel's is not
    events.wait(OrderCanceled)
    quiet()
    assert [type(e).__name__ for e in events] == ["OrderAccepted", "OrderFilled", "OrderCanceled"]
    fill = events.of(OrderFilled)[0]
    assert str(fill.trade_id) == "T1" and fill.last_qty == pytest.approx(0.2)
    assert fill.commission == pytest.approx(0.2 * 60000.0 * 0.001) and fill.currency == "USDT"
    assert fill.liquidity_side is LiquiditySide.TAKER
    assert fill.ts_event > BASE_MS * 1_000_000
    assert fill.tags == {"venue_order_id": vid, "venue_client_order_id": order.tags["venue_client_order_id"]}


def test_market_order_filled_at_once_has_no_accept(paper_config, live):
    client, driver, events = live()
    client.submit_order(orders().market(BTC, OrderSide.BUY, 0.1))
    driver.fill(driver.last_order_id(), 0.1, 60010.0, "T9")
    events.wait(OrderFilled)
    quiet()
    assert [type(e).__name__ for e in events] == ["OrderFilled"]


def test_fills_from_filled_changes_where_the_exchange_has_no_trade_data(paper_config, live):
    client, driver, events = live(updates="poll")
    client.fake_rest.has.update(fetchMyTrades=False, fetchOrderTrades=False)
    client.submit_order(orders().limit(BTC, OrderSide.BUY, 1.0, 100.0))
    vid = driver.last_order_id()
    driver.accept(vid)
    driver.fill(vid, 0.25, 100.0)
    events.wait(OrderFilled, 1)
    driver.fill(vid, 0.75, 104.0)
    fills = events.wait(OrderFilled, 2)
    quiet()
    assert [f.last_qty for f in fills] == pytest.approx([0.25, 0.75])
    assert [f.last_px for f in fills] == pytest.approx([100.0, 104.0])
    assert len({str(f.trade_id) for f in fills}) == 2 and len(events.of(OrderFilled)) == 2


# ── lost replies and early pushes ────────────────────────────────────

def test_a_lost_create_reply_is_found_by_client_id(paper_config, live):
    client, driver, events = live()
    client.fake_venue.lose_create_reply = True
    order = orders().limit(BTC, OrderSide.BUY, 0.1, 60000.0)
    client.submit_order(order)                     # placed, but the reply timed out
    driver.accept(driver.last_order_id())
    events.wait(OrderAccepted)
    assert order.tags["venue_order_id"] == driver.last_order_id()
    quiet()
    assert not events.of(OrderRejected)


def test_an_order_that_never_reached_the_exchange_is_rejected_after_the_timeout(paper_config, live):
    client, driver, events = live(updates="poll")
    client.fake_venue.create_errors.append(ccxt.RequestTimeout("okx POST /api/v5/trade/order timed out"))
    client.submit_order(orders().limit(BTC, OrderSide.BUY, 0.1, 60000.0))
    (rejected,) = events.wait(OrderRejected)
    assert "RequestTimeout" in rejected.reason and "after" in rejected.reason


def test_a_trade_pushed_before_the_create_reply_is_kept_until_the_reply(paper_config, live):
    client, driver, events = live()
    venue = client.fake_venue
    original = client.fake_rest.create_order

    def create_then_push_early(*args, **kwargs):
        reply = original(*args, **kwargs)
        trade = {"id": "EARLY", "order": reply["id"], "timestamp": BASE_MS + 1, "symbol": "BTC/USDT",
                 "side": "buy", "price": 60000.0, "amount": 0.1, "takerOrMaker": "taker",
                 "fee": {"cost": 6.0, "currency": "USDT"}}
        with venue.lock:
            venue.trades.append(trade)
        venue.push("trades", trade)        # no client id on a trade: it waits for the reply's order id
        deadline = time.monotonic() + WAIT
        while venue.queues["trades"] and time.monotonic() < deadline:   # the stream takes it first
            time.sleep(0.005)
        time.sleep(0.02)
        return reply
    client.fake_rest.create_order = create_then_push_early
    client.submit_order(orders().limit(BTC, OrderSide.BUY, 0.1, 60000.0))
    (fill,) = events.wait(OrderFilled)
    assert str(fill.trade_id) == "EARLY"
    quiet()
    assert len(events.of(OrderFilled)) == 1


def test_updates_for_orders_of_others_are_ignored(paper_config, live):
    client, driver, events = live()
    with client.fake_venue.lock:
        other = client.fake_venue.new_order("BTC/USDT", "limit", "buy", 1.0, 1.0, "someoneelse")
    driver.accept(other["id"])
    driver.fill(other["id"], 1.0, 1.0)
    quiet()
    assert not events


# ── reconciliation reads ─────────────────────────────────────────────

def test_reports_for_reconciliation(paper_config, live):
    client, driver, events = live()
    order = orders().limit(BTC, OrderSide.SELL, 0.5, 61000.0)
    client.submit_order(order)
    vid = driver.last_order_id()
    driver.accept(vid)
    driver.fill(vid, 0.2, 61000.0, "R1")
    events.wait(OrderFilled)

    (report,) = client.open_orders()
    assert (report.order_id, report.symbol, report.side, report.status) == (vid, "BTC/USDT", "SELL", "open")
    assert report.quantity == 0.5 and report.filled_quantity == pytest.approx(0.2)
    assert report.price == 61000.0 and report.average_fill_price == pytest.approx(61000.0)
    assert report.client_order_id == str(order.client_order_id)
    assert report.created.tz is not None

    (fill,) = client.fills(pd.Timestamp("2026-01-01", tz="UTC"))
    assert (fill.trade_id, fill.order_id, fill.side, fill.currency) == ("R1", vid, "SELL", "USDT")
    assert fill.commission == pytest.approx(0.2 * 61000.0 * 0.001)
    assert fill.client_order_id == str(order.client_order_id) and fill.instrument_id == "BTC-USDT.OKX"
    assert client.fills(pd.Timestamp("2030-01-01", tz="UTC")) == []

    assert client.query_order(order).filled_quantity == pytest.approx(0.2)
    stranger = orders("X").limit(BTC, OrderSide.BUY, 1, 1)
    assert client.query_order(stranger) is None


def test_reads_fail_loudly_when_the_exchange_is_unreachable(paper_config, live):
    client, driver, _ = live()
    driver.disconnect()
    with pytest.raises(ConnectorError):
        client.open_orders()
    with pytest.raises(ConnectorError):
        client.fills(pd.Timestamp("2026-01-01", tz="UTC"))


def test_an_order_from_before_a_restart_is_adopted_by_query(paper_config, live):
    client, driver, events = live()
    order = orders().limit(BTC, OrderSide.BUY, 0.3, 60000.0)
    client.submit_order(order)
    vid = driver.last_order_id()
    driver.accept(vid)
    events.wait(OrderAccepted)
    client.disconnect()

    fresh, _ = make_ccxt_execution_client()        # a new session on the same exchange account
    fresh.fake_rest = client.fake_rest
    fresh._ex = client.fake_rest
    fresh._stream_ex = FakeCcxtPro(client.fake_venue)
    later = Events()
    fresh.set_event_handler(later)
    fresh.connect()
    try:
        assert fresh.query_order(order).order_id == vid
        driver.fill(vid, 0.3, 60000.0, "AFTER")
        (fill,) = later.wait(OrderFilled)
        assert str(fill.trade_id) == "AFTER"
    finally:
        fresh.disconnect()


def test_disconnect_stops_the_update_thread(paper_config):
    client, _, _ = connected()
    thread = client._thread
    client.disconnect()
    assert not thread.is_alive() and not client.is_connected


# ── live streams on the data client ──────────────────────────────────

class Clock:
    def __init__(self, ns):
        self.ns = ns

    def __call__(self):
        return self.ns


def streaming_connector(clock, **config):
    venue = FakeCcxtVenue()
    rest = FakeCcxtRest(venue)
    rest.load_markets()
    pro = FakeCcxtPro(venue)
    conn = CcxtConnector({"min_request_interval": 0.0, "stream_reconnect_delay": 0.01,
                          "stream_max_reconnect_delay": 0.02, **config},
                         exchanges={"OKX": rest}, stream_exchanges={"OKX": pro}, now_ns=clock)
    return conn, FakeCcxtDriver(venue), pro


class Inbox(Events):
    pass


def test_bar_updates_carry_utc_close_times_and_closed_flags():
    bt = BarType.of("BTC-USDT.OKX", "1-HOUR")
    open_ms = int(pd.Timestamp("2026-10-17 09:00", tz="UTC").value // 1_000_000)
    hour = 3_600_000_000_000
    close_ns = open_ms * 1_000_000 + hour
    forming = bar_update(bt, [open_ms, 1, 2, 0.5, 1.5, 10], hour, close_ns - 1)
    assert forming.bar.ts_event == close_ns and forming.bar.timestamp == pd.Timestamp("2026-10-17 10:00", tz="UTC")
    assert not forming.closed and forming.ts_received == close_ns - 1 and forming.bar.ts_init == close_ns - 1
    assert bar_update(bt, [open_ms, 1, 2, 0.5, 1.5, 10], hour, close_ns).closed
    assert bar_update(bt, [open_ms, 1, 2, 0.5, None, 10], hour, close_ns) is None


def test_connector_says_it_trades_and_streams():
    conn = CcxtConnector()
    caps = conn.capabilities
    assert caps.live_data and caps.execution
    assert conn.capabilities_for(conn.venues.get("OKX")).execution
    client = conn.execution_client(exchange="okx", updates="poll")
    assert isinstance(client, CcxtExecutionClient) and client.environment == "paper"
    assert conn.data_client.subscription_limit() is None


def test_watched_bars_and_stream_status():
    t0 = int(pd.Timestamp("2026-10-17 09:30", tz="UTC").value)
    clock = Clock(t0)
    conn, driver, pro = streaming_connector(clock)
    bars, statuses = Inbox(), Inbox()
    data = conn.data_client
    data.set_stream_status_handler(statuses)
    bt = BarType.of("BTC-USDT.OKX", "1-MINUTE")
    data.subscribe_bars(bt, bars)
    open_ms = t0 // 1_000_000
    try:
        driver.push_candle("BTC/USDT", [open_ms, 100, 101, 99, 100.5, 3])
        (first,) = bars.wait(BarUpdate)
        assert first.bar.ts_event == t0 + 60_000_000_000 and not first.closed
        assert (first.bar.open, first.bar.high, first.bar.low, first.bar.close, first.bar.volume) == (100, 101, 99, 100.5, 3)
        clock.ns = t0 + 61_000_000_000
        driver.push_candle("BTC/USDT", [open_ms, 100, 102, 99, 101.0, 5])
        driver.push_candle("BTC/USDT", [open_ms + 60_000, 101, 101, 101, 101, 1])
        updates = bars.wait(BarUpdate, 3)
        assert [u.closed for u in updates] == [False, True, False]
        assert [s.state for s in statuses.of(StreamStatus)] == ["connected"]

        driver.disconnect()
        statuses.wait(StreamStatus, 2)
        driver.reconnect()
        driver.push_candle("BTC/USDT", [open_ms + 60_000, 101, 103, 101, 102, 2])
        statuses.wait(StreamStatus, 3)
        assert [s.state for s in statuses.of(StreamStatus)] == ["connected", "disconnected", "reconnected"]
        bars.wait(BarUpdate, 4)

        data.unsubscribe_bars(bt)
        quiet(0.05)
        driver.push_candle("BTC/USDT", [open_ms + 120_000, 1, 1, 1, 1, 1])
        quiet()
        assert len(bars.of(BarUpdate)) == 4
    finally:
        conn.close()
    assert pro.closed == 0      # an injected exchange is the caller's to close


@pytest.mark.parametrize("source,push", [("ticker", "ticker"), ("auto", "ticker"), ("book", "book")])
def test_watched_quotes(source, push):
    clock = Clock(int(pd.Timestamp("2026-10-17 09:30", tz="UTC").value))
    conn, driver, _ = streaming_connector(clock, quote_source=source)
    quotes = Inbox()
    conn.data_client.subscribe_quotes(BTC, quotes)
    try:
        ts_ms = BASE_MS + 5
        if push == "ticker":
            driver.push_ticker("BTC/USDT", 99.5, 100.5, 2.0, 3.0, ts_ms)
        else:
            driver.push_book("BTC/USDT", [[99.5, 2.0], [99.0, 9.0]], [[100.5, 3.0]], ts_ms)
        (q,) = quotes.wait(object)
        assert (q.instrument_id, q.bid, q.ask, q.bid_size, q.ask_size) == (BTC, 99.5, 100.5, 2.0, 3.0)
        assert q.ts_event == ts_ms * 1_000_000 and q.ts_init == clock.ns
    finally:
        conn.close()


def test_polled_bars_push_only_what_changed():
    t0 = int(pd.Timestamp("2026-10-17 09:30", tz="UTC").value)
    clock = Clock(t0 + 30_000_000_000)
    conn, driver, pro = streaming_connector(clock, stream_mode="poll", stream_poll_interval=0.01)
    open_ms = t0 // 1_000_000
    driver.push_candle("BTC/USDT", [open_ms - 60_000, 99, 100, 98, 99.5, 7])
    driver.push_candle("BTC/USDT", [open_ms, 100, 101, 99, 100.5, 3])
    bars = Inbox()
    conn.data_client.subscribe_bars(BarType.of("BTC-USDT.OKX", "1-MINUTE"), bars)
    try:
        updates = bars.wait(BarUpdate, 2)
        quiet()
        assert [u.closed for u in updates] == [True, False] and len(bars) == 2
        driver.push_candle("BTC/USDT", [open_ms, 100, 103, 99, 102.0, 4])
        later = bars.wait(BarUpdate, 3)
        assert later[-1].bar.close == 102.0 and not later[-1].closed
        assert "watch_ohlcv" not in pro.watch_calls
    finally:
        conn.close()


def test_an_unknown_market_is_a_stream_error():
    clock = Clock(int(pd.Timestamp("2026-10-17 09:30", tz="UTC").value))
    conn, _, _ = streaming_connector(clock, stream_mode="poll", stream_poll_interval=0.01)
    statuses = Inbox()
    conn.data_client.set_stream_status_handler(statuses)
    conn.data_client.subscribe_quotes(InstrumentId.from_str("NOPE-USDT.OKX"), lambda q: None)
    try:
        (status,) = statuses.wait(StreamStatus)
        assert status.state == "error" and "BadSymbol" in status.detail
    finally:
        conn.close()


def _push_trade(client, order_id, trade):
    venue = client.fake_venue
    trade = {"order": order_id, "timestamp": BASE_MS + 5, "symbol": "BTC/USDT", "side": "buy",
             "takerOrMaker": "taker", **trade}
    with venue.lock:
        venue.trades.append(trade)
    venue.push("trades", trade)


def test_a_trade_without_a_fee_is_marked_commission_unknown(paper_config, live):
    """ExecutionClient contract: no fee reported -> commission 0 and tags['commission_unknown']; a
    fee reported (even 0) carries no such tag, in the currency the exchange charged it in."""
    client, driver, events = live()
    client.submit_order(orders().limit(BTC, OrderSide.BUY, 0.3, 60000.0))
    vid = driver.last_order_id()
    _push_trade(client, vid, {"id": "NOFEE", "price": 60000.0, "amount": 0.1})
    _push_trade(client, vid, {"id": "ZEROFEE", "price": 60000.0, "amount": 0.1, "fee": {"cost": 0.0, "currency": "USDT"}})
    _push_trade(client, vid, {"id": "BASEFEE", "price": 60000.0, "amount": 0.1, "fee": {"cost": 0.0001, "currency": "BTC"}})
    fills = {str(f.trade_id): f for f in events.wait(OrderFilled, 3)}
    assert fills["NOFEE"].commission == 0.0 and fills["NOFEE"].tags["commission_unknown"] is True
    assert "commission_unknown" not in fills["ZEROFEE"].tags and fills["ZEROFEE"].commission == 0.0
    assert (fills["BASEFEE"].commission, fills["BASEFEE"].currency) == (0.0001, "BTC")
    assert "commission_unknown" not in fills["BASEFEE"].tags
