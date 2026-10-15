# Quickstart

This page runs one backtest in Python and in the dashboard, then validates the same strategy
with a walk-forward. It needs the [installation](installation.md) and the price cache's hourly
candles for `US.AAPL`. The figures below are for the year from 25 September 2025 to
25 September 2026, the last full year of the cache when they were taken; other dates give
other figures.

## A backtest

A moving-average crossover on Apple's hourly candles, at the US broker's commission of 0.005%
per side and 5 basis points of slippage on every fill:

=== "Python"

    ```python
    from datetime import datetime

    from midfreq.backtest.backtester import Backtester
    from midfreq.core.candle import Timeframe
    from midfreq.core.portfolio import Portfolio
    from midfreq.data.storage import DataStorage
    from midfreq.strategies.candle_strategies import MovingAverageCrossover

    portfolio = Portfolio(initial_cash=100_000, commission_rate=0.00005)  # US: 0.005% per side
    backtester = Backtester(DataStorage(), portfolio, slippage_bps=5)
    metrics = backtester.run(MovingAverageCrossover, ["US.AAPL"], Timeframe.HOUR_1,
                             start_date=datetime(2025, 9, 25), end_date=datetime(2026, 9, 25),
                             fast_period=5, slow_period=20)

    print(f"Return {metrics['return_pct']:+.2f}%, Sharpe {metrics['sharpe_ratio']:.2f}, "
          f"max drawdown {metrics['max_drawdown']:.2f}%, {metrics['total_trades']} trades")
    print(f"Buy and hold {metrics['benchmark_return_pct']:+.2f}%, alpha {metrics['alpha']:+.2f}%")
    ```

    The backtester reports its progress as it runs, then the two lines the script prints:

    ```text
    === Initializing Backtest Simulator ===
    Cash: $100,000.00 | Timeframe: 1h | Symbols: ['US.AAPL']
    Slippage: 5.0 bps
      [+] Loaded 1758 candles for US.AAPL
    Starting MA Crossover Strategy (Fast: 5, Slow: 20)

    === Simulating Trades (1758 events, chronological) ===

    === Simulation Complete ===
    Return +0.09%, Sharpe 0.04, max drawdown 4.09%, 109 trades
    Buy and hold +34.78%, alpha -34.69%
    ```

=== "Dashboard"

    Start the dashboard ([Installation](installation.md#the-dashboard)) and open **Backtest**.

    1. In the top bar, set the market to **US**.
    2. **Strategy**: *SMA Crossover*. Its parameters default to *Fast MA Period* 5 and
       *Slow MA Period* 20.
    3. **Data**: *Symbol* `US.AAPL`, *Timeframe* 1h, *Start* 2025-09-25, *End* 2026-09-25.
    4. **Money**: *Starting capital* 100,000; *Commission per side (%)* 0.005, the US default;
       *Slippage (bps)* 5. Leave **Risk & sizing** at its defaults (100 shares a trade, no
       stops, a drawdown halt at 10%).
    5. Press **Run backtest**.

    The result shows the same figures as the Python script: *Return* +0.09%, *Sharpe ratio*
    0.04, *Max drawdown* 4.09%, *Trades* 109, and *Buy and hold* +34.78%. The dashboard runs
    the same `Backtester` on the same cache through its API; the default sizing of 100 shares
    is also what a strategy without a risk manager trades.

The strategy made almost nothing over a year in which simply holding the stock returned 34.78%.
`metrics` holds more than the script prints: `final_equity`, `win_rate`, `profit_factor`,
`open_positions` and the inputs of the run. The portfolio keeps every trade in
`portfolio.trade_history` and the equity at every candle in `portfolio.equity_curve_detailed`;
[Backtest a strategy](../tutorials/backtest_a_strategy.md) goes through them.

## A walk-forward

A single backtest scores parameters that were chosen, by someone, on the same data. A
walk-forward cuts the period into consecutive windows, picks the parameters by grid search on
the first part of each window (training) and scores them on the rest (test), which the search
has not seen:

```python
from datetime import datetime

from midfreq.backtest.optimizer import walk_forward
from midfreq.core.candle import Timeframe
from midfreq.data.storage import DataStorage

result = walk_forward("SMA Crossover", ["US.AAPL"], Timeframe.HOUR_1,
                      datetime(2025, 9, 25), datetime(2026, 9, 25), DataStorage(),
                      n_splits=3, train_pct=0.6, objective="sharpe_ratio",
                      commission_rate=0.00005, slippage_bps=5)

for w in result["windows"]:
    print(f"window {w.window_id}: {w.best_params}  "
          f"train {w.train_metrics['return_pct']:+6.2f}%  test {w.test_metrics['return_pct']:+6.2f}%")
s = result["summary"]
print(f"mean test return {s['avg_oos_return']:+.2f}% per window, "
      f"{s['positive_windows']} of {s['total_windows']} windows positive")
```

```text
window 1: {'fast_period': 6, 'slow_period': 85}  train  +1.66%  test  -0.12%
window 2: {'fast_period': 4, 'slow_period': 141}  train  +0.13%  test  +0.00%
window 3: {'fast_period': 8, 'slow_period': 29}  train  +1.85%  test  +2.48%
mean test return +0.79% per window, 1 of 3 windows positive
```

The search picks different parameters in every window, and only one of the three test spans
made money. The second made no trade at all: its 141-candle slow average is ready only after
142 of the span's 245 candles, and no crossover followed. The parameter grid comes from the strategy's entry in `STRATEGY_REGISTRY`
(capped at 200 combinations), so the dashboard's **Optimize** page, on a local install, runs
the same search. The project held every result to a stricter version of this test, under two
window configurations; see [Optimization and validation](../concepts/optimization_and_validation.md).

## Next steps

- [Backtest a strategy](../tutorials/backtest_a_strategy.md): trades, equity, costs, risk
  settings and a cross-sectional strategy over the S&P 100.
- [Walk-forward a strategy](../tutorials/walk_forward.md): reading in-sample against
  out-of-sample, the two-configuration standard and the correction for the search.
- [Write an event-driven strategy](../tutorials/event_driven_strategy.md): the same kind of
  rule as orders on the venue engine.
- [Concepts](../concepts/index.md): how the pieces fit together.
