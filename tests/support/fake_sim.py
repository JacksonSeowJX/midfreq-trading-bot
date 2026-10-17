"""
The simulated venue (midfreq.trading.testing) behind the execution contract suite's VenueDriver.

make_sim_execution_client(**options) -> (SimulatedExecutionClient, SimDriver): the client with its
matching off (auto=False), so the driver plays the venue exactly as the fakes of real venues do.
Options go to SimulatedExecutionClient (environment='live' is refused, as it must be).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from midfreq.core.common.clock import TestClock
from midfreq.trading.testing import SimulatedExecutionClient

START_NS = int(pd.Timestamp("2026-10-17 13:30", tz="UTC").value)


class SimDriver:
    """The VenueDriver (tests/support/venue_driver.py) for SimulatedExecutionClient."""

    def __init__(self, sim: SimulatedExecutionClient, clock: TestClock):
        self.sim = sim
        self.clock = clock

    def _tick(self) -> None:
        self.clock.set_time(self.clock.timestamp_ns() + 1_000_000)   # every venue action a millisecond later

    def last_order_id(self) -> str:
        return self.sim.last_order_id()

    def submitted(self) -> List[dict]:
        return self.sim.submitted()

    def accept(self, venue_order_id: str) -> None:
        self._tick()
        self.sim.venue_accept(venue_order_id)

    def fill(self, venue_order_id: str, quantity: float, price: float, trade_id: Optional[str] = None) -> None:
        self._tick()
        self.sim.venue_fill(venue_order_id, quantity, price, trade_id)

    def reject(self, venue_order_id: str, reason: str = "") -> None:
        self._tick()
        self.sim.venue_reject(venue_order_id, reason)

    def cancel_ack(self, venue_order_id: str) -> None:
        self._tick()
        self.sim.venue_cancel(venue_order_id, "canceled")

    def refuse_cancel(self, venue_order_id: str, reason: str = "") -> None:
        self.sim.venue_refuse_cancel(venue_order_id, reason)

    def repeat_last_push(self) -> None:
        self.sim.repeat_last_push()

    def disconnect(self) -> None:
        self.sim.venue_disconnect()

    def reconnect(self) -> None:
        self.sim.venue_reconnect()


def make_sim_execution_client(**options) -> Tuple[SimulatedExecutionClient, SimDriver]:
    clock = options.pop("clock", None) or TestClock(START_NS)
    options.setdefault("auto", False)
    client = SimulatedExecutionClient(clock, **options)
    return client, SimDriver(client, clock)
