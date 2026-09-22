"""
Backfill 3 years of 1h data for the Hang Seng Index constituents (~88
tickers), for the cross-sectional reversal universe-size test on Hong
Kong, mirroring what backfill_sp100.py did for the US side.

List reconciled from two sources: Hang Seng Indexes' official July 2026
factsheet (hsi.com.hk, top 50 constituents by weight, includes 3 recent
additions -- BeOne Medicines 6160, Innovent Bio 1801, CATL 3750 -- not
yet reflected elsewhere) and Wikipedia's Hang Seng Index constituents
table (as of Jan 2026, for the remaining ~59 smaller-weight members).
The official index carries 93 constituents as of Jul 2026; this list
has 88, so it is a close approximation, not a perfect match, exactly
like the SP100 list was for the US side.

The existing 19-stock HK universe (config/symbols.json) is a subset of
this list and is skipped since it's already backfilled.

Usage:
    python3 scripts/backfill_hsi.py
"""
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from datetime import datetime, timedelta
from core.models import Timeframe
from core.storage import DataStorage
from providers.moomoo_provider import MoomooProvider

# Declared in config/symbols.json under universes.hsi (see backfill_sp100).
from core.config import ConfigLoader
HSI_CODES = [s.replace("HK.", "") for s in ConfigLoader().get_universe("hsi")]


def main():
    provider = MoomooProvider(host='127.0.0.1', port=11111)
    storage = DataStorage()

    already_have = {
        p.name.replace("HK_", "") for p in
        (Path(__file__).resolve().parent.parent / 'data').glob('HK_*')
        if (p / '1h.parquet').exists()
    }
    symbols = [f"HK.{c}" for c in HSI_CODES if c not in already_have]
    print(f"{len(already_have)} HK symbols already cached; "
          f"backfilling {len(symbols)} new ones\n")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=3 * 365)

    results = []
    for i, symbol in enumerate(symbols, 1):
        symbol_dir = symbol.replace(".", "_")
        print(f"[{i}/{len(symbols)}] {symbol}: {start_date.date()} -> {end_date.date()}")
        try:
            df = provider.get_historical_data(symbol, Timeframe.HOUR_1, start_date, end_date)
            if not df.empty:
                storage.append_data(df, symbol_dir, Timeframe.HOUR_1.value)
                results.append((symbol, len(df), "OK"))
                print(f"  [+] {len(df)} candles")
            else:
                results.append((symbol, 0, "EMPTY"))
                print("  [!] No data returned")
        except Exception as e:
            results.append((symbol, 0, f"ERROR: {e}"))
            print(f"  [!] Error: {e}")
        time.sleep(1.2)

    provider.close()

    print("\nBACKFILL SUMMARY")
    ok = [r for r in results if r[2] == "OK"]
    failed = [r for r in results if r[2] != "OK"]
    print(f"OK: {len(ok)}/{len(results)}")
    if failed:
        print("Failed symbols:")
        for label, count, status in failed:
            print(f"  {label}: {status}")


if __name__ == "__main__":
    main()
