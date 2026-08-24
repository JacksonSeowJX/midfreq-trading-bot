"""
Backfill 3 years of 1h data for the S&P 100 constituents (~101 tickers,
including the GOOG/GOOGL dual share class), for the cross-sectional
reversal universe-size test. List pulled from Wikipedia's S&P 100 page
(current as of this project's writing); "HONA" corrected to the real
Honeywell ticker HON.

Kept as a standalone list here, deliberately NOT merged into the
existing 15-stock "US" entry in config/symbols.json, since that list is
already the basis for reported results (US robust validation, Fed rate
test) and shouldn't silently change under those.

Usage:
    python3 scripts/backfill_sp100.py
"""
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from datetime import datetime, timedelta
from core.models import Timeframe
from core.storage import DataStorage
from providers.moomoo_provider import MoomooProvider

SP100_TICKERS = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AMAT", "AMD", "AMGN", "AMT", "AMZN",
    "AVGO", "AXP", "BA", "BAC", "BKNG", "BLK", "BMY", "BNY", "BRK.B", "C",
    "CAT", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX",
    "DE", "DHR", "DIS", "DUK", "EMR", "FDX", "GD", "GE", "GEV", "GILD",
    "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG",
    "JNJ", "JPM", "KO", "LIN", "LLY", "LMT", "LOW", "LRCX", "MA", "MCD",
    "MDLZ", "MDT", "META", "MMM", "MO", "MRK", "MS", "MSFT", "MU", "NEE",
    "NFLX", "NKE", "NOW", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM",
    "QCOM", "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TMO", "TMUS", "TSLA",
    "TXN", "UBER", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC", "WMT", "XOM",
]


def main():
    provider = MoomooProvider(host='127.0.0.1', port=11111)
    storage = DataStorage()

    symbols = [f"US.{t.replace('.', '')}" for t in SP100_TICKERS]  # BRK.B -> US.BRKB
    end_date = datetime.now()
    start_date = end_date - timedelta(days=3 * 365)

    print(f"Backfilling {len(symbols)} S&P 100 symbols x 1h x 3yr\n")
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
