"""
The contract between the execution contract suite and each connector's fake venue.

Each connector that trades ships a fake of its venue's SDK in tests/support (fake_moomoo.py,
fake_ccxt.py) with a factory

    make_<venue>_execution_client(**options) -> (ExecutionClient, VenueDriver)

returning a real ExecutionClient wired to the fake, and a driver through which a test plays the
venue's side. The suite (packages/midfreq-data/tests/test_execution_contract.py) runs the same
scenarios against every factory: what the client emits must be the same whatever the venue.
"""
from __future__ import annotations

from typing import List, Optional, Protocol


class VenueDriver(Protocol):
    def last_order_id(self) -> str:
        """The venue's id of the most recently submitted order."""

    def submitted(self) -> List[dict]:
        """Every order the venue received, oldest first: at least symbol, side, quantity, price,
        order type and the client order id the client sent (if the venue carries one)."""

    def accept(self, venue_order_id: str) -> None: ...

    def fill(self, venue_order_id: str, quantity: float, price: float, trade_id: Optional[str] = None) -> None:
        """One execution; several calls make partial fills."""

    def reject(self, venue_order_id: str, reason: str = "") -> None: ...

    def cancel_ack(self, venue_order_id: str) -> None:
        """The venue confirms a cancel the client asked for (or cancels on its own)."""

    def refuse_cancel(self, venue_order_id: str, reason: str = "") -> None:
        """The venue refuses the next cancel of this order (e.g. it has already filled)."""

    def repeat_last_push(self) -> None:
        """Deliver the venue's last push again (venues do this after reconnects)."""

    def disconnect(self) -> None: ...

    def reconnect(self) -> None: ...
