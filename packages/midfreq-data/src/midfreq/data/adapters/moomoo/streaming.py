"""
Live market data from Moomoo OpenD for the connector's DataClient: K-line pushes as BarUpdate and
best bid and ask (order-book pushes) as QuoteTick, with the stream's connection state reported
through DataClient._stream_status.

Bar times. Moomoo stamps a K-line with the END of its window in the exchange's own clock, in its
live pushes as in its history: the hourly US bars of the 16 October forward-test session were
stamped 10:30, 11:30, ..., 15:30 and 16:00 (live_sessions/session_20261016_*), and the hourly Hong
Kong bars in the local cache are stamped 10:30, 11:30, 12:00, 14:00, 15:00, 16:00 (data/HK_00700).
A start stamp would give 09:30 and 13:00 and could never give 12:00 or 16:00, when the market is
shut. The original engine relies on the same reading (live_engine._finalize_elapsed_candles:
"Moomoo labels candles by window END time"). So for an intraday bar `time_key` IS the bar's
scheduled close, a bar cut short by a session close included, and it only needs reading in the
venue's time zone (VenueSpec.localize) to become bar.ts_event in true UTC. A daily K-line's
time_key is the trading date at 00:00; its close is the session's close on that date
(VenueSpec.daily_close), 16:00 local.

Finality. A K-line push carries no flag saying the bar is final (the SDK's push columns are code,
name, time_key, open, close, high, low, volume, turnover, k_type, last_close, pe_ratio,
turnover_rate), so every BarUpdate has closed=False and the MarketDataService decides finality
(a newer bar, or the window plus a grace), as the original engine did.

Quotes. OpenD's QUOTE push has no bid or ask, so quotes subscribe ORDER_BOOK and take the top
level of each side; a push with an empty side (no bid or no ask) is skipped.

Subscriptions and quota. OpenD counts one subscription per (instrument, data type), shared by
every connection of the login, and refuses to cancel one within a minute of making it. The client
subscribes each (instrument, type) once, defers an unsubscribe until the minute has passed, and
reports `subscription_limit()` as what this connection may use: its own subscriptions plus the
quota still free (subscriptions held by other connections, e.g. another session, are not ours).

Connection. The streams use their own quote context (the history requests keep the provider's).
The SDK reconnects by itself every 6 s and re-subscribes from its own record; on top of that the
client re-subscribes everything it holds, then reports 'reconnected' (or 'error' if anything could
not be restored). 'connected' is reported when the context first opens, 'disconnected' when OpenD
drops it.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from midfreq.core.model.data import Bar, BarSpecification, BarType, QuoteTick
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id
from midfreq.core.venues.spec import VenueSpec
from midfreq.data.adapters.base import BarUpdate, ConnectorError
from midfreq.data.adapters.moomoo.execution import moomoo_sdk, open_bounded, tcp_probe

if TYPE_CHECKING:
    from midfreq.data.adapters.moomoo.connector import MoomooDataClient

log = logging.getLogger(__name__)

RET_OK = 0
ORDER_BOOK = "ORDER_BOOK"          # SubType.ORDER_BOOK
MIN_SUBSCRIPTION_S = 60.0          # OpenD: a subscription may be cancelled one minute after it was made


def kline_close_ns(time_key: str, spec: BarSpecification, venue: VenueSpec) -> Optional[int]:
    """bar.ts_event (UTC ns) of a Moomoo K-line stamped `time_key` (exchange-local wall clock):
    the time itself for an intraday bar (Moomoo stamps the window's end), the session close of
    the date for a daily bar. None when the wall-clock time does not exist (a DST gap)."""
    stamp = pd.DatetimeIndex([pd.Timestamp(time_key)])
    closes = venue.localize(stamp) if spec.is_intraday else venue.daily_close(stamp)
    if pd.isna(closes[0]):
        return None
    return int(closes[0].value)


def _thread_timer(delay: float, fn: Callable[[], None]) -> Any:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer


@dataclass
class _Sub:
    kind: str                                   # 'bar' or 'quote'
    handler: Callable[[Any], None]
    venue: VenueSpec
    instrument_id: InstrumentId
    bar_type: Optional[BarType] = None


class MoomooStreams:
    """The live side of MoomooDataClient. Thread-safe; handlers are called on the SDK's push thread."""

    def __init__(self, client: "MoomooDataClient", sdk: Any = None,
                 probe: Optional[Callable[[str, int, float], str]] = None,
                 timer: Optional[Callable[[float, Callable[[], None]], Any]] = None,
                 min_subscription_s: float = MIN_SUBSCRIPTION_S, probe_timeout: float = 1.5,
                 connect_timeout: float = 10.0, query_timeout: float = 10.0):
        self.client = client
        self.connector = client.connector
        self._sdk = sdk
        self._probe = probe or tcp_probe
        self._timer = timer or _thread_timer
        self.min_subscription_s = float(min_subscription_s)
        self.probe_timeout = probe_timeout
        self.connect_timeout = connect_timeout
        self.query_timeout = query_timeout
        self._lock = threading.RLock()
        self._ctx: Any = None
        self._subs: Dict[Tuple[str, str], _Sub] = {}           # (code, subtype) -> who receives it
        self._since: Dict[Tuple[str, str], float] = {}         # (code, subtype) -> when OpenD subscribed it (s)
        self._pending: Dict[Tuple[str, str], Any] = {}         # deferred unsubscribes (timers)
        self._close_cache: Dict[Tuple[str, str, str], Optional[int]] = {}

    def _now_s(self) -> float:
        return self.client._now_ns() / 1e9

    # ── context ──────────────────────────────────────────────────────

    def _context(self):
        with self._lock:
            if self._ctx is not None:
                return self._ctx
            sdk = self._sdk if self._sdk is not None else moomoo_sdk()
            self._sdk = sdk
            host, port = self.connector.config.host, int(self.connector.config.port)
            reason = self._probe(host, port, self.probe_timeout)
            if reason:
                self.client._stream_status("error", reason)
                raise ConnectorError(reason)
            ctx_class = _quote_context_class(sdk)
            try:
                ctx = open_bounded(lambda: ctx_class(self, host=host, port=port), self.connect_timeout,
                                   f"a quote context on OpenD at {host}:{port}")
            except ConnectorError as exc:
                self.client._stream_status("error", str(exc))
                raise
            try:
                ctx.set_sync_query_connect_timeout(self.query_timeout)
                for handler in _quote_handlers(sdk, self):
                    ctx.set_handler(handler)
            except BaseException:
                ctx.close()
                raise
            self._ctx = ctx
        self.client._stream_status("connected", f"OpenD {host}:{port}")
        return ctx

    def close(self) -> None:
        with self._lock:
            ctx, self._ctx = self._ctx, None
            for timer in self._pending.values():
                _cancel(timer)
            self._pending.clear()
            self._subs.clear()
            self._since.clear()
        if ctx is not None:
            ctx._mf_streams = None
            ctx.close()

    # ── subscriptions ────────────────────────────────────────────────

    def subscribe_bars(self, bar_type: BarType, handler: Callable[[BarUpdate], None]) -> None:
        conn = self.connector
        venue = conn.venue_spec(bar_type.instrument_id.venue)
        conn.capabilities_for(venue).check(bar_type.spec, conn.name)
        from midfreq.data.adapters.moomoo.connector import KLTYPES
        key = (conn.to_native(bar_type.instrument_id), KLTYPES[bar_type.spec.interval])
        self._subscribe(key, _Sub("bar", handler, venue, bar_type.instrument_id, bar_type))

    def unsubscribe_bars(self, bar_type: BarType) -> None:
        from midfreq.data.adapters.moomoo.connector import KLTYPES
        key = (self.connector.to_native(bar_type.instrument_id), KLTYPES.get(bar_type.spec.interval, ""))
        self._unsubscribe(key)

    def subscribe_quotes(self, instrument_id: InstrumentId, handler: Callable[[QuoteTick], None]) -> None:
        iid = as_instrument_id(instrument_id)
        venue = self.connector.venue_spec(iid.venue)
        self._subscribe((self.connector.to_native(iid), ORDER_BOOK), _Sub("quote", handler, venue, iid))

    def unsubscribe_quotes(self, instrument_id: InstrumentId) -> None:
        self._unsubscribe((self.connector.to_native(as_instrument_id(instrument_id)), ORDER_BOOK))

    def _subscribe(self, key: Tuple[str, str], sub: _Sub) -> None:
        ctx = self._context()
        with self._lock:
            _cancel(self._pending.pop(key, None))
            had = key in self._since
            self._subs[key] = sub          # registered first, so the first push is delivered
        if had:
            return                         # OpenD already streams it (a handler was replaced)
        code, subtype = key
        try:
            ret, err = ctx.subscribe([code], [subtype], is_first_push=True)
        except Exception as exc:
            ret, err = None, f"{type(exc).__name__}: {exc}"
        if ret != RET_OK:
            with self._lock:
                if self._subs.get(key) is sub:
                    del self._subs[key]
            self.connector.check_ret(-1, err, f"subscribe {code} {subtype}")
        with self._lock:
            self._since[key] = self._now_s()

    def _unsubscribe(self, key: Tuple[str, str]) -> None:
        with self._lock:
            if self._subs.pop(key, None) is None:
                return
            since = self._since.get(key)
            if since is None or self._ctx is None:
                return
            wait = self.min_subscription_s - (self._now_s() - since)
            if wait > 0:   # OpenD refuses an unsubscribe in the first minute: do it when allowed
                _cancel(self._pending.pop(key, None))
                self._pending[key] = self._timer(wait + 1.0, lambda: self._release(key))
                return
        self._release(key)

    def _release(self, key: Tuple[str, str]) -> None:
        with self._lock:
            self._pending.pop(key, None)
            ctx = self._ctx
            if key in self._subs or key not in self._since or ctx is None:
                return                     # wanted again meanwhile, or nothing to release
        code, subtype = key
        try:
            ret, err = ctx.unsubscribe([code], [subtype])
        except Exception as exc:
            ret, err = None, f"{type(exc).__name__}: {exc}"
        if ret == RET_OK:
            with self._lock:
                if key not in self._subs:
                    self._since.pop(key, None)
        else:
            log.warning("moomoo unsubscribe %s %s failed: %s", code, subtype, err)

    def subscription_limit(self) -> int:
        """Subscriptions this connection may hold: its own plus those still free on the account.
        One subscription is one (instrument, data type): bars and quotes of one stock use two."""
        ctx = self._context()
        try:
            ret, data = ctx.query_subscription(is_all_conn=True)
        except Exception as exc:
            ret, data = None, f"{type(exc).__name__}: {exc}"
        if ret != RET_OK:
            raise ConnectorError(f"moomoo subscription quota query failed: {data}")
        return int(data.get("own_used", 0) or 0) + int(data.get("remain", 0) or 0)

    def subscriptions(self) -> List[Tuple[str, str]]:
        with self._lock:
            return sorted(self._subs)

    # ── connection hooks (SDK threads) ───────────────────────────────

    def _on_disconnected(self, detail: Any) -> None:
        log.warning("moomoo quote connection lost (%s); the SDK reconnects every 6 s", detail)
        self.client._stream_status("disconnected", str(detail))

    def _on_reconnected(self, ctx) -> None:
        """OpenD is back. Re-subscribe everything held (the SDK has tried from its own record;
        subscribing again is harmless and tells us whether each one is really back)."""
        with self._lock:
            keys = sorted(self._subs)
        by_type: Dict[str, List[str]] = {}
        for code, subtype in keys:
            by_type.setdefault(subtype, []).append(code)
        failed = []
        for subtype, codes in by_type.items():
            try:
                ret, err = ctx.subscribe(codes, [subtype], is_first_push=True)
            except Exception as exc:
                ret, err = None, f"{type(exc).__name__}: {exc}"
            if ret != RET_OK:
                failed.append(f"{subtype} {codes}: {err}")
        now = self._now_s()
        with self._lock:
            for key in keys:
                self._since[key] = now     # a new connection: the minute starts again
        if failed:
            self.client._stream_status("error", "re-subscription after reconnect failed: " + "; ".join(failed))
        else:
            self.client._stream_status("reconnected", f"{len(keys)} subscriptions restored")

    # ── pushes (SDK thread) ──────────────────────────────────────────

    def _on_klines(self, data: Any) -> None:
        now = self.client._now_ns()
        rows = data.to_dict("records") if isinstance(data, pd.DataFrame) else list(data or [])
        for row in rows:
            key = (str(row.get("code")), str(row.get("k_type")))
            with self._lock:
                sub = self._subs.get(key)
            if sub is None or sub.kind != "bar":
                continue
            try:
                update = self._bar_update(sub, row, now)
            except (TypeError, ValueError, KeyError) as exc:
                log.warning("moomoo K-line push for %s %s cannot be read: %s (%r)", key[0], key[1], exc, row)
                continue
            if update is not None:
                self._deliver(sub, update)

    def _bar_update(self, sub: _Sub, row: Dict[str, Any], now: int) -> Optional[BarUpdate]:
        bar_type = sub.bar_type
        time_key = str(row["time_key"])
        cache_key = (str(sub.venue.venue), bar_type.spec.interval, time_key)
        ts = self._close_cache.get(cache_key, -1)
        if ts == -1:
            ts = kline_close_ns(time_key, bar_type.spec, sub.venue)
            if len(self._close_cache) > 4096:
                self._close_cache.clear()
            self._close_cache[cache_key] = ts
        if ts is None:
            log.warning("moomoo K-line %s %s stamped %s has no UTC time (DST gap); skipped",
                        bar_type.instrument_id, bar_type.spec, time_key)
            return None
        bar = Bar(bar_type, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]),
                  float(row["volume"]), ts, now)
        return BarUpdate(bar, closed=False, ts_received=now)

    def _on_order_book(self, book: Any) -> None:
        if not isinstance(book, dict):
            return
        key = (str(book.get("code")), ORDER_BOOK)
        with self._lock:
            sub = self._subs.get(key)
        if sub is None or sub.kind != "quote":
            return
        bids, asks = book.get("Bid") or [], book.get("Ask") or []
        if not bids or not asks:
            return
        now = self.client._now_ns()
        try:
            tick = QuoteTick(sub.instrument_id, float(bids[0][0]), float(asks[0][0]), float(bids[0][1]),
                             float(asks[0][1]), now, now)
        except (TypeError, ValueError, IndexError) as exc:
            log.warning("moomoo order book push for %s cannot be read: %s", key[0], exc)
            return
        self._deliver(sub, tick)

    def _deliver(self, sub: _Sub, item: Any) -> None:
        try:
            sub.handler(item)
        except Exception:   # one bad handler must not stop the push thread
            log.exception("moomoo stream handler for %s failed", sub.instrument_id)


def _cancel(timer: Any) -> None:
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass


# ── SDK subclasses (built per SDK module, so a fake SDK works the same way) ────────────────────

def _quote_context_class(sdk: Any):
    """OpenQuoteContext with two hooks: connection lost, and restored (after the SDK's own
    re-subscription)."""

    class _QuoteContext(sdk.OpenQuoteContext):
        def __init__(self, streams: MoomooStreams, **kwargs):
            self._mf_streams = streams
            self._mf_ready = False            # the constructor's first connect is not a reconnect
            super().__init__(**kwargs)
            self._mf_ready = True

        def on_api_socket_reconnected(self):
            result = super().on_api_socket_reconnected()
            streams = getattr(self, "_mf_streams", None)
            if streams is not None and getattr(self, "_mf_ready", False):
                streams._on_reconnected(self)
            return result

        def on_disconnect(self, *args, **kwargs):
            result = super().on_disconnect(*args, **kwargs)
            streams = getattr(self, "_mf_streams", None)
            if streams is not None:
                streams._on_disconnected(args[1:] if len(args) > 1 else args)
            return result

    return _QuoteContext


def _quote_handlers(sdk: Any, streams: MoomooStreams):
    ok = sdk.RET_OK

    class _KlineHandler(sdk.CurKlineHandlerBase):
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != ok:
                log.warning("moomoo K-line push error: %s", data)
                return ret, data
            try:
                streams._on_klines(data)
            except Exception:
                log.exception("moomoo K-line push could not be handled")
            return ret, data

    class _BookHandler(sdk.OrderBookHandlerBase):
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != ok:
                log.warning("moomoo order book push error: %s", data)
                return ret, data
            try:
                streams._on_order_book(data)
            except Exception:
                log.exception("moomoo order book push could not be handled")
            return ret, data

    return _KlineHandler(), _BookHandler()
