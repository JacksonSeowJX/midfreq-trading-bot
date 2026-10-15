"""
In-sample vs out-of-sample walk-forward, imported into the run store
=====================================================================

The alpha study's walk-forward (alpha_vs_market.py: the S&P 100,
cross-sectional reversal, 96 settings searched in every window, 70/30
train/test, highest Sharpe ratio chosen, 0.005% per side + 5 bps, history
ending 21 Aug 2026, Config A (9 windows) and Config B (15)) was re-run on
16 Oct 2026 keeping each window's training metrics as well as its test
metrics, and written to one JSON file: per configuration, the windows (dates,
chosen params, train_metrics, test_metrics) and the walk-forward summary.

This saves each configuration as one walk_forward run (source "script") in
the run store (midfreq.backtest.runstore), so the in-sample and out-of-sample
numbers of every window can be listed, queried and promoted like any other
run. The file keeps six metrics per segment (return_pct, sharpe_ratio,
max_drawdown, win_rate, total_trades, profit_factor), so the windows table has
those six with the is_ and oos_ prefixes.

The file does not hold the symbols or the full date range, so they are rebuilt
here the way the re-run built them (the S&P 100 symbols with cached candles,
anchored to the cache as of 21 Aug 2026) and checked against the windows'
dates: an import whose dates do not match stops before saving anything. The
data cut-off is the last cached candle of the last test segment. Reads the
JSON and data/; writes only to the run store. Running it again saves nothing
new (a configuration already imported from the same file is skipped).

Usage:
    uv run python research/studies/import_is_vs_oos.py PATH/TO/is_vs_oos.json [--store runs/]
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import alpha_vs_market as av
from midfreq.backtest.optimizer import generate_param_grid
from midfreq.backtest.runstore import default_run_store, record_from_walk_forward, run_store_for
from midfreq.core.candle import Timeframe
from midfreq.data.storage import DataStorage

END_DATE = '2026-08-21'               # the cache date the alpha study anchors to
UNIVERSE = 'sp100'
CAPITAL = 100_000.0                   # walk_forward's default, which the re-run used


def window_bounds(storage, symbols):
    """(start, end) exactly as is_vs_oos.py computed them."""
    end = storage.latest_common_timestamp(symbols, Timeframe.HOUR_1.value, as_of=END_DATE)
    return end - timedelta(days=av.HISTORY_DAYS), end


def data_cutoff(storage, symbols, last_test_end):
    """The last cached candle the last test segment used (test segments include their end day)."""
    bound = pd.Timestamp(last_test_end, tz='UTC') + pd.Timedelta(days=1)
    last = None
    for s in symbols:
        idx = storage.load_data(s.replace('.', '_'), Timeframe.HOUR_1.value).index
        idx = idx[idx < bound]
        if len(idx):
            last = idx.max() if last is None else max(last, idx.max())
    return last


def windows_of(raw):
    """The file's windows in WalkForwardWindow's field names."""
    return [{'window_id': w['window'], 'train_start': w['train'][0], 'train_end': w['train'][1],
             'test_start': w['test'][0], 'test_end': w['test'][1], 'best_params': w['params'],
             'train_metrics': w['train_metrics'], 'test_metrics': w['test_metrics']} for w in raw]


def already_imported(store, sha, config):
    for run_id in store.list(kind='walk_forward', strategy=av.STRATEGY, limit=None)['run_id']:
        meta = store.load(run_id).meta
        if meta.get('imported_sha256') == sha and meta.get('config') == config:
            return run_id
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('path', type=Path, help='the JSON is_vs_oos.py wrote')
    parser.add_argument('--store', help='a folder or gs://bucket/prefix (default: MIDFREQ_RUNS, else <repo>/runs)')
    args = parser.parse_args()

    data = json.loads(args.path.read_text())
    sha = hashlib.sha256(args.path.read_bytes()).hexdigest()
    finished = datetime.fromtimestamp(args.path.stat().st_mtime, tz=timezone.utc)
    store = run_store_for(args.store) if args.store else default_run_store()

    storage = DataStorage(str(av.REPO / 'data'))
    symbols, _ = av.sp100_symbols(storage, END_DATE)
    start, end = window_bounds(storage, symbols)
    n_splits = dict(av.CONFIGS)
    grid_size = len(generate_param_grid(av.STRATEGY))
    grid_size = min(grid_size, 200)                 # walk_forward samples a larger grid down to 200

    records = []
    for config, result in data.items():
        windows = windows_of(result['windows'])
        if pd.Timestamp(windows[0]['train_start']) != pd.Timestamp(start):
            raise SystemExit(f"{config}: the file's first window starts {windows[0]['train_start']}, "
                             f"but the S&P 100 window rebuilt here starts {start}; not imported")
        if len(windows) + len(result['summary'].get('skipped_windows') or []) != n_splits[config]:
            raise SystemExit(f"{config}: {len(windows)} windows, expected {n_splits[config]}; not imported")
        record = record_from_walk_forward(
            {'windows': windows, 'summary': result['summary']}, strategy=av.STRATEGY, symbols=symbols,
            timeframe=Timeframe.HOUR_1, start=start, end=end, objective=av.OBJECTIVE, n_splits=n_splits[config],
            train_pct=av.TRAIN_PCT, initial_capital=CAPITAL, commission_rate=av.RATE,
            slippage_bps=av.SLIPPAGE_BPS, param_grid_size=grid_size,
            data_cutoff=data_cutoff(storage, symbols, windows[-1]['test_end']), universe=UNIVERSE, source='script',
            meta={'study': 'alpha_vs_market walk-forward, re-run keeping the training metrics (is_vs_oos.py)',
                  'config': config, 'as_of': END_DATE, 'seconds': result.get('seconds'),
                  'imported_from': args.path.name, 'imported_sha256': sha,
                  'metrics_kept': sorted(windows[0]['test_metrics'])})
        record.created = finished                 # when the walk-forward finished, not when it was imported
        records.append((config, record))

    for config, record in records:
        done = already_imported(store, sha, config)
        if done:
            print(f'{config}: already imported as {done}')
            continue
        run_id = store.save(record)
        m = record.metrics
        efficiency = 'undefined' if m['wf_efficiency'] is None else f"{m['wf_efficiency']:.2f}"
        print(f"{config}: saved {run_id} | avg train {m['avg_train_return']:+.2f}% | "
              f"avg OOS {m['avg_oos_return']:+.2f}% | consistency {m['consistency_pct']:.0f}% | "
              f"efficiency {efficiency}")


if __name__ == '__main__':
    main()
