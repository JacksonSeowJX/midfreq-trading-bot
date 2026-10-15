"""Hand-built market data for the fill and slippage tests: bars, quotes, trades and L2 books at
round timestamps, so each test states its market in a line or two."""
from __future__ import annotations

import pandas as pd

from mft.backtest.market_state import MarketState
from mft.model import Bar, BarType, Equity, InstrumentId, OrderSide
from mft.model.data import BookLevel, OrderBookDepth, QuoteTick, TradeTick
from mft.model.orders import OrderFactory
from mft.model.identifiers import StrategyId

IID = InstrumentId.from_str("00700.XHKG")
T0 = pd.Timestamp("2024-01-02 02:30", tz="UTC").value
MIN = 60_000_000_000


def ts(i: int) -> int:
    return T0 + i * MIN


def bar(o, h, l, c, v=10_000.0, i=0, iid=IID):
    t = ts(i)
    return Bar(BarType.of(iid, "1-MINUTE"), float(o), float(h), float(l), float(c), float(v), t, t)


def quote(bid, ask, bid_size=1000.0, ask_size=1000.0, i=0, iid=IID):
    return QuoteTick(iid, float(bid), float(ask), float(bid_size), float(ask_size), ts(i), ts(i))


def trade(price, size, aggressor=None, i=0, iid=IID):
    return TradeTick(iid, float(price), float(size), aggressor, ts(i), ts(i))


def book(bids, asks, i=0, iid=IID):
    """bids/asks as [(price, size), ...], best first."""
    return OrderBookDepth(iid, tuple(BookLevel(float(p), float(s)) for p, s in bids),
                          tuple(BookLevel(float(p), float(s)) for p, s in asks), ts(i), ts(i))


def instrument(lot=1.0, tick=0.1):
    return Equity(id=IID, raw_symbol="00700", currency="HKD", price_increment=tick, lot_size=lot)


def state(*items, lot=1.0, tick=0.1) -> MarketState:
    s = MarketState(IID, instrument(lot, tick))
    for it in items:
        s.update(it)
    return s


_factory = OrderFactory(StrategyId("T"))


def market(side, qty):
    return _factory.market(IID, OrderSide(side), qty)


def limit(side, qty, price):
    return _factory.limit(IID, OrderSide(side), qty, price)


def submitted(s: MarketState) -> MarketState:
    """The state as an order arrives between data points."""
    s.trigger = None
    return s
