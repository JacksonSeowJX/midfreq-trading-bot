"""
ParquetDataCatalog, after NautilusTrader's `persistence.catalog`.

Layout:
    <root>/data/bar/<bar_type>/<first_ts_event>-<last_ts_event>.parquet
    <root>/instruments/<instrument_id>.json

One directory per BarType, so `00700.XHKG-1-HOUR-LAST-EXTERNAL` and the same stock's daily bars,
or the same bars fetched from a different source, can never be mixed up the way two files called
`1h.parquet` could. Rows hold OHLCV as float64 and ts_event/ts_init as int64 UTC nanoseconds
(Nautilus stores fixed-point integers; floats are kept here to match the original data).

Writes merge with what is already stored (a re-fetched bar replaces the stored one), and each bar
type is rewritten to a single file through a temporary file and an atomic rename, so an interrupted
write never leaves a half-written file behind.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from midfreq.core.common.timestamps import TimeLike, to_ns
from midfreq.core.model.data import Bar, BarType, bars_to_frame, frame_to_bars
from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id
from midfreq.core.model.instruments import Instrument

_COLUMNS = ["open", "high", "low", "close", "volume", "ts_event", "ts_init"]


class ParquetDataCatalog:
    def __init__(self, path: "str | Path", ignore_in_git: bool = True):
        """`ignore_in_git` drops a `.gitignore` containing '*' into a newly created catalog (as
        pytest does for its cache), so market data is not committed by accident."""
        self.path = Path(path)
        self._ignore_in_git = ignore_in_git

    # ── layout ───────────────────────────────────────────────────────

    @property
    def bar_root(self) -> Path:
        return self.path / "data" / "bar"

    @property
    def instrument_root(self) -> Path:
        return self.path / "instruments"

    def _ensure_root(self) -> None:
        created = not self.path.exists()
        self.path.mkdir(parents=True, exist_ok=True)
        if created and self._ignore_in_git:
            (self.path / ".gitignore").write_text("# market data catalog; not versioned\n*\n")

    def bar_dir(self, bar_type: BarType) -> Path:
        return self.bar_root / str(bar_type)

    # ── bars ─────────────────────────────────────────────────────────

    def write_bars(self, bars: Iterable[Bar]) -> Dict[str, int]:
        """Store bars, merging with any already stored for the same bar type. Returns the total
        number of rows now held per bar type."""
        groups: Dict[BarType, List[Bar]] = defaultdict(list)
        for bar in bars:
            groups[bar.bar_type].append(bar)
        totals = {}
        for bar_type, group in groups.items():
            totals[str(bar_type)] = self._write_frame(bar_type, bars_to_frame(group))
        return totals

    def _write_frame(self, bar_type: BarType, new: pd.DataFrame) -> int:
        self._ensure_root()
        directory = self.bar_dir(bar_type)
        directory.mkdir(parents=True, exist_ok=True)
        old_files = sorted(directory.glob("*.parquet"))
        existing = self._read_files(old_files)
        merged = pd.concat([existing, new[_COLUMNS]], ignore_index=True) if len(existing) else new[_COLUMNS]
        merged = merged.drop_duplicates("ts_event", keep="last").sort_values("ts_event", kind="mergesort")
        merged = merged.reset_index(drop=True)
        if merged.empty:
            return 0
        target = directory / f"{int(merged['ts_event'].iloc[0])}-{int(merged['ts_event'].iloc[-1])}.parquet"
        tmp = directory / f".{target.name}.tmp"
        table = pa.Table.from_pandas(merged, preserve_index=False)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}),
                                               b"bar_type": str(bar_type).encode()})
        pq.write_table(table, tmp)
        os.replace(tmp, target)
        for f in old_files:
            if f != target:
                f.unlink()
        return len(merged)

    @staticmethod
    def _read_files(files: List[Path]) -> pd.DataFrame:
        if not files:
            return pd.DataFrame(columns=_COLUMNS)
        frames = [pq.read_table(f).to_pandas() for f in files]
        return pd.concat(frames, ignore_index=True)[_COLUMNS]

    def bars_frame(self, bar_type: "BarType | str", start: Optional[TimeLike] = None,
                   end: Optional[TimeLike] = None) -> pd.DataFrame:
        """Stored bars as a DataFrame indexed by ts_event (UTC), inclusive of start and end."""
        bt = bar_type if isinstance(bar_type, BarType) else BarType.from_str(bar_type)
        df = self._read_files(sorted(self.bar_dir(bt).glob("*.parquet")))
        df = df.drop_duplicates("ts_event", keep="last").sort_values("ts_event", kind="mergesort")
        if start is not None:
            df = df[df["ts_event"] >= to_ns(start)]
        if end is not None:
            df = df[df["ts_event"] <= to_ns(end)]
        df = df.astype({"ts_event": "int64", "ts_init": "int64"})
        df.index = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
        df.index.name = "timestamp"
        return df

    def bars(self, bar_type: "BarType | str", start: Optional[TimeLike] = None,
             end: Optional[TimeLike] = None) -> List[Bar]:
        bt = bar_type if isinstance(bar_type, BarType) else BarType.from_str(bar_type)
        return frame_to_bars(self.bars_frame(bt, start, end), bt)

    def bar_types(self, instrument_id: "str | InstrumentId | None" = None) -> List[BarType]:
        if not self.bar_root.exists():
            return []
        out = [BarType.from_str(d.name) for d in sorted(self.bar_root.iterdir())
               if d.is_dir() and any(d.glob("*.parquet"))]
        if instrument_id is not None:
            iid = as_instrument_id(instrument_id)
            out = [bt for bt in out if bt.instrument_id == iid]
        return out

    # ── instruments ──────────────────────────────────────────────────

    def write_instruments(self, instruments: Iterable[Instrument]) -> int:
        self._ensure_root()
        self.instrument_root.mkdir(parents=True, exist_ok=True)
        n = 0
        for inst in instruments:
            target = self.instrument_root / f"{inst.id}.json"
            tmp = target.with_name(f".{target.name}.tmp")
            tmp.write_text(json.dumps(inst.to_dict(), indent=2, sort_keys=True, default=str))
            os.replace(tmp, target)
            n += 1
        return n

    def instruments(self, venue: "str | Venue | None" = None) -> List[Instrument]:
        if not self.instrument_root.exists():
            return []
        out = [Instrument.from_dict(json.loads(f.read_text()))
               for f in sorted(self.instrument_root.glob("*.json"))]
        if venue is not None:
            out = [i for i in out if str(i.id.venue) == str(venue).upper()]
        return out

    def instrument(self, instrument_id: "str | InstrumentId") -> Optional[Instrument]:
        f = self.instrument_root / f"{as_instrument_id(instrument_id)}.json"
        return Instrument.from_dict(json.loads(f.read_text())) if f.exists() else None
