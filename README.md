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

Update 8 (5 Jul 26)

## System Overview

This repository contains a modular quantitative trading framework for mid-frequency stock trading: market data ingestion (Moomoo/OpenD), Parquet storage, a strategy engine, a realistic backtester with risk management and optimization tooling, and a live paper-trading gateway.

## Features

- **Standardized OHLCV Schema**: All data is validated using Pydantic and converted to UTC timezone-aware timestamps.
- **Modular Provider System**: Easily add New data providers by implementing the `BaseDataProvider` interface.
- **Local Parquet Storage**: High-performance storage using Parquet format, organized by symbol and timeframe.
- **YFinance Support**: Initial implementation for fetching historical data and latest quotes/candles from Yahoo Finance.

## Project Structure

- `src/core/`: Core logic, models, and abstract interfaces.
- `src/providers/`: Concrete data provider implementations (e.g., YFinance).
- `src/utils/`: Helper functions and utilities.
- `data/`: Local storage for Parquet files (ignored by git).

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
python3 scripts/backfill_data.py
```

Run the backtesting dashboard:
```bash
PYTHONPATH=src streamlit run src/app.py
```

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
