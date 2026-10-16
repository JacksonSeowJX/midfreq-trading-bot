"""
Python backtester vs the C++ engine on the real S&P 100 cache: time, and identical results.

    uv run python packages/midfreq-engine/bench/bench_engine.py [--data DIR] [--out bench.json]
                                                                [--skip-python-walk-forward]

Scenarios (all at the US fee, 0.005% a side, plus 5 bps slippage):
  1. backtest      cross-sectional reversal, the live parameters (30, 3, 19), 3 years of hourly bars
  2. backtest      SMA crossover 5/20 over the same 100 symbols and 3 years
  3. grid search   the 96 reversal combinations over the forward test's training window
                   (3 Jul - 25 Sep 2026): the search that chose the live parameters
  4. walk-forward  the alpha study's configuration A (9 windows over the same 3 years)

Each scenario runs on Python, on C++ with one thread and on C++ with every core, and the outputs
are compared exactly (trade lists, equity curves, the result tables). The data cache is only read.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from midfreq.backtest.backtester import PythonBacktester
from midfreq.backtest.native import NativeBacktester
from midfreq.backtest.optimizer import grid_search, walk_forward
from midfreq.core.candle import Timeframe
from midfreq.core.paths import data_dir, repo_root
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import CrossSectionalReversal, MovingAverageCrossover

sys.path.insert(0, str(repo_root() / "research" / "studies"))
from cross_sectional_cost_sensitivity import (HISTORY_DAYS, OBJECTIVE, SLIPPAGE_BPS, STRATEGY,  # noqa: E402
                                              TRAIN_PCT, sp100_symbols)

RATE = 0.00005
H1 = Timeframe.HOUR_1


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def timed(fn, threads=None):
    if threads is not None:
        os.environ["MIDFREQ_ENGINE_THREADS"] = str(threads)
    t = time.perf_counter()
    with quiet():
        out = fn()
    return time.perf_counter() - t, out


def backtest(engine, storage, cls, symbols, start, end, **params):
    pf = Portfolio(initial_cash=100_000.0, commission_rate=RATE)
    bt = (PythonBacktester(storage, pf, slippage_bps=SLIPPAGE_BPS) if engine == "python"
          else NativeBacktester(storage, pf, slippage_bps=SLIPPAGE_BPS, fallback=False))
    m = bt.run(cls, symbols, H1, start_date=start, end_date=end, **params)
    return m, pf


def same_backtest(a, b):
    (ma, pa), (mb, pb) = a, b
    return (ma == mb and pa.trade_history == pb.trade_history
            and pa.equity_curve_detailed == pb.equity_curve_detailed and pa.positions == pb.positions)


def same_walk_forward(a, b):
    return ([w.__dict__ for w in a["windows"]] == [w.__dict__ for w in b["windows"]]
            and a["summary"] == b["summary"])


def scenario(name, run, same, skip_python=False):
    cores = os.cpu_count() or 1
    row = {"scenario": name}
    t1, one = timed(lambda: run("native"), threads=1)
    tn, many = timed(lambda: run("native"), threads=0)
    row.update(native_1_thread_s=round(t1, 3), native_all_cores_s=round(tn, 3), cores=cores)
    row["identical_across_threads"] = bool(same(one, many))
    if not skip_python:
        tp, py = timed(lambda: run("python"))
        row.update(python_s=round(tp, 3), speedup_1_thread=round(tp / t1, 1), speedup_all_cores=round(tp / tn, 1),
                   identical_to_python=bool(same(py, many)))
    print(json.dumps(row), flush=True)
    return row, many


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(data_dir()))
    ap.add_argument("--out")
    ap.add_argument("--skip-python-walk-forward", action="store_true",
                    help="the Python walk-forward takes about ten minutes")
    args = ap.parse_args()

    storage = DataStorage(args.data)
    symbols, _ = sp100_symbols(storage, "2026-08-21")
    end = storage.latest_common_timestamp(symbols, H1.value, as_of="2026-08-21")
    start = end - timedelta(days=HISTORY_DAYS)
    rows = []

    rows.append(scenario(
        "backtest: cross-sectional reversal (30, 3, 19), 3 years",
        lambda e: backtest(e, storage, CrossSectionalReversal, symbols, start, end,
                           lookback=30, top_n=3, rebalance_every=19), same_backtest)[0])
    rows.append(scenario(
        "backtest: SMA crossover 5/20, 3 years",
        lambda e: backtest(e, storage, MovingAverageCrossover, symbols, start, end,
                           fast_period=5, slow_period=20), same_backtest)[0])

    fwd = json.loads((repo_root() / "config" / "sp100_forward_test.json").read_text())
    train = fwd["train_window"]
    live_symbols = [s for s in symbols if s not in set(fwd.get("excluded_stale_symbols", []))]
    row, table = scenario(
        "grid search: 96 reversal combinations, forward-test training window",
        lambda e: grid_search(STRATEGY, live_symbols, H1, datetime.fromisoformat(train["start"]),
                              datetime.fromisoformat(train["end"]), storage, objective=OBJECTIVE,
                              slippage_bps=SLIPPAGE_BPS, commission_rate=RATE, engine=e),
        lambda a, b: a.equals(b))
    best = {k: int(table.iloc[0][k]) for k in ("lookback", "top_n", "rebalance_every")}
    row["best_params"] = best
    row["matches_live_params"] = best == fwd["params"]
    rows.append(row)

    rows.append(scenario(
        "walk-forward: configuration A, 9 windows, 3 years",
        lambda e: walk_forward(STRATEGY, symbols, H1, start, end, storage, n_splits=9, train_pct=TRAIN_PCT,
                               objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS, commission_rate=RATE, engine=e),
        same_walk_forward, skip_python=args.skip_python_walk_forward)[0])

    from midfreq import engine
    report = {"machine": {"platform": platform.platform(), "python": platform.python_version(),
                          **engine.core().build_info()},
              "data": {"symbols": len(symbols), "start": str(start), "end": str(end)},
              "scenarios": rows}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, default=str))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
