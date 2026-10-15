# Saved runs

The run store, `midfreq.backtest.runstore`, keeps every top-level run (a backtest, a comparison,
a grid search, a walk-forward) with what is needed to read it back and to reproduce it: how it
was run, its headline metrics, and its tables. The dashboard saves every job it finishes, and
its **Run history** page lists, opens and downloads them; scripts save through the same store,
a `LocalRunStore` over a folder or a `GcsRunStore` over a Cloud Storage prefix.

<!-- include: packages/midfreq-backtest/README.md#saved-runs strip-title -->

## Saving a run from a script

A backtest saved to a throwaway store, then read back:

```python
import contextlib
import io
import tempfile
from datetime import datetime

from midfreq.backtest.backtester import Backtester
from midfreq.backtest.runstore import LocalRunStore, record_from_backtest
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import MovingAverageCrossover

params = {"fast_period": 5, "slow_period": 20}
start, end = datetime(2025, 9, 25), datetime(2026, 9, 25)
portfolio = Portfolio(initial_cash=100_000, commission_rate=0.00005)
with contextlib.redirect_stdout(io.StringIO()):
    metrics = Backtester(DataStorage(), portfolio, slippage_bps=5).run(
        MovingAverageCrossover, ["US.AAPL"], Timeframe.HOUR_1, start_date=start, end_date=end, **params)

store = LocalRunStore(tempfile.mkdtemp())         # a throwaway store; default_run_store() is runs/
run_id = store.save(record_from_backtest(portfolio, metrics, strategy="SMA Crossover", params=params,
                                         symbols=["US.AAPL"], timeframe="1h", start=start, end=end))
print(run_id)                                     # UTC time, kind, hash: differs on every save
print(store.list()[["kind", "strategy", "return_pct", "sharpe_ratio", "total_trades"]].to_string(index=False))
print(store.table(run_id, "trades").head(3).to_string(index=False))
```

```text
20261015T221354_backtest_47a352
    kind      strategy  return_pct  sharpe_ratio  total_trades
backtest SMA Crossover    0.087937      0.042763           109
                timestamp  symbol action  qty      price  commission    cash_after exit_reason
2025-10-01 11:30:00+00:00 US.AAPL    BUY  100 254.711354    1.273557  74527.591039        None
2025-10-06 13:30:00+00:00 US.AAPL   SELL  100 255.487943    1.277440 100075.107905      signal
2025-10-08 12:30:00+00:00 US.AAPL    BUY  100 257.368372    1.286842  74336.983891        None
```

The manifest records the commit the run was made from, the data cut-off (the last candle any of
its backtests replayed) and the package version, so a result can be traced to the code and data
that produced it. A run the report cites is promoted into `results/` as committed files, and
never overwrites one there.

## Through the API

The dashboard's API saves each finished job with source `api` and reports its id as `run_id`;
`/api/runs` lists, opens and downloads saved runs (JSON, CSV or Parquet) and, where the
deployment allows it, deletes them. A save that fails is logged and the job still finishes, so
saving never fails a run. See [HTTP API](../api/http/index.md#run-store).
