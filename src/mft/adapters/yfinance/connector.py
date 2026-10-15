"""
Yahoo Finance connector.

Wraps the existing `providers.yfinance_provider.YFinanceProvider` (its download call and column
handling are reused). The connector adds:

- Symbology: Yahoo writes Hong Kong codes as four digits with a suffix ('00700.XHKG' -> '0700.HK'),
  Singapore '.SI', Shanghai '.SS', Shenzhen '.SZ', Tokyo '.T', and US share classes with a dash
  ('BRK.B' -> 'BRK-B'). The rules are per venue in config/venues.json.
- History limits: Yahoo serves 1-minute bars for the last 30 days (at most 7 days per request),
  5-minute bars for 60 days and hourly bars for 730 days. Requests are cut into windows inside those
  limits, and a start date beyond them is moved forward with a warning rather than failing.
- Time: Yahoo stamps intraday bars with their OPEN, so ts_event is derived from the venue sessions
  (the Hong Kong bar opening 11:30 closes at 12:00 for lunch, exactly as Moomoo stamps it).
- Intervals: the provider maps 4-hour requests to hourly data; this connector does not offer 4-HOUR.

yfinance reports most failures, rate limits included, by printing them and returning an empty frame.
Where that happens the connector checks yfinance's error table and retries a rate limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, List, Optional

import pandas as pd

from mft.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, DataClient, InstrumentProvider,
    RateLimitError, bars_from_frame, time_windows, utc_dates_or_local,
)
from mft.model.data import Bar, BarType
from mft.model.identifiers import InstrumentId, as_instrument_id, as_venue
from mft.model.instruments import Instrument
from mft.venues.spec import VenueSpec

# Interval -> core.models.Timeframe value understood by YFinanceProvider.
TIMEFRAMES = {"1-MINUTE": "1m", "5-MINUTE": "5m", "1-HOUR": "1h", "1-DAY": "1d"}

MAX_HISTORY = {
    "1-MINUTE": timedelta(days=30),
    "5-MINUTE": timedelta(days=60),
    "1-HOUR": timedelta(days=730),
    "1-DAY": None,
}

WINDOW = {
    "1-MINUTE": timedelta(days=7),
    "5-MINUTE": timedelta(days=59),
    "1-HOUR": timedelta(days=365),
    "1-DAY": timedelta(days=3650),
}


@dataclass
class YFinanceConnectorConfig(ConnectorConfig):
    min_request_interval: float = 0.5
    window_days: Optional[int] = None   # override the per-interval request window (tests)


def _yfinance_error(symbol: str) -> Optional[str]:
    """The error yfinance recorded for `symbol` on its last download, if any (yfinance keeps them in
    `yfinance.shared._ERRORS` instead of raising)."""
    try:
        import yfinance.shared as shared
    except Exception:  # noqa: BLE001 - yfinance missing or reorganised
        return None
    errors = getattr(shared, "_ERRORS", {}) or {}
    msg = errors.get(symbol) or errors.get(symbol.upper())
    return str(msg) if msg else None


class YFinanceConnector(Connector):
    name = "yfinance"
    config_class = YFinanceConnectorConfig

    def __init__(self, config=None, venues=None, provider: Any = None, **kwargs):
        """`provider` defaults to YFinanceProvider; tests pass a fake with `get_historical_data`."""
        super().__init__(config, venues, **kwargs)
        self._provider = provider
        self._real_provider = provider is None   # only the real one records errors in yfinance

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(intervals=tuple(TIMEFRAMES), max_history=dict(MAX_HISTORY))

    @property
    def provider(self):
        if self._provider is None:
            from providers.yfinance_provider import YFinanceProvider
            self._provider = YFinanceProvider()
        return self._provider

    def to_native(self, instrument_id) -> str:
        iid = as_instrument_id(instrument_id)
        opts = self.venue_spec(iid.venue).connector_options(self.name)
        sym = str(iid.symbol)
        if opts.get("zero_pad") and sym.isdigit():
            sym = str(int(sym)).zfill(int(opts["zero_pad"]))
        if opts.get("class_separator"):
            sym = sym.replace(".", opts["class_separator"])
        return sym + opts.get("suffix", "")

    def from_native(self, native: str, venue=None) -> InstrumentId:
        spec = self.venue_spec(venue) if venue is not None else self._venue_for(native)
        opts = spec.connector_options(self.name)
        suffix = opts.get("suffix", "")
        sym = native[: len(native) - len(suffix)] if suffix and native.endswith(suffix) else native
        if opts.get("class_separator"):
            sym = sym.replace(opts["class_separator"], ".")
        if opts.get("code_width") and sym.isdigit():
            sym = sym.zfill(int(opts["code_width"]))
        return InstrumentId.of(sym, spec.venue)

    def _venue_for(self, native: str) -> VenueSpec:
        """The venue a Yahoo symbol belongs to: by suffix ('.HK'), or for unsuffixed US tickers by
        the listing table."""
        served = self.venues.served_by(self.name)
        suffixed = [s for s in served if s.connectors[self.name].get("suffix")
                    and native.endswith(s.connectors[self.name]["suffix"])]
        if suffixed:
            return max(suffixed, key=lambda s: len(s.connectors[self.name]["suffix"]))
        plain = [s for s in served if not s.connectors[self.name].get("suffix")]
        if not plain:
            raise ValueError(f"no venue uses yfinance symbols like {native!r}")
        if len(plain) == 1:
            return plain[0]
        code = native
        for s in plain:
            sep = s.connectors[self.name].get("class_separator")
            if sep:
                code = code.replace(sep, ".")
        return self.venues.get(self.venues.pick_listed([s.venue for s in plain], code))

    def is_rate_limit(self, exc: BaseException) -> bool:
        if isinstance(exc, RateLimitError):
            return True
        text = f"{type(exc).__name__} {exc}".lower()
        return "ratelimit" in text or "too many requests" in text or "rate limit" in text

    def _create_instrument_provider(self) -> InstrumentProvider:
        return YFinanceInstrumentProvider(self)

    def _create_data_client(self) -> DataClient:
        return YFinanceDataClient(self, now_ns=self._now_ns)


class YFinanceDataClient(DataClient):
    connector: YFinanceConnector

    def _request_bars(self, bar_type: BarType, venue: VenueSpec,
                      start: pd.Timestamp, end: pd.Timestamp) -> List[Bar]:
        from core.models import Timeframe
        conn = self.connector
        native = conn.to_native(bar_type.instrument_id)
        interval = bar_type.spec.interval
        timeframe = Timeframe(TIMEFRAMES[interval])
        # Yahoo stamps bars with their open and treats `end` as exclusive, so pad both edges by
        # one bar; anything outside [start, end] is trimmed afterwards.
        pad = bar_type.spec.timedelta
        frames = []
        window = timedelta(days=conn.config.window_days) if conn.config.window_days else WINDOW[interval]
        for w_start, w_end in time_windows(start - pad, end + pad, window):
            def fetch(a=w_start, b=w_end):
                df = conn.provider.get_historical_data(native, timeframe, a.to_pydatetime(), b.to_pydatetime())
                if (df is None or df.empty) and conn._real_provider \
                        and conn.is_rate_limit(Exception(_yfinance_error(native) or "")):
                    raise RateLimitError(f"yfinance rate limit for {native}")
                return df
            df = conn.call(fetch, f"download {native}")
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return []
        frame = pd.concat(frames)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        index = pd.DatetimeIndex(frame.index)
        if bar_type.spec.is_intraday:
            closes = venue.bar_close_from_open(index, bar_type.spec)
        else:
            closes = venue.daily_close(utc_dates_or_local(index, venue))
        return bars_from_frame(frame, bar_type, closes)


class YFinanceInstrumentProvider(InstrumentProvider):
    """Yahoo publishes no lot sizes or tick sizes, so instruments get the venue's defaults."""
    connector: YFinanceConnector

    def _load(self, instrument_ids: List[InstrumentId]) -> List[Instrument]:
        out = []
        for iid in instrument_ids:
            inst = self.connector.venue_spec(iid.venue).default_instrument(str(iid.symbol))
            inst.info.update({"native_symbol": self.connector.to_native(iid),
                              "lot_size_source": "venue default"})
            out.append(inst)
        return out
