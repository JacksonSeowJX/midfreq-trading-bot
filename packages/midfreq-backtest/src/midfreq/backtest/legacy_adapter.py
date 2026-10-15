"""
Run the original midfreq.strategies.candle_strategies classes, unchanged, inside the event-driven BacktestEngine.

A candle strategy trades by calling `self.portfolio.execute_trade(symbol, is_buy, qty, price,
timestamp)` and reads cash and positions straight off that portfolio. LegacyStrategyAdapter gives
it a LegacyPortfolio: a real midfreq.core.portfolio.Portfolio subclass whose `execute_trade` turns the call
into a market order and sends it through the engine (RiskEngine, then the venue's
SimulatedExchange). The venue runs a LegacyFillModel, which fills at the price the strategy passed
with the original slippage wrapper's maths and charges commission = value x rate, and rejects a buy
the account cannot afford. When the venue's verdict comes back the trade is booked in the
LegacyPortfolio by the original `Portfolio.execute_trade`, so the strategy sees cash and positions
evolve exactly as they did under midfreq.backtest.backtester.Backtester. A booking that disagrees with the
venue's verdict raises LegacyDivergenceError rather than continuing silently.

Bars reach the strategy as midfreq.core.candle.Candle objects with the timestamps it was written against:
exchange-local times tagged as UTC (`VenueSpec.to_legacy_index`), even though the engine itself
runs on true UTC.

Limitations, by construction: a candle strategy has one cash balance, so all its instruments must
settle in one currency; and it groups "same timestamp" by its local-time stamps, so mixing venues
in different timezones in one legacy strategy is not meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Type

import pandas as pd

from midfreq.core.candle import Candle
from midfreq.core.portfolio import Portfolio as CorePortfolio
from midfreq.backtest.engine import BacktestEngine, BacktestEngineConfig
from midfreq.backtest.models import LegacyFillModel
from midfreq.backtest.results import BacktestResult
from midfreq.core.common.timestamps import TimeLike
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.enums import OrderSide, OrderStatus
from midfreq.core.model.fees import PercentFeeModel
from midfreq.core.model.identifiers import InstrumentId, as_instrument_id
from midfreq.data.persistence.legacy import LegacyDataReader
from midfreq.strategies.trading.strategy import Strategy, StrategyConfig
from midfreq.core.venues.spec import VenueRegistry, default_venues


class LegacyDivergenceError(AssertionError):
    """The original bookkeeping and the engine disagreed about a trade."""


class LegacyPortfolio(CorePortfolio):
    """A midfreq.core Portfolio whose trades are executed by the engine instead of by itself."""

    def __init__(self, adapter: "LegacyStrategyAdapter", initial_cash: float, commission_rate: float):
        super().__init__(initial_cash=initial_cash, commission_rate=commission_rate)
        self._adapter = adapter

    def execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=None):
        if qty <= 0:            # the original returns before doing anything
            return
        self._adapter._route_trade(symbol, is_buy, qty, price, timestamp, exit_reason)

    def book(self, symbol, is_buy, qty, price, timestamp, exit_reason=None):
        """The original Portfolio bookkeeping, unchanged."""
        CorePortfolio.execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=exit_reason)


class LegacyStrategyAdapter(Strategy):
    def __init__(self, strategy_class: Type, instrument_ids: Sequence["InstrumentId | str"],
                 bar_spec: "BarSpecification | str", initial_cash: float = 100000.0,
                 commission_rate: float = CorePortfolio.HK_FEE_RATE, slippage_bps: float = 0.0,
                 risk_manager=None, symbols: Optional[Mapping[InstrumentId, str]] = None,
                 strategy_params: Optional[Dict[str, Any]] = None,
                 venues: Optional[VenueRegistry] = None, config: Optional[StrategyConfig] = None):
        super().__init__(config or StrategyConfig(strategy_id=f"Legacy-{strategy_class.__name__}"))
        self.strategy_class = strategy_class
        self.instrument_ids = [as_instrument_id(i) for i in instrument_ids]
        self.bar_spec = bar_spec if isinstance(bar_spec, BarSpecification) else BarSpecification.from_str(bar_spec)
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps
        self.risk_manager = risk_manager
        self.strategy_params = dict(strategy_params or {})
        self._venues = venues or default_venues()
        reader = LegacyDataReader(".", self._venues)
        self.symbol_for: Dict[InstrumentId, str] = {
            iid: (symbols or {}).get(iid) or reader.legacy_symbol(iid) for iid in self.instrument_ids}
        self.instrument_for: Dict[str, InstrumentId] = {s: i for i, s in self.symbol_for.items()}
        self._fill_maths = LegacyFillModel(slippage_bps=slippage_bps)
        self._legacy_ts: Dict[tuple, pd.Timestamp] = {}
        self.legacy_portfolio: Optional[LegacyPortfolio] = None
        self.strategy = None
        self.latest_prices: Dict[str, float] = {}
        self.first_prices: Dict[str, float] = {}
        self.total_events = 0
        self._last_equity_ts = None

    # ── lifecycle ────────────────────────────────────────────────────

    def on_start(self) -> None:
        for iid in self.instrument_ids:
            self.subscribe_bars(BarType(iid, self.bar_spec))
        self.legacy_portfolio = LegacyPortfolio(self, self.initial_cash, self.commission_rate)
        if self.slippage_bps > 0:
            # midfreq.backtest.backtester publishes this only when slippage is on; basket strategies size with it.
            self.legacy_portfolio.slippage_mult = self.slippage_bps / 10000.0
        self.strategy = self.strategy_class(self.legacy_portfolio, risk_manager=self.risk_manager,
                                            **self.strategy_params)
        self.strategy.on_start()

    def on_bar(self, bar: Bar) -> None:
        iid = bar.bar_type.instrument_id
        symbol = self.symbol_for[iid]
        ts = self._legacy_timestamp(bar)
        candle = Candle(timestamp=ts, open=bar.open, high=bar.high, low=bar.low,
                        close=bar.close, volume=bar.volume)
        self.latest_prices[symbol] = candle.close
        if symbol not in self.first_prices:
            self.first_prices[symbol] = candle.close
        self.strategy.on_data(symbol, candle)
        # Equity once per timestamp, on its first candle: midfreq.backtest.backtester's exact rule.
        if ts != self._last_equity_ts:
            self.legacy_portfolio.equity_curve_detailed.append(
                {"timestamp": ts, "equity": self.legacy_portfolio.get_current_equity(self.latest_prices)})
            self._last_equity_ts = ts
        self.total_events += 1

    def _legacy_timestamp(self, bar: Bar) -> pd.Timestamp:
        spec = self._venues.get(bar.bar_type.instrument_id.venue)
        key = (spec.timezone, bar.bar_type.spec.is_intraday, bar.ts_event)
        ts = self._legacy_ts.get(key)
        if ts is None:
            ts = self._legacy_ts[key] = spec.to_legacy_index([bar.ts_event], bar.bar_type.spec)[0]
        return ts

    # ── the execute_trade route ──────────────────────────────────────

    def _route_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason) -> None:
        lp = self.legacy_portfolio
        iid = self.instrument_for.get(symbol)
        if iid is None:
            raise LegacyDivergenceError(f"strategy traded {symbol!r}, which is not one of its instruments")
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        order = self.order_factory.market(iid, side, qty, reference_price=price,
                                          tags={"exit_reason": exit_reason} if exit_reason else {})
        self.submit_order(order)
        before = len(lp.trade_history)
        if order.status is OrderStatus.FILLED:
            lp.book(symbol, is_buy, qty, order.avg_px, timestamp, exit_reason)
            if len(lp.trade_history) != before + 1:
                raise LegacyDivergenceError(f"engine filled {order} but the original bookkeeping rejected it")
        else:
            # Denied or rejected. Let the original bookkeeping reach its own verdict at the same
            # price, which also reproduces its side effects (the message it prints, and the empty
            # position entry a rejected sell leaves behind).
            lp.book(symbol, is_buy, qty, self._fill_maths.price(price, side), timestamp, exit_reason)
            if len(lp.trade_history) != before:
                raise LegacyDivergenceError(f"engine refused {order} ({order.reason}) but the original "
                                            f"bookkeeping accepted it")

    # ── results in the original format ───────────────────────────────

    def legacy_metrics(self) -> Dict[str, Any]:
        """The dict midfreq.backtest.backtester.Backtester.run returns, computed the same way."""
        from types import SimpleNamespace
        from midfreq.backtest.backtester import Backtester
        metrics = self.legacy_portfolio.calculate_metrics(self.latest_prices)
        holder = SimpleNamespace(portfolio=SimpleNamespace(initial_cash=self.legacy_portfolio.initial_cash))
        metrics["benchmark_return_pct"] = Backtester._calculate_benchmark(holder, self.first_prices, self.latest_prices)
        metrics["slippage_bps"] = self.slippage_bps
        metrics["total_events"] = self.total_events
        metrics["alpha"] = metrics["return_pct"] - metrics["benchmark_return_pct"]
        return metrics


@dataclass
class LegacyRun:
    metrics: Dict[str, Any]                       # the original Backtester.run dict
    portfolio: Optional[LegacyPortfolio]          # trade_history, equity curves, positions
    adapter: Optional[LegacyStrategyAdapter]
    result: Optional[BacktestResult]              # the engine's own view of the same run
    engine: Optional[BacktestEngine]
    skipped: List[str] = field(default_factory=list)


def run_legacy_backtest(strategy_class: Type, symbols: Sequence[str], timeframe: "str | BarSpecification",
                        data_dir: "str | Path", initial_cash: float = 100000.0,
                        commission_rate: float = CorePortfolio.HK_FEE_RATE, slippage_bps: float = 0.0,
                        risk_manager=None, start_date: Optional[TimeLike] = None,
                        end_date: Optional[TimeLike] = None, end_inclusive: bool = True,
                        venues: Optional[VenueRegistry] = None, **strategy_params) -> LegacyRun:
    """
    The BacktestEngine equivalent of `Backtester(storage, Portfolio(initial_cash, commission_rate),
    risk_manager, slippage_bps).run(strategy_class, symbols, timeframe, start_date, end_date,
    end_inclusive, **strategy_params)`, reading the same data/ cache (read-only).
    """
    venues = venues or default_venues()
    spec = timeframe if isinstance(timeframe, BarSpecification) else (
        BarSpecification.from_legacy(timeframe) if timeframe in ("1m", "5m", "1h", "4h", "1d")
        else BarSpecification.from_str(timeframe))
    reader = LegacyDataReader(data_dir, venues)
    ids = {s: reader.instrument_id(s) for s in symbols}
    currencies = {venues.get(i.venue).currency for i in ids.values()}
    if len(currencies) > 1:
        raise ValueError(f"a candle strategy has one cash balance; these symbols settle in {sorted(currencies)}")

    engine = BacktestEngine(BacktestEngineConfig(venues=venues))
    fill_model = LegacyFillModel(slippage_bps=slippage_bps)
    fee_model = PercentFeeModel(commission_rate)
    funded = False
    for venue in dict.fromkeys(i.venue for i in ids.values()):
        engine.add_venue(venue, None if funded else {venues.get(venue).currency: initial_cash},
                         fill_model=fill_model, fee_model=fee_model)
        funded = True

    loaded, skipped = [], []
    for symbol, iid in ids.items():
        bars = reader.read_bars(iid, spec, start_date, end_date, end_inclusive)
        if not bars:
            skipped.append(symbol)
            continue
        engine.add_data(bars)
        loaded.append(iid)
    if not loaded:
        return LegacyRun({}, None, None, None, engine, skipped)

    adapter = LegacyStrategyAdapter(strategy_class, loaded, spec, initial_cash, commission_rate,
                                    slippage_bps, risk_manager, {ids[s]: s for s in symbols if ids[s] in loaded},
                                    strategy_params, venues)
    engine.add_strategy(adapter)
    result = engine.run()
    return LegacyRun(adapter.legacy_metrics(), adapter.legacy_portfolio, adapter, result, engine, skipped)
