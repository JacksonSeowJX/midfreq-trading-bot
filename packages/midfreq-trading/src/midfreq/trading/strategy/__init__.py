"""The strategy host: runs the node's strategies, backtest strategies unchanged and native ones."""
from midfreq.trading.strategy.host import (  # noqa: F401
    build_risk_manager,
    legacy_symbol_for,
    LiveLegacyAdapter,
    LiveLegacyPortfolio,
    LiveTimeSlice,
    NativeStrategyRunner,
    NodeServices,
    round_to_tick,
    StrategyHost,
)
__all__ = ["build_risk_manager", "legacy_symbol_for", "LiveLegacyAdapter", "LiveLegacyPortfolio", "LiveTimeSlice", "NativeStrategyRunner", "NodeServices", "round_to_tick", "StrategyHost"]
