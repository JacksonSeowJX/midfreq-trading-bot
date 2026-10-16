"""The C++ engine against the Python backtester: identical trades, curves, positions and metrics.

Every ported strategy runs on both engines over the same synthetic universe (random walks, one
symbol starting late, shared timestamps), with and without each risk sizer, with and without
slippage, and the results must be equal, not close. Then: date windows, the cases that must fall
back to Python, and the optimiser giving the same table on both engines.

A strategy whose Python changed since its C++ port was verified (midfreq.backtest.parity) is
skipped here: it runs on Python, so there is nothing to compare. `python -m
midfreq.backtest.parity verify` runs this file on every port (MIDFREQ_ENGINE_VERIFYING=1) before
recording them."""
import contextlib
import io
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("midfreq.engine._core")

from midfreq.backtest.backtester import Backtester, PythonBacktester  # noqa: E402
from midfreq.backtest.native import (BatchRunner, NativeBacktester, NativeUnavailable,  # noqa: E402
                                     make_backtester, native_strategy_classes)
from midfreq.backtest.optimizer import grid_search, walk_forward  # noqa: E402
from midfreq.backtest.parity import port_reason  # noqa: E402
from midfreq.core.candle import Timeframe  # noqa: E402
from midfreq.core.portfolio import Portfolio  # noqa: E402
from midfreq.core.risk_manager import (EqualDollarSizer, FixedFractionalSizer, FixedQuantitySizer,  # noqa: E402
                                       KellyCriterionSizer, RiskManager)
from midfreq.data.storage import DataStorage  # noqa: E402
from midfreq.strategies import candle_strategies as cs  # noqa: E402
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY  # noqa: E402

SYMBOLS = [f"US.S{i:02d}" for i in range(8)]
H1 = Timeframe.HOUR_1


def make_storage(path, periods=700, freq="h", symbols=SYMBOLS):
    st = DataStorage(base_path=str(path))
    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-01-02 14:30", periods=periods, freq=freq, tz="UTC")
    for i, s in enumerate(symbols):
        c = np.round(50 * (1 + i / 10) * np.exp(np.cumsum(rng.normal(0, 0.012, len(idx)))), 2)
        df = pd.DataFrame({"open": c, "high": c * 1.003, "low": c * 0.997, "close": c, "volume": 1000.0},
                          index=idx)
        if i == 3:
            df = df.iloc[150:]  # a symbol that starts late
        with contextlib.redirect_stdout(io.StringIO()):
            st.save_data(df, s.replace(".", "_"), "1h")
    return st


@pytest.fixture(scope="module")
def storage(tmp_path_factory):
    return make_storage(tmp_path_factory.mktemp("native"))


@pytest.fixture(scope="module")
def long_storage(tmp_path_factory):
    """~100 days of 3-hourly bars: long enough for walk-forward windows."""
    return make_storage(tmp_path_factory.mktemp("native_long"), periods=800, freq="3h", symbols=SYMBOLS[:6])


CASES = [
    (cs.MovingAverageCrossover, {"fast_period": 5, "slow_period": 20}),
    (cs.MovingAverageCrossover, {"fast_period": 30, "slow_period": 10}),  # fast > slow: Python's slicing
    (cs.RSIStrategy, {"rsi_period": 10, "oversold": 35, "overbought": 65}),
    (cs.MACDStrategy, {}),
    (cs.MACDStrategy, {"fast_ema": 40, "slow_ema": 12, "signal_period": 5}),
    (cs.BollingerBandsStrategy, {"bb_period": 15, "num_std": 1.5}),
    (cs.ZScoreMeanReversion, {"lookback": 25, "entry_z": 1.5, "exit_z": 0.2}),
    (cs.PairsTradingStrategy, {"lookback": 20, "entry_z": 1.2}),
    (cs.EnsembleStrategy, {"consensus_threshold": 1}),
    (cs.RegimeSwitchStrategy, {"regime_lookback": 15, "er_threshold": 0.25}),
    (cs.CrossSectionalReversal, {"lookback": 10, "top_n": 3, "rebalance_every": 7}),
    (cs.CrossSectionalMomentum, {"lookback": 20, "top_n": 2, "rebalance_every": 5}),
    (cs.BuyAndHoldRiskOverlay, {"stop_loss_pct": 0.05, "trailing_stop_pct": 0.08}),
]


def risk(kind):
    return {
        None: None,
        "fractional": lambda: RiskManager(stop_loss_pct=0.03, trailing_stop_pct=0.05, take_profit_pct=0.06,
                                          max_drawdown_pct=0.08, position_sizer=FixedFractionalSizer(0.05)),
        "kelly": lambda: RiskManager(stop_loss_pct=0.02, position_sizer=KellyCriterionSizer()),
        "equal": lambda: RiskManager(trailing_stop_pct=0.04, position_sizer=EqualDollarSizer(3)),
        "fixed": lambda: RiskManager(take_profit_pct=0.03, stop_loss_pct=0.0, position_sizer=FixedQuantitySizer(50)),
    }[kind]() if kind else None


def require_current(cls):
    reason = port_reason(cls)
    if reason:
        pytest.skip(f"stale C++ port, runs on Python: {reason}")


def both(storage, cls, params, risk_kind=None, slippage=0.0, rate=0.0016, symbols=SYMBOLS, **dates):
    require_current(cls)
    out = []
    for native in (False, True):
        pf = Portfolio(initial_cash=100_000.0, commission_rate=rate)
        bt = (NativeBacktester(storage, pf, risk(risk_kind), slippage, fallback=False) if native
              else PythonBacktester(storage, pf, risk(risk_kind), slippage))
        with contextlib.redirect_stdout(io.StringIO()):
            m = bt.run(cls, symbols, H1, **dates, **params)
        if native:
            assert bt.engine_used == "native"
        out.append((m, pf))
    return out


def assert_identical(py, nat):
    (mp, pp), (mn, pn) = py, nat
    assert pn.trade_history == pp.trade_history
    assert pn.equity_curve_detailed == pp.equity_curve_detailed
    assert pn.equity_curve == pp.equity_curve
    assert list(pn.positions.items()) == list(pp.positions.items())  # dict order too
    assert pn.cash == pp.cash
    assert mn == mp


def test_every_ported_strategy_is_covered():
    assert {cls for cls, _ in CASES} == set(native_strategy_classes())


@pytest.mark.parametrize("cls,params", CASES, ids=lambda v: v.__name__ if isinstance(v, type) else "")
@pytest.mark.parametrize("risk_kind", [None, "fractional", "kelly", "equal", "fixed"])
def test_identical_to_the_python_backtester(storage, cls, params, risk_kind):
    py, nat = both(storage, cls, params, risk_kind, slippage=5.0, rate=0.00005)
    assert_identical(py, nat)


@pytest.mark.parametrize("cls,params", CASES, ids=lambda v: v.__name__ if isinstance(v, type) else "")
def test_identical_at_hk_fees_without_slippage(storage, cls, params):
    py, nat = both(storage, cls, params)
    assert_identical(py, nat)
    if cls is not cs.MovingAverageCrossover or params["fast_period"] < params["slow_period"]:
        assert py[1].trade_history, "the case should trade"


@pytest.mark.parametrize("dates", [
    dict(start_date=datetime(2025, 1, 10), end_date=datetime(2025, 1, 30)),
    dict(start_date=datetime(2025, 1, 10), end_date=datetime(2025, 1, 30, 15), end_inclusive=False),
    dict(end_date=datetime(2025, 1, 20)),
    dict(start_date=datetime(2025, 1, 25)),
    dict(start_date=datetime(2027, 1, 1)),  # no data: {} from both
])
@pytest.mark.parametrize("cls,params", [CASES[0], CASES[10], CASES[7]], ids=["sma", "reversal", "pairs"])
def test_date_windows_cut_the_same_candles(storage, cls, params, dates):
    py, nat = both(storage, cls, params, **dates)
    assert_identical(py, nat)


def test_a_symbol_without_data_is_skipped_by_both(storage):
    py, nat = both(storage, cs.CrossSectionalReversal, CASES[10][1], symbols=SYMBOLS + ["US.NONE"])
    assert_identical(py, nat)


def test_bad_parameters_raise_as_in_python(storage):
    for native in (False, True):
        bt = (NativeBacktester(storage, Portfolio(), fallback=False) if native else PythonBacktester(storage, Portfolio()))
        with pytest.raises(TypeError), contextlib.redirect_stdout(io.StringIO()):
            bt.run(cs.MovingAverageCrossover, SYMBOLS, H1, nonsense=1)


class SubclassedReversal(cs.CrossSectionalReversal):
    def _select_basket(self, ranked):
        return [s for s, _ in ranked[-self.top_n:]]


class LoggingPortfolio(Portfolio):
    pass


class HalfSizer(FixedQuantitySizer):
    pass


@pytest.mark.parametrize("why,kwargs", [
    ("no native version", dict(cls=cs.HMMRegimeSwitchStrategy, params={"refit_every": 200})),
    ("no native version", dict(cls=SubclassedReversal)),
    ("not a plain Portfolio", dict(portfolio=LoggingPortfolio)),
    ("no native version", dict(risk=RiskManager(position_sizer=HalfSizer(10)))),
])
def test_cases_it_cannot_match_run_on_python(storage, why, kwargs):
    cls = kwargs.get("cls", cs.CrossSectionalReversal)
    params = kwargs.get("params", {"lookback": 10, "top_n": 2})
    make = kwargs.get("portfolio", Portfolio)
    pf_py, pf_auto = make(), make()
    with contextlib.redirect_stdout(io.StringIO()):
        expected = PythonBacktester(storage, pf_py, kwargs.get("risk")).run(cls, SYMBOLS[:3], H1, **params)
        auto = NativeBacktester(storage, pf_auto, kwargs.get("risk"))
        got = auto.run(cls, SYMBOLS[:3], H1, **params)
    assert auto.engine_used == "python" and why in auto.fallback_reason
    assert got == expected and pf_auto.trade_history == pf_py.trade_history
    with pytest.raises(NativeUnavailable), contextlib.redirect_stdout(io.StringIO()):
        NativeBacktester(storage, make(), kwargs.get("risk"), fallback=False).run(cls, SYMBOLS[:3], H1, **params)


def test_missing_closes_run_on_python(tmp_path):
    st = DataStorage(base_path=str(tmp_path))
    idx = pd.date_range("2025-01-02", periods=60, freq="D", tz="UTC")
    c = np.linspace(100, 130, 60)
    c[20] = np.nan
    with contextlib.redirect_stdout(io.StringIO()):
        st.save_data(pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx),
                     "AAA", "1d")
        bt = NativeBacktester(st, Portfolio())
        bt.run(cs.MovingAverageCrossover, ["AAA"], Timeframe.DAY_1, fast_period=3, slow_period=10)
    assert bt.engine_used == "python" and "missing closes" in bt.fallback_reason


def test_make_backtester_picks_by_name_and_environment(storage, monkeypatch):
    assert type(make_backtester(storage, Portfolio(), engine="python")) is PythonBacktester
    assert make_backtester(storage, Portfolio(), engine="native").engine == "native"
    monkeypatch.setenv("MIDFREQ_ENGINE", "python")
    assert type(make_backtester(storage, Portfolio())) is PythonBacktester
    monkeypatch.setenv("MIDFREQ_ENGINE", "auto")
    assert type(make_backtester(storage, Portfolio())) is Backtester
    with pytest.raises(ValueError):
        make_backtester(storage, Portfolio(), engine="fortran")


def test_the_batch_runner_chooses_its_engine(storage):
    require_current(cs.CrossSectionalReversal)
    assert BatchRunner("auto", cs.CrossSectionalReversal, SYMBOLS, H1, storage, 1e5, 0.0).engine == "native"
    hmm = BatchRunner("auto", cs.HMMRegimeSwitchStrategy, SYMBOLS, H1, storage, 1e5, 0.0)
    assert hmm.engine == "python" and "no native version" in hmm.fallback_reason
    with pytest.raises(NativeUnavailable):
        BatchRunner("native", cs.MLDirectionClassifier, SYMBOLS, H1, storage, 1e5, 0.0)


@pytest.mark.parametrize("strategy", ["Cross-Sectional Reversal", "RSI", "Bollinger Bands"])
def test_grid_search_is_the_same_table_on_both_engines(storage, strategy):
    require_current(STRATEGY_REGISTRY[strategy]["class"])
    kw = dict(symbols=SYMBOLS, timeframe=H1, start_date=datetime(2025, 1, 2), end_date=datetime(2025, 1, 31),
              storage=storage, slippage_bps=5.0, commission_rate=0.00005, max_combinations=60)
    calls = {"python": [], "native": []}
    py = grid_search(strategy, engine="python", progress_callback=lambda p, m: calls["python"].append(p), **kw)
    nat = grid_search(strategy, engine="native", progress_callback=lambda p, m: calls["native"].append(p), **kw)
    assert len(py) > 0
    pd.testing.assert_frame_equal(nat, py, check_exact=True)
    assert calls["native"] == calls["python"]


def test_walk_forward_is_the_same_on_both_engines(long_storage):
    kw = dict(symbols=SYMBOLS[:6], timeframe=H1, start_date=datetime(2025, 1, 2), end_date=datetime(2025, 4, 12),
              storage=long_storage, n_splits=2, train_pct=0.7, slippage_bps=5.0, commission_rate=0.00005)
    rm = lambda: RiskManager(stop_loss_pct=0.04, position_sizer=EqualDollarSizer(2))  # noqa: E731
    for strategy, risk_manager in (("Cross-Sectional Reversal", None), ("Z-Score Mean Reversion", rm())):
        require_current(STRATEGY_REGISTRY[strategy]["class"])
        py = walk_forward(strategy, engine="python", risk_manager=risk_manager, **kw)
        nat = walk_forward(strategy, engine="native", risk_manager=risk_manager, **kw)
        assert py["windows"] and [w.__dict__ for w in nat["windows"]] == [w.__dict__ for w in py["windows"]]
        assert nat["summary"] == py["summary"]


# ─── Backtester itself: the C++ engine underneath, PythonBacktester's results ──────────────────

@pytest.mark.parametrize("cls,params", [CASES[10], CASES[0]], ids=["reversal", "sma"])
def test_the_backtester_runs_on_cpp_with_pythons_results(storage, cls, params):
    require_current(cls)
    pf_py, pf = Portfolio(), Portfolio()
    with contextlib.redirect_stdout(io.StringIO()):
        expected = PythonBacktester(storage, pf_py, slippage_bps=5.0).run(cls, SYMBOLS, H1, **params)
        bt = Backtester(storage, pf, slippage_bps=5.0)
        got = bt.run(cls, SYMBOLS, H1, **params)
    assert (bt.engine_used, bt.fallback_reason) == ("native", None)
    assert got == expected
    assert pf.trade_history == pf_py.trade_history and pf.equity_curve_detailed == pf_py.equity_curve_detailed


def test_the_backtester_says_when_and_why_it_ran_python(storage):
    hmm = {"refit_every": 200}
    with contextlib.redirect_stdout(io.StringIO()):
        bt = Backtester(storage, Portfolio())
        bt.run(cs.HMMRegimeSwitchStrategy, SYMBOLS[:2], H1, **hmm)
        assert bt.engine_used == "python" and "no native version" in bt.fallback_reason
        forced = Backtester(storage, Portfolio(), engine="python")
        forced.run(cs.MovingAverageCrossover, SYMBOLS[:2], H1)
        assert (forced.engine_used, forced.fallback_reason) == ("python", "engine=python")
        with pytest.raises(NativeUnavailable):
            Backtester(storage, Portfolio(), engine="native").run(cs.HMMRegimeSwitchStrategy, SYMBOLS[:2], H1, **hmm)


def test_a_second_run_on_the_same_portfolio_matches_python(storage):
    """The second run continues a portfolio that has traded, so it runs on Python; the slippage
    wrapper is still installed once, as building a PythonBacktester always did."""
    require_current(cs.MovingAverageCrossover)
    params = {"fast_period": 5, "slow_period": 20}
    pf_py, pf = Portfolio(), Portfolio()
    reference, bt = PythonBacktester(storage, pf_py, slippage_bps=10.0), Backtester(storage, pf, slippage_bps=10.0)
    with contextlib.redirect_stdout(io.StringIO()):
        for b in (reference, bt):
            b.run(cs.MovingAverageCrossover, SYMBOLS[:3], H1, end_date=datetime(2025, 1, 15), **params)
        assert bt.engine_used == "native"
        for b in (reference, bt):
            b.run(cs.MovingAverageCrossover, SYMBOLS[:3], H1, start_date=datetime(2025, 1, 16), **params)
    assert bt.engine_used == "python" and "already traded" in bt.fallback_reason
    assert pf.trade_history and pf.trade_history == pf_py.trade_history
    assert pf.cash == pf_py.cash
