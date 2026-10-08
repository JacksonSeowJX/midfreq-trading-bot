"""The day's last candle (16:00 New York) must be counted and stored.

From 5 to 8 Oct 2026 the launcher stopped every session at 15:59, so the
16:00 candle was dropped each day: the rebalance clock advanced 6 a day
instead of 7 and the cache held no closing prices. The replay harness drives
its own clock, so it never saw the launcher's real stop time.
"""
import importlib.util
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('start', ['2026-10-09T09:35:00', '2026-10-09T09:35:01',
                                   '2026-10-09T09:35:59', '2026-10-09T09:59:59',
                                   '2026-11-02T09:35:01'])       # last one: after US clocks change
def test_scheduled_session_outlasts_the_close(start):
    env = dict(os.environ, FAKE_ET=start, DRY_RUN='1', PYTHON=sys.executable)
    out = subprocess.run(['bash', 'scripts/live/run_sp100_forward_test.sh', '--scheduled'],
                         cwd=REPO, env=env, capture_output=True, text=True, timeout=60).stdout
    minutes = int(re.search(r'--duration (\d+)', out).group(1))
    t0 = datetime.fromisoformat(start)
    end = t0 + timedelta(minutes=minutes)
    close = t0.replace(hour=16, minute=0, second=0)
    assert close + timedelta(minutes=1) <= end <= close + timedelta(minutes=2), out


def _gapfill():
    spec = importlib.util.spec_from_file_location(
        'fill_live_cache_gaps', REPO / 'scripts' / 'data' / 'fill_live_cache_gaps.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bars(stamps, close):
    idx = pd.DatetimeIndex(pd.to_datetime(stamps, utc=True), name='timestamp')
    return pd.DataFrame({'open': close, 'high': close, 'low': close, 'close': close,
                         'volume': 1.0}, index=idx)


def test_gap_fill_adds_only_missing_completed_candles():
    day = ['2026-10-07 10:30', '2026-10-07 11:30', '2026-10-07 12:30', '2026-10-07 13:30',
           '2026-10-07 14:30', '2026-10-07 15:30']
    existing = _bars(['2026-10-01 16:00'] + day, close=100.0)
    fetched = _bars(['2026-10-01 16:00'] + day + ['2026-10-07 16:00', '2026-10-08 10:30',
                                                 '2026-10-08 11:30'], close=999.0)
    new = _gapfill().missing_candles(existing, fetched, since=datetime(2026, 10, 2),
                                     now_exchange=datetime(2026, 10, 8, 11, 5))
    # The dropped 16:00 close and the finished 10:30 candle come back; the
    # 11:30 candle is still forming at 11:05, and stored candles are not replaced.
    assert [str(t)[:16] for t in new.index] == ['2026-10-07 16:00', '2026-10-08 10:30']


def test_gap_fill_ignores_candles_before_since():
    existing = _bars(['2026-10-07 10:30'], close=100.0)
    fetched = _bars(['2026-09-30 16:00', '2026-10-07 10:30', '2026-10-07 16:00'], close=1.0)
    new = _gapfill().missing_candles(existing, fetched, since=datetime(2026, 10, 2),
                                     now_exchange=datetime(2026, 10, 9, 9, 0))
    assert [str(t)[:16] for t in new.index] == ['2026-10-07 16:00']
