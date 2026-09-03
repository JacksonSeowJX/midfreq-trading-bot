"""
Cross-Sectional Reversal on the S&P 100 (98 stocks with usable data)
=========================================================================
Same two-config robust standard as cross_sectional_robust_validation.py,
but on a much larger, real-index basket instead of the 15-stock ad hoc
US universe, to test whether the small-basket-noise caveat was actually
hiding something. BRK.B is dropped (ticker mapping issue, and the
account's 100-distinct-symbol historical-data quota was exhausted before
it could be re-fetched) — 98 of the 101 S&P 100 constituents are used.

Usage:
    python3 scripts/cross_sectional_sp100.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.optimizer import walk_forward
from backfill_sp100 import SP100_TICKERS

CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def main():
    storage = DataStorage()

    symbols = []
    for t in SP100_TICKERS:
        sym = f"US.{t.replace('.', '')}"
        folder = sym.replace('.', '_')
        if (Path(__file__).resolve().parent.parent / 'data' / folder / '1h.parquet').exists():
            symbols.append(sym)
    print(f"Using {len(symbols)}/{len(SP100_TICKERS)} S&P 100 symbols with local data\n")

    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    per_config = {}
    t0 = time.time()
    for cfg in CONFIGS:
        t_cfg = time.time()
        res = walk_forward(
            strategy_name='Cross-Sectional Reversal', symbols=symbols, timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=cfg['n_splits'], train_pct=cfg['train_pct'], objective=OBJECTIVE,
            slippage_bps=SLIPPAGE_BPS,
        )
        s = res.get('summary', {})
        per_config[cfg['name']] = {
            'oos_return': round(s.get('avg_oos_return', 0.0), 3),
            'consistency': round(s.get('consistency_pct', 0.0), 1),
            'n_windows': s.get('total_windows', 0),
        }
        print(f"{cfg['name']}: OOS {per_config[cfg['name']]['oos_return']:+.2f}% | "
              f"consistency {per_config[cfg['name']]['consistency']:.0f}% | "
              f"{time.time()-t_cfg:.0f}s", flush=True)

    robust = all(qualifies(per_config[c['name']]['oos_return'], per_config[c['name']]['consistency'])
                 for c in CONFIGS)
    row = {
        'market': 'US (S&P 100)', 'n_stocks': len(symbols),
        'config_a_oos': per_config[CONFIGS[0]['name']]['oos_return'],
        'config_a_consistency': per_config[CONFIGS[0]['name']]['consistency'],
        'config_b_oos': per_config[CONFIGS[1]['name']]['oos_return'],
        'config_b_consistency': per_config[CONFIGS[1]['name']]['consistency'],
        'robust': robust,
    }
    df = pd.DataFrame([row])
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'cross_sectional_sp100_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
