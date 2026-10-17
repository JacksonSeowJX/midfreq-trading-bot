"""
OrderManagementSystem: the one owner of every order's state in the trading node.

    submit(order) -> id -> PreTradeRisk -> journal (write-ahead) -> SUBMITTED -> ExecutionClient
    venue events (from the node's queue) -> handle_event -> state machine -> journal
                 -> PositionKeeper (fills) -> bus: TOPIC_ORDER_EVENTS, TOPIC_FILLS
    on_timer(now) -> the unfilled-order policy (re-price, then cancel)
    reconcile() -> the venues' fills and open orders against ours

It runs on the node's single event-loop thread and never waits for a venue: `submit` returns
once the client has the order, and everything the venue says afterwards arrives as an event.
(The only venue calls that wait for an answer are the queries the policy and reconciliation make
when an event is overdue: what the original engine learned is that a venue's status lags, so a
missing event is checked with the venue rather than assumed.)

Client order ids are `<strategy>-<YYYYMMDD>-<seq>` (UTC date of the node's clock), the sequence
restored from the journal at start-up so an id never repeats across restarts. An order that
arrives with an id in that format, for its own strategy and not used before, keeps it; any other
(an OrderFactory's 'O-<strategy>-000001', or a repeat) is given a new one and its own id is kept
in `tags["strategy_order_id"]`. The order object is the strategy's own and is updated in place.

The state machine (TRANSITIONS below), for each (current status, event) pair:

    apply    a legal transition
    race     a fill after the order was cancelled: the cancel and the fill crossed at the venue.
             The venue's fill is the truth, so it is applied (WARNING); the order becomes FILLED
             if that completes it, else stays CANCELED. The same holds for a fill after a
             rejection the OMS only inferred from a failed submit call (the venue may have the
             order after all).
    stale    a late or repeated acknowledgement (an accept overtaken by the fill push, a second
             cancel confirmation): nothing changes (INFO)
    illegal  anything else (a fill on a FILLED, REJECTED or DENIED order, a venue event on an
             order never sent): ERROR, ignored; reconciliation reports what remains
A fill is applied at most once per (venue, trade id); a repeated push is ignored.

The unfilled-order policy (UnfilledOrderPolicy) is the fix for the Lam Research buy of 15 October
2026 that sat unfilled for 30 s and was cancelled with no retry and no reason recorded. Each
submitted order starts a family (the order and the child orders that replace it). When the
family's working order has not filled completely `wait_s` after it was sent:
- a LIMIT order is re-priced, up to `max_reprices` times: the OMS cancels it, waits for the
  venue's confirmation (never two live orders for the same intent), checks the venue's fills for
  it, and submits a child order for what the family still has to fill, priced from the price
  source (ask + quote_offset_bps for a buy, bid - quote_offset_bps for a sell; without a quote,
  the last price +/- last_offset_bps), through risk again; the child carries
  tags parent (the order it replaces), root (the family's first order) and attempt;
- after the last re-price, or for a MARKET order, what is left is cancelled with the reason
  "unfilled after N attempts";
- a cancel the venue has not confirmed after `cancel_timeout_s` is checked with the venue
  (its fills and its view of the order), then re-sent, up to `max_cancel_retries` times;
- a fill that arrives late on a replaced order (the cancel/fill race) can over-commit the family:
  the working child is then cancelled and replaced by one for what is really left.
Every step is logged and journaled with the venue's reply. A strategy's cancel of any order in a
family cancels the family's working order and stops the policy for it.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

import pandas as pd

from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from midfreq.core.model.events import (
    OrderAccepted, OrderCanceled, OrderDenied, OrderEvent, OrderFilled, OrderRejected, OrderSubmitted,
)
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId, TradeId, Venue
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import ExecutionClient, FillReport, OrderReport
from midfreq.trading.interfaces import (
    TOPIC_FILLS, TOPIC_ORDER_EVENTS, PositionKeeper, PreTradeRisk, ReconcileReport,
)
from midfreq.trading.journal import OrderJournal, format_order_id, parse_order_id

log = logging.getLogger(__name__)

__all__ = ["OrderManagementSystem", "Outcome", "TRANSITIONS", "UnfilledOrderPolicy", "venue_status"]

_NS = 1_000_000_000
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
    (_S.REJECTED, OrderSubmitted): Outcome.STALE,

    (_S.DENIED, OrderDenied): Outcome.STALE,
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


# ── the unfilled-order policy ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnfilledOrderPolicy:
    enabled: bool = True
    wait_s: float = 30.0                # how long an order may work without filling completely
    max_reprices: int = 2               # child orders after the first; then cancel the rest
    quote_offset_bps: float = 0.0       # buy at ask x (1 + bps), sell at bid x (1 - bps)
    last_offset_bps: float = 100.0      # with no quote: last x (1 +/- bps) (the original engine's 1%)
    cancel_timeout_s: float = 30.0      # a cancel not confirmed by then is checked with the venue
    max_cancel_retries: int = 3
    cancel_market_orders: bool = True   # cancel a MARKET order unfilled after wait_s
    confirm_before_reprice: bool = True  # ask the venue for the cancelled order's fills first


@dataclass
class _Family:
    root: ClientOrderId
    target_qty: float
    members: List[ClientOrderId]
    active: ClientOrderId
    attempts: int = 1                   # orders sent so far, the first included
    since_ns: int = 0                   # when the active order was sent
    state: str = "working"              # working | repricing | cancelling | done
    cancel_sent_ns: Optional[int] = None
    cancel_retries: int = 0
    resizing: bool = False              # the pending re-price replaces a child after a late fill


def _price_parts(snapshot: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(bid, ask, last) from whatever the price source returns: a QuoteTick or any object with
    bid/ask/last attributes (each may be missing or None), a dict with those keys, or a number
    (the last price)."""
    if snapshot is None:
        return None, None, None
    if isinstance(snapshot, (int, float)):
        return None, None, float(snapshot)
    if isinstance(snapshot, Mapping):
        get = snapshot.get
    else:
        def get(name, default=None):
            return getattr(snapshot, name, default)

    def positive(x):
        try:
            x = float(x)
        except (TypeError, ValueError):
            return None
        return x if math.isfinite(x) and x > 0 else None
    return positive(get("bid")), positive(get("ask")), positive(get("last", get("last_price")))


def _to_tick(price: float, tick: float, up: bool) -> float:
    if not tick or tick <= 0:
        return price
    units = Decimal(repr(price)) / Decimal(repr(float(tick)))
    units = units.to_integral_value(rounding="ROUND_CEILING" if up else "ROUND_FLOOR")
    return float(units * Decimal(repr(float(tick))))


def _unfilled_after(attempts: int) -> str:
    return f"unfilled after {attempts} attempt{'' if attempts == 1 else 's'}"


def _floor_to_lot(quantity: float, lot: Optional[float]) -> float:
    if not lot or lot <= 0:
        return quantity
    units = (Decimal(repr(quantity)) / Decimal(repr(float(lot)))).to_integral_value(rounding="ROUND_FLOOR")
    return float(units * Decimal(repr(float(lot))))


# ── the OMS ────────────────────────────────────────────────────────────────────────────────────

PriceSource = Callable[[InstrumentId], Any]
SizeSource = Callable[[InstrumentId], Optional[float]]


class OrderManagementSystem:
    """
    clients: the ExecutionClient of each venue, which the node has connected and whose event
    handler puts events on the node's queue (the queue's consumer calls `handle_event`).
    price_source: for re-pricing (see _price_parts). lot_size / tick_size: optional, to keep a
    child's quantity in whole lots and its price on the tick (buys round up, sells down).
    """

    def __init__(self, clock: Clock, msgbus: MessageBus, risk: PreTradeRisk, positions: PositionKeeper,
                 journal: OrderJournal, clients: Mapping[Venue, ExecutionClient],
                 policy: Optional[UnfilledOrderPolicy] = None,
                 price_source: Optional[PriceSource] = None,
                 lot_size: Optional[SizeSource] = None,
                 tick_size: Optional[SizeSource] = None,
                 reconcile_lookback_s: float = 86_400.0,
                 reconcile_grace_s: float = 10.0,
                 journal_lookback_days: Optional[int] = 30,
                 restore: bool = True):
        self._clock = clock
        self._msgbus = msgbus
        self._risk = risk
        self._positions = positions
        self._journal = journal
        self._clients: Dict[Venue, ExecutionClient] = dict(clients)
        self.policy = policy or UnfilledOrderPolicy()
        self._price_source = price_source
        self._lot_size = lot_size
        self._tick_size = tick_size
        self._reconcile_lookback_ns = int(reconcile_lookback_s * _NS)
        self._reconcile_grace_ns = int(reconcile_grace_s * _NS)

        self._orders: Dict[ClientOrderId, Order] = {}
        self._open: Dict[ClientOrderId, Order] = {}
        self._by_venue_id: Dict[Tuple[Venue, str], ClientOrderId] = {}
        self._trade_ids: Set[Tuple[str, str]] = set()
        self._seq: Dict[Tuple[str, str], int] = {}
        self._families: Dict[ClientOrderId, _Family] = {}     # root -> family, every family
        self._family_of: Dict[ClientOrderId, _Family] = {}    # any member -> its family
        self._working: Dict[ClientOrderId, _Family] = {}      # root -> family not yet done
        self._cancel_reason: Dict[ClientOrderId, str] = {}
        self._local_reject: Set[ClientOrderId] = set()
        self._venue_filled: Dict[ClientOrderId, float] = {}   # what a venue query said had filled

        bind = getattr(risk, "bind_open_orders", None)
        if callable(bind):
            bind(self.open_orders)
        if restore:
            self.restore(journal_lookback_days)

    # ── start-up ──────────────────────────────────────────────────────────────────────────────

    def restore(self, lookback_days: Optional[int] = 30) -> int:
        """Rebuild the book from the journal: every order, the applied fills' trade ids and the
        id sequences; open orders get a family again (their policy clock restarts now). Returns
        the number of open orders restored; `reconcile()` should follow."""
        state = self._journal.load(lookback_days)
        now = self._clock.timestamp_ns()
        for table in (self._by_venue_id, self._families, self._family_of, self._working):
            table.clear()
        self._orders = dict(state.orders)
        self._open = {cid: o for cid, o in self._orders.items()
                      if o.is_open and o.status is not OrderStatus.INITIALIZED}
        self._trade_ids = set(state.trade_ids)
        for key, seq in state.sequences.items():
            self._seq[key] = max(self._seq.get(key, 0), seq)
        for cid, order in self._orders.items():
            voi = order.tags.get("venue_order_id")
            if voi is not None:
                self._by_venue_id[(order.venue, str(voi))] = cid
        for cid, order in self._open.items():
            root = ClientOrderId(str(order.tags.get("root", cid)))
            if root in self._families:
                continue
            members = [c for c, o in self._orders.items() if c == root or str(o.tags.get("root")) == str(root)]
            target = self._orders[root].quantity if root in self._orders else order.quantity
            fam = _Family(root, target, members, cid, int(order.tags.get("attempt", 1) or 1), now)
            self._families[root] = fam
            self._working[root] = fam
            for m in members:
                self._family_of[m] = fam
        if self._open:
            log.warning("restored %d open orders from the journal: %s", len(self._open),
                        ", ".join(str(c) for c in self._open))
        return len(self._open)

    # ── ids ───────────────────────────────────────────────────────────────────────────────────

    def next_order_id(self, strategy_id: StrategyId) -> ClientOrderId:
        """A new id `<strategy>-<YYYYMMDD>-<seq>`, never used before (journal included)."""
        day = pd.Timestamp(self._clock.timestamp_ns(), unit="ns", tz="UTC").strftime("%Y%m%d")
        key = (str(strategy_id), day)
        seq = self._seq.get(key, 0) + 1
        cid = format_order_id(strategy_id, day, seq)
        while cid in self._orders:
            seq += 1
            cid = format_order_id(strategy_id, day, seq)
        self._seq[key] = seq
        return cid

    def _assign_id(self, order: Order) -> None:
        cid = order.client_order_id
        parsed = parse_order_id(cid) if cid is not None else None
        if parsed is not None and parsed[0] == str(order.strategy_id) and cid not in self._orders:
            key = (parsed[0], parsed[1])
            self._seq[key] = max(self._seq.get(key, 0), parsed[2])
            return
        new = self.next_order_id(order.strategy_id)
        if cid is not None:
            order.tags.setdefault("strategy_order_id", str(cid))
        order.client_order_id = new

    # ── OrderManager ──────────────────────────────────────────────────────────────────────────

    def submit(self, order: Order) -> None:
        self._submit(order, family=None)

    def _submit(self, order: Order, family: Optional[_Family]) -> None:
        existing = self._orders.get(order.client_order_id) if order.client_order_id is not None else None
        if existing is order:
            log.error("order %s submitted twice; the second submit is ignored", order.client_order_id)
            return
        if order.status is not OrderStatus.INITIALIZED:
            log.error("order %s is %s; only a new order can be submitted", order.client_order_id,
                      order.status.value)
            return
        if family is None:
            self._assign_id(order)
        cid = order.client_order_id
        now = self._clock.timestamp_ns()
        if not order.ts_init:
            order.ts_init = now
        self._orders[cid] = order

        client = self._clients.get(order.venue)
        reason = None if client is not None else f"no execution client for venue {order.venue}"
        if reason is None:
            try:
                reason = self._risk.check(order)
            except Exception as exc:  # noqa: BLE001 - deny rather than send an unchecked order
                log.exception("risk check of %s raised", cid)
                reason = f"risk check failed: {exc}"
        if reason is None:
            try:
                self._journal.record_order(order, now)          # write-ahead: journaled before sent
            except OSError as exc:
                log.critical("cannot write the order journal (%s); order %s not sent", exc, cid)
                reason = f"order journal write failed: {exc}"
        if reason is not None:
            self._journal_safe("order", now, order, note="denied")
            self._process(OrderDenied(cid, order.strategy_id, order.instrument_id, now, reason=reason))
            return

        self._process(OrderSubmitted(cid, order.strategy_id, order.instrument_id, now))
        if family is None and order.time_in_force is not TimeInForce.IOC:
            fam = _Family(cid, order.quantity, [cid], cid, int(order.tags.get("attempt", 1) or 1), now)
            self._families[cid] = fam
            self._working[cid] = fam
            self._family_of[cid] = fam
        try:
            client.submit_order(order)
        except Exception as exc:  # noqa: BLE001 - the venue call failed; the order is not working
            log.error("submit of %s to %s failed: %s", cid, order.venue, exc, extra=self._extra(order))
            if order.is_open:
                self._local_reject.add(cid)
                self._process(OrderRejected(cid, order.strategy_id, order.instrument_id,
                                            self._clock.timestamp_ns(), reason=f"submit failed: {exc}"))
            return
        voi = order.tags.get("venue_order_id")
        if voi is not None:
            self._by_venue_id[(order.venue, str(voi))] = cid
        self._journal_safe("sent", now, order)
        log.info("sent %s: %s %g %s %s%s (venue order id %s)", cid, order.side.value, order.quantity,
                 order.instrument_id, order.order_type.value,
                 f" @ {order.price:g}" if order.price is not None else "", voi, extra=self._extra(order))

    def cancel(self, client_order_id: ClientOrderId) -> None:
        order = self._orders.get(client_order_id)
        if order is None:
            log.warning("cancel of unknown order %s ignored", client_order_id)
            return
        fam = self._family_of.get(client_order_id)
        if fam is not None and fam.state != "done":
            target = self._orders[fam.active]
            fam.state = "cancelling"
            fam.cancel_sent_ns = self._clock.timestamp_ns()
            fam.cancel_retries = 0
            if target.is_open:
                self._send_cancel(target, "cancelled by request")
            else:
                self._finish(fam, "cancelled by request")
            return
        if order.is_closed:
            log.info("cancel of %s ignored: already %s", client_order_id, order.status.value)
            return
        self._send_cancel(order, "cancelled by request")

    def handle_event(self, event: OrderEvent) -> None:
        if not isinstance(event, OrderEvent):
            log.error("not an order event: %r", event)
            return
        self._process(event)

    def order(self, client_order_id: ClientOrderId) -> Optional[Order]:
        return self._orders.get(client_order_id)

    def orders(self) -> List[Order]:
        return list(self._orders.values())

    def open_orders(self, strategy_id: Optional[StrategyId] = None) -> List[Order]:
        return [o for o in self._open.values() if strategy_id is None or o.strategy_id == strategy_id]

    def family(self, client_order_id: ClientOrderId) -> List[Order]:
        """The order with every order that replaced it or that it replaced, oldest first."""
        fam = self._family_of.get(client_order_id)
        if fam is None:
            order = self._orders.get(client_order_id)
            return [order] if order is not None else []
        return [self._orders[m] for m in fam.members]

    # ── events ────────────────────────────────────────────────────────────────────────────────

    def _resolve(self, event: OrderEvent) -> Optional[Order]:
        order = self._orders.get(event.client_order_id) if event.client_order_id is not None else None
        if order is None:
            voi = getattr(event, "tags", {}).get("venue_order_id") if isinstance(event, OrderFilled) else None
            if voi is not None and event.instrument_id is not None:
                cid = self._by_venue_id.get((event.instrument_id.venue, str(voi)))
                order = self._orders.get(cid) if cid is not None else None
        return order

    def _process(self, event: OrderEvent) -> Outcome:
        now = self._clock.timestamp_ns()
        order = self._resolve(event)
        if order is None:
            log.error("event for an unknown order ignored: %r", event)
            self._journal_event(event, now, None, False, Outcome.UNKNOWN)
            return Outcome.UNKNOWN
        if (event.client_order_id != order.client_order_id or event.strategy_id != order.strategy_id
                or event.instrument_id != order.instrument_id):
            if event.strategy_id != order.strategy_id or event.instrument_id != order.instrument_id:
                log.warning("event ids %s/%s/%s corrected to the order's %s/%s/%s", event.client_order_id,
                            event.strategy_id, event.instrument_id, order.client_order_id, order.strategy_id,
                            order.instrument_id)
            event = dataclasses.replace(event, client_order_id=order.client_order_id,
                                        strategy_id=order.strategy_id, instrument_id=order.instrument_id)
        key = None
        if isinstance(event, OrderFilled):
            if event.trade_id is not None:
                key = (order.venue.value, str(event.trade_id))
                if key in self._trade_ids:
                    log.info("duplicate fill %s on %s ignored", event.trade_id, order.client_order_id,
                             extra=self._extra(order))
                    return Outcome.DUPLICATE
            else:
                log.warning("fill on %s has no trade id; it cannot be told from a repeat", order.client_order_id)
            if not event.last_qty > 0:
                log.error("fill on %s with quantity %r ignored", order.client_order_id, event.last_qty)
                self._journal_event(event, now, order, False, Outcome.ILLEGAL, message="non-positive quantity")
                return Outcome.ILLEGAL
            voi = event.tags.get("venue_order_id")
            if voi is not None and "venue_order_id" not in order.tags:
                order.tags["venue_order_id"] = voi
                self._by_venue_id[(order.venue, str(voi))] = order.client_order_id

        outcome = transition(order.status, event)
        if (outcome is Outcome.ILLEGAL and isinstance(event, OrderFilled)
                and order.status is OrderStatus.REJECTED and order.client_order_id in self._local_reject):
            outcome = Outcome.RACE
        previous = order.status
        if outcome is Outcome.ILLEGAL:
            log.error("illegal transition ignored: %s is %s, got %s %s", order.client_order_id, previous.value,
                      type(event).__name__, self._describe(event), extra=self._extra(order))
            self._journal_event(event, now, order, False, outcome, previous)
            return outcome
        if outcome is Outcome.STALE:
            log.info("%s on %s (%s) changes nothing: %s", type(event).__name__, order.client_order_id,
                     previous.value, self._describe(event), extra=self._extra(order))
            self._journal_event(event, now, order, False, outcome, previous)
            return outcome
        if outcome is Outcome.RACE:
            log.warning("fill after %s on %s: %s %g @ %g applied (the venue's fill is the truth; the cancel "
                        "and the fill crossed)", previous.value, order.client_order_id, event.side.value,
                        event.last_qty, event.last_px, extra=self._extra(order))

        _apply(order, event)
        if isinstance(event, OrderCanceled):
            ours = self._cancel_reason.pop(order.client_order_id, None)
            if ours:
                order.reason = f"{ours}; venue: {event.reason}" if event.reason else ours
            else:
                order.reason = event.reason or "cancelled by the venue"
            if order.reason != event.reason:
                event = dataclasses.replace(event, reason=order.reason)
        if isinstance(event, OrderFilled):
            if key is not None:
                self._trade_ids.add(key)
            if order.filled_qty > order.quantity + _QTY_EPS * max(1.0, order.quantity):
                log.error("overfill on %s: %g filled of %g", order.client_order_id, order.filled_qty,
                          order.quantity, extra=self._extra(order))
        if order.is_open and order.status is not OrderStatus.INITIALIZED:
            self._open[order.client_order_id] = order
        else:
            self._open.pop(order.client_order_id, None)
        if isinstance(event, OrderFilled):
            try:
                self._positions.on_fill(event)
            except Exception:  # noqa: BLE001 - the order state is right; the ledger must be looked at
                log.exception("position keeper failed on fill %s of %s", event.trade_id, order.client_order_id)
        self._journal_event(event, now, order, True, outcome, previous)
        self._log_applied(order, event, previous)
        self._msgbus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id), event)
        if isinstance(event, OrderFilled):
            self._msgbus.publish(TOPIC_FILLS, event)
        self._after_event(order, event)
        return outcome

    # ── the unfilled-order policy ─────────────────────────────────────────────────────────────

    def on_timer(self, now_ns: int) -> None:
        wait_ns = int(self.policy.wait_s * _NS)
        cancel_ns = int(self.policy.cancel_timeout_s * _NS)
        for fam in list(self._working.values()):
            if fam.state == "done":
                self._working.pop(fam.root, None)
                continue
            order = self._orders[fam.active]
            if fam.state == "working":
                if order.is_closed:
                    self._finish(fam, f"{order.client_order_id} is {order.status.value}")
                    continue
                if not self.policy.enabled or now_ns - fam.since_ns < wait_ns:
                    continue
                self._escalate(fam, order, now_ns)
            elif fam.cancel_sent_ns is not None and now_ns - fam.cancel_sent_ns >= cancel_ns:
                if order.is_closed:
                    self._after_close(fam, order)
                else:
                    self._overdue_cancel(fam, order, now_ns)

    def _escalate(self, fam: _Family, order: Order, now_ns: int) -> None:
        waited = (now_ns - fam.since_ns) / _NS
        if order.order_type is OrderType.LIMIT and fam.attempts <= self.policy.max_reprices:
            if self._reprice_price(order) is None:
                fam.state = "cancelling"
                reason = (f"{_unfilled_after(fam.attempts)} (no quote or last price to re-price "
                          f"{order.instrument_id})")
            else:
                fam.state = "repricing"
                reason = (f"re-price {fam.attempts}/{self.policy.max_reprices}: {order.leaves_qty:g} of "
                          f"{order.quantity:g} unfilled after {waited:.0f}s")
        elif order.order_type is OrderType.MARKET and not self.policy.cancel_market_orders:
            return
        else:
            fam.state = "cancelling"
            reason = _unfilled_after(fam.attempts)
        fam.cancel_sent_ns = now_ns
        fam.cancel_retries = 0
        self._journal_safe("policy", now_ns, order, step=fam.state, reason=reason, attempts=fam.attempts,
                           root=str(fam.root))
        log.warning("%s: %s", order.client_order_id, reason, extra=self._extra(order))
        self._send_cancel(order, reason)

    def _send_cancel(self, order: Order, reason: str) -> None:
        cid = order.client_order_id
        self._cancel_reason[cid] = reason
        now = self._clock.timestamp_ns()
        client = self._clients.get(order.venue)
        self._journal_safe("cancel_request", now, order, reason=reason)
        if client is None:
            log.error("cannot cancel %s: no execution client for %s", cid, order.venue)
            return
        log.info("cancel %s (%s)", cid, reason, extra=self._extra(order))
        try:
            client.cancel_order(order)
        except Exception as exc:  # noqa: BLE001 - logged with the venue's reply; retried on timeout
            log.error("cancel of %s refused or failed: %s", cid, exc, extra=self._extra(order))
            self._journal_safe("venue_reply", now, order, call="cancel_order", error=str(exc))

    def _after_event(self, order: Order, event: OrderEvent) -> None:
        fam = self._family_of.get(order.client_order_id)
        if fam is None or fam.state == "done":
            return
        if isinstance(event, OrderFilled):
            filled = self._family_filled(fam)
            if filled >= fam.target_qty - _QTY_EPS * max(1.0, fam.target_qty):
                active = self._orders[fam.active]
                if active.is_open and active.client_order_id != order.client_order_id:
                    fam.state = "cancelling"
                    fam.cancel_sent_ns = self._clock.timestamp_ns()
                    self._send_cancel(active, f"family {fam.root} complete after a late fill on "
                                              f"{order.client_order_id}")
                    return
                self._finish(fam, f"filled {filled:g} of {fam.target_qty:g}")
                return
            if order.client_order_id != fam.active:
                active = self._orders[fam.active]
                if (active.is_open and fam.state == "working"
                        and filled + active.leaves_qty > fam.target_qty + _QTY_EPS * max(1.0, fam.target_qty)):
                    fam.state = "repricing"
                    fam.resizing = True
                    fam.cancel_sent_ns = self._clock.timestamp_ns()
                    fam.cancel_retries = 0
                    self._send_cancel(active, f"resize: a late fill on {order.client_order_id} leaves "
                                              f"{fam.target_qty - filled:g} to fill, not {active.leaves_qty:g}")
            return
        if order.client_order_id == fam.active and order.is_closed:
            self._after_close(fam, order)

    def _after_close(self, fam: _Family, order: Order) -> None:
        """The family's working order has closed (cancelled, rejected or denied)."""
        if fam.state == "repricing" and order.status is OrderStatus.CANCELED:
            self._spawn_child(fam, order)
        elif fam.state == "cancelling":
            filled = self._family_filled(fam)
            self._finish(fam, f"{order.reason or order.status.value}; filled {filled:g} of {fam.target_qty:g}")
        else:
            level = logging.WARNING if order.status is not OrderStatus.FILLED else logging.INFO
            log.log(level, "%s %s (%s); the policy stops for family %s", order.client_order_id,
                    order.status.value, order.reason, fam.root, extra=self._extra(order))
            self._finish(fam, f"{order.client_order_id} {order.status.value}: {order.reason}")

    def _spawn_child(self, fam: _Family, parent: Order) -> None:
        if self.policy.confirm_before_reprice:
            self._confirm_fills(parent)
            if fam.state != "repricing":
                return                     # the venue's fills completed the family
        filled = self._family_filled(fam)
        remaining = fam.target_qty - filled
        lot = self._size(self._lot_size, parent.instrument_id)
        remaining = _floor_to_lot(remaining, lot)
        if remaining <= _QTY_EPS * max(1.0, fam.target_qty):
            self._finish(fam, f"nothing left to re-price (filled {filled:g} of {fam.target_qty:g})")
            return
        price = self._reprice_price(parent)
        if price is None:
            self._finish(fam, f"{_unfilled_after(fam.attempts)}: no quote or last price to re-price "
                              f"{remaining:g} {parent.instrument_id}")
            return
        root = self._orders.get(fam.root, parent)
        attempt = fam.attempts if fam.resizing else fam.attempts + 1
        tags = {k: v for k, v in root.tags.items() if k not in ("venue_order_id", "parent", "attempt")}
        tags.update(parent=str(parent.client_order_id), root=str(fam.root), attempt=attempt)
        child = Order(self.next_order_id(parent.strategy_id), parent.strategy_id, parent.instrument_id,
                      parent.side, OrderType.LIMIT, float(remaining), float(price), parent.time_in_force,
                      parent.reference_price, tags, self._clock.timestamp_ns())
        fam.attempts = attempt
        fam.resizing = False
        fam.members.append(child.client_order_id)
        fam.active = child.client_order_id
        fam.state = "working"
        fam.since_ns = self._clock.timestamp_ns()
        fam.cancel_sent_ns = None
        fam.cancel_retries = 0
        self._family_of[child.client_order_id] = fam
        self._journal_safe("policy", fam.since_ns, parent, step="child", child=str(child.client_order_id),
                           quantity=remaining, price=price, attempt=attempt, root=str(fam.root))
        log.warning("re-price %s -> %s: %s %g %s @ %g (attempt %d of %d)", parent.client_order_id,
                    child.client_order_id, child.side.value, remaining, child.instrument_id, price, attempt,
                    self.policy.max_reprices + 1, extra=self._extra(child))
        self._submit(child, family=fam)

    def _finish(self, fam: _Family, summary: str) -> None:
        fam.state = "done"
        self._working.pop(fam.root, None)
        order = self._orders[fam.active]
        self._journal_safe("policy", self._clock.timestamp_ns(), order, step="done", root=str(fam.root),
                           attempts=fam.attempts, summary=summary)
        log.info("family %s done after %d attempt(s): %s", fam.root, fam.attempts, summary,
                 extra=self._extra(order))

    def _family_filled(self, fam: _Family) -> float:
        return sum(max(self._orders[m].filled_qty, self._venue_filled.get(m, 0.0)) for m in fam.members)

    def _overdue_cancel(self, fam: _Family, order: Order, now_ns: int) -> None:
        """A cancel the venue has not confirmed: ask the venue (its fills, then its view of the
        order), and act on its answer; re-send the cancel while the order is still working."""
        waited = (now_ns - fam.cancel_sent_ns) / _NS
        fam.cancel_sent_ns = now_ns            # the next check, if one is needed, is a timeout later
        log.warning("cancel of %s not confirmed after %.0fs; asking the venue", order.client_order_id, waited,
                    extra=self._extra(order))
        self._confirm_fills(order)
        if order.is_closed or fam.state == "done":
            return
        client = self._clients.get(order.venue)
        report: Optional[OrderReport] = None
        error = None
        try:
            report = client.query_order(order) if client is not None else None
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        self._journal_report(order, now_ns, "query_order", report, error)
        final = venue_status(report.status) if report is not None else None
        if error is None and report is None:
            self._process(OrderCanceled(order.client_order_id, order.strategy_id, order.instrument_id, now_ns,
                                        reason="the venue does not know the order (queried after an "
                                               "unconfirmed cancel)"))
            return
        if report is not None and report.filled_quantity > order.filled_qty + _QTY_EPS:
            self._venue_filled[order.client_order_id] = float(report.filled_quantity)
            log.warning("venue reports %g filled on %s but only %g in fills received; sizing on the venue's "
                        "figure until its fills arrive", report.filled_quantity, order.client_order_id,
                        order.filled_qty, extra=self._extra(order))
        if final in (OrderStatus.CANCELED, OrderStatus.REJECTED):
            cls = OrderCanceled if final is OrderStatus.CANCELED else OrderRejected
            self._process(cls(order.client_order_id, order.strategy_id, order.instrument_id, now_ns,
                              reason=f"venue status {report.status}" + (f": {report.remark}" if report.remark else "")))
            return
        if final is OrderStatus.FILLED:
            if self._family_filled(fam) >= fam.target_qty - _QTY_EPS * max(1.0, fam.target_qty):
                self._finish(fam, f"the venue reports {order.client_order_id} filled; waiting for its fills")
            return
        if fam.cancel_retries >= self.policy.max_cancel_retries:
            log.error("cancel of %s still not confirmed after %d retries (venue: %s); left to reconciliation",
                      order.client_order_id, fam.cancel_retries, report.status if report else error,
                      extra=self._extra(order))
            self._finish(fam, "cancel never confirmed")
            return
        fam.cancel_retries += 1
        self._send_cancel(order, self._cancel_reason.get(order.client_order_id) or f"retry {fam.cancel_retries}")

    def _confirm_fills(self, order: Order) -> int:
        """Apply any fill the venue has for `order` that never reached us; returns how many."""
        client = self._clients.get(order.venue)
        if client is None:
            return 0
        since = pd.Timestamp(order.ts_init, unit="ns", tz="UTC") - pd.Timedelta(minutes=5)
        try:
            reports = client.fills(since)
        except Exception as exc:  # noqa: BLE001
            log.warning("fills query for %s failed: %s", order.client_order_id, exc, extra=self._extra(order))
            self._journal_safe("venue_reply", self._clock.timestamp_ns(), order, call="fills", error=str(exc))
            return 0
        voi = order.tags.get("venue_order_id")
        applied = 0
        for r in reports:
            mine = (r.client_order_id is not None and r.client_order_id == str(order.client_order_id)) or \
                   (voi is not None and str(r.order_id) == str(voi))
            if mine and self._apply_fill_report(order, r, "re-query") in (Outcome.APPLY, Outcome.RACE):
                applied += 1
        if applied:
            log.warning("%d fill(s) of %s found only by asking the venue", applied, order.client_order_id,
                        extra=self._extra(order))
        return applied

    def _apply_fill_report(self, order: Order, report: FillReport, source: str) -> Outcome:
        try:
            side = OrderSide(str(report.side).upper())
            trade_id = TradeId(str(report.trade_id))
        except ValueError as exc:
            log.error("venue fill report for %s unreadable (%s): %r", order.client_order_id, exc, report)
            return Outcome.ILLEGAL
        if side is not order.side:
            log.error("venue fill %s for %s is a %s, the order a %s; not applied", report.trade_id,
                      order.client_order_id, side.value, order.side.value)
            return Outcome.ILLEGAL
        fill = OrderFilled(order.client_order_id, order.strategy_id, order.instrument_id,
                           int(pd.Timestamp(report.ts).value), trade_id=trade_id,
                           side=side, last_qty=float(report.quantity), last_px=float(report.price),
                           commission=float(report.commission or 0.0), currency=report.currency,
                           tags={"venue_order_id": str(report.order_id), "source": source})
        return self._process(fill)

    def _reprice_price(self, order: Order) -> Optional[float]:
        if self._price_source is None:
            return None
        try:
            snapshot = self._price_source(order.instrument_id)
        except Exception:  # noqa: BLE001
            log.exception("price source failed for %s", order.instrument_id)
            return None
        bid, ask, last = _price_parts(snapshot)
        q = self.policy.quote_offset_bps / 10_000.0
        f = self.policy.last_offset_bps / 10_000.0
        if order.is_buy:
            price = ask * (1 + q) if ask is not None else (last * (1 + f) if last is not None else None)
        else:
            price = bid * (1 - q) if bid is not None else (last * (1 - f) if last is not None else None)
        if price is None or not price > 0:
            return None
        tick = self._size(self._tick_size, order.instrument_id)
        price = _to_tick(price, tick, up=order.is_buy) if tick else price
        return price if price > 0 else None

    @staticmethod
    def _size(source: Optional[SizeSource], iid: InstrumentId) -> Optional[float]:
        if source is None:
            return None
        try:
            value = source(iid)
        except Exception:  # noqa: BLE001
            return None
        return float(value) if value else None

    # ── reconciliation ────────────────────────────────────────────────────────────────────────

    def reconcile(self) -> ReconcileReport:
        """For each venue: apply fills the venue has that we missed, then compare our open orders
        with the venue's. An open order of ours the venue no longer lists is looked up on its own
        (its status lags) and closed as the venue says; one the venue does not know at all is
        marked cancelled. What cannot be repaired from our side is listed as unexplained."""
        now = self._clock.timestamp_ns()
        matched = 0
        adjustments: List[str] = []
        unexplained: List[str] = []
        for venue, client in self._clients.items():
            mine = [o for o in self._open.values() if o.venue == venue]
            earliest = min([o.ts_init for o in mine] + [now - self._reconcile_lookback_ns])
            since = pd.Timestamp(earliest, unit="ns", tz="UTC")
            # 1. fills
            try:
                reports = client.fills(since)
            except Exception as exc:  # noqa: BLE001
                unexplained.append(f"{venue}: fills query failed: {exc}")
                reports = None
            for r in reports or []:
                order = self._order_for_report(venue, r.client_order_id, r.order_id)
                if order is None:
                    unexplained.append(f"{venue}: fill {r.trade_id} on venue order {r.order_id} ({r.side} "
                                       f"{r.quantity:g} {r.symbol} @ {r.price:g}) is not one of ours")
                    continue
                key = (venue.value, str(r.trade_id))
                if key in self._trade_ids:
                    matched += 1
                    continue
                outcome = self._apply_fill_report(order, r, "reconcile")
                if outcome in (Outcome.APPLY, Outcome.RACE):
                    adjustments.append(f"{order.client_order_id}: missed fill {r.trade_id} {r.side} {r.quantity:g} "
                                       f"@ {r.price:g} applied")
                elif outcome is not Outcome.DUPLICATE:
                    unexplained.append(f"{order.client_order_id}: venue fill {r.trade_id} not applied "
                                       f"({outcome.value}; order is {order.status.value})")
            # 2. open orders
            try:
                venue_open = client.open_orders()
            except Exception as exc:  # noqa: BLE001
                unexplained.append(f"{venue}: open orders query failed: {exc}")
                continue
            listed: Set[ClientOrderId] = set()
            for r in venue_open:
                order = self._order_for_report(venue, r.client_order_id, r.order_id)
                if order is None:
                    unexplained.append(f"{venue}: open order {r.order_id} ({r.side} {r.quantity:g} {r.symbol} "
                                       f"@ {r.price}) is not in the journal")
                    continue
                listed.add(order.client_order_id)
                if order.is_closed:
                    unexplained.append(f"{order.client_order_id}: the venue lists it open ({r.status}) but it is "
                                       f"{order.status.value} here")
                    continue
                matched += 1
                if r.filled_quantity > order.filled_qty + _QTY_EPS:
                    unexplained.append(f"{order.client_order_id}: the venue reports {r.filled_quantity:g} filled, "
                                       f"fills received {order.filled_qty:g}")
                if order.status is OrderStatus.SUBMITTED:
                    self._process(OrderAccepted(order.client_order_id, order.strategy_id, order.instrument_id, now))
                    adjustments.append(f"{order.client_order_id}: accepted (the venue lists it open)")
            for order in [o for o in self._open.values() if o.venue == venue]:
                if order.client_order_id in listed or now - order.ts_init < self._reconcile_grace_ns:
                    continue
                self._settle_unlisted(order, client, now, adjustments, unexplained)
        report = ReconcileReport("orders", now, matched, tuple(adjustments), tuple(unexplained))
        level = logging.WARNING if (adjustments or unexplained) else logging.INFO
        log.log(level, "order reconciliation: %d matched, %d repaired, %d unexplained", matched,
                len(adjustments), len(unexplained))
        for line in adjustments:
            log.warning("order reconciliation: %s", line)
        for line in unexplained:
            log.warning("order reconciliation: %s", line)
        self._journal_safe("reconcile", now, None, matched=matched, adjustments=list(adjustments),
                           unexplained=list(unexplained))
        return report

    def _settle_unlisted(self, order: Order, client: ExecutionClient, now: int,
                         adjustments: List[str], unexplained: List[str]) -> None:
        """Our open order is not among the venue's open orders: ask the venue about it."""
        cid = order.client_order_id
        report: Optional[OrderReport] = None
        error = None
        try:
            report = client.query_order(order)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        self._journal_report(order, now, "query_order", report, error)
        if error is not None:
            unexplained.append(f"{cid}: not among the venue's open orders and the query failed: {error}")
            return
        if report is None:
            self._process(OrderCanceled(cid, order.strategy_id, order.instrument_id, now,
                                        reason="reconciliation: the venue no longer knows this order"))
            adjustments.append(f"{cid}: the venue no longer knows it; marked cancelled")
            return
        final = venue_status(report.status)
        if report.filled_quantity > order.filled_qty + _QTY_EPS:
            unexplained.append(f"{cid}: the venue reports {report.filled_quantity:g} filled ({report.status}), "
                               f"fills received {order.filled_qty:g}")
            self._venue_filled[cid] = float(report.filled_quantity)
        if final is OrderStatus.CANCELED or final is OrderStatus.REJECTED:
            cls = OrderCanceled if final is OrderStatus.CANCELED else OrderRejected
            reason = f"reconciliation: venue status {report.status}" + (f": {report.remark}" if report.remark else "")
            self._process(cls(cid, order.strategy_id, order.instrument_id, now, reason=reason))
            adjustments.append(f"{cid}: {final.value.lower()} at the venue ({report.status}); marked so")
        elif final is OrderStatus.FILLED:
            if order.is_open:
                unexplained.append(f"{cid}: the venue says filled ({report.status}) but fills for only "
                                   f"{order.filled_qty:g} of {order.quantity:g} arrived; left open")
        else:
            unexplained.append(f"{cid}: not among the venue's open orders, but its status is {report.status}")

    def _order_for_report(self, venue: Venue, client_order_id: Optional[str], venue_order_id: Optional[str]) -> Optional[Order]:
        if client_order_id:
            try:
                order = self._orders.get(ClientOrderId(str(client_order_id)))
            except ValueError:
                order = None
            if order is not None:
                return order
        if venue_order_id is not None:
            cid = self._by_venue_id.get((venue, str(venue_order_id)))
            if cid is not None:
                return self._orders.get(cid)
        return None

    # ── journal and log helpers ───────────────────────────────────────────────────────────────

    def _journal_safe(self, kind: str, ts: int, order: Optional[Order], **fields: Any) -> None:
        try:
            self._journal.write(kind, ts, order, **fields)
        except OSError as exc:
            log.error("order journal write (%s) failed: %s", kind, exc)

    def _journal_event(self, event: OrderEvent, ts: int, order: Optional[Order], applied: bool,
                       outcome: Outcome, previous: Optional[OrderStatus] = None, message: str = "") -> None:
        try:
            self._journal.record_event(event, ts, order, applied=applied, outcome=outcome.value,
                                       previous_status=previous.value if previous is not None else None,
                                       message=message)
        except OSError as exc:
            log.error("order journal write failed for %s: %s", type(event).__name__, exc)

    def _journal_report(self, order: Order, ts: int, call: str, report: Optional[OrderReport],
                        error: Optional[str]) -> None:
        if error is not None:
            reply: Dict[str, Any] = {"error": error}
        elif report is None:
            reply = {"known": False}
        else:
            reply = {"status": report.status, "filled_quantity": report.filled_quantity,
                     "average_fill_price": report.average_fill_price, "remark": report.remark,
                     "venue_order_id": report.order_id}
        log.info("venue reply to %s for %s: %s", call, order.client_order_id, reply, extra=self._extra(order))
        self._journal_safe("venue_reply", ts, order, call=call, reply=reply)

    @staticmethod
    def _extra(order: Order) -> Dict[str, str]:
        return {"client_order_id": str(order.client_order_id), "strategy_id": str(order.strategy_id)}

    @staticmethod
    def _describe(event: OrderEvent) -> str:
        if isinstance(event, OrderFilled):
            return f"{event.side.value} {event.last_qty:g} @ {event.last_px:g} (trade {event.trade_id})"
        reason = getattr(event, "reason", "")
        return reason or ""

    def _log_applied(self, order: Order, event: OrderEvent, previous: OrderStatus) -> None:
        if isinstance(event, OrderFilled):
            log.info("fill %s: %s %g %s @ %g (%g of %g, %s -> %s)", order.client_order_id, event.side.value,
                     event.last_qty, order.instrument_id, event.last_px, order.filled_qty, order.quantity,
                     previous.value, order.status.value, extra=self._extra(order))
        elif isinstance(event, (OrderDenied, OrderRejected, OrderCanceled)):
            log.warning("%s %s: %s", order.client_order_id, order.status.value, order.reason,
                        extra=self._extra(order))
        else:
            log.info("%s %s -> %s", order.client_order_id, previous.value, order.status.value,
                     extra=self._extra(order))
