"""
Fetch daily historical data for ALL configured HK stocks from Moomoo.
Stores each stock's data into data/<SYMBOL>/1d.parquet
"""
import sys
import time
sys.path.insert(0, 'src')

from datetime import datetime, timedelta
from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from providers.moomoo_provider import MoomooProvider

def main():
    provider = MoomooProvider(host='127.0.0.1', port=11111)
    storage = DataStorage()
    config = ConfigLoader()
    
    hk_symbols = config.get_live_symbols(market="HK")
    print(f"Found {len(hk_symbols)} HK symbols to fetch: {hk_symbols}")
    
    # Fetch 1 year of daily data
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    timeframe = Timeframe.DAY_1
    
    results = []
    
    for i, symbol in enumerate(hk_symbols):
        symbol_dir = symbol.replace(".", "_")
        print(f"\n[{i+1}/{len(hk_symbols)}] Fetching {symbol} daily data...")
        
        try:
            df = provider.get_historical_data(symbol, timeframe, start_date, end_date)
            
            if not df.empty:
                storage.append_data(df, symbol_dir, timeframe.value)
                results.append((symbol, len(df), "OK"))
                print(f"  [+] {len(df)} daily candles saved")
            else:
                results.append((symbol, 0, "EMPTY"))
                print(f"  [!] No data returned")
        except Exception as e:
            results.append((symbol, 0, f"ERROR: {e}"))
            print(f"  [!] Error: {e}")
        
        # Small delay to respect Moomoo rate limits
        if i < len(hk_symbols) - 1:
            time.sleep(1)
    
    provider.close()
    
    # Summary
    print("\n" + "=" * 60)
    print("COLLECTION SUMMARY")
    print("=" * 60)
    print(f"{'Symbol':<15} {'Candles':<10} {'Status'}")
    print("-" * 45)
    for sym, count, status in results:
        print(f"{sym:<15} {count:<10} {status}")
    
    total = sum(r[1] for r in results)
    print(f"\nTotal: {total} daily candles across {len(results)} stocks")

if __name__ == "__main__":
    main()
