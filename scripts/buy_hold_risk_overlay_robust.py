"""
Buy & Hold + Risk Overlay — Robust Validation
==================================================================
Every strategy tested so far in this project tries to time ENTRIES:
decide when to get in and out based on a signal. All of them failed
the two-config robust standard. This tests a structurally different
question, flagged as future work in the report's Limitations section
(6.3): can the project's existing stop-loss / trailing-stop risk
infrastructure improve on unconditional buy-and-hold, without trying
to predict direction at all?

BuyAndHoldRiskOverlay (src/core/strategy.py) buys once at the first
candle of each walk-forward window and holds, exiting only if a
stop-loss or trailing stop fires (never on a signal, never re-entering
after a stop-out). Its two parameters (stop_loss_pct, trailing_stop_pct)
are grid-searched on the train portion of each window and tested
out-of-sample, exactly like every other strategy in this project. Same
two-config agreement standard (9-window / 15-window, 3yr, 5bps slippage)
applies before a symbol counts as "robust."

Usage:
    python3 scripts/buy_hold_risk_overlay_robust.py [market]   # HK or US, defaults to both
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from core.optimizer import walk_forward

STRATEGY = 'Buy & Hold + Risk Overlay'
CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def load_buyhold_benchmark(market):
    """Most recent buy_hold_benchmark_*.csv for this market, for a head-to-head comparison."""
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    pattern = 'buy_hold_benchmark_us_*.csv' if market == 'US' else 'buy_hold_benchmark_2*.csv'
    matches = sorted(results_dir.glob(pattern))
    if not matches:
        return {}
    df = pd.read_csv(matches[-1])
    return {row['symbol']: row for _, row in df.iterrows()}


def run_market(market, storage, config):
    symbols = config.get_live_symbols(market=market) or config.get_all_symbols(market=market)
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)
    buyhold = load_buyhold_benchmark(market)

    rows = []
    for sym in symbols:
        per_config = {}
        for cfg in CONFIGS:
            t_job = time.time()
            res = walk_forward(
                strategy_name=STRATEGY, symbols=[sym], timeframe=Timeframe.HOUR_1,
                start_date=start, end_date=end, storage=storage,
                n_splits=cfg['n_splits'], train_pct=cfg['train_pct'], objective=OBJECTIVE,
                slippage_bps=SLIPPAGE_BPS,
            )
            s = res.get('summary', {})
            if not s or s.get('error'):
                per_config[cfg['name']] = None
                print(f"{market} {sym} | {cfg['name']:16s} | skipped ({s.get('error', 'no result')})", flush=True)
                continue
            per_config[cfg['name']] = {
                'oos_return': round(s['avg_oos_return'], 3),
                'consistency': round(s['consistency_pct'], 1),
            }
            print(f"{market} {sym} | {cfg['name']:16s} | OOS {s['avg_oos_return']:+6.2f}% | "
                  f"consistency {s['consistency_pct']:3.0f}% | {time.time()-t_job:.0f}s", flush=True)

        both_qualify = all(
            per_config.get(cfg['name']) is not None and
            qualifies(per_config[cfg['name']]['oos_return'], per_config[cfg['name']]['consistency'])
            for cfg in CONFIGS
        )
        a = per_config.get(CONFIGS[0]['name'])
        b = per_config.get(CONFIGS[1]['name'])
        bh = buyhold.get(sym)
        rows.append({
            'market': market, 'symbol': sym,
            'config_a_oos': a['oos_return'] if a else None,
            'config_a_consistency': a['consistency'] if a else None,
            'config_b_oos': b['oos_return'] if b else None,
            'config_b_consistency': b['consistency'] if b else None,
            'buyhold_a_oos': round(bh['config_a_oos'], 3) if bh is not None else None,
            'buyhold_b_oos': round(bh['config_b_oos'], 3) if bh is not None else None,
            'beats_buyhold_both_configs': (
                bh is not None and a is not None and b is not None and
                a['oos_return'] > bh['config_a_oos'] and b['oos_return'] > bh['config_b_oos']
            ),
            'robust': both_qualify,
        })
    return rows


def main():
    markets = [sys.argv[1]] if len(sys.argv) > 1 else ['HK', 'US']
    storage = DataStorage()
    config = ConfigLoader()

    t0 = time.time()
    all_rows = []
    for market in markets:
        all_rows.extend(run_market(market, storage, config))

    df = pd.DataFrame(all_rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'buy_hold_risk_overlay_robust_{stamp}.csv'
    df.to_csv(out, index=False)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))
    n_robust = df['robust'].sum()
    print(f"\n{n_robust}/{len(df)} symbols robust (stop-loss/trailing-stop overlay beats plain "
          f"buy-and-hold consistently across both configs)")


if __name__ == "__main__":
    main()
