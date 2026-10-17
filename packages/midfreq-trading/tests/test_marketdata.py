"""LiveMarketDataService: bar finality, cross-sections, subscriptions, stalls, warm-up and the store.

A small fake DataClient stands in for a venue; time comes from a TestClock and explicit on_timer
calls, as in a replay. Stores live in temporary folders, never the repository's data/."""
import logging
import math
from types import SimpleNamespace

import pandas as pd
import pytest

from midfreq.core.common.clock import TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.venues.spec import default_venues
from midfreq.data.adapters.base import BarUpdate, ConnectorError, DataClient, StreamStatus
from midfreq.data.storage import DataStorage
from midfreq.trading.interfaces import TOPIC_BAR, CrossSection, StreamEvent
from midfreq.trading.marketdata import (
    DEFAULT_GRACE_S, LiveMarketDataService, SourcedStreamStatus, SubscriptionLimitError,
)

H1 = "1-HOUR"
MIN = 60 * 10**9
SEC = 10**9
GRACE = int(DEFAULT_GRACE_S * SEC)


def et(s: str) -> int:
    return pd.Timestamp(s, tz="America/New_York").tz_convert("UTC").value


def hkt(s: str) -> int:
    return pd.Timestamp(s, tz="Asia/Hong_Kong").tz_convert("UTC").value


def btype(symbol: str, spec: str = H1) -> BarType:
    return BarType.of(symbol, spec)


def mkbar(symbol: str, close_ns: int, close: float = 100.0, volume: float = 10.0, spec: str = H1) -> Bar:
    return Bar(btype(symbol, spec), close - 0.5, close + 1.0, close - 1.0, close, volume, close_ns, close_ns)


# ── fakes ──────────────────────────────────────────────────────────────────────────────────────

class ListQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def drain(self, service):
        items, self.items = self.items, []
        for item in items:
            assert service.dispatch(item)


class FakeDataClient(DataClient):
    """A venue stream: records (un)subscriptions and lets a test push bars, quotes and statuses."""

    def __init__(self, limit=None, name="fake", now_ns=None):
        kwargs = {"now_ns": now_ns} if now_ns is not None else {}
        super().__init__(SimpleNamespace(name=name), **kwargs)
        self.limit = limit
        self.bar_handlers = {}
        self.quote_handlers = {}
        self.calls = []
        self.fail_subscribe = False

    def _request_bars(self, bar_type, venue, start, end):
        return []

    def subscribe_bars(self, bar_type, handler):
        if self.fail_subscribe:
            raise ConnectorError("venue refused the subscription")
        self.calls.append(("sub", str(bar_type)))
        self.bar_handlers[bar_type] = handler

    def unsubscribe_bars(self, bar_type):
        self.calls.append(("unsub", str(bar_type)))
        self.bar_handlers.pop(bar_type, None)

    def subscribe_quotes(self, instrument_id, handler):
        self.calls.append(("subq", str(instrument_id)))
        self.quote_handlers[instrument_id] = handler

    def unsubscribe_quotes(self, instrument_id):
        self.calls.append(("unsubq", str(instrument_id)))
        self.quote_handlers.pop(instrument_id, None)

    def subscription_limit(self):
        return self.limit

    def push(self, bar, closed=False, ts_received=None):
        ts = self._now_ns() if ts_received is None else ts_received
        self.bar_handlers[bar.bar_type](BarUpdate(bar, closed, ts))

    def quote(self, q):
        self.quote_handlers[q.instrument_id](q)


class Recorder:
    def __init__(self, bus):
        self.all = []
        bus.subscribe("data.bars.*", lambda m: self.all.append(("bar", m)))
        bus.subscribe("data.cross_section.*", lambda m: self.all.append(("xs", m)))
        bus.subscribe("data.quotes.*", lambda m: self.all.append(("quote", m)))
        bus.subscribe("events.stream", lambda m: self.all.append(("stream", m)))

    def of(self, kind):
        return [m for k, m in self.all if k == kind]

    @property
    def bars(self):
        return self.of("bar")

    @property
    def xs(self):
        return self.of("xs")

    @property
    def stream(self):
        return self.of("stream")


def make_env(tmp_path, limit=None, start=None, clients=None, store_dir=None, **kwargs):
    clock = TestClock(et("2026-10-16 09:00") if start is None else start)
    client = FakeDataClient(limit=limit, now_ns=clock.timestamp_ns)
    if clients is None:
        clients = {"XNAS": client, "XNYS": client, "XHKG": client}
    bus = MessageBus()
    rec = Recorder(bus)
    queue = ListQueue()
    store = DataStorage(str(store_dir or tmp_path / "data"))
    svc = LiveMarketDataService(clients, bus, queue, store=store, clock=clock, **kwargs)
    return SimpleNamespace(svc=svc, client=client, bus=bus, rec=rec, q=queue, clock=clock, store=store,
                           tmp=tmp_path)


def push(env, bar, closed=False, ts_received=None, client=None):
    (client or env.client).push(bar, closed, ts_received)
    env.q.drain(env.svc)


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


# ── finality ───────────────────────────────────────────────────────────────────────────────────

def test_a_newer_push_makes_the_previous_bar_final_once(env):
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    t1, t2 = et("2026-10-16 10:30"), et("2026-10-16 11:30")
    push(env, mkbar("AAPL.XNAS", t1, 100.0))
    push(env, mkbar("AAPL.XNAS", t1, 101.0))          # the forming bar updates
    assert env.rec.bars == []
    push(env, mkbar("AAPL.XNAS", t2, 102.0))           # newer: 10:30 is final
    assert [(b.ts_event, b.close) for b in env.rec.bars] == [(t1, 101.0)]
    push(env, mkbar("AAPL.XNAS", t2, 103.0))
    env.svc.on_timer(t2 - 1)                           # 11:30 not elapsed yet
    assert len(env.rec.bars) == 1
    assert env.svc.forming_bar(btype("AAPL.XNAS")).close == 103.0
    assert env.svc.last_bar(btype("AAPL.XNAS")).close == 101.0


def test_the_venues_closed_flag_is_final_at_once_and_never_repeated(env, caplog):
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    t1 = et("2026-10-16 10:30")
    push(env, mkbar("AAPL.XNAS", t1, 100.0), closed=True)
    assert [b.close for b in env.rec.bars] == [100.0]
    with caplog.at_level(logging.DEBUG, logger="midfreq.trading.marketdata"):
        push(env, mkbar("AAPL.XNAS", t1, 100.0), closed=True)     # the same bar again
        push(env, mkbar("AAPL.XNAS", t1, 100.5))                  # a correction after it was final
    assert [b.close for b in env.rec.bars] == [100.0]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "late bar dropped" in warnings[0].getMessage()
    assert env.svc.late_bars == 2


def test_the_timer_makes_a_bar_final_after_its_close_plus_grace(env):
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    t = et("2026-10-16 15:30")
    push(env, mkbar("AAPL.XNAS", t, 100.0))
    env.svc.on_timer(t + GRACE - 1)
    assert env.rec.bars == []
    env.svc.on_timer(t + GRACE)
    env.svc.on_timer(t + GRACE + 5 * SEC)
    assert [b.ts_event for b in env.rec.bars] == [t]


def test_a_bar_that_ends_a_session_waits_for_the_next_open_or_for_stop(tmp_path):
    """The original made the lunch-break and closing bars final only on the next push or at
    shutdown, so no decision was ever taken on them while the market was shut."""
    env = make_env(tmp_path, start=hkt("2026-10-16 09:00"))
    hk = [f"{i:05d}.XHKG" for i in range(1, 11)]
    env.svc.subscribe_bars_many([btype(i) for i in hk], universe="hk")
    noon, reopen = hkt("2026-10-16 12:00"), hkt("2026-10-16 13:00")
    for iid in hk[:9]:
        push(env, mkbar(iid, noon))
    env.svc.on_timer(noon + GRACE)
    env.svc.on_timer(reopen + GRACE - 1)
    assert env.rec.bars == [] and env.rec.xs == [] and env.rec.stream == []
    env.svc.on_timer(reopen + GRACE)
    assert len(env.rec.bars) == 9 and [(x.ts_event, x.complete) for x in env.rec.xs] == [(noon, False)]

    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    close = et("2026-10-16 16:00")
    push(env, mkbar("AAPL.XNAS", close))
    env.svc.on_timer(close + 5 * GRACE)                # after the close: still forming
    assert env.rec.bars[-1].instrument_id != InstrumentId.from_str("AAPL.XNAS")
    env.svc.stop(now_ns=close + 2 * MIN)               # LE 675: final at shutdown
    assert env.rec.bars[-1].ts_event == close


def test_an_early_close_is_a_session_end_too(tmp_path):
    env = make_env(tmp_path, start=et("2026-11-27 09:00"))
    env.svc.subscribe_bars(btype("JPM.XNYS"))
    t = et("2026-11-27 13:00")                         # the day after Thanksgiving closes at 13:00
    push(env, mkbar("JPM.XNYS", t))
    env.svc.on_timer(t + 10 * GRACE)
    assert env.rec.bars == []
    env.svc.on_timer(et("2026-11-30 09:30") + GRACE)   # the next open is Monday
    assert [b.ts_event for b in env.rec.bars] == [t]


def test_a_failing_subscriber_does_not_break_the_service(env, caplog):
    def broken(bar):
        raise RuntimeError("strategy bug")
    env.bus.subscribe(TOPIC_BAR.format(bar_type=btype("AAPL.XNAS")), broken)
    env.svc.subscribe_bars(btype("AAPL.XNAS"), universe="u")
    with caplog.at_level(logging.ERROR, logger="midfreq.trading.marketdata"):
        push(env, mkbar("AAPL.XNAS", et("2026-10-16 10:30")), closed=True)
    assert len(env.rec.bars) == 1 and len(env.rec.xs) == 1
    assert any("failed on Bar" in r.getMessage() for r in caplog.records)


def test_a_push_older_than_the_forming_bar_is_dropped_and_does_not_replace_it(env, caplog):
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    t1, t2, t3 = et("2026-10-16 10:30"), et("2026-10-16 11:30"), et("2026-10-16 12:30")
    push(env, mkbar("AAPL.XNAS", t2, 102.0))           # forming 11:30, nothing before it
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.marketdata"):
        push(env, mkbar("AAPL.XNAS", t1, 99.0))        # a straggler from 10:30
    assert any("late bar dropped" in r.getMessage() for r in caplog.records)
    push(env, mkbar("AAPL.XNAS", t3, 103.0))
    assert [(b.ts_event, b.close) for b in env.rec.bars] == [(t2, 102.0)]


def test_a_bar_made_final_by_the_timer_drops_later_pushes_for_it(env):
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    t1, t2 = et("2026-10-16 10:30"), et("2026-10-16 11:30")
    push(env, mkbar("AAPL.XNAS", t1, 100.0))
    env.svc.on_timer(t1 + GRACE)
    push(env, mkbar("AAPL.XNAS", t1, 100.7))           # too late
    push(env, mkbar("AAPL.XNAS", t2, 101.0))
    env.svc.on_timer(t2 + GRACE)
    assert [(b.ts_event, b.close) for b in env.rec.bars] == [(t1, 100.0), (t2, 101.0)]


def test_the_newer_push_rule_matches_the_original_engine(env):
    """The same pushes through the original engine's _on_candle_update and through the service
    close the same candles, in the same order, with the same prices."""
    live_engine = pytest.importorskip("midfreq.live.live_engine")
    from midfreq.core.candle import Candle

    closed = []
    original = SimpleNamespace(_forming={}, _on_candle_closed=lambda s, c: closed.append((s, c.timestamp, c.close)))
    legacy = {"AAPL.XNAS": "US.AAPL", "MSFT.XNAS": "US.MSFT", "JPM.XNYS": "US.JPM"}
    for iid in legacy:
        env.svc.subscribe_bars(btype(iid))
    pushes = [("AAPL.XNAS", "10:30", 1.0), ("AAPL.XNAS", "10:30", 1.1), ("MSFT.XNAS", "10:30", 2.0),
              ("JPM.XNYS", "10:30", 3.0), ("AAPL.XNAS", "11:30", 1.2), ("MSFT.XNAS", "11:30", 2.1),
              ("MSFT.XNAS", "11:30", 2.2), ("JPM.XNYS", "11:30", 3.1), ("AAPL.XNAS", "12:30", 1.3),
              ("JPM.XNYS", "12:30", 3.2), ("MSFT.XNAS", "12:30", 2.3), ("MSFT.XNAS", "12:30", 2.4)]
    for iid, hhmm, px in pushes:
        local = f"2026-10-16 {hhmm}"
        # Moomoo's time_key is the local close; the original provider tagged it UTC.
        live_engine.LiveTradingEngine._on_candle_update(
            original, legacy[iid], Candle(timestamp=pd.to_datetime(local, utc=True), open=px, high=px,
                                          low=px, close=px, volume=1.0))
        push(env, mkbar(iid, et(local), px))
    venues = default_venues()
    mine = [(legacy[str(b.instrument_id)],
             venues.get(b.instrument_id.venue).to_legacy_index([b.ts_event], b.bar_type.spec)[0], b.close)
            for b in env.rec.bars]
    assert [(s, pd.Timestamp(t), c) for s, t, c in closed] == mine
    assert len(mine) == 6


# ── cross-sections ─────────────────────────────────────────────────────────────────────────────

def universe(env, n, name="u", venue="XNAS"):
    ids = [f"S{i}.{venue}" for i in range(n)]
    env.svc.subscribe_bars_many([btype(i) for i in ids], universe=name)
    return ids


def test_a_cross_section_is_published_as_soon_as_every_instrument_has_its_bar(env):
    ids = universe(env, 3)
    t = et("2026-10-16 10:30")
    for i, iid in enumerate(ids):
        push(env, mkbar(iid, t, 10.0 + i), closed=True)
    assert len(env.rec.xs) == 1
    xs = env.rec.xs[0]
    assert isinstance(xs, CrossSection)
    assert xs.universe == "u" and xs.ts_event == t and xs.complete and xs.missing == ()
    assert [str(i) for i in xs.bars] == ids
    # published right after the bar that completed it
    assert [k for k, _ in env.rec.all] == ["bar", "bar", "bar", "xs"]
    env.svc.on_timer(t + 2 * GRACE)
    assert len(env.rec.xs) == 1


def test_an_incomplete_cross_section_needs_its_window_to_elapse_and_90_percent(env, caplog):
    ids = universe(env, 10)
    t = et("2026-10-16 10:30")
    for iid in ids[:9]:
        push(env, mkbar(iid, t))                       # forming, made final by the timer
    env.svc.on_timer(t + GRACE - 1)
    assert env.rec.bars == [] and env.rec.xs == []
    env.svc.on_timer(t + GRACE)
    assert len(env.rec.bars) == 9 and len(env.rec.xs) == 1
    xs = env.rec.xs[0]
    assert not xs.complete and xs.missing == (InstrumentId.from_str(ids[9]),) and len(xs.bars) == 9
    assert [k for k, _ in env.rec.all][-1] == "xs"     # after all nine bars, not in the middle
    # The tenth reports afterwards: published as its bar, no second cross-section for 10:30.
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.marketdata"):
        push(env, mkbar(ids[9], t))
        env.svc.on_timer(t + GRACE + SEC)
    assert len(env.rec.bars) == 10 and len(env.rec.xs) == 1
    assert any("after that cross-section was decided" in r.getMessage() for r in caplog.records)


def test_below_the_minimum_share_there_is_no_cross_section_but_a_gap_event(env):
    ids = universe(env, 10)
    t1, t2 = et("2026-10-16 10:30"), et("2026-10-16 11:30")
    for iid in ids[:8]:
        push(env, mkbar(iid, t1), closed=True)
    env.svc.on_timer(t1 + GRACE)
    assert env.rec.xs == []
    gaps = [e for e in env.rec.stream if e.kind == "gap"]
    assert len(gaps) == 1 and "8 of 10" in gaps[0].detail
    for iid in ids:
        push(env, mkbar(iid, t2), closed=True)
    assert [(x.ts_event, x.complete) for x in env.rec.xs] == [(t2, True)]


@pytest.mark.parametrize("reported, published", [(18, True), (17, False)])
def test_the_minimum_is_the_original_engines_ceil_of_90_percent(env, reported, published):
    assert math.ceil(0.9 * 20) == 18
    ids = universe(env, 20)
    t = et("2026-10-16 10:30")
    for iid in ids[:reported]:
        push(env, mkbar(iid, t), closed=True)
    env.svc.on_timer(t + GRACE)
    assert bool(env.rec.xs) is published


def test_a_later_intervals_bar_decides_the_earlier_cross_section_without_a_timer(env):
    """candle_strategies 1048-1052: a new timestamp closes the cross-section being assembled."""
    ids = universe(env, 10)
    t1, t2 = et("2026-10-16 10:30"), et("2026-10-16 11:30")
    for iid in ids[:9]:
        push(env, mkbar(iid, t1), closed=True)
    assert env.rec.xs == []
    push(env, mkbar(ids[0], t2), closed=True)
    assert [(x.ts_event, x.complete, len(x.bars)) for x in env.rec.xs] == [(t1, False, 9)]


def test_a_universe_holds_one_bar_specification(env):
    env.svc.subscribe_bars(btype("AAPL.XNAS"), universe="u")
    with pytest.raises(ValueError, match="universe 'u'"):
        env.svc.subscribe_bars(btype("MSFT.XNAS", "1-MINUTE"), universe="u")


# ── subscriptions ──────────────────────────────────────────────────────────────────────────────

def test_one_venue_subscription_per_bar_type_however_many_subscribers(env):
    bt = btype("AAPL.XNAS")
    env.svc.subscribe_bars(bt, universe="a")
    env.svc.subscribe_bars(bt)
    env.svc.subscribe_bars(bt, universe="b")
    assert env.client.calls == [("sub", str(bt))]
    assert env.svc.universe("a") == (bt.instrument_id,) and env.svc.universe("b") == (bt.instrument_id,)
    env.svc.unsubscribe_bars(bt, universe="a")
    env.svc.unsubscribe_bars(bt)
    assert env.client.calls == [("sub", str(bt))] and env.svc.universe("a") == ()
    env.svc.unsubscribe_bars(bt, universe="b")
    assert env.client.calls == [("sub", str(bt)), ("unsub", str(bt))]
    push_ignored = BarUpdate(mkbar("AAPL.XNAS", et("2026-10-16 10:30")), True, 0)
    env.svc.on_bar_update(push_ignored)
    assert env.rec.bars == []


def test_the_subscription_limit_is_refused_with_a_clear_error(tmp_path):
    env = make_env(tmp_path, limit=3)
    for s in ("A", "B", "C"):
        env.svc.subscribe_bars(btype(f"{s}.XNAS"))
    env.svc.subscribe_bars(btype("A.XNAS"))            # a second subscriber needs no new stream
    with pytest.raises(SubscriptionLimitError, match="allows 3 streams at once.*3 in use and 1 more"):
        env.svc.subscribe_bars(btype("D.XNAS"))
    assert ("sub", str(btype("D.XNAS"))) not in env.client.calls
    env.svc.unsubscribe_bars(btype("C.XNAS"))
    env.svc.subscribe_bars(btype("D.XNAS"))
    assert env.svc.subscriptions()["FAKE"] == {"in_use": 3, "limit": 3, "unit": "stream"}


def test_a_universe_that_does_not_fit_is_refused_before_any_of_it_is_subscribed(tmp_path):
    env = make_env(tmp_path, limit=3)
    with pytest.raises(SubscriptionLimitError):
        env.svc.subscribe_bars_many([btype(f"S{i}.XNAS") for i in range(5)], universe="u")
    assert env.client.calls == [] and env.svc.universe("u") == ()


def test_quotes_count_against_the_limit_as_streams_or_as_instruments(tmp_path):
    env = make_env(tmp_path, limit=2)
    env.svc.subscribe_bars(btype("A.XNAS"))
    env.svc.subscribe_quotes("A.XNAS")
    with pytest.raises(SubscriptionLimitError):
        env.svc.subscribe_bars(btype("B.XNAS"))
    per_instrument = make_env(tmp_path, limit=2, limit_unit="instrument")
    per_instrument.svc.subscribe_bars(btype("A.XNAS"))
    per_instrument.svc.subscribe_quotes("A.XNAS")
    per_instrument.svc.subscribe_bars(btype("B.XNAS"))
    with pytest.raises(SubscriptionLimitError, match="allows 2 instruments"):
        per_instrument.svc.subscribe_quotes("C.XNAS")


def test_venues_served_by_one_client_share_its_limit(tmp_path):
    env = make_env(tmp_path, limit=2)                  # one client for XNAS, XNYS and XHKG
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    env.svc.subscribe_bars(btype("JPM.XNYS"))
    with pytest.raises(SubscriptionLimitError):
        env.svc.subscribe_bars(btype("00700.XHKG"))
    clock = TestClock(0)
    us, hk = FakeDataClient(limit=1, name="us", now_ns=clock.timestamp_ns), FakeDataClient(limit=1, name="hk")
    separate = LiveMarketDataService({"XNAS": us, "XHKG": hk}, MessageBus(), ListQueue(),
                                     store=DataStorage(str(tmp_path / "s")), clock=clock)
    separate.subscribe_bars(btype("AAPL.XNAS"))
    separate.subscribe_bars(btype("00700.XHKG"))
    with pytest.raises(ValueError, match="no data client for venue XNYS"):
        separate.subscribe_bars(btype("JPM.XNYS"))


def test_quotes_are_cached_and_published_for_subscribed_instruments_only(env):
    env.svc.subscribe_quotes("AAPL.XNAS")
    env.svc.subscribe_quotes("AAPL.XNAS")
    assert env.client.calls == [("subq", "AAPL.XNAS")]
    q = QuoteTick(InstrumentId.from_str("AAPL.XNAS"), 99.9, 100.1, 5, 7, 1, 1)
    env.client.quote(q)
    env.q.drain(env.svc)
    assert env.rec.of("quote") == [q] and env.svc.last_quote("AAPL.XNAS") == q
    assert env.svc.last_price("AAPL.XNAS") == pytest.approx(100.0)
    env.svc.on_quote(QuoteTick(InstrumentId.from_str("MSFT.XNAS"), 1, 2, 1, 1, 1, 1))
    assert len(env.rec.of("quote")) == 1
    env.svc.unsubscribe_quotes("AAPL.XNAS")
    env.svc.unsubscribe_quotes("AAPL.XNAS")
    assert env.client.calls[-1] == ("unsubq", "AAPL.XNAS")


def test_connector_handlers_only_enqueue(env):
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    handlers = env.svc.stream_handlers("XNAS")
    handlers.bar_update(BarUpdate(mkbar("AAPL.XNAS", et("2026-10-16 10:30")), True, 1))
    handlers.stream_status(StreamStatus("disconnected", "socket closed", 5))
    env.client._stream_status("reconnected", "subscriptions restored")   # the handler set at construction
    assert env.rec.all == [] and len(env.q.items) == 3
    status = env.q.items[1]
    assert isinstance(status, SourcedStreamStatus) and status.source == "FAKE"
    assert status.venues == ("XHKG", "XNAS", "XNYS")
    env.q.drain(env.svc)
    assert len(env.rec.bars) == 1
    assert [(e.kind, e.detail) for e in env.rec.stream] == [
        ("disconnected", "FAKE: socket closed"), ("reconnected", "FAKE: subscriptions restored")]
    assert env.svc.dispatch(object()) is False


# ── stalls ─────────────────────────────────────────────────────────────────────────────────────

def stalls(env):
    return [(e.kind, e.detail) for e in env.rec.stream if e.kind in ("stall", "error")]


def test_a_stall_resubscribes_once_and_a_second_stall_is_fatal(tmp_path):
    start = et("2026-10-16 10:00")
    env = make_env(tmp_path, start=start)
    bt = btype("AAPL.XNAS")
    env.svc.subscribe_bars(bt)
    env.svc.start()
    env.svc.on_timer(start + 75 * MIN)                 # exactly 1.25 hours: not yet (LE 579 is '>')
    assert stalls(env) == []
    env.svc.on_timer(start + 75 * MIN + SEC)
    assert len(stalls(env)) == 1 and stalls(env)[0][0] == "stall" and "resubscribing" in stalls(env)[0][1]
    assert env.client.calls == [("sub", str(bt)), ("unsub", str(bt)), ("sub", str(bt))]
    assert not env.svc.fatal_stall
    second = start + 75 * MIN + SEC + 75 * MIN + SEC
    env.svc.on_timer(second - SEC)
    assert len(stalls(env)) == 1
    env.svc.on_timer(second)
    assert stalls(env)[-1] == ("stall", "fatal") and env.svc.fatal_stall
    env.svc.on_timer(second + 120 * MIN)
    assert len(stalls(env)) == 2


def test_bar_updates_keep_a_stream_alive_and_nothing_counts_before_start(tmp_path):
    start = et("2026-10-16 10:00")
    env = make_env(tmp_path, start=start)
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    env.svc.on_timer(start + 300 * MIN)                # not started: no stall detection
    assert stalls(env) == []
    env.clock.set_time(start + 300 * MIN)
    env.svc.start()
    t0 = start + 300 * MIN                             # 15:00 ET
    push(env, mkbar("AAPL.XNAS", et("2026-10-16 15:30")), ts_received=t0 + 30 * MIN)
    env.svc.on_timer(t0 + 30 * MIN + 30 * MIN)         # 16:00; 30 min after the push
    assert stalls(env) == []


def test_a_failed_resubscribe_is_fatal_at_once(tmp_path):
    start = et("2026-10-16 10:00")
    env = make_env(tmp_path, start=start)
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    env.svc.start()
    env.client.fail_subscribe = True
    env.svc.on_timer(start + 76 * MIN)
    kinds = stalls(env)
    assert kinds[0][0] == "stall" and kinds[1][0] == "error" and kinds[2] == ("stall", "fatal")
    assert env.svc.fatal_stall


def test_the_hong_kong_lunch_break_and_holidays_do_not_count_as_silence(tmp_path):
    start = hkt("2026-10-16 11:00")
    env = make_env(tmp_path, start=start)
    env.svc.subscribe_bars(btype("00700.XHKG"))
    env.svc.start()
    push(env, mkbar("00700.XHKG", hkt("2026-10-16 12:00")), ts_received=hkt("2026-10-16 11:59"))
    env.svc.on_timer(hkt("2026-10-16 13:30"))          # 91 min of wall time, 31 of session
    assert stalls(env) == []
    env.svc.on_timer(hkt("2026-10-16 14:15"))          # 1 + 75 = 76 min of session
    assert len(stalls(env)) == 1


def test_nights_weekends_and_holidays_do_not_count_as_silence(tmp_path):
    start = hkt("2026-10-16 15:55")                    # Friday; Monday 19 Oct is a Hong Kong holiday
    env = make_env(tmp_path, start=start)
    env.svc.subscribe_bars(btype("00700.XHKG"))
    env.svc.start()
    env.svc.on_timer(hkt("2026-10-20 10:30"))          # 5 + 60 = 65 min of session
    assert stalls(env) == []
    env.svc.on_timer(hkt("2026-10-20 10:41"))          # 5 + 71 = 76
    assert len(stalls(env)) == 1


# ── warm-up and the store ──────────────────────────────────────────────────────────────────────

def store_frame(labels, start_px=100.0):
    idx = pd.DatetimeIndex(pd.to_datetime(labels, utc=True), name="timestamp")
    px = [start_px + i for i in range(len(labels))]
    return pd.DataFrame({"open": px, "high": [p + 1 for p in px], "low": [p - 1 for p in px],
                         "close": px, "volume": [1000.0] * len(px)}, index=idx)


US_DAY = ["10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:00"]


def test_warm_up_converts_the_stores_local_labels_to_true_utc(tmp_path):
    env = make_env(tmp_path, start=et("2026-10-16 12:00"))
    labels = [f"2026-10-15 {h}" for h in US_DAY] + ["2026-10-16 10:30", "2026-10-16 11:30", "2026-10-16 12:30"]
    env.store.save_data(store_frame(labels), "US_AAPL", "1h")
    bars = env.svc.warm_up(btype("AAPL.XNAS"), 5)
    # 12:30 has not closed at 12:00 ET; the last five closed bars, at their true UTC closes
    assert [b.ts_event for b in bars] == [et(f"2026-10-15 {h}") for h in ("14:30", "15:30", "16:00")] + \
        [et("2026-10-16 10:30"), et("2026-10-16 11:30")]
    assert bars[-1].timestamp == pd.Timestamp("2026-10-16 15:30", tz="UTC")       # 11:30 EDT
    assert [b.close for b in bars] == [104.0, 105.0, 106.0, 107.0, 108.0]
    assert all(b.ts_init == b.ts_event and b.bar_type == btype("AAPL.XNAS") for b in bars)

    env.store.save_data(store_frame(["2026-10-16 10:30", "2026-10-16 11:30"]), "HK_00700", "1h")
    hk = env.svc.warm_up(btype("00700.XHKG"), 60)
    assert [b.timestamp for b in hk] == [pd.Timestamp("2026-10-16 02:30", tz="UTC"),
                                         pd.Timestamp("2026-10-16 03:30", tz="UTC")]

    env.store.save_data(store_frame(["2026-10-14", "2026-10-15"]), "US_AAPL", "1d")
    daily = env.svc.warm_up(btype("AAPL.XNAS", "1-DAY"), 60)
    assert [b.ts_event for b in daily] == [et("2026-10-14 16:00"), et("2026-10-15 16:00")]


def test_streamed_bars_already_in_the_warm_up_are_dropped(tmp_path):
    env = make_env(tmp_path, start=et("2026-10-16 12:00"))
    env.store.save_data(store_frame(["2026-10-16 10:30", "2026-10-16 11:30"]), "US_AAPL", "1h")
    env.svc.warm_up(btype("AAPL.XNAS"), 60)
    env.svc.subscribe_bars(btype("AAPL.XNAS"))
    push(env, mkbar("AAPL.XNAS", et("2026-10-16 11:30")), closed=True)
    push(env, mkbar("AAPL.XNAS", et("2026-10-16 12:30")), closed=True)
    assert [b.ts_event for b in env.rec.bars] == [et("2026-10-16 12:30")]


def test_warm_up_without_cached_bars_is_empty_and_leaves_no_folder(tmp_path, caplog):
    env = make_env(tmp_path)
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.marketdata"):
        assert env.svc.warm_up(btype("MSFT.XNAS"), 60) == []
        assert env.svc.warm_up(btype("MSFT.XNAS", "15-MINUTE"), 60) == []
    assert not (tmp_path / "data" / "US_MSFT").exists()
    assert len(caplog.records) == 2


def run_session(env):
    """AAPL and Tencent hourly bars over part of a day, made final by newer pushes and at stop."""
    for iid in ("AAPL.XNAS", "00700.XHKG"):
        env.svc.subscribe_bars(btype(iid))
    for i, h in enumerate(US_DAY):
        push(env, mkbar("AAPL.XNAS", et(f"2026-10-16 {h}"), 200.0 + i, volume=1234.0 + i))
    for i, h in enumerate(["10:30", "11:30", "12:00", "14:00", "15:00", "16:00"]):
        push(env, mkbar("00700.XHKG", hkt(f"2026-10-16 {h}"), 500.0 + i, volume=99.0))


def test_stop_finalises_what_has_elapsed_unsubscribes_and_writes_the_store(tmp_path):
    env = make_env(tmp_path, start=et("2026-10-16 09:00"))
    run_session(env)
    report = env.svc.stop(now_ns=et("2026-10-16 15:59"))        # 16:00 has not elapsed
    assert ("unsub", str(btype("AAPL.XNAS"))) in env.client.calls
    assert report.bars_written == 6 + 6 and report.instruments_written == 2 and report.failed == 0
    df = pd.read_parquet(tmp_path / "data" / "US_AAPL" / "1h.parquet")
    assert str(df.index.dtype) == "datetime64[ns, UTC]" and df.index.name == "timestamp"
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert all(str(t) == "float64" for t in df.dtypes)
    assert list(df.index.strftime("%H:%M")) == US_DAY[:-1]      # local close times, labelled UTC
    hk = pd.read_parquet(tmp_path / "data" / "HK_00700" / "1h.parquet")
    assert list(hk.index.strftime("%H:%M")) == ["10:30", "11:30", "12:00", "14:00", "15:00", "16:00"]
    assert env.svc.stop() == report                              # a second stop does nothing


def test_stop_after_the_close_includes_the_last_bar_and_decides_its_cross_section(tmp_path):
    env = make_env(tmp_path)
    ids = universe(env, 10)
    t = et("2026-10-16 16:00")
    for iid in ids[:9]:
        push(env, mkbar(iid, t))
    env.svc.stop(now_ns=t + 90 * SEC)
    assert len(env.rec.bars) == 9
    assert [(x.ts_event, x.complete) for x in env.rec.xs] == [(t, False)]


def test_the_store_write_is_what_the_original_engine_wrote(tmp_path, monkeypatch):
    """The original engine's own _persist_candles, given the candles Moomoo pushed, and the
    service's stop(), given the same bars, leave identical files, on top of the same history."""
    live_engine = pytest.importorskip("midfreq.live.live_engine")
    import midfreq.data.storage as storage_module
    from midfreq.core.candle import Candle, Timeframe

    history = store_frame([f"2026-10-15 {h}" for h in US_DAY], start_px=150.0)
    hk_history = store_frame(["2026-10-15 15:00", "2026-10-15 16:00"], start_px=480.0)
    mine_dir, orig_dir = tmp_path / "mine", tmp_path / "orig"
    for d in (mine_dir, orig_dir):
        s = DataStorage(str(d))
        s.save_data(history, "US_AAPL", "1h")
        s.save_data(hk_history, "HK_00700", "1h")

    env = make_env(tmp_path, start=et("2026-10-16 09:00"), store_dir=mine_dir)
    run_session(env)
    env.svc.stop(now_ns=et("2026-10-16 16:01"))

    closed = {}
    for bar in env.rec.bars:                           # what Moomoo pushed: local close as time_key
        legacy = "US.AAPL" if bar.instrument_id.venue.value == "XNAS" else "HK.00700"
        tz = "America/New_York" if legacy.startswith("US") else "Asia/Hong_Kong"
        time_key = bar.timestamp.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")
        closed.setdefault(legacy, []).append(Candle(
            timestamp=pd.to_datetime(time_key, utc=True), open=bar.open, high=bar.high, low=bar.low,
            close=bar.close, volume=int(bar.volume)))
    monkeypatch.setattr(storage_module, "DataStorage", lambda: DataStorage(str(orig_dir)))
    live_engine.LiveTradingEngine._persist_candles(
        SimpleNamespace(_closed_candles=closed, timeframe=Timeframe.HOUR_1))

    for key in ("US_AAPL", "HK_00700"):
        pd.testing.assert_frame_equal(pd.read_parquet(mine_dir / key / "1h.parquet"),
                                      pd.read_parquet(orig_dir / key / "1h.parquet"))
    assert len(pd.read_parquet(mine_dir / "US_AAPL" / "1h.parquet")) == 14


def test_bars_written_at_stop_warm_the_next_session_up_at_the_same_times(tmp_path):
    env = make_env(tmp_path)
    run_session(env)
    env.svc.stop(now_ns=et("2026-10-16 16:01"))
    streamed = [b for b in env.rec.bars if b.instrument_id.symbol.value == "AAPL"]
    nxt = make_env(tmp_path, start=et("2026-10-19 09:00"))
    warm = nxt.svc.warm_up(btype("AAPL.XNAS"), 60)
    assert [(b.ts_event, b.close, b.volume) for b in warm] == [(b.ts_event, b.close, b.volume) for b in streamed]


def test_stream_events_use_the_interface_type(env):
    env.svc.on_stream_status(StreamStatus("weird", "unknown state", 7))
    assert env.rec.stream == [StreamEvent("error", "unknown state", 7)]
