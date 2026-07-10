"""
Pairs Trading Research: Cointegration Scan + Walk-Forward Validation
====================================================================
Stage 1 — scan all symbol pairs from the HK universe:
    * Engle-Granger cointegration test on log prices (general relationship)
    * ADF stationarity test on the unit log-ratio spread log(A/B) — this is
      the spread the PairsTradingStrategy actually trades (hedge ratio 1),
      so it is the decision-relevant test
    * price correlation (sanity metric)

Stage 2 — walk-forward validate the strategy on the best pairs (lowest
ADF p-value), with real costs + slippage. Only pairs that pass BOTH the
statistical test and out-of-sample validation earn forward-test status.

Outputs:
    results/pairs_scan_<stamp>.csv
    results/pairs_walkforward_<stamp>.csv

Usage:
    python3 scripts/pairs_research.py            # 1h, 1 year, top 5 pairs
"""
import sys
import time
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from statsmodels.tsa.stattools import adfuller, coint

from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from core.optimizer import walk_forward

TIMEFRAME = Timeframe.HOUR_1
HISTORY_DAYS = 365
TOP_N_VALIDATE = 5
SLIPPAGE_BPS = 5.0


def load_aligned_closes(storage, symbols):
    """Load 1h closes for all symbols, aligned on common timestamps."""
    series = {}
    for sym in symbols:
        df = storage.load_data(sym.replace('.', '_'), TIMEFRAME.value)
        if not df.empty:
            series[sym] = df['close']
    aligned = pd.DataFrame(series).dropna()
    return aligned


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    closes = load_aligned_closes(storage, symbols)
    print(f"Aligned dataset: {len(closes)} common 1h candles across {len(closes.columns)} symbols\n")

    # ── Stage 1: statistical scan of all pairs ─────────────────────
    rows = []
    pairs = list(combinations(closes.columns, 2))
    for a, b in pairs:
        log_a, log_b = np.log(closes[a]), np.log(closes[b])
        spread = log_a - log_b

        adf_p = adfuller(spread, autolag='AIC')[1]
        eg_p = coint(log_a, log_b)[1]
        corr = closes[a].corr(closes[b])

        rows.append({'pair': f"{a}/{b}", 'symbol_a': a, 'symbol_b': b,
                     'adf_p_unit_spread': round(adf_p, 4),
                     'engle_granger_p': round(eg_p, 4),
                     'correlation': round(corr, 3)})

    scan = pd.DataFrame(rows).sort_values('adf_p_unit_spread').reset_index(drop=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    results_dir.mkdir(exist_ok=True)
    scan_path = results_dir / f'pairs_scan_{stamp}.csv'
    scan.to_csv(scan_path, index=False)

    stationary = scan[scan['adf_p_unit_spread'] < 0.05]
    print(f"Scanned {len(pairs)} pairs — {len(stationary)} have a stationary "
          f"unit-ratio spread (ADF p < 0.05)")
    print(scan.head(10).to_string(index=False))
    print(f"\nScan saved: {scan_path}\n")

    # ── Stage 2: walk-forward validate the best pairs ──────────────
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)
    wf_rows = []

    candidates = scan.head(TOP_N_VALIDATE)
    for _, row in candidates.iterrows():
        a, b = row['symbol_a'], row['symbol_b']
        t0 = time.time()
        res = walk_forward(
            strategy_name='Pairs Trading', symbols=[a, b], timeframe=TIMEFRAME,
            start_date=start, end_date=end, storage=storage,
            n_splits=4, train_pct=0.7, objective='sharpe_ratio',
            slippage_bps=SLIPPAGE_BPS,
        )
        s = res.get('summary', {})
        if not s or s.get('error'):
            print(f"{a}/{b}: skipped ({s.get('error', 'no result')})")
            continue
        wf_rows.append({
            'pair': f"{a}/{b}",
            'adf_p_unit_spread': row['adf_p_unit_spread'],
            'avg_train_return_pct': round(s['avg_train_return'], 3),
            'avg_oos_return_pct': round(s['avg_oos_return'], 3),
            'oos_consistency_pct': round(s['consistency_pct'], 1),
            'generalizes': s['avg_oos_return'] > 0 and s['consistency_pct'] >= 50,
        })
        print(f"{a}/{b}: ADF p={row['adf_p_unit_spread']:.3f} | "
              f"train {s['avg_train_return']:+.2f}% | OOS {s['avg_oos_return']:+.2f}% | "
              f"consistency {s['consistency_pct']:.0f}% | {time.time()-t0:.0f}s")

    if wf_rows:
        wf = pd.DataFrame(wf_rows)
        wf_path = results_dir / f'pairs_walkforward_{stamp}.csv'
        wf.to_csv(wf_path, index=False)
        print(f"\nWalk-forward saved: {wf_path}")
        survivors = wf[wf['generalizes']]
        print(f"{len(survivors)}/{len(wf)} validated pairs generalize out-of-sample"
              + (":" if len(survivors) else "."))
        for _, r in survivors.iterrows():
            print(f"  -> {r['pair']} (OOS {r['avg_oos_return_pct']:+.2f}%/window)")


if __name__ == "__main__":
    main()
