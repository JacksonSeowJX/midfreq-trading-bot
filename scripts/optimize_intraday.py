"""
Intraday Optimization Research
==============================
Walk-forward optimization of every single-asset strategy on intraday
timeframes, using the calibrated HK fee model (0.16%/side) + slippage.

For each (strategy, timeframe): grid-search parameters on rolling train
windows, validate on unseen test windows, and report whether ANY
parameter region generalizes out-of-sample.

Outputs:
    results/walkforward_<timestamp>.csv   — one row per (strategy, tf, window)
    results/summary_<timestamp>.csv       — one row per (strategy, tf)

Usage:
    python3 scripts/optimize_intraday.py                 # HK.00700, 5m + 1h
    python3 scripts/optimize_intraday.py HK.00005        # another symbol
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

STRATEGIES = ['Z-Score Mean Reversion', 'RSI', 'Bollinger Bands',
              'SMA Crossover', 'MACD']

# (timeframe, history days, walk-forward splits)
PLANS = [
    (Timeframe.MIN_5, 182, 3),
    (Timeframe.HOUR_1, 365, 4),
]

SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'
TRAIN_PCT = 0.7


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'HK.00700'
    storage = DataStorage()
    end = datetime.now()

    results_dir = Path(__file__).resolve().parent.parent / 'results'
    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    window_rows = []
    summary_rows = []

    total = len(STRATEGIES) * len(PLANS)
    job = 0
    t0 = time.time()

    for timeframe, days, n_splits in PLANS:
        start = end - timedelta(days=days)
        for strat in STRATEGIES:
            job += 1
            t_job = time.time()
            print(f"[{job}/{total}] {strat} on {symbol} {timeframe.value} "
                  f"({days}d, {n_splits} windows)...", flush=True)

            res = walk_forward(
                strategy_name=strat, symbols=[symbol], timeframe=timeframe,
                start_date=start, end_date=end, storage=storage,
                n_splits=n_splits, train_pct=TRAIN_PCT, objective=OBJECTIVE,
                slippage_bps=SLIPPAGE_BPS,
            )
            s = res.get('summary', {})
            windows = res.get('windows', [])

            for w in windows:
                window_rows.append({
                    'strategy': strat, 'symbol': symbol, 'timeframe': timeframe.value,
                    'window': w.window_id,
                    'train_start': w.train_start.date(), 'test_start': w.test_start.date(),
                    'best_params': str(w.best_params),
                    'train_return_pct': round(w.train_metrics.get('return_pct', 0.0), 3),
                    'test_return_pct': round(w.test_metrics.get('return_pct', 0.0), 3),
                    'test_sharpe': round(w.test_metrics.get('sharpe_ratio', 0.0), 3),
                    'test_trades': w.test_metrics.get('total_trades', 0),
                    'test_max_dd': round(w.test_metrics.get('max_drawdown', 0.0), 3),
                })

            if s and not s.get('error'):
                summary_rows.append({
                    'strategy': strat, 'symbol': symbol, 'timeframe': timeframe.value,
                    'windows': s['total_windows'],
                    'avg_train_return_pct': round(s['avg_train_return'], 3),
                    'avg_oos_return_pct': round(s['avg_oos_return'], 3),
                    'oos_consistency_pct': round(s['consistency_pct'], 1),
                    'generalizes': s['avg_oos_return'] > 0 and s['consistency_pct'] >= 50,
                })
                print(f"    train {s['avg_train_return']:+.2f}% | "
                      f"OOS {s['avg_oos_return']:+.2f}% | "
                      f"consistency {s['consistency_pct']:.0f}% | "
                      f"{time.time() - t_job:.0f}s", flush=True)
            else:
                print(f"    skipped: {s.get('error', 'no result')}", flush=True)

    wf_path = results_dir / f'walkforward_{stamp}.csv'
    sm_path = results_dir / f'summary_{stamp}.csv'
    pd.DataFrame(window_rows).to_csv(wf_path, index=False)
    pd.DataFrame(summary_rows).to_csv(sm_path, index=False)

    print(f"\nDone in {(time.time() - t0) / 60:.1f} min")
    print(f"Windows: {wf_path}\nSummary: {sm_path}\n")

    if summary_rows:
        df = pd.DataFrame(summary_rows).sort_values('avg_oos_return_pct', ascending=False)
        print(df.to_string(index=False))
        survivors = df[df['generalizes']]
        print(f"\n{len(survivors)}/{len(df)} strategy/timeframe combos generalize out-of-sample"
              + (":" if len(survivors) else "."))
        for _, r in survivors.iterrows():
            print(f"  -> {r['strategy']} @ {r['timeframe']}")


if __name__ == "__main__":
    main()
