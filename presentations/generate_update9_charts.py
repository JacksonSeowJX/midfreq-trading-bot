"""
Generates the 4 charts used in generate_update9_slides.py.

Committed here (rather than living only in a scratchpad) so they're
reproducible and don't silently vanish between sessions. Run this
BEFORE generate_update9_slides.py, pointing both scripts at the same
output directory.

Data sources:
  - Combinations chart: results/combination_attempts_unified_*.csv
    (rule alone / HMM alone / adaptive selector / fixed blend, all
    computed in one script so every bar shares identical walk-forward
    window boundaries — see combination_attempts_unified.py)
  - Funnel / stress-funnel / buy-hold charts: pair counts and means
    already established and reported from best_tool_allocation_robust*.py,
    hmm_gross_robust_validation.py, us_robust_validation.py, and
    buy_hold_benchmark.py — hardcoded here as the final, verified numbers
    from those studies (each is its own separate, dated study by design,
    unlike the combinations chart which needed a single shared run).

Usage:
    python3 presentations/generate_update9_charts.py [output_dir]
"""
import sys
import glob
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent

SURFACE = '#EDE8E0'
INK = '#2D2D2D'
INK_MUTED = '#6B6B63'
GRID = '#D8D3CA'
RED = '#C62828'
GREEN = '#2E7D32'
ORANGE = '#eb6834'

plt.rcParams.update({
    'font.family': 'sans-serif', 'font.sans-serif': ['Helvetica Neue', 'Arial'],
    'text.color': INK, 'axes.facecolor': SURFACE, 'figure.facecolor': SURFACE,
    'savefig.facecolor': SURFACE,
})


def chart_combinations():
    files = sorted(glob.glob(str(REPO_ROOT / 'results' / 'combination_attempts_unified_*.csv')))
    if not files:
        raise FileNotFoundError("Run combination_attempts_unified.py first")
    df = pd.read_csv(files[-1])  # most recent

    labels = ['Rule\n(alone)', 'HMM\n(alone)', 'Adaptive\nSelector', '50/50\nFixed Blend']
    values = [df.rule_alone.mean(), df.hmm_alone.mean(),
              df.adaptive_selector.mean(), df.fixed_blend.mean()]
    colors_bar = [INK_MUTED, INK_MUTED, RED, ORANGE]

    fig, ax = plt.subplots(figsize=(8.4, 4.2), dpi=200)
    ax.bar(range(len(labels)), values, 0.55, color=colors_bar, zorder=3)
    for i, v in enumerate(values):
        ax.annotate(f'{v:+.3f}%', xy=(i, v), xytext=(0, -14 if v < 0 else 6),
                    textcoords='offset points', ha='center', fontsize=11, fontweight='bold', color=INK)
    ax.axhline(0, color=INK_MUTED, lw=1)
    ax.set_xticks(range(len(labels)), labels, fontsize=10)
    ax.set_ylabel('Mean out-of-sample return per window')
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u9_combinations.png', bbox_inches='tight')
    plt.close(fig)
    print('combinations chart done —', dict(zip(labels, [round(v, 3) for v in values])))


def chart_funnel():
    stages = ['Single test\n(1 configuration)', 'Two tests must\nagree (1yr data)',
              'Tripled the data\n(3yr, still 2 tests)', 'New stocks\n(8 more, 3yr, 2 tests)']
    passed = [2, 0, 0, 0]
    total = [11, 33, 33, 24]

    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=200)
    x = range(len(stages))
    ax.bar(x, total, 0.55, color=GRID, zorder=2, label='Pairs tested')
    ax.bar(x, passed, 0.55, color=[GREEN if p > 0 else RED for p in passed], zorder=3, label='Pairs that passed')
    for i, (p, t) in enumerate(zip(passed, total)):
        ax.annotate(f'{p}/{t}', xy=(i, max(t, 3) + 1), ha='center', fontsize=13, fontweight='bold',
                    color=GREEN if p > 0 else RED)
    ax.set_xticks(list(x), stages, fontsize=10)
    ax.set_ylabel('Strategy/symbol pairs tested')
    ax.set_ylim(0, 40)
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='upper right', frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u9_funnel.png', bbox_inches='tight')
    plt.close(fig)
    print('funnel chart done')


def chart_stress_funnel():
    stages = ['Two configs\nmust agree\n(HK, 1yr)', 'Tripled the\ndata (HK, 3yr)',
              'New sectors\n(HK, +8 stocks)', 'Costs removed\n(HMM, HK)', 'New market\n(15 US stocks)']
    passed = [0, 0, 0, 0, 0]
    total = [33, 33, 24, 19, 45]

    fig, ax = plt.subplots(figsize=(10.4, 4.6), dpi=200)
    x = range(len(stages))
    ax.bar(x, total, 0.55, color=GRID, zorder=2, label='Pairs tested')
    ax.bar(x, passed, 0.55, color=RED, zorder=3, label='Pairs that passed')
    for i, (p, t) in enumerate(zip(passed, total)):
        ax.annotate(f'{p}/{t}', xy=(i, t + 1.5), ha='center', fontsize=13, fontweight='bold', color=RED)
    ax.set_xticks(list(x), stages, fontsize=9.5)
    ax.set_ylabel('Strategy/symbol pairs tested')
    ax.set_ylim(0, 50)
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='upper left', frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u9v2_stress_funnel.png', bbox_inches='tight')
    plt.close(fig)
    print('stress funnel chart done')


def chart_buyhold():
    groups = ['HK\n(19 stocks)', 'US\n(15 stocks)']
    strat_vals = [-0.113, -0.207]
    bh_vals = [-1.114, 0.262]

    fig, ax = plt.subplots(figsize=(9.0, 4.6), dpi=200)
    x = [0, 1]
    width = 0.32
    ax.bar([i - width / 2 for i in x], strat_vals, width, color=INK_MUTED, label='Mean of 3 strategies', zorder=3)
    ax.bar([i + width / 2 for i in x], bh_vals, width, color=[RED, GREEN], label='Buy & Hold', zorder=3)
    for i, v in enumerate(strat_vals):
        ax.annotate(f'{v:+.2f}%', xy=(i - width / 2, v), xytext=(0, -16 if v < 0 else 6),
                    textcoords='offset points', ha='center', fontsize=11, fontweight='bold', color=INK)
    for i, v in enumerate(bh_vals):
        ax.annotate(f'{v:+.2f}%', xy=(i + width / 2, v), xytext=(0, -16 if v < 0 else 6),
                    textcoords='offset points', ha='center', fontsize=11, fontweight='bold', color=INK)
    ax.axhline(0, color=INK_MUTED, lw=1)
    ax.set_xticks(x, groups, fontsize=12)
    ax.set_ylabel('Mean OOS return per test window')
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='lower left', frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u9v2_buyhold.png', bbox_inches='tight')
    plt.close(fig)
    print('buy-hold chart done')


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    chart_combinations()
    chart_funnel()
    chart_stress_funnel()
    chart_buyhold()
