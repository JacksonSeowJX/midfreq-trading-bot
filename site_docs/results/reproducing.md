# Reproducing the numbers

Every reported figure comes from a study script in
[`research/studies/`](repo:research/README.md), which writes a timestamped CSV or JSON file to
`results/`. The files the report cites are committed, so the dashboard and
`midfreq.research.results_index` read them from a fresh clone. Recomputing them needs the price
cache ([Installation](../getting_started/installation.md#the-price-cache)).

Each study anchors its window to the data, through `DataStorage.latest_common_timestamp`, not to
the day it is run, and the commands below pass the end date the report used, so they reproduce
its figures for as long as the cache holds those candles:

```bash
# Cost sensitivity of the S&P 100 result (report Section 4.9)
uv run python research/studies/cross_sectional_cost_sensitivity.py --end-date 2026-08-21

# Probability of Backtest Overfitting, 0.274 (report Section 4.11)
uv run python research/studies/pbo_sp100_reversal.py --end-date 2026-08-21

# White's Reality Check, p = 0.094 and 0.098 (report Section 4.11)
uv run python research/studies/reality_check_study.py --end-date "2026-08-28 16:00" --n-boot 20000
```

A walk-forward over the S&P 100 takes about half an hour, so the Reality Check study, which
runs eight candidates under two configurations, takes hours. It caches each candidate's
per-window returns in `results/reality_check_cache.json` (not committed), keyed by its inputs
and a fingerprint of the code that produces them, so a change to the strategy or the backtester
forces a recompute.

## The headline counts

The 217 results and the 13 that passed are the newest file of each current study, stacked by
`results_index` (superseded studies, run before the testing code was audited, are left out):

```python
from midfreq.research import results_index

table = results_index.load_all()
print(results_index.summary(table))
passed = table[table.robust]
print(passed[["family", "strategy", "subject", "config_a_oos", "config_a_consistency",
              "config_b_oos", "config_b_consistency"]].round(2).to_string(index=False))
by_family = table.groupby("family").robust.agg(["sum", "count"])
print(by_family.to_string())
```

```text
{'total': 217, 'passed': 13, 'studies': 9, 'families': 9, 'pass_rate': 5.990783410138248}
                      family                     strategy                   subject  config_a_oos  config_a_consistency  config_b_oos  config_b_consistency
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                   US.MSFT          1.84                  55.6          2.84                  66.7
        Buy & Hold benchmark         Buy & Hold benchmark                  HK.00939          1.82                  66.7          2.62                  73.3
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                    US.XOM          2.21                  55.6          1.55                  66.7
        Buy & Hold benchmark         Buy & Hold benchmark                  HK.00005          1.12                  66.7          1.41                  60.0
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                  US.GOOGL          2.91                  66.7          0.92                  73.3
        Buy & Hold benchmark         Buy & Hold benchmark                  HK.00883          0.78                  55.6          3.87                  80.0
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                    US.JPM          1.84                  66.7          0.66                  53.3
Cross-Sectional (cost sweep) Cross-Sectional (cost sweep)            0% (best case)          5.88                  55.6          0.65                  66.7
             Cross-Sectional     Cross-Sectional Reversal              US (S&P 100)          5.88                  55.6          0.65                  66.7
Cross-Sectional (cost sweep) Cross-Sectional (cost sweep) 0.005% (moomoo SG actual)          5.73                  55.6          0.63                  66.7
   Buy & Hold + Risk Overlay    Buy & Hold + Risk Overlay                    US.JPM          0.51                  66.7          0.49                  53.3
     ML Direction Classifier      ML Direction Classifier                  US.GOOGL          0.22                  55.6          0.05                  53.3
     Single-Stock Strategies      ML Direction Classifier                  HK.00005          0.06                  66.7          0.01                  53.3
                                       sum  count
family                                           
Buy & Hold + Risk Overlay                1     34
Buy & Hold benchmark                     3     19
Buy & Hold benchmark (US)                4     15
Cross-Sectional                          1      4
Cross-Sectional (cost sweep)             2      4
ML Direction Classifier                  1     15
Single-Stock Strategies                  1     57
Single-Stock Strategies (US)             0     45
Single-Stock Strategies (new sectors)    0     24
```

This needs no price cache: it reads the committed `results/` only.

## In the tutorials

Several figures can be reproduced in a few lines on this site:

| Figure | Where |
|---|---|
| The forward test's settings over the last year: +56.83%, 449 trades | [Backtest a strategy](../tutorials/backtest_a_strategy.md#6-a-cross-sectional-strategy) |
| Reversal on the US 15 under both configurations: +1.215% and +3.837% per window | [Walk-forward a strategy](../tutorials/walk_forward.md#4-apply-the-two-configuration-standard) |
| The Reality Check's p-value for that candidate alone, 0.015 | [Walk-forward a strategy](../tutorials/walk_forward.md#5-correct-for-the-search) |
| The dashboard guide's Examples C, D and E | the tutorials above, and [The dashboard](../concepts/dashboard.md#reproducing-the-main-findings) |
