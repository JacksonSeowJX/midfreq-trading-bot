"""
Command line for data ingestion. Run with src/ on the path:

    cd src
    python -m midfreq.data venues
    python -m midfreq.data fetch --venue XHKG --symbols 00700 00005 --bar 1-HOUR --start 2024-01-01 --client moomoo
    python -m midfreq.data fetch --venue XNAS --symbols AAPL MSFT --bar 1-DAY --start 2020-01-01 --client yfinance
    python -m midfreq.data fetch --venue BINANCE --symbols BTCUSDT ETHUSDT --bar 1-HOUR --start 2024-01-01
    python -m midfreq.data fetch --venue KRAKEN --symbols BTC-USD --bar 1-HOUR --start 2024-06-01 --end 2024-06-30
    python -m midfreq.data migrate --legacy-dir ../data --catalog ../catalog --timeframes 1h --dry-run
    python -m midfreq.data catalog

`--client` and `--connector` are the same flag; without it the venue's default connector from
config/venues.json is used. `--end` defaults to now, and a bare date means the whole of that day.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from midfreq.data.adapters.registry import default_registry
from midfreq.data.service import DEFAULT_CATALOG, fetch_bars
from midfreq.data.persistence.catalog import ParquetDataCatalog
from midfreq.data.persistence.migrate import migrate_legacy


def _cmd_venues(args) -> int:
    reg = default_registry()
    print(f"{'venue':9} {'tz':18} {'ccy':5} {'default':9} connectors   fees")
    for spec in reg.venues:
        fees = spec.fees
        rate = (f"{fees['rate']:.4%}" if fees.get("model") == "percent"
                else f"maker {fees.get('maker', 0):.3%} / taker {fees.get('taker', 0):.3%}")
        flag = "" if spec.fees_calibrated else " (uncalibrated)"
        print(f"{str(spec.venue):9} {spec.timezone:18} {spec.currency:5} {spec.default_connector:9} "
              f"{','.join(sorted(spec.connectors)):12} {rate}{flag}")
    print(f"\nconnectors registered: {', '.join(reg.names())}")
    return 0


def _cmd_fetch(args) -> int:
    results = fetch_bars(args.venue, args.symbols, args.bar, args.start, args.end,
                         connector=args.connector,
                         catalog=None if args.dry_run else ParquetDataCatalog(args.catalog),
                         load_instruments=not args.no_instruments, dry_run=args.dry_run)
    for r in results:
        if args.dry_run:
            print(f"would fetch {r.bar_type}  (native symbol {r.native_symbol})")
        else:
            span = f"{r.first} .. {r.last}" if r.bars else "no bars"
            print(f"{r.bar_type}: {r.bars} bars ({span}); catalog now holds {r.stored}")
    return 0


def _cmd_migrate(args) -> int:
    rows = migrate_legacy(args.legacy_dir, args.catalog, timeframes=args.timeframes,
                          folders=args.folders, dry_run=args.dry_run)
    for r in rows:
        print(f"{'would copy' if args.dry_run else 'copied'} {r.folder}/{r.timeframe}.parquet -> {r.bar_type} ({r.rows} rows)")
    print(f"{len(rows)} files {'to copy' if args.dry_run else 'copied'}; {args.legacy_dir} was only read")
    return 0


def _cmd_catalog(args) -> int:
    cat = ParquetDataCatalog(args.catalog)
    types = cat.bar_types()
    if not types:
        print(f"{args.catalog}: empty")
        return 0
    for bt in types:
        df = cat.bars_frame(bt)
        print(f"{bt}: {len(df)} bars, {df.index.min()} .. {df.index.max()}")
    print(f"{len(cat.instruments())} instrument definitions")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m midfreq.data", description="Market data ingestion")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("venues", help="list configured venues and connectors").set_defaults(func=_cmd_venues)

    f = sub.add_parser("fetch", help="fetch bars into the catalog")
    f.add_argument("--venue", required=True, help="XHKG, XNAS, XNYS, BINANCE, ...")
    f.add_argument("--symbols", nargs="+", required=True, help="symbols as the venue lists them")
    f.add_argument("--bar", default="1-HOUR", help="bar specification, e.g. 1-HOUR, 1-DAY, 5-MINUTE")
    f.add_argument("--start", required=True)
    f.add_argument("--end", default=None)
    f.add_argument("--connector", "--client", dest="connector", default=None,
                   help="override the venue's default connector")
    f.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    f.add_argument("--no-instruments", action="store_true", help="skip loading instrument definitions")
    f.add_argument("--dry-run", action="store_true", help="resolve symbols and connector, fetch nothing")
    f.set_defaults(func=_cmd_fetch)

    m = sub.add_parser("migrate", help="copy the legacy data/ cache into the catalog (copy-only)")
    m.add_argument("--legacy-dir", required=True)
    m.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    m.add_argument("--timeframes", nargs="*", default=None, help="e.g. 1h 1d (default: all)")
    m.add_argument("--folders", nargs="*", default=None, help="e.g. HK_00700 US_AAPL (default: all)")
    m.add_argument("--dry-run", action="store_true")
    m.set_defaults(func=_cmd_migrate)

    c = sub.add_parser("catalog", help="summarise what the catalog holds")
    c.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    c.set_defaults(func=_cmd_catalog)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
