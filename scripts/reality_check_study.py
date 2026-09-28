"""
Apply White's Reality Check to the selection this project actually made.

Update 12 reported that cross-sectional reversal on the S&P 100 passed the
two-configuration standard, survived the real broker fee, and scored a PBO
of 0.274. It also conceded the one thing none of those tests address: the
result was picked from roughly two dozen strategy-universe pairs, and
finding one winner out of two dozen is close to what chance alone produces.

This is that test. Each candidate is one (strategy, universe) pair taken
through the same walk-forward, and the Reality Check asks whether the best
of them beats what the best of an equal number of edgeless candidates
would score.

Per-window returns are required, not just the means the result CSVs kept,
because the bootstrap resamples windows. So the walk-forwards are re-run.

Both configurations are tested separately, as everywhere else in this
project: config A is 9 windows, config B is 15.

Usage:
    python3 scripts/reality_check_study.py [--n-boot 5000] [--quick]
"""
import argparse
import contextlib
import io
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.config import ConfigLoader
from core.optimizer import walk_forward
from core.reality_check import reality_check, naive_single_test

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / 'results'

# One candidate per (strategy, universe). Fees follow the market, as
# established by calibration: HK 0.16%/side built up from the published
# schedule and since confirmed against a real paper fill at 0.17771% on a
# smaller trade; US 0.005%/side from moomoo SG's flat US$0.99 + 9% GST.
US_FEE, HK_FEE = 0.00005, Portfolio.HK_FEE_RATE
CANDIDATES = [
    ('Cross-Sectional Reversal', 'sp100',   US_FEE),
    ('Cross-Sectional Reversal', 'us_15',   US_FEE),
    ('Cross-Sectional Reversal', 'hsi',     HK_FEE),
    ('Cross-Sectional Reversal', 'hk_live', HK_FEE),
    ('Cross-Sectional Momentum', 'sp100',   US_FEE),
    ('Cross-Sectional Momentum', 'us_15',   US_FEE),
    ('Cross-Sectional Momentum', 'hsi',     HK_FEE),
    ('Cross-Sectional Momentum', 'hk_live', HK_FEE),
]
CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
SLIPPAGE_BPS = 5.0
MAX_STALENESS_DAYS = 3


def universe_symbols(cfg, name):
    """Cached symbols for a universe, dropping any that are badly stale.

    latest_common_timestamp takes the MINIMUM end date across symbols, so a
    single symbol that missed a refresh drags the whole study window back.
    """
    tickers = cfg.describe_universe(name).get('tickers')
    if tickers:
        syms = [f"US.{t.replace('.', '')}" if not t.startswith(('HK.', 'US.')) else t
                for t in tickers]
    else:
        syms = cfg.get_universe(name, with_data_only=True)
    last = {}
    for s in syms:
        p = REPO / 'data' / s.replace('.', '_') / '1h.parquet'
        if p.exists():
            try:
                last[s] = pd.read_parquet(p).index.max()
            except Exception:
                pass
    if not last:
        return [], []
    freshest = max(last.values())
    keep = [s for s, d in last.items() if (freshest - d).days <= MAX_STALENESS_DAYS]
    dropped = sorted(set(last) - set(keep))
    return keep, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-boot', type=int, default=5000)
    ap.add_argument('--quick', action='store_true',
                    help='only the 4 reversal candidates, for a fast sanity run')
    ap.add_argument('--end-date', default=None,
                    help="Common study end (YYYY-MM-DD) for EVERY candidate. Without it each "
                         "universe ends at its own newest candle, and the US universes are "
                         "4 weeks fresher than the HK ones because the historical quota was "
                         "spent on US symbols. That breaks the test: the bootstrap resamples "
                         "window indices PAIRED across candidates, so window 3 has to mean the "
                         "same calendar period for all of them, and a candidate measured over a "
                         "more favourable stretch would gain an unearned advantage.")
    args = ap.parse_args()

    cfg = ConfigLoader()
    storage = DataStorage()
    cands = [c for c in CANDIDATES if not args.quick or 'Reversal' in c[0]]

    print(f"White's Reality Check over {len(cands)} strategy-universe candidates\n")
    per_config = {name: {} for name, _ in CONFIGS}
    meta = {}
    t_all = time.time()

    for strategy, uni, fee in cands:
        syms, dropped = universe_symbols(cfg, uni)
        if len(syms) < 3:
            print(f"  SKIP {strategy} x {uni}: only {len(syms)} symbols with data")
            continue
        end = storage.latest_common_timestamp(syms, '1h')
        if args.end_date:
            forced = pd.Timestamp(args.end_date)
            if end.tzinfo is not None:
                forced = forced.tz_localize(end.tzinfo)
            if forced > end:
                print(f"  SKIP {strategy} x {uni}: data ends {end.date()}, "
                      f"before the requested common end {forced.date()}")
                continue
            end = forced
        start = end - timedelta(days=3 * 365)
        label = f"{strategy.replace('Cross-Sectional ', 'CS-')} x {uni}"
        print(f"  {label}: {len(syms)} symbols, {start.date()} -> {end.date()}, "
              f"fee {fee*100:.3f}%/side"
              + (f", dropped {len(dropped)} stale" if dropped else ""), flush=True)

        for cname, n_splits in CONFIGS:
            t0 = time.time()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    r = walk_forward(strategy_name=strategy, symbols=syms,
                                     timeframe=Timeframe.HOUR_1, start_date=start,
                                     end_date=end, storage=storage, n_splits=n_splits,
                                     train_pct=0.7, objective='sharpe_ratio',
                                     slippage_bps=SLIPPAGE_BPS, commission_rate=fee)
                except Exception as e:
                    print(f"      {cname}: FAILED {e}", flush=True)
                    continue
            s = r.get('summary', {})
            oos = s.get('oos_returns') or []
            if len(oos) < 4:
                print(f"      {cname}: only {len(oos)} windows, unusable", flush=True)
                continue
            per_config[cname][label] = oos
            meta[f"{label} | {cname}"] = {
                'n_symbols': len(syms), 'dropped_stale': dropped,
                'mean_oos': s.get('avg_oos_return'),
                'consistency': s.get('consistency_pct'),
                'windows': len(oos),
            }
            print(f"      {cname}: mean {s.get('avg_oos_return', 0):+7.3f}% "
                  f"consistency {s.get('consistency_pct', 0):5.1f}% "
                  f"({len(oos)} windows, {time.time()-t0:.0f}s)", flush=True)

    out = {'candidates_run': len(cands), 'slippage_bps': SLIPPAGE_BPS,
           'common_end_date': args.end_date,
           'n_boot': args.n_boot, 'meta': meta, 'by_config': {}}

    for cname, _ in CONFIGS:
        cands_c = per_config[cname]
        if len(cands_c) < 2:
            print(f"\n{cname}: only {len(cands_c)} usable candidate(s), cannot test a selection")
            continue
        # every candidate must share a window count for a paired resample
        counts = {}
        for k, v in cands_c.items():
            counts.setdefault(len(v), []).append(k)
        n_use = max(counts, key=lambda n: len(counts[n]))
        use = {k: cands_c[k] for k in counts[n_use]}
        excluded = [k for k in cands_c if k not in use]

        rc = reality_check(use, n_boot=args.n_boot)
        naive = naive_single_test(use[rc['best_candidate']], n_boot=args.n_boot)

        print(f"\n{'='*66}\n{cname}: {len(use)} candidates, {n_use} windows each")
        if excluded:
            print(f"  excluded (different window count): {excluded}")
        for k, m in sorted(rc['candidate_means'].items(), key=lambda kv: -kv[1]):
            mark = '  <- best' if k == rc['best_candidate'] else ''
            print(f"    {k:34s} {m:+7.3f}%{mark}")
        print(f"\n  best                       : {rc['best_candidate']} ({rc['observed_max_mean']:+.3f}%)")
        print(f"  naive p (winner alone)     : {naive:.4f}")
        print(f"  Reality Check p            : {rc['p_value']:.4f}")
        q = rc['bootstrap_max_quantiles']
        print(f"  what the best of {len(use)} edgeless candidates scores:")
        print(f"     median {q['p50']:+.3f}%   90th {q['p90']:+.3f}%   95th {q['p95']:+.3f}%")
        verdict = ("the selection found something beyond chance"
                   if rc['p_value'] < 0.05 else
                   "indistinguishable from what searching this many candidates produces")
        print(f"  verdict                    : {verdict}")
        out['by_config'][cname] = {**rc, 'naive_p_value': naive,
                                   'excluded': excluded,
                                   'per_window': use}

    stamp = time.strftime('%Y%m%d_%H%M%S')
    p = RESULTS / f'reality_check_{stamp}.json'
    p.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {p.relative_to(REPO)}  (total {(time.time()-t_all)/60:.1f} min)")


if __name__ == '__main__':
    main()
