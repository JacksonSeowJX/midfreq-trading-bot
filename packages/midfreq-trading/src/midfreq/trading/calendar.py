"""
Trading calendars per venue: when an exchange is open, in true UTC.

A `VenueCalendar` combines what config/venues.json says about a venue:

- `sessions` (exchange-local wall-clock times; Hong Kong's two sessions leave its lunch break
  12:00-13:00 closed) and `weekdays`, which VenueSpec already loads;
- `holidays`: ISO dates on which the exchange is closed all day;
- `early_closes`: {ISO date: "HH:MM"}, the local time trading ends on a shortened day (13:00 in
  New York the day after Thanksgiving; 12:00 in Hong Kong on the eves of Christmas, New Year
  and Lunar New Year, when only the morning session runs);
- `holiday_sources`: {year: URL of the exchange's published list}. The years listed are the years
  the holiday list is known to be complete for. Outside them the calendar knows only weekends,
  and says so once in the log.

Those three fields are additive: VenueSpec ignores them, so they are read here from the same file.
A 24/7 venue (crypto) has no sessions; its calendar treats each UTC day as one session.

`session(day)` is the TradingCalendar contract (first open and last close of the day); a venue
with a lunch break has more than one entry in `sessions(day)`, which `is_open`, `next_open`,
`next_close` and `open_intervals` all respect.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple, Union

import pandas as pd

from midfreq.core.model.identifiers import Venue, as_venue
from midfreq.core.venues.spec import DEFAULT_VENUES_PATH, VenueRegistry, VenueSpec, default_venues

log = logging.getLogger(__name__)

__all__ = ["HolidayData", "VenueCalendar", "load_calendars", "load_holiday_data"]

DayLike = Union[date, datetime, pd.Timestamp, str]
Interval = Tuple[pd.Timestamp, pd.Timestamp]

# How far next_open/next_close look ahead before giving up (a misconfigured venue with no
# weekdays would otherwise loop forever).
_MAX_SEARCH_DAYS = 400


@dataclass(frozen=True)
class HolidayData:
    """A venue's holiday list as config/venues.json gives it."""
    holidays: FrozenSet[date] = frozenset()
    early_closes: Mapping[date, time] = field(default_factory=dict)
    covered_years: FrozenSet[int] = frozenset()
    sources: Mapping[int, Tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, mic: str, d: Mapping) -> "HolidayData":
        try:
            holidays = frozenset(date.fromisoformat(s) for s in d.get("holidays", []))
            early = {date.fromisoformat(k): _parse_hhmm(v) for k, v in dict(d.get("early_closes", {})).items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{mic}: bad holidays/early_closes in venues.json: {exc}") from None
        clash = sorted(set(early) & holidays)
        if clash:
            raise ValueError(f"{mic}: {', '.join(map(str, clash))} listed both as a holiday and as an early close")
        raw_sources = d.get("holiday_sources", {})
        sources: Dict[int, Tuple[str, ...]] = {}
        if isinstance(raw_sources, Mapping):
            for year, urls in raw_sources.items():
                if str(year).startswith("_"):
                    continue
                sources[int(year)] = (urls,) if isinstance(urls, str) else tuple(urls)
        if sources:
            covered = frozenset(sources)
        else:
            covered = frozenset({d_.year for d_ in holidays} | {d_.year for d_ in early})
        return cls(holidays, early, covered, sources)

    @property
    def empty(self) -> bool:
        return not self.holidays and not self.early_closes and not self.covered_years


def _parse_hhmm(value: str) -> time:
    hh, mm = str(value).split(":")
    return time(int(hh), int(mm))


@lru_cache(maxsize=8)
def _holiday_file(path: str) -> Dict[str, HolidayData]:
    with open(path) as f:
        raw = json.load(f)
    return {mic: HolidayData.from_dict(mic, d) for mic, d in raw.get("venues", {}).items()}


def load_holiday_data(path: "Optional[Path | str]" = None) -> Dict[str, HolidayData]:
    """Every venue's holiday list from config/venues.json (or `path`), keyed by venue code."""
    return dict(_holiday_file(str(Path(path) if path else DEFAULT_VENUES_PATH)))


def _as_utc(value: "pd.Timestamp | datetime | str | int") -> pd.Timestamp:
    """A point in time as a UTC Timestamp; naive values are read as UTC, ints as epoch ns."""
    if isinstance(value, int):
        return pd.Timestamp(value, unit="ns", tz="UTC")
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


class VenueCalendar:
    """The TradingCalendar of one venue (midfreq.trading.interfaces.TradingCalendar)."""

    def __init__(self, spec: VenueSpec, holidays: Iterable[date] = (),
                 early_closes: Optional[Mapping[date, time]] = None,
                 covered_years: Optional[Iterable[int]] = None,
                 sources: Optional[Mapping[int, Tuple[str, ...]]] = None):
        self.spec = spec
        self._holidays: FrozenSet[date] = frozenset(holidays)
        self._early: Dict[date, time] = dict(early_closes or {})
        if covered_years is None:
            covered_years = {d.year for d in self._holidays} | {d.year for d in self._early}
        self._covered: FrozenSet[int] = frozenset(covered_years)
        self.sources: Dict[int, Tuple[str, ...]] = dict(sources or {})
        self._warned: Set[object] = set()

    # ── construction ─────────────────────────────────────────────────

    @classmethod
    def from_data(cls, spec: VenueSpec, data: Optional[HolidayData]) -> "VenueCalendar":
        data = data or HolidayData()
        return cls(spec, data.holidays, data.early_closes, data.covered_years, data.sources)

    @classmethod
    def for_venue(cls, venue: "str | Venue", venues: Optional[VenueRegistry] = None,
                  path: "Optional[Path | str]" = None) -> "VenueCalendar":
        """The calendar of `venue`: sessions from the registry (config/venues.json by default),
        holidays and early closes from `path` (config/venues.json by default)."""
        v = as_venue(venue)
        spec = (venues or default_venues()).get(v)
        return cls.from_data(spec, load_holiday_data(path).get(str(v)))

    # ── facts ────────────────────────────────────────────────────────

    @property
    def venue(self) -> Venue:
        return self.spec.venue

    @property
    def timezone(self) -> str:
        return self.spec.timezone

    @property
    def is_24_7(self) -> bool:
        return self.spec.is_24_7

    @property
    def holidays(self) -> FrozenSet[date]:
        return self._holidays

    @property
    def early_closes(self) -> Dict[date, time]:
        return dict(self._early)

    def knows_holidays(self, year: int) -> bool:
        """Whether the holiday list for `year` is known (listed in holiday_sources)."""
        return self.is_24_7 or year in self._covered

    def early_close(self, day: DayLike) -> Optional[time]:
        """The local closing time of a shortened day, or None on a normal day."""
        return self._early.get(self._day(day))

    # ── the TradingCalendar contract ─────────────────────────────────

    def is_trading_day(self, day: DayLike) -> bool:
        d = self._day(day)
        if d.weekday() not in self.spec.weekdays:
            return False
        if self.is_24_7:
            return d not in self._holidays
        self._check_known(d.year)
        return d not in self._holidays

    def session(self, day: DayLike) -> Optional[Interval]:
        """(first open, last close) of the day in UTC, None on a holiday or weekend. An early
        close shortens it; a lunch break is inside it (see `sessions` for the parts)."""
        parts = self.sessions(day)
        if not parts:
            return None
        return parts[0][0], parts[-1][1]

    def next_open(self, after: "pd.Timestamp | datetime | str | int") -> pd.Timestamp:
        """The first moment strictly after `after` at which a session starts, in UTC. The end of
        Hong Kong's lunch break (13:00) is such a moment. For a 24/7 venue it is the next UTC
        midnight (its sessions are UTC days); it is always open anyway."""
        ts = _as_utc(after)
        for d in self._days_from(ts):
            for start, _ in self.sessions(d):
                if start > ts:
                    return start
        raise ValueError(f"{self.venue}: no session opens within {_MAX_SEARCH_DAYS} days after {ts}")

    # ── beyond the contract ──────────────────────────────────────────

    def sessions(self, day: DayLike) -> List[Interval]:
        """Every continuous trading session of the day as (open, close) in UTC, in order: two in
        Hong Kong (the lunch break between them), one in New York, none on a holiday or weekend.
        An early close cuts the session it falls in and drops any that would start after it."""
        d = self._day(day)
        if not self.is_trading_day(d):
            return []
        if self.is_24_7:
            start = pd.Timestamp(d).tz_localize("UTC")
            return [(start, start + pd.Timedelta(days=1))]
        cut = self._early.get(d)
        out: List[Interval] = []
        for s in self.spec.sessions:
            open_t, close_t = s.open, s.close
            if cut is not None:
                if open_t >= cut:
                    continue
                close_t = min(close_t, cut)
            out.append((self._utc(d, open_t), self._utc(d, close_t)))
        return out

    def is_open(self, ts: "pd.Timestamp | datetime | str | int") -> bool:
        t = _as_utc(ts)
        local = t.tz_convert(self.timezone).date()
        for d in (local - timedelta(days=1), local):
            for start, end in self.sessions(d):
                if start <= t < end:
                    return True
        return False

    def next_close(self, after: "pd.Timestamp | datetime | str | int") -> pd.Timestamp:
        """The end of the session in progress at `after`, or of the next one, in UTC (12:00 in
        Hong Kong during the morning session: the lunch break counts as a close)."""
        ts = _as_utc(after)
        for d in self._days_from(ts):
            for _, end in self.sessions(d):
                if end > ts:
                    return end
        raise ValueError(f"{self.venue}: no session closes within {_MAX_SEARCH_DAYS} days after {ts}")

    def open_intervals(self, start: "pd.Timestamp | datetime | str | int",
                       end: "pd.Timestamp | datetime | str | int") -> List[Interval]:
        """The parts of [start, end) during which the venue is open, in UTC, merged and in order."""
        s, e = _as_utc(start), _as_utc(end)
        if e <= s:
            return []
        out: List[Interval] = []
        d = s.tz_convert(self.timezone).date() - timedelta(days=1)
        last = e.tz_convert(self.timezone).date()
        while d <= last:
            for o, c in self.sessions(d):
                lo, hi = max(o, s), min(c, e)
                if lo >= hi:
                    continue
                if out and out[-1][1] >= lo:
                    out[-1] = (out[-1][0], max(out[-1][1], hi))
                else:
                    out.append((lo, hi))
            d += timedelta(days=1)
        return out

    def open_duration(self, start: "pd.Timestamp | datetime | str | int",
                      end: "pd.Timestamp | datetime | str | int") -> pd.Timedelta:
        """How much of [start, end) the venue was open for."""
        total = pd.Timedelta(0)
        for o, c in self.open_intervals(start, end):
            total += c - o
        return total

    # ── helpers ──────────────────────────────────────────────────────

    def _day(self, day: DayLike) -> date:
        """A calendar date on the venue's own clock. A tz-aware time is converted to the venue's
        timezone first; a naive datetime or a string is taken at its face value."""
        if isinstance(day, pd.Timestamp):
            if day.tzinfo is not None:
                day = day.tz_convert(self.timezone)
            return day.date()
        if isinstance(day, datetime):
            if day.tzinfo is not None:
                day = day.astimezone(self.spec.tz)
            return day.date()
        if isinstance(day, date):
            return day
        if isinstance(day, str):
            return pd.Timestamp(day).date()
        raise TypeError(f"not a day: {day!r}")

    def _utc(self, d: date, t: time) -> pd.Timestamp:
        return pd.Timestamp(datetime.combine(d, t)).tz_localize(self.timezone).tz_convert("UTC")

    def _days_from(self, ts: pd.Timestamp):
        d = ts.tz_convert(self.timezone).date() - timedelta(days=1)
        for i in range(_MAX_SEARCH_DAYS + 1):
            yield d + timedelta(days=i)

    def _check_known(self, year: int) -> None:
        if year in self._covered:
            return
        key = ("year", year) if self._covered else ("none",)
        if key in self._warned:
            return
        self._warned.add(key)
        if self._covered:
            log.warning("%s: no holiday list for %d (known: %s); only weekends are treated as closed",
                        self.venue, year, ", ".join(map(str, sorted(self._covered))))
        else:
            log.warning("%s: config/venues.json has no holiday list for this venue; only weekends "
                        "are treated as closed", self.venue)

    def __repr__(self) -> str:
        years = ", ".join(map(str, sorted(self._covered))) or "none"
        return f"VenueCalendar({self.venue}, holidays known for: {years})"


def load_calendars(venues: Optional[VenueRegistry] = None,
                   path: "Optional[Path | str]" = None) -> Dict[str, VenueCalendar]:
    """A calendar for every venue in the registry, keyed by venue code."""
    reg = venues or default_venues()
    data = load_holiday_data(path)
    return {str(spec.venue): VenueCalendar.from_data(spec, data.get(str(spec.venue))) for spec in reg}
