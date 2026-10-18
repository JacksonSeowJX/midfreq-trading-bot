"""
Corporate actions (dividends, splits) for the trading node's reference data.

Two kinds of source:

- `JsonCorporateActionSource`: a curated file, config/corporate_actions.json, a JSON list of

      {"instrument_id": "00005.XHKG", "kind": "dividend", "ex_date": "2026-08-14",
       "amount": 0.10, "currency": "USD", "source": "HSBC interim results 2026"}

  `amount` is cash per share for a dividend and the ratio (new shares per old share: 2.0 for a
  2-for-1 split) for a split. Other keys (a pay date, a note) are allowed and ignored. A
  malformed entry is an error when the file is read, not something skipped quietly.

- `VenueCashFlowSource`: what a venue says it credited or debited to the account (Moomoo's
  account cash flow, an exchange's ledger). A connector implements `cash_flows`; dividends it can
  attribute to an instrument with a per-share amount and an ex-date also come back as
  CorporateActions. The rest stay cash flows, which the PositionKeeper books as cash: that is
  how a dividend that reached the broker's account reaches the bot's ledger too.

No source here calls a venue itself; venue sources are written in the connectors and handed in.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Protocol, Sequence

import pandas as pd

from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id
from midfreq.core.paths import config_dir
from midfreq.trading.core.interfaces import CorporateAction

log = logging.getLogger("midfreq.trading.corporate_actions")

__all__ = [
    "CashFlow", "CorporateActionSource", "JsonCorporateActionSource", "VenueCashFlowSource",
    "merge_actions", "parse_action",
]

KINDS = ("dividend", "split")


class CorporateActionSource(Protocol):
    def corporate_actions(self, instrument_id: InstrumentId, start: date, end: date) -> List[CorporateAction]:
        """Actions on `instrument_id` with an ex-date in [start, end], oldest first."""


def _as_date(value: Any, what: str) -> date:
    if isinstance(value, datetime):          # pd.Timestamp included
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{what}: not an ISO date: {value!r}") from None


def parse_action(entry: Mapping[str, Any], default_source: str = "") -> CorporateAction:
    """One corporate action from its JSON form; ValueError naming the problem otherwise."""
    if not isinstance(entry, Mapping):
        raise ValueError(f"a corporate action must be an object, got {entry!r}")
    missing = [k for k in ("instrument_id", "kind", "ex_date", "amount") if k not in entry]
    if missing:
        raise ValueError(f"corporate action {dict(entry)} lacks {missing}")
    kind = str(entry["kind"]).lower()
    if kind not in KINDS:
        raise ValueError(f"corporate action kind must be one of {KINDS}: {entry['kind']!r}")
    amount = float(entry["amount"])
    if not amount > 0:
        raise ValueError(f"corporate action amount must be positive: {entry['amount']!r}")
    return CorporateAction(
        instrument_id=as_instrument_id(str(entry["instrument_id"])),
        kind=kind,
        ex_date=_as_date(entry["ex_date"], "ex_date"),
        amount=amount,
        currency=str(entry.get("currency", "")),
        source=str(entry.get("source") or default_source),
    )


class JsonCorporateActionSource:
    """config/corporate_actions.json (or `path`), read once and kept; `reload()` re-reads it."""

    def __init__(self, path: "Optional[Path | str]" = None):
        self.path = Path(path) if path is not None else config_dir() / "corporate_actions.json"
        self._actions: Optional[List[CorporateAction]] = None

    def all(self) -> List[CorporateAction]:
        if self._actions is None:
            self._actions = self._read()
        return list(self._actions)

    def reload(self) -> None:
        self._actions = None

    def corporate_actions(self, instrument_id: "str | InstrumentId", start: date, end: date) -> List[CorporateAction]:
        iid = as_instrument_id(instrument_id)
        lo, hi = _as_date(start, "start"), _as_date(end, "end")
        out = [a for a in self.all() if a.instrument_id == iid and lo <= a.ex_date <= hi]
        return sorted(out, key=lambda a: (a.ex_date, a.kind))

    def _read(self) -> List[CorporateAction]:
        if not self.path.exists():
            log.info("no corporate actions file at %s", self.path)
            return []
        raw = json.loads(self.path.read_text() or "[]")
        if not isinstance(raw, list):
            raise ValueError(f"{self.path} must hold a JSON list of corporate actions")
        out = []
        for i, entry in enumerate(raw):
            try:
                out.append(parse_action(entry, default_source=f"file:{self.path.name}"))
            except ValueError as exc:
                raise ValueError(f"{self.path}, entry {i}: {exc}") from None
        return out


@dataclass(frozen=True)
class CashFlow:
    """One cash movement on an account as the venue reports it."""
    venue: Venue
    kind: str                               # 'dividend' | 'interest' | 'fee' | 'tax' | 'other'
    amount: float                           # credited (+) or debited (-), in `currency`
    currency: str
    ts: pd.Timestamp                        # when the venue booked it, UTC
    instrument_id: Optional[InstrumentId] = None
    per_share: Optional[float] = None       # for a dividend, where the venue says
    ex_date: Optional[date] = None
    reference: str = ""                     # the venue's own id for the movement
    description: str = ""                   # the venue's own words


class VenueCashFlowSource(ABC):
    """A venue's account cash flows, implemented by a connector (none is written yet)."""

    def __init__(self, venue: "str | Venue"):
        self.venue = venue if isinstance(venue, Venue) else Venue(str(venue).upper())

    @abstractmethod
    def cash_flows(self, start: date, end: date) -> List[CashFlow]:
        """Every cash movement booked in [start, end] (venue dates), oldest first."""

    def corporate_actions(self, instrument_id: "str | InstrumentId", start: date, end: date) -> List[CorporateAction]:
        """The dividends among the cash flows that name the instrument, a per-share amount and an
        ex-date in [start, end]. Cash flows without those cannot be turned into a per-share action.
        Cash is booked after the ex-date, so the search reaches 90 days past `end`."""
        iid = as_instrument_id(instrument_id)
        lo, hi = _as_date(start, "start"), _as_date(end, "end")
        flows = self.cash_flows(lo, (pd.Timestamp(hi) + pd.Timedelta(days=90)).date())
        out = []
        for f in flows:
            if (f.kind == "dividend" and f.instrument_id == iid and f.per_share and f.per_share > 0
                    and f.ex_date is not None and lo <= f.ex_date <= hi):
                out.append(CorporateAction(iid, "dividend", f.ex_date, float(f.per_share), f.currency,
                                           source=f"venue:{self.venue}" + (f" {f.reference}" if f.reference else "")))
        return sorted(out, key=lambda a: a.ex_date)


def merge_actions(groups: Iterable[Sequence[CorporateAction]]) -> List[CorporateAction]:
    """Actions from several sources, oldest first, one per (instrument, kind, ex-date, amount):
    the first source given wins, so the curated file is passed first."""
    seen = set()
    out: List[CorporateAction] = []
    for group in groups:
        for a in group:
            key = (a.instrument_id, a.kind, a.ex_date, round(a.amount, 10))
            if key in seen:
                continue
            seen.add(key)
            out.append(a)
    return sorted(out, key=lambda a: (a.ex_date, str(a.instrument_id), a.kind))
