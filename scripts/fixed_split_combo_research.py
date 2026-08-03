"""
Fixed-Split Combination: Rule + HMM, 50/50, No Picking
=========================================================
The adaptive selector (pick the better-looking tool per window using
training performance) made things WORSE than either baseline alone
(2026-07-24) — the training signal didn't predict the real winner.

This tests a different, much simpler idea: don't pick at all. Run BOTH
the rule and HMM simultaneously, each with half the capital, every
window, unconditionally. The window-by-window blended return is just
the plain average of the two strategies' own OOS returns for that
window (equivalent to two independent half-size portfolios summed).

Question: does never having to guess which tool will win make the
combined result MORE robust (smoother, more consistently positive)
than either tool alone — even if it can never beat the better of the
two in any single window?

Usage:
    python3 scripts/fixed_split_combo_research.py
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

N_SPLITS = 9   # matches the robust v2 study's window size (~121 days) on 3yr data
TRAIN_PCT = 0.7
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'
HISTORY_DAYS = 3 * 365


def main():
    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_live_symbols(market="HK")

    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    window_rows = []
    summary_rows = []
    t0 = time.time()

    for sym in symbols:
        t_sym = time.time()
        rule_res = walk_forward(
            strategy_name='Regime Switch', symbols=[sym], timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
            slippage_bps=SLIPPAGE_BPS,
        )
        hmm_res = walk_forward(
            strategy_name='HMM Regime Switch', symbols=[sym], timeframe=Timeframe.HOUR_1,
            start_date=start, end_date=end, storage=storage,
            n_splits=N_SPLITS, train_pct=TRAIN_PCT, objective=OBJECTIVE,
            slippage_bps=SLIPPAGE_BPS,
        )
        rule_windows = rule_res.get('windows', [])
        hmm_windows = hmm_res.get('windows', [])

        if not rule_windows or not hmm_windows or len(rule_windows) != len(hmm_windows):
            print(f"{sym}: skipped (mismatched windows)")
            continue

        blended_returns, rule_returns, hmm_returns = [], [], []
        for rw, hw in zip(rule_windows, hmm_windows):
            r_ret = rw.test_metrics.get('return_pct', 0.0)
            h_ret = hw.test_metrics.get('return_pct', 0.0)
            blended = 0.5 * r_ret + 0.5 * h_ret
            blended_returns.append(blended)
            rule_returns.append(r_ret)
            hmm_returns.append(h_ret)
            window_rows.append({
                'symbol': sym, 'window': rw.window_id,
                'rule_oos_return': round(r_ret, 3), 'hmm_oos_return': round(h_ret, 3),
                'blended_oos_return': round(blended, 3),
            })

        n = len(blended_returns)
        summary_rows.append({
            'symbol': sym, 'windows': n,
            'always_rule_mean': round(sum(rule_returns) / n, 3),
            'always_rule_consistency': round(100 * sum(1 for r in rule_returns if r > 0) / n, 1),
            'always_hmm_mean': round(sum(hmm_returns) / n, 3),
            'always_hmm_consistency': round(100 * sum(1 for r in hmm_returns if r > 0) / n, 1),
            'blended_mean': round(sum(blended_returns) / n, 3),
            'blended_consistency': round(100 * sum(1 for r in blended_returns if r > 0) / n, 1),
        })
        print(f"{sym} | rule {sum(rule_returns)/n:+6.2f}% | hmm {sum(hmm_returns)/n:+6.2f}% | "
              f"blend {sum(blended_returns)/n:+6.2f}% | {time.time()-t_sym:.0f}s", flush=True)

    wdf = pd.DataFrame(window_rows)
    sdf = pd.DataFrame(summary_rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    wdf.to_csv(results_dir / f'fixed_split_combo_windows_{stamp}.csv', index=False)
    sdf.to_csv(results_dir / f'fixed_split_combo_summary_{stamp}.csv', index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min\n")
    print(sdf.to_string(index=False))

    print(f"\nMean OOS return  — rule: {sdf['always_rule_mean'].mean():+.3f}%  "
          f"hmm: {sdf['always_hmm_mean'].mean():+.3f}%  blend: {sdf['blended_mean'].mean():+.3f}%")
    print(f"Mean consistency — rule: {sdf['always_rule_consistency'].mean():.0f}%  "
          f"hmm: {sdf['always_hmm_consistency'].mean():.0f}%  blend: {sdf['blended_consistency'].mean():.0f}%")

    blend_more_consistent = (sdf['blended_consistency'] > sdf[['always_rule_consistency', 'always_hmm_consistency']].max(axis=1)).sum()
    print(f"\nBlend beats BOTH individual consistencies on {blend_more_consistent}/{len(sdf)} symbols")


if __name__ == "__main__":
    main()
