# midfreq-research

The statistical tests and read-backs that the research studies and the dashboard share. It sits at the top of the package layers: it may import `midfreq.backtest`, `midfreq.strategies`, `midfreq.data` and `midfreq.core`, and nothing imports it except the studies, the dashboard and the presentation scripts.

| Module | What it does |
|---|---|
| `midfreq.research.overfitting` | Probability of Backtest Overfitting (PBO) by combinatorially symmetric cross-validation (Bailey et al. 2017). |
| `midfreq.research.reality_check` | White's Reality Check (2000) with the stationary bootstrap, over every strategy-universe pair that was tried. |
| `midfreq.research.results_index` | Stacks every two-configuration study CSV in `results/` into one table (newest run per study, superseded studies left out). |
| `midfreq.research.forward_test` | Reads the S&P 100 forward test back from `live_sessions/session_*.jsonl`, without OpenD. |

The studies that use these live in [`research/studies/`](../../research/README.md).

Tests: `uv run pytest -q packages/midfreq-research`
