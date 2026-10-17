"""The run store: saved runs read back whole, atomic saves, ids, listing, the optimizer's hooks,
promotion into results/, and the Cloud Storage store against a fake bucket."""
import io
import json
import contextlib
import os
import time
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from midfreq.backtest import runstore as rs
from midfreq.backtest.backtester import Backtester
from midfreq.backtest.optimizer import grid_search, walk_forward
from midfreq.backtest.runstore import (GcsRunStore, LocalRunStore, RunRecord, default_run_store, promote,
                                       record_from_backtest, record_from_compare, record_from_walk_forward)
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

SYMBOLS = ["AAA", "BBB"]
START, END = datetime(2026, 1, 1), datetime(2026, 8, 20)
STRATEGY = "Ensemble Voting"            # three combinations: a grid search or walk-forward in well under a second


@pytest.fixture
def storage(tmp_path):
    """240 daily candles of a random walk for each symbol."""
    with contextlib.redirect_stdout(io.StringIO()):
        storage = DataStorage(base_path=str(tmp_path / "data"))
        for i, symbol in enumerate(SYMBOLS):
            rng = np.random.default_rng(i)
            idx = pd.date_range("2026-01-01", periods=240, freq="D", tz="UTC")
            close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx))))
            storage.save_data(pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                                            "close": close, "volume": 1000.0}, index=idx), symbol, "1d")
    return storage


@pytest.fixture
def store(tmp_path):
    return LocalRunStore(tmp_path / "runs")


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def backtest(storage, slippage_bps=5.0, risk_manager=None):
    portfolio = Portfolio(initial_cash=100_000.0, commission_rate=0.00005)
    metrics = quiet(Backtester(storage, portfolio, risk_manager=risk_manager, slippage_bps=slippage_bps).run,
                    STRATEGY_REGISTRY["SMA Crossover"]["class"], ["AAA"], Timeframe.DAY_1,
                    start_date=START, end_date=END, fast_period=3, slow_period=10)
    return portfolio, metrics


def small_record(kind="backtest", created=None, **meta):
    return RunRecord(kind=kind, meta={"strategy": "SMA Crossover", **meta}, metrics={"return_pct": 1.5},
                     tables={"equity": pd.DataFrame({"t": pd.date_range("2026-01-01", periods=3, tz="UTC"),
                                                     "equity": [1.0, 2.0, 3.0]})},
                     created=created)


def wf(storage, **kwargs):
    return quiet(walk_forward, STRATEGY, SYMBOLS, Timeframe.DAY_1, START, END, storage, n_splits=2,
                 commission_rate=0.00005, slippage_bps=5.0, **kwargs)


def grid(storage, **kwargs):
    return quiet(grid_search, STRATEGY, SYMBOLS, Timeframe.DAY_1, START, END, storage, objective="return_pct",
                 commission_rate=0.00005, **kwargs)


# ── saving and reading back ─────────────────────────────────────────────────

def test_a_saved_backtest_reads_back_whole(storage, store):
    risk = RiskManager(stop_loss_pct=0.05, max_drawdown_pct=0.2,
                       position_sizer=create_position_sizer(SizingMethod.FIXED_QUANTITY, qty=50))
    portfolio, metrics = backtest(storage, risk_manager=risk)
    record = record_from_backtest(portfolio, metrics, strategy=STRATEGY_REGISTRY["SMA Crossover"]["class"],
                                  params={"fast_period": 3, "slow_period": 10}, symbols=["AAA"],
                                  timeframe=Timeframe.DAY_1, start=START, end=END, risk=risk, source="cli")
    run_id = store.save(record)
    assert rs.RUN_ID.fullmatch(run_id) and "_backtest_" in run_id
    assert record.run_id == run_id and record.created.tzinfo is not None
    assert run_id.startswith(record.created.strftime("%Y%m%dT%H%M%S"))

    manifest = json.loads((store.root / run_id / "run.json").read_text())
    assert manifest["kind"] == "backtest" and manifest["source"] == "cli"
    assert manifest["strategy"] == "SMA Crossover"                     # the registry name, from the class
    assert manifest["params"] == {"fast_period": 3, "slow_period": 10}
    assert (manifest["symbols"], manifest["n_symbols"], manifest["timeframe"]) == (["AAA"], 1, "1d")
    assert (manifest["start"], manifest["end"]) == ("2026-01-01T00:00:00+00:00", "2026-08-20T00:00:00+00:00")
    assert (manifest["commission_rate"], manifest["slippage_bps"], manifest["initial_capital"]) == (0.00005, 5.0, 100_000.0)
    assert manifest["risk"] == {"stop_loss_pct": 0.05, "trailing_stop_pct": None, "take_profit_pct": None,
                                "max_drawdown_pct": 0.2, "sizer": "FixedQuantitySizer", "sizer_params": {"qty": 50}}
    # the last candle replayed: the run ends on 20 Aug, inclusive
    assert manifest["data_cutoff"] == "2026-08-20T00:00:00+00:00"
    assert {"git_commit", "package_version", "host"} <= set(manifest)
    assert manifest["package_version"] == version("midfreq-backtest")    # whichever release is installed

    back = store.load(run_id)
    assert (back.kind, back.run_id, back.created) == ("backtest", run_id, record.created)
    assert back.metrics["return_pct"] == metrics["return_pct"]
    assert back.metrics["total_trades"] == metrics["total_trades"] > 0
    assert "open_positions" not in back.metrics                          # a dict: kept as a table instead
    assert back.meta["strategy"] == "SMA Crossover"
    equity = back.tables["equity"]
    assert list(equity.columns) == ["t", "equity"]
    assert len(equity) == len(portfolio.equity_curve_detailed)
    assert equity["equity"].tolist() == [e["equity"] for e in portfolio.equity_curve_detailed]
    assert str(equity["t"].dtype) == "datetime64[ns, UTC]"
    trades = store.table(run_id, "trades")
    assert len(trades) == len(portfolio.trade_history)
    assert trades["price"].tolist() == [t["price"] for t in portfolio.trade_history]
    assert list(trades.columns[:7]) == ["timestamp", "symbol", "action", "qty", "price", "commission", "cash_after"]
    pd.testing.assert_frame_equal(trades, back.tables["trades"])
    one_row = store.table(run_id, "metrics")
    assert len(one_row) == 1 and one_row["return_pct"].iloc[0] == metrics["return_pct"]

    store.delete(run_id)
    assert store.list().empty
    for call in (lambda: store.load(run_id), lambda: store.table(run_id, "equity"), lambda: store.delete(run_id)):
        with pytest.raises(KeyError):
            call()
    assert [p.name for p in store.root.iterdir()] == []                # nothing left behind


def test_a_run_with_no_trades_and_no_metrics_still_saves(store):
    portfolio = Portfolio()
    run_id = store.save(record_from_backtest(portfolio, {}, strategy="RSI", params={}, symbols=["AAA"], timeframe="1d"))
    back = store.load(run_id)
    assert back.tables["trades"].empty and list(back.tables["trades"].columns[:3]) == ["timestamp", "symbol", "action"]
    assert back.tables["equity"].empty and back.meta["data_cutoff"] is None
    assert back.metrics == {"n_open_positions": 0}
    empty = small_record()
    empty.metrics = {}
    assert store.load(store.save(empty)).metrics == {}


def test_an_unknown_table_is_a_key_error(store):
    run_id = store.save(small_record())
    for bad in ["trades", "run", "../run", "run.json", "Equity", "equity.parquet", ""]:
        with pytest.raises(KeyError):
            store.table(run_id, bad)


@pytest.mark.parametrize("bad", ["", "..", "../runs", "20261016T043512_backtest_abc12", "20261016T043512_backtest_ABC123",
                                 "20261016T043512_optimize_abc123", "20261016T043512_backtest_abc123/../x",
                                 ".tmp-abc", None, 7])
def test_run_ids_are_checked_strictly(store, bad):
    store.save(small_record())
    for call in (lambda: store.load(bad), lambda: store.table(bad, "equity"), lambda: store.delete(bad)):
        with pytest.raises(KeyError):
            call()


def test_a_folder_without_a_manifest_is_not_a_run(store):
    store.save(small_record())
    planted = store.root / "20261016T043512_backtest_abc123"
    planted.mkdir()
    (planted / "equity.parquet").write_bytes(b"x")
    assert len(store.list()) == 1
    with pytest.raises(KeyError):
        store.load(planted.name)


def test_two_saves_in_the_same_second_get_different_ids(store):
    created = datetime(2026, 10, 16, 4, 35, 12, tzinfo=timezone.utc)
    a, b = store.save(small_record(created=created)), store.save(small_record(created=created))
    assert a != b and a[:15] == b[:15] == "20261016T043512"
    assert set(store.list()["run_id"]) == {a, b}


def test_metrics_that_are_not_finite_round_trip_through_strict_json(store):
    record = small_record()
    record.metrics = {"profit_factor": float("inf"), "wf_efficiency": None, "low": float("-inf"), "n": np.int64(3)}
    run_id = store.save(record)
    text = (store.root / run_id / "run.json").read_text()
    json.loads(text, parse_constant=lambda c: pytest.fail(f"run.json is not strict JSON: {c}"))
    assert store.load(run_id).metrics == {"profit_factor": float("inf"), "wf_efficiency": None,
                                          "low": float("-inf"), "n": 3}
    row = store.list().iloc[0]
    assert row["profit_factor"] == float("inf") and row["low"] == float("-inf")
    assert store.table(run_id, "metrics")["profit_factor"].iloc[0] == float("inf")


@pytest.mark.parametrize("change, error", [
    (lambda r: setattr(r, "kind", "optimize"), ValueError),
    (lambda r: r.meta.update(created="yesterday"), ValueError),
    (lambda r: r.meta.update(source="web"), ValueError),
    (lambda r: r.metrics.update(open_positions={"AAA": {"qty": 1}}), TypeError),
    (lambda r: r.tables.update({"../escape": pd.DataFrame()}), ValueError),
    (lambda r: r.tables.update(metrics=pd.DataFrame()), ValueError),
    (lambda r: r.tables.update(bad=[1, 2]), TypeError),
])
def test_a_record_the_store_cannot_hold_is_refused_and_nothing_is_written(store, change, error):
    record = small_record()
    change(record)
    with pytest.raises(error):
        store.save(record)
    assert store.list().empty
    assert not store.root.exists() or [p for p in store.root.iterdir() if not p.name.startswith(".")] == []


# ── atomic saves ────────────────────────────────────────────────────────────

def test_a_crash_mid_save_leaves_nothing_listed(store, monkeypatch):
    store.save(small_record())
    real, calls = rs._write, []

    def crash_on_second(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(rs, "_write", crash_on_second)
    record = small_record()
    record.tables["trades"] = pd.DataFrame({"qty": [1, 2]})
    with pytest.raises(OSError):
        store.save(record)
    assert len(store.list()) == 1 and record.run_id is None
    assert sorted(p.name for p in store.root.iterdir() if p.name.startswith(".")) == []   # cleaned up


def test_a_killed_save_is_never_listed_and_is_swept_later(store):
    run_id = store.save(small_record())
    # a writer killed after writing everything, before the rename: its temporary folder stays
    killed = store.root / ".tmp-killed"
    killed.mkdir()
    for f in (store.root / run_id).iterdir():
        (killed / f.name).write_bytes(f.read_bytes())
    assert list(store.list()["run_id"]) == [run_id]
    old = time.time() - 2 * LocalRunStore.STALE_TMP_SECONDS
    os.utime(killed, (old, old))
    store.save(small_record())
    assert not killed.exists()


def test_a_missing_root_lists_empty_and_is_not_created(tmp_path):
    store = LocalRunStore(tmp_path / "nowhere")
    df = store.list()
    assert df.empty and list(df.columns) == list(rs.LIST_COLUMNS)
    assert not (tmp_path / "nowhere").exists()
    with pytest.raises(KeyError):
        store.load("20261016T043512_backtest_abc123")


# ── listing ─────────────────────────────────────────────────────────────────

def test_the_list_is_newest_first_with_filters_and_the_headline_metrics(store):
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ids = [store.save(small_record(created=t0 + timedelta(hours=1))),
           store.save(small_record(kind="walk_forward", created=t0 + timedelta(hours=3), universe="sp100",
                                   symbols=["US.AAPL", "US.MSFT"], start="2023-08-22", end="2026-08-21",
                                   objective="sharpe_ratio")),
           store.save(small_record(created=t0 + timedelta(hours=2), strategy="RSI", source="api"))]
    record = small_record(kind="compare", created=t0)
    record.meta = {"strategy": None, "strategies": ["RSI", "MACD"]}
    record.metrics = {"best_strategy": "MACD", "return_pct": 0.5}
    ids.append(store.save(record))

    df = store.list()
    assert list(df["run_id"]) == [ids[1], ids[2], ids[0], ids[3]]
    assert list(df.columns[:len(rs.LIST_COLUMNS)]) == list(rs.LIST_COLUMNS)
    assert {"return_pct", "best_strategy"} <= set(df.columns)
    assert str(df["created"].dtype) == "datetime64[ns, UTC]"
    first = df.iloc[0]
    assert (first["kind"], first["universe"], first["n_symbols"], first["source"]) == ("walk_forward", "sp100", 2, "script")
    assert first["start"] == pd.Timestamp("2023-08-22", tz="UTC") and first["objective"] == "sharpe_ratio"

    assert list(store.list(kind="walk_forward")["run_id"]) == [ids[1]]
    assert list(store.list(strategy="RSI")["run_id"]) == [ids[2], ids[3]]      # a comparison counts its strategies
    assert list(store.list(strategy="SMA Crossover", kind="backtest")["run_id"]) == [ids[0]]
    assert list(store.list(limit=2)["run_id"]) == [ids[1], ids[2]]
    assert store.list(kind="grid_search").empty
    with pytest.raises(ValueError):
        store.list(kind="optimize")


def test_another_process_s_saves_and_deletes_show_in_the_list(store, tmp_path):
    other = LocalRunStore(tmp_path / "runs")
    a = store.save(small_record())
    assert list(other.list()["run_id"]) == [a]
    b = other.save(small_record())
    assert set(store.list()["run_id"]) == {a, b}
    other.delete(a)
    assert list(store.list()["run_id"]) == [b]
    with pytest.raises(KeyError):
        store.load(a)


# ── the optimizer's hooks ───────────────────────────────────────────────────

def test_walk_forward_saves_one_run_with_every_window_in_and_out_of_sample(storage, store):
    result = wf(storage, run_store=store, run_meta={"source": "api", "universe": "test", "market": "US"})
    runs = store.list()
    assert len(runs) == 1 and runs["kind"].iloc[0] == "walk_forward"
    run = store.load(runs["run_id"].iloc[0])
    assert run.meta["source"] == "api" and run.meta["universe"] == "test" and run.meta["market"] == "US"
    assert (run.meta["strategy"], run.meta["method"], run.meta["objective"]) == (STRATEGY, "walk_forward", "sharpe_ratio")
    assert (run.meta["n_splits"], run.meta["train_pct"], run.meta["param_grid_size"]) == (2, 0.7, 3)
    assert (run.meta["commission_rate"], run.meta["slippage_bps"], run.meta["symbols"]) == (0.00005, 5.0, SYMBOLS)

    windows = run.tables["windows"]
    assert list(windows["window"]) == [w.window_id for w in result["windows"]] == [1, 2]
    assert list(windows.columns[:6]) == ["window", "train_start", "train_end", "test_start", "test_end",
                                         "consensus_threshold"]
    for (_, row), w in zip(windows.iterrows(), result["windows"]):
        assert row["train_start"] == pd.Timestamp(w.train_start, tz="UTC")
        assert row["test_end"] == pd.Timestamp(w.test_end, tz="UTC")
        assert row["consensus_threshold"] == w.best_params["consensus_threshold"]
        for prefix, metrics in (("is_", w.train_metrics), ("oos_", w.test_metrics)):
            assert metrics and all(f"{prefix}{k}" in windows.columns for k in metrics)       # every key
            for k, v in metrics.items():
                if isinstance(v, dict):
                    assert json.loads(row[f"{prefix}{k}"]) == json.loads(json.dumps(v))
                else:
                    assert row[f"{prefix}{k}"] == v
    s = result["summary"]
    for key in ("avg_train_return", "avg_oos_return", "consistency_pct", "positive_windows", "total_windows",
                "train_return_per_day", "oos_return_per_day", "wf_efficiency", "n_windows"):
        assert run.metrics[key] == s[key]
    assert run.metrics["n_skipped"] == 0 and "oos_returns" not in run.metrics
    assert list(windows["oos_return_pct"]) == s["oos_returns"]
    # the data cut-off: the last candle of the last test segment (through its end day)
    last_test_end = pd.Timestamp(result["windows"][-1].test_end, tz="UTC")
    index = [storage.load_data(sym, "1d").index for sym in SYMBOLS]
    expected = max(ix[ix < last_test_end + pd.Timedelta(days=1)].max() for ix in index)
    assert pd.Timestamp(run.meta["data_cutoff"]) == expected


def test_grid_search_saves_one_run_with_its_grid(storage, store):
    df = grid(storage, run_store=store, run_meta={"universe": "test"})
    runs = store.list()
    assert len(runs) == 1 and runs["kind"].iloc[0] == "grid_search"
    run = store.load(runs["run_id"].iloc[0])
    pd.testing.assert_frame_equal(run.tables["grid"], df)
    assert run.meta["best_params"] == {"consensus_threshold": int(df["consensus_threshold"].iloc[0])}
    assert isinstance(run.meta["best_params"]["consensus_threshold"], int)
    assert (run.meta["method"], run.meta["objective"], run.meta["max_combinations"]) == ("grid", "return_pct", 500)
    assert run.metrics["return_pct"] == df["return_pct"].iloc[0]
    assert run.metrics["objective_value"] == df["_objective"].iloc[0]
    assert (run.metrics["combinations"], run.metrics["grid_size"]) == (3, 3)
    assert run.meta["data_cutoff"] == "2026-08-20T00:00:00+00:00"


def test_the_optimizer_returns_exactly_the_same_with_and_without_a_store(storage, store):
    assert repr(wf(storage, run_store=store)) == repr(wf(storage))
    with_store, without = grid(storage, run_store=store), grid(storage)
    pd.testing.assert_frame_equal(with_store, without)
    assert with_store.attrs == without.attrs == {}
    assert len(store.list()) == 2


def test_a_failed_save_warns_and_the_result_is_still_returned(storage):
    class Broken(LocalRunStore):
        def save(self, record):
            raise OSError("bucket unreachable")

    with pytest.warns(RuntimeWarning, match="not saved"):
        result = wf(storage, run_store=Broken("unused"))
    assert repr(result) == repr(wf(storage))


def test_an_optimization_that_ran_nothing_saves_nothing(storage, store):
    short = quiet(walk_forward, STRATEGY, SYMBOLS, Timeframe.DAY_1, START, START + timedelta(days=40), storage,
                  n_splits=2, run_store=store)
    assert short["windows"] == [] and "error" in short["summary"]
    assert quiet(grid_search, "No Such Strategy", SYMBOLS, Timeframe.DAY_1, START, END, storage,
                 run_store=store).empty
    assert store.list().empty


def test_a_walk_forward_read_back_from_json_records_the_same_windows(storage, store):
    """record_from_walk_forward takes windows as mappings too (how an imported result arrives)."""
    result = wf(storage)
    as_dicts = {"windows": [vars(w) for w in result["windows"]], "summary": result["summary"]}
    a = record_from_walk_forward(result, strategy=STRATEGY, symbols=SYMBOLS, timeframe="1d", start=START, end=END,
                                 objective="sharpe_ratio", n_splits=2, train_pct=0.7)
    b = record_from_walk_forward(as_dicts, strategy=STRATEGY, symbols=SYMBOLS, timeframe="1d", start=START, end=END,
                                 objective="sharpe_ratio", n_splits=2, train_pct=0.7)
    pd.testing.assert_frame_equal(a.tables["windows"], b.tables["windows"])
    with pytest.raises(ValueError):
        record_from_walk_forward({"windows": [], "summary": {}}, strategy=STRATEGY, symbols=SYMBOLS, timeframe="1d",
                                 start=START, end=END, objective="sharpe_ratio", n_splits=2, train_pct=0.7)


def test_a_comparison_records_each_strategy_and_one_long_equity_table(storage, store):
    entries = []
    for label, params in (("SMA fast", {"fast_period": 3, "slow_period": 10}),
                          ("SMA slow", {"fast_period": 10, "slow_period": 30})):
        portfolio = Portfolio(initial_cash=100_000.0, commission_rate=0.00005)
        metrics = quiet(Backtester(storage, portfolio).run, STRATEGY_REGISTRY["SMA Crossover"]["class"], ["AAA"],
                        Timeframe.DAY_1, start_date=START, end_date=END, **params)
        entries.append({"label": label, "strategy": "SMA Crossover", "params": params, "symbols": ["AAA"],
                        "portfolio": portfolio, "metrics": metrics})
    entries.append({"label": "No data", "strategy": "RSI", "params": {}, "symbols": ["ZZZ"], "metrics": {}})
    run_id = store.save(record_from_compare(entries, timeframe=Timeframe.DAY_1, start=START, end=END, source="api"))
    run = store.load(run_id)
    table = run.tables["strategies"]
    by_return = sorted(entries[:2], key=lambda e: e["metrics"]["return_pct"], reverse=True)
    assert list(table["label"]) == [e["label"] for e in by_return]
    assert json.loads(table["params"].iloc[0]) == by_return[0]["params"]
    assert run.metrics["best_strategy"] == by_return[0]["label"] and run.metrics["n_strategies"] == 2
    assert run.metrics["return_pct"] == by_return[0]["metrics"]["return_pct"]
    equity = run.tables["equity"]
    assert list(equity.columns) == ["label", "t", "equity"]
    assert equity.groupby("label").size().to_dict() == {e["label"]: len(e["portfolio"].equity_curve_detailed)
                                                        for e in entries[:2]}
    assert set(run.tables["trades"]["label"]) <= {"SMA fast", "SMA slow"}
    assert run.meta["strategies"] == ["SMA fast", "SMA slow"] and run.meta["skipped"] == ["No data"]
    assert run.meta["commission_rate"] == 0.00005 and run.meta["strategy"] is None
    assert list(store.list(strategy="SMA fast")["run_id"]) == [run_id]


# ── promotion and the command line ──────────────────────────────────────────

def test_promote_writes_json_and_csv_and_never_overwrites(storage, store, tmp_path):
    run_id = store.save(record_from_backtest(*backtest(storage), strategy="SMA Crossover",
                                             params={"fast_period": 3, "slow_period": 10}, symbols=["AAA"],
                                             timeframe="1d", start=START, end=END))
    out = tmp_path / "results"
    written = promote(run_id, store=store, results_dir=out)
    assert sorted(p.name for p in written) == sorted([f"run_{run_id}.json", f"run_{run_id}_equity.csv",
                                                      f"run_{run_id}_trades.csv"])
    summary = json.loads((out / f"run_{run_id}.json").read_text())
    assert summary["run_id"] == run_id and summary["kind"] == "backtest" and summary["strategy"] == "SMA Crossover"
    assert summary["metrics"]["return_pct"] == store.load(run_id).metrics["return_pct"]
    assert summary["tables"]["trades"]["file"] == f"run_{run_id}_trades.csv"
    trades = pd.read_csv(out / f"run_{run_id}_trades.csv", float_precision="round_trip")
    assert trades["price"].tolist() == store.table(run_id, "trades")["price"].tolist()

    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError):
        promote(run_id, store=store, results_dir=out)
    (out / f"run_{run_id}_equity.csv").unlink()                         # one missing, the rest still there
    with pytest.raises(FileExistsError):
        promote(run_id, store=store, results_dir=out)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {k: v for k, v in before.items() if "equity" not in k}
    with pytest.raises(KeyError):
        promote("20261016T043512_backtest_abc123", store=store, results_dir=out)


def test_the_command_line_lists_shows_promotes_and_removes(storage, store, tmp_path, capsys):
    root = str(store.root)
    assert rs.main(["--store", root, "list"]) == 0 and "no saved runs" in capsys.readouterr().out
    wf(storage, run_store=store)
    run_id = store.list()["run_id"].iloc[0]
    assert rs.main(["--store", root, "list", "--kind", "walk_forward"]) == 0
    out = capsys.readouterr().out
    assert run_id in out and STRATEGY in out and "avg_oos_return" in out
    assert rs.main(["--store", root, "show", run_id]) == 0
    out = capsys.readouterr().out
    assert "windows: 2 rows" in out and "avg_oos_return" in out
    assert rs.main(["--store", root, "promote", run_id, "--to", str(tmp_path / "results")]) == 0
    assert (tmp_path / "results" / f"run_{run_id}_windows.csv").is_file()
    assert rs.main(["--store", root, "promote", run_id, "--to", str(tmp_path / "results")]) == 1
    assert "not promoted" in capsys.readouterr().err
    assert rs.main(["--store", root, "show", "../../etc"]) == 1
    assert rs.main(["--store", root, "rm", run_id]) == 0
    assert store.list().empty


# ── which store ─────────────────────────────────────────────────────────────

def test_the_default_store_follows_midfreq_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MIDFREQ_RUNS", str(tmp_path / "elsewhere"))
    store = default_run_store()
    assert isinstance(store, LocalRunStore) and store.root == (tmp_path / "elsewhere").resolve()
    assert default_run_store() is store                                 # one per location
    monkeypatch.setenv("MIDFREQ_RUNS", "gs://runs-bucket/hosted/runs")
    gcs = default_run_store()
    assert isinstance(gcs, GcsRunStore) and (gcs.bucket_name, gcs.prefix) == ("runs-bucket", "hosted/runs")
    monkeypatch.delenv("MIDFREQ_RUNS")
    from midfreq.core.paths import repo_root
    assert default_run_store().root == repo_root() / "runs"


def test_the_commit_comes_from_midfreq_git_commit_when_set(monkeypatch, store):
    monkeypatch.setenv("MIDFREQ_GIT_COMMIT", "abc1234")
    rs._git.cache_clear()
    try:
        run_id = store.save(small_record())
        assert store.load(run_id).meta["git_commit"] == "abc1234"
        assert store.list()["git_commit"].iloc[0] == "abc1234"
    finally:
        rs._git.cache_clear()


# ── Cloud Storage, against a fake bucket ────────────────────────────────────

class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    @property
    def generation(self):
        return self.bucket.objects[self.name][1]

    def upload_from_string(self, data, content_type=None):
        if self.bucket.fail_on and self.name.endswith(self.bucket.fail_on):
            raise OSError("network down")
        data = data.encode() if isinstance(data, str) else data
        self.bucket.objects[self.name] = (data, self.bucket.objects.get(self.name, (b"", 0))[1] + 1)
        self.bucket.uploads.append(self.name)

    def download_as_bytes(self):
        self.bucket.downloads.append(self.name)
        return self.bucket.objects[self.name][0]

    def download_to_filename(self, path):
        self.bucket.downloads.append(self.name)
        Path(path).write_bytes(self.bucket.objects[self.name][0])

    def delete(self):
        self.bucket.deletes.append(self.name)
        del self.bucket.objects[self.name]


class _Bucket:
    """A fake Cloud Storage bucket: name -> (bytes, generation)."""
    def __init__(self):
        self.objects, self.uploads, self.downloads, self.deletes, self.listings = {}, [], [], [], []
        self.fail_on = None

    def blob(self, name):
        return _Blob(self, name)

    def list_blobs(self, prefix=""):
        self.listings.append(prefix)
        return [_Blob(self, n) for n in sorted(self.objects) if n.startswith(prefix)]


class _Client:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, name):
        assert name == "runs-bucket"
        return self._bucket


@pytest.fixture
def gcs(tmp_path):
    bucket, now = _Bucket(), [0.0]

    def make(name="a"):
        return GcsRunStore("gs://runs-bucket/hosted/runs", cache_dir=tmp_path / f"cache-{name}", max_age=30,
                           client_factory=lambda: _Client(bucket), clock=lambda: now[0])
    return make, bucket, now


def test_a_gcs_run_is_uploaded_manifest_last_and_reads_back(gcs, tmp_path):
    make, bucket, now = gcs
    store = make()
    record = small_record(kind="walk_forward", symbols=["US.AAPL"])
    record.tables["windows"] = pd.DataFrame({"window": [1, 2], "oos_return_pct": [0.5, -0.25]})
    run_id = store.save(record)
    names = [n for n in bucket.uploads if f"/{run_id}/" in n]
    assert names[-1] == f"hosted/runs/{run_id}/run.json"
    assert sorted(names) == sorted(f"hosted/runs/{run_id}/{f}" for f in
                                   ("equity.parquet", "windows.parquet", "metrics.parquet", "run.json"))
    assert list(store.list()["run_id"]) == [run_id]

    reader = make("b")                                                  # another process: the dashboard
    df = reader.list(kind="walk_forward")
    assert list(df["run_id"]) == [run_id] and df["n_symbols"].iloc[0] == 1
    back = reader.load(run_id)
    pd.testing.assert_frame_equal(back.tables["windows"], record.tables["windows"])
    assert back.metrics == {"return_pct": 1.5}
    assert (tmp_path / "cache-b" / run_id / "windows.parquet").is_file()
    n = len(bucket.downloads)
    reader.table(run_id, "windows")                                     # from the cache now
    assert len(bucket.downloads) == n
    assert reader.table(run_id, "metrics")["return_pct"].iloc[0] == 1.5
    for bad in ("../../etc/passwd", "nope"):
        with pytest.raises(KeyError):
            reader.table(run_id, bad)
    for bad in ("../x", "20261016T043512_backtest_abc123"):
        with pytest.raises(KeyError):
            reader.load(bad)
    assert not any(p.name.endswith(".part") for p in (tmp_path / "cache-b").rglob("*"))


def test_the_gcs_list_is_re_read_at_most_every_max_age_seconds(gcs):
    make, bucket, now = gcs
    writer, reader = make("w"), make("r")
    a = writer.save(small_record())
    assert list(reader.list()["run_id"]) == [a]
    full = lambda: bucket.listings.count("hosted/runs/")
    assert full() == 1
    b = writer.save(small_record(created=datetime(2027, 1, 1, tzinfo=timezone.utc)))
    assert list(reader.list()["run_id"]) == [a]                         # within max_age: the cached listing
    assert full() == 1
    assert reader.load(b).run_id == b                                   # but a run asked for by id is found
    now[0] = 31
    assert list(reader.list()["run_id"]) == [b, a]
    manifests = [d for d in bucket.downloads if d.endswith("run.json")]
    assert len(manifests) == len(set(manifests))                        # each manifest downloaded once


def test_a_partial_gcs_upload_is_never_listed(gcs):
    make, bucket, now = gcs
    store = make()
    bucket.fail_on = "run.json"
    with pytest.raises(OSError):
        store.save(small_record())
    assert bucket.objects == {}                                         # what was uploaded is removed again
    # and a writer that died before its manifest: tables but no run.json
    bucket.objects["hosted/runs/20261016T043512_backtest_abc123/equity.parquet"] = (b"x", 1)
    assert store.list().empty and make("other").list().empty
    with pytest.raises(KeyError):
        store.load("20261016T043512_backtest_abc123")


def test_a_gcs_delete_takes_the_manifest_first(gcs, tmp_path):
    make, bucket, now = gcs
    store = make()
    run_id = store.save(small_record())
    store.load(run_id)
    store.delete(run_id)
    assert bucket.deletes[0] == f"hosted/runs/{run_id}/run.json" and bucket.objects == {}
    assert store.list().empty and not (tmp_path / "cache-a" / run_id).exists()
    with pytest.raises(KeyError):
        store.delete(run_id)


def test_a_gcs_listing_that_fails_keeps_the_last_one(gcs):
    make, bucket, now = gcs
    store = make()
    run_id = store.save(small_record())
    assert list(make("r").list()["run_id"]) == [run_id]
    reader = make("r2")
    reader.list()

    def down(prefix=""):
        raise OSError("network down")

    bucket.list_blobs = down
    now[0] = 31
    assert list(reader.list()["run_id"]) == [run_id]
    with pytest.raises(OSError):
        make("cold").list()                                             # never listed: an error, not "no runs"


def test_a_gcs_store_needs_a_gs_url():
    with pytest.raises(ValueError):
        GcsRunStore("/tmp/runs")
    with pytest.raises(ValueError):
        GcsRunStore("gs://")
