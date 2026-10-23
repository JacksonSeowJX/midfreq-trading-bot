"""A broker's position list can lag its fills (the Hong Kong paper account still listed 0 shares a
second after a 400-share fill was reported, 23 Oct 2026). The periodic shrink-only sync must not
erase a fill that recent; once the settle window has passed, a real difference is still taken."""
from __future__ import annotations

import logging

import pandas as pd

from _node_testkit import FakeDataClient, ScriptedVenue, standin_components, tiny_config_dict
from midfreq.core.common.clock import TestClock
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.data.adapters.base import BarUpdate
from midfreq.trading.config.settings import config_from_dict, instrument_for_symbol
from midfreq.trading.node.kernel import TradingNode

SPEC = BarSpecification.from_str("1-HOUR")
START = pd.Timestamp("2026-10-14 13:35", tz="UTC").value
FIRST_BAR = pd.Timestamp("2026-10-14 14:30", tz="UTC").value
HOUR, SEC = 3600 * 10**9, 10**9
SYMS = ("US.AAPL", "US.MSFT", "US.NVDA", "US.AMD")
ACCOUNT = {"cash": 1_000_000.0, "total_assets": 1_000_000.0}


def _node(tmp_path):
    venue = ScriptedVenue()
    venue.auto_fill_px = {}
    cfg = config_from_dict(tiny_config_dict(symbols=SYMS, params={"lookback": 1, "top_n": 1, "rebalance_every": 1}))
    node = TradingNode(cfg, mode="sim", clock=TestClock(START), records_dir=tmp_path / "rec",
                       store_dir=tmp_path / "data", records_tz="America/New_York",
                       components=standin_components(**{"execution_client:acct": venue,
                                                        "data_client": FakeDataClient()}))
    return node, venue


def _push(node, i, closes):
    ts = FIRST_BAR + i * HOUR
    when = max(START, ts - HOUR + SEC)
    for sym, c in zip(SYMS, closes):
        node.queue.put(BarUpdate(Bar(BarType(instrument_for_symbol(sym), SPEC), c, c, c, c, 1.0, ts, when), False, when))
    node.advance_to(when)


def _bought(tmp_path):
    node, venue = _node(tmp_path)
    node.start()
    _push(node, 0, (100, 100, 100, 100))
    _push(node, 1, (90, 100, 100, 100))
    _push(node, 2, (95, 95, 95, 95))
    a = node.host.legacy[0]
    assert [str(o.instrument_id) for o in venue.submitted] == ["AAPL.XNAS"]
    held = a.portfolio.get_position_qty("US.AAPL")
    assert held > 0
    return node, a, held


def test_a_fill_the_broker_does_not_show_yet_is_kept(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    node, a, held = _bought(tmp_path)
    assert a.reconcile(ACCOUNT, {})            # the broker lists nothing, a second after the fill
    assert a.portfolio.get_position_qty("US.AAPL") == held
    assert "does not show it yet" in caplog.text and "shrinking claim" not in caplog.text


def test_after_the_settle_window_a_real_difference_is_taken(tmp_path, caplog):
    node, a, held = _bought(tmp_path)
    node.advance_to(node.now_ns() + int(a.fill_settle_s + 1) * SEC)
    assert a.reconcile(ACCOUNT, {})
    assert a.portfolio.get_position_qty("US.AAPL") == 0
    assert "shrinking claim" in caplog.text


def test_the_broker_showing_the_fill_needs_no_wait(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    node, a, held = _bought(tmp_path)
    a.reconcile(ACCOUNT, {"US.AAPL": {"qty": held}})
    assert a.portfolio.get_position_qty("US.AAPL") == held
    assert "does not show it yet" not in caplog.text
