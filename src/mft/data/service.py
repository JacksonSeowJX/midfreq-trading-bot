"""
Fetching bars from a venue into the catalog: the logic behind `python -m mft.data fetch`.

The caller names a venue and symbols; the venue's default connector (or an override) is created
from the registry, translates the symbols, fetches, and the bars land in the catalog under their
BarType. Nothing here knows which vendor is behind the venue.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from mft.adapters.registry import ConnectorRegistry, default_registry
from mft.common.timestamps import TimeLike, is_date_only, to_utc_timestamp
from mft.model.data import BarSpecification, BarType
from mft.model.identifiers import InstrumentId
from mft.persistence.catalog import ParquetDataCatalog
from mft.venues.spec import VenueSpec

DEFAULT_CATALOG = Path(__file__).resolve().parents[3] / "catalog"


def instrument_id_for(venue: VenueSpec, symbol: str) -> InstrumentId:
    """A user-typed symbol on `venue` as an InstrumentId. Accepts the full id ('00700.XHKG') or the
    bare symbol, in any case; Hong Kong codes are zero-padded to five digits ('700' -> '00700')."""
    symbol = symbol.strip().upper()
    if symbol.endswith(f".{venue.venue}"):
        symbol = symbol[: -len(str(venue.venue)) - 1]
    if str(venue.venue) == "XHKG" and symbol.isdigit():
        symbol = symbol.zfill(5)
    return InstrumentId.of(symbol, venue.venue)


def end_of(value: TimeLike) -> pd.Timestamp:
    """An end bound as given, except that a bare date means the whole of that day."""
    ts = to_utc_timestamp(value)
    if is_date_only(value):
        ts = ts + pd.Timedelta(days=1) - pd.Timedelta(1, unit="ns")
    return ts


@dataclass
class FetchResult:
    instrument_id: InstrumentId
    bar_type: BarType
    native_symbol: str
    bars: int
    first: Optional[pd.Timestamp] = None
    last: Optional[pd.Timestamp] = None
    stored: int = 0


def fetch_bars(venue: str, symbols: Iterable[str], bar: str, start: TimeLike,
               end: Optional[TimeLike] = None, connector: Optional[str] = None,
               catalog: "ParquetDataCatalog | str | Path | None" = None,
               registry: Optional[ConnectorRegistry] = None,
               load_instruments: bool = True, dry_run: bool = False,
               connector_config: Optional[Dict[str, Any]] = None,
               **connector_kwargs) -> List[FetchResult]:
    """Fetch `bar` bars for `symbols` on `venue` into the catalog, one symbol at a time."""
    registry = registry or default_registry()
    spec = registry.venues.get(venue)
    spec_bar = BarSpecification.from_str(bar)
    if catalog is not None and not isinstance(catalog, ParquetDataCatalog):
        catalog = ParquetDataCatalog(catalog)
    conn = registry.for_venue(spec.venue, connector, connector_config, **connector_kwargs)
    ids = [instrument_id_for(spec, s) for s in symbols]
    end_ts = end_of(end) if end is not None else None
    results: List[FetchResult] = []
    if dry_run:
        conn.capabilities_for(spec).check(spec_bar, conn.name)
        return [FetchResult(i, BarType(i, spec_bar), conn.to_native(i), 0) for i in ids]
    with conn:
        if load_instruments and catalog is not None:
            catalog.write_instruments(conn.instrument_provider.load(ids))
        for iid in ids:
            bar_type = BarType(iid, spec_bar)
            bars = conn.data_client.request_bars(bar_type, start, end_ts)
            stored = catalog.write_bars(bars).get(str(bar_type), 0) if (catalog is not None and bars) else 0
            results.append(FetchResult(
                iid, bar_type, conn.to_native(iid), len(bars),
                bars[0].timestamp if bars else None, bars[-1].timestamp if bars else None, stored))
    return results
