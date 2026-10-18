"""
The runs the dashboard starts: a backtest, a comparison of strategies and an optimization (grid
search or walk-forward). They make the same library calls with the same arguments as the research
scripts, so the same inputs give the same numbers:

    plan(kind, request, settings) -> Plan     # checked up front, in the request (RunError -> 422)
    execute(plan, progress) -> result model   # the run itself, in a worker (runner.py)

A Plan is plain data, so it can be sent to a worker process. Keep this in step with app.py until
that is retired.
"""
import contextlib
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from midfreq.api import schemas as s
from midfreq.api.convert import plain, records, utc
from midfreq.api.labels import UNIVERSE_LABELS
from midfreq.api.settings import Settings
from midfreq.backtest.native import make_backtester
from midfreq.backtest.optimizer import OBJECTIVES, generate_param_grid, grid_search, walk_forward
from midfreq.core.candle import Timeframe
from midfreq.core.config import ConfigLoader
from midfreq.core.portfolio import Portfolio
from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

KINDS = ("backtest", "compare", "optimize")
TIMEFRAMES = ("1h", "1d")

# Per market: the currency, and the commission per side app.py defaults to. HK 0.16% is
# Portfolio.HK_FEE_RATE (commission, platform fee, 0.1% stamp duty and levies, calibrated against
# Moomoo paper fills); US 0.005% is moomoo Singapore's flat US$0.99 + GST per order at the
# position sizes the studies trade (as run_live.py and the S&P 100 studies use).
MARKETS = {
    "US": {"currency": "USD", "commission_rate": 0.00005},
    "HK": {"currency": "HKD", "commission_rate": Portfolio.HK_FEE_RATE},
}

# A symbol as the cache names it (as reference.SYMBOL): no path separators.
SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

# grid_search's metric columns; the rest of a row are the parameters (app.py's param_cols)
GRID_METRICS = ("return_pct", "sharpe_ratio", "max_drawdown", "win_rate", "profit_factor",
                "total_trades", "alpha")

Progress = Callable[[float, str], None]


class RunError(ValueError):
    """A run that cannot be done as asked. The message is shown to the user as it is."""


@dataclass(frozen=True)
class Run:
    """One strategy to run: its registry name, a label unique within the plan, its parameters
    (every one, defaults filled in) and the symbols it trades."""
    strategy: str
    label: str
    params: Dict[str, Any]
    symbols: Tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    kind: str
    data_dir: str
    market: str
    symbol: Optional[str]         # the stock single-stock strategies trade (and charted); None for baskets
    timeframe: str
    start: date
    end: date
    initial_capital: float
    commission_rate: float
    slippage_bps: float
    risk: Dict[str, Any]          # RiskSettings as a dict
    runs: Tuple[Run, ...]
    method: str = "grid"          # optimize only, from here on
    objective: str = "sharpe_ratio"
    max_combinations: int = 200
    n_splits: int = 3
    train_pct: float = 0.6


# ── what app.py decides from the sidebar ────────────────────────────────────

def is_basket(name: str) -> bool:
    """Cross-sectional strategies rank a universe against itself (app.py)."""
    return name.startswith("Cross-Sectional")


def _folder(symbol: str) -> str:
    """The cache's folder for a symbol (DataStorage: HK.00700 -> HK_00700)."""
    return symbol.upper().replace(".", "_")


def _cached(data_dir: Path, symbol: str, timeframe: str) -> bool:
    # Checked before anything loads: DataStorage.load_data creates a missing symbol's folder.
    return (Path(data_dir) / _folder(symbol) / f"{timeframe}.parquet").is_file()


def build_risk_manager(risk: Dict[str, Any], n_positions: int = 1) -> RiskManager:
    """app.py's build_risk_manager: the sidebar's stops, drawdown halt and position sizing.
    (equal_dollar, which the sidebar does not offer, splits equity over `n_positions`.)"""
    sizing = risk["sizing"]
    if sizing == "fixed_quantity":
        sizer = create_position_sizer(SizingMethod.FIXED_QUANTITY, qty=int(risk["qty"]))
    elif sizing == "percent_risk":
        sizer = create_position_sizer(SizingMethod.FIXED_FRACTIONAL, risk_pct=risk["risk_pct"])
    elif sizing == "kelly":
        sizer = create_position_sizer(SizingMethod.KELLY, fraction=risk["fraction"], max_pct=risk["max_pct"])
    else:
        sizer = create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=n_positions)
    return _risk_manager(risk, sizer)


def _risk_manager(risk: Dict[str, Any], sizer) -> RiskManager:
    return RiskManager(stop_loss_pct=risk.get("stop_loss_pct"), trailing_stop_pct=risk.get("trailing_stop_pct"),
                       take_profit_pct=risk.get("take_profit_pct"), max_drawdown_pct=risk["max_drawdown_pct"],
                       position_sizer=sizer)


def risk_manager_for(run: Run, risk: Dict[str, Any]) -> RiskManager:
    """app.py's risk_manager_for: equal-dollar sizing across top_n for baskets (keeping the
    sidebar's stops and drawdown halt), the sidebar's sizing otherwise."""
    if not is_basket(run.strategy):
        return build_risk_manager(risk, n_positions=len(run.symbols))
    sizer = create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=int(run.params.get("top_n", 2)))
    return _risk_manager(risk, sizer)


# ── planning: everything that can be checked before a run starts ────────────

def _config(settings: Settings) -> Optional[ConfigLoader]:
    path = settings.config_dir / "symbols.json"
    return ConfigLoader(str(path)) if path.is_file() else None


def _symbol(value: str, market: str, field: str) -> str:
    symbol = (value or "").strip().upper()
    if not SYMBOL.match(symbol):
        raise RunError(f"{field}: not a symbol: {value!r}")
    prefix = symbol.split(".", 1)[0] if "." in symbol else None
    if prefix in MARKETS and prefix != market:
        raise RunError(f"{field}: {symbol} is not a {market} symbol")
    return symbol


def _universe(settings: Settings, name: Optional[str], market: str, timeframe: str) -> List[str]:
    """The universe's symbols with cached candles, as app.py's sidebar lists them (1h data,
    ConfigLoader.get_universe(with_data_only=True)), here over Settings.data_dir; of those, the
    ones with candles in the run's timeframe (the backtester skips the rest)."""
    config = _config(settings)
    if not name:
        raise RunError("universe: a cross-sectional strategy ranks a universe, so it needs one")
    if config is None or name not in config.list_universes():
        raise RunError(f"universe: no universe {name!r}")
    declared = config.describe_universe(name).get("market")
    if declared and declared != market:
        raise RunError(f"universe: {name} is a {declared} universe, not {market}")
    symbols = [sym for sym in config.get_universe(name)
               if _cached(settings.data_dir, sym, "1h") and _cached(settings.data_dir, sym, timeframe)]
    if not symbols:
        raise RunError(f"universe: no symbol of {name} has cached {timeframe} candles")
    return symbols


def _params(name: str, given: Dict[str, Any]) -> Dict[str, Any]:
    """Every parameter of the strategy: the registry's default unless given, typed as the
    default is and within the registry's bounds (as the sidebar's number inputs keep them)."""
    spec = STRATEGY_REGISTRY[name]["params"]
    unknown = sorted(set(given) - set(spec))
    if unknown:
        raise RunError(f"{name} has no parameter {', '.join(unknown)}; it takes {', '.join(spec)}")
    out = {}
    for key, meta in spec.items():
        value = given.get(key, meta["default"])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RunError(f"{name}: {key} must be a number, not {value!r}")
        if isinstance(meta["default"], float):
            value = float(value)
        elif value != int(value):
            raise RunError(f"{name}: {key} must be a whole number, not {value!r}")
        else:
            value = int(value)
        if not meta["min"] <= value <= meta["max"]:
            raise RunError(f"{name}: {key} must be between {meta['min']} and {meta['max']}, not {value!r}")
        out[key] = value
    return out


def _check_numbers(req: s.RunSettings) -> None:
    if req.start > req.end:
        raise RunError(f"start ({req.start}) is after end ({req.end})")
    if not req.initial_capital > 0:
        raise RunError("initial_capital must be positive")
    if req.commission_rate is not None and not 0 <= req.commission_rate < 0.1:
        raise RunError("commission_rate is a fraction per side: at least 0 and below 0.1")
    if not 0 <= req.slippage_bps <= 1000:
        raise RunError("slippage_bps must be between 0 and 1000")
    r = req.risk
    for key in ("stop_loss_pct", "trailing_stop_pct", "take_profit_pct"):
        value = getattr(r, key)
        if value is not None and not 0 < value < 1:
            raise RunError(f"risk.{key} is a fraction (0.03 = 3%): above 0 and below 1")
    for key in ("max_drawdown_pct", "risk_pct", "fraction", "max_pct"):
        if not 0 < getattr(r, key) <= 1:
            raise RunError(f"risk.{key} is a fraction: above 0 and at most 1")
    if r.qty < 1:
        raise RunError("risk.qty must be at least 1")


def _label(runs: List[Tuple[str, Dict[str, Any]]], i: int) -> str:
    """The strategy's name, numbered when the plan runs it more than once (with other params)."""
    name = runs[i][0]
    same = [j for j, (other, _) in enumerate(runs) if other == name]
    return name if len(same) == 1 else f"{name} #{same.index(i) + 1}"


def _names(names: List[str]) -> str:
    unique = list(dict.fromkeys(names))
    return unique[0] if len(unique) == 1 else ", ".join(unique[:-1]) + " and " + unique[-1]


def _check_ranking(kind: str, name: str, params: Dict[str, Any], universe: str, symbols: List[str],
                   timeframe: str) -> None:
    """A basket strategy holds top_n of the stocks it ranks, so it needs more than top_n to rank:
    with top_n or fewer it would only ever hold them all. A search is checked at the largest
    basket its grid tries."""
    spec = STRATEGY_REGISTRY[name]["params"].get("top_n")
    held = 1 if spec is None else int(spec["max"] if kind == "optimize" else params["top_n"])
    if len(symbols) > held:
        return
    what = (f"the search tries {name} with baskets of up to {held}" if kind == "optimize"
            else f"{name} holds {held} of the stocks it ranks")
    raise RunError(f"universe: {what}, so it needs more than {held} to rank; {universe} has "
                   f"{len(symbols)} with cached {timeframe} candles")


def plan(kind: str, req: s.RunSettings, settings: Settings) -> Plan:
    """The run `req` asks for, checked against the registry and the candle cache. RunError
    names what is wrong.

    A cross-sectional strategy ranks a universe, so it takes `universe` and no `symbol`, and the
    universe must hold more stocks than its basket. A comparison is of single-stock strategies
    on one stock or of cross-sectional ones on one universe, never both: the two kinds trade
    different stocks, size their positions differently and are measured against different
    buy-and-hold benchmarks, so their rows are not like-for-like. (app.py allowed the mix.)"""
    _check_numbers(req)
    market, timeframe = req.market, req.timeframe

    if kind == "backtest":
        choices = [(req.strategy, dict(req.params))]
    elif kind == "compare":
        choices = [(c.strategy, dict(c.params)) for c in req.strategies]
    elif kind == "optimize":
        choices = [(req.strategy, {})]           # the grid supplies the parameters
    else:
        raise RunError(f"no run kind {kind!r}")
    for name, _ in choices:
        if name not in STRATEGY_REGISTRY:
            raise RunError(f"no strategy {name!r}; there are {', '.join(STRATEGY_REGISTRY)}")

    baskets = [name for name, _ in choices if is_basket(name)]
    singles = [name for name, _ in choices if not is_basket(name)]
    if baskets and singles:
        raise RunError(f"strategies: cross-sectional strategies ({_names(baskets)}) rank a universe of stocks "
                       f"against each other, so they are compared only with each other, not with single-stock "
                       f"strategies ({_names(singles)})")
    universe, symbol = None, None
    if baskets:
        universe = _universe(settings, req.universe, market, timeframe)
        if req.symbol:
            raise RunError(f"symbol: {baskets[0]} ranks the whole {req.universe} universe, so it takes no symbol")
    elif not req.symbol:
        raise RunError(f"symbol: {singles[0]} trades one stock, so it needs a symbol")
    else:
        symbol = _symbol(req.symbol, market, "symbol")
    pair = _symbol(req.pair_symbol, market, "pair_symbol") if req.pair_symbol else None

    runs = []
    for i, (name, given) in enumerate(choices):
        params = _params(name, given) if kind != "optimize" else {}
        # app.py's symbols_for: baskets get the universe, pairs trading its pair, the rest the symbol
        if is_basket(name):
            _check_ranking(kind, name, params, req.universe, universe, timeframe)
            symbols = universe
        else:
            symbols = [symbol]
            if name == "Pairs Trading":
                if not pair:
                    raise RunError("pair_symbol: Pairs Trading needs a second symbol")
                if pair == symbol:
                    raise RunError("pair_symbol: the pair needs two different symbols")
                symbols.append(pair)
            for sym in symbols:
                if not _cached(settings.data_dir, sym, timeframe):
                    raise RunError(f"no cached {timeframe} candles for {sym}")
        runs.append(Run(strategy=name, label=_label(choices, i), params=params, symbols=tuple(symbols)))

    extra = {}
    if kind == "optimize":
        if req.objective not in OBJECTIVES:
            raise RunError(f"objective: no objective {req.objective!r}; there are {', '.join(OBJECTIVES)}")
        # walk_forward's own check, made here so it is a 422 rather than a failed run
        if req.method == "walk_forward" and (req.end - req.start).days // req.n_splits < 30:
            raise RunError(f"Date range too short for walk-forward analysis: {req.n_splits} windows of "
                           f"at least 30 days need {req.n_splits * 30} days or more")
        extra = dict(method=req.method, objective=req.objective, max_combinations=req.max_combinations,
                     n_splits=req.n_splits, train_pct=req.train_pct)

    commission = req.commission_rate if req.commission_rate is not None else MARKETS[market]["commission_rate"]
    return Plan(kind=kind, data_dir=str(settings.data_dir), market=market, symbol=symbol, timeframe=timeframe,
                start=req.start, end=req.end, initial_capital=float(req.initial_capital),
                commission_rate=float(commission), slippage_bps=float(req.slippage_bps),
                risk=req.risk.model_dump(), runs=tuple(runs), **extra)


# ── running ─────────────────────────────────────────────────────────────────

def execute(plan: Plan, progress: Progress) -> Any:
    """Run the plan: a BacktestResult, CompareResult or OptimizeResult. progress(fraction 0..1,
    message) is called as it goes; RunError when it produced nothing (no candles in the dates)."""
    storage = DataStorage(plan.data_dir)
    if plan.kind == "backtest":
        return _backtest(plan, storage, progress)
    if plan.kind == "compare":
        return _compare(plan, storage, progress)
    return _optimize(plan, storage, progress)


def _no_data(plan: Plan, symbols) -> RunError:
    shown = ", ".join(list(symbols)[:5]) + (f" and {len(symbols) - 5} more" if len(symbols) > 5 else "")
    return RunError(f"no cached {plan.timeframe} candles for {shown} between {plan.start} and {plan.end}")


def _simulate(plan: Plan, storage: DataStorage, run: Run, progress: Callable[[float], None]):
    """One backtest, as app.py's backtest view runs it. (portfolio, metrics); metrics is {} when
    there were no candles. On the C++ engine when it is built and can run the strategy
    (MIDFREQ_ENGINE, default "auto"); the results are the same either way."""
    portfolio = Portfolio(initial_cash=plan.initial_capital, commission_rate=plan.commission_rate)
    backtester = make_backtester(storage, portfolio, risk_manager=risk_manager_for(run, plan.risk),
                                 slippage_bps=plan.slippage_bps)
    with contextlib.redirect_stdout(io.StringIO()):          # the backtester narrates to stdout
        metrics = backtester.run(STRATEGY_REGISTRY[run.strategy]["class"], symbols=list(run.symbols),
                                 timeframe=Timeframe(plan.timeframe), start_date=plan.start, end_date=plan.end,
                                 progress_callback=progress, **run.params)
    return portfolio, metrics


def _clean(value: Any) -> Any:
    """plain(), with every time made UTC (a time without a zone is UTC, as the backtester reads it)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, datetime):                          # pd.Timestamp too
        return utc(value)
    return plain(value)


def _equity(portfolio: Portfolio) -> List[s.SeriesPoint]:
    # app.py: the per-candle curve when there is one, else the per-trade curve
    data = portfolio.equity_curve_detailed or portfolio.equity_curve
    return [s.SeriesPoint(t=utc(e["timestamp"]), v=float(e["equity"])) for e in data]


def _trades(portfolio: Portfolio) -> List[s.TradeOut]:
    """The trade log. A sell's pnl pairs it with the symbol's last buy, as Portfolio.calculate_metrics
    does for the win rate and profit factor (gross of commission)."""
    out, bought = [], {}
    for t in portfolio.trade_history:
        pnl = None
        if t["action"] == "BUY":
            bought[t["symbol"]] = t["price"]
        elif t["symbol"] in bought:
            pnl = float((t["price"] - bought.pop(t["symbol"])) * t["qty"])
        out.append(s.TradeOut(timestamp=utc(t["timestamp"]), symbol=t["symbol"], action=t["action"],
                              qty=float(t["qty"]), price=float(t["price"]),
                              commission=float(t.get("commission", 0.0)), pnl=pnl))
    return out


def _backtest(plan: Plan, storage: DataStorage, progress: Progress) -> s.BacktestResult:
    run = plan.runs[0]
    progress(0.0, f"Running {run.strategy} on {len(run.symbols)} symbol(s)")
    portfolio, metrics = _simulate(plan, storage, run,
                                   lambda f: progress(min(f, 1.0) * 0.95, f"Simulating {run.strategy}"))
    if not metrics:
        raise _no_data(plan, run.symbols)
    chart = price_chart(plan, storage, run)
    held = benchmark(plan, storage, run.symbols)
    progress(1.0, f"{run.strategy}: {metrics['total_trades']} trades, {metrics['return_pct']:+.2f}%")
    return s.BacktestResult(strategy=run.strategy, symbols=list(run.symbols), params=run.params,
                            metrics=_clean(metrics), equity=_equity(portfolio), trades=_trades(portfolio),
                            chart=chart, benchmark=held)


def _compare(plan: Plan, storage: DataStorage, progress: Progress) -> s.CompareResult:
    """app.py's comparison: each strategy run on its own portfolio and risk manager, rows best
    return first. A strategy that produced nothing (no candles in the dates) is left out. The
    benchmark is buy and hold of what was compared on: the stock, or the whole universe."""
    rows, equity, skipped = [], {}, []
    n = len(plan.runs)
    for i, run in enumerate(plan.runs):
        progress(i / n, f"Running {run.label} ({i + 1}/{n})")
        portfolio, metrics = _simulate(plan, storage, run,
                                       lambda f, i=i, run=run: progress((i + min(f, 1.0)) / n,
                                                                        f"Running {run.label} ({i + 1}/{n})"))
        if not metrics:
            skipped.append(run.label)
            continue
        rows.append(s.CompareRow(strategy=run.label, params=run.params, metrics=_clean(metrics)))
        equity[run.label] = _equity(portfolio)
    if not rows:
        raise _no_data(plan, sorted({sym for run in plan.runs for sym in run.symbols}))
    rows.sort(key=lambda r: r.metrics["return_pct"], reverse=True)
    # plan() keeps baskets and single stocks apart, so the first run says which the comparison is on
    first = plan.runs[0]
    held = benchmark(plan, storage, first.symbols if is_basket(first.strategy) else [plan.symbol])
    progress(1.0, f"Compared {len(rows)} strategies" + (f"; no candles for {', '.join(skipped)}" if skipped else ""))
    return s.CompareResult(rows=rows, equity=equity, benchmark=held)


def _optimize(plan: Plan, storage: DataStorage, progress: Progress) -> s.OptimizeResult:
    run = plan.runs[0]
    # app.py: baskets run with no risk manager here, as the research studies did (the grid
    # varies top_n, so no fixed sizer fits); single stocks with the sidebar's.
    risk_manager = None if is_basket(run.strategy) else build_risk_manager(plan.risk, n_positions=len(run.symbols))
    common = dict(strategy_name=run.strategy, symbols=list(run.symbols), timeframe=Timeframe(plan.timeframe),
                  storage=storage, objective=plan.objective, initial_capital=plan.initial_capital,
                  risk_manager=risk_manager, slippage_bps=plan.slippage_bps, commission_rate=plan.commission_rate,
                  progress_callback=lambda f, message: progress(min(f, 1.0), message))
    if plan.method == "grid":
        return _grid(plan, run, grid_search(start_date=plan.start, end_date=plan.end,
                                            max_combinations=plan.max_combinations, **common))
    result = walk_forward(start_date=datetime.combine(plan.start, time.min),
                          end_date=datetime.combine(plan.end, time.min),
                          n_splits=plan.n_splits, train_pct=plan.train_pct, **common)
    return _walk_forward(plan, run, result)


def _grid(plan: Plan, run: Run, df: pd.DataFrame) -> s.OptimizeResult:
    if df.empty:
        raise _no_data(plan, run.symbols)
    param_cols = [c for c in df.columns if c not in GRID_METRICS and c != "_objective"]
    # _objective: the value ranked on (max_drawdown negated, an infinite profit factor as 999)
    rows = [s.GridRow(params={k: row[k] for k in param_cols},
                      metrics={k: row[k] for k in (*GRID_METRICS, "_objective")})
            for row in records(df)]
    summary = {"grid_size": len(generate_param_grid(run.strategy)), "combinations": len(rows),
               "objective_label": OBJECTIVES[plan.objective], "best_params": rows[0].params,
               "symbols": list(run.symbols)}
    return s.OptimizeResult(method="grid", objective=plan.objective, rows=rows, summary=summary)


def _walk_forward(plan: Plan, run: Run, result: Dict[str, Any]) -> s.OptimizeResult:
    summary = result.get("summary", {})
    if summary.get("error"):
        raise RunError(summary["error"])
    if not result.get("windows"):
        raise _no_data(plan, run.symbols)
    windows = [s.WalkForwardWindow(index=w.window_id, train_start=utc(w.train_start), train_end=utc(w.train_end),
                                   test_start=utc(w.test_start), test_end=utc(w.test_end),
                                   params=_clean(w.best_params),
                                   train_objective=_clean(w.train_metrics.get(plan.objective)),
                                   test_metrics=_clean(w.test_metrics))
               for w in result["windows"]]
    summary = {**_clean(summary), "objective_label": OBJECTIVES[plan.objective], "symbols": list(run.symbols)}
    return s.OptimizeResult(method="walk_forward", objective=plan.objective, windows=windows, summary=summary)


# ── the backtest's price chart: app.py's plot_* overlays as series ──────────

def _ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """The cached frame with lower-case OHLCV columns and a UTC index (as /api/candles reads it)."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce", utc=True)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[df.index.notna()].sort_index().dropna(subset=["open", "high", "low", "close"])


def indicators(name: str, df: pd.DataFrame, p: Dict[str, Any]) -> List[Tuple[str, str, Any]]:
    """The overlay app.py's plot function for `name` draws over `df`: (name, pane, series), where a
    number instead of a series is a horizontal level. Strategies app.py plots as a plain price chart
    (pairs, ensemble, HMM, cross-sectional, ML, buy & hold) have none."""
    close = df["close"]
    if name == "SMA Crossover":
        return [(f"Fast MA ({p['fast_period']})", "price", close.rolling(window=p["fast_period"]).mean()),
                (f"Slow MA ({p['slow_period']})", "price", close.rolling(window=p["slow_period"]).mean())]
    if name == "RSI":
        delta = close.diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        rs = gain.rolling(window=p["rsi_period"]).mean() / loss.rolling(window=p["rsi_period"]).mean()
        return [("RSI", "lower", 100 - (100 / (1 + rs))),
                (f"Overbought ({p['overbought']})", "lower", p["overbought"]),
                (f"Oversold ({p['oversold']})", "lower", p["oversold"])]
    if name == "MACD":
        macd = (close.ewm(span=p["fast_ema"], adjust=False).mean()
                - close.ewm(span=p["slow_ema"], adjust=False).mean())
        signal = macd.ewm(span=p["signal_period"], adjust=False).mean()
        return [("Histogram", "lower", macd - signal), ("MACD", "lower", macd), ("Signal", "lower", signal)]
    if name == "Bollinger Bands":
        middle = close.rolling(window=p["bb_period"]).mean()
        std = close.rolling(window=p["bb_period"]).std()
        return [("Upper Band", "price", middle + p["num_std"] * std),
                ("Lower Band", "price", middle - p["num_std"] * std),
                (f"SMA ({p['bb_period']})", "price", middle)]
    if name == "Z-Score Mean Reversion":
        mean = close.rolling(window=p["lookback"]).mean()
        z = (close - mean) / close.rolling(window=p["lookback"]).std()
        return [(f"Rolling Mean ({p['lookback']})", "price", mean), ("Z-Score", "lower", z),
                (f"+{p['entry_z']}σ", "lower", p["entry_z"]), (f"-{p['entry_z']}σ", "lower", -p["entry_z"]),
                ("0", "lower", 0.0)]
    if name == "Regime Switch":
        lookback = p["regime_lookback"]
        net_move = (close - close.shift(lookback)).abs()
        path = close.diff().abs().rolling(lookback).sum()
        return [("Efficiency Ratio", "lower", (net_move / path).clip(0, 1)),
                (f"Threshold ({p['er_threshold']})", "lower", p["er_threshold"])]
    return []


def _window(plan: Plan) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """The backtester's window: from start, through the whole of the end date."""
    return pd.Timestamp(plan.start, tz="UTC"), pd.Timestamp(plan.end, tz="UTC") + pd.Timedelta(days=1)


def price_chart(plan: Plan, storage: DataStorage, run: Run) -> Optional[s.PriceChart]:
    """The traded stock's candles (plan.symbol) over the run's dates with the strategy's overlay.
    The indicators are computed over the whole cached history, as app.py does, so they are warmed
    up at the start. None for a basket, which trades a universe rather than one stock, and when
    the stock has no candles in the dates."""
    if plan.symbol is None or not _cached(Path(plan.data_dir), plan.symbol, plan.timeframe):
        return None
    df = _ohlcv(storage.load_data(_folder(plan.symbol), plan.timeframe))
    t0, t1 = _window(plan)
    in_window = (df.index >= t0) & (df.index < t1)
    window = df[in_window]
    if window.empty:
        return None
    first, last = window.index[0].to_pydatetime(), window.index[-1].to_pydatetime()
    out = []
    for name, pane, series in indicators(run.strategy, df, run.params):
        if isinstance(series, (int, float)):
            points = [s.SeriesPoint(t=first, v=float(series)), s.SeriesPoint(t=last, v=float(series))]
        else:
            values = series[in_window].astype(float)
            points = [s.SeriesPoint(t=t.to_pydatetime(), v=v) for t, v in zip(values.index, values.tolist())
                      if math.isfinite(v)]
        out.append(s.Indicator(name=name, pane=pane, points=points))
    volume = (window["volume"].astype(float).fillna(0.0) if "volume" in window.columns
              else pd.Series(0.0, index=window.index)).tolist()
    candles = [s.Candle(t=t.to_pydatetime(), o=o, h=h, l=lo, c=c, v=v)
               for t, o, h, lo, c, v in zip(window.index, *(window[k].astype(float).tolist()
                                                             for k in ("open", "high", "low", "close")), volume)]
    return s.PriceChart(symbol=plan.symbol, candles=candles, indicators=out)


def benchmark(plan: Plan, storage: DataStorage, symbols) -> Optional[s.Benchmark]:
    """The backtester's buy-and-hold benchmark (Backtester._calculate_benchmark) over time: the
    starting capital split equally across the symbols with candles in the run's dates, each part
    spent at its symbol's first close there and held. It is valued at every candle of any of them
    at the latest closes, a part whose symbol has not started yet counted as cash, so its last
    value gives the run's benchmark_return_pct. None when no symbol has candles in the dates."""
    t0, t1 = _window(plan)
    closes = {}
    for sym in symbols:
        if not _cached(Path(plan.data_dir), sym, plan.timeframe):
            continue
        df = _ohlcv(storage.load_data(_folder(sym), plan.timeframe))
        close = df.loc[(df.index >= t0) & (df.index < t1), "close"].astype(float)
        if not close.empty:
            closes[sym] = close
    if not closes:
        return None
    part = plan.initial_capital / len(closes)
    first = pd.Series({sym: close.iloc[0] for sym, close in closes.items()})
    shares = (part / first).where(first > 0, 0.0)                # the backtester skips a price of 0 or less
    prices = pd.DataFrame({sym: close[~close.index.duplicated(keep="last")] for sym, close in closes.items()})
    prices = prices.sort_index().ffill()                         # NaN only before a symbol's first candle
    value = (prices * shares).sum(axis=1) + part * prices.isna().sum(axis=1)
    return s.Benchmark(symbols=list(closes), equity=[s.SeriesPoint(t=t.to_pydatetime(), v=float(v))
                                                     for t, v in value.items()])


# ── what a deployment can run ───────────────────────────────────────────────

def capabilities(settings: Settings) -> s.JobCapabilities:
    return s.JobCapabilities(kinds=[k for k in settings.job_kinds if k in KINDS],
                             max_concurrent=settings.max_concurrent_jobs,
                             objectives=dict(OBJECTIVES), markets=markets(settings))


def markets(settings: Settings) -> Dict[str, Dict[str, Any]]:
    """Per market: its currency and default commission, the symbols with cached candles
    ({symbol, timeframes}), and the universes declared for it (with how many of their symbols
    have 1h data, the sidebar's "symbols with cached data")."""
    config = _config(settings)
    # the cache's folders lose the symbol's dots (US_BRK_B); name them as config/symbols.json does
    named: Dict[str, str] = {}
    if config is not None:
        declared = [sym for m in config.config_data.get("markets", {}).values() for sym in m.get("symbols", [])]
        for universe in config.list_universes():
            declared += config.get_universe(universe)
        named = {_folder(sym): sym for sym in declared}

    out = {m: {**info, "symbols": [], "universes": []} for m, info in MARKETS.items()}
    data_dir = Path(settings.data_dir)
    folders = sorted(p for p in data_dir.iterdir() if p.is_dir()) if data_dir.is_dir() else []
    for folder in folders:
        symbol = named.get(folder.name) or folder.name.replace("_", ".", 1)
        market = symbol.split(".", 1)[0] if "." in symbol else None
        timeframes = [tf for tf in TIMEFRAMES if (folder / f"{tf}.parquet").is_file()]
        if market in out and timeframes:
            out[market]["symbols"].append({"symbol": symbol, "timeframes": timeframes})

    for name in (config.list_universes() if config is not None else []):
        symbols = config.get_universe(name)
        desc = config.describe_universe(name)
        market = desc.get("market")
        if not market:
            prefixes = {sym.split(".", 1)[0] for sym in symbols if "." in sym}
            market = prefixes.pop() if len(prefixes) == 1 else None
        if market in out:
            out[market]["universes"].append({
                "name": name, "label": UNIVERSE_LABELS.get(name, name), "description": str(desc.get("description") or ""),
                "symbols": len(symbols), "symbols_with_data": sum(_cached(data_dir, sym, "1h") for sym in symbols)})
    return out
