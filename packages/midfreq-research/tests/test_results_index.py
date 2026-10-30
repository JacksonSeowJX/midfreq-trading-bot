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
    _csv(tmp_path, 'buy_hold_benchmark_hk_20260101_120000.csv', [_row('HK.00700', 9.0, 9.0)], 1_000)
    _csv(tmp_path, 'buy_hold_benchmark_hk_20260201_120000.csv',
         [_row('HK.00700', -1.0, 2.0), _row('HK.00005', 3.0, 4.0)], 2_000)
    _csv(tmp_path, 'notes_20260101.csv', [{'x': 1}], 2_000)        # not a two-configuration result
    d = ri.load_all(results_dir=tmp_path)
    assert list(d.subject) == ['HK.00005', 'HK.00700']
    assert list(d.robust) == [True, False] and set(d.file) == {'buy_hold_benchmark_hk_20260201_120000.csv'}
    assert d.family.iloc[0] == 'Buy & Hold benchmark (HK)'


def test_superseded_studies_are_left_out_unless_asked_for(tmp_path):
    _csv(tmp_path, 'cross_sectional_sp100_20260101_120000.csv', [_row('US.SP100', 1.0, 1.0)], 1_000)
    assert ri.load_all(results_dir=tmp_path).empty
    assert len(ri.load_all(current_only=False, results_dir=tmp_path)) == 1


def test_studies_replaced_by_the_corrected_re_runs_are_superseded(tmp_path):
    # 19 Oct 2026: each pre-correction file has a corrected successor under another name
    for name in ('buy_hold_benchmark_20260811_044302.csv', 'ml_classifier_baseline_sameday_20260824_122641.csv',
                 'best_tool_new_stocks_full_20260804_014449.csv'):
        _csv(tmp_path, name, [_row('HK.00700', 1.0, 1.0)], 1_000)
    assert ri.load_all(results_dir=tmp_path).empty
    assert len(ri.load_all(current_only=False, results_dir=tmp_path)) == 3


def _json(d, name, obj):
    import json
    (d / name).write_text(json.dumps(obj))


def test_the_search_correction_reads_the_newest_pbo_run_before_the_unstamped_one(tmp_path):
    rc = {'common_end_date': '2026-08-28 16:00', 'n_boot': 20000, 'by_config': {}}
    _json(tmp_path, 'reality_check_20261030_204532.json', rc)
    _json(tmp_path, 'pbo_sp100_reversal_full96.json', {'pbo': 0.274, 'n_configs': 96})
    assert ri.load_search_correction(results_dir=tmp_path)['pbo']['pbo'] == 0.274
    _json(tmp_path, 'pbo_sp100_reversal_20261015_120000.json', {'pbo': 0.30, 'n_configs': 96})
    _json(tmp_path, 'pbo_sp100_reversal_20261030_204424.json', {'pbo': 0.278, 'n_configs': 96})
    assert ri.load_search_correction(results_dir=tmp_path)['pbo']['pbo'] == 0.278
