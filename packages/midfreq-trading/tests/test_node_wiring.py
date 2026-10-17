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


def make(tmp_path, venue=None, strategy=None, **sections):
    d = tiny_config_dict(symbols=SYMS, confirm_timeout_s=150, **(strategy or {}))
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


# ── H5: no order while trading is off or the market shut ──────────────────────────────────────

class SlowCancelVenue(ScriptedVenue):
    """Accepts orders, never fills them; confirms a cancel only when the test says so."""

    def cancel_order(self, order):
        self.cancels.append(order)


def test_h5_a_reprice_does_not_send_an_order_once_trading_is_off(tmp_path):
    """H5: an order is being re-priced (its cancel sent) when the node stops; the cancel's
    confirmation arrives during shutdown. The OMS must not send the child: trading is off."""
    node, venue = make(tmp_path, venue=SlowCancelVenue(), strategy={"confirm_fills": False},
                       oms={"unfilled_order": {"wait_s": 30, "max_reprices": 2}})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    node.advance_to(node.now_ns() + 31 * SEC)                  # the re-price's cancel goes out
    (parent,) = venue.submitted
    assert venue.cancels == [parent]
    venue.cancel(parent, "CANCELLED_ALL")                       # confirmed just as the node stops
    node.stop()
    assert venue.submitted == [parent], "no child order after trading was switched off"
    assert node.risk.halted


def test_h5_a_reprice_falling_in_the_lunch_break_is_not_sent(tmp_path):
    """H5: Hong Kong closes 12:00-13:00; a re-price falling due at 12:00:10 is refused by the
    calendar check, so no order reaches a shut market."""
    hk_start = pd.Timestamp("2026-10-14 11:59:30", tz="Asia/Hong_Kong").tz_convert("UTC").value
    d = tiny_config_dict(symbols=HK_SYMS, confirm_timeout_s=150, confirm_fills=False)
    d["oms"] = {"unfilled_order": {"wait_s": 30, "max_reprices": 2}}
    venue = ScriptedVenue()
    node = TradingNode(config_from_dict(d), mode="sim", clock=TestClock(hk_start), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data", records_tz="Asia/Hong_Kong",
                       components={"execution_client:acct": venue, "data_client": FakeDataClient()})
    node.start()
    for sym, c in zip(HK_SYMS, (400.0, 60.0, 300.0, 70.0)):
        bt = BarType(instrument_for_symbol(sym), SPEC)
        node.queue.put(BarUpdate(Bar(bt, c, c, c, c, 1.0, hk_start + 1800 * SEC, hk_start), False, hk_start))
    node.advance_to(hk_start)
    a = node.host.legacy[0]
    a.portfolio.execute_trade("HK.00700", True, 100, 400.0, pd.Timestamp("2026-10-14 11:30", tz="UTC"))
    assert len(venue.submitted) == 1
    node.advance_to(hk_start + 41 * SEC)                        # 12:00:11: re-price, its cancel confirmed
    assert len(venue.submitted) == 1, "no order sent into the lunch break"
    family = node.oms.family(venue.submitted[0].client_order_id)
    assert [o.status.value for o in family][-1] in ("DENIED", "CANCELED")
    node.stop()


def test_m2_halt_cancels_working_orders_and_denies_new_ones(tmp_path):
    """M2: the node's kill switch halts risk, pauses re-pricing and cancels what is working."""
    node, venue = make(tmp_path, venue=ScriptedVenue(), strategy={"confirm_fills": False})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    (working,) = venue.submitted
    node.halt("operator")
    node.process_pending()
    assert venue.cancels == [working] and working.status.value == "CANCELED"
    a.portfolio.execute_trade("US.MSFT", True, 5, 200.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert len(venue.submitted) == 1 and node.risk.halted
    node.stop()


def test_h7_the_account_source_must_be_the_execution_clients_environment(tmp_path):
    """H7: the node asks the connector for the account client of the execution client's
    environment, with only the options the account client takes (an execution option such as
    `updates` used to make it raise, which silently switched reconciliation off), and refuses an
    account whose environment differs from the execution client's."""
    from types import SimpleNamespace
    asked = []

    class Account:
        def __init__(self, environment):
            self.environment = environment

        def snapshot(self):
            return {"cash": 1e6, "total_assets": 1e6, "market_value": 0.0}

        def positions(self):
            return []

        def close(self):
            pass

    class Connector:
        name = "ccxt"

        def __init__(self, environment):
            from midfreq.core.venues.spec import default_venues
            self.env = environment
            self.venues = default_venues()

        def served_venues(self):
            return []

        def account_client(self, exchange, secrets=(), environment=None):
            asked.append({"exchange": exchange, "environment": environment})
            return Account(self.env)

        def connect(self):
            pass

        def close(self):
            pass

    class Registry:
        def __init__(self, conn):
            self.conn = conn

        def create(self, name, config=None):
            return self.conn

    for env, usable in (("paper", True), ("live", False)):
        d = tiny_config_dict(symbols=SYMS, mode="paper", confirm_timeout_s=150)
        d["accounts"]["acct"] = {"connector": "ccxt", "options": {"exchange": "okx", "updates": "poll"},
                                 "environment": "paper"}
        node = TradingNode(config_from_dict(d), mode="paper", clock=TestClock(START), records_dir=tmp_path / env,
                           store_dir=tmp_path / "data", registry=Registry(Connector(env)),
                           components={"execution_client:acct": ScriptedVenue("paper"),
                                       "data_client": FakeDataClient()})
        node.start()
        assert asked[-1] == {"exchange": "okx", "environment": "paper"}
        assert (node._account_sources["acct"] is not None) is usable
        assert (node.account_snapshot("acct") is not None) is usable
        node.stop()


# ── M3: a crash mid-rebalance ─────────────────────────────────────────────────────────────────

class StateReadingVenue(FillingVenue):
    """Fills at once; records what the state file says when each order arrives."""

    def __init__(self, state_file_of):
        super().__init__()
        self.state_file_of = state_file_of
        self.seen = []

    def submit_order(self, order):
        import json
        st = json.loads(self.state_file_of().read_text())
        self.seen.append((st["cross_section_count"], st["next_rebalance_at"]))
        super().submit_order(order)


def _rebalance(node):
    for i, closes in enumerate(((100, 100, 100, 100), (90, 100, 100, 100), (95, 95, 95, 95))):
        at = max(START, FIRST_BAR + (i - 1) * 3600 * SEC + SEC)
        for sym, c in zip(SYMS, closes):
            bar = Bar(BarType(instrument_for_symbol(sym), SPEC), c, c, c, c, 1.0, FIRST_BAR + i * 3600 * SEC, at)
            node.queue.put(BarUpdate(bar, False, at))
        node.advance_to(at)


def test_m3_a_rebalance_is_marked_done_before_its_first_order_is_sent(tmp_path):
    """M3: the state file says the rebalance is done before its first order leaves, so a crash
    in the middle of it does not make the next session run it again over the orders sent."""
    holder = {}
    venue = StateReadingVenue(lambda: holder["a"].state_file)
    node, _ = make(tmp_path, venue=venue, strategy={"params": {"lookback": 1, "top_n": 1, "rebalance_every": 1}})
    node.start()
    holder["a"] = node.host.legacy[0]
    _rebalance(node)
    assert venue.seen, "the rebalance sent an order"
    count, due = venue.seen[0]
    assert due > count, f"state at the first order: count {count}, next rebalance due at {due}"
    node.stop()


def test_m3_a_fill_journaled_but_not_booked_before_a_crash_is_booked_at_restart(tmp_path):
    """M3: the OMS journaled the fill, then the process died before the strategy booked it and
    saved its state. The next session books it into the strategy's book from the journal."""
    node, venue = make(tmp_path, venue=FillingVenue())
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a._book_fill = lambda fill, meta: None                       # dies before booking
    node.clock.set_time(START + 60 * SEC)
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    assert venue.submitted[0].status.value == "FILLED"
    node.abandon()
    again, _ = make(tmp_path, venue=FillingVenue())
    again.clock.set_time(START + 120 * SEC)
    again.start()
    b = again.host.legacy[0]
    assert b.portfolio.get_position_qty("US.AAPL") == 10
    assert b.portfolio.cash == pytest.approx(100_000.0 - 10 * 101.0 * (1 + 0.00005))
    assert again.positions.position(b.id, instrument_for_symbol("US.AAPL")) == 10
    again.stop()
    again2, _ = make(tmp_path, venue=FillingVenue())             # booked once, not at every start
    again2.clock.set_time(START + 180 * SEC)
    again2.start()
    assert again2.host.legacy[0].portfolio.get_position_qty("US.AAPL") == 10
    again2.stop()


class OfflineFillVenue(ScriptedVenue):
    """Leaves orders working; can fill one while the node is down (reported by fills())."""

    def __init__(self):
        super().__init__()
        self.reports = []

    def fill_offline(self, order, qty, px, ts_ns):
        from midfreq.data.adapters.base import FillReport
        self.reports.append(FillReport("D-OFF", order.tags["venue_order_id"], "US.AAPL", order.side.value, qty, px,
                                       pd.Timestamp(ts_ns, unit="ns", tz="UTC"), None, "USD",
                                       str(order.client_order_id), str(order.instrument_id)))

    def fills(self, since):
        return [r for r in self.reports if r.ts >= since]


def test_m3_a_fill_made_while_the_node_was_down_reaches_the_strategys_book(tmp_path):
    venue = OfflineFillVenue()
    node, _ = make(tmp_path, venue=venue, strategy={"confirm_fills": False}, oms={"unfilled_order": {"enabled": False}})
    node.start()
    push_prices(node, (100.0, 200.0, 300.0, 400.0))
    a = node.host.legacy[0]
    a.portfolio.execute_trade("US.AAPL", True, 10, 100.0, pd.Timestamp("2026-10-14 10:30", tz="UTC"))
    (order,) = venue.submitted
    node.abandon()                                               # the process dies; the order works
    venue.fill_offline(order, 10, 100.5, START + 90 * SEC)
    again, _ = make(tmp_path, venue=venue, oms={"unfilled_order": {"enabled": False}})
    again.clock.set_time(START + 120 * SEC)
    again.start()
    b = again.host.legacy[0]
    assert b.portfolio.get_position_qty("US.AAPL") == 10
    trades = [e for e in read_events(b.session_log.path) if e["type"] == "trade"] if b.session_log else []
    again.stop()
