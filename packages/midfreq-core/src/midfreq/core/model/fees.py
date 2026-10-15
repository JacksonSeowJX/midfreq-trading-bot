"""
Fee models: what a venue charges per fill.

Rates are per side and a fraction of traded value, as in the original framework's
`Portfolio.commission_rate`:
- HKEX 0.16% all-in (broker commission, platform fee, 0.1% stamp duty, levies), calibrated against
  Moomoo paper-trading fills on 2026-07-09 (midfreq.core.portfolio.Portfolio.HK_FEE_RATE);
- US 0.005% (Moomoo SG's published US schedule, as used by the S&P 100 studies);
- crypto venues charge a maker and a taker rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from midfreq.core.model.enums import LiquiditySide


class FeeModel:
    def commission(self, quantity: float, price: float, liquidity_side: LiquiditySide = LiquiditySide.TAKER,
                   instrument=None) -> float:
        raise NotImplementedError

    def rate(self, liquidity_side: LiquiditySide = LiquiditySide.TAKER, instrument=None) -> float:
        """The proportional rate for one side, for strategies that size net of costs."""
        raise NotImplementedError


@dataclass(frozen=True)
class PercentFeeModel(FeeModel):
    """commission = traded value x rate, whichever side provides liquidity."""
    rate_per_side: float

    def rate(self, liquidity_side: LiquiditySide = LiquiditySide.TAKER, instrument=None) -> float:
        return self.rate_per_side

    def commission(self, quantity, price, liquidity_side=LiquiditySide.TAKER, instrument=None) -> float:
        # Same operation order as midfreq.core.portfolio.Portfolio.execute_trade (value first, then x rate),
        # so a legacy run reproduces its commissions to the last bit.
        trade_value = quantity * price
        return trade_value * self.rate_per_side


@dataclass(frozen=True)
class MakerTakerFeeModel(FeeModel):
    """Crypto-style fees: resting (maker) fills are cheaper than aggressive (taker) ones. An
    instrument's own maker_fee/taker_fee, when set, override the venue default."""
    maker: float
    taker: float

    def rate(self, liquidity_side=LiquiditySide.TAKER, instrument=None) -> float:
        if liquidity_side is LiquiditySide.MAKER:
            own: Optional[float] = getattr(instrument, "maker_fee", None)
            return self.maker if own is None else own
        own = getattr(instrument, "taker_fee", None)
        return self.taker if own is None else own

    def commission(self, quantity, price, liquidity_side=LiquiditySide.TAKER, instrument=None) -> float:
        trade_value = quantity * price
        return trade_value * self.rate(liquidity_side, instrument)
