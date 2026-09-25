"""
Top up the S&P 100 1h cache with only the candles that are missing.

backfill_sp100.py re-pulls 3 full years per symbol, which is the right
thing for a cold start and the wrong thing for a top-up: the cache
already holds history to 2026-08-21 and only the tail is missing.

The historical K-line quota is charged per distinct SYMBOL, not per
candle, so a top-up costs the same quota as a full pull. What it saves
is time and transfer, and it makes the quota ceiling explicit: the run
stops cleanly when slots run out rather than failing symbol by symbol
with the same opaque error.

Usage:
    python3 scripts/refresh_sp100_incremental.py [--dry-run] [--limit N]
"""
import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from providers.moomoo_provider import MoomooProvider

REPO = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='report the gap per symbol, pull nothing')
    ap.add_argument('--limit', type=int, default=None, help='cap how many symbols to refresh')
    args = ap.parse_args()

    tickers = ConfigLoader().describe_universe('sp100')['tickers']
    symbols = [f"US.{t.replace('.', '')}" for t in tickers]
    storage = DataStorage()
    now = datetime.now()

    # What is actually missing, per symbol
    plan = []
    for s in symbols:
        d = s.replace('.', '_')
        p = REPO / 'data' / d / '1h.parquet'
        if not p.exists():
            plan.append((s, d, None, 'no cache'))
            continue
        try:
            import pandas as pd
            last = pd.read_parquet(p).index.max()
            gap = (now - last.tz_localize(None) if last.tzinfo else now - last).days
            plan.append((s, d, last, f'{gap}d behind'))
        except Exception as e:
            plan.append((s, d, None, f'unreadable: {e}'))

    stale = [x for x in plan if x[2] is None or (now - (x[2].tz_localize(None) if x[2].tzinfo else x[2])).days >= 1]
    print(f"{len(symbols)} symbols declared, {len(stale)} need a top-up")
    if args.dry_run:
        for s, _, last, note in plan[:12]:
            print(f"   {s:12s} last={str(last)[:16] or '—':16s} {note}")
        print("   ...")
        return

    provider = MoomooProvider(host='127.0.0.1', port=11111)

    # Ask the broker how many distinct-symbol slots are left before starting,
    # so the run is bounded by the real ceiling instead of discovering it.
    try:
        from moomoo import RET_OK
        ret, q = provider.ctx.get_history_kl_quota(get_detail=False)
        used, remain = (q[0], q[1]) if ret == RET_OK else (None, None)
        print(f"historical K-line quota: {used} used, {remain} remaining")
    except Exception as e:
        remain = None
        print(f"could not read quota ({e}); proceeding without a ceiling")

    todo = stale[:args.limit] if args.limit else stale
    if remain is not None and len(todo) > remain:
        print(f"capping this run at {remain} symbols to stay inside the quota "
              f"({len(todo) - remain} will remain stale)")
        todo = todo[:remain]

    ok = fail = 0
    for i, (s, d, last, _) in enumerate(todo, 1):
        start = (last - timedelta(hours=2)) if last is not None else now - timedelta(days=3 * 365)
        if getattr(start, 'tzinfo', None):
            start = start.tz_localize(None)
        try:
            df = provider.get_historical_data(s, Timeframe.HOUR_1, start, now)
            if df.empty:
                print(f"[{i}/{len(todo)}] {s}: nothing returned")
                fail += 1
            else:
                storage.append_data(df, d, Timeframe.HOUR_1.value)
                print(f"[{i}/{len(todo)}] {s}: +{len(df)} candles -> {str(df.index.max())[:16]}")
                ok += 1
        except Exception as e:
            print(f"[{i}/{len(todo)}] {s}: ERROR {e}")
            fail += 1
        time.sleep(1.2)

    provider.close()
    print(f"\ntopped up {ok}, failed {fail}, still stale {len(stale) - len(todo)}")


if __name__ == '__main__':
    main()
