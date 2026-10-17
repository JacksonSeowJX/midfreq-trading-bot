"""The node on its real components (midfreq.trading.components), with a scripted venue: a candle
strategy's order re-priced by the OMS (the wait follows the order's family), an execution
client's reconnect reconciling, the configuration's sections reaching the components, and the
records of a refused cancel."""
from __future__ import annotations

import threading

import pandas as pd
import pytest

from _node_testkit import FakeDataClient, ScriptedVenue, tiny_config_dict
from midfreq.core.common.clock import TestClock
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.data.adapters.base import BarUpdate
from midfreq.trading.config import ConfigError, config_from_dict, instrument_for_symbol, validate
from midfreq.trading.marketdata import LiveMarketDataService
from midfreq.trading.node import ExecutionStatus, TradingNode
from midfreq.trading.oms import OrderManagementSystem
from midfreq.trading.positions import PositionLedger
from midfreq.trading.refdata import ReferenceDataService
from midfreq.trading.risk import PreTradeRiskEngine
from midfreq.trading.session_log import read_events

SPEC = BarSpecification.from_str("1-HOUR")
START = pd.Timestamp("2026-10-14 13:35", tz="UTC").value          # 09:35 New York, a Wednesday
FIRST_BAR = pd.Timestamp("2026-10-14 14:30", tz="UTC").value
SYMS = ("US.AAPL", "US.MSFT", "US.NVDA", "US.AMD")
SEC = 10**9


class ChildFillingVenue(ScriptedVenue):
    """Accepts every order; leaves an original order unfilled, fills a re-priced child
    `child_delay_s` after it arrives (on the node's clock, delivered from the OMS's timer)."""

    def __init__(self, child_delay_s: float = 5.0):
        super().__init__()
        self.child_delay_ns = int(child_delay_s * SEC)
        self.clock = None
        self.pending = []

    def submit_order(self, order):
        super().submit_order(order)
        if "parent" in order.tags:
            self.pending.append((self.clock.timestamp_ns() + self.child_delay_ns, order))

    def deliver_due(self, now_ns):
        for due, order in list(self.pending):
            if due <= now_ns:
                self.pending.remove((due, order))
                self.fill(order, order.quantity, order.price)


def deliver_on_timer(node, venue):
    venue.clock = node.clock
    real = node.oms.on_timer

    def on_timer(now_ns):
        venue.deliver_due(now_ns)
        real(now_ns)
    node.oms.on_timer = on_timer


def make(tmp_path, venue=None, **sections):
    d = tiny_config_dict(symbols=SYMS, confirm_timeout_s=150)
    d.update(sections)
    cfg = config_from_dict(d)
    venue = venue or ChildFillingVenue()
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data", records_tz="America/New_York",
                       components={"execution_client:acct": venue, "data_client": FakeDataClient()})
    return node, venue


def push_prices(node, closes, at=START):
    for sym, c in zip(SYMS, closes):
        bar = Bar(BarType(instrument_for_symbol(sym), SPEC), c, c, c, c, 1.0, FIRST_BAR, at)
        node.queue.put(BarUpdate(bar, False, at))
    node.advance_to(at)


def test_the_node_builds_the_real_components(tmp_path):
    node, _ = make(tmp_path)
    node.start()
    assert isinstance(node.oms, OrderManagementSystem) and isinstance(node.risk, PreTradeRiskEngine)
    assert isinstance(node.positions, PositionLedger) and isinstance(node.marketdata, LiveMarketDataService)
    assert isinstance(node.refdata, ReferenceDataService)
    a = node.host.legacy[0]
    assert node.positions.cash(a.id) == a.portfolio.cash == 100_000.0     # the book seeded from the strategy's
    node.stop()


def test_a_repriced_order_is_waited_for_through_its_family(tmp_path):
    """The first order sits unfilled; after 30 s the OMS cancels it and sends a child at a new
    price with a new id. The strategy's execute_trade waits until the child fills (the parent's
    cancel does not end the wait), and books the child's fill as its trade."""
    node, venue = make(tmp_path, oms={"unfilled_order": {"wait_s": 30, "max_reprices": 2}})
    node.start()
    deliver_on_timer(node, venue)
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert venue.pending == [], "execute_trade returned before the child filled"
    assert len(venue.submitted) == 2
    parent, child = venue.submitted
    assert child.tags["root"] == str(parent.client_order_id) and child.tags["attempt"] == 2
    assert parent.client_order_id != child.client_order_id
    assert str(parent.client_order_id).startswith("xsr-test-20261014-")      # the OMS's own id
    assert parent.tags["strategy_order_id"].startswith(node.order_prefix)
    assert node.oms.family_done(parent.client_order_id)
    assert a.portfolio.get_position_qty("US.AAPL") == 10                    # booked from the child
    assert node.positions.position(a.id, child.instrument_id) == 10
    trades = [e for e in read_events(a.session_log.path) if e["type"] == "trade"]
    assert [(t["symbol"], t["side"], t["qty"], t["price"]) for t in trades] == [("US.AAPL", "BUY", 10.0, child.price)]
    assert trades[0]["timestamp"] == "2026-10-14 10:30:00+00:00"             # the decision candle, from the root
    assert a.open_orders() == []
    assert node.clock.timestamp_ns() - START >= 35 * SEC                     # 30 s, then the child's 5 s
    node.stop()


def test_without_a_reprice_the_wait_ends_when_the_order_is_cancelled(tmp_path):
    node, venue = make(tmp_path, oms={"unfilled_order": "original_engine"})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert len(venue.submitted) == 1 and venue.cancels == venue.submitted
    assert a.portfolio.get_position_qty("US.AAPL") == 0 and a.open_orders() == []
    assert node.clock.timestamp_ns() - START < 40 * SEC                      # 30 s and the cancel, not 150
    node.stop()


def test_an_execution_client_reconnect_reconciles(tmp_path):
    node, venue = make(tmp_path)
    node.start()
    reasons = []
    real = node._reconcile
    node._reconcile = lambda reason="periodic": (reasons.append(reason), real(reason))
    t = threading.Thread(target=lambda: (venue._status("disconnected", "link lost", START),
                                         venue._status("reconnected", "caught up", START)))
    t.start()
    t.join()
    assert reasons == []                                                     # only queued so far
    node.process_pending()
    assert node.dispatched["ExecutionStatus"] == 2
    assert reasons == ["account acct reconnected"]
    node.stop()


def test_a_refused_cancel_reaches_the_journal_and_the_strategy_topic(tmp_path):
    class Refusing(ScriptedVenue):
        def cancel_order(self, order):
            self.cancels.append(order)
            from midfreq.core.model.events import OrderCancelRejected
            self._emit(OrderCancelRejected(order.client_order_id, order.strategy_id, order.instrument_id, START,
                                           reason="being matched"))
    node, venue = make(tmp_path, venue=Refusing(), oms={"unfilled_order": {"wait_s": 30, "max_reprices": 0,
                                                                           "max_cancel_retries": 0}})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    order = venue.submitted[0]
    assert order.is_open                                                     # still working at the venue
    journal = "".join(p.read_text() for p in (tmp_path / "rec").glob("orders_*.jsonl"))
    assert "OrderCancelRejected" in journal and "being matched" in journal
    node.stop()


def test_the_configuration_reaches_the_components(tmp_path):
    node, _ = make(tmp_path, risk={"max_order_notional": 500.0, "price_band_pct": 0.05},
                   oms={"unfilled_order": {"wait_s": 45, "max_reprices": 1}},
                   marketdata={"min_cross_section_share": 0.75, "finality_grace_s": 10})
    node.start()
    sid = node.host.legacy[0].id
    assert node.risk.limits.max_order_notional == 500.0
    assert node.risk.limits_for(sid).fee_rate == 0.00005                     # the strategy's commission rate
    assert node.oms.policy.wait_s == 45 and node.oms.policy.max_reprices == 1
    assert node.marketdata._min_share == 0.75 and node.marketdata._grace_ns == 10 * SEC
    node.stop()


@pytest.mark.parametrize("section,value,message", [
    ("risk", {"max_order_value": 1}, "max_order_value"),
    ("oms", {"unfilled_order": {"reprice_attempts": 1}}, "reprice_attempts"),
    ("marketdata", {"grace": 5}, "grace"),
])
def test_a_misspelt_setting_is_refused(tmp_path, section, value, message):
    cfg = config_from_dict({**tiny_config_dict(symbols=SYMS, confirm_timeout_s=150), section: value})
    assert any(message in p for p in validate(cfg))
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data",
                       components={"execution_client:acct": ScriptedVenue(), "data_client": FakeDataClient()})
    with pytest.raises(ConfigError, match=message):
        node.start()


def test_a_strategy_that_would_give_up_before_the_oms_is_refused():
    cfg = config_from_dict({**tiny_config_dict(symbols=SYMS, confirm_timeout_s=75),
                            "oms": {"unfilled_order": {"wait_s": 30, "max_reprices": 2}}})
    assert any("confirm_timeout_s 75 is shorter" in p for p in validate(cfg))
    gtc = config_from_dict(tiny_config_dict(symbols=SYMS, confirm_timeout_s=150, pricing={"time_in_force": "GTC"}))
    assert any("DAY orders" in p for p in validate(gtc))


# ── safety review, 17 October 2026 ───────────────────────────────────────────────────────────

class FillingVenue(ScriptedVenue):
    """Accepts and fills every order at once at its limit."""

    def submit_order(self, order):
        super().submit_order(order)
        self.fill(order, order.quantity, order.price)


def test_c1_a_filled_order_resolves_at_once_under_the_id_the_adapter_stored(tmp_path):
    """C1: the adapter takes its order id from the OMS before it stores its record, so the OMS
    never renames the order, the fill resolves the wait at once (no confirm timeout) and the
    trade line carries the decision candle's timestamp."""
    node, venue = make(tmp_path, venue=FillingVenue())
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    seen = []
    real_submit = node.oms.submit

    def spy(order):
        before = order.client_order_id
        stored = before in a._orders
        real_submit(order)
        seen.append((before, order.client_order_id, stored))
    node.oms.submit = spy
    t0 = node.now_ns()
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert node.now_ns() == t0                                   # resolved without waiting
    (before, after, stored), = seen
    assert before == after and stored                            # never renamed; stored first
    assert str(before).startswith("xsr-test-20261014-")
    trades = [e for e in read_events(a.session_log.path) if e["type"] == "trade"]
    assert [(t["timestamp"], t["symbol"], t["qty"]) for t in trades] == [("2026-10-14 10:30:00+00:00", "US.AAPL", 10.0)]
    assert a.open_orders() == []
    node.stop()


def _tree(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("mode", ["sim", "shadow"])
def test_c4_shadow_and_sim_never_write_the_real_bar_store(tmp_path, monkeypatch, mode):
    """C4: a shadow or sim node, even configured to persist bars and given no store of its own,
    reads the real data/ for warm-up but never writes its bars (replayed or shadowed) into it."""
    from midfreq.core import paths
    root = tmp_path / "root"
    (root / "data" / "US_AAPL").mkdir(parents=True)
    (root / "data" / "US_AAPL" / "1h.parquet").write_bytes(b"the real cache")
    before = _tree(root / "data")
    monkeypatch.setenv("MIDFREQ_ROOT", str(root))
    paths.repo_root.cache_clear()
    try:
        d = tiny_config_dict(symbols=SYMS, confirm_timeout_s=150)
        d["data"] = {"persist_bars": True}
        cfg = config_from_dict(d)
        comps = {"data_client": FakeDataClient()}
        if mode == "sim":
            comps["execution_client:acct"] = ScriptedVenue()
        node = TradingNode(cfg, mode=mode, clock=TestClock(START), records_dir=tmp_path / "rec",
                           records_tz="America/New_York", components=comps)
        node.start()
        push_prices(node, (100.0, 200.0, 300.0, 400.0))
        later = FIRST_BAR + 3600 * SEC
        for sym, c in zip(SYMS, (101.0, 201.0, 301.0, 401.0)):
            bar = Bar(BarType(instrument_for_symbol(sym), SPEC), c, c, c, c, 1.0, later, FIRST_BAR + SEC)
            node.queue.put(BarUpdate(bar, False, FIRST_BAR + SEC))
        node.advance_to(FIRST_BAR + SEC)
        node.stop()
        assert node.marketdata.session_bars(), "bars were finalised in the session"
        assert _tree(root / "data") == before
    finally:
        paths.repo_root.cache_clear()


def test_c4_a_replay_leaves_the_real_data_dir_untouched(tmp_path, monkeypatch):
    """C4: a replay on the real components writes its bars to its own store only."""
    from midfreq.core import paths
    from midfreq.trading.replay import RecordedSession, replay_sessions
    root = tmp_path / "root"
    (root / "data" / "US_AAPL").mkdir(parents=True)
    (root / "data" / "US_AAPL" / "1h.parquet").write_bytes(b"the real cache")
    before = _tree(root / "data")
    monkeypatch.setenv("MIDFREQ_ROOT", str(root))
    paths.repo_root.cache_clear()
    try:
        hours = [(f"2026-10-14 {h}:30:00", {s: 100.0 + i + k for k, s in enumerate(SYMS)})
                 for i, h in enumerate((10, 11, 12))]
        sess = RecordedSession(None, "20261014", "CrossSectionalReversal", list(SYMS), "1h", hours)
        cfg = config_from_dict(tiny_config_dict(symbols=SYMS, confirm_timeout_s=150))
        report = replay_sessions(cfg, [sess], records_dir=tmp_path / "rec", store_dir=tmp_path / "store",
                                 components={})
        assert report.sessions and not report.sessions[0].crashed
        assert _tree(root / "data") == before
        assert list((tmp_path / "store").rglob("1h.parquet")), "the replay wrote its bars to its own store"
    finally:
        paths.repo_root.cache_clear()


def test_h1_a_resumed_strategy_can_sell_what_it_carried(tmp_path):
    """H1: the PositionKeeper is built before risk and seeded (with the right arguments) from the
    strategy's resumed book, so a sell of a carried position passes the risk check, and cash is
    the saved cash, not 0."""
    import json
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "state_crosssectionalreversal.json").write_text(json.dumps(
        {"saved_at": "x", "positions": {"US.AAPL": {"qty": 10.0, "entry_price": 100.0}}, "peak_prices": {},
         "cross_section_count": 3, "next_rebalance_at": 4, "cash": 99_000.0}, indent=1))
    node, venue = make(tmp_path, venue=FillingVenue())
    node.start()
    a = node.host.legacy[0]
    aapl = instrument_for_symbol("US.AAPL")
    assert node.positions.position(a.id, aapl) == 10 and node.positions.cash(a.id) == 99_000.0
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a.portfolio.execute_trade("US.AAPL", False, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert [(o.side.value, o.quantity) for o in venue.submitted] == [("SELL", 10.0)]
    assert node.risk.denied == 0 and a.portfolio.get_position_qty("US.AAPL") == 0
    node.stop()


def test_h1_board_lots_come_from_the_connectors_instrument_provider():
    """H1: reference data is built from the connectors, so HSBC trades in lots of 400 (the
    venue's default lot is 100) and risk refuses an odd lot."""
    from types import SimpleNamespace
    from midfreq.core.common.msgbus import MessageBus
    from midfreq.core.model import Equity, OrderFactory, OrderSide, StrategyId, TimeInForce
    from midfreq.core.venues.spec import default_venues
    from midfreq.trading.components import make_refdata
    hsbc = instrument_for_symbol("HK.00005")
    venues = default_venues()

    class Provider:
        def load(self, ids):
            return [Equity(id=i, raw_symbol=str(i.symbol), currency="HKD", lot_size=400.0) for i in ids]

        def find(self, iid):
            return None
    conn = SimpleNamespace(name="moomoo", venues=venues, instrument_provider=Provider(),
                           served_venues=lambda: [hsbc.venue])
    ctx = SimpleNamespace(venues=venues, connectors={"moomoo": conn}, config=SimpleNamespace(resolve_path=lambda p: p))
    refdata = make_refdata(ctx, {})
    assert refdata.instrument(hsbc).lot_size == 400.0
    assert venues.get(hsbc.venue).default_lot_size != 400.0
    led = PositionLedger(TestClock(START), MessageBus(), capital={"S": 1e6})
    risk = PreTradeRiskEngine(TestClock(START), MessageBus(), led, lot_size=lambda i: refdata.instrument(i).quantity_step,
                              reference_price=lambda i: 50.0)
    f = OrderFactory(StrategyId("S"))
    assert "lots of 400" in risk.check(f.limit(hsbc, OrderSide.BUY, 300, 50.0, TimeInForce.DAY))
    assert risk.check(f.limit(hsbc, OrderSide.BUY, 400, 50.0, TimeInForce.DAY)) is None


# ── H3: one state file, one writer ────────────────────────────────────────────────────────────

HK_SYMS = ("HK.00700", "HK.00005", "HK.00388", "HK.00941")
US_STATE = {"saved_at": "2026-10-15 04:01:04", "positions": {"US.INTC": {"qty": 302.0, "entry_price": 109.585}},
            "peak_prices": {}, "cross_section_count": 70, "next_rebalance_at": 76, "cash": 4738.62}


def paper_node(tmp_path, records, symbols=HK_SYMS, **strategy):
    from types import SimpleNamespace
    d = tiny_config_dict(symbols=symbols, mode="paper", confirm_timeout_s=150, **strategy)
    account = SimpleNamespace(snapshot=lambda: {"cash": 1e6, "total_assets": 1e6, "market_value": 0.0},
                              positions=lambda: [], close=lambda: None)
    return TradingNode(config_from_dict(d), mode="paper", clock=TestClock(START), records_dir=records,
                       store_dir=tmp_path / "data", records_tz="Asia/Hong_Kong",
                       components={"execution_client:acct": ScriptedVenue("paper"), "data_client": FakeDataClient(),
                                   "account_source:acct": account})


def test_h3_a_paper_node_does_not_take_the_forward_tests_state_file(tmp_path):
    """H3: an HK paper node running Cross-Sectional Reversal in live_sessions/ with no state_name
    uses a state file named after its strategy id, never the US forward test's
    state_crosssectionalreversal.json, and its session logs are its own."""
    import json
    records = tmp_path / "live_sessions"
    records.mkdir()
    us = records / "state_crosssectionalreversal.json"
    us.write_text(json.dumps(US_STATE, indent=1))
    before = us.read_bytes()
    node = paper_node(tmp_path, records)
    node.start()
    a = node.host.legacy[0]
    assert a.state_file.name == "state_xsr-test.json" and a.cross_section_count == 0
    node.stop()
    assert us.read_bytes() == before
    assert not list(records.glob("session_*crosssectionalreversal*"))


def test_h3_a_state_on_another_market_is_refused(tmp_path):
    import json
    records = tmp_path / "live_sessions"
    records.mkdir()
    (records / "state_crosssectionalreversal.json").write_text(json.dumps(US_STATE, indent=1))
    node = paper_node(tmp_path, records, state_name="crosssectionalreversal")
    with pytest.raises(Exception, match="US.INTC"):
        node.start()
    assert node.state == "stopped"


def test_h3_two_nodes_cannot_share_a_records_folder(tmp_path):
    records = tmp_path / "live_sessions"
    first = paper_node(tmp_path, records)
    first.start()
    second = paper_node(tmp_path, records)
    with pytest.raises(Exception, match="another node"):
        second.start()
    first.stop()
    third = paper_node(tmp_path, records)
    third.start()                                           # the lock went with the first node
    third.stop()


def test_h3_the_mode_defaults_to_shadow():
    d = tiny_config_dict(symbols=SYMS, confirm_timeout_s=150)
    d["node"].pop("mode")
    assert config_from_dict(d).mode == "shadow"
