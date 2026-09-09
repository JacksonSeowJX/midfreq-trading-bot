"""
Generates the 3 charts used in generate_update10_slides.py.

All computed directly from results/buy_hold_risk_overlay_robust_*.csv
(the most recent run), not hardcoded, so the charts stay accurate if
the study is rerun.

Usage:
    python3 presentations/generate_update10_charts.py [output_dir]
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


def load_df():
    files = sorted(glob.glob(str(REPO_ROOT / 'results' / 'buy_hold_risk_overlay_robust_*.csv')))
    if not files:
        raise FileNotFoundError("Run buy_hold_risk_overlay_robust.py first")
    return pd.read_csv(files[-1])


def chart_overview(df):
    n_robust = int(df['robust'].sum())
    n_beats = int((df['beats_buyhold_both_configs'] & ~df['robust']).sum())
    n_neither = len(df) - n_robust - n_beats

    labels = ['Robust,\nbut still lost\nto buy-and-hold', 'Beat buy-and-hold\n(loss mitigation\nonly)', 'Neither']
    values = [n_robust, n_beats, n_neither]
    colors_bar = [ORANGE, INK_MUTED, RED]

    fig, ax = plt.subplots(figsize=(8.0, 4.6), dpi=200)
    ax.bar(range(len(labels)), values, 0.5, color=colors_bar, zorder=3)
    for i, v in enumerate(values):
        ax.annotate(f'{v}/{len(df)}', xy=(i, v), xytext=(0, 6),
                    textcoords='offset points', ha='center', fontsize=13, fontweight='bold', color=INK)
    ax.set_xticks(range(len(labels)), labels, fontsize=11)
    ax.set_ylabel('Symbols (of 34)')
    ax.set_ylim(0, len(df) * 0.7)
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u10_overview.png', bbox_inches='tight')
    plt.close(fig)
    print(f'overview chart done — robust {n_robust}, beats {n_beats}, neither {n_neither}')


def chart_jpm(df):
    row = df[df.symbol == 'US.JPM'].iloc[0]
    configs = ['Config A\n(9 windows)', 'Config B\n(15 windows)']
    overlay_vals = [row['config_a_oos'], row['config_b_oos']]
    bh_vals = [row['buyhold_a_oos'], row['buyhold_b_oos']]

    fig, ax = plt.subplots(figsize=(7.4, 4.6), dpi=200)
    x = [0, 1]
    width = 0.32
    ax.bar([i - width / 2 for i in x], overlay_vals, width, color=ORANGE, label='Buy & Hold + Risk Overlay', zorder=3)
    ax.bar([i + width / 2 for i in x], bh_vals, width, color=GREEN, label='Plain Buy & Hold', zorder=3)
    for i, v in enumerate(overlay_vals):
        ax.annotate(f'{v:+.2f}%', xy=(i - width / 2, v), xytext=(0, 6), textcoords='offset points',
                    ha='center', fontsize=11, fontweight='bold', color=INK)
    for i, v in enumerate(bh_vals):
        ax.annotate(f'{v:+.2f}%', xy=(i + width / 2, v), xytext=(0, 6), textcoords='offset points',
                    ha='center', fontsize=11, fontweight='bold', color=INK)
    ax.axhline(0, color=INK_MUTED, lw=1)
    ax.set_xticks(x, configs, fontsize=12)
    ax.set_ylabel('Out-of-sample return')
    ax.set_ylim(0, max(bh_vals) * 1.3)
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='upper right', frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u10_jpm.png', bbox_inches='tight')
    plt.close(fig)
    print('JPM chart done —', dict(zip(configs, overlay_vals)))


def chart_loss_mitigation(df):
    beats = df[df['beats_buyhold_both_configs'] & ~df['robust']].copy()
    beats['bh_avg'] = (beats['buyhold_a_oos'] + beats['buyhold_b_oos']) / 2
    beats['overlay_avg'] = (beats['config_a_oos'] + beats['config_b_oos']) / 2
    worst5 = beats.sort_values('bh_avg').head(5)

    labels = [s.replace('HK.', 'HK ').replace('US.', 'US ') for s in worst5['symbol']]
    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=200)
    x = range(len(labels))
    width = 0.32
    ax.bar([i - width / 2 for i in x], worst5['bh_avg'], width, color=RED, label='Plain Buy & Hold', zorder=3)
    ax.bar([i + width / 2 for i in x], worst5['overlay_avg'], width, color=ORANGE, label='Buy & Hold + Risk Overlay', zorder=3)
    for i, v in enumerate(worst5['bh_avg']):
        ax.annotate(f'{v:+.2f}%', xy=(i - width / 2, v), xytext=(0, -14), textcoords='offset points',
                    ha='center', fontsize=10, fontweight='bold', color=INK)
    for i, v in enumerate(worst5['overlay_avg']):
        ax.annotate(f'{v:+.2f}%', xy=(i + width / 2, v), xytext=(0, -14 if v < 0 else 6), textcoords='offset points',
                    ha='center', fontsize=10, fontweight='bold', color=INK)
    ax.axhline(0, color=INK_MUTED, lw=1)
    ax.set_xticks(list(x), labels, fontsize=11)
    ax.set_ylabel('Mean OOS return (both configs averaged)')
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc='lower right', frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_u10_lossmitigation.png', bbox_inches='tight')
    plt.close(fig)
    print('loss mitigation chart done —', list(zip(worst5['symbol'], worst5['bh_avg'].round(2), worst5['overlay_avg'].round(2))))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_df()
    chart_overview(df)
    chart_jpm(df)
    chart_loss_mitigation(df)
