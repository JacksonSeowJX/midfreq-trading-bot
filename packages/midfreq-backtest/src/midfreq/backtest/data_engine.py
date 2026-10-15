"""
DataEngine: merges market-data streams (bars, quotes, trades, book snapshots) from any number of instruments and venues into one stream in UTC
time order (a k-way heap merge), and groups bars that share a timestamp into TimeSlices.

Ordering is by ts_init, then by the order the streams were added, then by position in the stream,
so ties are broken exactly as midfreq.backtest.backtester's stable sort broke them (symbol order). Because
every timestamp is true UTC, a Hong Kong bar closing at 16:00 HKT (08:00 UTC) is delivered before
a New York bar closing at 10:30 EDT (14:30 UTC) of the same day, which the original cache's
local-time stamps got the wrong way round.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from midfreq.core.model.data import Bar
from midfreq.core.model.identifiers import InstrumentId


@dataclass(frozen=True)
class TimeSlice:
    """Everything with one timestamp, in delivery order: `bars` holds the bars, `data` every item
    (bars, quotes, trades, book snapshots). len() counts bars."""
    ts_event: int
    bars: Tuple[Bar, ...]
    data: Tuple[object, ...] = ()

    def __iter__(self):
        return iter(self.bars)

    def __len__(self) -> int:
        return len(self.bars)

    def get(self, instrument_id: InstrumentId) -> Optional[Bar]:
        for bar in self.bars:
            if bar.bar_type.instrument_id == instrument_id:
                return bar
        return None

    @property
    def instrument_ids(self) -> List[InstrumentId]:
        return [b.bar_type.instrument_id for b in self.bars]


class DataEngine:
    def __init__(self):
        self._streams: List[List[Bar]] = []

    def add(self, items: Sequence[object]) -> None:
        """One stream of market data: bars, QuoteTicks, TradeTicks or OrderBookDepths, in any mix."""
        stream = list(items)
        if not stream:
            return
        if any(stream[i].ts_init > stream[i + 1].ts_init for i in range(len(stream) - 1)):
            stream.sort(key=lambda b: b.ts_init)   # stable: equal stamps keep their order
        self._streams.append(stream)

    @property
    def bar_count(self) -> int:
        return sum(len(s) for s in self._streams)

    def instruments(self) -> List[InstrumentId]:
        seen: Dict[InstrumentId, None] = {}
        for s in self._streams:
            for item in s:
                seen.setdefault(item.instrument_id, None)
        return list(seen)

    def iter_bars(self, start_ns: Optional[int] = None, end_ns: Optional[int] = None) -> Iterator[Bar]:
        streams = self._streams
        heap = []
        for i, s in enumerate(streams):
            j = 0
            if start_ns is not None:
                while j < len(s) and s[j].ts_init < start_ns:
                    j += 1
            if j < len(s):
                heap.append((s[j].ts_init, i, j))
        heapq.heapify(heap)
        while heap:
            ts, i, j = heapq.heappop(heap)
            if end_ns is not None and ts > end_ns:
                return
            yield streams[i][j]
            if j + 1 < len(streams[i]):
                heapq.heappush(heap, (streams[i][j + 1].ts_init, i, j + 1))

    def iter_slices(self, start_ns: Optional[int] = None, end_ns: Optional[int] = None) -> Iterator[TimeSlice]:
        batch: List[object] = []
        current = None
        for item in self.iter_bars(start_ns, end_ns):
            if current is not None and item.ts_init != current:
                yield self._slice(current, batch)
                batch = []
            current = item.ts_init
            batch.append(item)
        if batch:
            yield self._slice(current, batch)

    @staticmethod
    def _slice(ts: int, batch: List[object]) -> TimeSlice:
        return TimeSlice(ts, tuple(b for b in batch if isinstance(b, Bar)), tuple(batch))
