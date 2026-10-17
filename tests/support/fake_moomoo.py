"""
A fake of the parts of the `moomoo` SDK the Moomoo connector's live side uses, and of the OpenD
and broker behind it. No network, no OpenD.

    venue = FakeMoomoo(market="US")          # OpenD + the broker + the exchange, and the test's driver
    venue.sdk                                # stands in for the `moomoo` module
    conn = make_moomoo_connector(venue)      # a MoomooConnector wired to it (sdk, probe, timer, clock)

Injection. MoomooConnector takes `sdk` (the module the live side builds its contexts and push
handlers from), `probe` (the TCP check of OpenD's port), `timer` (deferred unsubscribes) and the
usual `now_ns`. `venue.sdk` is a namespace with the SDK's names: OpenSecTradeContext and
OpenQuoteContext (classes bound to this venue, which the client subclasses exactly as it subclasses
the real ones), the push handler bases (TradeOrderHandlerBase, TradeDealHandlerBase,
CurKlineHandlerBase, OrderBookHandlerBase: `on_recv_rsp(payload)` returns (RET_OK, DataFrame) /
(RET_OK, dict) as the real parsers do), RET_OK / RET_ERROR and the string constants (TrdEnv,
TrdSide, OrderType, OrderStatus, SubType, ...).

Pushes are delivered synchronously, on the caller's thread, so a test sees the client's events as
soon as a driver call returns. While the venue is disconnected pushes are lost and queries fail
with the SDK's own 'Connect timeout'; reconnect() runs each context's on_api_socket_reconnected,
as the SDK does after it reconnects.

`make_moomoo_execution_client(**options) -> (MoomooExecutionClient, FakeMoomoo)` is the factory
the execution contract suite uses; FakeMoomoo implements tests/support/venue_driver.VenueDriver.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

RET_OK, RET_ERROR = 0, -1

# Column lists as the SDK 10.2 builds its DataFrames.
ORDER_COLUMNS = [
    "code", "stock_name", "order_market", "trd_side", "order_type", "order_status", "order_id", "qty", "price",
    "create_time", "updated_time", "dealt_qty", "dealt_avg_price", "last_err_msg", "remark", "time_in_force",
    "fill_outside_rth", "session", "aux_price", "trail_type", "trail_value", "trail_spread", "currency",
    "jp_acc_type",
]
ORDER_PUSH_COLUMNS = [
    "trd_env", "code", "stock_name", "dealt_avg_price", "dealt_qty", "qty", "order_id", "order_type", "price",
    "order_status", "create_time", "updated_time", "trd_side", "last_err_msg", "trd_market", "remark",
    "time_in_force", "fill_outside_rth", "session", "aux_price", "trail_type", "trail_value", "trail_spread",
    "currency", "jp_acc_type",
]
DEAL_COLUMNS = [
    "code", "stock_name", "deal_market", "deal_id", "order_id", "qty", "price", "trd_side", "create_time",
    "counter_broker_id", "counter_broker_name", "status", "jp_acc_type",
]
DEAL_PUSH_COLUMNS = [
    "trd_env", "code", "stock_name", "deal_id", "order_id", "qty", "price", "trd_side", "create_time",
    "counter_broker_id", "counter_broker_name", "trd_market", "status", "jp_acc_type",
]
KLINE_COLUMNS = [
    "code", "name", "time_key", "open", "close", "high", "low", "volume", "turnover", "k_type", "last_close",
    "pe_ratio", "turnover_rate",
]
ACC_COLUMNS = ["acc_id", "trd_env", "acc_type", "uni_card_num", "card_num", "security_firm", "sim_acc_type",
               "trdmarket_auth", "acc_status", "acc_role", "jp_acc_type"]

MARKETS = {"US": ("America/New_York", "USD"), "HK": ("Asia/Hong_Kong", "HKD")}
FINAL = {"FILLED_ALL", "CANCELLED_PART", "CANCELLED_ALL", "FAILED", "SUBMIT_FAILED", "DISABLED", "DELETED",
         "FILL_CANCELLED"}


# ── SDK constants (the real ones are these strings) ─────────────────────────────────────────────

class TrdEnv:
    REAL = "REAL"
    SIMULATE = "SIMULATE"


class TrdSide:
    NONE, BUY, SELL, SELL_SHORT, BUY_BACK = "N/A", "BUY", "SELL", "SELL_SHORT", "BUY_BACK"


class OrderType:
    NONE, NORMAL, MARKET = "N/A", "NORMAL", "MARKET"


class TimeInForce:
    DAY, GTC = "DAY", "GTC"


class ModifyOrderOp:
    NONE, NORMAL, CANCEL, DISABLE, ENABLE, DELETE = "N/A", "NORMAL", "CANCEL", "DISABLE", "ENABLE", "DELETE"


class TrdMarket:
    NONE, HK, US = "N/A", "HK", "US"


class SecurityFirm:
    NONE, FUTUSECURITIES, FUTUINC, FUTUSG = "N/A", "FUTUSECURITIES", "FUTUINC", "FUTUSG"


class SubType:
    NONE, TICKER, QUOTE, ORDER_BOOK = "N/A", "TICKER", "QUOTE", "ORDER_BOOK"
    K_1M, K_3M, K_5M, K_15M, K_30M, K_60M, K_DAY = "K_1M", "K_3M", "K_5M", "K_15M", "K_30M", "K_60M", "K_DAY"


KLType = SubType


class OrderStatus:
    NONE = "N/A"
    UNSUBMITTED = "UNSUBMITTED"
    WAITING_SUBMIT = "WAITING_SUBMIT"
    SUBMITTING = "SUBMITTING"
    SUBMIT_FAILED = "SUBMIT_FAILED"
    TIMEOUT = "TIMEOUT"
    SUBMITTED = "SUBMITTED"
    FILLED_PART = "FILLED_PART"
    FILLED_ALL = "FILLED_ALL"
    CANCELLING_PART = "CANCELLING_PART"
    CANCELLING_ALL = "CANCELLING_ALL"
    CANCELLED_PART = "CANCELLED_PART"
    CANCELLED_ALL = "CANCELLED_ALL"
    FAILED = "FAILED"
    DISABLED = "DISABLED"
    DELETED = "DELETED"
    FILL_CANCELLED = "FILL_CANCELLED"


class DealStatus:
    OK, CANCELLED, CHANGED = "OK", "CANCELLED", "CHANGED"


# ── push handler bases: `payload` stands in for the protobuf the real parsers read ──────────────

@dataclass(frozen=True)
class PushError:
    """A push the SDK could not parse: on_recv_rsp returns (RET_ERROR, msg)."""
    msg: str


class RspHandlerBase:
    def on_recv_rsp(self, rsp_pb):
        return RET_OK, rsp_pb


class TradeOrderHandlerBase(RspHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        if isinstance(rsp_pb, PushError):
            return RET_ERROR, rsp_pb.msg
        return RET_OK, pd.DataFrame([rsp_pb], columns=ORDER_PUSH_COLUMNS)


class TradeDealHandlerBase(RspHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        if isinstance(rsp_pb, PushError):
            return RET_ERROR, rsp_pb.msg
        return RET_OK, pd.DataFrame([rsp_pb], columns=DEAL_PUSH_COLUMNS)


class CurKlineHandlerBase(RspHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        if isinstance(rsp_pb, PushError):
            return RET_ERROR, rsp_pb.msg
        return RET_OK, pd.DataFrame(list(rsp_pb), columns=KLINE_COLUMNS)


class OrderBookHandlerBase(RspHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        if isinstance(rsp_pb, PushError):
            return RET_ERROR, rsp_pb.msg
        return RET_OK, dict(rsp_pb)


TRADE_HANDLER_BASES = (TradeOrderHandlerBase, TradeDealHandlerBase)
QUOTE_HANDLER_BASES = (CurKlineHandlerBase, OrderBookHandlerBase)


# ── clock and timers ────────────────────────────────────────────────────────────────────────────

class FakeClock:
    """now_ns for the connector and the venue; advance() moves both."""

    def __init__(self, start: str = "2026-10-16 14:00:00"):
        self.ns = int(pd.Timestamp(start, tz="UTC").value)

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1e9)

    def set(self, when: str) -> None:
        self.ns = int(pd.Timestamp(when, tz="UTC").value)


class FakeTimer:
    def __init__(self, due_ns: int, fn: Callable[[], None]):
        self.due_ns, self.fn, self.cancelled, self.fired = due_ns, fn, False, False

    def cancel(self) -> None:
        self.cancelled = True


# ── contexts ────────────────────────────────────────────────────────────────────────────────────

class _ContextBase:
    venue: "FakeMoomoo" = None   # bound by FakeMoomoo.sdk's subclasses

    def _init(self, host, port):
        self.host, self.port = host, port
        self.handlers: Dict[type, Any] = {}
        self.closed = False
        self.query_timeout = None
        self.conn_id = next(self.venue._ids)

    def set_sync_query_connect_timeout(self, timeout):
        self.query_timeout = timeout

    def set_handler(self, handler):
        for base in self._handler_bases:
            if isinstance(handler, base):
                self.handlers[base] = handler
                return RET_OK
        return RET_ERROR

    def on_api_socket_reconnected(self):
        return RET_OK, ""

    def on_disconnect(self, conn_id, reason, msg):
        return None

    def _down(self) -> Optional[Tuple[int, str]]:
        if self.closed:
            return RET_ERROR, "Connection closed"
        if not self.venue.connected:
            return RET_ERROR, "Connect timeout"
        return None


class FakeTradeContext(_ContextBase):
    """OpenSecTradeContext: the trade queries and order calls the execution client makes."""
    _handler_bases = TRADE_HANDLER_BASES

    def __init__(self, filter_trdmarket="HK", host="127.0.0.1", port=11111, is_encrypt=None,
                 security_firm="FUTUSECURITIES"):
        self._init(host, port)
        self.market = str(filter_trdmarket)
        self.security_firm = security_firm
        self.venue.trade_contexts.append(self)
        self.on_api_socket_reconnected()   # the SDK runs it after the first connect too

    def close(self):
        self.closed = True

    def get_acc_list(self):
        self.venue._record("get_acc_list")
        return self._down() or (RET_OK, pd.DataFrame(self.venue.accounts, columns=ACC_COLUMNS))

    def place_order(self, price, qty, code, trd_side, order_type="NORMAL", adjust_limit=0, trd_env="REAL",
                    acc_id=0, acc_index=0, remark=None, time_in_force="DAY", fill_outside_rth=False, **kwargs):
        return self.venue._place(self, price=price, qty=qty, code=code, trd_side=trd_side, order_type=order_type,
                                 trd_env=trd_env, acc_id=acc_id, remark=remark, time_in_force=time_in_force)

    def modify_order(self, modify_order_op, order_id, qty, price, adjust_limit=0, trd_env="REAL", acc_id=0,
                     acc_index=0, **kwargs):
        return self.venue._modify(self, modify_order_op, str(order_id), qty, price, trd_env, acc_id)

    def order_list_query(self, order_id="", status_filter_list=(), code="", start="", end="", trd_env="REAL",
                         acc_id=0, acc_index=0, refresh_cache=False, order_market="N/A"):
        self.venue._record("order_list_query", order_id=order_id, trd_env=trd_env, refresh_cache=refresh_cache)
        down = self._down() or self.venue._query_error("order_list_query") or self.venue._check_env(trd_env, acc_id)
        if down:
            return down
        rows = [self.venue._visible(o) for o in self.venue.orders.values() if o["trd_env"] == trd_env
                and (not order_id or o["order_id"] == str(order_id))]
        return RET_OK, pd.DataFrame(rows, columns=ORDER_COLUMNS)

    def history_order_list_query(self, status_filter_list=(), code="", start="", end="", trd_env="REAL",
                                 acc_id=0, acc_index=0, order_market="N/A"):
        self.venue._record("history_order_list_query", start=start, end=end, trd_env=trd_env)
        down = self._down() or self.venue._query_error("history_order_list_query") or \
            self.venue._check_env(trd_env, acc_id)
        if down:
            return down
        rows = [self.venue._visible(o) for o in self.venue.orders.values() if o["trd_env"] == trd_env
                and start <= o["create_time"][:10] <= end]
        return RET_OK, pd.DataFrame(rows, columns=ORDER_COLUMNS)

    def deal_list_query(self, code="", trd_env="REAL", acc_id=0, acc_index=0, refresh_cache=False,
                        deal_market="N/A"):
        self.venue._record("deal_list_query", trd_env=trd_env, refresh_cache=refresh_cache)
        down = self._down() or self.venue._deals_error() or self.venue._check_env(trd_env, acc_id)
        if down:
            return down
        rows = [d for d in self.venue.deals if d["trd_env"] == trd_env]
        return RET_OK, pd.DataFrame(rows, columns=DEAL_COLUMNS)

    def history_deal_list_query(self, code="", start="", end="", trd_env="REAL", acc_id=0, acc_index=0,
                                deal_market="N/A"):
        self.venue._record("history_deal_list_query", start=start, end=end, trd_env=trd_env)
        down = self._down() or self.venue._deals_error() or self.venue._check_env(trd_env, acc_id)
        if down:
            return down
        rows = [d for d in self.venue.deals if d["trd_env"] == trd_env and start <= d["create_time"][:10] <= end]
        return RET_OK, pd.DataFrame(rows, columns=DEAL_COLUMNS)


class FakeQuoteContext(_ContextBase):
    """OpenQuoteContext: subscriptions, the quota query, and K-line / order-book pushes. `server`
    is what OpenD holds for this connection (lost when the connection drops); `record` is the
    SDK's own list, which it re-subscribes after a reconnect."""
    _handler_bases = QUOTE_HANDLER_BASES

    def __init__(self, host="127.0.0.1", port=11111, is_encrypt=None, is_async_connect=False):
        self._init(host, port)
        self.server: Dict[Tuple[str, str], int] = {}     # (code, subtype) -> subscribed at (ns)
        self.record: set = set()
        self.venue.quote_contexts.append(self)
        self.on_api_socket_reconnected()

    def close(self):
        self.closed = True
        self.server.clear()

    def on_api_socket_reconnected(self):
        if self.venue.sdk_resubscribes:
            for key in sorted(self.record):
                self.server[key] = self.venue.clock()
        return RET_OK, ""

    def subscribe(self, code_list, subtype_list, is_first_push=True, subscribe_push=True,
                  is_detailed_orderbook=False, extended_time=False, session="N/A"):
        self.venue._record("subscribe", codes=list(code_list), subtypes=list(subtype_list), first_push=is_first_push)
        down = self._down() or self.venue._query_error("subscribe")
        if down:
            return down
        keys = [(str(c), str(s)) for c in code_list for s in subtype_list]
        new = [k for k in keys if k not in self.server]
        if len(new) > self.venue.remaining_quota():
            return RET_ERROR, (f"Subscription quota is insufficient: {len(new)} more needed, "
                               f"{self.venue.remaining_quota()} remain of {self.venue.quota_total}")
        for key in new:
            self.server[key] = self.venue.clock()
        self.record.update(keys)
        if is_first_push:
            for key in keys:
                self.venue._first_push(self, key)
        return RET_OK, None

    def unsubscribe(self, code_list, subtype_list, unsubscribe_all=False):
        self.venue._record("unsubscribe", codes=list(code_list), subtypes=list(subtype_list))
        down = self._down()
        if down:
            return down
        keys = [(str(c), str(s)) for c in code_list for s in subtype_list]
        young = [k for k in keys if k in self.server and self.venue.clock() - self.server[k] < 60 * 10**9]
        if young:
            return RET_ERROR, f"Unsubscribe failed: a subscription can only be cancelled after one minute ({young})"
        for key in keys:
            self.server.pop(key, None)
            self.record.discard(key)
        return RET_OK, None

    def query_subscription(self, is_all_conn=True):
        self.venue._record("query_subscription")
        down = self._down()
        if down:
            return down
        total = self.venue.used_quota()
        sub_list: Dict[str, List[str]] = {}
        for code, subtype in sorted(self.server):
            sub_list.setdefault(subtype, []).append(code)
        return RET_OK, {"total_used": total, "own_used": len(self.server),
                        "remain": self.venue.quota_total - total, "sub_list": sub_list}


# ── OpenD, broker and exchange; the test's driver ───────────────────────────────────────────────

class FakeMoomoo:
    """
    The venue side of the fake, and the driver (tests/support/venue_driver.VenueDriver): accept,
    fill (partial fills by repeated calls), reject, cancel_ack, refuse_cancel, repeat_last_push,
    disconnect, reconnect, last_order_id, submitted. Quote helpers: push_kline, push_order_book.

    Knobs: deals_supported=False answers deal queries as some paper accounts do ('not supported');
    deal_push=False sends no deal pushes; deal_first=False pushes the order update before the deal;
    push_enabled=False loses every push (only polling sees what happened); lag_status(id) makes
    queries return the order as it is now until a cancel of it is attempted (the US paper lag);
    fail_next_place(msg, placed) makes the next place_order fail (with the order created anyway if
    `placed`, as after a timeout once the request was sent); accept_during_place() pushes a new
    order's acceptance before place_order returns; sdk_resubscribes=False stops the
    quote contexts re-subscribing from their own record after a reconnect; quota_total and
    quota_used_elsewhere set the subscription quota.
    """

    def __init__(self, market: str = "US", clock: Optional[FakeClock] = None, deals_supported: bool = True,
                 deal_push: bool = True, deal_first: bool = True, real_account: bool = False,
                 quota_total: int = 100, quota_used_elsewhere: int = 0, sdk_resubscribes: bool = True):
        self.market = market.upper()
        self.timezone, self.currency = MARKETS[self.market]
        self.clock = clock or FakeClock()
        self.deals_supported = deals_supported
        self.deal_push = deal_push
        self.deal_first = deal_first
        self.push_enabled = True
        self.sdk_resubscribes = sdk_resubscribes
        self.quota_total = quota_total
        self.quota_used_elsewhere = quota_used_elsewhere
        self.opend_up = True
        self.connected = True
        self.accounts = [_account(1001, "SIMULATE", self.market)]
        if real_account:
            self.accounts.append(_account(2001, "REAL", self.market))
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.deals: List[Dict[str, Any]] = []
        self.trade_contexts: List[FakeTradeContext] = []
        self.quote_contexts: List[FakeQuoteContext] = []
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.timers: List[FakeTimer] = []
        self.query_errors: Dict[str, str] = {}
        self._submitted: List[Dict[str, Any]] = []
        self._refuse: Dict[str, str] = {}
        self._lagging: Dict[str, Dict[str, Any]] = {}
        self._cancel_requested: set = set()
        self._place_failures: List[Tuple[str, bool]] = []
        self._accept_during_place = False
        self._last_push: Optional[Tuple[str, Any]] = None
        self._last_kline: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._last_book: Dict[str, Dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._order_ids = itertools.count(1)
        self._deal_ids = itertools.count(1)
        self.sdk = self._make_sdk()

    def _make_sdk(self) -> SimpleNamespace:
        venue = self
        trade_ctx = type("OpenSecTradeContext", (FakeTradeContext,), {"venue": venue})
        quote_ctx = type("OpenQuoteContext", (FakeQuoteContext,), {"venue": venue})
        return SimpleNamespace(
            RET_OK=RET_OK, RET_ERROR=RET_ERROR, OpenSecTradeContext=trade_ctx, OpenQuoteContext=quote_ctx,
            TradeOrderHandlerBase=TradeOrderHandlerBase, TradeDealHandlerBase=TradeDealHandlerBase,
            CurKlineHandlerBase=CurKlineHandlerBase, OrderBookHandlerBase=OrderBookHandlerBase,
            RspHandlerBase=RspHandlerBase, TrdEnv=TrdEnv, TrdSide=TrdSide, OrderType=OrderType,
            TimeInForce=TimeInForce, ModifyOrderOp=ModifyOrderOp, TrdMarket=TrdMarket, SecurityFirm=SecurityFirm,
            SubType=SubType, KLType=KLType, OrderStatus=OrderStatus, DealStatus=DealStatus)

    # ── injection points for the connector ───────────────────────────

    def probe(self, host: str, port: int, timeout: float) -> str:
        return "" if self.opend_up else f"OpenD is not reachable at {host}:{port} (Connection refused)"

    def timer(self, delay: float, fn: Callable[[], None]) -> FakeTimer:
        t = FakeTimer(self.clock() + int(delay * 1e9), fn)
        self.timers.append(t)
        return t

    def run_timers(self) -> int:
        """Fire the timers that are due; returns how many fired."""
        fired = 0
        for t in list(self.timers):
            if not t.cancelled and not t.fired and t.due_ns <= self.clock():
                t.fired = True
                t.fn()
                fired += 1
        return fired

    # ── VenueDriver ──────────────────────────────────────────────────

    def last_order_id(self) -> str:
        ids = [s["venue_order_id"] for s in self._submitted if s["venue_order_id"]]
        if not ids:
            raise AssertionError("no order has reached the fake venue")
        return ids[-1]

    def submitted(self) -> List[dict]:
        return [dict(s) for s in self._submitted]

    def accept(self, venue_order_id: str) -> None:
        o = self._order(venue_order_id)
        self._set(o, order_status="SUBMITTED")
        self._push_order(o)

    def fill(self, venue_order_id: str, quantity: float, price: float, trade_id: Optional[str] = None) -> None:
        o = self._order(venue_order_id)
        remaining = o["qty"] - o["dealt_qty"]
        if quantity <= 0 or quantity > remaining + 1e-9:
            raise ValueError(f"cannot fill {quantity} of order {venue_order_id}: {remaining} remain")
        dealt = o["dealt_qty"] + quantity
        avg = (o["dealt_avg_price"] * o["dealt_qty"] + price * quantity) / dealt
        self._set(o, dealt_qty=dealt, dealt_avg_price=avg,
                  order_status="FILLED_ALL" if dealt >= o["qty"] - 1e-9 else "FILLED_PART")
        deal = {"code": o["code"], "stock_name": o["stock_name"], "deal_market": self.market,
                "deal_id": str(trade_id) if trade_id is not None else str(9_000_000 + next(self._deal_ids)),
                "order_id": o["order_id"], "qty": float(quantity), "price": float(price), "trd_side": o["trd_side"],
                "create_time": self._local_now(), "counter_broker_id": 0, "counter_broker_name": "",
                "status": "OK", "jp_acc_type": "N/A", "trd_env": o["trd_env"], "trd_market": self.market}
        self.deals.append(deal)
        if self.deal_first:
            self._push_deal(deal)
            self._push_order(o)
        else:
            self._push_order(o)
            self._push_deal(deal)

    def reject(self, venue_order_id: str, reason: str = "") -> None:
        o = self._order(venue_order_id)
        self._set(o, order_status="FAILED", last_err_msg=reason or "Order rejected by the exchange")
        self._push_order(o)

    def cancel_ack(self, venue_order_id: str) -> None:
        o = self._order(venue_order_id)
        if o["order_status"] in FINAL:
            return   # nothing left to cancel
        self._set(o, order_status="CANCELLED_PART" if o["dealt_qty"] > 0 else "CANCELLED_ALL")
        self._push_order(o)

    def refuse_cancel(self, venue_order_id: str, reason: str = "") -> None:
        self._refuse[str(venue_order_id)] = reason or "This order cannot be cancelled: it has been filled"

    def repeat_last_push(self) -> None:
        if self._last_push is not None:
            self._deliver_trade(*self._last_push)

    def disconnect(self) -> None:
        """OpenD drops every connection: pushes are lost, queries fail, quote subscriptions go."""
        self.connected = False
        for ctx in self.trade_contexts + self.quote_contexts:
            if not ctx.closed:
                if isinstance(ctx, FakeQuoteContext):
                    ctx.server.clear()
                ctx.on_disconnect(ctx.conn_id, "RemoteClose", "connection reset by OpenD")

    def reconnect(self) -> None:
        self.connected = True
        for ctx in self.trade_contexts + self.quote_contexts:
            if not ctx.closed:
                ctx.on_api_socket_reconnected()

    # ── other driver helpers ─────────────────────────────────────────

    def set_status(self, venue_order_id: str, status: str, last_err_msg: str = "", push: bool = True) -> None:
        """Any OpenD order status (DISABLED, TIMEOUT, ...), pushed unless `push` is False."""
        o = self._order(venue_order_id)
        self._set(o, order_status=status, **({"last_err_msg": last_err_msg} if last_err_msg else {}))
        if push:
            self._push_order(o)

    def lag_status(self, venue_order_id: str) -> None:
        """From now on queries show the order as it is now, until a cancel of it is attempted."""
        self._lagging[str(venue_order_id)] = dict(self._order(venue_order_id))

    def fail_next_place(self, message: str, placed: bool = False) -> None:
        self._place_failures.append((message, placed))

    def accept_during_place(self, on: bool = True) -> None:
        """Push each new order's SUBMITTED update before place_order returns (OpenD's pushes run on
        their own thread and can overtake the reply)."""
        self._accept_during_place = on

    def push_order_raw(self, row: Any) -> None:
        self._deliver_trade("order", row)

    def push_deal_raw(self, row: Any) -> None:
        self._deliver_trade("deal", row)

    def cancel_requested(self, venue_order_id: str) -> bool:
        return str(venue_order_id) in self._cancel_requested

    def methods_called(self) -> List[str]:
        return [m for m, _ in self.calls]

    def push_kline(self, code: str, k_type: str, time_key: str, open_: float, high: float, low: float,
                   close: float, volume: float) -> None:
        row = {"code": code, "name": code, "time_key": time_key, "open": open_, "close": close, "high": high,
               "low": low, "volume": volume, "turnover": close * volume, "k_type": k_type, "last_close": open_,
               "pe_ratio": 0.0, "turnover_rate": 0.0}
        self._last_kline[(code, k_type)] = row
        for ctx in self._quote_receivers((code, k_type)):
            handler = ctx.handlers.get(CurKlineHandlerBase)
            if handler is not None:
                handler.on_recv_rsp([dict(row)])

    def push_order_book(self, code: str, bid: Optional[float], ask: Optional[float], bid_size: float = 100.0,
                        ask_size: float = 100.0) -> None:
        book = {"code": code, "name": code, "svr_recv_time_bid": "", "svr_recv_time_ask": "",
                "Bid": [(bid, bid_size, 1, {})] if bid is not None else [],
                "Ask": [(ask, ask_size, 1, {})] if ask is not None else []}
        self._last_book[code] = book
        for ctx in self._quote_receivers((code, "ORDER_BOOK")):
            handler = ctx.handlers.get(OrderBookHandlerBase)
            if handler is not None:
                handler.on_recv_rsp(dict(book))

    def used_quota(self) -> int:
        return self.quota_used_elsewhere + sum(len(c.server) for c in self.quote_contexts if not c.closed)

    def remaining_quota(self) -> int:
        return self.quota_total - self.used_quota()

    # ── internals ────────────────────────────────────────────────────

    def _record(self, method: str, **kwargs) -> None:
        self.calls.append((method, kwargs))

    def _local_now(self) -> str:
        return pd.Timestamp(self.clock(), unit="ns", tz="UTC").tz_convert(self.timezone).strftime(
            "%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _order(self, venue_order_id: str) -> Dict[str, Any]:
        try:
            return self.orders[str(venue_order_id)]
        except KeyError:
            raise AssertionError(f"the fake venue has no order {venue_order_id!r}") from None

    def _set(self, o: Dict[str, Any], **changes) -> None:
        o.update(changes, updated_time=self._local_now())

    def _visible(self, o: Dict[str, Any]) -> Dict[str, Any]:
        return dict(self._lagging.get(o["order_id"], o))

    def _query_error(self, method: str) -> Optional[Tuple[int, str]]:
        msg = self.query_errors.get(method)
        return (RET_ERROR, msg) if msg else None

    def _deals_error(self) -> Optional[Tuple[int, str]]:
        if not self.deals_supported:
            return RET_ERROR, "This function is not supported in paper trading"
        return self._query_error("deal_list_query")

    def _check_env(self, trd_env: str, acc_id: int) -> Optional[Tuple[int, str]]:
        ok = [a for a in self.accounts if a["trd_env"] == trd_env and (not acc_id or a["acc_id"] == int(acc_id))]
        if not ok:
            return RET_ERROR, f"No available {trd_env} account (acc_id {acc_id}) with {self.market} market authority"
        return None

    def _place(self, ctx, price, qty, code, trd_side, order_type, trd_env, acc_id, remark, time_in_force):
        self._record("place_order", price=price, qty=qty, code=code, trd_side=trd_side, order_type=order_type,
                     trd_env=trd_env, acc_id=acc_id, remark=remark, time_in_force=time_in_force)
        down = ctx._down()
        if down:   # never left the client: the venue did not receive it
            return down
        entry = {"symbol": code, "side": trd_side, "quantity": float(qty), "price": float(price),
                 "order_type": "MARKET" if order_type == "MARKET" else "LIMIT", "venue_order_type": order_type,
                 "client_order_id": remark, "remark": remark, "time_in_force": time_in_force, "trd_env": trd_env,
                 "venue_order_id": None}
        self._submitted.append(entry)
        down = self._check_env(trd_env, acc_id)
        if down:
            return down
        if remark is not None and len(str(remark).encode("utf-8")) > 64:
            return RET_ERROR, "remark must be at most 64 bytes in UTF-8"
        failure = self._place_failures.pop(0) if self._place_failures else None
        if failure is not None and not failure[1]:
            return RET_ERROR, failure[0]
        oid = str(5_000_000_000 + next(self._order_ids))
        now = self._local_now()
        o = {"code": code, "stock_name": code, "order_market": self.market, "trd_side": trd_side,
             "order_type": order_type, "order_status": "SUBMITTING", "order_id": oid, "qty": float(qty),
             "price": float(price), "create_time": now, "updated_time": now, "dealt_qty": 0.0,
             "dealt_avg_price": 0.0, "last_err_msg": "", "remark": remark or "", "time_in_force": time_in_force,
             "fill_outside_rth": False, "session": "N/A", "aux_price": 0.0, "trail_type": "N/A",
             "trail_value": 0.0, "trail_spread": 0.0, "currency": self.currency, "jp_acc_type": "N/A",
             "trd_env": trd_env, "trd_market": self.market}
        self.orders[oid] = o
        entry["venue_order_id"] = oid
        if self._accept_during_place:
            self.accept(oid)
        if failure is not None:   # sent, but the reply was lost
            return RET_ERROR, failure[0]
        return RET_OK, pd.DataFrame([{k: o.get(k) for k in ORDER_COLUMNS}], columns=ORDER_COLUMNS)

    def _modify(self, ctx, op, order_id, qty, price, trd_env, acc_id):
        self._record("modify_order", op=op, order_id=order_id, trd_env=trd_env)
        down = ctx._down() or self._check_env(trd_env, acc_id)
        if down:
            return down
        if op != ModifyOrderOp.CANCEL:
            return RET_ERROR, f"the fake venue only cancels (asked {op})"
        if order_id not in self.orders:
            return RET_ERROR, f"order {order_id} does not exist"
        self._lagging.pop(order_id, None)   # after a cancel attempt queries show the truth
        if order_id in self._refuse:
            return RET_ERROR, self._refuse.pop(order_id)
        status = self.orders[order_id]["order_status"]
        if status in FINAL:
            return RET_ERROR, f"This order cannot be cancelled: {status}"
        self._cancel_requested.add(order_id)
        return RET_OK, pd.DataFrame([{"trd_env": trd_env, "order_id": order_id}])

    def _push_order(self, o: Dict[str, Any]) -> None:
        self._deliver_trade("order", {k: o.get(k) for k in ORDER_PUSH_COLUMNS})

    def _push_deal(self, d: Dict[str, Any]) -> None:
        if self.deal_push and self.deals_supported:   # no deal queries, no deal pushes either
            self._deliver_trade("deal", {k: d.get(k) for k in DEAL_PUSH_COLUMNS})

    def _deliver_trade(self, kind: str, payload: Any) -> None:
        self._last_push = (kind, payload)
        if not (self.connected and self.push_enabled):
            return
        base = TradeOrderHandlerBase if kind == "order" else TradeDealHandlerBase
        for ctx in self.trade_contexts:
            handler = ctx.handlers.get(base)
            if handler is not None and not ctx.closed:
                handler.on_recv_rsp(dict(payload) if isinstance(payload, dict) else payload)

    def _quote_receivers(self, key: Tuple[str, str]) -> List[FakeQuoteContext]:
        if not self.connected:
            return []
        return [c for c in self.quote_contexts if not c.closed and key in c.server]

    def _first_push(self, ctx: FakeQuoteContext, key: Tuple[str, str]) -> None:
        code, subtype = key
        if subtype == "ORDER_BOOK":
            book = self._last_book.get(code)
            handler = ctx.handlers.get(OrderBookHandlerBase)
            if book is not None and handler is not None:
                handler.on_recv_rsp(dict(book))
        else:
            row = self._last_kline.get(key)
            handler = ctx.handlers.get(CurKlineHandlerBase)
            if row is not None and handler is not None:
                handler.on_recv_rsp([dict(row)])


def _account(acc_id: int, trd_env: str, market: str) -> Dict[str, Any]:
    return {"acc_id": acc_id, "trd_env": trd_env, "acc_type": "MARGIN", "uni_card_num": "N/A", "card_num": "N/A",
            "security_firm": "FUTUSECURITIES", "sim_acc_type": "STOCK" if trd_env == "SIMULATE" else "N/A",
            "trdmarket_auth": [market], "acc_status": "ACTIVE", "acc_role": "MASTER", "jp_acc_type": "N/A"}


# ── factories ───────────────────────────────────────────────────────────────────────────────────

def make_moomoo_connector(venue: FakeMoomoo, **config):
    """A MoomooConnector whose live side (execution client, streams) talks to `venue`."""
    from midfreq.data.adapters.moomoo.connector import MoomooConnector
    return MoomooConnector({"min_request_interval": 0.0, **config}, sdk=venue.sdk, probe=venue.probe,
                           timer=venue.timer, now_ns=venue.clock)


def make_moomoo_execution_client(*, venue: Optional[FakeMoomoo] = None, connect: bool = True, **options):
    """
    (MoomooExecutionClient, FakeMoomoo) for the execution contract suite: a real client on a fake
    OpenD, connected. `options` go to MoomooConnector.execution_client (market, trading_env, ...);
    the safety-net poll thread is off unless poll_interval is given, so events come only from the
    driver's pushes, synchronously (polls can be run with client.poll_now()).
    """
    venue = venue or FakeMoomoo(market=str(options.get("market", "US")))
    options.setdefault("poll_interval", 0)
    client = make_moomoo_connector(venue).execution_client(**options)
    if connect:
        client.connect()
    return client, venue
