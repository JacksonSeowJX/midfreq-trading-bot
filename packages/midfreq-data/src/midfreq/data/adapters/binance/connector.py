"""
Binance spot connector, written directly against Binance's public REST API (no SDK, no API key).

- Bars: GET /api/v3/klines returns at most 1,000 bars per call, stamped with their open time in
  UTC milliseconds. The connector pages forward by `startTime` until the window is covered, and sets
  ts_event = open + interval, the bar's close.
- Instruments: GET /api/v3/exchangeInfo gives each pair's PRICE_FILTER.tickSize,
  LOT_SIZE.stepSize/minQty and NOTIONAL (or older MIN_NOTIONAL) minNotional.
- Limits: requests are spaced client-side; HTTP 429 (request weight exceeded) and 418 (IP banned
  for ignoring 429s) are retried after the server's Retry-After, with exponential backoff
  otherwise.

The HTTP transport is a small injectable object, so tests run against a fake with no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from midfreq.data.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, ConnectorError, DataClient,
    InstrumentProvider, RateLimitError,
)
from midfreq.data.connection_settings import connection_settings
from midfreq.core.common.timestamps import ms_to_ns
from midfreq.core.model.data import Bar, BarType
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id, as_venue
from midfreq.core.model.instruments import CurrencyPair, Instrument
from midfreq.core.venues.spec import VenueSpec

INTERVALS = {
    "1-SECOND": "1s", "1-MINUTE": "1m", "3-MINUTE": "3m", "5-MINUTE": "5m", "15-MINUTE": "15m",
    "30-MINUTE": "30m", "1-HOUR": "1h", "2-HOUR": "2h", "4-HOUR": "4h", "6-HOUR": "6h",
    "8-HOUR": "8h", "12-HOUR": "12h", "1-DAY": "1d", "3-DAY": "3d", "1-WEEK": "1w",
}

KLINES_PATH = "/api/v3/klines"
EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"


@dataclass
class HttpResponse:
    status: int
    payload: Any
    headers: Mapping[str, str]


class RequestsTransport:
    """GET requests through a `requests.Session`."""

    def __init__(self, timeout: float = 10.0):
        import requests
        self._session = requests.Session()
        self._timeout = timeout

    def get(self, url: str, params: Dict[str, Any]) -> HttpResponse:
        r = self._session.get(url, params=params, timeout=self._timeout)
        try:
            payload = r.json()
        except ValueError:
            payload = r.text
        return HttpResponse(r.status_code, payload, dict(r.headers))

    def close(self) -> None:
        self._session.close()


@dataclass
class BinanceConnectorConfig(ConnectorConfig):
    base_url: str = ""                  # default: `binance` in config/connections.yaml, then the venue's base_url
    klines_limit: int = 1000            # Binance's maximum per call
    timeout: float = 10.0
    min_request_interval: float = 0.1   # well inside 6,000 request weight per minute


class BinanceConnector(Connector):
    name = "binance"
    config_class = BinanceConnectorConfig

    def __init__(self, config=None, venues=None, transport: Any = None, **kwargs):
        """`transport` needs one method, `get(url, params) -> HttpResponse`; it defaults to
        RequestsTransport."""
        super().__init__(config, venues, **kwargs)
        self._transport = transport

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(intervals=tuple(INTERVALS), max_bars_per_request=self.config.klines_limit)

    @property
    def transport(self):
        if self._transport is None:
            self._transport = RequestsTransport(self.config.timeout)
        return self._transport

    def _close(self) -> None:
        close = getattr(self._transport, "close", None)
        if close:
            close()

    def base_url(self, venue: "str | None" = None) -> str:
        if self.config.base_url:
            return self.config.base_url.rstrip("/")
        spec = self.venue_spec(venue or self.served_venues()[0])
        fallback = spec.connector_options(self.name).get("base_url", "https://api.binance.com")
        return connection_settings("binance", {"base_url": fallback})["base_url"].rstrip("/")

    def to_native(self, instrument_id) -> str:
        iid = as_instrument_id(instrument_id)
        self.venue_spec(iid.venue)
        return str(iid.symbol).upper()

    def from_native(self, native: str, venue=None) -> InstrumentId:
        v = as_venue(venue) if venue is not None else self.served_venues()[0]
        self.venue_spec(v)
        return InstrumentId.of(native.upper(), v)

    def get(self, path: str, params: Dict[str, Any], venue=None) -> Any:
        url = self.base_url(venue) + path

        def fetch():
            resp = self.transport.get(url, params)
            if resp.status in (429, 418):
                retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                raise RateLimitError(f"HTTP {resp.status} from {path}",
                                     retry_after=float(retry_after) if retry_after else None)
            if resp.status >= 400:
                raise ConnectorError(f"binance {path} HTTP {resp.status}: {resp.payload}")
            return resp.payload
        return self.call(fetch, path)

    def _create_instrument_provider(self) -> InstrumentProvider:
        return BinanceInstrumentProvider(self)

    def _create_data_client(self) -> DataClient:
        return BinanceDataClient(self, now_ns=self._now_ns)


class BinanceDataClient(DataClient):
    connector: BinanceConnector

    def _request_bars(self, bar_type: BarType, venue: VenueSpec,
                      start: pd.Timestamp, end: pd.Timestamp) -> List[Bar]:
        conn = self.connector
        symbol = conn.to_native(bar_type.instrument_id)
        interval_ms = bar_type.spec.nanos // 1_000_000
        cursor = start.value // 1_000_000 - interval_ms     # open time of the bar closing at `start`
        last_open = end.value // 1_000_000 - interval_ms     # open time of the bar closing at `end`
        bars: List[Bar] = []
        while cursor <= last_open:
            rows = conn.get(KLINES_PATH, {
                "symbol": symbol, "interval": INTERVALS[bar_type.spec.interval],
                "startTime": int(cursor), "endTime": int(last_open), "limit": conn.config.klines_limit,
            }, venue=venue.venue)
            if not rows:
                break
            for r in rows:
                open_ms = int(r[0])
                close_ns = ms_to_ns(open_ms + interval_ms)
                bars.append(Bar(bar_type, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                                float(r[5]), close_ns, close_ns))
            next_cursor = int(rows[-1][0]) + interval_ms
            if next_cursor <= cursor:
                break   # no progress: never loop forever on a misbehaving response
            cursor = next_cursor
        return bars


def _filters(info: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {f.get("filterType"): f for f in info.get("filters", [])}


def _float(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


class BinanceInstrumentProvider(InstrumentProvider):
    connector: BinanceConnector

    def _load(self, instrument_ids: List[InstrumentId]) -> List[Instrument]:
        conn = self.connector
        symbols = [conn.to_native(i) for i in instrument_ids]
        venue = instrument_ids[0].venue
        payload = conn.get(EXCHANGE_INFO_PATH, {"symbols": json.dumps(symbols, separators=(",", ":"))}, venue=venue)
        return [self._pair(info, venue) for info in payload.get("symbols", [])]

    def _load_all(self, spec: VenueSpec) -> List[Instrument]:
        payload = self.connector.get(EXCHANGE_INFO_PATH, {}, venue=spec.venue)
        return [self._pair(info, spec.venue) for info in payload.get("symbols", [])]

    def _pair(self, info: Dict[str, Any], venue) -> CurrencyPair:
        f = _filters(info)
        lot = f.get("LOT_SIZE", {})
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        return CurrencyPair(
            id=InstrumentId.of(info["symbol"], venue),
            raw_symbol=info["symbol"],
            currency=info["quoteAsset"],
            base_currency=info["baseAsset"],
            quote_currency=info["quoteAsset"],
            price_increment=_float(f.get("PRICE_FILTER", {}).get("tickSize")) or 0.01,
            size_increment=_float(lot.get("stepSize")) or 1e-8,
            min_quantity=_float(lot.get("minQty")),
            min_notional=_float(notional.get("minNotional")),
            info={"status": info.get("status")},
        )
