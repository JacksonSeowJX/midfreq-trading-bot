## Quantitative Trading Bot for mid-frequency stock trading

### Name: Seow Jia Xian Jackson
### FYP ID: CCDS25-1040
### Project Title: Development of Quantitative Trading Bot for mid-frequency stock trading
### Supervisor: Prof Chng Eng Siong
### Start Date: 12 Jan 2026
### End Date: 19 Oct 2026

## Contact
- Email: seow0126@e.ntu.edu.sg
- Linkedin: https://www.linkedin.com/in/jackson-seow-jia-xian-798186182/

## Overview

This project builds and evaluates a quantitative trading bot for mid-frequency (hourly) stock trading on Hong Kong and US equities, using the moomoo OpenAPI for both market data and paper trading.

The system covers the whole path from data to live orders: market data download and storage, a backtester with realistic costs, walk-forward validation, checks for overfitting, a live paper-trading engine that runs unattended on a cloud VM, and a dashboard over all of it.

The research question was whether any systematic strategy shows a real, tradable edge on these markets once it is tested properly. After 13 strategies on 4 stock universes, the answer reached is that none does.

## Results at a Glance

| | |
|---|---|
| Strategies implemented | 13: trend following, mean reversion, pairs trading, regime switching, cross-sectional reversal and momentum, an ML direction classifier, and buy-and-hold with a risk overlay |
| Stock universes | 19 Hong Kong stocks, 15 large US stocks, the S&P 100, and the Hang Seng Index (88 stocks) |
| Validation standard | Walk-forward over 3 years, split two ways (9 windows and 15 windows, each 70% training and 30% test). A result passes only if its mean out-of-sample return is positive and at least 50% of its test windows are profitable under both splits |
| Results brought to that standard | 217, of which 13 passed (6%) |
| Strongest result | Cross-sectional reversal on the S&P 100. It passes at the broker's real fee of 0.005% per side (+5.73% and +0.63% per test window) with a Probability of Backtest Overfitting of 0.274 |
| Corrected for the search | White's Reality Check over the 8 cross-sectional candidates gives p = 0.094 and 0.098, so the result cannot be told apart from luck. It also passes or fails depending on the week its data ends |
| Live trading | Paper trading from a Google Cloud VM: Hong Kong single-stock strategies from July to September 2026, then a forward test of S&P 100 reversal on a US$100,000 allocation from 28 September 2026 |

No strategy beat chance once the number of things tried was accounted for, and the single-stock strategies did not beat simply holding the same stocks. What the project delivers is the framework, a validation method that catches results a single backtest would have reported as real, and a live system checked against a real broker.

## Video Updates (YouTube)

To see all videos
- https://studio.youtube.com/channel/UCWvIDtAKkT7eFPqhEd4aaAA/videos/upload?filter=%5B%5D&sort=%7B%22columnType%22%3A%22date%22%2C%22sortOrder%22%3A%22DESCENDING%22%7D

Update 1 (29 Mar 26)
- https://studio.youtube.com/video/G-v-z53wfnk/edit
  
Update 2 (12 Apr 26)
- https://www.youtube.com/watch?v=GHJNfMNTLsA

Update 3 (26 Apr 26)
- https://youtu.be/EwG7gCxUNEM

Update 4 (10 May 26)
- https://youtu.be/LtlsBnNPh5E

Update 5 (24 May 26)
- https://youtu.be/SHOYmgZUYfA

Update 6 (7 Jun 26)
- https://youtu.be/QmzwcWnmRn0

Update 7 (21 Jun 26)
- https://youtu.be/SCY3jmu6lNo

Update 8 (24 Jul 26)
- https://youtu.be/x9YsrLUa8CE

Update 9 (7 Aug 26)
- https://youtu.be/LFCGzhBQoSA

Update 10 (21 Aug 26)
- https://youtu.be/pcb1sQdeAgk

Update 11 (4 Sep 26)
- https://youtu.be/oteaBkMB7T4

Update 12 (18 Sep 26)
- https://youtu.be/8UpOjeVDq7Y

Update 13 (9 Oct 26)
- https://youtu.be/18lVFtQxvnY

Update 14 (18 Oct 26)


## System Overview

- **Data** (`midfreq-data`: `providers/moomoo_provider.py`, `storage.py`): candles are pulled from moomoo OpenD and kept as Parquet, one file per symbol and timeframe. Every study uses moomoo data, the same source the live engine trades on. The Yahoo Finance and Interactive Brokers providers remain from early prototypes.
- **Strategies** (`midfreq-strategies`: `candle_strategies.py`): 13 strategies behind one interface and a registry, so the backtester, optimiser, dashboard and live engine all run the same strategy code.
- **Backtester** (`midfreq-backtest`: `backtester.py`; `midfreq-core`: `portfolio.py`, `risk_manager.py`): event-driven, with per-market fees checked against real fills (Hong Kong 0.16% and US 0.005% per side), slippage, board lots, stop-loss, trailing stop, take-profit, position sizing and a drawdown halt.
- **Validation** (`midfreq-backtest`: `optimizer.py`; `midfreq-research`: `overfitting.py`, `reality_check.py`): grid search, walk-forward optimisation, Probability of Backtest Overfitting, and White's Reality Check.
- **Live trading** (`midfreq-live`: `live_engine.py`, `order_gateway.py`; `apps/live_runner/run_live.py`): streams hourly candles, places marketable limit orders, records only what the broker confirms as filled, and keeps its state between daily sessions. Cron starts a session on the VM each US trading day, and the session logs are committed to `live_sessions/` daily.
- **Dashboard** (`apps/dashboard/app.py`): a Streamlit app for backtests, optimisation, the live accounts and every research result. See [docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md).

## Project Structure

```
packages/           six installable packages, all under the `midfreq` namespace,
                    each with its own tests/; a package imports only those below it
  midfreq-core/       candles, the venue/instrument/order/event model, portfolio,
                      risk rules, venues, repository paths, connection settings
  midfreq-data/       connectors (Moomoo, IBKR, yfinance, Binance, ccxt), the Parquet
                      cache and data catalog, `python -m midfreq.data`
  midfreq-strategies/ the 13-strategy library and registry; the event-driven Strategy API
  midfreq-backtest/   backtester, walk-forward optimiser, event-driven BacktestEngine
                      with fill and slippage models
  midfreq-live/       live trading engine and broker order gateway
  midfreq-research/   PBO, White's Reality Check, results index, forward-test read-back
apps/
  dashboard/        Streamlit dashboard (see docs/DASHBOARD_GUIDE.md)
  live_runner/      run_live.py, the live paper-trading entry point
research/studies/   every study the final report draws on (superseded/: earlier
                    versions replaced after the 2026-09-08 testing audit)
ops/                the VM's cron launchers and the deployment runbook
scripts/
  live/             config picker, end-to-end replay test, real-broker order-path test,
                    trade-blotter export
  data/             historical data download, incremental refresh and gap filling
  calibration/      cost-model measurements: HK fee from a real fill, bid-ask spreads
  tools/            dashboard screenshots, cache seeding, one-off charts
examples/           early provider and backtest demos
config/             stock universes (symbols.json), venues, connections, the live
                    forward-test config
results/            study outputs (CSV / JSON) read by the dashboard
live_sessions/      live session logs and strategy state, uploaded daily by the cloud VM
presentations/      update slide and chart generators
docs/               dashboard guide, engine architecture
tests/              cross-package tests: integration/, e2e/, slow/ (real data, opt-in)
data/               local Parquet price cache (ignored by git)
```

## Setup

The repository is a [uv](https://docs.astral.sh/uv/) workspace. One command creates `.venv/` with every package installed in editable mode, at the library versions pinned in `pyproject.toml` (the ones every reported result was produced with):
```bash
uv sync
```

Developed on Python 3.12. Downloading data and live trading need the moomoo OpenD gateway running and logged in. Its address, and those of the other brokers and exchanges, are in `config/connections.yaml` (default 127.0.0.1:11111, paper trading); any value can be overridden with an environment variable such as `MIDFREQ_MOOMOO_PORT=11112`. API keys never go in that file: put them in the environment or an untracked `.env`.

The price cache (`data/`) is not in the repository. From a fresh clone the dashboard's research and results views work straight away, because they read `results/` and `live_sessions/`. Running a new backtest needs the cache, which the scripts in `scripts/data/` download through OpenD.

## Usage

Run the dashboard:
```bash
uv run streamlit run apps/dashboard/app.py
```
See **[docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md)** for a walkthrough of every view and control.

Run the tests:
```bash
uv run pytest            # every package's tests and tests/
uv run lint-imports      # checks that packages only import the layers below them
```

Reproduce the report's key numbers (these need the price cache):
```bash
# Cost sensitivity of the S&P 100 result (report Section 4.9)
uv run python research/studies/cross_sectional_cost_sensitivity.py --end-date 2026-08-21

# Probability of Backtest Overfitting, 0.274 (report Section 4.11)
uv run python research/studies/pbo_sp100_reversal.py --end-date 2026-08-21

# White's Reality Check, p = 0.094 and 0.098 (report Section 4.11)
uv run python research/studies/reality_check_study.py --end-date "2026-08-28 16:00" --n-boot 20000
```

Live paper trading (needs OpenD):
```bash
# The S&P 100 forward test, as cron runs it on the VM. It only starts within 30 minutes of the
# US open and ends just after the close. Prefix with DRY_RUN=1 to print the command without running it
./ops/run_sp100_forward_test.sh --scheduled

# Any single strategy, for example on a Hong Kong stock during Hong Kong hours
uv run python apps/live_runner/run_live.py --strategy "Z-Score Mean Reversion" --symbols HK.00700 --duration 30
```

Check the live trading path without a broker, by replaying the recorded sessions through the real engine against a simulated one:
```bash
uv run python scripts/live/replay_live_sessions.py
```

Download historical data (needs OpenD):
```bash
uv run python scripts/data/backfill_data.py        # the configured Hong Kong stocks
uv run python scripts/data/backfill_sp100.py       # the S&P 100
```

## Data Schema

The system standardizes all OHLCV data to the following schema:
- `timestamp` (index; the candle's end time in the exchange's local time, stored with a UTC label)
- `open`
- `high`
- `low`
- `close`
- `volume`

## Venue-Based Engine (in development)

A second engine, modelled on NautilusTrader, sits beside the candle backtester without changing it (`midfreq.backtest.engine`, with its model in `midfreq.core.model` and its connectors in `midfreq.data.adapters`). Trading happens on explicit **venues** (XHKG, XNAS, XNYS, XSES, XSHG, XSHE, XTKS, and the crypto exchanges BINANCE, COINBASE, KRAKEN, OKX) in **instruments** such as `00700.XHKG`, from **bars** whose timestamp is their close in true UTC, through **orders** that a risk engine can deny and a simulated venue can reject. Each venue's timezone, trading sessions, currency, board-lot rule and fees are in `config/venues.json`.

- **Connectors** for Moomoo, IBKR and yfinance (wrapping the existing providers), Binance (its public REST API) and ccxt (Coinbase, Kraken, OKX, ...) share one contract and are tested against it with fake transports.
- **Ingestion** into a Parquet catalog keyed by bar type, plus a read-only reader for the existing `data/` cache that converts its local-time stamps to UTC.
- **Backtest engine** with a message bus, a data engine that merges venues in UTC order, one simulated exchange per venue, a risk engine reusing `midfreq.core.risk_manager`, and one cash account per currency (no FX conversion).
- **Every candle strategy runs unchanged** through a legacy adapter, with trade lists and final equity identical to the candle backtester; cross-sectional reversal and the moving-average crossover are also ported natively.

Live trading stays on the candle engine (`midfreq-live`) until the forward test ends. See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the design and how it maps to NautilusTrader.

```bash
uv run python -m midfreq.data venues
uv run python -m midfreq.data fetch --venue XHKG --symbols 00700 00005 --bar 1-HOUR --start 2024-01-01 --client moomoo
uv run python -m midfreq.data fetch --venue BINANCE --symbols BTCUSDT ETHUSDT --bar 1-HOUR --start 2024-01-01
```
