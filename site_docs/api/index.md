# API reference

<div class="grid cards" markdown>

-   :material-language-python:{ .lg .middle } **[Python API](python/index.md)**

    ---

    Every public module of the packages, generated from their docstrings each time this
    site is built, so it cannot drift from the code.

-   :material-api:{ .lg .middle } **[HTTP API](http/index.md)**

    ---

    The dashboard's API: its design, conventions and configuration, and the
    [full reference](http/reference.md) rendered from its OpenAPI contract.

</div>

## Python packages

| Package | Distribution | Summary |
|---|---|---|
| [`midfreq.core`](python/midfreq/core/index.md) | `midfreq-core` | Candles, the venue, instrument, order and event model, portfolio and risk rules, venues, paths, connection settings |
| [`midfreq.engine`](python/midfreq/engine/index.md) | `midfreq-engine` | The candle backtester's C++ core, through pybind11 |
| [`midfreq.data`](python/midfreq/data/index.md) | `midfreq-data` | Connectors with their execution clients and live streams, the Parquet price cache and data catalog, read-only account clients, the `fetch` command |
| [`midfreq.strategies`](python/midfreq/strategies/index.md) | `midfreq-strategies` | The candle strategy library and registry, the event-driven Strategy API, native strategies |
| [`midfreq.backtest`](python/midfreq/backtest/index.md) | `midfreq-backtest` | The candle backtester and optimizer, the venue engine with its fill and slippage models, the run store |
| [`midfreq.live`](python/midfreq/live/index.md) | `midfreq-live` | The session runner and its Moomoo order gateway |
| [`midfreq.trading`](python/midfreq/trading/index.md) | `midfreq-trading` | The trading node: OMS and order journal, pre-trade risk, position ledger, market and reference data, the strategy host, the simulated venue, the `midfreq-node` command |
| [`midfreq.research`](python/midfreq/research/index.md) | `midfreq-research` | Probability of Backtest Overfitting, White's Reality Check, the results index, the forward-test read-back |

The dashboard's API, `midfreq.api` in `apps/api`, is documented as an HTTP API rather than as
Python.
