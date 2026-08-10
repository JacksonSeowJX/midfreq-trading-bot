"""
Strategy vs. Buy-and-Hold: The Fair Comparison
==================================================
Combines the existing robust-validation results (Regime Switch, HMM
Regime Switch, ML Direction Classifier across all 19 symbols, configs
A/B) with the buy-and-hold benchmark over the same windows
(buy_hold_benchmark.py), and reports the UNCONDITIONAL mean OOS return
per strategy against buy-and-hold.

Deliberately does NOT report "best strategy per symbol" picked after
the fact — that would be hindsight selection bias (the exact failure
mode already documented for the adaptive selector, which used
training-period performance to guess the OOS winner and did worse than
either strategy alone precisely because that guess isn't reliable).
Every strategy's own unconditional mean is what's actually comparable
to a benchmark that also isn't selected with hindsight.

Usage:
    python3 scripts/strategy_vs_buyhold_comparison.py <strategy_csv1> [<strategy_csv2> ...] <buyhold_csv>
"""
import sys
from pathlib import Path

import pandas as pd

RESULTS_DIR = Path(__file__).resolve().parent.parent / 'results'


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    *strategy_paths, buyhold_path = sys.argv[1:]

    strat = pd.concat([pd.read_csv(p) for p in strategy_paths], ignore_index=True)
    strat['avg_oos'] = (strat['config_a_oos'] + strat['config_b_oos']) / 2

    bh = pd.read_csv(buyhold_path)
    bh['avg_oos'] = (bh['config_a_oos'] + bh['config_b_oos']) / 2
    bh['strategy'] = 'Buy & Hold'

    rows = []
    for name, sub in strat.groupby('strategy'):
        rows.append({
            'strategy': name,
            'mean_oos_pct': round(sub['avg_oos'].mean(), 3),
            'symbols_positive': int((sub['avg_oos'] > 0).sum()),
            'n_symbols': len(sub),
        })
    rows.append({
        'strategy': 'Buy & Hold',
        'mean_oos_pct': round(bh['avg_oos'].mean(), 3),
        'symbols_positive': int((bh['avg_oos'] > 0).sum()),
        'n_symbols': len(bh),
    })

    out_df = pd.DataFrame(rows).sort_values('mean_oos_pct', ascending=False).reset_index(drop=True)
    out_path = RESULTS_DIR / 'strategy_vs_buyhold_comparison.csv'
    out_df.to_csv(out_path, index=False)

    print("Unconditional mean OOS return per strategy vs. buy-and-hold (no hindsight selection):\n")
    print(out_df.to_string(index=False))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
