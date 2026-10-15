"""
Interactive Brokers (TWS / IB Gateway) connector.

Wraps the existing `midfreq.data.providers.ib_provider.IBProvider`, reusing its connection handling. Two things
the provider hard-codes are taken over here:

- Contracts: the provider builds every contract as `Stock(symbol, 'ISLAND', 'USD')`, i.e. Nasdaq
  only. A small subclass overrides `_get_contract` with the venue's exchange and currency from
  config/venues.json (SEHK/HKD for XHKG with '00700' sent as '700', SMART with a primary exchange
  for US stocks, SGX, TSEJ, ...).
- Time: the provider asks for `formatDate=1`, which returns bar times in the TWS login's timezone
  (naive, or with a zone suffix depending on the TWS version) and then tags them as UTC. The
  connector requests `formatDate=2` (UTC) itself, and since IB stamps a bar with its OPEN, derives
  the close from the venue's sessions.

History is fetched in windows (IB caps the duration of one request per bar size) and throttled to
IB's pacing rule of 60 historical requests per 10 minutes; a pacing violation is retried.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from midfreq.data.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, DataClient, InstrumentProvider,
    RateLimitError, bars_from_frame, time_windows,
)
from midfreq.core.model.data import Bar, BarType
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id, as_venue
from midfreq.core.model.instruments import Equity, Instrument
from midfreq.core.venues.spec import VenueSpec

BAR_SIZES = {
    "1-MINUTE": "1 min",
    "5-MINUTE": "5 mins",
    "15-MINUTE": "15 mins",
    "30-MINUTE": "30 mins",
    "1-HOUR": "1 hour",
    "4-HOUR": "4 hours",
    "1-DAY": "1 day",
}

# Longest window requested at once per bar size, inside IB's documented duration limits.
CHUNK_DAYS = {
    "1-MINUTE": 5,
    "5-MINUTE": 30,
    "15-MINUTE": 30,
    "30-MINUTE": 30,
    "1-HOUR": 30,
    "4-HOUR": 180,
    "1-DAY": 365,
}


@dataclass
class IBKRConnectorConfig(ConnectorConfig):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    use_rth: bool = True                 # regular trading hours only, as the provider requests
    what_to_show: str = "TRADES"
    min_request_interval: float = 10.0   # 60 historical requests per 10 minutes
    chunk_days: Optional[int] = None     # override the per-bar-size request window (tests)


def _make_provider(config: IBKRConnectorConfig):
    """An IBProvider whose contracts follow the venue spec. Imported lazily: importing ib_insync
    starts its event loop."""
    from ib_insync import Stock
    from midfreq.data.providers.ib_provider import IBProvider

    class VenueIBProvider(IBProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.contracts: Dict[str, Dict[str, str]] = {}

        def _get_contract(self, symbol: str):
            c = self.contracts.get(symbol)
            if c is None:
                return super()._get_contract(symbol)
            return Stock(c["symbol"], c["exchange"], c["currency"], primaryExchange=c.get("primary_exchange", ""))

    return VenueIBProvider(host=config.host, port=config.port, client_id=config.client_id)


class IBKRConnector(Connector):
    name = "ibkr"
    config_class = IBKRConnectorConfig

    def __init__(self, config=None, venues=None, provider: Any = None, **kwargs):
        """`provider` defaults to an IBProvider subclass; tests pass a fake with `.ib`,
        `connect()`, `disconnect()` and `_get_contract()`."""
        super().__init__(config, venues, **kwargs)
        self._provider = provider

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(intervals=tuple(BAR_SIZES))

    @property
    def provider(self):
        if self._provider is None:
            self._provider = _make_provider(self.config)
        return self._provider

    def _connect(self) -> None:
        self.provider.connect()

    def _close(self) -> None:
        if self._provider is not None:
            self._provider.disconnect()

    def to_native(self, instrument_id) -> str:
        iid = as_instrument_id(instrument_id)
        opts = self.venue_spec(iid.venue).connector_options(self.name)
        sym = str(iid.symbol)
        if opts.get("strip_leading_zeros"):
            sym = sym.lstrip("0") or "0"
        if opts.get("class_separator"):
            sym = sym.replace(".", opts["class_separator"])
        return sym

    def from_native(self, native: str, venue=None) -> InstrumentId:
        if venue is None:
            raise ValueError("IBKR symbols do not name their exchange; pass the venue")
        v = as_venue(venue)
        opts = self.venue_spec(v).connector_options(self.name)
        sym = native
        if opts.get("class_separator"):
            sym = sym.replace(opts["class_separator"], ".")
        if opts.get("strip_leading_zeros") and sym.isdigit():
            sym = sym.zfill(int(opts.get("code_width", 5)))
        return InstrumentId.of(sym, v)

    def contract_spec(self, instrument_id) -> Dict[str, str]:
        iid = as_instrument_id(instrument_id)
        opts = self.venue_spec(iid.venue).connector_options(self.name)
        return {"symbol": self.to_native(iid), "exchange": opts["exchange"],
                "currency": opts["currency"], "primary_exchange": opts.get("primary_exchange", "")}

    def qualified_contract(self, instrument_id):
        native = self.to_native(instrument_id)
        contracts = getattr(self.provider, "contracts", None)
        if isinstance(contracts, dict):
            contracts[native] = self.contract_spec(instrument_id)
        self.connect()
        contract = self.provider._get_contract(native)
        self.provider.ib.qualifyContracts(contract)
        return contract

    def is_rate_limit(self, exc: BaseException) -> bool:
        return isinstance(exc, RateLimitError) or "pacing violation" in str(exc).lower()

    def _create_instrument_provider(self) -> InstrumentProvider:
        return IBKRInstrumentProvider(self)

    def _create_data_client(self) -> DataClient:
        return IBKRDataClient(self, now_ns=self._now_ns)


class IBKRDataClient(DataClient):
    connector: IBKRConnector

    def _request_bars(self, bar_type: BarType, venue: VenueSpec,
                      start: pd.Timestamp, end: pd.Timestamp) -> List[Bar]:
        conn = self.connector
        contract = conn.qualified_contract(bar_type.instrument_id)
        interval = bar_type.spec.interval
        bar_size = BAR_SIZES[interval]
        # Ask from one bar before `start`: IB stamps bars with their open, so the bar closing at
        # `start` opened earlier.
        chunk = timedelta(days=conn.config.chunk_days or CHUNK_DAYS[interval])
        windows = time_windows(start - bar_type.spec.timedelta, end, chunk)
        rows: List[Any] = []
        for w_start, w_end in windows:
            days = max(1, math.ceil((w_end - w_start) / pd.Timedelta(days=1)))
            rows.extend(conn.call(lambda e=w_end, d=days: self._request(contract, e, f"{d} D", bar_size),
                                  f"historical data {contract_label(contract)}"))
        if not rows:
            return []
        frame = pd.DataFrame({
            "open": [r.open for r in rows], "high": [r.high for r in rows], "low": [r.low for r in rows],
            "close": [r.close for r in rows], "volume": [r.volume for r in rows],
        })
        stamps = pd.DatetimeIndex(pd.to_datetime([r.date for r in rows], utc=bar_type.spec.is_intraday))
        if bar_type.spec.is_intraday:
            closes = venue.bar_close_from_open(stamps, bar_type.spec)
        else:
            closes = venue.daily_close(stamps)     # daily bars come back as plain dates
        return bars_from_frame(frame, bar_type, closes)

    def _request(self, contract, end: pd.Timestamp, duration: str, bar_size: str) -> List[Any]:
        ib = self.connector.provider.ib
        errors: List[str] = []

        def on_error(*args):
            errors.append(" ".join(str(a) for a in args))

        listening = hasattr(ib, "errorEvent")
        if listening:
            ib.errorEvent += on_error
        try:
            bars = ib.reqHistoricalData(
                contract, endDateTime=end.to_pydatetime(), durationStr=duration,
                barSizeSetting=bar_size, whatToShow=self.connector.config.what_to_show,
                useRTH=self.connector.config.use_rth, formatDate=2)
        finally:
            if listening:
                ib.errorEvent -= on_error
        # ib_insync reports a pacing violation through errorEvent and returns no bars.
        if not bars and any("pacing" in e.lower() for e in errors):
            raise RateLimitError(f"IB pacing violation: {errors[-1]}")
        return list(bars or [])


def contract_label(contract) -> str:
    return f"{getattr(contract, 'symbol', contract)}@{getattr(contract, 'exchange', '')}"


class IBKRInstrumentProvider(InstrumentProvider):
    connector: IBKRConnector

    def _load(self, instrument_ids: List[InstrumentId]) -> List[Instrument]:
        conn = self.connector
        out = []
        for iid in instrument_ids:
            spec = conn.venue_spec(iid.venue)
            contract = conn.qualified_contract(iid)
            details = conn.call(lambda c=contract: conn.provider.ib.reqContractDetails(c), "contract details")
            tick = spec.price_increment
            if details:
                tick = float(getattr(details[0], "minTick", tick) or tick)
            # IB does not publish HK board lots in contract details, so lots come from the venue.
            out.append(Equity(id=iid, raw_symbol=str(iid.symbol), currency=spec.currency,
                              price_increment=tick, lot_size=spec.default_lot_size,
                              info={"native_symbol": conn.to_native(iid),
                                    "lot_size_source": "venue default"}))
        return out
