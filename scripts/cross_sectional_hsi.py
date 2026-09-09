"""
Cross-Sectional Reversal on the Hang Seng Index (86 new + 2 pre-existing
= 88 stocks with local data)
=========================================================================
Same two-config robust standard as cross_sectional_sp100.py, but for the
Hong Kong side: replaces the 19-stock ad hoc HK universe with the real
Hang Seng Index constituent list (see backfill_hsi.py), to test whether
the S&P 100 near-miss (both configs agreeing on direction, failing only
on consistency) was a US-specific effect or something that shows up on
a comparably broad Hong Kong universe too.

Usage:
    python3 scripts/cross_sectional_hsi.py
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
from backfill_hsi import HSI_CODES

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
    for code in HSI_CODES:
        sym = f"HK.{code}"
        folder = sym.replace('.', '_')
        if (Path(__file__).resolve().parent.parent / 'data' / folder / '1h.parquet').exists():
            symbols.append(sym)
    print(f"Using {len(symbols)}/{len(HSI_CODES)} Hang Seng Index symbols with local data\n")

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
        'market': 'HK (Hang Seng Index)', 'n_stocks': len(symbols),
        'config_a_oos': per_config[CONFIGS[0]['name']]['oos_return'],
        'config_a_consistency': per_config[CONFIGS[0]['name']]['consistency'],
        'config_b_oos': per_config[CONFIGS[1]['name']]['oos_return'],
        'config_b_consistency': per_config[CONFIGS[1]['name']]['consistency'],
        'robust': robust,
    }
    df = pd.DataFrame([row])
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'cross_sectional_hsi_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
