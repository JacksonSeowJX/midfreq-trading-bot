"""
Timestamp conventions.

Every timestamp inside the venue engine is an integer count of nanoseconds since the Unix epoch in UTC, as in
NautilusTrader. Integers sort and compare exactly, carry no timezone ambiguity, and are what the
catalog stores. These helpers convert at the edges: user input (dates, strings, datetimes) and
data sources (exchange-local clocks).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Union

import pandas as pd

TimeLike = Union[int, str, date, datetime, pd.Timestamp]

NANOS_PER_SECOND = 1_000_000_000
NANOS_PER_MILLI = 1_000_000


def to_utc_timestamp(value: TimeLike) -> pd.Timestamp:
    """Any time-like value as a tz-aware UTC Timestamp. Naive values are taken to be UTC;
    integers are nanoseconds since the epoch."""
    if isinstance(value, int):
        return pd.Timestamp(value, unit="ns", tz="UTC")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def to_ns(value: TimeLike) -> int:
    """Nanoseconds since the epoch (UTC) for any time-like value."""
    if isinstance(value, int):
        return value
    return int(to_utc_timestamp(value).value)


def ns_to_timestamp(ns: int) -> pd.Timestamp:
    """UTC Timestamp for an epoch-nanosecond integer."""
    return pd.Timestamp(int(ns), unit="ns", tz="UTC")


def ms_to_ns(ms: int) -> int:
    return int(ms) * NANOS_PER_MILLI


def ns_to_ms(ns: int) -> int:
    return int(ns) // NANOS_PER_MILLI


def is_date_only(value: TimeLike) -> bool:
    """True for a calendar date with no time of day ('2024-01-31', date(2024, 1, 31)). Used so a
    command-line `--end 2024-01-31` means the whole of that day rather than its first instant."""
    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    if isinstance(value, str):
        return len(value.strip()) == 10 and value.strip()[4] == "-"
    return False


def localize_index(index: pd.DatetimeIndex, tz: str) -> pd.DatetimeIndex:
    """
    Read naive (or wrongly UTC-tagged) wall-clock times as times in `tz` and return true UTC.

    Exchange clocks are what Moomoo and IBKR report, so this is the step that turns a bar stamped
    "10:30" in Hong Kong into 02:30 UTC. Wall-clock times that do not exist or are ambiguous in a
    daylight-saving change come back as NaT for the caller to drop; regular trading hours never
    contain one.
    """
    if index.tz is not None:
        index = index.tz_localize(None)
    local = index.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    return local.tz_convert("UTC")
