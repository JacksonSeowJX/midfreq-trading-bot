"""
Moomoo (Futu OpenD) connector.

Wraps the existing `midfreq.data.providers.moomoo_provider.MoomooProvider`: its connection handling and its
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
- Accounts: `account_client(market='US')` is a read-only view of the trade account through OpenD
  (see midfreq.data.adapters.moomoo.account).
- Trading: `execution_client(market='US')` places and cancels orders and reports fills as events
  (midfreq.data.adapters.moomoo.execution); paper unless trading_env is `real`.
- Live data: `data_client.subscribe_bars` / `subscribe_quotes` stream K-line and order-book pushes
  (midfreq.data.adapters.moomoo.streaming), on a quote context of their own.

`sdk` (the moomoo module, imported on first use by default) and `probe` (the TCP check of OpenD's
port) are how the live side reaches OpenD; tests pass tests/support/fake_moomoo's fakes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

import pandas as pd

from midfreq.data.adapters.base import (
    BarUpdate, Connector, ConnectorCapabilities, ConnectorConfig, ConnectorError, DataClient,
    InstrumentProvider, QuotaExceededError, RateLimitError, bars_from_frame,
)
from midfreq.data.adapters.moomoo.account import MoomooAccountClient, OpenDTransport
from midfreq.data.adapters.moomoo.execution import MoomooExecutionClient
from midfreq.data.adapters.moomoo.streaming import MoomooStreams
from midfreq.data.connection_settings import connection_settings
from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id, as_venue
from midfreq.core.model.instruments import Equity, Instrument
from midfreq.core.venues.spec import VenueSpec

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
    host: Optional[str] = None          # default: `moomoo` in config/connections.yaml
    port: Optional[int] = None
    max_count: int = 1000               # bars per page; OpenD's maximum
    autype: str = "qfq"                 # forward-adjusted prices, as the existing provider requests
    min_request_interval: float = 0.5   # 60 history requests per 30 seconds

    def __post_init__(self):
        conn = connection_settings("moomoo", {"host": "127.0.0.1", "port": 11111},
                                   host=self.host, port=self.port)
        self.host, self.port = conn["host"], conn["port"]


class MoomooConnector(Connector):
    name = "moomoo"
    config_class = MoomooConnectorConfig

    def __init__(self, config=None, venues=None, provider: Any = None,
                 trade_transport: Optional[OpenDTransport] = None, sdk: Any = None,
                 probe: Optional[Callable[[str, int, float], str]] = None,
                 timer: Optional[Callable[[float, Callable[[], None]], Any]] = None, **kwargs):
        """`provider` defaults to a MoomooProvider for the configured host/port; tests pass a fake
        exposing the same `_get_context()`, `get_lot_sizes()` and `close()`. `trade_transport`
        is how account clients reach OpenD (an OpenDTransport by default; tests pass a fake).
        `sdk`, `probe` and `timer` are how the live side (execution client, streams) reaches
        OpenD: the moomoo module, a TCP probe of OpenD's port, and a timer for deferred
        unsubscribes; tests pass fakes."""
        super().__init__(config, venues, **kwargs)
        self._provider = provider
        self._trade_transport = trade_transport
        self._sdk = sdk
        self._probe = probe
        self._timer = timer

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(intervals=tuple(KLTYPES), max_bars_per_request=self.config.max_count,
                                     live_data=True, execution=True)

    @property
    def provider(self):
        if self._provider is None:
            from midfreq.data.providers.moomoo_provider import MoomooProvider   # imports the moomoo SDK
            self._provider = MoomooProvider(host=self.config.host, port=self.config.port)
        return self._provider

    def context(self):
        return self.provider._get_context()

    def _connect(self) -> None:
        self.context()

    def _close(self) -> None:
        if self._provider is not None:
            self._provider.close()

    def close(self) -> None:
        """Close the history session and, if live streams were opened, their quote context
        (streams open lazily, without connect())."""
        super().close()
        if self._data_client is not None:
            self._data_client.close_streams()

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

    def account_client(self, market: str = "US", trading_env: Optional[str] = None,
                       security_firm: Optional[str] = None, acc_id: int = 0,
                       timezone: Optional[str] = None, secrets: Sequence[str] = ()) -> MoomooAccountClient:
        """
        The read-only trade account of one market ('US', 'HK') on the configured OpenD. The
        trading environment (simulate unless `real` is set on purpose) and security firm come from
        the `moomoo` connection unless given here; the account currency and the timezone of order
        times are those of the market's venue in config/venues.json. OpenD holds the login, so
        Moomoo accounts take no secrets.
        """
        if secrets:
            raise ConnectorError(f"moomoo accounts are read through OpenD, which holds the login; "
                                 f"remove secrets {list(secrets)} from the account entry")
        market = str(market).upper()
        specs = [s for s in self.venues.served_by(self.name)
                 if str(s.connector_options(self.name).get("market", "")).upper() == market]
        if not specs:
            raise ConnectorError(f"no venue in config/venues.json has moomoo market {market!r}")
        conn = connection_settings("moomoo", {"trading_env": "simulate", "security_firm": "FUTUSECURITIES"},
                                   trading_env=trading_env, security_firm=security_firm)
        return MoomooAccountClient(
            market=market, host=self.config.host, port=self.config.port,
            trading_env=conn["trading_env"], security_firm=conn["security_firm"],
            currency=specs[0].currency, timezone=timezone or specs[0].timezone, acc_id=acc_id,
            transport=self._trade_transport, instrument_id=lambda code: str(self.from_native(code)),
            now_ns=self._now_ns)

    def execution_client(self, market: str = "US", trading_env: Optional[str] = None,
                         **options: Any) -> MoomooExecutionClient:
        """
        Orders for the trade account of one market ('US', 'HK') on the configured OpenD. Paper
        (TrdEnv.SIMULATE) unless `trading_env`, or else the `moomoo` connection's trading_env, is
        `real`. Other options: see MoomooExecutionClient. Call connect() before submitting.
        """
        return MoomooExecutionClient(self, {"market": market, "trading_env": trading_env, **options},
                                     sdk=self._sdk, probe=self._probe)


class MoomooDataClient(DataClient):
    connector: MoomooConnector
    _streams: Optional[MoomooStreams] = None

    # ── live data (midfreq.data.adapters.moomoo.streaming) ───────────

    @property
    def streams(self) -> MoomooStreams:
        if self._streams is None:
            conn = self.connector
            self._streams = MoomooStreams(self, sdk=conn._sdk, probe=conn._probe, timer=conn._timer)
        return self._streams

    def subscribe_bars(self, bar_type: BarType, handler: Callable[[BarUpdate], None]) -> None:
        self.streams.subscribe_bars(bar_type, handler)

    def unsubscribe_bars(self, bar_type: BarType) -> None:
        self.streams.unsubscribe_bars(bar_type)

    def subscribe_bars_many(self, bar_types, handler: Callable[[BarUpdate], None]) -> None:
        self.streams.subscribe_bars_many(list(bar_types), handler)

    def unsubscribe_bars_many(self, bar_types) -> None:
        self.streams.unsubscribe_bars_many(list(bar_types))

    def subscribe_quotes(self, instrument_id: InstrumentId, handler: Callable[[QuoteTick], None]) -> None:
        self.streams.subscribe_quotes(instrument_id, handler)

    def unsubscribe_quotes(self, instrument_id: InstrumentId) -> None:
        self.streams.unsubscribe_quotes(instrument_id)

    def subscription_limit(self) -> Optional[int]:
        return self.streams.subscription_limit()

    def close_streams(self) -> None:
        if self._streams is not None:
            self._streams.close()
            self._streams = None

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
