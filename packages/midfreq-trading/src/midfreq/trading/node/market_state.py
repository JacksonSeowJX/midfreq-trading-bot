"""
What the node keeps of the market, for itself and its strategies: closed bars in the cache, the
stream's clocks (when a bar last arrived, for the heartbeat's lag), the stall reports written to
the session logs, the latest pushed prices, and the quotes, instruments and tick sizes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from midfreq.core.model.data import Bar
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.model.instruments import Instrument
from midfreq.trading.core.interfaces import StreamEvent
from midfreq.trading.node.clock import NANOS
from midfreq.trading.ops.logs import get_logger

log = get_logger("node")


class MarketStateMixin:
    """What TradingNode keeps of the market. Relies on the node's `cache`, `instruments`,
    `refdata`, `host`, `state` and `config.node.stall_check`; updates `_last_bar_at`,
    `_last_bar_ts` and `_stalls`, reads `_last_push` and `_last_update_at` (the loop writes them on
    each bar push), and sets `_stop_flag` on a fatal stall. Calls `now_ns` and `local_now`."""

    # ── data the node keeps ──────────────────────────────────────────────────────────────────
    def _on_closed_bar(self, bar: Bar) -> None:
        if isinstance(bar, Bar):
            self.cache.add_bar(bar)
        self._last_bar_at = self.now_ns()
        self._last_bar_ts = bar.ts_event if self._last_bar_ts is None else max(self._last_bar_ts, bar.ts_event)

    def _on_cross_section_seen(self, xs: Any) -> None:
        self._last_bar_at = self.now_ns()

    def _on_stream_event(self, event: StreamEvent) -> None:
        """The MarketDataService's stall reports, written to each candle strategy's session log as
        the original engine wrote them (minutes since the stream last delivered a bar, and whether
        it had been resubscribed before). The minutes are wall-clock time since the last push; the
        service's own count, in the event's detail, leaves out the hours the market is shut."""
        if not isinstance(event, StreamEvent) or event.kind != "stall" or self.state != "running":
            return
        since = self._last_update_at if self._last_update_at is not None else event.ts_event
        silent = round(max(0, self.now_ns() - since) / NANOS / 60, 1)
        resubscribed_before = self._stalls > 0
        self._stalls += 1
        for a in self.host.legacy:
            if a.session_log is not None:
                a.session_log.stall(silent, resubscribed_before, self.local_now())
        if event.detail == "fatal" and self.config.node.stall_check:
            log.error("STALL: no bar for %.0f min after resubscribing — stopping cleanly", silent)
            self._stop_flag = self._stop_flag or "stall"
        else:
            log.warning("STALL: no bar for %.0f min — %s", silent, event.detail)

    def price_snapshot(self, instrument_id: InstrumentId) -> Dict[str, Optional[float]]:
        """{'bid', 'ask', 'last'} for re-pricing an order (the OMS's price source)."""
        q = self.cache.quote(instrument_id)
        return {"bid": q.bid if q else None, "ask": q.ask if q else None, "last": self.latest_price(instrument_id)}

    def latest_price(self, instrument_id: InstrumentId) -> Optional[float]:
        """The latest pushed price (the forming bar's close), as the original engine priced orders."""
        last = self._last_push.get(instrument_id)
        return last[1] if last is not None else None

    def _quote_triplet(self, instrument_id: InstrumentId):
        q = self.cache.quote(instrument_id)
        last = self.latest_price(instrument_id)
        return (q.bid if q else None, q.ask if q else None, last)

    def tick_size(self, instrument_id: InstrumentId, price: Optional[float] = None) -> Optional[float]:
        """The tick a price of this instrument must be on (the venue's spread table at `price`)."""
        fn = getattr(self.refdata, "tick_size", None)
        if callable(fn):
            try:
                return fn(instrument_id, price)
            except Exception:
                pass
        inst = self.instrument(instrument_id)
        return inst.price_increment if inst is not None else None

    def instrument(self, instrument_id: InstrumentId) -> Optional[Instrument]:
        inst = self.instruments.get(instrument_id)
        if inst is None and self.refdata is not None:
            try:
                inst = self.refdata.instrument(instrument_id)
            except Exception:
                inst = None
        return inst
