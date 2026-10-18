"""
The OMS's order state machine: what each order event does to an order, given the order's status.

The state machine (TRANSITIONS below), for each (current status, event) pair:

    apply    a legal transition
    race     a fill after the order was cancelled or rejected: the cancel and the fill crossed at
             the venue, or the rejection was a guess (a failed submit call, a client that found
             no trace of the order in time) and the venue had the order after all. The venue's
             fill is the truth, so it is always applied (WARNING); the order becomes FILLED if
             that completes it, else keeps its status. Reconciliation then cancels at the venue
             whatever it still lists open (see `reconcile`).
    stale    a late or repeated acknowledgement (an accept overtaken by the fill push, a second
             cancel confirmation): nothing changes (INFO)
    illegal  anything else (a fill on a FILLED, REJECTED or DENIED order, a venue event on an
             order never sent): ERROR, ignored; reconciliation reports what remains

`transition` looks a pair up and `_apply` gives the order its new state; `venue_status` reads a
venue's own status word, from a query of the order, as a closed status.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple

from midfreq.core.model.enums import OrderStatus
from midfreq.core.model.events import (
    OrderAccepted, OrderCanceled, OrderCancelRejected, OrderDenied, OrderEvent, OrderFilled, OrderRejected,
    OrderSubmitted,
)
from midfreq.core.model.orders import Order

__all__ = ["Outcome", "TRANSITIONS", "transition", "venue_status"]

_QTY_EPS = 1e-9


# ── the state machine ──────────────────────────────────────────────────────────────────────────

class Outcome(str, Enum):
    APPLY = "apply"
    RACE = "race"
    STALE = "stale"
    ILLEGAL = "illegal"
    DUPLICATE = "duplicate"     # a fill whose trade id was already applied
    UNKNOWN = "unknown"         # an event for an order the OMS does not have


_S = OrderStatus
TRANSITIONS: Dict[Tuple[OrderStatus, type], Outcome] = {
    (_S.INITIALIZED, OrderSubmitted): Outcome.APPLY,
    (_S.INITIALIZED, OrderDenied): Outcome.APPLY,

    (_S.SUBMITTED, OrderSubmitted): Outcome.STALE,
    (_S.SUBMITTED, OrderAccepted): Outcome.APPLY,
    (_S.SUBMITTED, OrderRejected): Outcome.APPLY,
    (_S.SUBMITTED, OrderCanceled): Outcome.APPLY,
    (_S.SUBMITTED, OrderFilled): Outcome.APPLY,

    (_S.ACCEPTED, OrderSubmitted): Outcome.STALE,
    (_S.ACCEPTED, OrderAccepted): Outcome.STALE,
    (_S.ACCEPTED, OrderRejected): Outcome.APPLY,     # a venue may fail a resting order
    (_S.ACCEPTED, OrderCanceled): Outcome.APPLY,
    (_S.ACCEPTED, OrderFilled): Outcome.APPLY,

    (_S.PARTIALLY_FILLED, OrderSubmitted): Outcome.STALE,
    (_S.PARTIALLY_FILLED, OrderAccepted): Outcome.STALE,   # the fill push overtook the acknowledgement
    (_S.PARTIALLY_FILLED, OrderCanceled): Outcome.APPLY,
    (_S.PARTIALLY_FILLED, OrderFilled): Outcome.APPLY,

    (_S.FILLED, OrderSubmitted): Outcome.STALE,
    (_S.FILLED, OrderAccepted): Outcome.STALE,

    (_S.CANCELED, OrderFilled): Outcome.RACE,
    (_S.CANCELED, OrderCanceled): Outcome.STALE,
    (_S.CANCELED, OrderSubmitted): Outcome.STALE,
    (_S.CANCELED, OrderAccepted): Outcome.STALE,

    (_S.REJECTED, OrderRejected): Outcome.STALE,
    (_S.REJECTED, OrderFilled): Outcome.RACE,        # the rejection was a guess: the venue's fill is the truth
    (_S.REJECTED, OrderSubmitted): Outcome.STALE,

    (_S.DENIED, OrderDenied): Outcome.STALE,

    # A refused cancel changes no state: applied (journaled, published, acted on) while the order
    # works, a late answer once it has closed.
    (_S.SUBMITTED, OrderCancelRejected): Outcome.APPLY,
    (_S.ACCEPTED, OrderCancelRejected): Outcome.APPLY,
    (_S.PARTIALLY_FILLED, OrderCancelRejected): Outcome.APPLY,
    (_S.FILLED, OrderCancelRejected): Outcome.STALE,
    (_S.CANCELED, OrderCancelRejected): Outcome.STALE,
    (_S.REJECTED, OrderCancelRejected): Outcome.STALE,
    (_S.DENIED, OrderCancelRejected): Outcome.STALE,
}


def transition(status: OrderStatus, event: OrderEvent) -> Outcome:
    return TRANSITIONS.get((status, type(event)), Outcome.ILLEGAL)


def venue_status(status: Optional[str]) -> Optional[OrderStatus]:
    """A venue's own status word as a closed OrderStatus (FILLED, CANCELED, REJECTED), or None
    while the order may still change. Moomoo: FILLED_ALL, CANCELLED_PART/ALL, FAILED, DELETED,
    DISABLED, SUBMIT_FAILED (CANCELLING_* is not final); ccxt: closed, canceled, expired, rejected."""
    if not status:
        return None
    s = str(status).strip().upper().replace(" ", "_")
    if s.startswith("FILLED_ALL") or s in ("FILLED", "CLOSED"):
        return OrderStatus.FILLED
    if "CANCELLING" in s or "CANCELING" in s or "PENDING_CANCEL" in s:
        return None
    if any(w in s for w in ("CANCELLED", "CANCELED", "DELETED", "EXPIRED")):
        return OrderStatus.CANCELED
    if any(w in s for w in ("FAILED", "REJECT", "DISABLED")):
        return OrderStatus.REJECTED
    return None


def _complete(order: Order) -> bool:
    return order.filled_qty >= order.quantity - _QTY_EPS * max(1.0, order.quantity)


def _apply(order: Order, event: OrderEvent) -> None:
    """The order's new state after `event` (the transition was already checked)."""
    order.ts_last = event.ts_event
    if isinstance(event, OrderSubmitted):
        order.status = OrderStatus.SUBMITTED
    elif isinstance(event, OrderAccepted):
        order.status = OrderStatus.PARTIALLY_FILLED if order.filled_qty > 0 else OrderStatus.ACCEPTED
    elif isinstance(event, OrderDenied):
        order.status, order.reason = OrderStatus.DENIED, event.reason
    elif isinstance(event, OrderRejected):
        order.status, order.reason = OrderStatus.REJECTED, event.reason
    elif isinstance(event, OrderCanceled):
        order.status = OrderStatus.CANCELED
    elif isinstance(event, OrderCancelRejected):
        pass                                       # the order is as it was; the OMS asks the venue
    elif isinstance(event, OrderFilled):
        if order.filled_qty == 0 or order.avg_px is None:
            order.avg_px = event.last_px
        else:
            total = order.filled_qty + event.last_qty
            order.avg_px = (order.avg_px * order.filled_qty + event.last_px * event.last_qty) / total
        order.filled_qty += event.last_qty
        order.commission += event.commission
        if _complete(order):
            order.status = OrderStatus.FILLED
        elif order.status in (OrderStatus.CANCELED, OrderStatus.REJECTED):
            pass                                   # a late fill: the remainder stays cancelled
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
