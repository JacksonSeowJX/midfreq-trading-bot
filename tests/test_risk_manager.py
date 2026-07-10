"""RiskManager: exit signals, circuit breaker, and position sizers."""
import pytest

from core.risk_manager import (
    RiskManager, SizingMethod, create_position_sizer,
    FixedQuantitySizer, FixedFractionalSizer, KellyCriterionSizer,
)


class TestExitSignals:
    def test_fixed_stop_loss_triggers_at_threshold(self):
        rm = RiskManager(stop_loss_pct=0.03)
        rm.register_entry('A', 100.0)
        assert rm.check_exit_signals('A', 97.0, 100.0) == 'stop_loss'
        assert rm.check_exit_signals('A', 97.1, 100.0) is None

    def test_trailing_stop_follows_peak(self):
        rm = RiskManager(trailing_stop_pct=0.05)
        rm.register_entry('A', 100.0)
        rm.update_peak_price('A', 120.0)
        # 5% below the 120 peak = 114
        assert rm.check_exit_signals('A', 114.0, 100.0) == 'trailing_stop'
        assert rm.check_exit_signals('A', 114.1, 100.0) is None

    def test_take_profit_triggers_at_target(self):
        rm = RiskManager(take_profit_pct=0.05)
        rm.register_entry('A', 100.0)
        assert rm.check_exit_signals('A', 105.0, 100.0) == 'take_profit'
        assert rm.check_exit_signals('A', 104.9, 100.0) is None

    def test_no_signal_without_configured_exits(self):
        rm = RiskManager()
        rm.register_entry('A', 100.0)
        assert rm.check_exit_signals('A', 50.0, 100.0) is None


class TestCircuitBreaker:
    def test_halts_at_max_drawdown(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        assert rm.is_trading_halted(89_999.0, 100_000.0) is True

    def test_active_below_threshold(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        assert rm.is_trading_halted(95_000.0, 100_000.0) is False

    def test_resets_on_recovery(self):
        rm = RiskManager(max_drawdown_pct=0.10)
        assert rm.is_trading_halted(85_000.0, 100_000.0) is True
        assert rm.is_trading_halted(95_000.0, 100_000.0) is False


class TestSizers:
    def test_fixed_quantity(self):
        assert FixedQuantitySizer(qty=100).calculate_qty(1e6, 400.0) == 100

    def test_fixed_fractional_with_stop(self):
        # Risk 2% of 100k = 2000; stop 5 below entry -> 400 shares
        sizer = FixedFractionalSizer(risk_pct=0.02)
        assert sizer.calculate_qty(100_000, 100.0, stop_price=95.0) == 400

    def test_fixed_fractional_never_exceeds_affordable(self):
        sizer = FixedFractionalSizer(risk_pct=0.02)
        # Tight stop implies huge qty; must cap at equity/price
        qty = sizer.calculate_qty(100_000, 100.0, stop_price=99.99)
        assert qty <= 1000

    def test_kelly_falls_back_without_history(self):
        kelly = KellyCriterionSizer()
        fallback = FixedFractionalSizer(0.02)
        assert kelly.calculate_qty(100_000, 100.0) == fallback.calculate_qty(100_000, 100.0)

    def test_kelly_caps_at_max_pct(self):
        kelly = KellyCriterionSizer(fraction=1.0, max_pct=0.25)
        # Absurdly favorable stats -> raw Kelly >> cap
        qty = kelly.calculate_qty(100_000, 100.0, win_rate=0.9, avg_win=1000, avg_loss=100)
        assert qty == int(100_000 * 0.25 / 100.0)

    def test_kelly_zero_for_losing_strategy(self):
        kelly = KellyCriterionSizer()
        qty = kelly.calculate_qty(100_000, 100.0, win_rate=0.2, avg_win=100, avg_loss=100)
        assert qty == 0

    def test_factory_creates_correct_types(self):
        assert isinstance(create_position_sizer(SizingMethod.FIXED_QUANTITY, qty=50), FixedQuantitySizer)
        assert isinstance(create_position_sizer(SizingMethod.FIXED_FRACTIONAL, risk_pct=0.01), FixedFractionalSizer)
        assert isinstance(create_position_sizer(SizingMethod.KELLY), KellyCriterionSizer)
