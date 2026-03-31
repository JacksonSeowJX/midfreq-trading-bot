import pandas as pd
from typing import Dict, Any, Type
from datetime import datetime

from core.models import Candle, Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.strategy import BaseStrategy

class Backtester:
    """
    Historical Simulator Engine.
    Loads Parquet files and replays them rapidly into a provided Strategy.
    """
    def __init__(self, storage: DataStorage, portfolio: Portfolio):
        self.storage = storage
        self.portfolio = portfolio

    def run(self, strategy_class: Type[BaseStrategy], symbols: list[str], timeframe: Timeframe, **strategy_params) -> Dict[str, Any]:
        """
        Executes the backtest simulation across multiple symbols.
        """
        # Initialize strategy with the tracking portfolio
        strategy = strategy_class(self.portfolio, **strategy_params)
        
        print(f"=== Initializing Backtest Simulator ===")
        print(f"Cash: ${self.portfolio.initial_cash:,.2f} | Timeframe: {timeframe.value} | Symbols: {symbols}")
        
        # Load data for all symbols
        all_data: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            # Clean symbol for folder load e.g. HK.00700 -> HK_00700
            folder = symbol.replace('.', '_')
            df = self.storage.load_data(folder, timeframe.value)
            
            if df.empty:
                print(f"  [!] Skipped {symbol}: No data found for {timeframe.value}")
                continue
                
            all_data[symbol] = df
            print(f"  [+] Loaded {len(df)} candles for {symbol}")

        if not all_data:
            print("No data available to run simulation. Aborting.")
            return {}

        # Trigger strategy initialization
        strategy.on_start()

        print(f"\n=== Simulating Trades ===")
        # In a real multi-symbol backtester, we have to align the events chronologically 
        # but for this MVP, we will run each symbol sequentially from start to finish
        final_prices: Dict[str, float] = {}
        
        for symbol, df in all_data.items():
            print(f"--> Replaying {symbol}...")
            
            # Replay rows as candles
            for idx, row in df.iterrows():
                # Reconstruct Candle object 
                candle = Candle(
                    timestamp=idx,
                    open=row['open'],
                    high=row['high'],
                    low=row['low'],
                    close=row['close'],
                    volume=row['volume']
                )
                
                # Pass into strategy brain
                strategy.on_data(symbol, candle)
                
                # Keep track of the final closing price for PnL evaluation at the end
                final_prices[symbol] = candle.close

        print(f"\n=== Simulation Complete ===")
        
        # Generate Results
        return self.portfolio.calculate_metrics(final_prices)
