"""
The contracts between the trading node's components (docs/LIVE_ARCHITECTURE.md).

Every component runs on the node's single event-loop thread, except what the connectors call
back on their own threads: those callbacks only put items on the node's queue (`EventQueue`).
So the OMS, risk, positions, market data and strategies never need locks, and a session can be
replayed deterministically by feeding the same queue items with a test clock.

Messages between components go over midfreq.core.common.msgbus.MessageBus on these topics.
Closed bars use the engine's own topic, `data.bars.<bar_type>` (midfreq.core.common.msgbus), so a
native Strategy's `subscribe_bars(bar_type)` receives the MarketDataService's bars directly, as
in a backtest. Cross-sections have no counterpart in the Strategy API: the StrategyHost turns
each into a TimeSlice on `data.slices` for native strategies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple

import pandas as pd

from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.events import OrderEvent, OrderFilled
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId, Venue
from midfreq.core.model.instruments import Instrument
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import BarUpdate, PositionReport, StreamStatus

# ── bus topics ─────────────────────────────────────────────────────────────────────────────────
TOPIC_BAR = "data.bars.{bar_type}"                      # Bar: closed bars only (the engine's topic)
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

    def family(self, client_order_id: ClientOrderId) -> List[Order]:
        """The order with the child orders that replaced it when it was re-priced (tags parent,
        root, attempt), oldest first: one intent's orders."""

    def family_done(self, client_order_id: ClientOrderId) -> bool:
        """Whether that intent has its outcome: every order of the family closed and no child
        order still to come."""

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
    """The node's books (midfreq.trading.positions.PositionLedger): one per strategy."""

    def on_fill(self, fill: OrderFilled) -> None:
        """Book one fill (the OMS calls it once per applied fill)."""

    def position(self, strategy_id: StrategyId, instrument_id: InstrumentId) -> float: ...

    def positions(self, strategy_id: StrategyId) -> Dict[InstrumentId, float]: ...

    def cash(self, strategy_id: StrategyId) -> float: ...

    def avg_price(self, strategy_id: StrategyId, instrument_id: InstrumentId) -> Optional[float]: ...

    def snapshot(self, strategy_id: StrategyId) -> Dict[str, Any]:
        """The book as plain data; `restore` reads it back."""

    def restore(self, strategy_id: "StrategyId | str", cash: float, positions: Mapping[Any, Any],
                capital: Optional[float] = None, realized_pnl: float = 0.0, commissions: float = 0.0,
                dividends: float = 0.0, corporate_actions: Iterable[str] = ()) -> None:
        """Set a strategy's book: cash, and positions {instrument: (qty, avg_px)}."""

    def apply_corporate_action(self, strategy_id: "StrategyId | str", action: "CorporateAction") -> float:
        """Book a dividend (cash) or a split (quantity and average price); returns the cash."""

    def reconcile(self, venue: Venue, broker: List[PositionReport],
                  strategy_ids: Optional[Iterable[StrategyId]] = None) -> ReconcileReport:
        """Shrink-only: a strategy's claim never grows from the broker's view, only shrinks to it."""


class MarketDataService(Protocol):
    """midfreq.trading.marketdata.LiveMarketDataService. Inbound: the connectors' push threads
    only ever call the handlers of `stream_handlers()` (or the queue's put, which they are), and
    the node's loop hands each queued item back through `dispatch` (or the on_* methods)."""

    # ── inbound ──
    def stream_handlers(self, venue: Optional[Venue] = None) -> Any:
        """StreamHandlers(bar_update, quote, stream_status): what a DataClient is given; each only
        puts what it receives on the node's queue (stream status tagged with its source)."""

    def dispatch(self, item: Any) -> bool:
        """Handle a queued BarUpdate, QuoteTick or StreamStatus; False for anything else."""

    def on_bar_update(self, update: BarUpdate) -> None: ...

    def on_quote(self, quote: QuoteTick) -> None: ...

    def on_stream_status(self, status: StreamStatus) -> None: ...

    def on_timer(self, now_ns: int) -> None:
        """Finalise bars and cross-sections whose window has elapsed; detect stalls (a stall is
        a StreamEvent('stall') on TOPIC_STREAM, the stream is resubscribed once, and a second
        stall sets `fatal_stall`)."""

    # ── subscriptions and lifecycle ──
    def subscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None: ...

    def unsubscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None: ...

    def subscribe_quotes(self, instrument_id: InstrumentId) -> None: ...

    def unsubscribe_quotes(self, instrument_id: InstrumentId) -> None: ...

    def resubscribe(self) -> int:
        """Subscribe every live stream again now; returns how many."""

    def warm_up(self, bar_type: BarType, count: int) -> List[Bar]:
        """The last `count` closed bars from the local store (never the broker's metered history)."""

    def start(self) -> None: ...

    def stop(self) -> Any:
        """Unsubscribe, finalise bars whose window has elapsed, write the session's bars to the
        store (unless persisting is off); returns what was written."""

    # ── reads ──
    def last_price(self, instrument_id: InstrumentId) -> Optional[float]: ...

    @property
    def fatal_stall(self) -> bool: ...


class TradingCalendar(Protocol):
    def is_trading_day(self, day: date) -> bool: ...

    def session(self, day: date) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
        """(open, close) in UTC, None on a holiday or weekend; early closes included."""

    def next_open(self, after: pd.Timestamp) -> pd.Timestamp: ...


class ReferenceData(Protocol):
    def instrument(self, instrument_id: InstrumentId) -> Instrument: ...

    def calendar(self, venue: Venue) -> TradingCalendar: ...

    def corporate_actions(self, instrument_id: InstrumentId, start: date, end: date) -> List[CorporateAction]: ...
