from typing import Any, Dict
from core.models import Candle
from core.portfolio import Portfolio

class BaseStrategy:
    """
    Abstract strategy class. All custom algorithms should inherit from this.
    """
    def __init__(self, portfolio: Portfolio, **kwargs):
        self.portfolio = portfolio
        self.params = kwargs

    def on_start(self):
        """Called once before the strategy begins running data"""
        pass

    def on_data(self, symbol: str, candle: Candle):
        """
        Called every time a new completed candle appears.
        Override this method to implement crossover logic, mean reversion, etc.
        """
        raise NotImplementedError("Strategy must implement `on_data`")


class MovingAverageCrossover(BaseStrategy):
    """
    Classic SMA Crossover strategy for demonstration.
    Buys when fast MA crosses above slow MA.
    Sells when fast MA crosses below slow MA.
    """
    def __init__(self, portfolio: Portfolio, fast_period: int = 10, slow_period: int = 50):
        super().__init__(portfolio, fast_period=fast_period, slow_period=slow_period)
        self.fast = fast_period
        self.slow = slow_period
        
        # State tracking: symbol -> list of close prices
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting MA Crossover Strategy (Fast: {self.fast}, Slow: {self.slow})")

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []
            
        prices = self.history[symbol]
        prices.append(candle.close)
        
        # Truncate history array to save memory 
        # (we need enough for the slow MA + 1 for the previous period)
        if len(prices) > self.slow + 1:
            prices.pop(0)
            
        # We need enough data points to compute the slow MA and its previous state
        if len(prices) <= self.slow:
            return

        # Calculate Simple Moving Averages
        fast_ma = sum(prices[-self.fast:]) / self.fast
        slow_ma = sum(prices[-self.slow:]) / self.slow
        
        # Calculate moving averages for the *previous* candle to detect a crossover
        # Only evaluate if we have enough data (slow + 1)
        prev_fast_ma = sum(prices[-(self.fast+1):-1]) / self.fast if len(prices) > self.slow else fast_ma
        prev_slow_ma = sum(prices[-(self.slow+1):-1]) / self.slow if len(prices) > self.slow else slow_ma

        # Check for Crossover Signals
        current_position = self.portfolio.get_position_qty(symbol)
        
        # Golden Cross: Fast MA moves above Slow MA
        if prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma:
            if current_position == 0:
                # Execute Market Buy
                trade_qty = 100 # Buy 100 shares
                self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)

        # Death Cross: Fast MA moves below Slow MA
        elif prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma:
            if current_position > 0:
                # Liquidate all holdings
                self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp)
