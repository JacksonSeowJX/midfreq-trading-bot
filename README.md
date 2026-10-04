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

This project focuses on the development of a quantitative trading bot for mid-frequency U.S. stock trading. 

The system is designed with modular components including a market data service, backtesting engine, trading logic module, and order gateway. It supports configurable candlestick intervals (e.g., 5-minute, 1-hour, daily) and integrates with brokerage APIs for paper trading and execution testing.

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

Update 13 (2 Oct 26)

Update 14 (16 Oct 26)

## System Overview

This repository contains a modular quantitative trading framework for mid-frequency stock trading: market data ingestion (Moomoo/OpenD), Parquet storage, a strategy engine, a realistic backtester with risk management and optimization tooling, and a live paper-trading gateway.

## Features

- **Standardized OHLCV Schema**: All data is validated using Pydantic and converted to UTC timezone-aware timestamps.
- **Modular Provider System**: Easily add New data providers by implementing the `BaseDataProvider` interface.
- **Local Parquet Storage**: High-performance storage using Parquet format, organized by symbol and timeframe.
- **YFinance Support**: Initial implementation for fetching historical data and latest quotes/candles from Yahoo Finance.

## Project Structure

```
src/
  core/             strategies, backtester, walk-forward optimiser, risk manager,
                    live trading engine, order gateway, PBO and Reality Check
  providers/        market-data providers (moomoo is the one used throughout)
  app.py            Streamlit dashboard (see docs/DASHBOARD_GUIDE.md)
run_live.py         live paper-trading entry point
scripts/
  live/             the live forward test: nightly launcher, config picker, end-to-end
                    replay test, real-broker order-path test, trade-blotter export
  data/             historical data download and incremental refresh
  calibration/      cost-model measurements: HK fee from a real fill, bid-ask spreads
  research/         every study the final report draws on
  research/superseded/  earlier versions replaced after the 2026-09-08 testing audit,
                    kept for the record
  tools/            dashboard screenshots, cache seeding, one-off charts
config/             stock universes (symbols.json) and the live forward-test config
results/            study outputs (CSV / JSON) read by the dashboard
live_sessions/      live session logs and strategy state, uploaded daily by the cloud VM
presentations/      weekly-update slide and chart generators
tests/              unit tests
data/               local Parquet price cache (ignored by git)
```

## Setup

1. **Install Dependencies**:
   ```bash
   pip install pandas pyarrow pydantic python-dotenv pytz yfinance
   ```

2. **Set PYTHONPATH**:
   ```bash
   export PYTHONPATH=src
   ```

## Usage

Backfill historical data for all configured HK stocks (requires OpenD running):
```bash
python3 scripts/data/backfill_data.py
```

Run the backtesting dashboard:
```bash
PYTHONPATH=src streamlit run src/app.py
```

See **[docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md)** for a walkthrough of every view and control.

Run live paper trading (requires OpenD running, HK market hours):
```bash
python3 run_live.py --strategy "Z-Score Mean Reversion" --symbols HK.00700 --duration 30
```

## Data Schema

The system standardizes all OHLCV data to the following schema:
- `timestamp` (UTC, index)
- `open`
- `high`
- `low`
- `close`
- `volume`
