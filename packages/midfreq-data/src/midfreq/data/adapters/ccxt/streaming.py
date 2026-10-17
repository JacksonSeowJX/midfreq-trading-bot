"""
Live market data on the ccxt connector's DataClient: bars and top-of-book quotes, streamed.

- Bars: ccxt.pro's watch_ohlcv. Each push of a candle (the forming one, or the one just ended)
  becomes BarUpdate(bar, closed, ts_received) with bar.ts_event the candle's close (its open plus
  the interval) in true UTC, and `closed` True once that close time has passed when the push
  arrives. Deciding finality from newer pushes is the MarketDataService's job, not this one's.
- Quotes: watch_ticker (best bid and ask with their sizes) or, with quote_source 'book' or on an
  exchange without watchTicker, the top level of watch_order_book; each push is a QuoteTick.
- Without ccxt.pro, or on an exchange that lacks the watch method, the subscription polls the REST
  exchange instead (fetch_ohlcv for the last two candles, fetch_ticker) and pushes what changed.
- Every subscription runs as a task on one background asyncio thread per data client. A network
  error retries with a doubling delay; the stream status handler hears 'connected' at the first
  push, 'disconnected' at the first failure, 'reconnected' once every failing subscription pushes
  again, and 'error' for a subscription that cannot work (an unknown symbol) and is dropped.
- ccxt sets no subscription limit the connector knows of, so subscription_limit() is None.

Market data is public: these streams use the production exchange whatever the environment of
the account's ExecutionClient (OKX demo trading shares the production order book).

Handlers are called on the streaming thread and must only queue what they receive.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from midfreq.core.common.timestamps import ms_to_ns
from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id
from midfreq.data.adapters.base import BarUpdate
from midfreq.data.adapters.ccxt.execution import error_is, is_transient

try:  # ccxt.pro ships inside ccxt >= 4
    import ccxt.pro as ccxt_pro  # type: ignore
except ImportError:  # pragma: no cover
    ccxt_pro = None

log = logging.getLogger(__name__)

STREAM_MODES = ("auto", "watch", "poll")
QUOTE_SOURCES = ("auto", "ticker", "book")


def bar_update(bar_type: BarType, row, interval_ns: int, now_ns: int) -> Optional[BarUpdate]:
    """One ccxt OHLCV row ([open ms, o, h, l, c, v]) as a BarUpdate received at `now_ns`."""
    try:
        open_ms = int(row[0])
        o, h, lo, c = (float(x) for x in row[1:5])
        v = float(row[5]) if len(row) > 5 and row[5] is not None else 0.0
    except (TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(x) for x in (o, h, lo, c, v)):
        return None
    close_ns = ms_to_ns(open_ms) + interval_ns
    # closed only when the venue says so (base.py); an OHLCV row never does: the MarketDataService decides
    return BarUpdate(Bar(bar_type, o, h, lo, c, v, close_ns, now_ns), closed=False, ts_received=now_ns)


def quote_from_ticker(instrument_id: InstrumentId, ticker: Dict[str, Any], now_ns: int) -> Optional[QuoteTick]:
    bid, ask = _num(ticker.get("bid")), _num(ticker.get("ask"))
    if bid is None or ask is None:
        return None
    ts = ticker.get("timestamp")
    return QuoteTick(instrument_id, bid, ask, _num(ticker.get("bidVolume")) or 0.0,
                     _num(ticker.get("askVolume")) or 0.0, ms_to_ns(int(ts)) if ts else now_ns, now_ns)


def quote_from_book(instrument_id: InstrumentId, book: Dict[str, Any], now_ns: int) -> Optional[QuoteTick]:
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    ts = book.get("timestamp")
    return QuoteTick(instrument_id, float(bids[0][0]), float(asks[0][0]), float(bids[0][1]), float(asks[0][1]),
                     ms_to_ns(int(ts)) if ts else now_ns, now_ns)


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _has(ex: Any, feature: str, method: str) -> bool:
    if not hasattr(ex, method):
        return False
    has = getattr(ex, "has", None)
    return bool(has.get(feature, True)) if isinstance(has, dict) else True


class CcxtStreamingMixin:
    """The live-data methods of CcxtDataClient (mixed in before DataClient)."""

    _runner: Optional["_StreamRunner"] = None

    def _streams(self) -> "_StreamRunner":
        if self._runner is None:
            self._runner = _StreamRunner(self)
        return self._runner

    def subscribe_bars(self, bar_type: BarType, handler: Callable[[BarUpdate], None]) -> None:
        spec = self.connector.venue_spec(bar_type.instrument_id.venue)
        self.connector.capabilities_for(spec).check(bar_type.spec, self.connector.name)
        symbol = self.connector.to_native(bar_type.instrument_id)
        self._streams().subscribe(("bars", bar_type), handler, spec.venue, symbol, bar_type)

    def unsubscribe_bars(self, bar_type: BarType) -> None:
        if self._runner is not None:
            self._runner.unsubscribe(("bars", bar_type))

    def subscribe_quotes(self, instrument_id: InstrumentId, handler: Callable[[QuoteTick], None]) -> None:
        iid = as_instrument_id(instrument_id)
        spec = self.connector.venue_spec(iid.venue)
        symbol = self.connector.to_native(iid)
        self._streams().subscribe(("quotes", iid), handler, spec.venue, symbol, iid)

    def unsubscribe_quotes(self, instrument_id: InstrumentId) -> None:
        if self._runner is not None:
            self._runner.unsubscribe(("quotes", as_instrument_id(instrument_id)))

    def subscription_limit(self) -> Optional[int]:
        return None

    def stop_streams(self) -> None:
        """End every subscription and the streaming thread."""
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop()


class _StreamRunner:
    """One asyncio loop on a daemon thread; one task per subscription."""

    def __init__(self, client):
        self.client = client
        self.connector = client.connector
        cfg = self.connector.config
        self.mode = str(getattr(cfg, "stream_mode", "auto")).lower()
        if self.mode not in STREAM_MODES:
            raise ValueError(f"stream_mode must be one of {STREAM_MODES}, got {self.mode!r}")
        self.quote_source = str(getattr(cfg, "quote_source", "auto")).lower()
        if self.quote_source not in QUOTE_SOURCES:
            raise ValueError(f"quote_source must be one of {QUOTE_SOURCES}, got {self.quote_source!r}")
        self.poll_interval = float(getattr(cfg, "stream_poll_interval", 0.0) or 0.0)
        self.reconnect_delay = float(getattr(cfg, "stream_reconnect_delay", 1.0))
        self.max_reconnect_delay = float(getattr(cfg, "stream_max_reconnect_delay", 30.0))
        self._lock = threading.Lock()
        self._handlers: Dict[Tuple[str, Any], Callable] = {}
        self._tasks: Dict[Tuple[str, Any], asyncio.Task] = {}   # loop thread only
        self._pros: Dict[str, Any] = {}                          # loop thread only
        self._owned: set = set()
        self._failing: set = set()
        self._ever_connected = False
        self._down = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    # ── control (any thread) ─────────────────────────────────────────

    def subscribe(self, key, handler, venue, symbol, target) -> None:
        with self._lock:
            known = key in self._handlers
            self._handlers[key] = handler
        if known:
            return          # same stream, new handler
        loop = self._ensure_loop()
        loop.call_soon_threadsafe(self._start, key, venue, symbol, target)

    def unsubscribe(self, key) -> None:
        with self._lock:
            if self._handlers.pop(key, None) is None:
                return
            self._failing.discard(key)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._cancel, key)

    def stop(self) -> None:
        with self._lock:
            self._handlers.clear()
        loop, thread = self._loop, self._thread
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=10)
        except Exception as exc:
            log.debug("ccxt streams shutdown: %s", exc)
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)
        self._loop = self._thread = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def run():
                    asyncio.set_event_loop(loop)
                    ready.set()
                    loop.run_forever()
                    loop.close()
                thread = threading.Thread(target=run, name=f"{self.connector.name}-streams", daemon=True)
                thread.start()
                ready.wait()
                self._loop, self._thread = loop, thread
            return self._loop

    # ── loop thread ──────────────────────────────────────────────────

    def _start(self, key, venue, symbol, target) -> None:
        if key in self._tasks:
            return
        coro = self._bars(key, venue, symbol, target) if key[0] == "bars" else self._quotes(key, venue, symbol, target)
        self._tasks[key] = asyncio.get_running_loop().create_task(coro)

    def _cancel(self, key) -> None:
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()

    async def _shutdown(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for venue in list(self._owned):
            ex = self._pros.pop(venue, None)
            try:
                result = ex.close() if ex is not None else None
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log.debug("ccxt stream close %s: %s", venue, exc)
        self._owned.clear()

    def _pro(self, venue) -> Optional[Any]:
        """The venue's ccxt.pro exchange (injected, or built here, inside the loop), or None to poll."""
        key = str(venue)
        if self.mode == "poll":
            return None
        if key in self._pros:
            return self._pros[key]
        injected = (getattr(self.connector, "_stream_exchanges", None) or {}).get(key)
        ex = injected
        if ex is None:
            exchange_id = self.connector.exchange_id(venue)
            cls = getattr(ccxt_pro, exchange_id, None) if ccxt_pro is not None else None
            if cls is not None:
                ex = cls({"enableRateLimit": True, **self.connector.config.options})
                self._owned.add(key)
        self._pros[key] = ex
        return ex

    def _handler(self, key) -> Optional[Callable]:
        with self._lock:
            return self._handlers.get(key)

    def _deliver(self, key, item) -> None:
        handler = self._handler(key)
        if handler is None or item is None:
            return
        try:
            handler(item)
        except Exception:
            log.exception("ccxt stream handler failed for %s", key[1])

    def _status(self, state: str, detail: str) -> None:
        try:
            self.client._stream_status(state, detail)
        except Exception:
            log.exception("stream status handler failed")

    def _ok(self, key) -> None:
        with self._lock:
            self._failing.discard(key)
            if not self._ever_connected:
                self._ever_connected, self._down = True, False
                state = "connected"
            elif self._down and not self._failing:
                self._down = False
                state = "reconnected"
            else:
                return
        self._status(state, "every subscription restored" if state == "reconnected" else "")

    def _fail(self, key, exc: BaseException) -> None:
        with self._lock:
            self._failing.add(key)
            if self._down:
                return
            self._down = True
        self._status("disconnected", f"{key[1]}: {type(exc).__name__}: {exc}")

    def _error(self, key, exc: BaseException) -> None:
        with self._lock:
            self._failing.discard(key)
            self._handlers.pop(key, None)
        self._tasks.pop(key, None)
        log.error("ccxt stream %s dropped: %s: %s", key[1], type(exc).__name__, exc)
        self._status("error", f"{key[1]}: {type(exc).__name__}: {exc}")

    async def _retry_wait(self, key, exc, delay: float) -> float:
        self._fail(key, exc)
        await asyncio.sleep(delay)
        return min(delay * 2, self.max_reconnect_delay)

    def _now(self) -> int:
        return self.client._now_ns()

    # ── bars ─────────────────────────────────────────────────────────

    async def _bars(self, key, venue, symbol: str, bar_type: BarType) -> None:
        from midfreq.data.adapters.ccxt.connector import timeframe_for
        timeframe, interval_ns = timeframe_for(bar_type.spec), bar_type.spec.nanos
        ex = self._pro(venue)
        if ex is None or not _has(ex, "watchOHLCV", "watch_ohlcv"):
            return await self._poll_bars(key, venue, symbol, bar_type, timeframe, interval_ns)
        delay = self.reconnect_delay
        while True:
            try:
                rows = await ex.watch_ohlcv(symbol, timeframe)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if error_is(exc, "NotSupported"):
                    return await self._poll_bars(key, venue, symbol, bar_type, timeframe, interval_ns)
                if not is_transient(exc):
                    return self._error(key, exc)
                delay = await self._retry_wait(key, exc, delay)
                continue
            delay = self.reconnect_delay
            self._ok(key)
            now = self._now()
            for row in rows or []:
                self._deliver(key, bar_update(bar_type, row, interval_ns, now))

    async def _poll_bars(self, key, venue, symbol, bar_type, timeframe, interval_ns) -> None:
        ex = self.connector.exchange(venue)
        interval = self.poll_interval or max(1.0, min(10.0, interval_ns / 1e9 / 60))
        last: Dict[int, tuple] = {}
        delay = self.reconnect_delay
        while True:
            try:
                rows = await asyncio.to_thread(lambda: ex.fetch_ohlcv(symbol, timeframe, limit=2))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not (is_transient(exc) or self.connector.is_rate_limit(exc)):
                    return self._error(key, exc)
                delay = await self._retry_wait(key, exc, delay)
                continue
            delay = self.reconnect_delay
            self._ok(key)
            now = self._now()
            for row in (rows or [])[-2:]:
                update = bar_update(bar_type, row, interval_ns, now)
                if update is None:
                    continue
                b = update.bar
                signature = (b.open, b.high, b.low, b.close, b.volume, update.closed)
                if last.get(b.ts_event) != signature:
                    last[b.ts_event] = signature
                    self._deliver(key, update)
            for stale in sorted(last)[:-4]:
                del last[stale]
            await asyncio.sleep(interval)

    # ── quotes ───────────────────────────────────────────────────────

    async def _quotes(self, key, venue, symbol: str, instrument_id: InstrumentId) -> None:
        ex = self._pro(venue)
        source = self.quote_source
        if ex is not None and source == "auto":
            source = "ticker" if _has(ex, "watchTicker", "watch_ticker") else "book"
        method = "watch_ticker" if source == "ticker" else "watch_order_book"
        feature = "watchTicker" if source == "ticker" else "watchOrderBook"
        if ex is None or not _has(ex, feature, method):
            return await self._poll_quotes(key, venue, symbol, instrument_id)
        delay = self.reconnect_delay
        while True:
            try:
                data = await (ex.watch_ticker(symbol) if source == "ticker" else ex.watch_order_book(symbol, 5))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if error_is(exc, "NotSupported"):
                    return await self._poll_quotes(key, venue, symbol, instrument_id)
                if not is_transient(exc):
                    return self._error(key, exc)
                delay = await self._retry_wait(key, exc, delay)
                continue
            delay = self.reconnect_delay
            self._ok(key)
            now = self._now()
            quote = quote_from_ticker(instrument_id, data or {}, now) if source == "ticker" \
                else quote_from_book(instrument_id, data or {}, now)
            self._deliver(key, quote)

    async def _poll_quotes(self, key, venue, symbol, instrument_id) -> None:
        ex = self.connector.exchange(venue)
        interval = self.poll_interval or 2.0
        last = None
        delay = self.reconnect_delay
        while True:
            try:
                ticker = await asyncio.to_thread(ex.fetch_ticker, symbol)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not (is_transient(exc) or self.connector.is_rate_limit(exc)):
                    return self._error(key, exc)
                delay = await self._retry_wait(key, exc, delay)
                continue
            delay = self.reconnect_delay
            self._ok(key)
            quote = quote_from_ticker(instrument_id, ticker or {}, self._now())
            if quote is not None and (quote.bid, quote.ask, quote.bid_size, quote.ask_size) != last:
                last = (quote.bid, quote.ask, quote.bid_size, quote.ask_size)
                self._deliver(key, quote)
            await asyncio.sleep(interval)
