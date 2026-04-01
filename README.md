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

Week 7
- https://studio.youtube.com/video/G-v-z53wfnk/edit
Week 9
- https://www.youtube.com/watch?v=GHJNfMNTLsA
Week 11
-
Week 13
-
Week 15
-

## Mid-Frequency Market Data Service

This repository provides a modular and extensible framework for fetching, standardizing, and storing historical and live market data for US stocks. It is part of the "Development of a Modular Quantitative Trading Framework for Mid-Frequency Stock Trading" project.

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

Run the demonstration script to fetch and store data for several stocks:
```bash
python3 src/main.py
```

## Data Schema

The system standardizes all OHLCV data to the following schema:
- `timestamp` (UTC, index)
- `open`
- `high`
- `low`
- `close`
- `volume`
