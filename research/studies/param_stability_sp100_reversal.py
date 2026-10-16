"""
Parameter stability of the live S&P 100 reversal setting (30/3/19)
====================================================================
The S&P 100 forward test trades Cross-Sectional Reversal with lookback 30,
top_n 3, rebalance_every 19. That setting was not chosen by the 3-year
walk-forward: scripts/live/pick_sp100_live_config.py grid-searched the 96
settings of the registry grid (lookback 5-30 step 5, top_n 1-4,
rebalance_every 1, 7, 13, 19) by Sharpe ratio on the most recent 84 days
(2026-07-03 16:00 -> 2026-09-25 16:00, scored through the close of the last
day), at 0.005% commission per side and 5 bps slippage, over the 98 symbols
of config/sp100_forward_test.json (US.WMT and US.XOM left out as stale). It
scored Sharpe 5.490139617253906, +54.34602599596977%, 99 trades; the
runner-up scored 5.00. Its lookback and rebalance_every are both the grid's
maximum, so whether it sits on a smooth hill of good settings or on an
isolated spike cannot be seen on that grid. This study extends the grid past
both edges and asks that question, with the rules below fixed before any of
steps 2-5 was run (written and committed after step 0 only).

Design (as briefed, recorded verbatim)
--------------------------------------
Step 0: sanity anchor
Re-run 30/3/19 on the exact picker setup (same window, symbols, costs,
end-inclusive scoring as the picker on main now does: start_date=start,
end_date=end.date()) and confirm Sharpe 5.490139617253906, return
54.34602599596977, 99 trades exactly. Also confirm the 96-grid ranking
reproduces the runner-up at 5.00. If it doesn't match, stop and report why.

Step 2: in-sample surface (the 84-day selection window)
Fine grid: lookback 5-60 step 1, top_n 1-8, rebalance_every 1-40 step 1
(≈17,920 settings; with the C++ batch runner this should be minutes; if it is
far slower, coarsen lookback to step 2 and say so). Record Sharpe, return,
trades, max drawdown per setting. Compute the N26 table, the PLATEAU and
LOCATION tests, the rank/percentile of 30/3/19, and the distribution of
Sharpe across the fine grid.

Step 3: the same surface on data the setting was not chosen on
Fixed-parameter backtests (no re-optimisation) of the same fine grid on the
period BEFORE the selection window: from the start of the common S&P 100
history (check the cache; about 2023-08-22) to 2026-07-03 16:00,
end-exclusive at the window start so there is no overlap. Same symbols, costs
and slippage. Also split that period into three roughly one-year sub-periods
and compute the N26-median Sharpe and the percentile of 30/3/19 in each, to
see whether any "hill" stays in the same place. GENERALISATION test as
pre-registered.

Step 4: rebalance-phase sensitivity
With rebalance_every 19 and 7 hourly bars per US day, when rebalances happen
depends on the starting bar. For 30/3/19 on the 84-day window, shift the
start by k = 0..18 hourly bars (keep the same end) and report the spread of
Sharpe and return across the 19 phases (min / median / max, and where k=0
falls). Descriptive only.

Step 5: walk-forward choices (cheap, from existing results)
From the existing S&P 100 cross-sectional walk-forward result files (Config A
9 windows, Config B 15), list the parameters each window chose and count how
many lie within the N26 box (or equal 30/3/19). Descriptive only. Do not
re-run the walk-forward.

Pre-registered rules (recorded verbatim; fixed now, not to be tuned after seeing results)
-----------------------------------------------------------------------------------------
- Neighbourhood N26 = the 26 settings at ±1 original-grid step around 30/3/19: lookback ∈ {25,30,35}, top_n ∈ {2,3,4}, rebalance_every ∈ {13,19,25}, excluding 30/3/19 itself (35 and 25 lie outside the original grid; that is the point).
- "Close" = in-sample Sharpe ≥ 80% of 30/3/19's (≥ 0.8 × 5.4901).
- PLATEAU test (in-sample, the 84-day window): passes if at least 20 of the 26 N26 settings are "close".
- LOCATION test (in-sample): using the fine grid of step 2, the setting with the best SMOOTHED Sharpe (mean Sharpe over itself and its fine-grid neighbours at ±1 step on each axis, edges using whatever neighbours exist) lies within lookback ±5, top_n ±1, rebalance_every ±6 of 30/3/19.
- GENERALISATION test (step 3, data the setting was not chosen on): passes if the median Sharpe of 30/3/19 + N26 (27 settings) on the earlier period is > 0 AND above the median Sharpe of all fine-grid settings on that period.
- Overall: "robust" only if all three pass; otherwise report which failed. A pass here does not validate the strategy; a failure means 30/3/19's in-sample score is not evidence of a stable optimum.

How the rules are read here (fixed with them, before steps 2-5 were run)
-------------------------------------------------------------------------
1. Setup. Capital 100,000; commission 5e-05 per side; slippage 5 bps; no
   risk manager; hourly bars from data/; the 98 symbols of
   config/sp100_forward_test.json, in that order. Sharpe is the backtester's
   own (daily returns, mean/stdev x sqrt(252)). Every backtest is one
   fixed-parameter run from a flat portfolio. Engine: the batch runner
   (midfreq.backtest.native.BatchRunner, C++ where it can, else Python; the
   engine used is recorded).
2. In-sample window: start 2026-07-03 16:00, end_date 2026-09-25 with
   end_inclusive=True, exactly the picker's call (3 Jul 2026 was a market
   holiday, so the first bar is 6 Jul 10:30; 413 hourly cross-sections).
3. Fine grid: lookback 5..60 step 1 (56 values) x top_n 1..8 (8) x
   rebalance_every 1..40 step 1 (40) = 17,920 settings.
4. "Close": Sharpe >= 0.8 x 5.490139617253906 = 4.392111693803125.
5. LOCATION. "The fine grid of step 2" is read as the fine grid defined in
   Step 2 (step 1 on every axis; lookback is coarsened only if the run is
   far too slow, in which case its +-1 step is +-2). "Its fine-grid
   neighbours at +-1 step on each axis" is read as the 3x3x3 box around the
   setting (every combination of -1, 0, +1 steps on the three axes: up to 26
   neighbours, the same shape as N26), clipped at the grid's edges to the
   neighbours that exist; the smoothed Sharpe is the unweighted mean over the
   setting and those neighbours. "Within lookback +-5, top_n +-1,
   rebalance_every +-6 of 30/3/19" is lookback 25..35, top_n 2..4,
   rebalance_every 13..25, inclusive. A tie for the best smoothed Sharpe
   (not expected) goes to the first in (lookback, top_n, rebalance_every)
   order. As a descriptive check only, NOT part of the verdict, the smoothed
   optimum under the 7-point cross reading (+-1 along one axis at a time) is
   also reported.
6. Rank and percentile over all fine-grid settings that returned a result
   (a setting that never traded counts, with its Sharpe of 0): rank 1 is the
   highest Sharpe; percentile = 100 x the share of the OTHER settings with a
   strictly lower Sharpe. "Best setting" is the highest Sharpe among the
   settings that traded (the picker's rule).
7. Earlier period: start 2023-08-25 10:30, the first bar at which 97 of the
   98 symbols have data in the cache (13 have bars from 14 Aug 2023; GEV
   starts on 27 Mar 2024 and joins the ranking then, as it did in the
   walk-forward), to end_date 2026-07-03 16:00 with end_inclusive=False (the
   last bar is 2 Jul 2026 16:00). The cache does not reach back to the
   walk-forward's 2023-08-22 16:00 for 85 of the 98 symbols, so the common
   history starts on 25 Aug.
8. Sub-periods: calendar thirds of the earlier period, cut at midnight:
   [2023-08-25 10:30, 2024-08-07 00:00), [2024-08-07 00:00, 2025-07-20 00:00),
   [2025-07-20 00:00, 2026-07-03 16:00), each its own set of backtests from
   flat. Reported for each (descriptive): the N26 median (the 26
   neighbours), the median of the 27 settings, the fine-grid median, the
   rank and percentile of 30/3/19, and the best and best smoothed settings.
9. GENERALISATION: "the median Sharpe of all fine-grid settings on that
   period" is the median over all 17,920 settings on the earlier period
   (2023-08-25 10:30 -> 2026-07-03 16:00, one backtest each).
10. Phase: start_k is the k-th hourly cross-section of the in-sample window
   (k = 0 is 6 Jul 2026 10:30), end unchanged (through 25 Sep 2026),
   k = 0..18. Descriptive.
11. Walk-forward: the two saved walk_forward runs in the run store,
   runs/20261015T203710_walk_forward_d88c7d (Config A, 9 windows) and
   runs/20261015T203710_walk_forward_662c28 (Config B, 15 windows): the
   alpha study's walk-forward (100 symbols, 0.005%/side + 5 bps, history
   ending 21 Aug 2026) imported by import_is_vs_oos.py with each window's
   chosen parameters. A choice counts if it lies in the 27-setting box
   (lookback in {25,30,35}, top_n in {2,3,4}, rebalance_every in
   {13,19,25}); exact matches with 30/3/19 are counted separately. The
   walk-forward searched the 96-setting grid, so only lookback 25/30 and
   rebalance_every 13/19 of the box were available to it.
12. Step 0 runs first, inside this script: the 96-setting grid on the
   in-sample window. The script stops unless 30/3/19 is the best with Sharpe
   exactly 5.490139617253906, return 54.34602599596977% and 99 trades, and
   the runner-up rounds to 5.00.

Outputs: results/param_stability_sp100_reversal_<stamp>.json (setup, step 0,
every test with its numbers and PASS/FAIL, the N26 table, the summaries),
one CSV per surface (results/param_stability_sp100_reversal_<surface>_<stamp>.csv
for insample, earlier, sub1, sub2, sub3, plus n26, phase and walkforward) and
three figures in results/charts/. Reads data/ and runs/, never fetches; only
symbols whose bar file exists are asked for.

Usage:
    uv run python research/studies/param_stability_sp100_reversal.py
    uv run python research/studies/param_stability_sp100_reversal.py --figures-only results/param_stability_sp100_reversal_<stamp>.json
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from midfreq.core import paths
from midfreq.core.candle import Timeframe
from midfreq.data.storage import DataStorage
from midfreq.backtest.native import BatchRunner
from midfreq.backtest.optimizer import generate_param_grid
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

REPO = paths.repo_root()
RESULTS = REPO / 'results'
CHARTS = RESULTS / 'charts'
PREFIX = 'param_stability_sp100_reversal'

STRATEGY = 'Cross-Sectional Reversal'
CAPITAL = 100_000.0
COMMISSION = 0.00005
SLIPPAGE_BPS = 5.0
TF = Timeframe.HOUR_1

TRAIN_START = pd.Timestamp('2026-07-03 16:00')          # naive, as the picker passes it
TRAIN_END = pd.Timestamp('2026-09-25 16:00')            # scored through the close of this day
EARLY_START = pd.Timestamp('2023-08-25 10:30')
EARLY_END = TRAIN_START                                 # end-exclusive
SUB_CUTS = [EARLY_START, pd.Timestamp('2024-08-07 00:00'), pd.Timestamp('2025-07-20 00:00'), EARLY_END]

ANCHOR = (30, 3, 19)
ANCHOR_SHARPE = 5.490139617253906
ANCHOR_RETURN = 54.34602599596977
ANCHOR_TRADES = 99
CLOSE_FRAC = 0.8
PLATEAU_MIN_CLOSE = 20
LOC_TOL = (5, 1, 6)

LOOKBACKS = list(range(5, 61))
TOP_NS = list(range(1, 9))
REBALS = list(range(1, 41))
AXES = (LOOKBACKS, TOP_NS, REBALS)
KEYS = ('lookback', 'top_n', 'rebalance_every')
N26_VALUES = ([25, 30, 35], [2, 3, 4], [13, 19, 25])
PHASES = 19

WF_RUNS = {'A (9 windows)': '20261015T203710_walk_forward_d88c7d',
           'B (15 windows)': '20261015T203710_walk_forward_662c28'}

FORWARD_CONFIG = REPO / 'config' / 'sp100_forward_test.json'


# ─── Setup ───────────────────────────────────────────────────────────────────────────────────

def load_symbols():
    cfg = json.loads(FORWARD_CONFIG.read_text())
    symbols = cfg['symbols']
    assert len(symbols) == 98 and 'US.WMT' not in symbols and 'US.XOM' not in symbols, len(symbols)
    missing = [s for s in symbols if not (REPO / 'data' / s.replace('.', '_') / '1h.parquet').exists()]
    if missing:
        # DataStorage would create an empty folder for each; never ask for a symbol that is not cached
        raise SystemExit(f"no cached bars for {missing}; stopping before DataStorage is touched")
    return symbols, cfg


def data_fingerprint(symbols):
    h = hashlib.sha256()
    out = {}
    for s in symbols:
        f = REPO / 'data' / s.replace('.', '_') / '1h.parquet'
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        h.update(f"{s}:{digest}\n".encode())
        out[s] = digest
    return h.hexdigest(), out


def bar_times(symbols):
    ts = set()
    for s in symbols:
        ts |= set(pd.read_parquet(REPO / 'data' / s.replace('.', '_') / '1h.parquet').index)
    return pd.DatetimeIndex(sorted(ts))


def fine_grid():
    return [dict(zip(KEYS, (lb, n, r))) for lb in LOOKBACKS for n in TOP_NS for r in REBALS]


def run_surface(runner, grid, start, end, end_inclusive, label, chunk=2048):
    t0 = time.time()
    rows = []
    for lo in range(0, len(grid), chunk):
        batch = grid[lo:lo + chunk]
        for p, (m, _last) in zip(batch, runner.run(batch, start, end, end_inclusive=end_inclusive)):
            rows.append({**p, 'ok': bool(m), 'sharpe': m.get('sharpe_ratio', np.nan) if m else np.nan,
                         'return_pct': m.get('return_pct', np.nan) if m else np.nan,
                         'trades': m.get('total_trades', 0) if m else 0,
                         'max_drawdown': m.get('max_drawdown', np.nan) if m else np.nan,
                         'win_rate': m.get('win_rate', np.nan) if m else np.nan})
        print(f"    {label}: {min(lo + chunk, len(grid))}/{len(grid)} ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    return df, time.time() - t0


# ─── Analysis ────────────────────────────────────────────────────────────────────────────────

def cube(df, col='sharpe'):
    """The surface as an array indexed [lookback, top_n, rebalance_every] (NaN where no result)."""
    arr = np.full(tuple(len(a) for a in AXES), np.nan)
    i = df['lookback'].to_numpy() - LOOKBACKS[0]
    j = df['top_n'].to_numpy() - TOP_NS[0]
    k = df['rebalance_every'].to_numpy() - REBALS[0]
    arr[i, j, k] = df[col].to_numpy(dtype=float)
    return arr


def smooth(arr, shape='box'):
    """Mean over each cell and its existing +-1 neighbours: the 3x3x3 box, or the 7-point cross."""
    valid = ~np.isnan(arr)
    vals = np.where(valid, arr, 0.0)
    pv = np.pad(vals, 1)
    pc = np.pad(valid.astype(float), 1)
    s = np.zeros_like(vals)
    c = np.zeros_like(vals)
    n0, n1, n2 = arr.shape
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for dk in (-1, 0, 1):
                if shape == 'cross' and (di != 0) + (dj != 0) + (dk != 0) > 1:
                    continue
                s += pv[1 + di:1 + di + n0, 1 + dj:1 + dj + n1, 1 + dk:1 + dk + n2]
                c += pc[1 + di:1 + di + n0, 1 + dj:1 + dj + n1, 1 + dk:1 + dk + n2]
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(c > 0, s / c, np.nan)


def argbest(arr):
    """First (lookback, top_n, rebalance_every) with the highest value (C order = that order)."""
    flat = np.where(np.isnan(arr), -np.inf, arr).ravel()
    i, j, k = np.unravel_index(int(np.argmax(flat)), arr.shape)
    return (AXES[0][i], AXES[1][j], AXES[2][k]), float(arr[i, j, k])


def at(arr, setting):
    lb, n, r = setting
    return float(arr[lb - LOOKBACKS[0], n - TOP_NS[0], r - REBALS[0]])


def rank_of(values, x):
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    return {'rank': int((v > x).sum()) + 1, 'of': int(len(v)),
            'percentile': 100.0 * float((v < x).sum()) / (len(v) - 1),
            'n_strictly_higher': int((v > x).sum())}


def distribution(values):
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    q = np.percentile(v, [0, 5, 10, 25, 50, 75, 90, 95, 100])
    return {'n': int(len(v)), 'mean': float(v.mean()), 'std': float(v.std(ddof=1)),
            'min': float(q[0]), 'p05': float(q[1]), 'p10': float(q[2]), 'p25': float(q[3]),
            'median': float(q[4]), 'p75': float(q[5]), 'p90': float(q[6]), 'p95': float(q[7]),
            'max': float(q[8]), 'share_positive': float((v > 0).mean()),
            'share_close_to_anchor_in_sample': float((v >= CLOSE_FRAC * ANCHOR_SHARPE).mean())}


def n26_settings():
    return [(lb, n, r) for lb in N26_VALUES[0] for n in N26_VALUES[1] for r in N26_VALUES[2]
            if (lb, n, r) != ANCHOR]


def in_original_grid(setting):
    lb, n, r = setting
    return lb in range(5, 31, 5) and n in range(1, 5) and r in (1, 7, 13, 19)


def within_tolerance(setting):
    return all(abs(a - b) <= t for a, b, t in zip(setting, ANCHOR, LOC_TOL))


def best_traded(df):
    t = df[(df.trades > 0) & df.ok]
    r = t.sort_values(['sharpe'], ascending=False, kind='mergesort').iloc[0]
    return {'setting': [int(r.lookback), int(r.top_n), int(r.rebalance_every)], 'sharpe': float(r.sharpe),
            'return_pct': float(r.return_pct), 'trades': int(r.trades)}


def period_summary(df, label):
    arr = cube(df)
    sm = smooth(arr, 'box')
    sm_cross = smooth(arr, 'cross')
    best_sm, best_sm_val = argbest(sm)
    best_cr, best_cr_val = argbest(sm_cross)
    n26 = [at(arr, s) for s in n26_settings()]
    anchor = at(arr, ANCHOR)
    return {
        'label': label,
        'anchor_sharpe': anchor,
        'anchor_return_pct': at(cube(df, 'return_pct'), ANCHOR),
        'anchor_trades': int(at(cube(df, 'trades'), ANCHOR)),
        'anchor_rank': rank_of(arr.ravel(), anchor),
        'anchor_smoothed_sharpe': at(sm, ANCHOR),
        'n26_median': float(np.median(n26)),
        'n27_median': float(np.median(n26 + [anchor])),
        'fine_grid_median': float(np.nanmedian(arr)),
        'distribution': distribution(arr.ravel()),
        'best': best_traded(df),
        'best_smoothed': {'setting': list(best_sm), 'smoothed_sharpe': best_sm_val,
                          'raw_sharpe': at(arr, best_sm), 'within_location_tolerance': within_tolerance(best_sm)},
        'best_smoothed_cross_descriptive': {'setting': list(best_cr), 'smoothed_sharpe': best_cr_val,
                                            'raw_sharpe': at(arr, best_cr),
                                            'within_location_tolerance': within_tolerance(best_cr)},
        'n_settings': int(len(df)), 'n_failed': int((~df.ok).sum()),
        'n_zero_trade': int((df.ok & (df.trades == 0)).sum()),
        'n_above_anchor_in_sample_value': int((arr > ANCHOR_SHARPE).sum()),
    }


def step0(runner):
    grid = generate_param_grid(STRATEGY)
    assert len(grid) == 96, len(grid)
    res = runner.run(grid, TRAIN_START, TRAIN_END.date(), end_inclusive=True)
    rows = [{**p, 'sharpe': m.get('sharpe_ratio', 0.0), 'return_pct': m.get('return_pct', 0.0),
             'trades': m.get('total_trades', 0)} for p, (m, _) in zip(grid, res) if m]
    df = pd.DataFrame(rows)
    df = df[df.trades > 0].sort_values('sharpe', ascending=False, kind='mergesort').reset_index(drop=True)
    top, second = df.iloc[0], df.iloc[1]
    out = {'best': [int(top.lookback), int(top.top_n), int(top.rebalance_every)], 'sharpe': float(top.sharpe),
           'return_pct': float(top.return_pct), 'trades': int(top.trades),
           'runner_up': [int(second.lookback), int(second.top_n), int(second.rebalance_every)],
           'runner_up_sharpe': float(second.sharpe), 'configs_traded': int(len(df)),
           'top5': df.head(5).to_dict('records')}
    ok = (tuple(out['best']) == ANCHOR and out['sharpe'] == ANCHOR_SHARPE and out['return_pct'] == ANCHOR_RETURN
          and out['trades'] == ANCHOR_TRADES and round(out['runner_up_sharpe'], 2) == 5.00)
    out['reproduced'] = bool(ok)
    return out


def walkforward_choices(runs_dir):
    rows, summary = [], {}
    box = set((lb, n, r) for lb in N26_VALUES[0] for n in N26_VALUES[1] for r in N26_VALUES[2])
    for config, run_id in WF_RUNS.items():
        d = Path(runs_dir) / run_id
        meta = json.loads((d / 'run.json').read_text())
        assert meta['config'] == config and meta['strategy'] == STRATEGY and meta['universe'] == 'sp100', meta['config']
        w = pd.read_parquet(d / 'windows.parquet')
        n_box = n_exact = 0
        for _, r in w.iterrows():
            s = (int(r.lookback), int(r.top_n), int(r.rebalance_every))
            in_box, exact = s in box, s == ANCHOR
            n_box += in_box
            n_exact += exact
            rows.append({'config': config, 'window': int(r.window), 'train_start': str(r.train_start),
                         'train_end': str(r.train_end), 'test_start': str(r.test_start), 'test_end': str(r.test_end),
                         'lookback': s[0], 'top_n': s[1], 'rebalance_every': s[2],
                         'in_n27_box': in_box, 'equals_anchor': exact,
                         'within_location_tolerance': within_tolerance(s),
                         'is_sharpe': float(r.is_sharpe_ratio), 'oos_return_pct': float(r.oos_return_pct)})
        summary[config] = {'run_id': run_id, 'windows': int(len(w)), 'in_n27_box': int(n_box),
                           'equals_anchor': int(n_exact),
                           'distinct_settings': int(len({(x['lookback'], x['top_n'], x['rebalance_every'])
                                                         for x in rows if x['config'] == config})),
                           'commission_rate': meta['commission_rate'], 'slippage_bps': meta['slippage_bps'],
                           'start': meta['start'], 'end': meta['end'], 'imported_sha256': meta.get('imported_sha256')}
    return pd.DataFrame(rows), summary


def _py(x):
    if isinstance(x, dict):
        return {str(k): _py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_py(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, pd.Timestamp):
        return str(x)
    return x


def git_state():
    def g(*a):
        try:
            return subprocess.run(['git', '-C', str(REPO), *a], capture_output=True, text=True).stdout.strip()
        except Exception:
            return None
    return {'commit': g('rev-parse', 'HEAD'), 'branch': g('rev-parse', '--abbrev-ref', 'HEAD'),
            'dirty': bool(g('status', '--porcelain', '--untracked-files=no'))}


# ─── Figures ─────────────────────────────────────────────────────────────────────────────────
# The report's chart style (Report/generate_charts.py): warm surface, ink text, muted ticks, no
# frame. Sharpe is diverging around 0, so it gets two hues and a neutral middle: the report's red
# for negative and a blue for positive (red-green would not survive colour-blindness).

SURFACE = '#EDE8E0'; INK = '#2D2D2D'; MUTED = '#6B6B63'; GRID = '#D8D3CA'
RED = '#C62828'; GREEN = '#2E7D32'; ORANGE = '#eb6834'; BLUE = '#1F4E89'
MID = '#F4F1EC'


def _style():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Helvetica Neue', 'Arial'],
                         'text.color': INK, 'axes.labelcolor': INK, 'xtick.color': INK, 'ytick.color': INK,
                         'axes.facecolor': SURFACE, 'figure.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
                         'font.size': 7.5, 'axes.labelsize': 7.5, 'xtick.labelsize': 7, 'ytick.labelsize': 7,
                         'legend.fontsize': 7, 'axes.titlesize': 7.5})
    return plt


def _cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list('sharpe', [RED, '#E2958A', MID, '#93AFD3', BLUE])


def _heat(ax, arr2d, norm, cmap):
    """arr2d indexed [lookback, rebalance_every]; lookback on x, rebalance_every on y."""
    x_edges = np.arange(LOOKBACKS[0] - 0.5, LOOKBACKS[-1] + 1.0)
    y_edges = np.arange(REBALS[0] - 0.5, REBALS[-1] + 1.0)
    m = ax.pcolormesh(x_edges, y_edges, arr2d.T, cmap=cmap, norm=norm, shading='flat', rasterized=True)
    for side in ('top', 'right', 'left', 'bottom'):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=2, color=MUTED, pad=2)
    ax.set_xticks([5, 10, 20, 30, 40, 50, 60])
    ax.set_yticks([1, 7, 13, 19, 25, 31, 40])
    return m


def _marks(ax, top_n, best_smoothed=None):
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((4.5, 0.5), 26, 19, fill=False, ec=INK, lw=0.9, ls=(0, (3, 2)), zorder=5))
    if top_n == ANCHOR[1]:
        ax.plot(ANCHOR[0], ANCHOR[2], marker='o', ms=6.5, mfc='none', mec='white', mew=2.4, zorder=6)
        ax.plot(ANCHOR[0], ANCHOR[2], marker='o', ms=6.5, mfc='none', mec=INK, mew=1.2, zorder=7)
    if best_smoothed is not None and best_smoothed[1] == top_n:
        ax.plot(best_smoothed[0], best_smoothed[2], marker='D', ms=4.5, mfc='none', mec='white', mew=2.2, zorder=6)
        ax.plot(best_smoothed[0], best_smoothed[2], marker='D', ms=4.5, mfc='none', mec=INK, mew=1.0, zorder=7)


def _legend_handles(best_label, show_best=True):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    h = [Line2D([], [], marker='o', ls='none', ms=6, mfc='none', mec=INK, mew=1.2, label='30/3/19 (live)')]
    if show_best:
        h.append(Line2D([], [], marker='D', ls='none', ms=4.5, mfc='none', mec=INK, mew=1.0, label=best_label))
    h.append(Patch(fc='none', ec=INK, lw=0.9, ls=(0, (3, 2)), label='original 96-setting grid'))
    return h


def _not_shown_note(fig, best_smoothed, label, y):
    lb, n, r = best_smoothed
    fig.text(0.5, y, f'{label} {lb}/{n}/{r} is at top_n = {n}, not shown', ha='center', va='top',
             fontsize=6.3, color=MUTED)


def fig_heatmaps(insample, out_path, best_smoothed, period_label):
    plt = _style()
    from matplotlib.colors import Normalize
    arr = cube(insample)
    panels = [2, 3, 4]
    sl = [arr[:, n - TOP_NS[0], :] for n in panels]
    vmax = float(np.nanmax(np.abs(np.stack(sl))))
    norm = Normalize(-vmax, vmax)
    cmap = _cmap()
    fig, axes = plt.subplots(3, 1, figsize=(3.5, 6.1), dpi=300, sharex=True)
    for ax, n, a in zip(axes, panels, sl):
        m = _heat(ax, a, norm, cmap)
        _marks(ax, n, best_smoothed)
        ax.set_ylabel('Rebalance every (bars)')
        ax.text(0.985, 0.955, f'top_n = {n}', transform=ax.transAxes, ha='right', va='top', fontsize=7.5,
                fontweight='semibold', bbox=dict(fc=SURFACE, ec='none', pad=1.5, alpha=0.85))
    axes[-1].set_xlabel('Lookback (hourly bars)')
    shown = best_smoothed[1] in panels
    fig.legend(handles=_legend_handles('best smoothed setting', shown), loc='upper center', ncol=3,
               frameon=False, bbox_to_anchor=(0.5, 1.0), handletextpad=0.4, columnspacing=1.0, fontsize=6.6)
    if not shown:
        _not_shown_note(fig, best_smoothed, 'Best smoothed setting', 0.968)
    fig.tight_layout(rect=(0, 0.1, 1, 0.975 if shown else 0.955), h_pad=0.6)
    cax = fig.add_axes([0.18, 0.058, 0.72, 0.013])
    cb = fig.colorbar(m, cax=cax, orientation='horizontal')
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=2, color=MUTED, labelsize=6.8, pad=1.5)
    cb.set_label(f'Sharpe ratio, {period_label}', fontsize=7, labelpad=2)
    fig.savefig(out_path)
    plt.close(fig)


def fig_single_heatmap(df, top_n, out_path, best_smoothed, period_label):
    plt = _style()
    from matplotlib.colors import Normalize
    arr = cube(df)[:, top_n - TOP_NS[0], :]
    vmax = float(np.nanmax(np.abs(arr)))
    norm = Normalize(-vmax, vmax)
    fig, ax = plt.subplots(figsize=(3.5, 2.75), dpi=300)
    m = _heat(ax, arr, norm, _cmap())
    _marks(ax, top_n, best_smoothed)
    ax.set_ylabel('Rebalance every (bars)')
    ax.set_xlabel('Lookback (hourly bars)')
    ax.text(0.985, 0.955, f'top_n = {top_n}', transform=ax.transAxes, ha='right', va='top', fontsize=7.5,
            fontweight='semibold', bbox=dict(fc=SURFACE, ec='none', pad=1.5, alpha=0.85))
    shown = best_smoothed[1] == top_n
    fig.legend(handles=_legend_handles('best smoothed (this period)', shown), loc='upper center', ncol=3,
               frameon=False, bbox_to_anchor=(0.5, 1.0), handletextpad=0.4, columnspacing=0.9, fontsize=6.4)
    if not shown:
        _not_shown_note(fig, best_smoothed, 'Best smoothed setting in this period:', 0.935)
    cb = fig.colorbar(m, ax=ax, fraction=0.045, pad=0.02)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=2, color=MUTED, labelsize=6.8, pad=1.5)
    cb.set_label(f'Sharpe ratio, {period_label}', fontsize=7, labelpad=3)
    fig.tight_layout(rect=(0, 0, 1, 0.93 if shown else 0.89))
    fig.savefig(out_path)
    plt.close(fig)


def fig_slices(insample, earlier, out_path, early_label):
    plt = _style()
    ins, ear = cube(insample), cube(earlier)
    lb0, n0, r0 = (ANCHOR[0] - LOOKBACKS[0], ANCHOR[1] - TOP_NS[0], ANCHOR[2] - REBALS[0])
    slices = [('Lookback (hourly bars), top_n 3, rebalance every 19', LOOKBACKS, ins[:, n0, r0], ear[:, n0, r0], 30, 30),
              ('Rebalance every (bars), lookback 30, top_n 3', REBALS, ins[lb0, n0, :], ear[lb0, n0, :], 19, 19),
              ('top_n, lookback 30, rebalance every 19', TOP_NS, ins[lb0, :, r0], ear[lb0, :, r0], 3, 4)]
    fig, axes = plt.subplots(3, 1, figsize=(3.5, 5.6), dpi=300)
    thr = CLOSE_FRAC * ANCHOR_SHARPE
    for ax, (xlabel, xs, a, b, anchor_x, grid_max) in zip(axes, slices):
        ax.grid(axis='y', color=GRID, lw=0.6, zorder=1)
        for side in ('top', 'right', 'left', 'bottom'):
            ax.spines[side].set_visible(False)
        ax.tick_params(length=0, pad=2)
        ax.axhline(0, color=INK, lw=0.8, zorder=2)
        if grid_max < xs[-1]:
            ax.axvspan(grid_max + 0.5, xs[-1] + 0.5, color=GRID, alpha=0.45, lw=0, zorder=0)
        ax.axhline(thr, color=MUTED, lw=0.8, ls=(0, (3, 2)), zorder=2)
        ax.axvline(anchor_x, color=MUTED, lw=0.6, ls=':', zorder=2)
        ms = 3.2 if len(xs) <= 8 else 0
        ax.plot(xs, a, color=BLUE, lw=1.4, marker='o' if ms else None, ms=ms, zorder=4, label='In-sample (84-day selection window)')
        ax.plot(xs, b, color=ORANGE, lw=1.4, marker='o' if ms else None, ms=ms, zorder=4, label=f'Earlier period ({early_label})')
        ax.set_xlim(xs[0] - 0.5, xs[-1] + 0.5)
        ax.set_xlabel(xlabel, labelpad=2)
        ax.set_ylabel('Sharpe ratio')
    axes[0].text(LOOKBACKS[-1] + 0.3, thr, '80% of 30/3/19', ha='right', va='bottom', fontsize=6.4, color=MUTED)
    axes[0].text(LOOKBACKS[-1] + 0.3, axes[0].get_ylim()[1], 'beyond the original grid', ha='right', va='top',
                 fontsize=6.4, color=MUTED)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=1, frameon=False, bbox_to_anchor=(0.5, 1.0), fontsize=6.8,
               handlelength=1.6)
    fig.tight_layout(rect=(0, 0, 1, 0.94), h_pad=0.9)
    fig.savefig(out_path)
    plt.close(fig)


def fig_slices_wide(insample, earlier, out_path, early_label):
    """The three slices side by side at the report's full text width, in plain words."""
    plt = _style()
    ins, ear = cube(insample), cube(earlier)
    lb0, n0, r0 = (ANCHOR[0] - LOOKBACKS[0], ANCHOR[1] - TOP_NS[0], ANCHOR[2] - REBALS[0])
    slices = [('Lookback (hourly candles)', LOOKBACKS, ins[:, n0, r0], ear[:, n0, r0], 30, 30),
              ('Rebalance every (cross-sections)', REBALS, ins[lb0, n0, :], ear[lb0, n0, :], 19, 19),
              ('Basket size (stocks)', TOP_NS, ins[lb0, :, r0], ear[lb0, :, r0], 3, 4)]
    fig, axes = plt.subplots(1, 3, figsize=(6.0, 2.05), dpi=300, sharey=True,
                             gridspec_kw={'width_ratios': [1.25, 1.0, 0.6]})
    thr = CLOSE_FRAC * ANCHOR_SHARPE
    for ax, (xlabel, xs, a, b, anchor_x, grid_max) in zip(axes, slices):
        ax.grid(axis='y', color=GRID, lw=0.6, zorder=1)
        for side in ('top', 'right', 'left', 'bottom'):
            ax.spines[side].set_visible(False)
        ax.tick_params(length=0, pad=2)
        ax.axhline(0, color=INK, lw=0.8, zorder=2)
        if grid_max < xs[-1]:
            ax.axvspan(grid_max + 0.5, xs[-1] + 0.5, color=GRID, alpha=0.45, lw=0, zorder=0)
        ax.axhline(thr, color=MUTED, lw=0.8, ls=(0, (3, 2)), zorder=2)
        ax.axvline(anchor_x, color=MUTED, lw=0.6, ls=':', zorder=2)
        ms = 3.0 if len(xs) <= 8 else 0
        ax.plot(xs, a, color=BLUE, lw=1.3, marker='o' if ms else None, ms=ms, zorder=4,
                label='84-day selection window (6 Jul – 25 Sep 2026)')
        ax.plot(xs, b, color=ORANGE, lw=1.3, marker='o' if ms else None, ms=ms, zorder=4,
                label=f'Before it ({early_label})')
        ax.set_xlim(xs[0] - 0.5, xs[-1] + 0.5)
        ax.set_xlabel(xlabel, labelpad=2)
    axes[0].set_ylabel('Sharpe ratio')
    axes[2].set_xticks(TOP_NS)
    axes[0].text(LOOKBACKS[0] + 0.5, thr, '80% of the live setting', ha='left', va='bottom', fontsize=6.2,
                 color=MUTED)
    axes[1].text(REBALS[-1] + 0.3, axes[1].get_ylim()[1], 'beyond the original grid', ha='right', va='top',
                 fontsize=6.2, color=MUTED)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.0), fontsize=6.8,
               handlelength=1.6, columnspacing=1.4)
    fig.tight_layout(rect=(0, 0, 1, 0.9), w_pad=0.8)
    fig.savefig(out_path)
    plt.close(fig)


def draw_figures(res, surfaces):
    CHARTS.mkdir(parents=True, exist_ok=True)
    ins, ear = surfaces['insample'], surfaces['earlier']
    bs_in = tuple(res['in_sample']['best_smoothed']['setting'])
    bs_ear = tuple(res['earlier_period']['best_smoothed']['setting'])
    early_label = '25 Aug 2023 – 2 Jul 2026'
    paths_out = {'fig1': CHARTS / f'{PREFIX}_fig1_insample_heatmaps.png',
                 'fig2': CHARTS / f'{PREFIX}_fig2_slices.png',
                 'fig3': CHARTS / f'{PREFIX}_fig3_earlier_heatmap.png',
                 'report': CHARTS / f'{PREFIX}_report_slices.png'}
    fig_heatmaps(ins, paths_out['fig1'], bs_in, 'in-sample (6 Jul – 25 Sep 2026)')
    fig_slices(ins, ear, paths_out['fig2'], early_label)
    fig_slices_wide(ins, ear, paths_out['report'], early_label)
    fig_single_heatmap(ear, 3, paths_out['fig3'], bs_ear, early_label)
    return {k: str(v.relative_to(REPO)) for k, v in paths_out.items()}


# ─── Main ────────────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--runs-dir', default=str(REPO / 'runs'), help='run store holding the walk-forward runs')
    ap.add_argument('--figures-only', default=None,
                    help='redraw the figures from a results JSON written by this script (no backtests)')
    args = ap.parse_args()

    if args.figures_only:
        res = json.loads(Path(args.figures_only).read_text())
        surfaces = {k: pd.read_csv(REPO / v) for k, v in res['files']['surfaces'].items()}
        print(draw_figures(res, surfaces))
        return

    t_all = time.time()
    stamp = time.strftime('%Y%m%d_%H%M%S')
    symbols, cfg = load_symbols()
    assert cfg['train_window']['start'] == str(TRAIN_START) and cfg['train_window']['end'] == str(TRAIN_END)
    assert cfg['commission_per_side'] == COMMISSION and cfg['slippage_bps'] == SLIPPAGE_BPS
    assert tuple(cfg['params'][k] for k in KEYS) == ANCHOR
    storage = DataStorage(str(REPO / 'data'))
    fp, fp_files = data_fingerprint(symbols)
    times = bar_times(symbols)

    cls = STRATEGY_REGISTRY[STRATEGY]['class']
    runner = BatchRunner(None, cls, symbols, TF, storage, CAPITAL, COMMISSION, None, SLIPPAGE_BPS)
    print(f"{STRATEGY} | {len(symbols)} symbols | engine {runner.engine}"
          + (f" (fallback: {runner.fallback_reason})" if runner.fallback_reason else ''), flush=True)

    # Step 0
    s0 = step0(runner)
    print(f"  step 0: best {s0['best']} Sharpe {s0['sharpe']!r} return {s0['return_pct']!r} trades {s0['trades']}; "
          f"runner-up {s0['runner_up']} {s0['runner_up_sharpe']:.4f} -> reproduced={s0['reproduced']}", flush=True)
    if not s0['reproduced']:
        raise SystemExit("step 0 did not reproduce the picker's numbers; stopping")

    grid = fine_grid()
    assert len(grid) == 17_920
    surfaces, seconds = {}, {}
    print("  step 2: in-sample surface", flush=True)
    surfaces['insample'], seconds['insample'] = run_surface(runner, grid, TRAIN_START, TRAIN_END.date(), True, 'in-sample')
    print("  step 3: earlier period and its thirds", flush=True)
    surfaces['earlier'], seconds['earlier'] = run_surface(runner, grid, EARLY_START, EARLY_END, False, 'earlier')
    for i in range(3):
        key = f'sub{i + 1}'
        surfaces[key], seconds[key] = run_surface(runner, grid, SUB_CUTS[i], SUB_CUTS[i + 1], False, key)

    ins = surfaces['insample']
    a_row = ins[(ins.lookback == ANCHOR[0]) & (ins.top_n == ANCHOR[1]) & (ins.rebalance_every == ANCHOR[2])].iloc[0]
    assert a_row.sharpe == ANCHOR_SHARPE and a_row.return_pct == ANCHOR_RETURN and a_row.trades == ANCHOR_TRADES

    # smoothed surfaces kept in the CSVs
    for key, df in surfaces.items():
        sm, smc = smooth(cube(df), 'box'), smooth(cube(df), 'cross')
        idx = (df.lookback - LOOKBACKS[0], df.top_n - TOP_NS[0], df.rebalance_every - REBALS[0])
        df['sharpe_smoothed_box'] = sm[idx]
        df['sharpe_smoothed_cross'] = smc[idx]

    summaries = {k: period_summary(df, k) for k, df in surfaces.items()}
    for i in range(3):
        summaries[f'sub{i + 1}']['start'] = str(SUB_CUTS[i])
        summaries[f'sub{i + 1}']['end_exclusive'] = str(SUB_CUTS[i + 1])

    # N26 table
    arrs = {k: cube(df) for k, df in surfaces.items()}
    rets = cube(ins, 'return_pct')
    trades = cube(ins, 'trades')
    thr = CLOSE_FRAC * ANCHOR_SHARPE
    n26_rows = []
    for s in n26_settings():
        r = {'lookback': s[0], 'top_n': s[1], 'rebalance_every': s[2], 'in_original_grid': in_original_grid(s),
             'insample_sharpe': at(arrs['insample'], s), 'insample_return_pct': at(rets, s),
             'insample_trades': int(at(trades, s)),
             'insample_ratio_to_anchor': at(arrs['insample'], s) / ANCHOR_SHARPE,
             'close': at(arrs['insample'], s) >= thr,
             'earlier_sharpe': at(arrs['earlier'], s)}
        for i in range(3):
            r[f'sub{i + 1}_sharpe'] = at(arrs[f'sub{i + 1}'], s)
        n26_rows.append(r)
    n26 = pd.DataFrame(n26_rows)

    # Tests
    n_close = int(n26.close.sum())
    plateau = {'threshold_sharpe': thr, 'n_close': n_close, 'of': 26, 'required': PLATEAU_MIN_CLOSE,
               'pass': n_close >= PLATEAU_MIN_CLOSE}
    bs = summaries['insample']['best_smoothed']
    location = {'best_smoothed_setting': bs['setting'], 'best_smoothed_sharpe': bs['smoothed_sharpe'],
                'raw_sharpe_there': bs['raw_sharpe'], 'anchor_smoothed_sharpe': summaries['insample']['anchor_smoothed_sharpe'],
                'tolerance': {'lookback': LOC_TOL[0], 'top_n': LOC_TOL[1], 'rebalance_every': LOC_TOL[2]},
                'offset_from_anchor': [b - a for a, b in zip(ANCHOR, bs['setting'])],
                'pass': bool(bs['within_location_tolerance']),
                'descriptive_cross_reading': summaries['insample']['best_smoothed_cross_descriptive']}
    e = summaries['earlier']
    generalisation = {'n27_median_earlier': e['n27_median'], 'fine_grid_median_earlier': e['fine_grid_median'],
                      'above_zero': e['n27_median'] > 0, 'above_fine_grid_median': e['n27_median'] > e['fine_grid_median'],
                      'pass': bool(e['n27_median'] > 0 and e['n27_median'] > e['fine_grid_median'])}
    tests = {'plateau': plateau, 'location': location, 'generalisation': generalisation}
    failed = [k for k, v in tests.items() if not v['pass']]
    verdict = {'robust': not failed, 'failed': failed,
               'statement': ('robust: all three pre-registered tests passed (this does not validate the strategy)'
                             if not failed else
                             f"not robust: failed {', '.join(failed)}; 30/3/19's in-sample score is not evidence "
                             f"of a stable optimum")}

    # Step 4: phase
    lo_ts = TRAIN_START.tz_localize('UTC')
    hi_ts = TRAIN_END.normalize().tz_localize('UTC') + pd.Timedelta(days=1)
    win = times[(times >= lo_ts) & (times < hi_ts)]
    starts = [win[k].tz_localize(None) for k in range(PHASES)]
    t0 = time.time()
    phase_rows = []
    for k, st in enumerate(starts):
        m, _ = runner.run([dict(zip(KEYS, ANCHOR))], st, TRAIN_END.date(), end_inclusive=True)[0]
        phase_rows.append({'k': k, 'start': str(st), 'sharpe': m['sharpe_ratio'], 'return_pct': m['return_pct'],
                           'trades': m['total_trades'], 'max_drawdown': m['max_drawdown']})
    seconds['phase'] = time.time() - t0
    phase = pd.DataFrame(phase_rows)
    assert phase.sharpe.iloc[0] == ANCHOR_SHARPE and phase.return_pct.iloc[0] == ANCHOR_RETURN
    k0 = phase.iloc[0]
    phase_summary = {
        'n_phases': PHASES,
        'sharpe': {'min': float(phase.sharpe.min()), 'median': float(phase.sharpe.median()),
                   'max': float(phase.sharpe.max()), 'k0': float(k0.sharpe),
                   'k0_rank_of_19': int((phase.sharpe > k0.sharpe).sum()) + 1,
                   'argmin_k': int(phase.sharpe.idxmin()), 'argmax_k': int(phase.sharpe.idxmax())},
        'return_pct': {'min': float(phase.return_pct.min()), 'median': float(phase.return_pct.median()),
                       'max': float(phase.return_pct.max()), 'k0': float(k0.return_pct),
                       'k0_rank_of_19': int((phase.return_pct > k0.return_pct).sum()) + 1},
        'n_phases_close': int((phase.sharpe >= thr).sum()),
    }

    # Step 5: walk-forward
    wf, wf_summary = walkforward_choices(args.runs_dir)

    # Write
    files = {'surfaces': {}}
    for key, df in surfaces.items():
        p = RESULTS / f'{PREFIX}_{key}_{stamp}.csv'
        df.to_csv(p, index=False, float_format='%.12g')
        files['surfaces'][key] = str(p.relative_to(REPO))
    for key, df in (('n26', n26), ('phase', phase), ('walkforward', wf)):
        p = RESULTS / f'{PREFIX}_{key}_{stamp}.csv'
        df.to_csv(p, index=False, float_format='%.12g')
        files[key] = str(p.relative_to(REPO))

    res = {
        'study': PREFIX, 'stamp': stamp, 'git': git_state(),
        'setup': {'strategy': STRATEGY, 'symbols': symbols, 'n_symbols': len(symbols), 'capital': CAPITAL,
                  'commission_per_side': COMMISSION, 'slippage_bps': SLIPPAGE_BPS, 'timeframe': TF.value,
                  'anchor': list(ANCHOR), 'in_sample': {'start': str(TRAIN_START), 'end_date': str(TRAIN_END.date()),
                                                        'end_inclusive': True},
                  'earlier': {'start': str(EARLY_START), 'end': str(EARLY_END), 'end_inclusive': False},
                  'sub_periods': [[str(SUB_CUTS[i]), str(SUB_CUTS[i + 1])] for i in range(3)],
                  'fine_grid': {'lookback': [LOOKBACKS[0], LOOKBACKS[-1], 1], 'top_n': [TOP_NS[0], TOP_NS[-1], 1],
                                'rebalance_every': [REBALS[0], REBALS[-1], 1], 'n': len(grid)},
                  'engine': runner.engine, 'engine_requested': runner.requested,
                  'fallback_reason': runner.fallback_reason,
                  'engine_threads_env': os.environ.get('MIDFREQ_ENGINE_THREADS'),
                  'data_fingerprint_sha256': fp, 'data_files_sha256': fp_files,
                  'bars_last_common': str(times.max()), 'in_sample_cross_sections': int(len(win)),
                  'earlier_cross_sections': int(((times >= EARLY_START.tz_localize('UTC'))
                                                 & (times < EARLY_END.tz_localize('UTC'))).sum()),
                  'host': platform.node(), 'python': platform.python_version()},
        'step0': s0,
        'tests': tests, 'verdict': verdict,
        'in_sample': summaries['insample'], 'earlier_period': summaries['earlier'],
        'sub_periods': {k: summaries[k] for k in ('sub1', 'sub2', 'sub3')},
        'n26_table': n26.to_dict('records'),
        'phase': {'summary': phase_summary, 'rows': phase.to_dict('records')},
        'walk_forward': {'summary': wf_summary, 'rows': wf.to_dict('records')},
        'seconds': {**seconds, 'total': time.time() - t_all},
        'files': files,
    }
    res['files']['figures'] = draw_figures(res, surfaces)
    out = RESULTS / f'{PREFIX}_{stamp}.json'
    out.write_text(json.dumps(_py(res), indent=2))

    print(f"\n  PLATEAU        : {n_close}/26 close (>= {thr:.4f}) -> {'PASS' if plateau['pass'] else 'FAIL'}")
    print(f"  LOCATION       : best smoothed {bs['setting']} ({bs['smoothed_sharpe']:.3f}) -> "
          f"{'PASS' if location['pass'] else 'FAIL'}")
    print(f"  GENERALISATION : 27-median {e['n27_median']:.3f} vs fine-grid median {e['fine_grid_median']:.3f} -> "
          f"{'PASS' if generalisation['pass'] else 'FAIL'}")
    print(f"  verdict        : {verdict['statement']}")
    print(f"  saved          : {out.relative_to(REPO)}  ({time.time() - t_all:.0f}s)")


if __name__ == '__main__':
    main()
