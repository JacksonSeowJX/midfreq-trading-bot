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
  - `on_timer(now)`: a forming bar is final when now >= its close + grace, but only once its
    stream has delivered a push received after that close (it has moved past the bar); until
    then the timer waits `late_grace` (10 min), so a lagging stream's last updates to the bar
    are not cut off and then dropped as late .................................. LE 633-641, which
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
  - otherwise decided when a later interval has a final bar, as the original
    strategy closed an incomplete cross-section on the first candle of the
    next one (never on the clock; the last interval of a session, if
    incomplete, stays undecided at stop, as it did) .......................... CS 1048-1052
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

The parts of the service each have a module in this folder: subscriptions.py (the streams and each
venue account's subscription quota), finality.py, cross_section.py, stalls.py and store.py
(warm-up and the session's bars). This module holds the service itself: its construction, the
connectors' handlers, its lifecycle, the calls the node's loop makes, and what strategies and the
node read.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from midfreq.core.common.clock import Clock, LiveClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import Bar, BarSpecification, BarType, QuoteTick
from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id, as_venue
from midfreq.core.venues.spec import VenueRegistry, default_venues
from midfreq.data.adapters.base import BarUpdate, DataClient, StreamStatus
from midfreq.data.storage import DataStorage
from midfreq.trading.refdata.calendar import VenueCalendar
from midfreq.trading.core.interfaces import (
    TOPIC_QUOTE, TOPIC_STREAM, EventQueue, ReferenceData, StreamEvent, TradingCalendar,
)
from midfreq.trading.data.cross_section import DEFAULT_MIN_SHARE, CrossSectionMixin, _Universe
from midfreq.trading.data.finality import DEFAULT_GRACE_S, DEFAULT_LATE_GRACE_S, FinalityMixin, _BarState, _iso
from midfreq.trading.data.stalls import DEFAULT_STALL_FACTOR, StallsMixin
from midfreq.trading.data.store import DEFAULT_WARM_UP, CandleStore, PersistReport
from midfreq.trading.data.subscriptions import (
    SubscriptionLimitError, SubscriptionQuota, SubscriptionsMixin, _Stream,
)

log = logging.getLogger("midfreq.trading.marketdata")

__all__ = [
    "LiveMarketDataService", "PersistReport", "SourcedStreamStatus", "StreamHandlers", "SubscriptionLimitError",
    "DEFAULT_GRACE_S", "DEFAULT_MIN_SHARE", "DEFAULT_STALL_FACTOR", "DEFAULT_WARM_UP",
]

_STREAM_STATES = {"connected", "disconnected", "reconnected", "error"}


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


class LiveMarketDataService(SubscriptionsMixin, FinalityMixin, CrossSectionMixin, StallsMixin):
    """The node's MarketDataService (midfreq.trading.interfaces.MarketDataService).

    Its parts: subscriptions (SubscriptionsMixin, with a SubscriptionQuota per service), bar
    finality (FinalityMixin), cross-sections (CrossSectionMixin), stall detection (StallsMixin)
    and the candle store (a CandleStore).
    """

    def __init__(self, clients: Mapping["str | Venue", DataClient], bus: MessageBus, queue: EventQueue, *,
                 venues: Optional[VenueRegistry] = None,
                 refdata: Optional[ReferenceData] = None,
                 calendars: Optional[Mapping["str | Venue", TradingCalendar]] = None,
                 store: Optional[DataStorage] = None,
                 clock: Optional[Clock] = None,
                 grace: "float | timedelta" = DEFAULT_GRACE_S,
                 min_share: float = DEFAULT_MIN_SHARE,
                 stall_factor: float = DEFAULT_STALL_FACTOR,
                 limit_unit: str = "stream",
                 persist: bool = True,
                 late_grace: "float | timedelta" = DEFAULT_LATE_GRACE_S):
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
            data type: the DataClient contract's unit, which is how Moomoo's quota is charged) or
            "instrument" (for a client that counts instruments, whatever their data types).
        persist: write the session's bars to the store at stop() (off for shadow and sim runs,
            whose bars must not enter the real cache).
        late_grace: seconds after a bar's close before the timer makes it final when its stream
            has delivered no push received after that close (a lagging stream).
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
        self._clock = clock or LiveClock()
        self._candle_store = CandleStore(store, self._venues, self._clock)
        grace_s = grace.total_seconds() if isinstance(grace, timedelta) else float(grace)
        self._grace_ns = int(round(grace_s * 1e9))
        late_s = late_grace.total_seconds() if isinstance(late_grace, timedelta) else float(late_grace)
        self._late_grace_ns = int(round(max(late_s, grace_s) * 1e9))
        self._min_share = float(min_share)
        self._stall_factor = float(stall_factor)
        self._persist_bars = bool(persist)

        # clients, each once however many venues it serves
        self._client_of: Dict[Venue, int] = {}
        self._clients: List[DataClient] = []
        for v, client in clients.items():
            idx = next((i for i, c in enumerate(self._clients) if c is client), None)
            if idx is None:
                idx = len(self._clients)
                self._clients.append(client)
            self._client_of[as_venue(v)] = idx
        self._quota = SubscriptionQuota(self._clients, self._source, limit_unit)

        self._bars: Dict[BarType, _BarState] = {}
        self._universes: Dict[str, _Universe] = {}
        self._streams: Dict[Tuple[int, BarSpecification], _Stream] = {}
        self._quote_refs: Dict[InstrumentId, int] = {}
        self._quotes: Dict[InstrumentId, QuoteTick] = {}
        self._session_bars: Dict[BarType, List[Bar]] = {}
        self._due: Dict[Tuple[Venue, int], int] = {}

        self._down: Set[str] = set()                       # sources whose connection is down
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
        self._persisted = (self._candle_store.persist(self._session_bars) if self._persist_bars
                           else PersistReport(0, 0, 0))
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
        if kind == "disconnected":
            self._down.add(source)
        elif kind in ("connected", "reconnected"):
            self._down.discard(source)
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
        bars = self._candle_store.warm_up(bar_type, count)
        if bars:
            state = self._state(bar_type)
            state.floor_ts = max(state.floor_ts or bars[-1].ts_event, bars[-1].ts_event)
        return bars

    # ── publishing and bookkeeping ───────────────────────────────────────────────────────────

    def _send(self, topic: str, message: Any) -> None:
        """Publish; a subscriber's exception is logged, not raised, so one failing strategy
        cannot leave the service half-updated or starve the others of data (as the original's
        push handler guarded its callback)."""
        try:
            self._bus.publish(topic, message)
        except Exception:                                  # noqa: BLE001
            log.exception("a subscriber to %s failed on %s", topic, type(message).__name__)

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
