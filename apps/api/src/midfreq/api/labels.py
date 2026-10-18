"""
Human-readable names for result files and universes, as the web app shows them.
"""
import re
from pathlib import Path
from typing import Tuple

# app.py, research view: result-file prefix -> label
STUDY_LABELS = {
    'best_tool_new_stocks_allocation': 'Allocation — New Sectors',
    'best_tool_new_stocks_full': 'Full Comparison — New Sectors',
    'best_tool_robust_v2_allocation': 'Allocation — Robust, 3yr Data (current)',
    'best_tool_robust_v2_full': 'Full Comparison — Robust, 3yr Data (current)',
    'best_tool_robust_allocation': 'Allocation — Robust, 1yr Data (superseded)',
    'best_tool_robust_full': 'Full Comparison — Robust, 1yr Data (superseded)',
    'best_tool_allocation': 'Allocation — Single Config (superseded)',
    'best_tool_full': 'Full Comparison — Single Config (superseded)',
    'fixed_split_combo_summary': 'Fixed-Split 50/50 Combo — Summary',
    'fixed_split_combo_windows': 'Fixed-Split 50/50 Combo — Per-Window Detail',
    'ml_classifier': 'ML Direction Classifier — Walk-Forward',
    'cross_sectional_walkforward': 'Cross-Sectional Reversal — Walk-Forward (superseded)',
    'cross_sectional_robust': 'Cross-Sectional Reversal — Robust, HK 19 / US 15',
    'cross_sectional_sp100': 'Cross-Sectional Reversal — Robust, US S&P 100',
    'cross_sectional_hsi': 'Cross-Sectional Reversal — Robust, HK Hang Seng Index',
    'cross_sectional_momentum_robust': 'Cross-Sectional Momentum — Robust, HK 19 / US 15',
    'cross_sectional_corrected': 'Cross-Sectional, corrected — both directions, S&P 100 + Hang Seng (current, headline)',
    'cross_sectional_cost_sensitivity': 'Cross-Sectional — Cost Sensitivity of the S&P 100 result',
    'cross_sectional_momentum_broad': 'Cross-Sectional Momentum — Broad universes (superseded)',
    'buy_hold_risk_overlay_robust': 'Buy & Hold + Risk Overlay — Robust, HK 19 / US 15',
    'model_selector_windows': 'Adaptive Selector — Per-Window (failed)',
    'model_selector_summary': 'Adaptive Selector — Summary (failed)',
    'hmm_regime': 'HMM vs Rule — Head-to-Head',
    'hmm_forced_3state': 'HMM Forced 3-State Test',
    'pairs_walkforward': 'Pairs Trading — Walk-Forward',
    'pairs_scan': 'Pairs Trading — Cointegration Scan',
    'regime_switch_1h': 'Regime Switch — 1h Scan',
}

# app.py, sidebar: universe -> label
UNIVERSE_LABELS = {
    'hk_live': 'HK — live roster (19)',
    'us_15':   'US — 15 large caps (15)',
    'sp100':   'US — S&P 100 (100)',
    'hsi':     'HK — Hang Seng Index (88)',
}


def study_and_label(path: Path) -> Tuple[str, str]:
    """(study prefix, label) of a result file: the name without its _YYYYMMDD[_HHMMSS] stamp,
    and that prefix's label (or the prefix in title case), as app.py's label_for does."""
    stem = Path(path).stem
    m = re.match(r'^(.*?)_(\d{8}(_\d{6})?)$', stem)
    prefix = m.group(1) if m else stem
    return prefix, STUDY_LABELS.get(prefix, prefix.replace('_', ' ').title())
