"""
Backfill 3 years of 1h historical data for the US symbols in
config/symbols.json (market="US", status="backtest-only" — not part of
the live roster, research only). Mirrors backfill_data.py's HK plan.

Usage:
    uv run python scripts/data/backfill_us_data.py
"""
import time

from datetime import datetime, timedelta
from midfreq.core.candle import Timeframe
from midfreq.data.storage import DataStorage
from midfreq.core.config import ConfigLoader
from midfreq.data.providers.moomoo_provider import MoomooProvider


def main():
    provider = MoomooProvider()   # OpenD host/port from config/connections.yaml
    storage = DataStorage()
    config = ConfigLoader()

    symbols = config.get_all_symbols(market="US")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=3 * 365)

    print(f"Backfilling {len(symbols)} US symbols x 1h x 3yr\n")

    results = []
    for i, symbol in enumerate(symbols, 1):
        symbol_dir = symbol.replace(".", "_")
        print(f"[{i}/{len(symbols)}] {symbol}: {start_date.date()} -> {end_date.date()}")
        try:
            df = provider.get_historical_data(symbol, Timeframe.HOUR_1, start_date, end_date)
            if not df.empty:
                storage.append_data(df, symbol_dir, Timeframe.HOUR_1.value)
                results.append((symbol, len(df), "OK"))
                print(f"  [+] {len(df)} candles ({str(df.index.min())[:16]} -> {str(df.index.max())[:16]})")
            else:
                results.append((symbol, 0, "EMPTY"))
                print("  [!] No data returned")
        except Exception as e:
            results.append((symbol, 0, f"ERROR: {e}"))
            print(f"  [!] Error: {e}")
        time.sleep(1.5)

    provider.close()

    print("\nBACKFILL SUMMARY")
    for label, count, status in results:
        print(f"{label:<12} {count:<8} {status}")
    total = sum(r[1] for r in results)
    failed = sum(1 for r in results if r[2] != "OK")
    print(f"\nTotal: {total} candles | {failed} job(s) not OK")


if __name__ == "__main__":
    main()
