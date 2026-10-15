"""
BacktestEngine, after NautilusTrader's `backtest.engine.BacktestEngine`.

    engine = BacktestEngine()
    engine.add_venue("XHKG", starting_balances={"HKD": 1_000_000})
    engine.add_venue("XNAS", starting_balances={"USD": 100_000})
    engine.add_instrument(tencent); engine.add_instrument(apple)
    engine.add_data(catalog.bars(tencent_hourly)); engine.add_data(catalog.bars(apple_hourly))
    engine.add_strategy(MyStrategy(config))
    result = engine.run()
    print(result.summary())

Inside, per run:
    DataEngine        merges every bar stream in UTC order and groups equal timestamps;
    SimulatedExchange one per venue: resting orders, fills, lots, fees, cash checks;
    RiskEngine        pre-trade checks and optional stop exits (core.risk_manager rules);
    ExecutionEngine   routes orders and applies venue events to orders and the Portfolio;
    MessageBus        carries bars, slices, signals and order events to strategies;
    Portfolio         one cash Account per currency, open positions, equity history.

For each timestamp the engine: moves the clock; for each bar, lets the venue fill orders resting
from earlier bars, stores the bar, runs risk exits and publishes it; then publishes the TimeSlice
and records equity per currency.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd

from mft.accounting import Portfolio
from mft.backtest.data_engine import DataEngine
from mft.backtest.exchange import SimulatedExchange
from mft.backtest.models import FillModel
from mft.backtest.results import BacktestResult, currency_metrics
from mft.cache import Cache
from mft.common.clock import TestClock
from mft.common.msgbus import MessageBus
from mft.common.timestamps import TimeLike, to_ns
from mft.execution import ExecutionEngine
from mft.model.data import Bar
from mft.model.fees import FeeModel
from mft.model.identifiers import InstrumentId, Venue
from mft.model.instruments import Instrument
from mft.risk import RiskConfig, RiskEngine
from mft.trading.strategy import Strategy
from mft.venues.spec import VenueRegistry, VenueSpec, default_venues

log = logging.getLogger(__name__)


@dataclass
class BacktestEngineConfig:
    risk: RiskConfig = field(default_factory=RiskConfig)
    bar_capacity: int = 1000            # bars kept per bar type in the cache
    venues: Optional[VenueRegistry] = None


class BacktestEngine:
    def __init__(self, config: Optional[BacktestEngineConfig] = None):
        self.config = config or BacktestEngineConfig()
        self.venues = self.config.venues or default_venues()
        self.clock = TestClock()
        self.msgbus = MessageBus()
        self.cache = Cache(self.config.bar_capacity)
        self.portfolio = Portfolio(self.cache)
        self.risk_engine = RiskEngine(self.config.risk, self.cache, self.portfolio, self.clock)
        self.execution = ExecutionEngine(self.cache, self.portfolio, self.risk_engine, self.msgbus, self.clock)
        self.data_engine = DataEngine()
        self.exchanges: Dict[Venue, SimulatedExchange] = {}
        self.strategies: List[Strategy] = []
        self._first_close: Dict[InstrumentId, float] = {}
        self._result: Optional[BacktestResult] = None

    # ── setup ────────────────────────────────────────────────────────

    def add_venue(self, venue: "str | Venue | VenueSpec",
                  starting_balances: Optional[Mapping[str, float]] = None,
                  fill_model: Optional[FillModel] = None,
                  fee_model: Optional[FeeModel] = None) -> SimulatedExchange:
        """Add a simulated venue. Fees default to the venue's model in config/venues.json and fills
        to the bar close without slippage. `starting_balances` funds the per-currency accounts; a
        venue settling in an already funded currency (XNYS after XNAS) shares that account."""
        spec = venue if isinstance(venue, VenueSpec) else self.venues.get(venue)
        if spec.venue in self.exchanges:
            raise ValueError(f"venue {spec.venue} was already added")
        exchange = SimulatedExchange(spec, fill_model or FillModel(), fee_model or spec.fee_model(),
                                     self.cache, self.portfolio, self.clock, self.execution.handle)
        self.exchanges[spec.venue] = exchange
        self.execution.exchanges[spec.venue] = exchange
        for ccy, amount in (starting_balances or {}).items():
            self.portfolio.fund(ccy, amount)
        return exchange

    def add_instrument(self, instrument: Instrument) -> None:
        if instrument.id.venue not in self.exchanges:
            raise ValueError(f"add venue {instrument.id.venue} before its instruments")
        self.cache.add_instrument(instrument)
        self.portfolio.account(instrument.currency)

    def add_data(self, bars: Iterable[Bar]) -> None:
        """One stream of bars (any bar type). An instrument not added explicitly gets the venue's
        default trading rules."""
        bars = list(bars)
        if not bars:
            return
        for iid in {b.bar_type.instrument_id for b in bars}:
            if self.cache.instrument(iid) is None:
                if iid.venue not in self.exchanges:
                    raise ValueError(f"add venue {iid.venue} before data for {iid}")
                log.info("no instrument definition for %s; using %s defaults", iid, iid.venue)
                self.add_instrument(self.exchanges[iid.venue].spec.default_instrument(str(iid.symbol)))
        self.data_engine.add(bars)

    def add_strategy(self, strategy: Strategy) -> None:
        strategy.register(clock=self.clock, msgbus=self.msgbus, cache=self.cache,
                          portfolio=self.portfolio, execution=self.execution)
        self.strategies.append(strategy)

    # ── run ──────────────────────────────────────────────────────────

    def run(self, start: Optional[TimeLike] = None, end: Optional[TimeLike] = None) -> BacktestResult:
        if self._result is not None:
            raise RuntimeError("this engine has already run; create a new BacktestEngine")
        start_ns = to_ns(start) if start is not None else None
        end_ns = to_ns(end) if end is not None else None
        for strategy in self.strategies:
            strategy.on_start()

        bars = slices = 0
        first_ts = last_ts = None
        publish = self.msgbus.publish
        for time_slice in self.data_engine.iter_slices(start_ns, end_ns):
            self.clock.set_time(time_slice.ts_event)
            for bar in time_slice.bars:
                iid = bar.bar_type.instrument_id
                self.exchanges[iid.venue].process_bar(bar)
                self.cache.add_bar(bar)
                if iid not in self._first_close:
                    self._first_close[iid] = bar.close
                self.risk_engine.on_bar(bar)
                publish(f"data.bars.{bar.bar_type}", bar)
            publish("data.slices", time_slice)
            self.portfolio.snapshot(time_slice.ts_event)
            bars += len(time_slice)
            slices += 1
            first_ts = first_ts if first_ts is not None else time_slice.ts_event
            last_ts = time_slice.ts_event

        for strategy in self.strategies:
            strategy.on_stop()
        self._result = self._build_result(bars, slices, first_ts, last_ts)
        return self._result

    @property
    def result(self) -> Optional[BacktestResult]:
        return self._result

    # ── results ──────────────────────────────────────────────────────

    def _build_result(self, bars: int, slices: int, first_ts, last_ts) -> BacktestResult:
        p = self.portfolio
        metrics, curves, positions = {}, {}, {}
        for ccy, acct in p.accounts.items():
            ids = [i.id for i in self.cache.instruments() if i.currency == ccy]
            last = {str(i): self.cache.last_price(i) for i in ids if self.cache.last_price(i) is not None}
            first = {str(i): self._first_close[i] for i in ids if i in self._first_close}
            open_pos = {str(iid): {"qty": pos.quantity, "avg_px": pos.avg_px}
                        for iid, pos in p.positions.items() if pos.currency == ccy}
            positions.update({k: {**v, "currency": ccy} for k, v in open_pos.items()})
            points = p.equity_history.get(ccy, [])
            curves[ccy] = pd.Series([e for _, e in points],
                                    index=pd.to_datetime([t for t, _ in points], unit="ns", utc=True),
                                    name=f"equity_{ccy}", dtype=float)
            log_ccy = [t for t in p.trade_log if t["currency"] == ccy]
            metrics[ccy] = currency_metrics(acct.starting_balance, acct.balance, open_pos, log_ccy,
                                            points, last, first)
        return BacktestResult(
            starting_balances={c: a.starting_balance for c, a in p.accounts.items()},
            final_balances={c: a.balance for c, a in p.accounts.items()},
            final_equity={c: p.equity(c) for c in p.accounts},
            trade_log=list(p.trade_log),
            orders=self.cache.orders(),
            equity_curves=curves,
            open_positions=positions,
            metrics=metrics,
            bars_processed=bars,
            slices_processed=slices,
            first_ts=pd.Timestamp(first_ts, unit="ns", tz="UTC") if first_ts is not None else None,
            last_ts=pd.Timestamp(last_ts, unit="ns", tz="UTC") if last_ts is not None else None,
            venues=[str(v) for v in self.exchanges],
        )
