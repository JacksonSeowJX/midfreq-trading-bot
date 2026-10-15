"""Enumerations shared by the model, after NautilusTrader's `model.enums`."""
from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    CRYPTOCURRENCY = "CRYPTOCURRENCY"


class BarAggregation(str, Enum):
    SECOND = "SECOND"
    MINUTE = "MINUTE"
    HOUR = "HOUR"
    DAY = "DAY"
    WEEK = "WEEK"


class PriceType(str, Enum):
    LAST = "LAST"
    BID = "BID"
    ASK = "ASK"
    MID = "MID"


class AggregationSource(str, Enum):
    EXTERNAL = "EXTERNAL"   # bars built by the venue or data vendor
    INTERNAL = "INTERNAL"   # bars we aggregated ourselves from ticks


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(str, Enum):
    GTC = "GTC"   # good till cancelled
    DAY = "DAY"   # cancelled at the end of the venue's local trading day
    IOC = "IOC"   # fill now or cancel


class OrderStatus(str, Enum):
    INITIALIZED = "INITIALIZED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"     # resting at the venue
    DENIED = "DENIED"         # stopped by the RiskEngine before reaching the venue
    REJECTED = "REJECTED"     # refused by the venue (cash, lot size, no market)
    FILLED = "FILLED"
    CANCELED = "CANCELED"

    @property
    def is_closed(self) -> bool:
        return self in (OrderStatus.DENIED, OrderStatus.REJECTED, OrderStatus.FILLED, OrderStatus.CANCELED)


class LiquiditySide(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"
