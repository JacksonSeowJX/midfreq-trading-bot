"""
HMM Regime Detection Research
=============================
Head-to-head: HMM-driven regime switching vs the Efficiency-Ratio rule,
walk-forward validated (real costs + slippage) on 1h data across the
full HK universe.

Question: does a learned regime model (Gaussian HMM on returns, states
mapped to trading styles by their fitted drift/volatility) beat a
hand-tuned threshold rule out-of-sample?

Outputs:
    results/hmm_regime_<stamp>.csv     (per symbol, both strategies)

Usage:
    python3 scripts/hmm_regime_research.py
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

STRATEGIES = ['HMM Regime Switch', 'Regime Switch']
SLIPPAGE_BPS = 5.0


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=365)

    rows = []
    t0 = time.time()
    for sym in symbols:
        for strat in STRATEGIES:
            t_job = time.time()
            res = walk_forward(
                strategy_name=strat, symbols=[sym], timeframe=Timeframe.HOUR_1,
                start_date=start, end_date=end, storage=storage,
                n_splits=3, train_pct=0.7, objective='sharpe_ratio',
                slippage_bps=SLIPPAGE_BPS,
            )
            s = res.get('summary', {})
            if not s or s.get('error'):
                continue
            last_w = res['windows'][-1] if res['windows'] else None
            rows.append({
                'symbol': sym, 'strategy': strat,
                'avg_train_return_pct': round(s['avg_train_return'], 3),
                'avg_oos_return_pct': round(s['avg_oos_return'], 3),
                'oos_consistency_pct': round(s['consistency_pct'], 1),
                'latest_params': str(last_w.best_params) if last_w else '',
            })
            print(f"{sym} | {strat:20s} | train {s['avg_train_return']:+6.2f}% | "
                  f"OOS {s['avg_oos_return']:+6.2f}% | consistency {s['consistency_pct']:3.0f}% "
                  f"| {time.time()-t_job:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'hmm_regime_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")

    # Head-to-head table
    pivot = df.pivot_table(values='avg_oos_return_pct', index='symbol',
                           columns='strategy')
    pivot['hmm_advantage'] = pivot['HMM Regime Switch'] - pivot['Regime Switch']
    print(pivot.sort_values('hmm_advantage', ascending=False).round(3).to_string())

    hmm = df[df.strategy == 'HMM Regime Switch']
    er = df[df.strategy == 'Regime Switch']
    print(f"\nHMM:  positive OOS on {sum(hmm.avg_oos_return_pct > 0)}/{len(hmm)} symbols, "
          f"mean OOS {hmm.avg_oos_return_pct.mean():+.3f}%")
    print(f"ER:   positive OOS on {sum(er.avg_oos_return_pct > 0)}/{len(er)} symbols, "
          f"mean OOS {er.avg_oos_return_pct.mean():+.3f}%")
    wins = sum(pivot['hmm_advantage'] > 0)
    print(f"HMM beats ER on {wins}/{len(pivot)} symbols")


if __name__ == "__main__":
    main()
