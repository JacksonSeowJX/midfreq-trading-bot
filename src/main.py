import os
from datetime import datetime, timedelta
import pandas as pd
from core.models import Timeframe
from providers.yfinance_provider import YFinanceProvider
from core.storage import DataStorage

def main():
    # Initialize components
    # The provider handles fetching from external APIs (e.g., Yahoo Finance)
    provider = YFinanceProvider()
    # The storage handles saving/loading validated data to local Parquet files
    storage = DataStorage()

    # You can add more symbols to this list
    symbols = ["AAPL", "TSLA", "MSFT", "NVDA"]
    timeframe = Timeframe.DAY_1
    
    print(f"--- Market Data Service Demo ---")
    print(f"Timeframe: {timeframe.value}")
    
    # Define date range for historical data
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    for symbol in symbols:
        print(f"\nProcessing {symbol}...")
        
        # 1. Fetch historical data
        # Data is automatically standardized to UTC and standard OHLCV columns
        df = provider.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date
        )
        
        if not df.empty:
            print(f"  [✓] Fetched {len(df)} rows.")
            
            # 2. Save to standardized storage
            # Data is saved in data/{SYMBOL}/{timeframe}.parquet
            storage.save_data(df, symbol, timeframe.value)
            
            # 3. Verify storage by loading it back
            loaded_df = storage.load_data(symbol, timeframe.value)
            print(f"  [✓] Successfully stored and loaded {len(loaded_df)} rows.")
            
            # 4. Fetch latest live quote
            quote = provider.get_latest_quote(symbol)
            price = quote.get('last_price')
            print(f"  [✓] Latest Quote: ${price:.2f} at {quote['timestamp']}")
        else:
            print(f"  [!] No data found for {symbol}.")

    print(f"\nDemo complete. Check the 'data/' directory for the Parquet files.")

if __name__ == "__main__":
    main()
