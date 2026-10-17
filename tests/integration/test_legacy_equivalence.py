"""
Acceptance test for the legacy adapter: for EVERY strategy in midfreq.strategies.candle_strategies.STRATEGY_REGISTRY,
midfreq.backtest.backtester.Backtester and BacktestEngine + LegacyStrategyAdapter, run on the same data,
produce identical trade lists, identical final equity, identical metrics and identical equity
curves, and the engine's own account agrees with the original bookkeeping.

Comparisons are exact (==), not approximate: the adapter is meant to reproduce the original to the
last bit, so that results reported from the candle backtester remain reproducible on the engine.
"""
import contextlib
import io
from datetime import datetime

import pytest

from midfreq.backtest.backtester import PythonBacktester as Backtester  # the pure-Python reference
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.core.risk_manager import EqualDollarSizer, FixedFractionalSizer, RiskManager
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY, MovingAverageCrossover
from midfreq.backtest.legacy_adapter import LegacyDivergenceError, LegacyStrategyAdapter, run_legacy_backtest

from mft_synthetic import HK_CLOSES, HK_SYMBOLS, US_CLOSES, US_SYMBOLS, write_universe

STRATEGIES = sorted(STRATEGY_REGISTRY)


def defaults(name):
    return {k: v["default"] for k, v in STRATEGY_REGISTRY[name]["params"].items()}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("legacy_data")
    write_universe(d, HK_SYMBOLS, HK_CLOSES, "2024-01-02", 110, "1h", seed=7)
    write_universe(d, US_SYMBOLS, US_CLOSES, "2024-02-20", 60, "1h", seed=11)   # across the 10 Mar DST change
    write_universe(d, US_SYMBOLS, None, "2023-01-02", 300, "1d", seed=3)
    return d


# Each scenario: symbols, timeframe, cash, commission, slippage, risk manager factory, date window.
SCENARIOS = {
    "hk-hourly": (HK_SYMBOLS, "1h", 100_000.0, Portfolio.HK_FEE_RATE, 5.0, lambda: None, (None, None)),
    "us-hourly-dst-risk": (US_SYMBOLS, "1h", 250_000.0, 0.00005, 5.0,
                           # Risk 1% of equity against a 3% stop: about a third of equity per entry.
                           lambda: RiskManager(stop_loss_pct=0.03, trailing_stop_pct=0.05,
                                               position_sizer=FixedFractionalSizer(0.01)),
                           (datetime(2024, 3, 1), datetime(2024, 4, 12))),
    "us-daily-equal-dollar": (US_SYMBOLS, "1d", 250_000.0, 0.00005, 0.0,
                              lambda: RiskManager(stop_loss_pct=0.04, position_sizer=EqualDollarSizer(2)),
                              (None, None)),
}
TIMEFRAMES = {"1h": Timeframe.HOUR_1, "1d": Timeframe.DAY_1}


def run_both(data_dir, name, scenario):
    symbols, tf, cash, rate, slip, risk, (start, end) = SCENARIOS[scenario]
    params = defaults(name)
    cls = STRATEGY_REGISTRY[name]["class"]
    with contextlib.redirect_stdout(io.StringIO()):
        legacy_pf = Portfolio(initial_cash=cash, commission_rate=rate)
        legacy = Backtester(DataStorage(str(data_dir)), legacy_pf, risk_manager=risk(), slippage_bps=slip).run(
            cls, symbols, TIMEFRAMES[tf], start_date=start, end_date=end, **params)
        new = run_legacy_backtest(cls, symbols, tf, data_dir, cash, rate, slip, risk_manager=risk(),
                                  start_date=start, end_date=end, **params)
    return legacy_pf, legacy, new


@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("name", STRATEGIES)
def test_engine_reproduces_the_original_backtester(data_dir, name, scenario):
    legacy_pf, legacy, new = run_both(data_dir, name, scenario)
    pf = new.portfolio

    assert pf.trade_history == legacy_pf.trade_history                  # every field of every trade
    assert new.metrics["final_equity"] == legacy["final_equity"]
    assert {k: v for k, v in new.metrics.items() if k != "open_positions"} == \
        {k: v for k, v in legacy.items() if k != "open_positions"}
    assert pf.equity_curve_detailed == legacy_pf.equity_curve_detailed
    assert pf.positions == legacy_pf.positions

    # The engine's own books agree with the original bookkeeping.
    ccy = new.result.currencies[0]
    assert new.result.final_balances[ccy] == legacy_pf.cash
    assert len(new.result.trade_log) == len(legacy_pf.trade_history)
    assert [t["price"] for t in new.result.trade_log] == [t["price"] for t in legacy_pf.trade_history]
    assert {k: (v["qty"], v["avg_px"]) for k, v in new.result.open_positions.items()} == \
        {str(new.adapter.instrument_for[s]): (p["qty"], p["entry_price"]) for s, p in legacy_pf.positions.items()
         if p["qty"] > 0}


@pytest.mark.parametrize("name", STRATEGIES)
def test_each_strategy_actually_trades_in_the_base_scenario(data_dir, name):
    legacy_pf, legacy, _ = run_both(data_dir, name, "hk-hourly")
    assert legacy["total_trades"] > 0, "equivalence on zero trades would prove nothing"


def test_the_risk_scenarios_trade_too(data_dir):
    for scenario in ("us-hourly-dst-risk", "us-daily-equal-dollar"):
        total = sum(len(run_both(data_dir, n, scenario)[0].trade_history) for n in
                    ("SMA Crossover", "RSI", "Bollinger Bands", "Cross-Sectional Reversal"))
        assert total > 0, scenario


def test_candles_carry_the_legacy_local_timestamps(data_dir):
    seen = []

    class Spy(MovingAverageCrossover):
        def on_data(self, symbol, candle):
            seen.append((symbol, candle.timestamp))
            super().on_data(symbol, candle)

    with contextlib.redirect_stdout(io.StringIO()):
        run_legacy_backtest(Spy, ["HK.00700"], "1h", data_dir, end_date=datetime(2024, 1, 2))
    assert [str(ts) for _, ts in seen] == [f"2024-01-02 {t}:00+00:00" for t in HK_CLOSES]


def test_no_data_returns_an_empty_result_like_the_original(data_dir):
    with contextlib.redirect_stdout(io.StringIO()):
        run = run_legacy_backtest(MovingAverageCrossover, ["HK.00700"], "1h", data_dir,
                                  start_date=datetime(2030, 1, 1))
    assert run.metrics == {} and run.skipped == ["HK.00700"]


def test_one_currency_per_legacy_strategy(data_dir):
    with pytest.raises(ValueError, match="one cash balance"):
        run_legacy_backtest(MovingAverageCrossover, ["HK.00700", "US.AAPL"], "1h", data_dir)


def test_divergence_is_detected_not_ignored(data_dir):
    """If the engine and the original bookkeeping ever disagree about a trade, the run stops."""
    from midfreq.backtest import BacktestEngine, LegacyFillModel
    from midfreq.data.persistence import LegacyDataReader

    reader = LegacyDataReader(data_dir)
    iid = reader.instrument_id("HK.00700")
    engine = BacktestEngine()
    engine.add_venue("XHKG", {"HKD": 100_000.0}, fill_model=LegacyFillModel())
    engine.add_data(reader.read_bars(iid, "1-HOUR"))
    adapter = LegacyStrategyAdapter(MovingAverageCrossover, [iid], "1-HOUR", 100_000.0,
                                    strategy_params={"fast_period": 3, "slow_period": 8})
    engine.add_strategy(adapter)
    engine.portfolio.account("HKD").balance = 0.0      # the engine's account no longer matches
    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(LegacyDivergenceError):
        engine.run()
