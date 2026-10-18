"""
Bar finality for LiveMarketDataService: when a forming bar becomes final, and when a bar ends a
session and so waits for the next session's open.

A bar is published on TOPIC_BAR once, when it is final: on a newer push for the same bar type,
on the venue's `closed` flag, on the timer once its close (or the next open) plus the grace has
passed, or at stop. A push that is not newer than what is already final is dropped as late. The
rules, with the original engine's lines they reproduce ("LE", "CS"), are the "Finality" list in
the docstring of midfreq.trading.data.service.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.identifiers import Venue
from midfreq.trading.core.interfaces import TOPIC_BAR, TradingCalendar

log = logging.getLogger("midfreq.trading.marketdata")

__all__ = ["DEFAULT_GRACE_S", "DEFAULT_LATE_GRACE_S", "FinalityMixin"]

DEFAULT_GRACE_S = 60.0          # a forming bar is final this long after its close without a successor
DEFAULT_LATE_GRACE_S = 600.0    # ... or this long, while its stream has delivered nothing since the close


@dataclass
class _BarState:
    bar_type: BarType
    refs: int = 0
    stream_key: Optional[Tuple[int, BarSpecification]] = None
    forming: Optional[Bar] = None
    last_final_ts: Optional[int] = None     # newest bar published
    last_bar: Optional[Bar] = None
    floor_ts: Optional[int] = None          # newest warm-up bar: anything not newer is history


def _iso(ns: int) -> str:
    return pd.Timestamp(int(ns), unit="ns", tz="UTC").strftime("%Y-%m-%d %H:%M:%SZ")


class FinalityMixin:
    """The finality rules of LiveMarketDataService: making forming bars final and publishing them.

    It relies on the service's `_bars`, `_streams`, `_universes`, `_session_bars`, `_due`,
    `_grace_ns`, `_late_grace_ns`, `late_bars` and `bars_published`, on its `_calendar` and `_send`,
    and on `_add_to_universe` (CrossSectionMixin).
    """

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
            if now_ns >= (t if stopping else self._clock_due_ns(s, t)):
                due.append(s)
        due.sort(key=lambda s: s.forming.ts_event)
        for state in due:
            bar = state.forming
            state.forming = None
            self._finalise(state, bar, "window elapsed")

    def _clock_due_ns(self, state: _BarState, t: int) -> int:
        """When the timer may make this forming bar final during the session: the grace after its
        close (or the next open) once its stream has delivered a push received after the close,
        else not before the late grace: a lagging stream may still be updating it."""
        due = self._due_ns(state.bar_type.instrument_id.venue, t)
        stream = self._streams.get(state.stream_key) if state.stream_key is not None else None
        if stream is not None and stream.last_push_ns is not None and stream.last_push_ns > t:
            return due
        return max(due, t + self._late_grace_ns)

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
        self._send(TOPIC_BAR.format(bar_type=state.bar_type), bar)
        for u in list(self._universes.values()):
            if state.bar_type in u.members:
                self._add_to_universe(u, bar)

    def _late(self, state: _BarState, bar: Bar, why: str) -> None:
        self.late_bars += 1
        last = state.last_bar
        if last is not None and last.ts_event == bar.ts_event and (
                last.open, last.high, last.low, last.close, last.volume) == (
                bar.open, bar.high, bar.low, bar.close, bar.volume):
            log.debug("repeat of a published bar dropped: %s %s", state.bar_type, _iso(bar.ts_event))
            return
        log.warning("late bar dropped: %s %s close=%s (%s)", state.bar_type, _iso(bar.ts_event), bar.close, why)
