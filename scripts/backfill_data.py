"""
Backfill historical data for ALL configured HK stocks from Moomoo.

Pulls multiple timeframes per symbol (paginated — no 1000-candle cap):
    1d  : 3 years   (long-range backtests)
    1h  : 1 year    (mid-frequency backtests)
    5m  : 6 months  (mid-frequency backtests)
    1m  : 30 days   (fine-grained tests; Moomoo keeps limited 1m history)

Merges into existing data/<SYMBOL>/<tf>.parquet (dedup on timestamp).
Requires OpenD running and logged in. Historical requests work outside
market hours. Rate limit: 100 kline requests per 60s window — the sleep
between requests keeps us comfortably below it.

Usage:
    python3 backfill_data.py            # all symbols, all timeframes
    python3 backfill_data.py HK.00700   # single symbol
"""
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from datetime import datetime, timedelta
from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from providers.moomoo_provider import MoomooProvider

# (timeframe, how far back)
BACKFILL_PLAN = [
    (Timeframe.DAY_1, timedelta(days=3 * 365)),
    (Timeframe.HOUR_1, timedelta(days=365)),
    (Timeframe.MIN_5, timedelta(days=182)),
    (Timeframe.MIN_1, timedelta(days=30)),
]


def main():
    provider = MoomooProvider(host='127.0.0.1', port=11111)
    storage = DataStorage()
    config = ConfigLoader()

    symbols = config.get_live_symbols(market="HK")
    if len(sys.argv) > 1:
        symbols = [s for s in symbols if s in sys.argv[1:]]
        if not symbols:
            print(f"No configured symbol matches {sys.argv[1:]}")
            return

    end_date = datetime.now()
    total_jobs = len(symbols) * len(BACKFILL_PLAN)
    print(f"Backfilling {len(symbols)} symbols x {len(BACKFILL_PLAN)} timeframes = {total_jobs} jobs\n")

    results = []
    job = 0
    for symbol in symbols:
        symbol_dir = symbol.replace(".", "_")
        for timeframe, lookback in BACKFILL_PLAN:
            job += 1
            start_date = end_date - lookback
            label = f"{symbol} {timeframe.value}"
            print(f"[{job}/{total_jobs}] {label}: {start_date.date()} -> {end_date.date()}")

            try:
                df = provider.get_historical_data(symbol, timeframe, start_date, end_date)
                if not df.empty:
                    storage.append_data(df, symbol_dir, timeframe.value)
                    results.append((label, len(df), "OK"))
                    print(f"  [+] {len(df)} candles fetched "
                          f"({str(df.index.min())[:16]} -> {str(df.index.max())[:16]})")
                else:
                    results.append((label, 0, "EMPTY"))
                    print("  [!] No data returned")
            except Exception as e:
                results.append((label, 0, f"ERROR: {e}"))
                print(f"  [!] Error: {e}")

            time.sleep(1.5)  # stay well under the 100 req/min kline quota

    provider.close()

    print("\n" + "=" * 64)
    print("BACKFILL SUMMARY")
    print("=" * 64)
    print(f"{'Job':<24} {'Candles':<10} {'Status'}")
    print("-" * 50)
    for label, count, status in results:
        print(f"{label:<24} {count:<10} {status}")

    total = sum(r[1] for r in results)
    failed = sum(1 for r in results if r[2] != "OK")
    print(f"\nTotal: {total} candles | {failed} job(s) not OK")


if __name__ == "__main__":
    main()
