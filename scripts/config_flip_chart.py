from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent.parent / 'results' / 'charts' / 'config_flip.png'

SURFACE = '#EDE8E0'
INK = '#2D2D2D'
GRID = '#D8D3CA'
GREEN = '#2E7D32'
RED = '#C62828'

plt.rcParams.update({
    'font.family': 'sans-serif', 'font.sans-serif': ['Helvetica Neue', 'Arial'],
    'text.color': INK, 'axes.facecolor': SURFACE, 'figure.facecolor': SURFACE,
    'savefig.facecolor': SURFACE,
})

labels = ['AAPL\n+ HMM', 'TSLA\n+ Rule', 'AMZN\n+ HMM', 'PFE\n+ Rule', 'CAT\n+ HMM']
config_a = [0.029, 0.318, 0.163, 0.010, 0.502]
config_b = [-0.308, -0.049, 0.004, -0.006, -0.369]

x = range(len(labels))
width = 0.32
fig, ax = plt.subplots(figsize=(9.5, 5), dpi=200)
ax.bar([i - width/2 for i in x], config_a, width, color=GREEN, alpha=0.75,
       label='Config A (9 windows) — looked good', zorder=3)
ax.bar([i + width/2 for i in x], config_b, width, color=RED, alpha=0.75,
       label='Config B (15 windows) — same stock, same strategy', zorder=3)

for i, v in enumerate(config_a):
    ax.annotate(f'{v:+.2f}%', xy=(i - width/2, v), xytext=(0, 5), textcoords='offset points',
                ha='center', fontsize=10, fontweight='bold', color=INK)
for i, v in enumerate(config_b):
    ax.annotate(f'{v:+.2f}%', xy=(i + width/2, v), xytext=(0, -14), textcoords='offset points',
                ha='center', fontsize=10, fontweight='bold', color=INK)

ax.axhline(0, color=INK, lw=1)
ax.set_xticks(list(x), labels, fontsize=11)
ax.set_ylabel('Mean OOS return per test window')
ax.set_title('Five pairs that looked validated in one test, and failed in the other', fontsize=13)
ax.grid(axis='y', color=GRID, lw=0.8)
ax.spines[['top', 'right', 'left']].set_visible(False)
ax.tick_params(length=0)
ax.legend(loc='upper right', frameon=False, fontsize=9)
fig.tight_layout()
fig.savefig(OUT, bbox_inches='tight')
print(f"Saved {OUT}")
