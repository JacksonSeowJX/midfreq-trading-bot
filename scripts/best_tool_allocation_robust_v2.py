"""
Per-Symbol Best-Validated-Tool Allocation — Robust v2 (3 Years of Data)
==========================================================================
Direct follow-up to the 2026-07-31 null result (0/33 pairs survived
requiring agreement across two walk-forward configurations). The
suspected cause was too few independent windows: only 3-5 non-
overlapping windows per symbol over 1 year of 1h data.

On 2026-08-02 we found Moomoo actually provides 3 years of 1h history,
not the 1 year originally pulled — tripling the available data. This
script re-runs the SAME robustness test (agreement across two
differently-configured walk-forward studies) on the full 3-year window,
using n_splits=9 and n_splits=15 — chosen so each window is
approximately the SAME SIZE as the original 1-year study's windows
(~121 days and ~73 days respectively), just three times as many of
them. This isolates the variable being tested: does MORE independent
evidence (not different window sizing) change the verdict?

Usage:
    python3 scripts/best_tool_allocation_robust_v2.py
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
from core.config import ConfigLoader
from core.optimizer import walk_forward

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
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

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
                    slippage_bps=SLIPPAGE_BPS,
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
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    full_path = results_dir / f'best_tool_robust_v2_full_{stamp}.csv'
    df.to_csv(full_path, index=False)

    allocation = {}
    alloc_rows = []
    for sym in symbols:
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
    alloc_path = results_dir / f'best_tool_robust_v2_allocation_{stamp}.csv'
    alloc_df.to_csv(alloc_path, index=False)

    json_path = Path(__file__).resolve().parent.parent / 'config' / 'best_tool_allocation.json'
    json_path.write_text(json.dumps({
        'generated_at': str(datetime.now()),
        'method': 'robust v2 — 3yr history, agreement across 9-window and 15-window configs',
        'history_days': HISTORY_DAYS,
        'configs': CONFIGS,
        'min_consistency_pct': MIN_CONSISTENCY,
        'allocation': allocation,
    }, indent=2))

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")
    print(f"Full comparison: {full_path}")
    print(f"Allocation table: {alloc_path}\n")
    print(alloc_df.to_string(index=False))

    assigned = sum(1 for v in allocation.values() if v is not None)
    n_robust_total = df['robust'].sum()
    print(f"\n{n_robust_total}/{len(df)} (symbol, strategy) pairs were robust across both configs")
    print(f"{assigned}/{len(symbols)} symbols got an assigned tool; "
          f"{len(symbols)-assigned} stand aside")


if __name__ == "__main__":
    main()
