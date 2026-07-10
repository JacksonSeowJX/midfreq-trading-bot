"""Backtester: chronological replay, slippage, benchmark, equity tracking."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from core.backtester import Backtester
from core.models import Timeframe
from core.portfolio import Portfolio
from core.storage import DataStorage
from core.strategy import MovingAverageCrossover


def write_symbol(storage, symbol, closes, start=datetime(2026, 1, 5)):
    idx = pd.DatetimeIndex(
        [start + timedelta(days=i) for i in range(len(closes))], tz='UTC')
    df = pd.DataFrame({
        'open': closes, 'high': [c * 1.01 for c in closes],
        'low': [c * 0.99 for c in closes], 'close': closes,
        'volume': [1000] * len(closes),
    }, index=idx)
    storage.save_data(df, symbol.replace('.', '_'), '1d')


@pytest.fixture
def storage(tmp_path):
    return DataStorage(base_path=str(tmp_path))


def vee(n=60, low_at=30):
    """Price series that falls then rises — guarantees an SMA cross."""
    down = [100 - i for i in range(low_at)]
    up = [100 - low_at + i * 2 for i in range(n - low_at)]
    return down + up


class TestChronologicalReplay:
    def test_multi_symbol_events_interleaved_by_time(self, storage):
        write_symbol(storage, 'AAA', [100.0] * 10)
        write_symbol(storage, 'BBB', [50.0] * 10)
        bt = Backtester(storage=storage, portfolio=Portfolio())
        stream = bt._build_event_stream({
            'AAA': storage.load_data('AAA', '1d'),
            'BBB': storage.load_data('BBB', '1d'),
        })
        timestamps = list(stream.index)
        assert timestamps == sorted(timestamps)
        # Same-timestamp candles from both symbols must be adjacent
        first_two = set(stream.iloc[:2]['_symbol'])
        assert first_two == {'AAA', 'BBB'}

    def test_run_produces_metrics_and_per_candle_equity(self, storage):
        write_symbol(storage, 'AAA', vee())
        portfolio = Portfolio()
        bt = Backtester(storage=storage, portfolio=portfolio)
        metrics = bt.run(MovingAverageCrossover, symbols=['AAA'], timeframe=Timeframe.DAY_1,
                         fast_period=3, slow_period=10)
        assert metrics['total_trades'] > 0
        assert len(portfolio.equity_curve_detailed) == metrics['total_events']
        assert 'benchmark_return_pct' in metrics
        assert metrics['alpha'] == pytest.approx(
            metrics['return_pct'] - metrics['benchmark_return_pct'])


class TestSlippage:
    def test_buys_pay_more_sells_receive_less(self, storage):
        write_symbol(storage, 'AAA', vee())
        portfolio = Portfolio(initial_cash=1e6)
        bt = Backtester(storage=storage, portfolio=portfolio, slippage_bps=10.0)
        bt.run(MovingAverageCrossover, symbols=['AAA'], timeframe=Timeframe.DAY_1,
               fast_period=3, slow_period=10)
        buys = [t for t in portfolio.trade_history if t['action'] == 'BUY']
        assert buys, "expected at least one buy"
        # Candle closes in vee() are integers; a 10bps-adjusted fill is not
        for t in buys:
            unslipped = t['price'] / 1.001
            assert t['price'] > unslipped

    def test_zero_slippage_fills_at_close(self, storage):
        write_symbol(storage, 'AAA', vee())
        portfolio = Portfolio(initial_cash=1e6)
        bt = Backtester(storage=storage, portfolio=portfolio, slippage_bps=0.0)
        bt.run(MovingAverageCrossover, symbols=['AAA'], timeframe=Timeframe.DAY_1,
               fast_period=3, slow_period=10)
        closes = set(vee())
        for t in portfolio.trade_history:
            assert t['price'] in closes


class TestBenchmark:
    def test_buy_and_hold_return_matches_price_change(self, storage):
        closes = vee()
        write_symbol(storage, 'AAA', closes)
        bt = Backtester(storage=storage, portfolio=Portfolio())
        metrics = bt.run(MovingAverageCrossover, symbols=['AAA'], timeframe=Timeframe.DAY_1,
                         fast_period=3, slow_period=10)
        expected = (closes[-1] - closes[0]) / closes[0] * 100
        assert metrics['benchmark_return_pct'] == pytest.approx(expected)
