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

# Parameter stability of the forward test's setting (fixed windows; about a minute on the C++ engine)
uv run python research/studies/param_stability_sp100_reversal.py
```

The parameter-stability study's windows are fixed in the script, and it first reproduces the
search that chose the forward test's setting, stopping unless it scores exactly as recorded; its
walk-forward step reads two saved walk-forward runs from the run store (`runs/`). Its committed
output is `results/param_stability_sp100_reversal_20261017_015938.json`, with a CSV per surface.

A walk-forward over the S&P 100 takes about half an hour, so the Reality Check study, which
runs eight candidates under two configurations, takes hours. It caches each candidate's
per-window returns in `results/reality_check_cache.json` (not committed), keyed by its inputs
and a fingerprint of the code that produces them, so a change to the strategy or the backtester
forces a recompute.

## The headline counts

The 178 results and the 27 that passed are the newest file of each current study, stacked by
`results_index` (superseded studies, run before the testing code was audited and since re-run on
the corrected code, are left out):

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
{'total': 178, 'passed': 27, 'studies': 7, 'families': 7, 'pass_rate': 15.168539325842698}
                      family                     strategy                   subject  config_a_oos  config_a_consistency  config_b_oos  config_b_consistency
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                   US.AMZN          5.12                  55.6          4.45                  80.0
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                    US.JPM          3.45                  77.8          3.65                  73.3
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                  US.GOOGL          7.79                  55.6          3.25                  60.0
   Buy & Hold benchmark (HK)    Buy & Hold benchmark (HK)                  HK.00005          5.91                  88.9          2.41                  73.3
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                    US.WMT          4.18                  55.6          2.38                  66.7
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                   US.NVDA          2.85                  77.8          1.99                  66.7
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                      US.V          3.41                  77.8          1.48                  66.7
   Buy & Hold benchmark (HK)    Buy & Hold benchmark (HK)                  HK.00883          1.41                  66.7          3.04                  73.3
   Buy & Hold benchmark (HK)    Buy & Hold benchmark (HK)                  HK.01398          4.03                  55.6          1.36                  53.3
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                   US.AAPL          2.66                  55.6          1.27                  53.3
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                   US.META          2.09                  66.7          1.25                  60.0
   Buy & Hold benchmark (HK)    Buy & Hold benchmark (HK)                  HK.00939          3.14                  66.7          1.02                  53.3
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                   US.MSFT          5.46                  55.6          0.83                  60.0
Cross-Sectional (cost sweep) Cross-Sectional (cost sweep)            0% (best case)          5.88                  55.6          0.65                  66.7
             Cross-Sectional     Cross-Sectional Reversal              US (S&P 100)          5.88                  55.6          0.65                  66.7
Cross-Sectional (cost sweep) Cross-Sectional (cost sweep) 0.005% (moomoo SG actual)          5.73                  55.6          0.63                  66.7
Single-Stock Strategies (US)      ML Direction Classifier                   US.META          1.48                  55.6          0.58                  66.7
   Buy & Hold benchmark (US)    Buy & Hold benchmark (US)                    US.JNJ          0.44                  55.6          1.67                  66.7
Single-Stock Strategies (US)            HMM Regime Switch                   US.AAPL          0.42                  55.6          0.24                  53.3
   Buy & Hold benchmark (HK)    Buy & Hold benchmark (HK)                  HK.00941          2.62                  77.8          0.24                  53.3
   Buy & Hold + Risk Overlay    Buy & Hold + Risk Overlay                    US.JNJ          0.22                  66.7          0.25                  53.3
Single-Stock Strategies (US)      ML Direction Classifier                    US.JPM          0.18                  55.6          0.28                  73.3
Single-Stock Strategies (US)                Regime Switch                  US.GOOGL          0.34                  55.6          0.15                  53.3
Single-Stock Strategies (US)      ML Direction Classifier                     US.PG          0.15                  66.7          0.12                  66.7
   Buy & Hold benchmark (HK)    Buy & Hold benchmark (HK)                  HK.02020          1.49                  66.7          0.10                  53.3
Single-Stock Strategies (US)      ML Direction Classifier                  US.GOOGL          0.62                  55.6          0.04                  60.0
     Single-Stock Strategies      ML Direction Classifier                  HK.00005          0.06                  66.7          0.01                  53.3
                              sum  count
family                                  
Buy & Hold + Risk Overlay       1     34
Buy & Hold benchmark (HK)       6     19
Buy & Hold benchmark (US)      10     15
Cross-Sectional                 1      4
Cross-Sectional (cost sweep)    2      4
Single-Stock Strategies         1     57
Single-Stock Strategies (US)    6     45
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
