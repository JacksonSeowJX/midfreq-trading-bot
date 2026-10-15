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
