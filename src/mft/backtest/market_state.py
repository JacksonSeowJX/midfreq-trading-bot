"""
MarketState: what a simulated venue currently knows about one instrument.

Each SimulatedExchange keeps one per instrument and updates it with every bar, quote, trade and
book snapshot. Fill models and slippage models read it: a quote fill needs the best ask, a book
fill the levels, a square-root impact estimate the recent volatility and volume. `trigger` is the
item that has just arrived (None while an order is being submitted), so a fill model can tell "a new
trade printed" from "an order arrived between prints".
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

from mft.model.data import Bar, OrderBookDepth, QuoteTick, TradeTick
from mft.model.identifiers import InstrumentId


@dataclass
class MarketState:
    instrument_id: InstrumentId
    instrument: object = None
    last_bar: Optional[Bar] = None
    quote: Optional[QuoteTick] = None
    last_trade: Optional[TradeTick] = None
    book: Optional[OrderBookDepth] = None
    trigger: object = None
    ts_last: int = 0
    recent_closes: Deque[float] = field(default_factory=lambda: deque(maxlen=21))
    # Size we have already taken from the current book snapshot, per (side, price), so two orders
    # cannot both consume the same displayed liquidity. Cleared when a new snapshot arrives.
    book_consumed: Dict[tuple, float] = field(default_factory=dict)

    def update(self, item) -> None:
        if isinstance(item, Bar):
            self.last_bar = item
            self.recent_closes.append(item.close)
        elif isinstance(item, QuoteTick):
            self.quote = item
        elif isinstance(item, TradeTick):
            self.last_trade = item
        elif isinstance(item, OrderBookDepth):
            self.book = item
            self.book_consumed = {}
        else:
            raise TypeError(f"not market data: {item!r}")
        self.ts_last = item.ts_event
        self.trigger = item

    @property
    def has_bars(self) -> bool:
        return self.last_bar is not None

    @property
    def has_quotes(self) -> bool:
        return self.quote is not None

    @property
    def has_trades(self) -> bool:
        return self.last_trade is not None

    @property
    def has_book(self) -> bool:
        return self.book is not None

    @property
    def best_bid(self) -> Optional[float]:
        if self.book is not None and self.book.bids:
            return self.book.best_bid
        return self.quote.bid if self.quote is not None else None

    @property
    def best_ask(self) -> Optional[float]:
        if self.book is not None and self.book.asks:
            return self.book.best_ask
        return self.quote.ask if self.quote is not None else None

    @property
    def mid(self) -> Optional[float]:
        b, a = self.best_bid, self.best_ask
        return (a + b) / 2.0 if a is not None and b is not None else None

    @property
    def half_spread(self) -> Optional[float]:
        b, a = self.best_bid, self.best_ask
        return (a - b) / 2.0 if a is not None and b is not None else None

    @property
    def last_price(self) -> Optional[float]:
        """The latest observed price of any kind (for valuing positions)."""
        candidates = []
        if self.last_bar is not None:
            candidates.append((self.last_bar.ts_event, self.last_bar.close))
        if self.last_trade is not None:
            candidates.append((self.last_trade.ts_event, self.last_trade.price))
        if self.mid is not None:
            ts = max(x.ts_event for x in (self.quote, self.book) if x is not None)
            candidates.append((ts, self.mid))
        return max(candidates, key=lambda c: c[0])[1] if candidates else None
