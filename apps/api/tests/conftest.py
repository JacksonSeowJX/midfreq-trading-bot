"""
Fixtures for the API tests: a repository in miniature (results/, live_sessions/, config/, data/)
built in a temporary folder, a run store in memory, and a client for an app pointed at both.
Nothing here reads or writes the repository's own folders (or its runs/).

The client is FastAPI's TestClient when httpx is installed. httpx is not a dependency of the
workspace yet, so without it the same calls go through `_AsgiClient`, which drives the app
in-process the way TestClient does.
"""
import asyncio
import io
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode

import pandas as pd
import pytest

from midfreq.api.deps import get_run_store
from midfreq.api.main import create_app
from midfreq.api.settings import Settings
from midfreq.backtest.runstore import RunRecord, RunStore

try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):          # starlette raises RuntimeError without httpx
    TestClient = None


class _Response:
    def __init__(self, status_code, headers, body):
        self.status_code, self.headers, self.content = status_code, headers, body

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)


class _AsgiClient:
    """The part of TestClient these tests use (GET, DELETE), without httpx."""

    def __init__(self, app):
        self.app = app

    def get(self, url, params=None, headers=None):
        return self.request("GET", url, params=params, headers=headers)

    def delete(self, url, params=None, headers=None):
        return self.request("DELETE", url, params=params, headers=headers)

    def request(self, method, url, params=None, headers=None):
        path, _, query = url.partition("?")
        if params:
            extra = urlencode(params, doseq=True)
            query = f"{query}&{extra}" if query else extra
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
            "scheme": "http", "path": unquote(path), "raw_path": path.encode(), "root_path": "",
            "query_string": query.encode(), "client": ("testclient", 50000), "server": ("testserver", 80),
            "headers": [(b"host", b"testserver")]
                       + [(k.lower().encode(), str(v).encode()) for k, v in (headers or {}).items()],
        }
        sent = []

        async def call():
            done, requested = asyncio.Event(), []

            async def receive():
                # the request once, then (like TestClient) a disconnect when the response is over
                if not requested:
                    requested.append(True)
                    return {"type": "http.request", "body": b"", "more_body": False}
                await done.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    done.set()

            await self.app(scope, receive, send)

        asyncio.run(call())
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return _Response(start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body)


def make_client(app):
    return TestClient(app) if TestClient is not None else _AsgiClient(app)


# ── the run store, in memory ────────────────────────────────────────────────

class MemoryRunStore(RunStore):
    """A RunStore in memory. As a real one does, it keeps the meta as JSON (run.json) and every
    table and the metrics as Parquet, so what cannot be saved fails here too; ids are
    <UTC yyyymmddThhmmss>_<kind>_<counter>, newest listed first. `calls` records what was asked."""

    def __init__(self):
        self.runs = {}
        self.calls = []
        self._lock = threading.Lock()

    @staticmethod
    def _parquet(df: pd.DataFrame) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(df.to_parquet(index=False)))

    def save(self, record):
        self.calls.append(("save", record.kind))
        with self._lock:
            created = datetime.now(timezone.utc)
            run_id = f"{created:%Y%m%dT%H%M%S}_{record.kind}_{len(self.runs) + 1:06d}"
            metrics = self._parquet(pd.DataFrame([record.metrics])).iloc[0].to_dict() if record.metrics else {}
            self.runs[run_id] = RunRecord(kind=record.kind, meta=json.loads(json.dumps(record.meta)), metrics=metrics,
                                          tables={k: self._parquet(df) for k, df in record.tables.items()},
                                          run_id=run_id, created=created)
        return run_id

    def list(self, kind=None, strategy=None, limit=200):
        self.calls.append(("list", kind, strategy, limit))
        rows = []
        for rec in sorted(self.runs.values(), key=lambda r: r.run_id, reverse=True):
            meta = rec.meta
            if (kind and rec.kind != kind) or (strategy and meta.get("strategy") != strategy):
                continue
            rows.append({"run_id": rec.run_id, "created": rec.created, "kind": rec.kind, "source": meta.get("source"),
                         "strategy": meta.get("strategy"), "universe": meta.get("universe"),
                         "n_symbols": len(meta.get("symbols") or []), "timeframe": meta.get("timeframe"),
                         "start": meta.get("start"), "end": meta.get("end"), "objective": meta.get("objective"),
                         "method": meta.get("method"), "git_commit": meta.get("git_commit"), **rec.metrics})
        return pd.DataFrame(rows[:limit])

    def load(self, run_id):
        self.calls.append(("load", run_id))
        rec = self.runs[run_id]
        return RunRecord(kind=rec.kind, meta=json.loads(json.dumps(rec.meta)), metrics=dict(rec.metrics),
                         tables={k: df.copy() for k, df in rec.tables.items()}, run_id=rec.run_id, created=rec.created)

    def table(self, run_id, name):
        self.calls.append(("table", run_id, name))
        return self.runs[run_id].tables[name].copy()

    def delete(self, run_id):
        self.calls.append(("delete", run_id))
        del self.runs[run_id]


# ── the miniature repository ────────────────────────────────────────────────

US = ["US.AAA", "US.BBB", "US.CCC"]


def _acct(total, cash=900_000.0):
    return {"cash": cash, "total_assets": total, "market_value": total - cash}


def write_session(folder: Path, name: str, events):
    (folder / f"{name}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _record(folder: Path):
    """Three sessions shaped like the real logs: two S&P 100-style forward-test sessions (the
    second crashed: no session_end, a line cut short, a Traceback in its console log) and one
    early HK session whose trades were counted at session_end but not logged one by one."""
    folder.mkdir()
    write_session(folder, "session_20261005_213502_crosssectionalreversal_1001_1", [
        {"type": "session_start", "timestamp": "2026-10-05 21:35:02.481894", "strategy": "CrossSectionalReversal",
         "symbols": US, "timeframe": "1h", "account": _acct(1_000_000.0)},
        {"type": "candle_close", "symbol": "US.AAA", "timestamp": "2026-10-05 10:30:00+00:00", "close": 50.0,
         "equity": 100_000.0},
        {"type": "trade", "timestamp": "2026-10-05 10:30:00+00:00", "symbol": "US.AAA", "side": "BUY",
         "qty": 100.0, "price": 50.0, "order_id": "1", "exit_reason": None},
        {"type": "candle_close", "symbol": "US.AAA", "timestamp": "2026-10-05 16:00:00+00:00", "close": 55.0,
         "equity": 100_500.0},
        {"type": "session_end", "timestamp": "2026-10-06 04:01:05.530682", "candles_processed": 2, "trades": 1,
         "account": _acct(1_000_499.0), "positions": {"US.AAA": {"qty": 100.0, "entry_price": 50.0}}},
    ])
    crashed = folder / "session_20261006_213502_crosssectionalreversal_1002_1.jsonl"
    write_session(folder, crashed.stem, [
        {"type": "session_start", "timestamp": "2026-10-06 21:35:02.100000", "strategy": "CrossSectionalReversal",
         "symbols": US, "timeframe": "1h", "account": _acct(1_000_499.0)},
        {"type": "trade", "timestamp": "2026-10-06 10:30:00+00:00", "symbol": "US.AAA", "side": "SELL",
         "qty": 100.0, "price": 53.0, "order_id": "2", "exit_reason": "rebalance"},
        {"type": "trade", "timestamp": "2026-10-06 10:30:00+00:00", "symbol": "US.BBB", "side": "BUY",
         "qty": 10.0, "price": 100.0, "order_id": "3", "exit_reason": None},
        {"type": "candle_close", "symbol": "US.BBB", "timestamp": "2026-10-06 11:30:00+00:00", "close": 101.0,
         "equity": 100_510.0},
    ])
    with crashed.open("a") as f:
        f.write('{"type": "candle_close", "symbol": "US.B')          # cut short by the crash
    write_session(folder, "session_20260713_092423_rsistrategy_81890_1", [
        {"type": "session_start", "timestamp": "2026-07-13 09:24:23.100000", "strategy": "RSIStrategy",
         "symbols": ["HK.09888"], "timeframe": "1h", "account": _acct(999_927.296, cash=999_927.296)},
        {"type": "candle_close", "symbol": "HK.09888", "timestamp": "2026-07-13 10:30:00+00:00", "close": 80.0,
         "equity": 999_927.296},
        {"type": "session_end", "timestamp": "2026-07-13 16:01:51.587595", "candles_processed": 1, "trades": 1,
         "account": _acct(1_000_100.0, cash=1_000_100.0), "positions": {}},
    ])

    def console(name, session, crash=False):
        lines = ["Strategy params: {'lookback': 30}", "Connected to Moomoo trade context (PAPER) on 127.0.0.1:11111"]
        if session:
            lines.append(f"Session log: live_sessions/{session}.jsonl")
        if crash:
            lines += ["Traceback (most recent call last):", '  File "run_live.py", line 1', "RuntimeError: boom"]
        (folder / name).write_text("\n".join(lines) + "\n")

    console("console_sp100_reversal_20261005_213501.log", "session_20261005_213502_crosssectionalreversal_1001_1")
    console("console_sp100_reversal_20261006_213501.log", crashed.stem, crash=True)
    console("console_rsi_09888_20260713_092421.log", "session_20260713_092423_rsistrategy_81890_1")
    console("console_bollinger_09888_20260728_092501.log", None, crash=True)   # died before its session began
    (folder / "state_crosssectionalreversal.json").write_text("{}")
    (folder / "trade_blotter.csv").write_text("a,b\n1,2\n")


def _results(folder: Path):
    folder.mkdir()
    (folder / "charts").mkdir()
    cols = "strategy,market,config_a_oos,config_a_consistency,config_b_oos,config_b_consistency,robust\n"
    old = folder / "cross_sectional_corrected_20260901_000000.csv"
    old.write_text(cols + "CS-Reversal,sp100,9.0,90.0,9.0,90.0,True\n")
    new = folder / "cross_sectional_corrected_20260909_050515.csv"
    new.write_text(cols + "CS-Reversal,sp100,5.7,66.7,0.6,53.3,True\n"
                          "CS-Momentum,sp100,-1.2,33.3,-0.4,46.7,False\n")
    (folder / "cross_sectional_sp100_20260824_142925.csv").write_text(
        cols + "CS-Reversal,sp100,1.3,55.6,0.2,53.3,True\n")
    (folder / "spread_hk_20260923_115005.csv").write_text("symbol,spread_bps,note\nHK.00700,4.5,\nHK.00005,,thin\n")
    (folder / "reality_check_20260929_050842.json").write_text(json.dumps({
        "common_end_date": "2026-08-28 16:00:00", "n_boot": 2000,
        "by_config": {"A (9 windows)": {
            "p_value": 0.12, "naive_p_value": 0.01, "best_candidate": "CS-Reversal x sp100",
            "observed_max_mean": 5.7, "n_windows": 9, "n_candidates": 8,
            "bootstrap_max_quantiles": {"p95": 6.1},
            "candidate_means": {"CS-Reversal x sp100": 5.7, "CS-Momentum x hsi": -0.3}}}}))
    (folder / "pbo_sp100_reversal_full96.json").write_text(json.dumps({"pbo": 0.31, "n_configs": 96}))
    (folder / "alpha_vs_market_20260821_20261015_185634.json").write_text('{"alpha": NaN, "beta": 1.1}')
    (folder / "notes.txt").write_text("not a result")
    # mtimes: oldest to newest, so "newest first" is well defined
    for i, name in enumerate(["cross_sectional_sp100_20260824_142925.csv", old.name, new.name,
                              "spread_hk_20260923_115005.csv", "pbo_sp100_reversal_full96.json",
                              "reality_check_20260929_050842.json",
                              "alpha_vs_market_20260821_20261015_185634.json"]):
        os.utime(folder / name, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))


def _config(folder: Path):
    folder.mkdir()
    (folder / "connections.yaml").write_text(
        "moomoo:\n  host: 127.0.0.1\n  port: 11111\n"
        "accounts:\n"
        "  moomoo-us:\n    label: Moomoo paper (US)\n    connector: moomoo\n    options: {market: US}\n"
        "  okx:\n    connector: ccxt\n    options: {exchange: okx}\n")
    (folder / "sp100_forward_test.json").write_text(json.dumps({"capital": 50_000.0}))
    (folder / "symbols.json").write_text(json.dumps({
        "markets": {"HK": {"status": "live", "symbols": ["HK.00700", "HK.09888"]}},
        "universes": {
            "hk_live": {"market": "HK", "description": "HK live roster", "source": "markets.HK.symbols"},
            "sp100": {"market": "US", "description": "S&P 100", "symbols": US},
            "tiny": {"symbols": ["US.AAA"]},
        }}))


def _data(folder: Path):
    folder.mkdir()
    idx = pd.date_range("2026-10-01 10:30", periods=10, freq="h", tz="UTC", name="timestamp")
    df = pd.DataFrame({"open": range(10), "high": range(1, 11), "low": range(10), "close": range(10),
                       "volume": [100.0] * 10}, index=idx).astype(float)
    (folder / "US_AAA").mkdir()
    df.to_parquet(folder / "US_AAA" / "1h.parquet")
    daily = pd.DataFrame({"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0],
                          "volume": [5.0, None]},
                         index=pd.DatetimeIndex(["2026-10-01", "2026-10-02"], name="timestamp"))
    (folder / "HK_00700").mkdir()
    daily.to_parquet(folder / "HK_00700" / "1d.parquet")


@pytest.fixture
def repo(tmp_path):
    """The miniature repository's folders."""
    _record(tmp_path / "live_sessions")
    _results(tmp_path / "results")
    _config(tmp_path / "config")
    _data(tmp_path / "data")
    return tmp_path


@pytest.fixture
def settings(repo):
    return Settings(results_dir=repo / "results", live_sessions=str(repo / "live_sessions"),
                    data_dir=repo / "data", config_dir=repo / "config", web_dist=None,
                    probe_timeout=0.5, account_timeout=0.5)


@pytest.fixture
def run_store():
    return MemoryRunStore()


@pytest.fixture
def app(settings, run_store):
    app = create_app(settings)
    app.dependency_overrides[get_run_store] = lambda: run_store
    return app


@pytest.fixture
def client(app):
    return make_client(app)


@pytest.fixture
def client_for():
    """A client for an app the test builds itself."""
    return make_client
