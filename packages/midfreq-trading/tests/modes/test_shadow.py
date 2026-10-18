"""Shadow mode never sends an order: not through its own client, and not through the venue's."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from _node_testkit import FakeDataClient, standin_components, tiny_config_dict
from midfreq.core.common.clock import TestClock
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.enums import OrderSide
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled
from midfreq.core.model.identifiers import InstrumentId, StrategyId
from midfreq.core.model.orders import OrderFactory
from midfreq.data.adapters.base import BarUpdate
from midfreq.trading.config.settings import config_from_dict, instrument_for_symbol
from midfreq.trading.node.kernel import TradingNode
from midfreq.trading.persistence.session_log import read_events
from midfreq.trading.modes.shadow import ShadowExecutionClient

IID = InstrumentId.from_str("AAPL.XNAS")
NOW = pd.Timestamp("2026-10-14 14:00", tz="UTC").value


def _order(side=OrderSide.BUY, qty=10, price=101.0):
    o = OrderFactory(StrategyId("s"), lambda: NOW).limit(IID, side, qty, price)
    o.reference_price = 100.0
    return o


def test_none_mode_logs_journals_and_cancels(tmp_path):
    events = []
    c = ShadowExecutionClient("acct", journal_dir=tmp_path, now_ns=lambda: NOW)
    c.set_event_handler(events.append)
    o = _order()
    c.submit_order(o)
    assert [type(e) for e in events] == [OrderAccepted, OrderCanceled]
    assert events[1].reason == "shadow: not sent"
    rows = [json.loads(l) for l in (tmp_path / "shadow_20261014.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows] == ["intent", "cancel"]
    assert rows[0]["client_order_id"] == str(o.client_order_id) and rows[0]["price"] == 101.0
    assert rows[0]["reference_price"] == 100.0
    assert c.environment == "sim" and c.open_orders() == []


def test_quote_mode_fills_at_the_quote_when_marketable(tmp_path):
    events = []
    quote = {"q": (99.0, 100.5, 100.0)}
    c = ShadowExecutionClient("acct", fill_mode="quote", price_source=lambda iid: quote["q"], now_ns=lambda: NOW)
    c.set_event_handler(events.append)
    c.submit_order(_order(price=101.0))
    fill = events[-1]
    assert isinstance(fill, OrderFilled) and fill.last_px == 100.5 and fill.last_qty == 10
    quote["q"] = (99.0, 102.0, 101.5)
    o2 = _order(price=101.0)
    c.submit_order(o2)
    assert isinstance(events[-1], OrderAccepted) and len(c.open_orders()) == 1   # not marketable: working
    quote["q"] = (99.0, 100.9, 100.0)
    c.on_quote(IID)
    assert isinstance(events[-1], OrderFilled) and events[-1].last_px == 100.9
    o3 = _order(side=OrderSide.SELL, price=150.0)
    c.submit_order(o3)
    c.cancel_order(o3)
    assert isinstance(events[-1], OrderCanceled)
    assert len(c.fills(pd.Timestamp("2026-10-14", tz="UTC"))) == 2


class ForbiddenConnector:
    """A venue connector whose trading API must never be touched in shadow mode."""
    name = "moomoo"

    def __init__(self):
        self.data_client = FakeDataClient()
        self.connected = False

    def execution_client(self, **options):
        raise AssertionError("shadow mode asked the venue for an execution client")

    def account_client(self, **options):
        raise AssertionError("shadow mode asked the venue for the account")

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False


class Registry:
    def __init__(self, connector):
        self.connector = connector
        self.created = []

    def create(self, name, config=None, **kw):
        self.created.append(name)
        return self.connector


def test_a_shadow_node_trades_on_paper_only_in_its_log(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDFREQ_ROOT", str(tmp_path))
    from midfreq.core import paths
    paths.repo_root.cache_clear()
    try:
        live = tmp_path / "live_sessions"
        live.mkdir()
        seed = {"saved_at": "x", "positions": {}, "peak_prices": {}, "cross_section_count": 5,
                "next_rebalance_at": 6, "cash": 100000.0}
        (live / "state_crosssectionalreversal.json").write_text(json.dumps(seed, indent=1))
        before = (live / "state_crosssectionalreversal.json").read_bytes()
        syms = ("US.AAPL", "US.MSFT")
        cfg = config_from_dict(tiny_config_dict(symbols=syms, mode="shadow",
                                                params={"lookback": 1, "top_n": 1, "rebalance_every": 1}))
        conn = ForbiddenConnector()
        start = pd.Timestamp("2026-10-14 13:35", tz="UTC").value
        node = TradingNode(cfg, clock=TestClock(start), registry=Registry(conn), records_tz="America/New_York",
                           components=standin_components())
        node.start()
        assert node.records_dir == live / "shadow"
        assert isinstance(node.ctx.execution_clients["acct"], ShadowExecutionClient)
        a = node.host.legacy[0]
        assert a.strategy._cross_section_count == 5                     # the live state, read
        spec = BarSpecification.from_str("1-HOUR")
        first = pd.Timestamp("2026-10-14 14:30", tz="UTC").value
        for i, (pa, pm) in enumerate([(100.0, 100.0), (90.0, 110.0), (99.0, 99.0)]):
            push = max(start, first + (i - 1) * 3600 * 10**9 + 10**9)
            for sym, px in zip(syms, (pa, pm)):
                bar = Bar(BarType(instrument_for_symbol(sym), spec), px, px, px, px, 1.0, first + i * 3600 * 10**9, push)
                node.queue.put(BarUpdate(bar, False, push))
            node.advance_to(push)
        shadow = node.ctx.execution_clients["acct"]
        assert [(i["side"], i["instrument_id"]) for i in shadow.intents] == [("BUY", "AAPL.XNAS")]
        assert a.portfolio.positions == {}                              # cancelled at once: nothing booked
        node.advance_to(pd.Timestamp("2026-10-14 20:02", tz="UTC").value)
        node.stop()
        assert (live / "state_crosssectionalreversal.json").read_bytes() == before      # the live state untouched
        assert (live / "shadow" / "state_crosssectionalreversal.json").exists()
        assert list(live.glob("session_*.jsonl")) == []                                 # the dashboard sees none
        assert len(list((live / "shadow").glob("session_*.jsonl"))) == 1
        assert list((live / "shadow").glob("shadow_*.jsonl"))
        assert conn.connected is False and node.persist_bars is False
    finally:
        paths.repo_root.cache_clear()
