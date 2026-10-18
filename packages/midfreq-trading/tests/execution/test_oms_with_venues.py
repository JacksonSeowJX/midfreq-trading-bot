"""The OMS on the real execution clients over their fake venues (tests/support): what reconciliation,
re-queries and restarts do with what the venues report, where the venue clients' own conventions
meet the OMS's (cumulative status-derived fills, rejections a client had to guess, venue order
ids learned late). Safety review of 17 October 2026: C2, C3, H4."""
from __future__ import annotations

from datetime import timezone

import pandas as pd
import pytest

from fake_moomoo import FakeMoomoo, make_moomoo_execution_client
from midfreq.core.common.clock import TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model import InstrumentId, OrderFactory, OrderSide, OrderStatus, StrategyId, TimeInForce, Venue
from midfreq.trading.execution.journal import OrderJournal
from midfreq.trading.execution.oms import OrderManagementSystem, UnfilledOrderPolicy
from midfreq.trading.portfolio.ledger import PositionLedger
from midfreq.trading.risk.pretrade import PreTradeRiskEngine, RiskLimits

AAPL = InstrumentId.from_str("AAPL.XNAS")
S1 = StrategyId("S1")


class Rig:
    """The OMS, risk and ledger on a Moomoo client over the fake OpenD; one clock for both."""

    def __init__(self, tmp_path, venue: FakeMoomoo, policy=None, restore_from=None):
        self.venue = venue
        self.clock = TestClock(venue.clock())
        self.client = restore_from.client if restore_from is not None else make_moomoo_execution_client(venue=venue)[0]
        self.events = []
        self.client.set_event_handler(self.events.append)
        if restore_from is None:
            self.client.connect()
        self.bus = MessageBus()
        self.ledger = PositionLedger(self.clock, self.bus, capital={"S1": 1_000_000.0})
        self.risk = PreTradeRiskEngine(self.clock, self.bus, self.ledger, RiskLimits(), lot_size=lambda iid: 1.0)
        self.journal = OrderJournal(tmp_path / "journal")
        self.oms = OrderManagementSystem(self.clock, self.bus, self.risk, self.ledger, self.journal,
                                         {Venue("XNAS"): self.client},
                                         policy or UnfilledOrderPolicy(enabled=False), reconcile_grace_s=0)
        self.factory = OrderFactory(S1, self.clock.timestamp_ns)

    def tick(self, seconds=1.0):
        self.venue.clock.advance(seconds)
        self.clock.set_time(self.venue.clock())

    def pump(self):
        while self.events:
            self.oms.handle_event(self.events.pop(0))

    def buy(self, qty=100.0, price=100.0):
        order = self.factory.limit(AAPL, OrderSide.BUY, qty, price, TimeInForce.DAY)
        self.oms.submit(order)
        self.pump()
        return order


def test_c2_cumulative_status_fills_are_booked_once(tmp_path):
    """C2: on a paper account without deal queries, Moomoo reports an order's fills as one
    cumulative figure ('<order id>#<total filled>'). Reconciling twice while the order fills 30,
    then 50 in all, books 50, not 30 + 50."""
    rig = Rig(tmp_path, FakeMoomoo(deals_supported=False))
    order = rig.buy()
    vid = order.tags["venue_order_id"]
    rig.venue.push_enabled = False
    rig.venue.accept(vid)
    rig.venue.fill(vid, 30, 100.0)
    rig.tick()
    rig.oms.reconcile()
    assert order.filled_qty == 30 and rig.ledger.position(S1, AAPL) == 30
    rig.venue.fill(vid, 20, 101.0)
    rig.tick()
    rig.oms.reconcile()
    assert order.filled_qty == 50 and rig.ledger.position(S1, AAPL) == 50
    assert order.avg_px == pytest.approx((30 * 100.0 + 20 * 101.0) / 50)
    rig.tick()
    rig.oms.reconcile()                                         # nothing new: nothing booked
    assert rig.ledger.position(S1, AAPL) == 50


def test_c2_a_status_fill_then_the_real_deal_is_not_booked_twice(tmp_path):
    """C2: the deal push is lost, so after fill_grace the client books the fill from the order's
    status ('<id>#30'); the order is then cancelled. Reconciliation later lists the real deal:
    the venue's cumulative filled quantity is still 30, so nothing more is booked."""
    rig = Rig(tmp_path, FakeMoomoo(deal_push=False))
    order = rig.buy()
    vid = order.tags["venue_order_id"]
    rig.venue.accept(vid)
    rig.venue.fill(vid, 30, 100.0)                              # the order push says 30 dealt; no deal push
    rig.tick(6)
    rig.client.poll_now()                                       # fill_grace passed: booked from status
    rig.pump()
    assert order.filled_qty == 30
    rig.oms.cancel(order.client_order_id)
    rig.venue.cancel_ack(vid)
    rig.pump()
    assert order.status is OrderStatus.CANCELED
    rig.tick()
    rig.oms.reconcile()                                         # the deal list now has the real deal
    assert order.filled_qty == 30 and rig.ledger.position(S1, AAPL) == 30


def test_c2_after_a_restart_cumulative_and_deal_reports_book_only_what_is_missing(tmp_path):
    rig = Rig(tmp_path, FakeMoomoo(deals_supported=False))
    order = rig.buy()
    vid = order.tags["venue_order_id"]
    rig.venue.push_enabled = False
    rig.venue.accept(vid)
    rig.venue.fill(vid, 40, 100.0)
    rig.tick()
    rig.oms.reconcile()
    assert order.filled_qty == 40
    again = Rig(tmp_path, rig.venue, restore_from=rig)          # a new OMS on the same journal
    rig.venue.fill(vid, 10, 100.0)
    again.tick()
    again.oms.reconcile()
    restored = again.oms.order(order.client_order_id)
    assert restored.filled_qty == 50 and again.ledger.position(S1, AAPL) == 10   # the new 10 only


def _reject_by_guess(rig, qty=100.0):
    """place_order times out with nothing found by remark for 30 s: the client rejects it. Then
    the order turns up at OpenD after all (it was accepted late), under the same remark."""
    rig.venue.fail_next_place("send timeout", placed=False)
    order = rig.buy(qty)
    assert "venue_order_id" not in order.tags
    rig.tick(31)
    rig.client.poll_now()
    rig.pump()
    assert order.status is OrderStatus.REJECTED
    remark = rig.client._by_cid[order.client_order_id].remark
    rig.client._ctx.place_order(price=100.0, qty=qty, code="US.AAPL", trd_side="BUY", order_type="NORMAL",
                                trd_env="SIMULATE", acc_id=0, remark=remark, time_in_force="DAY")
    return order, rig.venue.last_order_id()


def test_c3_fills_on_an_order_rejected_by_guess_are_booked_and_the_order_is_cancelled(tmp_path):
    """C3: a rejection the client had to guess can be wrong. The venue's fills on that order are
    applied (the venue is the truth), and reconciliation cancels it at the venue instead of
    leaving a live order nobody manages."""
    rig = Rig(tmp_path, FakeMoomoo())
    order, oid = _reject_by_guess(rig)
    rig.venue.accept(oid)
    rig.venue.fill(oid, 40, 100.0)
    rig.pump()
    assert order.filled_qty == 40 and rig.ledger.position(S1, AAPL) == 40
    rig.tick()
    report = rig.oms.reconcile()
    assert order.tags["venue_order_id"] == oid                       # H4: learned and kept
    assert rig.venue.cancel_requested(oid), "the live remainder was cancelled at the venue"
    assert any("open at the venue" in a for a in report.adjustments)
    rig.venue.cancel_ack(oid)
    rig.pump()
    rig.tick(31)
    rig.oms.on_timer(rig.clock.timestamp_ns())
    rig.pump()
    assert order.status is OrderStatus.CANCELED and order.filled_qty == 40
    assert rig.ledger.position(S1, AAPL) == 40


def test_h4_a_venue_order_id_learned_by_reconciliation_is_kept_and_used_to_cancel(tmp_path):
    """H4: place_order's reply is lost but the order exists; reconciliation finds it by its
    remark. The OMS records the venue's order id on the order (and journals it), so a cancel
    reaches the venue at once instead of doing nothing."""
    rig = Rig(tmp_path, FakeMoomoo())
    rig.venue.fail_next_place("send timeout", placed=True)          # placed, reply lost
    order = rig.buy()
    oid = rig.venue.last_order_id()
    assert "venue_order_id" not in order.tags and order.is_open
    rig.tick(15)
    rig.oms.reconcile()
    assert order.tags["venue_order_id"] == oid
    assert rig.oms.order(order.client_order_id) is order
    journal = "".join(p.read_text() for p in (tmp_path / "journal").glob("orders_*.jsonl"))
    assert f'"venue_order_id": "{oid}"' in journal
    rig.oms.cancel(order.client_order_id)
    assert rig.venue.cancel_requested(oid)
