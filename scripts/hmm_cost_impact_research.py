"""
HMM Regime Switch: How Much of the Negative Return Is Fees?
==============================================================
Every walk-forward number reported for HMM Regime Switch so far already
includes the calibrated HK cost model (0.16%/side commission + 5bps
slippage). That leaves an open question: is the strategy losing because
there's no real signal, or because a real (if small) signal is being
eaten alive by round-trip costs?

This script runs the SAME walk-forward windows (3yr history, 9 splits,
all 19 live symbols) twice per symbol: once "net" with the real cost
model, and once "gross" with commission and slippage set to zero. The
gap between the two is the cost drag; whatever is left in the gross
number is the raw, cost-free signal.

Usage:
    python3 scripts/hmm_cost_impact_research.py
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
N_SPLITS = 9
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
HISTORY_DAYS = 3 * 365
NET_SLIPPAGE_BPS = 5.0
NET_COMMISSION = Portfolio.HK_FEE_RATE


def avg_trades_per_window(windows):
    if not windows:
        return 0.0
    return sum(w.test_metrics.get('total_trades', 0) for w in windows) / len(windows)


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

        net = walk_forward(
            strategy_name=STRATEGY, symbols=[sym], timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
            slippage_bps=NET_SLIPPAGE_BPS, commission_rate=NET_COMMISSION,
        )
        gross = walk_forward(
            strategy_name=STRATEGY, symbols=[sym], timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
            slippage_bps=0.0, commission_rate=0.0,
        )

        net_s = net.get('summary', {})
        gross_s = gross.get('summary', {})
        trades = avg_trades_per_window(net.get('windows', []))

        row = {
            'symbol': sym,
            'net_oos_mean': round(net_s.get('avg_oos_return', 0.0), 3),
            'net_consistency': round(net_s.get('consistency_pct', 0.0), 1),
            'gross_oos_mean': round(gross_s.get('avg_oos_return', 0.0), 3),
            'gross_consistency': round(gross_s.get('consistency_pct', 0.0), 1),
            'avg_trades_per_window': round(trades, 1),
        }
        row['cost_drag'] = round(row['gross_oos_mean'] - row['net_oos_mean'], 3)
        rows.append(row)

        print(f"{sym} | net {row['net_oos_mean']:+.2f}% (cons {row['net_consistency']:.0f}%) | "
              f"gross {row['gross_oos_mean']:+.2f}% (cons {row['gross_consistency']:.0f}%) | "
              f"drag {row['cost_drag']:+.2f}% | ~{row['avg_trades_per_window']:.1f} trades/window | "
              f"{time.time()-t_sym:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'hmm_cost_impact_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))
    print(f"\nMean OOS — net: {df['net_oos_mean'].mean():+.3f}%  "
          f"gross: {df['gross_oos_mean'].mean():+.3f}%  "
          f"mean cost drag: {df['cost_drag'].mean():+.3f}%")
    n_gross_positive = (df['gross_oos_mean'] > 0).sum()
    n_net_positive = (df['net_oos_mean'] > 0).sum()
    print(f"Symbols with positive mean OOS — gross: {n_gross_positive}/{len(df)}  "
          f"net: {n_net_positive}/{len(df)}")


if __name__ == "__main__":
    main()
