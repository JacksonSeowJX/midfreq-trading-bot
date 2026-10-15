"""Broker accounts through an AccountProvider, with fake AccountClients (no OpenD, no network)."""
import sys
import threading
import time

import pandas as pd
import pytest

from midfreq.api.account_provider import AccountEntry, AccountProvider, ConfiguredAccounts
from midfreq.api.deps import get_account_provider
from midfreq.data.adapters.base import (
    AccountClient, AccountSnapshot, AccountUnavailableError, OrderReport, PositionReport,
)

T = pd.Timestamp("2026-10-15 20:00:00", tz="UTC")


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
