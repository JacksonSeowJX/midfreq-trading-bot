"""LiveLegacyAdapter: a candle strategy's execute_trade becomes an order for the OMS, and its
portfolio is booked only when fills arrive."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import pytest

from midfreq.core.cache import Cache
from midfreq.core.common.clock import TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.enums import OrderSide, OrderType, TimeInForce
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import TradeId
from midfreq.core.venues.spec import default_venues
from midfreq.strategies.candle_strategies import CrossSectionalReversal
from midfreq.trading.config import PricingSettings, StrategySettings, instrument_for_symbol
from midfreq.trading.interfaces import TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_ORDER_EVENTS, CrossSection
from midfreq.trading.session_log import read_events
from midfreq.trading.strategy_host import LiveLegacyAdapter, NodeServices, round_to_tick

SPEC = BarSpecification.from_str("1-HOUR")
T0 = pd.Timestamp("2026-10-14 14:30", tz="UTC").value          # 10:30 New York: the first hourly bar
HOUR = 3600 * 10**9


@dataclass
class Rig:
    adapter: LiveLegacyAdapter
    bus: MessageBus
    clock: TestClock
    submitted: List[Any]
    cancels: List[Any]
    waits: List[float]
    latest: Dict[Any, float]
    state: Dict[str, Any] = field(default_factory=dict)

    def fill(self, order, qty, px, trade_id="T1"):
        self.bus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id),
                         OrderFilled(order.client_order_id, order.strategy_id, order.instrument_id, 0,
                                     trade_id=TradeId(trade_id), side=order.side, last_qty=qty, last_px=px,
                                     tags={"venue_order_id": "V1"}))

    def event(self, cls, order, **kw):
        self.bus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id),
                         cls(order.client_order_id, order.strategy_id, order.instrument_id, 0, **kw))

    def xs(self, ts, closes, complete=True):
        bars = {}
        for sym, c in closes.items():
            iid = self.adapter.instrument_for[sym]
            bars[iid] = Bar(BarType(iid, SPEC), c, c, c, c, 1.0, ts, ts)
        self.bus.publish(TOPIC_CROSS_SECTION.format(universe=self.adapter.universe),
                         CrossSection(self.adapter.universe, ts, bars, complete))


def make(tmp_path, symbols=("US.AAPL", "US.MSFT"), confirm=False, capital=10_000.0, lots=None,
         params=None, on_submit: Optional[Callable] = None, wait_result: Optional[Callable] = None,
         broker_positions=None, account=None, state_seed=None):
    venues = default_venues()
    bus, clock, cache = MessageBus(), TestClock(T0), Cache()
    submitted, cancels, waits, latest = [], [], [], {}
    st = {"open": True}

    def submit(order):
        submitted.append(order)
        if on_submit is not None:
            on_submit(order)

    def wait_until(pred, timeout, on_timeout):
        waits.append(timeout)
        if wait_result is not None:
            wait_result()
        if not pred():
            on_timeout()
            return pred()
        return True

    def instrument(iid):
        inst = venues.get(iid.venue).default_instrument(str(iid.symbol))
        if lots and str(iid) in lots:
            import dataclasses
            inst = dataclasses.replace(inst, lot_size=lots[str(iid)])
        return inst

    services = NodeServices(clock=clock, msgbus=bus, cache=cache, venues=venues, submit=submit,
                            cancel=cancels.append, wait_until=wait_until, latest_price=lambda iid: latest.get(iid),
                            quote=cache.quote, instrument=instrument,
                            local_now=lambda: datetime(2026, 10, 14, 21, 35, 2, 911315),
                            warm_up=lambda bt, n: [], market_open=lambda v: st["open"])
    settings = StrategySettings(id="t", cls="Cross-Sectional Reversal", capital=capital, account="a", universe="u",
                                confirm_fills=confirm, pricing=PricingSettings())
    ids = {s: instrument_for_symbol(s, venues) for s in symbols}
    if state_seed is not None:
        (tmp_path / "state_crosssectionalreversal.json").write_text(json.dumps(state_seed, indent=1))
    a = LiveLegacyAdapter(settings, services, strategy_class=CrossSectionalReversal,
                          params=params or {"lookback": 2, "top_n": 1, "rebalance_every": 1},
                          symbols=list(symbols), instrument_ids=ids, bar_spec=SPEC, commission_rate=0.00005,
                          records_dir=tmp_path, universe="u")
    a.open(account, {} if broker_positions is None else broker_positions)
    a.start_session(account, "1h")
    a.warm_up(0)
    a.trading_on()
    a.subscribe()
    return Rig(a, bus, clock, submitted, cancels, waits, latest, st)


TS = pd.Timestamp("2026-10-14 10:30", tz="UTC")


def trades_logged(rig):
    return [e for e in read_events(rig.adapter.session_log.path) if e["type"] == "trade"]


def test_execute_trade_sends_a_limit_order_and_books_nothing(tmp_path):
    rig = make(tmp_path)
    p = rig.adapter.portfolio
    rig.latest[rig.adapter.instrument_for["US.AAPL"]] = 100.0
    cash = p.cash
    p.execute_trade("US.AAPL", True, 10, 99.5, TS)
    assert len(rig.submitted) == 1
    o = rig.submitted[0]
    assert (o.side, o.order_type, o.quantity, o.price, o.reference_price) == (OrderSide.BUY, OrderType.LIMIT, 10, 101.0, 99.5)
    assert o.time_in_force is TimeInForce.DAY
    assert o.tags["decision_ts"] == str(TS) and o.tags["legacy_symbol"] == "US.AAPL"
    assert p.positions == {} and p.cash == cash and p.trade_history == []
    assert trades_logged(rig) == []


def test_the_fill_is_booked_when_it_arrives(tmp_path):
    rig = make(tmp_path)
    p = rig.adapter.portfolio
    rig.latest[rig.adapter.instrument_for["US.AAPL"]] = 100.0
    p.execute_trade("US.AAPL", True, 10, 99.5, TS)
    o = rig.submitted[0]
    rig.fill(o, 10.0, 100.2)
    assert p.positions == {"US.AAPL": {"qty": 10.0, "entry_price": 100.2}}
    assert p.cash == pytest.approx(10_000 - 10 * 100.2 * 1.00005)
    assert trades_logged(rig) == [{"type": "trade", "timestamp": str(TS), "symbol": "US.AAPL", "side": "BUY",
                                   "qty": 10.0, "price": 100.2, "order_id": "V1", "exit_reason": None}]
    state = json.loads((tmp_path / "state_crosssectionalreversal.json").read_text())
    assert state["positions"] == {"US.AAPL": {"qty": 10.0, "entry_price": 100.2}}
    rig.fill(o, 10.0, 100.2)                     # a repeated push is not booked twice
    assert p.get_position_qty("US.AAPL") == 10.0 and len(p.trade_history) == 1


def test_partial_fill_then_cancel_books_the_part(tmp_path):
    rig = make(tmp_path)
    p = rig.adapter.portfolio
    p.execute_trade("US.AAPL", True, 10, 100.0, TS)
    o = rig.submitted[0]
    rig.fill(o, 4.0, 100.0)
    rig.event(OrderCanceled, o, reason="remainder cancelled")
    assert p.get_position_qty("US.AAPL") == 4.0
    assert rig.adapter.open_orders() == []


def test_a_rejected_or_cancelled_order_books_nothing(tmp_path):
    rig = make(tmp_path)
    p = rig.adapter.portfolio
    p.execute_trade("US.AAPL", True, 10, 100.0, TS)
    rig.event(OrderRejected, rig.submitted[0], reason="insufficient cash")
    p.execute_trade("US.MSFT", True, 5, 100.0, TS)
    rig.event(OrderAccepted, rig.submitted[1])
    rig.event(OrderCanceled, rig.submitted[1], reason="CANCELLED_ALL")
    assert p.positions == {} and p.trade_history == [] and trades_logged(rig) == []


def test_confirm_mode_waits_for_the_outcome_before_returning(tmp_path):
    holder = {}
    rig = make(tmp_path, confirm=True, wait_result=lambda: holder["rig"].fill(holder["rig"].submitted[-1], 10.0, 100.0))
    holder["rig"] = rig
    rig.adapter.portfolio.execute_trade("US.AAPL", True, 10, 100.0, TS)
    # booked by the time execute_trade returned, and no cancel needed
    assert rig.adapter.portfolio.get_position_qty("US.AAPL") == 10.0
    assert rig.waits == [75.0] and rig.cancels == []


def test_confirm_mode_cancels_an_order_that_does_not_resolve(tmp_path):
    rig = make(tmp_path, confirm=True)
    rig.adapter.portfolio.execute_trade("US.AAPL", True, 10, 100.0, TS)
    assert rig.cancels == [rig.submitted[0].client_order_id]
    assert rig.adapter.portfolio.positions == {}
    # a late fill is still booked
    rig.fill(rig.submitted[0], 10.0, 100.0)
    assert rig.adapter.portfolio.get_position_qty("US.AAPL") == 10.0


def test_buy_rounded_down_to_the_board_lot_and_skipped_below_one(tmp_path):
    rig = make(tmp_path, symbols=("HK.00700", "HK.00005"), lots={"00700.XHKG": 100}, capital=1_000_000)
    p = rig.adapter.portfolio
    assert p.lot_sizes["HK.00700"] == 100
    p.execute_trade("HK.00700", True, 250, 400.0, TS)
    p.execute_trade("HK.00700", True, 50, 400.0, TS)
    assert [o.quantity for o in rig.submitted] == [200]
    assert rig.submitted[0].price == round_to_tick(400.0 * 1.01, 0.001)


def test_buy_trimmed_to_cash_at_the_worst_case_and_sell_capped_at_holding(tmp_path):
    rig = make(tmp_path, capital=10_000)
    p = rig.adapter.portfolio
    rig.latest[rig.adapter.instrument_for["US.AAPL"]] = 100.0
    p.execute_trade("US.AAPL", True, 200, 100.0, TS)
    assert rig.submitted[0].quantity == int(10_000 // (100.0 * 1.01 * 1.00005)) == 99
    rig.fill(rig.submitted[0], 99.0, 100.0)
    p.execute_trade("US.AAPL", False, 150, 100.0, TS, exit_reason="rebalance")
    sell = rig.submitted[1]
    assert (sell.side, sell.quantity, sell.price) == (OrderSide.SELL, 99.0, 99.0)
    assert sell.tags["exit_reason"] == "rebalance"
    p.execute_trade("US.MSFT", False, 5, 100.0, TS)          # holds none: nothing sent
    assert len(rig.submitted) == 2


def test_no_order_while_trading_is_off(tmp_path):
    rig = make(tmp_path)
    rig.adapter.trading_off()
    rig.adapter.portfolio.execute_trade("US.AAPL", True, 10, 100.0, TS)
    assert rig.submitted == []


def test_candles_arrive_in_the_strategys_timestamp_convention(tmp_path):
    rig = make(tmp_path)
    rig.xs(T0, {"US.AAPL": 100.0, "US.MSFT": 200.0})
    s = rig.adapter.strategy
    assert s.current_ts == pd.Timestamp("2026-10-14 10:30", tz="UTC")       # New York wall time, tagged UTC
    assert s.history == {"US.AAPL": [100.0], "US.MSFT": [200.0]}
    assert s._cross_section_count == 1                                       # closed on its last bar
    closes = [e for e in read_events(rig.adapter.session_log.path) if e["type"] == "candle_close"]
    assert [(e["symbol"], e["timestamp"], e["close"]) for e in closes] == [
        ("US.AAPL", "2026-10-14 10:30:00+00:00", 100.0), ("US.MSFT", "2026-10-14 10:30:00+00:00", 200.0)]
    assert rig.adapter.candles_processed == 2


def test_a_cross_section_final_while_the_market_is_closed_is_held(tmp_path):
    rig = make(tmp_path)
    rig.state["open"] = False
    rig.xs(T0, {"US.AAPL": 100.0, "US.MSFT": 200.0})
    assert rig.adapter.strategy.current_ts is None
    rig.adapter.on_timer()
    assert rig.adapter.strategy.current_ts is None
    rig.state["open"] = True
    rig.adapter.on_timer()
    assert rig.adapter.strategy._cross_section_count == 1


def test_held_cross_section_fed_with_trading_off_at_shutdown(tmp_path):
    rig = make(tmp_path, params={"lookback": 1, "top_n": 1, "rebalance_every": 1})
    rig.xs(T0, {"US.AAPL": 100.0, "US.MSFT": 200.0})
    rig.state["open"] = False
    rig.xs(T0 + HOUR, {"US.AAPL": 90.0, "US.MSFT": 210.0})                # a rebalance would be due
    rig.adapter.trading_off()
    rig.adapter.finalize()
    s = rig.adapter.strategy
    assert s._cross_section_count == 2 and rig.submitted == []
    assert s._next_rebalance_at <= s._cross_section_count                  # it stays due


def test_bars_that_never_make_a_cross_section_are_fed_before_the_next(tmp_path):
    rig = make(tmp_path)
    a = rig.adapter
    iid = a.instrument_for["US.AAPL"]
    rig.bus.publish(TOPIC_BAR.format(instrument_id=iid), Bar(BarType(iid, SPEC), 99.0, 99.0, 99.0, 99.0, 1.0, T0, T0))
    rig.xs(T0 + HOUR, {"US.AAPL": 100.0, "US.MSFT": 200.0})
    s = a.strategy
    assert s.history["US.AAPL"] == [99.0, 100.0]
    assert s._cross_section_count == 2            # the lone bar's cross-section, then the full one


def test_incomplete_published_cross_section_is_decided_at_once(tmp_path):
    rig = make(tmp_path, symbols=tuple(f"US.{s}" for s in ("AAPL", "MSFT", "NVDA", "AMD", "INTC", "CSCO",
                                                           "ORCL", "IBM", "QCOM", "TXN")))
    closes = {s: 100.0 for s in rig.adapter.symbols[:9]}                  # 9 of 10 at the window's end
    rig.xs(T0, closes, complete=False)
    assert rig.adapter.strategy._cross_section_count == 1
    assert rig.adapter.strategy.full_cross_section == 10                  # restored afterwards


def test_rebalance_sells_then_buys_with_the_proceeds(tmp_path):
    """Confirm mode against a venue that fills at once: the buy is sized on the sale's proceeds,
    as the original engine (synchronous) sized it."""
    holder = {}

    def fill_last():
        r = holder["rig"]
        o = r.submitted[-1]
        r.fill(o, o.quantity, o.reference_price, trade_id=f"T{len(r.submitted)}")
    rig = make(tmp_path, confirm=True, capital=10_000, wait_result=fill_last,
               params={"lookback": 1, "top_n": 1, "rebalance_every": 1})
    holder["rig"] = rig
    rig.xs(T0, {"US.AAPL": 100.0, "US.MSFT": 100.0})
    rig.xs(T0 + HOUR, {"US.AAPL": 90.0, "US.MSFT": 110.0})       # AAPL lags: buy it
    assert [(o.side.value, o.tags["legacy_symbol"]) for o in rig.submitted] == [("BUY", "US.AAPL")]
    rig.xs(T0 + 2 * HOUR, {"US.AAPL": 99.0, "US.MSFT": 99.0})    # MSFT lags now: sell AAPL, buy MSFT
    assert [(o.side.value, o.tags["legacy_symbol"]) for o in rig.submitted[1:]] == [("SELL", "US.AAPL"),
                                                                                   ("BUY", "US.MSFT")]
    p = rig.adapter.portfolio
    assert list(p.positions) == ["US.MSFT"]
    buy = rig.submitted[2]
    assert buy.quantity * 99.0 > 9_000          # the proceeds of the sale were available
    assert buy.tags["decision_ts"] == str(pd.Timestamp("2026-10-14 12:30", tz="UTC"))


def test_resume_shrinks_to_the_broker_and_restores_the_clock(tmp_path):
    seed = {"saved_at": "2026-10-15 04:01:04.743088",
            "positions": {"US.AAPL": {"qty": 10.0, "entry_price": 100.0}, "US.MSFT": {"qty": 5.0, "entry_price": 50.0}},
            "peak_prices": {"US.AAPL": 110.0, "US.MSFT": 55.0}, "cross_section_count": 70,
            "next_rebalance_at": 76, "cash": 4000.0}
    rig = make(tmp_path, state_seed=seed, broker_positions={"US.AAPL": {"qty": 6.0, "entry_price": 100.0}},
               account={"cash": 900_000.0, "total_assets": 1_000_000.0, "market_value": 100_000.0})
    p = rig.adapter.portfolio
    assert p.positions == {"US.AAPL": {"qty": 6.0, "entry_price": 100.0}}
    assert p.cash == 4000.0 and p.initial_cash == 10_000.0
    assert p._peak_prices == {"US.AAPL": 110.0}
    assert (rig.adapter.strategy._cross_section_count, rig.adapter.strategy._next_rebalance_at) == (70, 76)


def test_reconcile_shrinks_claims_and_bounds_cash(tmp_path):
    rig = make(tmp_path)
    p = rig.adapter.portfolio
    p.positions = {"US.AAPL": {"qty": 10.0, "entry_price": 100.0}, "US.MSFT": {"qty": 3.0, "entry_price": 10.0}}
    assert rig.adapter.reconcile({"cash": 500.0}, {"US.AAPL": {"qty": 20.0, "entry_price": 1.0}})
    assert p.positions == {"US.AAPL": {"qty": 10.0, "entry_price": 100.0}}     # never grown, MSFT dropped
    assert p.cash == 500.0
    assert not rig.adapter.reconcile(None, None)                               # no information: untouched
    assert "US.AAPL" in p.positions


def test_reconcile_leaves_a_symbol_with_an_order_in_flight_alone(tmp_path):
    """The broker may already show a sale the strategy has not booked: shrinking the claim then
    would make the fill unbookable when it arrives."""
    rig = make(tmp_path)
    p = rig.adapter.portfolio
    p.positions = {"US.AAPL": {"qty": 10.0, "entry_price": 100.0}}
    p.execute_trade("US.AAPL", False, 10, 101.0, TS)
    assert rig.adapter.reconcile({"cash": 1e6}, {})                  # the broker already sold it
    assert p.get_position_qty("US.AAPL") == 10.0
    rig.fill(rig.submitted[0], 10.0, 101.0)
    assert p.positions == {} and len(p.trade_history) == 1
    assert trades_logged(rig)[0]["side"] == "SELL"
