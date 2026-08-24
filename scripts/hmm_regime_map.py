"""
HMM Regime Map: Does "TREND" vs "RANGE" Actually Look Like a Trend?
========================================================================
Replays the HMM strategy's real classify-as-you-go logic (same
refit-every-60-candles, no-lookahead behavior used in backtesting and
live trading) over real price history, and plots the state label at
every point in time against the price itself. A sanity check on
whether the model's statistical definition of "trending" (drift
relative to hourly volatility, refit on a rolling 300-candle window)
matches what a human would call trending by eye.

Usage:
    python3 scripts/hmm_regime_map.py [SYMBOL] [n_candles]
    python3 scripts/hmm_regime_map.py HK.00700 1200
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from core.storage import DataStorage
from core.strategy import HMMRegimeSwitchStrategy
from core.portfolio import Portfolio

SURFACE = '#EDE8E0'
INK = '#2D2D2D'
COLOR_MAP = {'TREND': '#2E7D32', 'RANGE': '#2a78d6', 'FLAT': '#C62828'}


def build_regime_map(symbol: str, n_candles: int = 1200):
    storage = DataStorage()
    df = storage.load_data(symbol, '1h').tail(n_candles)
    closes = df['close'].tolist()
    timestamps = df.index.tolist()

    strat = HMMRegimeSwitchStrategy(Portfolio(initial_cash=100_000.0), n_states=2)

    labels = []
    prices_seen = []
    for close in closes:
        prices_seen.append(close)
        if len(prices_seen) < 60:
            labels.append(None)
            continue
        labels.append(strat._classify_regime(symbol, prices_seen))

    valid = [i for i, l in enumerate(labels) if l is not None]
    start = valid[0]
    return timestamps[start:], closes[start:], labels[start:]


def plot_regime_map(symbol: str, ts, px, lb, out_path: Path):
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Helvetica Neue', 'Arial'],
                          'text.color': INK, 'axes.facecolor': SURFACE, 'figure.facecolor': SURFACE,
                          'savefig.facecolor': SURFACE})
    fig, ax = plt.subplots(figsize=(13, 5), dpi=200)

    run_start = 0
    for i in range(1, len(lb) + 1):
        if i == len(lb) or lb[i] != lb[run_start]:
            ax.axvspan(ts[run_start], ts[i - 1] if i < len(lb) else ts[-1],
                       color=COLOR_MAP.get(lb[run_start], '#999'), alpha=0.15, lw=0)
            run_start = i

    ax.plot(ts, px, color=INK, lw=1.1, zorder=3)
    handles = [Patch(color=c, alpha=0.4, label=k) for k, c in COLOR_MAP.items() if k in set(lb)]
    ax.legend(handles=handles, loc='upper left', frameon=False)

    occ = pd.Series(lb).value_counts(normalize=True) * 100
    title = f"{symbol} — HMM regime classification over time  |  " + \
            "  ".join(f"{k}: {v:.0f}%" for k, v in occ.items())
    ax.set_title(title, fontsize=12)
    ax.set_ylabel('Close price')
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches='tight')
    plt.close(fig)
    return occ


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'HK.00700'
    n_candles = int(sys.argv[2]) if len(sys.argv) > 2 else 1200

    ts, px, lb = build_regime_map(symbol, n_candles)
    out_dir = Path(__file__).resolve().parent.parent / 'results' / 'charts'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"regime_map_{symbol.replace('.', '_')}.png"
    occ = plot_regime_map(symbol, ts, px, lb, out_path)

    print(f"Saved {out_path}")
    print(occ)


if __name__ == "__main__":
    main()
