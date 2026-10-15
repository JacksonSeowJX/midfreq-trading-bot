"""
The connector (adapter) pattern for data sources, after NautilusTrader's `adapters/`.

Every broker or exchange plugs in the same way. A `Connector` is the single object that knows one
source: its configuration, what it can do (`capabilities`), how it connects, and how it spells
symbols. It exposes two clients:

- an `InstrumentProvider` that returns the venue's trading rules for instruments (tick size,
  lot or size step, minimum notional, currency);
- a `DataClient` whose `request_bars(bar_type, start, end)` returns bars with `ts_event` = bar
  close in true UTC, whatever the source's own clock convention.

An `ExecutionClient` slot is defined for symmetry with Nautilus but no connector implements it in
this version: live orders still go through midfreq.live.order_gateway.

Everything source-specific (symbology, interval names, pagination, rate limiting, quotas, time
conversion) lives inside the connector, so the rest of the framework only ever sees InstrumentIds
and Bars.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Tuple, Type

import numpy as np
import pandas as pd

from midfreq.core.common.retry import RateLimitError, RetryPolicy, Throttle
from midfreq.core.common.timestamps import TimeLike, to_utc_timestamp
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.identifiers import ClientId, InstrumentId, Venue, as_instrument_id, as_venue
from midfreq.core.model.instruments import Instrument
from midfreq.core.venues.spec import VenueRegistry, VenueSpec, default_venues

log = logging.getLogger(__name__)

__all__ = [
    "Connector", "ConnectorCapabilities", "ConnectorConfig", "ConnectorError", "DataClient",
    "ExecutionClient", "InstrumentProvider", "QuotaExceededError", "RateLimitError",
    "UnsupportedIntervalError", "UnsupportedVenueError", "bars_from_frame",
]


# ── errors ───────────────────────────────────────────────────────────

class ConnectorError(Exception):
    """A data source returned an error the connector could not recover from."""


class UnsupportedIntervalError(ConnectorError, ValueError):
    """The connector cannot provide bars of the requested interval."""


class UnsupportedVenueError(ConnectorError, ValueError):
    """The connector is not configured for the requested venue."""


class QuotaExceededError(ConnectorError):
    """The source's usage quota is used up (e.g. Moomoo's 30-day history-kline quota). Retrying
    will not help, so this is not a rate-limit error."""


# ── configuration and capabilities ───────────────────────────────────

@dataclass
class ConnectorConfig:
    """Settings every connector shares. Connector-specific settings are added by subclassing."""
    max_retries: int = 5
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    min_request_interval: float = 0.0     # seconds between requests (client-side rate limit)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ConnectorConfig":
        d = dict(d or {})
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(d) - names
        if unknown:
            raise ValueError(f"unknown {cls.__name__} settings {sorted(unknown)}; allowed: {sorted(names)}")
        return cls(**d)


@dataclass(frozen=True)
class ConnectorCapabilities:
    intervals: Tuple[str, ...]                                 # e.g. ('1-MINUTE', '1-HOUR', '1-DAY')
    max_history: Dict[str, Optional[timedelta]] = field(default_factory=dict)  # per interval; None = no known limit
    max_bars_per_request: Optional[int] = None
    historical_bars: bool = True
    historical_trades: bool = False
    historical_quotes: bool = False
    live_data: bool = False
    execution: bool = False

    def supports(self, spec: BarSpecification) -> bool:
        return spec.interval in self.intervals and spec.price_type.value == "LAST"

    def check(self, spec: BarSpecification, connector: str) -> None:
        if not self.supports(spec):
            raise UnsupportedIntervalError(
                f"{connector} cannot provide {spec} bars; supported intervals: "
                f"{', '.join(self.intervals)} (LAST price only)")

    def history_limit(self, spec: BarSpecification) -> Optional[timedelta]:
        return self.max_history.get(spec.interval)


# ── clients ──────────────────────────────────────────────────────────

class InstrumentProvider(ABC):
    """Loads instrument definitions (trading rules) from the connector's source, with a cache."""

    def __init__(self, connector: "Connector"):
        self.connector = connector
        self._cache: Dict[InstrumentId, Instrument] = {}

    def load(self, instrument_ids: Iterable["str | InstrumentId"]) -> List[Instrument]:
        ids = [as_instrument_id(i) for i in instrument_ids]
        for iid in ids:
            self.connector.venue_spec(iid.venue)   # fail early on a venue we do not serve
        missing = [i for i in ids if i not in self._cache]
        if missing:
            for inst in self._load(missing):
                self._cache[inst.id] = inst
        not_found = [str(i) for i in ids if i not in self._cache]
        if not_found:
            raise ConnectorError(f"{self.connector.name}: no instrument definition for {not_found}")
        return [self._cache[i] for i in ids]

    def load_all(self, venue: "str | Venue") -> List[Instrument]:
        """Every instrument the source lists on `venue` (where the source can enumerate them)."""
        spec = self.connector.venue_spec(venue)
        instruments = self._load_all(spec)
        for inst in instruments:
            self._cache[inst.id] = inst
        return instruments

    def find(self, instrument_id: "str | InstrumentId") -> Optional[Instrument]:
        return self._cache.get(as_instrument_id(instrument_id))

    @abstractmethod
    def _load(self, instrument_ids: List[InstrumentId]) -> List[Instrument]:
        ...

    def _load_all(self, spec: VenueSpec) -> List[Instrument]:
        raise NotImplementedError(f"{self.connector.name} cannot list every instrument on {spec.venue}")


class DataClient(ABC):
    """Historical market data from one connector. Subclasses implement `_request_bars`; this base
    class validates the request and normalises whatever comes back."""

    def __init__(self, connector: "Connector", now_ns: Callable[[], int] = time.time_ns):
        self.connector = connector
        self._now_ns = now_ns

    @property
    def client_id(self) -> ClientId:
        return ClientId(self.connector.name)

    def request_bars(self, bar_type: BarType, start: TimeLike, end: Optional[TimeLike] = None) -> List[Bar]:
        """
        Bars of `bar_type` whose close time (ts_event) lies in [start, end], oldest first, with no
        duplicates and no bar that has not closed yet. Naive datetimes are read as UTC.
        """
        spec = self.connector.venue_spec(bar_type.instrument_id.venue)
        capabilities = self.connector.capabilities_for(spec)
        capabilities.check(bar_type.spec, self.connector.name)
        start_ts = to_utc_timestamp(start)
        end_ts = to_utc_timestamp(end) if end is not None else pd.Timestamp(self._now_ns(), unit="ns", tz="UTC")
        if end_ts < start_ts:
            raise ValueError(f"end {end_ts} is before start {start_ts}")
        limit = capabilities.history_limit(bar_type.spec)
        if limit is not None:
            earliest = pd.Timestamp(self._now_ns(), unit="ns", tz="UTC") - limit
            if start_ts < earliest:
                log.warning("%s only serves the last %d days of %s bars; starting at %s instead of %s",
                            self.connector.name, limit.days, bar_type.spec.interval, earliest, start_ts)
                start_ts = earliest
        bars = self._request_bars(bar_type, spec, start_ts, end_ts)
        return self._normalise(bars, start_ts.value, end_ts.value)

    def request_trades(self, instrument_id, start, end=None):
        raise NotImplementedError(f"{self.connector.name} does not provide historical trades")

    def request_quotes(self, instrument_id, start, end=None):
        raise NotImplementedError(f"{self.connector.name} does not provide historical quotes")

    def subscribe_bars(self, bar_type, handler):
        raise NotImplementedError("live data subscriptions are reserved for a later version")

    @abstractmethod
    def _request_bars(self, bar_type: BarType, venue: VenueSpec,
                      start: pd.Timestamp, end: pd.Timestamp) -> List[Bar]:
        ...

    def _normalise(self, bars: List[Bar], start_ns: int, end_ns: int) -> List[Bar]:
        now = self._now_ns()
        by_ts: Dict[int, Bar] = {}
        for bar in bars:
            if start_ns <= bar.ts_event <= end_ns and bar.ts_event <= now:
                by_ts[bar.ts_event] = bar   # pages can overlap at their edges; keep one bar per close
        return [by_ts[t] for t in sorted(by_ts)]


class ExecutionClient(ABC):
    """Reserved: order routing to a live venue. Not implemented by any connector in this version;
    live trading stays on midfreq.live.live_engine / midfreq.live.order_gateway until the forward test ends."""

    @abstractmethod
    def submit_order(self, order):
        ...

    @abstractmethod
    def cancel_order(self, order):
        ...


@dataclass(frozen=True)
class AccountSnapshot:
    """An account as the venue reports it. Amounts in `currency`."""
    currency: str
    total_assets: float
    cash: float
    market_value: float
    ts: pd.Timestamp                      # when the venue was asked, UTC


@dataclass(frozen=True)
class PositionReport:
    """One holding as the venue reports it. `symbol` is the venue's own symbol ('US.AAPL',
    'BTC/USDT'); `instrument_id` the framework's, when the connector can map it."""
    symbol: str
    quantity: float
    average_price: float
    market_price: Optional[float]
    market_value: Optional[float]
    unrealized_pnl: Optional[float]
    instrument_id: Optional[str] = None


@dataclass(frozen=True)
class OrderReport:
    """One order as the venue reports it; `status` is the venue's own word (FILLED_ALL, closed...)."""
    order_id: str
    symbol: str
    side: str                             # BUY or SELL
    quantity: float
    filled_quantity: float
    price: Optional[float]
    average_fill_price: Optional[float]
    status: str
    created: pd.Timestamp                 # UTC
    updated: Optional[pd.Timestamp] = None


class AccountUnavailableError(ConnectorError):
    """The venue's account cannot be read now (gateway down, no credentials, network)."""


class AccountClient(ABC):
    """Read-only view of one account at a venue: what the broker or exchange says the account holds.

    It never places, changes or cancels anything (that is ExecutionClient's job). The dashboard
    shows it next to the bot's own record so that the two can be reconciled. Credentials, where a
    venue needs them, come from the environment (midfreq.core.connections.get_secret); a
    read-only key is enough and is what should be used.
    """

    @abstractmethod
    def available(self) -> Tuple[bool, str]:
        """(True, '') if the account can be read now; otherwise (False, a short reason).
        Must return quickly (seconds) and never hang: callers probe before reading."""

    @abstractmethod
    def snapshot(self) -> AccountSnapshot:
        ...

    @abstractmethod
    def positions(self) -> List[PositionReport]:
        ...

    @abstractmethod
    def orders(self, days: int = 14) -> List[OrderReport]:
        """Orders created in the last `days` days, newest first."""

    def close(self) -> None:
        """Release any session with the venue."""


# ── the connector ────────────────────────────────────────────────────

class Connector(ABC):
    name: ClassVar[str] = ""
    config_class: ClassVar[Type[ConnectorConfig]] = ConnectorConfig

    def __init__(self, config: "ConnectorConfig | Dict[str, Any] | None" = None,
                 venues: Optional[VenueRegistry] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 now_ns: Callable[[], int] = time.time_ns):
        if config is None or isinstance(config, dict):
            config = self.config_class.from_dict(config)
        self.config = config
        self.venues = venues or default_venues()
        self.retry = RetryPolicy(config.max_retries, config.backoff_base, config.backoff_max, sleep)
        self.throttle = Throttle(config.min_request_interval, sleep=sleep)
        self._now_ns = now_ns
        self._instrument_provider: Optional[InstrumentProvider] = None
        self._data_client: Optional[DataClient] = None
        self._connected = False

    # Subclasses provide these.
    @property
    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        ...

    def capabilities_for(self, venue: VenueSpec) -> ConnectorCapabilities:
        """Capabilities on one venue. The same as `capabilities` except for connectors that front
        several exchanges with different abilities (ccxt)."""
        return self.capabilities

    @abstractmethod
    def _create_instrument_provider(self) -> InstrumentProvider:
        ...

    @abstractmethod
    def _create_data_client(self) -> DataClient:
        ...

    @abstractmethod
    def to_native(self, instrument_id: "str | InstrumentId") -> str:
        """InstrumentId -> the source's own symbol ('00700.XHKG' -> 'HK.00700' for Moomoo)."""

    @abstractmethod
    def from_native(self, native: str, venue: "str | Venue | None" = None) -> InstrumentId:
        """The source's symbol -> InstrumentId. `venue` is needed where the native symbol alone
        does not say (yfinance 'AAPL' could be XNAS or XNYS)."""

    def _connect(self) -> None:
        """Open the session with the source (OpenD, TWS, an HTTP session). Lazy by default."""

    def _close(self) -> None:
        ...

    # Shared behaviour.
    @property
    def client_id(self) -> ClientId:
        return ClientId(self.name)

    @property
    def instrument_provider(self) -> InstrumentProvider:
        if self._instrument_provider is None:
            self._instrument_provider = self._create_instrument_provider()
        return self._instrument_provider

    @property
    def data_client(self) -> DataClient:
        if self._data_client is None:
            self._data_client = self._create_data_client()
        return self._data_client

    @property
    def execution_client(self) -> Optional[ExecutionClient]:
        """Always None in this version (see ExecutionClient)."""
        return None

    def account_client(self, **options: Any) -> AccountClient:
        """A read-only AccountClient for one account at this source. `options` are the
        connector's own (Moomoo: market='US'; ccxt: exchange='okx'), taken from the `accounts`
        section of config/connections.yaml. Connectors without one raise ConnectorError."""
        raise ConnectorError(f"connector {self.name!r} has no account client")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> "Connector":
        if not self._connected:
            self._connect()
            self._connected = True
        return self

    def close(self) -> None:
        if self._connected:
            self._close()
            self._connected = False

    def __enter__(self) -> "Connector":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def venue_spec(self, venue: "str | Venue") -> VenueSpec:
        spec = self.venues.get(as_venue(venue))
        if self.name not in spec.connectors:
            raise UnsupportedVenueError(
                f"{self.name} is not configured for venue {spec.venue}; venues it serves: "
                f"{[str(s.venue) for s in self.venues.served_by(self.name)]}")
        return spec

    def served_venues(self) -> List[Venue]:
        return [s.venue for s in self.venues.served_by(self.name)]

    def call(self, fn: Callable[[], Any], what: str) -> Any:
        """One request to the source: throttled, and retried with backoff on rate limits."""
        def attempt():
            self.throttle.wait()
            return fn()
        return self.retry.call(attempt, self.is_rate_limit, what=f"{self.name} {what}")

    def is_rate_limit(self, exc: BaseException) -> bool:
        """Whether `exc` means "slow down". Connectors extend this with their own signals."""
        return isinstance(exc, RateLimitError)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(venues={[str(v) for v in self.served_venues()]})"


# ── helpers for connectors ───────────────────────────────────────────

def bars_from_frame(frame: pd.DataFrame, bar_type: BarType, closes_utc: pd.DatetimeIndex,
                    ts_init: Optional[int] = None) -> List[Bar]:
    """Bars from an OHLCV frame and the matching bar close times. Rows whose close time could not
    be determined (NaT, e.g. a wall-clock time skipped by daylight saving) are dropped."""
    if len(frame) == 0:
        return []
    closes = pd.DatetimeIndex(closes_utc)
    keep = ~closes.isna()
    ts = closes[keep].asi8
    cols = [frame[c].to_numpy(dtype=float)[keep] for c in ("open", "high", "low", "close", "volume")]
    return [
        Bar(bar_type, float(o), float(h), float(lo), float(c), float(v), int(t),
            int(t) if ts_init is None else ts_init)
        for o, h, lo, c, v, t in zip(*cols, ts)
    ]


def utc_dates_or_local(index: pd.DatetimeIndex, venue: VenueSpec) -> pd.DatetimeIndex:
    """
    Calendar dates of daily bars as naive midnights. Sources disagree on how they stamp a day: a
    naive date that the provider then tagged as UTC midnight (yfinance download, Moomoo), or the
    exchange's local midnight converted to UTC (yfinance history, IBKR). A UTC-midnight stamp is
    read as the date itself; anything else is converted to the venue's clock first.
    """
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        return idx.normalize()
    utc = idx.tz_convert("UTC")
    if len(utc) and bool(np.all(utc == utc.normalize())):
        return utc.tz_localize(None)
    return utc.tz_convert(venue.timezone).tz_localize(None).normalize()


def time_windows(start: pd.Timestamp, end: pd.Timestamp, step: timedelta) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """[start, end] cut into consecutive windows no longer than `step`, for sources that cap how
    much history one request may cover."""
    windows = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        windows.append((cursor, nxt))
        cursor = nxt
    if not windows:
        windows.append((start, end))
    return windows
