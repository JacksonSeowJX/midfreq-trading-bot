"""core.forward_test reads the forward test back from session logs alone."""
import json

from core import forward_test as ft

SYMS = ['US.AAA', 'US.BBB', 'US.CCC']


def _session(tmp_path, stamp, events, strategy='CrossSectionalReversal', symbols=SYMS):
    start = {'type': 'session_start', 'timestamp': f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} 21:35:02',
             'strategy': strategy, 'symbols': symbols, 'timeframe': '1h'}
    lines = []
    for e in events:
        if e['type'] == 'session_start':
            e = {**start, **e}
        lines.append(json.dumps(e))
    (tmp_path / f'session_{stamp}_213502_x_1_1.jsonl').write_text('\n'.join(lines) + '\n')


def _acct(total):
    return {'cash': 0.0, 'total_assets': total, 'market_value': 0.0}


def test_profit_is_the_change_in_the_account_since_the_test_began(tmp_path):
    _session(tmp_path, '20261001', [
        {'type': 'session_start', 'account': _acct(1_000_000.0)},
        {'type': 'session_end', 'timestamp': '2026-10-02 04:01:00', 'trades': 0,
         'account': _acct(1_000_000.0), 'positions': {}}])
    _session(tmp_path, '20261005', [
        {'type': 'session_start', 'account': _acct(1_000_000.0)},
        {'type': 'trade', 'timestamp': '2026-10-05 10:30:00+00:00', 'symbol': 'US.AAA', 'side': 'BUY',
         'qty': 100.0, 'price': 50.0},
        {'type': 'candle_close', 'symbol': 'US.AAA', 'timestamp': '2026-10-05 16:00:00+00:00', 'close': 55.0},
        {'type': 'session_end', 'timestamp': '2026-10-06 04:01:00', 'trades': 1,
         'account': _acct(1_000_499.0), 'positions': {'US.AAA': {'qty': 100.0, 'entry_price': 50.0}}}])
    d = ft.load(tmp_path, capital=100_000.0)
    assert d['sessions'] == 2 and d['rebalances'] == 1 and d['orders'] == 1
    assert d['pnl'] == 499.0 and round(d['pnl_pct'], 3) == 0.499 and d['value'] == 100_499.0
    h = d['holdings'].iloc[0]
    assert (h['Stock'], h['Last price'], h['Unrealised P&L']) == ('US.AAA', 55.0, 500.0)
    assert d['first_trade'] == '2026-10-05' and d['started'] == '20261001'


def test_a_sale_reports_the_profit_realised_against_its_purchase(tmp_path):
    _session(tmp_path, '20261005', [
        {'type': 'session_start', 'account': _acct(1_000_000.0)},
        {'type': 'trade', 'timestamp': '2026-10-05 10:30:00+00:00', 'symbol': 'US.AAA', 'side': 'BUY',
         'qty': 100.0, 'price': 50.0},
        {'type': 'session_end', 'timestamp': '2026-10-06 04:01:00', 'trades': 1,
         'account': _acct(1_000_000.0), 'positions': {'US.AAA': {'qty': 100.0, 'entry_price': 50.0}}}])
    _session(tmp_path, '20261008', [
        {'type': 'session_start', 'account': _acct(1_000_200.0)},
        {'type': 'trade', 'timestamp': '2026-10-08 10:30:00+00:00', 'symbol': 'US.AAA', 'side': 'SELL',
         'qty': 100.0, 'price': 53.0, 'exit_reason': 'rebalance'},
        {'type': 'session_end', 'timestamp': '2026-10-09 04:01:00', 'trades': 1,
         'account': _acct(1_000_298.0), 'positions': {}}])
    d = ft.load(tmp_path)
    sale = d['trades'].iloc[-1]
    assert sale['Side'] == 'SELL' and sale['Realised P&L'] == 300.0 and round(sale['Realised %'], 6) == 6.0
    assert d['realised'] == 300.0 and d['rebalances'] == 2 and len(d['holdings']) == 0
    assert d['pnl'] == 298.0                       # the account's own figure, after fees


def test_other_strategies_and_markets_are_not_counted(tmp_path):
    _session(tmp_path, '20260925', [
        {'type': 'session_start', 'account': _acct(997_000.0)},
        {'type': 'session_end', 'timestamp': '2026-09-25 16:00:00', 'trades': 0,
         'account': _acct(990_000.0), 'positions': {}}], strategy='BollingerBandsStrategy', symbols=['HK.00700'])
    assert ft.load(tmp_path) is None
    _session(tmp_path, '20261005', [
        {'type': 'session_start', 'account': _acct(1_000_000.0)},
        {'type': 'session_end', 'timestamp': '2026-10-06 04:01:00', 'trades': 0,
         'account': _acct(1_000_010.0), 'positions': {}}])
    d = ft.load(tmp_path)
    assert d['sessions'] == 1 and d['pnl'] == 10.0


def test_a_log_line_cut_short_by_a_crash_is_skipped(tmp_path):
    _session(tmp_path, '20261005', [
        {'type': 'session_start', 'account': _acct(1_000_000.0)},
        {'type': 'session_end', 'timestamp': '2026-10-06 04:01:00', 'trades': 0,
         'account': _acct(1_000_010.0), 'positions': {}}])
    f = next(tmp_path.glob('session_*.jsonl'))
    f.write_text(f.read_text() + '{"type": "candle_close", "symbol": "US.A')
    assert ft.load(tmp_path)['pnl'] == 10.0


def test_search_correction_reads_the_newest_reality_check(tmp_path):
    from core import results_index as ri
    assert ri.load_search_correction(tmp_path) is None
    cfg = lambda p, best: {'p_value': p, 'naive_p_value': 0.02, 'best_candidate': best, 'observed_max_mean': 6.15,
                           'n_windows': 9, 'n_candidates': 2, 'bootstrap_max_quantiles': {'p95': 7.48},
                           'candidate_means': {'CS-Reversal x sp100': 6.15, 'CS-Momentum x hsi': -7.6}}
    (tmp_path / 'reality_check_20260928_000000.json').write_text(json.dumps(
        {'n_boot': 5000, 'common_end_date': None, 'by_config': {'A (9 windows)': cfg(0.5, 'CS-Momentum x hsi')}}))
    (tmp_path / 'reality_check_20260929_000000.json').write_text(json.dumps(
        {'n_boot': 20000, 'common_end_date': '2026-08-28 16:00:00',
         'by_config': {'A (9 windows)': cfg(0.094, 'CS-Reversal x sp100')}}))
    (tmp_path / 'reality_check_cache.json').write_text('{}')          # the cache is not a result
    sc = ri.load_search_correction(tmp_path)
    c = sc['configs'][0]
    assert sc['file'] == 'reality_check_20260929_000000.json' and sc['end'] == '2026-08-28' and sc['pbo'] is None
    assert (c['p_value'], c['best'], c['luck_p95']) == (0.094, 'Reversal, S&P 100', 7.48)
    assert list(c['means']) == ['Momentum, Hang Seng', 'Reversal, S&P 100']      # sorted, worst first
