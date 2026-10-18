"""
The node's time and timers: the clock it reads (a LiveClock, or a TestClock in replays and
tests), the records' wall clock (`local_now`: this machine's local time, or `records_tz`'s), and
the timers that the loop puts on its queue as TimerEvents when they are due.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

NANOS = 1_000_000_000


# ── timers ────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimerEvent:
    name: str
    ts: int


@dataclass
class _Timer:
    name: str
    interval_ns: int
    next_due: int


class ClockMixin:
    """TradingNode's time and timers. Relies on the node's `clock`, `_records_tz` (None: this
    machine's local time), `config.node` (the timers' intervals `timer_s`, `reconcile_s` and
    `heartbeat_s`), `_timers` and `queue`."""

    # ── time ─────────────────────────────────────────────────────────────────────────────────
    def now_ns(self) -> int:
        return self.clock.timestamp_ns()

    def local_now(self) -> datetime:
        """The records' wall clock: this machine's local time (the original engine's
        datetime.now()), or `records_tz`'s, read from the node's clock."""
        ns = self.clock.timestamp_ns()
        if self._records_tz is None:
            return datetime.fromtimestamp(ns // NANOS).replace(microsecond=(ns // 1000) % 1_000_000)
        return pd.Timestamp(ns, unit="ns", tz="UTC").tz_convert(self._records_tz).tz_localize(None).to_pydatetime()

    def local_to_ns(self, dt: datetime) -> int:
        """A records timestamp (local_now's wall time, naive) back to UTC ns."""
        if self._records_tz is None:
            return int(dt.timestamp() * NANOS)
        return int(pd.Timestamp(dt).tz_localize(self._records_tz).value)

    # ── timers ───────────────────────────────────────────────────────────────────────────────
    def _arm_timers(self) -> None:
        n, now = self.config.node, self.now_ns()
        for name, seconds in (("timer", n.timer_s), ("reconcile", n.reconcile_s), ("heartbeat", n.heartbeat_s)):
            interval = int(float(seconds) * NANOS)
            self._timers[name] = _Timer(name, interval, now + interval)

    def _enqueue_due_timers(self, now: int) -> None:
        for t in self._timers.values():
            if now >= t.next_due:
                self.queue.put(TimerEvent(t.name, now))
                while t.next_due <= now:
                    t.next_due += t.interval_ns
