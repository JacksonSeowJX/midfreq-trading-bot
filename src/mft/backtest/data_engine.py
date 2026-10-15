"""
DataEngine: merges bar streams from any number of instruments and venues into one stream in UTC
time order (a k-way heap merge), and groups bars that share a timestamp into TimeSlices.

Ordering is by ts_init, then by the order the streams were added, then by position in the stream,
so ties are broken exactly as core.backtester's stable sort broke them (symbol order). Because
every timestamp is true UTC, a Hong Kong bar closing at 16:00 HKT (08:00 UTC) is delivered before
a New York bar closing at 10:30 EDT (14:30 UTC) of the same day, which the original cache's
local-time stamps got the wrong way round.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from mft.model.data import Bar
from mft.model.identifiers import InstrumentId


@dataclass(frozen=True)
class TimeSlice:
    """Every bar with one ts_event, in delivery order."""
    ts_event: int
    bars: Tuple[Bar, ...]

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

    def add(self, bars: Sequence[Bar]) -> None:
        stream = list(bars)
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
            seen.setdefault(s[0].bar_type.instrument_id, None)
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
        batch: List[Bar] = []
        current = None
        for bar in self.iter_bars(start_ns, end_ns):
            if current is not None and bar.ts_init != current:
                yield TimeSlice(current, tuple(batch))
                batch = []
            current = bar.ts_init
            batch.append(bar)
        if batch:
            yield TimeSlice(current, tuple(batch))
