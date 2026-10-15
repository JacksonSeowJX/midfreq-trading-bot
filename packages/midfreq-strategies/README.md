# midfreq-strategies

The trading rules, separate from how they are run. Depends only on `midfreq-core` (plus numpy and
pandas; the HMM strategies need the `hmm` extra: hmmlearn, scikit-learn).

## Contents

- `midfreq.strategies.candle_strategies`: the candle-based library. `BaseStrategy` and its
  subclasses (moving-average crossover, RSI, Bollinger, z-score, pairs, HMM regime, cross-sectional
  momentum and reversal, ...) trade by calling `self.portfolio.execute_trade(...)` on each candle.
  `STRATEGY_REGISTRY` maps display names to class and parameter ranges; the dashboard, the
  optimiser and the live bot all read it.
- `midfreq.strategies.trading`: the event-driven Strategy API (`Strategy`, `StrategyConfig`). A
  strategy subscribes to bars, receives `on_bar` / `on_slice` / `on_signal` callbacks and submits
  orders; the venue decides fills, lots, fees and cash.
- `midfreq.strategies.event_driven`: strategies written natively against that API:
  `MACrossover`, and the cross-sectional pair `CrossSectionalRanker` (publishes a ranking Signal)
  and `EqualWeightBasket` (turns it into orders).

Nothing here runs a backtest; that is `midfreq-backtest`.

## Usage

```python
from midfreq.core.portfolio import Portfolio
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

entry = STRATEGY_REGISTRY["Cross-Sectional Reversal"]
strategy = entry["class"](Portfolio(initial_cash=100_000.0), lookback=10, top_n=2, rebalance_every=6)
```

```python
from midfreq.strategies.event_driven import MACrossover, MACrossoverConfig

strategy = MACrossover(MACrossoverConfig(
    bar_types=["00700.XHKG-1-HOUR-LAST-EXTERNAL"], fast_period=5, slow_period=20))
# then: BacktestEngine.add_strategy(strategy)  (see midfreq-backtest)
```

Tests: `uv run pytest -q packages/midfreq-strategies`.
