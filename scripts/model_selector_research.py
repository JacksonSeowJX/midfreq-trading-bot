"""
Adaptive Model Selector: HMM vs Rule, Chosen Per-Symbol Per-Window
===================================================================
Motivation: the HMM v2 study (2026-07-16) showed the learned model beats
the hand-tuned Efficiency Ratio rule ON AVERAGE (6/11 symbols, mean OOS
+0.047% vs -0.087%) but LOSES on the rule's two best symbols (Tencent,
Baidu). Neither tool wins everywhere. This script tests whether picking
the better one PER WINDOW, using only training-window information (no
lookahead), beats committing to either one alone.

Method: run walk_forward() once for 'Regime Switch' (the rule) and once
for 'HMM Regime Switch' (the model), with IDENTICAL symbols/dates/splits
so window boundaries line up exactly (window computation is pure date
math, independent of strategy — verified in optimizer.py). For each
window, compare the two strategies' TRAINING performance only, pick the
winner, and record THAT winner's out-of-sample test result as the
selector's score for that window. This is a legitimate walk-forward
selection — the choice is made without ever looking at test data.

Reports the selector's OOS performance against "always use the rule"
and "always use the model" baselines, per symbol and in aggregate.

Usage:
    python3 scripts/model_selector_research.py
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
            print(f"{sym}: skipped (mismatched or missing windows)")
            continue

        selector_oos, rule_oos, hmm_oos = [], [], []
        for rw, hw in zip(rule_windows, hmm_windows):
            rule_train = rw.train_metrics.get(OBJECTIVE, 0.0)
            hmm_train = hw.train_metrics.get(OBJECTIVE, 0.0)
            picked = 'HMM' if hmm_train > rule_train else 'Rule'
            picked_test_return = (hw if picked == 'HMM' else rw).test_metrics.get('return_pct', 0.0)

            selector_oos.append(picked_test_return)
            rule_oos.append(rw.test_metrics.get('return_pct', 0.0))
            hmm_oos.append(hw.test_metrics.get('return_pct', 0.0))

            window_rows.append({
                'symbol': sym, 'window': rw.window_id,
                'rule_train_sharpe': round(rule_train, 3), 'hmm_train_sharpe': round(hmm_train, 3),
                'picked': picked,
                'rule_oos_return': round(rw.test_metrics.get('return_pct', 0.0), 3),
                'hmm_oos_return': round(hw.test_metrics.get('return_pct', 0.0), 3),
                'selector_oos_return': round(picked_test_return, 3),
            })

        avg_selector = sum(selector_oos) / len(selector_oos)
        avg_rule = sum(rule_oos) / len(rule_oos)
        avg_hmm = sum(hmm_oos) / len(hmm_oos)
        summary_rows.append({
            'symbol': sym, 'windows': len(selector_oos),
            'always_rule_oos': round(avg_rule, 3),
            'always_hmm_oos': round(avg_hmm, 3),
            'selector_oos': round(avg_selector, 3),
            'selector_beats_both': avg_selector > max(avg_rule, avg_hmm),
        })
        print(f"{sym}: rule {avg_rule:+.3f}% | hmm {avg_hmm:+.3f}% | "
              f"selector {avg_selector:+.3f}% | {time.time()-t_sym:.0f}s", flush=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    wf_path = results_dir / f'model_selector_windows_{stamp}.csv'
    sm_path = results_dir / f'model_selector_summary_{stamp}.csv'
    pd.DataFrame(window_rows).to_csv(wf_path, index=False)
    df = pd.DataFrame(summary_rows)
    df.to_csv(sm_path, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min")
    print(df.sort_values('selector_oos', ascending=False).to_string(index=False))

    print(f"\nAlways-rule mean OOS:   {df['always_rule_oos'].mean():+.3f}%")
    print(f"Always-HMM mean OOS:    {df['always_hmm_oos'].mean():+.3f}%")
    print(f"Selector mean OOS:      {df['selector_oos'].mean():+.3f}%")
    print(f"Selector beats BOTH baselines on {df['selector_beats_both'].sum()}/{len(df)} symbols")
    print(f"\nSaved: {wf_path}\nSaved: {sm_path}")


if __name__ == "__main__":
    main()
