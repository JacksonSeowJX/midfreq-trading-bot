"""Broker accounts through an AccountProvider, with fake AccountClients (no OpenD, no network),
and the snapshots the VM publishes into the record (broker_<id>.json)."""
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from midfreq.api.account_provider import AccountEntry, AccountProvider, ConfiguredAccounts
from midfreq.api.deps import get_account_provider
from midfreq.api.settings import get_settings
from midfreq.data.adapters.base import (
    AccountClient, AccountSnapshot, AccountUnavailableError, OrderReport, PositionReport,
)

T = pd.Timestamp("2026-10-15 20:00:00", tz="UTC")
PUBLISHED = T - pd.Timedelta(days=3)          # a stale snapshot: the VM last read the account three days ago


class FakeClient(AccountClient):
    def __init__(self, available=(True, ""), hang: threading.Event = None, fail: Exception = None):
        self._available, self._hang, self._fail = available, hang, fail
        self.closed = False

    def available(self):
        if self._hang is not None:
            self._hang.wait(10)
        return self._available

    def snapshot(self):
        if self._fail is not None:
            raise self._fail
        return AccountSnapshot(currency="USD", total_assets=1_003_800.96, cash=905_029.34,
                               market_value=98_771.63, ts=T)

    def positions(self):
        return [PositionReport(symbol="US.CAT", quantity=43.0, average_price=774.24, market_price=780.0,
                               market_value=33_540.0, unrealized_pnl=247.68, instrument_id="CAT.XNYS"),
                PositionReport(symbol="US.INTC", quantity=302.0, average_price=109.585, market_price=None,
                               market_value=None, unrealized_pnl=None),
                PositionReport(symbol="BTC", quantity=0.5, average_price=None, market_price=None,
                               market_value=None, unrealized_pnl=None)]          # a spot balance: no cost basis

    def orders(self, days=14):
        self.days = days
        return [OrderReport(order_id="3115473", symbol="US.QCOM", side="SELL", quantity=179.0,
                            filled_quantity=179.0, price=None, average_fill_price=176.815,
                            status="FILLED_ALL", created=T - pd.Timedelta(days=2), updated=T)]

    def close(self):
        self.closed = True


class FakeAccounts(AccountProvider):
    def __init__(self, clients):
        self.clients, self.made = clients, []

    def accounts(self):
        return [AccountEntry(id=k, label=f"Label {k}", connector="fake") for k in self.clients]

    def client(self, account_id):
        make = self.clients[account_id]
        c = make()
        self.made.append(c)
        return c


@pytest.fixture
def release():
    """Lets hanging fake clients finish once the test is over."""
    ev = threading.Event()
    yield ev
    ev.set()


@pytest.fixture
def accounts(app, release):
    def no_client():
        raise AccountUnavailableError("account clients are not installed")
    fake = FakeAccounts({
        "good": FakeClient,
        "down": lambda: FakeClient(available=(False, "OpenD is not running on 127.0.0.1:11111")),
        "hangs": lambda: FakeClient(hang=release),
        "nofactory": no_client,
        "breaks": lambda: FakeClient(fail=AccountUnavailableError("gateway dropped the session")),
    })
    app.dependency_overrides[get_account_provider] = lambda: fake
    return fake


def test_accounts_are_listed_with_a_probe_that_never_hangs(client, accounts):
    t0 = time.monotonic()
    rows = client.get("/api/accounts").json()
    assert time.monotonic() - t0 < 3                       # probe_timeout is 0.5 s in the fixture
    by_id = {r["id"]: r for r in rows}
    assert [r["id"] for r in rows] == ["good", "down", "hangs", "nofactory", "breaks"]
    assert by_id["good"] == {"id": "good", "label": "Label good", "connector": "fake", "available": True, "reason": "", "kind": "live"}
    assert by_id["down"]["available"] is False and "OpenD" in by_id["down"]["reason"]
    assert by_id["hangs"]["available"] is False and "no answer within" in by_id["hangs"]["reason"]
    assert by_id["nofactory"]["available"] is False and "not installed" in by_id["nofactory"]["reason"]
    assert all(c.closed for c in accounts.made if not c._hang)


def test_an_account_view_maps_the_clients_reports(client, accounts):
    r = client.get("/api/accounts/good", params={"order_days": 30})
    assert r.status_code == 200
    v = r.json()
    assert (v["id"], v["label"], v["connector"]) == ("good", "Label good", "fake")
    assert pd.Timestamp(v["as_of"]) == T
    assert v["snapshot"] == {"currency": "USD", "total_assets": 1_003_800.96, "cash": 905_029.34,
                             "market_value": 98_771.63}
    assert v["positions"][0]["instrument_id"] == "CAT.XNYS" and v["positions"][1]["market_price"] is None
    assert v["positions"][2]["average_price"] is None
    order = v["orders"][0]
    assert (order["order_id"], order["side"], order["status"], order["price"]) == ("3115473", "SELL", "FILLED_ALL", None)
    assert pd.Timestamp(order["created"]) == T - pd.Timedelta(days=2)
    made = accounts.made[-1]
    assert made.days == 30 and made.closed


def test_unknown_account_is_404(client, accounts):
    assert client.get("/api/accounts/nope").status_code == 404


def test_an_id_the_factory_does_not_know_is_404(client, app):
    def unknown():
        raise KeyError("unknown account 'gone'")
    app.dependency_overrides[get_account_provider] = lambda: FakeAccounts({"gone": unknown})
    assert client.get("/api/accounts/gone").status_code == 404


@pytest.mark.parametrize("account_id", ["down", "nofactory", "breaks"])
def test_an_account_that_cannot_be_read_is_503_with_the_reason(client, accounts, account_id):
    r = client.get(f"/api/accounts/{account_id}")
    assert r.status_code == 503 and r.json()["detail"]


def test_an_account_that_never_answers_is_503_not_a_hang(client, accounts):
    t0 = time.monotonic()
    r = client.get("/api/accounts/hangs")
    assert r.status_code == 503 and "no answer within" in r.json()["detail"]
    assert time.monotonic() - t0 < 3


def test_order_days_is_bounded(client, accounts):
    assert client.get("/api/accounts/good", params={"order_days": 0}).status_code == 422
    assert client.get("/api/accounts/good", params={"order_days": 91}).status_code == 422


# ── the default provider ────────────────────────────────────────────────────

def test_the_default_provider_lists_the_accounts_section_of_connections_yaml(repo):
    entries = ConfiguredAccounts(repo / "config" / "connections.yaml").accounts()
    assert entries == [AccountEntry("moomoo-us", "Moomoo paper (US)", "moomoo"), AccountEntry("okx", "okx", "ccxt")]
    assert ConfiguredAccounts(repo / "config" / "missing.yaml").accounts() == []


def test_without_the_connectors_factory_accounts_are_listed_as_unavailable(client, monkeypatch):
    # The real provider, with the factory module made unimportable: no broker is contacted.
    monkeypatch.setitem(sys.modules, "midfreq.data.adapters.accounts", None)
    rows = client.get("/api/accounts").json()
    assert [r["id"] for r in rows] == ["moomoo-us", "okx"]
    assert all(not r["available"] and "not installed" in r["reason"] for r in rows)
    r = client.get("/api/accounts/moomoo-us")
    assert r.status_code == 503 and "not installed" in r.json()["detail"]
    assert client.get("/api/accounts/nope").status_code == 404


def test_accounts_can_be_switched_off_for_a_deployment_without_a_gateway(monkeypatch):
    from midfreq.api.settings import default_settings
    monkeypatch.setenv("MIDFREQ_DASHBOARD_ACCOUNTS", "off")
    monkeypatch.setenv("MIDFREQ_LIVE_SESSIONS", "gs://b/live_sessions")
    s = default_settings()
    assert s.accounts_enabled is False and s.live_sessions == "gs://b/live_sessions"
    from midfreq.api.account_provider import NoAccounts
    from midfreq.api.deps import get_account_provider
    assert isinstance(get_account_provider(s), NoAccounts)
    assert get_account_provider(s).accounts() == []


@pytest.mark.parametrize("value, enabled, snapshots_only", [("on", True, False), ("snapshots", True, True),
                                                           ("off", False, False)])
def test_the_accounts_mode_comes_from_the_environment(monkeypatch, value, enabled, snapshots_only):
    from midfreq.api.settings import default_settings
    monkeypatch.setenv("MIDFREQ_DASHBOARD_ACCOUNTS", value)
    s = default_settings()
    assert (s.accounts_enabled, s.snapshot_accounts_only) == (enabled, snapshots_only)
    assert isinstance(get_account_provider(s), ConfiguredAccounts) is enabled


# ── published snapshots (broker_<id>.json in the record) ────────────────────

def snapshot_doc(account_id, as_of=PUBLISHED):
    """A snapshot as midfreq.data.snapshots writes it."""
    return {"id": account_id, "label": "the VM's label", "connector": "moomoo", "as_of": as_of.isoformat(),
            "published_by": "trading-bot",
            "snapshot": {"currency": "USD", "total_assets": 1_000_100.0, "cash": 900_000.0, "market_value": 100_100.0},
            "positions": [{"symbol": "US.CAT", "quantity": 43.0, "average_price": 774.24, "market_price": 780.0,
                           "market_value": 33_540.0, "unrealized_pnl": 247.68, "instrument_id": "CAT.XNYS"}],
            "orders": [{"order_id": "o2", "symbol": "US.CAT", "side": "BUY", "quantity": 43.0, "filled_quantity": 43.0,
                        "price": None, "average_fill_price": 774.24, "status": "FILLED_ALL",
                        "created": (as_of - pd.Timedelta(hours=1)).isoformat(), "updated": as_of.isoformat()},
                       {"order_id": "o1", "symbol": "US.QCOM", "side": "SELL", "quantity": 179.0, "filled_quantity": 0.0,
                        "price": 176.0, "average_fill_price": None, "status": "CANCELLED_ALL",
                        "created": (as_of - pd.Timedelta(days=10)).isoformat(), "updated": None}]}


@pytest.fixture
def publish(settings):
    """Writes broker_<id>.json into the record, as the VM's publisher does."""
    def write(account_id, text=None):
        (Path(settings.live_sessions) / f"broker_{account_id}.json").write_text(
            json.dumps(snapshot_doc(account_id)) if text is None else text)
    return write


@pytest.fixture
def hosted(app, settings):
    """The hosted dashboard: MIDFREQ_DASHBOARD_ACCOUNTS=snapshots."""
    app.dependency_overrides[get_settings] = lambda: replace(settings, snapshot_accounts_only=True)


def test_hosted_lists_only_accounts_with_a_snapshot_and_never_probes(client, accounts, hosted, publish):
    publish("hangs")
    publish("down")
    rows = client.get("/api/accounts").json()
    assert rows == [{"id": "down", "label": "Label down", "connector": "fake", "available": True, "reason": "",
                     "kind": "snapshot"},
                    {"id": "hangs", "label": "Label hangs", "connector": "fake", "available": True, "reason": "",
                     "kind": "snapshot"}]                              # configuration order
    assert accounts.made == []                                        # no client, so no gateway was asked


def test_hosted_serves_the_snapshot_as_published_however_old(client, accounts, hosted, publish):
    publish("down")
    r = client.get("/api/accounts/down")
    assert r.status_code == 200
    v = r.json()
    assert pd.Timestamp(v["as_of"]) == PUBLISHED                       # its age is the web app's to show
    assert (v["id"], v["label"], v["connector"]) == ("down", "Label down", "fake")      # names: the configuration
    assert v["snapshot"] == {"currency": "USD", "total_assets": 1_000_100.0, "cash": 900_000.0,
                             "market_value": 100_100.0}
    assert v["positions"][0]["instrument_id"] == "CAT.XNYS"
    assert [(o["order_id"], o["price"], o["average_fill_price"]) for o in v["orders"]] == \
        [("o2", None, 774.24), ("o1", 176.0, None)]
    assert pd.Timestamp(v["orders"][0]["created"]) == PUBLISHED - pd.Timedelta(hours=1)
    # order_days counts back from the publish time
    week = client.get("/api/accounts/down", params={"order_days": 7}).json()
    assert [o["order_id"] for o in week["orders"]] == ["o2"]
    assert accounts.made == []


def test_hosted_has_no_view_of_an_account_without_a_snapshot(client, accounts, hosted, publish):
    publish("down")
    assert client.get("/api/accounts/good").status_code == 404        # configured, never published
    assert client.get("/api/accounts/nope").status_code == 404
    publish("unconfigured")
    assert client.get("/api/accounts/unconfigured").status_code == 404
    assert [r["id"] for r in client.get("/api/accounts").json()] == ["down"]
    assert accounts.made == []


MALFORMED = {
    "not JSON": ('{"id": "down", "as_of": ', "cannot be read"),
    "a list": ("[]", "not an account snapshot"),
    "no totals": (json.dumps({**snapshot_doc("down"), "snapshot": {"currency": "USD"}}), "snapshot.total_assets"),
    "a bad time": (json.dumps({**snapshot_doc("down"), "as_of": "yesterday"}), "as_of"),
    "NaN": (json.dumps(snapshot_doc("down")).replace("1000100.0", "NaN"), "NaN"),
    "another account": (json.dumps(snapshot_doc("good")), "holds account 'good'"),
}


@pytest.mark.parametrize("text, why", MALFORMED.values(), ids=MALFORMED.keys())
def test_hosted_a_malformed_snapshot_is_503_with_the_reason(client, accounts, hosted, publish, text, why):
    publish("down", text=text)
    r = client.get("/api/accounts/down")
    assert r.status_code == 503
    assert "broker_down.json" in r.json()["detail"] and why in r.json()["detail"]
    (row,) = client.get("/api/accounts").json()
    assert (row["id"], row["kind"], row["available"]) == ("down", "snapshot", False) and why in row["reason"]


def test_a_readable_account_is_live_even_with_a_snapshot(client, accounts, publish):
    publish("good")
    row = next(r for r in client.get("/api/accounts").json() if r["id"] == "good")
    assert (row["kind"], row["available"], row["reason"]) == ("live", True, "")
    v = client.get("/api/accounts/good").json()
    assert pd.Timestamp(v["as_of"]) == T and v["snapshot"]["total_assets"] == 1_003_800.96     # read now


def test_where_the_gateway_cannot_be_read_the_snapshot_is_listed(client, accounts, publish):
    for account_id in ("down", "hangs", "nofactory", "breaks"):
        publish(account_id)
    by_id = {r["id"]: r for r in client.get("/api/accounts").json()}
    assert list(by_id) == ["good", "down", "hangs", "nofactory", "breaks"]
    assert by_id["good"]["kind"] == "live"
    for account_id, why in (("down", "OpenD"), ("hangs", "no answer within"), ("nofactory", "not installed")):
        row = by_id[account_id]
        assert (row["kind"], row["available"]) == ("snapshot", True) and why in row["reason"], row
    assert (by_id["breaks"]["kind"], by_id["breaks"]["available"]) == ("live", True)   # its probe answers


@pytest.mark.parametrize("account_id", ["down", "hangs", "nofactory", "breaks"])
def test_an_account_that_cannot_be_read_is_served_from_its_snapshot(client, accounts, publish, account_id):
    publish(account_id)
    r = client.get(f"/api/accounts/{account_id}")
    assert r.status_code == 200
    v = r.json()
    assert pd.Timestamp(v["as_of"]) == PUBLISHED and (v["id"], v["label"]) == (account_id, f"Label {account_id}")


def test_an_unreadable_gateway_with_a_malformed_snapshot_is_503_with_both_reasons(client, accounts, publish):
    publish("down", text="{")
    r = client.get("/api/accounts/down")
    assert r.status_code == 503
    assert "OpenD" in r.json()["detail"] and "broker_down.json cannot be read" in r.json()["detail"]
    row = next(r for r in client.get("/api/accounts").json() if r["id"] == "down")
    assert (row["kind"], row["available"]) == ("snapshot", False)
    assert "OpenD" in row["reason"] and "broker_down.json" in row["reason"]


def test_accounts_switched_off_show_no_snapshot_either(client, app, settings, publish):
    publish("moomoo-us")
    app.dependency_overrides[get_settings] = lambda: replace(settings, accounts_enabled=False,
                                                             snapshot_accounts_only=True)
    assert client.get("/api/accounts").json() == []
    assert client.get("/api/accounts/moomoo-us").status_code == 404


def test_the_publishers_file_is_served_as_the_accounts_view(client, settings, repo, hosted, monkeypatch):
    """midfreq.data.snapshots, against a fake OpenD, into the record; then the hosted dashboard
    with the configured accounts (conftest's connections.yaml)."""
    from account_fakes import FakeOpenD, FakeTradeContext, fake_account_registry, moomoo_order
    from midfreq.data import snapshots

    def no_gateway(self, account_id):
        raise AssertionError("the hosted dashboard asked a gateway")
    monkeypatch.setattr(ConfiguredAccounts, "client", no_gateway)
    ctx = FakeTradeContext({"total_assets": 1_000_250.5, "cash": 990_000.0, "market_val": 10_250.5, "currency": "USD"},
                           [{"code": "US.AAPL", "qty": 10.0, "average_cost": 200.0, "nominal_price": 225.05,
                             "market_val": 2250.5, "unrealized_pl": 250.5}],
                           [moomoo_order("o5", "2026-10-15 15:30:00")], [])
    snapshots.publish("moomoo-us", settings.live_sessions, repo / "config" / "connections.yaml",
                      registry=fake_account_registry(FakeOpenD(ctx), now_ns=lambda: T.value))

    assert client.get("/api/accounts").json() == [{"id": "moomoo-us", "label": "Moomoo paper (US)", "connector": "moomoo",
                                                   "available": True, "reason": "", "kind": "snapshot"}]
    v = client.get("/api/accounts/moomoo-us").json()
    assert pd.Timestamp(v["as_of"]) == T
    assert v["snapshot"] == {"currency": "USD", "total_assets": 1_000_250.5, "cash": 990_000.0, "market_value": 10_250.5}
    assert v["positions"] == [{"symbol": "US.AAPL", "quantity": 10.0, "average_price": 200.0, "market_price": 225.05,
                               "market_value": 2250.5, "unrealized_pnl": 250.5, "instrument_id": "AAPL.XNAS"}]
    (order,) = v["orders"]
    assert (order["order_id"], pd.Timestamp(order["created"])) == ("o5", pd.Timestamp("2026-10-15 19:30", tz="UTC"))
