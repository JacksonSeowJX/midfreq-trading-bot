"""
Read-only access to the original cache, data/<MKT>_<CODE>/<tf>.parquet.

That cache is what the live engine on the VM reads and what every reported result was computed
from, so this module never writes, moves or creates anything under it (it does not even use
midfreq.data.storage.DataStorage, whose constructor creates directories).

The time convention is the important part. moomoo_provider stored Moomoo's `time_key`, an
exchange-LOCAL bar close time, with `pd.to_datetime(..., utc=True)`, so a Hong Kong bar that closed
at 10:30 HKT (02:30 UTC) is stored as 10:30 UTC and a New York bar that closed at 10:30 EDT
(14:30 UTC) is also stored as 10:30 UTC. Within one market that only shifts every bar by the same
offset, which is why the original single-market backtests were unaffected; across markets it puts
bars in the wrong order. The reader therefore re-reads each file's timestamps in its venue's
timezone and converts them to true UTC (daily bars, stored at 00:00 of the trading date, get the
session's close time). `venue.to_legacy_index` is the exact inverse, used to show core strategies
the timestamps they were written against.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from midfreq.core.common.timestamps import TimeLike
from midfreq.core.model.data import Bar, BarSpecification, BarType, frame_to_bars
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id
from midfreq.core.venues.spec import VenueRegistry, VenueSpec, default_venues

log = logging.getLogger(__name__)

_FOLDER_RE = re.compile(r"^(?P<market>[A-Z]{2})_(?P<code>[A-Z0-9.]+)$")
_CONNECTOR = "moomoo"   # the legacy folders use Moomoo's market codes


@dataclass(frozen=True)
class LegacyEntry:
    folder: str                 # 'HK_00700'
    legacy_symbol: str          # 'HK.00700', as core strategies know it
    instrument_id: InstrumentId
    timeframes: Tuple[str, ...]


class LegacyDataReader:
    def __init__(self, data_dir: "str | Path", venues: Optional[VenueRegistry] = None):
        self.data_dir = Path(data_dir)
        self.venues = venues or default_venues()

    # ── symbols ──────────────────────────────────────────────────────

    def instrument_id(self, legacy_symbol: str) -> InstrumentId:
        """'HK.00700' or 'HK_00700' -> 00700.XHKG; 'US.AAPL' -> AAPL.XNAS (listing table)."""
        market, sep, code = legacy_symbol.replace("_", ".", 1).partition(".")
        if not sep:
            raise ValueError(f"legacy symbols look like 'HK.00700': {legacy_symbol!r}")
        venue = self.venues.venue_for_market(_CONNECTOR, market, code)
        return InstrumentId.of(code, venue)

    def legacy_symbol(self, instrument_id: "str | InstrumentId") -> str:
        """00700.XHKG -> 'HK.00700'."""
        iid = as_instrument_id(instrument_id)
        market = self.venues.get(iid.venue).connector_options(_CONNECTOR)["market"]
        return f"{market}.{iid.symbol}"

    def folder(self, instrument_id: "str | InstrumentId") -> Path:
        return self.data_dir / self.legacy_symbol(instrument_id).replace(".", "_", 1)

    def path(self, instrument_id: "str | InstrumentId", spec: "BarSpecification | str") -> Path:
        sp = spec if isinstance(spec, BarSpecification) else BarSpecification.from_str(spec)
        tf = sp.legacy_timeframe
        if tf is None:
            raise ValueError(f"the legacy cache has no {sp} files")
        return self.folder(instrument_id) / f"{tf}.parquet"

    def entries(self) -> List[LegacyEntry]:
        """Every <MKT>_<CODE> folder with at least one timeframe file, as instruments. Folders in
        other layouts (yfinance-era 'AAPL', 'TENCENT_HK') are skipped."""
        out = []
        if not self.data_dir.is_dir():
            return out
        for d in sorted(self.data_dir.iterdir()):
            m = _FOLDER_RE.match(d.name) if d.is_dir() else None
            if not m:
                continue
            try:
                iid = self.instrument_id(d.name)
            except ValueError:
                continue
            tfs = tuple(sorted(f.stem for f in d.glob("*.parquet")))
            if tfs:
                out.append(LegacyEntry(d.name, f"{m['market']}.{m['code']}", iid, tfs))
        return out

    # ── bars ─────────────────────────────────────────────────────────

    def bar_type(self, instrument_id: "str | InstrumentId", spec: "BarSpecification | str") -> BarType:
        return BarType.of(as_instrument_id(instrument_id), spec)

    def read_raw(self, instrument_id: "str | InstrumentId", spec: "BarSpecification | str",
                 start: Optional[TimeLike] = None, end: Optional[TimeLike] = None,
                 end_inclusive: bool = True) -> pd.DataFrame:
        """The file as stored (local times tagged UTC), date-filtered exactly as
        midfreq.backtest.backtester.Backtester.run filters it, so a run over the same window sees the same
        candles."""
        path = self.path(instrument_id, spec)
        if not path.exists():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.read_parquet(path, engine="pyarrow")
        if not df.empty and start is not None:
            df = df[df.index >= pd.to_datetime(start, utc=True)]
        if not df.empty and end is not None:
            cutoff = pd.to_datetime(end, utc=True)
            if end_inclusive:
                cutoff = cutoff + pd.Timedelta(days=1)
            df = df[df.index < cutoff]
        return df

    def read_frame(self, instrument_id: "str | InstrumentId", spec: "BarSpecification | str",
                   start: Optional[TimeLike] = None, end: Optional[TimeLike] = None,
                   end_inclusive: bool = True) -> pd.DataFrame:
        """Bars indexed by their true UTC close, with int64 ts_event/ts_init columns."""
        iid = as_instrument_id(instrument_id)
        sp = spec if isinstance(spec, BarSpecification) else BarSpecification.from_str(spec)
        raw = self.read_raw(iid, sp, start, end, end_inclusive)
        venue = self.venues.get(iid.venue)
        closes = self._true_utc(raw.index, venue, sp)
        keep = ~closes.isna()
        if not keep.all():
            log.warning("%s %s: dropped %d rows whose local time does not exist or is ambiguous",
                        iid, sp, int((~keep).sum()))
        df = raw.loc[keep, ["open", "high", "low", "close", "volume"]].astype(float).copy()
        df.index = closes[keep]
        df.index.name = "timestamp"
        df["ts_event"] = df.index.asi8
        df["ts_init"] = df["ts_event"]
        return df[~df.index.duplicated(keep="last")]

    def read_bars(self, instrument_id: "str | InstrumentId", spec: "BarSpecification | str",
                  start: Optional[TimeLike] = None, end: Optional[TimeLike] = None,
                  end_inclusive: bool = True) -> List[Bar]:
        iid = as_instrument_id(instrument_id)
        return frame_to_bars(self.read_frame(iid, spec, start, end, end_inclusive), self.bar_type(iid, spec))

    @staticmethod
    def _true_utc(index: pd.DatetimeIndex, venue: VenueSpec, spec: BarSpecification) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(index)
        if len(idx) == 0:
            return pd.DatetimeIndex([], tz="UTC")
        if spec.is_intraday:
            return venue.localize(idx)
        return venue.daily_close(idx.tz_localize(None) if idx.tz is not None else idx)
