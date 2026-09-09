"""
Cross-Sectional Momentum on the Broad Universes (S&P 100 + Hang Seng)
=========================================================================
cross_sectional_momentum_robust.py tested momentum only on the 19-stock
HK and 15-stock US baskets. Those baskets are too small to be meaningful
evidence for a cross-sectional strategy, which works by ranking the
universe against itself: ranking 19 names and holding the top 2 is a
crude sort, and the literature evaluates these effects on hundreds of
stocks. That caveat was already noted for reversal, so applying it to
reversal but not momentum would be inconsistent.

This runs momentum on the same two broad, real-index universes reversal
was tested on (S&P 100, Hang Seng Index) under the identical two-config
standard, so both directions of the cross-sectional bet are judged on
equal, adequately-powered footing.

Usage:
    python3 scripts/cross_sectional_momentum_broad.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.optimizer import walk_forward
from backfill_sp100 import SP100_TICKERS
from backfill_hsi import HSI_CODES

STRATEGY = 'Cross-Sectional Momentum'
CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def symbols_for(market):
    out = []
    if market == 'US (S&P 100)':
        for t in SP100_TICKERS:
            sym = f"US.{t.replace('.', '')}"
            if (DATA_DIR / sym.replace('.', '_') / '1h.parquet').exists():
                out.append(sym)
    else:
        for code in HSI_CODES:
            sym = f"HK.{code}"
            if (DATA_DIR / sym.replace('.', '_') / '1h.parquet').exists():
                out.append(sym)
    return out


def main():
    storage = DataStorage()
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    for market in ('US (S&P 100)', 'HK (Hang Seng Index)'):
        symbols = symbols_for(market)
        print(f"\n=== {market}: {len(symbols)} symbols ===", flush=True)
        per_config = {}
        for cfg in CONFIGS:
            t_cfg = time.time()
            res = walk_forward(
                strategy_name=STRATEGY, symbols=symbols, timeframe=Timeframe.HOUR_1,
                start_date=start, end_date=end, storage=storage,
                n_splits=cfg['n_splits'], train_pct=cfg['train_pct'],
                objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS,
            )
            s = res.get('summary', {})
            per_config[cfg['name']] = {
                'oos_return': round(s.get('avg_oos_return', 0.0), 3),
                'consistency': round(s.get('consistency_pct', 0.0), 1),
                'oos_returns': [round(x, 4) for x in s.get('oos_returns', [])],
            }
            print(f"{cfg['name']}: OOS {per_config[cfg['name']]['oos_return']:+.2f}% | "
                  f"consistency {per_config[cfg['name']]['consistency']:.0f}% | "
                  f"{time.time()-t_cfg:.0f}s", flush=True)

        robust = all(qualifies(per_config[c['name']]['oos_return'],
                               per_config[c['name']]['consistency']) for c in CONFIGS)
        rows.append({
            'market': market, 'n_stocks': len(symbols),
            'config_a_oos': per_config[CONFIGS[0]['name']]['oos_return'],
            'config_a_consistency': per_config[CONFIGS[0]['name']]['consistency'],
            'config_b_oos': per_config[CONFIGS[1]['name']]['oos_return'],
            'config_b_consistency': per_config[CONFIGS[1]['name']]['consistency'],
            'robust': robust,
        })

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'cross_sectional_momentum_broad_{stamp}.csv'
    df.to_csv(out, index=False)
    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
