"""
Cost Sensitivity of the S&P 100 Cross-Sectional Reversal Result
=========================================================================
cross_sectional_corrected.py found S&P 100 reversal passing the
dual-configuration standard at zero commission, which is a best case,
not a tradeable claim. This re-runs that exact study at four commission
rates with everything else held fixed, to show how much of the result
survives trading costs:

  0%       best case, no fees at all
  0.005%   moomoo SG's published US schedule: US$0.99 per order plus
           pass-through fees, which is roughly 0.002%-0.006% per side
           at the US$25k-100k positions this strategy trades, taken at
           the conservative end
  0.03%    the placeholder used before that schedule was checked
  0.16%    the Hong Kong rate (Portfolio.HK_FEE_RATE), as a pessimistic
           upper bound

This is the source of results/cross_sectional_cost_sensitivity_*.csv,
cited in Section 4.9 of the report and in Update 12. Those figures were
computed when the S&P 100 cache ended on 21 Aug 2026 (window 2023-08-22
16:00 -> 2026-08-21 16:00, 100 symbols). The cache has been topped up
since, so to reproduce them:

    python3 scripts/research/cross_sectional_cost_sensitivity.py --end-date 2026-08-21

Checked on 5 Oct 2026: that reproduces the report's CSV byte for byte,
and every per-window return matches the logs of the original runs.

Without --end-date the window ends at the newest candle that every
symbol has. The 8 walk-forwards (4 rates x 2 configurations) are
independent and take about 12 minutes each, so they run in parallel.

Usage:
    python3 scripts/research/cross_sectional_cost_sensitivity.py [--end-date YYYY-MM-DD]
        [--jobs N] [--out PATH]
"""
import os
import sys
import time
import json
import argparse
import contextlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))

import pandas as pd

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.optimizer import walk_forward
from core.config import ConfigLoader

STRATEGY = 'Cross-Sectional Reversal'
RATES = [
    (0.0,                  '0%\n(best case)'),
    (0.00005,              '0.005%\n(moomoo SG actual)'),
    (0.0003,               '0.03%\n(old placeholder)'),
    (Portfolio.HK_FEE_RATE, '0.16%\n(HK rate, pessimistic)'),
]
CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
SLIPPAGE_BPS = 5.0
HISTORY_DAYS = 3 * 365
MIN_CONSISTENCY = 50.0
MAX_STALENESS_DAYS = 3


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def sp100_symbols(storage, end_date=None):
    """S&P 100 symbols with cached candles, minus any that stop short of the end.

    latest_common_timestamp takes the MINIMUM end across symbols, so one
    symbol that missed a refresh would drag the whole window back with it.
    """
    syms = ConfigLoader().get_universe('sp100', with_data_only=True)
    last = {s: storage.latest_common_timestamp([s], Timeframe.HOUR_1.value, as_of=end_date)
            for s in syms}
    last = {s: d for s, d in last.items() if d is not None}
    freshest = max(last.values())
    keep = [s for s in syms if s in last and (freshest - last[s]).days <= MAX_STALENESS_DAYS]
    return keep, sorted(set(syms) - set(keep))


def run_one(task):
    """One walk-forward. Runs in a worker process, so it opens its own storage."""
    rate, n_splits, symbols, start, end = task
    t = time.time()
    with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet):
        res = walk_forward(strategy_name=STRATEGY, symbols=symbols, timeframe=Timeframe.HOUR_1,
                           start_date=start, end_date=end, storage=DataStorage(str(REPO / 'data')),
                           n_splits=n_splits, train_pct=TRAIN_PCT, objective=OBJECTIVE,
                           slippage_bps=SLIPPAGE_BPS, commission_rate=rate)
    s = res['summary']
    return {'rate': rate, 'n_splits': n_splits, 'oos': s['avg_oos_return'],
            'consistency': s['consistency_pct'], 'oos_returns': s['oos_returns'],
            'windows_used': s.get('total_windows', 0), 'seconds': round(time.time() - t)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--end-date', default=None,
                    help="re-run the study as it stood when the cache ended on this day "
                         "(YYYY-MM-DD); 2026-08-21 reproduces the report's figures")
    ap.add_argument('--jobs', type=int, default=4,
                    help='walk-forwards to run at once, of 8 (default 4)')
    ap.add_argument('--out', default=None,
                    help='CSV to write (default results/cross_sectional_cost_sensitivity_<stamp>.csv); '
                         'per-window detail goes next to it as <name>_detail.json')
    args = ap.parse_args()

    storage = DataStorage(str(REPO / 'data'))
    symbols, dropped = sp100_symbols(storage, args.end_date)
    end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value, as_of=args.end_date)
    start = end - timedelta(days=HISTORY_DAYS)
    print(f"S&P 100 {STRATEGY} | {len(symbols)} symbols | {start} -> {end}"
          + (f" | dropped as stale: {', '.join(dropped)}" if dropped else ''), flush=True)

    tasks = [(rate, n, symbols, start, end) for rate, _ in RATES for _, n in CONFIGS]
    done = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for fut in as_completed([pool.submit(run_one, t) for t in tasks]):
            r = fut.result()
            done[(r['rate'], r['n_splits'])] = r
            print(f"  {r['rate'] * 100:.3f}%/side | {r['n_splits']:2d} windows | OOS {r['oos']:+7.3f}% | "
                  f"consistency {r['consistency']:5.1f}% | {r['seconds']}s", flush=True)

    rows, runs = [], {}
    for rate, label in RATES:
        a, b = done[(rate, CONFIGS[0][1])], done[(rate, CONFIGS[1][1])]
        rows.append({
            'commission_per_side': rate, 'label': label,
            'config_a_oos': round(a['oos'], 3), 'config_a_consistency': round(a['consistency'], 1),
            'config_b_oos': round(b['oos'], 3), 'config_b_consistency': round(b['consistency'], 1),
            'robust': qualifies(a['oos'], a['consistency']) and qualifies(b['oos'], b['consistency']),
        })
        for (cname, _), r in zip(CONFIGS, (a, b)):
            runs[f"{rate} | {cname}"] = {k: r[k] for k in ('oos', 'consistency', 'oos_returns', 'windows_used')}

    out = Path(args.out) if args.out else (
        REPO / 'results' / f"cross_sectional_cost_sensitivity_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    df = pd.DataFrame(rows)
    df.to_csv(out, index=False)
    detail = out.with_name(f"{out.stem}_detail.json")
    detail.write_text(json.dumps({
        'strategy': STRATEGY, 'universe': 'sp100', 'n_symbols': len(symbols), 'dropped': dropped,
        'start': str(start), 'end': str(end), 'end_date_arg': args.end_date,
        'train_pct': TRAIN_PCT, 'objective': OBJECTIVE, 'slippage_bps': SLIPPAGE_BPS,
        'runs': runs,
    }, indent=2))

    print(f"\nDone in {(time.time() - t0) / 60:.1f} min — saved {out}\n")
    view = df.assign(label=df['label'].str.replace('\n', ' '))
    print(view[['label', 'config_a_oos', 'config_a_consistency', 'config_b_oos',
                'config_b_consistency', 'robust']].to_string(index=False))


if __name__ == '__main__':
    main()
