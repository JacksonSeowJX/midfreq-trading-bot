"""
Combination Attempts, Unified: Rule Alone vs HMM Alone vs Adaptive
Selector vs Fixed 50/50 Blend — All From One Set of Windows
========================================================================
Slide 2 of Update 9 compares four numbers on one chart, but they used
to come from two separate scripts run on different days: Rule/HMM/Blend
from fixed_split_combo_research.py (9 windows, 3yr data), and Adaptive
Selector from an older model_selector_research.py run (4 windows, 1yr
data). Since walk_forward() computes windows as "N days back from right
now," even re-running the SAME config on a different day shifts every
window boundary. The only way to make all four numbers genuinely
comparable is to compute them together, in one script, sharing one
start/end and one set of window boundaries.

Usage:
    python3 scripts/combination_attempts_unified.py
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

N_SPLITS = 9
TRAIN_PCT = 0.7
SLIPPAGE_BPS = 5.0
OBJECTIVE = 'sharpe_ratio'
HISTORY_DAYS = 3 * 365
ORIGINAL_11 = ['HK.00700', 'HK.00005', 'HK.09988', 'HK.03690', 'HK.01299',
               'HK.00941', 'HK.02318', 'HK.01211', 'HK.01810', 'HK.09618', 'HK.09888']


def main():
    storage = DataStorage()
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    for sym in ORIGINAL_11:
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

        selector_oos, blend_oos, rule_oos, hmm_oos = [], [], [], []
        for rw, hw in zip(rule_windows, hmm_windows):
            r_test = rw.test_metrics.get('return_pct', 0.0)
            h_test = hw.test_metrics.get('return_pct', 0.0)
            rule_oos.append(r_test)
            hmm_oos.append(h_test)
            blend_oos.append((r_test + h_test) / 2)

            rule_train = rw.train_metrics.get(OBJECTIVE, 0.0)
            hmm_train = hw.train_metrics.get(OBJECTIVE, 0.0)
            picked = 'HMM' if hmm_train > rule_train else 'Rule'
            selector_oos.append(h_test if picked == 'HMM' else r_test)

        rows.append({
            'symbol': sym, 'windows': len(rule_oos),
            'rule_alone': sum(rule_oos) / len(rule_oos),
            'hmm_alone': sum(hmm_oos) / len(hmm_oos),
            'adaptive_selector': sum(selector_oos) / len(selector_oos),
            'fixed_blend': sum(blend_oos) / len(blend_oos),
        })
        print(f"{sym} | rule {rows[-1]['rule_alone']:+.3f}% | hmm {rows[-1]['hmm_alone']:+.3f}% | "
              f"selector {rows[-1]['adaptive_selector']:+.3f}% | blend {rows[-1]['fixed_blend']:+.3f}% | "
              f"{time.time()-t_sym:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'combination_attempts_unified_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))
    print(f"\nMean — rule alone: {df.rule_alone.mean():+.3f}%  hmm alone: {df.hmm_alone.mean():+.3f}%  "
          f"adaptive selector: {df.adaptive_selector.mean():+.3f}%  fixed blend: {df.fixed_blend.mean():+.3f}%")


if __name__ == "__main__":
    main()
