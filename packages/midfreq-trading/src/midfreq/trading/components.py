"""
The trading node's default components, built from the node configuration with their real
constructor arguments (docs/LIVE_ARCHITECTURE.md):

    refdata     midfreq.trading.refdata.ReferenceDataService       (section `refdata`)
    journal     midfreq.trading.journal.OrderJournal               (section `journal`)
    positions   midfreq.trading.positions.PositionLedger           (section `positions`)
    risk        midfreq.trading.risk.PreTradeRiskEngine            (section `risk`)
    oms         midfreq.trading.oms.OrderManagementSystem          (section `oms`)
    marketdata  midfreq.trading.marketdata.LiveMarketDataService   (section `marketdata`)
    sim_execution, sim_data   midfreq.trading.testing.sim_venue (mode `sim`)

Each factory takes the NodeContext (what the node has built so far, and the node itself for its
prices, instruments and market hours) and its configuration section, and refuses settings it
does not know, so a misspelt limit is an error rather than a limit silently not applied.
`check_settings` runs the same checks without building anything (`midfreq-node check-config`).
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from midfreq.core.model.fees import FeeModel, PercentFeeModel
from midfreq.core.model.identifiers import InstrumentId, StrategyId, Venue
from midfreq.trading.config import ConfigError, NodeConfig, instrument_for_symbol

if TYPE_CHECKING:
    from midfreq.trading.node import NodeContext

log = logging.getLogger(__name__)

__all__ = [
    "RISK_SETTINGS", "check_settings", "make_journal", "make_marketdata", "make_oms", "make_positions",
    "make_refdata", "make_risk", "make_sim_data", "make_sim_execution", "risk_limits",
]


def _known(section: str, settings: Mapping[str, Any], allowed) -> Dict[str, Any]:
    unknown = set(settings) - set(allowed)
    if unknown:
        raise ConfigError(f"{section}: unknown settings {sorted(unknown)}; allowed: {sorted(allowed)}")
    return dict(settings)


def _instrument(ctx: "NodeContext") -> Callable[[InstrumentId], Any]:
    return ctx.node.instrument


def _lot_size(ctx: "NodeContext") -> Callable[[InstrumentId], Optional[float]]:
    def lot(iid: InstrumentId) -> Optional[float]:
        inst = ctx.node.instrument(iid)
        if inst is None:
            raise KeyError(f"unknown instrument {iid}")
        return inst.quantity_step
    return lot


def _tick_size(ctx: "NodeContext") -> Callable[[InstrumentId], Optional[float]]:
    def tick(iid: InstrumentId) -> Optional[float]:
        inst = ctx.node.instrument(iid)
        return inst.price_increment if inst is not None else None
    return tick


def _strategy_venues(cfg: NodeConfig, venues) -> Dict[str, List[Venue]]:
    out: Dict[str, List[Venue]] = {}
    for s in cfg.strategies:
        if s.universe in cfg.universes:
            out[s.id] = sorted({instrument_for_symbol(sym, venues).venue for sym in cfg.universes[s.universe].symbols},
                               key=str)
    return out


# ── reference data ────────────────────────────────────────────────────────────────────────────

REFDATA_SETTINGS = ("corporate_actions", "venues_path")


def make_refdata(ctx: "NodeContext", settings: Mapping[str, Any]):
    """Instruments from the connectors' InstrumentProviders (none in sim mode: the venues' lot
    rules), calendars from config/venues.json, corporate actions from the curated file."""
    from midfreq.trading.corporate_actions import JsonCorporateActionSource
    from midfreq.trading.refdata import ReferenceDataService
    s = _known("refdata", settings, REFDATA_SETTINGS)
    kwargs: Dict[str, Any] = {"venues": ctx.venues}
    if s.get("corporate_actions"):
        kwargs["corporate_actions"] = JsonCorporateActionSource(ctx.config.resolve_path(s["corporate_actions"]))
    if s.get("venues_path"):
        kwargs["venues_path"] = ctx.config.resolve_path(s["venues_path"])
    connectors = list(ctx.connectors.values())
    if connectors:
        return ReferenceDataService.from_connectors(connectors, **kwargs)
    return ReferenceDataService({}, **kwargs)


# ── order journal ─────────────────────────────────────────────────────────────────────────────

JOURNAL_SETTINGS = ("directory", "fsync")


def make_journal(ctx: "NodeContext", settings: Mapping[str, Any]):
    """orders_<date>.jsonl in the node's records folder (or `directory`)."""
    from midfreq.trading.journal import OrderJournal
    s = _known("journal", settings, JOURNAL_SETTINGS)
    directory = ctx.config.resolve_path(s["directory"]) if s.get("directory") else ctx.records_dir
    return OrderJournal(directory, fsync=bool(s.get("fsync", False)))


# ── positions ─────────────────────────────────────────────────────────────────────────────────

POSITIONS_SETTINGS = ("default_capital", "keep_bookings")


def fee_models(cfg: NodeConfig, venues) -> Dict[Venue, FeeModel]:
    """Each traded venue's fee model (config/venues.json), replaced by a strategy's own
    `commission_rate` where one is configured (the rate its candle portfolio books)."""
    out: Dict[Venue, FeeModel] = {}
    rates: Dict[Venue, set] = {}
    for sid, vs in _strategy_venues(cfg, venues).items():
        s = cfg.strategy(sid)
        for v in vs:
            out.setdefault(v, venues.get(v).fee_model())
            if s.commission_rate is not None:
                rates.setdefault(v, set()).add(float(s.commission_rate))
    for v, rs in rates.items():
        if len(rs) > 1:
            log.warning("%s: strategies configure different commission rates %s; the fee model uses %g",
                        v, sorted(rs), max(rs))
        out[v] = PercentFeeModel(max(rs))
    return out


def make_positions(ctx: "NodeContext", settings: Mapping[str, Any]):
    """One book per strategy, starting at its capital cap; fee estimates for fills whose venue
    reports no fee."""
    from midfreq.trading.positions import PositionLedger
    s = _known("positions", settings, POSITIONS_SETTINGS)
    cfg = ctx.config
    capital = {s_.id: float(s_.capital) for s_ in cfg.strategies if s_.capital is not None}

    def instrument_of(symbol: str) -> Optional[InstrumentId]:
        try:
            return instrument_for_symbol(symbol, ctx.venues)
        except (ValueError, KeyError):
            return None
    return PositionLedger(ctx.clock, ctx.msgbus, capital=capital, default_capital=s.get("default_capital"),
                          fee_models=fee_models(cfg, ctx.venues), instrument_of=instrument_of,
                          instrument=_instrument(ctx), keep_bookings=int(s.get("keep_bookings", 10_000)))


# ── pre-trade risk ────────────────────────────────────────────────────────────────────────────

RISK_SETTINGS = ("max_order_notional", "max_position_notional", "max_gross_notional", "price_band_pct",
                 "check_cash", "allow_short", "fee_rate", "market_slippage_pct", "warn_at",
                 "require_session_open", "strategies")


def risk_limits(cfg: NodeConfig, settings: Mapping[str, Any]):
    """(the default RiskLimits, {strategy id: RiskLimits}) from the `risk` section. A strategy's
    `fee_rate` defaults to its commission_rate, so the cash check prices a buy as it is sized."""
    from midfreq.trading.risk import RiskLimits
    s = _known("risk", settings, RISK_SETTINGS)
    fields = {f.name for f in dataclasses.fields(RiskLimits)}
    base = {k: v for k, v in s.items() if k in fields}
    limits = RiskLimits(**base)
    per = dict(s.get("strategies") or {})
    unknown_ids = set(per) - {x.id for x in cfg.strategies}
    if unknown_ids:
        raise ConfigError(f"risk.strategies: no strategy {sorted(unknown_ids)}")
    by_strategy = {}
    for st in cfg.strategies:
        own = _known(f"risk.strategies.{st.id}", per.get(st.id) or {}, fields)
        merged = {**base, **own}
        if "fee_rate" not in merged and st.commission_rate is not None:
            merged["fee_rate"] = float(st.commission_rate)
        by_strategy[st.id] = RiskLimits(**merged)
    return limits, by_strategy


def make_risk(ctx: "NodeContext", settings: Mapping[str, Any]):
    """Limits from the `risk` section; reference prices, lots and market hours from the node."""
    from midfreq.trading.risk import PreTradeRiskEngine
    limits, by_strategy = risk_limits(ctx.config, settings)
    node = ctx.node
    is_open = None
    if settings.get("require_session_open", True):
        def is_open(venue: Venue, ts_ns: int) -> bool:
            return node.market_open(venue, ts_ns)
    return PreTradeRiskEngine(ctx.clock, ctx.msgbus, ctx.positions, limits, reference_price=node.latest_price,
                              lot_size=_lot_size(ctx), is_open=is_open, strategy_limits=by_strategy)


# ── OMS ───────────────────────────────────────────────────────────────────────────────────────

OMS_SETTINGS = ("unfilled_order", "reconcile_lookback_s", "reconcile_grace_s", "journal_lookback_days")


def make_oms(ctx: "NodeContext", settings: Mapping[str, Any]):
    """The OMS on the node's execution clients, with the configured unfilled-order policy
    (`oms.unfilled_order`: UnfilledOrderPolicy's fields, or the preset 'original_engine') and
    re-pricing from the node's quotes and last prices."""
    from midfreq.trading.oms import OrderManagementSystem, UnfilledOrderPolicy
    s = _known("oms", settings, OMS_SETTINGS)
    try:
        policy = UnfilledOrderPolicy.from_config(s.get("unfilled_order"))
    except ValueError as exc:
        raise ConfigError(f"oms: {exc}") from None
    kwargs = {k: s[k] for k in ("reconcile_lookback_s", "reconcile_grace_s", "journal_lookback_days") if k in s}
    return OrderManagementSystem(ctx.clock, ctx.msgbus, ctx.risk, ctx.positions, ctx.journal,
                                 ctx.execution_by_venue, policy=policy, price_source=ctx.node.price_snapshot,
                                 lot_size=_lot_size(ctx), tick_size=_tick_size(ctx), **kwargs)


# ── market data ───────────────────────────────────────────────────────────────────────────────

MARKETDATA_SETTINGS = ("min_cross_section_share", "finality_grace_s", "limit_unit", "persist")


def make_marketdata(ctx: "NodeContext", settings: Mapping[str, Any]):
    """One subscription per bar type on each venue's data client; warm-up from (and the session's
    bars to) the bar store: `store_dir` when the node has one, else data/ of MIDFREQ_ROOT."""
    from midfreq.data.storage import DataStorage
    from midfreq.trading.marketdata import DEFAULT_GRACE_S, DEFAULT_MIN_SHARE, LiveMarketDataService
    s = _known("marketdata", settings, MARKETDATA_SETTINGS)
    store = DataStorage(base_path=str(ctx.store_dir)) if ctx.store_dir is not None else None
    return LiveMarketDataService(
        ctx.data_clients, ctx.msgbus, ctx.queue, venues=ctx.venues, refdata=ctx.refdata, store=store,
        clock=ctx.clock, grace=float(s.get("finality_grace_s", DEFAULT_GRACE_S)),
        min_share=float(s.get("min_cross_section_share", DEFAULT_MIN_SHARE)),
        stall_factor=float(ctx.config.node.stall_factor), limit_unit=str(s.get("limit_unit", "stream")),
        persist=bool(s.get("persist", ctx.persist_bars)))


# ── the simulated venue (mode `sim`) ──────────────────────────────────────────────────────────

SIM_EXECUTION_SETTINGS = ("latency_ns", "slippage_bps", "partial_fills", "partial_fill_interval_ns", "account_id")


def make_sim_execution(ctx: "NodeContext", settings: Mapping[str, Any]):
    """SimulatedExecutionClient matching at the node's latest prices, charging each venue's fee
    model in the venue's currency, and refusing quantities that are not whole lots."""
    from midfreq.trading.testing.sim_venue import SimulatedExecutionClient
    s = _known("sim_execution (account options)", {k: v for k, v in settings.items() if k != "market"},
               SIM_EXECUTION_SETTINGS)
    node = ctx.node
    models = fee_models(ctx.config, ctx.venues)

    def commission(order, qty: float, price: float) -> float:
        model = models.get(order.instrument_id.venue) or ctx.venues.get(order.instrument_id.venue).fee_model()
        return model.commission(qty, price)

    def check_lot(order) -> Optional[str]:
        inst = node.instrument(order.instrument_id)
        if inst is not None and not inst.is_valid_qty(order.quantity):
            return f"quantity {order.quantity:g} is not a whole number of lots of {inst.quantity_step:g}"
        return None
    schedule = getattr(ctx, "sim_scheduler", None)
    return SimulatedExecutionClient(
        ctx.clock, price_source=node.latest_price, schedule=schedule,
        latency_ns=int(s.get("latency_ns", 0)) if schedule is not None else 0,
        slippage_bps=float(s.get("slippage_bps", 0.0)), partial_fills=tuple(s.get("partial_fills", ())),
        partial_fill_interval_ns=int(s.get("partial_fill_interval_ns", 0)) if schedule is not None else 0,
        check_lot=check_lot, commission=commission,
        currency=lambda iid: ctx.venues.get(iid.venue).currency,
        account_id=str(s.get("account_id", "SIM-001")))


def make_sim_data(ctx: "NodeContext", settings: Mapping[str, Any], venue: Optional[Venue] = None):
    """A SimulatedDataClient with nothing recorded: in `sim` mode the bars come from whoever
    drives the node (a replay puts them on its queue, or passes its own data client)."""
    from midfreq.trading.testing.sim_venue import SimScheduler, SimulatedDataClient
    _known("sim_data", settings, ())
    scheduler = getattr(ctx, "sim_scheduler", None)
    if scheduler is None:
        from midfreq.core.common.clock import TestClock
        if not isinstance(ctx.clock, TestClock):
            raise ConfigError("sim mode needs a test clock (a replay) or recorded data to drive it; "
                              "use `midfreq-node replay`")
        scheduler = ctx.sim_scheduler = SimScheduler(ctx.clock)
    return SimulatedDataClient([], ctx.clock, scheduler)


# ── checking a configuration without building anything ────────────────────────────────────────

def check_settings(cfg: NodeConfig) -> List[str]:
    """Every problem with the components' sections, as sentences ([] when they are usable)."""
    from midfreq.trading.oms import UnfilledOrderPolicy
    problems: List[str] = []
    for name, section, allowed in (("refdata", cfg.refdata, REFDATA_SETTINGS),
                                   ("journal", cfg.journal, JOURNAL_SETTINGS),
                                   ("positions", cfg.positions, POSITIONS_SETTINGS),
                                   ("oms", cfg.oms, OMS_SETTINGS),
                                   ("marketdata", cfg.marketdata, MARKETDATA_SETTINGS)):
        try:
            _known(name, section, allowed)
        except ConfigError as exc:
            problems.append(str(exc))
    try:
        risk_limits(cfg, cfg.risk)
    except (ConfigError, TypeError, ValueError) as exc:
        problems.append(str(exc))
    try:
        policy = UnfilledOrderPolicy.from_config(cfg.oms.get("unfilled_order"))
    except ValueError as exc:
        problems.append(f"oms: {exc}")
    else:
        for s in cfg.strategies:
            if s.kind == "legacy" and s.confirm_fills and s.confirm_timeout_s < policy.budget_s():
                problems.append(
                    f"strategy {s.id!r}: confirm_timeout_s {s.confirm_timeout_s:g} is shorter than the unfilled-order "
                    f"policy's {policy.budget_s():g} s ({policy.max_reprices + 1} attempts of {policy.wait_s:g} s and "
                    f"a {policy.cancel_timeout_s:g} s cancel): the strategy would give up on an order the OMS is still "
                    f"re-pricing")
    share = cfg.marketdata.get("min_cross_section_share")
    if share is not None and not 0 < float(share) <= 1:
        problems.append(f"marketdata.min_cross_section_share must be in (0, 1], got {share}")
    return problems
