"""
HMM Regime Switch: Does a Real (Cost-Free) Edge Survive the Robust Test?
==========================================================================
The cost-impact study (2026-08-04) found HMM Regime Switch's out-of-sample
return is close to flat before trading costs (+0.011%/window gross vs
-0.045%/window net, 11/19 symbols gross-positive vs 4/19 net-positive).
That's suggestive but not validated — a single walk-forward configuration
can look promising by chance, which is exactly why every other finding in
this project has required agreement across two independently-configured
walk-forward studies before counting as real (see
best_tool_allocation_robust_v2.py).

This script applies that same discipline to the gross (cost-free) number:
for every symbol, run BOTH configs (9-window and 15-window, same 3yr
history) under both the real cost model and zero cost, using the exact
same window boundaries for the net/gross pair within each config so the
only variable that changes is cost. A symbol only counts as a validated
gross edge if it qualifies (positive OOS return, >=50% consistency) in
BOTH configs.

Usage:
    python3 scripts/hmm_gross_robust_validation.py
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
from core.portfolio import Portfolio
from core.optimizer import walk_forward

STRATEGY = 'HMM Regime Switch'
CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
NET_SLIPPAGE_BPS = 5.0
NET_COMMISSION = Portfolio.HK_FEE_RATE


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def run_one(sym, storage, start, end, cfg, gross):
    res = walk_forward(
        strategy_name=STRATEGY, symbols=[sym], timeframe=Timeframe.HOUR_1,
        start_date=start, end_date=end, storage=storage,
        n_splits=cfg['n_splits'], train_pct=cfg['train_pct'], objective=OBJECTIVE,
        slippage_bps=0.0 if gross else NET_SLIPPAGE_BPS,
        commission_rate=0.0 if gross else NET_COMMISSION,
    )
    s = res.get('summary', {})
    if not s or s.get('error'):
        return None
    return {'oos_return': round(s['avg_oos_return'], 3), 'consistency': round(s['consistency_pct'], 1)}


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    for sym in symbols:
        t_sym = time.time()
        net_per_cfg, gross_per_cfg = {}, {}
        for cfg in CONFIGS:
            net_per_cfg[cfg['name']] = run_one(sym, storage, start, end, cfg, gross=False)
            gross_per_cfg[cfg['name']] = run_one(sym, storage, start, end, cfg, gross=True)

        net_robust = all(
            net_per_cfg.get(cfg['name']) is not None and
            qualifies(net_per_cfg[cfg['name']]['oos_return'], net_per_cfg[cfg['name']]['consistency'])
            for cfg in CONFIGS
        )
        gross_robust = all(
            gross_per_cfg.get(cfg['name']) is not None and
            qualifies(gross_per_cfg[cfg['name']]['oos_return'], gross_per_cfg[cfg['name']]['consistency'])
            for cfg in CONFIGS
        )

        row = {
            'symbol': sym,
            'net_a_oos': net_per_cfg[CONFIGS[0]['name']]['oos_return'] if net_per_cfg[CONFIGS[0]['name']] else None,
            'net_a_cons': net_per_cfg[CONFIGS[0]['name']]['consistency'] if net_per_cfg[CONFIGS[0]['name']] else None,
            'net_b_oos': net_per_cfg[CONFIGS[1]['name']]['oos_return'] if net_per_cfg[CONFIGS[1]['name']] else None,
            'net_b_cons': net_per_cfg[CONFIGS[1]['name']]['consistency'] if net_per_cfg[CONFIGS[1]['name']] else None,
            'net_robust': net_robust,
            'gross_a_oos': gross_per_cfg[CONFIGS[0]['name']]['oos_return'] if gross_per_cfg[CONFIGS[0]['name']] else None,
            'gross_a_cons': gross_per_cfg[CONFIGS[0]['name']]['consistency'] if gross_per_cfg[CONFIGS[0]['name']] else None,
            'gross_b_oos': gross_per_cfg[CONFIGS[1]['name']]['oos_return'] if gross_per_cfg[CONFIGS[1]['name']] else None,
            'gross_b_cons': gross_per_cfg[CONFIGS[1]['name']]['consistency'] if gross_per_cfg[CONFIGS[1]['name']] else None,
            'gross_robust': gross_robust,
        }
        rows.append(row)
        print(f"{sym} | net A {row['net_a_oos']:+.2f}%/{row['net_a_cons']:.0f}% B {row['net_b_oos']:+.2f}%/{row['net_b_cons']:.0f}% "
              f"robust={net_robust} | gross A {row['gross_a_oos']:+.2f}%/{row['gross_a_cons']:.0f}% "
              f"B {row['gross_b_oos']:+.2f}%/{row['gross_b_cons']:.0f}% robust={gross_robust} | "
              f"{time.time()-t_sym:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'hmm_gross_robust_validation_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))
    print(f"\nNet robust: {df['net_robust'].sum()}/{len(df)}")
    print(f"Gross robust: {df['gross_robust'].sum()}/{len(df)}")
    if df['gross_robust'].any():
        print("\nGross-robust symbols:")
        print(df[df['gross_robust']][['symbol', 'gross_a_oos', 'gross_a_cons', 'gross_b_oos', 'gross_b_cons']].to_string(index=False))


if __name__ == "__main__":
    main()
