"""
ReferenceDataService: what the trading node knows about instruments and venues that is not
market data (docs/LIVE_ARCHITECTURE.md).

- Instruments (lot, tick, currency) from each venue's connector `InstrumentProvider`, asked once
  per instrument and then kept. Many instruments are asked for in one call per venue
  (`instruments`), because Moomoo answers lot sizes from a market snapshot of up to 400 codes.
  When a provider cannot answer (OpenD down) or gives no lot size, the venue's lot rule from
  config/venues.json is used and the log says so: the original engine's "lot size lookup
  failed, orders may be rejected as odd lots" case (midfreq.live.live_engine, run()).
- Trading calendars per venue (midfreq.trading.calendar.VenueCalendar).
- Corporate actions from the curated file and any venue cash-flow sources
  (midfreq.trading.corporate_actions), and the venues' cash flows themselves.

Everything runs on the node's thread; a provider call is made only on a cache miss.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id, as_venue
from midfreq.core.model.instruments import CurrencyPair, Instrument
from midfreq.core.venues.spec import VenueRegistry, VenueSpec, default_venues
from midfreq.data.adapters.base import Connector, InstrumentProvider
from midfreq.trading.calendar import VenueCalendar
from midfreq.trading.corporate_actions import (
    CashFlow, CorporateActionSource, JsonCorporateActionSource, VenueCashFlowSource, merge_actions,
)
from midfreq.trading.interfaces import CorporateAction, TradingCalendar

log = logging.getLogger(__name__)

__all__ = ["ReferenceDataService"]


class ReferenceDataService:
    """The node's ReferenceData (midfreq.trading.interfaces.ReferenceData)."""

    def __init__(self, providers: Optional[Mapping["str | Venue", InstrumentProvider]] = None, *,
                 venues: Optional[VenueRegistry] = None,
                 calendars: Optional[Mapping["str | Venue", TradingCalendar]] = None,
                 corporate_actions: Optional[CorporateActionSource] = None,
                 cash_flow_sources: Iterable[VenueCashFlowSource] = (),
                 venues_path: "Optional[Path | str]" = None):
        """
        providers: the InstrumentProvider to ask for each venue's instruments.
        venues: the venue registry (config/venues.json by default).
        calendars: calendars to use instead of building them from `venues_path`.
        corporate_actions: the curated source (config/corporate_actions.json by default).
        cash_flow_sources: venue cash-flow sources, written in the connectors.
        venues_path: the venues.json to read holidays from (config/venues.json by default).
        """
        self._venues = venues or default_venues()
        self._providers: Dict[Venue, InstrumentProvider] = {
            as_venue(v): p for v, p in (providers or {}).items()}
        self._venues_path = venues_path
        self._calendars: Dict[Venue, TradingCalendar] = {
            as_venue(v): c for v, c in (calendars or {}).items()}
        self._ca_file = corporate_actions if corporate_actions is not None else JsonCorporateActionSource()
        self._cash_sources: List[VenueCashFlowSource] = list(cash_flow_sources)
        self._instruments: Dict[InstrumentId, Instrument] = {}
        self._provider_calls = 0

    @classmethod
    def from_connectors(cls, connectors: Iterable[Connector], **kwargs) -> "ReferenceDataService":
        """Each venue's instruments from the connector config/venues.json names as its default,
        or else from the first connector given that serves it."""
        connectors = list(connectors)
        providers: Dict[Venue, InstrumentProvider] = {}
        chosen: Dict[Venue, str] = {}
        for conn in connectors:
            for venue in conn.served_venues():
                default = conn.venues.get(venue).default_connector
                if venue not in providers or (default == conn.name and chosen[venue] != default):
                    providers[venue] = conn.instrument_provider
                    chosen[venue] = conn.name
        if kwargs.get("venues") is None and connectors:
            kwargs["venues"] = connectors[0].venues
        return cls(providers, **kwargs)

    # ── instruments ──────────────────────────────────────────────────

    def instrument(self, instrument_id: "str | InstrumentId") -> Instrument:
        return self.instruments([instrument_id])[0]

    def instruments(self, instrument_ids: Iterable["str | InstrumentId"]) -> List[Instrument]:
        """The instruments, in the order asked; one provider call per venue for those not cached."""
        ids = [as_instrument_id(i) for i in instrument_ids]
        missing: Dict[Venue, List[InstrumentId]] = {}
        for iid in ids:
            if iid not in self._instruments and iid not in missing.get(iid.venue, []):
                missing.setdefault(iid.venue, []).append(iid)
        for venue, batch in missing.items():
            self._load(venue, batch)
        return [self._instruments[i] for i in ids]

    def find(self, instrument_id: "str | InstrumentId") -> Optional[Instrument]:
        """The cached instrument, without asking a provider."""
        return self._instruments.get(as_instrument_id(instrument_id))

    def lot_size(self, instrument_id: "str | InstrumentId") -> float:
        return self.instrument(instrument_id).quantity_step

    def lot_sizes(self, instrument_ids: Iterable["str | InstrumentId"]) -> Dict[InstrumentId, float]:
        return {i.id: i.quantity_step for i in self.instruments(instrument_ids)}

    def refresh(self, instrument_ids: Optional[Iterable["str | InstrumentId"]] = None) -> None:
        """Forget cached instruments (all of them, or these) so the next request asks again,
        e.g. after a provider that was down at start-up is back."""
        if instrument_ids is None:
            self._instruments.clear()
            return
        for i in instrument_ids:
            self._instruments.pop(as_instrument_id(i), None)

    @property
    def provider_calls(self) -> int:
        """How many times a provider was asked (for tests and the ops log)."""
        return self._provider_calls

    def _load(self, venue: Venue, batch: List[InstrumentId]) -> None:
        spec = self._venues.get(venue)
        provider = self._providers.get(venue)
        loaded: Dict[InstrumentId, Instrument] = {}
        reason = f"no instrument provider for {venue}"
        if provider is not None:
            self._provider_calls += 1
            try:
                loaded = {inst.id: inst for inst in provider.load(batch)}
                reason = "the provider did not return it"
            except Exception as exc:                       # noqa: BLE001 - any provider failure falls back
                # InstrumentProvider.load raises when any one instrument is unknown, after caching
                # the ones it did load: keep those, so one delisted code does not cost the whole
                # batch its real board lots.
                find = getattr(provider, "find", None)
                for iid in batch:
                    found = find(iid) if find is not None else None
                    if found is not None:
                        loaded[iid] = found
                reason = f"the provider failed: {exc}"
                log.warning("%s: instrument lookup failed (%s); %d of %d instruments use the venue's "
                            "lot rule", venue, exc, len(batch) - len(loaded), len(batch))
        for iid in batch:
            inst = loaded.get(iid)
            if inst is None:
                inst = self._fallback(iid, spec, reason)
            else:
                inst = self._with_lot_rule(inst, spec)
            self._instruments[iid] = inst

    @staticmethod
    def _fallback(iid: InstrumentId, spec: VenueSpec, reason: str) -> Instrument:
        inst = spec.default_instrument(str(iid.symbol))
        if spec.lot_size_rule == "per_instrument" and not spec.is_crypto:
            log.warning("%s: board lot unknown (%s); using %s's default of %g, so orders may be "
                        "rejected as odd lots", iid, reason, spec.venue, spec.default_lot_size)
        return dataclasses.replace(inst, info={**inst.info, "lot_size_source": f"venue lot rule ({reason})"})

    @staticmethod
    def _with_lot_rule(inst: Instrument, spec: VenueSpec) -> Instrument:
        """The provider's instrument, with the venue's lot rule where it gave no usable lot."""
        if isinstance(inst, CurrencyPair):
            if inst.quantity_step > 0:
                return inst
            return dataclasses.replace(inst, size_increment=spec.default_lot_size,
                                       info={**inst.info, "lot_size_source": "venue lot rule"})
        if inst.lot_size and inst.lot_size > 0:
            return inst
        if spec.lot_size_rule == "per_instrument":
            log.warning("%s: the provider gave no board lot; using %s's default of %g",
                        inst.id, spec.venue, spec.default_lot_size)
        return dataclasses.replace(inst, lot_size=spec.default_lot_size,
                                   info={**inst.info, "lot_size_source": "venue lot rule"})

    # ── price ticks ──────────────────────────────────────────────────

    def _tick_tables(self) -> Dict[Venue, List[tuple]]:
        """Venues whose tick depends on the price (config/venues.json `tick_table`, HKEX's
        spread table): [(upto, tick), ...], the last `upto` None."""
        tables = getattr(self, "_ticks", None)
        if tables is None:
            import json
            from midfreq.core.venues.spec import DEFAULT_VENUES_PATH
            tables = {}
            try:
                raw = json.loads(Path(self._venues_path or DEFAULT_VENUES_PATH).read_text()).get("venues", {})
            except (OSError, ValueError) as exc:
                log.warning("tick tables not read (%s): instruments' own price increments only", exc)
                raw = {}
            for mic, d in raw.items():
                bands = (d.get("tick_table") or {}).get("bands")
                if bands:
                    tables[as_venue(mic)] = [(None if u is None else float(u), float(t)) for u, t in bands]
            self._ticks = tables
        return tables

    def tick_size(self, instrument_id: "str | InstrumentId", price: Optional[float] = None) -> float:
        """The tick a price must be a multiple of: the venue's spread table at `price` where it
        has one (HKEX: 0.2 between 200 and 500), else the instrument's price increment."""
        iid = as_instrument_id(instrument_id)
        table = self._tick_tables().get(iid.venue)
        if table and price is not None and price > 0:
            for upto, tick in table:
                if upto is None or price < upto:
                    return tick
        return self.instrument(iid).price_increment

    # ── calendars ────────────────────────────────────────────────────

    def calendar(self, venue: "str | Venue") -> TradingCalendar:
        v = as_venue(venue)
        cal = self._calendars.get(v)
        if cal is None:
            cal = self._calendars[v] = VenueCalendar.for_venue(v, self._venues, self._venues_path)
        return cal

    # ── corporate actions and cash flows ─────────────────────────────

    def corporate_actions(self, instrument_id: "str | InstrumentId", start: date, end: date) -> List[CorporateAction]:
        """Dividends and splits with an ex-date in [start, end]: the curated file first, then the
        instrument's venue's cash-flow sources; one entry per action."""
        iid = as_instrument_id(instrument_id)
        groups: List[Sequence[CorporateAction]] = [self._ca_file.corporate_actions(iid, start, end)]
        for src in self._cash_sources:
            if src.venue == iid.venue:
                try:
                    groups.append(src.corporate_actions(iid, start, end))
                except Exception as exc:                   # noqa: BLE001 - a venue source must not break the file's answer
                    log.warning("%s: cash-flow source failed (%s); corporate actions from the file only",
                                src.venue, exc)
        return merge_actions(groups)

    def cash_flows(self, venue: "str | Venue", start: date, end: date) -> List[CashFlow]:
        """What the venue's cash-flow sources report for [start, end], oldest first."""
        v = as_venue(venue)
        out: List[CashFlow] = []
        for src in self._cash_sources:
            if src.venue == v:
                out.extend(src.cash_flows(start, end))
        return sorted(out, key=lambda f: f.ts)

    def add_cash_flow_source(self, source: VenueCashFlowSource) -> None:
        self._cash_sources.append(source)
