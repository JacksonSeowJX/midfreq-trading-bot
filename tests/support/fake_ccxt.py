"""
A scriptable fake of a ccxt exchange for the ccxt connector's execution client (no network).

- FakeCcxtVenue: the exchange's side. It holds the orders and trades and queues what a websocket
  would push. A test (through FakeCcxtDriver, the VenueDriver of tests/support/venue_driver.py)
  accepts, fills, rejects and cancels orders, repeats a push, and cuts and restores the link.
- FakeCcxtRest: the synchronous ccxt exchange object (load_markets, amount_to_precision,
  price_to_precision, create_order, cancel_order, fetch_order, fetch_open_orders,
  fetch_closed_orders, fetch_my_trades, set_sandbox_mode, close), answering in ccxt's unified
  structures with times in epoch ms, raising ccxt's own error classes.
- FakeCcxtPro: the ccxt.pro side (async watch_orders, watch_my_trades, watch_ohlcv, watch_ticker,
  watch_order_book), each returning the pushes queued since its last call, as ccxt.pro does with
  newUpdates. While the link is down every call raises ccxt.NetworkError and pushes are lost.

make_ccxt_execution_client(**options) -> (CcxtExecutionClient, FakeCcxtDriver) wires a real
CcxtExecutionClient (OKX, paper) to these; option updates='watch' (default) or 'poll' chooses how
the client hears of changes. Other options go to the client.
"""
from __future__ import annotations

import asyncio
import copy
import itertools
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import ccxt  # the fake raises ccxt's own error classes

from midfreq.data.adapters.ccxt.connector import CcxtConnector
from midfreq.data.adapters.ccxt.execution import CcxtExecutionClient

BASE_MS = 1_792_195_200_000          # 2026-10-17 00:00 UTC
FEE_RATE = 0.001


def spot_market(base: str, quote: str, amount_step: float = 1e-8, price_tick: float = 0.1) -> Dict[str, Any]:
    return {"id": f"{base}-{quote}", "symbol": f"{base}/{quote}", "base": base, "quote": quote, "spot": True,
            "type": "spot", "precision": {"amount": amount_step, "price": price_tick},
            "limits": {"amount": {"min": amount_step}, "cost": {"min": 1.0}}}


DEFAULT_MARKETS = {"BTC/USDT": spot_market("BTC", "USDT"), "ETH/USDT": spot_market("ETH", "USDT", 1e-6, 0.01)}


class FakeCcxtVenue:
    """The exchange: orders, trades, pushes. Thread-safe (the test, the client's threads)."""

    def __init__(self, exchange_id: str = "okx", markets: Optional[Dict[str, Dict[str, Any]]] = None):
        self.exchange_id = exchange_id
        self.markets = copy.deepcopy(markets or DEFAULT_MARKETS)
        self.lock = threading.RLock()
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Dict[str, Any]] = []
        self.submissions: List[Dict[str, Any]] = []
        self.cancel_requests: List[str] = []
        self.refusals: Dict[str, str] = {}
        self.create_errors: deque = deque()        # exceptions the next create_order calls raise
        self.lose_create_reply = False             # the next create_order is placed but its reply lost
        self.connected = True
        self.streaming = False                     # a FakeCcxtPro is attached: pushes are queued
        self.queues: Dict[str, deque] = {"orders": deque(), "trades": deque(), "ohlcv": deque(), "ticker": deque(),
                                         "book": deque()}
        self.last_push: Optional[Tuple[str, Dict[str, Any]]] = None
        self.candles: Dict[str, Dict[int, List[float]]] = {}    # symbol -> open ms -> OHLCV row
        self.tickers: Dict[str, Dict[str, Any]] = {}
        self.calls: List[str] = []
        self._ids = itertools.count(1001)
        self._trade_ids = itertools.count(1)
        self._clock = itertools.count(1)

    def now_ms(self) -> int:
        return BASE_MS + next(self._clock) * 1000

    def check_link(self) -> None:
        if not self.connected:
            raise ccxt.NetworkError(f"{self.exchange_id} GET https://www.okx.com: connection lost")

    def push(self, channel: str, payload: Dict[str, Any]) -> None:
        with self.lock:
            if not self.connected or not self.streaming:
                return
            self.last_push = (channel, copy.deepcopy(payload))
            self.queues[channel].append(copy.deepcopy(payload))

    def drain(self, channel: str) -> List[Dict[str, Any]]:
        with self.lock:
            items = list(self.queues[channel])
            self.queues[channel].clear()
            return items

    def order(self, venue_order_id: str) -> Dict[str, Any]:
        with self.lock:
            if venue_order_id not in self.orders:
                raise ccxt.OrderNotFound(f"{self.exchange_id} order {venue_order_id} does not exist")
            return self.orders[venue_order_id]

    def new_order(self, symbol, type_, side, amount, price, client_order_id) -> Dict[str, Any]:
        oid = str(next(self._ids))
        ts = self.now_ms()
        o = {"id": oid, "clientOrderId": client_order_id, "timestamp": ts, "datetime": None,
             "lastTradeTimestamp": None, "lastUpdateTimestamp": ts, "symbol": symbol, "type": type_,
             "timeInForce": "GTC", "side": side, "price": price, "average": None, "amount": amount,
             "filled": 0.0, "remaining": amount, "cost": 0.0, "status": None, "fee": None, "trades": [],
             "info": {"ordId": oid, "clOrdId": client_order_id, "state": ""}}
        self.orders[oid] = o
        return o

    def update(self, oid: str, status: str, info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self.lock:
            o = self.order(oid)
            o["status"] = status
            o["lastUpdateTimestamp"] = self.now_ms()
            o["info"].update(info or {})
            self.push("orders", o)
            return copy.deepcopy(o)


class FakeCcxtRest:
    """The synchronous ccxt exchange object."""

    def __init__(self, venue: FakeCcxtVenue):
        self.venue = venue
        self.id = venue.exchange_id
        self.has = {"fetchMyTrades": True, "fetchOrderTrades": False, "fetchOpenOrders": True,
                    "fetchClosedOrders": True, "fetchOrder": True, "createOrder": True, "cancelOrder": True}
        self.requiredCredentials = {"apiKey": True, "secret": True, "password": True}
        self.precisionMode = 4
        self.timeframes = {"1m": "1m", "5m": "5m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}
        self.markets: Dict[str, Dict[str, Any]] = {}
        self.sandbox = False
        self.closed = 0

    def _call(self, name: str) -> None:
        self.venue.calls.append(name)
        self.venue.check_link()

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox = bool(enabled)

    def load_markets(self, reload: bool = False, params=None):
        self._call("load_markets")
        self.markets = copy.deepcopy(self.venue.markets)
        return self.markets

    def market(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.markets:
            raise ccxt.BadSymbol(f"{self.id} does not have market symbol {symbol}")
        return self.markets[symbol]

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        step = self.market(symbol)["precision"]["amount"]
        units = int(float(amount) / step + 1e-9)
        if units <= 0:
            raise ccxt.InvalidOrder(f"{self.id} amount of {symbol} must be greater than minimum amount precision of {step}")
        return repr(round(units * step, 12))

    def price_to_precision(self, symbol: str, price: float) -> str:
        tick = self.market(symbol)["precision"]["price"]
        return repr(round(round(float(price) / tick) * tick, 12))

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        self._call("create_order")
        params = dict(params or {})
        self.market(symbol)
        v = self.venue
        with v.lock:
            if v.create_errors:
                raise v.create_errors.popleft()
            cid = params.get("clOrdId") or params.get("newClientOrderId") or params.get("clientOrderId")
            if cid and any(o.get("clientOrderId") == cid for o in v.orders.values()):
                raise ccxt.InvalidOrder(f"{self.id} duplicated clOrdId {cid}")
            o = v.new_order(symbol, type, side, float(amount), None if price is None else float(price), cid)
            v.submissions.append({"symbol": symbol, "side": side, "quantity": float(amount),
                                  "price": None if price is None else float(price), "order_type": type,
                                  "client_order_id": cid, "venue_order_id": o["id"], "params": params})
            if v.lose_create_reply:
                v.lose_create_reply = False
                raise ccxt.RequestTimeout(f"{self.id} POST https://www.okx.com/api/v5/trade/order timed out")
            # OKX answers a placement with the ids only; the state comes from the stream or a fetch
            return {"id": o["id"], "clientOrderId": cid, "timestamp": None, "status": None, "symbol": symbol,
                    "info": {"ordId": o["id"], "clOrdId": cid, "sCode": "0", "sMsg": "Order placed"}}

    def cancel_order(self, id, symbol=None, params=None):
        self._call("cancel_order")
        v = self.venue
        with v.lock:
            o = v.order(str(id))
            reason = v.refusals.pop(str(id), None)
            if reason is not None:
                raise ccxt.InvalidOrder(f"{self.id} {{\"sCode\":\"51400\",\"sMsg\":\"{reason}\"}}")
            if o["status"] in ("closed", "canceled", "rejected", "expired"):
                raise ccxt.OrderNotFound(f"{self.id} order {id} is already {o['status']}")
            v.cancel_requests.append(str(id))
            return {"id": str(id), "clientOrderId": o["clientOrderId"], "status": None,
                    "info": {"ordId": str(id), "sCode": "0", "sMsg": ""}}

    def fetch_order(self, id, symbol=None, params=None):
        self._call("fetch_order")
        v = self.venue
        params = dict(params or {})
        with v.lock:
            cid = params.get("clOrdId") or params.get("clientOrderId")
            if id is None and cid:
                for o in v.orders.values():
                    if o["clientOrderId"] == cid:
                        return copy.deepcopy(o)
                raise ccxt.OrderNotFound(f"{self.id} order with clOrdId {cid} does not exist")
            return copy.deepcopy(v.order(str(id)))

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        self._call("fetch_open_orders")
        with self.venue.lock:
            return [copy.deepcopy(o) for o in self.venue.orders.values()
                    if o["status"] == "open" and (symbol is None or o["symbol"] == symbol)]

    def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
        self._call("fetch_closed_orders")
        with self.venue.lock:
            return [copy.deepcopy(o) for o in self.venue.orders.values()
                    if o["status"] in ("closed", "canceled", "rejected", "expired")
                    and (symbol is None or o["symbol"] == symbol) and (since is None or o["timestamp"] >= since)]

    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        self._call("fetch_my_trades")
        with self.venue.lock:
            out = [copy.deepcopy(t) for t in self.venue.trades
                   if (symbol is None or t["symbol"] == symbol) and (since is None or t["timestamp"] >= since)]
        out.sort(key=lambda t: t["timestamp"])
        return out[:limit] if limit else out

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        self._call("fetch_ohlcv")
        self.market(symbol)
        with self.venue.lock:
            rows = [list(r) for _, r in sorted(self.venue.candles.get(symbol, {}).items())]
        return rows[-limit:] if limit else rows

    def fetch_ticker(self, symbol, params=None):
        self._call("fetch_ticker")
        self.market(symbol)
        with self.venue.lock:
            return copy.deepcopy(self.venue.tickers.get(symbol, {"symbol": symbol, "bid": None, "ask": None}))

    def close(self) -> None:
        self.closed += 1


class FakeCcxtPro:
    """The ccxt.pro (websocket) side: each watch returns what was pushed since its last call."""

    POLL_S = 0.002

    def __init__(self, venue: FakeCcxtVenue, has: Optional[Dict[str, bool]] = None):
        self.venue = venue
        venue.streaming = True
        self.id = venue.exchange_id
        self.has = {"watchOrders": True, "watchMyTrades": True, "watchOHLCV": True, "watchTicker": True,
                    "watchOrderBook": True, **(has or {})}
        self.sandbox = False
        self.closed = 0
        self.watch_calls: List[str] = []

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox = bool(enabled)

    async def _wait(self, channel: str, keep=lambda item: True) -> List[Dict[str, Any]]:
        while True:
            self.venue.check_link()
            items = [i for i in self.venue.drain(channel) if keep(i)]
            if items:
                return items
            await asyncio.sleep(self.POLL_S)

    async def watch_orders(self, symbol=None, since=None, limit=None, params=None):
        self.watch_calls.append("watch_orders")
        return await self._wait("orders")

    async def watch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        self.watch_calls.append("watch_my_trades")
        return await self._wait("trades")

    async def watch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        self.watch_calls.append("watch_ohlcv")
        items = await self._wait("ohlcv", lambda i: i.get("symbol", symbol) == symbol)
        return [i["row"] for i in items]

    async def watch_ticker(self, symbol, params=None):
        self.watch_calls.append("watch_ticker")
        items = await self._wait("ticker", lambda i: i.get("symbol") == symbol)
        return items[-1]

    async def watch_order_book(self, symbol, limit=None, params=None):
        self.watch_calls.append("watch_order_book")
        items = await self._wait("book", lambda i: i.get("symbol") == symbol)
        return items[-1]

    async def close(self) -> None:
        self.closed += 1


class FakeCcxtDriver:
    """The VenueDriver (tests/support/venue_driver.py) for FakeCcxtVenue."""

    def __init__(self, venue: FakeCcxtVenue):
        self.venue = venue

    def last_order_id(self) -> str:
        with self.venue.lock:
            return next(reversed(self.venue.orders))

    def submitted(self) -> List[dict]:
        with self.venue.lock:
            return [dict(s) for s in self.venue.submissions]

    def accept(self, venue_order_id: str) -> None:
        self.venue.update(venue_order_id, "open", {"state": "live"})

    def fill(self, venue_order_id: str, quantity: float, price: float, trade_id: Optional[str] = None) -> None:
        v = self.venue
        with v.lock:
            o = v.order(venue_order_id)
            ts = v.now_ms()
            quantity, price = float(quantity), float(price)
            quote = v.markets[o["symbol"]]["quote"]
            trade = {"id": trade_id or f"T{next(v._trade_ids)}", "order": venue_order_id, "timestamp": ts,
                     "datetime": None, "symbol": o["symbol"], "type": o["type"], "side": o["side"],
                     "takerOrMaker": "taker", "price": price, "amount": quantity, "cost": quantity * price,
                     "fee": {"cost": quantity * price * FEE_RATE, "currency": quote},
                     "info": {"ordId": venue_order_id, "clOrdId": o["clientOrderId"], "tradeId": trade_id}}
            v.trades.append(trade)
            filled = o["filled"] + quantity
            o["cost"] += quantity * price
            o["average"] = o["cost"] / filled
            o["filled"], o["remaining"] = filled, max(o["amount"] - filled, 0.0)
            o["lastTradeTimestamp"] = ts       # OKX's orders carry no trades: fetch them
            done = filled >= o["amount"] - 1e-12
            o["status"] = "closed" if done else "open"
            o["info"]["state"] = "filled" if done else "partially_filled"
            o["lastUpdateTimestamp"] = ts
            v.push("orders", o)        # ccxt.pro gets both from OKX's one orders-channel message
            v.push("trades", trade)

    def reject(self, venue_order_id: str, reason: str = "") -> None:
        self.venue.update(venue_order_id, "rejected", {"state": "order_failed", "sMsg": reason or "rejected"})

    def cancel_ack(self, venue_order_id: str) -> None:
        self.venue.update(venue_order_id, "canceled", {"state": "canceled"})

    def refuse_cancel(self, venue_order_id: str, reason: str = "") -> None:
        with self.venue.lock:
            self.venue.refusals[venue_order_id] = reason or "Order cancellation failed"

    def repeat_last_push(self) -> None:
        v = self.venue
        with v.lock:
            if v.last_push is not None and v.connected and v.streaming:
                channel, payload = v.last_push
                v.queues[channel].append(copy.deepcopy(payload))

    def disconnect(self) -> None:
        with self.venue.lock:
            self.venue.connected = False

    def reconnect(self) -> None:
        with self.venue.lock:
            self.venue.connected = True

    # beyond the protocol: market data pushes for the streaming tests
    def push_candle(self, symbol: str, row: List[float]) -> None:
        """A candle update ([open ms, o, h, l, c, v]): pushed, and what fetch_ohlcv returns."""
        with self.venue.lock:
            self.venue.candles.setdefault(symbol, {})[int(row[0])] = list(row)
        self.venue.push("ohlcv", {"symbol": symbol, "row": list(row)})

    def push_ticker(self, symbol: str, bid: float, ask: float, bid_size: float = 1.0, ask_size: float = 1.0,
                    ts_ms: Optional[int] = None) -> None:
        ticker = {"symbol": symbol, "bid": bid, "ask": ask, "bidVolume": bid_size, "askVolume": ask_size,
                  "timestamp": ts_ms}
        with self.venue.lock:
            self.venue.tickers[symbol] = ticker
        self.venue.push("ticker", ticker)

    def push_book(self, symbol: str, bids: List[List[float]], asks: List[List[float]],
                  ts_ms: Optional[int] = None) -> None:
        self.venue.push("book", {"symbol": symbol, "bids": bids, "asks": asks, "timestamp": ts_ms})


QUICK = {"poll_interval": 0.01, "housekeeping_interval": 0.01, "reconnect_delay": 0.01,
         "max_reconnect_delay": 0.05, "unconfirmed_timeout": 0.5, "resync_interval": 0.2}


def make_ccxt_execution_client(**options) -> Tuple[CcxtExecutionClient, FakeCcxtDriver]:
    """A real CcxtExecutionClient (OKX) on a FakeCcxtVenue. `updates` = 'watch' (default) or
    'poll'; `markets` replaces the venue's markets; everything else is a client option."""
    updates = options.pop("updates", "watch")
    markets = options.pop("markets", None)
    venue = FakeCcxtVenue("okx", markets)
    rest = FakeCcxtRest(venue)
    pro = FakeCcxtPro(venue) if updates == "watch" else None
    connector = CcxtConnector({"min_request_interval": 0.0})
    client = CcxtExecutionClient(connector, {"exchange": "okx", "updates": updates, **QUICK, **options},
                                 exchange=rest, stream_exchange=pro, get_secret=lambda var: None)
    client.fake_rest, client.fake_pro, client.fake_venue = rest, pro, venue
    return client, FakeCcxtDriver(venue)
