"""Quote, trade and book data types, MarketState, and merging them with bars."""
import pytest

from mft.backtest import DataEngine
from mft.cache import Cache
from mft.model import InstrumentId, OrderSide

from mft_market import IID, bar, book, quote, state, trade, ts

APPLE = InstrumentId.from_str("AAPL.XNAS")


def test_tick_types():
    q = quote(99.9, 100.1)
    assert q.mid == pytest.approx(100.0) and q.spread == pytest.approx(0.2)
    b = book([(99.9, 300), (99.8, 500), (99.9, 50)], [(100.1, 200)])
    assert b.best_bid == 99.9 and b.best_ask == 100.1
    assert b.size_at(OrderSide.BUY, 99.9) == 350 and b.size_at(OrderSide.SELL, 100.1) == 200
    assert trade(100, 5, OrderSide.SELL).aggressor_side is OrderSide.SELL


def test_data_engine_merges_bars_and_ticks_in_time_order():
    de = DataEngine()
    de.add([bar(1, 1, 1, 1, i=0), bar(2, 2, 2, 2, i=3)])
    de.add([quote(1, 2, i=1), quote(1, 2, i=3)])
    de.add([trade(1.5, 10, i=2), trade(1.6, 10, i=3, iid=APPLE)])
    kinds = [type(x).__name__ for x in de.iter_bars()]
    assert kinds == ["Bar", "QuoteTick", "TradeTick", "Bar", "QuoteTick", "TradeTick"]
    slices = list(de.iter_slices())
    assert [len(s.data) for s in slices] == [1, 1, 1, 3]
    assert [len(s) for s in slices] == [1, 0, 0, 1]          # len() counts bars only
    assert set(de.instruments()) == {IID, APPLE}


def test_market_state_tracks_every_kind():
    s = state(bar(10, 11, 9, 10.5, i=0))
    assert s.has_bars and not s.has_quotes and s.best_bid is None and s.last_price == 10.5
    s.update(quote(10.4, 10.6, i=1))
    assert s.mid == pytest.approx(10.5) and s.half_spread == pytest.approx(0.1)
    s.update(trade(10.7, 100, i=2))
    assert s.last_price == 10.7 and s.trigger.price == 10.7
    s.update(book([(10.5, 100)], [(10.8, 100)], i=3))
    assert (s.best_bid, s.best_ask) == (10.5, 10.8)           # the book outranks the quote
    s.take("book", ("SELL", 10.8), 60)
    s.take("trade", ("x",), 1)
    assert s.available("book", ("SELL", 10.8), 100) == 40
    s.update(book([(10.5, 100)], [(10.8, 100)], i=4))
    assert s.available("book", ("SELL", 10.8), 100) == 100   # a new snapshot restores liquidity
    assert s.consumed("trade") == {("x",): 1}                 # other kinds keep their record
    with pytest.raises(TypeError):
        s.update(object())
    assert s.ts_last == ts(4)


def test_cache_last_price_uses_the_latest_of_any_kind():
    c = Cache()
    c.add_data(bar(10, 11, 9, 10.5, i=0))
    assert c.last_price(IID) == 10.5
    c.add_data(quote(10.4, 10.8, i=1))
    assert c.last_price(IID) == pytest.approx(10.6)
    c.add_data(trade(10.7, 5, i=2))
    assert c.last_price(IID) == 10.7 and c.trade(IID).size == 5
    c.add_data(book([(10.0, 1)], [(10.2, 1)], i=3))
    assert c.last_price(IID) == pytest.approx(10.1) and c.book(IID).best_ask == 10.2
