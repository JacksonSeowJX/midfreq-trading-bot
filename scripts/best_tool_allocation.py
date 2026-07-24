"""
Per-Symbol Best-Validated-Tool Allocation
==========================================
Builds a STATIC allocation table: for each symbol, which strategy (if
any) has the strongest walk-forward out-of-sample track record?

This is NOT the same thing as the adaptive model-selector experiment
(2026-07-24), which picked a tool per-WINDOW using in-sample training
performance and made things WORSE, because training performance didn't
predict which tool would actually win out-of-sample. This script picks
per-SYMBOL, once, using the walk-forward OOS record ITSELF — the actual
evidence of what worked, not a proxy that was proven unreliable. It's a
portfolio-construction decision made after the evidence exists, not a
live prediction of which model is about to win.

All three candidate strategies are run with IDENTICAL settings (same
n_splits, train_pct, date range, objective, costs) so the comparison is
fair — mixing numbers from the separately-configured studies run on
different days would not be.

A symbol gets NO assignment ("stand aside") if no candidate both beats
zero AND clears 50% OOS consistency — a real, honest outcome for a
stock with no currently-validated edge, not a failure of the script.

Usage:
    python3 scripts/best_tool_allocation.py
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
N_SPLITS = 3
TRAIN_PCT = 0.7
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0


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
            t_job = time.time()
            res = walk_forward(
                strategy_name=strat, symbols=[sym], timeframe=Timeframe.HOUR_1,
                start_date=start, end_date=end, storage=storage,
                n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
                slippage_bps=SLIPPAGE_BPS,
            )
            s = res.get('summary', {})
            if not s or s.get('error'):
                continue
            rows.append({
                'symbol': sym, 'strategy': strat,
                'avg_oos_return_pct': round(s['avg_oos_return'], 3),
                'oos_consistency_pct': round(s['consistency_pct'], 1),
            })
            print(f"{sym} | {strat:24s} | OOS {s['avg_oos_return']:+6.2f}% | "
                  f"consistency {s['consistency_pct']:3.0f}% | {time.time()-t_job:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    full_path = results_dir / f'best_tool_full_{stamp}.csv'
    df.to_csv(full_path, index=False)

    # ─── Build the allocation: best qualifying candidate per symbol ───
    allocation = {}
    alloc_rows = []
    for sym in symbols:
        sub = df[df.symbol == sym]
        qualified = sub[(sub.avg_oos_return_pct > 0) & (sub.oos_consistency_pct >= MIN_CONSISTENCY)]
        if qualified.empty:
            allocation[sym] = None
            alloc_rows.append({'symbol': sym, 'assigned': 'NONE (stand aside)',
                               'oos_return_pct': None, 'consistency_pct': None})
        else:
            best = qualified.loc[qualified.avg_oos_return_pct.idxmax()]
            allocation[sym] = best['strategy']
            alloc_rows.append({'symbol': sym, 'assigned': best['strategy'],
                               'oos_return_pct': best['avg_oos_return_pct'],
                               'consistency_pct': best['oos_consistency_pct']})

    alloc_df = pd.DataFrame(alloc_rows)
    alloc_path = results_dir / f'best_tool_allocation_{stamp}.csv'
    alloc_df.to_csv(alloc_path, index=False)

    json_path = Path(__file__).resolve().parent.parent / 'config' / 'best_tool_allocation.json'
    json_path.write_text(json.dumps({
        'generated_at': str(datetime.now()),
        'settings': {'n_splits': N_SPLITS, 'train_pct': TRAIN_PCT,
                    'slippage_bps': SLIPPAGE_BPS, 'objective': OBJECTIVE,
                    'min_consistency_pct': MIN_CONSISTENCY},
        'allocation': allocation,
    }, indent=2))

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")
    print(f"Full comparison: {full_path}")
    print(f"Allocation table: {alloc_path}")
    print(f"Machine-readable: {json_path}\n")
    print(alloc_df.to_string(index=False))

    assigned = sum(1 for v in allocation.values() if v is not None)
    print(f"\n{assigned}/{len(symbols)} symbols got an assigned tool; "
          f"{len(symbols)-assigned} stand aside (no validated edge yet)")


if __name__ == "__main__":
    main()
