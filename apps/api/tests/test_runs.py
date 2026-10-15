"""Saved runs: the Run history routes over a run store (conftest's MemoryRunStore), and every
finished job saved to it, with the job's numbers, as RunRecords.

The routes need no httpx (conftest's client does GET and DELETE); the jobs are posted, so those
tests need FastAPI's TestClient."""
import io
import json
import logging
import threading
import time
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from midfreq.api import deps
from midfreq.api.deps import get_run_store
from midfreq.api.routers.jobs import get_job_runner
from midfreq.api.runner import JobRunner
from midfreq.api.saved_runs import UnavailableRunStore
from midfreq.api.settings import get_settings
from midfreq.backtest.runstore import LocalRunStore, RunRecord

SYMBOLS = ["US.AAA", "US.BBB", "US.CCC"]          # conftest's sp100 universe
START, END = "2026-01-05", "2026-03-31"


def _record(kind="backtest", strategy="RSI", **metrics):
    t = pd.date_range("2026-01-05 14:30", periods=3, freq="h", tz="UTC")
    return RunRecord(kind=kind, meta={"source": "script", "strategy": strategy, "symbols": ["US.AAA"],
                                      "universe": "US.AAA", "start": "2026-01-05", "end": "2026-03-31",
                                      "git_commit": "abc123", "params": {"rsi_period": 14}},
                     metrics={"return_pct": 1.5, "sharpe_ratio": 0.8, **metrics},
                     tables={"equity": pd.DataFrame({"t": t, "equity": [100.0, np.nan, 101.5]}),
                             "trades": pd.DataFrame({"symbol": ["US.AAA"], "qty": [10.0]})})


# ── the routes ──────────────────────────────────────────────────────────────

def test_no_runs_yet_is_an_empty_list(client):
    assert client.get("/api/runs").json() == []


def test_runs_are_listed_newest_first_with_their_headline_numbers(client, run_store):
    first = run_store.save(_record(profit_factor=2.0))
    second = run_store.save(_record(kind="walk_forward", strategy="Cross-Sectional Reversal", avg_oos_return=0.7))
    listed = client.get("/api/runs").json()
    assert [r["run_id"] for r in listed] == [second, first]
    newest = listed[0]
    assert {k: newest[k] for k in ("kind", "source", "strategy", "universe", "n_symbols", "start", "end", "git_commit")} == {
        "kind": "walk_forward", "source": "script", "strategy": "Cross-Sectional Reversal", "universe": "US.AAA",
        "n_symbols": 1, "start": "2026-01-05", "end": "2026-03-31", "git_commit": "abc123"}
    # a metric only the other run has (NaN in this row of the list) is left out
    assert newest["metrics"] == {"return_pct": 1.5, "sharpe_ratio": 0.8, "avg_oos_return": 0.7}
    assert listed[1]["metrics"] == {"return_pct": 1.5, "sharpe_ratio": 0.8, "profit_factor": 2.0}
    assert pd.Timestamp(newest["created"]).tzinfo is not None


def test_the_list_passes_its_filters_to_the_store_and_refuses_an_unknown_kind(client, run_store):
    run_store.save(_record())
    run_store.save(_record(kind="compare"))
    assert [r["kind"] for r in client.get("/api/runs", params={"kind": "compare"}).json()] == ["compare"]
    assert client.get("/api/runs", params={"strategy": "MACD"}).json() == []
    assert len(client.get("/api/runs", params={"limit": 1}).json()) == 1
    assert run_store.calls[-3:] == [("list", "compare", None, 200), ("list", None, "MACD", 200), ("list", None, None, 1)]
    r = client.get("/api/runs", params={"kind": "optimize"})
    assert r.status_code == 422 and "grid_search" in r.json()["detail"]


def test_a_run_opens_with_its_summary_how_it_was_run_and_its_tables(client, run_store):
    run_id = run_store.save(_record(profit_factor=None))
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["summary"]["run_id"] == run_id and detail["summary"]["kind"] == "backtest"
    # as saved: a metric present and null (an infinite profit factor) stays, as null
    assert detail["summary"]["metrics"] == {"return_pct": 1.5, "sharpe_ratio": 0.8, "profit_factor": None}
    assert detail["meta"]["params"] == {"rsi_period": 14} and detail["meta"]["source"] == "script"
    assert detail["tables"] == ["equity", "trades"]


# (%2E%2E: a client normalises a bare .. away before it is sent, the server decodes this one to ..)
@pytest.mark.parametrize("bad", ["%2E%2E", ".hidden", "a.b", "a%20b", "x" * 200, "-leading"])
def test_an_id_that_cannot_be_a_run_is_404_without_asking_the_store(client, run_store, bad):
    for url in (f"/api/runs/{bad}", f"/api/runs/{bad}/tables/equity"):
        assert client.get(url).status_code == 404
    assert client.delete(f"/api/runs/{bad}").status_code == 404
    assert run_store.calls == []


def test_an_unknown_run_or_table_is_404(client, run_store):
    run_id = run_store.save(_record())
    assert client.get("/api/runs/20200101T000000_backtest_nope").status_code == 404
    r = client.get(f"/api/runs/{run_id}/tables/windows")
    assert r.status_code == 404 and "windows" in r.json()["detail"]
    assert client.get(f"/api/runs/{run_id}/tables/%2E%2E").status_code == 404
    assert client.get(f"/api/runs/{run_id}/tables/Equity.parquet").status_code == 404


def test_a_table_as_json_rows(client, run_store):
    run_id = run_store.save(_record())
    body = client.get(f"/api/runs/{run_id}/tables/equity").json()
    assert body == {"run_id": run_id, "name": "equity", "columns": ["t", "equity"], "total": 3, "rows": [
        {"t": "2026-01-05T14:30:00+00:00", "equity": 100.0},
        {"t": "2026-01-05T15:30:00+00:00", "equity": None},              # NaN
        {"t": "2026-01-05T16:30:00+00:00", "equity": 101.5}]}
    limited = client.get(f"/api/runs/{run_id}/tables/equity", params={"limit": 1}).json()
    assert (len(limited["rows"]), limited["total"]) == (1, 3)


def test_a_table_downloads_as_csv_and_as_parquet(client, run_store):
    run_id = run_store.save(_record())
    saved = run_store.table(run_id, "equity")
    csv = client.get(f"/api/runs/{run_id}/tables/equity", params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.headers["content-disposition"] == f'attachment; filename="{run_id}_equity.csv"'
    back = pd.read_csv(io.StringIO(csv.text), parse_dates=["t"])
    pd.testing.assert_frame_equal(back, saved, check_dtype=False)

    pq = client.get(f"/api/runs/{run_id}/tables/equity", params={"format": "parquet"})
    assert pq.status_code == 200 and pq.headers["content-type"] == "application/vnd.apache.parquet"
    assert pq.headers["content-disposition"] == f'attachment; filename="{run_id}_equity.parquet"'
    pd.testing.assert_frame_equal(pd.read_parquet(io.BytesIO(pq.content)), saved)


def test_a_long_table_streams_as_csv_in_chunks_with_one_header(client, run_store):
    from midfreq.api.routers import runs as runs_router
    rec = _record()
    rec.tables = {"big": pd.DataFrame({"i": range(25_000)})}
    run_id = run_store.save(rec)
    csv = client.get(f"/api/runs/{run_id}/tables/big", params={"format": "csv"}).text
    assert runs_router.CSV_ROWS < 25_000
    assert csv.splitlines() == ["i"] + [str(i) for i in range(25_000)]


def test_a_table_with_an_index_or_list_cells_is_served_whole(client, run_store):
    rec = _record()
    rec.tables = {"lists": pd.DataFrame({"symbols": [["US.AAA", "US.BBB"], []]})}
    run_id = run_store.save(rec)
    # a store that hands a table back with its index (the memory store, like Parquet, drops it on save)
    run_store.runs[run_id].tables["by_day"] = pd.DataFrame({"v": [1.0, 2.0]}, index=pd.Index(["d1", "d2"], name="day"))
    assert client.get(f"/api/runs/{run_id}/tables/by_day").json()["columns"] == ["day", "v"]
    assert [r["symbols"] for r in client.get(f"/api/runs/{run_id}/tables/lists").json()["rows"]] == [["US.AAA", "US.BBB"], []]


def test_delete_removes_a_run(client, run_store):
    run_id = run_store.save(_record())
    r = client.delete(f"/api/runs/{run_id}")
    assert r.status_code == 204 and r.content == b""
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.delete(f"/api/runs/{run_id}").status_code == 404


def test_delete_is_403_where_runs_are_not_deletable(client, app, settings, run_store):
    run_id = run_store.save(_record())
    app.dependency_overrides[get_settings] = lambda: replace(settings, runs_deletable=False)
    for target in (run_id, "20200101T000000_backtest_nope", "%2E%2E"):    # refused before anything is looked up
        r = client.delete(f"/api/runs/{target}")
        assert r.status_code == 403 and "not allowed" in r.json()["detail"]
    assert run_id in run_store.runs
    assert client.get(f"/api/runs/{run_id}").status_code == 200           # reading is still fine


def test_a_store_that_cannot_be_read_is_503_with_the_reason(client, app):
    app.dependency_overrides[get_run_store] = lambda: UnavailableRunStore("no credentials for gs://bucket")
    for url in ("/api/runs", "/api/runs/20261016T000000_backtest_1", "/api/runs/20261016T000000_backtest_1/tables/equity"):
        r = client.get(url)
        assert r.status_code == 503 and r.json()["detail"] == "no credentials for gs://bucket"

    class Broken(UnavailableRunStore):
        def list(self, kind=None, strategy=None, limit=200):
            raise ConnectionError("bucket timed out")

    app.dependency_overrides[get_run_store] = lambda: Broken("")
    r = client.get("/api/runs")
    assert r.status_code == 503 and r.json()["detail"] == "the run store cannot be read: ConnectionError: bucket timed out"


def test_the_default_store_is_made_once_and_a_failure_is_retried(monkeypatch):
    from midfreq.backtest import runstore
    made = []
    monkeypatch.setattr(deps, "_run_store", None)
    monkeypatch.setattr(runstore, "default_run_store", lambda: (_ for _ in ()).throw(ImportError("no google-cloud-storage")))
    failed = get_run_store()
    assert isinstance(failed, UnavailableRunStore) and "ImportError: no google-cloud-storage" in failed.reason
    monkeypatch.setattr(runstore, "default_run_store", lambda: made.append(object()) or made[-1])
    store = get_run_store()
    assert get_run_store() is store is made[0] and len(made) == 1


# ── jobs are saved ──────────────────────────────────────────────────────────

def _candles(seed: int) -> pd.DataFrame:
    days = pd.bdate_range(START, END)
    idx = pd.DatetimeIndex([d + pd.Timedelta(minutes=870 + 60 * h) for d in days for h in range(7)],
                           name="timestamp").tz_localize("UTC")
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
                         "close": close, "volume": rng.integers(1_000, 5_000, len(idx)).astype(float)}, index=idx)


@pytest.fixture
def jobs(app, client, settings):
    """post(kind, **body) -> the finished job, run on threads; the app's runner is `runner`."""
    pytest.importorskip("httpx")
    for i, symbol in enumerate(SYMBOLS):
        folder = settings.data_dir / symbol.replace(".", "_")
        folder.mkdir(exist_ok=True)
        _candles(seed=i + 1).to_parquet(folder / "1h.parquet")
    runner = JobRunner(workers="thread")
    app.dependency_overrides[get_job_runner] = lambda: runner

    def post(kind, wait_for=("done", "failed"), **overrides):
        payload = {"market": "US", "symbol": "US.AAA", "universe": "sp100", "timeframe": "1h", "start": START,
                   "end": END, **overrides}
        r = client.post(f"/api/jobs/{kind}", json=payload)
        assert r.status_code == 202, r.text
        return wait(client, r.json()["id"], wait_for)

    post.runner = runner
    yield post
    runner.shutdown()


def wait(client, job_id, until=("done", "failed"), timeout=60.0, check=None):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until and (check is None or check(job)):
            return job
        assert time.monotonic() < deadline, f"job still {job['status']}: {job['message']}"
        time.sleep(0.02)


def _json(value):
    return json.loads(json.dumps(value))


def test_a_backtest_job_is_saved_with_its_settings_metrics_equity_and_trades(jobs, client, run_store):
    job = jobs("backtest", strategy="RSI", params={"rsi_period": 10}, slippage_bps=2.0)
    assert job["status"] == "done", job["error"]
    run_id = job["run_id"]
    assert run_id and run_id.split("_")[1] == "backtest"
    bt = job["backtest"]
    saved = run_store.runs[run_id]
    meta = saved.meta
    assert {k: meta[k] for k in ("source", "job_id", "job_kind", "strategy", "params", "symbols", "n_symbols",
                                 "universe", "market", "currency", "timeframe", "start", "end", "initial_capital",
                                 "commission_rate", "slippage_bps")} == {
        "source": "api", "job_id": job["id"], "job_kind": "backtest", "strategy": "RSI",
        "params": {"rsi_period": 10, "oversold": 30, "overbought": 70}, "symbols": ["US.AAA"], "n_symbols": 1,
        "universe": "US.AAA", "market": "US", "currency": "USD", "timeframe": "1h", "start": START, "end": END,
        "initial_capital": 100_000.0, "commission_rate": 0.00005, "slippage_bps": 2.0}
    assert meta["risk"]["sizing"] == "fixed_quantity" and meta["request"] == job["request"]
    assert meta["data_cutoff"] == pd.Timestamp(bt["equity"][-1]["t"]).isoformat()
    scalars = {k: v for k, v in bt["metrics"].items() if not isinstance(v, (dict, list))}
    positions = bt["metrics"]["open_positions"]
    assert _json(saved.metrics) == {**scalars, "n_open_positions": len(positions)}
    assert "open_positions" not in saved.metrics
    assert ("positions" in saved.tables) == bool(positions)

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["tables"][:2] == ["equity", "trades"]
    assert detail["summary"]["metrics"]["return_pct"] == bt["metrics"]["return_pct"]
    equity = client.get(f"/api/runs/{run_id}/tables/equity").json()["rows"]
    assert [(pd.Timestamp(r["t"]), r["equity"]) for r in equity] == [(pd.Timestamp(p["t"]), p["v"]) for p in bt["equity"]]
    trades = client.get(f"/api/runs/{run_id}/tables/trades").json()
    assert trades["columns"] == ["timestamp", "symbol", "action", "qty", "price", "commission", "pnl"]
    assert [{**r, "timestamp": pd.Timestamp(r["timestamp"])} for r in trades["rows"]] == [
        {**t, "timestamp": pd.Timestamp(t["timestamp"])} for t in bt["trades"]]

    listed = client.get("/api/runs").json()[0]
    assert (listed["run_id"], listed["kind"], listed["source"], listed["strategy"]) == (run_id, "backtest", "api", "RSI")


def test_a_compare_job_is_saved_with_each_strategy_and_its_equity(jobs, run_store):
    job = jobs("compare", strategies=[{"strategy": "RSI"}, {"strategy": "RSI", "params": {"rsi_period": 7}},
                                      {"strategy": "Cross-Sectional Reversal"}])
    assert job["status"] == "done", job["error"]
    saved = run_store.runs[job["run_id"]]
    rows = job["compare"]["rows"]
    assert saved.kind == "compare"
    assert saved.meta["strategy"] == "RSI, Cross-Sectional Reversal"
    assert saved.meta["universe"] == "US.AAA, sp100" and saved.meta["n_symbols"] == 3
    assert saved.meta["strategies"] == [r["strategy"] for r in rows]                       # the labels, best first
    assert [r["label"] for r in saved.meta["runs"]] == ["RSI #1", "RSI #2", "Cross-Sectional Reversal"]
    assert saved.meta["params"] == {r["strategy"]: r["params"] for r in rows}
    table = saved.tables["strategies"]
    assert list(table["label"]) == [r["strategy"] for r in rows]
    assert sorted(set(table["strategy"])) == ["Cross-Sectional Reversal", "RSI"]
    assert [json.loads(p) for p in table["params"]] == [r["params"] for r in rows]
    assert list(table["return_pct"]) == [r["metrics"]["return_pct"] for r in rows]
    assert dict(zip(table["label"], table["n_symbols"]))["Cross-Sectional Reversal"] == 3
    assert saved.metrics["best_strategy"] == rows[0]["strategy"] and saved.metrics["n_strategies"] == 3
    assert saved.metrics["return_pct"] == rows[0]["metrics"]["return_pct"]
    equity = saved.tables["equity"]
    for label, curve in job["compare"]["equity"].items():
        assert list(equity.loc[equity["label"] == label, "equity"]) == [p["v"] for p in curve]


def test_a_grid_search_is_saved_with_every_combination(jobs, run_store):
    job = jobs("optimize", strategy="RSI", method="grid", objective="return_pct", max_combinations=5)
    assert job["status"] == "done", job["error"]
    saved = run_store.runs[job["run_id"]]
    opt = job["optimize"]
    assert saved.kind == "grid_search" and job["run_id"].split("_", 1)[1].startswith("grid_search")
    assert (saved.meta["method"], saved.meta["objective"], saved.meta["max_combinations"]) == ("grid", "return_pct", 5)
    assert saved.meta["best_params"] == opt["rows"][0]["params"]
    grid = saved.tables["grid"]
    assert len(grid) == len(opt["rows"])                                               # best first
    for col in ("rsi_period", "oversold", "overbought"):
        assert list(grid[col]) == [r["params"][col] for r in opt["rows"]]
    assert list(grid["_objective"]) == [r["metrics"]["_objective"] for r in opt["rows"]]
    assert saved.metrics["objective_value"] == opt["rows"][0]["metrics"]["_objective"]
    assert saved.metrics["combinations"] == len(opt["rows"]) and "_objective" not in saved.metrics


def test_a_walk_forward_is_saved_with_in_sample_and_out_of_sample_figures_per_window(jobs, run_store):
    job = jobs("optimize", strategy="Ensemble Voting", method="walk_forward", n_splits=2, train_pct=0.6)
    assert job["status"] == "done", job["error"]
    saved = run_store.runs[job["run_id"]]
    opt = job["optimize"]
    assert saved.kind == "walk_forward"
    assert (saved.meta["n_splits"], saved.meta["train_pct"], saved.meta["objective"]) == (2, 0.6, "sharpe_ratio")
    windows = saved.tables["windows"]
    assert list(windows["window"]) == [w["index"] for w in opt["windows"]]
    assert [t.isoformat() for t in windows["train_start"]] == [pd.Timestamp(w["train_start"]).isoformat() for w in opt["windows"]]
    assert list(windows["consensus_threshold"]) == [w["params"]["consensus_threshold"] for w in opt["windows"]]
    assert list(windows["is_sharpe_ratio"]) == [w["train_objective"] for w in opt["windows"]]
    assert list(windows["is_return_pct"]) == opt["summary"]["train_returns"]
    assert list(windows["oos_return_pct"]) == [w["test_metrics"]["return_pct"] for w in opt["windows"]]
    assert list(windows["oos_sharpe_ratio"]) == [w["test_metrics"]["sharpe_ratio"] for w in opt["windows"]]
    assert not [c for c in windows.columns if c.startswith("oos_open_positions")]
    assert saved.metrics["avg_oos_return"] == opt["summary"]["avg_oos_return"]
    assert saved.metrics["avg_train_return"] == opt["summary"]["avg_train_return"]
    assert saved.metrics["n_skipped"] == 0
    assert "oos_returns" not in saved.metrics          # lists are in the windows table


def test_a_run_that_cannot_be_saved_still_finishes(jobs, app, caplog):
    class Failing(UnavailableRunStore):
        def save(self, record):
            raise PermissionError("403 storage.objects.create denied")

    app.dependency_overrides[get_run_store] = lambda: Failing("")
    with caplog.at_level(logging.ERROR, logger="midfreq.api.runner"):
        job = jobs("backtest", strategy="RSI")
    assert (job["status"], job["run_id"], job["error"]) == ("done", None, None)
    assert job["backtest"]["metrics"]["total_trades"] >= 0
    assert "could not be saved" in caplog.text and "storage.objects.create denied" in caplog.text

    app.dependency_overrides[get_run_store] = lambda: UnavailableRunStore("no bucket")
    job = jobs("backtest", strategy="RSI")
    assert (job["status"], job["run_id"]) == ("done", None)


def test_a_failed_job_is_not_saved(jobs, run_store):
    job = jobs("backtest", strategy="RSI", start="2025-01-01", end="2025-02-01")
    assert (job["status"], job["run_id"]) == ("failed", None)
    assert run_store.runs == {} and run_store.calls == []


class SlowStore:
    """Wraps a store: save waits until `go` is set."""

    def __init__(self, inner):
        self.inner, self.go = inner, threading.Event()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def save(self, record):
        assert self.go.wait(10)
        return self.inner.save(record)


def test_the_job_says_it_is_saving_and_is_done_once_saved(jobs, app, client, run_store):
    slow = SlowStore(run_store)
    app.dependency_overrides[get_run_store] = lambda: slow
    job = jobs("backtest", wait_for=("running",), strategy="RSI")
    saving = wait(client, job["id"], until=("running",), check=lambda j: j["message"] == "Saving the run")
    assert (saving["progress"], saving["run_id"], saving["finished"]) == (1.0, None, None)
    assert jobs.runner.active() == 1                                   # it still holds its place
    slow.go.set()
    done = wait(client, job["id"])
    assert done["status"] == "done" and done["run_id"] in run_store.runs
    assert done["message"].startswith("RSI: ")                         # the run's own last message


def test_a_save_that_takes_too_long_leaves_the_job_done_and_reports_the_run_when_it_lands(jobs, app, client, run_store):
    slow = SlowStore(run_store)
    app.dependency_overrides[get_run_store] = lambda: slow
    jobs.runner.save_timeout = 0.2
    job = jobs("backtest", strategy="RSI")
    assert (job["status"], job["run_id"]) == ("done", None)
    slow.go.set()
    late = wait(client, job["id"], check=lambda j: j["run_id"] is not None, timeout=10)
    assert late["run_id"] in run_store.runs


def test_every_kind_of_job_saves_into_the_real_local_store_and_reads_back(jobs, app, client, tmp_path):
    """The records go through midfreq.backtest.runstore's own checks (reserved keys, table names,
    single-value metrics, Parquet) and its listing, then out of the routes."""
    store = LocalRunStore(tmp_path / "runs")
    app.dependency_overrides[get_run_store] = lambda: store
    done = [jobs("backtest", strategy="RSI"),
            jobs("compare", strategies=[{"strategy": "RSI"}, {"strategy": "MACD"}]),
            jobs("optimize", strategy="RSI", method="grid", max_combinations=5),
            jobs("optimize", strategy="Ensemble Voting", method="walk_forward", n_splits=2)]
    assert all(j["status"] == "done" and j["run_id"] for j in done), [j["error"] for j in done]
    listed = {r["run_id"]: r for r in client.get("/api/runs").json()}
    assert [listed[j["run_id"]]["kind"] for j in done] == ["backtest", "compare", "grid_search", "walk_forward"]
    for j in done:
        row = listed[j["run_id"]]
        assert (row["source"], row["start"], row["end"], row["universe"]) == ("api", START, END, row["universe"])
        assert row["git_commit"] == store.load(j["run_id"]).meta["git_commit"]
        detail = client.get(f"/api/runs/{j['run_id']}").json()
        assert detail["meta"]["job_id"] == j["id"]
        for name in detail["tables"]:
            assert client.get(f"/api/runs/{j['run_id']}/tables/{name}").status_code == 200
    assert listed[done[0]["run_id"]]["metrics"]["return_pct"] == done[0]["backtest"]["metrics"]["return_pct"]
    assert listed[done[3]["run_id"]]["metrics"]["avg_oos_return"] == done[3]["optimize"]["summary"]["avg_oos_return"]
    windows = client.get(f"/api/runs/{done[3]['run_id']}/tables/windows").json()
    assert {"is_sharpe_ratio", "is_return_pct", "oos_return_pct", "oos_sharpe_ratio"} <= set(windows["columns"])

    r = client.delete(f"/api/runs/{done[0]['run_id']}")
    assert r.status_code == 204 and done[0]["run_id"] not in {x["run_id"] for x in client.get("/api/runs").json()}
