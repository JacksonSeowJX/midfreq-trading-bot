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


class RSIStrategy(BaseStrategy):
    """
    Relative Strength Index (Mean Reversion) strategy.
    Buys when RSI drops below the oversold threshold.
    Sells when RSI rises above the overbought threshold.
    """
    def __init__(self, portfolio: Portfolio, rsi_period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__(portfolio, rsi_period=rsi_period, oversold=oversold, overbought=overbought)
        self.period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting RSI Strategy (Period: {self.period}, Oversold: {self.oversold}, Overbought: {self.overbought})")

    def _compute_rsi(self, prices: list) -> float:
        """Compute RSI from a list of closing prices."""
        if len(prices) < self.period + 1:
            return 50.0  # Neutral default

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = deltas[-(self.period):]

        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]

        avg_gain = sum(gains) / self.period if gains else 0.0
        avg_loss = sum(losses) / self.period if losses else 0.0

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []

        prices = self.history[symbol]
        prices.append(candle.close)

        # Keep only enough data for RSI calculation
        if len(prices) > self.period + 2:
            prices.pop(0)

        if len(prices) < self.period + 1:
            return

        rsi = self._compute_rsi(prices)
        current_position = self.portfolio.get_position_qty(symbol)

        # Oversold → BUY signal
        if rsi < self.oversold and current_position == 0:
            trade_qty = 100
            self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)

        # Overbought → SELL signal
        elif rsi > self.overbought and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp)


class MACDStrategy(BaseStrategy):
    """
    Moving Average Convergence Divergence (Momentum) strategy.
    Uses EMA-based MACD line and signal line crossovers.
    Buys when MACD crosses above the signal line.
    Sells when MACD crosses below the signal line.
    """
    def __init__(self, portfolio: Portfolio, fast_ema: int = 12, slow_ema: int = 26, signal_period: int = 9):
        super().__init__(portfolio, fast_ema=fast_ema, slow_ema=slow_ema, signal_period=signal_period)
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.signal_period = signal_period
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting MACD Strategy (Fast EMA: {self.fast_ema}, Slow EMA: {self.slow_ema}, Signal: {self.signal_period})")

    @staticmethod
    def _ema(prices: list, period: int) -> list:
        """Compute Exponential Moving Average series."""
        if len(prices) < period:
            return []
        multiplier = 2.0 / (period + 1)
        ema_values = [sum(prices[:period]) / period]  # SMA seed
        for price in prices[period:]:
            ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])
        return ema_values

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []

        prices = self.history[symbol]
        prices.append(candle.close)

        # Need enough data: slow_ema + signal_period + 1 (for prev crossover detection)
        min_len = self.slow_ema + self.signal_period + 1
        if len(prices) < min_len:
            return

        # Trim to save memory
        max_keep = min_len + 50
        if len(prices) > max_keep:
            self.history[symbol] = prices[-max_keep:]
            prices = self.history[symbol]

        fast_ema_vals = self._ema(prices, self.fast_ema)
        slow_ema_vals = self._ema(prices, self.slow_ema)

        # Align: slow EMA starts later, so trim fast EMA to match
        offset = len(fast_ema_vals) - len(slow_ema_vals)
        fast_aligned = fast_ema_vals[offset:]

        # MACD line = Fast EMA - Slow EMA
        macd_line = [f - s for f, s in zip(fast_aligned, slow_ema_vals)]

        if len(macd_line) < self.signal_period + 1:
            return

        # Signal line = EMA of MACD line
        signal_line = self._ema(macd_line, self.signal_period)

        if len(signal_line) < 2:
            return

        # Crossover detection on the last two values
        macd_offset = len(macd_line) - len(signal_line)
        macd_curr = macd_line[-1]
        macd_prev = macd_line[-2]
        signal_curr = signal_line[-1]
        signal_prev = signal_line[-2]

        current_position = self.portfolio.get_position_qty(symbol)

        # Bullish crossover: MACD crosses above Signal
        if macd_prev <= signal_prev and macd_curr > signal_curr:
            if current_position == 0:
                trade_qty = 100
                self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)

        # Bearish crossover: MACD crosses below Signal
        elif macd_prev >= signal_prev and macd_curr < signal_curr:
            if current_position > 0:
                self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp)


class BollingerBandsStrategy(BaseStrategy):
    """
    Bollinger Bands (Volatility / Mean Reversion) strategy.
    Buys when price touches the lower band (oversold).
    Sells when price touches the upper band (overbought).
    """
    def __init__(self, portfolio: Portfolio, bb_period: int = 20, num_std: float = 2.0):
        super().__init__(portfolio, bb_period=bb_period, num_std=num_std)
        self.period = bb_period
        self.num_std = num_std
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting Bollinger Bands Strategy (Period: {self.period}, Std Dev: {self.num_std})")

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []

        prices = self.history[symbol]
        prices.append(candle.close)

        if len(prices) > self.period + 2:
            prices.pop(0)

        if len(prices) < self.period:
            return

        window = prices[-self.period:]
        middle = sum(window) / self.period
        variance = sum((p - middle) ** 2 for p in window) / self.period
        std_dev = variance ** 0.5

        upper_band = middle + self.num_std * std_dev
        lower_band = middle - self.num_std * std_dev

        current_position = self.portfolio.get_position_qty(symbol)

        # Price at or below lower band → BUY
        if candle.close <= lower_band and current_position == 0:
            trade_qty = 100
            self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)


        # Price at or above upper band → SELL
        elif candle.close >= upper_band and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp)


class ZScoreMeanReversion(BaseStrategy):
    """
    Statistical Mean Reversion strategy using Z-Score.
    
    Computes a rolling z-score: z = (price - rolling_mean) / rolling_std
    Buys when z < -entry_z (statistically cheap — price is multiple std devs below mean).
    Sells when z > +entry_z (statistically expensive).
    Exits (flattens) when z reverts toward 0 (crosses exit_z).
    
    Mathematical basis: Assumes price is mean-reverting and approximately normally
    distributed over the lookback window. Extreme z-scores (|z| > 2) occur <5% of
    the time under a normal distribution, suggesting a reversion is likely.
    """
    def __init__(self, portfolio: Portfolio, lookback: int = 20, entry_z: float = 2.0, exit_z: float = 0.5):
        super().__init__(portfolio, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting Z-Score Mean Reversion (Lookback: {self.lookback}, Entry Z: ±{self.entry_z}, Exit Z: ±{self.exit_z})")

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []

        prices = self.history[symbol]
        prices.append(candle.close)

        if len(prices) > self.lookback + 2:
            prices.pop(0)

        if len(prices) < self.lookback:
            return

        window = prices[-self.lookback:]
        mean = sum(window) / self.lookback
        variance = sum((p - mean) ** 2 for p in window) / self.lookback
        std = variance ** 0.5

        if std == 0:
            return

        z = (candle.close - mean) / std
        current_position = self.portfolio.get_position_qty(symbol)

        # Z-score very negative → price is statistically cheap → BUY
        if z < -self.entry_z and current_position == 0:
            trade_qty = 100
            self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)

        # Z-score reverts toward 0 → take profit on long
        elif current_position > 0 and z > -self.exit_z:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp)


class PairsTradingStrategy(BaseStrategy):
    """
    Statistical Arbitrage / Pairs Trading strategy.
    
    Trades the price RATIO (spread) between two correlated stocks.
    When the spread deviates significantly from its rolling mean (measured
    by z-score), we bet on mean reversion of the spread.
    
    Requires two symbols to be passed to the backtester. The strategy:
      1. Computes spread = price_A / price_B (log ratio)
      2. Computes rolling z-score of the spread
      3. When z < -entry_z: BUY stock A (undervalued relative to B)
      4. When z > +entry_z: SELL stock A (overvalued relative to B)
      5. Exit when z crosses back toward 0
    
    Mathematical basis: If two stocks are cointegrated (long-run equilibrium),
    their ratio is stationary and will revert to its mean. We exploit temporary
    dislocations in this ratio for profit.
    
    Note: In this simplified version, we only trade stock A (long/flat).
    A full implementation would simultaneously short stock B.
    """
    def __init__(self, portfolio: Portfolio, lookback: int = 30, entry_z: float = 2.0, exit_z: float = 0.5):
        super().__init__(portfolio, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        # Store prices for both symbols
        self.prices: Dict[str, list] = {}
        self._symbols_list: list = []

    def on_start(self):
        print(f"Starting Pairs Trading (Lookback: {self.lookback}, Entry Z: ±{self.entry_z}, Exit Z: ±{self.exit_z})")

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.prices:
            self.prices[symbol] = []
            self._symbols_list.append(symbol)

        self.prices[symbol].append(candle.close)

        # Need at least 2 symbols for pairs trading
        if len(self._symbols_list) < 2:
            return

        sym_a = self._symbols_list[0]
        sym_b = self._symbols_list[1]

        # Only evaluate when we're processing the second symbol's candle
        # (ensures both have the same number of data points at evaluation time)
        if symbol != sym_b:
            return

        prices_a = self.prices[sym_a]
        prices_b = self.prices[sym_b]

        min_len = min(len(prices_a), len(prices_b))
        if min_len < self.lookback:
            return

        # Compute the spread (price ratio) over the lookback window
        import math
        spread = []
        for i in range(min_len - self.lookback, min_len):
            if prices_b[i] != 0:
                spread.append(math.log(prices_a[i] / prices_b[i]))

        if len(spread) < self.lookback:
            return

        mean_spread = sum(spread) / len(spread)
        var_spread = sum((s - mean_spread) ** 2 for s in spread) / len(spread)
        std_spread = var_spread ** 0.5

        if std_spread == 0:
            return

        current_spread = spread[-1]
        z = (current_spread - mean_spread) / std_spread

        current_position = self.portfolio.get_position_qty(sym_a)

        # Spread is very low → stock A is cheap relative to B → BUY A
        if z < -self.entry_z and current_position == 0:
            trade_qty = 100
            self.portfolio.execute_trade(sym_a, True, trade_qty, prices_a[-1], candle.timestamp)

        # Spread reverts → take profit
        elif current_position > 0 and z > -self.exit_z:
            self.portfolio.execute_trade(sym_a, False, current_position, prices_a[-1], candle.timestamp)


class EnsembleStrategy(BaseStrategy):
    """
    Ensemble Voting Strategy.
    
    Combines RSI, MACD, and Bollinger Bands.
    Each sub-strategy gets 1 vote (-1 for Sell, 0 for Hold, +1 for Buy).
    
    Buy Signal: Total Score >= 2 (at least 2 out of 3 strategies say BUY)
    Sell Signal: Total Score <= -2 (at least 2 out of 3 strategies say SELL)
    """
    def __init__(self, portfolio: Portfolio, consensus_threshold: int = 2):
        super().__init__(portfolio, consensus_threshold=consensus_threshold)
        self.consensus_threshold = consensus_threshold
        
        # Instantiate sub-strategies (we use dummy portfolios for them since the ensemble handles actual trades)
        dummy_portfolio = Portfolio(initial_cash=0, commission_rate=0)
        self.rsi_strat = RSIStrategy(dummy_portfolio, rsi_period=14, oversold=30, overbought=70)
        self.macd_strat = MACDStrategy(dummy_portfolio, fast_ema=12, slow_ema=26, signal_period=9)
        self.bb_strat = BollingerBandsStrategy(dummy_portfolio, bb_period=20, num_std=2.0)
        
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting Ensemble Strategy (Consensus Threshold: ±{self.consensus_threshold})")
        self.rsi_strat.on_start()
        self.macd_strat.on_start()
        self.bb_strat.on_start()

    def on_data(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []

        self.history[symbol].append(candle.close)
        prices = self.history[symbol]
        
        # Truncate history to save memory (longest lookback needed is 50 for bb or macd)
        if len(prices) > 100:
            prices.pop(0)
            
        if len(prices) < 30:
            return

        vote_score = 0
        
        # 1. RSI Vote
        rsi = self.rsi_strat._compute_rsi(prices)
        if rsi < self.rsi_strat.oversold:
            vote_score += 1
        elif rsi > self.rsi_strat.overbought:
            vote_score -= 1
            
        # 2. MACD Vote
        fast_ema_vals = self.macd_strat._ema(prices, self.macd_strat.fast_ema)
        slow_ema_vals = self.macd_strat._ema(prices, self.macd_strat.slow_ema)
        
        offset = len(fast_ema_vals) - len(slow_ema_vals)
        fast_aligned = fast_ema_vals[offset:]
        macd_line = [f - s for f, s in zip(fast_aligned, slow_ema_vals)]
        
        if len(macd_line) >= self.macd_strat.signal_period + 1:
            signal_line = self.macd_strat._ema(macd_line, self.macd_strat.signal_period)
            if len(signal_line) >= 2:
                macd_curr = macd_line[-1]
                macd_prev = macd_line[-2]
                signal_curr = signal_line[-1]
                signal_prev = signal_line[-2]
                
                if macd_prev <= signal_prev and macd_curr > signal_curr:
                    vote_score += 1
                elif macd_prev >= signal_prev and macd_curr < signal_curr:
                    vote_score -= 1
                    
        # 3. Bollinger Bands Vote
        window = prices[-self.bb_strat.period:]
        if len(window) == self.bb_strat.period:
            middle = sum(window) / self.bb_strat.period
            variance = sum((p - middle) ** 2 for p in window) / self.bb_strat.period
            std_dev = variance ** 0.5
            upper_band = middle + self.bb_strat.num_std * std_dev
            lower_band = middle - self.bb_strat.num_std * std_dev
            
            if candle.close <= lower_band:
                vote_score += 1
            elif candle.close >= upper_band:
                vote_score -= 1

        current_position = self.portfolio.get_position_qty(symbol)

        # Consensus BUY
        if vote_score >= self.consensus_threshold and current_position == 0:
            trade_qty = 100
            self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)

        # Consensus SELL
        elif vote_score <= -self.consensus_threshold and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp)


# ─── Strategy Registry ─────────────────────────────────────────────
# Maps display names to (strategy_class, default_params) for the GUI
STRATEGY_REGISTRY = {
    "SMA Crossover": {
        "class": MovingAverageCrossover,
        "params": {
            "fast_period": {"label": "Fast MA Period", "min": 2, "max": 50, "default": 5, "step": 1},
            "slow_period": {"label": "Slow MA Period", "min": 5, "max": 200, "default": 20, "step": 1},
        }
    },
    "RSI": {
        "class": RSIStrategy,
        "params": {
            "rsi_period": {"label": "RSI Period", "min": 2, "max": 50, "default": 14, "step": 1},
            "oversold":   {"label": "Oversold Threshold", "min": 10, "max": 40, "default": 30, "step": 1},
            "overbought": {"label": "Overbought Threshold", "min": 60, "max": 95, "default": 70, "step": 1},
        }
    },
    "MACD": {
        "class": MACDStrategy,
        "params": {
            "fast_ema":      {"label": "Fast EMA Period", "min": 2, "max": 50, "default": 12, "step": 1},
            "slow_ema":      {"label": "Slow EMA Period", "min": 10, "max": 100, "default": 26, "step": 1},
            "signal_period": {"label": "Signal Line Period", "min": 2, "max": 30, "default": 9, "step": 1},
        }
    },
    "Bollinger Bands": {
        "class": BollingerBandsStrategy,
        "params": {
            "bb_period": {"label": "BB Period", "min": 5, "max": 50, "default": 20, "step": 1},
            "num_std":   {"label": "Std Deviations", "min": 1.0, "max": 3.5, "default": 2.0, "step": 0.1},
        }
    },
    "Z-Score Mean Reversion": {
        "class": ZScoreMeanReversion,
        "params": {
            "lookback": {"label": "Lookback Window", "min": 5, "max": 100, "default": 20, "step": 1},
            "entry_z":  {"label": "Entry Z-Score", "min": 1.0, "max": 3.5, "default": 2.0, "step": 0.1},
            "exit_z":   {"label": "Exit Z-Score", "min": 0.0, "max": 1.5, "default": 0.5, "step": 0.1},
        }
    },
    "Pairs Trading": {
        "class": PairsTradingStrategy,
        "params": {
            "lookback": {"label": "Lookback Window", "min": 10, "max": 100, "default": 30, "step": 1},
            "entry_z":  {"label": "Entry Z-Score", "min": 1.0, "max": 3.5, "default": 2.0, "step": 0.1},
            "exit_z":   {"label": "Exit Z-Score", "min": 0.0, "max": 1.5, "default": 0.5, "step": 0.1},
        }
    },
    "Ensemble Voting": {
        "class": EnsembleStrategy,
        "params": {
            "consensus_threshold": {"label": "Consensus Required (votes)", "min": 1, "max": 3, "default": 2, "step": 1},
        }
    },
}

