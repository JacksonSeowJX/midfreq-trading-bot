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

Code layout
-----------
TradingNode is one class, composed of mixins that share its state, one module each: `clock` (time
and timers), `wiring` (the records folder's lock, the components, an execution client and an
account source per account, a data client per venue), `startup` (the other start-up steps),
`market_state` (what the node keeps of the market: closed bars, the stream's clocks and stalls,
prices, instruments), `orders` (submitting, cancelling and waiting on orders), `shutdown` (the
stopping steps), `broker_view` (the account and positions as the broker reports them,
reconciliation, the heartbeat, market hours) and `signals`. The queue and its items are in
`event_queue`; the NodeContext and the building of components in `context`. This module keeps the
construction, the lifecycle and the loop with its dispatch, and every name it has always offered.
"""
from __future__ import annotations

import collections
import os
import time
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from midfreq.core.cache import Cache
from midfreq.core.common.clock import Clock, LiveClock, TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import BarType, QuoteTick
from midfreq.core.model.events import OrderEvent
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.model.instruments import Instrument
from midfreq.core.venues.spec import VenueRegistry, default_venues
from midfreq.data.adapters.base import BarUpdate, StreamStatus
from midfreq.trading.config.settings import NodeConfig
from midfreq.trading.modes.shadow import ShadowExecutionClient
from midfreq.trading.node.broker_view import BrokerViewMixin
from midfreq.trading.node.clock import NANOS, ClockMixin, TimerEvent, _Timer
from midfreq.trading.node.context import (  # noqa: F401
    DEFAULT_COMPONENTS, NodeContext, NodeError, _import, build_component,
)
from midfreq.trading.node.event_queue import ExecutionStatus, NodeEventQueue, StopRequest
from midfreq.trading.node.market_state import MarketStateMixin
from midfreq.trading.node.orders import OrdersMixin
from midfreq.trading.node.shutdown import ShutdownMixin
from midfreq.trading.node.signals import SignalsMixin
from midfreq.trading.node.startup import StartupMixin
from midfreq.trading.node.wiring import REQUIRED_METHODS, WiringMixin  # noqa: F401
from midfreq.trading.ops.logs import OpsLogging, get_logger, log_context
from midfreq.trading.strategy.host import StrategyHost

log = get_logger("node")


# ── the node ──────────────────────────────────────────────────────────────────────────────────

class TradingNode(ClockMixin, WiringMixin, StartupMixin, MarketStateMixin, OrdersMixin, ShutdownMixin,
                  BrokerViewMixin, SignalsMixin):
    """The live trading node: built from a NodeConfig, started, run until its session ends, and
    stopped (this module's docstring has the whole of it). The steps are in the mixins; this class
    holds the state they share, the lifecycle and the loop."""

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

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────
    @property
    def trading_enabled(self) -> bool:
        return self.host is not None and self.state == "running" and self.host.trading

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

    # ── the kill switch and alerts ───────────────────────────────────────────────────────────
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
