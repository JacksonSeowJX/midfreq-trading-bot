"""
The candle strategy's side of the adapter: the portfolio it sees live (`LiveLegacyPortfolio`,
whose execute_trade becomes an order and whose book is written only by fills), and the helpers
the node builds a candle strategy with: its RiskManager, its symbols and price rounding.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional

from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.portfolio import Portfolio as CorePortfolio
from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from midfreq.core.venues.spec import VenueRegistry
from midfreq.trading.config.settings import RiskManagerSettings

if TYPE_CHECKING:
    from midfreq.trading.strategy.legacy.adapter import LiveLegacyAdapter


# ── candle strategies ─────────────────────────────────────────────────────────────────────────

def legacy_symbol_for(iid: InstrumentId, venues: VenueRegistry) -> str:
    """AAPL.XNAS -> 'US.AAPL' (the Moomoo market code a candle strategy knows), or the instrument
    id itself for venues Moomoo does not serve (crypto)."""
    spec = venues.get(iid.venue)
    market = spec.connectors.get("moomoo", {}).get("market")
    return f"{market}.{iid.symbol}" if market else str(iid)


def build_risk_manager(settings: Optional[RiskManagerSettings], params: Mapping[str, Any]) -> Optional[RiskManager]:
    """The candle strategy's RiskManager as midfreq.live.run_live builds it from its flags."""
    if settings is None:
        return None

    def pct(v):
        return float(v) if v not in (None, 0, 0.0) else None

    if settings.sizing == "equal-dollar":
        n = settings.basket_size or params.get("top_n") or 2
        sizer = create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=int(n))
    elif settings.sizing == "fixed":
        sizer = create_position_sizer(SizingMethod.FIXED_QUANTITY, qty=int(settings.qty))
    else:
        sizer = None
    return RiskManager(stop_loss_pct=pct(settings.stop_loss_pct), trailing_stop_pct=pct(settings.trailing_stop_pct),
                       take_profit_pct=pct(settings.take_profit_pct),
                       max_drawdown_pct=float(settings.max_drawdown_pct), position_sizer=sizer)


def round_to_tick(price: float, tick: Optional[float]) -> float:
    """Round to the instrument's price increment: to decimals when it is a power of ten (0.01 ->
    round(price, 2), the original engine's rounding), else to the nearest multiple."""
    if not tick or tick <= 0:
        return round(price, 2)
    digits = -math.log10(tick)
    if abs(digits - round(digits)) < 1e-9:
        return round(price, int(round(digits)))
    steps = round(price / tick)
    return round(steps * tick, 10)


class LiveLegacyPortfolio(CorePortfolio):
    """The portfolio a candle strategy sees live: the original bookkeeping, whose trades are sent
    to the OMS by the adapter and booked only when they fill."""

    def __init__(self, adapter: "LiveLegacyAdapter", initial_cash: float, commission_rate: float,
                 capital_cap: Optional[float]):
        super().__init__(initial_cash=initial_cash, commission_rate=commission_rate)
        self._adapter = adapter
        self.capital_cap = capital_cap
        self.warming_up = False                 # read by the strategies: trading off
        self.lot_sizes: Dict[str, int] = {}
        self.price_ref: Optional[Callable[[str], Optional[float]]] = None
        self.cross_section_count = 0
        self.next_rebalance_at: Optional[int] = None
        self.resumed_cross_section_count = 0
        self.resumed_next_rebalance_at: Optional[int] = None

    def execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=None):
        if self.warming_up or qty <= 0:
            return
        self._adapter._route_trade(symbol, is_buy, qty, price, timestamp, exit_reason)

    def book(self, symbol, is_buy, qty, price, timestamp, exit_reason=None) -> bool:
        """The original Portfolio bookkeeping, unchanged; True if it booked the trade."""
        before = len(self.trade_history)
        CorePortfolio.execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=exit_reason)
        return len(self.trade_history) == before + 1
