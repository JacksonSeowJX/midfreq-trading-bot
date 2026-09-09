"""
Per-Window Detail for the Cross-Sectional Near-Miss
=========================================================================
The S&P 100 result (+1.28% mean OOS at only 33% consistency, Config A)
is the closest anything in this project has come to validating. The
aggregate numbers alone can't distinguish between "a real but uneven
edge" and "one or two outlier windows dragging the mean positive".

This re-runs the same S&P 100 and Hang Seng Index studies but saves the
PER-WINDOW out-of-sample returns that walk_forward already computes,
so the shape of the result can be inspected and charted directly.
No new methodology, just keeping detail the earlier scripts discarded.

Usage:
    python3 scripts/cross_sectional_window_detail.py
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
from backfill_sp100 import SP100_TICKERS
from backfill_hsi import HSI_CODES

CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
OBJECTIVE = 'sharpe_ratio'
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def symbols_for(market):
    out = []
    if market == 'sp100':
        for t in SP100_TICKERS:
            sym = f"US.{t.replace('.', '')}"
            if (DATA_DIR / sym.replace('.', '_') / '1h.parquet').exists():
                out.append(sym)
    else:
        for code in HSI_CODES:
            sym = f"HK.{code}"
            if (DATA_DIR / sym.replace('.', '_') / '1h.parquet').exists():
                out.append(sym)
    return out


def main():
    storage = DataStorage()
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    detail = {}
    t0 = time.time()
    for market in ('sp100', 'hsi'):
        symbols = symbols_for(market)
        detail[market] = {'n_stocks': len(symbols), 'configs': {}}
        print(f"\n=== {market}: {len(symbols)} symbols ===", flush=True)
        for cfg in CONFIGS:
            t_cfg = time.time()
            res = walk_forward(
                strategy_name='Cross-Sectional Reversal', symbols=symbols,
                timeframe=Timeframe.HOUR_1, start_date=start, end_date=end,
                storage=storage, n_splits=cfg['n_splits'], train_pct=cfg['train_pct'],
                objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS,
            )
            s = res.get('summary', {})
            detail[market]['configs'][cfg['name']] = {
                'oos_returns': [round(x, 4) for x in s.get('oos_returns', [])],
                'train_returns': [round(x, 4) for x in s.get('train_returns', [])],
                'avg_oos_return': round(s.get('avg_oos_return', 0.0), 3),
                'consistency_pct': round(s.get('consistency_pct', 0.0), 1),
            }
            print(f"{cfg['name']}: OOS {s.get('avg_oos_return', 0):+.2f}% | "
                  f"consistency {s.get('consistency_pct', 0):.0f}% | "
                  f"windows {s.get('oos_returns', [])} | {time.time()-t_cfg:.0f}s", flush=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'cross_sectional_window_detail_{stamp}.json'
    out.write_text(json.dumps(detail, indent=2))
    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}")


if __name__ == "__main__":
    main()
