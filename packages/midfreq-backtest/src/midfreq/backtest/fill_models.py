"""
Fill models: how a simulated venue fills an order, given the market data it has.

The right model depends on the data. With only bars, a fill price has to be assumed (the close,
or the next bar's open) and a cost added on top; with quotes the order pays the actual spread and
cannot take more than the size displayed; with trades a resting limit fills only when the market
actually printed at or through it; with an L2 book a large order walks the levels and may run out
of depth. One interface covers all of them:

    fill(order, market_state) -> list[Fill]

It returns the fills the order gets right now (none, part of its remaining quantity, or all of
it). The exchange calls it when the order arrives (`market_state.trigger is None`) and again for
every new data item of that instrument while the order rests. A list allows partial fills and fills
at several prices (walking a book).

Models:
- BarFillModel: market orders at the decision bar's close, or at the next bar's open; limit orders
  along an assumed intrabar path O-H-L-C or O-L-H-C; optional fill probability when a limit is only
  touched; optional cap at a share of the bar's volume.
- QuoteFillModel: buy at the ask, sell at the bid, at most the displayed size; the rest waits for
  later quotes. Limits fill when the opposite quote crosses them.
- TradeFillModel: market orders at the next printed trade; limits when a trade prints at or through
  them, up to a participation share of the printed size.
- OrderBookFillModel: market orders walk the L2 levels; limits wait in the queue behind the size
  displayed at their price and fill as trades consume it.
- AutoFillModel: per instrument, the finest data present (book > quotes > trades > bars).
- LegacyFillModel: fills at the price a candle strategy passed, as midfreq.backtest.backtester did.

Slippage (midfreq.backtest.slippage) is added by the exchange only to fills flagged `apply_slippage`:
bar and trade fills, whose price does not include the cost of crossing the spread. Quote and book
fills already pay the spread and depth.

For compatibility, `FillModel(timing=..., slippage_bps=...)` called directly still builds the
bar-based model the engine has always used.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from midfreq.core.model.data import Bar, OrderBookDepth, QuoteTick, TradeTick
from midfreq.core.model.enums import LiquiditySide, OrderSide, OrderType, TimeInForce

CLOSE = "close"
NEXT_OPEN = "next_open"
REFERENCE = "reference"

TAKER = LiquiditySide.TAKER
MAKER = LiquiditySide.MAKER


@dataclass(frozen=True)
class Fill:
    quantity: float
    price: float
    liquidity_side: LiquiditySide = TAKER
    apply_slippage: bool = False      # True where the price does not already include trading costs


def _crosses(side: OrderSide, price: float, limit: float) -> bool:
    """Whether a counterparty price is good enough for a limit: at or below for a buy."""
    return price <= limit if side is OrderSide.BUY else price >= limit


def _through(side: OrderSide, price: float, limit: float) -> bool:
    return price < limit if side is OrderSide.BUY else price > limit


class FillModel:
    """Base class. Called directly with keyword arguments it returns a BarFillModel, which is what
    `FillModel(slippage_bps=5)` and `FillModel(timing='next_open')` always meant."""

    round_lots: bool = True

    def __new__(cls, *args, **kwargs):
        if cls is FillModel:
            return BarFillModel(*args, **kwargs)
        return super().__new__(cls)

    def fill(self, order, state) -> List[Fill]:
        raise NotImplementedError

    def forget(self, order) -> None:
        """The order is no longer resting; drop any per-order state (queue position)."""

    def tradable(self, qty: float, state) -> float:
        """`qty` rounded down to whole lots (when lots are enforced)."""
        inst = getattr(state, "instrument", None)
        if qty <= 0:
            return 0.0
        return inst.make_qty(qty) if (self.round_lots and inst is not None) else qty

    def describe(self) -> str:
        return type(self).__name__


# ── bars ─────────────────────────────────────────────────────────────

class BarFillModel(FillModel):
    def __init__(self, timing: str = CLOSE, slippage_bps: float = 0.0, round_lots: bool = True,
                 prob_fill_on_touch: float = 1.0, max_volume_share: Optional[float] = None,
                 seed: int = 0):
        """
        Args:
            timing: 'close' fills a market order at the close of the bar it was decided on (as
                midfreq.backtest.backtester does); 'next_open' at the instrument's next bar's open; 'reference' at
                the order's reference price (the legacy adapter).
            slippage_bps: kept for compatibility; the engine turns it into FixedBpsSlippage when no
                slippage model is given.
            prob_fill_on_touch: chance that a limit is filled when the bar only touches it (high or low
                exactly at the limit) rather than trading through it. Queue position is unknown from
                bars, so 1.0 (the default) is optimistic. Draws come from a generator seeded with `seed`.
            max_volume_share: fill at most this share of a bar's volume per bar; the rest stays open.
        """
        if timing not in (CLOSE, NEXT_OPEN, REFERENCE):
            raise ValueError(f"timing must be 'close', 'next_open' or 'reference', got {timing!r}")
        if not 0.0 <= prob_fill_on_touch <= 1.0:
            raise ValueError("prob_fill_on_touch must be between 0 and 1")
        self.timing = timing
        self.slippage_bps = slippage_bps
        self.round_lots = round_lots
        self.prob_fill_on_touch = prob_fill_on_touch
        self.max_volume_share = max_volume_share
        self.seed = seed
        self._rng = random.Random(seed)

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(timing={self.timing!r}, slippage_bps={self.slippage_bps}, "
                f"round_lots={self.round_lots}, prob_fill_on_touch={self.prob_fill_on_touch}, "
                f"max_volume_share={self.max_volume_share})")

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and repr(self) == repr(other)

    __hash__ = object.__hash__

    @property
    def slippage_mult(self) -> float:
        return self.slippage_bps / 10000.0

    def price(self, base: float, side: OrderSide) -> float:
        """`base` moved against the trader by `slippage_bps` (midfreq.backtest.backtester's expression)."""
        mult = self.slippage_mult
        if side is OrderSide.BUY:
            return base * (1.0 + mult)
        return base * (1.0 - mult)

    @staticmethod
    def intrabar_path(bar: Bar) -> List[float]:
        """The assumed order prices were visited in: open, then whichever extreme is nearer the open,
        then the other extreme, then close."""
        if abs(bar.high - bar.open) <= abs(bar.open - bar.low):
            return [bar.open, bar.high, bar.low, bar.close]
        return [bar.open, bar.low, bar.high, bar.close]

    def sequence(self, order, bar: Bar) -> float:
        """Where along the bar's path the order becomes fillable (0 = at the open, inf = never), so
        the exchange can fill resting orders in the order the market reached them."""
        if order.order_type is OrderType.MARKET:
            return 0.0
        path = self.intrabar_path(bar)
        if _crosses(order.side, path[0], order.price):
            return 0.0
        for k in range(1, len(path)):
            a, b = path[k - 1], path[k]
            lo, hi = min(a, b), max(a, b)
            if lo <= order.price <= hi:
                return (k - 1) + (abs(order.price - a) / (hi - lo) if hi > lo else 0.0)
        return math.inf

    def _cap(self, qty: float, bar: Optional[Bar], state) -> float:
        if self.max_volume_share is None or bar is None:
            return self.tradable(qty, state)
        allowed = state.available("bar", ("volume",), self.max_volume_share * bar.volume)
        return self.tradable(min(qty, allowed), state)

    def _take(self, fills: List[Fill], state) -> List[Fill]:
        if self.max_volume_share is not None:
            for f in fills:
                state.take("bar", ("volume",), f.quantity)
        return fills

    def fill(self, order, state) -> List[Fill]:
        trigger = state.trigger
        leaves = order.leaves_qty
        bar = state.last_bar
        if order.order_type is OrderType.MARKET:
            if self.timing == REFERENCE:
                base = order.reference_price if order.reference_price is not None else (bar.close if bar else None)
                return [Fill(leaves, base, TAKER, True)] if base is not None else []
            if trigger is None:
                if self.timing == CLOSE and bar is not None:
                    qty = self._cap(leaves, bar, state)
                    return self._take([Fill(qty, bar.close, TAKER, True)], state) if qty > 0 else []
                return []
            if isinstance(trigger, Bar):
                base = trigger.open if self.timing == NEXT_OPEN else trigger.close
                qty = self._cap(leaves, trigger, state)
                return self._take([Fill(qty, base, TAKER, True)], state) if qty > 0 else []
            return []

        # LIMIT
        if trigger is None:
            # On arrival only an IOC limit can fill, at the last close if that is good enough.
            if order.time_in_force is TimeInForce.IOC and bar is not None and _crosses(order.side, bar.close, order.price):
                qty = self._cap(leaves, bar, state)
                return self._take([Fill(qty, bar.close, TAKER, False)], state) if qty > 0 else []
            return []
        if not isinstance(trigger, Bar):
            return []
        extreme = trigger.low if order.side is OrderSide.BUY else trigger.high
        if not _crosses(order.side, extreme, order.price):
            return []
        gapped = _through(order.side, trigger.open, order.price)
        if extreme == order.price and not gapped and self._rng.random() >= self.prob_fill_on_touch:
            return []          # only touched, and this time the queue ahead was not cleared
        qty = self._cap(leaves, trigger, state)
        if qty <= 0:
            return []
        if gapped:
            return self._take([Fill(qty, trigger.open, TAKER, False)], state)
        return self._take([Fill(qty, order.price, MAKER, False)], state)


class LegacyFillModel(BarFillModel):
    """Fills at the price a candle strategy passed to execute_trade, with midfreq.backtest.backtester's slippage
    arithmetic and no lot rounding (the original backtester traded odd lots)."""

    def __init__(self, timing: str = REFERENCE, slippage_bps: float = 0.0, round_lots: bool = False, **kwargs):
        super().__init__(timing=timing, slippage_bps=slippage_bps, round_lots=round_lots, **kwargs)


# ── quotes ───────────────────────────────────────────────────────────

class QuoteFillModel(FillModel):
    """Top-of-book fills: buys at the ask and sells at the bid, never more than the size displayed
    there; what is left waits for later quotes."""

    def __init__(self, round_lots: bool = True):
        self.round_lots = round_lots

    def fill(self, order, state) -> List[Fill]:
        q = state.quote
        if q is None or not (state.trigger is None or isinstance(state.trigger, QuoteTick)):
            return []
        buy = order.side is OrderSide.BUY
        price, size = (q.ask, q.ask_size) if buy else (q.bid, q.bid_size)
        key = ("ask" if buy else "bid", price)
        if order.order_type is OrderType.LIMIT and not _crosses(order.side, price, order.price):
            return []
        qty = self.tradable(min(order.leaves_qty, state.available("quote", key, size)), state)
        if qty <= 0:
            return []
        state.take("quote", key, qty)
        if order.order_type is OrderType.MARKET or state.trigger is None:
            return [Fill(qty, price, TAKER, False)]     # crossing the spread now
        # A resting limit the quote moved through was hit at its own price, as a maker.
        return [Fill(qty, order.price, MAKER, False)]


# ── trades ───────────────────────────────────────────────────────────

class TradeFillModel(FillModel):
    """Fills only against printed trades. A market order takes the next print; a limit fills when a
    trade prints through it, or at its price with the other side aggressing, for at most
    `participation` of the printed size (the rest of that print belonged to orders ahead of ours)."""

    def __init__(self, participation: float = 1.0, round_lots: bool = True):
        if not 0.0 < participation <= 1.0:
            raise ValueError("participation must be in (0, 1]")
        self.participation = participation
        self.round_lots = round_lots

    def fill(self, order, state) -> List[Fill]:
        t = state.trigger
        if not isinstance(t, TradeTick):
            return []
        key = ("print",)
        allowed = state.available("trade", key, self.participation * t.size)
        if order.order_type is OrderType.MARKET:
            qty = self.tradable(min(order.leaves_qty, allowed), state)
            if qty <= 0:
                return []
            state.take("trade", key, qty)
            return [Fill(qty, t.price, TAKER, True)]
        through = _through(order.side, t.price, order.price)
        same_side_aggressor = t.aggressor_side is order.side
        at = t.price == order.price and not same_side_aggressor
        if not (through or at):
            return []
        qty = self.tradable(min(order.leaves_qty, allowed), state)
        if qty <= 0:
            return []
        state.take("trade", key, qty)
        return [Fill(qty, order.price, MAKER, False)]


# ── order book ───────────────────────────────────────────────────────

class OrderBookFillModel(FillModel):
    """L2 fills. A market order (or the marketable part of a limit) walks the opposite side level by
    level, at each level's price, until it is filled or the displayed depth runs out; the rest waits
    for the next snapshot. A limit that rests joins the queue behind the size displayed at its price
    when it arrives; trades at that price consume the queue first and then fill the order, a trade
    through the price fills it, and the queue ahead only shrinks (to the displayed size) as the book
    shows orders ahead of it leaving."""

    def __init__(self, round_lots: bool = True):
        self.round_lots = round_lots
        self._queue: Dict[object, float] = {}

    def queue_ahead(self, order) -> Optional[float]:
        return self._queue.get(order)

    def _walk(self, order, book: OrderBookDepth, state, limit: Optional[float], liquidity) -> List[Fill]:
        buy = order.side is OrderSide.BUY
        levels = book.asks if buy else book.bids
        side_key = "ask" if buy else "bid"
        avail = []
        for lvl in levels:
            if limit is not None and not _crosses(order.side, lvl.price, limit):
                break
            a = state.available("book", (side_key, lvl.price), lvl.size)
            if a > 0:
                avail.append((lvl.price, a))
        total = self.tradable(min(order.leaves_qty, sum(a for _, a in avail)), state)
        fills, left = [], total
        for price, a in avail:
            if left <= 0:
                break
            q = min(a, left)
            fills.append(Fill(q, price, liquidity, False))
            state.take("book", (side_key, price), q)
            left -= q
        return fills

    def fill(self, order, state) -> List[Fill]:
        trigger, book = state.trigger, state.book
        if order.order_type is OrderType.MARKET:
            if book is None or not (trigger is None or isinstance(trigger, OrderBookDepth)):
                return []
            return self._walk(order, book, state, None, TAKER)

        fills: List[Fill] = []
        if trigger is None:
            if book is not None:
                fills = self._walk(order, book, state, order.price, TAKER)
            if order.leaves_qty - sum(f.quantity for f in fills) > 0 and order not in self._queue:
                own = book.size_at(order.side, order.price) if book is not None else 0.0
                self._queue[order] = own
            return fills

        queue = self._queue.setdefault(order, 0.0)
        if isinstance(trigger, OrderBookDepth):
            self._queue[order] = min(queue, trigger.size_at(order.side, order.price))
            # The opposite side now trades at or through the limit: those sellers hit our bid.
            crossing = self._walk(order, trigger, state, order.price, MAKER)
            return [Fill(f.quantity, order.price, MAKER, False) for f in crossing]
        if isinstance(trigger, TradeTick):
            available = state.available("trade", ("print",), trigger.size)
            if _through(order.side, trigger.price, order.price):
                fillable = available
            elif trigger.price == order.price and trigger.aggressor_side is not order.side:
                eaten = min(queue, available)
                self._queue[order] = queue - eaten
                state.take("trade", ("print",), eaten)
                fillable = available - eaten
            else:
                return []
            qty = self.tradable(min(order.leaves_qty, fillable), state)
            if qty <= 0:
                return []
            state.take("trade", ("print",), qty)
            return [Fill(qty, order.price, MAKER, False)]
        return []

    def forget(self, order) -> None:
        self._queue.pop(order, None)


# ── choose by data ───────────────────────────────────────────────────

class AutoFillModel(FillModel):
    """Per instrument, the finest data available decides: an L2 book, else quotes, else trades, else
    bars. A venue with books for some instruments and only bars for others is simulated as well as
    its data allows for each."""

    def __init__(self, bar: Optional[BarFillModel] = None, quote: Optional[QuoteFillModel] = None,
                 trade: Optional[TradeFillModel] = None, book: Optional[OrderBookFillModel] = None,
                 round_lots: bool = True):
        self.round_lots = round_lots
        self.bar = bar or BarFillModel(round_lots=round_lots)
        self.quote = quote or QuoteFillModel(round_lots=round_lots)
        self.trade = trade or TradeFillModel(round_lots=round_lots)
        self.book = book or OrderBookFillModel(round_lots=round_lots)

    @property
    def slippage_bps(self) -> float:
        return self.bar.slippage_bps

    def choose(self, state) -> FillModel:
        if state.has_book:
            return self.book
        if state.has_quotes:
            return self.quote
        if state.has_trades:
            return self.trade
        return self.bar

    def fill(self, order, state) -> List[Fill]:
        return self.choose(state).fill(order, state)

    def sequence(self, order, bar: Bar) -> float:
        return self.bar.sequence(order, bar)

    def forget(self, order) -> None:
        self.book.forget(order)
