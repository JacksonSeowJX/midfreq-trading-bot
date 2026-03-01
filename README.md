# Mid-Frequency Market Data Service

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