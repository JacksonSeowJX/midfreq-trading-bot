"""
Alpha of the S&P 100 cross-sectional reversal result, measured against the market
===================================================================================

Written and committed on 15 Oct 2026, before any of the numbers below were
computed, so that the measures and the verdict rule are fixed in advance.

Why. Section 4.9 of the report judges the result by its out-of-sample return
alone: +5.73% per window (Config A) and +0.63% (Config B) at the broker's
0.005% fee. The S&P 100 rose over the same three years, and a strategy that
always holds three of its stocks carries most of the market's movement. So
part of that return may be the market rising, not the ranking working. This
study separates the two.

Setup, fixed: the study behind the report's cost table (Section 4.9) - the
S&P 100, cross-sectional reversal, 96 settings searched in every window,
70/30 train/test, highest Sharpe ratio chosen, 5 bps slippage, 0.005% per
side, history ending 21 Aug 2026, Config A (9 windows) and Config B (15).
--end-date 2026-08-28 repeats it on the common window of Section 4.11.

Benchmark: an equal-weight buy-and-hold of the same stocks over each test
segment, bought at the segment's first candle with no fees. It is the
benchmark the backtester already reports for every run.

Measures:
  1. Per window: strategy return, benchmark return, and the excess of one
     over the other.
  2. Alpha and beta: daily returns of every test segment pooled, regressed
     as  r_strategy = alpha + beta * r_benchmark + e,  with Newey-West
     (5-lag) t-statistics.
  3. Market-neutral: the daily excess r_strategy - r_benchmark, which is the
     return of the chosen basket over the rest of the index (beta held at 1),
     with its mean and Newey-West t-statistic.

Verdict rule, fixed: the result shows alpha only if, in BOTH configurations,
  (a) the mean per-window excess is positive and at least 50% of windows
      have a positive excess (the report's dual-configuration standard,
      applied to the excess instead of the raw return), and
  (b) the regression alpha is positive with a t-statistic of at least 2.

Also reported, descriptively only (too short to test): the live forward
test against an equal-weight index of its 98 stocks, from the session logs.

Usage:
    python3 scripts/research/alpha_vs_market.py [--end-date 2026-08-21] [--jobs 2]
"""
import os
import sys
import json
import time
import argparse
import contextlib
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
sys.path.insert(0, str(REPO / 'scripts' / 'research'))

import numpy as np
import pandas as pd
import statsmodels.api as sm

from midfreq.core.candle import Timeframe
from midfreq.data.storage import DataStorage
from midfreq.core.portfolio import Portfolio
from midfreq.backtest.backtester import Backtester
from midfreq.backtest.optimizer import walk_forward
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY
from cross_sectional_cost_sensitivity import (sp100_symbols, STRATEGY, TRAIN_PCT, OBJECTIVE,
                                              SLIPPAGE_BPS, HISTORY_DAYS)

RATE = 0.00005                      # the broker's US fee, as in the report's cost table
CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
NW_LAGS = 5
CAPITAL = 100_000.0


def _quiet():
    return contextlib.redirect_stdout(open(os.devnull, 'w'))


def segment_prices(storage, symbols, start, end):
    """Hourly closes of every symbol over a test segment, filtered exactly as the backtester filters."""
    lo = pd.to_datetime(start, utc=True)
    hi = pd.to_datetime(end, utc=True) + pd.Timedelta(days=1)          # the backtester's inclusive end
    cols = {}
    for s in symbols:
        df = storage.load_data(s.replace('.', '_'), Timeframe.HOUR_1.value)
        if not df.empty:
            c = df['close'][(df.index >= lo) & (df.index < hi)]
            if not c.empty:
                cols[s] = c
    return pd.DataFrame(cols).sort_index()


def daily_paths(prices, trades, capital):
    """Value of the strategy and of the equal-weight index at the last candle of each day.

    Both are marked at the same moment with the same closes. The strategy's
    holdings and cash at that moment come from its own trade records; the
    index buys every stock at its first close in the segment and holds.
    """
    filled = prices.ffill()
    day_ends = filled.groupby(filled.index.normalize()).tail(1).index
    first = prices.apply(lambda c: c.dropna().iloc[0])
    trades = sorted(trades, key=lambda t: pd.Timestamp(t['timestamp']))
    qty, cash, k, rows = {}, capital, 0, []
    for t in day_ends:
        while k < len(trades) and pd.Timestamp(trades[k]['timestamp']) <= t:
            tr = trades[k]
            qty[tr['symbol']] = qty.get(tr['symbol'], 0) + (tr['qty'] if tr['action'] == 'BUY' else -tr['qty'])
            cash = tr['cash_after']
            k += 1
        px = filled.loc[t]
        held = sum(q * px[s] for s, q in qty.items() if q)
        rows.append({'day': t, 'strategy': cash + held, 'benchmark': (px / first).mean()})
    return pd.DataFrame(rows).set_index('day')


def run_config(task):
    """One configuration: the walk-forward, then each test segment re-run for its trades."""
    name, n_splits, symbols, start, end = task
    storage = DataStorage(str(REPO / 'data'))
    t = time.time()
    with _quiet():
        res = walk_forward(strategy_name=STRATEGY, symbols=symbols, timeframe=Timeframe.HOUR_1,
                           start_date=start, end_date=end, storage=storage, n_splits=n_splits,
                           train_pct=TRAIN_PCT, objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS,
                           commission_rate=RATE)
    cls = STRATEGY_REGISTRY[STRATEGY]['class']
    windows = []
    for w in res['windows']:
        pf = Portfolio(initial_cash=CAPITAL, commission_rate=RATE)
        bt = Backtester(storage=storage, portfolio=pf, risk_manager=None, slippage_bps=SLIPPAGE_BPS)
        with _quiet():
            m = bt.run(cls, symbols=symbols, timeframe=Timeframe.HOUR_1,
                       start_date=w.test_start, end_date=w.test_end, **w.best_params)
        paths = daily_paths(segment_prices(storage, symbols, w.test_start, w.test_end), pf.trade_history, CAPITAL)
        windows.append({
            'window': w.window_id, 'test_start': str(w.test_start), 'test_end': str(w.test_end),
            'params': w.best_params, 'trades': len(pf.trade_history),
            'return_walk_forward': w.test_metrics.get('return_pct', 0.0),
            'return': m['return_pct'], 'benchmark_return': m['benchmark_return_pct'],
            'path_return': (paths['strategy'].iloc[-1] / CAPITAL - 1) * 100,
            'path_benchmark_return': (paths['benchmark'].iloc[-1] - 1) * 100,
            'daily': [[str(d), float(r['strategy']), float(r['benchmark'])] for d, r in paths.iterrows()],
        })
    return name, windows, round(time.time() - t)


def daily_returns(window):
    """Daily returns of the strategy and the index within one test segment, from where each started."""
    p = pd.DataFrame(window['daily'], columns=['day', 'strategy', 'benchmark']).set_index('day')
    p = pd.concat([pd.DataFrame({'strategy': [CAPITAL], 'benchmark': [1.0]}, index=['start']), p])
    return p.pct_change().dropna()


def analyse(windows):
    per = pd.DataFrame([{k: w[k] for k in ('window', 'test_start', 'test_end', 'return', 'benchmark_return')}
                        for w in windows])
    per['excess'] = per['return'] - per['benchmark_return']
    daily = pd.concat([daily_returns(w) for w in windows])
    X = sm.add_constant(daily['benchmark'])
    fit = sm.OLS(daily['strategy'], X).fit(cov_type='HAC', cov_kwds={'maxlags': NW_LAGS})
    ex = daily['strategy'] - daily['benchmark']
    exfit = sm.OLS(ex, np.ones(len(ex))).fit(cov_type='HAC', cov_kwds={'maxlags': NW_LAGS})
    trading_days = 252
    out = {
        'windows': len(per),
        'mean_return': per['return'].mean(), 'mean_benchmark': per['benchmark_return'].mean(),
        'mean_excess': per['excess'].mean(), 'pct_windows_excess_positive': (per['excess'] > 0).mean() * 100,
        'days': len(daily),
        'alpha_daily_pct': fit.params['const'] * 100, 'alpha_t': fit.tvalues['const'],
        'alpha_annual_pct': fit.params['const'] * trading_days * 100,
        'beta': fit.params['benchmark'], 'beta_t': fit.tvalues['benchmark'], 'r2': fit.rsquared,
        'excess_daily_pct': exfit.params.iloc[0] * 100, 'excess_t': exfit.tvalues.iloc[0],
        'excess_annual_pct': exfit.params.iloc[0] * trading_days * 100,
        'strategy_daily_vol_pct': daily['strategy'].std() * 100,
        'benchmark_daily_vol_pct': daily['benchmark'].std() * 100,
    }
    out['passes_a'] = bool(out['mean_excess'] > 0 and out['pct_windows_excess_positive'] >= 50)
    out['passes_b'] = bool(out['alpha_daily_pct'] > 0 and out['alpha_t'] >= 2)
    return per, out


def live_forward_test():
    """The forward test against an equal-weight index of its own stocks, from the session logs."""
    from midfreq.research import forward_test as ft
    d = ft.load(REPO / 'live_sessions')
    if not d or not d['first_trade']:
        return None
    closes = {}
    for f in sorted((REPO / 'live_sessions').glob('session_*crosssectionalreversal*.jsonl')):
        for line in f.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('type') == 'candle_close':
                closes.setdefault(e['symbol'], {})[pd.Timestamp(e['timestamp'])] = e['close']
    px = pd.DataFrame(closes).sort_index()
    first = px.index[px.index >= pd.Timestamp(d['first_trade'], tz=px.index.tz)][0]
    base = px.loc[first]
    index = (px.loc[first:] / base).mean(axis=1)
    last = index.index[-1]
    return {'from_candle': str(first), 'to_candle': str(last), 'stocks': int(base.notna().sum()),
            'equal_weight_index_return_pct': (index.iloc[-1] - 1) * 100,
            'forward_test_return_pct': d['pnl_pct'], 'forward_test_as_of': str(d['as_of']),
            'note': 'descriptive only; the forward test is measured at its last recorded session, '
                    'the index at its last recorded candle'}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--end-date', default='2026-08-21')
    ap.add_argument('--jobs', type=int, default=2)
    args = ap.parse_args()

    storage = DataStorage(str(REPO / 'data'))
    symbols, dropped = sp100_symbols(storage, args.end_date)
    end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value, as_of=args.end_date)
    start = end - timedelta(days=HISTORY_DAYS)
    print(f"S&P 100 {STRATEGY} vs equal-weight index | {len(symbols)} symbols | {start} -> {end} | "
          f"{RATE * 100:.3f}%/side", flush=True)

    tasks = [(name, n, symbols, start, end) for name, n in CONFIGS]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        runs = {name: (wins, secs) for name, wins, secs in pool.map(run_config, tasks)}

    summary, rows = {}, []
    for name, _ in CONFIGS:
        wins, secs = runs[name]
        # every re-run must reproduce the walk-forward, and the daily paths must end where the backtester ends
        bad = [w['window'] for w in wins if abs(w['return'] - w['return_walk_forward']) > 1e-6
               or abs(w['path_return'] - w['return']) > 0.01
               or abs(w['path_benchmark_return'] - w['benchmark_return']) > 0.01]
        per, out = analyse(wins)
        out['rerun_mismatches'] = bad
        summary[name] = out
        rows += [dict(r, config=name) for r in per.to_dict('records')]
        print(f"\n{name} ({secs}s): strategy {out['mean_return']:+.2f}%/window, index "
              f"{out['mean_benchmark']:+.2f}%, excess {out['mean_excess']:+.2f}% "
              f"({out['pct_windows_excess_positive']:.0f}% of windows positive)")
        print(f"  daily, {out['days']} days: alpha {out['alpha_daily_pct']:+.3f}%/day "
              f"(t = {out['alpha_t']:.2f}), beta {out['beta']:.2f} (R2 {out['r2']:.2f}); "
              f"market-neutral excess {out['excess_daily_pct']:+.3f}%/day (t = {out['excess_t']:.2f})")
        if bad:
            print(f"  ! windows whose re-run did not reproduce the walk-forward: {bad}")

    verdict = all(s['passes_a'] and s['passes_b'] for s in summary.values())
    live = live_forward_test()
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out = REPO / 'results' / f"alpha_vs_market_{args.end_date.replace('-', '')}_{stamp}.json"
    out.write_text(json.dumps({
        'strategy': STRATEGY, 'universe': 'sp100', 'n_symbols': len(symbols), 'dropped': dropped,
        'start': str(start), 'end': str(end), 'commission_per_side': RATE, 'slippage_bps': SLIPPAGE_BPS,
        'newey_west_lags': NW_LAGS, 'verdict_alpha': verdict, 'by_config': summary, 'per_window': rows,
        'live_forward_test': live,
    }, indent=2, default=float))
    pd.DataFrame(rows).to_csv(out.with_suffix('.csv'), index=False)
    print(f"\nVerdict (pre-registered rule): {'ALPHA' if verdict else 'no alpha'}")
    if live:
        print(f"Live, {live['from_candle'][:16]} -> {live['to_candle'][:16]}: forward test "
              f"{live['forward_test_return_pct']:+.2f}% vs equal-weight index {live['equal_weight_index_return_pct']:+.2f}%")
    print(f"Saved {out}")


if __name__ == '__main__':
    main()
