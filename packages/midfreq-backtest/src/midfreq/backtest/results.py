"""
BacktestResult: what a run produced, per currency.

Performance metrics are computed by the original code, midfreq.core.portfolio.Portfolio.calculate_metrics
(Sharpe on daily returns, maximum drawdown, win rate, profit factor) and midfreq.backtest.backtester's
equal-weight buy-and-hold benchmark, so a number reported from either engine means the same thing.
Results are never summed across currencies: there is no FX conversion in this version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from midfreq.backtest.backtester import Backtester
from midfreq.core.portfolio import Portfolio as CorePortfolio
from midfreq.core.model.orders import Order


@dataclass
class BacktestResult:
    starting_balances: Dict[str, float]
    final_balances: Dict[str, float]
    final_equity: Dict[str, float]
    trade_log: List[Dict[str, Any]]
    orders: List[Order]
    equity_curves: Dict[str, pd.Series]
    open_positions: Dict[str, Dict[str, Any]]
    metrics: Dict[str, Dict[str, Any]]
    bars_processed: int = 0
    slices_processed: int = 0
    data_processed: int = 0                    # bars plus quotes, trades and book snapshots
    first_ts: pd.Timestamp = None
    last_ts: pd.Timestamp = None
    venues: List[str] = field(default_factory=list)

    @property
    def currencies(self) -> List[str]:
        return list(self.starting_balances)

    def trades(self) -> pd.DataFrame:
        """Every fill, oldest first, with the account balance after it."""
        return pd.DataFrame(self.trade_log)

    def return_pct(self, currency: str) -> float:
        start = self.starting_balances[currency]
        return (self.final_equity[currency] - start) / start * 100 if start else 0.0

    def orders_by_status(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for o in self.orders:
            out[o.status.value] = out.get(o.status.value, 0) + 1
        return out

    def summary(self) -> str:
        lines = [f"Backtest {self.first_ts} .. {self.last_ts}: {self.bars_processed} bars, "
                 f"{len(self.trade_log)} fills, orders {self.orders_by_status()}"]
        for ccy in self.currencies:
            m = self.metrics.get(ccy, {})
            lines.append(
                f"  {ccy}: {self.starting_balances[ccy]:,.2f} -> {self.final_equity[ccy]:,.2f} "
                f"({self.return_pct(ccy):+.2f}%), Sharpe {m.get('sharpe_ratio', 0):.2f}, "
                f"max drawdown {m.get('max_drawdown', 0):.2f}%, benchmark {m.get('benchmark_return_pct', 0):+.2f}%")
        return "\n".join(lines)


def currency_metrics(starting_balance: float, balance: float, positions: Dict[str, Dict[str, float]],
                     trade_log: List[Dict[str, Any]], equity_points: List[tuple],
                     last_prices: Dict[str, float], first_prices: Dict[str, float]) -> Dict[str, Any]:
    """midfreq.core.portfolio.Portfolio.calculate_metrics over one currency's account, plus the
    equal-weight buy-and-hold benchmark of midfreq.backtest.backtester."""
    p = CorePortfolio(initial_cash=starting_balance, commission_rate=0.0)
    p.cash = balance
    p.positions = {sym: {"qty": pos["qty"], "entry_price": pos["avg_px"]} for sym, pos in positions.items()}
    p.trade_history = [{"timestamp": t["timestamp"], "symbol": t["instrument_id"], "action": t["side"],
                        "qty": t["qty"], "price": t["price"], "commission": t["commission"]}
                       for t in trade_log]
    p.equity_curve_detailed = [{"timestamp": pd.Timestamp(ts, unit="ns", tz="UTC"), "equity": eq}
                               for ts, eq in equity_points]
    metrics = p.calculate_metrics(last_prices)
    holder = SimpleNamespace(portfolio=SimpleNamespace(initial_cash=starting_balance))
    metrics["benchmark_return_pct"] = Backtester._calculate_benchmark(holder, first_prices, last_prices)
    metrics["alpha"] = metrics["return_pct"] - metrics["benchmark_return_pct"]
    metrics["open_positions"] = {k: dict(v) for k, v in positions.items()}
    return metrics
