"""
Cross-Sectional Strategies, Corrected Re-Run
=========================================================================
Supersedes cross_sectional_sp100.py, cross_sectional_hsi.py and
cross_sectional_momentum_broad.py. Those studies' numbers are not
trustworthy: a 2026-09-08 audit found six defects, five of which
affected them directly.

  1. NON-DETERMINISM. The basket was a Python set, and set iteration
     order over strings varies with the per-process hash seed. Because
     Portfolio.execute_trade rejects a buy it cannot afford, which
     stocks actually got bought depended on that order. The identical
     config returned +2.06% under PYTHONHASHSEED=1 and +1.65% under
     PYTHONHASHSEED=2. Fixed: the basket is now an ordered list.

  2. POSITION SIZING BY SHARE PRICE. With no risk manager the strategy
     bought a flat 100 shares of every name, so dollar exposure ranged
     from $2,529 (T) to $125,540 (LLY) on the S&P 100 and 3 names could
     not be bought at all. The basket's return tracked whichever holding
     happened to be most expensive rather than the ranking. Fixed:
     equal-dollar weighting, net of slippage and commission.

  3. TRAIN/TEST LEAKAGE. test_start == train_end, and the backtester's
     end filter was inclusive of the end date's full day, so every
     boundary day appeared in BOTH sets: 2.0% of test candles under
     Config A, 4.9% under Config B. Fixed via end_inclusive=False on
     the train segment.

  4. RUN-DATE DEPENDENCE. Windows were anchored to datetime.now(), so
     the same study drifted as the trailing 3-year window slid: S&P 100
     read +1.28% on 24 Aug and -2.69% on 8 Sep. Fixed: anchored to
     DataStorage.latest_common_timestamp().

  5. HONG KONG FEES ON US TRADES. walk_forward defaults to
     Portfolio.HK_FEE_RATE (0.16%/side, calibrated from live HK fills)
     and no US study overrode it, so US results paid 0.32% round-trip
     on trades that would cost near nothing at Moomoo US pricing. There
     are no US live fills to calibrate against, so US runs here use
     ZERO commission and are reported as a best-case bound, consistent
     with the cost-free study already in Section 4.5. A null result that
     survives zero costs is stronger than one assuming an invented rate.

Usage:
    python3 scripts/cross_sectional_corrected.py
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.optimizer import walk_forward
from backfill_sp100 import SP100_TICKERS
from backfill_hsi import HSI_CODES

CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
STRATEGIES = ['Cross-Sectional Reversal', 'Cross-Sectional Momentum']
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'

# US: no live fills exist to calibrate from, so run cost-free and report
# as a best-case bound. HK: calibrated from this project's own live fills.
MARKETS = {
    'US (S&P 100)': {'commission': 0.0, 'cost_note': 'zero (best-case bound, uncalibrated)'},
    'HK (Hang Seng Index)': {'commission': Portfolio.HK_FEE_RATE, 'cost_note': '0.16%/side (calibrated from live fills)'},
}


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def symbols_for(market):
    out = []
    if 'S&P' in market:
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
    rows, detail = [], {}
    t0 = time.time()

    for market, cfg_market in MARKETS.items():
        symbols = symbols_for(market)
        end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value)
        start = end - timedelta(days=HISTORY_DAYS)
        print(f"\n=== {market}: {len(symbols)} symbols | window {start.date()} -> {end.date()} "
              f"| commission {cfg_market['cost_note']} ===", flush=True)

        for strat in STRATEGIES:
            per_config = {}
            for cfg in CONFIGS:
                t_cfg = time.time()
                res = walk_forward(
                    strategy_name=strat, symbols=symbols, timeframe=Timeframe.HOUR_1,
                    start_date=start, end_date=end, storage=storage,
                    n_splits=cfg['n_splits'], train_pct=cfg['train_pct'],
                    objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS,
                    commission_rate=cfg_market['commission'],
                )
                s = res.get('summary', {})
                per_config[cfg['name']] = {
                    'oos_return': round(s.get('avg_oos_return', 0.0), 3),
                    'consistency': round(s.get('consistency_pct', 0.0), 1),
                    'oos_returns': [round(x, 4) for x in s.get('oos_returns', [])],
                    'windows_used': s.get('total_windows', 0),
                    'windows_skipped': len(s.get('skipped_windows', [])),
                }
                pc = per_config[cfg['name']]
                print(f"  {strat:28s} | {cfg['name']:16s} | OOS {pc['oos_return']:+7.3f}% | "
                      f"consistency {pc['consistency']:5.1f}% | used {pc['windows_used']:2d} "
                      f"skipped {pc['windows_skipped']} | {time.time()-t_cfg:.0f}s", flush=True)

            robust = all(qualifies(per_config[c['name']]['oos_return'],
                                   per_config[c['name']]['consistency']) for c in CONFIGS)
            rows.append({
                'market': market, 'strategy': strat, 'n_stocks': len(symbols),
                'window_start': str(start.date()), 'window_end': str(end.date()),
                'commission': cfg_market['cost_note'],
                'config_a_oos': per_config[CONFIGS[0]['name']]['oos_return'],
                'config_a_consistency': per_config[CONFIGS[0]['name']]['consistency'],
                'config_b_oos': per_config[CONFIGS[1]['name']]['oos_return'],
                'config_b_consistency': per_config[CONFIGS[1]['name']]['consistency'],
                'robust': robust,
            })
            detail[f'{market} | {strat}'] = per_config

    df = pd.DataFrame(rows)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    results_dir = Path(__file__).resolve().parent.parent / 'results'
    out = results_dir / f'cross_sectional_corrected_{stamp}.csv'
    df.to_csv(out, index=False)
    (results_dir / f'cross_sectional_corrected_detail_{stamp}.json').write_text(json.dumps(detail, indent=2))

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df[['market', 'strategy', 'config_a_oos', 'config_a_consistency',
              'config_b_oos', 'config_b_consistency', 'robust']].to_string(index=False))
    print(f"\n{df['robust'].sum()}/{len(df)} market-strategy pairs robust")


if __name__ == "__main__":
    main()
