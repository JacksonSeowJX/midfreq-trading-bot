"""
Minimal stand-ins for the node's components (A: OMS, risk, positions; C: market data, reference
data), written against midfreq.trading.interfaces under the protocols' own method names, so the
node's own logic (lifecycle, queue, timers, records) is tested in isolation. The real components
are tested on their own and, together, by the real-components replay equivalence test. They do
the least the node needs and nothing clever:

- StandInOMS: risk check, route by venue, apply venue events to the Order, publish them on
  events.order.<strategy> (and fills on events.fills); cancels orders left open `wait_s`.
- StandInMarketData: the original engine's bar finality (a bar is final when a newer push
  arrives, or when its window plus a grace has elapsed), cross-sections per universe (complete,
  or at least 90% once the window has elapsed), warm-up from a legacy bar cache folder, and the
  session's bars written back there at stop.
- StandInPositions, StandInRisk, StandInRefData: the obvious.
"""
from __future__ import annotations

import contextlib
import io
import math
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.enums import OrderSide, OrderStatus
from midfreq.core.model.events import (
    OrderAccepted, OrderCanceled, OrderDenied, OrderFilled, OrderRejected,
)
from midfreq.core.model.identifiers import AccountId, InstrumentId, TradeId
from midfreq.core.model.orders import Order
from midfreq.core.venues.spec import default_venues
from midfreq.data.adapters.base import DataClient, ExecutionClient, OrderReport, FillReport
from midfreq.trading.interfaces import (
    TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_FILLS, TOPIC_ORDER_EVENTS, TOPIC_QUOTE, CrossSection, ReconcileReport,
)


class StandInRisk:
    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.deny: Dict[str, str] = dict((config or {}).get("deny", {}))
        self._halted = False
        self.checked: List[Order] = []

    def check(self, order: Order) -> Optional[str]:
        self.checked.append(order)
        if self._halted:
            return "halted"
        return self.deny.get(str(order.instrument_id))

    def halt(self, reason: str, planned: bool = False) -> None:
        self._halted = True

    def resume(self) -> None:
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted


class StandInPositions:
    def __init__(self):
        self.qty: Dict[Any, Dict[InstrumentId, float]] = defaultdict(lambda: defaultdict(float))
        self._cash: Dict[Any, float] = defaultdict(float)
        self.fills: List[OrderFilled] = []
        self.restored: Dict[Any, Any] = {}
        self.reconciled: List[Any] = []

    def on_fill(self, fill: OrderFilled) -> None:
        self.fills.append(fill)
        sign = 1 if fill.side is OrderSide.BUY else -1
        self.qty[fill.strategy_id][fill.instrument_id] += sign * fill.last_qty
        self._cash[fill.strategy_id] -= sign * fill.last_qty * fill.last_px

    def position(self, strategy_id, instrument_id) -> float:
        return self.qty[strategy_id][instrument_id]

    def positions(self, strategy_id) -> Dict[InstrumentId, float]:
        return {k: v for k, v in self.qty[strategy_id].items() if v}

    def cash(self, strategy_id) -> float:
        return self._cash[strategy_id]

    def avg_price(self, strategy_id, instrument_id):
        return None

    def restore(self, strategy_id, cash, positions, capital=None, realized_pnl=0.0, commissions=0.0,
                dividends=0.0, corporate_actions=()) -> None:
        self.restored[strategy_id] = (dict(positions), cash)
        self.qty[strategy_id].clear()
        for iid, (q, _px) in positions.items():
            self.qty[strategy_id][iid] = q
        self._cash[strategy_id] = cash

    def snapshot(self, strategy_id):
        return {"cash": self._cash[strategy_id], "positions": dict(self.qty[strategy_id])}

    def reconcile(self, venue, broker, strategy_ids=None) -> ReconcileReport:
        self.reconciled.append((venue, list(broker)))
        return ReconcileReport("positions", 0, len(broker))


class StandInOMS:
    def __init__(self, msgbus, clock, execution_clients, risk, positions, config: Optional[Mapping[str, Any]] = None):
        self.msgbus = msgbus
        self.clock = clock
        self.clients = execution_clients
        self.risk = risk
        self.positions = positions
        from midfreq.trading.oms import UnfilledOrderPolicy       # the configuration's own reader
        policy = UnfilledOrderPolicy.from_config((config or {}).get("unfilled_order"))
        self.wait_ns = int(policy.wait_s * 1e9)
        self.orders: Dict[Any, Order] = {}
        self.submitted_at: Dict[Any, int] = {}
        self.events: List[Any] = []
        self.calls: List[str] = []
        self._seen_trades: set = set()

    def _publish(self, event) -> None:
        self.msgbus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=event.strategy_id), event)
        if isinstance(event, OrderFilled):
            self.msgbus.publish(TOPIC_FILLS, event)

    def submit(self, order: Order) -> None:
        self.calls.append("submit")
        self.orders[order.client_order_id] = order
        reason = self.risk.check(order)
        now = self.clock.timestamp_ns()
        if reason:
            order.status = OrderStatus.DENIED
            order.reason = reason
            self._publish(OrderDenied(order.client_order_id, order.strategy_id, order.instrument_id, now, reason=reason))
            return
        order.status = OrderStatus.SUBMITTED
        self.submitted_at[order.client_order_id] = now
        self.clients[order.instrument_id.venue].submit_order(order)

    def cancel(self, client_order_id) -> None:
        o = self.orders.get(client_order_id)
        if o is not None and o.is_open:
            self.clients[o.instrument_id.venue].cancel_order(o)

    def handle_event(self, event) -> None:
        self.events.append(event)
        o = self.orders.get(event.client_order_id)
        if o is None:
            return
        if isinstance(event, OrderFilled):
            key = (str(event.client_order_id), str(event.trade_id))
            if key in self._seen_trades:
                return
            self._seen_trades.add(key)
            filled = o.filled_qty + event.last_qty
            o.avg_px = ((o.avg_px or 0.0) * o.filled_qty + event.last_px * event.last_qty) / filled
            o.filled_qty = filled
            o.status = OrderStatus.FILLED if filled >= o.quantity - 1e-12 else OrderStatus.PARTIALLY_FILLED
            self.positions.on_fill(event)                       # the OMS books what it applies
        elif isinstance(event, OrderAccepted):
            if o.status is OrderStatus.SUBMITTED:
                o.status = OrderStatus.ACCEPTED
        elif isinstance(event, OrderCanceled):
            o.status = OrderStatus.CANCELED
            o.reason = event.reason
        elif isinstance(event, OrderRejected):
            o.status = OrderStatus.REJECTED
            o.reason = event.reason
        self._publish(event)

    def order(self, client_order_id) -> Optional[Order]:
        return self.orders.get(client_order_id)

    def next_order_id(self, strategy_id):
        from midfreq.core.model.identifiers import ClientOrderId
        self._n = getattr(self, "_n", 0) + 1
        return ClientOrderId(f"{strategy_id}-20261014-{self._n:04d}")

    def family(self, client_order_id) -> List[Order]:
        o = self.orders.get(client_order_id)
        return [o] if o is not None else []

    def pause_policy(self, reason) -> None:
        self.paused = reason

    def resume_policy(self) -> None:
        self.paused = None

    def family_done(self, client_order_id) -> bool:
        o = self.orders.get(client_order_id)
        return o is not None and o.is_closed

    def open_orders(self, strategy_id=None) -> List[Order]:
        return [o for o in self.orders.values() if o.is_open and (strategy_id is None or o.strategy_id == strategy_id)]

    def on_timer(self, now_ns: int) -> None:
        for o in self.open_orders():
            if now_ns - self.submitted_at.get(o.client_order_id, now_ns) >= self.wait_ns:
                self.cancel(o.client_order_id)

    def reconcile(self) -> ReconcileReport:
        self.calls.append("reconcile")
        return ReconcileReport("orders", self.clock.timestamp_ns(), len(self.open_orders()))


class StandInRefData:
    def __init__(self, venues=None):
        self.venues = venues or default_venues()
        self.lots: Dict[str, float] = {}

    def instrument(self, instrument_id):
        spec = self.venues.get(instrument_id.venue)
        inst = spec.default_instrument(str(instrument_id.symbol))
        lot = self.lots.get(str(instrument_id))
        if lot:
            import dataclasses
            inst = dataclasses.replace(inst, lot_size=lot)
        return inst

    def instruments(self, instrument_ids):
        return [self.instrument(i) for i in instrument_ids]

    def calendar(self, venue):
        from midfreq.trading.calendar import VenueCalendar
        return VenueCalendar.for_venue(venue, self.venues)

    def corporate_actions(self, instrument_id, start, end):
        return []


class StandInMarketData:
    """Bar finality, cross-sections, warm-up and persistence, as described in the module."""

    def __init__(self, msgbus, clock, venues, queue, data_clients, store_dir=None,
                 config: Optional[Mapping[str, Any]] = None):
        self.msgbus = msgbus
        self.clock = clock
        self.venues = venues
        self.queue = queue
        self.data_clients = data_clients
        self.store_dir = Path(store_dir) if store_dir else None
        cfg = dict(config or {})
        self.grace_ns = int(float(cfg.get("finality_grace_s", 30)) * 1e9)
        self.share = float(cfg.get("min_cross_section_share", 0.9))
        self.persist = bool(cfg.get("persist", True))
        self.members: Dict[str, set] = defaultdict(set)
        self.universe_of: Dict[InstrumentId, str] = {}
        self.bar_types: List[BarType] = []
        self.forming: Dict[BarType, Bar] = {}
        self.final_ts: Dict[BarType, int] = {}
        self.pending: Dict[str, Dict[int, Dict[InstrumentId, Bar]]] = defaultdict(dict)
        self.published: Dict[str, set] = defaultdict(set)
        self.session_bars: Dict[BarType, List[Bar]] = defaultdict(list)
        self.calls: List[str] = []
        self.started = self.stopped = False
        self.resubscribed = 0
        self.quotes: Dict[InstrumentId, Any] = {}
        self.fatal_stall = False

    def subscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None:
        self.calls.append("subscribe_bars")
        if bar_type not in self.bar_types:
            self.bar_types.append(bar_type)
        u = universe or str(bar_type.instrument_id)
        self.members[u].add(bar_type.instrument_id)
        self.universe_of[bar_type.instrument_id] = u
        client = self.data_clients.get(bar_type.instrument_id.venue)
        if client is not None:
            client.subscribe_bars(bar_type, self.queue.put)

    def resubscribe(self) -> int:
        self.resubscribed += 1
        return len(self.bar_types)

    def subscribe_quotes(self, instrument_id) -> None:
        self.calls.append("subscribe_quotes")

    def dispatch(self, item) -> bool:
        from midfreq.core.model.data import QuoteTick
        from midfreq.data.adapters.base import BarUpdate, StreamStatus
        if isinstance(item, BarUpdate):
            self.on_bar_update(item)
        elif isinstance(item, QuoteTick):
            self.on_quote(item)
        elif isinstance(item, StreamStatus):
            self.on_stream_status(item)
        else:
            return False
        return True

    def on_quote(self, quote) -> None:
        self.quotes[quote.instrument_id] = quote
        self.msgbus.publish(TOPIC_QUOTE.format(instrument_id=quote.instrument_id), quote)

    def on_stream_status(self, status) -> None:
        self.calls.append(f"stream:{status.state}")

    def on_bar_update(self, update) -> None:
        bar = update.bar
        bt = bar.bar_type
        if bar.ts_event <= self.final_ts.get(bt, -1):
            return
        forming = self.forming.get(bt)
        if forming is not None and bar.ts_event > forming.ts_event:
            self._finalise(forming)
        if forming is None or bar.ts_event >= forming.ts_event:
            self.forming[bt] = bar
        if update.closed:
            self._finalise(bar)
            self.forming.pop(bt, None)

    def _finalise(self, bar: Bar) -> None:
        bt = bar.bar_type
        if bar.ts_event <= self.final_ts.get(bt, -1):
            return
        self.final_ts[bt] = bar.ts_event
        self.session_bars[bt].append(bar)
        self.msgbus.publish(TOPIC_BAR.format(bar_type=bt), bar)
        u = self.universe_of.get(bt.instrument_id)
        if u is None or bar.ts_event in self.published[u]:
            return
        xs = self.pending[u].setdefault(bar.ts_event, {})
        xs[bt.instrument_id] = bar
        if len(xs) >= len(self.members[u]):
            self._publish(u, bar.ts_event, complete=True)

    def _publish(self, universe: str, ts: int, complete: bool) -> None:
        bars = self.pending[universe].pop(ts)
        self.published[universe].add(ts)
        missing = tuple(sorted(self.members[universe] - set(bars), key=str))
        self.msgbus.publish(TOPIC_CROSS_SECTION.format(universe=universe),
                            CrossSection(universe, ts, dict(bars), complete, missing))

    def on_timer(self, now_ns: int) -> None:
        self.calls.append("on_timer")
        for bt, bar in sorted(self.forming.items(), key=lambda kv: kv[1].ts_event):
            if bar.ts_event + self.grace_ns <= now_ns:
                self._finalise(bar)
                del self.forming[bt]
        for u, pending in self.pending.items():
            for ts in sorted(pending):
                if ts + self.grace_ns <= now_ns and len(pending[ts]) >= math.ceil(self.share * len(self.members[u])):
                    self._publish(u, ts, complete=len(pending[ts]) >= len(self.members[u]))

    def warm_up(self, bar_type: BarType, count: int) -> List[Bar]:
        self.calls.append("warm_up")
        if self.store_dir is None or not self.store_dir.exists():
            return []
        from midfreq.data.persistence.legacy import LegacyDataReader
        reader = LegacyDataReader(self.store_dir, self.venues)
        try:
            return reader.read_bars(bar_type.instrument_id, bar_type.spec)[-count:]
        except (FileNotFoundError, ValueError):
            return []

    def start(self) -> None:
        self.calls.append("start")
        self.started = True

    def stop(self) -> None:
        self.calls.append("stop")
        self.on_timer(self.clock.timestamp_ns())
        self.stopped = True
        if not (self.persist and self.store_dir is not None):
            return
        from midfreq.data.persistence.legacy import LegacyDataReader
        from midfreq.data.storage import DataStorage
        reader = LegacyDataReader(self.store_dir, self.venues)
        store = DataStorage(base_path=str(self.store_dir))
        for bt, bars in self.session_bars.items():
            if not bars:
                continue
            spec = self.venues.get(bt.instrument_id.venue)
            idx = spec.to_legacy_index([b.ts_event for b in bars], bt.spec)
            df = pd.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars],
                               "low": [b.low for b in bars], "close": [b.close for b in bars],
                               "volume": [b.volume for b in bars]},
                              index=pd.DatetimeIndex(idx, name="timestamp"))
            folder = reader.legacy_symbol(bt.instrument_id).replace(".", "_", 1)
            with contextlib.redirect_stdout(io.StringIO()):
                store.append_data(df, folder, bt.spec.legacy_timeframe)


class FakeDataClient(DataClient):
    def __init__(self):
        super().__init__(SimpleNamespace(name="fake"))
        self.subscribed: List[Any] = []
        self.handlers: List[Any] = []

    def subscribe_bars(self, bar_type, handler) -> None:
        self.subscribed.append(bar_type)
        self.handlers.append(handler)

    def unsubscribe_bars(self, bar_type) -> None:
        pass

    def subscribe_quotes(self, instrument_id, handler) -> None:
        pass

    def _request_bars(self, bar_type, venue, start, end):
        return []


class ScriptedVenue(ExecutionClient):
    """An execution client a test drives by hand: orders are recorded, nothing else happens
    until the test calls fill/cancel/reject (from any thread)."""

    def __init__(self, environment: str = "sim"):
        super().__init__(SimpleNamespace(name="scripted"), {})
        self._env = environment
        self.submitted: List[Order] = []
        self.cancels: List[Order] = []
        self.connected = False
        self.auto_fill_px: Optional[Dict[str, float]] = None      # fill at once at this price per instrument
        self._n = 0

    @property
    def environment(self) -> str:
        return self._env

    @property
    def account_id(self) -> AccountId:
        return AccountId("SCRIPTED")

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def submit_order(self, order: Order) -> None:
        self._n += 1
        order.tags["venue_order_id"] = f"V{self._n}"
        self.submitted.append(order)
        self._emit(OrderAccepted(order.client_order_id, order.strategy_id, order.instrument_id, 0))
        if self.auto_fill_px is not None:
            px = self.auto_fill_px.get(str(order.instrument_id), order.price)
            self.fill(order, order.quantity, px)

    def fill(self, order: Order, qty: float, px: float, trade_id: Optional[str] = None) -> None:
        self._n += 1
        self._emit(OrderFilled(order.client_order_id, order.strategy_id, order.instrument_id, 0,
                               trade_id=TradeId(trade_id or f"T{self._n}"), side=order.side, last_qty=float(qty),
                               last_px=float(px), tags={"venue_order_id": order.tags.get("venue_order_id")}))

    def cancel(self, order: Order, reason: str = "cancelled") -> None:
        self._emit(OrderCanceled(order.client_order_id, order.strategy_id, order.instrument_id, 0, reason=reason))

    def reject(self, order: Order, reason: str = "rejected") -> None:
        self._emit(OrderRejected(order.client_order_id, order.strategy_id, order.instrument_id, 0, reason=reason))

    def cancel_order(self, order: Order) -> None:
        self.cancels.append(order)
        self.cancel(order, "cancelled on request")

    def query_order(self, order: Order) -> Optional[OrderReport]:
        return None

    def open_orders(self) -> List[OrderReport]:
        return []

    def fills(self, since) -> List[FillReport]:
        return []


def standin_components(**overrides) -> Dict[str, Any]:
    """The `components=` a TradingNode needs to run on the stand-ins."""
    comps: Dict[str, Any] = {
        "refdata": StandInRefData,
        "journal": None,
        "risk": StandInRisk,
        "positions": StandInPositions,
        "oms": StandInOMS,
        "marketdata": StandInMarketData,
    }
    comps.update(overrides)
    return comps


def tiny_config_dict(symbols=("US.AAPL", "US.MSFT", "US.NVDA", "US.AMD"), mode="sim", **strategy):
    """A small cross-sectional reversal node on a few US symbols."""
    s = {"id": "xsr-test", "class": "Cross-Sectional Reversal", "account": "acct", "universe": "u",
         "capital": 100000.0, "commission_rate": 0.00005,
         "params": {"lookback": 2, "top_n": 1, "rebalance_every": 1},
         "risk_manager": {"stop_loss_pct": 0, "max_drawdown_pct": 0.15, "sizing": "equal-dollar"},
         "warmup_bars": 0}
    s.update(strategy)
    return {
        "node": {"name": "test", "mode": mode, "timeframe": "1h"},
        "accounts": {"acct": {"connector": "moomoo", "options": {"market": "US"}, "environment": "paper"}},
        "universes": {"u": {"symbols": list(symbols)}},
        "strategies": [s],
    }
