"""TradingNode: lifecycle order, the queue and its single thread, timers, stall handling, modes."""
from __future__ import annotations

import os
import signal
import threading
import time

import pandas as pd
import pytest

from _node_testkit import (
    FakeDataClient, ScriptedVenue, StandInMarketData, StandInOMS, standin_components, tiny_config_dict,
)
from midfreq.core.common.clock import LiveClock, TestClock
from midfreq.core.model.data import Bar, BarSpecification, BarType, QuoteTick
from midfreq.core.model.events import OrderAccepted
from midfreq.data.adapters.base import BarUpdate, StreamStatus
from midfreq.trading.config import config_from_dict, instrument_for_symbol
from midfreq.trading.interfaces import TOPIC_HEARTBEAT, TOPIC_ORDER_EVENTS
from midfreq.trading.node import NodeError, StopRequest, TimerEvent, TradingNode, build_component
from midfreq.trading.session_log import read_events

SPEC = BarSpecification.from_str("1-HOUR")
START = pd.Timestamp("2026-10-14 13:35", tz="UTC").value          # 09:35 New York, a Wednesday
FIRST_BAR = pd.Timestamp("2026-10-14 14:30", tz="UTC").value      # the 10:30 bar
HOUR = 3600 * 10**9
SEC = 10**9
SYMS = ("US.AAPL", "US.MSFT", "US.NVDA", "US.AMD")


def make_node(tmp_path, mode="sim", clock=None, venue=None, data=None, cfg_overrides=None, **strategy):
    d = tiny_config_dict(symbols=SYMS, mode=mode, **strategy)
    for k, v in (cfg_overrides or {}).items():
        d.setdefault(k, {}).update(v)
    cfg = config_from_dict(d)
    venue = venue or ScriptedVenue()
    data = data or FakeDataClient()
    node = TradingNode(cfg, mode=mode, clock=clock or TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data", records_tz="America/New_York",
                       components=standin_components(**{"execution_client:acct": venue, "data_client": data}))
    return node, venue, data


def push(node, i, closes, at=None):
    """Push the i-th hourly bar of each symbol (closes in SYMS order) and process it."""
    ts = FIRST_BAR + i * HOUR
    when = at if at is not None else max(START, ts - HOUR + SEC)
    for sym, c in zip(SYMS, closes):
        bar = Bar(BarType(instrument_for_symbol(sym), SPEC), c, c, c, c, 1.0, ts, when)
        node.queue.put(BarUpdate(bar, False, when))
    node.advance_to(when)


def test_start_runs_the_lifecycle_in_order(tmp_path):
    node, venue, data = make_node(tmp_path)
    node.start()
    assert node.lifecycle == ["config", "connect", "reference_data", "reconcile_orders", "restore", "reconcile",
                              "session_start", "warm_up", "subscribe", "running"]
    assert node.state == "running" and venue.connected
    md = node.marketdata
    assert md.calls == ["subscribe_bars"] * len(SYMS) + ["start"]  # warm-up of 0 bars asks for none
    assert len(data.subscribed) == len(SYMS)                       # one subscription per instrument
    assert node.oms.calls == ["reconcile"]                          # orders reconciled at start-up
    a = node.host.legacy[0]
    events = read_events(a.session_log.path)
    assert [e["type"] for e in events] == ["session_start"]
    assert events[0]["symbols"] == list(SYMS) and events[0]["timeframe"] == "1h"
    assert a.session_log.path.name.startswith("session_20261014_093500_crosssectionalreversal_")
    assert node.trading_enabled


def test_stop_runs_the_original_engines_shutdown_order(tmp_path):
    node, venue, _ = make_node(tmp_path)
    node.start()
    a = node.host.legacy[0]
    seen = {}
    real_stop = node.marketdata.stop

    def stop_md():
        seen["state_saved_before_bars_written"] = a.state_file.exists()
        seen["trading_on"] = not a.portfolio.warming_up
        real_stop()
    node.marketdata.stop = stop_md
    node.stop("test")
    steps = [s for s in node.lifecycle if s.startswith("stop:")]
    assert steps == ["stop:trading_off", "stop:drain", "stop:cancel_open_orders", "stop:finalise", "stop:save_state",
                     "stop:persist_bars", "stop:strategies_stop", "stop:session_end", "stop:summary",
                     "stop:disconnect"]
    assert seen == {"state_saved_before_bars_written": True, "trading_on": False}
    assert read_events(a.session_log.path)[-1]["type"] == "session_end"
    assert not venue.connected and node.state == "stopped"
    node.stop("again")                                              # idempotent
    assert node.lifecycle[-1] == "stopped"


def test_items_put_from_other_threads_are_dispatched_on_the_loop(tmp_path):
    node, venue, data = make_node(tmp_path)
    node.start()
    handler = data.handlers[0]                                      # what the MDS gave the data client
    bt = data.subscribed[0]
    seen_threads = []

    def connector_thread():
        handler(BarUpdate(Bar(bt, 1, 1, 1, 1, 1, FIRST_BAR, START), False, START))
        node.queue.put(QuoteTick(bt.instrument_id, 99.0, 101.0, 1, 1, START, START))
        node.queue.put(StreamStatus("reconnected", "test", START))
        node.queue.put(lambda: seen_threads.append(threading.current_thread()))
    t = threading.Thread(target=connector_thread)
    t.start()
    t.join()
    assert node.marketdata.forming == {}                            # nothing handled on the other thread
    node.process_pending()
    assert seen_threads == [threading.current_thread()]
    assert node.marketdata.forming[bt].ts_event == FIRST_BAR
    assert node.latest_price(bt.instrument_id) == 1
    assert node.cache.quote(bt.instrument_id).ask == 101.0
    assert node.oms.calls.count("reconcile") == 2                   # a reconnect reconciles
    assert node.dispatched["BarUpdate"] == 1 and node.dispatched["QuoteTick"] == 1


def test_order_events_go_to_the_oms_and_are_published(tmp_path):
    node, venue, _ = make_node(tmp_path)
    node.start()
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    # confirm mode: the order waited, timed out (the scripted venue never answered) and was cancelled
    assert venue.submitted and venue.cancels == venue.submitted
    order = venue.submitted[0]
    got = []
    node.msgbus.subscribe(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id), got.append)
    venue.fill(order, 10, 100.5)                                   # a late fill, from the venue's thread
    assert got == []
    node.process_pending()
    assert [type(e).__name__ for e in got] == ["OrderFilled"]
    assert a.portfolio.get_position_qty("US.AAPL") == 10
    assert node.positions.position(order.strategy_id, order.instrument_id) == 10      # booked once


def test_a_full_rebalance_through_the_node(tmp_path):
    venue = ScriptedVenue()
    venue.auto_fill_px = {}
    node, _, _ = make_node(tmp_path, venue=venue, params={"lookback": 1, "top_n": 1, "rebalance_every": 1})
    node.start()
    push(node, 0, (100, 100, 100, 100))
    push(node, 1, (90, 100, 100, 100))
    assert venue.submitted == []                                    # needs a second cross-section to rank
    push(node, 2, (95, 95, 95, 95))                                 # completes 11:30: AAPL lags
    assert [(o.side.value, str(o.instrument_id)) for o in venue.submitted] == [("BUY", "AAPL.XNAS")]
    a = node.host.legacy[0]
    assert list(a.portfolio.positions) == ["US.AAPL"]
    trades = [e for e in read_events(a.session_log.path) if e["type"] == "trade"]
    assert trades[0]["timestamp"] == "2026-10-14 11:30:00+00:00" and trades[0]["side"] == "BUY"
    node.advance_to(pd.Timestamp("2026-10-14 20:02", tz="UTC").value)
    node.stop()
    import json
    st = json.loads(a.state_file.read_text())
    assert st["positions"]["US.AAPL"]["qty"] == trades[0]["qty"]
    assert st["cross_section_count"] == 3                           # 10:30 .. 12:30, the last fed at shutdown
    assert node.errors == 0


def test_timers_reconcile_and_heartbeat(tmp_path):
    node, _, _ = make_node(tmp_path)
    node.start()
    beats = []
    node.msgbus.subscribe(TOPIC_HEARTBEAT, beats.append)
    node.advance_to(START + 299 * SEC)
    assert node.oms.calls.count("reconcile") == 1 and beats == []
    node.advance_to(START + 301 * SEC)
    assert node.oms.calls.count("reconcile") == 2
    assert len(beats) == 1 and beats[0].open_orders == 0
    node.advance_to(START + 1200 * SEC)                             # missed timers are coalesced
    assert node.oms.calls.count("reconcile") == 3 and len(beats) == 2
    assert node.marketdata.calls.count("on_timer") >= 3


def test_stall_resubscribes_once_then_stops(tmp_path):
    """The real MarketDataService detects the stall (75 minutes of session time without a bar
    update), resubscribes once, then reports it fatal; the node logs both and stops."""
    cfg = config_from_dict(tiny_config_dict(symbols=SYMS))
    data = FakeDataClient()
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data", records_tz="America/New_York",
                       components=standin_components(**{"execution_client:acct": ScriptedVenue(), "data_client": data,
                                                        "marketdata": "midfreq.trading.components:make_marketdata"}))
    node.start()
    assert len(data.subscribed) == len(SYMS)
    node.advance_to(START + 74 * 60 * SEC)
    assert node._stop_flag is None
    node.advance_to(START + 76 * 60 * SEC)
    assert len(data.subscribed) == 2 * len(SYMS) and node._stop_flag is None       # resubscribed once
    node.advance_to(START + 152 * 60 * SEC)
    assert node._stop_flag == "stall" and node.marketdata.fatal_stall
    stalls = [e for e in read_events(node.host.legacy[0].session_log.path) if e["type"] == "stall"]
    assert [s["resubscribed_before"] for s in stalls] == [False, True]
    assert stalls[0]["silent_min"] == pytest.approx(76.0)


def test_no_stall_while_the_market_is_closed(tmp_path):
    node, _, _ = make_node(tmp_path, clock=TestClock(pd.Timestamp("2026-10-14 21:00", tz="UTC").value))
    node.start()
    node.advance_to(pd.Timestamp("2026-10-15 13:00", tz="UTC").value)     # overnight
    assert node._stop_flag is None and node.marketdata.resubscribed == 0


def test_market_open_follows_the_venue_sessions(tmp_path):
    node, _, _ = make_node(tmp_path)
    node.start()
    v = instrument_for_symbol("US.AAPL").venue
    at = lambda s: pd.Timestamp(s, tz="America/New_York").tz_convert("UTC").value
    assert node.market_open(v, at("2026-10-14 09:30"))
    assert node.market_open(v, at("2026-10-14 15:59"))
    assert not node.market_open(v, at("2026-10-14 16:00"))
    assert not node.market_open(v, at("2026-10-17 11:00"))                # a Saturday
    hk = instrument_for_symbol("HK.00700").venue
    assert not node.market_open(hk, pd.Timestamp("2026-10-14 12:30", tz="Asia/Hong_Kong").tz_convert("UTC").value)


def test_run_with_a_live_clock_until_a_stop_is_requested(tmp_path):
    node, venue, _ = make_node(tmp_path, clock=LiveClock())
    ran_on = []

    def later():
        time.sleep(0.3)
        node.queue.put(lambda: ran_on.append(threading.current_thread()))
        time.sleep(0.3)
        node.request_stop("test")
    threading.Thread(target=later, daemon=True).start()
    code = node.run()
    assert code == 0 and node.state == "stopped" and node.stop_reason == "test"
    assert ran_on == [threading.current_thread()]


def test_run_stops_at_the_duration(tmp_path):
    node, _, _ = make_node(tmp_path, clock=LiveClock())
    t0 = time.time()
    assert node.run(duration_min=0.5 / 60) == 0
    assert node.stop_reason == "duration" and time.time() - t0 < 10


def test_sigterm_stops_the_node_cleanly(tmp_path):
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signal handlers need the main thread")
    node = make_node(tmp_path, clock=LiveClock())[0]
    node._install_signals = True
    previous = signal.getsignal(signal.SIGTERM)

    def send():
        for _ in range(200):
            if node.state == "running" and node._previous_signals:
                break
            time.sleep(0.02)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=send, daemon=True).start()
    assert node.run(duration_min=1) == 0
    assert node.stop_reason == "signal SIGTERM"
    assert signal.getsignal(signal.SIGTERM) is previous            # handlers restored


def test_paper_mode_refuses_a_client_that_is_not_paper(tmp_path):
    cfg = config_from_dict(tiny_config_dict(symbols=SYMS, mode="paper"))
    node = TradingNode(cfg, clock=TestClock(START), records_dir=tmp_path,
                       components=standin_components(**{"execution_client:acct": ScriptedVenue("live"),
                                                         "data_client": FakeDataClient()}))
    with pytest.raises(NodeError, match="refusing"):
        node.start()
    assert node.state == "stopped"


def test_live_mode_needs_the_account_to_say_live(tmp_path):
    cfg = config_from_dict(tiny_config_dict(symbols=SYMS, mode="live"))
    node = TradingNode(cfg, clock=TestClock(START), records_dir=tmp_path,
                       components=standin_components(**{"execution_client:acct": ScriptedVenue("live"),
                                                         "data_client": FakeDataClient()}))
    with pytest.raises(NodeError, match="environment: live"):
        node.start()


def test_build_component_passes_only_what_the_constructor_names(tmp_path):
    node, _, _ = make_node(tmp_path)
    node.start()

    class NeedsThings:
        def __init__(self, clock, msgbus, config, other=5):
            self.args = (clock, msgbus, config, other)

    class NeedsTheImpossible:
        def __init__(self, a_thing_nobody_has):
            pass

    class FromNode:
        @classmethod
        def from_node(cls, ctx, settings):
            obj = cls()
            obj.ctx, obj.settings = ctx, settings
            return obj
    obj = build_component(NeedsThings, node.ctx, "x", {"limit": 3})
    assert obj.args == (node.clock, node.msgbus, {"limit": 3}, 5)
    with pytest.raises(NodeError, match="a_thing_nobody_has"):
        build_component(NeedsTheImpossible, node.ctx, "y")
    assert build_component(FromNode, node.ctx, "z", {"k": 1}).settings == {"k": 1}
    assert build_component(f"{StandInOMS.__module__}:StandInOMS", node.ctx, "oms").clients is node.ctx.execution_by_venue


def test_records_go_to_a_separate_folder_outside_paper_and_live(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDFREQ_ROOT", str(tmp_path))
    from midfreq.core import paths
    paths.repo_root.cache_clear()
    try:
        cfg = config_from_dict(tiny_config_dict(symbols=SYMS))
        assert TradingNode(cfg, mode="shadow", clock=TestClock(START)).records_dir == tmp_path / "live_sessions" / "shadow"
        assert TradingNode(cfg, mode="sim", clock=TestClock(START)).records_dir == tmp_path / "live_sessions" / "sim"
        assert TradingNode(cfg, mode="paper", clock=TestClock(START)).records_dir == tmp_path / "live_sessions"
    finally:
        paths.repo_root.cache_clear()


def test_ops_logging_writes_the_json_file(tmp_path):
    import io
    import json
    stream = io.StringIO()
    d = tiny_config_dict(symbols=SYMS)
    cfg = config_from_dict(d)
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec", configure_logging=True,
                       ops_stream=stream, records_tz="America/New_York",
                       components=standin_components(**{"execution_client:acct": ScriptedVenue(),
                                                         "data_client": FakeDataClient()}))
    node.start()
    node.stop()
    files = list((tmp_path / "rec").glob("ops_*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(l) for l in files[0].read_text().splitlines()]
    assert rows and all(r["session"] == node.session_id for r in rows)
    assert rows[0]["ts"].startswith("2026-10-14T13:35:00")
    assert any(r["component"] == "strategy.xsr-test" for r in rows)
    console = stream.getvalue().splitlines()
    assert console[0].startswith("2026-10-14 21:35:00.000 SGT | 09:35:00 ET | INFO    | node | ")
    assert any("SESSION SUMMARY" in line for line in console)
    assert any("Session log: " in line for line in console)


def test_a_native_strategy_runs_on_bus_events(tmp_path):
    """A midfreq.strategies.trading.Strategy (MACrossover) hosted natively: bars arrive on its
    bar-type topic, its market order goes to the OMS, and its own portfolio books the fill."""
    venue = ScriptedVenue()
    venue.auto_fill_px = {"AAPL.XNAS": 50.0}
    d = tiny_config_dict(symbols=("US.AAPL",), **{"class": "midfreq.strategies.event_driven.ma_crossover:MACrossover",
                                                   "kind": "native", "params": {"fast_period": 1, "slow_period": 2,
                                                                                "trade_size": 10}})
    d["strategies"][0].pop("risk_manager")
    cfg = config_from_dict(d)
    from midfreq.trading.config import validate
    assert validate(cfg) == []
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       records_tz="America/New_York",
                       components=standin_components(**{"execution_client:acct": venue, "data_client": FakeDataClient()}))
    node.start()
    runner = node.host.native[0]
    iid = instrument_for_symbol("US.AAPL")
    for i, c in enumerate((50.0, 49.0, 48.0, 51.0, 52.0)):
        ts = FIRST_BAR + i * HOUR
        when = max(START, ts - HOUR + SEC)
        node.queue.put(BarUpdate(Bar(BarType(iid, SPEC), c, c, c, c, 1.0, ts, when), False, when))
        node.advance_to(when)
    assert [(o.side.value, o.order_type.value, o.quantity) for o in venue.submitted] == [("BUY", "MARKET", 10.0)]
    assert runner.portfolio.net_qty(iid) == 10.0
    assert runner.portfolio.account("USD").balance == 100_000.0 - 500.0
    node.stop()
    assert runner.phase == "stopped"
