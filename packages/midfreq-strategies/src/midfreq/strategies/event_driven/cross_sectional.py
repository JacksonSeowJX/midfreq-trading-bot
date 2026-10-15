"""
Cross-sectional reversal, written natively as two parts that talk through a Signal.

midfreq.strategies.candle_strategies.CrossSectionalReversal did everything in one `on_data`: assemble the cross-section,
rank it, decide the basket, size and fill the trades. Here the work is split the way a research
desk splits it:

1. CrossSectionalRanker (the model) receives each complete TimeSlice, keeps every instrument's
   recent closes, and when a rebalance is due publishes one Signal whose value is the whole
   universe ranked by its return over `lookback` bars, worst first. It never trades.
2. EqualWeightBasket (the portfolio step) receives that ranking and turns it into orders: sell
   holdings that left the basket, then buy the new members, sized to an equal share of equity net
   of costs. `select='bottom'` holds the laggards (reversal), `select='top'` the leaders
   (momentum).

Either half can be replaced on its own: a different ranking model with the same basket, or the
same ranking feeding a long/short or risk-weighted construction.

Against the candle version: rankings, baskets, sizes and prices are the same (the tests check this
on data where every instrument reports every bar). The differences are deliberate: the venue
rounds orders to whole board lots (the candle backtester traded odd lots in Hong Kong); the basket
values a holding that is missing from the current slice at its last close rather than at zero; and
the ranking runs at the end of a slice, so the final slice of the data can rebalance, which the
candle version, closing a cross-section only when the next one began, never reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from midfreq.core.model.data import BarSpecification
from midfreq.core.model.enums import OrderSide
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id
from midfreq.core.model.signals import Signal
from midfreq.strategies.trading.strategy import Strategy, StrategyConfig

Ranking = Tuple[Tuple[InstrumentId, float], ...]


@dataclass
class CrossSectionalRankerConfig(StrategyConfig):
    instrument_ids: Sequence[str] = ()    # the universe; empty = whatever arrives in the slices
    bar_spec: str = "1-HOUR"
    lookback: int = 10
    rebalance_every: int = 1              # cross-sections between rankings
    min_ranked: int = 1                   # publish only if at least this many can be ranked
    signal_name: str = "xs_return_rank"


class CrossSectionalRanker(Strategy):
    """Publishes the universe ranked by `lookback`-bar return (ascending) every `rebalance_every`
    complete cross-sections."""

    def __init__(self, config: CrossSectionalRankerConfig):
        super().__init__(config)
        self.lookback = config.lookback
        self.rebalance_every = max(1, config.rebalance_every)
        self.universe = {as_instrument_id(i) for i in config.instrument_ids}
        self.spec = BarSpecification.from_str(config.bar_spec)
        self.history: Dict[InstrumentId, List[float]] = {}
        self._count = 0
        self._next_at = self.rebalance_every

    def on_start(self) -> None:
        self.subscribe_slices()

    def on_slice(self, time_slice) -> None:
        bars = [b for b in time_slice.bars
                if b.bar_type.spec == self.spec and (not self.universe or b.bar_type.instrument_id in self.universe)]
        if not bars:
            return
        for bar in bars:
            prices = self.history.setdefault(bar.bar_type.instrument_id, [])
            prices.append(bar.close)
            if len(prices) > self.lookback + 2:
                prices.pop(0)
        self._count += 1
        if self._count < self._next_at:
            return
        while self._next_at <= self._count:
            self._next_at += self.rebalance_every
        ranking = self.rank(bars)
        if len(ranking) >= self.config.min_ranked:
            self.publish_signal(self.config.signal_name, ranking, ts_event=time_slice.ts_event)

    def rank(self, bars) -> Ranking:
        scores = {}
        for bar in bars:
            prices = self.history[bar.bar_type.instrument_id]
            if len(prices) <= self.lookback:
                continue
            past = prices[-(self.lookback + 1)]
            if past > 0:
                scores[bar.bar_type.instrument_id] = (bar.close / past) - 1.0
        return tuple(sorted(scores.items(), key=lambda kv: kv[1]))   # stable: ties keep slice order


@dataclass
class EqualWeightBasketConfig(StrategyConfig):
    signal_name: str = "xs_return_rank"
    top_n: int = 2
    select: str = "bottom"               # 'bottom' = reversal (laggards), 'top' = momentum (leaders)
    currency: str = "USD"                # the account the basket trades from
    # Sizing allowances; set them to the venue's fill model slippage and fee rate so a buy sized
    # to its full share of equity is not rejected once slippage and commission are added.
    slippage_bps: float = 0.0
    commission_rate: float = 0.0


class EqualWeightBasket(Strategy):
    """Holds `top_n` instruments from a ranking, equal-weighted by value."""

    def __init__(self, config: EqualWeightBasketConfig):
        super().__init__(config)
        if config.select not in ("bottom", "top"):
            raise ValueError("select must be 'bottom' or 'top'")
        self.top_n = max(1, config.top_n)
        self.cost_mult = (1.0 + config.slippage_bps / 10000.0) * (1.0 + config.commission_rate)
        self.rebalances = 0

    def on_start(self) -> None:
        self.subscribe_signals(self.config.signal_name)

    def select(self, ranking: Ranking) -> List[InstrumentId]:
        if self.config.select == "bottom":
            return [iid for iid, _ in ranking[: self.top_n]]
        return [iid for iid, _ in reversed(ranking[-self.top_n:])]

    def on_signal(self, signal: Signal) -> None:
        ranking: Ranking = signal.value
        if len(ranking) < self.top_n:
            return
        ranked = {iid for iid, _ in ranking}
        basket = self.select(ranking)
        members = set(basket)
        self.rebalances += 1

        for pos in self.portfolio.positions_open():
            if pos.instrument_id in ranked and pos.instrument_id not in members:
                self.submit_order(self.order_factory.market(pos.instrument_id, OrderSide.SELL, pos.quantity,
                                                            tags={"exit_reason": "rebalance"}))

        budget = self._equity() / self.top_n
        for iid in basket:
            if len(self.portfolio.positions_open()) >= self.top_n:
                break
            if self.portfolio.net_qty(iid) == 0:
                price = self.cache.last_price(iid)
                qty = int(budget / (price * self.cost_mult)) if price and price > 0 else 0
                if qty > 0:
                    self.submit_order(self.order_factory.market(iid, OrderSide.BUY, qty))

    def _equity(self) -> float:
        equity = self.portfolio.balance(self.config.currency)
        for pos in self.portfolio.positions_open():
            if pos.currency == self.config.currency:
                equity += pos.quantity * self.cache.last_price(pos.instrument_id)
        return equity
