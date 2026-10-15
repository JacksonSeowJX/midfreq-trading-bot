"""Synthetic market data written in the ORIGINAL cache layout (data/<MKT>_<CODE>/<tf>.parquet,
exchange-local times tagged as UTC) with core's own DataStorage, so the legacy Backtester and the
new engine read exactly the same files."""
from __future__ import annotations

import contextlib
import io
from typing import List, Optional

import numpy as np
import pandas as pd

from core.storage import DataStorage

HK_CLOSES = ["10:30", "11:30", "12:00", "14:00", "15:00", "16:00"]
US_CLOSES = ["10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:00"]

HK_SYMBOLS = ["HK.00700", "HK.00005", "HK.09988", "HK.00941"]
US_SYMBOLS = ["US.AAPL", "US.JPM", "US.MSFT", "US.XOM"]


def write_universe(data_dir, symbols: List[str], closes: Optional[List[str]], start: str, days: int,
                   timeframe: str, seed: int) -> None:
    """Prices with a cycle (so mean-reversion and crossover rules fire) plus a random walk (so the
    symbols diverge and rankings change). `closes=None` writes daily bars at local midnight."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=days)
    stamps = ([pd.Timestamp(f"{d.date()} {t}") for d in dates for t in closes] if closes else list(dates))
    index = pd.DatetimeIndex(stamps, tz="UTC", name="timestamp")
    n = len(index)
    storage = DataStorage(str(data_dir))
    with contextlib.redirect_stdout(io.StringIO()):
        for k, symbol in enumerate(symbols):
            t = np.arange(n)
            close = 60 + 15 * k + 6 * np.sin(t / (8 + 3 * k)) + 3 * np.sin(t / (29 + k)) \
                + np.cumsum(rng.normal(0, 0.9, n))
            close = np.maximum(close, 5.0)
            df = pd.DataFrame({
                "open": close * (1 + rng.normal(0, 0.002, n)), "high": close * 1.004,
                "low": close * 0.996, "close": close,
                "volume": rng.integers(1000, 5000, n).astype(float),
            }, index=index)
            storage.save_data(df, symbol.replace(".", "_"), timeframe)
