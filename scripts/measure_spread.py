"""
Measure the bid-ask spread across a trading universe, to replace the
hardcoded slippage assumption with something derived from market data.

Every study in this project applies SLIPPAGE_BPS = 5.0, a round number
with no derivation behind it. Real slippage on a market order has two
parts: the spread you cross (you buy at the ask, but the backtester
prices the fill at the candle close, roughly mid) and the drift between
deciding and executing. The first is directly measurable from quotes and
is the floor for any market order, so it is what this script measures.

HALF the quoted spread is the relevant figure: crossing from mid to ask
costs half the bid-ask distance.

Limits worth stating wherever these numbers are used:
  - Snapshots sample the spread at moments, not continuously. Spreads
    widen near the open and close and during volatility, so a mid-session
    sample is a lower bound on the average.
  - This ignores market impact, which matters for large orders and which
    quotes cannot reveal.
  - It therefore establishes a FLOOR for slippage, not a full estimate.

Usage:
    python3 scripts/measure_spread.py HK [samples] [interval_seconds]
    python3 scripts/measure_spread.py US [samples] [interval_seconds]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
from moomoo import OpenQuoteContext, RET_OK

from core.config import ConfigLoader


def universe(market):
    if market == 'US':
        from backfill_sp100 import SP100_TICKERS
        codes = [f"US.{t.replace('.', '')}" for t in SP100_TICKERS]
    else:
        codes = ConfigLoader().get_live_symbols(market='HK') or []
    root = Path(__file__).resolve().parent.parent / 'data'
    return [c for c in codes if (root / c.replace('.', '_') / '1h.parquet').exists()]


def main():
    market = (sys.argv[1] if len(sys.argv) > 1 else 'HK').upper()
    n_samples = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    interval = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    syms = universe(market)
    if not syms:
        print(f"no {market} symbols with local data"); return
    print(f"{market}: {len(syms)} symbols, {n_samples} samples every {interval}s", flush=True)

    quote = OpenQuoteContext(host='127.0.0.1', port=11111)
    rows = []
    for i in range(n_samples):
        # snapshot is capped per call, so chunk the universe
        for chunk in [syms[j:j + 50] for j in range(0, len(syms), 50)]:
            ret, snap = quote.get_market_snapshot(chunk)
            if ret != RET_OK:
                print(f"  sample {i+1}: snapshot failed: {snap}", flush=True)
                continue
            for _, r in snap.iterrows():
                bid, ask = r.get('bid_price'), r.get('ask_price')
                if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
                    continue
                mid = (bid + ask) / 2
                rows.append({'sample': i + 1, 'code': r['code'], 'bid': bid, 'ask': ask,
                             'half_spread_bps': (ask - bid) / 2 / mid * 1e4})
        print(f"  sample {i+1}/{n_samples}: {len(rows)} quotes so far", flush=True)
        if i < n_samples - 1:
            time.sleep(interval)
    quote.close()

    if not rows:
        print("no usable quotes captured (market closed?)"); return

    df = pd.DataFrame(rows)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'spread_{market.lower()}_{stamp}.csv'
    df.to_csv(out, index=False)

    per_sym = df.groupby('code')['half_spread_bps'].median().sort_values()
    print(f"\nhalf-spread in bps, per symbol median ({len(per_sym)} symbols):")
    print(f"  min    {per_sym.min():6.2f}   ({per_sym.idxmin()})")
    print(f"  median {per_sym.median():6.2f}")
    print(f"  mean   {per_sym.mean():6.2f}")
    print(f"  p90    {per_sym.quantile(0.9):6.2f}")
    print(f"  max    {per_sym.max():6.2f}   ({per_sym.idxmax()})")
    print(f"\ncurrent hardcoded assumption: 5.00 bps")
    print(f"measured floor for a market order in {market}: {per_sym.median():.2f} bps (median symbol)")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
