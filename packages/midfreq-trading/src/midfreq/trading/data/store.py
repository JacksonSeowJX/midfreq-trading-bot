"""
The local candle store as LiveMarketDataService uses it: warm-up reads an instrument's last bars
from it, and stop() appends the session's bars to it.

Warm-up never asks the broker's metered history. The store labels intraday bars with the
exchange-local close time tagged as UTC and daily bars with local midnight: warm-up converts both
to true UTC closes, as streamed bars carry, and the session's bars are written back in the store's
own convention. The store is DataStorage() unless one is given, i.e. the data/ folder of
MIDFREQ_ROOT (midfreq.core.paths). The rules, with the original engine's lines they reproduce
("LE"), are the "Warm-up and the store" list in the docstring of midfreq.trading.data.service.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from midfreq.core.common.clock import Clock
from midfreq.core.model.data import BAR_COLUMNS, Bar, BarSpecification, BarType
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.venues.spec import VenueRegistry, VenueSpec
from midfreq.data.storage import DataStorage

log = logging.getLogger("midfreq.trading.marketdata")

__all__ = ["DEFAULT_WARM_UP", "CandleStore", "PersistReport"]

DEFAULT_WARM_UP = 60            # LE 373: _warm_up(candles=60)

# LE 381-382: the window the original engine wanted `count` cached candles inside, else it went
# to the broker. Used here only to warn that the cache is stale.
_WARM_UP_LOOKBACK = {"1m": timedelta(days=3), "5m": timedelta(days=10),
                     "1h": timedelta(days=30), "1d": timedelta(days=200)}


@dataclass(frozen=True)
class PersistReport:
    """What `stop()` wrote to the store."""
    bars_written: int
    instruments_written: int
    failed: int
    errors: Tuple[str, ...] = ()


class CandleStore:
    """The service's bar store: reads for warm-up and the session's writes at stop.

    store: the DataStorage to use; None opens DataStorage() (the data/ folder of MIDFREQ_ROOT) the
        first time it is needed.
    venues: the venue registry (sessions and local time zones, and Moomoo's market codes for the
        store's file keys).
    clock: the node's clock: warm-up keeps only bars closed by now.
    """

    def __init__(self, store: Optional[DataStorage], venues: VenueRegistry, clock: Clock):
        self._store = store
        self._venues = venues
        self._clock = clock

    # ── warm-up from the store ───────────────────────────────────────────────────────────────

    def warm_up(self, bar_type: BarType, count: int) -> List[Bar]:
        """The last `count` closed bars of `bar_type` from the local store, oldest first, with
        ts_event the true UTC close (the store's local-as-UTC labels converted). Never asks the
        broker (LE 384-392)."""
        iid = bar_type.instrument_id
        tf = bar_type.spec.legacy_timeframe
        if tf is None:
            log.warning("[warmup] %s: the store has no %s files", iid, bar_type.spec.interval)
            return []
        try:
            store = self._get_store()
            key = self._store_key(iid)
            # DataStorage creates a symbol's folder when asked for its path; look first so a miss
            # leaves no empty folder behind.
            if not (Path(store.base_path) / key.upper().replace(".", "_") / f"{tf}.parquet").exists():
                log.warning("[warmup] %s: no history available in the store", iid)
                return []
            df = store.load_data(key, tf)
        except Exception as exc:                           # noqa: BLE001 - a bad file must not stop the node
            log.warning("[warmup] %s: store not readable (%s)", iid, exc)
            return []
        if df is None or df.empty:
            log.warning("[warmup] %s: no history available in the store", iid)
            return []
        spec = self._venues.get(iid.venue)
        closes = self._true_utc(pd.DatetimeIndex(df.index), spec, bar_type.spec)
        keep = ~closes.isna()
        if not keep.all():
            log.warning("[warmup] %s: dropped %d rows whose local time does not exist", iid, int((~keep).sum()))
        values = df.loc[keep, BAR_COLUMNS].astype(float).to_numpy()
        ts = closes[keep].asi8
        # One bar per close (the last row wins, as DataStorage.append_data keeps), oldest first,
        # closed by now.
        frame = pd.DataFrame(values, columns=BAR_COLUMNS, index=ts)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        now = self._clock.timestamp_ns()
        frame = frame[frame.index <= now]
        tail = frame.tail(int(count)) if count > 0 else frame.iloc[0:0]
        bars = [Bar(bar_type, float(o), float(h), float(lo), float(c), float(v), int(t), int(t))
                for t, (o, h, lo, c, v) in zip(tail.index, tail.to_numpy())]
        window = _WARM_UP_LOOKBACK.get(tf, timedelta(days=5))
        recent = int((frame.index >= now - int(window.total_seconds() * 1e9)).sum())
        if len(bars) < count:
            log.warning("[warmup] %s: only %d of %d bars in the store", iid, len(bars), count)
        elif recent < count:
            log.warning("[warmup] %s: the store is stale: only %d bars from the last %d days (want %d); "
                        "the original engine would have asked the broker, this one never does",
                        iid, recent, window.days, count)
        return bars

    @staticmethod
    def _true_utc(index: pd.DatetimeIndex, venue: VenueSpec, spec: BarSpecification) -> pd.DatetimeIndex:
        """The store's labels as true UTC closes (midfreq.data.persistence.legacy does the same)."""
        if len(index) == 0:
            return pd.DatetimeIndex([], tz="UTC")
        if spec.is_intraday:
            return venue.localize(index)                   # local close tagged UTC -> UTC
        naive = index.tz_localize(None) if index.tz is not None else index
        return venue.daily_close(naive)                    # local midnight of the date -> session close

    # ── the store ────────────────────────────────────────────────────────────────────────────

    def _get_store(self) -> DataStorage:
        if self._store is None:
            self._store = DataStorage()                    # data/ of MIDFREQ_ROOT (midfreq.core.paths)
        return self._store

    def _store_key(self, iid: InstrumentId) -> str:
        """The store's file key for an instrument: the original's `symbol.replace('.', '_')` of
        its Moomoo-style symbol ('US.AAPL' -> 'US_AAPL'; LE 401, 661), the symbol being what
        midfreq.data.persistence.legacy.LegacyDataReader.legacy_symbol gives. A venue Moomoo
        does not serve is keyed '<VENUE>_<SYMBOL>'."""
        try:
            market = self._venues.get(iid.venue).connector_options("moomoo")["market"]
            symbol = f"{market}.{iid.symbol}"
        except (KeyError, ValueError):
            symbol = f"{iid.venue}.{iid.symbol}"
        return symbol.replace(".", "_")

    def persist(self, session_bars: Dict[BarType, List[Bar]]) -> PersistReport:
        """Append the session's bars to the store, as LE 643-667 did: one frame per instrument
        with float columns open..volume indexed by the local close tagged UTC ('timestamp')."""
        if not session_bars:
            return PersistReport(0, 0, 0)
        try:
            store = self._get_store()
        except Exception as exc:                           # noqa: BLE001 - LE 651-653
            log.warning("[cache] not written (%s)", exc)
            return PersistReport(0, 0, len(session_bars), (str(exc),))
        written = instruments = failed = 0
        errors: List[str] = []
        for bt, bars in session_bars.items():
            if not bars:
                continue
            try:
                tf = bt.spec.legacy_timeframe
                if tf is None:
                    raise ValueError(f"the store has no {bt.spec.interval} files")
                venue = self._venues.get(bt.instrument_id.venue)
                index = venue.to_legacy_index([b.ts_event for b in bars], bt.spec)
                df = pd.DataFrame(
                    {"open": [b.open for b in bars], "high": [b.high for b in bars],
                     "low": [b.low for b in bars], "close": [b.close for b in bars],
                     "volume": [b.volume for b in bars]},
                    index=pd.DatetimeIndex(index, name="timestamp")).astype(float)
                store.append_data(df, self._store_key(bt.instrument_id), tf)
                written += len(bars)
                instruments += 1
            except Exception as exc:                       # noqa: BLE001 - LE 663-664: count and go on
                failed += 1
                errors.append(f"{bt}: {exc}")
                log.warning("[cache] %s not written: %s", bt, exc)
        log.info("[cache] wrote %d bars for %d instruments%s", written, instruments,
                 f", {failed} failed" if failed else "")
        return PersistReport(written, instruments, failed, tuple(errors))
