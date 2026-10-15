"""
Moomoo (Futu OpenD) connector.

Wraps the existing `providers.moomoo_provider.MoomooProvider`: its connection handling and its
OpenD quote context are reused as they are. The connector owns what the provider left implicit:

- Symbology: `00700.XHKG` <-> `HK.00700`, `AAPL.XNAS` <-> `US.AAPL` (the venue's market code comes
  from config/venues.json; a US symbol is placed on XNAS or XNYS by the listing table).
- Pagination and errors: the provider's own page loop stops at the first error and returns the
  pages it already had, so a rate limit mid-download produced a silently truncated history. Here
  each page is retried with backoff on Moomoo's frequency limit (60 history requests per 30 s), and
  an exhausted 30-day history quota raises QuotaExceededError instead of returning partial data.
- Time: Moomoo's `time_key` is the bar's CLOSE in exchange-local time (a Hong Kong bar stamped
  10:30 covers 09:30-10:30). The provider tags it as UTC; the connector reads it in the venue's
  timezone, so ts_event is the true UTC close.
- Intervals: only intervals OpenD serves natively. The provider quietly returned hourly bars when
  asked for 4-hour ones; this connector refuses 4-HOUR with UnsupportedIntervalError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

import pandas as pd

from mft.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, ConnectorError, DataClient,
    InstrumentProvider, QuotaExceededError, RateLimitError, bars_from_frame,
)
from mft.model.data import Bar, BarType
from mft.model.identifiers import InstrumentId, Venue, as_instrument_id, as_venue
from mft.model.instruments import Equity, Instrument
from mft.venues.spec import VenueSpec

RET_OK = 0   # moomoo.RET_OK; defined here so importing the connector does not import the SDK

# Interval -> OpenD KLType value (the SDK's KLType constants are these strings).
KLTYPES = {
    "1-MINUTE": "K_1M",
    "3-MINUTE": "K_3M",
    "5-MINUTE": "K_5M",
    "15-MINUTE": "K_15M",
    "30-MINUTE": "K_30M",
    "1-HOUR": "K_60M",
    "1-DAY": "K_DAY",
}

_RATE_LIMIT_MARKERS = ("frequen", "too many", "too fast", "频率", "频繁")
_QUOTA_MARKERS = ("quota", "额度")
_SNAPSHOT_BATCH = 400   # get_market_snapshot accepts at most 400 codes per call


@dataclass
class MoomooConnectorConfig(ConnectorConfig):
    host: str = "127.0.0.1"
    port: int = 11111
    max_count: int = 1000               # bars per page; OpenD's maximum
    autype: str = "qfq"                 # forward-adjusted prices, as the existing provider requests
    min_request_interval: float = 0.5   # 60 history requests per 30 seconds


class MoomooConnector(Connector):
    name = "moomoo"
    config_class = MoomooConnectorConfig

    def __init__(self, config=None, venues=None, provider: Any = None, **kwargs):
        """`provider` defaults to a MoomooProvider for the configured host/port; tests pass a fake
        exposing the same `_get_context()`, `get_lot_sizes()` and `close()`."""
        super().__init__(config, venues, **kwargs)
        self._provider = provider

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(intervals=tuple(KLTYPES), max_bars_per_request=self.config.max_count)

    @property
    def provider(self):
        if self._provider is None:
            from providers.moomoo_provider import MoomooProvider   # imports the moomoo SDK
            self._provider = MoomooProvider(host=self.config.host, port=self.config.port)
        return self._provider

    def context(self):
        return self.provider._get_context()

    def _connect(self) -> None:
        self.context()

    def _close(self) -> None:
        if self._provider is not None:
            self._provider.close()

    def to_native(self, instrument_id) -> str:
        iid = as_instrument_id(instrument_id)
        market = self.venue_spec(iid.venue).connector_options(self.name)["market"]
        return f"{market}.{iid.symbol}"

    def from_native(self, native: str, venue=None) -> InstrumentId:
        if "." not in native:
            raise ValueError(f"Moomoo symbols look like 'HK.00700': {native!r}")
        market, code = native.split(".", 1)
        v = as_venue(venue) if venue is not None else self.venues.venue_for_market(self.name, market, code)
        return InstrumentId.of(code, v)

    def is_rate_limit(self, exc: BaseException) -> bool:
        return isinstance(exc, RateLimitError)

    def check_ret(self, ret: int, data: Any, what: str) -> None:
        """Turn an OpenD error return into the right exception."""
        if ret == RET_OK:
            return
        msg = str(data)
        low = msg.lower()
        if any(m in low for m in _RATE_LIMIT_MARKERS):
            raise RateLimitError(f"{what}: {msg}")
        if any(m in low for m in _QUOTA_MARKERS):
            raise QuotaExceededError(f"{what}: {msg}")
        raise ConnectorError(f"moomoo {what} failed: {msg}")

    def _create_instrument_provider(self) -> InstrumentProvider:
        return MoomooInstrumentProvider(self)

    def _create_data_client(self) -> DataClient:
        return MoomooDataClient(self, now_ns=self._now_ns)


class MoomooDataClient(DataClient):
    connector: MoomooConnector

    def _request_bars(self, bar_type: BarType, venue: VenueSpec,
                      start: pd.Timestamp, end: pd.Timestamp) -> List[Bar]:
        conn = self.connector
        native = conn.to_native(bar_type.instrument_id)
        ktype = KLTYPES[bar_type.spec.interval]
        # OpenD takes calendar dates in the exchange's own clock.
        start_local = start.tz_convert(venue.timezone).strftime("%Y-%m-%d")
        end_local = end.tz_convert(venue.timezone).strftime("%Y-%m-%d")

        pages = []
        page_req_key = None
        while True:
            def fetch(key=page_req_key):
                ret, data, next_key = conn.context().request_history_kline(
                    native, start=start_local, end=end_local, ktype=ktype,
                    autype=conn.config.autype, max_count=conn.config.max_count, page_req_key=key)
                conn.check_ret(ret, data, f"history kline {native}")
                return data, next_key
            data, page_req_key = conn.call(fetch, f"history kline {native}")
            if data is not None and len(data):
                pages.append(data)
            if page_req_key is None:
                break

        if not pages:
            return []
        frame = pd.concat(pages, ignore_index=True)
        stamps = pd.DatetimeIndex(pd.to_datetime(frame["time_key"]))
        if bar_type.spec.is_intraday:
            closes = venue.localize(stamps)        # time_key is already the bar's close
        else:
            closes = venue.daily_close(stamps)     # time_key is the trading date at 00:00
        return bars_from_frame(frame, bar_type, closes)


class MoomooInstrumentProvider(InstrumentProvider):
    connector: MoomooConnector

    def _load(self, instrument_ids: List[InstrumentId]) -> List[Instrument]:
        conn = self.connector
        natives = [conn.to_native(i) for i in instrument_ids]
        lots = {}
        for i in range(0, len(natives), _SNAPSHOT_BATCH):
            batch = natives[i:i + _SNAPSHOT_BATCH]
            lots.update(conn.call(lambda b=batch: conn.provider.get_lot_sizes(b), "market snapshot") or {})
        out = []
        for iid, native in zip(instrument_ids, natives):
            out.append(self._equity(iid, native, lots.get(native)))
        return out

    def _load_all(self, spec: VenueSpec) -> List[Instrument]:
        conn = self.connector
        market = spec.connector_options(conn.name)["market"]

        def fetch():
            ret, data = conn.context().get_stock_basicinfo(market, "STOCK")
            conn.check_ret(ret, data, f"stock list {market}")
            return data
        data = conn.call(fetch, f"stock list {market}")
        out = []
        for _, row in data.iterrows():
            iid = conn.from_native(row["code"])
            if iid.venue == spec.venue:
                out.append(self._equity(iid, row["code"], row.get("lot_size")))
        return out

    def _equity(self, iid: InstrumentId, native: str, lot: Optional[float]) -> Equity:
        spec = self.connector.venue_spec(iid.venue)
        return Equity(
            id=iid, raw_symbol=str(iid.symbol), currency=spec.currency,
            price_increment=spec.price_increment,
            lot_size=float(lot) if lot else spec.default_lot_size,
            info={"native_symbol": native,
                  "lot_size_source": "moomoo snapshot" if lot else "venue default"},
        )
