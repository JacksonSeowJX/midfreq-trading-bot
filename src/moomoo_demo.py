import time
import pandas as pd
from datetime import datetime, timedelta
from core.models import Timeframe, Candle
from core.storage import DataStorage
from core.config import ConfigLoader
from providers.moomoo_provider import MoomooProvider

def main():
    provider = MoomooProvider(host='127.0.0.1', port=11111)
    storage = DataStorage()
    config = ConfigLoader()
    
    # Get the "live" status HK symbols from our new config
    live_hk_symbols = config.get_live_symbols(market="HK")
    if not live_hk_symbols:
        print("No live HK symbols found in config!")
        return
        
    # We will just demo the first one in the list
    hk_symbol = live_hk_symbols[0]
    # Clean the symbol name for directory usage (e.g. "HK.00700" -> "HK_00700")
    symbol_dir = hk_symbol.replace(".", "_")
    timeframe = Timeframe.MIN_1  # 1-minute candles

    # Note: Moomoo supports 1m, 5m, 60m, and daily candles. 
    # We are demonstrating 1m here.
    
    def on_new_candle(candle: Candle):
        print(f"  [*] Live Candle arrived: {candle.timestamp.strftime('%H:%M:%S')} | Close={candle.close} | Vol={candle.volume}")
        
        # Convert single candle to DataFrame
        df = pd.DataFrame([{
            'open': candle.open,
            'high': candle.high,
            'low': candle.low,
            'close': candle.close,
            'volume': candle.volume
        }], index=[candle.timestamp])
        
        # Append silently to parquet
        # storage.append_data will print "Data saved to..." each time a candle is processed
        storage.append_data(df, symbol_dir, timeframe.value)

    try:
        print("--- Moomoo Market Data Demo (Live Streaming & Storage) ---")
        
        # 1. Fetch historical 1-min data to build baseline
        print(f"\n[1] Fetching historical {timeframe.value} data for {hk_symbol}...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=2) # Past 2 days of 1-min data
        
        df_hist = provider.get_historical_data(hk_symbol, timeframe, start_date, end_date)
        
        if not df_hist.empty:
            storage.append_data(df_hist, symbol_dir, timeframe.value)
            print(f"  [✓] Fetched {len(df_hist)} historical {timeframe.value} candles. Seeded storage.")
        else:
            print("  [!] No historical data returned.")
            
        # 2. Start Live Streaming
        print(f"\n[2] Starting live {timeframe.value} candle streaming for {hk_symbol}...")
        print("  Listening for 30 seconds. Candles will be appended to Parquet as they arrive...")
        provider.start_live_streaming(hk_symbol, timeframe, on_new_candle)

        # Keep the script alive for 30s so the callback triggers
        time.sleep(30)
        
        print("\nReading back the final Parquet file to verify tail:")
        df_verif = storage.load_data(symbol_dir, timeframe.value)
        print(df_verif.tail(5))
        
        print("\nDemo complete.")

    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        provider.close()

if __name__ == "__main__":
    main()
