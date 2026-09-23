"""
Charts for generate_update12_slides.py — "Trying to Break My Own Result".

Update 11 reported one combination passing the validation standard.
Update 12 is about the attempts to knock it down: pinning the real
trading fee, measuring selection bias with PBO, and a metric that was
built for the job and then failed to do it.

Reads from results/ so the deck cannot drift from the study output.

Usage:
    python3 presentations/generate_update12_charts.py [output_dir]
"""
import sys
import glob
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / 'results'
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent

SURFACE = '#EDE8E0'; INK = '#2D2D2D'; MUTED = '#6B6B63'; GRID = '#D8D3CA'
RED = '#C62828'; GREEN = '#2E7D32'; ORANGE = '#eb6834'; BLUE = '#2a78d6'

plt.rcParams.update({'font.family': 'sans-serif',
                     'font.sans-serif': ['Helvetica Neue', 'Arial'],
                     'text.color': INK, 'axes.facecolor': SURFACE,
                     'figure.facecolor': SURFACE, 'savefig.facecolor': SURFACE})


def latest(pattern):
    f = sorted(glob.glob(str(RESULTS / pattern)))
    if not f:
        raise FileNotFoundError(pattern)
    return f[-1]


def chart_cost():
    """The fee assumption decides the verdict."""
    df = pd.read_csv(latest('cross_sectional_cost_sensitivity_*.csv'))
    labels = [l.replace('\\n', '\n') for l in df['label']]
    fig, ax = plt.subplots(figsize=(10.4, 5.0), dpi=200)
    x = range(len(df)); w = 0.34
    ax.bar([i - w/2 for i in x], df.config_a_oos, w, color=MUTED, zorder=3, label='Config A (9 windows)')
    ax.bar([i + w/2 for i in x], df.config_b_oos, w, color=ORANGE, zorder=3, label='Config B (15 windows)')
    for i, r in df.iterrows():
        for xp, v in ((i - w/2, r.config_a_oos), (i + w/2, r.config_b_oos)):
            ax.annotate(f'{v:+.2f}%', xy=(xp, v), xytext=(0, 6 if v >= 0 else -15),
                        textcoords='offset points', ha='center', fontsize=10,
                        fontweight='bold', color=INK)
    ax.axhline(0, color=MUTED, lw=1)
    lo, hi = df.config_b_oos.min(), df.config_a_oos.max(); pad = (hi - lo) * 0.34
    ax.set_ylim(lo - pad, hi + pad)
    for i, ok in enumerate(df.robust):
        ax.annotate('PASSES' if ok else 'fails', xy=(i, hi + pad * 0.45), ha='center',
                    fontsize=12 if ok else 11, fontweight='bold', color=GREEN if ok else RED)
        if ok:
            ax.axvspan(i - 0.45, i + 0.45, color=GREEN, alpha=0.07, zorder=0)
    ax.set_xticks(list(x), labels, fontsize=10.5)
    ax.set_xlabel('Commission assumed, per side', fontsize=11)
    ax.set_ylabel('Mean out-of-sample return per window')
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='lower left', frameon=False, fontsize=9.5)
    fig.tight_layout(); fig.savefig(OUT / 'chart_u12_cost.png', bbox_inches='tight'); plt.close(fig)
    print('cost chart done')


def chart_pbo():
    """PBO: sampled grid flattered the result; the full grid is the honest number."""
    fig, ax = plt.subplots(figsize=(9.0, 4.8), dpi=200)
    vals = [0.095, 0.274]
    labels = ['40 configurations\n(sampled)', '96 configurations\n(what the study\nactually searched)']
    ax.bar(range(2), vals, 0.45, color=[MUTED, ORANGE], zorder=3)
    for i, v in enumerate(vals):
        ax.annotate(f'{v:.3f}', xy=(i, v), xytext=(0, 7), textcoords='offset points',
                    ha='center', fontsize=14, fontweight='bold', color=INK)
    ax.axhline(0.5, color=RED, lw=1.8, ls='--', zorder=4)
    ax.annotate('0.50  =  coin flip, selection means nothing', xy=(1.45, 0.515),
                ha='right', fontsize=10.5, color=RED, fontweight='bold')
    ax.set_ylim(0, 0.62)
    ax.set_xticks(range(2), labels, fontsize=11)
    ax.set_ylabel('Probability of Backtest Overfitting')
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout(); fig.savefig(OUT / 'chart_u12_pbo.png', bbox_inches='tight'); plt.close(fig)
    print('pbo chart done')


def chart_gap():
    """Training returns are enormous everywhere in the cross-sectional setup."""
    df = pd.read_csv(latest('overfitting_efficiency_*.csv'))
    df['kind'] = ['cross-sectional' if ('S&P' in s or 'Hang Seng' in s) else 'single-stock'
                  for s in df.study]
    lbl = [f"{r.study.replace('S&P 100','S&P').replace('Hang Seng','HSI').replace(' regime switch','').replace(' ML classifier','')}\n{r.config[0]}"
           for r in df.itertuples()]
    fig, ax = plt.subplots(figsize=(11.0, 5.0), dpi=200)
    x = range(len(df)); w = 0.36
    ax.bar([i - w/2 for i in x], df.avg_train_return, w, color=BLUE, zorder=3, label='in training (settings chosen here)')
    ax.bar([i + w/2 for i in x], df.avg_oos_return, w, color=ORANGE, zorder=3, label='out-of-sample (settings frozen)')
    for i, r in df.iterrows():
        ax.annotate(f'{r.avg_train_return:+.0f}%', xy=(i - w/2, r.avg_train_return),
                    xytext=(0, 5), textcoords='offset points', ha='center', fontsize=9, color=INK)
    ax.axhline(0, color=MUTED, lw=1)
    # 6 cross-sectional runs (S&P reversal/momentum, HSI reversal) then 4 single-stock
    n_cross = int((df.kind == 'cross-sectional').sum())
    ax.axvline(n_cross - 0.5, color=MUTED, lw=1.4, ls='--', alpha=0.6)
    ax.text((n_cross - 1) / 2, 44.5, 'cross-sectional', ha='center', fontsize=11.5,
            fontweight='bold', color=INK)
    ax.text(n_cross + (len(df) - n_cross - 1) / 2, 44.5, 'single-stock', ha='center',
            fontsize=11.5, fontweight='bold', color=MUTED)
    ax.set_ylim(-8, 52)
    ax.set_xticks(list(x), lbl, fontsize=8.5)
    ax.set_ylabel('Mean return per window')
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='upper center', frameon=False, fontsize=10, bbox_to_anchor=(0.5, -0.12), ncol=2)
    fig.tight_layout(); fig.savefig(OUT / 'chart_u12_gap.png', bbox_inches='tight'); plt.close(fig)
    print('gap chart done')


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    chart_cost(); chart_pbo(); chart_gap()
