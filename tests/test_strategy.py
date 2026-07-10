"""Strategies: signal logic, regime classification, registry integrity."""
from datetime import datetime, timedelta

import pytest

from core.models import Candle
from core.portfolio import Portfolio
from core.strategy import (
    STRATEGY_REGISTRY, MovingAverageCrossover, RegimeSwitchStrategy,
)

T0 = datetime(2026, 1, 5, 10, 0)


def feed(strategy, symbol, closes):
    for i, c in enumerate(closes):
        strategy.on_data(symbol, Candle(
            timestamp=T0 + timedelta(hours=i),
            open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000))


class TestSMACrossover:
    def test_buys_on_golden_cross_and_sells_on_death_cross(self):
        p = Portfolio(initial_cash=1e6)
        s = MovingAverageCrossover(p, fast_period=3, slow_period=6)
        # Fall (fast below slow), then sharp rise (golden cross), then fall again
        closes = [100 - i for i in range(10)] + [92 + i * 3 for i in range(8)] \
            + [113 - i * 3 for i in range(8)]
        feed(s, 'A', closes)
        actions = [t['action'] for t in p.trade_history]
        assert actions[:2] == ['BUY', 'SELL']

    def test_no_double_buy_while_holding(self):
        p = Portfolio(initial_cash=1e6)
        s = MovingAverageCrossover(p, fast_period=3, slow_period=6)
        closes = [100 - i for i in range(10)] + [92 + i * 3 for i in range(12)]
        feed(s, 'A', closes)
        buys = [t for t in p.trade_history if t['action'] == 'BUY']
        assert len(buys) == 1


class TestRegimeSwitch:
    def test_monotonic_series_classified_as_trend(self):
        p = Portfolio(initial_cash=1e6)
        s = RegimeSwitchStrategy(p, regime_lookback=10, er_threshold=0.3)
        feed(s, 'A', [100 + i for i in range(15)])
        assert s.regime.get('A') == 'TREND'

    def test_oscillating_series_classified_as_range(self):
        p = Portfolio(initial_cash=1e6)
        s = RegimeSwitchStrategy(p, regime_lookback=10, er_threshold=0.3)
        feed(s, 'A', [100 + (1 if i % 2 else -1) for i in range(20)])
        assert s.regime.get('A') == 'RANGE'

    def test_efficiency_ratio_bounds(self):
        p = Portfolio()
        s = RegimeSwitchStrategy(p, regime_lookback=5)
        straight = [100, 101, 102, 103, 104, 105]
        assert s._efficiency_ratio(straight) == pytest.approx(1.0)
        churn = [100, 101, 100, 101, 100, 101]
        er = s._efficiency_ratio(churn)
        assert 0.0 <= er < 0.5

    def test_flattens_position_on_regime_flip(self):
        p = Portfolio(initial_cash=1e6)
        s = RegimeSwitchStrategy(p, regime_lookback=6, er_threshold=0.5,
                                 fast_period=2, slow_period=4, bb_period=4, num_std=1.0)
        # Force a position, then force a regime flip from TREND to RANGE
        trend = [100 + i * 2 for i in range(12)]
        feed(s, 'A', trend)
        assert s.regime.get('A') == 'TREND'
        p.execute_trade('A', True, 100, trend[-1], T0)  # simulate an open position
        churn_base = trend[-1]
        churn = [churn_base + (1 if i % 2 else -1) for i in range(12)]
        feed(s, 'A', churn)
        assert s.regime.get('A') == 'RANGE'
        exit_reasons = [t.get('exit_reason') for t in p.trade_history if t['action'] == 'SELL']
        assert 'regime_change' in exit_reasons


class TestRegistry:
    def test_every_entry_instantiates_with_defaults(self):
        p = Portfolio()
        for name, info in STRATEGY_REGISTRY.items():
            defaults = {k: v['default'] for k, v in info['params'].items()}
            strategy = info['class'](p, **defaults)
            assert hasattr(strategy, 'on_data'), name

    def test_param_metadata_complete(self):
        for name, info in STRATEGY_REGISTRY.items():
            for key, meta in info['params'].items():
                for field in ('label', 'min', 'max', 'default', 'step'):
                    assert field in meta, f"{name}.{key} missing {field}"
                assert meta['min'] <= meta['default'] <= meta['max'], f"{name}.{key}"
