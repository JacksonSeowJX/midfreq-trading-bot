"""
Does the Null Result Hold on US Large Caps Too?
===================================================
Every strategy tested on the HK universe failed the robust validation
standard (agreement across a 9-window and a 15-window walk-forward
config, both on 3yr of 1h data). This runs the exact same test — same
strategies, same configs, same methodology — on 15 US large caps across
tech, finance, energy, healthcare, consumer, and industrials, to check
whether the null result is specific to this HK universe or holds more
broadly. US market data confirmed free (no paid subscription) as of
2026-08-11.

Re-run on 19 Oct 2026 on the corrected validation machinery (Section 3.6.3 of the report): the
window is anchored to the data (DataStorage.latest_common_timestamp) instead of the run date,
and US runs pass their commission explicitly (zero, the best-case bound of the corrected US
studies), so a US run is no longer charged the Hong Kong rate.

Usage:
    uv run python research/studies/us_robust_validation.py
"""
import time
import json

import pandas as pd
from datetime import datetime, timedelta

from midfreq.core import paths
from midfreq.core.candle import Timeframe
from midfreq.data.storage import DataStorage
from midfreq.core.config import ConfigLoader
from midfreq.backtest.optimizer import walk_forward

CANDIDATES = ['Regime Switch', 'HMM Regime Switch', 'ML Direction Classifier']
CONFIGS = [
    {'name': 'A (9 windows, ~121d each)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows, ~73d each)', 'n_splits': 15, 'train_pct': 0.7},
]
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_all_symbols(market="US")

    end = storage.latest_common_timestamp(symbols, '1h')    # the data's end, not the run date
    start = end - timedelta(days=HISTORY_DAYS)
    print(f"Window anchored to data: {start.date()} -> {end.date()}", flush=True)

    rows = []
    t0 = time.time()
    for sym in symbols:
        for strat in CANDIDATES:
            per_config = {}
            for cfg in CONFIGS:
                t_job = time.time()
                res = walk_forward(
                    strategy_name=strat, symbols=[sym], timeframe=Timeframe.HOUR_1,
                    start_date=start, end_date=end, storage=storage,
                    n_splits=cfg['n_splits'], train_pct=cfg['train_pct'], objective=OBJECTIVE,
                    slippage_bps=SLIPPAGE_BPS, commission_rate=0.0,   # US: best-case bound, not the HK rate
                )
                s = res.get('summary', {})
                if not s or s.get('error'):
                    per_config[cfg['name']] = None
                    print(f"{sym} | {strat:24s} | {cfg['name']:26s} | "
                          f"skipped ({s.get('error', 'no result')})", flush=True)
                    continue
                per_config[cfg['name']] = {
                    'oos_return': round(s['avg_oos_return'], 3),
                    'consistency': round(s['consistency_pct'], 1),
                    'n_windows': s['total_windows'],
                }
                print(f"{sym} | {strat:24s} | {cfg['name']:26s} | "
                      f"OOS {s['avg_oos_return']:+6.2f}% | consistency {s['consistency_pct']:3.0f}% | "
                      f"windows {s['total_windows']:2d} | {time.time()-t_job:.0f}s", flush=True)

            both_qualify = all(
                per_config.get(cfg['name']) is not None and
                qualifies(per_config[cfg['name']]['oos_return'], per_config[cfg['name']]['consistency'])
                for cfg in CONFIGS
            )
            rows.append({
                'symbol': sym, 'strategy': strat,
                'config_a_oos': per_config.get(CONFIGS[0]['name'], {}).get('oos_return') if per_config.get(CONFIGS[0]['name']) else None,
                'config_a_consistency': per_config.get(CONFIGS[0]['name'], {}).get('consistency') if per_config.get(CONFIGS[0]['name']) else None,
                'config_b_oos': per_config.get(CONFIGS[1]['name'], {}).get('oos_return') if per_config.get(CONFIGS[1]['name']) else None,
                'config_b_consistency': per_config.get(CONFIGS[1]['name'], {}).get('consistency') if per_config.get(CONFIGS[1]['name']) else None,
                'robust': both_qualify,
            })

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_dir = paths.results_dir()
    full_path = results_dir / f'us_robust_validation_full_{stamp}.csv'
    df.to_csv(full_path, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {full_path}\n")
    n_robust_total = df['robust'].sum()
    print(f"{n_robust_total}/{len(df)} (symbol, strategy) pairs were robust across both configs")

    for s in CANDIDATES:
        sub = df[df.strategy == s]
        sub_avg = (sub['config_a_oos'] + sub['config_b_oos']) / 2
        print(f"{s:28s}: mean {sub_avg.mean():+.3f}%  ({(sub_avg>0).sum()}/{len(sub)} symbols positive)  "
              f"robust: {sub['robust'].sum()}/{len(sub)}")


if __name__ == "__main__":
    main()
