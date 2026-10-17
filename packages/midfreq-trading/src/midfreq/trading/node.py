"""
TradingNode: the live trading node's kernel. It wires the components together from the
configuration, runs their lifecycle, and owns the one thread on which all of them run.

Threads and the queue
---------------------
Connectors call back on their own threads (a venue SDK's push thread, a websocket task). Those
callbacks only `put` on the node's `NodeEventQueue` (a queue.Queue). One loop, on the thread that
called `run()`, takes the items in order and dispatches them:

    BarUpdate     -> MarketDataService.dispatch (bar finality, cross-sections, on the bus)
    QuoteTick     -> the cache, then MarketDataService.dispatch (TOPIC_QUOTE)
    StreamStatus  -> MarketDataService.dispatch and the ops log (a data stream's reconnect
                     triggers a reconciliation)
    OrderEvent    -> OMS.handle_event (which journals it, applies it, books fills in the
                     PositionKeeper and publishes it)
    ExecutionStatus -> the ops log; 'reconnected' (an execution client caught up after a
                     drop) triggers a reconciliation of orders and positions
    TimerEvent    -> bar finality, cross-sections and stall detection (MarketDataService.on_timer)
                     and the OMS's unfilled-order policy (every `timer_s`), reconciliation with
                     the venues (every `reconcile_s`, 300 s), a heartbeat (every `heartbeat_s`)

Stalls are detected by the MarketDataService (no bar update for `stall_factor` intervals of
session time: resubscribe once, then `fatal_stall`); the node writes each StreamEvent('stall')
to the session logs, as the original engine did, and stops cleanly on a fatal one.

Timers are queue items too: the loop puts each due timer on the queue and dispatches it in turn.
With a LiveClock time is the wall clock; with a TestClock (replays, tests) nothing moves until
`advance_to(ts)` sets the clock and processes what is due, so a replay is deterministic.

Lifecycle
---------
start(): load the reference data -> connect the connectors and the execution clients (their
handlers put on the queue) -> restore every strategy's state (shrink-only against the broker) and
reconcile orders and positions -> session_start -> warm up from the local store -> subscribe;
then run() until the duration ends, a stop is requested (SIGTERM/SIGINT) or the stream stalls
twice; then stop(), in the original engine's order: trading off -> cancel what is still working
-> finalise the bars whose window has elapsed -> save state -> write the session's bars to the
store -> session_end -> disconnect.

Modes
-----
`shadow`: real market data, but every account's orders go to a ShadowExecutionClient, which logs
and journals them and never sends them; records go to <records>/shadow/. `sim`: the simulated
venue (midfreq.trading.testing.sim_venue) for data and execution, records to <records>/sim/.
`paper`: the venues' paper accounts. `live`: real money, only when the account's configuration
says `environment: live` and the execution client reports 'live' itself.

Components
----------
The defaults (`DEFAULT_COMPONENTS`) are the factories of midfreq.trading.components, which build
the real classes (ReferenceDataService, OrderJournal, PositionLedger, PreTradeRiskEngine,
OrderManagementSystem, LiveMarketDataService, the simulated venue) with their real constructor
arguments from the configuration's sections. The configuration's `components:` section or the
`components=` argument override them with import paths, classes, factories or ready instances
(tests use stand-ins). A class may define `from_node(ctx, settings)`; otherwise a constructor or
factory is called with the keyword arguments it names out of those `NodeContext.offered()`
provides (ctx, clock, msgbus, queue, cache, venues, refdata, journal, risk, positions, oms,
execution clients, data clients, its own `settings` section, ...). Every component must offer
the methods of its protocol in midfreq.trading.interfaces, under those names.
"""
from __future__ import annotations

import collections
import dataclasses
import fcntl
import importlib
import inspect
import os
import queue
import signal
import threading
import time
import typing
from dataclasses import dataclass, field, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from midfreq.core.cache import Cache
from midfreq.core.common.clock import Clock, LiveClock, TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import Bar, BarSpecification, BarType, QuoteTick
from midfreq.core.model.events import OrderDenied, OrderEvent
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId, Venue, as_instrument_id
from midfreq.core.model.instruments import Instrument
from midfreq.core.model.orders import Order
from midfreq.core.paths import live_sessions_dir
from midfreq.core.venues.spec import VenueRegistry, default_venues
from midfreq.data.adapters.base import BarUpdate, ExecutionClient, PositionReport, StreamStatus
from midfreq.trading.config import (
    NodeConfig, StrategySettings, TIMEFRAMES, instrument_for_symbol, resolve_strategy_class, state_slug,
    strategy_params,
)
from midfreq.trading.interfaces import (
    TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_HEARTBEAT, TOPIC_ORDER_EVENTS, TOPIC_STREAM, Heartbeat,
    ReconcileReport, StreamEvent,
)
from midfreq.trading.ops import (
    OpsLogging, OpsSettings, describe_heartbeat, get_logger, log_context, quiet_venue_sdk_loggers,
    resident_memory_mb,
)
from midfreq.trading.shadow import ShadowExecutionClient
from midfreq.trading.strategy_host import (
    LiveLegacyAdapter, NativeStrategyRunner, NodeServices, StrategyHost, build_risk_manager, legacy_symbol_for,
)

log = get_logger("node")

DEFAULT_COMPONENTS: Dict[str, str] = {
    "refdata": "midfreq.trading.components:make_refdata",
    "journal": "midfreq.trading.components:make_journal",
    "positions": "midfreq.trading.components:make_positions",
    "risk": "midfreq.trading.components:make_risk",
    "oms": "midfreq.trading.components:make_oms",
    "marketdata": "midfreq.trading.components:make_marketdata",
    "sim_execution": "midfreq.trading.components:make_sim_execution",
    "sim_data": "midfreq.trading.components:make_sim_data",
}

# What each component must offer (midfreq.trading.interfaces), checked when the node is built.
REQUIRED_METHODS: Dict[str, Tuple[str, ...]] = {
    "oms": ("submit", "cancel", "handle_event", "order", "family", "family_done", "next_order_id", "open_orders",
            "on_timer", "reconcile", "pause_policy", "resume_policy"),
    "marketdata": ("dispatch", "subscribe_bars", "subscribe_quotes", "resubscribe", "warm_up", "start", "stop",
                   "on_timer"),
    "risk": ("check", "halt", "resume"),
    # (and the OMS's pause_policy/resume_policy, checked below)
    "positions": ("on_fill", "position", "positions", "cash", "restore", "snapshot", "reconcile"),
    "refdata": ("instrument", "instruments", "calendar"),
}

NANOS = 1_000_000_000


class NodeError(RuntimeError):
    """The node cannot start or continue (a component missing, a live account not allowed...)."""


# ── queue items ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimerEvent:
    name: str
    ts: int


@dataclass(frozen=True)
class StopRequest:
    reason: str = "requested"


@dataclass(frozen=True)
class ExecutionStatus:
    """A change in an execution client's connection (ExecutionClient.set_status_handler),
    tagged with the account it serves."""
    account_id: str
    status: StreamStatus


class NodeEventQueue:
    """The node's inbox (midfreq.trading.interfaces.EventQueue): thread-safe, first in first out."""

    def __init__(self):
        self._q: "queue.Queue[Any]" = queue.Queue()
        self.put_count = 0

    def put(self, item: Any) -> None:
        self._q.put(item)
        self.put_count += 1

    def get(self, timeout: Optional[float] = None) -> Any:
        """The next item, or None when nothing arrived within `timeout` (0: do not wait)."""
        try:
            if timeout is not None and timeout <= 0:
                return self._q.get_nowait()
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()


@dataclass
class _Timer:
    name: str
    interval_ns: int
    next_due: int


# ── building components ───────────────────────────────────────────────────────────────────────

@dataclass
class NodeContext:
    """Everything built so far, offered to the component factories."""
    config: NodeConfig
    mode: str
    clock: Clock
    msgbus: MessageBus
    queue: NodeEventQueue
    cache: Cache
    venues: VenueRegistry
    records_dir: Path
    store_dir: Optional[Path]
    session_id: str
    persist_bars: bool
    connectors: Dict[str, Any] = field(default_factory=dict)
    data_clients: Dict[Venue, Any] = field(default_factory=dict)
    execution_clients: Dict[str, ExecutionClient] = field(default_factory=dict)       # by account id
    execution_by_venue: Dict[Venue, ExecutionClient] = field(default_factory=dict)
    refdata: Any = None
    journal: Any = None
    risk: Any = None
    positions: Any = None
    oms: Any = None
    marketdata: Any = None
    alert: Callable[[str, str], None] = lambda subject, body: None
    node: Any = None                                    # the TradingNode (prices, instruments, hours)
    sim_scheduler: Any = None                           # sim mode: the simulated venue's scheduler

    def offered(self, settings: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """The keyword arguments a component's constructor may ask for, by name."""
        data_client = next(iter(self.data_clients.values()), None) if len(set(map(id, self.data_clients.values()))) == 1 else None
        out = {
            "ctx": self, "node_context": self,
            "clock": self.clock, "msgbus": self.msgbus, "bus": self.msgbus, "queue": self.queue,
            "event_queue": self.queue, "cache": self.cache, "venues": self.venues,
            "venue_registry": self.venues, "node_config": self.config, "mode": self.mode,
            "records_dir": self.records_dir, "directory": self.records_dir, "journal_dir": self.records_dir,
            "session_dir": self.records_dir, "log_dir": self.records_dir, "session_id": self.session_id,
            "store_dir": self.store_dir, "data_dir": self.store_dir, "persist": self.persist_bars,
            "persist_bars": self.persist_bars, "connectors": self.connectors, "data_clients": self.data_clients,
            "data_client": data_client, "execution_clients": self.execution_by_venue,
            "clients": self.execution_by_venue, "execution_clients_by_account": self.execution_clients,
            "refdata": self.refdata, "reference_data": self.refdata, "journal": self.journal,
            "risk": self.risk, "risk_engine": self.risk, "positions": self.positions,
            "position_keeper": self.positions, "oms": self.oms, "order_manager": self.oms,
            "marketdata": self.marketdata, "market_data": self.marketdata,
            "now_ns": self.clock.timestamp_ns, "alert": self.alert,
            "config": dict(settings or {}), "settings": dict(settings or {}),
        }
        for k, v in (settings or {}).items():
            out.setdefault(k, v)
        return out


def _import(path: str) -> Any:
    module, sep, attr = path.partition(":")
    if not sep:
        module, _, attr = path.rpartition(".")
    return getattr(importlib.import_module(module), attr)


def build_component(spec: Any, ctx: NodeContext, name: str, settings: Optional[Mapping[str, Any]] = None,
                    extra: Optional[Mapping[str, Any]] = None) -> Any:
    """Make one component from an import path, a class, a factory function or an instance."""
    obj = _import(spec) if isinstance(spec, str) else spec
    if not (inspect.isclass(obj) or inspect.isfunction(obj) or inspect.ismethod(obj)
            or type(obj).__name__ == "partial"):
        return obj                                           # a ready instance
    offered = ctx.offered(settings)
    if extra:
        offered.update(extra)
    if inspect.isclass(obj) and hasattr(obj, "from_node"):
        return obj.from_node(ctx, dict(settings or {}))
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return obj()
    kwargs, missing = {}, []
    for pname, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if pname in offered and offered[pname] is not None:
            kwargs[pname] = offered[pname]
        elif p.default is inspect.Parameter.empty:
            missing.append(pname)
    if missing:
        raise NodeError(f"component {name} ({getattr(obj, '__qualname__', obj)}) needs {missing}, "
                        f"which the node does not provide; it can provide {sorted(offered)}")
    return obj(**kwargs)


# ── the node ──────────────────────────────────────────────────────────────────────────────────

class TradingNode:
    def __init__(self, config: NodeConfig, *, mode: Optional[str] = None, clock: Optional[Clock] = None,
                 components: Optional[Mapping[str, Any]] = None, registry: Any = None,
                 venues: Optional[VenueRegistry] = None, records_dir: Optional["str | Path"] = None,
                 store_dir: Optional["str | Path"] = None, records_tz: Optional[str] = None,
                 configure_logging: bool = False, install_signal_handlers: bool = False,
                 sleep: Callable[[float], None] = time.sleep, ops_stream: Any = None):
        self.config = config
        self.mode = mode or config.node.mode
        if self.mode not in ("shadow", "sim", "paper", "live"):
            raise NodeError(f"unknown mode {self.mode!r}")
        self.clock: Clock = clock or LiveClock()
        self.test_clock = isinstance(self.clock, TestClock)
        self.venues = venues or default_venues()
        self.msgbus = MessageBus()
        self.queue = NodeEventQueue()
        self.cache = Cache()
        self._components = {**DEFAULT_COMPONENTS, **config.components, **dict(components or {})}
        self._registry = registry
        self._sleep = sleep
        self._records_tz = ZoneInfo(records_tz or config.node.records_tz) if (records_tz or config.node.records_tz) else None
        self.records_dir = Path(records_dir) if records_dir else self._default_records_dir()
        self.store_dir = Path(store_dir) if store_dir else None
        persist = config.data.persist_bars
        self.persist_bars = (self.mode in ("paper", "live")) if persist is None else bool(persist)
        started = self.local_now()
        self.session_stamp = f"{started:%Y%m%d_%H%M%S}"
        self.session_id = f"{self.session_stamp}_{config.node.name}_{os.getpid()}"
        self.order_prefix = f"N{started:%y%m%d%H%M%S}"
        self.state = "created"
        self.exit_code = 0
        self.stop_reason: Optional[str] = None
        self._stop_flag: Optional[str] = None
        self._configure_logging = configure_logging
        self._install_signals = install_signal_handlers
        self._ops_stream = ops_stream
        self.ops: Optional[OpsLogging] = None
        self.ctx: Optional[NodeContext] = None
        self.host: Optional[StrategyHost] = None
        self.oms = self.risk = self.positions = self.marketdata = self.refdata = self.journal = None
        self._account_sources: Dict[str, Any] = {}
        self.accounts_at_start: Dict[str, Optional[Dict[str, Any]]] = {}
        self._held: Deque[Any] = collections.deque()
        self._timers: Dict[str, _Timer] = {}
        self._pumping = False
        self._last_push: Dict[InstrumentId, Tuple[int, float]] = {}
        self._last_bar_at: Optional[int] = None          # clock time a closed bar last arrived
        self._last_bar_ts: Optional[int] = None          # that bar's ts_event
        self._last_update_at: Optional[int] = None       # clock time a bar push last arrived
        self._stalls = 0                                 # StreamEvent('stall') seen this session
        self.instruments: Dict[InstrumentId, Instrument] = {}
        self.bar_types: Dict[BarType, Optional[str]] = {}    # bar type -> universe
        self.dispatched = collections.Counter()
        self.errors = 0
        self.integration_notes: List[str] = []
        self._previous_signals: Dict[int, Any] = {}
        self.lifecycle: List[str] = []                       # the steps taken, in order (tests read it)

    # ── time ─────────────────────────────────────────────────────────────────────────────────
    def now_ns(self) -> int:
        return self.clock.timestamp_ns()

    def local_now(self) -> datetime:
        """The records' wall clock: this machine's local time (the original engine's
        datetime.now()), or `records_tz`'s, read from the node's clock."""
        ns = self.clock.timestamp_ns()
        if self._records_tz is None:
            return datetime.fromtimestamp(ns // NANOS).replace(microsecond=(ns // 1000) % 1_000_000)
        return pd.Timestamp(ns, unit="ns", tz="UTC").tz_convert(self._records_tz).tz_localize(None).to_pydatetime()

    def local_to_ns(self, dt: datetime) -> int:
        """A records timestamp (local_now's wall time, naive) back to UTC ns."""
        if self._records_tz is None:
            return int(dt.timestamp() * NANOS)
        return int(pd.Timestamp(dt).tz_localize(self._records_tz).value)

    def _journal_fills(self, strategy_id: StrategyId) -> List[Dict[str, Any]]:
        """The journal's applied fills of one strategy (for fills its book never received)."""
        if self.journal is None or not callable(getattr(self.journal, "load", None)):
            return []
        state = self.journal.load(int(self.config.oms.get("journal_lookback_days", 30)))
        return [f for f in getattr(state, "fills", []) if f.get("strategy_id") == str(strategy_id)]

    def _default_records_dir(self) -> Path:
        n = self.config.node
        if n.records_dir:
            base = self.config.resolve_path(n.records_dir)
            return base if self.mode in ("paper", "live") else base / self.mode
        base = live_sessions_dir()
        return base if self.mode in ("paper", "live") else base / self.mode

    def _seed_state_dir(self) -> Optional[Path]:
        """Shadow and sim start from the live state (read only) until they have their own."""
        if self.mode in ("paper", "live"):
            return None
        if self.config.node.seed_state_from:
            return self.config.resolve_path(self.config.node.seed_state_from)
        return live_sessions_dir() if self.mode == "shadow" else None

    @property
    def trading_enabled(self) -> bool:
        return self.host is not None and self.state == "running" and self.host.trading

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    def _step(self, name: str) -> None:
        self.lifecycle.append(name)
        log.debug("lifecycle: %s", name)

    def start(self) -> None:
        if self.state != "created":
            return
        self.state = "starting"
        try:
            self._install_ops()
            self._step("config")
            self._banner()
            self._build()
            self._step("connect")
            self._connect()
            self._step("reference_data")
            self._load_reference_data()
            # Orders first: fills the venue made while the node was down are journaled before the
            # strategies resume, which book whatever the journal has that their state lacks.
            self._step("reconcile_orders")
            self._reconcile_orders(reason="start-up")
            self._step("restore")
            self._open_strategies()
            self._step("reconcile")
            self._reconcile(reason="start-up")
            self._step("session_start")
            self._start_sessions()
            self._step("warm_up")
            self._warm_up()
            self._step("subscribe")
            self._subscribe()
            self._arm_timers()
            self.state = "running"
            self.host.trading_on()
            self._step("running")
            log.info("running in %s mode: %d strategies, %d instruments", self.mode, len(self.host.all),
                     len(self.instruments))
        except Exception:
            log.exception("start-up failed")
            self.exit_code = 1
            self._teardown_after_failed_start()
            raise

    def run(self, duration_min: Optional[float] = None) -> int:
        """Start (if not yet), then process the queue until the duration ends, a stop is
        requested or the stream stalls for good; then stop. Returns an exit code."""
        if self.test_clock:
            raise NodeError("run() needs a live clock; drive a TestClock with advance_to()")
        self.start()
        minutes = duration_min if duration_min is not None else self.config.node.duration_min
        deadline = self.now_ns() + int(minutes * 60 * NANOS) if minutes else None
        if self._install_signals:
            self._install_signal_handlers()
        reason = "requested"
        try:
            while True:
                if self._stop_flag:
                    reason = self._stop_flag
                    break
                now = self.now_ns()
                if deadline is not None and now >= deadline:
                    log.info("Session duration reached.")
                    reason = "duration"
                    break
                self._enqueue_due_timers(now)
                item = self._next_item(timeout=self._wait_s(now, deadline))
                if item is not None:
                    self._dispatch(item)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            reason = "interrupted"
        except Exception:
            log.exception("the node's loop failed")
            reason = "error"
            self.exit_code = 1
        finally:
            self._restore_signal_handlers()
            self.stop(reason)
        return self.exit_code

    def request_stop(self, reason: str = "requested") -> None:
        """Ask the loop to stop at its next turn (thread-safe; signal handlers use it)."""
        self._stop_flag = reason

    def process_pending(self, max_items: int = 10_000_000) -> int:
        """Run what is due now and everything on the queue (for a TestClock, or a test)."""
        self._enqueue_due_timers(self.now_ns())
        n = 0
        while n < max_items:
            item = self._next_item(timeout=0)
            if item is None:
                break
            self._dispatch(item)
            n += 1
            if self._stop_flag:
                break
        return n

    def advance_to(self, ts_ns: int) -> int:
        """TestClock only: move the clock to `ts_ns` and process what is due."""
        if not self.test_clock:
            raise NodeError("advance_to() needs a TestClock")
        if ts_ns > self.clock.timestamp_ns():
            self.clock.set_time(ts_ns)
        return self.process_pending()

    def stop(self, reason: str = "requested") -> None:
        """Shut down in the original engine's order. Safe to call more than once."""
        if self.state in ("stopping", "stopped"):
            return
        if self.state in ("created",):
            self.state = "stopped"
            return
        self.stop_reason = reason
        self.state = "stopping"
        log.info("stopping (%s)", reason)
        host = self.host
        def trading_off():
            # Nothing may send an order from here on, the OMS's re-pricing included.
            host.trading_off()
            self.risk.halt(f"session ending ({reason}): trading off", planned=True)
            self.oms.pause_policy("trading off")

        steps: List[Tuple[str, Callable[[], None]]] = [
            ("trading_off", trading_off),
            ("drain", self._drain_data),
            ("cancel_open_orders", self._cancel_open_orders),
            ("finalise", self._finalise),
        ]
        clocks: Dict[str, Optional[int]] = {}

        def save():
            clocks.update(host.save_states())

        def persist():
            self.marketdata.stop()
            self._drain_data()
            host.finalize()
            if host.clocks() != clocks:
                host.save_states()

        steps += [("save_state", save), ("persist_bars", persist), ("strategies_stop", host.stop_native),
                  ("session_end", self._end_sessions), ("summary", self._summary),
                  ("disconnect", self._disconnect)]
        for name, fn in steps:
            self._step(f"stop:{name}")
            try:
                fn()
            except Exception:
                log.exception("shutdown step %s failed; continuing", name)
                self.exit_code = 1
        self.state = "stopped"
        self._step("stopped")
        self._unlock_records()
        if self.ops is not None:
            self.ops.uninstall()

    def abandon(self) -> None:
        """Drop the node without any shutdown, as a crashed process would (replays use it)."""
        self.state = "stopped"
        self.stop_reason = "abandoned"
        self._unlock_records()                           # a dead process's lock goes with it
        if self.ops is not None:
            self.ops.uninstall()

    # ── start-up steps ───────────────────────────────────────────────────────────────────────
    def _install_ops(self) -> None:
        if not self._configure_logging:
            return
        n = self.config.node
        settings = OpsSettings(level=n.log_level, console=self.config.ops.console, json_file=self.config.ops.json_file,
                               primary_tz=n.console_tz, primary_label=n.console_tz_label,
                               secondary_tz=n.market_tz, secondary_label=n.market_tz_label,
                               email_alerts=n.email_alerts)
        self.ops = OpsLogging(settings, session_id=self.session_id, log_dir=self.records_dir,
                              file_stem=f"ops_{self.session_stamp}_{n.name}",
                              time_source=lambda: self.clock.timestamp_ns() / NANOS,
                              stream=self._ops_stream).install()

    def halt(self, reason: str) -> None:
        """The kill switch: every new order denied (risk), no re-pricing (OMS), and every working
        order cancelled. Trading stays off until resume()."""
        log.critical("HALT: %s", reason)
        self.risk.halt(reason)
        self.oms.pause_policy(f"halted: {reason}")
        for order in list(self.oms.open_orders()):
            try:
                self.oms.cancel(order.client_order_id)
            except Exception as exc:
                log.error("cancel of %s at the halt failed: %s", order.client_order_id, exc)
        self.alert("trading halted", reason)

    def resume(self) -> None:
        self.risk.resume()
        self.oms.resume_policy()
        log.warning("trading resumed")

    def alert(self, subject: str, body: str = "") -> None:
        log.error("ALERT %s: %s", subject, body)

    def _banner(self) -> None:
        n = self.config.node
        log.info("=" * 60)
        log.info("TRADING NODE %s — %s mode", n.name, self.mode.upper())
        log.info("strategies: %s", ", ".join(f"{s.id} ({s.cls})" for s in self.config.strategies))
        log.info("records: %s", self.records_dir)
        log.info("=" * 60)

    def _registry_obj(self):
        if self._registry is None:
            from midfreq.data.adapters.registry import ConnectorRegistry
            self._registry = ConnectorRegistry(self.venues)
        return self._registry

    def _connector(self, name: str) -> Any:
        from midfreq.data.adapters.registry import ConnectorRegistry
        key = ConnectorRegistry.canonical(name)
        conn = self.ctx.connectors.get(key)
        if conn is None:
            conn = self._registry_obj().create(key, self.config.connectors.get(key) or None)
            self.ctx.connectors[key] = conn
        return conn

    def _universe_instruments(self, s: StrategySettings) -> List[Tuple[str, InstrumentId]]:
        return [(sym, instrument_for_symbol(sym, self.venues)) for sym in self.config.universes[s.universe].symbols]

    def _lock_records(self) -> None:
        """One node per records folder: an exclusive lock on <records>/.node.lock, held until the
        node stops (the operating system drops it if the process dies)."""
        self.records_dir.mkdir(parents=True, exist_ok=True)
        path = self.records_dir / ".node.lock"
        f = open(path, "a+")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            holder = f.read().strip()
            f.close()
            raise NodeError(f"another node is using {self.records_dir} ({path}: {holder or 'pid unknown'}); "
                            "one node per records folder") from None
        f.seek(0)
        f.truncate()
        f.write(f"pid {os.getpid()} session {self.session_id}\n")
        f.flush()
        self._lock_file = f

    def _unlock_records(self) -> None:
        f, self._lock_file = getattr(self, "_lock_file", None), None
        if f is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            finally:
                f.close()

    def _build(self) -> None:
        cfg = self.config
        self._lock_records()
        self.ctx = ctx = NodeContext(cfg, self.mode, self.clock, self.msgbus, self.queue, self.cache, self.venues,
                                     self.records_dir, self.store_dir, self.session_id, self.persist_bars,
                                     alert=self.alert, node=self)
        comps = self._components
        # Which venues each account trades, from its strategies' universes.
        venues_of: Dict[str, set] = collections.defaultdict(set)
        all_venues: set = set()
        for s in cfg.strategies:
            for _, iid in self._universe_instruments(s):
                venues_of[s.account].add(iid.venue)
                all_venues.add(iid.venue)
                self.bar_types[BarType(iid, cfg.bar_spec(s))] = s.universe

        # Execution clients, one per account.
        for aid in sorted({s.account for s in cfg.strategies}):
            client = self._make_execution_client(aid)
            ctx.execution_clients[aid] = client
            for v in venues_of[aid]:
                if v in ctx.execution_by_venue and ctx.execution_by_venue[v] is not client:
                    raise NodeError(f"venue {v} is traded through two accounts; one account per venue")
                ctx.execution_by_venue[v] = client
            self._account_sources[aid] = self._make_account_source(aid, client)

        # Data clients, one per venue.
        for v in sorted(all_venues, key=str):
            ctx.data_clients[v] = self._make_data_client(v)

        # The components, in dependency order: risk needs the books, the OMS needs both and the
        # journal, the market data service the reference data's calendars.
        self.refdata = ctx.refdata = build_component(comps["refdata"], ctx, "refdata", cfg.refdata)
        self.journal = ctx.journal = build_component(comps["journal"], ctx, "journal", cfg.journal)
        self.positions = ctx.positions = build_component(comps["positions"], ctx, "positions", cfg.positions)
        self.risk = ctx.risk = build_component(comps["risk"], ctx, "risk", cfg.risk)
        self.oms = ctx.oms = build_component(comps["oms"], ctx, "oms", cfg.oms)
        md_settings = {"persist": self.persist_bars, **cfg.marketdata}
        self.marketdata = ctx.marketdata = build_component(comps["marketdata"], ctx, "marketdata", md_settings)
        for name, obj in (("refdata", self.refdata), ("positions", self.positions), ("risk", self.risk),
                          ("oms", self.oms), ("marketdata", self.marketdata)):
            absent = [m for m in REQUIRED_METHODS[name] if not callable(getattr(obj, m, None))]
            if absent:
                raise NodeError(f"component {name} ({type(obj).__name__}) lacks {absent} "
                                "(midfreq.trading.interfaces)")
        # Closed bars: into the cache, and the heartbeat's stream lag. Stall events: the session logs.
        for bt in self.bar_types:
            self.msgbus.subscribe(TOPIC_BAR.format(bar_type=bt), self._on_closed_bar)
        self.msgbus.subscribe(TOPIC_CROSS_SECTION.format(universe="*"), self._on_cross_section_seen)
        self.msgbus.subscribe(TOPIC_STREAM, self._on_stream_event)

        services = NodeServices(
            clock=self.clock, msgbus=self.msgbus, cache=self.cache, venues=self.venues,
            submit=self._submit, cancel=self._cancel, wait_until=self.wait_until,
            latest_price=self.latest_price, quote=self.cache.quote, instrument=self.instrument,
            local_now=self.local_now, warm_up=self._warm_up_bars, order_prefix=self.order_prefix,
            alert=self.alert, market_open=self.market_open, order=self.oms.order,
            family_done=self.oms.family_done, next_order_id=self.oms.next_order_id,
            journal_fills=self._journal_fills, local_to_ns=self.local_to_ns)
        self.host = StrategyHost(services)
        self._services = services

    def _make_execution_client(self, aid: str) -> ExecutionClient:
        acc = self.config.accounts[aid]
        override = self._components.get(f"execution_client:{aid}") or self._components.get("execution_client")
        if self.mode == "shadow":
            sh = self.config.shadow
            return ShadowExecutionClient(aid, fill_mode=sh.get("fill_mode", "none"), price_source=self._quote_triplet,
                                         now_ns=self.clock.timestamp_ns, journal_dir=self.records_dir,
                                         commission_rate=float(sh.get("commission_rate", 0.0)))
        if override is not None:
            client = build_component(override, self.ctx, f"execution_client:{aid}", acc.options,
                                     {"account": acc, "account_id": aid})
        elif self.mode == "sim":
            client = build_component(self._components["sim_execution"], self.ctx, "sim_execution", acc.options,
                                     {"account": acc, "account_id": aid})
        else:
            client = self._connector(acc.connector).execution_client(**acc.options)
        env = getattr(client, "environment", None)
        if self.mode == "paper" and env != "paper":
            raise NodeError(f"account {aid}: paper mode but the execution client reports environment {env!r}; refusing")
        if self.mode == "live" and (acc.environment != "live" or env != "live"):
            raise NodeError(f"account {aid}: live mode needs `environment: live` in the node configuration "
                            f"(have {acc.environment!r}) and a client that reports 'live' (have {env!r})")
        if self.mode == "sim" and env == "live":
            raise NodeError(f"account {aid}: sim mode but the execution client reports 'live'; refusing")
        return client

    def _make_account_source(self, aid: str, client: Any) -> Any:
        override = self._components.get(f"account_source:{aid}")
        if override is not None:
            return build_component(override, self.ctx, f"account_source:{aid}", {})
        if callable(getattr(client, "snapshot", None)) and callable(getattr(client, "positions", None)):
            return client
        if self.mode == "sim":
            return None
        if self.mode == "shadow" and not self.config.shadow.get("read_account", False):
            # The shadow ledger is not the broker's: reconciling against the real account would
            # erase its simulated holdings. Its capital is the configured cap.
            return None
        acc = self.config.accounts[aid]
        env = getattr(client, "environment", None)
        try:
            factory = self._connector(acc.connector).account_client
            if acc.account_options is not None:
                kwargs = dict(acc.account_options)
            else:
                # The execution client's options, as far as the account client takes them (an
                # execution option such as ccxt's `updates` must not make it fail).
                accepted = inspect.signature(factory).parameters
                kwargs = {k: v for k, v in acc.options.items() if k in accepted}
            if "environment" in inspect.signature(factory).parameters and "environment" not in kwargs:
                kwargs["environment"] = env
            src = factory(**kwargs)
        except Exception as exc:
            self.integration_notes.append(f"account {aid}: no read-only account client ({exc})")
            log.error("account %s: no account client (%s); positions and cash are not reconciled with the "
                      "broker; the capital cap and the state file are the only record", aid, exc)
            return None
        src_env = getattr(src, "environment", None)
        if src_env is not None and env is not None and src_env != env:
            log.error("account %s: the account client reads the %s account but orders go to the %s one; "
                      "refusing to reconcile against it", aid, src_env, env)
            self.integration_notes.append(f"account {aid}: account client is {src_env}, execution {env}; not used")
            try:
                src.close()
            except Exception:
                pass
            return None
        return src

    def _make_data_client(self, venue: Venue) -> Any:
        override = self._components.get(f"data_client:{venue}") or self._components.get("data_client")
        if override is not None:
            client = build_component(override, self.ctx, f"data_client:{venue}", {}, {"venue": venue})
        elif self.mode == "sim":
            client = build_component(self._components["sim_data"], self.ctx, "sim_data", {}, {"venue": venue})
        else:
            name = self.config.data.connector or self.venues.get(venue).default_connector
            client = self._connector(name).data_client
        # The MarketDataService gives each data client its stream handlers (they only enqueue).
        return client

    def _connect(self) -> None:
        for name, conn in self.ctx.connectors.items():
            log.info("connecting %s", name)
            conn.connect()
        for aid, client in self.ctx.execution_clients.items():
            client.set_event_handler(self.queue.put)
            set_status = getattr(client, "set_status_handler", None)
            if callable(set_status):
                set_status(lambda status, aid=aid: self.queue.put(ExecutionStatus(aid, status)))
            client.connect()
            log.info("account %s: %s execution (%s)", aid, getattr(client, "environment", "?"), type(client).__name__)
        quiet_venue_sdk_loggers()

    def _load_reference_data(self) -> None:
        ids = sorted({bt.instrument_id for bt in self.bar_types}, key=str)
        try:
            self.refdata.instruments(ids)                 # one provider call per venue
        except Exception as exc:
            log.warning("reference data: bulk load failed (%s); loading one by one", exc)
        failed = []
        for iid in ids:
            try:
                inst = self.refdata.instrument(iid)
            except Exception:
                inst = None
            if inst is None:
                failed.append(str(iid))
                inst = self.venues.get(iid.venue).default_instrument(str(iid.symbol))
            self.instruments[iid] = inst
            self.cache.add_instrument(inst)
        if failed:
            log.warning("no instrument definition for %d instruments (%s%s): using the venues' defaults; orders "
                        "may be refused as odd lots", len(failed), ", ".join(failed[:5]), "..." if len(failed) > 5 else "")
        lots = {str(i): inst.lot_size for i, inst in self.instruments.items() if inst.lot_size and inst.lot_size > 1}
        if lots:
            log.info("board lots: %s", lots)

    def _open_strategies(self) -> None:
        cfg = self.config
        seed = self._seed_state_dir()
        for aid in sorted({s.account for s in cfg.strategies}):
            self.accounts_at_start[aid] = self.account_snapshot(aid)
        for s in cfg.strategies:
            spec = cfg.bar_spec(s)
            pairs = self._universe_instruments(s)
            if s.kind == "native":
                self._open_native(s, spec, pairs)
                continue
            cls, _ = resolve_strategy_class(s.cls)
            if not s.state_name:
                if self.mode in ("paper", "live"):
                    raise NodeError(f"strategy {s.id}: {self.mode} mode needs an explicit state_name (its "
                                    "state_<name>.json); refusing to guess one")
                s = dataclasses.replace(s, state_name=state_slug(s, cls, self.mode))
            params = strategy_params(s)
            venue_spec = self.venues.get(pairs[0][1].venue)
            commission = s.commission_rate if s.commission_rate is not None else float(venue_spec.fees.get("rate", 0.0))
            adapter = LiveLegacyAdapter(
                s, self._services, strategy_class=cls, params=params, symbols=[p[0] for p in pairs],
                instrument_ids=dict(pairs), bar_spec=spec, commission_rate=commission, records_dir=self.records_dir,
                seed_state_dir=seed, risk_manager=build_risk_manager(s.risk_manager, params), universe=s.universe)
            account = self.accounts_at_start.get(s.account)
            broker, _ = self.broker_positions(s.account)
            with log_context(strategy=s.id):
                adapter.open(account, broker)
            self.host.add_legacy(adapter)
            self._seed_position_keeper(adapter)

    def _open_native(self, s: StrategySettings, spec: BarSpecification, pairs) -> None:
        cls = resolve_strategy_class(s.cls)[0]
        params = dict(s.params)
        cfg_cls = None
        try:
            hints = typing.get_type_hints(cls.__init__)
            cfg_cls = hints.get("config")
        except Exception:
            cfg_cls = None
        if cfg_cls is not None and is_dataclass(cfg_cls):
            fields = getattr(cfg_cls, "__dataclass_fields__", {})
            if "bar_types" in fields and "bar_types" not in params:
                params["bar_types"] = [str(BarType(iid, spec)) for _, iid in pairs]
            if "instrument_ids" in fields and "instrument_ids" not in params:
                params["instrument_ids"] = [str(iid) for _, iid in pairs]
            strategy = cls(cfg_cls(strategy_id=s.id, **params))
        else:
            strategy = cls(**params)
        currency = self.venues.get(pairs[0][1].venue).currency
        runner = NativeStrategyRunner(strategy, s, self._services, [iid for _, iid in pairs], spec, currency)
        self.host.add_native(runner, universe=s.universe)

    def _seed_position_keeper(self, adapter: LiveLegacyAdapter) -> None:
        """Make the PositionKeeper's book of a candle strategy what its own portfolio holds (the
        strategy's book under the original engine's rules: resumed shrink-only, cash bounded by
        the broker's under the cap), keeping the book's running totals. Called at start-up and
        after each reconciliation of the strategy; between them both book the same fills."""
        positions = {adapter.instrument_for[sym]: (p["qty"], p["entry_price"])
                     for sym, p in adapter.portfolio.positions.items()}
        cash = adapter.portfolio.cash
        mine = self.positions.positions(adapter.id)
        if self.positions.cash(adapter.id) == cash and mine == {i: q for i, (q, _) in positions.items()}:
            return
        snap = self.positions.snapshot(adapter.id)
        self.positions.restore(adapter.id, cash, positions, capital=adapter.capital_cap,
                               realized_pnl=snap.get("realized_pnl", 0.0), commissions=snap.get("commissions", 0.0),
                               dividends=snap.get("dividends", 0.0),
                               corporate_actions=snap.get("corporate_actions", ()))

    def _start_sessions(self) -> None:
        for a in self.host.legacy:
            tf = a.settings.timeframe or self.config.node.timeframe
            label = tf if tf in TIMEFRAMES else (a.bar_spec.legacy_timeframe or str(a.bar_spec))
            with log_context(strategy=str(a.id)):
                a.start_session(self.accounts_at_start.get(a.settings.account), label)
        for r in self.host.native:
            r.start()

    def _warm_up_bars(self, bar_type: BarType, count: int) -> List[Bar]:
        return list(self.marketdata.warm_up(bar_type, count) or [])

    def _warm_up(self) -> None:
        for a in self.host.legacy:
            with log_context(strategy=str(a.id)):
                a.warm_up(a.settings.warmup_bars)
        for r in self.host.native:
            r.warm_up(r.settings.warmup_bars)

    def _subscribe(self) -> None:
        self.host.subscribe()
        md = self.marketdata
        for bt, universe in self.bar_types.items():
            md.subscribe_bars(bt, universe)
        if self.config.data.quotes or any(s.pricing.rule in ("quote", "mid") for s in self.config.strategies):
            for iid in sorted({bt.instrument_id for bt in self.bar_types}, key=str):
                md.subscribe_quotes(iid)
        md.start()
        self._last_bar_at = self._last_update_at = self.now_ns()   # the stream's clocks start now

    def _arm_timers(self) -> None:
        n, now = self.config.node, self.now_ns()
        for name, seconds in (("timer", n.timer_s), ("reconcile", n.reconcile_s), ("heartbeat", n.heartbeat_s)):
            interval = int(float(seconds) * NANOS)
            self._timers[name] = _Timer(name, interval, now + interval)

    def _teardown_after_failed_start(self) -> None:
        try:
            self._disconnect()
        except Exception:
            pass
        self._unlock_records()
        self.state = "stopped"
        if self.ops is not None:
            self.ops.uninstall()

    # ── the loop ─────────────────────────────────────────────────────────────────────────────
    def _wait_s(self, now: int, deadline: Optional[int]) -> float:
        nxt = min((t.next_due for t in self._timers.values()), default=now + NANOS)
        if deadline is not None:
            nxt = min(nxt, deadline)
        return max(0.01, min(0.5, (nxt - now) / NANOS))

    def _next_item(self, timeout: float) -> Any:
        if self._held:
            return self._held.popleft()
        return self.queue.get(timeout=timeout)

    def _enqueue_due_timers(self, now: int) -> None:
        for t in self._timers.values():
            if now >= t.next_due:
                self.queue.put(TimerEvent(t.name, now))
                while t.next_due <= now:
                    t.next_due += t.interval_ns

    def _dispatch(self, item: Any) -> None:
        kind = type(item).__name__
        self.dispatched[kind] += 1
        try:
            if isinstance(item, BarUpdate):
                self._on_bar_update(item)
            elif isinstance(item, QuoteTick):
                self._on_quote(item)
            elif isinstance(item, OrderEvent):
                self._on_order_event(item)
            elif isinstance(item, StreamStatus):
                self._on_stream_status(item)
            elif isinstance(item, ExecutionStatus):
                self._on_execution_status(item)
            elif isinstance(item, TimerEvent):
                self._on_timer(item)
            elif isinstance(item, StopRequest):
                self._stop_flag = item.reason
            elif callable(item):
                item()
            else:
                log.warning("unknown item on the queue: %r", item)
        except Exception:
            self.errors += 1
            log.exception("handling %s failed", kind)

    def _on_bar_update(self, update: BarUpdate) -> None:
        self._last_update_at = self.now_ns()
        self.marketdata.dispatch(update)
        # The latest pushed price is updated after the MarketDataService has seen the push, as
        # the original engine updated its forming candle after finalising the previous one.
        bar = update.bar
        prev = self._last_push.get(bar.bar_type.instrument_id)
        if prev is None or bar.ts_event >= prev[0]:
            self._last_push[bar.bar_type.instrument_id] = (bar.ts_event, bar.close)

    def _on_quote(self, quote: QuoteTick) -> None:
        self.cache.add_quote(quote)
        self.marketdata.dispatch(quote)
        for client in self.ctx.execution_clients.values():
            if isinstance(client, ShadowExecutionClient):
                client.on_quote(quote.instrument_id)

    def _on_order_event(self, event: OrderEvent) -> None:
        with log_context(client_order_id=str(event.client_order_id), strategy=str(event.strategy_id)):
            self.oms.handle_event(event)

    def _on_stream_status(self, status: StreamStatus) -> None:
        """A data stream's connection changed (the MarketDataService publishes it on TOPIC_STREAM).
        After a reconnect the venue may have filled or cancelled orders unseen: reconcile."""
        self.marketdata.dispatch(status)
        level = {"connected": 20, "reconnected": 30, "disconnected": 40, "error": 40}.get(status.state, 30)
        log.log(level, "stream %s: %s", status.state, status.detail)
        if status.state == "reconnected":
            self._reconcile(reason="reconnected")

    def _on_execution_status(self, item: ExecutionStatus) -> None:
        """An execution client's connection changed. 'reconnected' comes once it has caught up on
        what it missed; the OMS and the books are then reconciled with the venue."""
        st = item.status
        level = {"connected": 20, "reconnected": 30, "disconnected": 40, "error": 40}.get(st.state, 30)
        log.log(level, "account %s execution %s: %s", item.account_id, st.state, st.detail)
        if st.state == "reconnected":
            self._reconcile(reason=f"account {item.account_id} reconnected")

    def _on_timer(self, timer: TimerEvent) -> None:
        now = self.now_ns()
        if timer.name == "timer":
            if self.state == "running":
                self.marketdata.on_timer(now)
                self.host.on_timer()
            self.oms.on_timer(now)
            if self.state == "running" and self.config.node.stall_check and getattr(self.marketdata, "fatal_stall", False):
                if not self._stop_flag:
                    log.error("STALL: the market data stream stalled again after resubscribing — stopping cleanly")
                self._stop_flag = self._stop_flag or "stall"
        elif timer.name == "reconcile":
            self._reconcile(reason="periodic")
        elif timer.name == "heartbeat":
            self._heartbeat(now)

    # ── data the node keeps ──────────────────────────────────────────────────────────────────
    def _on_closed_bar(self, bar: Bar) -> None:
        if isinstance(bar, Bar):
            self.cache.add_bar(bar)
        self._last_bar_at = self.now_ns()
        self._last_bar_ts = bar.ts_event if self._last_bar_ts is None else max(self._last_bar_ts, bar.ts_event)

    def _on_cross_section_seen(self, xs: Any) -> None:
        self._last_bar_at = self.now_ns()

    def _on_stream_event(self, event: StreamEvent) -> None:
        """The MarketDataService's stall reports, written to each candle strategy's session log as
        the original engine wrote them (minutes since the stream last delivered a bar, and whether
        it had been resubscribed before). The minutes are wall-clock time since the last push; the
        service's own count, in the event's detail, leaves out the hours the market is shut."""
        if not isinstance(event, StreamEvent) or event.kind != "stall" or self.state != "running":
            return
        since = self._last_update_at if self._last_update_at is not None else event.ts_event
        silent = round(max(0, self.now_ns() - since) / NANOS / 60, 1)
        resubscribed_before = self._stalls > 0
        self._stalls += 1
        for a in self.host.legacy:
            if a.session_log is not None:
                a.session_log.stall(silent, resubscribed_before, self.local_now())
        if event.detail == "fatal" and self.config.node.stall_check:
            log.error("STALL: no bar for %.0f min after resubscribing — stopping cleanly", silent)
            self._stop_flag = self._stop_flag or "stall"
        else:
            log.warning("STALL: no bar for %.0f min — %s", silent, event.detail)

    def price_snapshot(self, instrument_id: InstrumentId) -> Dict[str, Optional[float]]:
        """{'bid', 'ask', 'last'} for re-pricing an order (the OMS's price source)."""
        q = self.cache.quote(instrument_id)
        return {"bid": q.bid if q else None, "ask": q.ask if q else None, "last": self.latest_price(instrument_id)}

    def latest_price(self, instrument_id: InstrumentId) -> Optional[float]:
        """The latest pushed price (the forming bar's close), as the original engine priced orders."""
        last = self._last_push.get(instrument_id)
        return last[1] if last is not None else None

    def _quote_triplet(self, instrument_id: InstrumentId):
        q = self.cache.quote(instrument_id)
        last = self.latest_price(instrument_id)
        return (q.bid if q else None, q.ask if q else None, last)

    def instrument(self, instrument_id: InstrumentId) -> Optional[Instrument]:
        inst = self.instruments.get(instrument_id)
        if inst is None and self.refdata is not None:
            try:
                inst = self.refdata.instrument(instrument_id)
            except Exception:
                inst = None
        return inst

    # ── orders ───────────────────────────────────────────────────────────────────────────────
    def _submit(self, order: Order) -> None:
        if self.state != "running" or not self.host.trading:
            # Never reached by the adapters (they stop creating orders first); a guard all the same.
            log.error("order %s refused: trading is off", order.client_order_id)
            self.msgbus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id),
                                OrderDenied(order.client_order_id, order.strategy_id, order.instrument_id,
                                            self.now_ns(), reason="trading is off"))
            return
        self.cache.add_order(order)
        self.oms.submit(order)

    def _cancel(self, client_order_id: ClientOrderId) -> None:
        self.oms.cancel(client_order_id)

    def wait_until(self, predicate: Callable[[], bool], timeout_s: float,
                   on_timeout: Optional[Callable[[], None]] = None) -> bool:
        """Process order events (and the OMS's timers) until `predicate()` holds, holding every
        other item for later. After `timeout_s` calls `on_timeout` (a cancel) and waits up to the
        same again for its outcome. True if the predicate held."""
        if predicate():
            return True
        if self._pumping:
            return predicate()
        self._pumping = True
        try:
            deadline = self.now_ns() + int(timeout_s * NANOS)
            gave_up = False
            last_timer = self.now_ns()
            while True:
                self._pump_order_events()
                if predicate():
                    return True
                now = self.now_ns()
                if self._stop_flag and not gave_up and on_timeout is not None:
                    # A stop was requested (SIGTERM): cancel what is left now, and wait only a
                    # few seconds for the cancel's outcome.
                    log.warning("stop requested (%s) while waiting for an order: cancelling it", self._stop_flag)
                    on_timeout()
                    gave_up = True
                    deadline = min(deadline, now + 5 * NANOS)
                    continue
                if now >= deadline:
                    if gave_up or on_timeout is None:
                        return predicate()
                    on_timeout()
                    gave_up = True
                    deadline = now + int(max(timeout_s / 2.0, 5.0) * NANOS)
                    continue
                if self.test_clock:
                    self.clock.set_time(min(deadline, now + NANOS))
                    self.oms.on_timer(self.now_ns())
                else:
                    item = self.queue.get(timeout=0.2)
                    if item is not None:
                        if isinstance(item, OrderEvent):
                            self._dispatch(item)
                        else:
                            self._held.append(item)
                    now = self.now_ns()
                    if now - last_timer >= NANOS:
                        self.oms.on_timer(now)
                        last_timer = now
        finally:
            self._pumping = False

    def _pump_order_events(self) -> None:
        while True:
            item = self.queue.get(timeout=0)
            if item is None:
                return
            if isinstance(item, OrderEvent):
                self._dispatch(item)
            else:
                self._held.append(item)

    def _cancel_open_orders(self) -> None:
        try:
            open_orders = list(self.oms.open_orders())
        except Exception as exc:
            log.warning("cannot list open orders at shutdown: %s", exc)
            return
        if not open_orders:
            return
        log.warning("cancelling %d open orders before shutdown", len(open_orders))
        for o in open_orders:
            try:
                self.oms.cancel(o.client_order_id)
            except Exception as exc:
                log.error("cancel of %s failed: %s", o.client_order_id, exc)
        wait = float(self.config.node.stop_order_wait_s)

        def none_open():
            try:
                return not self.oms.open_orders()
            except Exception:
                return True
        if not self.wait_until(none_open, wait):
            log.error("%d orders still open at shutdown", len(self.oms.open_orders()))

    # ── stopping steps ───────────────────────────────────────────────────────────────────────
    def _drain_data(self) -> None:
        """Process everything already received (trading is off by now)."""
        for _ in range(10_000_000):
            item = self._next_item(timeout=0)
            if item is None:
                return
            if isinstance(item, (StopRequest, TimerEvent)):
                continue
            self._dispatch(item)

    def _finalise(self) -> None:
        """Bars whose window has elapsed are final now: publish them and their cross-sections,
        then feed the strategies what never made a cross-section."""
        self.marketdata.on_timer(self.now_ns())
        self._drain_data()
        self.host.finalize()

    def _end_sessions(self) -> None:
        cache: Dict[str, tuple] = {}
        for a in self.host.legacy:
            aid = a.settings.account
            if aid not in cache:
                cache[aid] = (self.account_snapshot(aid), self.broker_positions(aid, attempts=1)[0])
            account, positions = cache[aid]
            a.end_session(account or {}, positions or {})

    def _summary(self) -> None:
        log.info("=" * 60)
        log.info("SESSION SUMMARY")
        log.info("=" * 60)
        for a in self.host.legacy:
            log.info("%s: candles processed %d, orders sent %d, trades booked %d, clock %s, held %s", a.id,
                     a.candles_processed, a.orders_sent, len(a.portfolio.trade_history), a.cross_section_count,
                     a.positions() or "none")
        for r in self.host.native:
            log.info("%s: orders sent %d", r.id, r.orders_sent)
        for aid in self.ctx.execution_clients:
            acc = self.account_snapshot(aid)
            if acc:
                log.info("account %s: cash=%s total=%s", aid, f"{acc.get('cash', 0):,.2f}", f"{acc.get('total_assets', 0):,.2f}")
        for a in self.host.legacy:
            if a.session_log is not None:
                log.info("Full session log:  %s", a.session_log.path)
        if self.integration_notes:
            for note in self.integration_notes:
                log.info("integration note: %s", note)

    def _disconnect(self) -> None:
        if self.ctx is None:
            return
        for aid, client in self.ctx.execution_clients.items():
            try:
                client.disconnect()
            except Exception as exc:
                log.warning("disconnect %s failed: %s", aid, exc)
        for src in self._account_sources.values():
            if src is not None and src not in self.ctx.execution_clients.values():
                try:
                    src.close()
                except Exception:
                    pass
        for name, conn in self.ctx.connectors.items():
            try:
                conn.close()
            except Exception as exc:
                log.warning("close %s failed: %s", name, exc)

    # ── the broker's view ────────────────────────────────────────────────────────────────────
    def account_snapshot(self, account_id: str) -> Optional[Dict[str, Any]]:
        """{'cash', 'total_assets', 'market_value'} as the venue reports the account, or None."""
        src = self._account_sources.get(account_id)
        if src is None:
            return None
        try:
            snap = src.snapshot()
        except Exception as exc:
            log.warning("account %s: snapshot failed (%s)", account_id, exc)
            return None
        if snap is None:
            return None
        if isinstance(snap, Mapping):
            return dict(snap)
        return {"cash": float(snap.cash), "total_assets": float(snap.total_assets),
                "market_value": float(snap.market_value)}

    def broker_positions(self, account_id: str, attempts: int = 3, wait_s: float = 2.0
                         ) -> Tuple[Optional[Dict[str, Dict[str, float]]], Optional[List[PositionReport]]]:
        """The account's holdings as {symbol: {'qty', 'entry_price'}} (the state rules' form) and
        as PositionReports; (None, None) when they could not be read (never 'holds nothing')."""
        src = self._account_sources.get(account_id)
        if src is None:
            return None, None
        for i in range(max(1, attempts)):
            try:
                reports = src.positions()
            except Exception as exc:
                reports = None
                log.warning("account %s: position query failed (%s)", account_id, exc)
            if reports is not None and not any(r.quantity for r in reports):
                snap = self.account_snapshot(account_id)
                if snap and float(snap.get("market_value") or 0.0) > 0:
                    # "No positions" from an account the broker values at more than nothing is a
                    # failed read, never "holds nothing" (it would wipe every claim).
                    log.warning("account %s: no positions listed but the account holds %s in securities; "
                                "treated as a failed read", account_id, f"{float(snap['market_value']):,.2f}")
                    reports = None
            if reports is not None:
                out: Dict[str, Dict[str, float]] = {}
                for r in reports:
                    if r.quantity > 0:
                        ap = r.average_price
                        out[self._legacy_name(r)] = {"qty": float(r.quantity),
                                                     "entry_price": float(ap) if ap is not None and ap == ap else 0.0}
                return out, list(reports)
            if i < attempts - 1 and not self.test_clock:
                self._sleep(wait_s)
        return None, None

    def _legacy_name(self, report: PositionReport) -> str:
        if report.instrument_id:
            try:
                return legacy_symbol_for(as_instrument_id(report.instrument_id), self.venues)
            except Exception:
                pass
        return report.symbol

    def _report_venue(self, report: PositionReport, account_id: str) -> Optional[Venue]:
        if report.instrument_id:
            try:
                return as_instrument_id(report.instrument_id).venue
            except Exception:
                return None
        try:
            return instrument_for_symbol(report.symbol, self.venues).venue
        except Exception:
            return None

    def _reconcile(self, reason: str = "periodic") -> None:
        """The OMS against the venues' orders and fills; then, per account, each candle strategy
        against the broker (the original engine's shrink-only sync_with_broker, which also bounds
        its cash), its PositionKeeper book made the same, and the other strategies' books
        reconciled shrink-only by the PositionKeeper."""
        if self.oms is None:
            return
        if reason != "start-up":                         # at start-up it ran before the strategies opened
            self._reconcile_orders(reason)
        legacy_ids = {str(a.id) for a in self.host.legacy}
        others = [s.id for s in self.config.strategies if s.id not in legacy_ids]
        for aid in self.ctx.execution_clients:
            account = self.account_snapshot(aid) if reason != "start-up" else self.accounts_at_start.get(aid)
            legacy, reports = self.broker_positions(aid, attempts=1)
            if reason != "start-up":
                for a in self.host.legacy:
                    if a.settings.account == aid:
                        with log_context(strategy=str(a.id)):
                            a.reconcile(account, legacy)
                        self._seed_position_keeper(a)
            mine = [sid for sid in others if self.config.strategy(sid).account == aid]
            if reports is None or not mine:
                continue
            by_venue: Dict[Venue, List[PositionReport]] = collections.defaultdict(list)
            for r in reports:
                v = self._report_venue(r, aid)
                if v is not None:
                    by_venue[v].append(r)
            for v in {bt.instrument_id.venue for bt in self.bar_types}:
                if self.ctx.execution_by_venue.get(v) is not self.ctx.execution_clients[aid]:
                    continue
                try:
                    self._log_report(self.positions.reconcile(v, by_venue.get(v, []), strategy_ids=mine), reason)
                except Exception as exc:
                    log.error("position reconciliation for %s failed (%s): %s", v, reason, exc)

    def _reconcile_orders(self, reason: str) -> None:
        try:
            report = self.oms.reconcile()
            self._log_report(report, reason)
        except Exception as exc:
            log.error("order reconciliation failed (%s): %s", reason, exc)

    def _log_report(self, report: Any, reason: str) -> None:
        if not isinstance(report, ReconcileReport):
            return
        for adj in report.adjustments:
            log.warning("reconcile %s (%s): %s", report.source, reason, adj)
        for u in report.unexplained:
            log.error("reconcile %s (%s): unexplained: %s", report.source, reason, u)

    def _heartbeat(self, now: int) -> None:
        try:
            open_orders = len(self.oms.open_orders())
        except Exception:
            open_orders = -1
        lag = (now - self._last_bar_at) / NANOS if self._last_bar_at is not None else None
        hb = Heartbeat(now, self._last_bar_ts, lag, open_orders, self.host.positions(), resident_memory_mb())
        self.msgbus.publish(TOPIC_HEARTBEAT, hb)
        log.info(describe_heartbeat(hb))

    def _calendar(self, venue: Venue) -> Any:
        if self.refdata is None or not callable(getattr(self.refdata, "calendar", None)):
            return None
        try:
            return self.refdata.calendar(venue)
        except Exception:
            return None

    def market_open(self, venue: Venue, now_ns: Optional[int] = None, grace_s: float = 0.0) -> bool:
        """Whether `venue` is in a trading session now: its sessions in config/venues.json (lunch
        breaks included), and, where the reference data has a calendar, its holidays and early
        closes. `grace_s` extends each session's end."""
        now = self.now_ns() if now_ns is None else now_ns
        spec = self.venues.get(venue)
        if spec.is_24_7:
            return True
        ts = pd.Timestamp(now, unit="ns", tz="UTC")
        local = ts.tz_convert(spec.timezone).tz_localize(None)
        grace = pd.Timedelta(seconds=grace_s)
        close_utc = None
        cal = self._calendar(venue)
        session = "unknown"
        if cal is not None:
            try:
                session = cal.session(local.date())
            except Exception:
                session = "unknown"
            if session is None:
                return False
            if session != "unknown":
                close_utc = pd.Timestamp(session[1])
                close_utc = close_utc.tz_localize("UTC") if close_utc.tzinfo is None else close_utc.tz_convert("UTC")
        if session == "unknown" and local.weekday() not in spec.weekdays:
            return False
        tod = local - local.normalize()
        in_session = any(s.open_offset <= tod < s.close_offset + grace for s in spec.sessions)
        if in_session and close_utc is not None and ts >= close_utc + grace:
            return False
        return in_session

    # ── signals ──────────────────────────────────────────────────────────────────────────────
    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum, frame):
            # Only a flag: taking the queue's lock here could deadlock with the loop's get().
            self._stop_flag = f"signal {signal.Signals(signum).name}"
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._previous_signals[sig] = signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def _restore_signal_handlers(self) -> None:
        for sig, previous in self._previous_signals.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError):
                pass
        self._previous_signals.clear()
