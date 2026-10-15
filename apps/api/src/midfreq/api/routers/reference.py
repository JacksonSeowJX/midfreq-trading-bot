"""
Reference data and the service's health: the strategy registry, the declared universes
(config/symbols.json) and cached candles (data/, through midfreq.data.storage.DataStorage).
"""
import re
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from midfreq.api import __version__
from midfreq.api import schemas as s
from midfreq.api.deps import get_record_store, get_settings
from midfreq.api.labels import UNIVERSE_LABELS
from midfreq.api.settings import Settings
from midfreq.api.store import RecordStore
from midfreq.core.config import ConfigLoader
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

router = APIRouter()

# A symbol as the cache names it (HK.00700, US.AAPL, US.BRK.B, AAPL): no path separators.
SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OHLC = ["open", "high", "low", "close"]


def _number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.get("/api/health", response_model=s.Health, tags=["service"])
def health(settings: Settings = Depends(get_settings), store: RecordStore = Depends(get_record_store)):
    return s.Health(version=__version__, sources={
        "results": settings.results_dir.is_dir(),
        "live_sessions": store.available(),
        "data_cache": settings.data_dir.is_dir(),
        # can this deployment read a broker gateway itself? false when accounts are off, and on the
        # hosted dashboard, which only shows the snapshots the VM publishes
        "accounts": settings.accounts_enabled and not settings.snapshot_accounts_only,
    })


@router.get("/api/strategies", response_model=List[s.StrategyInfo], tags=["reference"])
def strategies():
    return [s.StrategyInfo(name=name, params=[
                s.StrategyParam(name=key, label=str(meta.get("label") or key), default=meta.get("default"),
                                min=_number(meta.get("min")), max=_number(meta.get("max")),
                                step=_number(meta.get("step")))
                for key, meta in (info.get("params") or {}).items()])
            for name, info in STRATEGY_REGISTRY.items()]


@router.get("/api/universes", response_model=List[s.Universe], tags=["reference"])
def universes(settings: Settings = Depends(get_settings)):
    path = settings.config_dir / "symbols.json"
    if not path.is_file():
        return []
    config = ConfigLoader(str(path))
    out = []
    for name in config.list_universes():
        desc = config.describe_universe(name)
        out.append(s.Universe(name=name, label=UNIVERSE_LABELS.get(name, name), market=desc.get("market"),
                              symbols=len(config.get_universe(name)),
                              description=str(desc.get("description") or "")))
    return out


def _bound(value: Optional[str], name: str) -> Optional[pd.Timestamp]:
    if value is None or not value.strip():
        return None
    try:
        ts = pd.Timestamp(value.strip())
    except (TypeError, ValueError):
        ts = pd.NaT
    if ts is pd.NaT:
        raise HTTPException(status_code=422, detail=f"{name}: not a date or time: {value!r}")
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@router.get("/api/candles", response_model=List[s.Candle], tags=["reference"],
            responses={404: {"description": "no cached candles for this symbol"}})
def candles(symbol: str, timeframe: str = Query("1h", pattern="^(1m|5m|1h|4h|1d)$"),
            start: Optional[str] = None, end: Optional[str] = None,
            limit: int = Query(5000, ge=1, le=100000),
            settings: Settings = Depends(get_settings)):
    # The most recent `limit` candles within [start, end]. A time without a zone is UTC; an
    # `end` given as a date alone includes that whole day. (No docstring: it would change the
    # published contract's description.)
    if not SYMBOL.match(symbol):
        raise HTTPException(status_code=422, detail=f"not a symbol: {symbol!r}")
    t0, t1 = _bound(start, "start"), _bound(end, "end")
    if t1 is not None and DATE_ONLY.match(end.strip()):
        t1 = t1 + pd.Timedelta(days=1) - pd.Timedelta(1, "ns")
    # The cache layout is DataStorage's: data/<SYMBOL with . as _>/<timeframe>.parquet. Checked
    # first because DataStorage.load_data creates the symbol's folder when it is missing.
    if not (settings.data_dir / symbol.upper().replace(".", "_") / f"{timeframe}.parquet").is_file():
        raise HTTPException(status_code=404, detail=f"no cached {timeframe} candles for {symbol}")
    df = DataStorage(str(settings.data_dir)).load_data(symbol, timeframe)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        df.index = pd.to_datetime(df.index, errors="coerce", utc=True)
    idx = df.index
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    if not set(OHLC) <= set(df.columns):
        raise HTTPException(status_code=404, detail=f"cached {timeframe} file for {symbol} has no OHLC columns")
    df = df[df.index.notna()].sort_index().dropna(subset=OHLC)
    if t0 is not None:
        df = df[df.index >= t0]
    if t1 is not None:
        df = df[df.index <= t1]
    df = df.tail(limit)

    volume = (df["volume"].astype(float).fillna(0.0) if "volume" in df.columns
              else pd.Series(0.0, index=df.index)).tolist()
    return [{"t": t.to_pydatetime(), "o": o, "h": h, "l": lo, "c": c, "v": v}
            for t, o, h, lo, c, v in zip(df.index, *(df[k].astype(float).tolist() for k in OHLC), volume)]
