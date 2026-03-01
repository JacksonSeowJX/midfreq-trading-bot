import asyncio
from datetime import datetime, timedelta
from core.models import Timeframe, Candle
from providers.ib_provider import IBProvider

def on_new_candle(candle: Candle):
    print(f"[*] New Candle Aggregated: {candle}")

async def main():
    # Note: Requires TWS or IB Gateway to be running!
    # Default Paper Trading port is 7497
    provider = IBProvider(host='127.0.0.1', port=7497, client_id=10)
    
    try:
        print("Attempting to connect to Interactive Brokers...")
        provider.connect()
        
        symbol = "AAPL"
        timeframe = Timeframe.MIN_1
        
        # 1. Test Historical Data
        print(f"\nFetching historical 1-min data for {symbol}...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)
        
        df = provider.get_historical_data(symbol, timeframe, start_date, end_date)
        if not df.empty:
            print(f"Fetched {len(df)} historical bars.")
            print(df.tail())
        
        # 2. Test Live Streaming
        print(f"\nStarting live 1-min candle aggregation for {symbol}...")
        print("Waiting for market data (ticks)... Press Ctrl+C to stop.")
        
        provider.start_live_streaming(symbol, timeframe, on_new_candle)
        
        # Keep the script running to receive ticks
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure TWS/IB Gateway is open and 'ActiveX and Socket Clients' is enabled in settings.")
    finally:
        provider.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
