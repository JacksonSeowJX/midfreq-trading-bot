from typing import Any, Dict, Optional
from core.models import Candle
from core.portfolio import Portfolio
from core.risk_manager import RiskManager

class BaseStrategy:
    """
    Abstract strategy class. All custom algorithms should inherit from this.
    """
    def __init__(self, portfolio: Portfolio, risk_manager: Optional[RiskManager] = None, **kwargs):
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self.params = kwargs

    def on_start(self):
        """Called once before the strategy begins running data"""
        pass

    def _get_trade_qty(self, symbol: str, price: float) -> int:
        """Calculate trade quantity using risk manager or default to 100."""
        if self.risk_manager:
            stats = self.portfolio.get_trade_stats()
            equity = self.portfolio.cash + sum(
                p['qty'] * price for p in self.portfolio.positions.values()
            )
            return self.risk_manager.calculate_position_size(
                equity=equity,
                entry_price=price,
                win_rate=stats.get('win_rate'),
                avg_win=stats.get('avg_win'),
                avg_loss=stats.get('avg_loss'),
            )
        return 100

    def _check_risk_exits(self, symbol: str, candle: Candle) -> bool:
        """
        Check and execute any risk-based exit signals.
        Returns True if an exit was triggered (strategy logic should be skipped).
        """
        if not self.risk_manager:
            return False

        current_position = self.portfolio.get_position_qty(symbol)
        if current_position <= 0:
            return False

        # Update trailing stop peak price
        self.risk_manager.update_peak_price(symbol, candle.close)
        self.portfolio.update_peak_price(symbol, candle.close)

        # Check for risk exit signals
        entry_price = self.portfolio.get_entry_price(symbol)
        exit_reason = self.risk_manager.check_exit_signals(
            symbol, candle.close, entry_price
        )

        if exit_reason:
            self.portfolio.execute_trade(
                symbol, False, current_position, candle.close, candle.timestamp,
                exit_reason=exit_reason
            )
            self.risk_manager.clear_position(symbol)
            return True

        return False

    def _is_halted(self, current_price: float) -> bool:
        """Check if the portfolio circuit breaker has been triggered."""
        if not self.risk_manager:
            return False
        equity = self.portfolio.cash + sum(
            p['qty'] * current_price for p in self.portfolio.positions.values()
        )
        return self.risk_manager.is_trading_halted(equity, self.portfolio.initial_cash)

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
    def __init__(self, portfolio: Portfolio, fast_period: int = 10, slow_period: int = 50, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, fast_period=fast_period, slow_period=slow_period)
        self.fast = fast_period
        self.slow = slow_period
        
        # State tracking: symbol -> list of close prices
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting MA Crossover Strategy (Fast: {self.fast}, Slow: {self.slow})")

    def on_data(self, symbol: str, candle: Candle):
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
                trade_qty = self._get_trade_qty(symbol, candle.close)
                if trade_qty > 0:
                    self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)
                    if self.risk_manager:
                        self.risk_manager.register_entry(symbol, candle.close)

        # Death Cross: Fast MA moves below Slow MA
        elif prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma:
            if current_position > 0:
                # Liquidate all holdings
                self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp, exit_reason='signal')
                if self.risk_manager:
                    self.risk_manager.clear_position(symbol)


class RSIStrategy(BaseStrategy):
    """
    Relative Strength Index (Mean Reversion) strategy.
    Buys when RSI drops below the oversold threshold.
    Sells when RSI rises above the overbought threshold.
    """
    def __init__(self, portfolio: Portfolio, rsi_period: int = 14, oversold: float = 30, overbought: float = 70, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, rsi_period=rsi_period, oversold=oversold, overbought=overbought)
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
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
            trade_qty = self._get_trade_qty(symbol, candle.close)
            if trade_qty > 0:
                self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(symbol, candle.close)

        # Overbought → SELL signal
        elif rsi > self.overbought and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)


class MACDStrategy(BaseStrategy):
    """
    Moving Average Convergence Divergence (Momentum) strategy.
    Uses EMA-based MACD line and signal line crossovers.
    Buys when MACD crosses above the signal line.
    Sells when MACD crosses below the signal line.
    """
    def __init__(self, portfolio: Portfolio, fast_ema: int = 12, slow_ema: int = 26, signal_period: int = 9, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, fast_ema=fast_ema, slow_ema=slow_ema, signal_period=signal_period)
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
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
                trade_qty = self._get_trade_qty(symbol, candle.close)
                if trade_qty > 0:
                    self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)
                    if self.risk_manager:
                        self.risk_manager.register_entry(symbol, candle.close)

        # Bearish crossover: MACD crosses below Signal
        elif macd_prev >= signal_prev and macd_curr < signal_curr:
            if current_position > 0:
                self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp, exit_reason='signal')
                if self.risk_manager:
                    self.risk_manager.clear_position(symbol)


class BollingerBandsStrategy(BaseStrategy):
    """
    Bollinger Bands (Volatility / Mean Reversion) strategy.
    Buys when price touches the lower band (oversold).
    Sells when price touches the upper band (overbought).
    """
    def __init__(self, portfolio: Portfolio, bb_period: int = 20, num_std: float = 2.0, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, bb_period=bb_period, num_std=num_std)
        self.period = bb_period
        self.num_std = num_std
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting Bollinger Bands Strategy (Period: {self.period}, Std Dev: {self.num_std})")

    def on_data(self, symbol: str, candle: Candle):
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
            trade_qty = self._get_trade_qty(symbol, candle.close)
            if trade_qty > 0:
                self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(symbol, candle.close)


        # Price at or above upper band → SELL
        elif candle.close >= upper_band and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)


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
    def __init__(self, portfolio: Portfolio, lookback: int = 20, entry_z: float = 2.0, exit_z: float = 0.5, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.history: Dict[str, list] = {}

    def on_start(self):
        print(f"Starting Z-Score Mean Reversion (Lookback: {self.lookback}, Entry Z: ±{self.entry_z}, Exit Z: ±{self.exit_z})")

    def on_data(self, symbol: str, candle: Candle):
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
            trade_qty = self._get_trade_qty(symbol, candle.close)
            if trade_qty > 0:
                self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(symbol, candle.close)

        # Z-score reverts toward 0 → take profit on long
        elif current_position > 0 and z > -self.exit_z:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)


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
    def __init__(self, portfolio: Portfolio, lookback: int = 30, entry_z: float = 2.0, exit_z: float = 0.5, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        # Store prices for both symbols
        self.prices: Dict[str, list] = {}
        self._symbols_list: list = []

    def on_start(self):
        print(f"Starting Pairs Trading (Lookback: {self.lookback}, Entry Z: ±{self.entry_z}, Exit Z: ±{self.exit_z})")

    def on_data(self, symbol: str, candle: Candle):
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
            trade_qty = self._get_trade_qty(sym_a, prices_a[-1])
            if trade_qty > 0:
                self.portfolio.execute_trade(sym_a, True, trade_qty, prices_a[-1], candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(sym_a, prices_a[-1])

        # Spread reverts → take profit
        elif current_position > 0 and z > -self.exit_z:
            self.portfolio.execute_trade(sym_a, False, current_position, prices_a[-1], candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(sym_a)


class EnsembleStrategy(BaseStrategy):
    """
    Ensemble Voting Strategy.
    
    Combines RSI, MACD, and Bollinger Bands.
    Each sub-strategy gets 1 vote (-1 for Sell, 0 for Hold, +1 for Buy).
    
    Buy Signal: Total Score >= 2 (at least 2 out of 3 strategies say BUY)
    Sell Signal: Total Score <= -2 (at least 2 out of 3 strategies say SELL)
    """
    def __init__(self, portfolio: Portfolio, consensus_threshold: int = 2, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, consensus_threshold=consensus_threshold)
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
        # Check risk exits first
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

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
            trade_qty = self._get_trade_qty(symbol, candle.close)
            if trade_qty > 0:
                self.portfolio.execute_trade(symbol, True, trade_qty, candle.close, candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(symbol, candle.close)

        # Consensus SELL
        elif vote_score <= -self.consensus_threshold and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position, candle.close, candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)


# ─── Strategy Registry ─────────────────────────────────────────────
# Maps display names to (strategy_class, default_params) for the GUI
class RegimeSwitchStrategy(BaseStrategy):
    """
    Regime-aware meta-strategy: classifies the market as TRENDING or
    RANGING each candle, then trades with the style suited to that regime.

    Regime signal — Kaufman Efficiency Ratio over `regime_lookback` candles:
        ER = |close_now - close_then| / sum(|candle-to-candle moves|)
    ER near 1 = price moved in a straight line (trend); near 0 = price
    churned without going anywhere (range).

    TRENDING (ER >= er_threshold)  -> SMA crossover (ride the trend)
    RANGING  (ER <  er_threshold)  -> Bollinger Bands (fade the extremes)

    On a regime flip while holding a position, the position is closed
    (exit_reason='regime_change') so each leg starts from a clean slate.
    Motivated by the 2026-07 walk-forward study: mean reversion won in
    ranging conditions, trend-following needs trends — nothing won
    unconditionally.
    """
    def __init__(self, portfolio: Portfolio,
                 regime_lookback: int = 20, er_threshold: float = 0.30,
                 fast_period: int = 5, slow_period: int = 20,
                 bb_period: int = 14, num_std: float = 2.5,
                 risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager,
                         regime_lookback=regime_lookback, er_threshold=er_threshold,
                         fast_period=fast_period, slow_period=slow_period,
                         bb_period=bb_period, num_std=num_std)
        self.regime_lookback = regime_lookback
        self.er_threshold = er_threshold
        self.fast = fast_period
        self.slow = slow_period
        self.bb_period = bb_period
        self.num_std = num_std
        self.history: Dict[str, list] = {}
        self.regime: Dict[str, str] = {}  # symbol -> 'TREND' | 'RANGE' | 'FLAT'
        # How much price history to retain (subclasses may need more)
        self._history_keep = max(self.slow, self.bb_period, self.regime_lookback) + 2
        # Hysteresis: a NEW regime must persist this many consecutive candles
        # before we commit to it (1 = switch immediately, original behavior)
        self.min_dwell = 1
        self._pending_regime: Dict[str, tuple] = {}  # symbol -> (candidate, count)

    def on_start(self):
        print(f"Starting Regime Switch Strategy (ER lookback: {self.regime_lookback}, "
              f"threshold: {self.er_threshold} | trend: SMA {self.fast}/{self.slow} | "
              f"range: BB {self.bb_period}/{self.num_std}σ)")

    def _efficiency_ratio(self, prices: list) -> Optional[float]:
        window = prices[-(self.regime_lookback + 1):]
        if len(window) < self.regime_lookback + 1:
            return None
        net_move = abs(window[-1] - window[0])
        path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
        return (net_move / path) if path > 0 else 0.0

    def _classify_regime(self, symbol: str, prices: list) -> Optional[str]:
        """
        Classify the current market regime for a symbol.
        Returns 'TREND', 'RANGE', 'FLAT' (stand aside), or None (not enough data).
        Subclasses can override this with alternative regime models.
        """
        er = self._efficiency_ratio(prices)
        if er is None:
            return None
        return 'TREND' if er >= self.er_threshold else 'RANGE'

    def on_data(self, symbol: str, candle: Candle):
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return

        prices = self.history.setdefault(symbol, [])
        prices.append(candle.close)
        if len(prices) > self._history_keep:
            prices.pop(0)

        regime = self._classify_regime(symbol, prices)
        if regime is None:
            return

        # Hysteresis: only commit to a regime change after it has persisted
        # for min_dwell consecutive candles (suppresses classifier flicker)
        committed = self.regime.get(symbol)
        if committed is not None and regime != committed and self.min_dwell > 1:
            cand, count = self._pending_regime.get(symbol, (None, 0))
            count = count + 1 if cand == regime else 1
            self._pending_regime[symbol] = (regime, count)
            if count < self.min_dwell:
                regime = committed  # not confirmed yet — stay the course
        else:
            self._pending_regime.pop(symbol, None)

        # Regime flip while holding -> flatten, let the new leg start clean
        prev_regime = self.regime.get(symbol)
        current_position = self.portfolio.get_position_qty(symbol)
        if prev_regime is not None and regime != prev_regime and current_position > 0:
            self.portfolio.execute_trade(symbol, False, current_position,
                                         candle.close, candle.timestamp,
                                         exit_reason='regime_change')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)
            current_position = 0
        self.regime[symbol] = regime

        if regime == 'TREND':
            self._trend_leg(symbol, candle, prices, current_position)
        elif regime == 'RANGE':
            self._range_leg(symbol, candle, prices, current_position)
        # 'FLAT': deliberately stand aside

    def _trend_leg(self, symbol: str, candle: Candle, prices: list, position: float):
        if len(prices) <= self.slow:
            return
        fast_ma = sum(prices[-self.fast:]) / self.fast
        slow_ma = sum(prices[-self.slow:]) / self.slow
        prev_fast = sum(prices[-(self.fast + 1):-1]) / self.fast
        prev_slow = sum(prices[-(self.slow + 1):-1]) / self.slow

        if prev_fast <= prev_slow and fast_ma > slow_ma and position == 0:
            qty = self._get_trade_qty(symbol, candle.close)
            if qty > 0:
                self.portfolio.execute_trade(symbol, True, qty, candle.close, candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(symbol, candle.close)
        elif prev_fast >= prev_slow and fast_ma < slow_ma and position > 0:
            self.portfolio.execute_trade(symbol, False, position, candle.close,
                                         candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)

    def _range_leg(self, symbol: str, candle: Candle, prices: list, position: float):
        if len(prices) < self.bb_period:
            return
        window = prices[-self.bb_period:]
        middle = sum(window) / self.bb_period
        std = (sum((p - middle) ** 2 for p in window) / self.bb_period) ** 0.5
        upper = middle + self.num_std * std
        lower = middle - self.num_std * std

        if candle.close <= lower and position == 0:
            qty = self._get_trade_qty(symbol, candle.close)
            if qty > 0:
                self.portfolio.execute_trade(symbol, True, qty, candle.close, candle.timestamp)
                if self.risk_manager:
                    self.risk_manager.register_entry(symbol, candle.close)
        elif candle.close >= upper and position > 0:
            self.portfolio.execute_trade(symbol, False, position, candle.close,
                                         candle.timestamp, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)


class HMMRegimeSwitchStrategy(RegimeSwitchStrategy):
    """
    Regime switching driven by a Gaussian Hidden Markov Model instead of a
    hand-tuned Efficiency Ratio threshold.

    An HMM assumes the market moves between hidden states, each emitting
    log-returns with its own mean (drift) and variance (volatility). The
    model AND the state sequence are learned unsupervised from the data —
    no per-stock threshold tuning.

    State -> trading style mapping (from each state's fitted statistics):
        |drift| / volatility >= trend_score  ->  TREND  (SMA crossover leg)
        otherwise                            ->  RANGE  (Bollinger leg)
        highest-volatility state (3+ states) ->  FLAT   (stand aside)

    The model is refit every `refit_every` candles on a trailing window of
    `fit_window` closes, using only past data (no lookahead). Until enough
    history exists, falls back to the Efficiency Ratio rule.
    """
    def __init__(self, portfolio: Portfolio,
                 n_states: int = 2, trend_score: float = 0.06,
                 refit_every: int = 60, fit_window: int = 300,
                 min_dwell: int = 1, vol_feature: int = 0,
                 fast_period: int = 5, slow_period: int = 20,
                 bb_period: int = 14, num_std: float = 2.5,
                 risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, fast_period=fast_period, slow_period=slow_period,
                         bb_period=bb_period, num_std=num_std, risk_manager=risk_manager)
        self.params.update(n_states=n_states, trend_score=trend_score,
                           refit_every=refit_every, fit_window=fit_window,
                           min_dwell=min_dwell, vol_feature=vol_feature)
        self.n_states = n_states
        self.trend_score = trend_score
        self.refit_every = refit_every
        self.fit_window = fit_window
        self.min_dwell = int(min_dwell)          # hysteresis (base class applies it)
        self.vol_feature = bool(vol_feature)     # 2-D emissions: (return, |return|)
        self._history_keep = max(self._history_keep, fit_window + 2)
        self._models: Dict[str, Any] = {}          # symbol -> fitted GaussianHMM
        self._state_regime: Dict[str, dict] = {}   # symbol -> {state: 'TREND'|'RANGE'|'FLAT'}
        self._since_fit: Dict[str, int] = {}

    def on_start(self):
        print(f"Starting HMM Regime Switch (states: {self.n_states}, "
              f"trend score: {self.trend_score}, refit every {self.refit_every} | "
              f"trend: SMA {self.fast}/{self.slow} | range: BB {self.bb_period}/{self.num_std}σ)")

    def _fit_model(self, symbol: str, prices: list):
        import numpy as np
        import warnings
        import logging
        from hmmlearn.hmm import GaussianHMM
        logging.getLogger('hmmlearn').setLevel(logging.ERROR)  # EM oscillation noise

        feats = self._features(prices[-self.fit_window:])
        if feats is None or len(feats) < self.n_states * 10:
            return

        model = GaussianHMM(n_components=self.n_states, covariance_type='diag',
                            n_iter=100, random_state=42)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                model.fit(feats)
            except Exception:
                return

        # Map each hidden state to a trading style from its fitted stats on
        # the RETURN dimension (dimension 0 regardless of feature count)
        mus = model.means_[:, 0]
        sigmas = np.sqrt(model.covars_.reshape(self.n_states, -1)[:, 0])
        regime_map = {}
        for s in range(self.n_states):
            score = abs(mus[s]) / sigmas[s] if sigmas[s] > 0 else 0.0
            regime_map[s] = 'TREND' if score >= self.trend_score else 'RANGE'
        if self.n_states >= 3:
            regime_map[int(sigmas.argmax())] = 'FLAT'  # crisis state: stand aside

        self._models[symbol] = model
        self._state_regime[symbol] = regime_map

    def _classify_regime(self, symbol: str, prices: list) -> Optional[str]:
        # Not enough history for a stable fit yet -> Efficiency Ratio fallback
        if len(prices) < max(self.fit_window // 2, 60):
            return super()._classify_regime(symbol, prices)

        count = self._since_fit.get(symbol, self.refit_every)  # fit on first call
        if count >= self.refit_every or symbol not in self._models:
            self._fit_model(symbol, prices)
            self._since_fit[symbol] = 0
        else:
            self._since_fit[symbol] = count + 1

        model = self._models.get(symbol)
        if model is None:
            return super()._classify_regime(symbol, prices)

        import warnings
        feats = self._features(prices[-min(len(prices), self.fit_window):])
        if feats is None:
            return super()._classify_regime(symbol, prices)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                current_state = int(model.predict(feats)[-1])
            except Exception:
                return super()._classify_regime(symbol, prices)
        return self._state_regime[symbol].get(current_state, 'RANGE')

    def _features(self, closes_list):
        """Emission features: log-returns, optionally paired with |return|
        (a per-candle volatility proxy) so states can separate calm from
        turbulent periods, not just up-drift from down-drift."""
        import numpy as np
        closes = np.asarray(closes_list, dtype=float)
        if len(closes) < 3:
            return None
        returns = np.diff(np.log(closes))
        if self.vol_feature:
            return np.column_stack([returns, np.abs(returns)])
        return returns.reshape(-1, 1)


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
    "Regime Switch": {
        "class": RegimeSwitchStrategy,
        "params": {
            "regime_lookback": {"label": "Regime Lookback (candles)", "min": 10, "max": 50, "default": 20, "step": 5},
            "er_threshold":    {"label": "Trend Threshold (Efficiency Ratio)", "min": 0.15, "max": 0.60, "default": 0.30, "step": 0.05},
        }
    },
    "HMM Regime Switch": {
        "class": HMMRegimeSwitchStrategy,
        "params": {
            "n_states":    {"label": "Hidden States", "min": 2, "max": 3, "default": 2, "step": 1},
            "trend_score": {"label": "Trend Score Threshold (|drift|/vol)", "min": 0.02, "max": 0.10, "default": 0.06, "step": 0.04},
            "min_dwell":   {"label": "Regime Dwell (candles to confirm)", "min": 1, "max": 3, "default": 1, "step": 2},
            "vol_feature": {"label": "Volatility Feature (0=off, 1=on)", "min": 0, "max": 1, "default": 0, "step": 1},
        }
    },
}

