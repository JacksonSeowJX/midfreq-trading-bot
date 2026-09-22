"""
Walk-Forward Efficiency (Pardo, 2008) across this project's key studies.

The dual-configuration standard used throughout gives a binary verdict:
a result either passes or it does not. It says nothing about HOW MUCH a
strategy degrades once it leaves the data its parameters were fitted to,
and that degradation is the direct signature of overfitting.

Walk-forward efficiency is out-of-sample performance expressed as a
fraction of in-sample performance. Pardo treats efficiency below roughly
50% as a warning sign. Note that train and test segments differ in
length, so both are converted to return-per-day before the ratio is
taken (see core/optimizer.py).

Reading the output:
  > 50%    out-of-sample holds up reasonably against training
  0 to 50% real degradation; the edge is substantially fitted
  negative  sign reversal: profitable in training, losing out-of-sample

Usage:
    python3 scripts/overfitting_efficiency.py
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
from datetime import timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.optimizer import walk_forward
from core.config import ConfigLoader
from backfill_sp100 import SP100_TICKERS
from backfill_hsi import HSI_CODES

CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0
US_RATE = 0.00005          # moomoo SG published US schedule, ~0.005%/side
HK_RATE = Portfolio.HK_FEE_RATE
DATA = Path(__file__).resolve().parent.parent / 'data'


def have(sym):
    return (DATA / sym.replace('.', '_') / '1h.parquet').exists()


def sp100():
    return [s for s in (f"US.{t.replace('.', '')}" for t in SP100_TICKERS) if have(s)]


def hsi():
    return [s for s in (f"HK.{c}" for c in HSI_CODES) if have(s)]


# The cross-sectional basket studies, then single-stock comparators so the
# validated result can be read against strategies known to have failed.
JOBS = [
    ('S&P 100 reversal',      'Cross-Sectional Reversal', sp100, US_RATE),
    ('S&P 100 momentum',      'Cross-Sectional Momentum', sp100, US_RATE),
    ('Hang Seng reversal',    'Cross-Sectional Reversal', hsi,   HK_RATE),
    ('HK.00700 regime switch', 'Regime Switch',           lambda: ['HK.00700'], HK_RATE),
    ('HK.00005 ML classifier', 'ML Direction Classifier', lambda: ['HK.00005'], HK_RATE),
]


def main():
    storage = DataStorage()
    rows = []
    t0 = time.time()

    for label, strat, symfn, rate in JOBS:
        symbols = symfn()
        if not symbols:
            print(f"skip {label}: no data"); continue
        end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value)
        start = end - timedelta(days=HISTORY_DAYS)
        for cname, n in CONFIGS:
            t = time.time()
            res = walk_forward(
                strategy_name=strat, symbols=symbols, timeframe=Timeframe.HOUR_1,
                start_date=start, end_date=end, storage=storage,
                n_splits=n, train_pct=0.7, objective='sharpe_ratio',
                slippage_bps=SLIPPAGE_BPS, commission_rate=rate,
            )
            s = res.get('summary', {})
            eff = s.get('wf_efficiency')
            rows.append({
                'study': label, 'config': cname, 'n_stocks': len(symbols),
                'train_pct_per_day': round(s.get('train_return_per_day', 0.0), 6),
                'oos_pct_per_day': round(s.get('oos_return_per_day', 0.0), 6),
                'avg_train_return': round(s.get('avg_train_return', 0.0), 3),
                'avg_oos_return': round(s.get('avg_oos_return', 0.0), 3),
                'consistency': round(s.get('consistency_pct', 0.0), 1),
                'wf_efficiency_pct': None if eff is None else round(eff * 100, 1),
            })
            e = rows[-1]['wf_efficiency_pct']
            print(f"{label:24s} | {cname:16s} | train {rows[-1]['avg_train_return']:+7.2f}% "
                  f"| OOS {rows[-1]['avg_oos_return']:+7.2f}% | efficiency "
                  f"{'n/a' if e is None else f'{e:+7.1f}%'} | {time.time()-t:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'overfitting_efficiency_{stamp}.csv'
    df.to_csv(out, index=False)
    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df[['study', 'config', 'avg_train_return', 'avg_oos_return',
              'consistency', 'wf_efficiency_pct']].to_string(index=False))


if __name__ == "__main__":
    main()
