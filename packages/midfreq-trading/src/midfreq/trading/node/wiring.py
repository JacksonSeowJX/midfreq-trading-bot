"""
Building the node at start-up: one node per records folder (an exclusive lock on it), an
execution client and an account source per account, a data client per venue, then the components
in dependency order (reference data, journal, positions, risk, OMS, market data), each checked for
the methods of its protocol, and the StrategyHost with the services the strategies use.
"""
from __future__ import annotations

import collections
import fcntl
import inspect
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from midfreq.core.model.data import BarType
from midfreq.core.model.identifiers import InstrumentId, Venue
from midfreq.core.paths import live_sessions_dir
from midfreq.data.adapters.base import ExecutionClient
from midfreq.trading.config.settings import StrategySettings, instrument_for_symbol
from midfreq.trading.core.interfaces import TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_STREAM
from midfreq.trading.modes.shadow import ShadowExecutionClient
from midfreq.trading.node.context import NodeContext, NodeError, build_component
from midfreq.trading.ops.logs import get_logger
from midfreq.trading.strategy.host import NodeServices, StrategyHost

log = get_logger("node")

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


class WiringMixin:
    """TradingNode's build. Relies on the node's `config`, `mode`, `clock`, `msgbus`, `queue`,
    `cache`, `venues`, `records_dir`, `store_dir`, `session_id`, `persist_bars`, `order_prefix`,
    `_components` and `_registry`; fills `bar_types`, `_account_sources` and `integration_notes`,
    and sets `ctx`, the components (`refdata`, `journal`, `positions`, `risk`, `oms`,
    `marketdata`), `host`, `_services` and `_lock_file`. What it subscribes on the bus and gives
    the strategies are the node's own methods: `_on_closed_bar`, `_on_cross_section_seen`,
    `_on_stream_event`, `_quote_triplet`, `_submit`, `_cancel`, `wait_until`, `latest_price`,
    `instrument`, `tick_size`, `market_open`, `local_now`, `local_to_ns`, `_warm_up_bars`,
    `_journal_fills` and `alert`."""

    # ── the records folder ───────────────────────────────────────────────────────────────────
    def _default_records_dir(self) -> Path:
        n = self.config.node
        if n.records_dir:
            base = self.config.resolve_path(n.records_dir)
            return base if self.mode in ("paper", "live") else base / self.mode
        base = live_sessions_dir()
        return base if self.mode in ("paper", "live") else base / self.mode

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

    # ── connectors and clients ───────────────────────────────────────────────────────────────
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

    # ── building ─────────────────────────────────────────────────────────────────────────────
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
            journal_fills=self._journal_fills, local_to_ns=self.local_to_ns, tick_size=self.tick_size)
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
