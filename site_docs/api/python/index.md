# Python API

This reference is generated from the source each time the site is built: one page per public
module of the six packages, rendered by [mkdocstrings](https://mkdocstrings.github.io/) from the
docstrings and signatures. The source is read by static analysis
([griffe](https://mkdocstrings.github.io/griffe/)); nothing is imported, so no vendor SDK is
needed to build it.

Each package's page is the distribution's README, the names the package re-exports, and its
modules. Modules whose name starts with an underscore are private and are not listed.

| Package | Start with |
|---|---|
| [`midfreq.core`](midfreq/core/index.md) | [`model`](midfreq/core/model/index.md) (identifiers, instruments, bars, orders, events), [`venues`](midfreq/core/venues/index.md), [`portfolio`](midfreq/core/portfolio.md), [`risk_manager`](midfreq/core/risk_manager.md) |
| [`midfreq.data`](midfreq/data/index.md) | [`adapters`](midfreq/data/adapters/index.md) (the connector contract and registry), [`persistence`](midfreq/data/persistence/index.md), [`storage`](midfreq/data/storage.md) |
| [`midfreq.strategies`](midfreq/strategies/index.md) | [`candle_strategies`](midfreq/strategies/candle_strategies.md), [`trading`](midfreq/strategies/trading/index.md), [`event_driven`](midfreq/strategies/event_driven/index.md) |
| [`midfreq.backtest`](midfreq/backtest/index.md) | [`backtester`](midfreq/backtest/backtester.md), [`optimizer`](midfreq/backtest/optimizer.md), [`engine`](midfreq/backtest/engine.md), [`fill_models`](midfreq/backtest/fill_models.md), [`runstore`](midfreq/backtest/runstore.md) |
| [`midfreq.live`](midfreq/live/index.md) | [`live_engine`](midfreq/live/live_engine.md), [`order_gateway`](midfreq/live/order_gateway.md) |
| [`midfreq.research`](midfreq/research/index.md) | [`reality_check`](midfreq/research/reality_check.md), [`overfitting`](midfreq/research/overfitting.md), [`results_index`](midfreq/research/results_index.md) |

!!! note "Not in this reference"
    `midfreq.data.providers` (the original Moomoo, Interactive Brokers and yfinance providers,
    which the connectors wrap and the live engine still uses) has no `__init__.py`, so it is an
    implicit namespace package, which griffe's static analysis does not traverse. Its modules
    are documented in their source: [`packages/midfreq-data/src/midfreq/data/providers/`](repo:packages/midfreq-data/src/midfreq/data/providers).
