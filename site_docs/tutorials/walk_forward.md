# Walk-forward a strategy

This tutorial validates a strategy the way the project did. It runs a walk-forward, reads the
in-sample results against the out-of-sample ones, applies the project's two-configuration
standard, and corrects for the number of candidates tried with White's Reality Check. The
figures reproduce [Example E](../concepts/dashboard.md#example-e-a-walk-forward-validation) of
the dashboard guide and two of the eight candidates of the project's Reality Check study.

**Needs:** the [installation](../getting_started/installation.md) and the price cache's hourly
candles for the 15 US large caps of `config/symbols.json`. Steps 4 and 5 backtest 96 parameter
combinations in each of 24 windows per strategy, and took about four minutes each. The code blocks
form one script; run them in order.

The method is described in [Optimization and validation](../concepts/optimization_and_validation.md).

## 1. Run a walk-forward

Cross-sectional reversal on the US 15, over the year to 25 September 2026, in three windows
with 60% of each for training, at the US commission and without slippage:

```python
from datetime import datetime

from midfreq.backtest.optimizer import walk_forward
from midfreq.core.candle import Timeframe
from midfreq.core.config import ConfigLoader
from midfreq.data.storage import DataStorage

storage = DataStorage()
us15 = ConfigLoader().get_universe("us_15", with_data_only=True)
print(us15)

result = walk_forward("Cross-Sectional Reversal", us15, Timeframe.HOUR_1,
                      datetime(2025, 9, 25), datetime(2026, 9, 25), storage,
                      n_splits=3, train_pct=0.6, objective="sharpe_ratio",
                      commission_rate=0.00005)
```

```text
['US.AAPL', 'US.MSFT', 'US.NVDA', 'US.TSLA', 'US.GOOGL', 'US.AMZN', 'US.META', 'US.JPM', 'US.V', 'US.XOM', 'US.JNJ', 'US.PFE', 'US.PG', 'US.WMT', 'US.CAT']
```

In each window, `walk_forward` backtests all 96 combinations of the strategy's grid
(`lookback` 5 to 30, `top_n` 1 to 4, `rebalance_every` 1 to 19) on the training span, keeps the
one with the best Sharpe ratio, and backtests it once on the test span. It took about 30 seconds.

## 2. Read in-sample against out-of-sample

```python
s = result["summary"]
print(f"mean test return {s['avg_oos_return']:+.2f}%, mean train return {s['avg_train_return']:+.2f}%, "
      f"windows positive {s['consistency_pct']:.0f}%, WF efficiency {s['wf_efficiency']:.0%}")
```

```text
mean test return +3.45%, mean train return +17.66%, windows positive 100%, WF efficiency 29%
```

The windows, with the parameters chosen in each:

```python
import pandas as pd

rows = [{"window": w.window_id,
         "train": f"{w.train_start:%Y-%m-%d} to {w.train_end:%Y-%m-%d}",
         "test": f"{w.test_start:%Y-%m-%d} to {w.test_end:%Y-%m-%d}",
         **w.best_params,
         "train %": round(w.train_metrics["return_pct"], 2),
         "test %": round(w.test_metrics["return_pct"], 2),
         "train Sharpe": round(w.train_metrics["sharpe_ratio"], 2),
         "test Sharpe": round(w.test_metrics["sharpe_ratio"], 2)}
        for w in result["windows"]]
print(pd.DataFrame(rows).to_string(index=False))
```

```text
 window                    train                     test  lookback  top_n  rebalance_every  train %  test %  train Sharpe  test Sharpe
      1 2025-09-25 to 2025-12-06 2025-12-06 to 2026-01-24        10      2                7    24.13    0.49          5.03         0.25
      2 2026-01-24 to 2026-04-06 2026-04-06 to 2026-05-25        25      2                1    13.15    7.79          2.28         2.28
      3 2026-05-25 to 2026-08-05 2026-08-05 to 2026-09-23        10      2                7    15.71    2.06          2.97         0.94
```

Every test span made money, but far less than its training span, and the search chose
different parameters in the second window. The training figures are what a grid search over
each span would report; only the test figures are evidence, because only there were the
parameters fixed before the data was seen.

## 3. Walk-forward efficiency

The test spans are shorter than the training spans, so the two are compared per day:

```python
window_days = (datetime(2026, 9, 25) - datetime(2025, 9, 25)).days // 3
train_days = int(window_days * 0.6)
test_days = window_days - train_days
print(f"{window_days}-day windows: {train_days} days of training, {test_days} of test")
print(f"{s['avg_train_return'] / train_days:.3f}% a day in training, "
      f"{s['avg_oos_return'] / test_days:.3f}% a day in test")
```

```text
121-day windows: 72 days of training, 49 of test
0.245% a day in training, 0.070% a day in test
```

0.070 over 0.245 is the walk-forward efficiency of 29%: out of sample, the chosen parameters
earned less than a third of what they earned on the data they were chosen on. Below about 50%
is a warning sign of overfitting.

## 4. Apply the two-configuration standard

A walk-forward over one year in three windows is a small test, and its result depends on where
the windows fall. The project's standard runs the walk-forward over three years twice, cut into
9 windows (Config A) and into 15 (Config B), with 70% for training, and passes a result only if
under both the mean test return is positive and at least half the test windows are profitable.
Over the same three years as the project's studies (to 28 August 2026), with 5 basis points of
slippage:

```python
from datetime import timedelta

END = datetime(2026, 8, 28, 16)            # the studies' common end
START = END - timedelta(days=3 * 365)


def two_configs(strategy):
    """The project's standard: the same walk-forward cut into 9 and into 15 windows."""
    return {name: walk_forward(strategy, us15, Timeframe.HOUR_1, START, END, storage,
                               n_splits=n_splits, train_pct=0.7, objective="sharpe_ratio",
                               commission_rate=0.00005, slippage_bps=5)["summary"]
            for name, n_splits in (("A", 9), ("B", 15))}


def passes(summary):
    return summary["avg_oos_return"] > 0 and summary["consistency_pct"] >= 50


reversal = two_configs("Cross-Sectional Reversal")
for name, s in reversal.items():
    print(f"Config {name}: {s['total_windows']} windows, mean test return {s['avg_oos_return']:+.3f}%, "
          f"{s['consistency_pct']:.1f}% of windows positive")
print("passes the standard:", all(passes(s) for s in reversal.values()))
```

```text
Config A: 9 windows, mean test return +1.215%, 77.8% of windows positive
Config B: 15 windows, mean test return +3.837%, 66.7% of windows positive
passes the standard: True
```

These are the figures the project's Reality Check study recorded for this candidate
(`results/reality_check_20260929_050842.json`). The candidate passes the standard. That is
evidence about this one candidate, which was not the only one tried.

## 5. Correct for the search

The same standard applied to cross-sectional momentum on the same stocks gives a second
candidate on the same windows. White's Reality Check then asks whether the better of the two is
better than the best of two candidates with no edge would be:

```python
from midfreq.research.reality_check import naive_single_test, reality_check

momentum = two_configs("Cross-Sectional Momentum")
for name in ("A", "B"):
    candidates = {"Reversal, US 15": reversal[name]["oos_returns"],
                  "Momentum, US 15": momentum[name]["oos_returns"]}
    rc = reality_check(candidates, n_boot=20000)
    best = rc["best_candidate"]
    alone = naive_single_test(candidates[best], n_boot=20000)
    print(f"Config {name}: best {best} at {rc['observed_max_mean']:+.2f}% per window; "
          f"p = {alone:.3f} tested alone, {rc['p_value']:.3f} over both candidates")
```

```text
Config A: best Momentum, US 15 at +4.16% per window; p = 0.001 tested alone, 0.002 over both candidates
Config B: best Reversal, US 15 at +3.84% per window; p = 0.015 tested alone, 0.016 over both candidates
```

Over two candidates, the winner of each configuration looks real. But the correction is only as
honest as the list of candidates it is given, and the project did not try two: it tried
reversal and momentum on four universes over these same windows. Over all eight, the winners
are Reversal on the S&P 100 under Config A (+6.15% per window) and Reversal on the US 15 under
Config B (+3.84%, the candidate above, whose p-value alone is the 0.015 printed here), and the
Reality Check gives p = 0.094 and 0.098. Neither is below 5%: the best result cannot be told
apart from the luck of picking the best of eight. See [Results](../results/index.md) and
`research/studies/reality_check_study.py`, which runs all eight.

## Summary

| Step | What it showed |
|---|---|
| Walk-forward, 1 year, 3 windows | test returns positive in every window, but a walk-forward efficiency of 29% |
| Two-configuration standard, 3 years | the candidate passes under both cuts |
| Reality Check over the 8 candidates tried | p = 0.094 and 0.098: not distinguishable from the luck of the search |

On the S&P 100, the same strategy with the settings a grid search picked returns +56.83% in a
single backtest over the last year ([Backtest a strategy](backtest_a_strategy.md#6-a-cross-sectional-strategy)).
Each step of the validation removes part of what such a figure appears to show.
