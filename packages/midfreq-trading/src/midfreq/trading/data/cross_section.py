"""
Cross-sections for LiveMarketDataService: each universe's final bars gathered per interval and
published on TOPIC_CROSS_SECTION, once per universe and interval.

An interval is published as soon as every instrument of the universe has its final bar for it;
otherwise, once a later interval has a final bar, with at least ceil(min_share * N) instruments
(the others listed as missing), or else not at all, with a StreamEvent('gap') saying so. The
rules, with the original engine's lines they reproduce ("LE", "CS"), are the "Cross-sections"
list in the docstring of midfreq.trading.data.service.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.identifiers import InstrumentId
from midfreq.trading.core.interfaces import TOPIC_CROSS_SECTION, TOPIC_STREAM, CrossSection, StreamEvent
from midfreq.trading.data.finality import _iso

log = logging.getLogger("midfreq.trading.marketdata")

__all__ = ["DEFAULT_MIN_SHARE", "CrossSectionMixin"]

DEFAULT_MIN_SHARE = 0.9         # LE 363: math.ceil(0.9 * len(symbols))


@dataclass
class _Universe:
    name: str
    spec: BarSpecification
    members: Dict[BarType, int] = field(default_factory=dict)          # bar type -> subscriptions
    pending: Dict[int, Dict[InstrumentId, Bar]] = field(default_factory=dict)
    done_through: Optional[int] = None      # newest interval published or given up as a gap
    published: int = 0
    gaps: int = 0


class CrossSectionMixin:
    """Assembling and publishing LiveMarketDataService's cross-sections, one universe at a time.

    It relies on the service's `_min_share` and `_clock`, and on its `_send`.
    """

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
        """Publish every interval of `u` that can be decided: complete, or, once a later interval
        has a final bar, with at least ceil(min_share * N) instruments (else a gap). The clock
        never decides an incomplete interval, at stop neither: the original engine's timing."""
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
            if not any(other > t for other in u.pending):
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
