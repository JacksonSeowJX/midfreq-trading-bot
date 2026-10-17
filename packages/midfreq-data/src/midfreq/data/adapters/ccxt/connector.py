"""
Generic crypto-exchange connector through the ccxt library (Coinbase, Kraken, OKX, Bybit, ...).

ccxt gives one interface to over a hundred exchanges; this connector maps it onto the framework's:

- Symbology: ccxt's unified symbols ('BTC/USDT') contain a slash, which cannot appear in a catalog
  path, so instrument ids use either a dash ('BTC-USD.COINBASE', which is also Coinbase's and
  OKX's own product id) or, per venue, the exchange's own market id ('BTCUSDT.BINANCE'), set by
  `symbology` in config/venues.json.
- Intervals: whatever the exchange lists in `exchange.timeframes`, so capabilities are per venue.
- Pagination: `fetch_ohlcv(since=..., limit=...)` pages forward by open time. Some exchanges cap
  history (Kraken returns only the latest 720 bars); a gap at the start of the requested window is
  logged rather than hidden.
- Rate limits: ccxt's own `enableRateLimit` spaces requests; RateLimitExceeded and DDoSProtection
  are retried with backoff.
- Accounts: `account_client(exchange='okx', secrets=[...])` is a read-only view of an account,
  with a key from the environment (see midfreq.data.adapters.ccxt.account).
- Trading: `execution_client(exchange='okx')` routes orders to one account, paper (the exchange's
  sandbox) unless connections.yaml says live (midfreq.data.adapters.ccxt.execution).
- Live data: the data client streams bars and quotes through ccxt.pro, or polls without it
  (midfreq.data.adapters.ccxt.streaming).

ccxt is optional: the package imports without it, and creating a real exchange object without it
raises a clear error. Tests inject a fake exchange object.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd

from midfreq.data.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, ConnectorError, DataClient,
    InstrumentProvider, RateLimitError,
)
from midfreq.data.adapters.ccxt.account import CcxtAccountClient
from midfreq.data.adapters.ccxt.execution import CcxtExecutionClient
from midfreq.data.adapters.ccxt.streaming import CcxtStreamingMixin
from midfreq.data.connection_settings import connection_settings
from midfreq.core.common.timestamps import ms_to_ns
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.enums import BarAggregation
from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id, as_venue
from midfreq.core.model.instruments import CurrencyPair, Instrument
from midfreq.core.venues.spec import VenueSpec

try:  # optional dependency
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover - exercised only where ccxt is absent
    ccxt = None

log = logging.getLogger(__name__)

_UNITS = {BarAggregation.SECOND: "s", BarAggregation.MINUTE: "m", BarAggregation.HOUR: "h",
          BarAggregation.DAY: "d", BarAggregation.WEEK: "w"}
_AGG_BY_UNIT = {v: k for k, v in _UNITS.items()}
_TICK_SIZE = 4         # ccxt.TICK_SIZE
_DECIMAL_PLACES = 2    # ccxt.DECIMAL_PLACES


def ccxt_available() -> bool:
    return ccxt is not None


def timeframe_for(spec: BarSpecification) -> str:
    return f"{spec.step}{_UNITS[spec.aggregation]}"


def interval_for(timeframe: str) -> Optional[str]:
    """'1h' -> '1-HOUR'; None for timeframes the model has no aggregation for (months)."""
    unit = timeframe[-1]
    if unit not in _AGG_BY_UNIT or not timeframe[:-1].isdigit():
        return None
    return f"{int(timeframe[:-1])}-{_AGG_BY_UNIT[unit].value}"


@dataclass
class CcxtConnectorConfig(ConnectorConfig):
    options: Dict[str, Any] = field(default_factory=dict)   # passed to the ccxt exchange constructor
    default_bars_per_request: int = 500
    bars_per_request: Optional[int] = None   # override the venue's max_bars_per_request
    # live streams (midfreq.data.adapters.ccxt.streaming)
    stream_mode: str = "auto"                # auto (watch with ccxt.pro, else poll) | watch | poll
    quote_source: str = "auto"               # auto | ticker | book
    stream_poll_interval: float = 0.0        # seconds between polls; 0 = by bar interval (quotes: 2 s)
    stream_reconnect_delay: float = 1.0      # first wait after a stream error, doubled up to the max
    stream_max_reconnect_delay: float = 30.0


class CcxtConnector(Connector):
    name = "ccxt"
    config_class = CcxtConnectorConfig

    def __init__(self, config=None, venues=None, exchanges: Optional[Dict[str, Any]] = None,
                 account_exchange_factory: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                 stream_exchanges: Optional[Dict[str, Any]] = None,
                 execution_exchange_factory: Optional[Callable[[str, Dict[str, Any]], Any]] = None, **kwargs):
        """`exchanges` maps a venue ('KRAKEN') to a ready ccxt exchange object; by default one is
        created per venue from its `exchange_id`. `account_exchange_factory(exchange_id, config)`
        makes the authenticated exchange objects of account clients (ccxt's class by default;
        tests pass a fake). `stream_exchanges` maps a venue to a ready ccxt.pro exchange for the
        live streams, and `execution_exchange_factory(exchange_id, config)` makes the REST
        exchange objects of execution clients (both ccxt's by default)."""
        super().__init__(config, venues, **kwargs)
        self._exchanges: Dict[str, Any] = {str(k).upper(): v for k, v in (exchanges or {}).items()}
        self._markets_loaded: set = set()
        self._account_exchange_factory = account_exchange_factory
        self._stream_exchanges: Dict[str, Any] = {str(k).upper(): v for k, v in (stream_exchanges or {}).items()}
        self._execution_exchange_factory = execution_exchange_factory

    @property
    def capabilities(self) -> ConnectorCapabilities:
        # Without a venue, the intervals every model aggregation can express; per-venue
        # capabilities (capabilities_for) narrow this to what the exchange lists.
        return ConnectorCapabilities(intervals=("1-MINUTE", "5-MINUTE", "15-MINUTE", "30-MINUTE",
                                                "1-HOUR", "4-HOUR", "1-DAY", "1-WEEK"),
                                     live_data=ccxt_available(), execution=ccxt_available())

    def capabilities_for(self, venue: VenueSpec) -> ConnectorCapabilities:
        ex = self.exchange(venue.venue)
        intervals = tuple(i for i in (interval_for(tf) for tf in getattr(ex, "timeframes", {}) or {}) if i)
        return ConnectorCapabilities(intervals=intervals, max_bars_per_request=self.bars_per_request(venue),
                                     live_data=ccxt_available(), execution=ccxt_available())

    def bars_per_request(self, venue: VenueSpec) -> int:
        if self.config.bars_per_request:
            return int(self.config.bars_per_request)
        return int(venue.connector_options(self.name).get("max_bars_per_request",
                                                          self.config.default_bars_per_request))

    def exchange(self, venue: "str | Venue"):
        key = str(as_venue(venue))
        if key not in self._exchanges:
            exchange_id = self.exchange_id(key)
            if ccxt is None:
                raise ConnectorError("the ccxt connector needs the ccxt package: pip install ccxt")
            cls = getattr(ccxt, exchange_id, None)
            if cls is None:
                raise ConnectorError(f"ccxt has no exchange {exchange_id!r}")
            self._exchanges[key] = cls({"enableRateLimit": True, **self.config.options})
        return self._exchanges[key]

    def exchange_id(self, venue: "str | Venue") -> str:
        """The venue's `exchange_id` in config/venues.json, else `default_exchange` of the `ccxt`
        entry in config/connections.yaml."""
        opts = self.venue_spec(venue).connector_options(self.name)
        if opts.get("exchange_id"):
            return opts["exchange_id"]
        exchange_id = connection_settings("ccxt", {"default_exchange": None})["default_exchange"]
        if not exchange_id:
            raise ConnectorError(f"{venue}: no ccxt exchange_id in config/venues.json and no "
                                 "default_exchange in config/connections.yaml")
        return exchange_id

    def markets(self, venue: "str | Venue") -> Dict[str, Dict[str, Any]]:
        key = str(as_venue(venue))
        ex = self.exchange(key)
        if key not in self._markets_loaded:
            self.call(ex.load_markets, f"load_markets {key}")
            self._markets_loaded.add(key)
        return ex.markets

    def _symbology(self, venue) -> str:
        return self.venue_spec(venue).connector_options(self.name).get("symbology", "dash")

    def to_native(self, instrument_id) -> str:
        """InstrumentId -> ccxt unified symbol ('BTC-USD.COINBASE' -> 'BTC/USD')."""
        iid = as_instrument_id(instrument_id)
        sym = str(iid.symbol)
        if self._symbology(iid.venue) == "native_id":
            for m in self.markets(iid.venue).values():
                if m.get("id") == sym:
                    return m["symbol"]
            raise ConnectorError(f"{iid.venue} has no market with id {sym!r}")
        base, _, quote = sym.partition("-")
        if not quote:
            raise ValueError(f"{iid}: dash symbology expects '<BASE>-<QUOTE>'")
        return f"{base}/{quote}"

    def from_native(self, native: str, venue=None) -> InstrumentId:
        """ccxt unified symbol -> InstrumentId on `venue` (required: a unified symbol does not say
        which exchange)."""
        if venue is None:
            raise ValueError("ccxt symbols do not name their exchange; pass the venue")
        v = as_venue(venue)
        if self._symbology(v) == "native_id":
            return InstrumentId.of(self.markets(v)[native]["id"], v)
        return InstrumentId.of(native.replace("/", "-"), v)

    def is_rate_limit(self, exc: BaseException) -> bool:
        if isinstance(exc, RateLimitError):
            return True
        if ccxt is not None and isinstance(exc, (ccxt.RateLimitExceeded, ccxt.DDoSProtection)):
            return True
        return type(exc).__name__ in ("RateLimitExceeded", "DDoSProtection")

    def _create_instrument_provider(self) -> InstrumentProvider:
        return CcxtInstrumentProvider(self)

    def _create_data_client(self) -> DataClient:
        return CcxtDataClient(self, now_ns=self._now_ns)

    def execution_client(self, **options: Any) -> CcxtExecutionClient:
        """
        An ExecutionClient for one account at ccxt exchange `exchange` (default 'okx'), in the
        exchange's sandbox unless config/connections.yaml says `trading_env: live` for ccxt.
        Options: midfreq.data.adapters.ccxt.execution.OPTIONS (environment, secrets,
        exchange_options, updates, intervals, ...).
        """
        if ccxt is None and self._execution_exchange_factory is None:
            raise ConnectorError("trading through ccxt needs the ccxt package: pip install ccxt")
        return CcxtExecutionClient(self, options, exchange_factory=self._execution_exchange_factory,
                                   now_ns=self._now_ns)

    def close(self) -> None:
        if self._data_client is not None:
            self._data_client.stop_streams()
        super().close()

    def account_client(self, exchange: str, secrets: Sequence[str] = (), quote: Optional[str] = None,
                       exchange_options: Optional[Dict[str, Any]] = None,
                       timeout_ms: int = 5000) -> CcxtAccountClient:
        """
        A read-only account at ccxt exchange `exchange` ('okx'), whose key is in the environment
        variables named by `secrets`. Values are in `quote`, by default the currency of the venue
        in config/venues.json whose exchange_id is `exchange` (OKX: USDT); spot holdings get that
        venue's instrument ids where its symbology is 'dash'. `exchange_options` go to the ccxt
        constructor after this connector's own `options`.
        """
        exchange_id = str(exchange).lower()
        specs = [s for s in self.venues.served_by(self.name)
                 if str(s.connector_options(self.name).get("exchange_id", "")).lower() == exchange_id]
        spec = specs[0] if specs else None
        if quote is None:
            if spec is None:
                raise ConnectorError(f"no venue in config/venues.json has ccxt exchange_id {exchange_id!r}; "
                                     "give the account a `quote` option (e.g. USDT)")
            quote = spec.currency
        instrument_id = None
        if spec is not None and self._symbology(spec.venue) == "dash":
            def instrument_id(symbol: str) -> Optional[str]:
                return None if ":" in symbol else str(self.from_native(symbol, spec.venue))   # ':' = a derivative
        return CcxtAccountClient(
            exchange_id, secrets=secrets, quote=quote, exchange_factory=self._account_exchange_factory,
            exchange_options={**self.config.options, **(exchange_options or {})},
            instrument_id=instrument_id, timeout_ms=timeout_ms, now_ns=self._now_ns)


class CcxtDataClient(CcxtStreamingMixin, DataClient):
    """Historical bars (request_bars) and live streams (subscribe_bars, subscribe_quotes)."""
    connector: CcxtConnector

    def _request_bars(self, bar_type: BarType, venue: VenueSpec,
                      start: pd.Timestamp, end: pd.Timestamp) -> List[Bar]:
        conn = self.connector
        ex = conn.exchange(venue.venue)
        symbol = conn.to_native(bar_type.instrument_id)
        timeframe = timeframe_for(bar_type.spec)
        interval_ms = bar_type.spec.nanos // 1_000_000
        limit = conn.bars_per_request(venue)
        cursor = start.value // 1_000_000 - interval_ms
        last_open = end.value // 1_000_000 - interval_ms
        first_page = True
        bars: List[Bar] = []
        while cursor <= last_open:
            rows = conn.call(lambda since=int(cursor): ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit),
                             f"fetch_ohlcv {symbol}")
            rows = [r for r in (rows or []) if int(r[0]) >= cursor]
            if not rows:
                break
            if first_page and int(rows[0][0]) > cursor + interval_ms:
                log.warning("%s returned %s %s bars from %s, not from %s: the exchange does not serve "
                            "older history at this interval", venue.venue, symbol, timeframe,
                            pd.Timestamp(int(rows[0][0]), unit="ms", tz="UTC"),
                            pd.Timestamp(cursor, unit="ms", tz="UTC"))
            first_page = False
            for r in rows:
                if int(r[0]) > last_open:
                    break
                close_ns = ms_to_ns(int(r[0]) + interval_ms)
                bars.append(Bar(bar_type, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                                float(r[5] or 0.0), close_ns, close_ns))
            next_cursor = int(rows[-1][0]) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return bars


def _tick(value: Any, mode: int) -> Optional[float]:
    if value is None:
        return None
    if mode == _DECIMAL_PLACES:
        return 10.0 ** -int(value)
    return float(value)


class CcxtInstrumentProvider(InstrumentProvider):
    connector: CcxtConnector

    def _load(self, instrument_ids: List[InstrumentId]) -> List[Instrument]:
        out = []
        for iid in instrument_ids:
            markets = self.connector.markets(iid.venue)
            unified = self.connector.to_native(iid)
            if unified in markets:
                out.append(self._pair(iid, markets[unified]))
        return out

    def _load_all(self, spec: VenueSpec) -> List[Instrument]:
        markets = self.connector.markets(spec.venue)
        out = []
        for unified, m in markets.items():
            if m.get("spot", True):
                out.append(self._pair(self.connector.from_native(unified, spec.venue), m))
        return out

    def _pair(self, iid: InstrumentId, m: Dict[str, Any]) -> CurrencyPair:
        mode = getattr(self.connector.exchange(iid.venue), "precisionMode", _TICK_SIZE)
        precision = m.get("precision", {}) or {}
        limits = m.get("limits", {}) or {}
        return CurrencyPair(
            id=iid,
            raw_symbol=str(m.get("id", iid.symbol)),
            currency=m["quote"],
            base_currency=m["base"],
            quote_currency=m["quote"],
            price_increment=_tick(precision.get("price"), mode) or 0.01,
            size_increment=_tick(precision.get("amount"), mode) or 1e-8,
            min_quantity=(limits.get("amount") or {}).get("min"),
            min_notional=(limits.get("cost") or {}).get("min"),
            info={"unified_symbol": m.get("symbol"), "ccxt_maker": m.get("maker"), "ccxt_taker": m.get("taker")},
        )
