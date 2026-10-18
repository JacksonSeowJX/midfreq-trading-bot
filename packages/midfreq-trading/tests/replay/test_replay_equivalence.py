"""
Replay equivalence: the node, in sim mode with a test clock, fed the candles the recorded October
2026 S&P 100 sessions received, makes the same decisions as the original engine fed the same
candles by the existing replay harness (scripts/live/replay_live_sessions.py): the same orders,
for the same symbols, quantities and prices, decided on the same candles, session by session;
and the harness's invariants hold for the node.

Both runs start from the same bar cache: a synthetic history (deterministic, 84 hourly bars per
symbol before the first replayed day) written to two temporary folders, because a worktree has no
data/ cache and tests never read or write the real one. The recorded sessions are copied into a
temporary folder first; live_sessions/ is only read.

The original engine is driven exactly as the harness drives it (its FakeBroker, FakeProvider and
ReplayClock are imported from the script). Most tests run the node on minimal stand-ins for the
OMS, risk, positions, market data and reference data (tests/_node_testkit.py), which isolate the
node's own logic; `test_replay_equivalence_with_the_real_components` runs the same comparisons
on the components the node builds by default (midfreq.trading.components: A's OMS, pre-trade risk
and position ledger, the order journal, C's market data and reference data), with the forward
test's configuration (its risk limits included) and the original engine's unfilled-order policy.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import random
import zlib
from datetime import datetime as _dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from _node_testkit import standin_components
from midfreq.core.paths import repo_root
from midfreq.trading.config.settings import load_config
from midfreq.trading.replay.sessions import (
    ReplayBroker, compare_decisions, decisions, load_recorded_sessions, repeat_backwards, replay_sessions,
)
from midfreq.core.model.identifiers import InstrumentId
from midfreq.trading.persistence.session_log import read_events

REPO = repo_root()
NY = ZoneInfo("America/New_York")
CONFIG = REPO / "config" / "node_sp100.yaml"


def _harness():
    spec = importlib.util.spec_from_file_location("replay_live_sessions_harness",
                                                  REPO / "scripts" / "live" / "replay_live_sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed_store(dirs, sessions, days: int = 12):
    """A deterministic hourly history for every symbol, ending the day before the first session."""
    first = pd.Timestamp(sessions[0].hours[0][0]).tz_localize(None).normalize()
    bdays = pd.bdate_range(end=first - pd.Timedelta(days=1), periods=days)
    stamps = [d + pd.Timedelta(hours=h, minutes=m) for d in bdays
              for h, m in ((10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30), (16, 0))]
    index = pd.DatetimeIndex(stamps).tz_localize("UTC")
    index.name = "timestamp"
    first_close = {}
    for ts, closes in sessions[0].hours:
        for sym, c in closes.items():
            first_close.setdefault(sym, c)
    for sym in sessions[0].symbols:
        rng = random.Random(zlib.crc32(sym.encode()))
        p = first_close.get(sym, 100.0) * (0.9 + 0.2 * rng.random())
        closes = []
        for _ in stamps:
            p *= 1 + rng.gauss(0, 0.006)
            closes.append(round(p, 4))
        df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                           "volume": [1.0] * len(closes)}, index=index)
        for d in dirs:
            folder = Path(d) / sym.replace(".", "_", 1)
            folder.mkdir(parents=True, exist_ok=True)
            df.to_parquet(folder / "1h.parquet")


def run_original(sessions, data_dir: Path, log_dir: Path, params, symbols, cap, fee, broker_kwargs,
                 crash_session=None, state=None, broker_pos=None):
    """The original LiveTradingEngine through `sessions`, exactly as the harness's run_scenario
    drives it. Returns (trades, per-session clocks, broker)."""
    import midfreq.data.storage as storage_mod
    import midfreq.live.live_engine as live_mod
    from midfreq.core.candle import Candle, Timeframe
    from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer
    from midfreq.live.live_engine import LiveTradingEngine
    from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

    h = _harness()
    log_dir.mkdir(parents=True, exist_ok=True)
    if state is not None:
        import json
        (log_dir / "state_crosssectionalreversal.json").write_text(json.dumps(state, indent=1))
    real = storage_mod.DataStorage
    storage_mod.DataStorage = lambda *a, **k: real(base_path=str(data_dir))
    real_dt = live_mod.datetime
    live_mod.datetime = h.ReplayClock
    broker = h.FakeBroker(**broker_kwargs)
    if broker_pos:
        broker.pos = {s: {"qty": q, "cost": c} for s, (q, c) in broker_pos.items()}
    trades, clocks = [], []
    buf = io.StringIO()
    try:
        for day, sess in enumerate(sessions):
            hours = sess.hours
            day0 = pd.Timestamp(hours[0][0]).tz_localize(None).normalize()
            h.ReplayClock.fake = _dt(day0.year, day0.month, day0.day, 9, 35, tzinfo=NY)
            sizer = create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=params["top_n"])
            rm = RiskManager(stop_loss_pct=None, max_drawdown_pct=0.15, position_sizer=sizer)
            with contextlib.redirect_stdout(buf):
                eng = LiveTradingEngine(provider=h.FakeProvider(), gateway=broker,
                                        strategy_class=STRATEGY_REGISTRY["Cross-Sectional Reversal"]["class"],
                                        symbols=symbols, timeframe=Timeframe.HOUR_1, risk_manager=rm,
                                        session_log_dir=str(log_dir), commission_rate=fee, capital_cap=cap, **params)
                broker.trading_disabled_probe = lambda: eng.portfolio.warming_up
                eng._warm_up()
                xs_start = eng.strategy._cross_section_count
                for i, (ts, closes) in enumerate(hours):
                    t = pd.Timestamp(ts)
                    for sym, c in closes.items():
                        broker.market[sym] = c
                        eng._on_candle_update(sym, Candle(timestamp=t, open=c, high=c, low=c, close=c, volume=1))
                    eng.portfolio.sync_with_broker()
                    if crash_session == day and i == len(hours) // 2:
                        break
                if crash_session != day:
                    h.ReplayClock.fake = _dt(day0.year, day0.month, day0.day, 16, 0, 30, tzinfo=NY)
                    eng._shutdown()
            clocks.append((xs_start, eng.strategy._cross_section_count))
            trades += [e for e in read_events(eng._session_file) if e.get("type") == "trade"]
    finally:
        storage_mod.DataStorage = real
        live_mod.datetime = real_dt
        h.ReplayClock.fake = None
    return trades, clocks, broker


def _trade_rows(trades):
    return [(str(pd.Timestamp(t["timestamp"])), t["symbol"], t["side"], float(t["qty"]), float(t["price"]),
             t.get("exit_reason")) for t in trades]


@pytest.fixture(scope="module")
def sp100():
    cfg = load_config(CONFIG, mode="sim")
    return cfg, cfg.strategy("sp100-xsr")


def _sessions(recorded_dir, copies):
    sessions = load_recorded_sessions(recorded_dir, "crosssectionalreversal")
    assert sessions, "no recorded cross-sectional sessions to replay"
    return repeat_backwards(sessions, copies) if copies > 1 else sessions


def _compare(tmp_path, recorded_dir, sp100, copies=1, broker_kwargs=None, crash_session=None, components=None):
    """components: None runs the node on the stand-ins; a mapping (even empty) on the node's
    defaults with those overrides."""
    cfg, s = sp100
    sessions = _sessions(recorded_dir, copies)
    orig_data, node_data = tmp_path / "orig_data", tmp_path / "node_data"
    seed_store([orig_data, node_data], sessions)
    symbols = sessions[0].symbols
    params = {k: int(v) for k, v in s.params.items()}
    kw = dict(broker_kwargs or {})
    orig_trades, orig_clocks, orig_broker = run_original(
        sessions, orig_data, tmp_path / "orig_logs", params, symbols, s.capital, s.commission_rate, kw,
        crash_session=crash_session)
    report = replay_sessions(cfg, sessions, records_dir=tmp_path / "node_logs", store_dir=node_data,
                             strategy_id=s.id, broker_kwargs=kw, crash_session=crash_session,
                             components=standin_components() if components is None else components)
    return sessions, orig_trades, orig_clocks, orig_broker, report


def test_recorded_sessions_load(recorded_sessions):
    sessions = load_recorded_sessions(recorded_sessions)
    assert len(sessions) >= 13
    assert all(len(s.symbols) == 98 for s in sessions)
    first = sessions[0]
    assert first.date == "20260928" and [len(c) for _, c in first.hours] == [98, 98, 98, 14]
    trades = [t for s in sessions for t in s.trades]
    assert {(t["timestamp"][:16], t["symbol"]) for t in trades} >= {("2026-10-13 10:30", "US.CAT"),
                                                                   ("2026-10-13 10:30", "US.AMD")}
    copies = repeat_backwards(sessions, 3)
    assert len(copies) == 3 * len(sessions)
    assert copies[-1].hours == sessions[-1].hours


def test_node_makes_the_original_engines_decisions(tmp_path, recorded_sessions, sp100):
    """The clean broker: the same orders, decided on the same candles, at the same quantities and
    prices; the same rebalance clock every session; and every harness invariant holds."""
    sessions, orig_trades, orig_clocks, orig_broker, report = _compare(tmp_path, recorded_sessions, sp100, copies=2)
    assert report.ok, report.fails
    node_trades = report.trades
    assert len(orig_trades) >= 12, "the replay should cross several rebalances"
    assert compare_decisions(orig_trades, node_trades, with_qty=True) == []
    assert _trade_rows(node_trades) == _trade_rows(orig_trades)
    assert [(r.xs_start, r.xs_end) for r in report.sessions] == orig_clocks
    assert report.sessions[-1].broker_held == {s: p["qty"] for s, p in orig_broker.pos.items()}
    assert abs(report.sessions[-1].cash_broker - orig_broker.cash) < 1e-6
    assert report.rebalances >= 4


def test_node_candles_match_the_original_engines(tmp_path, recorded_sessions, sp100):
    """Every recorded candle reaches the strategy and the session log, as with the original engine."""
    cfg, s = sp100
    sessions = _sessions(recorded_sessions, 1)[:3]
    orig_data, node_data = tmp_path / "o", tmp_path / "n"
    seed_store([orig_data, node_data], sessions)
    params = {k: int(v) for k, v in s.params.items()}
    run_original(sessions, orig_data, tmp_path / "orig_logs", params, sessions[0].symbols, s.capital,
                 s.commission_rate, {})
    report = replay_sessions(cfg, sessions, records_dir=tmp_path / "node_logs", store_dir=node_data,
                             strategy_id=s.id, components=standin_components())
    assert report.ok, report.fails

    def closes(folder):
        out = []
        for f in sorted(Path(folder).glob("session_*.jsonl")):
            out.append(sorted((e["symbol"], e["timestamp"], e["close"]) for e in read_events(f)
                              if e.get("type") == "candle_close"))
        return out
    assert closes(tmp_path / "node_logs") == closes(tmp_path / "orig_logs")
    # 28 Sep: 3 full hours and 14 of 98 symbols in the 4th, all logged.
    assert len(closes(tmp_path / "node_logs")[0]) == 3 * 98 + 14


@pytest.mark.parametrize("name,kwargs", [("every 4th order not filled", {"reject_every": 4}),
                                         ("partial fills", {"partial": True})])
def test_node_matches_the_original_engine_with_a_failing_broker(tmp_path, recorded_sessions, sp100, name, kwargs):
    sessions, orig_trades, orig_clocks, orig_broker, report = _compare(tmp_path, recorded_sessions, sp100,
                                                                       broker_kwargs=kwargs)
    assert report.ok, report.fails
    assert _trade_rows(report.trades) == _trade_rows(orig_trades)
    assert [(r.xs_start, r.xs_end) for r in report.sessions] == orig_clocks
    assert report.sessions[-1].broker_held == {s: p["qty"] for s, p in orig_broker.pos.items()}


def test_node_matches_the_original_engine_through_a_crash(tmp_path, recorded_sessions, sp100):
    """The process dies mid-session on day 3; the next session resumes from the state saved at the
    last cross-section, for both."""
    sessions, orig_trades, orig_clocks, orig_broker, report = _compare(tmp_path, recorded_sessions, sp100,
                                                                       crash_session=2)
    assert report.ok, report.fails
    assert report.sessions[2].crashed
    assert _trade_rows(report.trades) == _trade_rows(orig_trades)
    assert [(r.xs_start, r.xs_end) for r in report.sessions] == orig_clocks


def test_node_invariants_hold_when_broker_queries_fail(tmp_path, recorded_sessions, sp100):
    """10% of account queries fail. The two engines ask the broker at different moments, so their
    failures land differently; the node is held to the harness's invariants only."""
    cfg, s = sp100
    sessions = _sessions(recorded_sessions, 1)
    node_data = tmp_path / "node_data"
    seed_store([node_data], sessions)
    report = replay_sessions(cfg, sessions, records_dir=tmp_path / "node_logs", store_dir=node_data,
                             strategy_id=s.id, broker_kwargs={"query_fail_rate": 0.10, "seed": 3},
                             components=standin_components())
    assert report.ok, report.fails


def test_replay_reads_the_recorded_trades_decision_by_decision(recorded_sessions):
    """compare_decisions groups by decision candle: the recorded 13 Oct rebalance."""
    sessions = load_recorded_sessions(recorded_sessions)
    recorded = [t for s in sessions for t in s.trades]
    d = decisions(recorded)
    assert d["2026-10-13 10:30:00+00:00"] == {"SELL": ["US.ACN", "US.QCOM"], "BUY": ["US.AMD", "US.CAT"]}
    assert compare_decisions(recorded, recorded) == []
    assert compare_decisions(recorded, recorded[:-1]) != []


@pytest.mark.parametrize("name,copies,kwargs,crash", [
    ("clean broker", 2, {}, None),
    ("every 4th order not filled", 1, {"reject_every": 4}, None),
    ("partial fills", 1, {"partial": True}, None),
    ("a crash mid-session", 1, {}, 2),
])
def test_replay_equivalence_with_the_real_components(tmp_path, recorded_sessions, sp100, name, copies, kwargs, crash):
    """The node on its real components (no stand-in anywhere: the default factories build the
    OMS, risk, positions, journal, market data and reference data from config/node_sp100.yaml)
    makes the original engine's decisions on the recorded sessions: the same orders, at the same
    quantities and prices, on the same candles; the same rebalance clock; the same broker
    holdings and cash; and the PositionKeeper's book equals the strategy's own."""
    sessions, orig_trades, orig_clocks, orig_broker, report = _compare(
        tmp_path, recorded_sessions, sp100, copies=copies, broker_kwargs=kwargs, crash_session=crash, components={})
    assert report.ok, report.fails
    assert compare_decisions(orig_trades, report.trades, with_qty=True) == []
    assert _trade_rows(report.trades) == _trade_rows(orig_trades)
    assert [(r.xs_start, r.xs_end) for r in report.sessions] == orig_clocks
    assert report.sessions[-1].broker_held == {s: p["qty"] for s, p in orig_broker.pos.items()}
    assert abs(report.sessions[-1].cash_broker - orig_broker.cash) < 1e-6
    if not kwargs and crash is None:
        assert len(orig_trades) >= 12 and report.rebalances >= 4
    journal = sorted((tmp_path / "node_logs").glob("orders_*.jsonl"))
    assert journal, "the OMS journaled its orders"


def test_without_confirming_fills_the_baskets_differ_but_the_books_hold(tmp_path, recorded_sessions, sp100):
    """Why candle strategies wait for each order's outcome (confirm_fills): sent asynchronously,
    a rebalance's sells are still unbooked when its buys are decided, so the strategy still counts
    top_n names and buys nothing. The records stay consistent (no fill is lost to a reconciliation
    while it is in flight), but the decisions are no longer the original engine's."""
    import copy
    cfg, s = sp100
    cfg, s = copy.deepcopy(cfg), copy.deepcopy(s)
    cfg.strategy(s.id).confirm_fills = False
    sessions, orig_trades, _, _, report = _compare(tmp_path, recorded_sessions, (cfg, cfg.strategy(s.id)))
    assert report.ok, report.fails
    assert compare_decisions(orig_trades, report.trades) != []



# The forward test's state on Saturday 17 October 2026 (live_sessions/state_crosssectionalreversal.json,
# copied here so the test never reads live_sessions/): clock 84, the next rebalance due at 95.
STATE_17_OCT = {"saved_at": "2026-10-17 04:01:04.571102",
                "positions": {"US.AMAT": {"qty": 74.0, "entry_price": 432.18},
                              "US.MU": {"qty": 35.0, "entry_price": 922.15}},
                "peak_prices": {"US.AMAT": 542.28, "US.MU": 1088.3995},
                "cross_section_count": 84, "next_rebalance_at": 95, "cash": 33310.90598600003}


def _synthetic_days(symbols, days=("2026-10-19", "2026-10-20")):
    """Seven hourly cross-sections a day, every symbol, from a deterministic walk (AMAT and MU
    near their recent prices)."""
    from midfreq.trading.replay.sessions import RecordedSession
    start = {"US.AMAT": 540.0, "US.MU": 1080.0}
    out = []
    for d, day in enumerate(days):
        hours = []
        for k, hm in enumerate(("10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:00")):
            closes = {}
            for sym in symbols:
                rng = random.Random(zlib.crc32(f"{sym}|{day}|{hm}".encode()))
                base = start.get(sym, 50.0 + (zlib.crc32(sym.encode()) % 400))
                closes[sym] = round(base * (1 + 0.004 * (d * 7 + k)) * (1 + rng.gauss(0, 0.01)), 2)
            hours.append((f"{day} {hm}:00+00:00", closes))          # as recorded: local close tagged UTC
        out.append(RecordedSession(None, day.replace("-", ""), "CrossSectionalReversal", list(symbols), "1h", hours))
    return out


def test_from_the_real_state_the_first_node_rebalance_matches_the_original(tmp_path, sp100):
    """The takeover: the node starts on Monday 19 October from the forward test's real state of
    17 October (clock 84, AMAT 74, MU 35, cash 33,310.91; the broker holding those) and runs a
    synthetic Monday and Tuesday on its real components with the S&P 100 configuration. Monday
    has no rebalance (84 -> 91); the first one falls due at 95, on Tuesday's 13:30 cross-section.
    The node sends the original engine's orders, on the same candle, at the same quantities and
    prices, and both end with the same clock, holdings and cash."""
    import copy
    from midfreq.trading.replay.sessions import ReplayBroker, replay_sessions
    cfg, s = sp100
    symbols = list(cfg.universes[s.universe].symbols)
    sessions = _synthetic_days(symbols)
    orig_data, node_data = tmp_path / "orig_data", tmp_path / "node_data"
    seed_store([orig_data, node_data], sessions)
    params = {k: int(v) for k, v in s.params.items()}
    held = {"US.AMAT": (74.0, 432.18), "US.MU": (35.0, 922.15)}
    orig_trades, orig_clocks, orig_broker = run_original(
        sessions, orig_data, tmp_path / "orig_logs", params, symbols, s.capital, s.commission_rate, {},
        state=copy.deepcopy(STATE_17_OCT), broker_pos=held)
    import json
    node_logs = tmp_path / "node_logs"
    node_logs.mkdir()
    (node_logs / "state_crosssectionalreversal.json").write_text(json.dumps(STATE_17_OCT, indent=1))
    broker = ReplayBroker(fee=s.commission_rate)
    broker.pos = {sym: {"qty": q, "cost": c, "iid": InstrumentId.from_str(f"{sym[3:]}.XNAS")}
                  for sym, (q, c) in held.items()}
    report = replay_sessions(cfg, sessions, records_dir=node_logs, store_dir=node_data, strategy_id=s.id,
                             broker=broker, components={})
    assert report.ok, report.fails
    assert [(r.xs_start, r.xs_end) for r in report.sessions] == orig_clocks == [(84, 91), (91, 98)]
    assert report.sessions[0].orders == 0                         # Monday: no rebalance
    assert orig_trades, "the rebalance at 95 traded"
    assert {str(pd.Timestamp(t["timestamp"]))[:16] for t in orig_trades} == {"2026-10-20 13:30"}
    assert _trade_rows(report.trades) == _trade_rows(orig_trades)
    assert report.sessions[-1].broker_held == {k: v["qty"] for k, v in orig_broker.pos.items()}
    assert abs(report.sessions[-1].cash_broker - orig_broker.cash) < 1e-6
    final = json.loads((node_logs / "state_crosssectionalreversal.json").read_text())
    orig_final = json.loads((tmp_path / "orig_logs" / "state_crosssectionalreversal.json").read_text())
    assert final["positions"] == orig_final["positions"]
    assert abs(final["cash"] - orig_final["cash"]) < 1e-6
    assert (final["cross_section_count"], final["next_rebalance_at"]) == \
        (orig_final["cross_section_count"], orig_final["next_rebalance_at"])
