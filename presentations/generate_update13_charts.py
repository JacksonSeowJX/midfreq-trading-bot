"""
Charts for Update 13 — correcting for how many combinations were tried.

Reads results/reality_check_*.json where a chart depends on the study, so
the deck cannot drift from the numbers. The fee chart is fixed data from
the 2026-09-24 paper-fill calibration.

Usage:
    python3 presentations/generate_update13_charts.py [output_dir]
"""
import glob
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / 'results'
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent

SURFACE='#EDE8E0'; INK='#2D2D2D'; MUTED='#6B6B63'; GRID='#D8D3CA'
RED='#C62828'; GREEN='#2E7D32'; ORANGE='#eb6834'; BLUE='#2a78d6'
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Helvetica Neue','Arial'],
                     'text.color':INK,'axes.facecolor':SURFACE,'figure.facecolor':SURFACE,
                     'savefig.facecolor':SURFACE})


def latest_rc():
    # dated results only: reality_check_cache.json matches reality_check_*.json and sorts last
    f = sorted(glob.glob(str(RESULTS / 'reality_check_2*.json')))
    return json.loads(Path(f[-1]).read_text()) if f else None


def chart_fee():
    """The HK fee: measured on a real fill, rebuilt from the published schedule."""
    notional, measured = 43740.00, 77.73
    items = [
        ('stamp duty\n0.1% rounded up', math.ceil(notional * 0.001)),
        ('platform fee\nFLAT HK\\$15', 15.0),
        ('commission\n0.03% min \\$3', max(3.0, notional * 0.0003)),
        ('HKEX\n0.00565%', notional * 0.0000565),
        ('CCASS\n0.002% min \\$2', max(2.0, notional * 0.00002)),
        ('SFC + FRC\nlevies', notional * 0.000027 + notional * 0.0000015),
    ]
    total = sum(v for _, v in items)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.6, 4.8), dpi=200,
                                  gridspec_kw={'width_ratios': [1.25, 1]})
    labels = [k for k, _ in items]; vals = [v for _, v in items]
    cols = [ORANGE if 'FLAT' in k else MUTED for k in labels]
    ax.bar(range(len(vals)), vals, 0.62, color=cols, zorder=3)
    for i, v in enumerate(vals):
        ax.annotate(f'{v:,.2f}', xy=(i, v), xytext=(0, 5), textcoords='offset points',
                    ha='center', fontsize=10, fontweight='bold')
    ax.set_xticks(range(len(labels)), labels, fontsize=9)
    ax.set_ylabel('HK\\$ on a HK\\$43,740 trade')
    ax.set_title(f'Rebuilt from the published schedule = HK\\${total:,.2f}\n'
                 f'Measured on a real paper fill = HK\\${measured:,.2f}   '
                 f'(HK\\${abs(total-measured):.2f} apart)',
                 fontsize=11.5, loc='left', color=INK, pad=10)
    ax.grid(axis='y', color=GRID, lw=0.8); ax.tick_params(length=0)
    ax.spines[['top','right','left']].set_visible(False)

    # the flat fee is why the percentage depends on trade size
    var = (measured - 15.0) / notional
    sizes = np.array([25_000, 50_000, 100_000, 250_000, 500_000])
    pct = (var * sizes + 15.0) / sizes * 100
    ax2.plot(sizes, pct, marker='o', color=ORANGE, lw=2.4, ms=8, zorder=3)
    ax2.axhline(0.16, color=GREEN, ls='--', lw=2)
    ax2.annotate('0.160% — what the code assumes', xy=(sizes[-1], 0.1645), ha='right',
                 fontsize=10, color=GREEN, fontweight='bold')
    for s, p in zip(sizes, pct):
        ax2.annotate(f'{p:.3f}%', xy=(s, p), xytext=(0, 8), textcoords='offset points',
                     ha='center', fontsize=9.5)
    ax2.set_xscale('log'); ax2.set_xticks(sizes)
    ax2.set_xticklabels([f'{s//1000}k' for s in sizes], fontsize=10)
    ax2.set_xlabel('trade size (HK\\$)'); ax2.set_ylabel('effective fee per side')
    ax2.set_title('A flat HK\\$15 means the % depends on size',
                  fontsize=11.5, loc='left', color=INK, pad=10)
    ax2.grid(axis='y', color=GRID, lw=0.8); ax2.tick_params(length=0)
    ax2.spines[['top','right','left']].set_visible(False)
    fig.tight_layout(); fig.savefig(OUT / 'chart_u13_fee.png', bbox_inches='tight')
    plt.close(fig); print('fee chart done')


def chart_drift():
    """Same study, three data end dates: pass, fail, pass.

    Config B's consistency (share of its 15 test windows that made money) is
    the figure that decides the verdict, so that is what is plotted, against
    the 50% bar. Figures: 21 Aug from cross_sectional_cost_sensitivity at
    0.005%; 28 Aug and 25 Sep from the two Reality Check runs.
    """
    ends = ['data to\n21 Aug 2026', 'data to\n28 Aug 2026', 'data to\n25 Sep 2026']
    cons_b = [66.7, 46.7, 80.0]
    wins_b = ['10 of 15', '7 of 15', '12 of 15']
    mean_a = [5.73, 6.15, 7.79]; mean_b = [0.63, 1.87, 2.99]
    passed = [c >= 50 for c in cons_b]
    fig, ax = plt.subplots(figsize=(10.2, 4.9), dpi=200)
    cols = [GREEN if ok else RED for ok in passed]
    ax.bar(range(3), cons_b, 0.5, color=cols, zorder=3)
    ax.axhline(50, color=INK, ls='--', lw=1.6, zorder=4)
    ax.annotate('50% bar', xy=(2.42, 51.5), fontsize=10, color=INK, ha='right')
    for i in range(3):
        ax.annotate(f"{wins_b[i]} windows\nprofitable ({cons_b[i]:.1f}%)", xy=(i, max(cons_b[i], 52)),
                    xytext=(0, 6), textcoords='offset points', ha='center', fontsize=10.5,
                    fontweight='bold')
        ax.annotate('PASSES' if passed[i] else 'FAILS', xy=(i, 8), ha='center', fontsize=13,
                    fontweight='bold', color='white', zorder=5)
        ax.annotate(f"A {mean_a[i]:+.2f}%   B {mean_b[i]:+.2f}%", xy=(i, -15), ha='center',
                    fontsize=9.5, color=MUTED, annotation_clip=False)
    ax.set_xticks(range(3), ends, fontsize=11)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Config B: share of test windows profitable (%)')
    ax.grid(axis='y', color=GRID, lw=0.8); ax.tick_params(length=0)
    ax.spines[['top','right','left']].set_visible(False)
    ax.set_title('Same strategy, code and fee. Only the end date of the data changes.',
                 fontsize=11.5, loc='left', color=MUTED, pad=10)
    fig.tight_layout(); fig.subplots_adjust(bottom=0.2)
    fig.savefig(OUT / 'chart_u13_drift.png', bbox_inches='tight'); plt.close(fig); print('drift chart done')


def chart_rc():
    """The Reality Check: distribution of the best-of-N edgeless score, winner marked.

    The shaded tail to the right of the actual winner IS the p-value, which a
    quantile band cannot show. The distribution is recomputed from the stored
    per-window returns with the same bootstrap and seed as the study.
    """
    rc = latest_rc()
    if not rc or not rc.get('by_config'):
        print('rc chart SKIPPED — no reality_check_2*.json yet'); return
    sys.path.insert(0, str(REPO / 'src'))
    from core.reality_check import stationary_bootstrap_indices
    cfgs = list(rc['by_config'])
    fig, axes = plt.subplots(1, len(cfgs), figsize=(6.3*len(cfgs), 4.6), dpi=200, squeeze=False)
    for ax, cname in zip(axes[0], cfgs):
        d = rc['by_config'][cname]
        R = np.array(list(d['per_window'].values()), dtype=float)
        centred = R - R.mean(axis=1, keepdims=True)
        rng = np.random.default_rng(12345)
        n = R.shape[1]
        boot = np.array([centred[:, stationary_bootstrap_indices(n, d.get('mean_block', 2.0), rng)]
                         .mean(axis=1).max() for _ in range(d.get('n_boot', 20000))])
        obs = d['observed_max_mean']
        bins = np.linspace(min(boot.min(), 0), max(boot.max(), obs) * 1.05, 60)
        ax.hist(boot[boot < obs], bins=bins, color=MUTED, alpha=0.55, zorder=2,
                label='best of %d candidates with no edge' % d['n_candidates'])
        ax.hist(boot[boot >= obs], bins=bins, color=RED, alpha=0.85, zorder=3,
                label=f'luck matches or beats the winner: {(boot >= obs).mean():.1%}')
        ax.axvline(obs, color=INK, lw=2.4, zorder=4)
        ax.annotate(f"actual winner\n{d['best_candidate'].replace('CS-', '')}\n{obs:+.2f}%",
                    xy=(obs, 0.97), xycoords=('data', 'axes fraction'), va='top',
                    ha='left', xytext=(6, 0), textcoords='offset points', fontsize=10, fontweight='bold')
        ax.set_title(f"{cname}:  Reality Check p = {d['p_value']:.3f}   (tested alone: {d['naive_p_value']:.3f})",
                     fontsize=11.5, loc='left', pad=10)
        ax.set_xlabel('mean out-of-sample return per window (%)')
        ax.set_ylabel('bootstrap resamples')
        ax.grid(axis='y', color=GRID, lw=0.8); ax.tick_params(length=0)
        ax.spines[['top','right']].set_visible(False)
        ax.legend(frameon=False, fontsize=9, loc='center right')
    fig.tight_layout(); fig.savefig(OUT / 'chart_u13_rc.png', bbox_inches='tight')
    plt.close(fig); print('rc chart done')


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    chart_fee(); chart_drift(); chart_rc()
