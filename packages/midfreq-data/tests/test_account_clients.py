"""
The AccountClient contract: every account client must pass these, each against a fake transport
that speaks its source's native format (no network, no OpenD, no exchange).

1. available() is (True, '') when the venue can be read;
2. snapshot() reports currency, total assets, cash and market value, stamped now in UTC;
3. positions() maps the venue's holdings (symbol, quantity, prices, value, P&L, instrument id);
4. orders(days) is newest first, only orders created in the last `days` days, live and history
   merged with the final state kept;
5. an unreachable venue (OpenD down, no API key) is reported by available() and raises
   AccountUnavailableError from every read, quickly;
6. nothing but read methods is ever touched on the venue's SDK (the fakes raise otherwise).

Then each client's own behaviour, and the `accounts` factory against a temporary connections.yaml.
"""
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import pytest

from midfreq.data.adapters import accounts
from midfreq.data.adapters.base import (
    AccountSnapshot, AccountUnavailableError, ConnectorError, OrderReport, PositionReport,
)
from midfreq.data.adapters.ccxt import account as ccxt_account
from midfreq.data.adapters.ccxt.account import CcxtAccountClient, credential_field
from midfreq.data.adapters.ccxt.connector import CcxtConnector
from midfreq.data.adapters.moomoo import account as moomoo_account
from midfreq.data.adapters.moomoo.account import MoomooAccountClient, OpenDTransport
from midfreq.data.adapters.moomoo.connector import MoomooConnector
from midfreq.data.adapters.registry import ConnectorRegistry
from midfreq.core.paths import repo_root

from account_fakes import (
    FakeAccountExchange, FakeOpenD, FakeTradeContext, ccxt_order, ccxt_spot_market, moomoo_order,
)

NOW = pd.Timestamp("2026-10-15 20:00", tz="UTC")          # 16:00 in New York, 04:00 next day in Hong Kong
NOW_NS = lambda: NOW.value                                 # noqa: E731
QUICK = 2.0                                                # seconds an unavailable venue may take to say so
OPEND = {"host": "127.0.0.1", "port": 11111}


# ── the venues' accounts, in their native formats ──────────────────

US_ACCINFO = {"total_assets": 1_000_250.5, "cash": 990_000.0, "market_val": 10_250.5, "currency": "USD"}
US_POSITIONS = [
    {"code": "US.AAPL", "qty": 10.0, "average_cost": 200.0, "cost_price": 199.0, "nominal_price": 225.05,
     "market_val": 2250.5, "unrealized_pl": 250.5, "position_side": "LONG"},
    {"code": "US.MSFT", "qty": 20.0, "average_cost": 400.0, "nominal_price": 400.0, "market_val": 8000.0,
     "unrealized_pl": "N/A", "pl_val": 0.0, "pl_val_valid": True, "position_side": "LONG"},
    {"code": "US.KO", "qty": 0.0, "average_cost": 0.0, "nominal_price": 70.0, "market_val": 0.0,   # sold today
     "unrealized_pl": 0.0, "position_side": "LONG"},
]
US_TODAY = [   # order_list_query: today's orders, and o4 (placed yesterday) now filled
    moomoo_order("o5", "2026-10-15 15:30:00", "BUY", updated_local="2026-10-15 15:30:17"),
    moomoo_order("o4", "2026-10-14 15:30:05", "BUY", status="FILLED_ALL"),
]
US_HISTORY = [   # history_order_list_query, by local create date
    moomoo_order("o4", "2026-10-14 15:30:05", "BUY", dealt_qty=0.0, dealt_avg=0.0, status="SUBMITTED"),
    moomoo_order("o3", "2026-10-12 15:30:00", "SELL"),
    moomoo_order("o2", "2026-10-05 15:30:00", "BUY"),
    moomoo_order("o0", "2026-10-01 10:00:00", "BUY"),      # the window's first date, but before its first hour
    moomoo_order("o1", "2026-09-25 15:30:00", "SELL"),
]

OKX_ENV = {"MIDFREQ_TEST_OKX_READ_API_KEY": "key", "MIDFREQ_TEST_OKX_READ_API_SECRET": "secret",
           "MIDFREQ_TEST_OKX_READ_API_PASSPHRASE": "phrase"}
OKX_TOTALS = {"USDT": 5000.0, "BTC": 0.1, "ETH": 2.0, "XYZ": 100.0, "DOGE": 0.0}
OKX_MARKETS = {"BTC/USDT": ccxt_spot_market("BTC", "USDT"), "ETH/USDT": ccxt_spot_market("ETH", "USDT"),
               "BTC/USDT:USDT": {"id": "BTC-USDT-SWAP", "symbol": "BTC/USDT:USDT", "spot": False, "swap": True}}
OKX_TICKERS = {"BTC/USDT": {"symbol": "BTC/USDT", "last": 60000.0}, "ETH/USDT": {"symbol": "ETH/USDT", "last": 2500.0}}
OKX_POSITIONS = [
    {"symbol": "BTC/USDT:USDT", "contracts": 2.0, "contractSize": 0.01, "side": "short", "entryPrice": 61000.0,
     "markPrice": 60000.0, "notional": 1200.0, "unrealizedPnl": 20.0},
    {"symbol": "ETH/USDT:USDT", "contracts": 0.0, "contractSize": 0.1, "side": "long"},      # flat
]
HOUR, DAY = pd.Timedelta(hours=1), pd.Timedelta(days=1)


def _okx_open():
    return [ccxt_order("c5", NOW - HOUR, filled=0.0, average=None, status="open"),
            ccxt_order("c4", NOW - DAY, filled=0.0, average=None, status="open"),
            ccxt_order("c1", NOW - 20 * DAY, filled=0.0, average=None, status="open")]   # resting for weeks


def _okx_closed():
    return [ccxt_order("c4", NOW - DAY, status="closed"),
            ccxt_order("c3", NOW - 3 * DAY, side="sell", filled=0.0, average=None, status="canceled"),
            ccxt_order("c2", NOW - 10 * DAY),
            ccxt_order("c0", NOW - 15 * DAY)]


def _set_env(monkeypatch, env: Dict[str, str], present: bool = True):
    for var, value in env.items():
        if present:
            monkeypatch.setenv(var, value)
        else:
            monkeypatch.delenv(var, raising=False)


def _moomoo(monkeypatch, reachable: bool = True, **ctx_kwargs):
    ctx = FakeTradeContext(US_ACCINFO, US_POSITIONS, US_TODAY, US_HISTORY, **ctx_kwargs)
    conn = MoomooConnector(OPEND, trade_transport=FakeOpenD(ctx, port_open=reachable), now_ns=NOW_NS)
    return conn.account_client(market="US"), ctx


def _okx(monkeypatch, reachable: bool = True, **ex_kwargs):
    _set_env(monkeypatch, OKX_ENV, present=reachable)
    ex = FakeAccountExchange(OKX_TOTALS, OKX_MARKETS, OKX_TICKERS, OKX_POSITIONS, _okx_open(), _okx_closed(),
                             **ex_kwargs)
    conn = CcxtConnector(account_exchange_factory=ex.factory, now_ns=NOW_NS)
    return conn.account_client(exchange="okx", secrets=list(OKX_ENV)), ex


@dataclass
class Case:
    id: str
    build: Callable                                    # (monkeypatch, reachable) -> (client, sdk fake)
    read_methods: Tuple[str, ...]
    snapshot: Tuple[str, float, float, float]          # currency, total_assets, cash, market_value
    positions: Dict[str, tuple]                        # symbol -> quantity, average, price, value, pnl, instrument
    orders_14: List[str]
    orders_2: List[str]
    newest_created: pd.Timestamp
    merged: Tuple[str, str]                            # an order in both lists -> the status kept


CASES = [
    Case("moomoo-US", _moomoo, moomoo_account.READ_METHODS,
         ("USD", 1_000_250.5, 990_000.0, 10_250.5),
         {"US.AAPL": (10.0, 200.0, 225.05, 2250.5, 250.5, "AAPL.XNAS"),
          "US.MSFT": (20.0, 400.0, 400.0, 8000.0, 0.0, "MSFT.XNAS")},
         ["o5", "o4", "o3", "o2"], ["o5", "o4"], pd.Timestamp("2026-10-15 19:30", tz="UTC"),
         ("o4", "FILLED_ALL")),
    Case("ccxt-okx", _okx, ccxt_account.READ_METHODS,
         ("USDT", 16_000.0, 5000.0, 11_000.0),
         {"BTC/USDT": (0.1, None, 60000.0, 6000.0, None, "BTC-USDT.OKX"),
          "ETH/USDT": (2.0, None, 2500.0, 5000.0, None, "ETH-USDT.OKX"),
          "XYZ": (100.0, None, None, None, None, None),
          "BTC/USDT:USDT": (-0.02, 61000.0, 60000.0, -1200.0, 20.0, None)},
         ["c5", "c4", "c3", "c2"], ["c5", "c4"], NOW - HOUR,
         ("c4", "closed")),
]
PARAMS = [pytest.param(c, id=c.id) for c in CASES]


# ── the contract ─────────────────────────────────────────────────────

@pytest.mark.parametrize("case", PARAMS)
def test_available_when_reachable(case, monkeypatch):
    client, _ = case.build(monkeypatch)
    assert client.available() == (True, "")


@pytest.mark.parametrize("case", PARAMS)
def test_snapshot(case, monkeypatch):
    client, _ = case.build(monkeypatch)
    snap = client.snapshot()
    assert isinstance(snap, AccountSnapshot)
    currency, total, cash, value = case.snapshot
    assert snap.currency == currency
    assert (snap.total_assets, snap.cash, snap.market_value) == pytest.approx((total, cash, value))
    assert snap.ts == NOW and str(snap.ts.tz) == "UTC"


@pytest.mark.parametrize("case", PARAMS)
def test_positions(case, monkeypatch):
    client, _ = case.build(monkeypatch)
    positions = client.positions()
    assert all(isinstance(p, PositionReport) for p in positions)
    got = {p.symbol: p for p in positions}
    assert sorted(got) == sorted(case.positions), "missing, extra or flat positions"
    for symbol, (qty, avg, price, value, pnl, iid) in case.positions.items():
        p = got[symbol]
        assert p.quantity == pytest.approx(qty), symbol
        assert (p.average_price is None) if avg is None else p.average_price == pytest.approx(avg), symbol
        for field, want in (("market_price", price), ("market_value", value), ("unrealized_pnl", pnl)):
            have = getattr(p, field)
            assert (have is None) if want is None else have == pytest.approx(want), (symbol, field)
        assert p.instrument_id == iid, symbol


@pytest.mark.parametrize("case", PARAMS)
def test_orders_newest_first_within_days(case, monkeypatch):
    client, _ = case.build(monkeypatch)
    orders = client.orders(days=14)
    assert all(isinstance(o, OrderReport) for o in orders)
    assert [o.order_id for o in orders] == case.orders_14
    assert [o.order_id for o in client.orders(days=2)] == case.orders_2
    assert orders[0].created == case.newest_created
    for o in orders:
        assert str(o.created.tz) == "UTC" and NOW - pd.Timedelta(days=14) <= o.created <= NOW
        assert o.side in ("BUY", "SELL")
        assert o.updated is None or str(o.updated.tz) == "UTC"
    merged_id, status = case.merged
    assert {o.order_id: o.status for o in orders}[merged_id] == status


@pytest.mark.parametrize("case", PARAMS)
def test_unavailable_venue_is_reported_quickly(case, monkeypatch):
    client, fake = case.build(monkeypatch, reachable=False)
    start = time.monotonic()
    ok, reason = client.available()
    assert not ok and reason
    for read in (client.snapshot, client.positions, client.orders):
        with pytest.raises(AccountUnavailableError):
            read()
    assert time.monotonic() - start < QUICK
    assert not fake.calls, "the venue was queried although it is unreachable"


@pytest.mark.parametrize("case", PARAMS)
def test_only_read_methods_are_touched(case, monkeypatch):
    client, fake = case.build(monkeypatch)
    client.available()
    client.snapshot()
    client.positions()
    client.orders(days=30)
    client.close()
    assert fake.violations == []
    assert fake.methods_called <= set(case.read_methods), fake.methods_called
    with pytest.raises(AssertionError):          # and the fake would have caught a write
        fake.place_order


@pytest.mark.parametrize("case", PARAMS)
def test_close_releases_the_session_and_reads_reopen_it(case, monkeypatch):
    client, fake = case.build(monkeypatch)
    client.snapshot()
    client.close()
    client.close()
    assert fake.closed == 1
    assert client.snapshot().currency == case.snapshot[0]


# ── Moomoo ───────────────────────────────────────────────────────────

def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestMoomoo:
    def test_probe_is_a_quick_tcp_check(self):
        port = _closed_port()
        start = time.monotonic()
        reason = OpenDTransport().probe("127.0.0.1", port, 1.5)
        assert f"127.0.0.1:{port}" in reason and time.monotonic() - start < QUICK
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            assert OpenDTransport().probe("127.0.0.1", listener.getsockname()[1], 1.5) == ""

    def test_closed_port_never_reaches_the_sdk(self):
        """With the real transport and nothing listening, no trade context is ever created (the
        SDK's constructor would retry forever)."""
        client = MoomooAccountClient("US", port=_closed_port(), now_ns=NOW_NS)
        start = time.monotonic()
        ok, reason = client.available()
        assert not ok and "not reachable" in reason
        with pytest.raises(AccountUnavailableError, match="not reachable"):
            client.orders()
        assert client._ctx is None and time.monotonic() - start < QUICK

    def test_handshake_that_never_completes_is_abandoned(self):
        release = threading.Event()
        ctx = FakeTradeContext(US_ACCINFO, [], [], [])
        client = MoomooAccountClient("US", transport=FakeOpenD(ctx, block=release), connect_timeout=0.2,
                                     now_ns=NOW_NS)
        start = time.monotonic()
        ok, reason = client.available()
        assert not ok and "handshake" in reason and time.monotonic() - start < QUICK
        release.set()                                  # the SDK finally connects: the late context is closed
        deadline = time.monotonic() + QUICK
        while not ctx.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ctx.closed == 1 and client._ctx is None

    def test_an_opend_error_is_unavailable(self, monkeypatch):
        client, _ = _moomoo(monkeypatch, errors={"accinfo_query": "Not logged in to OpenD"})
        with pytest.raises(AccountUnavailableError, match="Not logged in"):
            client.snapshot()

    def test_queries_use_the_configured_account(self, monkeypatch):
        monkeypatch.delenv("MIDFREQ_MOOMOO_TRADING_ENV", raising=False)
        monkeypatch.delenv("MIDFREQ_MOOMOO_SECURITY_FIRM", raising=False)
        ctx = FakeTradeContext(US_ACCINFO, US_POSITIONS, US_TODAY, US_HISTORY)
        opend = FakeOpenD(ctx)
        client = MoomooConnector({"host": "10.0.0.5", "port": 11112}, trade_transport=opend,
                                 now_ns=NOW_NS).account_client(market="us")
        client.snapshot()
        client.orders(days=14)
        assert opend.opened == [("US", "10.0.0.5", 11112, "FUTUSECURITIES")]
        assert {kw["trd_env"] for _, kw in ctx.calls} == {"SIMULATE"}
        assert {kw["acc_id"] for _, kw in ctx.calls} == {0}
        calls = dict(ctx.calls)
        assert calls["accinfo_query"]["currency"] == "USD"
        # the history window is in New York dates: 16:00 on 15 Oct back 14 days
        assert (calls["history_order_list_query"]["start"], calls["history_order_list_query"]["end"]) == \
            ("2026-10-01", "2026-10-15")

    def test_hong_kong_times_sides_and_shorts(self, monkeypatch):
        today = [moomoo_order("h3", "2026-10-15 09:32:00", "SELL_SHORT", code="HK.00700", price=0.0,
                              dealt_avg=480.4),
                 moomoo_order("h2", "2026-10-15 09:31:00", "BUY_BACK", code="HK.00700", dealt_qty=0.0,
                              dealt_avg=0.0, status="CANCELLED_ALL")]
        positions = [{"code": "HK.00700", "qty": 100.0, "average_cost": 480.0, "nominal_price": 479.0,
                      "market_val": -47900.0, "unrealized_pl": 100.0, "position_side": "SHORT"}]
        ctx = FakeTradeContext({**US_ACCINFO, "currency": "N/A"}, positions, today, [])
        client = MoomooConnector(OPEND, trade_transport=FakeOpenD(ctx), now_ns=NOW_NS).account_client(market="HK")
        assert client.timezone == "Asia/Hong_Kong" and client.snapshot().currency == "HKD"
        sold, cancelled = client.orders(days=1)
        assert sold.created == pd.Timestamp("2026-10-15 01:32", tz="UTC")      # 09:32 in Hong Kong
        assert (sold.side, sold.price, sold.average_fill_price) == ("SELL", None, 480.4)   # market order: no limit
        assert (cancelled.side, cancelled.filled_quantity, cancelled.average_fill_price) == ("BUY", 0.0, None)
        (short,) = client.positions()
        assert short.quantity == -100.0 and short.instrument_id == "00700.XHKG"

    def test_options_are_validated(self, monkeypatch):
        conn = MoomooConnector(OPEND, trade_transport=FakeOpenD(), now_ns=NOW_NS)
        assert conn.account_client(market="US", trading_env="real").trd_env == "REAL"
        with pytest.raises(ValueError, match="trading_env"):
            conn.account_client(market="US", trading_env="live")
        with pytest.raises(ConnectorError, match="market 'XX'"):
            conn.account_client(market="XX")
        with pytest.raises(ConnectorError, match="no secrets|take no secrets|remove secrets"):
            conn.account_client(market="US", secrets=["MOOMOO_PASSWORD"])

    def test_creating_an_account_client_does_not_import_the_sdk(self):
        code = ("import sys; import midfreq.data.adapters.accounts; "
                "from midfreq.data.adapters.moomoo.connector import MoomooConnector; "
                "MoomooConnector().account_client(market='US'); print('moomoo' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=repo_root(), capture_output=True, text=True,
                             check=True)
        assert out.stdout.strip() == "False"


# ── ccxt ─────────────────────────────────────────────────────────────

def _okx_client(secrets: Dict[str, Optional[str]], ex: Optional[FakeAccountExchange] = None, **kwargs):
    ex = ex or FakeAccountExchange(OKX_TOTALS, OKX_MARKETS, OKX_TICKERS, OKX_POSITIONS, _okx_open(), _okx_closed())
    client = CcxtAccountClient("okx", secrets=list(secrets), quote="USDT", exchange_factory=ex.factory,
                               get_secret=secrets.get, now_ns=NOW_NS, **kwargs)
    return client, ex


class TestCcxt:
    def test_credentials_come_from_the_named_variables(self):
        client, ex = _okx_client({"OKX_READ_API_KEY": "k", "OKX_READ_API_SECRET": "s",
                                  "OKX_READ_API_PASSPHRASE": "p"}, exchange_options={"timeout": 3000})
        assert client.available() == (True, "")
        assert ex.exchange_id == "okx"
        assert ex.config == {"apiKey": "k", "secret": "s", "password": "p", "enableRateLimit": True,
                             "timeout": 3000}

    def test_credential_names(self):
        assert [credential_field(v) for v in ("OKX_READ_API_KEY", "OKX_READ_API_SECRET", "OKX_PASSPHRASE",
                                              "X_PASSWORD", "BINANCE_SECRET_KEY", "X_UID")] == \
            ["apiKey", "secret", "password", "password", "secret", "uid"]
        with pytest.raises(ValueError, match="cannot tell"):
            credential_field("OKX_TOKEN")
        with pytest.raises(ValueError, match="same ccxt credential"):
            CcxtAccountClient("okx", secrets=["A_API_KEY", "B_API_KEY"])

    def test_a_missing_secret_is_named_and_no_exchange_is_made(self):
        client, ex = _okx_client({"OKX_READ_API_KEY": "k", "OKX_READ_API_SECRET": "s",
                                  "OKX_READ_API_PASSPHRASE": None})
        ok, reason = client.available()
        assert not ok and reason.startswith("missing OKX_READ_API_PASSPHRASE")
        assert "OKX_READ_API_KEY" not in reason and ex.config is None

    def test_an_account_without_secrets_is_unavailable(self):
        client, _ = _okx_client({})
        ok, reason = client.available()
        assert not ok and "no API key" in reason

    def test_unpriced_holdings_are_named_and_left_out(self, caplog):
        client, _ = _okx_client(OKX_ENV)
        with caplog.at_level("WARNING"):
            assert client.snapshot().market_value == pytest.approx(11_000.0)
        assert "XYZ" in caplog.text

    def test_an_exchange_error_is_unavailable(self):
        class NetworkError(Exception):
            pass
        ex = FakeAccountExchange(OKX_TOTALS, OKX_MARKETS, OKX_TICKERS,
                                 errors={"fetch_balance": NetworkError("okx GET /balance timed out")})
        client, _ = _okx_client(OKX_ENV, ex)
        with pytest.raises(AccountUnavailableError, match="NetworkError: okx GET /balance timed out"):
            client.snapshot()

    def test_exchanges_without_positions_or_tickers(self):
        class NotSupported(Exception):
            pass
        ex = FakeAccountExchange(OKX_TOTALS, OKX_MARKETS, OKX_TICKERS, OKX_POSITIONS,
                                 has={"fetchPositions": False, "fetchTickers": False})
        client, _ = _okx_client(OKX_ENV, ex)
        assert {p.symbol for p in client.positions()} == {"BTC/USDT", "ETH/USDT", "XYZ"}
        assert "fetch_positions" not in ex.methods_called and "fetch_ticker" in ex.methods_called
        ex.has["fetchPositions"] = True                # claims support, then refuses: no derivatives
        ex.errors["fetch_positions"] = NotSupported("okx fetchPositions() is not supported")
        assert len(client.positions()) == 3

    def test_closed_orders_are_asked_from_the_window_start(self):
        client, ex = _okx_client(OKX_ENV)
        client.orders(days=14)
        since = dict(ex.calls)["fetch_closed_orders"]["since"]
        assert since == (NOW - pd.Timedelta(days=14)).value // 1_000_000

    def test_quote_currency_and_instrument_ids_come_from_the_venue(self):
        ex = FakeAccountExchange({}, {}, {})
        conn = CcxtConnector(account_exchange_factory=ex.factory)
        assert conn.account_client(exchange="OKX").quote == "USDT"
        with pytest.raises(ConnectorError, match="quote"):
            conn.account_client(exchange="bybit")
        bybit = conn.account_client(exchange="bybit", quote="usdc")
        assert bybit.quote == "USDC" and bybit._instrument_id is None

    def test_the_default_factory_builds_ccxt_objects_without_a_request(self):
        pytest.importorskip("ccxt")
        ex = ccxt_account.ccxt_exchange("okx", {"apiKey": "k", "secret": "s", "password": "p",
                                                "enableRateLimit": True, "timeout": 5000})
        assert (ex.apiKey, ex.password, ex.timeout, ex.enableRateLimit) == ("k", "p", 5000, True)
        assert ex.markets is None                      # nothing was loaded
        with pytest.raises(ConnectorError, match="no exchange 'nosuchexchange'"):
            ccxt_account.ccxt_exchange("nosuchexchange", {})


# ── the accounts factory ─────────────────────────────────────────────

ACCOUNTS_YAML = """
moomoo:
  host: 127.0.0.1
accounts:
  paper-us:
    label: Paper (US)
    connector: moomoo
    options: {market: US}
  okx:
    connector: ccxt
    options: {exchange: okx}
    secrets: [MIDFREQ_TEST_OKX_READ_API_KEY, MIDFREQ_TEST_OKX_READ_API_SECRET, MIDFREQ_TEST_OKX_READ_API_PASSPHRASE]
  futu-hk:
    connector: futu
    options: {market: HK}
  binance:
    connector: binance
  typo:
    connector: moomoo
    options: {market: US, colour: blue}
  nowhere:
    connector: moomoo
    options: {market: XX}
  bloomberg:
    connector: bloomberg
"""


@pytest.fixture
def connections_file(tmp_path):
    path = tmp_path / "connections.yaml"
    path.write_text(ACCOUNTS_YAML)
    return path


@pytest.fixture
def fake_registry():
    ctx = FakeTradeContext(US_ACCINFO, US_POSITIONS, US_TODAY, US_HISTORY)
    ex = FakeAccountExchange(OKX_TOTALS, OKX_MARKETS, OKX_TICKERS, OKX_POSITIONS, _okx_open(), _okx_closed())
    reg = ConnectorRegistry()
    reg.register("moomoo", lambda config=None, **kw: MoomooConnector(
        OPEND, trade_transport=FakeOpenD(ctx), now_ns=NOW_NS, **kw))
    reg.register("ccxt", lambda config=None, **kw: CcxtConnector(
        config, account_exchange_factory=ex.factory, now_ns=NOW_NS, **kw))
    return reg


class TestAccountsFactory:
    def test_ids_and_entries(self, connections_file):
        assert accounts.account_ids(connections_file) == ["paper-us", "okx", "futu-hk", "binance", "typo",
                                                          "nowhere", "bloomberg"]
        assert accounts.account_config("paper-us", connections_file) == {
            "id": "paper-us", "label": "Paper (US)", "connector": "moomoo", "options": {"market": "US"},
            "secrets": [], "publish": False}
        okx = accounts.account_config("okx", str(connections_file))
        assert okx["label"] == "okx" and okx["secrets"] == list(OKX_ENV)
        with pytest.raises(KeyError, match="unknown account 'nope'"):
            accounts.account_config("nope", connections_file)

    def test_a_dict_works_like_the_file(self):
        conns = {"accounts": {"a": {"connector": "moomoo"}}}
        assert accounts.load_accounts(conns) == {"a": {"id": "a", "label": "a", "connector": "moomoo",
                                                       "options": {}, "secrets": [], "publish": False}}
        assert accounts.account_ids({}) == []
        with pytest.raises(ValueError, match="needs a `connector`"):
            accounts.load_accounts({"accounts": {"a": {"label": "no connector"}}})
        with pytest.raises(ValueError, match="secrets"):
            accounts.load_accounts({"accounts": {"a": {"connector": "ccxt", "secrets": "OKX_KEY"}}})
        assert accounts.account_config("a", {"accounts": {"a": {"connector": "moomoo", "publish": True}}})["publish"]
        with pytest.raises(ValueError, match="`publish` must be true or false"):
            accounts.load_accounts({"accounts": {"a": {"connector": "moomoo", "publish": "yes"}}})

    def test_create_moomoo_account(self, connections_file, fake_registry):
        client = accounts.create_account_client("paper-us", connections_file, registry=fake_registry)
        assert isinstance(client, MoomooAccountClient) and client.market == "US"
        assert client.available() == (True, "")
        assert client.snapshot().total_assets == pytest.approx(1_000_250.5)
        hk = accounts.create_account_client("futu-hk", connections_file, registry=fake_registry)   # an alias
        assert isinstance(hk, MoomooAccountClient) and hk.market == "HK" and hk.currency == "HKD"

    def test_create_exchange_account(self, connections_file, fake_registry, monkeypatch):
        _set_env(monkeypatch, OKX_ENV, present=False)
        client = accounts.create_account_client("okx", connections_file, registry=fake_registry)
        assert isinstance(client, CcxtAccountClient)
        ok, reason = client.available()
        assert not ok and "MIDFREQ_TEST_OKX_READ_API_KEY" in reason
        _set_env(monkeypatch, OKX_ENV)
        assert client.available() == (True, "")
        assert [o.order_id for o in client.orders(days=2)] == ["c5", "c4"]

    @pytest.mark.parametrize("account_id, match", [
        ("binance", "has no account client"), ("typo", "colour"), ("nowhere", "market 'XX'"),
        ("bloomberg", "unknown connector"),
    ])
    def test_entries_that_cannot_be_served(self, connections_file, fake_registry, account_id, match):
        with pytest.raises(ConnectorError, match=match):
            accounts.create_account_client(account_id, connections_file, registry=fake_registry)

    def test_the_shipped_accounts_can_be_created(self):
        """config/connections.yaml: every account is well formed and its connector makes a client
        (no request is made)."""
        ids = accounts.account_ids()
        assert ids, "config/connections.yaml lists no accounts"
        for account_id in ids:
            client = accounts.create_account_client(account_id)
            assert client.__class__.__name__.endswith("AccountClient")
            client.close()
