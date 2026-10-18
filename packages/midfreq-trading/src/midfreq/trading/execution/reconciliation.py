"""
The OMS's reconciliation with the venues: what a venue's own records of our orders (its fills,
its open orders, its answer to a query of one order) say, applied to the OMS's book.

- `reconcile()`: each venue's fills and open orders against ours. An open order of ours the
  venue no longer lists is looked up on its own and settled as the venue says; an order the OMS
  closed that the venue still lists open is reopened and its remainder cancelled at the venue.
- The fills of one order, asked for when an event is overdue (`_confirm_fills`, for the
  unfilled-order policy), and the rule both share for applying a venue's fill reports: each fill
  at most once, and never beyond the venue's own cumulative filled quantity.
- The venue's id of an order the OMS has none for (a lost place reply), learned from any of
  these reports.

ReconciliationMixin holds the OMS's methods for all of this.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from midfreq.core.model.enums import OrderSide, OrderStatus
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import ClientOrderId, TradeId, Venue
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import ExecutionClient, FillReport, OrderReport
from midfreq.trading.core.interfaces import ReconcileReport
from midfreq.trading.execution.order_state import _QTY_EPS, Outcome, venue_status
from midfreq.trading.execution.unfilled_policy import _Family

log = logging.getLogger("midfreq.trading.oms")

__all__ = ["ReconciliationMixin"]


class ReconciliationMixin:
    """The reconciliation part of OrderManagementSystem (oms.py): `reconcile` and its steps (an
    order the venue no longer lists, a closed order it still lists open, the venue's order ids),
    and the venue's fills for one order.

    Relies on the OMS's state `_clock`, `_clients`, `_orders`, `_open`, `_by_venue_id`,
    `_trade_ids`, `_families`, `_family_of`, `_working`, `_local_reject`, `_venue_filled`,
    `_reconcile_lookback_ns` and `_reconcile_grace_ns`; on its methods `_process`, `fill_key`,
    `_journal_safe`, `_journal_report` and `_extra` (oms.py); and on `_send_cancel`
    (UnfilledPolicyMixin)."""

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
        foreign: List[str] = []
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
            by_order: Dict[ClientOrderId, List[FillReport]] = {}
            for r in reports or []:
                order = self._order_for_report(venue, r.client_order_id, r.order_id)
                if order is None:
                    foreign.append(f"{venue}: fill {r.trade_id} on venue order {r.order_id} ({r.side} "
                                   f"{r.quantity:g} {r.symbol} @ {r.price:g}) is not one of ours")
                    continue
                by_order.setdefault(order.client_order_id, []).append(r)
            for cid, mine in by_order.items():
                self._learn_venue_id(self._orders[cid], mine[0].order_id, "the venue's fills")
                notes: List[str] = []
                _, m = self._apply_reports(self._orders[cid], mine, "reconcile", notes)
                matched += m
                for note in notes:
                    (adjustments if "missed fill" in note else unexplained).append(note)
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
                    foreign.append(f"{venue}: open order {r.order_id} ({r.side} {r.quantity:g} {r.symbol} "
                                   f"@ {r.price}) is not in the journal")
                    continue
                listed.add(order.client_order_id)
                self._learn_venue_id(order, r.order_id, "the venue's open orders")
                if order.is_closed:
                    if order.status in (OrderStatus.REJECTED, OrderStatus.CANCELED):
                        self._reopen_and_cancel(order, r, now, adjustments)
                    else:
                        unexplained.append(f"{order.client_order_id}: the venue lists it open ({r.status}) but it "
                                           f"is {order.status.value} here")
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
        report = ReconcileReport("orders", now, matched, tuple(adjustments), tuple(unexplained), tuple(foreign))
        level = logging.WARNING if (adjustments or unexplained) else logging.INFO
        log.log(level, "order reconciliation: %d matched, %d repaired, %d unexplained, %d not ours", matched,
                len(adjustments), len(unexplained), len(foreign))
        for line in adjustments:
            log.warning("order reconciliation: %s", line)
        self._journal_safe("reconcile", now, None, matched=matched, adjustments=list(adjustments),
                           unexplained=list(unexplained), foreign=list(foreign))
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
        if report is not None:
            self._learn_venue_id(order, report.order_id, "a query of the order")
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

    def _learn_venue_id(self, order: Order, venue_order_id: Any, source: str) -> None:
        """A venue report revealed the venue's id of an order the OMS has none for (a lost
        place reply): keep it on the order, index it and journal it, so cancels and later
        reports reach the right order."""
        vid = str(venue_order_id or "").strip()
        if not vid or order.tags.get("venue_order_id"):
            return
        order.tags["venue_order_id"] = vid
        self._by_venue_id[(order.venue, vid)] = order.client_order_id
        log.warning("%s: the venue's order id %s learned from %s", order.client_order_id, vid, source,
                    extra=self._extra(order))
        self._journal_safe("venue_id", self._clock.timestamp_ns(), order, venue_order_id=vid, source=source)

    def _reopen_and_cancel(self, order: Order, report: OrderReport, now: int, adjustments: List[str]) -> None:
        """The venue lists the order open but the OMS has closed it (a rejection or a cancel it had
        to guess). Its fills are applied as they come; what is left is cancelled at the venue,
        through a family in 'cancelling' so an unconfirmed cancel is followed up."""
        cid = order.client_order_id
        previous = order.status
        reason = (f"open at the venue ({report.status}) but {previous.value} here: reopened to cancel the "
                  f"remainder at the venue")
        order.status = OrderStatus.PARTIALLY_FILLED if order.filled_qty > 0 else OrderStatus.ACCEPTED
        order.reason = reason
        self._open[cid] = order
        self._local_reject.discard(cid)
        fam = self._family_of.get(cid)
        if fam is None:
            fam = _Family(cid, order.quantity, [cid], cid, int(order.tags.get("attempt", 1) or 1), now)
            self._families[cid] = fam
            self._family_of[cid] = fam
        fam.active = cid
        fam.state = "cancelling"
        fam.cancel_sent_ns = now
        fam.cancel_retries = 0
        self._working[fam.root] = fam
        log.error("%s: %s", cid, reason, extra=self._extra(order))
        self._journal_safe("reopened", now, order, previous_status=previous.value, venue_status=report.status)
        adjustments.append(f"{cid}: {reason}")
        self._send_cancel(order, "cancel the remainder of an order the venue still had open")

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

    # ── the venue's fills for one order ───────────────────────────────────────────────────────

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
        mine = [r for r in reports
                if (r.client_order_id is not None and r.client_order_id == str(order.client_order_id))
                or (voi is not None and str(r.order_id) == str(voi))]
        if mine:
            self._learn_venue_id(order, mine[0].order_id, "the venue's fills")
        applied, _ = self._apply_reports(order, mine, "re-query", [])
        if applied:
            log.warning("%d fill(s) of %s found only by asking the venue", applied, order.client_order_id,
                        extra=self._extra(order))
        return applied

    def _apply_reports(self, order: Order, reports: List[FillReport], source: str,
                       notes: List[str]) -> Tuple[int, int]:
        """Apply what one order's fill reports add to what the OMS has booked, never beyond the
        venue's own cumulative filled quantity: the larger of the executions' total and any
        cumulative report. A cumulative report (Moomoo without deal queries) adds only its total
        less what is booked; an execution report under an id not seen before adds only what the
        venue's total leaves room for, since the same execution may already be booked under
        another id (a status-derived fill whose deal arrived later). Returns (applied, matched)."""
        if not reports:
            return 0, 0
        executions = [r for r in reports if not r.cumulative]
        venue_total = max([sum(float(r.quantity) for r in executions)] +
                          [float(r.quantity) for r in reports if r.cumulative])
        applied = matched = 0
        for r in sorted(reports, key=lambda r: (bool(r.cumulative), pd.Timestamp(r.ts).value, str(r.trade_id))):
            if self.fill_key(order, r.trade_id, r.order_id) in self._trade_ids:
                matched += 1
                continue
            already = order.filled_qty
            if r.cumulative:
                qty = float(r.quantity) - already
                px = float(r.price)
                if qty > _QTY_EPS and already > 0 and order.avg_px is not None:
                    px = (float(r.price) * float(r.quantity) - order.avg_px * already) / qty
                    if not (math.isfinite(px) and px > 0):
                        px = float(r.price)
            else:
                qty = min(float(r.quantity), venue_total - already)
                px = float(r.price)
            if qty <= _QTY_EPS * max(1.0, order.quantity):
                matched += 1                                  # booked already, under another id
                continue
            if qty < float(r.quantity) - _QTY_EPS and not r.cumulative:
                log.warning("venue fill %s of %s: %g of its %g already booked under another id; %g applied",
                            r.trade_id, order.client_order_id, float(r.quantity) - qty, r.quantity, qty,
                            extra=self._extra(order))
            outcome = self._apply_fill_report(order, r, source, quantity=qty, price=px)
            if outcome in (Outcome.APPLY, Outcome.RACE):
                applied += 1
                notes.append(f"{order.client_order_id}: missed fill {r.trade_id} {r.side} {qty:g} @ {px:g} "
                             f"applied{' (cumulative report)' if r.cumulative else ''}")
            elif outcome is Outcome.DUPLICATE:
                matched += 1
            else:
                notes.append(f"{order.client_order_id}: venue fill {r.trade_id} not applied "
                             f"({outcome.value}; order is {order.status.value})")
        return applied, matched

    def _apply_fill_report(self, order: Order, report: FillReport, source: str,
                           quantity: Optional[float] = None, price: Optional[float] = None) -> Outcome:
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
        tags: Dict[str, Any] = {"venue_order_id": str(report.order_id), "source": source}
        if report.commission is None or report.cumulative:
            tags["commission_unknown"] = True
        if report.cumulative:
            tags["cumulative_report"] = True
        qty = float(report.quantity) if quantity is None else float(quantity)
        px = float(report.price) if price is None else float(price)
        commission = float(report.commission or 0.0)
        if report.commission and qty != float(report.quantity) and float(report.quantity) > 0:
            commission *= qty / float(report.quantity)
        fill = OrderFilled(order.client_order_id, order.strategy_id, order.instrument_id,
                           int(pd.Timestamp(report.ts).value), trade_id=trade_id,
                           side=side, last_qty=qty, last_px=px,
                           commission=commission, currency=report.currency,
                           tags=tags)
        return self._process(fill)
