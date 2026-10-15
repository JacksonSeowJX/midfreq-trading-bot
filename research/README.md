# Research studies

Each file in `studies/` is one study: a standalone script that runs a fixed experiment (a walk-forward, a cost sweep, PBO, the Reality Check) over the cached bars in `data/` and writes its result to `results/` as a timestamped CSV or JSON, for example `results/cross_sectional_corrected_<stamp>.csv`. The docstring at the top of each file says what it tests, what was fixed in advance and how to run it. The reported numbers in the final report come from these files.

`studies/superseded/` holds studies whose numbers were replaced, mostly by `cross_sectional_corrected.py` after the 8 Sep 2026 audit. They are kept so the earlier results stay reproducible; `midfreq.research.results_index` leaves their output out of the overview.

Run a study from the repository root with the workspace environment:

```bash
uv sync
uv run python research/studies/cross_sectional_cost_sensitivity.py --end-date 2026-08-21
uv run python research/studies/pbo_sp100_reversal.py --end-date 2026-08-21
```

Most take minutes to an hour, since they run many walk-forwards. They read `data/` but never fetch into it (to refresh the cache, use `scripts/data/`); `best_tool_allocation_robust_v2.py` also rewrites `config/best_tool_allocation.json`, and `ml_classifier_macro_research.py` downloads macro series. Studies find `config/`, `data/` and `results/` through `midfreq.core.paths`, and the shared statistics they use are in the [`midfreq-research`](../packages/midfreq-research/README.md) package. The dashboard's Research Studies and Results Overview views read whatever is in `results/`.
