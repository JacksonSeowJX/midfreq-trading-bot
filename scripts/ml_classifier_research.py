"""
ML Direction Classifier Research: Walk-Forward Validation vs the Field
========================================================================
Walk-forward validates the logistic-regression direction classifier
across the full HK universe, then places it alongside every other
strategy family studied so far (single-indicator rules, regime
switching — rule and HMM, cross-sectional reversal) for a single
head-to-head comparison table.

Usage:
    python3 scripts/ml_classifier_research.py
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

N_SPLITS = 3  # longer windows — the classifier needs ~120 candles of history
              # to warm up before it will trade at all; 4 splits left too
              # little runway inside each test window (same fix as the HMM study)
TRAIN_PCT = 0.7
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=365)

    rows = []
    t0 = time.time()
    for sym in symbols:
        t_sym = time.time()
        res = walk_forward(
            strategy_name='ML Direction Classifier', symbols=[sym], timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
            slippage_bps=SLIPPAGE_BPS,
        )
        s = res.get('summary', {})
        if not s or s.get('error'):
            print(f"{sym}: skipped ({s.get('error', 'no result')})")
            continue
        last_w = res['windows'][-1] if res['windows'] else None
        rows.append({
            'symbol': sym,
            'avg_train_return_pct': round(s['avg_train_return'], 3),
            'avg_oos_return_pct': round(s['avg_oos_return'], 3),
            'oos_consistency_pct': round(s['consistency_pct'], 1),
            'generalizes': s['avg_oos_return'] > 0 and s['consistency_pct'] >= 50,
            'latest_params': str(last_w.best_params) if last_w else '',
        })
        print(f"{sym}: train {s['avg_train_return']:+6.2f}% | OOS {s['avg_oos_return']:+6.2f}% | "
              f"consistency {s['consistency_pct']:3.0f}% | {time.time()-t_sym:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'ml_classifier_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.sort_values('avg_oos_return_pct', ascending=False).to_string(index=False))

    survivors = df[df['generalizes']]
    print(f"\n{len(survivors)}/{len(df)} symbols generalize out-of-sample")
    print(f"Mean OOS return: {df['avg_oos_return_pct'].mean():+.3f}%")
    print(f"Mean consistency: {df['oos_consistency_pct'].mean():.0f}%")


if __name__ == "__main__":
    main()
