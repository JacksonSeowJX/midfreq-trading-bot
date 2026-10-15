"""
Fill models: when, and at what price, a simulated venue fills a market order.

- FillModel(timing="close") (the default): fill at the close of the bar the decision was made on,
  plus slippage. This is the original backtester's convention, so results stay comparable.
- FillModel(timing="next_open"): fill at the OPEN of the instrument's next bar, plus slippage. The
  more conservative choice; it removes the assumption that one can trade at the very price that
  triggered the decision.
- LegacyFillModel: fill at the price the strategy itself passed (`Order.reference_price`) with the
  original slippage wrapper's maths and no lot rounding, so core strategies run through
  mft.legacy reproduce core.backtester.Backtester exactly.

Slippage is in basis points and always against the trader: buys pay price x (1 + bps/10000),
sells receive price x (1 - bps/10000), the same expression as core.backtester.
"""
from __future__ import annotations

from dataclasses import dataclass

from mft.model.enums import OrderSide

CLOSE = "close"
NEXT_OPEN = "next_open"
REFERENCE = "reference"


@dataclass(frozen=True)
class FillModel:
    timing: str = CLOSE
    slippage_bps: float = 0.0
    round_lots: bool = True        # round quantities down to whole lots / size steps

    def __post_init__(self):
        if self.timing not in (CLOSE, NEXT_OPEN, REFERENCE):
            raise ValueError(f"timing must be 'close', 'next_open' or 'reference', got {self.timing!r}")

    @property
    def slippage_mult(self) -> float:
        return self.slippage_bps / 10000.0

    def price(self, base: float, side: OrderSide) -> float:
        mult = self.slippage_mult
        if side is OrderSide.BUY:
            return base * (1.0 + mult)       # pay more
        return base * (1.0 - mult)           # receive less


@dataclass(frozen=True)
class LegacyFillModel(FillModel):
    timing: str = REFERENCE
    round_lots: bool = False
