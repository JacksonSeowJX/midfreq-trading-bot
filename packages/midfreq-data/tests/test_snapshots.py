"""
Broker snapshots (midfreq.data.snapshots) against fake venues: no OpenD, no exchange, no network.

The file is the API's AccountView plus published_by; each account is one connection and four
queries; an account that cannot be read leaves the previous file as it was; the write is atomic;
nothing from the account's options or secrets reaches the file.
"""
import json
import os
import socket

import pandas as pd
import pytest

from midfreq.core.paths import repo_root
from midfreq.data import snapshots
from midfreq.data.adapters.base import AccountUnavailableError
from midfreq.data.adapters.moomoo import account as moomoo_account

from account_fakes import (
    FakeAccountExchange, FakeOpenD, FakeTradeContext, ccxt_spot_market, fake_account_registry, moomoo_order,
)

NOW = pd.Timestamp("2026-10-15 20:00", tz="UTC")          # 16:00 in New York
NOW_NS = lambda: NOW.value                                 # noqa: E731

ACCINFO = {"total_assets": 1_000_250.5, "cash": 990_000.0, "market_val": 10_250.5, "currency": "USD"}
POSITIONS = [{"code": "US.AAPL", "qty": 10.0, "average_cost": 200.0, "nominal_price": 225.05,
              "market_val": 2250.5, "unrealized_pl": 250.5, "position_side": "LONG"},
             {"code": "US.MSFT", "qty": 20.0, "average_cost": 400.0, "nominal_price": "N/A",
              "market_val": 8000.0, "unrealized_pl": "N/A", "pl_val_valid": False, "position_side": "LONG"}]
TODAY = [moomoo_order("o5", "2026-10-15 15:30:00", "BUY", code="US.AAPL", updated_local="2026-10-15 15:30:17")]
HISTORY = [moomoo_order("o3", "2026-10-12 15:30:00", "SELL", code="US.KO"),
           moomoo_order("o1", "2026-09-25 15:30:00", "SELL")]           # older than 14 days

SECRET_ENV = {"MIDFREQ_TEST_SNAP_READ_API_KEY": "k-3f9a1c", "MIDFREQ_TEST_SNAP_READ_API_SECRET": "s-77e0b2",
              "MIDFREQ_TEST_SNAP_READ_API_PASSPHRASE": "p-c41d9e"}

CONNECTIONS = {"accounts": {
    "paper-us": {"label": "Paper (US)", "connector": "moomoo", "options": {"market": "US"}, "publish": True},
    "quiet": {"connector": "moomoo", "options": {"market": "US"}},
    "okx": {"label": "OKX", "connector": "ccxt", "options": {"exchange": "okx"}, "secrets": list(SECRET_ENV),
            "publish": True},
}}

VIEW_KEYS = {"id", "label", "connector", "as_of", "snapshot", "positions", "orders"}


@pytest.fixture
def venue():
    """The fake OpenD and trade context, a fake exchange, and a registry over them."""
    ctx = FakeTradeContext(ACCINFO, POSITIONS, TODAY, HISTORY)
    opend = FakeOpenD(ctx)
    ex = FakeAccountExchange({"USDT": 5000.0, "BTC": 0.1}, {"BTC/USDT": ccxt_spot_market("BTC", "USDT")},
                             {"BTC/USDT": {"symbol": "BTC/USDT", "last": 60000.0}})
    return ctx, opend, ex, fake_account_registry(opend, ex, now_ns=NOW_NS)


def _publish(venue, out_dir, account_id="paper-us", connections=CONNECTIONS):
    return snapshots.publish(account_id, out_dir, connections, registry=venue[3])


def test_the_file_is_the_accounts_view_as_the_dashboard_serves_it(venue, tmp_path):
    path = _publish(venue, tmp_path)
    assert path == tmp_path / "broker_paper-us.json"
    doc = json.loads(path.read_text())
    assert set(doc) == VIEW_KEYS | {"published_by"}
    assert (doc["id"], doc["label"], doc["connector"]) == ("paper-us", "Paper (US)", "moomoo")
    assert pd.Timestamp(doc["as_of"]) == NOW and doc["as_of"].endswith("+00:00")
    assert doc["published_by"] == socket.gethostname()
    assert doc["snapshot"] == {"currency": "USD", "total_assets": 1_000_250.5, "cash": 990_000.0,
                               "market_value": 10_250.5}
    aapl, msft = doc["positions"]
    assert aapl == {"symbol": "US.AAPL", "quantity": 10.0, "average_price": 200.0, "market_price": 225.05,
                    "market_value": 2250.5, "unrealized_pnl": 250.5, "instrument_id": "AAPL.XNAS"}
    assert msft["market_price"] is None and msft["unrealized_pnl"] is None
    # the last 14 days, newest first, times in UTC
    assert [o["order_id"] for o in doc["orders"]] == ["o5", "o3"]
    o5 = doc["orders"][0]
    assert (o5["side"], o5["status"], o5["quantity"], o5["filled_quantity"]) == ("BUY", "FILLED_ALL", 10.0, 10.0)
    assert pd.Timestamp(o5["created"]) == pd.Timestamp("2026-10-15 19:30", tz="UTC")
    assert pd.Timestamp(o5["updated"]) == pd.Timestamp("2026-10-15 19:30:17", tz="UTC")


def test_one_connection_four_queries_and_nothing_else(venue, tmp_path):
    ctx, opend, _, _ = venue
    _publish(venue, tmp_path)
    assert len(opend.opened) == 1 and ctx.closed == 1
    assert sorted(m for m, _ in ctx.calls) == sorted(moomoo_account.READ_METHODS)      # each once
    assert ctx.violations == []


def test_no_option_or_secret_reaches_the_file(venue, tmp_path, monkeypatch):
    for var, value in SECRET_ENV.items():
        monkeypatch.setenv(var, value)
    text = _publish(venue, tmp_path, "okx").read_text()
    doc = json.loads(text)
    assert set(doc) == VIEW_KEYS | {"published_by"}
    assert doc["snapshot"]["total_assets"] == pytest.approx(11_000.0)
    for word in [*SECRET_ENV, *SECRET_ENV.values(), "secrets", "options", "exchange"]:
        assert word not in text, word


def test_an_unavailable_account_leaves_the_previous_snapshot(tmp_path):
    previous = tmp_path / "broker_paper-us.json"
    previous.write_text('{"as_of": "2026-10-14T20:00:00+00:00"}\n')
    down = fake_account_registry(FakeOpenD(port_open=False), now_ns=NOW_NS)
    with pytest.raises(AccountUnavailableError, match="not reachable"):
        snapshots.publish("paper-us", tmp_path, CONNECTIONS, registry=down)
    assert previous.read_text() == '{"as_of": "2026-10-14T20:00:00+00:00"}\n'
    assert os.listdir(tmp_path) == ["broker_paper-us.json"]


def test_a_read_that_fails_half_way_writes_nothing(tmp_path):
    ctx = FakeTradeContext(ACCINFO, POSITIONS, TODAY, HISTORY, errors={"order_list_query": "busy, try again"})
    reg = fake_account_registry(FakeOpenD(ctx), now_ns=NOW_NS)
    with pytest.raises(AccountUnavailableError, match="busy"):
        snapshots.publish("paper-us", tmp_path, CONNECTIONS, registry=reg)
    assert os.listdir(tmp_path) == [] and ctx.closed == 1


def test_the_write_is_atomic(venue, tmp_path, monkeypatch):
    _publish(venue, tmp_path)
    before = (tmp_path / "broker_paper-us.json").read_text()

    def interrupted(src, dst):
        assert os.path.basename(src).startswith(".broker_paper-us.json.")   # hidden, not broker_*.json
        assert json.loads(open(src).read())["id"] == "paper-us"           # complete before the rename
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="disk full"):
        _publish(venue, tmp_path)
    assert (tmp_path / "broker_paper-us.json").read_text() == before
    assert os.listdir(tmp_path) == ["broker_paper-us.json"]                # no temporary file left


def test_the_folder_is_made_and_the_file_is_readable_like_the_record(venue, tmp_path):
    path = _publish(venue, tmp_path / "runtime" / "live_sessions")
    assert path.is_file() and (path.stat().st_mode & 0o777) == 0o644


def test_ids_that_are_not_file_names_are_refused(venue, tmp_path):
    for bad in ["../evil", "a/b", ".hidden", ""]:
        with pytest.raises(ValueError, match="cannot be a file name"):
            snapshots.snapshot_name(bad)
    conns = {"accounts": {"../evil": {"connector": "moomoo", "options": {"market": "US"}, "publish": True}}}
    with pytest.raises(ValueError):
        _publish(venue, tmp_path / "out", "../evil", conns)
    assert not (tmp_path / "evil").exists() and not (tmp_path / "out").exists()


def test_published_ids_are_the_accounts_with_publish_true(tmp_path):
    assert snapshots.published_ids(CONNECTIONS) == ["paper-us", "okx"]
    assert snapshots.published_ids({}) == []
    assert snapshots.published_ids(repo_root() / "config" / "connections.yaml") == ["moomoo-us", "moomoo-hk"]


# ── the command line ─────────────────────────────────────────────────

def test_the_command_publishes_every_publish_account_quietly(venue, tmp_path, monkeypatch, capsys):
    for var, value in SECRET_ENV.items():
        monkeypatch.setenv(var, value)
    assert snapshots.main(["--out-dir", str(tmp_path)], CONNECTIONS, venue[3]) == 0
    assert sorted(os.listdir(tmp_path)) == ["broker_okx.json", "broker_paper-us.json"]
    assert capsys.readouterr() == ("", "")


def test_the_command_publishes_named_accounts_whatever_their_flag(venue, tmp_path):
    assert snapshots.main(["quiet", "--out-dir", str(tmp_path)], CONNECTIONS, venue[3]) == 0
    assert os.listdir(tmp_path) == ["broker_quiet.json"]


def test_a_failure_is_one_line_with_the_reason_and_the_others_still_publish(venue, tmp_path, monkeypatch, capsys):
    for var in SECRET_ENV:
        monkeypatch.delenv(var, raising=False)                  # okx has no key: unavailable
    code = snapshots.main(["okx", "nope", "paper-us", "--out-dir", str(tmp_path)], CONNECTIONS, venue[3])
    assert code == 1
    assert os.listdir(tmp_path) == ["broker_paper-us.json"]
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 2
    assert err[0].startswith("broker snapshot okx: missing MIDFREQ_TEST_SNAP_READ_API_KEY")
    assert err[1].startswith("broker snapshot nope: unknown account 'nope'")
    assert all("previous snapshot" in line for line in err)


def test_nothing_to_publish_is_a_quiet_success(tmp_path, capsys):
    conns = {"accounts": {"a": {"connector": "moomoo", "options": {"market": "US"}}}}
    assert snapshots.main(["--out-dir", str(tmp_path / "out")], conns) == 0
    assert not (tmp_path / "out").exists() and capsys.readouterr() == ("", "")


def test_a_malformed_accounts_section_is_reported(tmp_path, capsys):
    assert snapshots.main(["--out-dir", str(tmp_path)], {"accounts": ["not", "a", "mapping"]}) == 1
    assert "must be a mapping" in capsys.readouterr().err
