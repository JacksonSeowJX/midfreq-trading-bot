"""
Moving-average crossover, written natively against the Strategy API.

The rule is core.strategy.MovingAverageCrossover's: buy a fixed size when the fast simple moving
average crosses above the slow one while flat, sell the whole position when it crosses back below.
What changes is the shape. The core version computed the rule and filled the trade itself; here the
strategy only submits orders, and the venue decides the fill (price, lot size, fees, cash).

With the default fill model (bar close plus slippage) and the venue's fee rate equal to the core
Portfolio's commission rate, it makes the same trades as the core version; the tests check that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from mft.model.data import Bar, BarType
from mft.model.enums import OrderSide
from mft.model.identifiers import InstrumentId
from mft.trading.strategy import Strategy, StrategyConfig


@dataclass
class MACrossoverConfig(StrategyConfig):
    bar_types: Sequence[str] = ()         # e.g. ['00700.XHKG-1-HOUR-LAST-EXTERNAL']
    fast_period: int = 10
    slow_period: int = 50
    trade_size: float = 100.0             # shares per entry (rounded down to lots by the venue)


class MACrossover(Strategy):
    def __init__(self, config: MACrossoverConfig):
        super().__init__(config)
        if not 0 < config.fast_period < config.slow_period:
            raise ValueError("need 0 < fast_period < slow_period")
        self.fast = config.fast_period
        self.slow = config.slow_period
        self.history: Dict[InstrumentId, List[float]] = {}

    def on_start(self) -> None:
        for bt in self.config.bar_types:
            self.subscribe_bars(bt if isinstance(bt, BarType) else BarType.from_str(bt))

    def on_bar(self, bar: Bar) -> None:
        iid = bar.bar_type.instrument_id
        prices = self.history.setdefault(iid, [])
        prices.append(bar.close)
        if len(prices) > self.slow + 1:
            prices.pop(0)
        if len(prices) <= self.slow:
            return   # need the slow average now and one bar ago

        fast_ma = sum(prices[-self.fast:]) / self.fast
        slow_ma = sum(prices[-self.slow:]) / self.slow
        prev_fast = sum(prices[-(self.fast + 1):-1]) / self.fast
        prev_slow = sum(prices[-(self.slow + 1):-1]) / self.slow

        held = self.portfolio.net_qty(iid)
        if prev_fast <= prev_slow and fast_ma > slow_ma:
            if held == 0:
                self.submit_order(self.order_factory.market(iid, OrderSide.BUY, self.config.trade_size))
        elif prev_fast >= prev_slow and fast_ma < slow_ma:
            if held > 0:
                self.submit_order(self.order_factory.market(iid, OrderSide.SELL, held,
                                                            tags={"exit_reason": "signal"}))
