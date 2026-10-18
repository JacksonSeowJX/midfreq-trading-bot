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

A fill is applied at most once per (venue, the venue's order id or else the client order id,
trade id): venues number executions per instrument or per order, not per account (OKX), so the
trade id alone is not a key. A repeated push is ignored.

Each part of the OMS has its own module in this folder:

    order_state.py       the state machine (TRANSITIONS): what each event does to an order
    unfilled_policy.py   UnfilledOrderPolicy and the order families: re-price, then cancel;
                         refused and unconfirmed cancels (UnfilledPolicyMixin)
    reconciliation.py    the venues' fills and open orders against ours (ReconciliationMixin)
    pricing.py           a child order's price and size, on the tick and in lots (RepricingMixin)
    journal.py           the order journal

This module holds OrderManagementSystem, built from the three mixins, and the rest of its work:
construction, start-up from the journal, ids, the OrderManager calls (submit, cancel and the
queries), each event's processing, and the journal and log helpers. Every part logs through the
one logger, `midfreq.trading.oms`.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import pandas as pd

from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.enums import OrderStatus, TimeInForce
from midfreq.core.model.events import (
    OrderCanceled, OrderCancelRejected, OrderDenied, OrderEvent, OrderFilled, OrderRejected, OrderSubmitted,
)
from midfreq.core.model.identifiers import ClientOrderId, StrategyId, Venue
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import ExecutionClient, OrderReport
from midfreq.trading.core.interfaces import TOPIC_FILLS, TOPIC_ORDER_EVENTS, PositionKeeper, PreTradeRisk
from midfreq.trading.execution.journal import OrderJournal, format_order_id, parse_order_id
# Outcome, TRANSITIONS, transition, venue_status and UnfilledOrderPolicy are imported from this
# module elsewhere, so they stay importable here.
from midfreq.trading.execution.order_state import (  # noqa: F401
    _QTY_EPS, TRANSITIONS, Outcome, _apply, transition, venue_status,
)
from midfreq.trading.execution.pricing import PriceSource, RepricingMixin, SizeSource
from midfreq.trading.execution.reconciliation import ReconciliationMixin
from midfreq.trading.execution.unfilled_policy import _NS, UnfilledOrderPolicy, UnfilledPolicyMixin, _Family

log = logging.getLogger("midfreq.trading.oms")

__all__ = ["OrderManagementSystem", "Outcome", "TRANSITIONS", "UnfilledOrderPolicy", "venue_status"]


# ── the OMS ────────────────────────────────────────────────────────────────────────────────────

class OrderManagementSystem(UnfilledPolicyMixin, ReconciliationMixin, RepricingMixin):
    """
    clients: the ExecutionClient of each venue, which the node has connected and whose event
    handler puts events on the node's queue (the queue's consumer calls `handle_event`).
    price_source: for re-pricing (see _price_parts in pricing.py). lot_size / tick_size: optional,
    to keep a child's quantity in whole lots and its price on the tick (buys round up, sells down).
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
        # An order the venue does not list yet is left alone at least as long as its client keeps
        # looking for it (a lost place reply: Moomoo and ccxt look for 30 s), plus a margin.
        windows = [float(getattr(c, "unconfirmed_window_s", 0.0) or 0.0) for c in self._clients.values()]
        grace_s = max([float(reconcile_grace_s)] + [w + 5.0 for w in windows if w > 0])
        self._reconcile_grace_ns = int(grace_s * _NS)

        self._orders: Dict[ClientOrderId, Order] = {}
        self._open: Dict[ClientOrderId, Order] = {}
        self._by_venue_id: Dict[Tuple[Venue, str], ClientOrderId] = {}
        self._trade_ids: Set[Tuple[str, str, str]] = set()   # fill keys (see fill_key)
        self._seq: Dict[Tuple[str, str], int] = {}
        self._families: Dict[ClientOrderId, _Family] = {}     # root -> family, every family
        self._family_of: Dict[ClientOrderId, _Family] = {}    # any member -> its family
        self._working: Dict[ClientOrderId, _Family] = {}      # root -> family not yet done
        self._cancel_reason: Dict[ClientOrderId, str] = {}
        self._local_reject: Set[ClientOrderId] = set()
        self._venue_filled: Dict[ClientOrderId, float] = {}   # what a venue query said had filled
        self._policy_paused: Optional[str] = None             # why no re-price may send an order now

        for component in (risk, positions):
            bind = getattr(component, "bind_open_orders", None)
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

    @staticmethod
    def fill_key(order: Order, trade_id: Any, venue_order_id: Any = None) -> Tuple[str, str, str]:
        """(venue, the venue's order id or else the client order id, trade id): what makes a fill
        unique (a trade id alone is not: OKX numbers trades per instrument)."""
        voi = order.tags.get("venue_order_id") or venue_order_id
        return order.venue.value, str(voi) if voi else str(order.client_order_id), str(trade_id)

    def family_done(self, client_order_id: ClientOrderId) -> bool:
        """Whether the family of this order has reached its outcome: every member closed and the
        unfilled-order policy finished with it (no child order still to come)."""
        orders = self.family(client_order_id)
        if not orders or any(o.is_open for o in orders):
            return False
        fam = self._family_of.get(client_order_id)
        return fam is None or fam.state == "done" or fam.root not in self._working

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
                key = self.fill_key(order, event.trade_id, event.tags.get("venue_order_id"))
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
        if isinstance(event, OrderCancelRejected):
            self._cancel_reason.pop(order.client_order_id, None)     # that cancel is over
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
        elif isinstance(event, OrderCancelRejected):
            log.warning("%s: cancel refused (%s); the order is %s", order.client_order_id, event.reason,
                        order.status.value, extra=self._extra(order))
        else:
            log.info("%s %s -> %s", order.client_order_id, previous.value, order.status.value,
                     extra=self._extra(order))
