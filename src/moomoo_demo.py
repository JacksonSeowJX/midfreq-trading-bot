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

    # SG and HK test symbols
    # Moomoo format: "SG.D05" for DBS (Singapore), "HK.00700" for Tencent (Hong Kong)
    sg_symbol = "SG.D05"    # DBS Group (Singapore)
    hk_symbol = "HK.00700"  # Tencent (Hong Kong)

    try:
        print("--- Moomoo Market Data Demo ---")
        print(f"Testing SG and HK market data retrieval\n")

        # --- Test 1: Historical Data (SG) ---
        print(f"[Test 1a] Fetching historical daily data for {sg_symbol} (DBS)...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        df_sg = provider.get_historical_data(sg_symbol, Timeframe.DAY_1, start_date, end_date)
        if not df_sg.empty:
            print(f"  [✓] Fetched {len(df_sg)} bars for {sg_symbol}")
            print(df_sg.tail())
            storage.save_data(df_sg, "DBS_SG", Timeframe.DAY_1.value)
        else:
            print(f"  [!] No data returned for {sg_symbol}")

        # --- Test 1b: Historical Data (HK) ---
        print(f"\n[Test 1b] Fetching historical daily data for {hk_symbol} (Tencent)...")
        df_hk = provider.get_historical_data(hk_symbol, Timeframe.DAY_1, start_date, end_date)
        if not df_hk.empty:
            print(f"  [✓] Fetched {len(df_hk)} bars for {hk_symbol}")
            print(df_hk.tail())
            storage.save_data(df_hk, "TENCENT_HK", Timeframe.DAY_1.value)
        else:
            print(f"  [!] No data returned for {hk_symbol}")

        # --- Test 2: Latest Quote ---
        print(f"\n[Test 2] Fetching latest quote for {sg_symbol}...")
        quote = provider.get_latest_quote(sg_symbol)
        print(f"  [✓] Quote: {quote}")

        # --- Test 3: Live Streaming ---
        print(f"\n[Test 3] Starting live 1-min candle streaming for {hk_symbol}...")
        print("  Listening for 30 seconds... (Press Ctrl+C to stop early)")
        provider.start_live_streaming(hk_symbol, Timeframe.MIN_1, on_new_candle)

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
