"""
Cache: the engine's in-memory store of instruments, recent bars and orders, after NautilusTrader's
`cache`. Strategies read it (through a read-only view); only engine components write to it.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional

from mft.model.data import Bar, BarType, OrderBookDepth, QuoteTick, TradeTick
from mft.model.identifiers import ClientOrderId, InstrumentId, StrategyId, as_instrument_id
from mft.model.instruments import Instrument
from mft.model.orders import Order


class Cache:
    def __init__(self, bar_capacity: int = 1000):
        self.bar_capacity = bar_capacity
        self._instruments: Dict[InstrumentId, Instrument] = {}
        self._bars: Dict[BarType, Deque[Bar]] = {}
        self._last_bar: Dict[InstrumentId, Bar] = {}
        self._last_px: Dict[InstrumentId, float] = {}
        self._quotes: Dict[InstrumentId, QuoteTick] = {}
        self._trades: Dict[InstrumentId, TradeTick] = {}
        self._books: Dict[InstrumentId, OrderBookDepth] = {}
        self._orders: Dict[ClientOrderId, Order] = {}

    # writes (engine only)
    def add_instrument(self, instrument: Instrument) -> None:
        self._instruments[instrument.id] = instrument

    def add_bar(self, bar: Bar) -> None:
        q = self._bars.get(bar.bar_type)
        if q is None:
            q = self._bars[bar.bar_type] = deque(maxlen=self.bar_capacity)
        q.append(bar)
        self._last_bar[bar.bar_type.instrument_id] = bar
        self._last_px[bar.bar_type.instrument_id] = bar.close

    def add_quote(self, quote: QuoteTick) -> None:
        self._quotes[quote.instrument_id] = quote
        self._last_px[quote.instrument_id] = quote.mid

    def add_trade(self, trade: TradeTick) -> None:
        self._trades[trade.instrument_id] = trade
        self._last_px[trade.instrument_id] = trade.price

    def add_book(self, book: OrderBookDepth) -> None:
        self._books[book.instrument_id] = book
        if book.bids and book.asks:
            self._last_px[book.instrument_id] = (book.best_bid + book.best_ask) / 2.0

    def add_data(self, item) -> None:
        if isinstance(item, Bar):
            self.add_bar(item)
        elif isinstance(item, QuoteTick):
            self.add_quote(item)
        elif isinstance(item, TradeTick):
            self.add_trade(item)
        elif isinstance(item, OrderBookDepth):
            self.add_book(item)
        else:
            raise TypeError(f"not market data: {item!r}")

    def add_order(self, order: Order) -> None:
        self._orders[order.client_order_id] = order

    # reads
    def instrument(self, instrument_id) -> Optional[Instrument]:
        return self._instruments.get(as_instrument_id(instrument_id))

    def instruments(self) -> List[Instrument]:
        return list(self._instruments.values())

    def bars(self, bar_type: BarType) -> List[Bar]:
        """Recent bars of a type, oldest first (at most `bar_capacity`)."""
        return list(self._bars.get(bar_type, ()))

    def bar(self, bar_type: BarType, index: int = 0) -> Optional[Bar]:
        """The latest bar (index 0), or the one `index` bars back."""
        q = self._bars.get(bar_type)
        if not q or index >= len(q):
            return None
        return q[-1 - index]

    def last_bar(self, instrument_id) -> Optional[Bar]:
        return self._last_bar.get(as_instrument_id(instrument_id))

    def last_price(self, instrument_id) -> Optional[float]:
        """The latest price of any kind: bar close, trade price, or quote/book mid."""
        return self._last_px.get(as_instrument_id(instrument_id))

    def quote(self, instrument_id) -> Optional[QuoteTick]:
        return self._quotes.get(as_instrument_id(instrument_id))

    def trade(self, instrument_id) -> Optional[TradeTick]:
        return self._trades.get(as_instrument_id(instrument_id))

    def book(self, instrument_id) -> Optional[OrderBookDepth]:
        return self._books.get(as_instrument_id(instrument_id))

    def order(self, client_order_id: ClientOrderId) -> Optional[Order]:
        return self._orders.get(client_order_id)

    def orders(self, strategy_id: Optional[StrategyId] = None) -> List[Order]:
        return [o for o in self._orders.values() if strategy_id is None or o.strategy_id == strategy_id]

    def orders_open(self, strategy_id: Optional[StrategyId] = None) -> List[Order]:
        return [o for o in self.orders(strategy_id) if o.is_open]


class CacheView:
    """What a strategy sees of the cache: the read methods only."""

    def __init__(self, cache: Cache):
        self._cache = cache

    def __getattr__(self, name):
        if name.startswith("add_") or name.startswith("_"):
            raise AttributeError(f"the strategy's cache is read-only ({name})")
        return getattr(self._cache, name)
