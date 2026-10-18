"""
Prices and sizes for the unfilled-order policy's child orders: the bid, ask and last price from
whatever the price source returns, a price on the tick and a quantity in whole lots.

The helpers are pure functions. RepricingMixin holds the OMS's methods that read its price, tick
and lot sources and apply the policy's offsets: a buy at ask + quote_offset_bps, a sell at
bid - quote_offset_bps, and without a quote the last price +/- last_offset_bps; buys round up to
the tick and sells down.
"""
from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Tuple

from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.model.orders import Order

log = logging.getLogger("midfreq.trading.oms")

__all__ = ["PriceSource", "RepricingMixin", "SizeSource"]

PriceSource = Callable[[InstrumentId], Any]
SizeSource = Callable[[InstrumentId], Optional[float]]


def _price_parts(snapshot: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(bid, ask, last) from whatever the price source returns: a QuoteTick or any object with
    bid/ask/last attributes (each may be missing or None), a dict with those keys, or a number
    (the last price)."""
    if snapshot is None:
        return None, None, None
    if isinstance(snapshot, (int, float)):
        return None, None, float(snapshot)
    if isinstance(snapshot, Mapping):
        get = snapshot.get
    else:
        def get(name, default=None):
            return getattr(snapshot, name, default)

    def positive(x):
        try:
            x = float(x)
        except (TypeError, ValueError):
            return None
        return x if math.isfinite(x) and x > 0 else None
    return positive(get("bid")), positive(get("ask")), positive(get("last", get("last_price")))


def _to_tick(price: float, tick: float, up: bool) -> float:
    if not tick or tick <= 0:
        return price
    units = Decimal(repr(price)) / Decimal(repr(float(tick)))
    units = units.to_integral_value(rounding="ROUND_CEILING" if up else "ROUND_FLOOR")
    return float(units * Decimal(repr(float(tick))))


def _floor_to_lot(quantity: float, lot: Optional[float]) -> float:
    if not lot or lot <= 0:
        return quantity
    units = (Decimal(repr(quantity)) / Decimal(repr(float(lot)))).to_integral_value(rounding="ROUND_FLOOR")
    return float(units * Decimal(repr(float(lot))))


class RepricingMixin:
    """The re-pricing part of OrderManagementSystem (oms.py): a child order's price from the
    price source with the policy's offsets, on the tick, and the tick and lot sizes.

    Relies on the OMS's `policy` (the offsets), `_price_source` and `_tick_size`."""

    def _reprice_price(self, order: Order) -> Optional[float]:
        if self._price_source is None:
            return None
        try:
            snapshot = self._price_source(order.instrument_id)
        except Exception:  # noqa: BLE001
            log.exception("price source failed for %s", order.instrument_id)
            return None
        bid, ask, last = _price_parts(snapshot)
        q = self.policy.quote_offset_bps / 10_000.0
        f = self.policy.last_offset_bps / 10_000.0
        if order.is_buy:
            price = ask * (1 + q) if ask is not None else (last * (1 + f) if last is not None else None)
        else:
            price = bid * (1 - q) if bid is not None else (last * (1 - f) if last is not None else None)
        if price is None or not price > 0:
            return None
        tick = self._tick_at(order.instrument_id, price)
        price = _to_tick(price, tick, up=order.is_buy) if tick else price
        return price if price > 0 else None

    def _tick_at(self, iid: InstrumentId, price: float) -> Optional[float]:
        """The tick at `price` (a tick source may take the price: HKEX's spread table), else the
        instrument's one tick."""
        if self._tick_size is None:
            return None
        try:
            value = self._tick_size(iid, price)
        except TypeError:
            return self._size(self._tick_size, iid)
        except Exception:  # noqa: BLE001
            return None
        return float(value) if value else None

    @staticmethod
    def _size(source: Optional[SizeSource], iid: InstrumentId) -> Optional[float]:
        if source is None:
            return None
        try:
            value = source(iid)
        except Exception:  # noqa: BLE001
            return None
        return float(value) if value else None
