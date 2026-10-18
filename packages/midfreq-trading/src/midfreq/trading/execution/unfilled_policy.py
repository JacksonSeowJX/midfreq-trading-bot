"""
The OMS's unfilled-order policy: an order that has not filled completely in time is re-priced,
then cancelled, and a cancel the venue refuses or does not confirm is followed up.

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

OrderCancelRejected (the venue refused a cancel) changes nothing in the order: the OMS stops
waiting for that cancel, asks the venue for the order's fills and state, and applies what the
venue says (a fill that won the race, a cancel or rejection the push never brought). If the
order is still working, the policy goes on from there: a re-price waits `wait_s` again before it
tries again, a cancel is checked and re-sent after `cancel_timeout_s`; after more than
`max_cancel_retries` refusals the policy stops for the family and leaves the order working (the
strategy's own wait and reconciliation see it).

UnfilledPolicyMixin holds the OMS's methods for all of this. A child order's price comes from
pricing.py; the venue's fills and view of an order are asked for through reconciliation.py.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

from midfreq.core.model.enums import OrderStatus, OrderType
from midfreq.core.model.events import OrderCanceled, OrderCancelRejected, OrderEvent, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import ClientOrderId
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import OrderReport
from midfreq.trading.execution.order_state import _QTY_EPS, venue_status
from midfreq.trading.execution.pricing import _floor_to_lot

log = logging.getLogger("midfreq.trading.oms")

__all__ = ["UnfilledOrderPolicy", "UnfilledPolicyMixin"]

_NS = 1_000_000_000


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

    PRESETS = ("original_engine",)

    @classmethod
    def original_engine(cls) -> "UnfilledOrderPolicy":
        """What midfreq.live.order_gateway.place_and_confirm did: wait 30 s, no re-price,
        cancel, wait up to 30 s more for the cancel's outcome, no retry."""
        return cls(wait_s=30.0, max_reprices=0, cancel_timeout_s=30.0, max_cancel_retries=0)

    @classmethod
    def from_config(cls, value: Any) -> "UnfilledOrderPolicy":
        """A policy from configuration: None (the defaults), a preset's name
        ('original_engine'), or a mapping of this class's fields (anything else is refused)."""
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            if value not in cls.PRESETS:
                raise ValueError(f"unknown unfilled-order preset {value!r}; presets: {list(cls.PRESETS)}")
            return getattr(cls, value)()
        if not isinstance(value, Mapping):
            raise ValueError(f"unfilled_order must be a mapping or a preset name, not {type(value).__name__}")
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(value) - names
        if unknown:
            raise ValueError(f"unknown unfilled_order settings {sorted(unknown)}; allowed: {sorted(names)}")
        policy = cls(**dict(value))
        if policy.wait_s <= 0 or policy.cancel_timeout_s <= 0 or policy.max_reprices < 0 \
                or policy.max_cancel_retries < 0:
            raise ValueError("unfilled_order: wait_s and cancel_timeout_s must be positive, max_reprices and "
                             "max_cancel_retries not negative")
        return policy

    def budget_s(self) -> float:
        """The longest a family can take to reach its outcome when the venue answers: every
        attempt's wait, plus the last cancel's timeout."""
        return (self.max_reprices + 1) * self.wait_s + self.cancel_timeout_s


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
    refusals: int = 0                   # cancels the venue refused (OrderCancelRejected)
    resizing: bool = False              # the pending re-price replaces a child after a late fill


def _unfilled_after(attempts: int) -> str:
    return f"unfilled after {attempts} attempt{'' if attempts == 1 else 's'}"


class UnfilledPolicyMixin:
    """The unfilled-order policy's part of OrderManagementSystem (oms.py): the timer, the
    families' re-prices and cancels, refused and unconfirmed cancels.

    Relies on the OMS's state `policy`, `_clock`, `_clients`, `_orders`, `_family_of`, `_working`,
    `_cancel_reason`, `_venue_filled`, `_policy_paused` and `_lot_size`; on its methods
    `_process`, `_submit`, `next_order_id`, `_journal_safe`, `_journal_report` and `_extra`
    (oms.py); on `_confirm_fills` and `_learn_venue_id` (ReconciliationMixin); and on
    `_reprice_price` and `_size` (RepricingMixin)."""

    # ── the unfilled-order policy ─────────────────────────────────────────────────────────────

    def pause_policy(self, reason: str) -> None:
        """Trading is off (shutdown, a halt): the unfilled-order policy sends no new order. An
        order unfilled after wait_s is cancelled instead of re-priced, and a re-price whose cancel
        is confirmed later ends there; cancels already sent are still followed up."""
        if self._policy_paused is None:
            log.warning("unfilled-order policy paused (%s): no re-priced order will be sent", reason)
        self._policy_paused = reason

    def resume_policy(self) -> None:
        self._policy_paused = None

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
        if (order.order_type is OrderType.LIMIT and fam.attempts <= self.policy.max_reprices
                and self._policy_paused is None):
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
        if isinstance(event, OrderCancelRejected):
            self._cancel_refused(order, event)
            return
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
        if fam.state == "repricing" and order.status is OrderStatus.CANCELED and self._policy_paused is not None:
            self._finish(fam, f"re-price not sent: {self._policy_paused}")
        elif fam.state == "repricing" and order.status is OrderStatus.CANCELED:
            self._spawn_child(fam, order)
        elif fam.state == "cancelling":
            filled = self._family_filled(fam)
            self._finish(fam, f"{order.reason or order.status.value}; filled {filled:g} of {fam.target_qty:g}")
        else:
            level = logging.WARNING if order.status is not OrderStatus.FILLED else logging.INFO
            log.log(level, "%s %s (%s); the policy stops for family %s", order.client_order_id,
                    order.status.value, order.reason, fam.root, extra=self._extra(order))
            self._finish(fam, f"{order.client_order_id} {order.status.value}: {order.reason}")

    def _cancel_refused(self, order: Order, event: OrderCancelRejected) -> None:
        """The venue refused a cancel: stop waiting for it, ask the venue what the order really
        is (its fills, then its state), apply that, and let the policy go on from there."""
        now = self._clock.timestamp_ns()
        cid = order.client_order_id
        log.warning("cancel of %s refused by the venue (%s); asking the venue for its state", cid,
                    event.reason, extra=self._extra(order))
        self._confirm_fills(order)
        if order.is_open:
            client = self._clients.get(order.venue)
            report: Optional[OrderReport] = None
            error = None
            try:
                report = client.query_order(order) if client is not None else None
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            self._journal_report(order, now, "query_order", report, error)
            if report is not None:
                self._learn_venue_id(order, report.order_id, "a query of the order")
                if report.filled_quantity > order.filled_qty + _QTY_EPS:
                    self._venue_filled[cid] = float(report.filled_quantity)
                final = venue_status(report.status)
                if final in (OrderStatus.CANCELED, OrderStatus.REJECTED) and order.is_open:
                    cls = OrderCanceled if final is OrderStatus.CANCELED else OrderRejected
                    self._process(cls(cid, order.strategy_id, order.instrument_id, now,
                                      reason=f"venue status {report.status}" +
                                             (f": {report.remark}" if report.remark else "")))
        fam = self._family_of.get(cid)
        if fam is None or fam.state == "done" or fam.active != cid or order.is_closed:
            return
        if fam.state not in ("repricing", "cancelling"):
            return
        fam.refusals += 1
        if fam.refusals > self.policy.max_cancel_retries:
            log.error("%s: the venue refused %d cancels (%s); the policy stops and leaves the order working",
                      cid, fam.refusals, event.reason, extra=self._extra(order))
            self._finish(fam, f"cancel refused {fam.refusals} times; left working ({event.reason})")
            return
        if fam.state == "repricing":
            # The order keeps working at its price: wait again before the next attempt.
            fam.state = "working"
            fam.resizing = False
            fam.since_ns = now
            fam.cancel_sent_ns = None
        else:
            # Still to be cancelled: checked with the venue and sent again after the timeout.
            fam.cancel_sent_ns = now

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
        if report is not None:
            self._learn_venue_id(order, report.order_id, "a query of the order")
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
