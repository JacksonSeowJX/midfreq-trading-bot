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
    f = sorted(glob.glob(str(RESULTS / 'reality_check_*.json')))
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
    """Same study, five more weeks of data, different answer."""
    fig, ax = plt.subplots(figsize=(9.2, 4.6), dpi=200)
    labels = ['Config A\n(9 windows)', 'Config B\n(15 windows)']
    then, now = [5.726, 0.632], [7.788, 2.987]
    x = np.arange(2); w = 0.34
    ax.bar(x - w/2, then, w, color=MUTED, zorder=3, label='data to 21 Aug (Update 12)')
    ax.bar(x + w/2, now, w, color=ORANGE, zorder=3, label='data to 25 Sep (now)')
    for xp, v in list(zip(x - w/2, then)) + list(zip(x + w/2, now)):
        ax.annotate(f'{v:+.3f}%', xy=(xp, v), xytext=(0, 5), textcoords='offset points',
                    ha='center', fontsize=10.5, fontweight='bold')
    ax.set_xticks(x, labels, fontsize=11)
    ax.set_ylabel('mean out-of-sample return per window')
    ax.set_ylim(0, max(now) * 1.3)
    ax.grid(axis='y', color=GRID, lw=0.8); ax.tick_params(length=0)
    ax.spines[['top','right','left']].set_visible(False)
    ax.legend(frameon=False, fontsize=10, loc='upper left')
    ax.set_title('Nothing changed but the data window sliding forward 5 weeks',
                 fontsize=11.5, loc='left', color=MUTED, pad=10)
    fig.tight_layout(); fig.savefig(OUT / 'chart_u13_drift.png', bbox_inches='tight')
    plt.close(fig); print('drift chart done')


def chart_rc():
    """The Reality Check itself: where the winner falls in the null distribution."""
    rc = latest_rc()
    if not rc or not rc.get('by_config'):
        print('rc chart SKIPPED — no reality_check_*.json yet'); return
    cfgs = list(rc['by_config'])
    fig, axes = plt.subplots(1, len(cfgs), figsize=(6.4*len(cfgs), 4.8), dpi=200, squeeze=False)
    for ax, cname in zip(axes[0], cfgs):
        d = rc['by_config'][cname]
        q = d['bootstrap_max_quantiles']
        ax.axvspan(q['p50'], q['p95'], color=MUTED, alpha=0.18, zorder=1,
                   label='where the best of an edgeless set lands (50th-95th)')
        for key, lbl in (('p50','median'), ('p95','95th')):
            ax.axvline(q[key], color=MUTED, ls=':', lw=1.6, zorder=2)
            ax.annotate(lbl, xy=(q[key], 0.92), xycoords=('data','axes fraction'),
                        rotation=90, fontsize=9, color=MUTED, ha='right', va='top')
        obs = d['observed_max_mean']
        ax.axvline(obs, color=GREEN if d['p_value'] < 0.05 else RED, lw=3, zorder=4)
        ax.annotate(f"actual best\n{obs:+.2f}%", xy=(obs, 0.55),
                    xycoords=('data','axes fraction'), fontsize=11, fontweight='bold',
                    color=GREEN if d['p_value'] < 0.05 else RED,
                    ha='left' if obs < q['p95'] else 'right',
                    xytext=(8 if obs < q['p95'] else -8, 0), textcoords='offset points')
        ax.set_title(f"{cname}\nReality Check p = {d['p_value']:.3f}    "
                     f"(naive p = {d['naive_p_value']:.3f})",
                     fontsize=12, loc='left', pad=10)
        ax.set_xlabel('mean out-of-sample return per window'); ax.set_yticks([])
        ax.grid(axis='x', color=GRID, lw=0.8); ax.tick_params(length=0)
        ax.spines[['top','right','left']].set_visible(False)
        ax.legend(frameon=False, fontsize=9, loc='upper left')
    fig.tight_layout(); fig.savefig(OUT / 'chart_u13_rc.png', bbox_inches='tight')
    plt.close(fig); print('rc chart done')


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    chart_fee(); chart_drift(); chart_rc()
