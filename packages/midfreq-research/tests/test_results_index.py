"""midfreq.research.results_index stacks every two-configuration study CSV into one table."""
import os

import pandas as pd

from midfreq.research import results_index as ri


def _csv(d, name, rows, mtime):
    p = d / name
    pd.DataFrame(rows).to_csv(p, index=False)
    os.utime(p, (mtime, mtime))


def _row(sym, a, b, ca=60.0, cb=60.0):
    return {'symbol': sym, 'config_a_oos': a, 'config_a_consistency': ca,
            'config_b_oos': b, 'config_b_consistency': cb}


def test_only_the_newest_run_of_each_study_is_read_and_robust_rows_sort_first(tmp_path):
    _csv(tmp_path, 'buy_hold_benchmark_20260101_120000.csv', [_row('HK.00700', 9.0, 9.0)], 1_000)
    _csv(tmp_path, 'buy_hold_benchmark_20260201_120000.csv',
         [_row('HK.00700', -1.0, 2.0), _row('HK.00005', 3.0, 4.0)], 2_000)
    _csv(tmp_path, 'notes_20260101.csv', [{'x': 1}], 2_000)        # not a two-configuration result
    d = ri.load_all(results_dir=tmp_path)
    assert list(d.subject) == ['HK.00005', 'HK.00700']
    assert list(d.robust) == [True, False] and set(d.file) == {'buy_hold_benchmark_20260201_120000.csv'}
    assert d.family.iloc[0] == 'Buy & Hold benchmark'


def test_superseded_studies_are_left_out_unless_asked_for(tmp_path):
    _csv(tmp_path, 'cross_sectional_sp100_20260101_120000.csv', [_row('US.SP100', 1.0, 1.0)], 1_000)
    assert ri.load_all(results_dir=tmp_path).empty
    assert len(ri.load_all(current_only=False, results_dir=tmp_path)) == 1
