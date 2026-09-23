"""
One place that knows about every validated backtest in results/.

The Research Studies view shows one CSV at a time, which is fine for
reading a single study but gives no way to see the project's whole
record at once. Every study that was brought to the two-configuration
standard writes the same four columns (config_a_oos, config_a_consistency,
config_b_oos, config_b_consistency) plus a `robust` verdict, so they can
be stacked into a single table regardless of which script produced them.

`load_all()` returns that table. Each row is one thing that was tested
against the standard, labelled with the strategy, the universe or symbol
it ran on, and whether it passed.

Only the newest file per study prefix is read, so superseded re-runs do
not double-count. Pass current_only=False to include them.
"""
from pathlib import Path
import re
from typing import Optional

import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
RESULTS = REPO / 'results'

REQUIRED = ['config_a_oos', 'config_a_consistency', 'config_b_oos', 'config_b_consistency']

# Studies whose numbers are known to be untrustworthy. The 2026-09-08
# audit found six defects; cross_sectional_corrected supersedes the three
# cross-sectional studies that ran before the fixes.
SUPERSEDED = {
    'cross_sectional_sp100', 'cross_sectional_hsi', 'cross_sectional_robust',
    'cross_sectional_momentum_robust', 'cross_sectional_momentum_broad',
    'cross_sectional_walkforward', 'best_tool_robust_full', 'best_tool_full',
    'ml_classifier_macro',
}

# What each study was testing, for grouping in the overview.
STUDY_META = {
    'cross_sectional_corrected':   ('Cross-Sectional', 'universe'),
    'cross_sectional_cost_sensitivity': ('Cross-Sectional (cost sweep)', 'universe'),
    'buy_hold_risk_overlay_robust': ('Buy & Hold + Risk Overlay', 'symbol'),
    'best_tool_robust_v2_full':    ('Single-Stock Strategies', 'symbol'),
    'best_tool_new_stocks_full':   ('Single-Stock Strategies (new sectors)', 'symbol'),
    'us_robust_validation_full':   ('Single-Stock Strategies (US)', 'symbol'),
    'ml_classifier_baseline_sameday': ('ML Direction Classifier', 'symbol'),
    'buy_hold_benchmark':          ('Buy & Hold benchmark', 'symbol'),
    'buy_hold_benchmark_us':       ('Buy & Hold benchmark (US)', 'symbol'),
}


def _prefix(path: Path) -> str:
    m = re.match(r'^(.*?)_(\d{8}(_\d{6})?)$', path.stem)
    return m.group(1) if m else path.stem


def _newest_per_prefix(paths):
    seen = {}
    for p in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        pre = _prefix(p)
        if pre not in seen:
            seen[pre] = p
    return seen


def load_all(current_only: bool = True, results_dir: Optional[Path] = None) -> pd.DataFrame:
    """Every two-configuration result in results/, as one table."""
    rdir = Path(results_dir) if results_dir else RESULTS
    rows = []
    for prefix, path in _newest_per_prefix(rdir.glob('*.csv')).items():
        if current_only and prefix in SUPERSEDED:
            continue
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if not all(c in df.columns for c in REQUIRED):
            continue
        family, scope = STUDY_META.get(prefix, (prefix.replace('_', ' ').title(), 'symbol'))
        for _, r in df.iterrows():
            # what this row was run on
            if 'symbol' in df.columns and pd.notna(r.get('symbol')):
                subject = str(r['symbol'])
            elif 'market' in df.columns and pd.notna(r.get('market')):
                subject = str(r['market'])
            elif 'label' in df.columns and pd.notna(r.get('label')):
                subject = str(r['label']).replace('\n', ' ')
            else:
                subject = prefix
            strategy = str(r['strategy']) if 'strategy' in df.columns and pd.notna(r.get('strategy')) else family
            try:
                a, b = float(r['config_a_oos']), float(r['config_b_oos'])
                ca, cb = float(r['config_a_consistency']), float(r['config_b_consistency'])
            except (TypeError, ValueError):
                continue
            robust = bool(r['robust']) if 'robust' in df.columns and pd.notna(r.get('robust')) \
                else (a > 0 and b > 0 and ca >= 50 and cb >= 50)
            rows.append({
                'family': family, 'scope': scope, 'strategy': strategy, 'subject': subject,
                'config_a_oos': a, 'config_a_consistency': ca,
                'config_b_oos': b, 'config_b_consistency': cb,
                'worst_config': min(a, b), 'robust': robust,
                'study': prefix, 'file': path.name,
            })
    if not rows:
        return pd.DataFrame(columns=['family', 'scope', 'strategy', 'subject', 'config_a_oos',
                                     'config_a_consistency', 'config_b_oos', 'config_b_consistency',
                                     'worst_config', 'robust', 'study', 'file'])
    return pd.DataFrame(rows).sort_values(['robust', 'worst_config'], ascending=[False, False]).reset_index(drop=True)


def summary(df: Optional[pd.DataFrame] = None) -> dict:
    """Headline counts for the overview banner."""
    d = load_all() if df is None else df
    return {
        'total': len(d),
        'passed': int(d.robust.sum()),
        'studies': d.study.nunique(),
        'families': d.family.nunique(),
        'pass_rate': (d.robust.sum() / len(d) * 100) if len(d) else 0.0,
    }


if __name__ == '__main__':
    d = load_all()
    s = summary(d)
    print(f"{s['total']} validated results across {s['studies']} studies, "
          f"{s['passed']} passed ({s['pass_rate']:.1f}%)\n")
    for fam, g in d.groupby('family'):
        print(f"  {fam:42s} {int(g.robust.sum()):>2}/{len(g):<3} passed")
    print("\npassing rows:")
    p = d[d.robust]
    if p.empty:
        print("  none")
    else:
        print(p[['family', 'strategy', 'subject', 'config_a_oos', 'config_b_oos']].to_string(index=False))
