"""
RiskEngine: pre-trade checks between a strategy and the venue, after NautilusTrader's `risk`.

The rules themselves are midfreq.core.risk_manager's, reused rather than rewritten:
- the drawdown circuit breaker (`RiskManager.is_trading_halted`), checked per currency account;
  while halted, orders that add exposure are DENIED and orders that reduce it still pass;
- optional stop-loss / trailing-stop / take-profit exits (`RiskManager.check_exit_signals`),
  applied by the engine on every bar to open positions, so a native strategy gets the same exits
  core strategies coded by hand;
plus limits a production risk engine would have: maximum order notional per currency and maximum
position size as a fraction of the account's equity.

With the default RiskConfig nothing is checked beyond order validity, which is what the legacy
adapter relies on: core strategies carry their own RiskManager, exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from midfreq.core.risk_manager import RiskManager
from midfreq.core.model.enums import OrderSide, OrderType
from midfreq.core.model.events import OrderFilled
from midfreq.core.model.identifiers import InstrumentId, StrategyId
from midfreq.core.model.orders import Order, OrderFactory


@dataclass
class RiskConfig:
    bypass: bool = False
    max_drawdown_pct: Optional[float] = None          # e.g. 0.10 halts new exposure at -10%
    max_order_notional: Dict[str, float] = field(default_factory=dict)   # currency -> limit
    max_position_pct: Optional[float] = None          # position value / account equity
    stop_loss_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

    @property
    def manages_exits(self) -> bool:
        return any(x is not None for x in (self.stop_loss_pct, self.trailing_stop_pct, self.take_profit_pct))


class RiskEngine:
    def __init__(self, config: RiskConfig, cache, portfolio, clock):
        self.config = config
        self._cache = cache
        self._portfolio = portfolio
        self._clock = clock
        self._halts: Dict[str, RiskManager] = {}
        self._exits = RiskManager(stop_loss_pct=config.stop_loss_pct,
                                  trailing_stop_pct=config.trailing_stop_pct,
                                  take_profit_pct=config.take_profit_pct,
                                  max_drawdown_pct=1.0) if config.manages_exits else None
        self._exit_factories: Dict[StrategyId, OrderFactory] = {}
        self._pending_exit: Dict[InstrumentId, Order] = {}
        self.execution = None          # set by the engine; used to submit exit orders
        self.denied = 0

    # ── pre-trade ────────────────────────────────────────────────────

    def check(self, order: Order) -> Optional[str]:
        """None if the order may go to the venue, else the reason it is denied."""
        if self.config.bypass:
            return None
        if not order.quantity > 0:
            return f"quantity must be positive, got {order.quantity}"
        instrument = self._cache.instrument(order.instrument_id)
        if instrument is None:
            return f"unknown instrument {order.instrument_id}"
        if order.order_type is OrderType.LIMIT and not (order.price and order.price > 0):
            return "a limit order needs a positive price"
        adds_exposure = order.side is OrderSide.BUY
        ccy = instrument.currency
        price = order.price or order.reference_price or self._cache.last_price(order.instrument_id)

        if adds_exposure and self.config.max_drawdown_pct is not None:
            rm = self._halts.get(ccy)
            if rm is None:
                rm = self._halts[ccy] = RiskManager(max_drawdown_pct=self.config.max_drawdown_pct)
            acct = self._portfolio.account(ccy)
            if rm.is_trading_halted(self._portfolio.equity(ccy), acct.starting_balance):
                return (f"trading halted: {ccy} equity is down {self.config.max_drawdown_pct:.0%} or more "
                        f"from its starting balance; only reducing orders are allowed")
        if price:
            limit = self.config.max_order_notional.get(ccy)
            if limit is not None and order.quantity * price > limit:
                return f"order notional {order.quantity * price:,.2f} {ccy} exceeds the limit {limit:,.2f}"
            if adds_exposure and self.config.max_position_pct is not None:
                equity = self._portfolio.equity(ccy)
                after = (self._portfolio.net_qty(order.instrument_id) + order.quantity) * price
                if equity > 0 and after / equity > self.config.max_position_pct:
                    return (f"position would be {after / equity:.1%} of {ccy} equity, above "
                            f"{self.config.max_position_pct:.0%}")
        return None

    # ── exits on open positions ──────────────────────────────────────

    def on_bar(self, bar) -> None:
        if self._exits is None:
            return
        iid = bar.bar_type.instrument_id
        pos = self._portfolio.position(iid)
        if pos is None or iid in self._pending_exit:
            return
        key = str(iid)
        self._exits.update_peak_price(key, bar.close)
        reason = self._exits.check_exit_signals(key, bar.close, pos.avg_px)
        if reason and self.execution is not None and pos.strategy_id is not None:
            factory = self._exit_factories.setdefault(
                pos.strategy_id, OrderFactory(pos.strategy_id, self._clock.timestamp_ns, prefix="R"))
            order = factory.market(iid, OrderSide.SELL, pos.quantity, tags={"exit_reason": reason})
            self._pending_exit[iid] = order
            self.execution.submit(order)
            if order.is_closed:
                self._pending_exit.pop(iid, None)

    def on_fill(self, fill: OrderFilled) -> None:
        if self._exits is None:
            return
        key = str(fill.instrument_id)
        pos = self._portfolio.position(fill.instrument_id)
        if pos is None:
            self._exits.clear_position(key)
            self._pending_exit.pop(fill.instrument_id, None)
        elif fill.side is OrderSide.BUY and pos.quantity == fill.last_qty:
            self._exits.register_entry(key, fill.last_px)
