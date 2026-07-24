"""
Cross-Sectional Reversal Research
==================================
Walk-forward validation of the cross-sectional (relative) mean-reversion
strategy on the whole 11-stock HK universe at once — unlike every other
study in this project, there is ONE backtest here, not one per symbol,
because the strategy needs the whole universe simultaneously to rank
stocks against each other.

A hand-tuned smoke test showed heavy over-trading at every-candle
rebalancing (1,424 trades/year, -5.0 Sharpe) that improves monotonically
as rebalancing slows down. Rather than hand-picking a frequency that
looks good in-sample, this script lets grid search find the best
lookback/top_n/rebalance_every combination on the TRAIN portion of each
walk-forward window, then reports how that choice performed on the
UNSEEN test portion — the same discipline applied to every other
strategy in this project.

Usage:
    python3 scripts/cross_sectional_research.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from core.optimizer import walk_forward

N_SPLITS = 4
TRAIN_PCT = 0.7
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=365)

    print(f"Universe: {len(symbols)} symbols, 1h candles, {N_SPLITS} walk-forward windows\n")

    t0 = time.time()
    res = walk_forward(
        strategy_name='Cross-Sectional Reversal', symbols=symbols, timeframe=Timeframe.HOUR_1,
        start_date=start, end_date=end, storage=storage,
        n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
        slippage_bps=SLIPPAGE_BPS,
        progress_callback=lambda pct, msg: print(f"  {msg}", flush=True),
    )

    windows = res.get('windows', [])
    summary = res.get('summary', {})

    if not windows:
        print(f"No result: {summary.get('error', 'unknown')}")
        return

    print(f"\nDone in {(time.time()-t0)/60:.1f} min\n")
    for w in windows:
        print(f"Window {w.window_id}: best_params={w.best_params} | "
              f"train {w.train_metrics.get('return_pct', 0):+.2f}% "
              f"(trades={w.train_metrics.get('total_trades', 0)}) | "
              f"OOS {w.test_metrics.get('return_pct', 0):+.2f}% "
              f"(trades={w.test_metrics.get('total_trades', 0)})")

    print(f"\nAvg train return: {summary['avg_train_return']:+.2f}%")
    print(f"Avg OOS return:    {summary['avg_oos_return']:+.2f}%")
    print(f"OOS consistency:   {summary['consistency_pct']:.0f}%")
    verdict = "GENERALIZES" if summary['avg_oos_return'] > 0 and summary['consistency_pct'] >= 50 else "DOES NOT GENERALIZE"
    print(f"\nVerdict: {verdict}")


if __name__ == "__main__":
    main()
