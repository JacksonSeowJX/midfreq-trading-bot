"""
Accounts, positions and the portfolio.

Accounts are per CURRENCY, shared by every venue that settles in it: XNAS and XNYS draw on one
USD account, as they would at a broker, and BINANCE and OKX on one USDT account. There is no FX
conversion in this version, so results are reported per currency and never summed across them.

Cash and position arithmetic deliberately repeats core.portfolio.Portfolio.execute_trade
operation for operation (value = qty x price, commission = value x rate, cash += -(value +
commission) for a buy), so a strategy run through the legacy adapter keeps a balance identical to
the last bit to the original backtester's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from mft.model.enums import OrderSide
from mft.model.events import OrderFilled
from mft.model.identifiers import InstrumentId, StrategyId


@dataclass
class Account:
    currency: str
    starting_balance: float = 0.0
    balance: float = 0.0
    commissions: float = 0.0

    def __post_init__(self):
        self.balance = self.starting_balance


@dataclass
class Position:
    instrument_id: InstrumentId
    currency: str
    quantity: float = 0.0
    avg_px: float = 0.0
    realized_pnl: float = 0.0
    commissions: float = 0.0
    strategy_id: Optional[StrategyId] = None
    opened_ns: int = 0

    def apply(self, side: OrderSide, qty: float, px: float, commission: float) -> None:
        if side is OrderSide.BUY:
            new_qty = self.quantity + qty
            # Weighted average entry, the same expression as core.portfolio.Portfolio.
            self.avg_px = ((self.quantity * self.avg_px) + (qty * px)) / new_qty
            self.quantity = new_qty
        else:
            self.realized_pnl += (px - self.avg_px) * qty
            self.quantity -= qty
            if self.quantity == 0:
                self.avg_px = 0.0
        self.commissions += commission

    @property
    def is_open(self) -> bool:
        return self.quantity > 0


class Portfolio:
    """Accounts and open positions, with an equity history per currency."""

    def __init__(self, cache):
        self._cache = cache
        self.accounts: Dict[str, Account] = {}
        self._funded: set = set()
        # Open positions only, in the order they were opened (a closed position is removed, so a
        # reopened one moves to the end, as in core.portfolio.Portfolio.positions).
        self.positions: Dict[InstrumentId, Position] = {}
        self.closed_positions: List[Position] = []
        self.fills: List[OrderFilled] = []
        self.trade_log: List[dict] = []      # one readable row per fill, with the balance after it
        self.equity_history: Dict[str, List[Tuple[int, float]]] = {}

    # ── accounts ─────────────────────────────────────────────────────

    def fund(self, currency: str, amount: float) -> Account:
        if currency in self._funded:
            raise ValueError(f"the {currency} account is already funded; venues that settle in the same "
                             f"currency share one account")
        acct = self.account(currency)
        acct.starting_balance = float(amount)
        acct.balance = float(amount)
        self._funded.add(currency)
        return acct

    def account(self, currency: str) -> Account:
        acct = self.accounts.get(currency)
        if acct is None:
            acct = self.accounts[currency] = Account(currency)
        return acct

    # ── positions ────────────────────────────────────────────────────

    def position(self, instrument_id: InstrumentId) -> Optional[Position]:
        return self.positions.get(instrument_id)

    def net_qty(self, instrument_id: InstrumentId) -> float:
        p = self.positions.get(instrument_id)
        return p.quantity if p is not None else 0.0

    def is_flat(self, instrument_id: InstrumentId) -> bool:
        return self.net_qty(instrument_id) == 0

    def apply_fill(self, fill: OrderFilled) -> None:
        acct = self.account(fill.currency)
        trade_value = fill.last_qty * fill.last_px
        if fill.side is OrderSide.BUY:
            total_cost = trade_value + fill.commission
            acct.balance += -total_cost
        else:
            total_cost = trade_value - fill.commission
            acct.balance += total_cost
        acct.commissions += fill.commission

        pos = self.positions.get(fill.instrument_id)
        if pos is None:
            pos = self.positions[fill.instrument_id] = Position(
                fill.instrument_id, fill.currency, strategy_id=fill.strategy_id, opened_ns=fill.ts_event)
        pos.apply(fill.side, fill.last_qty, fill.last_px, fill.commission)
        pos.strategy_id = fill.strategy_id
        if pos.quantity == 0:
            del self.positions[fill.instrument_id]
            self.closed_positions.append(pos)
        self.fills.append(fill)
        self.trade_log.append({
            "timestamp": pd.Timestamp(fill.ts_event, unit="ns", tz="UTC"),
            "instrument_id": str(fill.instrument_id), "side": fill.side.value, "qty": fill.last_qty,
            "price": fill.last_px, "commission": fill.commission, "currency": fill.currency,
            "cash_after": acct.balance, "strategy_id": str(fill.strategy_id),
            "client_order_id": str(fill.client_order_id), "exit_reason": fill.tags.get("exit_reason"),
        })

    # ── valuation ────────────────────────────────────────────────────

    def market_value(self, currency: str) -> float:
        total = 0.0
        for iid, pos in self.positions.items():
            if pos.currency == currency:
                px = self._cache.last_price(iid)
                total += pos.quantity * (px if px is not None else pos.avg_px)
        return total

    def equity(self, currency: str) -> float:
        return self.account(currency).balance + self.market_value(currency)

    def currencies(self) -> List[str]:
        return list(self.accounts)

    def snapshot(self, ts_ns: int) -> None:
        for ccy in self.accounts:
            self.equity_history.setdefault(ccy, []).append((ts_ns, self.equity(ccy)))


class PortfolioView:
    """What a strategy sees of the portfolio: queries only, no way to move cash or positions."""

    def __init__(self, portfolio: Portfolio):
        self._p = portfolio

    def net_qty(self, instrument_id) -> float:
        return self._p.net_qty(instrument_id)

    def is_flat(self, instrument_id) -> bool:
        return self._p.is_flat(instrument_id)

    def position(self, instrument_id) -> Optional[Position]:
        return self._p.position(instrument_id)

    def positions_open(self) -> List[Position]:
        return list(self._p.positions.values())

    def balance(self, currency: str) -> float:
        return self._p.account(currency).balance

    def equity(self, currency: str) -> float:
        return self._p.equity(currency)

    def starting_balance(self, currency: str) -> float:
        return self._p.account(currency).starting_balance
