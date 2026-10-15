# Getting started

The repository is a [uv](https://docs.astral.sh/uv/) workspace of six Python packages that share
one `midfreq` namespace, two applications (the dashboard's HTTP API and its web front end), the
research studies that produced the project's results, and the scripts that run the live
paper-trading sessions. Everything a backtest needs is in the workspace except the price data,
which is downloaded once into a local Parquet cache.

<div class="grid cards" markdown>

-   :material-download-outline:{ .lg .middle } **[Installation](installation.md)**

    ---

    Python 3.12, `uv sync`, the price cache, and when the broker's gateway (moomoo OpenD) is
    needed.

-   :material-play-circle-outline:{ .lg .middle } **[Quickstart](quickstart.md)**

    ---

    A backtest in about fifteen lines of Python, the same run in the dashboard, and a
    walk-forward.

</div>

## Choose a path

| Goal | Start with | Then |
|---|---|---|
| Backtest a strategy | [Quickstart](quickstart.md) | [Backtest a strategy](../tutorials/backtest_a_strategy.md), [Backtesting](../concepts/backtesting.md) |
| Judge whether a result is real | [Walk-forward a strategy](../tutorials/walk_forward.md) | [Optimization and validation](../concepts/optimization_and_validation.md), [Results](../results/index.md) |
| Write a strategy | [Strategies](../concepts/strategies.md) | [Write an event-driven strategy](../tutorials/event_driven_strategy.md) |
| Fetch data from a broker or exchange | [Data and connectors](../concepts/data.md) | [Integrations](../integrations/index.md), [Add a connector](../tutorials/add_a_connector.md) |
| Run or inspect live paper trading | [Live trading](../concepts/live_trading.md) | [Moomoo (OpenD)](../integrations/moomoo.md), [Releases and deployment](../developer_guide/releases_and_deployment.md) |
| Use the dashboard | [The dashboard](../concepts/dashboard.md) | [HTTP API](../api/http/index.md) |

## Two backtesting engines

The framework has two engines, kept side by side and checked against each other. Every
reported result was produced with the first; the second is the venue-based design that the
framework is moving to.

| | Candle backtester | Venue engine |
|---|---|---|
| Entry point | `midfreq.backtest.backtester.Backtester`, with `optimizer.grid_search` and `optimizer.walk_forward` on top | `midfreq.backtest.BacktestEngine` |
| Strategies | `BaseStrategy` subclasses in `midfreq.strategies.candle_strategies`, which trade by calling `portfolio.execute_trade` on each candle | `midfreq.strategies.trading.Strategy` subclasses, which submit orders and receive fill events |
| Data | the price cache, `data/<MARKET>_<CODE>/<timeframe>.parquet` | bars, quotes, trades or order-book snapshots, from the data catalog or the price cache, in true UTC |
| Costs | one commission rate and one slippage allowance per run | a fee model and a fill and slippage model per venue; one cash account per currency |
| Used by | the research studies, the dashboard, the live engine | new strategies; every candle strategy also runs in it, unchanged, with identical results |

[Backtesting](../concepts/backtesting.md) describes both.

## What needs what

| To | You need |
|---|---|
| Read the research results and the recorded live sessions | a clone and `uv sync` |
| Run the tests | a clone and `uv sync`; the default suite needs no data, network or broker |
| Backtest, optimize, run the research studies | the price cache in `data/` |
| Download prices, trade on paper, read the broker account | moomoo OpenD running and logged in |
| Run the dashboard locally | the above, plus Node 22.12 or later to build the web app |
