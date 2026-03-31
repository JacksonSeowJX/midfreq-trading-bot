import time
from datetime import datetime, timedelta
from core.models import Timeframe, Candle
from core.storage import DataStorage
from providers.moomoo_provider import MoomooProvider

def on_new_candle(candle: Candle):
    print(f"  [*] Live Candle: {candle}")

def main():
    # Requires OpenD gateway to be running on your Mac
    provider = MoomooProvider(host='127.0.0.1', port=11111)
    storage = DataStorage()

    # HK test symbols
    hk_symbol_1 = "HK.00700"  # Tencent
    hk_symbol_2 = "HK.00005"  # HSBC

    try:
        print("--- Moomoo Market Data Demo ---")
        print("Testing HK market data retrieval\n")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        # --- Test 1a: Historical Data (Tencent) ---
        print(f"[Test 1a] Fetching historical daily data for {hk_symbol_1} (Tencent)...")
        df_1 = provider.get_historical_data(hk_symbol_1, Timeframe.DAY_1, start_date, end_date)
        if not df_1.empty:
            print(f"  [✓] Fetched {len(df_1)} bars for {hk_symbol_1}")
            print(df_1.tail())
            storage.save_data(df_1, "TENCENT_HK", Timeframe.DAY_1.value)
        else:
            print(f"  [!] No data returned for {hk_symbol_1}")

        # --- Test 1b: Historical Data (HSBC) ---
        print(f"\n[Test 1b] Fetching historical daily data for {hk_symbol_2} (HSBC)...")
        df_2 = provider.get_historical_data(hk_symbol_2, Timeframe.DAY_1, start_date, end_date)
        if not df_2.empty:
            print(f"  [✓] Fetched {len(df_2)} bars for {hk_symbol_2}")
            print(df_2.tail())
            storage.save_data(df_2, "HSBC_HK", Timeframe.DAY_1.value)
        else:
            print(f"  [!] No data returned for {hk_symbol_2}")

        # --- Test 2: Latest Quote ---
        print(f"\n[Test 2] Fetching latest quote for {hk_symbol_1}...")
        quote = provider.get_latest_quote(hk_symbol_1)
        print(f"  [✓] Quote: {quote}")

        # --- Test 3: Live Streaming ---
        print(f"\n[Test 3] Starting live 1-min candle streaming for {hk_symbol_1}...")
        print("  Listening for 30 seconds... (Press Ctrl+C to stop early)")
        provider.start_live_streaming(hk_symbol_1, Timeframe.MIN_1, on_new_candle)

        # Keep alive to receive push updates
        time.sleep(30)

        print("\nDemo complete.")

    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nError: {e}")
        print("Make sure Moomoo OpenD is running and you are logged in.")
    finally:
        provider.close()

if __name__ == "__main__":
    main()
