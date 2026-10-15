"""Fill and slippage models inside the engine: tick data, partial fills, order states, accounts."""
import pytest

from mft.backtest import (
    AutoFillModel, BacktestEngine, BarFillModel, FillModel, FixedBpsSlippage, NoSlippage,
    OrderBookFillModel, QuoteFillModel, SpreadSlippage, TradeFillModel,
)
from mft.model import Equity, OrderSide, OrderStatus, TimeInForce
from mft.trading import Strategy

from mft_market import IID, bar, book, quote, trade


class Script(Strategy):
    """Submits scripted orders when the n-th item of any subscribed kind arrives."""

    def __init__(self, actions):
        super().__init__()
        self.actions, self.n, self.events, self.seen = actions, 0, [], []

    def on_start(self):
        self.subscribe_quote_ticks(IID)
        self.subscribe_trade_ticks(IID)
        self.subscribe_order_book(IID)
        self.subscribe_bars("00700.XHKG-1-MINUTE-LAST-EXTERNAL")

    def _step(self, item):
        self.seen.append(type(item).__name__)
        act = self.actions.get(self.n)
        self.n += 1
        if act:
            act(self)

    on_quote_tick = on_trade_tick = on_order_book = on_bar = _step

    def on_event(self, e):
        self.events.append(type(e).__name__)


def run(data, actions, cash=1_000_000.0, lot=1.0, **venue):
    engine = BacktestEngine()
    engine.add_venue("XHKG", {"HKD": cash}, **venue)
    engine.add_instrument(Equity(id=IID, raw_symbol="00700", currency="HKD", price_increment=0.1, lot_size=lot))
    engine.add_data(data)
    s = Script(actions)
    engine.add_strategy(s)
    return engine.run(), s


def buy(qty, **kw):
    return lambda s: s.submit_order(s.order_factory.market(IID, OrderSide.BUY, qty, **kw))


def buy_limit(qty, px, tif=TimeInForce.GTC):
    return lambda s: s.submit_order(s.order_factory.limit(IID, OrderSide.BUY, qty, px, time_in_force=tif))


def test_default_is_still_bar_close_without_slippage():
    engine = BacktestEngine()
    ex = engine.add_venue("XHKG", {"HKD": 1})
    assert isinstance(ex.fill_model, BarFillModel) and ex.fill_model.timing == "close"
    assert ex.slippage_model == FixedBpsSlippage(0.0)
    ex2 = BacktestEngine().add_venue("XHKG", fill_model=FillModel(slippage_bps=5))
    assert ex2.slippage_model == FixedBpsSlippage(5)
    ex3 = BacktestEngine().add_venue("XHKG", fill_model=QuoteFillModel())
    assert ex3.slippage_model == NoSlippage()


def test_quote_partial_fill_rests_then_completes():
    data = [quote(99.9, 100.1, ask_size=300, i=0), quote(100.0, 100.2, ask_size=500, i=1)]
    result, s = run(data, {0: buy(500)}, fill_model=QuoteFillModel())
    order = result.orders[0]
    assert [(t["qty"], t["price"]) for t in result.trade_log] == [(300, 100.1), (200, 100.2)]
    assert order.status is OrderStatus.FILLED and order.filled_qty == 500
    assert order.avg_px == pytest.approx((300 * 100.1 + 200 * 100.2) / 500)
    assert s.events == ["OrderSubmitted", "OrderFilled", "OrderAccepted", "OrderFilled"]
    commission = sum(t["commission"] for t in result.trade_log)
    assert result.final_balances["HKD"] == pytest.approx(1_000_000 - 300 * 100.1 - 200 * 100.2 - commission)


def test_partially_filled_status_while_working():
    result, _ = run([quote(99.9, 100.1, ask_size=300, i=0)], {0: buy(500)}, fill_model=QuoteFillModel())
    order = result.orders[0]
    assert order.status is OrderStatus.PARTIALLY_FILLED and order.is_open and order.leaves_qty == 200


def test_ioc_remainder_is_cancelled():
    result, s = run([quote(99.9, 100.1, ask_size=300, i=0)], {0: buy(500, time_in_force=TimeInForce.IOC)},
                    fill_model=QuoteFillModel())
    order = result.orders[0]
    assert order.status is OrderStatus.CANCELED and order.filled_qty == 300 and "IOC" in order.reason


def test_book_walk_gives_a_volume_weighted_average():
    data = [book([(99.9, 100)], [(100.1, 100), (100.3, 100)], i=0)]
    result, _ = run(data, {0: buy(150)}, fill_model=OrderBookFillModel())
    order = result.orders[0]
    assert order.avg_px == pytest.approx((100 * 100.1 + 50 * 100.3) / 150)
    assert [t["price"] for t in result.trade_log] == [100.1, 100.3]


def test_running_out_of_cash_part_way_cancels_the_rest():
    data = [book([], [(100.0, 100), (200.0, 100)], i=0)]
    result, _ = run(data, {0: buy(200)}, cash=15_000.0, fill_model=OrderBookFillModel())
    order = result.orders[0]
    assert order.filled_qty == 100 and order.status is OrderStatus.CANCELED
    assert "insufficient HKD cash" in order.reason


def test_book_limit_queue_fills_from_trades_in_the_engine():
    data = [book([(99.9, 300)], [(100.1, 100)], i=0), trade(99.9, 250, OrderSide.SELL, i=1),
            trade(99.9, 150, OrderSide.SELL, i=2)]
    result, s = run(data, {0: buy_limit(100, 99.9)}, fill_model=OrderBookFillModel())
    assert [(t["qty"], t["price"]) for t in result.trade_log] == [(100, 99.9)]
    assert result.trade_log[0]["timestamp"].value == data[2].ts_event          # after the queue cleared
    assert s.seen == ["OrderBookDepth", "TradeTick", "TradeTick"]


def test_trade_fills_get_slippage_quote_fills_do_not():
    data = [trade(100.0, 1000, i=0), trade(100.0, 1000, i=1)]
    result, _ = run(data, {0: buy(10)}, fill_model=TradeFillModel(), slippage_model=FixedBpsSlippage(10))
    assert result.trade_log[0]["price"] == pytest.approx(100.0 * 1.001)
    result, _ = run([quote(99.9, 100.1, i=0)], {0: buy(10)}, fill_model=QuoteFillModel(),
                    slippage_model=FixedBpsSlippage(10))
    assert result.trade_log[0]["price"] == 100.1


def test_spread_slippage_on_bar_fills_uses_quotes_when_present():
    data = [quote(99.8, 100.2, i=0), bar(100, 100, 100, 100, i=0)]
    result, _ = run(data, {1: buy(10)}, fill_model=BarFillModel(), slippage_model=SpreadSlippage())
    assert result.trade_log[0]["price"] == pytest.approx(100.2)            # close + half the quoted spread


def test_auto_model_uses_the_finest_data_per_instrument():
    data = [bar(100, 100, 100, 100, i=0), quote(100.4, 100.6, ask_size=5, i=1), quote(100.4, 100.6, i=2)]
    result, _ = run(data, {0: buy(10), 1: buy(10)}, fill_model=AutoFillModel())
    assert [(t["qty"], t["price"]) for t in result.trade_log] == [(10, 100.0), (5, 100.6), (5, 100.6)]
    assert result.data_processed == 3 and result.bars_processed == 1


def test_positions_are_valued_at_the_latest_price_of_any_kind():
    data = [quote(99.9, 100.1, i=0), trade(105.0, 10, i=1)]
    result, _ = run(data, {0: buy(100)}, fill_model=QuoteFillModel())
    assert result.final_equity["HKD"] == pytest.approx(result.final_balances["HKD"] + 100 * 105.0)


def test_resting_limits_fill_in_the_order_the_bar_reached_them():
    """Two resting buys share too little cash: the one the intrabar path reaches first is filled."""
    data = [bar(10, 10, 10, 10, i=0), bar(10, 10.2, 8, 9, i=1)]      # high nearer the open: O-H-L-C
    actions = {0: lambda s: (s.submit_order(s.order_factory.limit(IID, OrderSide.BUY, 100, 8.5)),
                             s.submit_order(s.order_factory.limit(IID, OrderSide.BUY, 100, 9.5)))}
    result, _ = run(data, actions, cash=1_000.0)
    deep, shallow = result.orders
    assert shallow.status is OrderStatus.FILLED and deep.status is OrderStatus.REJECTED
