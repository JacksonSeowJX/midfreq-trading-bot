"""Clocks. In a backtest time does not flow by itself: the engine moves the TestClock to each
bar's timestamp, so `clock.timestamp_ns()` inside a strategy is the data time, never wall time."""
from __future__ import annotations

import time

import pandas as pd


class Clock:
    def timestamp_ns(self) -> int:
        raise NotImplementedError

    def utc_now(self) -> pd.Timestamp:
        return pd.Timestamp(self.timestamp_ns(), unit="ns", tz="UTC")


class TestClock(Clock):
    __test__ = False   # not a pytest test class

    def __init__(self, start_ns: int = 0):
        self._ns = start_ns

    def set_time(self, ns: int) -> None:
        if ns < self._ns:
            raise ValueError(f"time cannot go backwards ({ns} < {self._ns})")
        self._ns = ns

    def timestamp_ns(self) -> int:
        return self._ns


class LiveClock(Clock):
    def timestamp_ns(self) -> int:
        return time.time_ns()
