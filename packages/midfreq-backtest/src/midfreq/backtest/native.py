"""
The native (C++) engine behind the Backtester interface, and the engine choice.

midfreq.engine (packages/midfreq-engine) is a C++17 core: the merged event stream, the event loop,
portfolio accounting, the risk rules, the metrics and eleven of the thirteen candle strategies,
exposed to Python with pybind11. It reproduces PythonBacktester exactly, trade for trade and bit
for bit; this module is its adapter, and backtester.Backtester runs on it:

    NativeBacktester(storage, portfolio, risk_manager, slippage_bps).run(...)
        what Backtester.run hands a backtest to: PythonBacktester's arguments and metrics, and the
        portfolio left holding the same trades, positions and equity curves
    BatchRunner(engine, strategy_class, symbols, timeframe, storage, ...)
        many parameter sets over one universe: what grid_search and walk_forward run on. Natively,
        the data is loaded and merged once and the runs share it on all cores
    make_backtester(storage, portfolio, ..., engine=None)
        PythonBacktester for "python", else Backtester

Engines: "python" (PythonBacktester, the reference), "native" (C++, an error if it cannot run the
case), "auto" (C++ when it can, else Python). The default comes from MIDFREQ_ENGINE, else "auto".
The C++ engine runs a case only when it is sure to match: one of the ported strategy classes
exactly (not a subclass, which may override anything), whose Python is still the Python its port
was verified against (midfreq.backtest.parity), a fresh plain Portfolio, a plain RiskManager with
one of the four sizers, and UTC-indexed bars with no missing closes. Anything else (the HMM and
ML strategies, a strategy edited since its port, live portfolios, ...) runs on Python under
"auto". So the Python strategies stay the definition: changing one never changes a result, it
only runs on Python until its port is updated and re-verified.

The C++ hands back each run's daily returns and the Sharpe ratio is finished here with Python's
statistics module, which works in exact arithmetic; every other number is computed in C++ with
the same operations in the same order as the Python code.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
import statistics
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Type

import numpy as np
import pandas as pd

from midfreq.backtest.backtester import Backtester, PythonBacktester
from midfreq.backtest.parity import port_reason
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.core.risk_manager import (EqualDollarSizer, FixedFractionalSizer, FixedQuantitySizer,
                                       KellyCriterionSizer, RiskManager)
from midfreq.data.storage import DataStorage
from midfreq.strategies import candle_strategies as cs

ENGINES = ("auto", "native", "python")

log = logging.getLogger(__name__)
_warned: set = set()


def _stale(cls: type) -> Optional[str]:
    """Why the C++ port of `cls` is not used (its Python changed since it was verified), or None.
    Logged once per strategy and process, so a slow optimiser has a visible reason."""
    reason = port_reason(cls)
    if reason and cls not in _warned:
        _warned.add(cls)
        log.warning("%s runs on the Python backtester: %s", cls.__name__, reason)
    return reason


class NativeUnavailable(RuntimeError):
    """engine="native" was asked for a case the C++ engine cannot run."""


# ─── Which strategies are native, and their parameters as the C++ reads them ──────────────────

def _cross_sectional(s) -> Dict[str, Any]:
    return {"lookback": s.lookback, "top_n": s.top_n, "rebalance_every": s.rebalance_every}


def _zscore(s) -> Dict[str, Any]:
    return {"lookback": s.lookback, "entry_z": s.entry_z, "exit_z": s.exit_z}


# exact class -> (native name, the constructed strategy's effective parameters, which of them
# must be integers). Parameters are read off a constructed Python strategy, so defaults and
# normalisation (rebalance_every >= 1, the ensemble's fixed sub-strategies) are Python's own.
_NATIVE: Dict[type, Tuple[str, Callable[[Any], Dict[str, Any]], Tuple[str, ...]]] = {
    cs.MovingAverageCrossover: ("sma_crossover", lambda s: {"fast_period": s.fast, "slow_period": s.slow},
                                ("fast_period", "slow_period")),
    cs.RSIStrategy: ("rsi", lambda s: {"rsi_period": s.period, "oversold": s.oversold,
                                       "overbought": s.overbought}, ("rsi_period",)),
    cs.MACDStrategy: ("macd", lambda s: {"fast_ema": s.fast_ema, "slow_ema": s.slow_ema,
                                         "signal_period": s.signal_period},
                      ("fast_ema", "slow_ema", "signal_period")),
    cs.BollingerBandsStrategy: ("bollinger_bands", lambda s: {"bb_period": s.period, "num_std": s.num_std},
                                ("bb_period",)),
    cs.ZScoreMeanReversion: ("zscore_mean_reversion", _zscore, ("lookback",)),
    cs.PairsTradingStrategy: ("pairs_trading", _zscore, ("lookback",)),
    cs.EnsembleStrategy: ("ensemble_voting", lambda s: {
        "consensus_threshold": s.consensus_threshold,
        "rsi_period": s.rsi_strat.period, "rsi_oversold": s.rsi_strat.oversold,
        "rsi_overbought": s.rsi_strat.overbought,
        "macd_fast": s.macd_strat.fast_ema, "macd_slow": s.macd_strat.slow_ema,
        "macd_signal": s.macd_strat.signal_period,
        "bb_period": s.bb_strat.period, "bb_num_std": s.bb_strat.num_std,
    }, ("rsi_period", "macd_fast", "macd_slow", "macd_signal", "bb_period")),
    cs.RegimeSwitchStrategy: ("regime_switch", lambda s: {
        "regime_lookback": s.regime_lookback, "er_threshold": s.er_threshold,
        "fast_period": s.fast, "slow_period": s.slow, "bb_period": s.bb_period, "num_std": s.num_std,
        "min_dwell": s.min_dwell, "history_keep": s._history_keep,
    }, ("regime_lookback", "fast_period", "slow_period", "bb_period", "min_dwell", "history_keep")),
    cs.CrossSectionalReversal: ("cross_sectional_reversal", _cross_sectional,
                                ("lookback", "top_n", "rebalance_every")),
    cs.CrossSectionalMomentum: ("cross_sectional_momentum", _cross_sectional,
                                ("lookback", "top_n", "rebalance_every")),
    cs.BuyAndHoldRiskOverlay: ("buy_and_hold_risk_overlay", lambda s: {
        "stop_loss_pct": s.risk_manager.stop_loss_pct or 0.0,
        "trailing_stop_pct": s.risk_manager.trailing_stop_pct or 0.0,
    }, ()),
}


def native_strategy_classes() -> List[type]:
    """The strategy classes the C++ engine implements."""
    return list(_NATIVE)


def resolve_engine(engine: Optional[str] = None) -> str:
    name = (engine or os.environ.get("MIDFREQ_ENGINE") or "auto").strip().lower()
    if name not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, not {name!r}")
    return name


def native_available() -> bool:
    """True when midfreq-engine is installed and its extension built (never on the VM)."""
    try:
        from midfreq import engine
    except ImportError:
        return False
    return engine.available()


def _core():
    from midfreq import engine
    return engine.core()


def _threads() -> int:
    try:
        return max(0, int(os.environ.get("MIDFREQ_ENGINE_THREADS", "0")))
    except ValueError:
        return 0


def _sum_mode() -> str:
    from midfreq import engine
    return engine.sum_mode()


# ─── Eligibility ─────────────────────────────────────────────────────────────────────────────

def _risk_config(risk_manager: Optional[RiskManager]) -> Tuple[Optional[dict], Optional[str]]:
    """The risk manager as the C++ reads it, or why it cannot be (None, reason)."""
    if risk_manager is None:
        return None, None
    if type(risk_manager) is not RiskManager:
        return None, f"risk manager {type(risk_manager).__name__} is not a plain RiskManager"
    sizer = risk_manager.position_sizer
    kind = type(sizer)
    if kind is FixedQuantitySizer:
        s = {"method": "fixed_quantity", "qty": sizer.qty}
    elif kind is FixedFractionalSizer:
        s = {"method": "fixed_fractional", "risk_pct": sizer.risk_pct}
    elif kind is KellyCriterionSizer:
        s = {"method": "kelly", "fraction": sizer.fraction, "max_pct": sizer.max_pct}
    elif kind is EqualDollarSizer:
        s = {"method": "equal_dollar", "n_positions": sizer.n_positions, "cost_buffer": sizer.cost_buffer}
    else:
        return None, f"position sizer {kind.__name__} has no native version"
    if s.get("qty") is not None and not isinstance(s["qty"], int):
        return None, "FixedQuantitySizer.qty is not an integer"
    if s.get("n_positions") is not None and not isinstance(s["n_positions"], int):
        return None, "EqualDollarSizer.n_positions is not an integer"
    return {"stop_loss_pct": risk_manager.stop_loss_pct, "trailing_stop_pct": risk_manager.trailing_stop_pct,
            "take_profit_pct": risk_manager.take_profit_pct, "max_drawdown_pct": risk_manager.max_drawdown_pct,
            "sizer": s}, None


def _native_params(strategy) -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    """(native name, parameters, None) for a constructed strategy, or (None, None, reason)."""
    entry = _NATIVE.get(type(strategy))
    if entry is None:
        return None, None, f"{type(strategy).__name__} has no native version"
    reason = _stale(type(strategy))
    if reason:
        return None, None, reason
    name, extract, ints = entry
    params = extract(strategy)
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, None, f"parameter {key}={value!r} is not a number"
        if key in ints and not isinstance(value, int):
            return None, None, f"parameter {key}={value!r} is not an integer"
        if key in ints and value < 1 and key != "min_dwell":
            return None, None, f"parameter {key}={value!r} is below 1"
    return name, params, None


def _fresh_portfolio_reason(portfolio) -> Optional[str]:
    if type(portfolio) is not Portfolio:
        return f"portfolio {type(portfolio).__name__} is not a plain Portfolio"
    if "execute_trade" in vars(portfolio):
        return "the portfolio's execute_trade is wrapped"
    if (portfolio.positions or portfolio.trade_history or portfolio.equity_curve
            or portfolio.equity_curve_detailed or portfolio.cash != portfolio.initial_cash):
        return "the portfolio has already traded"
    if not portfolio.initial_cash > 0:
        return "initial cash is not positive"
    return None


# ─── Market data ─────────────────────────────────────────────────────────────────────────────

def _bounds(start_date, end_date, end_inclusive: bool) -> Tuple[Optional[int], Optional[int]]:
    """Backtester.run's date filter as [start, cutoff) in UTC nanoseconds."""
    start = pd.to_datetime(start_date, utc=True).as_unit("ns").value if start_date else None
    cutoff = None
    if end_date:
        c = pd.to_datetime(end_date, utc=True)
        if end_inclusive:
            c = c + pd.Timedelta(days=1)
        cutoff = c.as_unit("ns").value
    return start, cutoff


class MarketData:
    """The bars of a universe, loaded once and merged in C++, for any number of runs.

    `symbols` are the ones with any bars, in the order asked (duplicates once, as the Python
    backtester's dict keeps them). `reason` says why the C++ engine cannot use the data, if so.
    """

    def __init__(self, storage: DataStorage, symbols: Sequence[str], timeframe: Timeframe):
        self.timeframe = timeframe
        self.reason: Optional[str] = None
        self.symbols: List[str] = []
        self.candles: Dict[str, int] = {}
        frames: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = storage.load_data(symbol.replace('.', '_'), timeframe.value)
            if df is None or df.empty:
                continue
            frames[symbol] = df
        cols = ("open", "high", "low", "close", "volume")
        arrays = {c: [] for c in ("ts",) + cols}
        for symbol, df in frames.items():
            idx = df.index
            if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None or str(idx.tz) != "UTC":
                self.reason = f"{symbol}: the index is not UTC timestamps"
                break
            if idx.hasnans:
                self.reason = f"{symbol}: the index has missing timestamps"
                break
            missing = [c for c in cols if c not in df.columns]
            if missing:
                self.reason = f"{symbol}: no {', '.join(missing)} column"
                break
            try:
                values = {c: np.ascontiguousarray(df[c].to_numpy(dtype="float64")) for c in cols}
            except (TypeError, ValueError):
                self.reason = f"{symbol}: a price column is not numeric"
                break
            if np.isnan(values["close"]).any():
                self.reason = f"{symbol}: missing closes"
                break
            self.symbols.append(symbol)
            self.candles[symbol] = len(df)
            arrays["ts"].append(np.ascontiguousarray(idx.as_unit("ns").asi8, dtype=np.int64))
            for c in cols:
                arrays[c].append(values[c])
        self.data = None
        if self.reason is None:
            self.data = _core().MarketData(self.symbols, arrays["ts"], arrays["open"], arrays["high"],
                                           arrays["low"], arrays["close"], arrays["volume"])

    def range(self, start: Optional[int], cutoff: Optional[int]) -> Tuple[int, int]:
        return self.data.range(start, cutoff)


# ─── Results back into Python's shapes ──────────────────────────────────────────────────────

def exact_sharpe(daily_returns: Sequence[float]) -> float:
    """Portfolio.calculate_metrics' Sharpe ratio over these returns, in its exact arithmetic."""
    returns = [float(r) for r in daily_returns]
    if not returns:
        return 0.0
    mean_ret = statistics.mean(returns)
    std_ret = statistics.stdev(returns) if len(returns) > 1 else 0.0
    return (mean_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0.0


def _metrics(result: dict, symbols: Sequence[str], slippage_bps: float) -> Dict[str, Any]:
    """A native run's result as the dict Backtester.run returns."""
    m = result["metrics"]
    return {
        'initial_cash': m['initial_cash'],
        'final_equity': m['final_equity'],
        'return_pct': m['return_pct'],
        'total_trades': m['total_trades'],
        'cash_balance': m['cash_balance'],
        'open_positions': {symbols[s]: {'qty': int(q), 'entry_price': float(e)} for s, q, e in result['positions']},
        'sharpe_ratio': exact_sharpe(result['daily_returns']),
        'max_drawdown': m['max_drawdown'],
        'win_rate': m['win_rate'],
        'profit_factor': m['profit_factor'],
        'benchmark_return_pct': m['benchmark_return_pct'],
        'slippage_bps': slippage_bps,
        'total_events': result['total_events'],
        'alpha': m['return_pct'] - m['benchmark_return_pct'],
    }


def _timestamps(ns: np.ndarray) -> List[pd.Timestamp]:
    return list(pd.DatetimeIndex(np.asarray(ns, dtype="datetime64[ns]"), tz="UTC")) if len(ns) else []


def _fill_portfolio(portfolio: Portfolio, result: dict, symbols: Sequence[str]) -> None:
    """Leave the portfolio as Backtester.run would: cash, positions, trades and both curves."""
    reasons = _core().exit_reasons()
    portfolio.cash = result['cash']
    portfolio.positions = {symbols[s]: {'qty': int(q), 'entry_price': float(e)} for s, q, e in result['positions']}

    t = result['trades']
    stamps = _timestamps(t['ts'])
    history = []
    for i, ts in enumerate(stamps):
        record = {
            'timestamp': ts,
            'symbol': symbols[int(t['symbol'][i])],
            'action': 'BUY' if t['is_buy'][i] else 'SELL',
            'qty': int(t['qty'][i]),
            'price': float(t['price'][i]),
            'commission': float(t['commission'][i]),
            'cash_after': float(t['cash_after'][i]),
        }
        reason = reasons[int(t['reason'][i])]
        if reason:
            record['exit_reason'] = reason
        history.append(record)
    portfolio.trade_history = history

    for attr, key in (('equity_curve', 'trade_equity'), ('equity_curve_detailed', 'equity')):
        curve = result[key]
        setattr(portfolio, attr, [{'timestamp': ts, 'equity': float(e)}
                                  for ts, e in zip(_timestamps(curve['ts']), curve['equity'])])


def _later(so_far, ts_ns: Optional[int]):
    """runstore.latest_candle's rule: the later of the cut-off so far and this run's last bar."""
    if ts_ns is None:
        return so_far
    last = pd.Timestamp(ts_ns, tz="UTC")
    if so_far is not None and pd.Timestamp(so_far) >= last:
        return so_far
    return last


# ─── The drop-in backtester ─────────────────────────────────────────────────────────────────

class NativeBacktester:
    """Backtester.run on the C++ engine, with the same arguments and results.

    fallback=True ("auto"): a case the C++ engine cannot run goes to PythonBacktester instead.
    After run(), `engine_used` is "native" or "python" and `fallback_reason` says why Python.
    `data` may be a MarketData already loaded for the same storage, symbols and timeframe.
    """

    def __init__(self, storage: DataStorage, portfolio: Portfolio, risk_manager: RiskManager = None,
                 slippage_bps: float = 0.0, fallback: bool = True, data: Optional[MarketData] = None,
                 python: Optional[Callable[[], PythonBacktester]] = None):
        # `python`: the PythonBacktester to fall back to (Backtester passes its own, made once, so
        # the slippage wrapper is installed once however many runs fall back)
        self._python_factory = python
        self.storage = storage
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self.slippage_bps = slippage_bps
        self.fallback = fallback
        self.data = data
        self.engine_used: Optional[str] = None
        self.fallback_reason: Optional[str] = None

    def _python(self, reason: str, strategy_class, symbols, timeframe, **kwargs):
        if not self.fallback:
            raise NativeUnavailable(reason)
        self.engine_used, self.fallback_reason = "python", reason
        bt = self._python_factory() if self._python_factory else PythonBacktester(
            storage=self.storage, portfolio=self.portfolio, risk_manager=self.risk_manager,
            slippage_bps=self.slippage_bps)
        return bt.run(strategy_class, symbols, timeframe, **kwargs)

    def run(self, strategy_class: Type[cs.BaseStrategy], symbols: list, timeframe: Timeframe,
            start_date: datetime = None, end_date: datetime = None,
            progress_callback: Optional[Callable[[float], None]] = None,
            end_inclusive: bool = True, **strategy_params) -> Dict[str, Any]:
        args = dict(start_date=start_date, end_date=end_date, progress_callback=progress_callback,
                    end_inclusive=end_inclusive, **strategy_params)
        if not native_available():
            return self._python("the native engine is not built", strategy_class, symbols, timeframe, **args)
        reason = _fresh_portfolio_reason(self.portfolio)
        risk, risk_reason = _risk_config(self.risk_manager)
        reason = reason or risk_reason
        if reason:
            return self._python(reason, strategy_class, symbols, timeframe, **args)

        # Constructed as Backtester.run does (bad parameters raise here, before any data is read);
        # its attributes are the parameters the C++ strategy runs with.
        strategy = strategy_class(self.portfolio, risk_manager=self.risk_manager, **strategy_params)
        name, params, reason = _native_params(strategy)
        if reason:
            return self._python(reason, strategy_class, symbols, timeframe, **args)

        print("=== Initializing Backtest Simulator (native C++ engine) ===")
        print(f"Cash: ${self.portfolio.initial_cash:,.2f} | Timeframe: {timeframe.value} | Symbols: {symbols}")
        if self.slippage_bps > 0:
            print(f"Slippage: {self.slippage_bps:.1f} bps")

        data = self.data
        if data is None or data.timeframe != timeframe:
            data = MarketData(self.storage, symbols, timeframe)
        if data.reason:
            return self._python(data.reason, strategy_class, symbols, timeframe, **args)
        start, cutoff = _bounds(start_date, end_date, end_inclusive)
        lo, hi = data.range(start, cutoff)
        if hi <= lo:
            print("No data available to run simulation. Aborting.")
            self.engine_used = "native"
            return {}
        print(f"  [+] Loaded {hi - lo} candles across {len(data.symbols)} symbols")

        strategy.on_start()
        print(f"\n=== Simulating Trades ({hi - lo} events, chronological) ===")
        result = _core().run_backtest(
            data.data, name, params, initial_cash=self.portfolio.initial_cash,
            commission_rate=self.portfolio.commission_rate, slippage_bps=self.slippage_bps,
            sum_mode=_sum_mode(), risk=risk, start=start, cutoff=cutoff, record=True,
            progress=progress_callback)
        print("\n=== Simulation Complete ===")
        self.engine_used, self.fallback_reason = "native", None

        if self.slippage_bps > 0:
            self.portfolio.slippage_mult = self.slippage_bps / 10000.0
        _fill_portfolio(self.portfolio, result, data.symbols)
        seen, last = result['seen'], result['last_price']
        latest = {data.symbols[i]: float(last[i]) for i in range(len(data.symbols)) if seen[i]}
        # the reference formulas over the state the C++ left: identical to Backtester.run's
        metrics = self.portfolio.calculate_metrics(latest)
        metrics['benchmark_return_pct'] = result['metrics']['benchmark_return_pct']
        metrics['slippage_bps'] = self.slippage_bps
        metrics['total_events'] = result['total_events']
        metrics['alpha'] = metrics['return_pct'] - metrics['benchmark_return_pct']
        return metrics


def make_backtester(storage: DataStorage, portfolio: Portfolio, risk_manager: RiskManager = None,
                    slippage_bps: float = 0.0, engine: Optional[str] = None):
    """PythonBacktester for "python"; Backtester (C++ underneath) for "native" and "auto"."""
    name = resolve_engine(engine)
    if name == "python":
        return PythonBacktester(storage=storage, portfolio=portfolio, risk_manager=risk_manager,
                                slippage_bps=slippage_bps)
    return Backtester(storage, portfolio, risk_manager=risk_manager, slippage_bps=slippage_bps, engine=name)


# ─── Many parameter sets: the optimiser's runner ───────────────────────────────────────────

class BatchRunner:
    """Runs one strategy over one universe for many parameter sets and date ranges.

        runner.run(param_list, start, end, end_inclusive=True) -> [(metrics, last_bar)]

    metrics is what Backtester.run returns ({} for a run that raised or had no data); last_bar is
    the run's last candle (for the saved run's data cut-off). On Python every run is a
    PythonBacktester.run with its output silenced, exactly as the optimiser always ran them.
    """

    def __init__(self, engine: Optional[str], strategy_class: Type[cs.BaseStrategy], symbols: List[str],
                 timeframe: Timeframe, storage: DataStorage, initial_capital: float, commission_rate: float,
                 risk_manager: Optional[RiskManager] = None, slippage_bps: float = 0.0):
        self.strategy_class = strategy_class
        self.symbols = symbols
        self.timeframe = timeframe
        self.storage = storage
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.risk_manager = risk_manager
        self.slippage_bps = slippage_bps
        self.requested = resolve_engine(engine)
        self.engine = "python"
        self.fallback_reason: Optional[str] = None
        self._data: Optional[MarketData] = None
        self._risk: Optional[dict] = None
        if self.requested != "python":
            reason = self._native_reason()
            if reason is None:
                self.engine = "native"
            elif self.requested == "native":
                raise NativeUnavailable(reason)
            else:
                self.fallback_reason = reason

    def _native_reason(self) -> Optional[str]:
        if not native_available():
            return "the native engine is not built"
        if self.strategy_class not in _NATIVE:
            return f"{self.strategy_class.__name__} has no native version"
        reason = _stale(self.strategy_class)
        if reason:
            return reason
        if not self.initial_capital > 0:
            return "initial capital is not positive"
        self._risk, reason = _risk_config(self.risk_manager)
        if reason:
            return reason
        self._data = MarketData(self.storage, self.symbols, self.timeframe)
        return self._data.reason

    def run(self, param_list: Sequence[Dict[str, Any]], start_date=None, end_date=None,
            end_inclusive: bool = True) -> List[Tuple[Dict[str, Any], Optional[pd.Timestamp]]]:
        if self.engine == "native":
            return self._run_native(param_list, start_date, end_date, end_inclusive)
        return [self._run_python(p, start_date, end_date, end_inclusive) for p in param_list]

    def _run_python(self, params, start_date, end_date, end_inclusive):
        from midfreq.backtest.runstore import latest_candle
        portfolio = Portfolio(initial_cash=self.initial_capital, commission_rate=self.commission_rate)
        bt = PythonBacktester(storage=self.storage, portfolio=portfolio, risk_manager=self.risk_manager,
                              slippage_bps=self.slippage_bps)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                metrics = bt.run(self.strategy_class, symbols=self.symbols, timeframe=self.timeframe,
                                 start_date=start_date, end_date=end_date, end_inclusive=end_inclusive, **params)
            except Exception:
                metrics = {}
        return metrics, latest_candle(portfolio)

    def _run_native(self, param_list, start_date, end_date, end_inclusive):
        out: List[Tuple[Dict[str, Any], Optional[pd.Timestamp]]] = [({}, None)] * len(param_list)
        jobs: List[Tuple[int, str, dict]] = []
        for i, params in enumerate(param_list):
            # constructed as Backtester.run does: a combination the class rejects is a failed run
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    strategy = self.strategy_class(Portfolio(initial_cash=self.initial_capital,
                                                             commission_rate=self.commission_rate),
                                                   risk_manager=self.risk_manager, **params)
            except Exception:
                continue
            name, native_params, reason = _native_params(strategy)
            if reason:  # e.g. a period of 0: Python decides what that does
                out[i] = self._run_python(params, start_date, end_date, end_inclusive)
                continue
            jobs.append((i, name, native_params))
        if not jobs:
            return out
        start, cutoff = _bounds(start_date, end_date, end_inclusive)
        results = _core().run_batch(
            self._data.data, jobs[0][1], [p for _, _, p in jobs], initial_cash=self.initial_capital,
            commission_rate=self.commission_rate, slippage_bps=self.slippage_bps, sum_mode=_sum_mode(),
            risk=self._risk, start=start, cutoff=cutoff, threads=_threads())
        for (i, _, _), r in zip(jobs, results):
            if r.get("error") is not None or not r["ok"]:
                out[i] = ({}, _later(None, r.get("last_ts")))
                continue
            out[i] = (_metrics(r, self._data.symbols, self.slippage_bps), _later(None, r["last_ts"]))
        return out
