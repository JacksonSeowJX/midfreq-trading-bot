"""
The simulated venue (midfreq.trading.testing.sim_venue): fill rules, latency and partial fills on a
TestClock, rejections, cancels racing fills, reconciliation reads, and the recorded-bar feed.
The contract scenarios it shares with the real venues are in
packages/midfreq-data/tests/test_execution_contract.py.
"""
from __future__ import annotations

import pandas as pd
import pytest

from midfreq.core.common.clock import TestClock
from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.enums import OrderSide
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import InstrumentId, StrategyId
from midfreq.core.model.orders import OrderFactory
from midfreq.data.adapters.base import BarUpdate, ConnectorError
from midfreq.trading.testing import SimScheduler, SimulatedDataClient, SimulatedExecutionClient

AAPL = InstrumentId.from_str("AAPL.XNAS")
T0 = int(pd.Timestamp("2026-10-17 13:30", tz="UTC").value)
MS = 1_000_000
HOUR = 3_600_000_000_000


def kinds(events):
    return [type(e).__name__ for e in events]


def venue(**kwargs):
    clock = kwargs.pop("clock", None) or TestClock(T0)
    events = []
    sim = SimulatedExecutionClient(clock, **kwargs)
    sim.set_event_handler(events.append)
    sim.connect()
    return sim, clock, events


def orders():
    return OrderFactory(StrategyId("XSReversal-001"))


# ── fill rules ───────────────────────────────────────────────────────

def test_market_orders_fill_at_the_reference_price_moved_by_slippage():
    sim, _, events = venue(slippage_bps=10, commission=lambda o, q, p: q * p * 0.0005, currency="USD")
    f = orders()
    sim.submit_order(f.market(AAPL, OrderSide.BUY, 10, reference_price=200.0))
    sim.submit_order(f.market(AAPL, OrderSide.SELL, 5, reference_price=200.0))
    assert kinds(events) == ["OrderAccepted", "OrderFilled"] * 2
    buy, sell = [e for e in events if isinstance(e, OrderFilled)]
    assert buy.last_px == pytest.approx(200.2) and buy.last_qty == 10
    assert sell.last_px == pytest.approx(199.8) and sell.side is OrderSide.SELL
    assert buy.commission == pytest.approx(10 * 200.2 * 0.0005) and buy.currency == "USD"
    assert buy.tags["venue_order_id"] == "SIM-1" and str(buy.trade_id) == "SIM-T1"


def test_market_orders_without_a_reference_use_the_price_source_or_are_rejected():
    prices = {}
    sim, _, events = venue(price_source=prices.get)
    f = orders()
    sim.submit_order(f.market(AAPL, OrderSide.BUY, 1))
    assert kinds(events) == ["OrderRejected"] and "no price for AAPL.XNAS" in events[0].reason
    prices[AAPL] = 150.0
    sim.submit_order(f.market(AAPL, OrderSide.BUY, 1))
    assert events[-1].last_px == 150.0


def test_a_marketable_limit_fills_at_the_market_and_a_resting_one_at_its_limit():
    sim, _, events = venue()
    sim.set_price(AAPL, 100.0)
    f = orders()
    sim.submit_order(f.limit(AAPL, OrderSide.BUY, 1, 101.0))       # marketable: gets 100, better than 101
    assert events[-1].last_px == 100.0
    resting = f.limit(AAPL, OrderSide.BUY, 2, 98.0)
    sim.submit_order(resting)
    assert kinds(events[-1:]) == ["OrderAccepted"]
    sim.on_bar(Bar(BarType.of(AAPL, "1-HOUR"), 100, 100, 99, 99.0, 1000, T0, T0))   # not low enough
    assert kinds(events[-1:]) == ["OrderAccepted"]
    sim.on_bar(Bar(BarType.of(AAPL, "1-HOUR"), 99, 99, 97, 97.0, 1000, T0 + HOUR, T0 + HOUR))
    assert isinstance(events[-1], OrderFilled) and events[-1].last_px == 98.0 and events[-1].last_qty == 2
    sell = f.limit(AAPL, OrderSide.SELL, 1, 99.0)
    sim.submit_order(sell)
    sim.on_quote(QuoteTick(AAPL, 99.4, 99.6, 1, 1, T0 + 2 * HOUR, T0 + 2 * HOUR))
    assert events[-1].last_px == 99.0 and events[-1].side is OrderSide.SELL


def test_latency_and_partial_fills_follow_the_test_clock():
    clock = TestClock(T0)
    scheduler = SimScheduler(clock)
    sim, _, events = venue(clock=clock, schedule=scheduler, latency_ns=50 * MS, partial_fills=(0.25, 0.25, 0.5),
                           partial_fill_interval_ns=1000 * MS)
    sim.submit_order(orders().market(AAPL, OrderSide.BUY, 8, reference_price=10.0))
    assert events == [] and sim.open_orders() == []          # still on its way
    scheduler.advance_to(T0 + 49 * MS)
    assert events == []
    scheduler.advance_to(T0 + 50 * MS)
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]
    assert events[0].ts_event == T0 + 50 * MS and events[1].last_qty == 2
    scheduler.run()
    fills = [e for e in events if isinstance(e, OrderFilled)]
    assert [f.last_qty for f in fills] == [2, 2, 4]
    assert [f.ts_event for f in fills] == [T0 + 50 * MS, T0 + 1050 * MS, T0 + 2050 * MS]
    assert clock.timestamp_ns() == T0 + 2050 * MS


def test_partial_slices_round_to_the_quantity_step():
    sim, _, events = venue(partial_fills=(0.5, 0.5), quantity_step=lambda iid: 100.0)
    sim.submit_order(orders().market(AAPL, OrderSide.BUY, 300, reference_price=10.0))
    assert [e.last_qty for e in events if isinstance(e, OrderFilled)] == [100, 200]


def test_a_cancel_between_slices_cancels_the_rest():
    clock = TestClock(T0)
    scheduler = SimScheduler(clock)
    sim, _, events = venue(clock=clock, schedule=scheduler, partial_fills=(0.5, 0.5), partial_fill_interval_ns=HOUR)
    order = orders().market(AAPL, OrderSide.BUY, 10, reference_price=10.0)
    sim.submit_order(order)
    scheduler.advance_to(T0 + 1)
    sim.cancel_order(order)
    scheduler.run()
    assert kinds(events) == ["OrderAccepted", "OrderFilled", "OrderCanceled"]
    assert events[1].last_qty == 5 and "canceled" in events[2].reason


def test_a_cancel_arriving_after_the_fill_is_refused():
    clock = TestClock(T0)
    scheduler = SimScheduler(clock)
    sim, _, events = venue(clock=clock, schedule=scheduler, latency_ns=10 * MS)
    order = orders().market(AAPL, OrderSide.BUY, 1, reference_price=10.0)
    sim.submit_order(order)
    sim.cancel_order(order)               # sent at once, but reaches the venue after the order filled
    scheduler.run()
    assert kinds(events) == ["OrderAccepted", "OrderFilled"]


def test_a_resting_order_is_cancelled():
    sim, _, events = venue()
    sim.set_price(AAPL, 100.0)
    order = orders().limit(AAPL, OrderSide.BUY, 1, 90.0)
    sim.submit_order(order)
    sim.cancel_order(order)
    assert kinds(events) == ["OrderAccepted", "OrderCanceled"]
    assert sim.open_orders() == []


def test_rejections_from_the_lot_and_cash_checks():
    lot = lambda o: None if o.quantity % 100 == 0 else f"{o.quantity:g} is not a whole board lot of 100"   # noqa: E731
    cash = lambda o, px: None if o.quantity * px <= 10_000 else f"insufficient cash for {o.quantity * px:,.0f}"  # noqa: E731
    sim, _, events = venue(check_lot=lot, check_cash=cash)
    f = orders()
    sim.submit_order(f.market(AAPL, OrderSide.BUY, 150, reference_price=10.0))
    sim.submit_order(f.limit(AAPL, OrderSide.BUY, 2000, 10.0))
    sim.submit_order(f.market(AAPL, OrderSide.BUY, 100, reference_price=10.0))
    assert kinds(events) == ["OrderRejected", "OrderRejected", "OrderAccepted", "OrderFilled"]
    assert "board lot" in events[0].reason and "insufficient cash for 20,000" in events[1].reason


def test_reports_for_reconciliation():
    sim, clock, _ = venue(partial_fills=(0.5, 0.5), commission=lambda o, q, p: 1.0, currency="USD")
    sim.set_price(AAPL, 100.0)
    f = orders()
    filled = f.market(AAPL, OrderSide.BUY, 4, reference_price=100.0)
    sim.submit_order(filled)
    resting = f.limit(AAPL, OrderSide.SELL, 3, 120.0)
    sim.submit_order(resting)
    (report,) = sim.open_orders()
    assert (report.order_id, report.side, report.quantity, report.filled_quantity, report.price, report.status) == \
        ("SIM-2", "SELL", 3, 0, 120.0, "ACCEPTED")
    assert report.client_order_id == str(resting.client_order_id)
    fills = sim.fills(pd.Timestamp(T0, unit="ns", tz="UTC"))
    assert [(x.order_id, x.quantity, x.price, x.commission, x.currency) for x in fills] == \
        [("SIM-1", 2, 100.0, 1.0, "USD")] * 2
    assert fills[0].client_order_id == str(filled.client_order_id) and fills[0].instrument_id == "AAPL.XNAS"
    assert sim.fills(pd.Timestamp(T0 + 1, unit="ns", tz="UTC")) == []
    assert sim.query_order(filled).status == "FILLED" and sim.query_order(filled).average_fill_price == 100.0


def test_the_simulated_venue_runs_only_in_sim():
    clock = TestClock(T0)
    with pytest.raises(ConnectorError):
        SimulatedExecutionClient(clock, environment="live")
    with pytest.raises(ValueError, match="scheduler"):
        SimulatedExecutionClient(clock, latency_ns=1)
    sim = SimulatedExecutionClient(clock)
    assert sim.environment == "sim" and str(sim.client_id) == "SIM" and str(sim.account_id) == "SIM-001"
    with pytest.raises(ConnectorError, match="not connected"):
        sim.submit_order(orders().market(AAPL, OrderSide.BUY, 1, reference_price=1.0))


def test_the_same_inputs_give_the_same_events():
    def run():
        clock = TestClock(T0)
        scheduler = SimScheduler(clock)
        sim, _, events = venue(clock=clock, schedule=scheduler, latency_ns=5 * MS, slippage_bps=3,
                               partial_fills=(0.3, 0.7), partial_fill_interval_ns=MS)
        f = orders()
        for i in range(5):
            sim.submit_order(f.market(AAPL, OrderSide.BUY if i % 2 else OrderSide.SELL, 10 + i, reference_price=50 + i))
            scheduler.advance_to(clock.timestamp_ns() + 2 * MS)
        scheduler.run()
        return events
    assert run() == run()


# ── the recorded-bar feed ────────────────────────────────────────────

def hourly(n, start=T0, close0=100.0):
    bt = BarType.of(AAPL, "1-HOUR")
    return [Bar(bt, close0 + i, close0 + i + 1, close0 + i - 1, close0 + i + 0.5, 1000 + i,
                start + (i + 1) * HOUR, start + (i + 1) * HOUR) for i in range(n)]


def test_recorded_bars_arrive_on_the_clock_in_time_order():
    clock = TestClock(T0)
    bars = hourly(3)
    data = SimulatedDataClient(list(reversed(bars)), clock, delay_ns=2 * MS)
    got = []
    data.subscribe_bars(BarType.of(AAPL, "1-HOUR"), got.append)
    data.scheduler.advance_to(T0 + HOUR + MS)
    assert got == []
    data.scheduler.advance_to(T0 + HOUR + 2 * MS)
    assert len(got) == 1 and got[0] == BarUpdate(bars[0], True, T0 + HOUR + 2 * MS)
    data.scheduler.run()
    assert [u.bar for u in got] == bars and all(u.closed for u in got)
    assert [u.ts_received for u in got] == [b.ts_event + 2 * MS for b in bars]
    assert data.last_price(AAPL) == bars[-1].close
    assert data.subscription_limit() is None and str(data.client_id) == "SIM"


def test_recorded_pushes_and_quotes_keep_their_own_times():
    clock = TestClock(T0)
    bt = BarType.of(AAPL, "1-HOUR")
    forming = Bar(bt, 1, 2, 0.5, 1.5, 10, T0 + HOUR, T0 + HOUR // 2)
    final = Bar(bt, 1, 2, 0.5, 1.8, 20, T0 + HOUR, T0 + HOUR + 5 * MS)
    quote = QuoteTick(AAPL, 1.79, 1.81, 5, 6, T0 + HOUR + 7 * MS, T0 + HOUR + 7 * MS)
    data = SimulatedDataClient([BarUpdate(final, True, T0 + HOUR + 5 * MS), quote,
                                BarUpdate(forming, False, T0 + HOUR // 2)], clock)
    seen = []
    data.subscribe_bars(bt, lambda u: seen.append(("bar", u.closed, clock.timestamp_ns())))
    data.subscribe_quotes(AAPL, lambda q: seen.append(("quote", q.bid, clock.timestamp_ns())))
    data.scheduler.run()
    assert seen == [("bar", False, T0 + HOUR // 2), ("bar", True, T0 + HOUR + 5 * MS),
                    ("quote", 1.79, T0 + HOUR + 7 * MS)]
    assert data.request_bars(bt, T0, T0 + 2 * HOUR) == [final]     # history holds closed bars only


def test_the_venue_sees_each_bar_before_the_strategy():
    clock = TestClock(T0)
    scheduler = SimScheduler(clock)
    bars = hourly(2, close0=100.0)                     # closes 100.5 then 101.5; lows 99 then 100
    data = SimulatedDataClient(bars, clock, scheduler)
    sim, _, events = venue(clock=clock, schedule=scheduler, price_source=data.last_price)
    data.add_listener(sim.on_item)
    order = orders().limit(AAPL, OrderSide.SELL, 1, 101.0)
    seen_by_strategy = []
    data.subscribe_bars(BarType.of(AAPL, "1-HOUR"),
                        lambda u: seen_by_strategy.append((u.bar.close, kinds(events))))
    scheduler.advance_to(bars[0].ts_event)
    sim.submit_order(order)                            # 100.5 < 101: rests
    scheduler.advance_to(bars[1].ts_event)
    assert seen_by_strategy == [(100.5, []), (101.5, ["OrderAccepted", "OrderFilled"])]
    assert events[-1].last_px == 101.0


def test_history_and_unsubscribe():
    clock = TestClock(T0 + 10 * HOUR)
    old = hourly(3, start=T0)
    new = hourly(2, start=T0 + 10 * HOUR)
    data = SimulatedDataClient(new + old, clock, history=old)    # past items are history, not replayed
    bt = BarType.of(AAPL, "1-HOUR")
    got = []
    data.subscribe_bars(bt, got.append)
    assert data.request_bars(bt, T0, T0 + 20 * HOUR) == old
    data.scheduler.advance_to(new[0].ts_event)
    data.unsubscribe_bars(bt)
    data.scheduler.run()
    assert [u.bar for u in got] == [new[0]]
    assert data.request_bars(bt, T0, T0 + 20 * HOUR) == old + new    # delivered bars count, subscribed or not
