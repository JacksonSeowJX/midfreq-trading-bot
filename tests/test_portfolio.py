"""Portfolio: cash accounting, position tracking, and performance metrics."""
from datetime import datetime, timedelta

import pytest

from core.portfolio import Portfolio

T0 = datetime(2026, 1, 5, 10, 0)


def make_portfolio(cash=100_000.0, fee=0.001):
    return Portfolio(initial_cash=cash, commission_rate=fee)


class TestTrades:
    def test_buy_deducts_value_plus_commission(self):
        p = make_portfolio()
        p.execute_trade('HK.00700', True, 100, 400.0, T0)
        assert p.cash == pytest.approx(100_000 - 40_000 - 40.0)
        assert p.get_position_qty('HK.00700') == 100
        assert p.get_entry_price('HK.00700') == 400.0

    def test_sell_credits_value_minus_commission(self):
        p = make_portfolio()
        p.execute_trade('HK.00700', True, 100, 400.0, T0)
        p.execute_trade('HK.00700', False, 100, 410.0, T0 + timedelta(days=1))
        assert p.get_position_qty('HK.00700') == 0
        assert 'HK.00700' not in p.positions
        expected = 100_000 - 40_000 - 40.0 + 41_000 - 41.0
        assert p.cash == pytest.approx(expected)

    def test_insufficient_cash_rejects_buy(self):
        p = make_portfolio(cash=1_000.0)
        p.execute_trade('HK.00700', True, 100, 400.0, T0)
        assert p.cash == 1_000.0
        assert p.get_position_qty('HK.00700') == 0
        assert p.trade_history == []

    def test_overselling_rejected_and_cash_restored(self):
        p = make_portfolio()
        p.execute_trade('HK.00700', True, 100, 400.0, T0)
        cash_before = p.cash
        p.execute_trade('HK.00700', False, 200, 410.0, T0)
        assert p.cash == pytest.approx(cash_before)
        assert p.get_position_qty('HK.00700') == 100

    def test_average_entry_price_on_scale_in(self):
        p = make_portfolio(cash=1_000_000.0)
        p.execute_trade('HK.00700', True, 100, 400.0, T0)
        p.execute_trade('HK.00700', True, 100, 500.0, T0)
        assert p.get_entry_price('HK.00700') == pytest.approx(450.0)
        assert p.get_position_qty('HK.00700') == 200

    def test_default_commission_is_calibrated_hk_rate(self):
        assert Portfolio().commission_rate == pytest.approx(Portfolio.HK_FEE_RATE) == 0.0016


class TestEquityAndMetrics:
    def test_current_equity_includes_positions(self):
        p = make_portfolio()
        p.execute_trade('HK.00700', True, 100, 400.0, T0)
        equity = p.get_current_equity({'HK.00700': 420.0})
        assert equity == pytest.approx(p.cash + 42_000)

    def test_max_drawdown_from_equity_curve(self):
        p = make_portfolio()
        # Synthetic per-candle curve: 100k -> 120k -> 90k -> 110k. DD = 25%.
        for i, eq in enumerate([100_000, 120_000, 90_000, 110_000]):
            p.equity_curve_detailed.append({'timestamp': T0 + timedelta(days=i), 'equity': eq})
        metrics = p.calculate_metrics({})
        assert metrics['max_drawdown'] == pytest.approx(25.0)

    def test_sharpe_positive_for_steady_gains(self):
        p = make_portfolio()
        for i in range(30):
            p.equity_curve_detailed.append(
                {'timestamp': T0 + timedelta(days=i), 'equity': 100_000 * (1.001 ** i)})
        metrics = p.calculate_metrics({})
        assert metrics['sharpe_ratio'] > 0

    def test_trade_stats_win_rate(self):
        p = make_portfolio(cash=1_000_000.0)
        # Round trip 1: win. Round trip 2: loss.
        p.execute_trade('A', True, 100, 100.0, T0)
        p.execute_trade('A', False, 100, 110.0, T0)
        p.execute_trade('A', True, 100, 100.0, T0)
        p.execute_trade('A', False, 100, 95.0, T0)
        stats = p.get_trade_stats()
        assert stats['win_rate'] == pytest.approx(0.5)
        assert stats['avg_win'] == pytest.approx(1000.0)
        assert stats['avg_loss'] == pytest.approx(500.0)
