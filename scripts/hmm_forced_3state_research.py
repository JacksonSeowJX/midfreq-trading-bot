"""
Forced 3-State HMM: Is the 'Danger State' Being Starved of a Fair Chance?
============================================================================
Every HMM Regime Switch walk-forward study so far has let grid search
choose between n_states=2 and n_states=3 (a registry param). In every
study run to date, the search has picked 2 states as best in nearly
every window -- but that could mean either "3 states genuinely doesn't
help" or "3 states never wins a fair fight because it needs more data
per state, so it's structurally disadvantaged in the same grid search
as 2-state."

This script removes the choice: n_states is FORCED to 3 for every
window (not searched), across the same walk-forward window boundaries
used throughout this project (3yr history, 9 splits), and compared
directly against the existing 2-vs-3 search result for the same
symbols. A direct backtest sweep is used instead of walk_forward()'s
grid search, since forcing a single fixed param isn't natively
supported by that API.

Usage:
    python3 scripts/hmm_forced_3state_research.py
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
from core.backtester import Backtester
from core.strategy import STRATEGY_REGISTRY
from core.optimizer import walk_forward

N_SPLITS = 9
TRAIN_PCT = 0.7
SLIPPAGE_BPS = 5.0
HISTORY_DAYS = 3 * 365
ORIGINAL_11 = ['HK.00700', 'HK.00005', 'HK.09988', 'HK.03690', 'HK.01299',
               'HK.00941', 'HK.02318', 'HK.01211', 'HK.01810', 'HK.09618', 'HK.09888']


def run_forced_3state_window(symbol, storage, train_start, train_end, test_start, test_end):
    """Run n_states=3 (fixed) on train and test portions of one window."""
    info = STRATEGY_REGISTRY['HMM Regime Switch']
    params = {k: v['default'] for k, v in info['params'].items()}
    params['n_states'] = 3

    def run(start, end):
        p = Portfolio(initial_cash=100_000.0)
        bt = Backtester(storage=storage, portfolio=p, slippage_bps=SLIPPAGE_BPS)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                return bt.run(info['class'], symbols=[symbol], timeframe=Timeframe.HOUR_1,
                             start_date=start, end_date=end, **params)
            except Exception:
                return {}

    train_metrics = run(train_start, train_end)
    test_metrics = run(test_start, test_end)
    return train_metrics, test_metrics


def main():
    storage = DataStorage()
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    total_days = (end - start).days
    window_size = total_days // N_SPLITS

    rows = []
    t0 = time.time()
    for sym in ORIGINAL_11:
        t_sym = time.time()

        # Existing 2-vs-3 search result, for direct comparison
        search_res = walk_forward(
            strategy_name='HMM Regime Switch', symbols=[sym], timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective='sharpe_ratio',
            slippage_bps=SLIPPAGE_BPS,
        )
        search_summary = search_res.get('summary', {})

        # Forced 3-state sweep across the SAME window boundaries
        oos_returns = []
        n_states_3_picked_naturally = 0
        for w in range(N_SPLITS):
            w_start = start + timedelta(days=w * window_size)
            w_end = w_start + timedelta(days=window_size)
            train_days = int(window_size * TRAIN_PCT)
            train_start = w_start
            train_end = w_start + timedelta(days=train_days)
            test_start = train_end
            test_end = w_end

            _, test_metrics = run_forced_3state_window(sym, storage, train_start, train_end, test_start, test_end)
            oos_returns.append(test_metrics.get('return_pct', 0.0))

        n = len(oos_returns)
        forced_mean = sum(oos_returns) / n if n else 0.0
        forced_consistency = 100 * sum(1 for r in oos_returns if r > 0) / n if n else 0.0

        for w in search_res.get('windows', []):
            if w.best_params.get('n_states') == 3:
                n_states_3_picked_naturally += 1

        rows.append({
            'symbol': sym,
            'search_oos_mean': round(search_summary.get('avg_oos_return', 0.0), 3),
            'search_consistency': round(search_summary.get('consistency_pct', 0.0), 1),
            'n_states_3_picked_by_search': n_states_3_picked_naturally,
            'forced_3state_oos_mean': round(forced_mean, 3),
            'forced_3state_consistency': round(forced_consistency, 1),
        })
        print(f"{sym} | search (2v3): {search_summary.get('avg_oos_return', 0.0):+.2f}% "
              f"(3-state picked {n_states_3_picked_naturally}/{N_SPLITS} windows) | "
              f"forced 3-state: {forced_mean:+.2f}% cons {forced_consistency:.0f}% | "
              f"{time.time()-t_sym:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'hmm_forced_3state_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))
    print(f"\nMean OOS — 2-vs-3 search: {df['search_oos_mean'].mean():+.3f}%  "
          f"forced 3-state: {df['forced_3state_oos_mean'].mean():+.3f}%")
    print(f"Total windows where search picked 3-state naturally: "
          f"{df['n_states_3_picked_by_search'].sum()}/{N_SPLITS * len(ORIGINAL_11)}")


if __name__ == "__main__":
    main()
