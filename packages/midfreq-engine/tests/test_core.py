"""The C++ core's arithmetic contract with CPython, and the merged event stream.

The engine reproduces the Python backtester bit for bit only if its sums, powers and int()
round exactly as CPython's do; these pin that down on random inputs."""
import math
import random
import sys

import numpy as np
import pytest

core = pytest.importorskip("midfreq.engine._core")

from midfreq import engine  # noqa: E402


def lists(seed, n=300):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        k = rng.randint(1, 60)
        scale = 10 ** rng.uniform(-6, 6)
        out.append([rng.gauss(0, 1) * scale + rng.choice([0, 1e3, -1e8]) for _ in range(k)])
    return out


def test_sum_matches_the_interpreters_sum():
    mode = engine.sum_mode()
    assert mode == ("neumaier" if sys.version_info >= (3, 12) else "naive")
    for xs in lists(1):
        assert core._sum(xs, mode) == sum(xs)
    assert core._sum([], mode) == 0.0
    assert core._sum([0.1] * 10, mode) == sum([0.1] * 10)


def test_naive_sum_is_left_to_right():
    for xs in lists(2):
        acc = 0
        for x in xs:
            acc = acc + x
        assert core._sum(xs, "naive") == acc


def test_compensated_sum_differs_from_naive_where_it_should():
    xs = [1e16, 1.0, -1e16]
    assert core._sum(xs, "neumaier") == 1.0
    assert core._sum(xs, "naive") == 0.0


def test_fsum_is_math_fsum():
    for xs in lists(3):
        assert core._fsum(xs) == math.fsum(xs)
    assert core._fsum([1e-16, 1.0, 1e16]) == math.fsum([1e-16, 1.0, 1e16])


@pytest.mark.parametrize("seed", range(5))
def test_pow_is_float_pow(seed):
    rng = random.Random(seed)
    for _ in range(2000):
        x = rng.uniform(-50, 50) * 10 ** rng.uniform(-8, 3)
        assert core._pow(x, 2.0) == x ** 2
        assert core._pow(abs(x), 0.5) == abs(x) ** 0.5
    assert core._pow(0.0, 0.5) == 0.0 ** 0.5
    assert core._pow(-0.0, 2.0) == (-0.0) ** 2
    assert core._pow(252.0, 0.5) == 252 ** 0.5


def test_int_truncates_and_raises_as_python():
    for x in (2.9, -2.9, 0.0, -0.5, 1e15 + 0.5):
        assert core._int(x) == int(x)
    with pytest.raises(OverflowError):
        core._int(float("inf"))
    with pytest.raises(ValueError):
        core._int(float("nan"))


def series(ts, close):
    ts = np.asarray(ts, dtype=np.int64)
    c = np.asarray(close, dtype=float)
    return ts, c


def test_the_stream_is_a_stable_merge_in_symbol_order():
    a_ts, a = series([1, 2, 2, 5], [10, 11, 12, 13])
    b_ts, b = series([0, 2, 3], [20, 21, 22])
    d = core.MarketData(["A", "B"], [a_ts, b_ts], [a, b], [a, b], [a, b], [a, b], [a, b])
    assert len(d) == 7 and d.symbols == ["A", "B"]
    assert list(d.timestamps()) == [0, 1, 2, 2, 2, 3, 5]
    # start <= ts < cutoff
    assert d.range(2, 5) == (2, 6)
    assert d.range(None, None) == (0, 7)
    assert d.range(6, None) == (7, 7)
    assert d.range(4, 1) == (6, 6)


def test_mismatched_columns_are_refused():
    ts, c = series([1, 2], [1.0, 2.0])
    with pytest.raises(ValueError):
        core.MarketData(["A"], [ts], [c], [c], [c], [c[:1]], [c])
    with pytest.raises(ValueError):
        core.MarketData(["A", "B"], [ts], [c], [c], [c], [c], [c])


def test_the_registry_lists_the_ported_strategies():
    assert set(core.strategies()) == {
        "sma_crossover", "rsi", "macd", "bollinger_bands", "zscore_mean_reversion", "pairs_trading",
        "ensemble_voting", "regime_switch", "cross_sectional_reversal", "cross_sectional_momentum",
        "buy_and_hold_risk_overlay"}
    assert core.exit_reasons()[0] == "" and "rebalance" in core.exit_reasons()


def test_an_unknown_strategy_or_bad_parameter_is_an_error():
    ts, c = series([1, 2], [1.0, 2.0])
    d = core.MarketData(["A"], [ts], [c], [c], [c], [c], [c])
    with pytest.raises(ValueError):
        core.run_backtest(d, "nope", {}, initial_cash=1e5, commission_rate=0.0)
    with pytest.raises(ValueError):
        core.run_backtest(d, "sma_crossover", {"fast_period": 0}, initial_cash=1e5, commission_rate=0.0)
    with pytest.raises(ValueError):
        core.run_backtest(d, "sma_crossover", {"fast_period": 2.5}, initial_cash=1e5, commission_rate=0.0)


def test_an_empty_range_is_not_ok_and_a_run_releases_results():
    ts, c = series(range(10), np.linspace(100, 90, 10))
    d = core.MarketData(["A"], [ts], [c], [c], [c], [c], [c])
    empty = core.run_backtest(d, "buy_and_hold_risk_overlay", {}, initial_cash=1e5, commission_rate=0.0,
                              start=100)
    assert empty["ok"] is False and empty["total_events"] == 0
    r = core.run_backtest(d, "buy_and_hold_risk_overlay", {"stop_loss_pct": 0.05}, initial_cash=1e5,
                          commission_rate=0.001)
    assert r["ok"] and r["total_events"] == 10 and r["last_ts"] == 9
    # bought 100 at 100 (the fixed sizer), stopped out at or below 95
    assert list(r["trades"]["is_buy"]) == [1, 0] and list(r["trades"]["qty"]) == [100, 100]
    assert core.exit_reasons()[r["trades"]["reason"][1]] == "stop_loss"
    assert len(r["equity"]["ts"]) == 10


def test_a_batch_keeps_order_and_reports_errors_per_run():
    rng = np.random.default_rng(0)
    ts = np.arange(400, dtype=np.int64) * 3_600_000_000_000
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    d = core.MarketData(["A"], [ts], [c], [c], [c], [c], [c])
    params = [{"fast_period": f, "slow_period": 20} for f in (3, 5, 0, 8)]
    out = core.run_batch(d, "sma_crossover", params, initial_cash=1e5, commission_rate=0.0, threads=3)
    assert [o["error_type"] for o in out] == [None, None, "ValueError", None]
    one = [core.run_backtest(d, "sma_crossover", p, initial_cash=1e5, commission_rate=0.0)
           for p in (params[0], params[1], params[3])]
    for o, r in zip([out[0], out[1], out[3]], one):
        assert o["metrics"] == r["metrics"]
