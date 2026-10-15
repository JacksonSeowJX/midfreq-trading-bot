"""
Copy the original data/ cache into a ParquetDataCatalog.

Copy-only: the legacy files are read through LegacyDataReader (read-only) and never moved,
rewritten or deleted, because the live engine on the VM keeps reading them. The catalog must be a
different directory, not inside data/.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from midfreq.core.model.data import BarSpecification
from midfreq.data.persistence.catalog import ParquetDataCatalog
from midfreq.data.persistence.legacy import LegacyDataReader
from midfreq.core.venues.spec import VenueRegistry


@dataclass(frozen=True)
class MigrationRow:
    folder: str
    timeframe: str
    bar_type: str
    rows: int


def _check_paths(data_dir: Path, catalog_dir: Path) -> None:
    d, c = data_dir.resolve(), catalog_dir.resolve()
    if c == d or d in c.parents:
        raise ValueError(f"the catalog ({c}) must not be the legacy data directory or inside it ({d})")


def migrate_legacy(data_dir: "str | Path", catalog_dir: "str | Path",
                   timeframes: Optional[Iterable[str]] = None,
                   folders: Optional[Iterable[str]] = None,
                   dry_run: bool = False,
                   venues: Optional[VenueRegistry] = None) -> List[MigrationRow]:
    """Copy every (or the selected) legacy folder and timeframe into the catalog, converting
    timestamps to true UTC. With dry_run, report what would be copied and write nothing."""
    data_dir, catalog_dir = Path(data_dir), Path(catalog_dir)
    _check_paths(data_dir, catalog_dir)
    reader = LegacyDataReader(data_dir, venues)
    catalog = ParquetDataCatalog(catalog_dir)
    wanted_tf = set(timeframes) if timeframes else None
    wanted_folders = {f.replace(".", "_", 1) for f in folders} if folders else None

    report: List[MigrationRow] = []
    for entry in reader.entries():
        if wanted_folders and entry.folder not in wanted_folders:
            continue
        for tf in entry.timeframes:
            if wanted_tf and tf not in wanted_tf:
                continue
            try:
                spec = BarSpecification.from_legacy(tf)
            except ValueError:
                continue
            bar_type = reader.bar_type(entry.instrument_id, spec)
            frame = reader.read_frame(entry.instrument_id, spec)
            if not dry_run and len(frame):
                catalog._write_frame(bar_type, frame)
            report.append(MigrationRow(entry.folder, tf, str(bar_type), len(frame)))
        if not dry_run:
            venue = reader.venues.get(entry.instrument_id.venue)
            inst = venue.default_instrument(str(entry.instrument_id.symbol))
            inst.info.update({"lot_size_source": "venue default (the legacy cache has no lot sizes)",
                              "legacy_symbol": entry.legacy_symbol})
            catalog.write_instruments([inst])
    return report
