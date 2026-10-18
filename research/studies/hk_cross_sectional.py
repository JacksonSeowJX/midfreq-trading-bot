"""
Hong Kong cross-sectional strategies at low turnover (pre-registered)
=====================================================================
Written and saved on 18 Oct 2026, before any of its numbers were computed.
The candidates, grid, universe, costs and acceptance rule below are fixed;
nothing is added or tuned after seeing results. Anything tried afterwards
goes in a separately labelled exploratory section, is counted in the
search total, and is never called a pass.

Why. cross_sectional_corrected.py ran cross-sectional reversal and momentum
on the Hang Seng universe with the registry grid, which rebalances every
1, 7, 13 or 19 hourly bars, and both failed at the calibrated HK fee
(reversal -1.165% / -3.829% per window, momentum -7.599% / -2.666%). At
0.16% per side plus spread a basket that turns over hourly pays its edge
away. This study asks whether the same two strategies survive when they
are only allowed to rebalance about daily or about weekly.

Universe. config/symbols.json `hsi`, the universe cross_sectional_corrected
called "HK (Hang Seng Index)": 88 names. It was checked on 18 Oct, before
any run, to be exactly the 88 HK names with a cached 1h.parquet, so the
second universe the brief asks for ("all 88 cached HK names", run only if
`hsi` is narrower) is the same set and is not run twice; the script checks
this again at start-up and runs both if they ever differ. Window: the
latest candle all 88 share (2026-08-28 16:00 in the current cache) and
3 x 365 days before it, as cross_sectional_corrected anchors it.

Strategies (STRATEGY_REGISTRY classes, unchanged, run on the C++ engine):
  1. Cross-Sectional Reversal  (buy the top_n worst performers over lookback)
  2. Cross-Sectional Momentum  (buy the top_n best performers over lookback)
  Not included: a sector-neutral reversal. No sector or industry mapping
  for these HK names exists in the repo (config/, research/, packages/,
  apps/, scripts/ searched on 18 Oct), and the brief forbids inventing one.

Grid, chosen for low turnover (18 points per strategy):
  lookback         {6, 12, 30} hourly bars (about 1, 2 and 5 trading days;
                   HK has 6 bars a day: 10:30 11:30 12:00 14:00 15:00 16:00)
  top_n            {3, 5, 10}
  rebalance_every  {6, 30} bars (about daily and about weekly)
  walk_forward takes its grid from optimizer.generate_param_grid (the
  registry's 96 points); this study swaps that function for the grid above
  inside this process only. walk_forward itself is called unchanged.

Candidate configurations searched: 2 strategies x 18 grid points x 1
universe = 36.

Validation (the project's dual-configuration standard):
  Config A: 9 windows (~121 days), Config B: 15 windows (~73 days), 70%
  train. Each window grid-searches its training segment by Sharpe ratio
  (walk_forward's rule: combinations that traded rank above those that did
  not) and trades the chosen setting unchanged on its test segment.
  Capital 100,000, no risk manager (the strategy's own equal-dollar basket).

Costs:
  Decision: commission 0.0016 per side (Portfolio.HK_FEE_RATE, the XHKG rate
  in config/venues.json, calibrated on paper fills) plus 5 bps slippage per
  side (SLIPPAGE_BPS = 5.0, the convention of cross_sectional_corrected.py
  and reality_check_study.py for HK; measured HK median half-spread 3.33 bps).
  Cost sensitivity, descriptive only, each a full re-run with the selection
  redone at that rate: commission 0.0008 per side (asked for by the brief)
  and 0.0 (zero commission, still 5 bps slippage; separates "no signal" from
  "signal eaten by fees").

Acceptance rule (decided at 0.0016 only). A walk-forward candidate PASSES
only if, under BOTH Config A and Config B, its mean out-of-sample return
per window is > 0 AND at least 50% of its windows have a return > 0.

Also reported (pre-registered, descriptive):
  * Every one of the 36 fixed settings traded unchanged on every test
    segment, with no selection. The same rule is applied ("fixed-setting
    rule met"), but a fixed setting is picked in hindsight from 36, so it is
    never called a pass on its own: it would count only if it also survived
    the Reality Check across all 36 (p < 0.05 under both configurations).
  * Alpha view, as alpha_vs_market.py: the equal-weight buy-and-hold of the
    same 88 names over each test segment (the backtester's benchmark), the
    per-window excess and the share of windows with positive excess; for the
    two walk-forward candidates at 0.0016 also alpha_vs_market's daily
    regression r_strategy = alpha + beta * r_benchmark (Newey-West, 5 lags).
  * Turnover: fills (buys + sells) per test window.
  * Exit-cost-adjusted return: return_pct values open positions at the last
    close without paying to sell them; this deducts that sale (5 bps +
    commission). The decision still uses return_pct, as every study does.
  * White's Reality Check (midfreq.research.reality_check; 5000 resamples,
    mean block 2, seed 12345) on the per-window OOS returns at 0.0016, each
    configuration separately, over (i) the 36 fixed settings and (ii) the 2
    walk-forward candidates. The brief requires it only if something
    passes; it is cheap, so it is computed regardless.
  * Live settings, as scripts/live/pick_sp100_live_config.py: the same 18
    points grid-searched by Sharpe on the most recent 84 days ending at the
    universe's latest common candle, at 0.0016 + 5 bps. Recommended for live
    only for a candidate that passed.

Compute: one process, the C++ engine on 3 threads (MIDFREQ_ENGINE_THREADS),
run under nice -n 10. Reads data/ only; writes results/ only (or --out-dir).

EXPLORATORY SECTION (added 18 Oct 2026 AFTER the pre-registered results
were seen; run only with --exploratory; adds NO candidate configurations, so
the search total stays 36; nothing in it can be a pass):
  E1. Turnover in capital terms. Fills per window do not compare baskets of
      different sizes (one fill is all the capital at top_n=1, a tenth of it
      at top_n=10), and the registry-grid study this one follows turned out
      to have FEWER fills per window than this "low-turnover" grid. So each
      test segment of the two walk-forward candidates at 0.0016 is re-run for
      its trades, and the traded notional / capital and the fees + slippage
      paid (% of capital) are measured; the same for the registry-grid
      walk-forward of cross_sectional_corrected (reproduced exactly here).
  E2. The two fixed settings that met the rule in hindsight
      (lb12_n3_rb30 for BOTH reversal and momentum, which buy opposite ends of
      the same ranking): alpha_vs_market's daily regression on each, to see
      whether their return over the index is beta rather than ranking skill.

Usage:
    nice -n 10 uv run python research/studies/hk_cross_sectional.py [--smoke] [--out-dir DIR]
        [--n-boot 5000] [--exploratory]
"""
import os
os.environ.setdefault('MIDFREQ_ENGINE_THREADS', '3')

import argparse
import contextlib
import io
import itertools
import json
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from midfreq.core import paths
from midfreq.core.candle import Timeframe
from midfreq.core.config import ConfigLoader
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.backtest import optimizer
from midfreq.backtest.backtester import Backtester
from midfreq.backtest.native import BatchRunner
from midfreq.research.reality_check import reality_check, naive_single_test
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY
from alpha_vs_market import daily_paths, segment_prices, analyse

REPO = paths.repo_root()
DATA = paths.data_dir()

# ── Pre-registered (see the docstring) ───────────────────────────────────────
UNIVERSE = 'hsi'
STRATEGIES = ['Cross-Sectional Reversal', 'Cross-Sectional Momentum']
GRID_AXES = {'lookback': [6, 12, 30], 'top_n': [3, 5, 10], 'rebalance_every': [6, 30]}
GRID = [dict(zip(GRID_AXES, combo)) for combo in itertools.product(*GRID_AXES.values())]
CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
SLIPPAGE_BPS = 5.0
HISTORY_DAYS = 3 * 365
DECISION_RATE = Portfolio.HK_FEE_RATE          # 0.0016
RATES = [DECISION_RATE, 0.0008, 0.0]           # decision first, then the sensitivity lines
MIN_CONSISTENCY = 50.0
CAPITAL = 100_000.0
LIVE_TRAIN_DAYS = 84
MAX_STALENESS_DAYS = 3                          # pick_sp100_live_config's rule
ENGINE = 'native'                               # fail loudly rather than fall back to Python
RC_BLOCK, RC_SEED = 2.0, 12345
N_CANDIDATE_CONFIGS = len(STRATEGIES) * len(GRID)   # x universes, set in main()


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def short(strategy):
    return strategy.replace('Cross-Sectional ', 'CS-')


def pkey(p):
    return f"lb{p['lookback']}_n{p['top_n']}_rb{p['rebalance_every']}"


def qualifies(oos_mean, consistency):
    return oos_mean > 0 and consistency >= MIN_CONSISTENCY


@contextlib.contextmanager
def study_grid():
    """walk_forward reads its grid from optimizer.generate_param_grid; give it this study's grid."""
    original = optimizer.generate_param_grid
    optimizer.generate_param_grid = lambda name: [dict(p) for p in GRID]
    try:
        yield
    finally:
        optimizer.generate_param_grid = original


def universes():
    """{name: symbols}. `hsi`, plus every cached HK name if `hsi` is narrower than the cache."""
    hsi = ConfigLoader().get_universe(UNIVERSE, with_data_only=True)
    cached = sorted(p.name.replace('_', '.', 1) for p in DATA.iterdir()
                    if p.name.startswith('HK_') and (p / '1h.parquet').exists())
    out = {UNIVERSE: hsi}
    if set(hsi) != set(cached):
        out['hk_all_cached'] = cached
    return out, cached


def window_metrics(m, rate):
    """One test segment's numbers from a run's metrics ({} if the run produced nothing)."""
    if not m:
        return {'return': 0.0, 'benchmark_return': None, 'excess': None, 'fills': 0,
                'exit_adjusted_return': 0.0, 'sharpe': 0.0}
    ret = m.get('return_pct', 0.0)
    bench = m.get('benchmark_return_pct')
    final, cash = m.get('final_equity', CAPITAL), m.get('cash_balance', CAPITAL)
    held = final - cash
    sold = cash + held * (1 - SLIPPAGE_BPS / 1e4) * (1 - rate)
    return {'return': ret, 'benchmark_return': bench,
            'excess': (ret - bench) if bench is not None else None,
            'fills': int(m.get('total_trades', 0)),
            'exit_adjusted_return': (sold - CAPITAL) / CAPITAL * 100,
            'sharpe': m.get('sharpe_ratio', 0.0)}


def summarise(wins):
    r = np.array([w['return'] for w in wins], dtype=float)
    ex = [w['excess'] for w in wins if w['excess'] is not None]
    bm = [w['benchmark_return'] for w in wins if w['benchmark_return'] is not None]
    adj = np.array([w['exit_adjusted_return'] for w in wins], dtype=float)
    return {
        'windows': len(wins),
        'oos_mean': float(r.mean()) if len(r) else 0.0,
        'consistency': float((r > 0).mean() * 100) if len(r) else 0.0,
        'benchmark_mean': float(np.mean(bm)) if bm else None,
        'excess_mean': float(np.mean(ex)) if ex else None,
        'pct_windows_excess_positive': float(np.mean(np.array(ex) > 0) * 100) if ex else None,
        'fills_mean': float(np.mean([w['fills'] for w in wins])) if wins else 0.0,
        'fills_min': int(min(w['fills'] for w in wins)) if wins else 0,
        'fills_max': int(max(w['fills'] for w in wins)) if wins else 0,
        'exit_adjusted_mean': float(adj.mean()) if len(adj) else 0.0,
        'exit_adjusted_consistency': float((adj > 0).mean() * 100) if len(adj) else 0.0,
        'oos_returns': [float(x) for x in r],
    }


def run_walk_forward(strategy, symbols, start, end, n_splits, rate, storage):
    with study_grid(), quiet():
        res = optimizer.walk_forward(
            strategy_name=strategy, symbols=symbols, timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage, n_splits=n_splits,
            train_pct=TRAIN_PCT, objective=OBJECTIVE, initial_capital=CAPITAL,
            slippage_bps=SLIPPAGE_BPS, commission_rate=rate, engine=ENGINE)
    s = res.get('summary', {})
    wins = []
    for w in res['windows']:
        row = {'window': w.window_id, 'train_start': str(w.train_start), 'train_end': str(w.train_end),
               'test_start': str(w.test_start), 'test_end': str(w.test_end),
               'params': dict(w.best_params),
               'train_return': w.train_metrics.get('return_pct', 0.0),
               'train_sharpe': w.train_metrics.get('sharpe_ratio', 0.0),
               'train_fills': int(w.train_metrics.get('total_trades', 0)),
               '_test_start': w.test_start, '_test_end': w.test_end}
        # a test run that produced nothing comes back as walk_forward's zero placeholder
        row.update(window_metrics(w.test_metrics if 'benchmark_return_pct' in w.test_metrics else {}, rate))
        wins.append(row)
    extra = {'skipped_windows': [str(x) for x in s.get('skipped_windows', [])],
             'avg_train_return': s.get('avg_train_return'), 'wf_efficiency': s.get('wf_efficiency')}
    return wins, extra


def run_fixed(strategy, symbols, bounds, rate, storage):
    """Every grid point traded unchanged on every test segment: {pkey: [window metrics]}."""
    cls = STRATEGY_REGISTRY[strategy]['class']
    runner = BatchRunner(ENGINE, cls, symbols, Timeframe.HOUR_1, storage, CAPITAL, rate,
                         None, SLIPPAGE_BPS)
    out = {pkey(p): [] for p in GRID}
    for ts, te in bounds:
        for p, (m, _) in zip(GRID, runner.run(GRID, ts, te)):
            out[pkey(p)].append(window_metrics(m, rate))
    return out


def alpha_view(strategy, symbols, wins, rate, storage, price_cache):
    """alpha_vs_market's measures for one walk-forward: each test segment re-run for its trades."""
    cls = STRATEGY_REGISTRY[strategy]['class']
    windows, mismatches = [], []
    for w in wins:
        pf = Portfolio(initial_cash=CAPITAL, commission_rate=rate)
        bt = Backtester(storage=storage, portfolio=pf, risk_manager=None, slippage_bps=SLIPPAGE_BPS)
        with quiet():
            m = bt.run(cls, symbols=symbols, timeframe=Timeframe.HOUR_1,
                       start_date=w['_test_start'], end_date=w['_test_end'], **w['params'])
        key = (w['test_start'], w['test_end'])
        if key not in price_cache:
            price_cache[key] = segment_prices(storage, symbols, w['_test_start'], w['_test_end'])
        path = daily_paths(price_cache[key], pf.trade_history, CAPITAL)
        rec = {'window': w['window'], 'test_start': w['test_start'], 'test_end': w['test_end'],
               'return': m.get('return_pct', 0.0), 'benchmark_return': m.get('benchmark_return_pct', 0.0),
               'daily': [[str(d), float(r['strategy']), float(r['benchmark'])] for d, r in path.iterrows()]}
        if (abs(rec['return'] - w['return']) > 1e-6
                or abs((path['strategy'].iloc[-1] / CAPITAL - 1) * 100 - rec['return']) > 0.01
                or abs((path['benchmark'].iloc[-1] - 1) * 100 - rec['benchmark_return']) > 0.01):
            mismatches.append(w['window'])
        windows.append(rec)
    _, out = analyse(windows)
    out['rerun_mismatches'] = mismatches
    return {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in out.items()}


def live_pick(strategy, symbols, storage):
    """pick_sp100_live_config's procedure on this study's grid, at the decision rate."""
    last = {s: pd.read_parquet(DATA / s.replace('.', '_') / '1h.parquet').index.max() for s in symbols}
    freshest = max(last.values())
    keep = [s for s in symbols if (freshest - last[s]).days <= MAX_STALENESS_DAYS]
    end = storage.latest_common_timestamp(keep, '1h')
    start = end - timedelta(days=LIVE_TRAIN_DAYS)
    cls = STRATEGY_REGISTRY[strategy]['class']
    runner = BatchRunner(ENGINE, cls, keep, Timeframe.HOUR_1, storage, CAPITAL, DECISION_RATE,
                         None, SLIPPAGE_BPS)
    rows = []
    for p, (m, _) in zip(GRID, runner.run(GRID, start, end.date())):
        rows.append({**p, 'objective': optimizer._get_objective_value(m, OBJECTIVE) if m else 0.0,
                     'return_pct': m.get('return_pct', 0.0) if m else 0.0,
                     'trades': int(m.get('total_trades', 0)) if m else 0})
    traded = [r for r in rows if r['trades'] > 0]
    best = max(traded, key=lambda r: r['objective']) if traded else None
    boundary = []
    if best:
        for k, vals in GRID_AXES.items():
            if best[k] == vals[0]:
                boundary.append(f"{k}={best[k]} is the grid minimum {vals}")
            elif best[k] == vals[-1]:
                boundary.append(f"{k}={best[k]} is the grid maximum {vals}")
    return {'train_start': str(start), 'train_end': str(end), 'days': LIVE_TRAIN_DAYS,
            'symbols': len(keep), 'excluded_stale': sorted(set(symbols) - set(keep)),
            'configs_searched': len(GRID), 'configs_that_traded': len(traded), 'best': best,
            'top5': sorted(traded, key=lambda r: -r['objective'])[:5], 'boundary_warnings': boundary}


# ── EXPLORATORY (post hoc, see the docstring): no candidate configurations, never a pass ─────

HINDSIGHT_SETTINGS = [('Cross-Sectional Reversal', {'lookback': 12, 'top_n': 3, 'rebalance_every': 30}),
                      ('Cross-Sectional Momentum', {'lookback': 12, 'top_n': 3, 'rebalance_every': 30})]


def _rerun(strategy, symbols, params, ts, te, rate, storage):
    """One test segment re-run for its trades: metrics, portfolio, and what the trading cost."""
    cls = STRATEGY_REGISTRY[strategy]['class']
    pf = Portfolio(initial_cash=CAPITAL, commission_rate=rate)
    bt = Backtester(storage=storage, portfolio=pf, risk_manager=None, slippage_bps=SLIPPAGE_BPS)
    with quiet():
        m = bt.run(cls, symbols=symbols, timeframe=Timeframe.HOUR_1, start_date=ts, end_date=te, **params)
    s = SLIPPAGE_BPS / 1e4
    # trade prices already carry the slippage: a buy at close*(1+s), a sell at close*(1-s)
    notional = sum(t['qty'] * t['price'] for t in pf.trade_history)
    commission = sum(t['commission'] for t in pf.trade_history)
    slippage = sum(t['qty'] * t['price'] * s / ((1 + s) if t['action'] == 'BUY' else (1 - s))
                   for t in pf.trade_history)
    return m, pf, {'fills': len(pf.trade_history), 'notional_x_capital': notional / CAPITAL,
                   'cost_pct_of_capital': (commission + slippage) / CAPITAL * 100}


def exploratory(storage, symbols, start, end):
    out = {'label': 'EXPLORATORY, post hoc. Adds no candidate configurations (search total stays 36); '
                    'nothing here is a pass.', 'E1_turnover': {}, 'E2_hindsight_alpha': {}}
    bounds = {}
    for grid_name, ctx in (('study grid (18)', study_grid),
                           ('registry grid (96, cross_sectional_corrected)', contextlib.nullcontext)):
        for strategy in STRATEGIES:
            for cname, n_splits in CONFIGS:
                with ctx(), quiet():
                    res = optimizer.walk_forward(
                        strategy_name=strategy, symbols=symbols, timeframe=Timeframe.HOUR_1,
                        start_date=start, end_date=end, storage=storage, n_splits=n_splits,
                        train_pct=TRAIN_PCT, objective=OBJECTIVE, initial_capital=CAPITAL,
                        slippage_bps=SLIPPAGE_BPS, commission_rate=DECISION_RATE, engine=ENGINE)
                bounds.setdefault(cname, [(w.test_start, w.test_end) for w in res['windows']])
                rows = []
                for w in res['windows']:
                    m, _, t = _rerun(strategy, symbols, w.best_params, w.test_start, w.test_end,
                                     DECISION_RATE, storage)
                    rows.append({'window': w.window_id, 'params': dict(w.best_params),
                                 'return': m.get('return_pct', 0.0),
                                 'walk_forward_return': w.test_metrics.get('return_pct', 0.0), **t})
                agg = {
                    'oos_mean': float(np.mean([r['return'] for r in rows])),
                    'fills_mean': float(np.mean([r['fills'] for r in rows])),
                    'notional_x_capital_mean': float(np.mean([r['notional_x_capital'] for r in rows])),
                    'cost_pct_of_capital_mean': float(np.mean([r['cost_pct_of_capital'] for r in rows])),
                    'rebalance_every_chosen': {str(k): int(v) for k, v in pd.Series(
                        [r['params']['rebalance_every'] for r in rows]).value_counts().sort_index().items()},
                    'top_n_chosen': {str(k): int(v) for k, v in pd.Series(
                        [r['params']['top_n'] for r in rows]).value_counts().sort_index().items()},
                    'rerun_mismatches': [r['window'] for r in rows
                                         if abs(r['return'] - r['walk_forward_return']) > 1e-6],
                    'windows': rows}
                out['E1_turnover'][f"{grid_name} | {strategy} | {cname}"] = agg
                print(f"  E1 {grid_name:46s} {short(strategy):12s} {cname:15s} | OOS {agg['oos_mean']:+7.3f}% | "
                      f"fills {agg['fills_mean']:6.1f} | traded {agg['notional_x_capital_mean']:5.2f}x capital | "
                      f"costs {agg['cost_pct_of_capital_mean']:5.2f}% of capital/window | rebalance_every "
                      f"{agg['rebalance_every_chosen']} top_n {agg['top_n_chosen']}"
                      + (f" | MISMATCH {agg['rerun_mismatches']}" if agg['rerun_mismatches'] else ''), flush=True)

    price_cache = {}
    for strategy, params in HINDSIGHT_SETTINGS:
        for cname, _ in CONFIGS:
            windows, costs = [], []
            for i, (ts, te) in enumerate(bounds[cname], 1):
                m, pf, t = _rerun(strategy, symbols, params, ts, te, DECISION_RATE, storage)
                key = (str(ts), str(te))
                if key not in price_cache:
                    price_cache[key] = segment_prices(storage, symbols, ts, te)
                path = daily_paths(price_cache[key], pf.trade_history, CAPITAL)
                windows.append({'window': i, 'test_start': str(ts), 'test_end': str(te),
                                'return': m.get('return_pct', 0.0),
                                'benchmark_return': m.get('benchmark_return_pct', 0.0),
                                'daily': [[str(d), float(r['strategy']), float(r['benchmark'])]
                                          for d, r in path.iterrows()]})
                costs.append(t)
            _, a = analyse(windows)
            a = {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in a.items()}
            a['notional_x_capital_mean'] = float(np.mean([c['notional_x_capital'] for c in costs]))
            a['cost_pct_of_capital_mean'] = float(np.mean([c['cost_pct_of_capital'] for c in costs]))
            out['E2_hindsight_alpha'][f"{strategy} {pkey(params)} | {cname}"] = a
            print(f"  E2 {short(strategy)} {pkey(params)} {cname:15s} | return {a['mean_return']:+.3f}% "
                  f"index {a['mean_benchmark']:+.3f}% excess {a['mean_excess']:+.3f}% | alpha "
                  f"{a['alpha_daily_pct']:+.4f}%/day t={a['alpha_t']:.2f} beta {a['beta']:.2f} (R2 {a['r2']:.2f}) "
                  f"| vol {a['strategy_daily_vol_pct']:.2f}%/day vs index {a['benchmark_daily_vol_pct']:.2f}%",
                  flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--smoke', action='store_true', help='2 symbols, plumbing check only')
    ap.add_argument('--out-dir', default=None, help='where to write (default results/)')
    ap.add_argument('--n-boot', type=int, default=5000)
    ap.add_argument('--exploratory', action='store_true',
                    help='run ONLY the post-hoc exploratory section (see the docstring)')
    args = ap.parse_args()

    t_all = time.time()
    storage = DataStorage(str(DATA))
    unis, cached = universes()
    if args.smoke:
        unis = {k: v[:2] for k, v in unis.items()}
    n_candidates = N_CANDIDATE_CONFIGS * len(unis)
    # Results go to results/hk_search/, outside the results index (midfreq.research.results_index reads
    # results/*.csv only): the record of 217 results the report, the dashboard and the demo cite stays as
    # it is until the amended report decides how the Hong Kong search enters it.
    out_dir = Path(args.out_dir) if args.out_dir else paths.results_dir() / 'hk_search'
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"HK cross-sectional, low turnover | universes {', '.join(f'{k} ({len(v)})' for k, v in unis.items())}"
          f" | {len(cached)} cached HK names | grid {len(GRID)} per strategy | "
          f"{n_candidates} candidate configurations | engine {ENGINE}, "
          f"{os.environ['MIDFREQ_ENGINE_THREADS']} threads", flush=True)

    if args.exploratory:
        result = {}
        for uni, symbols in unis.items():
            end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value)
            start = end - timedelta(days=HISTORY_DAYS)
            print(f"\n=== EXPLORATORY (post hoc, not candidates) | {uni}: {len(symbols)} symbols | "
                  f"{start} -> {end} ===", flush=True)
            result[uni] = exploratory(storage, symbols, start, end)
        name = f"hk_cross_sectional_exploratory_{'smoke_' if args.smoke else ''}{time.strftime('%Y%m%d_%H%M%S')}"
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(result, indent=1, default=str))
        print(f"\nDone in {(time.time()-t_all)/60:.1f} min | saved {path}")
        return

    rows, detail = [], {'pre_registered': {
        'universe': UNIVERSE, 'strategies': STRATEGIES, 'grid': GRID_AXES, 'configs': CONFIGS,
        'train_pct': TRAIN_PCT, 'objective': OBJECTIVE, 'slippage_bps': SLIPPAGE_BPS,
        'decision_rate': DECISION_RATE, 'rates': RATES, 'min_consistency': MIN_CONSISTENCY,
        'candidate_configurations': n_candidates, 'smoke': args.smoke}, 'universes': {}}

    for uni, symbols in unis.items():
        end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value)
        start = end - timedelta(days=HISTORY_DAYS)
        print(f"\n=== {uni}: {len(symbols)} symbols | {start} -> {end} ===", flush=True)
        ud = {'symbols': symbols, 'start': str(start), 'end': str(end), 'walk_forward': {},
              'fixed': {}, 'alpha': {}, 'reality_check': {}, 'live_pick': {}, 'runtime_s': {}}
        price_cache = {}
        bounds = {}
        for rate in RATES:
            for strategy in STRATEGIES:
                wf_cfg, fx_cfg = {}, {}
                for cname, n_splits in CONFIGS:
                    t0 = time.time()
                    wins, extra = run_walk_forward(strategy, symbols, start, end, n_splits, rate, storage)
                    t_wf = time.time() - t0
                    b = [(w['_test_start'], w['_test_end']) for w in wins]
                    bounds.setdefault(cname, [(str(x), str(y)) for x, y in b])
                    t1 = time.time()
                    fixed = run_fixed(strategy, symbols, b, rate, storage)
                    t_fx = time.time() - t1
                    # the walk-forward's test run and the fixed setting it chose are the same backtest
                    mism = [w['window'] for i, w in enumerate(wins)
                            if abs(fixed[pkey(w['params'])][i]['return'] - w['return']) > 1e-9]
                    s = summarise(wins)
                    s.update(extra, consistency_check_mismatches=mism,
                             params_chosen={k: int(v) for k, v in pd.Series(
                                 [pkey(w['params']) for w in wins]).value_counts().items()})
                    wf_cfg[cname] = {'summary': s, 'windows': wins}
                    fx_cfg[cname] = {k: {'summary': summarise(v), 'windows': v} for k, v in fixed.items()}
                    ud['runtime_s'][f"{short(strategy)} | {rate} | {cname}"] = {
                        'walk_forward': round(t_wf, 1), 'fixed_settings': round(t_fx, 1)}
                    print(f"  {short(strategy):12s} {rate*100:.2f}%/side {cname:15s} | "
                          f"OOS {s['oos_mean']:+7.3f}% | {s['consistency']:5.1f}% windows > 0 | "
                          f"bench {s['benchmark_mean'] if s['benchmark_mean'] is not None else float('nan'):+7.3f}% | "
                          f"fills/window {s['fills_mean']:6.1f} | wf {t_wf:.0f}s fixed {t_fx:.0f}s"
                          + (f" | MISMATCH {mism}" if mism else ''), flush=True)
                ud['walk_forward'][f"{strategy} | {rate}"] = wf_cfg
                ud['fixed'][f"{strategy} | {rate}"] = fx_cfg

                a, b_ = (wf_cfg[c]['summary'] for c, _ in CONFIGS)
                passes = qualifies(a['oos_mean'], a['consistency']) and qualifies(b_['oos_mean'], b_['consistency'])
                rows.append(_row(uni, 'walk_forward', strategy, '', rate, a, b_,
                                 passes if rate == DECISION_RATE else None))
                for k in fx_cfg[CONFIGS[0][0]]:
                    fa, fb = (fx_cfg[c][k]['summary'] for c, _ in CONFIGS)
                    met = qualifies(fa['oos_mean'], fa['consistency']) and qualifies(fb['oos_mean'], fb['consistency'])
                    rows.append(_row(uni, 'fixed_setting', strategy, k, rate, fa, fb, None, met))

        # alpha view of the two walk-forward candidates at the decision rate
        for strategy in STRATEGIES:
            for cname, _ in CONFIGS:
                t0 = time.time()
                wins = ud['walk_forward'][f"{strategy} | {DECISION_RATE}"][cname]['windows']
                av = alpha_view(strategy, symbols, wins, DECISION_RATE, storage, price_cache)
                ud['alpha'][f"{strategy} | {cname}"] = av
                print(f"  alpha {short(strategy):12s} {cname:15s} | excess {av['mean_excess']:+.3f}%/window "
                      f"({av['pct_windows_excess_positive']:.0f}% > 0) | alpha {av['alpha_daily_pct']:+.4f}%/day "
                      f"t={av['alpha_t']:.2f} beta {av['beta']:.2f} | re-run mismatches {av['rerun_mismatches']} "
                      f"| {time.time()-t0:.0f}s", flush=True)

        # White's Reality Check at the decision rate
        for cname, _ in CONFIGS:
            fixed_c = {f"{short(s)} {k}": ud['fixed'][f"{s} | {DECISION_RATE}"][cname][k]['summary']['oos_returns']
                       for s in STRATEGIES for k in ud['fixed'][f"{s} | {DECISION_RATE}"][cname]}
            wf_c = {short(s): ud['walk_forward'][f"{s} | {DECISION_RATE}"][cname]['summary']['oos_returns']
                    for s in STRATEGIES}
            res = {}
            for label, cands in (('fixed_settings_36', fixed_c), ('walk_forward_2', wf_c)):
                try:
                    rc = reality_check(cands, n_boot=args.n_boot, mean_block=RC_BLOCK, seed=RC_SEED)
                    rc['naive_p_value'] = naive_single_test(cands[rc['best_candidate']], n_boot=args.n_boot,
                                                            mean_block=RC_BLOCK, seed=RC_SEED)
                except ValueError as e:
                    rc = {'error': str(e)}
                res[label] = rc
                if 'p_value' in rc:
                    print(f"  RC {cname:15s} {label:18s}: best {rc['best_candidate']} "
                          f"{rc['observed_max_mean']:+.3f}% | RC p {rc['p_value']:.4f} | naive p "
                          f"{rc['naive_p_value']:.4f}", flush=True)
                else:
                    print(f"  RC {cname:15s} {label:18s}: {rc['error']}", flush=True)
            ud['reality_check'][cname] = res

        for strategy in STRATEGIES:
            lp = live_pick(strategy, symbols, storage)
            ud['live_pick'][strategy] = lp
            b = lp['best']
            print(f"  live pick {short(strategy):12s} {lp['train_start'][:10]} -> {lp['train_end'][:16]}: "
                  + (f"lookback={b['lookback']} top_n={b['top_n']} rebalance_every={b['rebalance_every']} "
                     f"sharpe {b['objective']:.3f} return {b['return_pct']:+.2f}% trades {b['trades']}"
                     if b else 'nothing traded')
                  + (f" | EDGE: {'; '.join(lp['boundary_warnings'])}" if lp['boundary_warnings'] else ''),
                  flush=True)
        ud['bounds'] = bounds
        for by_cfg in ud['walk_forward'].values():
            for c in by_cfg.values():
                c['windows'] = [{k: v for k, v in w.items() if not k.startswith('_')} for w in c['windows']]
        detail['universes'][uni] = ud

    df = pd.DataFrame(rows)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    name = f"hk_cross_sectional_{'smoke_' if args.smoke else ''}{stamp}"
    csv = out_dir / f"{name}.csv"
    df.to_csv(csv, index=False)
    detail['runtime_min'] = round((time.time() - t_all) / 60, 2)
    (out_dir / f"{name}.json").write_text(json.dumps(detail, indent=1, default=str))

    print(f"\nDone in {(time.time()-t_all)/60:.1f} min | saved {csv} (+ .json)\n")
    view = df[(df.kind == 'walk_forward')]
    print(view[['universe', 'strategy', 'commission', 'config_a_oos', 'config_a_consistency',
                'config_b_oos', 'config_b_consistency', 'config_a_fills', 'config_b_fills',
                'passes']].to_string(index=False))
    dec = df[(df.kind == 'walk_forward') & (df.commission == DECISION_RATE)]
    print(f"\n{int(dec['passes'].sum())}/{len(dec)} walk-forward candidates pass at "
          f"{DECISION_RATE*100:.2f}%/side; {n_candidates} candidate configurations searched")
    fx = df[(df.kind == 'fixed_setting') & (df.commission == DECISION_RATE)]
    print(f"{int(fx['fixed_rule_met'].sum())}/{len(fx)} fixed settings meet the rule in hindsight "
          f"(not passes; see the Reality Check)")


def _row(uni, kind, strategy, setting, rate, a, b, passes, fixed_met=None):
    return {
        'universe': uni, 'kind': kind, 'strategy': strategy, 'setting': setting,
        'commission': rate, 'slippage_bps': SLIPPAGE_BPS,
        'config_a_oos': round(a['oos_mean'], 3), 'config_a_consistency': round(a['consistency'], 1),
        'config_b_oos': round(b['oos_mean'], 3), 'config_b_consistency': round(b['consistency'], 1),
        'config_a_benchmark': _r(a['benchmark_mean']), 'config_b_benchmark': _r(b['benchmark_mean']),
        'config_a_excess': _r(a['excess_mean']), 'config_b_excess': _r(b['excess_mean']),
        'config_a_excess_pos_pct': _r(a['pct_windows_excess_positive'], 1),
        'config_b_excess_pos_pct': _r(b['pct_windows_excess_positive'], 1),
        'config_a_fills': round(a['fills_mean'], 1), 'config_b_fills': round(b['fills_mean'], 1),
        'config_a_exit_adj_oos': round(a['exit_adjusted_mean'], 3),
        'config_b_exit_adj_oos': round(b['exit_adjusted_mean'], 3),
        'passes': passes, 'fixed_rule_met': fixed_met,
    }


def _r(x, nd=3):
    return None if x is None else round(x, nd)


if __name__ == '__main__':
    main()
