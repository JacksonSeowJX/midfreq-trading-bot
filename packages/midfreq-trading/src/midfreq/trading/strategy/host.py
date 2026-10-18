"""
The strategy host: runs the node's strategies on the message bus.

Candle strategies (midfreq.strategies.candle_strategies) run unchanged through `LiveLegacyAdapter`,
the live counterpart of midfreq.backtest.legacy_adapter. Event-driven strategies
(midfreq.strategies.trading.Strategy) run natively through `NativeStrategyRunner`.

Where each part is
------------------
- `StrategyHost` (here): every strategy of the node, and the bridge that gives native strategies
  each cross-section as a LiveTimeSlice on data.slices.
- `NodeServices` (midfreq.trading.strategy.services): what the node gives the host.
- `LiveLegacyAdapter`, `LiveLegacyPortfolio` and their helpers (midfreq.trading.strategy.legacy):
  a candle strategy live; that package's docstring tells how a candle strategy trades live.
- `NativeStrategyRunner` and `LiveTimeSlice` (midfreq.trading.strategy.native): an event-driven
  strategy live.

Every one of these names is importable from this module too.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from midfreq.trading.core.interfaces import TOPIC_CROSS_SECTION, CrossSection
from midfreq.trading.ops.logs import get_logger
from midfreq.trading.strategy.legacy import (  # noqa: F401  (importable from here, as before)
    LiveLegacyAdapter, LiveLegacyPortfolio, build_risk_manager, legacy_symbol_for, round_to_tick,
)
from midfreq.trading.strategy.native import LiveTimeSlice, NativeStrategyRunner  # noqa: F401
from midfreq.trading.strategy.services import NodeServices  # noqa: F401

log = get_logger("strategy_host")


class StrategyHost:
    """Every strategy of the node. Native strategies receive the MarketDataService's closed bars
    on their own topic (TOPIC_BAR is the engine's data.bars.<bar_type>); cross-sections, which
    the Strategy API does not know, are bridged to data.slices as a LiveTimeSlice."""

    def __init__(self, services: NodeServices):
        self.services = services
        self.legacy: List[LiveLegacyAdapter] = []
        self.native: List[NativeStrategyRunner] = []
        self._bridged_universes: Set[str] = set()

    @property
    def all(self) -> List[Any]:
        return [*self.legacy, *self.native]

    def add_legacy(self, adapter: LiveLegacyAdapter) -> LiveLegacyAdapter:
        self.legacy.append(adapter)
        return adapter

    def add_native(self, runner: NativeStrategyRunner, universe: Optional[str] = None) -> NativeStrategyRunner:
        self.native.append(runner)
        bus = self.services.msgbus
        if universe and universe not in self._bridged_universes:
            bus.subscribe(TOPIC_CROSS_SECTION.format(universe=universe), self._bridge_slice)
            self._bridged_universes.add(universe)
        runner.register()
        return runner

    def _bridge_slice(self, xs: CrossSection) -> None:
        bars = tuple(sorted(xs.bars.values(), key=lambda b: str(b.bar_type.instrument_id)))
        self.services.msgbus.publish("data.slices", LiveTimeSlice(xs.ts_event, bars, bars, xs.complete))

    def subscribe(self) -> None:
        for a in self.legacy:
            a.subscribe()

    def trading_on(self) -> None:
        for a in self.legacy:
            a.trading_on()
        for r in self.native:
            r.trading_on()

    def trading_off(self) -> None:
        for a in self.legacy:
            a.trading_off()
        for r in self.native:
            r.trading_off()

    @property
    def trading(self) -> bool:
        return (any(a.phase == "live" and a.portfolio is not None and not a.portfolio.warming_up for a in self.legacy)
                or any(r.trading for r in self.native))

    def finalize(self) -> None:
        for a in self.legacy:
            a.finalize()

    def on_timer(self) -> None:
        for a in self.legacy:
            a.on_timer()

    def save_states(self) -> Dict[str, Optional[int]]:
        """Save every candle strategy's state; returns its rebalance clock per strategy."""
        out = {}
        for a in self.legacy:
            try:
                a.save_state()
            except Exception as exc:
                a.log.error("state save failed: %s", exc)
            out[str(a.id)] = a.cross_section_count
        return out

    def clocks(self) -> Dict[str, Optional[int]]:
        return {str(a.id): a.cross_section_count for a in self.legacy}

    def stop_native(self) -> None:
        for r in self.native:
            try:
                r.stop()
            except Exception as exc:
                r.log.error("on_stop failed: %s", exc)

    def positions(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for a in self.legacy:
            for sym, qty in a.positions().items():
                out[f"{a.id}:{sym}"] = qty
        for r in self.native:
            for pos in r.portfolio.positions.values():
                out[f"{r.id}:{pos.instrument_id}"] = pos.quantity
        return out
