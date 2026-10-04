"""
Seed the Reality Check cache from a completed study JSON.

The cache was added while a study was already running, so that run cannot
write to it. Its HK candidates are nonetheless computed over exactly the
window an aligned re-run needs (2026-08-28, since HK data was never
refreshed), and recomputing them would cost about an hour for nothing.

This reads a reality_check_*.json and writes its per-window returns into
the cache under the same key the study builds, so the aligned re-run skips
them and only recomputes the US candidates whose window actually changes.

Usage:
    python3 scripts/tools/seed_rc_cache.py results/reality_check_XXXX.json \
        --only hsi hk_live --end 2026-08-28
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / 'results' / 'reality_check_cache.json'

# must match _cache_key() in reality_check_study.py
FEES = {'sp100': 5e-05, 'us_15': 5e-05, 'hsi': 0.0016, 'hk_live': 0.0016}
LABEL_TO_STRATEGY = {'CS-Reversal': 'Cross-Sectional Reversal',
                     'CS-Momentum': 'Cross-Sectional Momentum'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('study_json')
    ap.add_argument('--only', nargs='*', default=None,
                    help='universes to seed (default: all present)')
    ap.add_argument('--end', required=True, help='study end date the entries were computed at')
    args = ap.parse_args()

    data = json.loads(Path(args.study_json).read_text())
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    meta_all = data.get('meta', {})
    added = skipped = 0

    for cname, block in data.get('by_config', {}).items():
        for label, oos in block.get('per_window', {}).items():
            # label looks like "CS-Reversal x sp100"
            try:
                strat_short, uni = [s.strip() for s in label.split(' x ')]
            except ValueError:
                skipped += 1; continue
            if args.only and uni not in args.only:
                skipped += 1; continue
            strategy = LABEL_TO_STRATEGY.get(strat_short)
            if strategy is None or uni not in FEES:
                skipped += 1; continue
            m = meta_all.get(f"{label} | {cname}", {})
            n_syms = m.get('n_symbols')
            if not n_syms:
                skipped += 1; continue
            # start is end minus 3 years, the same arithmetic the study uses
            import datetime as dt
            end_d = dt.date.fromisoformat(args.end)
            start_d = end_d - dt.timedelta(days=3 * 365)
            key = (f"{strategy}|{uni}|{cname}|{start_d.isoformat()}|{args.end}|"
                   f"{FEES[uni]}|{n_syms}")
            cache[key] = {'oos_returns': oos,
                          'meta': {**m, 'start': start_d.isoformat(), 'end': args.end,
                                   'seeded_from': Path(args.study_json).name}}
            added += 1
            print(f"  seeded {label} | {cname}  ({len(oos)} windows)")

    CACHE.write_text(json.dumps(cache, indent=1))
    print(f"\n{added} entries seeded, {skipped} skipped. Cache now holds {len(cache)}.")


if __name__ == '__main__':
    sys.exit(main())
