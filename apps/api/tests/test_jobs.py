"""Runs as jobs: backtest, compare and optimize end to end, the same numbers as the Backtester
gives directly, and the runner's limits (403, 404, 422, 429), progress and failures.

The candles are synthetic, written into the temporary repository's data/ (conftest); the end to
end runs go through worker processes as the server's do, the rest through threads."""
import io
import contextlib
import json
import threading
import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("httpx")                 # POST needs FastAPI's TestClient

from midfreq.api import runs                                          # noqa: E402
from midfreq.api import schemas as s                                  # noqa: E402
from midfreq.api.convert import plain                                 # noqa: E402
from midfreq.api.routers.jobs import get_job_runner                   # noqa: E402
from midfreq.api.runner import JobRunner                              # noqa: E402
from midfreq.api.settings import get_settings                         # noqa: E402
from midfreq.backtest.backtester import Backtester                    # noqa: E402
from midfreq.backtest.optimizer import OBJECTIVES, generate_param_grid  # noqa: E402
from midfreq.core.candle import Timeframe                             # noqa: E402
from midfreq.core.portfolio import Portfolio                          # noqa: E402
from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer  # noqa: E402
from midfreq.data.storage import DataStorage                          # noqa: E402
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY    # noqa: E402

SYMBOLS = ["US.AAA", "US.BBB", "US.CCC"]          # conftest's sp100 universe
START, END = "2026-01-05", "2026-04-30"


def _candles(seed: int) -> pd.DataFrame:
    """Seven hourly candles each weekday, a random walk."""
    days = pd.bdate_range(START, END)
    idx = pd.DatetimeIndex([d + pd.Timedelta(minutes=870 + 60 * h) for d in days for h in range(7)],
                           name="timestamp").tz_localize("UTC")
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, len(idx))),
                         "low": np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, len(idx))), "close": close,
                         "volume": rng.integers(1_000, 5_000, len(idx)).astype(float)}, index=idx)


@pytest.fixture
def market(settings):
    """Four months of hourly candles for the three sp100 symbols (replacing conftest's ten)."""
    for i, symbol in enumerate(SYMBOLS):
        folder = settings.data_dir / symbol.replace(".", "_")
        folder.mkdir(exist_ok=True)
        _candles(seed=i + 1).to_parquet(folder / "1h.parquet")
    return settings


@pytest.fixture(scope="module")
def process_runner():
    runner = JobRunner(max_concurrent=2, workers="process")
    yield runner
    runner.shutdown()


@pytest.fixture
def thread_runner():
    runner = JobRunner(max_concurrent=1, workers="thread")
    yield runner
    runner.shutdown()


@pytest.fixture
def use(app, client, market):
    """use(runner) -> the client, with the app's jobs on that runner."""
    def _use(runner):
        app.dependency_overrides[get_job_runner] = lambda: runner
        return client
    return _use


def body(**overrides):
    return {"market": "US", "symbol": "US.AAA", "universe": "sp100", "timeframe": "1h",
            "start": "2026-01-05", "end": "2026-04-30", **overrides}


def wait(client, job_id, until=("done", "failed"), timeout=60.0, check=None):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until and (check is None or check(job)):
            return job
        assert time.monotonic() < deadline, f"job still {job['status']}: {job['message']}"
        time.sleep(0.02)


def submit(client, kind, payload, expect=202):
    r = client.post(f"/api/jobs/{kind}", json=payload)
    assert r.status_code == expect, r.text
    return r.json()


# ── capabilities ────────────────────────────────────────────────────────────

def test_capabilities_list_the_kinds_objectives_and_what_each_market_has_cached(client, app, settings, market):
    caps = client.get("/api/jobs/capabilities").json()
    assert caps["kinds"] == ["backtest", "compare", "optimize"]
    assert caps["max_concurrent"] == 1
    assert caps["objectives"] == OBJECTIVES
    us, hk = caps["markets"]["US"], caps["markets"]["HK"]
    assert (us["commission_rate"], us["currency"]) == (0.00005, "USD")
    assert (hk["commission_rate"], hk["currency"]) == (Portfolio.HK_FEE_RATE, "HKD") == (0.0016, "HKD")
    assert us["symbols"] == [{"symbol": sym, "timeframes": ["1h"]} for sym in SYMBOLS]
    assert hk["symbols"] == [{"symbol": "HK.00700", "timeframes": ["1d"]}]
    assert [(u["name"], u["symbols"], u["symbols_with_data"]) for u in us["universes"]] == [("sp100", 3, 3), ("tiny", 1, 1)]
    assert hk["universes"] == [{"name": "hk_live", "label": "HK — live roster (19)", "description": "HK live roster",
                                "symbols": 2, "symbols_with_data": 0}]

    app.dependency_overrides[get_settings] = lambda: replace(settings, job_kinds=("backtest", "nonsense"),
                                                             max_concurrent_jobs=3)
    caps = client.get("/api/jobs/capabilities").json()
    assert (caps["kinds"], caps["max_concurrent"]) == (["backtest"], 3)


# ── each kind, end to end, in worker processes ──────────────────────────────

def test_backtest_runs_in_a_worker_and_returns_metrics_equity_trades_and_the_chart(use, process_runner, market):
    client = use(process_runner)
    job = submit(client, "backtest", body(strategy="RSI", params={"rsi_period": 10}))
    assert (job["kind"], job["status"], job["progress"], job["backtest"]) == ("backtest", "queued", 0.0, None)
    assert job["request"]["strategy"] == "RSI" and job["request"]["start"] == "2026-01-05"

    job = wait(client, job["id"])
    assert job["status"] == "done", job["error"]
    assert job["progress"] == 1.0 and job["error"] is None
    created, started, finished = (pd.Timestamp(job[k]) for k in ("created", "started", "finished"))
    assert created <= started <= finished
    bt = job["backtest"]
    assert (bt["strategy"], bt["symbols"]) == ("RSI", ["US.AAA"])
    assert bt["params"] == {"rsi_period": 10, "oversold": 30, "overbought": 70}       # defaults filled in
    assert {"return_pct", "sharpe_ratio", "max_drawdown", "win_rate", "total_trades", "profit_factor",
            "benchmark_return_pct", "alpha", "final_equity"} <= set(bt["metrics"])
    assert bt["metrics"]["total_trades"] == len(bt["trades"]) > 0
    assert job["message"] == f"RSI: {bt['metrics']['total_trades']} trades, {bt['metrics']['return_pct']:+.2f}%"
    assert len(bt["equity"]) == len(_candles(1))                                   # one point per candle
    sells = [t for t in bt["trades"] if t["action"] == "SELL"]
    assert sells and all(t["pnl"] is not None for t in sells)

    chart = bt["chart"]
    assert chart["symbol"] == "US.AAA" and len(chart["candles"]) == len(_candles(1))
    assert [(i["name"], i["pane"]) for i in chart["indicators"]] == [
        ("RSI", "lower"), ("Overbought (70)", "lower"), ("Oversold (30)", "lower")]
    held = bt["benchmark"]                             # buy and hold of the stock, as the metrics measure it
    assert held["symbols"] == ["US.AAA"] and len(held["equity"]) == len(bt["equity"])
    assert held["equity"][-1]["v"] == pytest.approx(100_000.0 * (1 + bt["metrics"]["benchmark_return_pct"] / 100))
    rsi = chart["indicators"][0]["points"]
    close = _candles(1)["close"]
    delta = close.diff()
    expected = 100 - 100 / (1 + delta.clip(lower=0).rolling(10).mean() / (-delta.clip(upper=0)).rolling(10).mean())
    assert [p["v"] for p in rsi] == pytest.approx(expected.dropna().tolist())
    assert [p["v"] for p in chart["indicators"][1]["points"]] == [70.0, 70.0]

    listed = client.get("/api/jobs").json()
    assert listed[0]["id"] == job["id"] and listed[0]["status"] == "done"
    assert all(j["backtest"] is None and j["compare"] is None and j["optimize"] is None for j in listed)


def _direct(settings, strategy, params, symbols, start, end, *, capital=100_000.0, commission=0.00005,
            slippage=0.0, stop=None, trailing=None, take=None, max_dd=0.10, sizer=None):
    """The library calls a backtest makes, written out."""
    portfolio = Portfolio(initial_cash=capital, commission_rate=commission)
    risk = RiskManager(stop_loss_pct=stop, trailing_stop_pct=trailing, take_profit_pct=take, max_drawdown_pct=max_dd,
                       position_sizer=sizer)
    bt = Backtester(storage=DataStorage(str(settings.data_dir)), portfolio=portfolio, risk_manager=risk,
                    slippage_bps=slippage)
    with contextlib.redirect_stdout(io.StringIO()):
        metrics = bt.run(STRATEGY_REGISTRY[strategy]["class"], symbols=symbols, timeframe=Timeframe.HOUR_1,
                         start_date=pd.Timestamp(start).date(), end_date=pd.Timestamp(end).date(), **params)
    return metrics, portfolio


@pytest.mark.parametrize("case", ["single stock, stops, slippage, percent risk", "basket, equal dollar"])
def test_a_backtest_through_the_api_equals_a_direct_backtester_run(use, process_runner, market, case):
    client = use(process_runner)
    if case.startswith("single"):
        params = {"lookback": 15, "entry_z": 1.5, "exit_z": 0.3}
        payload = body(strategy="Z-Score Mean Reversion", params=params, start="2026-02-02", end="2026-04-15",
                       initial_capital=50_000.0, slippage_bps=5.0,
                       risk={"stop_loss_pct": 0.03, "trailing_stop_pct": 0.05, "take_profit_pct": 0.08,
                             "max_drawdown_pct": 0.2, "sizing": "percent_risk", "risk_pct": 0.05})
        metrics, portfolio = _direct(market, "Z-Score Mean Reversion", params, ["US.AAA"], "2026-02-02", "2026-04-15",
                                     capital=50_000.0, slippage=5.0, stop=0.03, trailing=0.05, take=0.08, max_dd=0.2,
                                     sizer=create_position_sizer(SizingMethod.FIXED_FRACTIONAL, risk_pct=0.05))
    else:
        params = {"lookback": 10, "top_n": 2, "rebalance_every": 6}
        payload = body(strategy="Cross-Sectional Reversal", params=params, commission_rate=0.001, symbol=None)
        metrics, portfolio = _direct(market, "Cross-Sectional Reversal", params, SYMBOLS, START, END, commission=0.001,
                                     sizer=create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=2))
    job = wait(client, submit(client, "backtest", payload)["id"])
    assert job["status"] == "done", job["error"]
    bt = job["backtest"]

    assert metrics["total_trades"] > 0
    assert bt["metrics"] == json.loads(json.dumps(plain(metrics)))
    assert bt["symbols"] == (SYMBOLS if case.startswith("basket") else ["US.AAA"])
    assert [p["v"] for p in bt["equity"]] == [e["equity"] for e in portfolio.equity_curve_detailed]
    assert [pd.Timestamp(p["t"]) for p in bt["equity"]] == [e["timestamp"] for e in portfolio.equity_curve_detailed]
    assert [(t["symbol"], t["action"], t["qty"], t["price"], t["commission"]) for t in bt["trades"]] == [
        (t["symbol"], t["action"], t["qty"], t["price"], t["commission"]) for t in portfolio.trade_history]


def test_compare_runs_each_strategy_and_ranks_them_by_return(use, process_runner, market):
    client = use(process_runner)
    job = submit(client, "compare", body(strategies=[{"strategy": "RSI"}, {"strategy": "RSI", "params": {"rsi_period": 7}},
                                                     {"strategy": "MACD"}]))
    job = wait(client, job["id"])
    assert job["status"] == "done", job["error"]
    rows = job["compare"]["rows"]
    assert sorted(r["strategy"] for r in rows) == ["MACD", "RSI #1", "RSI #2"]
    returns = [r["metrics"]["return_pct"] for r in rows]
    assert returns == sorted(returns, reverse=True)
    by_label = {r["strategy"]: r for r in rows}
    assert by_label["RSI #2"]["params"]["rsi_period"] == 7 and by_label["RSI #1"]["params"]["rsi_period"] == 14
    assert list(job["compare"]["equity"]) == ["RSI #1", "RSI #2", "MACD"]     # run order
    assert all(len(curve) == len(_candles(1)) for curve in job["compare"]["equity"].values())
    assert job["message"] == "Compared 3 strategies"
    # the yardstick: holding the stock they all traded, which every row's alpha is measured against
    held = job["compare"]["benchmark"]
    assert held["symbols"] == ["US.AAA"] and len(held["equity"]) == len(_candles(1))
    for row in rows:
        assert held["equity"][-1]["v"] == pytest.approx(100_000.0 * (1 + row["metrics"]["benchmark_return_pct"] / 100))


def test_a_basket_runs_on_its_universe_against_holding_the_universe_in_equal_amounts(use, process_runner, market):
    """No one stock's price chart: the equity is compared with the equal-weight universe, which
    is the backtester's own benchmark (its last value gives benchmark_return_pct)."""
    client = use(process_runner)
    job = wait(client, submit(client, "backtest", body(strategy="Cross-Sectional Reversal", symbol=None))["id"])
    assert job["status"] == "done", job["error"]
    bt = job["backtest"]
    assert bt["symbols"] == SYMBOLS and bt["chart"] is None
    held = bt["benchmark"]
    assert held["symbols"] == SYMBOLS
    assert [p["t"] for p in held["equity"]] == [p["t"] for p in bt["equity"]]                # every candle
    assert held["equity"][0]["v"] == pytest.approx(100_000.0)
    assert held["equity"][-1]["v"] == pytest.approx(100_000.0 * (1 + bt["metrics"]["benchmark_return_pct"] / 100))
    closes = pd.DataFrame({sym: _candles(i + 1)["close"] for i, sym in enumerate(SYMBOLS)})
    expected = (closes / closes.iloc[0] * 100_000.0 / 3).sum(axis=1)
    assert [p["v"] for p in held["equity"]] == pytest.approx(expected.tolist())
    assert job["request"]["symbol"] is None and job["request"]["universe"] == "sp100"

    job = wait(client, submit(client, "compare", body(symbol=None, strategies=[
        {"strategy": "Cross-Sectional Reversal"}, {"strategy": "Cross-Sectional Momentum"}]))["id"])
    assert job["status"] == "done", job["error"]
    compared = job["compare"]
    assert sorted(r["strategy"] for r in compared["rows"]) == ["Cross-Sectional Momentum", "Cross-Sectional Reversal"]
    assert compared["benchmark"] == held                                                     # the same universe


def test_the_benchmark_holds_a_symbol_from_its_first_candle_in_the_dates(settings):
    """Two symbols, the second starting an hour later: until then its half of the capital is
    cash. A candle after the end date is left out, as the backtester leaves it out."""
    idx = pd.date_range("2026-03-02 14:30", periods=3, freq="h", tz="UTC", name="timestamp")
    for symbol, t, closes in (("US.XXX", list(idx) + [pd.Timestamp("2026-03-03 14:30", tz="UTC")], [10, 20, 15, 99]),
                              ("US.YYY", idx[1:], [50, 40])):
        folder = settings.data_dir / symbol.replace(".", "_")
        folder.mkdir()
        c = np.array(closes, dtype=float)
        pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0},
                     index=pd.DatetimeIndex(t, name="timestamp")).to_parquet(folder / "1h.parquet")
    plan = runs.Plan(kind="backtest", data_dir=str(settings.data_dir), market="US", symbol=None, timeframe="1h",
                     start=pd.Timestamp("2026-03-02").date(), end=pd.Timestamp("2026-03-02").date(),
                     initial_capital=1_000.0, commission_rate=0.0, slippage_bps=0.0, risk={}, runs=())
    held = runs.benchmark(plan, DataStorage(str(settings.data_dir)), ["US.XXX", "US.YYY", "US.ZZZ"])
    assert held.symbols == ["US.XXX", "US.YYY"]                    # US.ZZZ has no candles
    # 50 shares of XXX at 10 and, from the second candle, 10 of YYY at 50
    assert [(p.t, p.v) for p in held.equity] == [(idx[0], 500 + 500), (idx[1], 1_000 + 500), (idx[2], 750 + 400)]
    assert runs.benchmark(replace(plan, start=pd.Timestamp("2027-01-01").date(), end=pd.Timestamp("2027-02-01").date()),
                          DataStorage(str(settings.data_dir)), ["US.XXX"]) is None


def test_grid_search_returns_the_combinations_best_first(use, process_runner, market):
    client = use(process_runner)
    job = wait(client, submit(client, "optimize", body(strategy="RSI", method="grid", objective="return_pct",
                                                       max_combinations=5))["id"])
    assert job["status"] == "done", job["error"]
    opt = job["optimize"]
    assert (opt["method"], opt["objective"], opt["windows"]) == ("grid", "return_pct", [])
    assert 0 < len(opt["rows"]) <= 5
    assert all(set(r["params"]) == {"rsi_period", "oversold", "overbought"} for r in opt["rows"])
    assert all(r["metrics"]["_objective"] == r["metrics"]["return_pct"] for r in opt["rows"])
    traded = [r["metrics"]["_objective"] for r in opt["rows"] if r["metrics"]["total_trades"] > 0]
    assert traded == sorted(traded, reverse=True)
    assert opt["summary"]["grid_size"] == len(generate_param_grid("RSI"))
    assert opt["summary"]["best_params"] == opt["rows"][0]["params"]
    assert job["message"] == f"Tested {len(opt['rows'])}/{len(opt['rows'])} combinations"


def test_walk_forward_returns_each_window(use, process_runner, market):
    client = use(process_runner)
    job = wait(client, submit(client, "optimize", body(strategy="Ensemble Voting", method="walk_forward",
                                                       n_splits=2, train_pct=0.6))["id"])
    assert job["status"] == "done", job["error"]
    opt = job["optimize"]
    assert opt["method"] == "walk_forward" and opt["rows"] == []
    assert [w["index"] for w in opt["windows"]] == [1, 2]
    first = opt["windows"][0]
    assert first["train_start"] == "2026-01-05T00:00:00Z"
    assert first["train_start"] < first["train_end"] == first["test_start"] < first["test_end"]
    assert set(first["params"]) == {"consensus_threshold"}
    assert "return_pct" in first["test_metrics"]
    assert opt["summary"]["total_windows"] == 2
    assert opt["summary"]["oos_returns"] == [w["test_metrics"]["return_pct"] for w in opt["windows"]]
    assert opt["summary"]["objective_label"] == OBJECTIVES["sharpe_ratio"]
    assert job["message"] == "Walk-forward complete"


# ── progress, limits and failures (threads, so the tests can hold a run) ────

class Gate:
    """A target that reports half-way, then waits to be let through to the real run."""

    def __init__(self):
        self.go = threading.Event()

    def __call__(self, plan, progress):
        progress(0.5, "half-way")
        assert self.go.wait(10)
        return runs.execute(plan, progress)


def test_progress_moves_from_queued_through_running_to_done(use, market):
    gate = Gate()
    runner = JobRunner(workers="thread", target=gate)
    client = use(runner)
    try:
        job = submit(client, "backtest", body(strategy="SMA Crossover"))
        assert job["status"] == "queued"
        held = wait(client, job["id"], until=("running",), check=lambda j: j["progress"] == 0.5)
        assert held["message"] == "half-way" and held["started"] is not None and held["finished"] is None
        gate.go.set()
        done = wait(client, job["id"])
        assert (done["status"], done["progress"]) == ("done", 1.0)
    finally:
        runner.shutdown()


@pytest.mark.parametrize("payload, expected", [
    (body(strategy="RSI", method="grid", max_combinations=6), [f"Tested {i}/6 combinations" for i in range(1, 7)]),
    (body(strategy="Ensemble Voting", method="walk_forward", n_splits=2), ["Window 1/2", "Window 2/2", "Walk-forward complete"]),
])
def test_optimize_reports_progress_per_combination_or_window(use, market, payload, expected):
    seen = []

    def recording(plan, progress):
        return runs.execute(plan, lambda f, m: (seen.append((f, m)), progress(f, m)))

    runner = JobRunner(workers="thread", target=recording)
    client = use(runner)
    try:
        assert wait(client, submit(client, "optimize", payload)["id"])["status"] == "done"
    finally:
        runner.shutdown()
    assert [m for _, m in seen] == expected
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0 and fractions[0] < 1.0


def test_a_run_beyond_max_concurrent_is_429(use, market):
    gate = Gate()
    runner = JobRunner(max_concurrent=1, workers="thread", target=gate)
    client = use(runner)
    try:
        first = submit(client, "backtest", body(strategy="RSI"))
        r = client.post("/api/jobs/compare", json=body(strategies=[{"strategy": "RSI"}, {"strategy": "MACD"}]))
        assert r.status_code == 429 and "at most 1" in r.json()["detail"]
        gate.go.set()
        assert wait(client, first["id"])["status"] == "done"
        second = submit(client, "backtest", body(strategy="RSI"))        # room again
        assert wait(client, second["id"])["status"] == "done"
    finally:
        runner.shutdown()


def test_a_kind_this_server_does_not_allow_is_403(use, app, settings, thread_runner):
    client = use(thread_runner)
    app.dependency_overrides[get_settings] = lambda: replace(settings, job_kinds=("backtest", "compare"))
    r = client.post("/api/jobs/optimize", json=body(strategy="RSI"))
    assert r.status_code == 403 and "optimize" in r.json()["detail"]
    assert client.get("/api/jobs").json() == []


def test_an_unknown_job_is_404(use, thread_runner):
    client = use(thread_runner)
    assert client.get("/api/jobs/nope").status_code == 404


@pytest.mark.parametrize("kind, payload, says", [
    ("backtest", body(strategy="No Such Strategy"), "no strategy"),
    ("backtest", body(strategy="RSI", params={"rsi_periodd": 3}), "no parameter rsi_periodd"),
    ("backtest", body(strategy="RSI", params={"rsi_period": 500}), "between 2 and 50"),
    ("backtest", body(strategy="RSI", params={"rsi_period": 7.5}), "whole number"),
    ("backtest", body(strategy="RSI", params={"rsi_period": "7"}), "must be a number"),
    ("backtest", body(strategy="RSI", symbol="../US_AAA"), "not a symbol"),
    ("backtest", body(strategy="RSI", symbol="US.ZZZ"), "no cached 1h candles for US.ZZZ"),
    ("backtest", body(strategy="RSI", timeframe="1d"), "no cached 1d candles for US.AAA"),
    ("backtest", body(strategy="RSI", symbol="HK.00700"), "not a US symbol"),
    ("backtest", body(strategy="Cross-Sectional Reversal", universe=None), "needs one"),
    ("backtest", body(strategy="Cross-Sectional Reversal", universe="nowhere"), "no universe"),
    ("backtest", body(strategy="Cross-Sectional Reversal", universe="hk_live"), "HK universe"),
    ("backtest", body(strategy="RSI", symbol=None), "symbol: RSI trades one stock, so it needs a symbol"),
    ("backtest", body(strategy="Cross-Sectional Reversal"),
     "symbol: Cross-Sectional Reversal ranks the whole sp100 universe, so it takes no symbol"),
    ("backtest", body(strategy="Cross-Sectional Reversal", symbol=None, universe="tiny"),
     "universe: Cross-Sectional Reversal holds 2 of the stocks it ranks, so it needs more than 2 to rank; tiny has 1 "
     "with cached 1h candles"),
    ("backtest", body(strategy="Cross-Sectional Momentum", symbol=None, params={"top_n": 3}),
     "needs more than 3 to rank; sp100 has 3"),
    ("optimize", body(strategy="Cross-Sectional Reversal", symbol=None),
     "universe: the search tries Cross-Sectional Reversal with baskets of up to 4, so it needs more than 4"),
    ("compare", body(strategies=[{"strategy": "RSI"}, {"strategy": "Cross-Sectional Reversal"}]),
     "strategies: cross-sectional strategies (Cross-Sectional Reversal) rank a universe of stocks against each other, "
     "so they are compared only with each other, not with single-stock strategies (RSI)"),
    ("compare", body(symbol=None, universe="tiny", strategies=[{"strategy": "Cross-Sectional Reversal"},
                                                               {"strategy": "Cross-Sectional Momentum"}]),
     "needs more than 2 to rank; tiny has 1"),
    ("backtest", body(strategy="Pairs Trading"), "needs a second symbol"),
    ("backtest", body(strategy="RSI", start="2026-04-30", end="2026-01-05"), "after end"),
    ("backtest", body(strategy="RSI", risk={"stop_loss_pct": 3}), "risk.stop_loss_pct"),
    ("compare", body(strategies=[{"strategy": "RSI"}, {"strategy": "MACD", "params": {"fast_ema": 1}}]), "MACD: fast_ema"),
    ("optimize", body(strategy="RSI", objective="luck"), "no objective"),
    ("optimize", body(strategy="RSI", method="walk_forward", n_splits=5, start="2026-03-01"), "too short"),
])
def test_runs_that_cannot_be_done_are_422_before_they_start(use, thread_runner, settings, kind, payload, says):
    client = use(thread_runner)
    before = sorted(p.name for p in settings.data_dir.iterdir())
    r = client.post(f"/api/jobs/{kind}", json=payload)
    assert r.status_code == 422 and says in r.json()["detail"], r.text
    assert client.get("/api/jobs").json() == []
    assert sorted(p.name for p in settings.data_dir.iterdir()) == before      # nothing created in the cache


def test_a_run_with_no_candles_in_its_dates_fails_with_the_reason(use, process_runner):
    client = use(process_runner)
    job = wait(client, submit(client, "backtest", body(strategy="RSI", start="2025-01-01", end="2025-02-01"))["id"])
    assert job["status"] == "failed" and job["backtest"] is None
    assert job["error"] == "no cached 1h candles for US.AAA between 2025-01-01 and 2025-02-01"
    assert job["finished"] is not None


def test_an_unexpected_error_fails_the_job_and_the_runner_carries_on(use, market):
    calls = []

    def flaky(plan, progress):
        calls.append(plan)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return runs.execute(plan, progress)

    runner = JobRunner(workers="thread", target=flaky)
    client = use(runner)
    try:
        failed = wait(client, submit(client, "backtest", body(strategy="RSI"))["id"])
        assert (failed["status"], failed["error"]) == ("failed", "RuntimeError: boom")
        assert wait(client, submit(client, "backtest", body(strategy="RSI"))["id"])["status"] == "done"
    finally:
        runner.shutdown()


def test_the_server_has_one_runner_of_worker_processes_and_stops_it_on_shutdown(app, market):
    from fastapi.testclient import TestClient
    from midfreq.api.routers import jobs as jobs_router

    with TestClient(app) as client:                     # as a context manager: startup and shutdown run
        job = wait(client, submit(client, "backtest", body(strategy="MACD"))["id"])
        assert job["status"] == "done", job["error"]
        runner = jobs_router._runners[1]
        assert runner.workers == "process" and runner.max_concurrent == 1
        assert [j["id"] for j in client.get("/api/jobs").json()] == [job["id"]]
    assert jobs_router._runners == {} and runner._pool is None


def test_the_registry_keeps_the_most_recent_jobs(market):
    runner = JobRunner(workers="thread", keep=3, target=lambda plan, progress: None)
    plan = runs.plan("backtest", s.BacktestRequest(**body(strategy="RSI")), market)
    try:
        ids = []
        for _ in range(5):
            ids.append(runner.submit("backtest", {}, plan).id)
            deadline = time.monotonic() + 5
            while runner.active() and time.monotonic() < deadline:
                time.sleep(0.01)
        assert [j.id for j in runner.jobs()] == ids[:1:-1]          # the newest three, newest first
        assert runner.get(ids[0]) is None and runner.get(ids[-1]).status == "done"
    finally:
        runner.shutdown()
