"""
Probability of Backtest Overfitting: S&P 100 Cross-Sectional Reversal
=========================================================================
Combinatorially symmetric cross-validation (Bailey, Borwein, Lopez de
Prado and Zhu, 2017) over the strategy's whole parameter grid. The
3-year period is cut into 10 equal blocks and every configuration is
scored (Sharpe ratio) on every block. For each of the C(10,5) = 252
ways of choosing 5 blocks as in-sample, the configuration that scored
best in-sample is ranked against all the others on the remaining 5.
PBO is the share of those 252 splits in which the in-sample winner
lands in the bottom half out-of-sample, so pure noise scores 0.5.

The grid is lookback {5..30 step 5} x top_n {1..4} x rebalance
{1,7,13,19} = 96 configurations, and all of them are tested. Costs
match the US account: 0.005% per side plus 5 bps slippage.

This is the source of results/pbo_sp100_reversal_full96.json, the 0.274
(69 of 252 splits) cited in Section 4.11 of the report and in Update 12.
That figure was computed when the S&P 100 cache ended on 21 Aug 2026
(window 2023-08-22 16:00 -> 2026-08-21 16:00, 100 symbols). The cache
has been topped up since, so to reproduce it:

    python3 scripts/research/pbo_sp100_reversal.py --end-date 2026-08-21

Checked on 5 Oct 2026: that reproduces pbo and mean_logit to full
floating-point precision.

960 universe backtests in one process, about 16 minutes.

Usage:
    python3 scripts/research/pbo_sp100_reversal.py [--end-date YYYY-MM-DD] [--out PATH]
"""
import sys
import time
import json
import argparse
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))

from core.models import Timeframe
from core.storage import DataStorage
from core.overfitting import compute_pbo
from core.config import ConfigLoader

STRATEGY = 'Cross-Sectional Reversal'
N_SUBSETS = 10
MAX_CONFIGS = 96
OBJECTIVE = 'sharpe_ratio'
COMMISSION = 0.00005
SLIPPAGE_BPS = 5.0
HISTORY_DAYS = 3 * 365
MAX_STALENESS_DAYS = 3


def sp100_symbols(storage, end_date=None):
    """S&P 100 symbols with cached candles, minus any that stop short of the end.

    latest_common_timestamp takes the MINIMUM end across symbols, so one
    symbol that missed a refresh would drag the whole window back with it.
    """
    syms = ConfigLoader().get_universe('sp100', with_data_only=True)
    last = {s: storage.latest_common_timestamp([s], Timeframe.HOUR_1.value, as_of=end_date)
            for s in syms}
    last = {s: d for s, d in last.items() if d is not None}
    freshest = max(last.values())
    keep = [s for s in syms if s in last and (freshest - last[s]).days <= MAX_STALENESS_DAYS]
    return keep, sorted(set(syms) - set(keep))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--end-date', default=None,
                    help="re-run the study as it stood when the cache ended on this day "
                         "(YYYY-MM-DD); 2026-08-21 reproduces the report's figure")
    ap.add_argument('--out', default=None,
                    help='JSON to write (default results/pbo_sp100_reversal_<stamp>.json)')
    args = ap.parse_args()

    storage = DataStorage(str(REPO / 'data'))
    symbols, dropped = sp100_symbols(storage, args.end_date)
    end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value, as_of=args.end_date)
    start = end - timedelta(days=HISTORY_DAYS)
    print(f"S&P 100 {STRATEGY} | {len(symbols)} symbols | {start} -> {end} | "
          f"{N_SUBSETS} blocks, up to {MAX_CONFIGS} configs"
          + (f" | dropped as stale: {', '.join(dropped)}" if dropped else ''), flush=True)

    t0 = time.time()
    r = compute_pbo(strategy_name=STRATEGY, symbols=symbols, timeframe=Timeframe.HOUR_1,
                    start_date=start, end_date=end, storage=storage, n_subsets=N_SUBSETS,
                    objective=OBJECTIVE, max_configs=MAX_CONFIGS, slippage_bps=SLIPPAGE_BPS,
                    commission_rate=COMMISSION, progress=True)
    below = round(r['pbo'] * r['n_trials'])
    print(f"\n  configs tested : {r['params_tested']}")
    print(f"  PBO            : {r['pbo']:.3f}  ({below} of {r['n_trials']} splits below the median)")
    print(f"  mean logit     : {r['mean_logit']:+.3f}")
    print(f"  elapsed        : {(time.time() - t0) / 60:.1f} min")

    out = Path(args.out) if args.out else (
        REPO / 'results' / f"pbo_sp100_reversal_{time.strftime('%Y%m%d_%H%M%S')}.json")
    r.update({'strategy': STRATEGY, 'universe': 'sp100', 'n_symbols': len(symbols),
              'dropped': dropped, 'start': str(start), 'end': str(end),
              'end_date_arg': args.end_date, 'objective': OBJECTIVE,
              'commission_rate': COMMISSION, 'slippage_bps': SLIPPAGE_BPS})
    out.write_text(json.dumps(r, indent=2))
    print(f"  saved          : {out}")


if __name__ == '__main__':
    main()
