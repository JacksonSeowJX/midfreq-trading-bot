"""
Fake account transports for AccountClient tests, each answering in its source's native format
(no network, no OpenD, no exchange):

- Moomoo: FakeOpenD (the TCP probe and the trade-context factory) and FakeTradeContext (the SDK's
  accinfo_query / position_list_query / order_list_query / history_order_list_query, which return
  (ret, DataFrame) with OpenD's column names and exchange-local time strings);
- ccxt: FakeAccountExchange (fetch_balance, load_markets, fetch_tickers, fetch_positions,
  fetch_open_orders, fetch_closed_orders, in ccxt's unified structures, times in epoch ms).

The SDK-side fakes are strictly read-only: touching any attribute they do not define (place_order,
modify_order, unlock_trade, create_order, cancel_order, withdraw, ...) records a violation and
raises, so a client that tries to write fails the test.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import pandas as pd


class ReadOnlyFake:
    """Defines only read methods; anything else is a violation."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.__dict__.setdefault("violations", []).append(name)
        raise AssertionError(f"read-only account client touched {type(self).__name__}.{name}")

    def _record(self, method: str, **kwargs) -> None:
        self.__dict__.setdefault("calls", []).append((method, kwargs))

    @property
    def methods_called(self) -> set:
        return {m for m, _ in self.__dict__.get("calls", [])}


# ── Moomoo ───────────────────────────────────────────────────────────

class FakeTradeContext(ReadOnlyFake):
    def __init__(self, accinfo: Dict[str, Any], positions: List[Dict[str, Any]],
                 today: List[Dict[str, Any]], history: List[Dict[str, Any]],
                 errors: Optional[Dict[str, str]] = None):
        self.accinfo = accinfo
        self.positions = positions
        self.today = today
        self.history = history
        self.errors = dict(errors or {})
        self.calls: List[tuple] = []
        self.violations: List[str] = []
        self.closed = 0

    def _answer(self, method: str, rows):
        if method in self.errors:
            return -1, self.errors[method]
        return 0, pd.DataFrame(rows)

    def accinfo_query(self, trd_env="REAL", acc_id=0, acc_index=0, refresh_cache=False, currency="HKD", **kw):
        self._record("accinfo_query", trd_env=trd_env, acc_id=acc_id, currency=currency)
        return self._answer("accinfo_query", [self.accinfo])

    def position_list_query(self, code="", trd_env="REAL", acc_id=0, acc_index=0, **kw):
        self._record("position_list_query", trd_env=trd_env, acc_id=acc_id)
        return self._answer("position_list_query", self.positions)

    def order_list_query(self, order_id="", trd_env="REAL", acc_id=0, acc_index=0, **kw):
        self._record("order_list_query", trd_env=trd_env, acc_id=acc_id)
        return self._answer("order_list_query", self.today)

    def history_order_list_query(self, status_filter_list=(), code="", start="", end="", trd_env="REAL",
                                 acc_id=0, acc_index=0, **kw):
        """Like OpenD: the orders whose local create date lies in [start, end] (dates)."""
        self._record("history_order_list_query", trd_env=trd_env, acc_id=acc_id, start=start, end=end)
        rows = [r for r in self.history if start <= str(r["create_time"])[:10] <= end]
        return self._answer("history_order_list_query", rows)

    def close(self):
        self.closed += 1


class FakeOpenD:
    """Stands in for OpenDTransport. `port_open=False` is OpenD down; `block` (an Event) makes
    open_context wait, as the SDK does when the handshake never completes."""

    def __init__(self, ctx: Optional[FakeTradeContext] = None, port_open: bool = True,
                 block: Optional[threading.Event] = None):
        self.ctx = ctx
        self.port_open = port_open
        self.block = block
        self.probes: List[tuple] = []
        self.opened: List[tuple] = []

    def probe(self, host, port, timeout):
        self.probes.append((host, port, timeout))
        return "" if self.port_open else f"OpenD is not reachable at {host}:{port} (Connection refused)"

    def open_context(self, market, host, port, security_firm, query_timeout):
        self.opened.append((market, host, port, security_firm))
        if self.block is not None:
            self.block.wait()
        return self.ctx


def moomoo_order(order_id: str, create_local: str, side: str = "BUY", qty: float = 10.0, price: float = 100.0,
                 dealt_qty: float = 10.0, dealt_avg: float = 100.0, status: str = "FILLED_ALL",
                 code: str = "US.AAPL", updated_local: Optional[str] = None) -> Dict[str, Any]:
    return {"code": code, "stock_name": code, "trd_side": side, "order_type": "NORMAL",
            "order_status": status, "order_id": order_id, "qty": qty, "price": price,
            "create_time": create_local, "updated_time": updated_local or create_local,
            "dealt_qty": dealt_qty, "dealt_avg_price": dealt_avg, "currency": "USD"}


# ── ccxt ─────────────────────────────────────────────────────────────

class FakeAccountExchange(ReadOnlyFake):
    def __init__(self, totals: Dict[str, float], markets: Dict[str, Dict[str, Any]],
                 tickers: Dict[str, Dict[str, Any]], positions: Optional[List[Dict[str, Any]]] = None,
                 open_orders: Optional[List[Dict[str, Any]]] = None,
                 closed_orders: Optional[List[Dict[str, Any]]] = None,
                 has: Optional[Dict[str, Any]] = None, errors: Optional[Dict[str, Exception]] = None):
        self.totals = totals
        self.markets = markets
        self.tickers = tickers
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.closed_orders = closed_orders or []
        self.has = {"fetchTickers": True, "fetchPositions": True, "fetchOpenOrders": True,
                    "fetchClosedOrders": True, "fetchOrders": False, **(has or {})}
        self.errors = dict(errors or {})
        self.calls: List[tuple] = []
        self.violations: List[str] = []
        self.config: Optional[Dict[str, Any]] = None
        self.exchange_id: Optional[str] = None
        self.closed = 0

    def factory(self, exchange_id: str, config: Dict[str, Any]) -> "FakeAccountExchange":
        """Use as the client's exchange_factory: remembers what the client built it with."""
        self.exchange_id, self.config = exchange_id, dict(config)
        return self

    def _check(self, method: str, **kwargs) -> None:
        self._record(method, **kwargs)
        if method in self.errors:
            raise self.errors[method]

    def fetch_balance(self, params=None):
        self._check("fetch_balance")
        return {"info": {}, "total": dict(self.totals), "free": dict(self.totals),
                "used": {k: 0.0 for k in self.totals}, **{k: {"total": v} for k, v in self.totals.items()}}

    def load_markets(self, reload=False, params=None):
        self._check("load_markets")
        return self.markets

    def fetch_tickers(self, symbols=None, params=None):
        self._check("fetch_tickers", symbols=list(symbols or []))
        return {s: t for s, t in self.tickers.items() if symbols is None or s in symbols}

    def fetch_ticker(self, symbol, params=None):
        self._check("fetch_ticker", symbol=symbol)
        return self.tickers[symbol]

    def fetch_positions(self, symbols=None, params=None):
        self._check("fetch_positions")
        return list(self.positions)

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        self._check("fetch_open_orders", since=since)
        return list(self.open_orders)

    def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
        self._check("fetch_closed_orders", since=since)
        return [o for o in self.closed_orders if since is None or o["timestamp"] >= since]

    def close(self):
        self.closed += 1


def ccxt_order(order_id: str, ts: pd.Timestamp, side: str = "buy", amount: float = 0.1, price: float = 60000.0,
               filled: float = 0.1, average: Optional[float] = 60000.0, status: str = "closed",
               symbol: str = "BTC/USDT") -> Dict[str, Any]:
    ms = int(ts.value // 1_000_000)
    return {"id": order_id, "clientOrderId": None, "timestamp": ms, "datetime": ts.isoformat(),
            "lastTradeTimestamp": ms + 1000 if filled else None, "lastUpdateTimestamp": ms + 2000,
            "symbol": symbol, "type": "limit", "side": side, "price": price, "amount": amount,
            "filled": filled, "remaining": amount - filled, "average": average, "status": status,
            "fee": None, "trades": [], "info": {}}


def ccxt_spot_market(base: str, quote: str) -> Dict[str, Any]:
    return {"id": f"{base}-{quote}", "symbol": f"{base}/{quote}", "base": base, "quote": quote,
            "spot": True, "swap": False}
