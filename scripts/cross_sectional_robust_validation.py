"""
Cross-Sectional Reversal, Brought Up to the Same Standard as Everything Else
================================================================================
The only prior study of this strategy (2026-07-25) used 4 windows on 1
year of data, a single configuration, predating the two-config-agreement
standard and the 3-year data discovery this project later adopted for
every other strategy. This reruns it properly: configs A (9 windows) and
B (15 windows) on 3 years of data, both required to agree, on both the
full 19-stock Hong Kong universe and the 15-stock US universe.

Structural note: unlike the other three strategies, cross-sectional
reversal ranks the WHOLE stock universe against itself in a single
backtest, it does not produce one result per symbol. So this study
produces exactly 2 results (one per market), not dozens of pairs.

Caveat this script cannot fix: cross-sectional strategies are typically
evaluated on universes of hundreds to thousands of stocks in the
literature. 15-19 stocks is a small basket to rank, and that alone adds
noise independent of whether a real reversal effect exists. A null
result here is weaker evidence than the same null result was for the
other three strategies, which don't depend on universe breadth.

Usage:
    python3 scripts/cross_sectional_robust_validation.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from core.optimizer import walk_forward

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


def run_market(market_name, symbols, storage, start, end):
    print(f"\n=== {market_name}: {len(symbols)} stocks as one basket ===")
    per_config = {}
    for cfg in CONFIGS:
        t0 = time.time()
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
        print(f"  {cfg['name']}: OOS {per_config[cfg['name']]['oos_return']:+.2f}% | "
              f"consistency {per_config[cfg['name']]['consistency']:.0f}% | "
              f"{time.time()-t0:.0f}s", flush=True)

    robust = all(qualifies(per_config[c['name']]['oos_return'], per_config[c['name']]['consistency'])
                 for c in CONFIGS)
    return {
        'market': market_name,
        'n_stocks': len(symbols),
        'config_a_oos': per_config[CONFIGS[0]['name']]['oos_return'],
        'config_a_consistency': per_config[CONFIGS[0]['name']]['consistency'],
        'config_b_oos': per_config[CONFIGS[1]['name']]['oos_return'],
        'config_b_consistency': per_config[CONFIGS[1]['name']]['consistency'],
        'robust': robust,
    }


def main():
    storage = DataStorage()
    config = ConfigLoader()
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    rows.append(run_market('HK', config.get_live_symbols(market="HK"), storage, start, end))
    rows.append(run_market('US', config.get_all_symbols(market="US"), storage, start, end))

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'cross_sectional_robust_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
