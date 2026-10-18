"""
The node's start-up steps after the build, in TradingNode.start's order: the ops logging and the
banner, connecting the connectors and the execution clients, the reference data, opening the
strategies (each restored from its state, shrink-only against the broker), their sessions, the
warm-up from the local store and the subscriptions; and the teardown after a start that failed.
"""
from __future__ import annotations

import dataclasses
import typing
from dataclasses import is_dataclass
from pathlib import Path
from typing import List, Optional

from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.paths import live_sessions_dir
from midfreq.trading.config.settings import (
    StrategySettings, TIMEFRAMES, resolve_strategy_class, state_slug, strategy_params,
)
from midfreq.trading.node.clock import NANOS
from midfreq.trading.node.context import NodeError
from midfreq.trading.node.event_queue import ExecutionStatus
from midfreq.trading.ops.logs import OpsLogging, OpsSettings, get_logger, log_context, quiet_venue_sdk_loggers
from midfreq.trading.strategy.host import LiveLegacyAdapter, NativeStrategyRunner, build_risk_manager

log = get_logger("node")


class StartupMixin:
    """TradingNode's start-up steps. Relies on the node's `config`, `mode`, `clock`, `queue`,
    `cache`, `venues`, `records_dir`, `session_id`, `session_stamp`, `_configure_logging`,
    `_ops_stream`, `ctx`, `host`, `_services`, `refdata`, `positions`, `marketdata` and
    `bar_types`; fills `instruments` and `accounts_at_start`, and sets `ops`, `state` (after a
    failed start) and the stream's clocks `_last_bar_at` and `_last_update_at`. Calls `now_ns`,
    `_universe_instruments` and `_unlock_records` (wiring), `account_snapshot` and
    `broker_positions` (the broker's view) and `_disconnect` (shutdown)."""

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

    def _banner(self) -> None:
        n = self.config.node
        log.info("=" * 60)
        log.info("TRADING NODE %s — %s mode", n.name, self.mode.upper())
        log.info("strategies: %s", ", ".join(f"{s.id} ({s.cls})" for s in self.config.strategies))
        log.info("records: %s", self.records_dir)
        log.info("=" * 60)

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

    def _seed_state_dir(self) -> Optional[Path]:
        """Shadow and sim start from the live state (read only) until they have their own."""
        if self.mode in ("paper", "live"):
            return None
        if self.config.node.seed_state_from:
            return self.config.resolve_path(self.config.node.seed_state_from)
        return live_sessions_dir() if self.mode == "shadow" else None

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

    def _teardown_after_failed_start(self) -> None:
        try:
            self._disconnect()
        except Exception:
            pass
        self._unlock_records()
        self.state = "stopped"
        if self.ops is not None:
            self.ops.uninstall()
