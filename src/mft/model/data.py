"""
Bars and bar types, after NautilusTrader's `model.data`.

A BarType names a stream of bars completely, so it can be used as a catalog key and a message-bus
topic: `00700.XHKG-1-HOUR-LAST-EXTERNAL` is hourly bars of Tencent on HKEX, built from last-trade
prices by the data vendor (EXTERNAL) rather than by us.

`Bar.ts_event` is the time the bar CLOSED, in UTC nanoseconds. That is Nautilus's convention and it
is also what Moomoo's `time_key` means (a Moomoo bar stamped 10:30 covers 09:30-10:30), so a
strategy acting on a bar can never see a price from after `ts_event`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Iterable, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from mft.model.enums import AggregationSource, BarAggregation, PriceType
from mft.model.identifiers import InstrumentId

if TYPE_CHECKING:
    from mft.model.enums import OrderSide

_AGG_SECONDS = {
    BarAggregation.SECOND: 1,
    BarAggregation.MINUTE: 60,
    BarAggregation.HOUR: 3600,
    BarAggregation.DAY: 86400,
    BarAggregation.WEEK: 7 * 86400,
}

# The original framework names timeframes '1h', '1d', ... (core.models.Timeframe and the
# data/<MKT>_<CODE>/<tf>.parquet file names). These are the same intervals.
LEGACY_TIMEFRAMES = {
    "1m": "1-MINUTE",
    "5m": "5-MINUTE",
    "1h": "1-HOUR",
    "4h": "4-HOUR",
    "1d": "1-DAY",
}


@dataclass(frozen=True, slots=True)
class BarSpecification:
    step: int
    aggregation: BarAggregation
    price_type: PriceType = PriceType.LAST

    def __post_init__(self):
        if int(self.step) <= 0:
            raise ValueError(f"bar step must be positive, got {self.step}")

    @classmethod
    def from_str(cls, value: str) -> "BarSpecification":
        """Parse '1-HOUR' or '1-HOUR-LAST'."""
        parts = value.strip().upper().split("-")
        if len(parts) not in (2, 3):
            raise ValueError(f"bar specification must be '<step>-<AGGREGATION>[-<PRICE>]': {value!r}")
        try:
            step = int(parts[0])
            aggregation = BarAggregation(parts[1])
            price_type = PriceType(parts[2]) if len(parts) == 3 else PriceType.LAST
        except ValueError as exc:
            raise ValueError(f"invalid bar specification {value!r}: {exc}") from None
        return cls(step, aggregation, price_type)

    @classmethod
    def from_legacy(cls, timeframe: str) -> "BarSpecification":
        """'1h' -> 1-HOUR-LAST."""
        if timeframe not in LEGACY_TIMEFRAMES:
            raise ValueError(f"unknown legacy timeframe {timeframe!r}; have {sorted(LEGACY_TIMEFRAMES)}")
        return cls.from_str(LEGACY_TIMEFRAMES[timeframe])

    @property
    def legacy_timeframe(self) -> Optional[str]:
        """'1h' for 1-HOUR, or None when the old framework has no name for this interval."""
        key = f"{self.step}-{self.aggregation.value}"
        for tf, spec in LEGACY_TIMEFRAMES.items():
            if spec == key:
                return tf
        return None

    @property
    def timedelta(self) -> timedelta:
        return timedelta(seconds=self.step * _AGG_SECONDS[self.aggregation])

    @property
    def nanos(self) -> int:
        return self.step * _AGG_SECONDS[self.aggregation] * 1_000_000_000

    @property
    def is_intraday(self) -> bool:
        return self.nanos < 86400 * 1_000_000_000

    @property
    def interval(self) -> str:
        """'1-HOUR': the part connectors map to their own interval names."""
        return f"{self.step}-{self.aggregation.value}"

    def __str__(self) -> str:
        return f"{self.step}-{self.aggregation.value}-{self.price_type.value}"


@dataclass(frozen=True, slots=True)
class BarType:
    instrument_id: InstrumentId
    spec: BarSpecification
    aggregation_source: AggregationSource = AggregationSource.EXTERNAL

    @classmethod
    def from_str(cls, value: str) -> "BarType":
        """Parse '00700.XHKG-1-HOUR-LAST-EXTERNAL'. Splits from the right, so symbols that contain
        '-' (Coinbase's 'BTC-USD') still parse."""
        parts = value.strip().rsplit("-", 4)
        if len(parts) != 5:
            raise ValueError(f"bar type must be '<instrument>-<step>-<AGG>-<PRICE>-<SOURCE>': {value!r}")
        instrument, step, agg, price, source = parts
        return cls(
            InstrumentId.from_str(instrument),
            BarSpecification.from_str(f"{step}-{agg}-{price}"),
            AggregationSource(source.upper()),
        )

    @classmethod
    def of(cls, instrument_id: "str | InstrumentId", spec: "str | BarSpecification") -> "BarType":
        iid = instrument_id if isinstance(instrument_id, InstrumentId) else InstrumentId.from_str(instrument_id)
        sp = spec if isinstance(spec, BarSpecification) else BarSpecification.from_str(spec)
        return cls(iid, sp)

    @property
    def venue(self):
        return self.instrument_id.venue

    def __str__(self) -> str:
        return f"{self.instrument_id}-{self.spec}-{self.aggregation_source.value}"


@dataclass(frozen=True, slots=True)
class Bar:
    bar_type: BarType
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts_event: int   # bar close, UTC ns
    ts_init: int    # when this object was created; equal to ts_event for historical data

    @property
    def instrument_id(self) -> InstrumentId:
        return self.bar_type.instrument_id

    @property
    def timestamp(self) -> pd.Timestamp:
        """ts_event as a UTC Timestamp, for display."""
        return pd.Timestamp(self.ts_event, unit="ns", tz="UTC")


BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


def bars_to_frame(bars: Iterable[Bar]) -> pd.DataFrame:
    """Bars as a DataFrame indexed by ts_event (UTC), with ts_event/ts_init kept as int64 columns."""
    bars = list(bars)
    df = pd.DataFrame({
        "open": np.fromiter((b.open for b in bars), float, len(bars)),
        "high": np.fromiter((b.high for b in bars), float, len(bars)),
        "low": np.fromiter((b.low for b in bars), float, len(bars)),
        "close": np.fromiter((b.close for b in bars), float, len(bars)),
        "volume": np.fromiter((b.volume for b in bars), float, len(bars)),
        "ts_event": np.fromiter((b.ts_event for b in bars), np.int64, len(bars)),
        "ts_init": np.fromiter((b.ts_init for b in bars), np.int64, len(bars)),
    })
    df.index = pd.to_datetime(df["ts_event"], unit="ns", utc=True)
    df.index.name = "timestamp"
    return df


def frame_to_bars(df: pd.DataFrame, bar_type: BarType) -> List[Bar]:
    """Rows with open..volume plus ts_event (and optionally ts_init) as Bars."""
    if df.empty:
        return []
    ts_event = df["ts_event"].to_numpy(dtype=np.int64)
    ts_init = df["ts_init"].to_numpy(dtype=np.int64) if "ts_init" in df else ts_event
    cols = [df[c].to_numpy(dtype=float) for c in BAR_COLUMNS]
    return [
        Bar(bar_type, float(o), float(h), float(lo), float(c), float(v), int(te), int(ti))
        for o, h, lo, c, v, te, ti in zip(*cols, ts_event, ts_init)
    ]


# ── tick and order-book data ─────────────────────────────────────────
# Finer data than bars, for venues and periods where it exists. Each carries ts_event (when the
# venue stamped it) and ts_init (when we created it), UTC nanoseconds, like Bar, so the DataEngine
# can merge all of them into one time-ordered stream.

@dataclass(frozen=True, slots=True)
class QuoteTick:
    """Top of book: the best bid and ask and the size displayed at each."""
    instrument_id: InstrumentId
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts_event: int
    ts_init: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class TradeTick:
    """One printed trade. `aggressor_side` is the side that crossed the spread (BUY = a buyer lifted
    the offer), or None when the venue does not say."""
    instrument_id: InstrumentId
    price: float
    size: float
    aggressor_side: Optional["OrderSide"]
    ts_event: int
    ts_init: int
    trade_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBookDepth:
    """An L2 snapshot: aggregated size per price, best level first on each side."""
    instrument_id: InstrumentId
    bids: Tuple[BookLevel, ...]
    asks: Tuple[BookLevel, ...]
    ts_event: int
    ts_init: int

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    def size_at(self, side: "OrderSide", price: float) -> float:
        """Displayed size at `price` on the bid side (BUY) or the ask side (SELL)."""
        levels = self.bids if side.value == "BUY" else self.asks
        return sum(l.size for l in levels if l.price == price)


MarketData = Union[Bar, QuoteTick, TradeTick, OrderBookDepth]


def instrument_of(item: "MarketData") -> InstrumentId:
    return item.instrument_id
