"""
Retry with exponential backoff, shared by every connector.

Each data source signals "slow down" differently (Binance HTTP 429/418 with Retry-After, Moomoo a
frequency-limit error string, IBKR a pacing violation, ccxt a RateLimitExceeded exception), so a
connector supplies the predicate that recognises its own rate-limit errors and this module does the
waiting. `sleep` is injectable so tests run instantly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

log = logging.getLogger(__name__)


class RateLimitError(Exception):
    """A data source asked us to slow down. `retry_after` (seconds) is honoured when given."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class RetryPolicy:
    max_retries: int = 5
    backoff_base: float = 1.0      # seconds before the first retry
    backoff_max: float = 60.0      # cap on any single wait
    sleep: Callable[[float], None] = time.sleep

    def delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Seconds to wait before retry number `attempt` (1-based)."""
        if retry_after is not None:
            return min(float(retry_after), self.backoff_max)
        return min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)

    def call(self, fn: Callable[[], T], is_rate_limit: Callable[[BaseException], bool],
             retry_after: Callable[[BaseException], Optional[float]] = lambda e: getattr(e, "retry_after", None),
             what: str = "request") -> T:
        """Run `fn`, retrying while it raises an error `is_rate_limit` recognises. Any other error,
        or running out of retries, propagates to the caller unchanged."""
        attempt = 0
        while True:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - filtered by the predicate below
                if not is_rate_limit(exc) or attempt >= self.max_retries:
                    raise
                attempt += 1
                wait = self.delay(attempt, retry_after(exc))
                log.warning("%s rate-limited (%s); retry %d/%d in %.1fs",
                            what, exc, attempt, self.max_retries, wait)
                self.sleep(wait)


class Throttle:
    """Keeps at least `min_interval` seconds between consecutive requests to one source."""

    def __init__(self, min_interval: float = 0.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = self._clock()
        if self._last is not None:
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last = now
