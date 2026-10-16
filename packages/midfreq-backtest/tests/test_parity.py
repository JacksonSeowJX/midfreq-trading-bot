"""Stale C++ ports: a strategy whose Python changed since its port was verified runs on Python.

Fingerprints ignore docstrings, comments and formatting but not code; a stale strategy (or stale
shared code) falls back under engine="auto" with the reason, raises under "native", and gives
Python's results; `status` reports it, and `verify` records only after the tests pass."""
import contextlib
import importlib.util
import io
import json
import logging
import subprocess
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("midfreq.engine._core")

from midfreq.backtest import native, parity  # noqa: E402
from midfreq.backtest.backtester import PythonBacktester  # noqa: E402
from midfreq.backtest.native import BatchRunner, NativeBacktester, NativeUnavailable  # noqa: E402
from midfreq.backtest.optimizer import grid_search  # noqa: E402
from midfreq.core.candle import Timeframe  # noqa: E402
from midfreq.core.portfolio import Portfolio  # noqa: E402
from midfreq.data.storage import DataStorage  # noqa: E402
from midfreq.strategies import candle_strategies as cs  # noqa: E402

H1 = Timeframe.HOUR_1
SYMBOLS = ["US.A", "US.B", "US.C", "US.D"]
REVERSAL = {"lookback": 5, "top_n": 2, "rebalance_every": 3}


@pytest.fixture(autouse=True)
def not_verifying(monkeypatch):
    monkeypatch.delenv(parity.VERIFYING, raising=False)
    native._warned.clear()


@pytest.fixture(scope="module")
def storage(tmp_path_factory):
    st = DataStorage(base_path=str(tmp_path_factory.mktemp("parity")))
    rng = np.random.default_rng(3)
    idx = pd.date_range("2025-03-03 14:30", periods=300, freq="h", tz="UTC")
    for s in SYMBOLS:
        c = np.round(40 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            st.save_data(pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx),
                         s.replace(".", "_"), "1h")
    return st


def stale_record(name="CrossSectionalReversal", shared=False):
    record = parity.current_record()
    if shared:
        record["shared"] = "0" * 64
    else:
        record["strategies"][name] = "0" * 64
    return record


def load_class(tmp_path, monkeypatch, name, source):
    path = tmp_path / f"{name}.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)  # inspect finds a class's source through it
    spec.loader.exec_module(module)
    return module.Thing


def test_documentation_and_formatting_do_not_change_a_fingerprint_but_code_does(tmp_path, monkeypatch):
    base = load_class(tmp_path, monkeypatch, "parity_v1", "class Thing:\n    def f(self, x):\n        return x + 1\n")
    documented = load_class(tmp_path, monkeypatch, "parity_v2", (
        'class Thing:\n    """A thing."""\n\n    def f(self, x):  # add one\n'
        '        """Adds one."""\n        return (x\n                + 1)\n'))
    changed = load_class(tmp_path, monkeypatch, "parity_v3", "class Thing:\n    def f(self, x):\n        return x + 2\n")
    assert parity.normalized_source(base) == parity.normalized_source(documented)
    assert parity.normalized_source(base) != parity.normalized_source(changed)


def test_a_fingerprint_covers_the_bases_and_the_classes_a_strategy_calls():
    assert parity.sources(cs.CrossSectionalMomentum) == [cs.CrossSectionalMomentum, cs.CrossSectionalReversal,
                                                         cs.BaseStrategy]
    assert {cs.RSIStrategy, cs.MACDStrategy, cs.BollingerBandsStrategy, cs.BaseStrategy} <= set(
        parity.sources(cs.EnsembleStrategy))
    assert {PythonBacktester, Portfolio} <= set(parity.SHARED)
    assert parity.fingerprint(cs.CrossSectionalMomentum) != parity.fingerprint(cs.CrossSectionalReversal)


def test_the_record_names_every_port():
    record = parity.verified()
    assert record is not None, "packages/midfreq-engine/src/midfreq/engine/verified.json is missing"
    assert set(record["strategies"]) == {c.__name__ for c in native.native_strategy_classes()}
    assert len(record["shared"]) == 64


def run_both(storage, engine_cls, **kw):
    pf = Portfolio()
    with contextlib.redirect_stdout(io.StringIO()):
        bt = engine_cls(storage, pf, **kw)
        m = bt.run(cs.CrossSectionalReversal, SYMBOLS, H1, **REVERSAL)
    return bt, m, pf


@pytest.mark.parametrize("shared", [False, True], ids=["strategy changed", "shared code changed"])
def test_a_stale_port_runs_on_python_with_the_reason(storage, monkeypatch, caplog, shared):
    monkeypatch.setattr(parity, "verified", lambda: stale_record(shared=shared))
    _, expected, pf_py = run_both(storage, PythonBacktester)
    with caplog.at_level(logging.WARNING, logger="midfreq.backtest.native"):
        bt, got, pf = run_both(storage, NativeBacktester)
    assert bt.engine_used == "python"
    assert ("changed since" in bt.fallback_reason) and ("verify" in bt.fallback_reason)
    assert got == expected and pf.trade_history == pf_py.trade_history
    assert "runs on the Python backtester" in caplog.text
    with pytest.raises(NativeUnavailable):
        run_both(storage, NativeBacktester, fallback=False)


def test_only_the_changed_strategy_falls_back(storage, monkeypatch):
    monkeypatch.setattr(parity, "verified", lambda: stale_record("RSIStrategy"))
    bt, _, _ = run_both(storage, NativeBacktester)
    assert bt.engine_used == "native"
    rsi = BatchRunner("auto", cs.RSIStrategy, SYMBOLS, H1, storage, 1e5, 0.0)
    assert rsi.engine == "python" and rsi.fallback_reason.startswith("RSIStrategy (or a class it builds on) changed")


def test_verifying_uses_every_port(storage, monkeypatch):
    monkeypatch.setattr(parity, "verified", lambda: stale_record())
    monkeypatch.setenv(parity.VERIFYING, "1")
    bt, _, _ = run_both(storage, NativeBacktester, fallback=False)
    assert bt.engine_used == "native"


def test_the_optimiser_gives_pythons_table_for_a_stale_port(storage, monkeypatch):
    monkeypatch.setattr(parity, "verified", lambda: stale_record())
    kw = dict(symbols=SYMBOLS, timeframe=H1, start_date=datetime(2025, 3, 3), end_date=datetime(2025, 3, 14),
              storage=storage, max_combinations=12)
    auto = grid_search("Cross-Sectional Reversal", engine="auto", **kw)
    py = grid_search("Cross-Sectional Reversal", engine="python", **kw)
    pd.testing.assert_frame_equal(auto, py, check_exact=True)


def test_status_reports_stale_ports_and_warns_on_github(monkeypatch, capsys):
    monkeypatch.setattr(parity, "verified", lambda: stale_record())
    assert parity.main(["status", "--github"]) == 0
    out = capsys.readouterr().out
    assert any(line.startswith("CrossSectionalReversal") and "stale" in line for line in out.splitlines())
    assert "::warning title=C++ port stale::CrossSectionalReversal" in out
    assert "10 of 11 C++ ports current" in out


def test_verify_records_only_when_the_tests_pass(tmp_path, monkeypatch):
    out = tmp_path / "verified.json"
    calls = []

    def fake_run(cmd, cwd, env):
        calls.append((cmd, env.get(parity.VERIFYING)))
        return subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(parity.subprocess, "run", fake_run)
    returncode = 1
    assert parity.main(["verify", "--record-to", str(out)]) == 1
    assert not out.exists()
    returncode = 0
    assert parity.main(["verify", "--record-to", str(out)]) == 0
    assert json.loads(out.read_text())["strategies"] == parity.current_record()["strategies"]
    assert all(flag == "1" for _, flag in calls)
    assert any("test_native.py" in " ".join(cmd) for cmd, _ in calls)
