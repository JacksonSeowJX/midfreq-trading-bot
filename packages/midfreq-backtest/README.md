# midfreq-backtest

Running strategies over historical data. Depends on `midfreq-core`, `midfreq-data` and
`midfreq-strategies`.

## Contents

Two engines, kept side by side and checked against each other:

- `backtester.Backtester`: the original candle-by-candle loop for
  `midfreq.strategies.candle_strategies` classes, with `optimizer` (grid search and walk-forward)
  on top. The dashboard and the research studies use these.
- `BacktestEngine` (`engine`, re-exported from `midfreq.backtest`): the event-driven engine for
  `midfreq.strategies.trading.Strategy` subclasses. `DataEngine` merges bar, quote, trade and
  book streams in UTC order into `TimeSlice`s; one `SimulatedExchange` per venue fills orders
  through a `FillModel` (`fill_models`) and a `SlippageModel` (`slippage`), with lot sizes, fees
  and cash from the venue spec; `risk.RiskEngine` checks orders before they reach the venue;
  `results.BacktestResult` reports per currency.
- `legacy_adapter`: `run_legacy_backtest` runs any candle strategy, unchanged, inside
  `BacktestEngine`. For every strategy in `STRATEGY_REGISTRY` it reproduces `Backtester` exactly
  (same trades, same final equity); `tests/integration/test_legacy_equivalence.py` checks this.

## Usage

```python
from midfreq.backtest.backtester import Backtester
from midfreq.core.candle import Timeframe
from midfreq.core.paths import data_dir
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import MovingAverageCrossover

metrics = Backtester(DataStorage(str(data_dir())), Portfolio(initial_cash=100_000.0), slippage_bps=5.0).run(
    MovingAverageCrossover, ["HK.00700"], Timeframe.HOUR_1, fast_period=5, slow_period=20)
print(metrics["return_pct"], metrics["final_equity"])
```

```python
from midfreq.backtest import BacktestEngine, FillModel
from midfreq.core.model import BarType, PercentFeeModel
from midfreq.core.paths import data_dir
from midfreq.data.persistence import LegacyDataReader
from midfreq.strategies.event_driven import MACrossover, MACrossoverConfig

reader = LegacyDataReader(data_dir())
iid = reader.instrument_id("HK.00700")
engine = BacktestEngine()
engine.add_venue(iid.venue, {"HKD": 100_000.0}, fill_model=FillModel(slippage_bps=5.0),
                 fee_model=PercentFeeModel(0.001))
engine.add_data(reader.read_bars(iid, "1-HOUR"))
engine.add_strategy(MACrossover(MACrossoverConfig(
    bar_types=[str(BarType.of(iid, "1-HOUR"))], fast_period=5, slow_period=20)))
result = engine.run()
print(result.summary())
```

Tests: `uv run pytest -q packages/midfreq-backtest tests/integration`. The same equivalence on
the real data cache is opt-in: `MIDFREQ_SLOW=1 uv run pytest -q tests/slow`.

## Saved runs

`runstore` keeps every top-level run (a backtest, a comparison, a grid search, a walk-forward)
with what is needed to read it back and to reproduce it. A run is one folder,
`runs/<run_id>/`: `run.json` (kind, source, strategy, params, symbols, universe, timeframe,
dates, capital, commission, slippage, risk settings, objective and method, git commit, data
cut-off, package version, host, created in UTC, and the headline metrics), `metrics.parquet`
(one row) and one Parquet file per table: `equity` and `trades` for a backtest, `strategies`,
`equity` and `trades` (long, by label) for a comparison, `grid` (params and metrics per
combination) for a grid search, `windows` (per window: dates, chosen params, every in-sample
metric as `is_*` and every out-of-sample metric as `oos_*`) for a walk-forward. An
optimization is saved once; its sub-backtests are not saved one by one. There is no index file:
the listing is read from the manifests, so processes saving at once cannot corrupt it, and a
save writes a hidden temporary folder and renames it into place, so a half-written run is
never listed. Run ids look like `20261016T043512_walk_forward_3fa9c2` (UTC time, kind, hash).

The store is `MIDFREQ_RUNS` (a folder, or `gs://bucket/prefix` for the hosted dashboard, which
needs `google-cloud-storage`, the `midfreq-api[gcs]` extra), else `<repo>/runs/`, which git
ignores.

```python
from midfreq.backtest.optimizer import walk_forward
from midfreq.backtest.runstore import default_run_store, record_from_backtest

store = default_run_store()
result = walk_forward(..., run_store=store, run_meta={"universe": "sp100"})   # saved once, at the end
run_id = store.save(record_from_backtest(portfolio, metrics, strategy="SMA Crossover", params=params,
                                         symbols=["HK.00700"], timeframe="1h", start=start, end=end))

store.list(kind="walk_forward")                 # one row per run, newest first, metrics as columns
store.table(run_id, "trades")                   # one table; store.load(run_id) for the whole run
```

`grid_search` and `walk_forward` take `run_store` (default `None`: nothing is saved, and the
result is identical either way); a failed save warns and still returns the result. To get the
id back, build the record yourself (`record_from_grid_search`, `record_from_walk_forward`,
`record_from_compare`) and call `store.save`.

The command line lists, shows, removes and promotes runs. `promote` copies a run the report
cites into `results/` as committed files, `run_<id>.json` and `run_<id>_<table>.csv` (CSV
because git ignores Parquet), and never overwrites one:

```bash
uv run python -m midfreq.backtest.runstore list [--kind walk_forward] [--strategy NAME]
uv run python -m midfreq.backtest.runstore show <run_id>
uv run python -m midfreq.backtest.runstore promote <run_id>
uv run python -m midfreq.backtest.runstore rm <run_id>
```

Across runs, DuckDB reads the folders directly (`window` is an SQL keyword, so quote it):

```bash
uv run --with duckdb python -c "import duckdb; print(duckdb.sql('''
  SELECT split_part(filename, '/', 2) AS run_id, \"window\", is_return_pct, oos_return_pct
  FROM read_parquet('runs/*/windows.parquet', filename = true) ORDER BY run_id, \"window\"'''))"
```
