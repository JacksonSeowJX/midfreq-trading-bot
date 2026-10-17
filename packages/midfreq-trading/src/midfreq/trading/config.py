"""
The trading node's configuration: one YAML file (config/node_<name>.yaml) naming the mode, the
venues' connectors and accounts, the strategies with their parameters and universes, the risk
limits, the policy for orders left unfilled and the capital cap.

    node:
      name: sp100
      mode: shadow                 # shadow | sim | paper | live
      timeframe: 1h
    connectors:
      moomoo: {}                   # ConnectorConfig settings for the registry's connector
    data:
      connector: moomoo            # which connector streams market data (else each venue's default)
      quotes: false                # subscribe to quotes for pricing (Moomoo: costs quote quota)
    accounts:
      moomoo-us:
        connector: moomoo
        options: {market: US}      # passed to connector.execution_client() / account_client()
        environment: paper         # paper | live | sim; `live` is required, here, to trade real money
    universes:
      sp100: {from: config/sp100_forward_test.json}      # or symbols: [US.AAPL, ...]
    strategies:
      - id: sp100-xsr
        class: Cross-Sectional Reversal                   # registry name or module:Class
        from: config/sp100_forward_test.json              # params, symbols and capital from here
        account: moomoo-us
        universe: sp100
        capital: 100000
        commission_rate: 0.00005
        risk_manager: {stop_loss_pct: 0, max_drawdown_pct: 0.15, sizing: equal-dollar}
        pricing: {rule: last, slip: 0.01}
    risk: {...}                    # passed to the pre-trade risk engine
    oms: {unfilled_order: {...}}   # passed to the OMS
    components: {oms: "midfreq.trading.oms:OrderManagementSystem", ...}   # overrides

Paths in the file are relative to the runtime root (MIDFREQ_ROOT, see midfreq.core.paths).
`load_config` reads and checks a file; `validate` returns every problem found, without touching a
network or a broker (it is what `midfreq-node check-config` runs).
"""
from __future__ import annotations

import importlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from midfreq.core.model.data import BarSpecification
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.paths import repo_root
from midfreq.core.venues.spec import VenueRegistry, default_venues

MODES = ("shadow", "sim", "paper", "live")
ENVIRONMENTS = ("paper", "live", "sim")
TIMEFRAMES = {"1m": "1-MINUTE", "5m": "5-MINUTE", "1h": "1-HOUR", "4h": "4-HOUR", "1d": "1-DAY"}
PRICING_RULES = ("last", "quote", "mid")


class ConfigError(ValueError):
    """The node configuration is invalid; the message lists every problem found."""


# ── sections ──────────────────────────────────────────────────────────────────────────────────

@dataclass
class NodeSettings:
    name: str = "node"
    mode: str = "paper"
    timeframe: str = "1h"
    records_dir: Optional[str] = None       # default: live_sessions/ (paper, live), live_sessions/<mode>/ otherwise
    seed_state_from: Optional[str] = None   # shadow/sim: read the strategies' first state from here
    records_tz: Optional[str] = None        # zone of the records' local timestamps; None = this machine's
    heartbeat_s: float = 300.0
    reconcile_s: float = 300.0
    timer_s: float = 1.0                    # bar finality and OMS policies
    stall_factor: float = 1.25              # no closed bar for this many intervals = stalled
    stall_check: bool = True
    duration_min: Optional[float] = None
    stop_order_wait_s: float = 30.0         # at shutdown, how long to wait for cancels to land
    log_level: str = "INFO"
    console_tz: str = "Asia/Singapore"
    console_tz_label: str = "SGT"
    market_tz: str = "America/New_York"
    market_tz_label: str = "ET"
    email_alerts: bool = False


@dataclass
class DataSettings:
    connector: Optional[str] = None         # None: each venue's default connector (config/venues.json)
    quotes: bool = False
    persist_bars: Optional[bool] = None     # None: on in paper/live, off in shadow/sim


@dataclass
class AccountConfig:
    id: str
    connector: str
    options: Dict[str, Any] = field(default_factory=dict)
    environment: str = "paper"
    account_options: Optional[Dict[str, Any]] = None    # for the read-only AccountClient, if different


@dataclass
class UniverseConfig:
    name: str
    symbols: List[str]


@dataclass
class RiskManagerSettings:
    """The candle strategy's own RiskManager (midfreq.core.risk_manager), as run_live builds it.
    Zero or null disables a stop."""
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    max_drawdown_pct: float = 0.10
    sizing: Optional[str] = None            # 'fixed' | 'equal-dollar' | None (strategy default)
    qty: int = 100                          # fixed sizing
    basket_size: Optional[int] = None       # equal-dollar: default the strategy's top_n


@dataclass
class PricingSettings:
    """How a candle strategy's trade becomes a limit price. `last`: the latest traded price moved
    `slip` through (above for a buy, below for a sell), the original engine's rule. `quote`: the
    ask (buy) or bid (sell) moved `slip` through, falling back to `last` without a quote. `mid`:
    the quote's mid moved `slip` through."""
    rule: str = "last"
    slip: float = 0.01
    time_in_force: str = "DAY"


@dataclass
class StrategySettings:
    id: str
    cls: str
    kind: str = "legacy"                    # 'legacy' (candle strategy, via the adapter) | 'native'
    params: Dict[str, Any] = field(default_factory=dict)
    account: str = ""
    universe: str = ""
    timeframe: Optional[str] = None
    capital: Optional[float] = None
    commission_rate: Optional[float] = None
    risk_manager: Optional[RiskManagerSettings] = None
    pricing: PricingSettings = field(default_factory=PricingSettings)
    warmup_bars: int = 60
    state_name: Optional[str] = None        # state_<state_name>.json; default the class name, lower case
    confirm_fills: bool = True              # candle strategies: wait for each order's outcome (see strategy_host)
    confirm_timeout_s: float = 75.0
    source: Optional[str] = None            # the `from:` file


@dataclass
class OpsSection:
    json_file: bool = True
    console: bool = True


@dataclass
class NodeConfig:
    node: NodeSettings = field(default_factory=NodeSettings)
    connectors: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    data: DataSettings = field(default_factory=DataSettings)
    accounts: Dict[str, AccountConfig] = field(default_factory=dict)
    universes: Dict[str, UniverseConfig] = field(default_factory=dict)
    strategies: List[StrategySettings] = field(default_factory=list)
    risk: Dict[str, Any] = field(default_factory=dict)
    oms: Dict[str, Any] = field(default_factory=dict)
    marketdata: Dict[str, Any] = field(default_factory=dict)
    refdata: Dict[str, Any] = field(default_factory=dict)
    journal: Dict[str, Any] = field(default_factory=dict)
    positions: Dict[str, Any] = field(default_factory=dict)
    shadow: Dict[str, Any] = field(default_factory=dict)
    components: Dict[str, str] = field(default_factory=dict)
    ops: OpsSection = field(default_factory=OpsSection)
    source_path: Optional[Path] = None
    base_dir: Optional[Path] = None

    # ── convenience ───────────────────────────────────────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self.node.mode

    def strategy(self, strategy_id: str) -> StrategySettings:
        for s in self.strategies:
            if s.id == strategy_id:
                return s
        raise KeyError(f"no strategy {strategy_id!r}; configured: {[s.id for s in self.strategies]}")

    def bar_spec(self, strategy: Optional[StrategySettings] = None) -> BarSpecification:
        tf = (strategy.timeframe if strategy and strategy.timeframe else None) or self.node.timeframe
        return BarSpecification.from_str(TIMEFRAMES.get(tf, tf))

    def with_mode(self, mode: Optional[str]) -> "NodeConfig":
        if mode:
            self.node.mode = mode
        return self

    def resolve_path(self, value: "str | Path") -> Path:
        p = Path(value)
        if p.is_absolute():
            return p
        return (self.base_dir or repo_root()) / p


# ── loading ───────────────────────────────────────────────────────────────────────────────────

def _section(raw: Mapping[str, Any], key: str) -> Dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"`{key}` must be a mapping, got {type(value).__name__}")
    return dict(value)


def _known(cls, d: Mapping[str, Any], where: str) -> Dict[str, Any]:
    names = set(cls.__dataclass_fields__)
    unknown = set(d) - names
    if unknown:
        raise ConfigError(f"{where}: unknown settings {sorted(unknown)}; allowed: {sorted(names)}")
    return dict(d)


def _read_json(path: Path, where: str) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{where}: {path} does not exist") from None
    except ValueError as e:
        raise ConfigError(f"{where}: {path} is not valid JSON ({e})") from None


def config_from_dict(raw: Mapping[str, Any], base_dir: Optional[Path] = None,
                     source_path: Optional[Path] = None) -> NodeConfig:
    """Build a NodeConfig from the parsed YAML. Structural errors raise ConfigError; semantic
    checks are `validate`'s."""
    if not isinstance(raw, Mapping):
        raise ConfigError("the node configuration must be a mapping")
    allowed = {"node", "connectors", "data", "accounts", "universes", "strategies", "risk", "oms",
               "marketdata", "refdata", "journal", "positions", "shadow", "components", "ops"}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"unknown sections {sorted(unknown)}; allowed: {sorted(allowed)}")
    cfg = NodeConfig(base_dir=base_dir, source_path=source_path)
    cfg.node = NodeSettings(**_known(NodeSettings, _section(raw, "node"), "node"))
    cfg.connectors = {str(k): dict(v or {}) for k, v in _section(raw, "connectors").items()}
    cfg.data = DataSettings(**_known(DataSettings, _section(raw, "data"), "data"))
    for key in ("risk", "oms", "marketdata", "refdata", "journal", "positions", "shadow"):
        setattr(cfg, key, _section(raw, key))
    cfg.components = {str(k): str(v) for k, v in _section(raw, "components").items()}
    cfg.ops = OpsSection(**_known(OpsSection, _section(raw, "ops"), "ops"))

    for aid, entry in _section(raw, "accounts").items():
        entry = dict(entry or {})
        if "connector" not in entry:
            raise ConfigError(f"account {aid!r} needs a `connector`")
        entry = _known(AccountConfig, {**entry, "id": str(aid)}, f"account {aid!r}")
        cfg.accounts[str(aid)] = AccountConfig(**entry)

    for name, entry in _section(raw, "universes").items():
        entry = dict(entry or {})
        symbols = entry.get("symbols")
        if entry.get("from"):
            src = cfg.resolve_path(entry["from"])
            symbols = _read_json(src, f"universe {name!r}").get("symbols")
        if not symbols or not isinstance(symbols, list):
            raise ConfigError(f"universe {name!r} needs `symbols` (a list) or `from` (a JSON file with one)")
        cfg.universes[str(name)] = UniverseConfig(str(name), [str(s) for s in symbols])

    strategies = raw.get("strategies") or []
    if not isinstance(strategies, list):
        raise ConfigError("`strategies` must be a list")
    for i, entry in enumerate(strategies):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"strategies[{i}] must be a mapping")
        entry = dict(entry)
        sid = str(entry.pop("id", "") or "")
        where = f"strategy {sid or i!r}"
        if "class" not in entry:
            raise ConfigError(f"{where} needs a `class`")
        cls_name = str(entry.pop("class"))
        params = dict(entry.pop("params", {}) or {})
        source = entry.pop("from", None)
        capital = entry.pop("capital", None)
        universe = entry.pop("universe", None)
        if source:
            src = cfg.resolve_path(source)
            data = _read_json(src, where)
            params = {**dict(data.get("params") or {}), **params}
            if capital is None and data.get("capital") is not None:
                capital = float(data["capital"])
            if universe is None and data.get("symbols"):
                universe = sid or f"strategy{i}"
                if universe not in cfg.universes:
                    cfg.universes[universe] = UniverseConfig(universe, [str(s) for s in data["symbols"]])
        rm = entry.pop("risk_manager", None)
        pricing = entry.pop("pricing", None)
        entry = _known(StrategySettings, entry, where)
        settings = StrategySettings(
            id=sid or cls_name, cls=cls_name, params=params, universe=str(universe or ""),
            capital=float(capital) if capital is not None else None, source=str(source) if source else None,
            risk_manager=RiskManagerSettings(**_known(RiskManagerSettings, dict(rm), f"{where} risk_manager"))
            if rm is not None else None,
            pricing=PricingSettings(**_known(PricingSettings, dict(pricing), f"{where} pricing"))
            if pricing is not None else PricingSettings(),
            **entry)
        cfg.strategies.append(settings)
    return cfg


def load_config(path: "str | Path", mode: Optional[str] = None, check: bool = True,
                venues: Optional[VenueRegistry] = None) -> NodeConfig:
    """Read a node configuration. With `check` (the default) every semantic problem is collected
    and raised together as one ConfigError."""
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        candidate = repo_root() / path
        if candidate.exists():
            path = candidate
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        raise ConfigError(f"node configuration {path} does not exist") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from None
    cfg = config_from_dict(raw, base_dir=repo_root(), source_path=path)
    cfg.with_mode(mode)
    if check:
        problems = validate(cfg, venues)
        if problems:
            raise ConfigError(f"{path}: " + "; ".join(problems))
    return cfg


# ── checking ──────────────────────────────────────────────────────────────────────────────────

def resolve_strategy_class(name: str) -> Tuple[type, Dict[str, Any]]:
    """(class, registry default params) for a registry name ('Cross-Sectional Reversal') or an
    import path ('package.module:Class'). Imports nothing from a venue SDK."""
    from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY
    if name in STRATEGY_REGISTRY:
        info = STRATEGY_REGISTRY[name]
        return info["class"], {k: v["default"] for k, v in info["params"].items()}
    module, sep, attr = name.partition(":")
    if not sep:
        module, _, attr = name.rpartition(".")
    if not module or not attr:
        raise ConfigError(f"unknown strategy class {name!r}: not in STRATEGY_REGISTRY and not 'module:Class'")
    try:
        cls = getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        raise ConfigError(f"cannot import strategy class {name!r}: {e}") from None
    for reg in STRATEGY_REGISTRY.values():
        if reg["class"] is cls:
            return cls, {k: v["default"] for k, v in reg["params"].items()}
    return cls, {}


def strategy_params(settings: StrategySettings) -> Dict[str, Any]:
    """Registry defaults overridden by the configured params, each coerced to its default's type
    (as run_live's --params does)."""
    _, defaults = resolve_strategy_class(settings.cls)
    params = dict(defaults)
    for key, value in settings.params.items():
        default = defaults.get(key)
        if isinstance(default, bool) or default is None:
            params[key] = value
        elif isinstance(default, int):
            params[key] = int(float(value))
        elif isinstance(default, float):
            params[key] = float(value)
        else:
            params[key] = value
    return params


def is_instrument_id(symbol: str, venues: VenueRegistry) -> bool:
    try:
        iid = InstrumentId.from_str(symbol)
    except ValueError:
        return False
    return str(iid.venue) in venues


def instrument_for_symbol(symbol: str, venues: Optional[VenueRegistry] = None) -> InstrumentId:
    """A configured symbol as an InstrumentId: an instrument id as it is ('BTCUSDT.OKX',
    'BRK.B.XNYS'), or a candle strategy's Moomoo-style symbol ('US.AAPL' -> AAPL.XNAS,
    'HK.00700' -> 00700.XHKG) through the venues' listing table."""
    venues = venues or default_venues()
    if is_instrument_id(symbol, venues):
        return InstrumentId.from_str(symbol)
    market, sep, code = symbol.replace("_", ".", 1).partition(".")
    if not sep:
        raise ValueError(f"cannot place symbol {symbol!r} on a venue: use '<MARKET>.<CODE>' or '<symbol>.<VENUE>'")
    venue = venues.venue_for_market("moomoo", market, code)
    return InstrumentId.of(code, venue)


def validate(cfg: NodeConfig, venues: Optional[VenueRegistry] = None) -> List[str]:
    """Every problem with the configuration, as readable sentences; [] when it is usable."""
    venues = venues or default_venues()
    problems: List[str] = []
    n = cfg.node
    if n.mode not in MODES:
        problems.append(f"node.mode must be one of {list(MODES)}, got {n.mode!r}")
    if n.timeframe not in TIMEFRAMES:
        try:
            BarSpecification.from_str(n.timeframe)
        except ValueError:
            problems.append(f"node.timeframe {n.timeframe!r} is neither {sorted(TIMEFRAMES)} nor '<step>-<AGG>'")
    for key in ("heartbeat_s", "reconcile_s", "timer_s", "stall_factor"):
        if getattr(n, key) is None or float(getattr(n, key)) <= 0:
            problems.append(f"node.{key} must be positive")
    if not cfg.strategies:
        problems.append("no strategies configured")
    for aid, acc in cfg.accounts.items():
        if acc.environment not in ENVIRONMENTS:
            problems.append(f"account {aid!r}: environment must be one of {list(ENVIRONMENTS)}, got {acc.environment!r}")
        if n.mode == "live" and acc.environment != "live" and any(s.account == aid for s in cfg.strategies):
            problems.append(f"mode is live but account {aid!r} says environment {acc.environment!r}: "
                            "real-money trading needs `environment: live` on the account as well")
        if n.mode == "paper" and acc.environment == "live":
            problems.append(f"account {aid!r} is a live account but the mode is paper; refusing to guess")
    ids = [s.id for s in cfg.strategies]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate strategy ids {sorted(dupes)}")
    slugs: Dict[str, str] = {}
    for s in cfg.strategies:
        where = f"strategy {s.id!r}"
        if s.kind not in ("legacy", "native"):
            problems.append(f"{where}: kind must be 'legacy' or 'native', got {s.kind!r}")
        try:
            cls, defaults = resolve_strategy_class(s.cls)
        except ConfigError as e:
            problems.append(f"{where}: {e}")
            cls, defaults = None, {}
        if cls is not None and s.kind == "legacy" and defaults:
            unknown = set(s.params) - set(defaults)
            if unknown:
                problems.append(f"{where}: unknown params {sorted(unknown)}; valid: {sorted(defaults)}")
        if cls is not None:
            slug = s.state_name or cls.__name__.lower()
            if slug in slugs:
                problems.append(f"{where} and strategy {slugs[slug]!r} would share state_{slug}.json; "
                                "give one a `state_name`")
            slugs[slug] = s.id
        if s.account not in cfg.accounts:
            problems.append(f"{where}: account {s.account!r} is not configured (have {sorted(cfg.accounts)})")
        if s.universe not in cfg.universes:
            problems.append(f"{where}: universe {s.universe!r} is not configured (have {sorted(cfg.universes)})")
        else:
            currencies = set()
            for sym in cfg.universes[s.universe].symbols:
                try:
                    iid = instrument_for_symbol(sym, venues)
                    currencies.add(venues.get(iid.venue).currency)
                except (ValueError, KeyError) as e:
                    problems.append(f"{where}: symbol {sym!r}: {e}")
            if s.kind == "legacy" and len(currencies) > 1:
                problems.append(f"{where}: a candle strategy has one cash balance; its symbols settle in {sorted(currencies)}")
        if s.timeframe and s.timeframe not in TIMEFRAMES:
            problems.append(f"{where}: timeframe {s.timeframe!r} is not one of {sorted(TIMEFRAMES)}")
        if s.capital is not None and (not math.isfinite(s.capital) or s.capital <= 0):
            problems.append(f"{where}: capital must be positive")
        if s.pricing.rule not in PRICING_RULES:
            problems.append(f"{where}: pricing.rule must be one of {list(PRICING_RULES)}")
        if not 0 <= s.pricing.slip < 0.2:
            problems.append(f"{where}: pricing.slip {s.pricing.slip} is outside [0, 0.2)")
        if s.pricing.time_in_force not in ("DAY", "GTC", "IOC"):
            problems.append(f"{where}: pricing.time_in_force must be DAY, GTC or IOC")
        rm = s.risk_manager
        if rm is not None and rm.sizing not in (None, "fixed", "equal-dollar"):
            problems.append(f"{where}: risk_manager.sizing must be 'fixed' or 'equal-dollar'")
        if s.warmup_bars < 0:
            problems.append(f"{where}: warmup_bars must not be negative")
    if cfg.data.connector is not None:
        from midfreq.data.adapters.registry import ConnectorRegistry
        if ConnectorRegistry.canonical(cfg.data.connector) not in ConnectorRegistry(venues).names():
            problems.append(f"data.connector {cfg.data.connector!r} is not a registered connector")
    return problems
