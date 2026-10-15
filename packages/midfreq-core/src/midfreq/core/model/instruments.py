"""
Instruments, after NautilusTrader's `model.instruments`.

An instrument carries the venue rules an order has to satisfy: the currency it settles in, the
price tick, and the smallest tradable quantity. For an equity that quantity is the board lot (100
shares for Tencent, 400 for HSBC, 1 for any US stock); for a crypto pair it is a size step on a
fractional quantity (0.00001 BTC on Binance), plus a minimum notional value per order.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional

from midfreq.core.model.enums import AssetClass
from midfreq.core.model.identifiers import InstrumentId


def _floor_to_step(value: float, step: float) -> float:
    """Largest multiple of `step` not above `value`, without float drift (0.3 / 0.1 is 2.9999...)."""
    if step <= 0:
        return value
    d_step = Decimal(repr(step))
    units = math.floor(Decimal(repr(value)) / d_step)
    return float(units * d_step)


@dataclass(frozen=True, kw_only=True)
class Instrument:
    id: InstrumentId
    raw_symbol: str                      # the venue's own code, e.g. '00700', 'BTCUSDT'
    currency: str                        # settlement / quote currency, e.g. 'HKD', 'USDT'
    price_increment: float = 0.01
    size_increment: float = 1.0          # quantity granularity (1 share, 0.00001 BTC)
    lot_size: float = 1.0                # orders must be a multiple of this (HK board lot)
    min_quantity: Optional[float] = None
    min_notional: Optional[float] = None
    maker_fee: Optional[float] = None    # overrides the venue fee model when set
    taker_fee: Optional[float] = None
    asset_class: AssetClass = AssetClass.EQUITY
    info: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def venue(self):
        return self.id.venue

    @property
    def quantity_step(self) -> float:
        """The step every order quantity must be a multiple of."""
        return max(self.lot_size, self.size_increment)

    def make_qty(self, quantity: float) -> float:
        """Round `quantity` DOWN to a tradable amount (a whole number of lots / size steps). Rounding
        down never spends more than the strategy asked for."""
        return _floor_to_step(quantity, self.quantity_step)

    def is_valid_qty(self, quantity: float) -> bool:
        return quantity > 0 and self.make_qty(quantity) == quantity

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["id"] = str(self.id)
        d["asset_class"] = self.asset_class.value
        d["type"] = type(self).__name__
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Instrument":
        d = dict(d)
        kind = d.pop("type", "Equity")
        d["id"] = InstrumentId.from_str(d["id"])
        d["asset_class"] = AssetClass(d.get("asset_class", AssetClass.EQUITY.value))
        cls = {"Equity": Equity, "CurrencyPair": CurrencyPair, "Instrument": Instrument}[kind]
        return cls(**d)


@dataclass(frozen=True, kw_only=True)
class Equity(Instrument):
    isin: Optional[str] = None
    asset_class: AssetClass = AssetClass.EQUITY


@dataclass(frozen=True, kw_only=True)
class CurrencyPair(Instrument):
    """A spot crypto pair such as BTCUSDT.BINANCE: priced in `quote_currency`, quantity in
    `base_currency`, tradable in fractional sizes on `size_increment`."""
    base_currency: str = ""
    quote_currency: str = ""
    lot_size: float = 0.0
    asset_class: AssetClass = AssetClass.CRYPTOCURRENCY

    @property
    def quantity_step(self) -> float:
        return self.size_increment if self.size_increment > 0 else self.lot_size
