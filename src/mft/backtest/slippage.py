"""
Slippage models: the cost of trading that a fill price taken from bars or trades leaves out.

A bar close or a printed trade is a price somebody traded at, not the price our order would have
got: crossing the spread, and moving the market when the order is large, cost more. The exchange
adds slippage to fills whose price does not already include those costs (bar and trade fills).
Quote fills already buy at the ask and book fills already walk the depth, so no slippage is added
to them.

Interface: adjust(price, side, qty, market_state, instrument) -> price. The result is never better
for the trader than `price`: higher for a buy, lower for a sell.

- NoSlippage
- FixedBpsSlippage: a fixed number of basis points (5 by default; the arithmetic core.backtester
  uses, so results match it to the last bit).
- FixedTickSlippage: a whole number of price increments.
- SpreadSlippage: half the quoted spread when quotes exist, else an assumed half-spread (3.33 bps
  by default, the median half-spread measured on the Hong Kong stocks in the report).
- VolatilitySlippage: a fraction of the last bar's high-low range.
- SquareRootImpactSlippage: half-spread + k x sigma x price x sqrt(qty / bar volume), the
  square-root market-impact law; sigma is the volatility of recent close-to-close returns.
- RandomSlippage: a seeded random draw around a mean, never negative, for robustness runs.
"""
from __future__ import annotations

import math
import random
from typing import Optional

from mft.model.enums import OrderSide

HK_MEDIAN_HALF_SPREAD_BPS = 3.33   # measured on the Hong Kong stocks (report, cost calibration)


def _against(price: float, side: OrderSide, cost: float) -> float:
    cost = max(0.0, cost)
    return price + cost if side is OrderSide.BUY else price - cost


class SlippageModel:
    def adjust(self, price: float, side: OrderSide, qty: float, state=None, instrument=None) -> float:
        raise NotImplementedError

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in vars(self).items() if not k.startswith("_"))
        return f"{type(self).__name__}({fields})"

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and repr(self) == repr(other)

    __hash__ = object.__hash__


class NoSlippage(SlippageModel):
    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        return price


class FixedBpsSlippage(SlippageModel):
    def __init__(self, bps: float = 5.0):
        if bps < 0:
            raise ValueError("bps cannot be negative")
        self.bps = bps

    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        # Exactly core.backtester's slippage wrapper: price x (1 +/- bps / 10000).
        mult = self.bps / 10000.0
        if side is OrderSide.BUY:
            return price * (1.0 + mult)
        return price * (1.0 - mult)


class FixedTickSlippage(SlippageModel):
    def __init__(self, ticks: int = 1):
        if ticks < 0:
            raise ValueError("ticks cannot be negative")
        self.ticks = ticks

    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        tick = getattr(instrument, "price_increment", None) or 0.01
        return _against(price, side, self.ticks * tick)


class SpreadSlippage(SlippageModel):
    def __init__(self, assumed_half_spread_bps: float = HK_MEDIAN_HALF_SPREAD_BPS):
        self.assumed_half_spread_bps = assumed_half_spread_bps

    def half_spread(self, price: float, state) -> float:
        quoted = getattr(state, "half_spread", None) if state is not None else None
        if quoted is not None and quoted > 0:
            return quoted
        return price * self.assumed_half_spread_bps / 10000.0

    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        return _against(price, side, self.half_spread(price, state))


class VolatilitySlippage(SlippageModel):
    def __init__(self, fraction: float = 0.1):
        if fraction < 0:
            raise ValueError("fraction cannot be negative")
        self.fraction = fraction

    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        bar = getattr(state, "last_bar", None)
        if bar is None:
            return price
        return _against(price, side, self.fraction * (bar.high - bar.low))


class SquareRootImpactSlippage(SlippageModel):
    """
    cost = half-spread + k x sigma x price x sqrt(qty / volume)

    The square-root law: market impact grows with the square root of the order's share of traded
    volume, scaled by volatility. sigma is the standard deviation of recent close-to-close returns
    (the last bar's high-low range over its close until two returns exist); volume is the last
    bar's. Without a bar, only the half-spread is charged.
    """

    def __init__(self, k: float = 1.0, assumed_half_spread_bps: float = HK_MEDIAN_HALF_SPREAD_BPS):
        self.k = k
        self.assumed_half_spread_bps = assumed_half_spread_bps
        self._spread = SpreadSlippage(assumed_half_spread_bps)

    @staticmethod
    def sigma(state) -> Optional[float]:
        closes = list(getattr(state, "recent_closes", []) or [])
        if len(closes) >= 3:
            rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
            if len(rets) >= 2:
                mean = sum(rets) / len(rets)
                return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        bar = getattr(state, "last_bar", None)
        if bar is not None and bar.close > 0:
            return (bar.high - bar.low) / bar.close
        return None

    def impact(self, price: float, qty: float, state) -> float:
        bar = getattr(state, "last_bar", None)
        sigma = self.sigma(state)
        if bar is None or sigma is None or bar.volume <= 0:
            return 0.0
        return self.k * sigma * price * math.sqrt(qty / bar.volume)

    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        cost = self._spread.half_spread(price, state) + self.impact(price, qty, state)
        return _against(price, side, cost)


class RandomSlippage(SlippageModel):
    """A cost in basis points drawn from a normal distribution, truncated at zero; the same seed
    gives the same sequence, so a robustness run can be repeated exactly."""

    def __init__(self, mean_bps: float = 5.0, std_bps: float = 2.0, seed: int = 0):
        self.mean_bps = mean_bps
        self.std_bps = std_bps
        self.seed = seed
        self._rng = random.Random(seed)

    def adjust(self, price, side, qty, state=None, instrument=None) -> float:
        bps = max(0.0, self._rng.gauss(self.mean_bps, self.std_bps))
        return _against(price, side, price * bps / 10000.0)
