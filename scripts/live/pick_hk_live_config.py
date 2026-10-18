"""
Choose the settings for the Hong Kong session's infrastructure test, by the same procedure as the
US forward test (scripts/live/pick_sp100_live_config.py): grid-search the most recent training
segment by Sharpe, net of Hong Kong's all-in commission and the project's slippage, and write the
winner to config/hk_forward_test.json, which config/node_hk.yaml reads.

What this is, and is not. The Hong Kong strategy search of 18 Oct 2026 (research/studies/
hk_cross_sectional.py, hk_pairs.py, hk_single_stock.py) found nothing fit to trade: the one
strategy that met the validation standard, the ML Direction Classifier on HSBC (HK.00005), is
what luck looks like across the 51 candidates of its family (White's Reality Check p = 0.79 and
0.46). It is traded on the Hong Kong paper account only to test the trading node on a second
market, during Hong Kong hours, not as an edge; the file says so in its `purpose`.

Usage:
    python scripts/live/pick_hk_live_config.py [--symbol HK.00005] [--train-days 84]
"""
import argparse
import contextlib
import io
import json
import time
from datetime import datetime, timedelta

from midfreq.backtest.backtester import Backtester
from midfreq.backtest.optimizer import _get_objective_value, generate_param_grid
from midfreq.core.candle import Timeframe
from midfreq.core.paths import repo_root
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

REPO = repo_root()
STRATEGY = 'ML Direction Classifier'
HK_COMMISSION = 0.0016      # Hong Kong all-in per side (config/venues.json, calibrated on paper fills)
HK_SLIPPAGE_BPS = 5.0       # the project's convention; the measured HK median half-spread is 3.33 bps
CAPITAL = 100_000.0         # HK$ of the paper account; one board lot of HSBC (400 shares) at HK$150


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='HK.00005')
    ap.add_argument('--train-days', type=int, default=84,
                    help="Training window length. 84 = what config A's 121-day windows train on.")
    ap.add_argument('--objective', default='sharpe_ratio')
    args = ap.parse_args()

    storage = DataStorage()
    end = storage.latest_common_timestamp([args.symbol], '1h')
    if end is None:
        print(f"No cached hourly data for {args.symbol}.")
        return
    start = end - timedelta(days=args.train_days)
    print(f"{args.symbol} | training {start.date()} -> {end.date()} ({args.train_days}d) | "
          f"commission {HK_COMMISSION*100:.2f}%/side | slippage {HK_SLIPPAGE_BPS} bps\n")

    grid = generate_param_grid(STRATEGY)
    cls = STRATEGY_REGISTRY[STRATEGY]['class']
    rows = []
    t0 = time.time()
    for params in grid:
        pf = Portfolio(initial_cash=CAPITAL, commission_rate=HK_COMMISSION)
        bt = Backtester(storage=storage, portfolio=pf, slippage_bps=HK_SLIPPAGE_BPS)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                m = bt.run(cls, symbols=[args.symbol], timeframe=Timeframe.HOUR_1,
                           start_date=start, end_date=end.date(), **params)
            except Exception:
                m = {}
        rows.append({**params, 'objective': _get_objective_value(m, args.objective) if m else 0.0,
                     'return_pct': m.get('return_pct', 0.0), 'trades': m.get('total_trades', 0)})
    print(f"  {len(grid)} configs scored ({time.time()-t0:.0f}s)")

    traded = [r for r in rows if r['trades'] > 0]
    if not traded:
        print("\nNo configuration placed a trade in this window. Nothing to deploy.")
        return
    best = max(traded, key=lambda r: r['objective'])
    names = list(STRATEGY_REGISTRY[STRATEGY]['params'])
    params = {k: best[k] for k in names}
    print(f"\nbest of {len(grid)} ({len(traded)} actually traded): {params}")
    print(f"  {args.objective}={best['objective']:.4f}  return={best['return_pct']:+.3f}%  "
          f"trades={best['trades']}")

    # a winner on the edge of the grid is a warning, not a result (see the US picker)
    warnings = []
    spec = STRATEGY_REGISTRY[STRATEGY]['params']
    for k in names:
        values = sorted({r[k] for r in rows})
        if len(values) > 1 and best[k] in (values[0], values[-1]):
            edge = 'MAXIMUM' if best[k] == values[-1] else 'MINIMUM'
            warnings.append(f"{k}={best[k]} is the grid {edge} ({values})")
    for w in warnings:
        print(f"  warning: {w}")

    out = {
        'generated_at': str(end),
        'chosen_at_wallclock': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': ("Infrastructure test of the trading node on a second market (Hong Kong paper account, "
                    "Hong Kong hours). Not a validated edge: the 18 Oct 2026 Hong Kong search found nothing "
                    "fit to trade, and this strategy's pass is consistent with luck across its family "
                    "(White's Reality Check p = 0.79 under config A, 0.46 under config B)."),
        'strategy': STRATEGY,
        'symbols': [args.symbol],
        'rationale': ('Grid-searched the most recent training segment by Sharpe, the same procedure each '
                      'walk-forward window uses and the US forward test used.'),
        'train_window': {'start': str(start), 'end': str(end), 'days': args.train_days},
        'objective': args.objective,
        'commission_per_side': HK_COMMISSION,
        'slippage_bps': HK_SLIPPAGE_BPS,
        'configs_searched': len(grid),
        'configs_that_traded': len(traded),
        'params': params,
        'boundary_warnings': warnings,
        'in_sample': {'objective': best['objective'], 'return_pct': best['return_pct'], 'trades': best['trades']},
        'capital': CAPITAL,
        'capital_rationale': ('HK$100,000 of the Hong Kong paper account, as the earlier Hong Kong paper test. '
                              'HSBC trades in board lots of 400 shares (about HK$60,000 at HK$150), so the '
                              'strategy holds one lot; the node rounds buys down to the lot.'),
    }
    path = REPO / 'config' / 'hk_forward_test.json'
    path.write_text(json.dumps(out, indent=2) + '\n')
    print(f"\nwrote {path}")


if __name__ == '__main__':
    main()
