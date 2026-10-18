"""
Building the node's components: the NodeContext (everything built so far, offered to the
component factories by keyword), the default factories (`DEFAULT_COMPONENTS`, in
midfreq.trading.node.components), and `build_component`, which makes one component from an import
path, a class, a factory function or a ready instance. NodeError is the node's error when a
component cannot be built, and for anything else that keeps the node from starting or continuing.
"""
from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from midfreq.core.cache import Cache
from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.identifiers import Venue
from midfreq.core.venues.spec import VenueRegistry
from midfreq.data.adapters.base import ExecutionClient
from midfreq.trading.config.settings import NodeConfig
from midfreq.trading.node.event_queue import NodeEventQueue

DEFAULT_COMPONENTS: Dict[str, str] = {
    "refdata": "midfreq.trading.node.components:make_refdata",
    "journal": "midfreq.trading.node.components:make_journal",
    "positions": "midfreq.trading.node.components:make_positions",
    "risk": "midfreq.trading.node.components:make_risk",
    "oms": "midfreq.trading.node.components:make_oms",
    "marketdata": "midfreq.trading.node.components:make_marketdata",
    "sim_execution": "midfreq.trading.node.components:make_sim_execution",
    "sim_data": "midfreq.trading.node.components:make_sim_data",
}


class NodeError(RuntimeError):
    """The node cannot start or continue (a component missing, a live account not allowed...)."""


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
