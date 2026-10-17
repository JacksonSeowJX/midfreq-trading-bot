"""The node on its real components (midfreq.trading.components), with a scripted venue: a candle
strategy's order re-priced by the OMS (the wait follows the order's family), an execution
client's reconnect reconciling, the configuration's sections reaching the components, and the
records of a refused cancel."""
from __future__ import annotations

import threading

import pandas as pd
import pytest

from _node_testkit import FakeDataClient, ScriptedVenue, tiny_config_dict
from midfreq.core.common.clock import TestClock
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.data.adapters.base import BarUpdate
from midfreq.trading.config import ConfigError, config_from_dict, instrument_for_symbol, validate
from midfreq.trading.marketdata import LiveMarketDataService
from midfreq.trading.node import ExecutionStatus, TradingNode
from midfreq.trading.oms import OrderManagementSystem
from midfreq.trading.positions import PositionLedger
from midfreq.trading.refdata import ReferenceDataService
from midfreq.trading.risk import PreTradeRiskEngine
from midfreq.trading.session_log import read_events

SPEC = BarSpecification.from_str("1-HOUR")
START = pd.Timestamp("2026-10-14 13:35", tz="UTC").value          # 09:35 New York, a Wednesday
FIRST_BAR = pd.Timestamp("2026-10-14 14:30", tz="UTC").value
SYMS = ("US.AAPL", "US.MSFT", "US.NVDA", "US.AMD")
SEC = 10**9


class ChildFillingVenue(ScriptedVenue):
    """Accepts every order; leaves an original order unfilled, fills a re-priced child at once."""

    def submit_order(self, order):
        super().submit_order(order)
        if "parent" in order.tags:
            self.fill(order, order.quantity, order.price)


def make(tmp_path, venue=None, **sections):
    d = tiny_config_dict(symbols=SYMS, confirm_timeout_s=150)
    d.update(sections)
    cfg = config_from_dict(d)
    venue = venue or ChildFillingVenue()
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data", records_tz="America/New_York",
                       components={"execution_client:acct": venue, "data_client": FakeDataClient()})
    return node, venue


def push_prices(node, closes, at=START):
    for sym, c in zip(SYMS, closes):
        bar = Bar(BarType(instrument_for_symbol(sym), SPEC), c, c, c, c, 1.0, FIRST_BAR, at)
        node.queue.put(BarUpdate(bar, False, at))
    node.advance_to(at)


def test_the_node_builds_the_real_components(tmp_path):
    node, _ = make(tmp_path)
    node.start()
    assert isinstance(node.oms, OrderManagementSystem) and isinstance(node.risk, PreTradeRiskEngine)
    assert isinstance(node.positions, PositionLedger) and isinstance(node.marketdata, LiveMarketDataService)
    assert isinstance(node.refdata, ReferenceDataService)
    a = node.host.legacy[0]
    assert node.positions.cash(a.id) == a.portfolio.cash == 100_000.0     # the book seeded from the strategy's
    node.stop()


def test_a_repriced_order_is_waited_for_through_its_family(tmp_path):
    """The first order sits unfilled; after 30 s the OMS cancels it and sends a child at a new
    price with a new id. The strategy's execute_trade waits until the child fills (the parent's
    cancel does not end the wait), and books the child's fill as its trade."""
    node, venue = make(tmp_path, oms={"unfilled_order": {"wait_s": 30, "max_reprices": 2}})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert len(venue.submitted) == 2
    parent, child = venue.submitted
    assert child.tags["root"] == str(parent.client_order_id) and child.tags["attempt"] == 2
    assert parent.client_order_id != child.client_order_id
    assert str(parent.client_order_id).startswith("xsr-test-20261014-")      # the OMS's own id
    assert parent.tags["strategy_order_id"].startswith(node.order_prefix)
    assert node.oms.family_done(parent.client_order_id)
    assert a.portfolio.get_position_qty("US.AAPL") == 10                    # booked from the child
    assert node.positions.position(a.id, child.instrument_id) == 10
    trades = [e for e in read_events(a.session_log.path) if e["type"] == "trade"]
    assert [(t["symbol"], t["side"], t["qty"], t["price"]) for t in trades] == [("US.AAPL", "BUY", 10.0, child.price)]
    assert trades[0]["timestamp"] == "2026-10-14 10:30:00+00:00"             # the decision candle, from the root
    assert a.open_orders() == []
    assert node.clock.timestamp_ns() - START >= 30 * SEC                     # it waited the 30 s
    node.stop()


def test_without_a_reprice_the_wait_ends_when_the_order_is_cancelled(tmp_path):
    node, venue = make(tmp_path, oms={"unfilled_order": "original_engine"})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert len(venue.submitted) == 1 and venue.cancels == venue.submitted
    assert a.portfolio.get_position_qty("US.AAPL") == 0 and a.open_orders() == []
    assert node.clock.timestamp_ns() - START < 40 * SEC                      # 30 s and the cancel, not 150
    node.stop()


def test_an_execution_client_reconnect_reconciles(tmp_path):
    node, venue = make(tmp_path)
    node.start()
    reasons = []
    real = node._reconcile
    node._reconcile = lambda reason="periodic": (reasons.append(reason), real(reason))
    t = threading.Thread(target=lambda: (venue._status("disconnected", "link lost", START),
                                         venue._status("reconnected", "caught up", START)))
    t.start()
    t.join()
    assert reasons == []                                                     # only queued so far
    node.process_pending()
    assert node.dispatched["ExecutionStatus"] == 2
    assert reasons == ["account acct reconnected"]
    node.stop()


def test_a_refused_cancel_reaches_the_journal_and_the_strategy_topic(tmp_path):
    class Refusing(ScriptedVenue):
        def cancel_order(self, order):
            self.cancels.append(order)
            from midfreq.core.model.events import OrderCancelRejected
            self._emit(OrderCancelRejected(order.client_order_id, order.strategy_id, order.instrument_id, START,
                                           reason="being matched"))
    node, venue = make(tmp_path, venue=Refusing(), oms={"unfilled_order": {"wait_s": 30, "max_reprices": 0,
                                                                           "max_cancel_retries": 0}})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    order = venue.submitted[0]
    assert order.is_open                                                     # still working at the venue
    journal = "".join(p.read_text() for p in (tmp_path / "rec").glob("orders_*.jsonl"))
    assert "OrderCancelRejected" in journal and "being matched" in journal
    node.stop()


def test_the_configuration_reaches_the_components(tmp_path):
    node, _ = make(tmp_path, risk={"max_order_notional": 500.0, "price_band_pct": 0.05},
                   oms={"unfilled_order": {"wait_s": 45, "max_reprices": 1}},
                   marketdata={"min_cross_section_share": 0.75, "finality_grace_s": 10})
    node.start()
    sid = node.host.legacy[0].id
    assert node.risk.limits.max_order_notional == 500.0
    assert node.risk.limits_for(sid).fee_rate == 0.00005                     # the strategy's commission rate
    assert node.oms.policy.wait_s == 45 and node.oms.policy.max_reprices == 1
    assert node.marketdata._min_share == 0.75 and node.marketdata._grace_ns == 10 * SEC
    node.stop()


@pytest.mark.parametrize("section,value,message", [
    ("risk", {"max_order_value": 1}, "max_order_value"),
    ("oms", {"unfilled_order": {"reprice_attempts": 1}}, "reprice_attempts"),
    ("marketdata", {"grace": 5}, "grace"),
])
def test_a_misspelt_setting_is_refused(tmp_path, section, value, message):
    cfg = config_from_dict({**tiny_config_dict(symbols=SYMS, confirm_timeout_s=150), section: value})
    assert any(message in p for p in validate(cfg))
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data",
                       components={"execution_client:acct": ScriptedVenue(), "data_client": FakeDataClient()})
    with pytest.raises(ConfigError, match=message):
        node.start()


def test_a_strategy_that_would_give_up_before_the_oms_is_refused():
    cfg = config_from_dict({**tiny_config_dict(symbols=SYMS, confirm_timeout_s=75),
                            "oms": {"unfilled_order": {"wait_s": 30, "max_reprices": 2}}})
    assert any("confirm_timeout_s 75 is shorter" in p for p in validate(cfg))
    gtc = config_from_dict(tiny_config_dict(symbols=SYMS, confirm_timeout_s=150, pricing={"time_in_force": "GTC"}))
    assert any("DAY orders" in p for p in validate(gtc))


# ── safety review, 17 October 2026 ───────────────────────────────────────────────────────────

class FillingVenue(ScriptedVenue):
    """Accepts and fills every order at once at its limit."""

    def submit_order(self, order):
        super().submit_order(order)
        self.fill(order, order.quantity, order.price)


def test_c1_a_filled_order_resolves_at_once_under_the_id_the_adapter_stored(tmp_path):
    """C1: the adapter takes its order id from the OMS before it stores its record, so the OMS
    never renames the order, the fill resolves the wait at once (no confirm timeout) and the
    trade line carries the decision candle's timestamp."""
    node, venue = make(tmp_path, venue=FillingVenue())
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    seen = []
    real_submit = node.oms.submit

    def spy(order):
        before = order.client_order_id
        stored = before in a._orders
        real_submit(order)
        seen.append((before, order.client_order_id, stored))
    node.oms.submit = spy
    t0 = node.now_ns()
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert node.now_ns() == t0                                   # resolved without waiting
    (before, after, stored), = seen
    assert before == after and stored                            # never renamed; stored first
    assert str(before).startswith("xsr-test-20261014-")
    trades = [e for e in read_events(a.session_log.path) if e["type"] == "trade"]
    assert [(t["timestamp"], t["symbol"], t["qty"]) for t in trades] == [("2026-10-14 10:30:00+00:00", "US.AAPL", 10.0)]
    assert a.open_orders() == []
    node.stop()
