"""
Per-Symbol Best-Validated-Tool Allocation — Robust Version
=============================================================
Direct fix for the instability found on 2026-07-25: a single walk-forward
configuration is not reliable evidence. Tencent's Regime Switch result
was +0.635%/window under one window configuration (n_splits=4, evaluated
2026-07-16) and -0.68%/window under another (n_splits=3, evaluated
2026-07-25) — same strategy, same symbol, same costs, just different
window boundaries. With only 3-5 non-overlapping windows per symbol,
that kind of sign flip is a real sampling-variance risk, not noise to
shrug off.

This version requires a candidate to CLEAR THE BAR (avg OOS > 0 AND
consistency >= 50%) under TWO INDEPENDENTLY CONFIGURED studies —
different n_splits, so the window boundaries are genuinely different —
before it is trusted enough to assign. A result that only looks good
under one specific window slicing is exactly the fragile case this is
designed to catch and reject.

Usage:
    python3 scripts/best_tool_allocation_robust.py
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
    {'name': 'A (3 windows)', 'n_splits': 3, 'train_pct': 0.7},
    {'name': 'B (5 windows)', 'n_splits': 5, 'train_pct': 0.7},
]
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=365)

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
                    continue
                per_config[cfg['name']] = {
                    'oos_return': round(s['avg_oos_return'], 3),
                    'consistency': round(s['consistency_pct'], 1),
                }
                print(f"{sym} | {strat:24s} | {cfg['name']:14s} | "
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
    full_path = results_dir / f'best_tool_robust_full_{stamp}.csv'
    df.to_csv(full_path, index=False)

    # ─── Allocation: among ROBUST candidates only, pick the one with the
    # best average OOS return across both configs ───
    allocation = {}
    alloc_rows = []
    for sym in symbols:
        sub = df[(df.symbol == sym) & (df.robust)]
        if sub.empty:
            allocation[sym] = None
            alloc_rows.append({'symbol': sym, 'assigned': 'NONE (stand aside)',
                               'avg_oos_pct': None})
        else:
            sub = sub.copy()
            sub['avg_oos'] = (sub['config_a_oos'] + sub['config_b_oos']) / 2
            best = sub.loc[sub['avg_oos'].idxmax()]
            allocation[sym] = best['strategy']
            alloc_rows.append({'symbol': sym, 'assigned': best['strategy'],
                               'avg_oos_pct': round(best['avg_oos'], 3)})

    alloc_df = pd.DataFrame(alloc_rows)
    alloc_path = results_dir / f'best_tool_robust_allocation_{stamp}.csv'
    alloc_df.to_csv(alloc_path, index=False)

    json_path = Path(__file__).resolve().parent.parent / 'config' / 'best_tool_allocation.json'
    json_path.write_text(json.dumps({
        'generated_at': str(datetime.now()),
        'method': 'robust — requires agreement across 2 independent walk-forward configs',
        'configs': CONFIGS,
        'min_consistency_pct': MIN_CONSISTENCY,
        'allocation': allocation,
    }, indent=2))

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")
    print(f"Full comparison: {full_path}")
    print(f"Allocation table: {alloc_path}")
    print(f"Machine-readable: {json_path}\n")
    print(alloc_df.to_string(index=False))

    assigned = sum(1 for v in allocation.values() if v is not None)
    n_robust_total = df['robust'].sum()
    print(f"\n{n_robust_total}/{len(df)} (symbol, strategy) pairs were robust across both configs")
    print(f"{assigned}/{len(symbols)} symbols got an assigned tool; "
          f"{len(symbols)-assigned} stand aside (no result survives both configurations)")


if __name__ == "__main__":
    main()
