"""
Buy-and-Hold Benchmark: What Would Doing Nothing Have Returned?
===================================================================
Every strategy tested in this project (rule-based regime switch, HMM
regime switch, ML classifier, cross-sectional reversal, and the two
combination attempts) failed the same robust validation standard: OOS
return positive AND >=50% consistent, agreeing across two independently
-configured walk-forward studies (9-window and 15-window, same 3yr
history). That's a well-supported null result, but it's been standing
alone — nothing so far says what a symbol did on its own over those
same test windows.

This script computes plain buy-and-hold return (first close to last
close, no trading, no costs) over the EXACT SAME test windows used
throughout this project, for the same 19 symbols and both configs, so
the null result has a concrete anchor: did these stocks even go up
during the periods our strategies were tested on?

Usage:
    python3 scripts/buy_hold_benchmark.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.storage import DataStorage
from core.config import ConfigLoader

CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
HISTORY_DAYS = 3 * 365


def window_bounds(start_date, end_date, n_splits, train_pct):
    total_days = (end_date - start_date).days
    window_size = total_days // n_splits
    bounds = []
    for w in range(n_splits):
        w_start = start_date + timedelta(days=w * window_size)
        w_end = w_start + timedelta(days=window_size)
        train_days = int(window_size * train_pct)
        test_start = w_start + timedelta(days=train_days)
        test_end = w_end
        bounds.append((test_start, test_end))
    return bounds


def buy_hold_return(df, test_start, test_end):
    ts_start = pd.Timestamp(test_start, tz='UTC')
    ts_end = pd.Timestamp(test_end, tz='UTC')
    window = df.loc[(df.index >= ts_start) & (df.index <= ts_end)]
    if len(window) < 2:
        return None
    start_price = window['close'].iloc[0]
    end_price = window['close'].iloc[-1]
    return (end_price - start_price) / start_price * 100


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    for sym in symbols:
        df = storage.load_data(sym, '1h')
        if df.empty:
            print(f"{sym} | no data, skipping")
            continue

        row = {'symbol': sym}
        for cfg in CONFIGS:
            bounds = window_bounds(start, end, cfg['n_splits'], cfg['train_pct'])
            returns = [r for r in (buy_hold_return(df, ts, te) for ts, te in bounds) if r is not None]
            n = len(returns)
            avg = sum(returns) / n if n else 0.0
            consistency = 100 * sum(1 for r in returns if r > 0) / n if n else 0.0
            key = 'a' if cfg is CONFIGS[0] else 'b'
            row[f'config_{key}_oos'] = round(avg, 3)
            row[f'config_{key}_consistency'] = round(consistency, 1)
        rows.append(row)
        print(f"{sym} | A {row['config_a_oos']:+.2f}%/{row['config_a_consistency']:.0f}% | "
              f"B {row['config_b_oos']:+.2f}%/{row['config_b_consistency']:.0f}%")

    df_out = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'buy_hold_benchmark_{stamp}.csv'
    df_out.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df_out.to_string(index=False))
    print(f"\nMean buy-and-hold OOS — config A: {df_out['config_a_oos'].mean():+.3f}%  "
          f"config B: {df_out['config_b_oos'].mean():+.3f}%")
    print(f"Mean consistency — config A: {df_out['config_a_consistency'].mean():.1f}%  "
          f"config B: {df_out['config_b_consistency'].mean():.1f}%")


if __name__ == "__main__":
    main()
