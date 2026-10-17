"""
The contracts between the trading node's components (docs/LIVE_ARCHITECTURE.md).

Every component runs on the node's single event-loop thread, except what the connectors call
back on their own threads: those callbacks only put items on the node's queue (`EventQueue`).
So the OMS, risk, positions, market data and strategies never need locks, and a session can be
replayed deterministically by feeding the same queue items with a test clock.

Messages between components go over midfreq.core.common.msgbus.MessageBus on these topics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

import pandas as pd

from midfreq.core.model.data import Bar, BarType
from midfreq.core.model.events import OrderEvent, OrderFilled
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId, Venue
from midfreq.core.model.instruments import Instrument
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import PositionReport

# ── bus topics ─────────────────────────────────────────────────────────────────────────────────
TOPIC_BAR = "data.bars.{instrument_id}"                 # Bar: closed bars only
TOPIC_CROSS_SECTION = "data.cross_section.{universe}"   # CrossSection
TOPIC_QUOTE = "data.quotes.{instrument_id}"             # QuoteTick
TOPIC_ORDER_EVENTS = "events.order.{strategy_id}"       # OrderEvent, after the OMS applied it
TOPIC_FILLS = "events.fills"                            # OrderFilled, every strategy
TOPIC_POSITIONS = "events.positions.{strategy_id}"      # PositionChanged
TOPIC_RISK = "events.risk"                              # RiskEvent: denials, halts, resumes
TOPIC_STREAM = "events.stream"                          # StreamEvent: connects, gaps, stalls
TOPIC_HEARTBEAT = "ops.heartbeat"                       # Heartbeat


# ── messages ───────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CrossSection:
    """Every subscribed instrument's bar for one interval of a universe, published once the
    interval is complete (all reported) or its window has elapsed with at least the minimum share
    (the 90% rule of the original engine)."""
    universe: str
    ts_event: int                                   # the interval's close, UTC ns
    bars: Mapping[InstrumentId, Bar]
    complete: bool
    missing: Tuple[InstrumentId, ...] = ()


@dataclass(frozen=True)
class PositionChanged:
    strategy_id: StrategyId
    instrument_id: InstrumentId
    quantity: float                                 # after the change
    avg_px: Optional[float]
    realized_pnl: float
    ts_event: int


@dataclass(frozen=True)
class RiskEvent:
    kind: str                                       # 'denied' | 'halted' | 'resumed' | 'limit_warning'
    reason: str
    ts_event: int
    client_order_id: Optional[ClientOrderId] = None
    strategy_id: Optional[StrategyId] = None


@dataclass(frozen=True)
class StreamEvent:
    kind: str                                       # 'connected' | 'disconnected' | 'reconnected' | 'stall' | 'gap' | 'error'
    detail: str
    ts_event: int
    instrument_id: Optional[InstrumentId] = None


@dataclass(frozen=True)
class Heartbeat:
    ts_event: int
    last_bar_ts: Optional[int]
    stream_lag_s: Optional[float]
    open_orders: int
    positions: Dict[str, float] = field(default_factory=dict)
    memory_mb: Optional[float] = None


@dataclass(frozen=True)
class ReconcileReport:
    """What a reconciliation found and did. `adjustments` are the changes made to our side
    (shrink-only for positions); `unexplained` are differences left for a person to look at."""
    source: str                                     # 'orders' | 'positions'
    ts_event: int
    matched: int
    adjustments: Tuple[str, ...] = ()
    unexplained: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CorporateAction:
    instrument_id: InstrumentId
    kind: str                                       # 'dividend' | 'split'
    ex_date: date
    amount: float                                   # cash per share (dividend) or ratio (split)
    currency: str = ""
    source: str = ""


# ── components ─────────────────────────────────────────────────────────────────────────────────
class EventQueue(Protocol):
    """The node's inbox. Thread-safe; everything else in the node is single-threaded."""
    def put(self, item: Any) -> None: ...


class OrderManager(Protocol):
    """The OMS: the one owner of every order's state."""

    def submit(self, order: Order) -> None:
        """Pre-trade risk, then route to the venue's ExecutionClient and journal it. Never waits
        for the venue; a denial becomes OrderDenied on TOPIC_ORDER_EVENTS."""

    def cancel(self, client_order_id: ClientOrderId) -> None: ...

    def handle_event(self, event: OrderEvent) -> None:
        """Apply a venue event (from the queue) to the order, journal it, publish it."""

    def order(self, client_order_id: ClientOrderId) -> Optional[Order]: ...

    def open_orders(self, strategy_id: Optional[StrategyId] = None) -> List[Order]: ...

    def on_timer(self, now_ns: int) -> None:
        """Time-based policies: re-price or cancel orders left unfilled, expire day orders."""

    def reconcile(self) -> ReconcileReport:
        """Compare the journal's open orders and recent fills with the venues' and repair our side."""


class PreTradeRisk(Protocol):
    def check(self, order: Order) -> Optional[str]:
        """None if the order may go to the venue; otherwise why not (becomes OrderDenied)."""

    def halt(self, reason: str) -> None:
        """Kill switch: deny every new order until resume()."""

    def resume(self) -> None: ...

    @property
    def halted(self) -> bool: ...


class PositionKeeper(Protocol):
    def on_fill(self, fill: OrderFilled) -> None: ...

    def position(self, strategy_id: StrategyId, instrument_id: InstrumentId) -> float: ...

    def positions(self, strategy_id: StrategyId) -> Dict[InstrumentId, float]: ...

    def cash(self, strategy_id: StrategyId) -> float: ...

    def reconcile(self, venue: Venue, broker: List[PositionReport]) -> ReconcileReport:
        """Shrink-only: a strategy's claim never grows from the broker's view, only shrinks to it."""


class MarketDataService(Protocol):
    def subscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None: ...

    def subscribe_quotes(self, instrument_id: InstrumentId) -> None: ...

    def warm_up(self, bar_type: BarType, count: int) -> List[Bar]:
        """The last `count` closed bars from the local store (never the broker's metered history)."""

    def start(self) -> None: ...

    def stop(self) -> None:
        """Unsubscribe, finalise bars whose window has elapsed, write the session's bars to the store."""

    def on_timer(self, now_ns: int) -> None:
        """Finalise bars and cross-sections whose window has elapsed; detect stalls."""


class TradingCalendar(Protocol):
    def is_trading_day(self, day: date) -> bool: ...

    def session(self, day: date) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
        """(open, close) in UTC, None on a holiday or weekend; early closes included."""

    def next_open(self, after: pd.Timestamp) -> pd.Timestamp: ...


class ReferenceData(Protocol):
    def instrument(self, instrument_id: InstrumentId) -> Instrument: ...

    def calendar(self, venue: Venue) -> TradingCalendar: ...

    def corporate_actions(self, instrument_id: InstrumentId, start: date, end: date) -> List[CorporateAction]: ...
