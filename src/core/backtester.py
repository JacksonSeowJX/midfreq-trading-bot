import pandas as pd
from typing import Dict, Any, Type, Optional, Callable
from datetime import datetime

from core.models import Candle, Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.strategy import BaseStrategy
from core.risk_manager import RiskManager

class Backtester:
    """
    Event-Driven Historical Simulator Engine.
    
    Loads Parquet files, merges all symbols into a single chronological
    event stream, and replays them in time order into a Strategy.
    
    Features:
      - Chronological multi-asset replay (not sequential per symbol)
      - Configurable slippage model
      - Per-candle equity tracking for accurate Sharpe/Drawdown
      - Buy-and-hold benchmark calculation
    """
    def __init__(self, storage: DataStorage, portfolio: Portfolio, 
                 risk_manager: RiskManager = None,
                 slippage_bps: float = 0.0):
        """
        Args:
            storage: DataStorage instance for loading Parquet files
            portfolio: Portfolio instance to track trades and equity
            risk_manager: Optional RiskManager for stop-loss/sizing
            slippage_bps: Slippage in basis points (e.g., 5.0 = 0.05%)
                          Applied to execution price: buys pay more, sells receive less
        """
        self.storage = storage
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self.slippage_bps = slippage_bps
        
        # Store original execute_trade to wrap with slippage
        self._original_execute_trade = portfolio.execute_trade
        if slippage_bps > 0:
            self._apply_slippage_wrapper()

    def _apply_slippage_wrapper(self):
        """Wrap portfolio.execute_trade to apply slippage to execution prices."""
        original = self._original_execute_trade
        slippage_mult = self.slippage_bps / 10000.0
        
        def execute_with_slippage(symbol, is_buy, qty, price, timestamp, exit_reason=None):
            if is_buy:
                adjusted_price = price * (1.0 + slippage_mult)  # Pay more
            else:
                adjusted_price = price * (1.0 - slippage_mult)  # Receive less
            return original(symbol, is_buy, qty, adjusted_price, timestamp, exit_reason=exit_reason)
        
        self.portfolio.execute_trade = execute_with_slippage

    def _build_event_stream(self, all_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Merge all symbols into a single time-ordered event stream.
        
        Each row has a '_symbol' column indicating which stock the candle belongs to.
        Candles from the same timestamp are grouped together (one per symbol).
        """
        events = []
        for symbol, df in all_data.items():
            df_copy = df.copy()
            df_copy['_symbol'] = symbol
            events.append(df_copy)
        
        merged = pd.concat(events)
        # Sort by timestamp (index), then by symbol for deterministic ordering
        merged = merged.sort_index(kind='mergesort')
        return merged

    def run(self, strategy_class: Type[BaseStrategy], symbols: list[str], timeframe: Timeframe, 
            start_date: datetime = None, end_date: datetime = None,
            progress_callback: Optional[Callable[[float], None]] = None,
            **strategy_params) -> Dict[str, Any]:
        """
        Executes the backtest simulation across multiple symbols.
        
        Args:
            strategy_class: Strategy class to instantiate
            symbols: List of stock symbols to trade
            timeframe: Candle timeframe (1m, 5m, 1h, 1d, etc.)
            start_date: Start of backtest period
            end_date: End of backtest period
            progress_callback: Optional function called with progress (0.0 - 1.0)
            **strategy_params: Parameters passed to the strategy constructor
            
        Returns:
            Dict with performance metrics
        """
        # Initialize strategy with the tracking portfolio and optional risk manager
        strategy = strategy_class(self.portfolio, risk_manager=self.risk_manager, **strategy_params)
        
        print(f"=== Initializing Backtest Simulator ===")
        print(f"Cash: ${self.portfolio.initial_cash:,.2f} | Timeframe: {timeframe.value} | Symbols: {symbols}")
        if self.slippage_bps > 0:
            print(f"Slippage: {self.slippage_bps:.1f} bps")
        
        # Load data for all symbols
        all_data: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            # Clean symbol for folder load e.g. HK.00700 -> HK_00700
            folder = symbol.replace('.', '_')
            df = self.storage.load_data(folder, timeframe.value)
            
            if not df.empty and start_date:
                df = df[df.index >= pd.to_datetime(start_date, utc=True)]
            if not df.empty and end_date:
                # Include the entire end_date by adding 1 day
                df = df[df.index < (pd.to_datetime(end_date, utc=True) + pd.Timedelta(days=1))]

            if df.empty:
                print(f"  [!] Skipped {symbol}: No data found for {timeframe.value} in given date range")
                continue
                
            all_data[symbol] = df
            print(f"  [+] Loaded {len(df)} candles for {symbol}")

        if not all_data:
            print("No data available to run simulation. Aborting.")
            return {}

        # Trigger strategy initialization
        strategy.on_start()

        # ─── Build Chronological Event Stream ─────────────────────────
        event_stream = self._build_event_stream(all_data)
        total_events = len(event_stream)
        
        print(f"\n=== Simulating Trades ({total_events} events, chronological) ===")
        
        # Track latest prices for all symbols (for equity calculation)
        latest_prices: Dict[str, float] = {}
        
        # Track first prices for buy-and-hold benchmark
        first_prices: Dict[str, float] = {}
        
        # ─── Replay Events in Time Order ──────────────────────────────
        last_equity_date = None  # Track when we last recorded equity
        
        for i, (idx, row) in enumerate(event_stream.iterrows()):
            symbol = row['_symbol']
            
            # Reconstruct Candle object
            candle = Candle(
                timestamp=idx,
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume']
            )
            
            # Update latest prices
            latest_prices[symbol] = candle.close
            
            # Record first price per symbol for benchmark
            if symbol not in first_prices:
                first_prices[symbol] = candle.close
            
            # Pass into strategy brain
            strategy.on_data(symbol, candle)
            
            # ─── Per-Candle Equity Tracking ───────────────────────────
            # Record equity snapshot at most once per unique timestamp
            # (avoids duplicate entries when multiple symbols share a timestamp)
            candle_date = idx
            if candle_date != last_equity_date:
                equity = self.portfolio.get_current_equity(latest_prices)
                self.portfolio.equity_curve_detailed.append({
                    'timestamp': idx,
                    'equity': equity
                })
                last_equity_date = candle_date
            
            # Progress callback
            if progress_callback and (i % 50 == 0 or i == total_events - 1):
                progress_callback((i + 1) / total_events)

        print(f"\n=== Simulation Complete ===")
        
        # ─── Calculate Benchmark ──────────────────────────────────────
        benchmark_return = self._calculate_benchmark(first_prices, latest_prices)
        
        # Generate Results
        metrics = self.portfolio.calculate_metrics(latest_prices)
        metrics['benchmark_return_pct'] = benchmark_return
        metrics['slippage_bps'] = self.slippage_bps
        metrics['total_events'] = total_events
        
        # Calculate alpha (strategy return - benchmark return)
        metrics['alpha'] = metrics['return_pct'] - benchmark_return
        
        return metrics

    def _calculate_benchmark(self, first_prices: Dict[str, float], 
                              final_prices: Dict[str, float]) -> float:
        """
        Calculate buy-and-hold benchmark return.
        
        Simulates equal-weight allocation across all symbols at the start,
        holding until the end. Returns the percentage return.
        """
        if not first_prices or not final_prices:
            return 0.0
        
        # Equal weight allocation
        capital_per_stock = self.portfolio.initial_cash / len(first_prices)
        
        total_benchmark_value = 0.0
        for symbol in first_prices:
            if symbol in final_prices and first_prices[symbol] > 0:
                shares = capital_per_stock / first_prices[symbol]
                total_benchmark_value += shares * final_prices[symbol]
        
        benchmark_return = ((total_benchmark_value - self.portfolio.initial_cash) / 
                           self.portfolio.initial_cash) * 100
        return benchmark_return
