# Packages

The library code is six packages in [`packages/`](repo:packages), each an installable
distribution with its own `pyproject.toml`, `README.md` and `tests/`. All six contribute modules to
one `midfreq` namespace package (PEP 420), so code imports `midfreq.core`, `midfreq.data` and so
on whichever distribution provides them.

In a checkout, `uv sync` installs every package in editable mode. On the trading VM they are
installed from the wheels of a versioned release (see [Deployment](../deployment.md#releases)),
and every package carries the same version.

| Package | Depends on (`midfreq`) | Optional extras | Summary |
|---|---|---|---|
| [midfreq-core](midfreq-core.md) | none | | The domain model every other package builds on: candles, the venue and order model, portfolio, risk rules, venues, paths, connection settings |
| [midfreq-data](midfreq-data.md) | core | `moomoo`, `ibkr`, `yfinance`, `crypto` | Market data: connectors, the Parquet bar cache and data catalog, read-only account clients, the `fetch` command |
| [midfreq-strategies](midfreq-strategies.md) | core | `hmm` | The trading rules: the candle-strategy library and registry, and the event-driven Strategy API |
| [midfreq-backtest](midfreq-backtest.md) | core, data, strategies | | Running strategies over history: the candle backtester and optimizer, and the event-driven `BacktestEngine` |
| [midfreq-live](midfreq-live.md) | core, data, strategies | | Live paper trading through moomoo OpenD; installs the `midfreq-live` command |
| [midfreq-research](midfreq-research.md) | core, data, strategies, backtest | | The statistical tests and read-backs shared by the studies and the dashboard |

The import rule between them is described under
[Architecture](../architecture/index.md#packages-and-their-layers). Each page in this section is
the package's own `README.md`, included when the site is built.

## Applications

The applications are not part of the six packages. Their READMEs are in the repository:

| Application | README |
|---|---|
| `apps/api`: the dashboard's HTTP API, the `midfreq-api` distribution | [apps/api/README.md](repo:apps/api/README.md); see also [API](../api/index.md) |
| `apps/web`: the dashboard front end | [apps/web/README.md](repo:apps/web/README.md) |
| `apps/dashboard`: the earlier Streamlit dashboard | [apps/dashboard/README.md](repo:apps/dashboard/README.md) |
| `apps/live_runner`: starts the live engine from a checkout | [apps/live_runner/run_live.py](repo:apps/live_runner/run_live.py) |
