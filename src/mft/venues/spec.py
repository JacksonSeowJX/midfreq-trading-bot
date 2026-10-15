"""
VenueSpec: what the framework knows about one trading venue, loaded from config/venues.json.

A venue spec answers the questions that used to be answered implicitly by "this is an HK script":
which timezone the exchange clock runs in, when it is open (HKEX closes for lunch), what currency
it settles in, how big a board lot is, what a trade costs, and how each connector spells its
symbols. It also does the time arithmetic that turns a data source's bar stamps into bar CLOSE
times in UTC, because that arithmetic depends on the sessions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mft.common.timestamps import localize_index
from mft.model.data import BarSpecification
from mft.model.enums import AssetClass
from mft.model.fees import FeeModel, MakerTakerFeeModel, PercentFeeModel
from mft.model.identifiers import InstrumentId, Venue, as_venue
from mft.model.instruments import CurrencyPair, Equity, Instrument

DEFAULT_VENUES_PATH = Path(__file__).resolve().parents[3] / "config" / "venues.json"


@dataclass(frozen=True)
class Session:
    open: time
    close: time

    @property
    def open_offset(self) -> pd.Timedelta:
        return pd.Timedelta(hours=self.open.hour, minutes=self.open.minute)

    @property
    def close_offset(self) -> pd.Timedelta:
        return pd.Timedelta(hours=self.close.hour, minutes=self.close.minute)


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class VenueSpec:
    venue: Venue
    name: str
    asset_class: AssetClass
    timezone: str
    currency: str
    sessions: Tuple[Session, ...]           # empty tuple means trading 24/7
    weekdays: Tuple[int, ...]
    lot_size_rule: str                      # 'per_instrument' or 'fixed'
    default_lot_size: float
    price_increment: float
    fees: Dict[str, Any]
    default_connector: str
    connectors: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, mic: str, d: Dict[str, Any]) -> "VenueSpec":
        raw_sessions = d.get("sessions", [])
        sessions = () if raw_sessions == "24/7" else tuple(
            Session(_parse_time(o), _parse_time(c)) for o, c in raw_sessions)
        lot = d.get("lot_size", {})
        return cls(
            venue=Venue(mic),
            name=d.get("name", mic),
            asset_class=AssetClass(d.get("asset_class", "EQUITY")),
            timezone=d["timezone"],
            currency=d["currency"],
            sessions=sessions,
            weekdays=tuple(d.get("weekdays", [0, 1, 2, 3, 4])),
            lot_size_rule=lot.get("rule", "fixed"),
            default_lot_size=float(lot.get("default", 1)),
            price_increment=float(d.get("price_increment", 0.01)),
            fees=dict(d.get("fees", {})),
            default_connector=d.get("default_connector", ""),
            connectors={k: dict(v) for k, v in d.get("connectors", {}).items()},
        )

    # ── basic properties ─────────────────────────────────────────────

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def is_24_7(self) -> bool:
        return not self.sessions

    @property
    def is_crypto(self) -> bool:
        return self.asset_class is AssetClass.CRYPTOCURRENCY

    @property
    def fees_calibrated(self) -> bool:
        return bool(self.fees.get("calibrated", False))

    def fee_model(self) -> FeeModel:
        model = self.fees.get("model", "percent")
        if model == "percent":
            return PercentFeeModel(float(self.fees["rate"]))
        if model == "maker_taker":
            return MakerTakerFeeModel(float(self.fees["maker"]), float(self.fees["taker"]))
        raise ValueError(f"{self.venue}: unknown fee model {model!r}")

    def connector_options(self, connector: str) -> Dict[str, Any]:
        """This venue's settings for one connector; a clear error when it does not serve the venue."""
        try:
            return self.connectors[connector]
        except KeyError:
            raise ValueError(
                f"connector {connector!r} is not configured for venue {self.venue}; "
                f"configured: {sorted(self.connectors)}") from None

    def default_instrument(self, symbol: str, quote_currency: Optional[str] = None) -> Instrument:
        """An instrument with this venue's default rules, for when the connector cannot look the
        real ones up (for example the legacy cache, which never stored lot sizes)."""
        iid = InstrumentId.of(symbol, self.venue)
        if self.is_crypto:
            quote = quote_currency or self.currency
            base = symbol[: -len(quote)] if symbol.endswith(quote) else symbol.split("-")[0]
            return CurrencyPair(id=iid, raw_symbol=symbol, currency=quote, base_currency=base,
                                quote_currency=quote, price_increment=self.price_increment,
                                size_increment=self.default_lot_size)
        return Equity(id=iid, raw_symbol=symbol, currency=self.currency,
                      price_increment=self.price_increment, size_increment=1.0,
                      lot_size=self.default_lot_size)

    # ── time arithmetic ──────────────────────────────────────────────

    def localize(self, index: pd.DatetimeIndex) -> pd.DatetimeIndex:
        """Exchange wall-clock times (naive, or mis-tagged as UTC) -> true UTC."""
        return localize_index(index, self.timezone)

    def daily_close(self, dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
        """UTC close time of the trading day on each local calendar date: the last session's close
        (16:00 in Hong Kong is 08:00 UTC), or the following UTC midnight for a 24/7 venue."""
        days = pd.DatetimeIndex(dates)
        if days.tz is not None:
            days = days.tz_localize(None)
        days = days.normalize()
        if self.is_24_7:
            return (days + pd.Timedelta(days=1)).tz_localize("UTC")
        return localize_index(days + self.sessions[-1].close_offset, self.timezone)

    def bar_close_from_open(self, opens_utc: pd.DatetimeIndex, spec: BarSpecification) -> pd.DatetimeIndex:
        """
        Close times for bars stamped with their OPEN time (yfinance, IBKR, Binance, ccxt).

        Usually open + interval, but a bar that runs into a session close ends there: yfinance's
        hourly Hong Kong bar opening 11:30 closes at 12:00 for lunch, and the US bar opening 15:30
        closes at 16:00. That matches how Moomoo stamps the same bars, so the same bar has the same
        ts_event whichever connector fetched it.
        """
        opens = pd.DatetimeIndex(opens_utc)
        if opens.tz is None:
            opens = opens.tz_localize("UTC")
        if not spec.is_intraday:
            if self.is_24_7:
                return opens.tz_convert("UTC") + spec.timedelta
            return self.daily_close(opens.tz_convert(self.timezone).tz_localize(None))
        ends = opens + spec.timedelta
        if self.is_24_7:
            return ends.tz_convert("UTC")
        # Work in naive local wall-clock so a daylight-saving change cannot shift the session close.
        local = opens.tz_convert(self.timezone).tz_localize(None)
        tod = local - local.normalize()
        result = ends.tz_convert("UTC")
        for session in self.sessions:
            in_session = (tod >= session.open_offset) & (tod < session.close_offset)
            if not in_session.any():
                continue
            session_close = localize_index(local.normalize() + session.close_offset, self.timezone)
            clamped = np.where(in_session & (session_close < result), session_close.asi8, result.asi8)
            result = pd.DatetimeIndex(clamped.astype("datetime64[ns]")).tz_localize("UTC")
        return result

    def to_legacy_index(self, ts_event: Iterable[int], spec: BarSpecification) -> pd.DatetimeIndex:
        """
        The timestamp the original framework would have stored for each bar: the exchange-local
        close time tagged as UTC (as moomoo_provider wrote it), or local midnight for daily bars.
        This is the inverse of the legacy reader and is what core strategies are shown.
        """
        utc = pd.DatetimeIndex(pd.to_datetime(np.asarray(list(ts_event), dtype=np.int64), unit="ns", utc=True))
        local = utc.tz_convert(self.timezone).tz_localize(None)
        if not spec.is_intraday:
            if self.is_24_7:
                local = local - pd.Timedelta(days=1)
            local = local.normalize()
        return local.tz_localize("UTC")


class VenueRegistry:
    """All configured venues, plus the listing table that places a US symbol on XNAS or XNYS."""

    def __init__(self, specs: Dict[str, VenueSpec], listings: Optional[Dict[str, Any]] = None):
        self._specs = dict(specs)
        self._listings = listings or {}

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> "VenueRegistry":
        path = Path(path) if path else DEFAULT_VENUES_PATH
        with open(path) as f:
            raw = json.load(f)
        specs = {mic: VenueSpec.from_dict(mic, d) for mic, d in raw.get("venues", {}).items()}
        listings = {k: v for k, v in raw.get("listings", {}).items() if not k.startswith("_")}
        return cls(specs, listings)

    def get(self, venue: str | Venue) -> VenueSpec:
        key = str(as_venue(venue))
        try:
            return self._specs[key]
        except KeyError:
            raise KeyError(f"unknown venue {key!r}; configured: {sorted(self._specs)}") from None

    def __contains__(self, venue) -> bool:
        return str(venue).upper() in self._specs

    def __iter__(self) -> Iterator[VenueSpec]:
        return iter(self._specs.values())

    def venues(self) -> List[Venue]:
        return [s.venue for s in self._specs.values()]

    def served_by(self, connector: str) -> List[VenueSpec]:
        return [s for s in self._specs.values() if connector in s.connectors]

    def venue_for_market(self, connector: str, market: str, code: str) -> Venue:
        """
        The venue a connector's market code refers to: Moomoo 'HK' -> XHKG. A market that maps to
        several venues ('US' -> XNAS and XNYS) is resolved with the listing table.
        """
        candidates = [s for s in self.served_by(connector)
                      if s.connectors[connector].get("market", "").upper() == market.upper()]
        if not candidates:
            raise ValueError(f"no venue configured for {connector} market {market!r}")
        if len(candidates) == 1:
            return candidates[0].venue
        return self.pick_listed([c.venue for c in candidates], code)

    def pick_listed(self, candidates: List[Venue], code: str) -> Venue:
        """Choose between venues a symbol alone does not distinguish (a US ticker could be on XNAS
        or XNYS) using the listing table in config/venues.json."""
        wanted = {str(v) for v in candidates}
        for table in self._listings.values():
            listed = {k for k in table if k != "default"} | {table.get("default")}
            if not wanted <= listed:
                continue
            for mic, symbols in table.items():
                if mic != "default" and code.upper() in {s.upper() for s in symbols}:
                    return Venue(mic)
            if table.get("default"):
                return Venue(table["default"])
        raise ValueError(f"{code!r} could be on any of {sorted(wanted)} and the listing table does not say which")


@lru_cache(maxsize=1)
def default_venues() -> VenueRegistry:
    """The registry from config/venues.json, loaded once."""
    return VenueRegistry.load()
