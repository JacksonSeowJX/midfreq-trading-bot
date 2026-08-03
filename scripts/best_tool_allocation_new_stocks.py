"""
Per-Symbol Best-Validated-Tool Allocation — New Sectors (2026-08-04)
=========================================================================
Same robustness methodology as best_tool_allocation_robust_v2.py
(3 years of 1h data, agreement required across a 9-window and a
15-window walk-forward configuration), applied to the 8 stocks added
2026-08-03 to test sectors absent from the original 11 (banking,
energy, real estate, gaming, exchange infrastructure, consumer).

Question: does the null result on the original 11 (0/33 pairs survive
two-configuration agreement, confirmed twice) generalize to a different
part of the HK market, or is there a sector-specific edge the original
universe just didn't contain?

Usage:
    python3 scripts/best_tool_allocation_new_stocks.py
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.optimizer import walk_forward

NEW_SYMBOLS = ['HK.01398', 'HK.00939', 'HK.00883', 'HK.00016',
               'HK.00027', 'HK.00388', 'HK.02020', 'HK.09999']
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
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    for sym in NEW_SYMBOLS:
        for strat in CANDIDATES:
            per_config = {}
            for cfg in CONFIGS:
                t_job = time.time()
                res = walk_forward(
                    strategy_name=strat, symbols=[sym], timeframe=Timeframe.HOUR_1,
                    start_date=start, end_date=end, storage=storage,
                    n_splits=cfg['n_splits'], train_pct=cfg['train_pct'], objective=OBJECTIVE,
                    slippage_bps=SLIPPAGE_BPS,
                )
                s = res.get('summary', {})
                if not s or s.get('error'):
                    per_config[cfg['name']] = None
                    print(f"{sym} | {strat:24s} | {cfg['name']:26s} | skipped", flush=True)
                    continue
                per_config[cfg['name']] = {
                    'oos_return': round(s['avg_oos_return'], 3),
                    'consistency': round(s['consistency_pct'], 1),
                }
                print(f"{sym} | {strat:24s} | {cfg['name']:26s} | "
                      f"OOS {s['avg_oos_return']:+6.2f}% | consistency {s['consistency_pct']:3.0f}% | "
                      f"{time.time()-t_job:.0f}s", flush=True)

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
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    full_path = results_dir / f'best_tool_new_stocks_full_{stamp}.csv'
    df.to_csv(full_path, index=False)

    allocation = {}
    alloc_rows = []
    for sym in NEW_SYMBOLS:
        sub = df[(df.symbol == sym) & (df.robust)]
        if sub.empty:
            allocation[sym] = None
            alloc_rows.append({'symbol': sym, 'assigned': 'NONE (stand aside)', 'avg_oos_pct': None})
        else:
            sub = sub.copy()
            sub['avg_oos'] = (sub['config_a_oos'] + sub['config_b_oos']) / 2
            best = sub.loc[sub['avg_oos'].idxmax()]
            allocation[sym] = best['strategy']
            alloc_rows.append({'symbol': sym, 'assigned': best['strategy'],
                               'avg_oos_pct': round(best['avg_oos'], 3)})

    alloc_df = pd.DataFrame(alloc_rows)
    alloc_path = results_dir / f'best_tool_new_stocks_allocation_{stamp}.csv'
    alloc_df.to_csv(alloc_path, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")
    print(f"Full comparison: {full_path}")
    print(f"Allocation table: {alloc_path}\n")
    print(alloc_df.to_string(index=False))

    assigned = sum(1 for v in allocation.values() if v is not None)
    n_robust_total = df['robust'].sum()
    print(f"\n{n_robust_total}/{len(df)} (symbol, strategy) pairs were robust across both configs")
    print(f"{assigned}/{len(NEW_SYMBOLS)} new symbols got an assigned tool; "
          f"{len(NEW_SYMBOLS)-assigned} stand aside")


if __name__ == "__main__":
    main()
