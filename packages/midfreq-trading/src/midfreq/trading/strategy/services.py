"""
What the node gives the strategy host: its clock, message bus, cache and venues, and the OMS's
and the market data's services as plain callables, so the host can be driven by the node, a
replay or a test alike.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from midfreq.core.cache import Cache
from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import Bar, BarType, QuoteTick
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId
from midfreq.core.model.instruments import Instrument
from midfreq.core.model.orders import Order
from midfreq.core.venues.spec import VenueRegistry


# ── what the node gives the host ──────────────────────────────────────────────────────────────

@dataclass
class NodeServices:
    """The node's services the strategies need, as plain callables (so the host can be driven
    by the node, a replay or a test alike)."""
    clock: Clock
    msgbus: MessageBus
    cache: Cache
    venues: VenueRegistry
    submit: Callable[[Order], None]                               # OMS.submit
    cancel: Callable[[ClientOrderId], None]                       # OMS.cancel
    wait_until: Callable[[Callable[[], bool], float, Callable[[], None]], bool]
    latest_price: Callable[[InstrumentId], Optional[float]]       # last pushed (forming) bar close
    quote: Callable[[InstrumentId], Optional[QuoteTick]]
    instrument: Callable[[InstrumentId], Optional[Instrument]]
    local_now: Callable[[], datetime]                             # the records' wall clock
    warm_up: Callable[[BarType, int], List[Bar]]                  # MarketDataService.warm_up
    order_prefix: str = "N"
    alert: Callable[[str, str], None] = lambda subject, body: None
    market_open: Callable[[Any], bool] = lambda venue: True               # venue in session now
    order: Optional[Callable[[ClientOrderId], Optional[Order]]] = None    # OMS.order
    family_done: Optional[Callable[[ClientOrderId], bool]] = None         # OMS.family_done
    next_order_id: Optional[Callable[[StrategyId], ClientOrderId]] = None  # OMS.next_order_id
    # the order journal's applied fills of a strategy (dicts, see JournalState.fills), and the
    # records' local wall time back to UTC ns: for fills journaled but never booked (a crash)
    journal_fills: Optional[Callable[[StrategyId], List[Dict[str, Any]]]] = None
    local_to_ns: Optional[Callable[[datetime], int]] = None
    tick_size: Optional[Callable[[InstrumentId, float], Optional[float]]] = None   # the tick at a price
