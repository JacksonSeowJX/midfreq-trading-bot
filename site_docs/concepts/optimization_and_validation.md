# Optimization and validation

A backtest scores a set of parameters on the data it is run on. If those parameters were chosen
by looking at the same data, by a person or by a search, the score overstates what to expect;
and the more combinations, strategies and universes are tried, the better the best of them
looks by luck alone. The project therefore judges a strategy only by returns on data its
parameters were not chosen on, under two different splits of the data, and then corrects for
the number of candidates it tried.

| Tool | Module | Question it answers |
|---|---|---|
| Grid search | `midfreq.backtest.optimizer.grid_search` | which parameters scored best on this period? |
| Walk-forward | `midfreq.backtest.optimizer.walk_forward` | do the parameters a search picks hold up on the span that follows? |
| Two-configuration standard | the studies in `research/studies/` | does that hold whichever way the period is cut into windows? |
| Probability of Backtest Overfitting | `midfreq.research.overfitting` | within one strategy and universe, is the configuration the search picked better than the others, or the luckiest of them? |
| White's Reality Check | `midfreq.research.reality_check` | across every strategy and universe tried, is the winner better than what trying that many produces? |

## Grid search

`grid_search` backtests every combination of a strategy's parameter grid over one period and
returns them as a DataFrame, best first. The grid is the Cartesian product of the minimum,
maximum and step of each parameter in `STRATEGY_REGISTRY` (`generate_param_grid`); a grid larger
than `max_combinations` (500) is sampled at even intervals. Combinations that made no trade rank
below every combination that did, since a strategy that never enters the market would otherwise
score a neutral objective and outrank active ones.

| Objective | Ranks by |
|---|---|
| `sharpe_ratio` (default) | the annualized Sharpe ratio |
| `return_pct` | the total return |
| `profit_factor` | gross profit over gross loss |
| `max_drawdown` | the maximum drawdown, lowest first |

The commission defaults to the Hong Kong rate, `Portfolio.HK_FEE_RATE`; pass the market's own
rate (`commission_rate=0.00005` for US stocks). A grid search scores the best combination on
the data it was picked from, so its top row is an upper bound, not an estimate.

## Walk-forward

`walk_forward` cuts the period into `n_splits` consecutive windows of equal length. In each, a
grid search (of at most 200 combinations) picks the parameters on the first `train_pct` of the
window, the training span, and they are scored on the rest, the test span, which the search has
not seen. The quickstart's walk-forward of a moving-average crossover on Apple, three windows
with 60% training:

```mermaid
%%{init: {"themeVariables": {"sectionBkgColor": "transparent", "altSectionBkgColor": "transparent",
  "sectionBkgColor2": "transparent", "taskBkgColor": "#8b949e", "taskBorderColor": "#8b949e",
  "taskTextColor": "#0f1216", "taskTextLightColor": "#0f1216", "taskTextOutsideColor": "#8b949e",
  "activeTaskBkgColor": "#4c8df6", "activeTaskBorderColor": "#4c8df6", "gridColor": "#8b949e",
  "titleColor": "#8b949e", "textColor": "#8b949e", "todayLineColor": "transparent"}}}%%
gantt
    dateFormat YYYY-MM-DD
    axisFormat %b %Y
    section Window 1
    train : 2025-09-25, 2025-12-06
    test  : active, 2025-12-06, 2026-01-24
    section Window 2
    train : 2026-01-24, 2026-04-06
    test  : active, 2026-04-06, 2026-05-25
    section Window 3
    train : 2026-05-25, 2026-08-05
    test  : active, 2026-08-05, 2026-09-23
```

The training span ends where the test span begins, and the training backtest excludes that
day, so no candle is in both. A window whose test span lies mostly beyond the last cached candle
is skipped, and listed in `skipped_windows`, rather than scored as a flat result.

The result holds each window (dates, the chosen parameters, the training and the test metrics)
and a summary:

| Summary | Meaning |
|---|---|
| `avg_oos_return` | the mean test (out-of-sample) return per window: the figure the project judges by |
| `consistency_pct`, `positive_windows` | the share and number of test windows with a positive return |
| `avg_train_return` | the mean training (in-sample) return, for contrast |
| `wf_efficiency` | the test return per day as a share of the training return per day (Pardo, 2008); below about 50% is a warning, and it is undefined when training lost money |

The test spans are shorter than the training spans, which is why the efficiency compares
returns per day rather than per window.

## The two-configuration standard

Every result in the project was held to one standard: a walk-forward over 3 years, run twice
with different cuts.

| | Windows | Training share | Test spans |
|---|---|---|---|
| Config A | 9 | 70% | about 37 days each |
| Config B | 15 | 70% | about 22 days each |

A result **passes** only if, under both Config A and Config B, its mean out-of-sample return per
test window is positive and at least 50% of its test windows are profitable. Requiring both
guards against a result that depends on where the windows happen to fall. The studies write
these four figures for every result (`config_a_oos`, `config_a_consistency`, `config_b_oos`,
`config_b_consistency`) with the verdict (`robust`), and `midfreq.research.results_index`
stacks them into one table: 217 results, of which 13 pass ([Results](../results/index.md)).

## White's Reality Check

Passing the standard is evidence about one candidate. But the candidate that passed was picked
from many, and the best of many edgeless candidates passes some of the time. White's Reality
Check (White, 2000) tests the selection itself:

1. take each candidate's out-of-sample returns per window;
2. centre each candidate on its own mean, so that no candidate has an edge;
3. resample the windows with the stationary bootstrap (Politis and Romano, 1994), which keeps
   neighbouring windows together, and record the largest mean across the candidates;
4. the p-value is the share of resamples whose largest mean is at least the winner's real mean.

`reality_check(candidates, n_boot=5000, mean_block=2.0, seed=12345)` takes
`{name: [return per window]}`; every candidate must have the same windows, because a resample
draws the same windows for all of them. `naive_single_test` applies the same bootstrap to the
winner alone, ignoring that others were tried: the p-value a report would quote if it mentioned
only the winner.

The project ran it over the 8 cross-sectional candidates, reversal and momentum on each of four
universes, with 20,000 resamples: p = 0.094 under Config A and 0.098 under Config B, against
0.058 and 0.015 for the winner tested alone. Both corrected values are above 0.05, so the best
result cannot be told apart from the luck of picking the best of eight.

## Probability of Backtest Overfitting

The Probability of Backtest Overfitting (PBO), by combinatorially symmetric cross-validation
(Bailey, Borwein, López de Prado and Zhu, 2017), asks a narrower question within one strategy and
universe. The period is split into S subsets; for every way of choosing half of them as
in-sample, the configuration that scores best in-sample is found, and its rank among all
configurations on the other half is looked up. PBO is the share of those splits in which the
in-sample winner ranks below the median out-of-sample. Near 0, the search finds something real;
near 0.5, it is a coin flip; above 0.5, it picks configurations that do worse than the median.

`build_performance_matrix` runs the backtests, one per configuration and subset;
`pbo_from_matrix` does the arithmetic on that matrix; `compute_pbo` does both. For the S&P 100
reversal, over 96 configurations and 10 subsets (252 splits), PBO is 0.274.

## What a search over noise looks like

Both corrections can be seen at work on candidates that have no edge by construction:

```python
import numpy as np

from midfreq.research.overfitting import pbo_from_matrix
from midfreq.research.reality_check import naive_single_test, reality_check

rng = np.random.default_rng(7)
trials, alone, corrected, pbos = 200, 0, 0, []
for _ in range(trials):
    # eight candidates with no edge: 15 test windows each, mean 0, 3% of noise per window
    candidates = {f"c{i}": list(rng.normal(0.0, 3.0, 15)) for i in range(8)}
    rc = reality_check(candidates, n_boot=1000, seed=int(rng.integers(1 << 31)))
    best = candidates[rc["best_candidate"]]
    alone += naive_single_test(best, n_boot=1000, seed=int(rng.integers(1 << 31))) < 0.05
    corrected += rc["p_value"] < 0.05
    # 96 configurations scored on 10 subsets of pure noise
    pbos.append(pbo_from_matrix(rng.normal(0.0, 1.0, (10, 96)).tolist())["pbo"])

print(f"best of 8 edgeless candidates called real at 5%: "
      f"tested alone {alone / trials:.0%}, Reality Check {corrected / trials:.0%}")
print(f"PBO of a search over noise: mean {np.mean(pbos):.3f} over {trials} searches")
```

```text
best of 8 edgeless candidates called real at 5%: tested alone 48%, Reality Check 8%
PBO of a search over noise: mean 0.519 over 200 searches
```

Tested alone, the best of eight candidates with no edge is called real in about half the
trials. The Reality Check brings that close to the 5% it is designed for (with only 15 windows
the bootstrap is approximate), and PBO over noise averages one half.

The [Walk-forward a strategy](../tutorials/walk_forward.md) tutorial applies these steps to a
real strategy.
