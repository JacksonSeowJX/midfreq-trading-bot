"""
LiveMarketDataService: the trading node's one source of market data (docs/LIVE_ARCHITECTURE.md).

Every strategy's bars and quotes come from here, over the message bus, and every venue stream is
opened once however many strategies want it. The connectors' push threads never call in: the
handlers given to the DataClients (`stream_handlers()`) only put the pushes on the node's
EventQueue, and the node's loop hands them back to `on_bar_update`, `on_quote` and
`on_stream_status`, and calls `on_timer` with its clock. So everything below runs on one thread,
and a session replays exactly from the same queue items and timer calls.

The rules, and the code of the original engine (midfreq.live.live_engine, "LE" below; line
numbers as of commit 5237559) or of the candle strategies (midfreq.strategies.candle_strategies,
"CS") that decides the same thing:

Finality (a bar is published on TOPIC_BAR once, when it is final)
  - a newer push for the same bar type makes the forming bar final .......... LE 473-475
  - the venue's own `closed` flag makes the pushed bar final at once ........ (new: connectors
    whose venue marks bars final; the original's Moomoo stream never did)
  - `on_timer(now)`: a forming bar is final when now >= its close + grace .. LE 633-641, which
    did this only at shutdown with no grace; here the timer does it during the session too.
    A bar that ends a session (Hong Kong's 12:00 lunch break, the day's close, an early close)
    waits instead for the next session's open + grace: the original made such a bar final only
    on the next push (13:00 after lunch) or at shutdown with trading off (LE 603-611, 670-675),
    so no decision is ever taken on it while the market is shut
  - `stop()`: every forming bar whose window has elapsed (close <= now) ...... LE 603-641, 675
  - oldest first when several become final together .......................... LE 629-633
  - a push older than the forming bar, or not newer than the bar already
    published, is dropped with a WARNING and never published (the original
    let an older push replace the forming candle, LE 477, and relied on the
    strategy to drop the resulting late candle, CS 1041-1047)
Cross-sections (published on TOPIC_CROSS_SECTION, once per universe and interval)
  - complete as soon as every instrument of the universe has its final bar
    for the interval .......................................................... LE 366-371 + CS 1065-1075
  - otherwise decided when the interval's window has elapsed (the same moment
    the timer makes its bars final, or close <= now at stop) or a later
    interval has a final bar .................................................. CS 1048-1052
    and then published only if at least ceil(min_share * N) instruments
    reported (min_share 0.9) .................................................. LE 361-363 + CS 1091-1097
    with `missing` listing the others; below that, no cross-section, and a
    StreamEvent('gap') says so (the strategy counted that cross-section but
    did not trade on it)
  - a final bar for an interval already decided is still published on
    TOPIC_BAR, but joins no cross-section (logged) ............................. CS 1046-1047
Stalls (TOPIC_STREAM)
  - no bar update for 1.25 intervals of session time on a stream ............. LE 322-325, 571-579
    -> StreamEvent('stall', ...) and the stream is resubscribed once ......... LE 583-593
  - stalled again (or the resubscribe failed) -> StreamEvent('stall', 'fatal')
    and `fatal_stall` is True: the node stops cleanly ......................... LE 594-597
  - the clock starts when the service starts, not at the first bar ........... LE 554-557
  - time outside the venue's sessions (nights, weekends, holidays, Hong Kong's
    lunch break) does not count; daily bars count wall time as the original did
Warm-up and the store
  - `warm_up(bar_type, count)`: the last `count` bars from the local store
    (midfreq.data.storage.DataStorage), never the broker's metered history ... LE 373-431
    (the original fell back to the broker; this never does, and warns when the
    cache is stale by the original's own window). The store labels intraday
    bars with the exchange-local close time tagged as UTC and daily bars with
    local midnight; warm-up converts both to true UTC closes, as streamed bars
    carry.
  - `stop()`: unsubscribe, finalise what has elapsed, then append the
    session's bars to the store in the store's own convention (columns
    open/high/low/close/volume as floats, index 'timestamp' = local close
    tagged UTC, file <MKT>_<CODE>/<tf>.parquet) ............................... LE 643-667, 669-683

The store is DataStorage() unless one is given, i.e. the data/ folder of MIDFREQ_ROOT
(midfreq.core.paths); tests pass a DataStorage on a temporary folder.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

import pandas as pd

from midfreq.core.common.clock import Clock, LiveClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import BAR_COLUMNS, Bar, BarSpecification, BarType, QuoteTick
from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id, as_venue
from midfreq.core.venues.spec import VenueRegistry, VenueSpec, default_venues
from midfreq.data.adapters.base import BarUpdate, DataClient, StreamStatus
from midfreq.data.storage import DataStorage
from midfreq.trading.calendar import VenueCalendar
from midfreq.trading.interfaces import (
    TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_QUOTE, TOPIC_STREAM, CrossSection, EventQueue, ReferenceData,
    StreamEvent, TradingCalendar,
)

log = logging.getLogger(__name__)

__all__ = [
    "LiveMarketDataService", "PersistReport", "SourcedStreamStatus", "StreamHandlers", "SubscriptionLimitError",
    "DEFAULT_GRACE_S", "DEFAULT_MIN_SHARE", "DEFAULT_STALL_FACTOR", "DEFAULT_WARM_UP",
]

DEFAULT_GRACE_S = 60.0          # a forming bar is final this long after its close without a successor
DEFAULT_MIN_SHARE = 0.9         # LE 363: math.ceil(0.9 * len(symbols))
DEFAULT_STALL_FACTOR = 1.25     # LE 325: 1.25 candle periods of silence
DEFAULT_WARM_UP = 60            # LE 373: _warm_up(candles=60)

# LE 381-382: the window the original engine wanted `count` cached candles inside, else it went
# to the broker. Used here only to warn that the cache is stale.
_WARM_UP_LOOKBACK = {"1m": timedelta(days=3), "5m": timedelta(days=10),
                     "1h": timedelta(days=30), "1d": timedelta(days=200)}

_STREAM_STATES = {"connected", "disconnected", "reconnected", "error"}
_NS_PER_MIN = 60 * 1_000_000_000


class SubscriptionLimitError(RuntimeError):
    """A subscription would take a venue account past the number of streams it may hold."""


@dataclass(frozen=True)
class SourcedStreamStatus(StreamStatus):
    """A StreamStatus tagged with the client it came from, so the service knows which streams it
    is about. Still a StreamStatus for anyone dispatching on the type."""
    source: str = ""
    venues: Tuple[str, ...] = ()


@dataclass(frozen=True)
class StreamHandlers:
    """The callables to give a DataClient: each only puts what it receives on the node's queue."""
    bar_update: Callable[[BarUpdate], None]
    quote: Callable[[QuoteTick], None]
    stream_status: Callable[[StreamStatus], None]


@dataclass(frozen=True)
class PersistReport:
    """What `stop()` wrote to the store."""
    bars_written: int
    instruments_written: int
    failed: int
    errors: Tuple[str, ...] = ()


@dataclass
class _BarState:
    bar_type: BarType
    refs: int = 0
    stream_key: Optional[Tuple[int, BarSpecification]] = None
    forming: Optional[Bar] = None
    last_final_ts: Optional[int] = None     # newest bar published
    last_bar: Optional[Bar] = None
    floor_ts: Optional[int] = None          # newest warm-up bar: anything not newer is history


@dataclass
class _Universe:
    name: str
    spec: BarSpecification
    members: Dict[BarType, int] = field(default_factory=dict)          # bar type -> subscriptions
    pending: Dict[int, Dict[InstrumentId, Bar]] = field(default_factory=dict)
    done_through: Optional[int] = None      # newest interval published or given up as a gap
    published: int = 0
    gaps: int = 0


@dataclass
class _Stream:
    """One live bar stream: one client, one bar specification (all its instruments)."""
    client_index: int
    spec: BarSpecification
    venues: Set[Venue] = field(default_factory=set)
    bar_types: Set[BarType] = field(default_factory=set)
    last_update_ns: Optional[int] = None
    resubscribed: bool = False
    fatal: bool = False


def _iso(ns: int) -> str:
    return pd.Timestamp(int(ns), unit="ns", tz="UTC").strftime("%Y-%m-%d %H:%M:%SZ")


def _merge_ns(intervals: List[Tuple[int, int]]) -> int:
    """Total length of the union of [start, end) intervals."""
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


class LiveMarketDataService:
    """The node's MarketDataService (midfreq.trading.interfaces.MarketDataService)."""

    def __init__(self, clients: Mapping["str | Venue", DataClient], bus: MessageBus, queue: EventQueue, *,
                 venues: Optional[VenueRegistry] = None,
                 refdata: Optional[ReferenceData] = None,
                 calendars: Optional[Mapping["str | Venue", TradingCalendar]] = None,
                 store: Optional[DataStorage] = None,
                 clock: Optional[Clock] = None,
                 grace: "float | timedelta" = DEFAULT_GRACE_S,
                 min_share: float = DEFAULT_MIN_SHARE,
                 stall_factor: float = DEFAULT_STALL_FACTOR,
                 limit_unit: str = "stream"):
        """
        clients: the DataClient serving each venue (one client may serve several venues, as
            Moomoo serves XHKG, XNAS and XNYS; its subscription limit is then shared).
        bus: where bars, cross-sections, quotes and stream events are published.
        queue: the node's EventQueue; the stream handlers put pushes on it.
        venues: the venue registry (config/venues.json by default).
        refdata / calendars: where trading calendars come from (for stall detection); by default
            VenueCalendar from config/venues.json.
        store: the bar store for warm-up and the session's bars (DataStorage() by default, the
            data/ folder of MIDFREQ_ROOT).
        clock: the node's clock (wall clock by default; a TestClock in replays).
        grace: seconds after a bar's close before the timer makes it final without a successor.
        min_share: the share of a universe an incomplete cross-section needs (0.9).
        stall_factor: intervals of silence that count as a stall (1.25).
        limit_unit: what a client's subscription_limit() counts: "stream" (one per instrument and
            data type, which is how Moomoo's quota is charged) or "instrument".
        """
        if limit_unit not in ("stream", "instrument"):
            raise ValueError(f"limit_unit must be 'stream' or 'instrument', not {limit_unit!r}")
        if not 0 < min_share <= 1:
            raise ValueError(f"min_share must be in (0, 1], not {min_share}")
        self._bus = bus
        self._queue = queue
        self._venues = venues or default_venues()
        self._refdata = refdata
        self._calendar_overrides: Dict[Venue, TradingCalendar] = {
            as_venue(v): c for v, c in (calendars or {}).items()}
        self._calendars: Dict[Venue, TradingCalendar] = {}
        self._store = store
        self._clock = clock or LiveClock()
        grace_s = grace.total_seconds() if isinstance(grace, timedelta) else float(grace)
        self._grace_ns = int(round(grace_s * 1e9))
        self._min_share = float(min_share)
        self._stall_factor = float(stall_factor)
        self._limit_unit = limit_unit

        # clients, each once however many venues it serves
        self._client_of: Dict[Venue, int] = {}
        self._clients: List[DataClient] = []
        for v, client in clients.items():
            idx = next((i for i, c in enumerate(self._clients) if c is client), None)
            if idx is None:
                idx = len(self._clients)
                self._clients.append(client)
            self._client_of[as_venue(v)] = idx
        self._limits: Dict[int, Optional[int]] = {}
        self._in_use: Dict[int, Dict[Tuple[str, ...], int]] = {i: {} for i in range(len(self._clients))}

        self._bars: Dict[BarType, _BarState] = {}
        self._universes: Dict[str, _Universe] = {}
        self._streams: Dict[Tuple[int, BarSpecification], _Stream] = {}
        self._quote_refs: Dict[InstrumentId, int] = {}
        self._quotes: Dict[InstrumentId, QuoteTick] = {}
        self._session_bars: Dict[BarType, List[Bar]] = {}
        self._due: Dict[Tuple[Venue, int], int] = {}

        self._started = False
        self._stopped = False
        self._started_ns: Optional[int] = None
        self._fatal = False
        self._persisted: Optional[PersistReport] = None
        self.late_bars = 0
        self.bars_published = 0

        for idx, client in enumerate(self._clients):
            client.set_stream_status_handler(self._status_handler(idx))

    # ── handlers for the connectors (any thread: they only enqueue) ──────────────────────────

    def stream_handlers(self, venue: "Optional[str | Venue]" = None) -> StreamHandlers:
        """The callables to hand a DataClient. Bar and quote pushes go on the queue unchanged; a
        stream status is tagged with the client serving `venue` (when given) first."""
        status = self._status_handler(self._client_of[as_venue(venue)]) if venue is not None else self._queue.put
        return StreamHandlers(bar_update=self._queue.put, quote=self._queue.put, stream_status=status)

    def _status_handler(self, idx: int) -> Callable[[StreamStatus], None]:
        source = self._source(idx)
        venues = tuple(sorted(str(v) for v, i in self._client_of.items() if i == idx))
        put = self._queue.put

        def handler(status: StreamStatus) -> None:
            put(SourcedStreamStatus(status.state, status.detail, status.ts, source=source, venues=venues))
        return handler

    def dispatch(self, item: Any) -> bool:
        """Hand one queue item to the right handler; False if it is not market data."""
        if isinstance(item, BarUpdate):
            self.on_bar_update(item)
        elif isinstance(item, QuoteTick):
            self.on_quote(item)
        elif isinstance(item, StreamStatus):
            self.on_stream_status(item)
        else:
            return False
        return True

    # ── subscriptions ────────────────────────────────────────────────────────────────────────

    def subscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None:
        """Stream `bar_type` (once per bar type, however many callers), as part of `universe`
        when given. Raises SubscriptionLimitError when the venue account has no room for it."""
        self.subscribe_bars_many([bar_type], universe)

    def subscribe_bars_many(self, bar_types: Iterable[BarType], universe: Optional[str] = None) -> None:
        """Several bar types at once, checked against the subscription limit as a whole first, so
        a universe that does not fit is refused before any of it is subscribed."""
        bts = list(dict.fromkeys(bar_types))
        if not bts:
            return
        if self._stopped:
            raise RuntimeError("the market data service has stopped")
        for bt in bts:
            self._client_index(bt.instrument_id.venue)
        if universe is not None:
            spec = self._universes[universe].spec if universe in self._universes else bts[0].spec
            odd = [str(bt) for bt in bts if bt.spec != spec]
            if odd:
                raise ValueError(f"universe {universe!r} is {spec} bars; cannot add {odd}")
        # Room at each client for the streams that are new.
        wanted: Dict[int, Set[Tuple[str, ...]]] = {}
        for bt in bts:
            state = self._bars.get(bt)
            if state is None or state.refs == 0:
                idx = self._client_index(bt.instrument_id.venue)
                wanted.setdefault(idx, set()).add(self._limit_key(bt.instrument_id, f"bars:{bt.spec}"))
        for idx, keys in wanted.items():
            self._check_limit(idx, keys, ", ".join(sorted(str(bt) for bt in bts))[:200])
        if universe is not None and universe not in self._universes:
            self._universes[universe] = _Universe(universe, bts[0].spec)
        for bt in bts:
            state = self._state(bt)
            if state.refs == 0:
                idx = self._client_index(bt.instrument_id.venue)
                self._clients[idx].subscribe_bars(bt, self._queue.put)
                self._acquire(idx, self._limit_key(bt.instrument_id, f"bars:{bt.spec}"))
                stream = self._stream(idx, bt)
                state.stream_key = (idx, bt.spec)
                if self._started and stream.last_update_ns is None:
                    stream.last_update_ns = self._clock.timestamp_ns()
                log.debug("subscribed %s", bt)
            state.refs += 1
            if universe is not None:
                members = self._universes[universe].members
                members[bt] = members.get(bt, 0) + 1

    def unsubscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None:
        """Drop one subscription; the venue stream closes with the last one."""
        state = self._bars.get(bar_type)
        if state is None or state.refs <= 0:
            log.debug("unsubscribe_bars(%s): not subscribed", bar_type)
            return
        if universe is not None and universe in self._universes:
            members = self._universes[universe].members
            if bar_type in members:
                members[bar_type] -= 1
                if members[bar_type] <= 0:
                    del members[bar_type]
        state.refs -= 1
        if state.refs == 0:
            idx = self._client_index(bar_type.instrument_id.venue)
            try:
                self._clients[idx].unsubscribe_bars(bar_type)
            except Exception as exc:                       # noqa: BLE001 - the venue may refuse; we stop listening anyway
                log.warning("unsubscribe %s failed: %s", bar_type, exc)
            self._release(idx, self._limit_key(bar_type.instrument_id, f"bars:{bar_type.spec}"))
            state.forming = None

    def subscribe_quotes(self, instrument_id: "str | InstrumentId") -> None:
        iid = as_instrument_id(instrument_id)
        if self._stopped:
            raise RuntimeError("the market data service has stopped")
        if self._quote_refs.get(iid, 0) == 0:
            idx = self._client_index(iid.venue)
            key = self._limit_key(iid, "quotes")
            self._check_limit(idx, {key}, f"{iid} quotes")
            self._clients[idx].subscribe_quotes(iid, self._queue.put)
            self._acquire(idx, key)
        self._quote_refs[iid] = self._quote_refs.get(iid, 0) + 1

    def unsubscribe_quotes(self, instrument_id: "str | InstrumentId") -> None:
        iid = as_instrument_id(instrument_id)
        refs = self._quote_refs.get(iid, 0)
        if refs <= 0:
            return
        self._quote_refs[iid] = refs - 1
        if refs == 1:
            idx = self._client_index(iid.venue)
            try:
                self._clients[idx].unsubscribe_quotes(iid)
            except Exception as exc:                       # noqa: BLE001
                log.warning("unsubscribe quotes %s failed: %s", iid, exc)
            self._release(idx, self._limit_key(iid, "quotes"))

    def subscriptions(self) -> Dict[str, Dict[str, Any]]:
        """Per client: streams in use and its limit, for the ops log."""
        out = {}
        for idx in range(len(self._clients)):
            out[self._source(idx)] = {"in_use": len(self._in_use[idx]), "limit": self._limit(idx),
                                      "unit": self._limit_unit}
        return out

    def universe(self, name: str) -> Tuple[InstrumentId, ...]:
        u = self._universes.get(name)
        return tuple(bt.instrument_id for bt in u.members) if u else ()

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Arm the stall detector: every stream's clock starts now (LE 557)."""
        if self._started:
            return
        now = self._clock.timestamp_ns()
        self._started, self._started_ns = True, now
        for stream in self._streams.values():
            stream.last_update_ns = now

    def stop(self, now_ns: Optional[int] = None) -> PersistReport:
        """Unsubscribe everything, make final every forming bar whose window has elapsed (and
        decide their cross-sections), then append the session's bars to the store. Trading must
        already be off: the bars finalised here arrive after the market's close (LE 670-675).
        Calling it again does nothing."""
        if self._stopped:
            return self._persisted or PersistReport(0, 0, 0)
        now = self._clock.timestamp_ns() if now_ns is None else int(now_ns)
        self._stopped = True
        self._unsubscribe_all()
        self._finalise_elapsed(now, stopping=True)
        for u in list(self._universes.values()):
            self._evaluate(u, now, stopping=True)
        self._persisted = self._persist()
        return self._persisted

    @property
    def fatal_stall(self) -> bool:
        """True once a stream stalled again after its one resubscribe: the node should stop."""
        return self._fatal

    # ── the node's loop calls these ──────────────────────────────────────────────────────────

    def on_bar_update(self, update: BarUpdate) -> None:
        bar = update.bar
        state = self._bars.get(bar.bar_type)
        if self._stopped or state is None or state.refs <= 0:
            log.debug("ignored a push for %s (not subscribed)", bar.bar_type)
            return
        self._touch(state, update.ts_received)
        ts = bar.ts_event
        if state.last_final_ts is not None and ts <= state.last_final_ts:
            self._late(state, bar, f"its bar for {_iso(state.last_final_ts)} is already final")
            return
        if state.floor_ts is not None and ts <= state.floor_ts:
            self._late(state, bar, f"the warm-up history already runs to {_iso(state.floor_ts)}")
            return
        forming = state.forming
        if forming is not None:
            if ts > forming.ts_event:                      # LE 473-475: a newer push closes the previous bar
                state.forming = None
                self._finalise(state, forming, "newer push")
            elif ts < forming.ts_event:
                self._late(state, bar, f"older than the forming bar for {_iso(forming.ts_event)}")
                return
        if update.closed:
            state.forming = None
            self._finalise(state, bar, "closed by the venue")
        else:
            state.forming = bar

    def on_quote(self, quote: QuoteTick) -> None:
        if self._stopped or self._quote_refs.get(quote.instrument_id, 0) <= 0:
            return
        self._quotes[quote.instrument_id] = quote
        self._send(TOPIC_QUOTE.format(instrument_id=quote.instrument_id), quote)

    def on_stream_status(self, status: StreamStatus) -> None:
        source = getattr(status, "source", "")
        kind = status.state if status.state in _STREAM_STATES else "error"
        detail = f"{source}: {status.detail}" if source else status.detail
        if kind in ("disconnected", "error"):
            log.warning("stream %s: %s", kind, detail)
        else:
            log.info("stream %s: %s", kind, detail)
        self._send(TOPIC_STREAM, StreamEvent(kind, detail, int(status.ts)))

    def on_timer(self, now_ns: int) -> None:
        """Make final every forming bar whose close plus the grace has passed (the next session's
        open plus the grace for a bar that ends a session), decide the cross-sections whose window
        has elapsed, and look for stalled streams."""
        if self._stopped:
            return
        now = int(now_ns)
        self._finalise_elapsed(now)
        for u in list(self._universes.values()):
            self._evaluate(u, now)
        if self._started:
            self._check_stalls(now)

    # ── what strategies and the node may read ────────────────────────────────────────────────

    def last_bar(self, bar_type: BarType) -> Optional[Bar]:
        state = self._bars.get(bar_type)
        return state.last_bar if state else None

    def forming_bar(self, bar_type: BarType) -> Optional[Bar]:
        state = self._bars.get(bar_type)
        return state.forming if state else None

    def last_quote(self, instrument_id: "str | InstrumentId") -> Optional[QuoteTick]:
        return self._quotes.get(as_instrument_id(instrument_id))

    def last_price(self, instrument_id: "str | InstrumentId") -> Optional[float]:
        """The latest price seen: the forming bar's close (the original's price reference, LE
        358), else the last final bar's close, else the quote's mid."""
        iid = as_instrument_id(instrument_id)
        best: Optional[Bar] = None
        for state in self._bars.values():
            if state.bar_type.instrument_id != iid:
                continue
            for bar in (state.forming, state.last_bar):
                if bar is not None and (best is None or bar.ts_init > best.ts_init or
                                        (bar.ts_init == best.ts_init and bar.ts_event > best.ts_event)):
                    best = bar
        if best is not None:
            return best.close
        quote = self._quotes.get(iid)
        return quote.mid if quote is not None else None

    def session_bars(self, bar_type: Optional[BarType] = None) -> Dict[BarType, List[Bar]]:
        """The bars published this session (what stop() writes to the store)."""
        if bar_type is not None:
            return {bar_type: list(self._session_bars.get(bar_type, []))}
        return {bt: list(bars) for bt, bars in self._session_bars.items()}

    # ── warm-up from the store ───────────────────────────────────────────────────────────────

    def warm_up(self, bar_type: BarType, count: int = DEFAULT_WARM_UP) -> List[Bar]:
        """The last `count` closed bars of `bar_type` from the local store, oldest first, with
        ts_event the true UTC close (the store's local-as-UTC labels converted). Never asks the
        broker (LE 384-392). Bars streamed later that are not newer than these are history and
        are dropped."""
        iid = bar_type.instrument_id
        tf = bar_type.spec.legacy_timeframe
        if tf is None:
            log.warning("[warmup] %s: the store has no %s files", iid, bar_type.spec.interval)
            return []
        try:
            store = self._get_store()
            key = self._store_key(iid)
            # DataStorage creates a symbol's folder when asked for its path; look first so a miss
            # leaves no empty folder behind.
            if not (Path(store.base_path) / key.upper().replace(".", "_") / f"{tf}.parquet").exists():
                log.warning("[warmup] %s: no history available in the store", iid)
                return []
            df = store.load_data(key, tf)
        except Exception as exc:                           # noqa: BLE001 - a bad file must not stop the node
            log.warning("[warmup] %s: store not readable (%s)", iid, exc)
            return []
        if df is None or df.empty:
            log.warning("[warmup] %s: no history available in the store", iid)
            return []
        spec = self._venues.get(iid.venue)
        closes = self._true_utc(pd.DatetimeIndex(df.index), spec, bar_type.spec)
        keep = ~closes.isna()
        if not keep.all():
            log.warning("[warmup] %s: dropped %d rows whose local time does not exist", iid, int((~keep).sum()))
        values = df.loc[keep, BAR_COLUMNS].astype(float).to_numpy()
        ts = closes[keep].asi8
        # One bar per close (the last row wins, as DataStorage.append_data keeps), oldest first,
        # closed by now.
        frame = pd.DataFrame(values, columns=BAR_COLUMNS, index=ts)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        now = self._clock.timestamp_ns()
        frame = frame[frame.index <= now]
        tail = frame.tail(int(count)) if count > 0 else frame.iloc[0:0]
        bars = [Bar(bar_type, float(o), float(h), float(lo), float(c), float(v), int(t), int(t))
                for t, (o, h, lo, c, v) in zip(tail.index, tail.to_numpy())]
        window = _WARM_UP_LOOKBACK.get(tf, timedelta(days=5))
        recent = int((frame.index >= now - int(window.total_seconds() * 1e9)).sum())
        if len(bars) < count:
            log.warning("[warmup] %s: only %d of %d bars in the store", iid, len(bars), count)
        elif recent < count:
            log.warning("[warmup] %s: the store is stale: only %d bars from the last %d days (want %d); "
                        "the original engine would have asked the broker, this one never does",
                        iid, recent, window.days, count)
        if bars:
            state = self._state(bar_type)
            state.floor_ts = max(state.floor_ts or bars[-1].ts_event, bars[-1].ts_event)
        return bars

    @staticmethod
    def _true_utc(index: pd.DatetimeIndex, venue: VenueSpec, spec: BarSpecification) -> pd.DatetimeIndex:
        """The store's labels as true UTC closes (midfreq.data.persistence.legacy does the same)."""
        if len(index) == 0:
            return pd.DatetimeIndex([], tz="UTC")
        if spec.is_intraday:
            return venue.localize(index)                   # local close tagged UTC -> UTC
        naive = index.tz_localize(None) if index.tz is not None else index
        return venue.daily_close(naive)                    # local midnight of the date -> session close

    # ── finality ─────────────────────────────────────────────────────────────────────────────

    def _finalise_elapsed(self, now_ns: int, stopping: bool = False) -> None:
        """The timer's rule (close + grace, or the next open + grace for a bar that ends a
        session), or at stop the original's: the window has elapsed (LE 637)."""
        # Oldest first (LE 629-633): in a stalled hour some forming bars are an interval older
        # than others, and finalising a newer one first would decide its interval's predecessor
        # without them.
        due = []
        for s in list(self._bars.values()):
            if s.forming is None:
                continue
            t = s.forming.ts_event
            if now_ns >= (t if stopping else self._due_ns(s.bar_type.instrument_id.venue, t)):
                due.append(s)
        due.sort(key=lambda s: s.forming.ts_event)
        for state in due:
            bar = state.forming
            state.forming = None
            self._finalise(state, bar, "window elapsed")

    def _due_ns(self, venue: Venue, t: int) -> int:
        """When the timer may make a bar closing at `t` final: t + grace, or, when `t` ends a
        session, the next session's open + grace (by when a newer push has normally done it)."""
        key = (venue, t)
        due = self._due.get(key)
        if due is None:
            due = t + self._grace_ns
            try:
                cal = self._calendar(venue)
                ts = pd.Timestamp(t, unit="ns", tz="UTC")
                if self._ends_session(cal, ts):
                    due = max(due, cal.next_open(ts).value + self._grace_ns)
            except Exception as exc:                       # noqa: BLE001 - no calendar answer: plain grace
                log.debug("%s: no session information for %s (%s)", venue, _iso(t), exc)
            self._due[key] = due
        return due

    @staticmethod
    def _ends_session(cal: TradingCalendar, ts: pd.Timestamp) -> bool:
        is_open = getattr(cal, "is_open", None)
        if is_open is not None:
            return (not is_open(ts)) and is_open(ts - pd.Timedelta(1, unit="ns"))
        # Only the TradingCalendar contract: the day's last close.
        for offset in (-1, 0, 1):
            sess = cal.session((ts + pd.Timedelta(days=offset)).date())
            if sess is not None and sess[1] == ts:
                return True
        return False

    def _finalise(self, state: _BarState, bar: Bar, reason: str) -> None:
        state.last_final_ts = bar.ts_event
        state.last_bar = bar
        self._session_bars.setdefault(state.bar_type, []).append(bar)
        self.bars_published += 1
        log.debug("bar final (%s): %s %s close=%s", reason, state.bar_type, _iso(bar.ts_event), bar.close)
        self._send(TOPIC_BAR.format(instrument_id=bar.instrument_id), bar)
        for u in list(self._universes.values()):
            if state.bar_type in u.members:
                self._add_to_universe(u, bar)

    def _send(self, topic: str, message: Any) -> None:
        """Publish; a subscriber's exception is logged, not raised, so one failing strategy
        cannot leave the service half-updated or starve the others of data (as the original's
        push handler guarded its callback)."""
        try:
            self._bus.publish(topic, message)
        except Exception:                                  # noqa: BLE001
            log.exception("a subscriber to %s failed on %s", topic, type(message).__name__)

    def _late(self, state: _BarState, bar: Bar, why: str) -> None:
        self.late_bars += 1
        last = state.last_bar
        if last is not None and last.ts_event == bar.ts_event and (
                last.open, last.high, last.low, last.close, last.volume) == (
                bar.open, bar.high, bar.low, bar.close, bar.volume):
            log.debug("repeat of a published bar dropped: %s %s", state.bar_type, _iso(bar.ts_event))
            return
        log.warning("late bar dropped: %s %s close=%s (%s)", state.bar_type, _iso(bar.ts_event), bar.close, why)

    # ── cross-sections ───────────────────────────────────────────────────────────────────────

    def _add_to_universe(self, u: _Universe, bar: Bar) -> None:
        t = bar.ts_event
        if u.done_through is not None and t <= u.done_through:
            log.warning("%s: %s bar for %s arrived after that cross-section was decided; published as a "
                        "bar only", u.name, bar.instrument_id, _iso(t))
            return
        u.pending.setdefault(t, {})[bar.instrument_id] = bar
        self._evaluate(u, None)

    def _evaluate(self, u: _Universe, now_ns: Optional[int], stopping: bool = False) -> None:
        """Publish every interval of `u` that can be decided: complete, or (window elapsed, or a
        later interval has a final bar) with at least ceil(min_share * N) instruments."""
        members = [bt.instrument_id for bt in u.members]
        n = len(members)
        if n == 0:
            return
        member_set = set(members)
        venues = {i.venue for i in members}
        need = math.ceil(self._min_share * n)            # LE 363, the same expression
        for t in sorted(u.pending):
            if u.done_through is not None and t <= u.done_through:
                del u.pending[t]
                continue
            bars = u.pending[t]
            have = [i for i in bars if i in member_set]    # in the order they became final
            if len(have) >= n:
                self._publish(u, t, bars, have, members, complete=True)
                continue
            newer = any(other > t for other in u.pending)
            elapsed = now_ns is not None and (
                now_ns >= t if stopping else now_ns >= max(self._due_ns(v, t) for v in venues))
            if not (newer or elapsed):
                break
            if len(have) >= need:
                self._publish(u, t, bars, have, members, complete=False)
            else:
                self._gap(u, t, len(have), n, need, now_ns)

    def _publish(self, u: _Universe, t: int, bars: Dict[InstrumentId, Bar], have: List[InstrumentId],
                 members: List[InstrumentId], complete: bool) -> None:
        missing = tuple(sorted((i for i in members if i not in bars), key=str))
        xs = CrossSection(u.name, t, {i: bars[i] for i in have}, complete, missing)
        u.done_through = t
        u.pending.pop(t, None)
        u.published += 1
        if complete:
            log.info("cross-section %s %s: all %d instruments", u.name, _iso(t), len(have))
        else:
            log.warning("cross-section %s %s: %d of %d instruments; missing %s", u.name, _iso(t),
                        len(have), len(members), ", ".join(map(str, missing)))
        self._send(TOPIC_CROSS_SECTION.format(universe=u.name), xs)

    def _gap(self, u: _Universe, t: int, have: int, n: int, need: int, now_ns: Optional[int]) -> None:
        u.done_through = t
        u.pending.pop(t, None)
        u.gaps += 1
        detail = (f"{u.name} {_iso(t)}: {have} of {n} instruments reported, below the minimum of "
                  f"{need}; no cross-section")
        log.warning("gap: %s", detail)
        self._send(TOPIC_STREAM, StreamEvent(
            "gap", detail, self._clock.timestamp_ns() if now_ns is None else now_ns))

    # ── stalls ───────────────────────────────────────────────────────────────────────────────

    def _touch(self, state: _BarState, ts_received: Optional[int]) -> None:
        if state.stream_key is None:
            return
        stream = self._streams.get(state.stream_key)
        if stream is None:
            return
        ts = int(ts_received) if ts_received else self._clock.timestamp_ns()
        if stream.last_update_ns is None or ts > stream.last_update_ns:
            stream.last_update_ns = ts

    def _check_stalls(self, now_ns: int) -> None:
        for stream in self._streams.values():
            if stream.fatal or not any(self._bars[bt].refs > 0 for bt in stream.bar_types):
                continue
            if stream.last_update_ns is None:
                stream.last_update_ns = now_ns
                continue
            silent = self._silent_ns(stream, now_ns)
            if silent <= self._stall_factor * stream.spec.nanos:      # LE 579: strictly more than
                continue
            what = (f"no bar update for {silent / _NS_PER_MIN:.1f} min of session time on "
                    f"{self._source(stream.client_index)} {stream.spec.interval} "
                    f"({', '.join(sorted(map(str, stream.venues)))})")
            if not stream.resubscribed:
                log.warning("STALL: %s; resubscribing once", what)
                self._send(TOPIC_STREAM, StreamEvent("stall", f"{what}; resubscribing once", now_ns))
                stream.resubscribed = True
                try:
                    self._resubscribe(stream)
                    stream.last_update_ns = now_ns             # LE 592
                    continue
                except Exception as exc:                       # noqa: BLE001 - LE 594-595: fall through to stopping
                    log.error("resubscribe failed: %s", exc)
                    self._send(TOPIC_STREAM, StreamEvent("error", f"resubscribe failed: {exc}", now_ns))
            stream.fatal = True
            self._fatal = True
            log.error("STALL: %s after resubscribing once; the node should stop cleanly", what)
            self._send(TOPIC_STREAM, StreamEvent("stall", "fatal", now_ns))

    def _resubscribe(self, stream: _Stream) -> None:
        client = self._clients[stream.client_index]
        for bt in sorted((b for b in stream.bar_types if self._bars[b].refs > 0), key=str):
            try:
                client.unsubscribe_bars(bt)
            except Exception as exc:                           # noqa: BLE001 - a dead stream may refuse this
                log.warning("unsubscribe %s before resubscribing failed: %s", bt, exc)
            client.subscribe_bars(bt, self._queue.put)

    def _silent_ns(self, stream: _Stream, now_ns: int) -> int:
        """Time since the stream's last update that the venue was open; wall time for daily bars."""
        start = stream.last_update_ns
        if now_ns <= start:
            return 0
        if not stream.spec.is_intraday:
            return now_ns - start
        intervals: List[Tuple[int, int]] = []
        for venue in stream.venues:
            intervals.extend(self._open_intervals(venue, start, now_ns))
        return _merge_ns(intervals)

    def _open_intervals(self, venue: Venue, start_ns: int, end_ns: int) -> List[Tuple[int, int]]:
        cal = self._calendar(venue)
        s = pd.Timestamp(start_ns, unit="ns", tz="UTC")
        e = pd.Timestamp(end_ns, unit="ns", tz="UTC")
        if hasattr(cal, "open_intervals"):
            return [(o.value, c.value) for o, c in cal.open_intervals(s, e)]
        # Only the TradingCalendar contract: first open to last close of each day.
        out = []
        day = (s - pd.Timedelta(days=1)).date()
        while day <= (e + pd.Timedelta(days=1)).date():
            sess = cal.session(day)
            if sess is not None:
                lo, hi = max(sess[0], s), min(sess[1], e)
                if lo < hi:
                    out.append((lo.value, hi.value))
            day = (pd.Timestamp(day) + pd.Timedelta(days=1)).date()
        return out

    def _calendar(self, venue: Venue) -> TradingCalendar:
        cal = self._calendars.get(venue)
        if cal is None:
            if venue in self._calendar_overrides:
                cal = self._calendar_overrides[venue]
            elif self._refdata is not None:
                cal = self._refdata.calendar(venue)
            else:
                cal = VenueCalendar.for_venue(venue, self._venues)
            self._calendars[venue] = cal
        return cal

    # ── the store ────────────────────────────────────────────────────────────────────────────

    def _get_store(self) -> DataStorage:
        if self._store is None:
            self._store = DataStorage()                    # data/ of MIDFREQ_ROOT (midfreq.core.paths)
        return self._store

    def _store_key(self, iid: InstrumentId) -> str:
        """The store's file key for an instrument: the original's `symbol.replace('.', '_')` of
        its Moomoo-style symbol ('US.AAPL' -> 'US_AAPL'; LE 401, 661), the symbol being what
        midfreq.data.persistence.legacy.LegacyDataReader.legacy_symbol gives. A venue Moomoo
        does not serve is keyed '<VENUE>_<SYMBOL>'."""
        try:
            market = self._venues.get(iid.venue).connector_options("moomoo")["market"]
            symbol = f"{market}.{iid.symbol}"
        except (KeyError, ValueError):
            symbol = f"{iid.venue}.{iid.symbol}"
        return symbol.replace(".", "_")

    def _persist(self) -> PersistReport:
        """Append the session's bars to the store, as LE 643-667 did: one frame per instrument
        with float columns open..volume indexed by the local close tagged UTC ('timestamp')."""
        if not self._session_bars:
            return PersistReport(0, 0, 0)
        try:
            store = self._get_store()
        except Exception as exc:                           # noqa: BLE001 - LE 651-653
            log.warning("[cache] not written (%s)", exc)
            return PersistReport(0, 0, len(self._session_bars), (str(exc),))
        written = instruments = failed = 0
        errors: List[str] = []
        for bt, bars in self._session_bars.items():
            if not bars:
                continue
            try:
                tf = bt.spec.legacy_timeframe
                if tf is None:
                    raise ValueError(f"the store has no {bt.spec.interval} files")
                venue = self._venues.get(bt.instrument_id.venue)
                index = venue.to_legacy_index([b.ts_event for b in bars], bt.spec)
                df = pd.DataFrame(
                    {"open": [b.open for b in bars], "high": [b.high for b in bars],
                     "low": [b.low for b in bars], "close": [b.close for b in bars],
                     "volume": [b.volume for b in bars]},
                    index=pd.DatetimeIndex(index, name="timestamp")).astype(float)
                store.append_data(df, self._store_key(bt.instrument_id), tf)
                written += len(bars)
                instruments += 1
            except Exception as exc:                       # noqa: BLE001 - LE 663-664: count and go on
                failed += 1
                errors.append(f"{bt}: {exc}")
                log.warning("[cache] %s not written: %s", bt, exc)
        log.info("[cache] wrote %d bars for %d instruments%s", written, instruments,
                 f", {failed} failed" if failed else "")
        return PersistReport(written, instruments, failed, tuple(errors))

    # ── bookkeeping ──────────────────────────────────────────────────────────────────────────

    def _state(self, bar_type: BarType) -> _BarState:
        state = self._bars.get(bar_type)
        if state is None:
            state = self._bars[bar_type] = _BarState(bar_type)
        return state

    def _stream(self, idx: int, bar_type: BarType) -> _Stream:
        key = (idx, bar_type.spec)
        stream = self._streams.get(key)
        if stream is None:
            stream = self._streams[key] = _Stream(idx, bar_type.spec)
        stream.venues.add(bar_type.instrument_id.venue)
        stream.bar_types.add(bar_type)
        return stream

    def _client_index(self, venue: "str | Venue") -> int:
        v = as_venue(venue)
        try:
            return self._client_of[v]
        except KeyError:
            raise ValueError(f"no data client for venue {v}; have {sorted(map(str, self._client_of))}") from None

    def _source(self, idx: int) -> str:
        client = self._clients[idx]
        try:
            return str(client.client_id)
        except Exception:                                  # noqa: BLE001 - a client without a connector name
            return f"client{idx}"

    def _limit(self, idx: int) -> Optional[int]:
        if idx not in self._limits:
            try:
                self._limits[idx] = self._clients[idx].subscription_limit()
            except Exception as exc:                       # noqa: BLE001 - unknown limit: the venue will say
                log.warning("%s: subscription limit unknown (%s)", self._source(idx), exc)
                self._limits[idx] = None
        return self._limits[idx]

    def _limit_key(self, iid: InstrumentId, what: str) -> Tuple[str, ...]:
        return (str(iid), what) if self._limit_unit == "stream" else (str(iid),)

    def _check_limit(self, idx: int, keys: Set[Tuple[str, ...]], what: str) -> None:
        limit = self._limit(idx)
        if limit is None:
            return
        in_use = self._in_use[idx]
        new = [k for k in keys if k not in in_use]
        if len(in_use) + len(new) > limit:
            raise SubscriptionLimitError(
                f"{self._source(idx)} allows {limit} {self._limit_unit}s at once on this account; "
                f"{len(in_use)} in use and {len(new)} more requested ({what}). Unsubscribe something, "
                f"or share one subscription between strategies.")

    def _acquire(self, idx: int, key: Tuple[str, ...]) -> None:
        self._in_use[idx][key] = self._in_use[idx].get(key, 0) + 1

    def _release(self, idx: int, key: Tuple[str, ...]) -> None:
        n = self._in_use[idx].get(key, 0) - 1
        if n <= 0:
            self._in_use[idx].pop(key, None)
        else:
            self._in_use[idx][key] = n

    def _unsubscribe_all(self) -> None:
        for bt, state in self._bars.items():
            if state.refs > 0:
                try:
                    self._clients[self._client_index(bt.instrument_id.venue)].unsubscribe_bars(bt)
                except Exception as exc:                   # noqa: BLE001 - shutting down regardless
                    log.warning("unsubscribe %s failed: %s", bt, exc)
        for iid, refs in self._quote_refs.items():
            if refs > 0:
                try:
                    self._clients[self._client_index(iid.venue)].unsubscribe_quotes(iid)
                except Exception as exc:                   # noqa: BLE001
                    log.warning("unsubscribe quotes %s failed: %s", iid, exc)
