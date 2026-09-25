"""
Choose the parameters to trade live, using the same procedure the
walk-forward study uses at every window.

The study never commits to one configuration: each window grid-searches
its own training segment and trades whatever that segment selected. So
there is no single "the winning config" to read off the results — the
top_n alone moved between 1 and 2 across windows.

Going live is just the next window. This grid-searches the most recent
training segment (the same 84 days config A trains on) by Sharpe ratio,
net of the real US commission and the measured half-spread, and writes
the winner to config/sp100_forward_test.json.

Writing it to a file rather than passing it on a command line means the
parameters actually being traded are recorded where the dashboard and
the report can both cite them, and are diffable if they change.

Usage:
    python3 scripts/pick_sp100_live_config.py [--train-days 84]
"""
import argparse
import io
import json
import contextlib
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.backtester import Backtester
from core.config import ConfigLoader
from core.strategy import STRATEGY_REGISTRY
from core.optimizer import generate_param_grid, _get_objective_value

REPO = Path(__file__).resolve().parent.parent
STRATEGY = 'Cross-Sectional Reversal'
US_COMMISSION = 0.00005     # moomoo SG: flat US$0.99 + 9% GST, as a fraction
US_SLIPPAGE_BPS = 5.0       # half-spread; HK measured 3.33 bps median, 5.0 is conservative


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--train-days', type=int, default=84,
                    help="Training window length. 84 = what config A's 121-day windows train on.")
    ap.add_argument('--objective', default='sharpe_ratio')
    ap.add_argument('--max-staleness', type=int, default=3,
                    help='Drop symbols whose last candle is more than this many days '
                         'behind the freshest symbol.')
    args = ap.parse_args()

    tickers = ConfigLoader().describe_universe('sp100')['tickers']
    cached = [f"US.{t.replace('.', '')}" for t in tickers
              if (REPO / 'data' / f"US_{t.replace('.', '')}" / '1h.parquet').exists()]

    # Require currency, not just presence. latest_common_timestamp takes the
    # MINIMUM across symbols, so two symbols that missed a refresh drag the
    # whole training window back to their stale end date — here WMT and XOM
    # ran out of historical quota and would have anchored an otherwise
    # current universe three weeks into the past. Drop anything that is not
    # within --max-staleness days of the freshest symbol instead.
    import pandas as pd
    last_seen = {}
    for s in cached:
        try:
            last_seen[s] = pd.read_parquet(REPO / 'data' / s.replace('.', '_') / '1h.parquet').index.max()
        except Exception:
            pass
    if not last_seen:
        print("No cached S&P 100 data at all. Run scripts/refresh_sp100_incremental.py first.")
        return
    freshest = max(last_seen.values())
    symbols = [s for s, d in last_seen.items()
               if (freshest - d).days <= args.max_staleness]
    dropped = sorted(set(last_seen) - set(symbols))
    if dropped:
        print(f"excluding {len(dropped)} stale symbol(s): {', '.join(dropped)}")
        for s in dropped:
            print(f"   {s} last candle {str(last_seen[s])[:16]}, "
                  f"{(freshest - last_seen[s]).days}d behind")

    storage = DataStorage()
    end = storage.latest_common_timestamp(symbols, '1h')
    start = end - timedelta(days=args.train_days)
    print(f"{len(symbols)} symbols | training {start.date()} -> {end.date()} "
          f"({args.train_days}d) | commission {US_COMMISSION*100:.4f}%/side | "
          f"slippage {US_SLIPPAGE_BPS} bps\n")

    grid = generate_param_grid(STRATEGY)
    cls = STRATEGY_REGISTRY[STRATEGY]['class']
    rows = []
    t0 = time.time()
    for i, params in enumerate(grid, 1):
        pf = Portfolio(initial_cash=100_000.0, commission_rate=US_COMMISSION)
        bt = Backtester(storage=storage, portfolio=pf, slippage_bps=US_SLIPPAGE_BPS)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                m = bt.run(cls, symbols=symbols, timeframe=Timeframe.HOUR_1,
                           start_date=start, end_date=end, end_inclusive=False, **params)
            except Exception:
                m = {}
        obj = _get_objective_value(m, args.objective) if m else 0.0
        rows.append({**params, 'objective': obj,
                     'return_pct': m.get('return_pct', 0.0),
                     'trades': m.get('total_trades', 0)})
        if i % 12 == 0 or i == len(grid):
            print(f"  {i}/{len(grid)} configs scored ({time.time()-t0:.0f}s)", flush=True)

    traded = [r for r in rows if r['trades'] > 0]
    if not traded:
        print("\nNo configuration placed a trade in this window. Nothing to deploy.")
        return
    best = max(traded, key=lambda r: r['objective'])

    print(f"\nbest of {len(grid)} ({len(traded)} actually traded):")
    print(f"  lookback={best['lookback']} top_n={best['top_n']} "
          f"rebalance_every={best['rebalance_every']}")
    print(f"  {args.objective}={best['objective']:.4f}  "
          f"return={best['return_pct']:+.3f}%  trades={best['trades']}")

    top = sorted(traded, key=lambda r: -r['objective'])[:5]
    print("\n  top 5:")
    for r in top:
        print(f"    lookback={r['lookback']:>2} top_n={r['top_n']} rebal={r['rebalance_every']:>2}  "
              f"sharpe={r['objective']:>7.4f}  return={r['return_pct']:>+7.3f}%  trades={r['trades']:>3}")

    # A winner sitting on the edge of the grid is a warning, not a result:
    # it means the search wanted to go further than the grid allowed, so the
    # "best" value is an artefact of where the grid stops rather than an
    # optimum. Record it, because it is the kind of thing that is obvious in
    # the moment and invisible three weeks later.
    from core.strategy import STRATEGY_REGISTRY as _REG
    _spec = _REG[STRATEGY]['params']
    boundary = []
    for k, v in _spec.items():
        vals = list(range(v['min'], v['max'] + 1, v['step']))
        if best[k] == vals[0]:
            boundary.append(f"{k}={best[k]} is the grid MINIMUM ({vals})")
        elif best[k] == vals[-1]:
            boundary.append(f"{k}={best[k]} is the grid MAXIMUM ({vals})")
    if boundary:
        print("\n  WARNING — the selected config sits on the edge of the search grid:")
        for b in boundary:
            print(f"    {b}")
        print("    The optimum may lie outside the grid, so this value reflects where the")
        print("    grid stops as much as what the data prefers.")

    out = {
        'generated_at': str(storage.latest_common_timestamp(symbols, '1h')),
        'boundary_warnings': boundary,
        'excluded_stale_symbols': dropped,
        'chosen_at_wallclock': time.strftime('%Y-%m-%d %H:%M:%S'),
        'strategy': STRATEGY,
        'universe': 'sp100',
        'rationale': ('Grid-searched the most recent training segment by Sharpe, the same procedure '
                      'each walk-forward window uses. Going live is the next window.'),
        'train_window': {'start': str(start), 'end': str(end), 'days': args.train_days},
        'objective': args.objective,
        'commission_per_side': US_COMMISSION,
        'slippage_bps': US_SLIPPAGE_BPS,
        'configs_searched': len(grid),
        'configs_that_traded': len(traded),
        'params': {'lookback': int(best['lookback']), 'top_n': int(best['top_n']),
                   'rebalance_every': int(best['rebalance_every'])},
        'in_sample': {'objective': best['objective'], 'return_pct': best['return_pct'],
                      'trades': best['trades']},
        'symbols': symbols,
        'caveat': ('In-sample numbers above are what the search selected on, not a forecast. '
                   'The validated out-of-sample expectation for this combination is '
                   '+5.726%/window (config A) and +0.632%/window (config B) at this fee.'),
    }
    path = REPO / 'config' / 'sp100_forward_test.json'
    path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {path.relative_to(REPO)}")


if __name__ == '__main__':
    main()
