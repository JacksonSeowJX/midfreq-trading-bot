"""
Cross-Sectional Momentum — Robust Validation
==================================================================
Mirrors cross_sectional_robust_validation.py exactly, but for the
opposite bet: Cross-Sectional Momentum buys today's relative LEADERS
(the top `top_n` recent performers in the universe) instead of the
laggards, betting outperformance continues rather than reverts.

Same two-config standard (9-window / 15-window, 3yr, 5bps slippage) on
the existing 19-stock HK and 15-stock US universes. Like reversal, this
produces one result per market (cross-sectional strategies rank the
whole universe in a single backtest, not one result per symbol).

Usage:
    python3 scripts/cross_sectional_momentum_robust.py
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

STRATEGY = 'Cross-Sectional Momentum'
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


def run_market(market, storage, config):
    symbols = config.get_live_symbols(market=market) or config.get_all_symbols(market=market)
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    per_config = {}
    for cfg in CONFIGS:
        t_cfg = time.time()
        res = walk_forward(
            strategy_name=STRATEGY, symbols=symbols, timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=cfg['n_splits'], train_pct=cfg['train_pct'], objective=OBJECTIVE,
            slippage_bps=SLIPPAGE_BPS,
        )
        s = res.get('summary', {})
        per_config[cfg['name']] = {
            'oos_return': round(s.get('avg_oos_return', 0.0), 3),
            'consistency': round(s.get('consistency_pct', 0.0), 1),
        }
        print(f"{market} | {cfg['name']:16s} | OOS {per_config[cfg['name']]['oos_return']:+6.2f}% | "
              f"consistency {per_config[cfg['name']]['consistency']:3.0f}% | {time.time()-t_cfg:.0f}s", flush=True)

    robust = all(qualifies(per_config[c['name']]['oos_return'], per_config[c['name']]['consistency'])
                 for c in CONFIGS)
    return {
        'market': market, 'n_stocks': len(symbols),
        'config_a_oos': per_config[CONFIGS[0]['name']]['oos_return'],
        'config_a_consistency': per_config[CONFIGS[0]['name']]['consistency'],
        'config_b_oos': per_config[CONFIGS[1]['name']]['oos_return'],
        'config_b_consistency': per_config[CONFIGS[1]['name']]['consistency'],
        'robust': robust,
    }


def main():
    storage = DataStorage()
    config = ConfigLoader()

    t0 = time.time()
    rows = [run_market('HK', storage, config), run_market('US', storage, config)]

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'cross_sectional_momentum_robust_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
