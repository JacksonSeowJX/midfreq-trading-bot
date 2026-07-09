import time
from datetime import datetime, timedelta
from core.models import Timeframe, Candle
from providers.ib_provider import IBProvider

def on_new_candle(candle: Candle):
    print(f"  [*] New Candle Aggregated: {candle}")

def main():
    # Note: Requires TWS or IB Gateway to be running!
    # Default Paper Trading port is 7497
    provider = IBProvider(host='127.0.0.1', port=7497, client_id=10)
    
    try:
        print("--- Interactive Brokers Demo ---")
        print("Attempting to connect to Interactive Brokers...")
        provider.connect()
        
        symbol = "AAPL"
        
        # --- Test 1: Historical Data ---
        print(f"\n[Test 1] Fetching historical daily data for {symbol}...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=5)
        
        df = provider.get_historical_data(symbol, Timeframe.DAY_1, start_date, end_date)
        if not df.empty:
            print(f"  [✓] Fetched {len(df)} historical bars.")
            print(df)
        else:
            print("  [!] No historical data returned.")

        # --- Test 2: Latest Quote ---
        print(f"\n[Test 2] Fetching latest quote for {symbol}...")
        quote = provider.get_latest_quote(symbol)
        print(f"  [✓] Quote: {quote}")

        # --- Test 3: Live Streaming ---
        print(f"\n[Test 3] Starting live 1-min candle aggregation for {symbol}...")
        print("  Listening for 30 seconds... (Press Ctrl+C to stop early)")
        
        provider.start_live_streaming(symbol, Timeframe.MIN_1, on_new_candle)
        provider.run_live(duration_seconds=30)
        
        print("\nDemo complete.")
            
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nError: {e}")
        print("Make sure TWS/IB Gateway is open and 'ActiveX and Socket Clients' is enabled in settings.")
    finally:
        provider.disconnect()

if __name__ == "__main__":
    main()
