"""PreTradeRiskEngine: every check, the kill switch and the RiskEvents (midfreq.trading.risk)."""
import pytest

from midfreq.core.common.clock import TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model import (
    ClientOrderId, InstrumentId, Order, OrderFilled, OrderSide, OrderStatus, OrderType, StrategyId, TradeId,
)
from midfreq.trading.interfaces import TOPIC_RISK, RiskEvent
from midfreq.trading.positions import PositionLedger
from midfreq.trading.risk import PreTradeRiskEngine, RiskLimits, affordable_quantity, is_whole_lots

AAPL = InstrumentId.from_str("AAPL.XNAS")
MSFT = InstrumentId.from_str("MSFT.XNAS")
TENCENT = InstrumentId.from_str("00700.XHKG")
UNKNOWN = InstrumentId.from_str("ZZZZ.XNAS")
S1 = StrategyId("S1")
PRICES = {AAPL: 100.0, MSFT: 200.0, TENCENT: 400.0}
LOTS = {AAPL: 1.0, MSFT: 1.0, TENCENT: 100.0}


class Env:
    def __init__(self, limits=None, capital=100_000.0, is_open=None, **kw):
        self.clock = TestClock(1_000)
        self.bus = MessageBus()
        self.events = []
        self.bus.subscribe(TOPIC_RISK, self.events.append)
        self.ledger = PositionLedger(self.clock, self.bus, capital={"S1": capital})
        self.open = []
        self.risk = PreTradeRiskEngine(self.clock, self.bus, self.ledger, limits or RiskLimits(),
                                       reference_price=PRICES.get, lot_size=LOTS.get,
                                       is_open=is_open, open_orders=lambda sid: list(self.open), **kw)
        self.n = 0

    def order(self, side="B", qty=10, iid=AAPL, price=None, order_type=None, ref=None):
        self.n += 1
        otype = order_type or (OrderType.LIMIT if price is not None else OrderType.MARKET)
        return Order(ClientOrderId(f"S1-20261015-{self.n:04d}"), S1, iid,
                     OrderSide.BUY if side == "B" else OrderSide.SELL, otype, float(qty), price,
                     reference_price=ref)

    def hold(self, qty, iid=AAPL, px=100.0):
        self.n += 1
        self.ledger.on_fill(OrderFilled(ClientOrderId(f"F{self.n}"), S1, iid, 1, trade_id=TradeId(f"T{self.n}"),
                                        side=OrderSide.BUY, last_qty=float(qty), last_px=px))

    def working(self, order):
        order.status = OrderStatus.ACCEPTED
        self.open.append(order)
        return order


def test_a_plain_order_passes_and_nothing_is_published():
    env = Env()
    assert env.risk.check(env.order(price=100.0)) is None
    assert env.events == [] and env.risk.denied == 0


def test_kill_switch_denies_every_order_until_resumed():
    env = Env()
    env.hold(10)
    env.risk.halt("drawdown 12%")
    assert env.risk.halted and env.risk.halt_reason == "drawdown 12%"
    assert env.risk.check(env.order(price=100.0)) == "trading halted: drawdown 12%"
    assert env.risk.check(env.order("S", 5, price=100.0)).startswith("trading halted")   # reducing too
    env.risk.resume()
    assert not env.risk.halted and env.risk.check(env.order(price=100.0)) is None
    kinds = [e.kind for e in env.events]
    assert kinds == ["halted", "denied", "denied", "resumed"]
    denied = env.events[1]
    assert isinstance(denied, RiskEvent) and denied.strategy_id == S1 and denied.client_order_id is not None


@pytest.mark.parametrize("make, expected", [
    (lambda e: e.order(qty=0), "quantity must be positive"),
    (lambda e: e.order(qty=-5), "quantity must be positive"),
    (lambda e: e.order(qty=float("nan")), "quantity must be positive"),
    (lambda e: e.order(order_type=OrderType.LIMIT), "a limit order needs a positive price"),
    (lambda e: e.order(price=-1.0), "a limit order needs a positive price"),
    (lambda e: e.order(iid=UNKNOWN, price=10.0), "unknown instrument ZZZZ.XNAS"),
    (lambda e: e.order(qty=150, iid=TENCENT, price=400.0), "not a whole number of lots of 100"),
])
def test_order_validity(make, expected):
    env = Env()
    reason = env.risk.check(make(env))
    assert reason is not None and expected in reason
    assert env.events[-1].kind == "denied"


def test_whole_lots_without_float_drift():
    assert is_whole_lots(200, 100) and not is_whole_lots(150, 100)
    assert is_whole_lots(0.3, 0.1) and is_whole_lots(0.00003, 0.00001) and not is_whole_lots(0.35, 0.1)
    env = Env()
    assert env.risk.check(env.order(qty=200, iid=TENCENT, price=400.0)) is None


def test_session_must_be_open():
    calls = []

    def is_open(venue, ts):
        calls.append((venue, ts))
        return venue.value != "XHKG"
    env = Env(is_open=is_open)
    assert env.risk.check(env.order(price=100.0)) is None
    assert env.risk.check(env.order(qty=100, iid=TENCENT, price=400.0)) == "XHKG is not open for trading"
    assert calls[0] == (AAPL.venue, 1_000)


def test_price_band_against_the_reference_price():
    env = Env(RiskLimits(price_band_pct=0.05))
    assert env.risk.check(env.order(price=104.9)) is None
    reason = env.risk.check(env.order(price=106.0))
    assert "6.0% from the reference 100" in reason and "5% band" in reason
    assert env.risk.check(env.order("B", 10)) is None            # a market order has no limit to check
    env.risk._reference_price = lambda iid: None
    assert "no reference price" in env.risk.check(env.order(price=100.0))


def test_a_market_order_needs_some_price_to_be_valued():
    env = Env()
    env.risk._reference_price = lambda iid: None
    assert "no price to value" in env.risk.check(env.order("B", 10))
    assert env.risk.check(env.order("B", 10, ref=99.0)) is None     # the decision price will do


def test_max_order_notional():
    env = Env(RiskLimits(max_order_notional=5_000.0))
    assert env.risk.check(env.order(qty=50, price=100.0)) is None
    assert "order notional 5,100.00 exceeds the limit 5,000.00" in env.risk.check(env.order(qty=51, price=100.0))


def test_no_short_selling_counts_open_sells():
    env = Env()
    env.hold(10)
    assert env.risk.check(env.order("S", 10, price=100.0)) is None
    assert "sell 11 exceeds the 10 held" in env.risk.check(env.order("S", 11, price=100.0))
    env.working(env.order("S", 6, price=100.0))
    assert "4 held (position 10, 6 already being sold)" in env.risk.check(env.order("S", 5, price=100.0))
    assert Env(RiskLimits(allow_short=True)).risk.check(Env().order("S", 5, price=100.0)) is None


def test_position_limit_counts_the_position_and_open_buys_and_spares_reducing_orders():
    env = Env(RiskLimits(max_position_notional=3_000.0))
    env.hold(10)                                                     # 1,000
    env.working(env.order("B", 10, price=100.0))                     # +1,000 working
    assert env.risk.check(env.order("B", 10, price=100.0)) is None   # 3,000: at the limit
    reason = env.risk.check(env.order("B", 11, price=100.0))
    assert "position in AAPL.XNAS would be 3,100.00" in reason
    assert env.risk.check(env.order("S", 10, price=100.0)) is None   # reduces exposure


def test_gross_limit_across_instruments_with_a_warning_near_it():
    env = Env(RiskLimits(max_gross_notional=10_000.0, warn_at=0.8))
    env.hold(30, iid=MSFT, px=200.0)                                 # 6,000 at the reference 200
    assert env.risk.check(env.order("B", 25, price=100.0)) is None   # 8,500: warned
    assert [e.kind for e in env.events] == ["limit_warning"]
    assert env.risk.check(env.order("B", 25, price=100.0)) is None   # no second warning while above
    assert "gross notional would be 10,100.00" in env.risk.check(env.order("B", 41, price=100.0))


def test_cash_check_uses_the_worst_case_and_open_buys():
    env = Env(RiskLimits(fee_rate=0.001, market_slippage_pct=0.01), capital=10_000.0)
    # limit: 99 x 100 x 1.001 = 9,909.90 fits; 100 x 100 x 1.001 = 10,010 does not
    assert env.risk.check(env.order("B", 99, price=100.0)) is None
    assert "buy needs up to 10,010.00" in env.risk.check(env.order("B", 100, price=100.0))
    # market: valued 1% through the reference: 98 x 101 x 1.001 = 9,908.10 fits; 99 does not
    assert env.risk.check(env.order("B", 98)) is None
    assert env.risk.check(env.order("B", 99)) is not None
    env.working(env.order("B", 50, price=100.0))                     # reserves 5,005
    reason = env.risk.check(env.order("B", 50, price=100.0))
    assert "5,005.00 reserved by open buys" in reason
    assert env.risk.check(env.order("B", 49, price=100.0)) is None
    no_cash = Env(RiskLimits(check_cash=False), capital=0.0)
    assert no_cash.risk.check(no_cash.order("B", 10, price=100.0)) is None


def test_limits_per_strategy():
    env = Env(RiskLimits(), strategy_limits={"S1": RiskLimits(max_order_notional=100.0)})
    assert "exceeds the limit 100.00" in env.risk.check(env.order(qty=2, price=100.0))


def test_a_failing_reference_source_denies():
    env = Env()

    def boom(iid):
        raise RuntimeError("cache gone")
    env.risk._reference_price = boom
    assert env.risk.check(env.order(price=100.0)) == "risk check failed: cache gone"


def test_affordable_quantity_is_the_original_engines_sizing():
    # LivePortfolio.execute_trade: worst = ref x (1 + slip) x (1 + commission); int(cash // worst); lots
    assert affordable_quantity(10_000.0, 100.0, 0.01, 0.0016) == 98.0
    assert affordable_quantity(100_000.0, 400.0, 0.01, 0.0016, lot=100) == 200.0
    assert affordable_quantity(10.0, 100.0) == 0.0 and affordable_quantity(10.0, 0.0) == 0.0
