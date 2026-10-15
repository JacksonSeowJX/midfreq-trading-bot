# Installation

## Requirements

| Requirement | Needed for |
|---|---|
| [uv](https://docs.astral.sh/uv/) | everything: it creates the environment and runs every command |
| Python 3.12 | everything; uv installs it if the machine does not have it |
| git | a clone of the repository |
| The price cache (`data/`) | backtests, optimizations and the research studies ([below](#the-price-cache)) |
| [moomoo OpenD](../integrations/moomoo.md), running and logged in | downloading prices, live paper trading and reading the broker account only |
| Node 22.12 or later | building the dashboard's web app ([below](#the-dashboard)) |

The workspace resolves for Python 3.12 only (`[tool.uv] environments` in the root
`pyproject.toml`), the version every reported result was produced with. The trading VM runs
the same packages on Python 3.10 with its own pinned versions; see
[Releases and deployment](../developer_guide/releases_and_deployment.md).

## Install the workspace

```bash
git clone https://github.com/JacksonSeowJX/midfreq-trading-bot.git
cd midfreq-trading-bot
uv sync --python 3.12
```

`uv sync` creates `.venv/` and installs, in editable mode, the six packages under `packages/`
and the dashboard's API (`apps/api`), with:

- every connector's optional dependencies (the `moomoo`, `ibkr`, `yfinance` and `crypto`
  extras of `midfreq-data`) and the `hmm` extra of `midfreq-strategies`;
- the `dev` dependency group: pytest, import-linter, httpx, google-auth and requests;
- third-party libraries at the versions every result was produced with, held by
  `constraint-dependencies` in the root `pyproject.toml` and by `uv.lock` (for example pandas
  2.2.2, numpy 1.26.4 and pyarrow 16.1.0).

Run every command through uv (`uv run python ...`, `uv run pytest`), or activate `.venv/`
first.

## Check the installation

`python -m midfreq.data venues` lists the configured venues and the registered connectors. It
reads `config/venues.json` only and makes no connection:

```bash
uv run python -m midfreq.data venues
```

```text
venue     tz                 ccy   default   connectors   fees
XHKG      Asia/Hong_Kong     HKD   moomoo    ibkr,moomoo,yfinance 0.1600%
XNAS      America/New_York   USD   moomoo    ibkr,moomoo,yfinance 0.0050%
XNYS      America/New_York   USD   moomoo    ibkr,moomoo,yfinance 0.0050%
XSES      Asia/Singapore     SGD   moomoo    ibkr,moomoo,yfinance 0.1000% (uncalibrated)
XSHG      Asia/Shanghai      CNY   moomoo    ibkr,moomoo,yfinance 0.0600% (uncalibrated)
XSHE      Asia/Shanghai      CNY   moomoo    ibkr,moomoo,yfinance 0.0600% (uncalibrated)
XTKS      Asia/Tokyo         JPY   moomoo    ibkr,moomoo,yfinance 0.0500% (uncalibrated)
BINANCE   UTC                USDT  binance   binance,ccxt maker 0.100% / taker 0.100% (uncalibrated)
COINBASE  UTC                USD   ccxt      ccxt         maker 0.600% / taker 1.200% (uncalibrated)
KRAKEN    UTC                USD   ccxt      ccxt         maker 0.250% / taker 0.400% (uncalibrated)
OKX       UTC                USDT  ccxt      ccxt         maker 0.080% / taker 0.100% (uncalibrated)

connectors registered: binance, ccxt, ibkr, moomoo, yfinance
```

The test suite is hermetic: it uses fake brokers, temporary folders and synthetic data, so it
passes on a fresh clone with no price cache and no network.

```bash
uv run pytest -q
```

## The price cache

Backtests read candles from `data/`, one Parquet file per symbol and timeframe. The folder is
not in the repository. From a fresh clone, the research results (`results/`) and the recorded
live sessions can be read straight away; a new backtest needs the cache.

```text
data/
  HK_00700/
    1h.parquet         # Tencent, hourly
    1d.parquet         # Tencent, daily
  US_AAPL/
    1h.parquet
  ...
```

Every file has the same schema: a `timestamp` index (the candle's end time in the exchange's
local time, stored with a UTC label) and the columns `open`, `high`, `low`, `close` and `volume`.
[Data and connectors](../concepts/data.md#the-price-cache) explains the timestamp convention and
how the venue engine reads it in true UTC.

The scripts in [`scripts/data/`](repo:scripts/data) download the cache through moomoo OpenD:

```bash
uv run python scripts/data/backfill_data.py      # the configured Hong Kong stocks
uv run python scripts/data/backfill_sp100.py     # the S&P 100
uv run python scripts/data/backfill_hsi.py       # the Hang Seng Index constituents
```

The connectors can also fetch bars from other sources into the newer Parquet data catalog
(`catalog/`); see [Data and connectors](../concepts/data.md#the-data-catalog).

## Moomoo OpenD

OpenD is the moomoo OpenAPI's gateway, a separate program that holds the broker login. The
framework needs it for three things only: downloading prices, live paper trading, and reading
the broker account. Backtests, optimizations, the research studies and the tests do not.

Its address and trading environment come from the `moomoo` entry of
[`config/connections.yaml`](repo:config/connections.yaml): `127.0.0.1:11111` and the paper
environment (`trading_env: simulate`) by default. Any value can be overridden with an
environment variable, for example `MIDFREQ_MOOMOO_PORT=11112`. Real money is used only if that
entry, or `MIDFREQ_MOOMOO_TRADING_ENV`, says `real` explicitly. See
[Moomoo (OpenD)](../integrations/moomoo.md).

## Folders and the runtime root

Code finds `config/`, `data/`, `results/`, `live_sessions/` and `catalog/` through
`midfreq.core.paths`, never by counting directory levels. The root is the `MIDFREQ_ROOT`
environment variable if set, otherwise the repository in a checkout. A release installed on the
trading VM points `MIDFREQ_ROOT` at its runtime folder.

## The dashboard

The dashboard is a React app (`apps/web`) over a FastAPI service (`apps/api`). To run it
locally:

```bash
(cd apps/web && npm ci && npm run build)          # once, and after changes to the web app
uv run uvicorn midfreq.api.main:app --port 8000    # then open http://127.0.0.1:8000
```

The API serves the built web app and its own interactive documentation at `/docs`. See
[The dashboard](../concepts/dashboard.md) for the views and
[HTTP API](../api/http/index.md) for the routes.

!!! info "The hosted dashboard"
    A hosted copy of the dashboard runs on Google Cloud Run at
    <https://midfreq-dashboard-123943780205.asia-southeast1.run.app>. It is private: visitors
    sign in with Google, and access is by invitation.
