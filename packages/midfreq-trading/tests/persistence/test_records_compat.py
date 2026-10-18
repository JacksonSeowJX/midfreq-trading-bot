"""The node's records are the original engine's: state_<strategy>.json byte for byte, and
session_*.jsonl line for line, checked against files the original engine itself writes here (and
against the recorded state file), and read back by midfreq.research.forward_test."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from _node_testkit import ScriptedVenue, FakeDataClient, standin_components, tiny_config_dict
from midfreq.core.common.clock import TestClock
from midfreq.core.paths import repo_root
from midfreq.core.portfolio import Portfolio
from midfreq.trading.persistence import state as state_mod
from midfreq.trading.persistence.session_log import SessionLog, read_events, session_file_name

REPO = repo_root()
FIXED = datetime(2026, 10, 15, 4, 1, 4, 743088)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED if tz is None else FIXED.replace(tzinfo=tz)


class FakeGateway:
    def __init__(self, cash=1_000_000.0, positions=None):
        self.cash = cash
        self._positions = positions or {}
        self.orders = []

    def get_account_info(self):
        return {"cash": self.cash, "total_assets": self.cash, "market_value": 0.0}

    def get_positions(self):
        return dict(self._positions)

    def place_order(self, symbol, is_buy, qty, price):
        self.orders.append((symbol, is_buy, qty, price))
        return {"ok": True, "order_id": f"30000{len(self.orders)}", "message": "submitted"}


class FakeProvider:
    def get_lot_sizes(self, symbols):
        return {}

    def get_historical_data(self, *a, **k):
        return pd.DataFrame()

    def start_live_streaming_multi(self, *a, **k):
        pass


# ── state_<strategy>.json ──────────────────────────────────────────────────────────────────────

def _original_portfolio(tmp_path, monkeypatch, cap=100_000.0, positions=None):
    import midfreq.live.live_engine as live_mod
    from midfreq.live.live_engine import LivePortfolio
    monkeypatch.setattr(live_mod, "datetime", FixedDateTime)
    (tmp_path / "orig").mkdir(exist_ok=True)
    lp = LivePortfolio(FakeGateway(positions=positions), commission_rate=0.00005,
                       state_file=tmp_path / "orig" / "state_crosssectionalreversal.json", capital_cap=cap)
    return lp


@pytest.mark.parametrize("cap", [100_000.0, None])
def test_state_file_bytes_are_the_original_engines(tmp_path, monkeypatch, cap):
    lp = _original_portfolio(tmp_path, monkeypatch, cap)
    lp.positions = {"US.INTC": {"qty": 302.0, "entry_price": 109.585}, "US.CAT": {"qty": 43.0, "entry_price": 774.24},
                    "US.AMD": {"qty": 57.0, "entry_price": 587.592}}
    lp._peak_prices = {"US.INTC": 125.22, "US.CAT": 866.62, "US.AMD": 655.6, "US.SOLD": 12.0}
    lp.cash = 4738.620650000026
    lp.cross_section_count, lp.next_rebalance_at = 70, 76
    lp.save_state()
    mine = tmp_path / "node" / "state_crosssectionalreversal.json"
    state_mod.save_state(mine, lp, 70, 76, cap, FIXED)
    assert mine.read_bytes() == (tmp_path / "orig" / "state_crosssectionalreversal.json").read_bytes()


def test_the_recorded_state_file_round_trips_byte_for_byte(tmp_path):
    src = REPO / "live_sessions" / "state_crosssectionalreversal.json"
    copy = tmp_path / src.name
    shutil.copy2(src, copy)
    text = copy.read_text()
    assert state_mod.render_state(json.loads(text)) == text


def test_the_node_resumes_the_original_engines_state(tmp_path, monkeypatch):
    lp = _original_portfolio(tmp_path, monkeypatch)
    lp.positions = {"US.INTC": {"qty": 302.0, "entry_price": 109.585}}
    lp._peak_prices = {"US.INTC": 125.22}
    lp.cash = 4738.62
    lp.cross_section_count, lp.next_rebalance_at = 70, 76
    lp.save_state()
    saved = state_mod.load_state(tmp_path / "orig" / "state_crosssectionalreversal.json")
    p = Portfolio(100_000.0, 0.00005)
    _, p.cash = state_mod.opening_cash({"cash": 900_000.0, "total_assets": 1_000_000.0}, 100_000.0)
    r = state_mod.resume(p, saved, {"cash": 900_000.0}, {"US.INTC": {"qty": 302.0, "entry_price": 109.0}}, 100_000.0)
    assert p.positions == lp.positions and p._peak_prices == {"US.INTC": 125.22} and p.cash == 4738.62
    assert (r.cross_section_count, r.next_rebalance_at) == (70, 76)


def test_the_original_engine_resumes_the_nodes_state(tmp_path, monkeypatch):
    p = Portfolio(100_000.0, 0.00005)
    p.positions = {"US.CAT": {"qty": 43.0, "entry_price": 774.24}}
    p._peak_prices = {"US.CAT": 866.62}
    p.cash = 66_000.5
    (tmp_path / "orig").mkdir()
    state_mod.save_state(tmp_path / "orig" / "state_crosssectionalreversal.json", p, 88, 95, 100_000.0, FIXED)
    lp = _original_portfolio(tmp_path, monkeypatch, positions={"US.CAT": {"qty": 43.0, "entry_price": 770.0}})
    assert lp.positions == {"US.CAT": {"qty": 43.0, "entry_price": 774.24}}
    assert lp.cash == 66_000.5 and lp.get_peak_price("US.CAT") == 866.62
    assert (lp.resumed_cross_section_count, lp.resumed_next_rebalance_at) == (88, 95)


def test_resume_rules(tmp_path):
    saved = {"positions": {"A": {"qty": 100.0, "entry_price": 50.0}, "B": {"qty": 10.0, "entry_price": 5.0}},
             "peak_prices": {"A": 60.0, "B": 6.0}, "cross_section_count": 3, "next_rebalance_at": None, "cash": None}
    # broker holds less: shrink; broker cannot be asked: trust the file
    p = Portfolio(100_000.0)
    r = state_mod.resume(p, saved, {}, {"A": {"qty": 40.0}}, None)
    assert p.positions == {"A": {"qty": 40.0, "entry_price": 50.0}} and r.shrunk == {"A": (100.0, 40.0), "B": (10.0, 0)}
    p = Portfolio(100_000.0)
    state_mod.resume(p, saved, {}, None, None)
    assert set(p.positions) == {"A", "B"}
    # capped, no saved cash: cap minus the cost of what it holds, bounded by the broker's cash
    p = Portfolio(100_000.0)
    state_mod.resume(p, saved, {"cash": 1e6}, None, 100_000.0)
    assert p.cash == 100_000.0 - 100 * 50.0 - 10 * 5.0
    assert (state_mod.opening_cash({"cash": 5.0, "total_assets": 9.0}, 100.0)) == (9.0, 5.0)
    assert state_mod.opening_cash({}, 100.0) == (100.0, 100.0)


def test_an_unreadable_state_file_stops_the_start(tmp_path):
    bad = tmp_path / "state_x.json"
    bad.write_text("{not json")
    with pytest.raises(state_mod.StateFileError, match="CRITICAL"):
        state_mod.load_state(bad)


def test_state_anomaly_is_reported(tmp_path, caplog):
    path = tmp_path / "state_x.json"
    p = Portfolio(1000.0)
    p.positions = {"A": {"qty": 1.0, "entry_price": 10.0}}
    state_mod.save_state(path, p, 0, None, None, FIXED)
    p.positions["A"]["entry_price"] = 11.0
    with caplog.at_level("ERROR", logger="midfreq.trading.state"):
        state_mod.save_state(path, p, 0, None, None, FIXED)
    assert "STATE ANOMALY" in caplog.text


# ── session_*.jsonl ────────────────────────────────────────────────────────────────────────────

def _original_session(tmp_path, monkeypatch):
    """A short session of the original engine: two candles, a trade, start and end."""
    import midfreq.data.storage as storage_mod
    import midfreq.live.live_engine as live_mod
    from midfreq.core.candle import Candle, Timeframe
    from midfreq.live.live_engine import LiveTradingEngine
    from midfreq.strategies.candle_strategies import CrossSectionalReversal

    monkeypatch.setattr(live_mod, "datetime", FixedDateTime)
    real = storage_mod.DataStorage
    monkeypatch.setattr(storage_mod, "DataStorage", lambda *a, **k: real(base_path=str(tmp_path / "data")))
    clock = SimpleNamespace(t=1_000.0)
    fake_time = SimpleNamespace(time=lambda: clock.t, sleep=lambda s: setattr(clock, "t", clock.t + s))
    monkeypatch.setattr(live_mod, "time", fake_time)
    eng = LiveTradingEngine(provider=FakeProvider(), gateway=FakeGateway(), strategy_class=CrossSectionalReversal,
                            symbols=["US.AAPL", "US.MSFT"], timeframe=Timeframe.HOUR_1,
                            session_log_dir=str(tmp_path / "orig"), commission_rate=0.00005, capital_cap=100_000.0,
                            lookback=2, top_n=1, rebalance_every=1)
    t1, t2 = pd.Timestamp("2026-10-14 10:30", tz="UTC"), pd.Timestamp("2026-10-14 11:30", tz="UTC")
    for sym, c in (("US.AAPL", 100.0), ("US.MSFT", 200.5)):
        eng._on_candle_update(sym, Candle(timestamp=t1, open=c, high=c, low=c, close=c, volume=1))
    for sym, c in (("US.AAPL", 101.0), ("US.MSFT", 199.0)):
        eng._on_candle_update(sym, Candle(timestamp=t2, open=c, high=c, low=c, close=c, volume=1))
    eng.portfolio.execute_trade("US.AAPL", True, 10, 100.0, t1)
    eng.portfolio.execute_trade("US.AAPL", False, 10, 101.0, t2, exit_reason="rebalance")
    eng.run(duration_minutes=1 / 60)
    return eng._session_file


def test_session_log_lines_are_the_original_engines(tmp_path, monkeypatch):
    orig = _original_session(tmp_path, monkeypatch)
    events = [json.loads(line) for line in orig.read_text().splitlines()]
    assert [e["type"] for e in events] == ["candle_close", "candle_close", "trade", "trade", "session_start",
                                           "candle_close", "candle_close", "session_end"]
    mine = SessionLog(tmp_path / "node", "crosssectionalreversal", now=lambda: FIXED)
    for e in events:
        t = e["type"]
        if t == "candle_close":
            mine.candle_close(e["symbol"], pd.Timestamp(e["timestamp"]), e["close"], e["equity"])
        elif t == "trade":
            mine.trade(pd.Timestamp(e["timestamp"]), e["symbol"], e["side"], e["qty"], e["price"], e["order_id"],
                       e["exit_reason"])
        elif t == "session_start":
            mine.session_start(e["strategy"], e["symbols"], e["timeframe"], e["account"])
        elif t == "session_end":
            mine.session_end(e["candles_processed"], e["trades"], e["account"], e["positions"])
    assert mine.path.read_bytes() == orig.read_bytes()
    # the same file name, but for the per-process counter
    strip = lambda name: re.sub(r"_\d+\.jsonl$", "", name)
    assert strip(mine.path.name) == strip(orig.name)
    assert session_file_name(FIXED, "x", 7, 3) == "session_20261015_040104_x_7_3.jsonl"


def test_stall_event_format(tmp_path):
    log = SessionLog(tmp_path, "x", now=lambda: FIXED)
    log.stall(75.2, False)
    assert log.path.read_text() == ('{"type": "stall", "timestamp": "2026-10-15 04:01:04.743088", '
                                    '"silent_min": 75.2, "resubscribed_before": false}\n')


def test_parallel_session_logs_never_share_a_file(tmp_path):
    a = SessionLog(tmp_path, "x", now=lambda: FIXED)
    b = SessionLog(tmp_path, "x", now=lambda: FIXED)
    assert a.path != b.path


def test_forward_test_reads_a_node_session(tmp_path):
    """A node session through a rebalance, read back by midfreq.research.forward_test.load (the
    dashboard and the API's reader)."""
    from midfreq.research import forward_test
    from midfreq.trading.config.settings import config_from_dict
    from midfreq.trading.node.kernel import TradingNode
    from midfreq.trading.modes.replay import ReplayBroker
    from midfreq.core.model.data import Bar, BarType, BarSpecification
    from midfreq.data.adapters.base import BarUpdate
    from midfreq.trading.config.settings import instrument_for_symbol

    syms = ("US.AAPL", "US.MSFT")
    cfg = config_from_dict(tiny_config_dict(symbols=syms, params={"lookback": 1, "top_n": 1, "rebalance_every": 1}))
    start = pd.Timestamp("2026-10-14 13:35", tz="UTC").value
    clock = TestClock(start)
    broker = ReplayBroker(fee=0.00005, symbol_of=lambda iid: f"US.{iid.symbol}", now_ns=clock.timestamp_ns)
    node = TradingNode(cfg, mode="sim", clock=clock, records_dir=tmp_path / "rec", store_dir=tmp_path / "data",
                       records_tz="America/New_York",
                       components=standin_components(**{"execution_client:acct": broker, "data_client": FakeDataClient()}))
    broker.trading_disabled_probe = lambda: not node.trading_enabled
    node.start()
    spec = BarSpecification.from_str("1-HOUR")
    hour = 3600 * 10**9
    first = pd.Timestamp("2026-10-14 14:30", tz="UTC").value
    prices = [(100.0, 100.0), (90.0, 110.0), (99.0, 99.0), (99.0, 99.0)]
    for i, (pa, pm) in enumerate(prices):
        push = max(start, first + i * hour - hour + 10**9)
        for sym, px in zip(syms, (pa, pm)):
            broker.market[sym] = px
            node.queue.put(BarUpdate(Bar(BarType(instrument_for_symbol(sym), spec), px, px, px, px, 1.0,
                                         first + i * hour, push), False, push))
        node.advance_to(push)
    node.advance_to(pd.Timestamp("2026-10-14 20:02", tz="UTC").value)
    node.stop()
    assert not broker.violations and node.errors == 0
    d = forward_test.load(tmp_path / "rec", capital=100_000.0, market="US")
    assert d is not None and d["sessions"] == 1
    trades = d["trades"]
    assert list(zip(trades["Side"], trades["Stock"])) == [("BUY", "US.AAPL"), ("SELL", "US.AAPL"), ("BUY", "US.MSFT")]
    assert list(trades["Decided on candle"]) == ["2026-10-14 11:30", "2026-10-14 12:30", "2026-10-14 12:30"]
    assert list(d["holdings"]["Stock"]) == ["US.MSFT"]
    assert d["holdings"]["Shares"].iloc[0] == broker.held()["US.MSFT"]
    assert len(d["equity"]) == 2                     # start and end, from the broker's account total


def test_m9_the_state_file_is_world_readable_and_synced_before_the_rename(tmp_path, monkeypatch):
    """M9 (safety review): the state file is written 0644 like the original engine's (mkstemp
    makes 0600, which the dashboard's user cannot read), and synced to disk before it replaces
    the old one."""
    import os
    import stat
    from midfreq.trading.persistence import state as state_mod
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(state_mod.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    path = tmp_path / "state_x.json"
    state_mod.write_state(path, {"positions": {}})
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert synced, "fsync before the rename"
