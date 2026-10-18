"""
Stall detection for LiveMarketDataService: a bar stream that has been silent too long is
resubscribed once, and a second stall is fatal, so the node stops cleanly.

A stream stalls when it has had no bar update for `stall_factor` (1.25) intervals of session time:
time outside the venue's sessions (nights, weekends, holidays, Hong Kong's lunch break) does not
count, and daily bars count wall time. The rules, with the original engine's lines they reproduce
("LE"), are the "Stalls" list in the docstring of midfreq.trading.data.service.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import pandas as pd

from midfreq.core.model.identifiers import Venue
from midfreq.trading.core.interfaces import TOPIC_STREAM, StreamEvent
from midfreq.trading.data.finality import _BarState
from midfreq.trading.data.subscriptions import _Stream

log = logging.getLogger("midfreq.trading.marketdata")

__all__ = ["DEFAULT_STALL_FACTOR", "StallsMixin"]

DEFAULT_STALL_FACTOR = 1.25     # LE 325: 1.25 candle periods of silence

_NS_PER_MIN = 60 * 1_000_000_000


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


class StallsMixin:
    """Watching LiveMarketDataService's bar streams for stalls, and resubscribing them.

    It relies on the service's `_streams`, `_bars`, `_clients`, `_queue`, `_clock`,
    `_stall_factor` and `_fatal`, and on its `_send`, `_source` and `_calendar`.
    """

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
        if stream.last_push_ns is None or ts > stream.last_push_ns:
            stream.last_push_ns = ts

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

    def resubscribe(self) -> int:
        """Unsubscribe and subscribe again every live bar stream now (an operator's or the
        node's request; the stall detector does the same for one stalled stream). Returns how
        many streams were resubscribed; a stream that fails is logged and skipped."""
        done = 0
        for stream in list(self._streams.values()):
            if stream.fatal or not any(self._bars[bt].refs > 0 for bt in stream.bar_types):
                continue
            try:
                self._resubscribe(stream)
            except Exception as exc:                       # noqa: BLE001 - the others still get theirs
                log.error("resubscribe of %s %s failed: %s", self._source(stream.client_index),
                          stream.spec.interval, exc)
                continue
            stream.last_update_ns = self._clock.timestamp_ns()
            done += 1
        return done

    def _resubscribe(self, stream: _Stream) -> None:
        client = self._clients[stream.client_index]
        bts = sorted((b for b in stream.bar_types if self._bars[b].refs > 0), key=str)
        try:
            client.unsubscribe_bars_many(bts)
        except Exception as exc:                               # noqa: BLE001 - a dead stream may refuse this
            log.warning("unsubscribe of %d bar types before resubscribing failed: %s", len(bts), exc)
        client.subscribe_bars_many(bts, self._queue.put)

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
