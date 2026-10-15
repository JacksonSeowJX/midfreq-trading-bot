"""
mft: a venue-based trading framework modelled on NautilusTrader.

It sits alongside the original `core` package rather than replacing it: `core` still drives the
live paper-trading engine and reproduces every reported result, while `mft` adds explicit Venue,
Instrument, Bar, Order and Signal objects, connectors that know which exchange and which data
source a bar came from, a Parquet data catalog, and an event-driven backtest engine that can run
the original strategies unchanged through `mft.legacy`.

Layout (names follow NautilusTrader where a concept matches):
    model/        identifiers, instruments, bars, orders, events, signals, fee models
    venues/       VenueSpec and the registry loaded from config/venues.json
    adapters/     connectors (Moomoo, IBKR, yfinance, Binance, ccxt) and their registry
    persistence/  ParquetDataCatalog, read-only reader for the legacy data/ cache, migration
    data/         the `python -m mft.data` command line
    trading/      the Strategy API
    backtest/     BacktestEngine and its components
    strategies/   native strategies written against the new API
    legacy.py     runs any core.strategy class inside the new engine
"""

__version__ = "0.1.0"
