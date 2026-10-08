"""
Add candles the live sessions failed to store to the 1h cache.

From 5 to 8 Oct 2026 every forward-test session stopped at 15:59 New York
time, a minute before the close (see run_sp100_forward_test.sh), so the
day's last candle, the half-hour one ending 16:00, was never stored. The
next session's warm-up then ranked the universe on a history with no
closing prices for those days.

This asks the broker for the same period and adds ONLY the candles the
cache lacks. Candles already stored are left exactly as they are, and a
candle whose window has not ended yet is never written, so it is safe to
run while a session is live.

Quota: one historical K-line slot per symbol that does not already hold
one. Re-requesting a symbol that holds a slot is free.

Usage:
    python3 scripts/data/fill_live_cache_gaps.py --since 2026-10-02 [--dry-run]
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))

import pandas as pd

from core.models import Timeframe
from core.storage import DataStorage


def missing_candles(existing: pd.DataFrame, fetched: pd.DataFrame, since, now_exchange) -> pd.DataFrame:
    """Rows of `fetched` that are complete, on or after `since`, and absent from `existing`.

    Candle timestamps are the exchange's wall-clock END time labelled as UTC,
    so a candle is complete once its timestamp is not later than the exchange's
    current wall-clock time, `now_exchange` (naive).
    """
    if fetched.empty:
        return fetched
    done = pd.Timestamp(now_exchange).tz_localize('UTC')
    start = pd.Timestamp(since).tz_localize('UTC')
    out = fetched[(fetched.index >= start) & (fetched.index <= done)]
    if not existing.empty:
        out = out[~out.index.isin(existing.index)]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--since', required=True, help='first day to check, YYYY-MM-DD')
    ap.add_argument('--dry-run', action='store_true', help='report what is missing, write nothing')
    ap.add_argument('--data-dir', default=str(REPO / 'data'), help='cache to fill (default: this repo)')
    ap.add_argument('--config', default=str(REPO / 'config' / 'sp100_forward_test.json'),
                    help='file whose "symbols" list is filled (default: the forward-test universe)')
    args = ap.parse_args()

    symbols = json.loads(Path(args.config).read_text())['symbols']
    market = symbols[0].split('.')[0].upper()
    tz = ZoneInfo('America/New_York' if market == 'US' else 'Asia/Hong_Kong')
    since = datetime.strptime(args.since, '%Y-%m-%d')
    storage = DataStorage(args.data_dir)

    from providers.moomoo_provider import MoomooProvider
    from moomoo import RET_OK
    provider = MoomooProvider(host='127.0.0.1', port=11111)

    def quota():
        ret, q = provider._get_context().get_history_kl_quota(get_detail=False)
        return f"{q[0]} used, {q[1]} remaining" if ret == RET_OK else f"unreadable ({q})"

    print(f"{len(symbols)} symbols | since {args.since} | cache {args.data_dir}"
          + (' | DRY RUN' if args.dry_run else ''))
    print(f"historical K-line quota before: {quota()}")

    added = filled = failed = 0
    stamps = {}
    for sym in symbols:
        folder = sym.replace('.', '_')
        try:
            fetched = provider.get_historical_data(sym, Timeframe.HOUR_1, since, datetime.now())
            if fetched.empty:
                print(f"  {sym}: nothing returned"); failed += 1
                continue
            existing = storage.load_data(folder, Timeframe.HOUR_1.value)
            new = missing_candles(existing, fetched, since, datetime.now(tz).replace(tzinfo=None))
            if len(new):
                if not args.dry_run:
                    storage.save_data(pd.concat([existing, new]).sort_index(), folder, Timeframe.HOUR_1.value)
                added += len(new); filled += 1
                for ts in new.index:
                    stamps[str(ts)[:16]] = stamps.get(str(ts)[:16], 0) + 1
        except Exception as e:
            print(f"  {sym}: ERROR {e}"); failed += 1
        time.sleep(0.6)
    provider.close()

    print(f"\n{'would add' if args.dry_run else 'added'} {added} candles to {filled} symbols; "
          f"{failed} failed; {len(symbols) - filled - failed} already complete")
    for ts in sorted(stamps):
        print(f"   {ts}  x{stamps[ts]}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
