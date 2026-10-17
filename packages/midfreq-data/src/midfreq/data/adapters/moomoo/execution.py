"""
Moomoo (OpenD) execution client: orders, cancels and fills for one trade account (paper or real)
of one market ('US', 'HK'), through the SDK's OpenSecTradeContext.

What it carries over from midfreq.live.order_gateway, the original engine's gateway:

- Paper unless told otherwise. The trading environment comes from the `trading_env` option, else
  the `moomoo` connection (config/connections.yaml, or MIDFREQ_MOOMOO_TRADING_ENV), else
  `simulate`. Only `real` (or `live`) selects TrdEnv.REAL; any other word is refused rather than
  guessed, so a typo can never select real money.
- Orders go at the price given: a LIMIT order is OpenD's NORMAL order at `order.price`, a MARKET
  order is OpenD's MARKET order. The client never invents or adjusts a price (the original
  gateway's 1% marketable-limit slip is the OMS's policy now, not the venue client's).
- A cancel OpenD refuses (usually because the order has already filled) is followed by a fresh
  query of the order and its deals: on 5 October the US paper fill showed up only in the query
  made after the refused cancel.
- Status queries can lag the broker (US paper read SUBMITTED for 30 s while the broker stamped
  the fill 16-18 s in), so an order's outcome is never decided from a status alone: fills come
  from executions (deals, one OrderFilled per deal id); a cancel is final only when OpenD says
  CANCELLED_*; an OrderCanceled for a partly filled order waits for its fills; and a poll of
  orders and deals (refresh_cache=True) runs behind the pushes as a safety net.

Events. Push handlers run on the SDK's callback thread and the poll on the client's own thread;
both only update the client's bookkeeping (under one lock) and call `_emit`. A repeated push, or a
poll that sees what a push already reported, emits nothing: OrderAccepted at most once per order,
OrderFilled at most once per deal id, one terminal event per order.

Matching orders back. Our ClientOrderId goes in OpenD's `remark` (at most 64 bytes; a longer id is
shortened with a hash, see `remark_for`), so an order can be recognised in pushes that arrive
before place_order returns, in the poll after a place_order whose outcome is unknown, and in
`open_orders()` / `fills()` for reconciliation.

Commission. OpenD's deal pushes and deal lists carry no fee, so every OrderFilled has commission 0
and tags["commission_unknown"] = True; the PositionKeeper estimates it from the venue's fee model.

Deals on paper. If OpenD answers that deal queries are not supported in this environment, fills
are derived from the order's dealt quantity and average price instead (tags["synthetic_fill"]).
The same happens, after `fill_grace` seconds, when the order's status reports more filled than
the deals received so far (a deal push that never arrives).

Testing. `sdk` (the moomoo module, or tests/support/fake_moomoo's fake) and `probe` (the TCP
check of OpenD's port) are injected through the connector; nothing here imports the SDK until
`connect()`.
"""
from __future__ import annotations

import hashlib
import logging
import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, List, Optional, Set, Tuple

import pandas as pd

from midfreq.core.common.timestamps import ns_to_timestamp, to_utc_timestamp
from midfreq.core.model.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import AccountId, ClientOrderId, InstrumentId, StrategyId, TradeId
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import ConnectorError, ExecutionClient, FillReport, OrderReport
from midfreq.data.adapters.moomoo.account import OpenDTransport, _num, _positive, _side
from midfreq.data.connection_settings import connection_settings

if TYPE_CHECKING:
    from midfreq.data.adapters.moomoo.connector import MoomooConnector

log = logging.getLogger(__name__)

RET_OK = 0                      # moomoo.RET_OK
REMARK_BYTES = 64               # OpenD refuses a longer remark
_EPS = 1e-9

# Accepted spellings of trading_env (connections.yaml, MIDFREQ_MOOMOO_TRADING_ENV, or the option).
# The values are the SDK's TrdEnv constants, which are these plain strings.
TRADING_ENVS = {"simulate": "SIMULATE", "paper": "SIMULATE", "real": "REAL", "live": "REAL"}

# ── Moomoo order statuses ─────────────────────────────────────────────────────────────────────
# What each of the SDK's OrderStatus values (moomoo.common.constant.OrderStatus, SDK 10.2) means
# for our order. Fills never come from a status: they come from deals (or, where OpenD has no
# deals, from the dealt quantity, see the module docstring).
PENDING = "pending"     # not at the exchange yet: no event
WORKING = "working"     # live at the venue: OrderAccepted (once)
FILLED = "filled"       # completely filled: OrderAccepted (once); the OrderFilled events come from deals
CANCELED = "canceled"   # OrderCanceled, after the fills of any part that executed
REJECTED = "rejected"   # OrderRejected (an order that never worked)
EXPIRED = "expired"     # stopped working without a cancel: OrderCanceled if it had been accepted, else OrderRejected
BUSTED = "busted"       # the venue reversed the order's fills: OrderCanceled (no 'fill busted' event exists)
UNKNOWN = "unknown"     # outcome unknown: no event, keep polling

ORDER_STATUS: Dict[str, str] = {
    "N/A": UNKNOWN,                 # NONE: unknown status
    "UNSUBMITTED": PENDING,         # not submitted
    "WAITING_SUBMIT": PENDING,      # queued by Moomoo (e.g. placed before the open)
    "SUBMITTING": PENDING,          # on its way to the exchange
    "SUBMIT_FAILED": REJECTED,      # submission failed
    "TIMEOUT": UNKNOWN,             # processing timed out, result unknown
    "SUBMITTED": WORKING,           # at the exchange, waiting to fill
    "FILLED_PART": WORKING,         # partly filled, the rest working
    "FILLED_ALL": FILLED,           # completely filled
    "CANCELLING_PART": WORKING,     # cancel in progress, part filled
    "CANCELLING_ALL": WORKING,      # cancel in progress
    "CANCELLED_PART": CANCELED,     # part filled, the rest cancelled
    "CANCELLED_ALL": CANCELED,      # cancelled, nothing filled
    "FAILED": REJECTED,             # refused by the server
    "DISABLED": EXPIRED,            # disabled (no longer working; OpenD can re-enable it, we never do)
    "DELETED": EXPIRED,             # deleted (only orders without fills can be)
    "FILL_CANCELLED": BUSTED,       # executed, then the fills were cancelled by the venue
}
OPEN_KINDS = frozenset({PENDING, WORKING, UNKNOWN})

# place_order errors that mean the request never left this process (the order cannot exist) ...
_NOT_SENT = ("connect timeout", "connection closed")
# ... and those that mean it may have reached OpenD (outcome unknown until we find it by remark).
_MAYBE_SENT = ("timeout", "disconnect", "sendfail", "超时", "断开")
# OpenD's answer when a query is not available in this environment (deals on some paper accounts).
_UNSUPPORTED = ("not support", "unsupported", "不支持")


def remark_for(client_order_id: Any) -> str:
    """The `remark` sent with an order: the ClientOrderId itself when it fits OpenD's 64 bytes,
    else its head, '~' and 12 hex digits of its SHA-1, so it stays unique and recognisable."""
    text = str(client_order_id)
    raw = text.encode("utf-8")
    if len(raw) <= REMARK_BYTES:
        return text
    digest = hashlib.sha1(raw).hexdigest()[:12]
    head = raw[: REMARK_BYTES - len(digest) - 1].decode("utf-8", errors="ignore")
    return f"{head}~{digest}"


# ── helpers shared with the streaming module ──────────────────────────────────────────────────

def moomoo_sdk() -> Any:
    """The moomoo SDK module, imported on first use (importing midfreq.data must not import it)."""
    try:
        import moomoo
    except ImportError:
        raise ConnectorError("the moomoo SDK is not installed (the `moomoo` extra)") from None
    return moomoo


def tcp_probe(host: str, port: int, timeout: float) -> str:
    """'' if OpenD's port accepts a connection within `timeout` seconds, else why not."""
    return OpenDTransport().probe(host, port, timeout)


def open_bounded(factory: Callable[[], Any], timeout: float, what: str) -> Any:
    """
    Create an SDK context on a worker thread and give up after `timeout` seconds. The SDK's
    context constructors retry a refused or stalled connection every 6 s forever and never raise,
    so the port is probed first and the constructor is not waited on beyond `timeout`; a context
    that arrives after we gave up is closed at once.
    """
    box: Dict[str, Any] = {}
    box_lock = threading.Lock()

    def work():
        try:
            ctx = factory()
        except BaseException as exc:   # handed to the caller below
            with box_lock:
                box["error"] = exc
            return
        with box_lock:
            late = box.get("abandoned", False)
            if not late:
                box["ctx"] = ctx
        if late:
            try:
                ctx.close()
            except Exception:
                pass

    worker = threading.Thread(target=work, name=f"moomoo-open-{what[:24]}", daemon=True)
    worker.start()
    worker.join(timeout)
    with box_lock:
        if "ctx" in box:
            return box["ctx"]
        if "error" in box:
            exc = box["error"]
            raise ConnectorError(f"cannot open {what}: {exc}") from exc
        box["abandoned"] = True
    raise ConnectorError(f"OpenD accepted the connection but did not complete the handshake for {what} "
                         f"within {timeout:g} s")


def local_time_ns(value: Any, tz: str) -> Optional[int]:
    """An OpenD wall-clock string ('2026-10-15 10:30:17.123', the market's own clock) as UTC ns,
    or None if it cannot be read. In the hour a DST change repeats, the summer-time reading wins."""
    if value is None or (isinstance(value, str) and value.strip() in ("", "N/A")):
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
    return int(ts.tz_convert("UTC").value)


def _text(value: Any) -> str:
    s = "" if value is None else str(value).strip()
    return "" if s in ("N/A", "nan", "None") else s


def _records(data: Any) -> List[Dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, pd.DataFrame):
        return data.to_dict("records")
    if isinstance(data, dict):
        return [data]
    return list(data)


# ── bookkeeping ────────────────────────────────────────────────────────────────────────────────

@dataclass
class _Tracked:
    """What the client knows and has said about one order. Copied from the Order at submit time,
    so SDK threads never read the OMS's Order object."""
    client_order_id: ClientOrderId
    strategy_id: StrategyId
    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    code: str
    remark: str
    currency: str
    venue_order_id: Optional[str] = None
    status: str = ""                        # OpenD's latest status word
    last_err: str = ""                      # OpenD's latest last_err_msg
    accepted: bool = False                  # OrderAccepted emitted
    closed: bool = False                    # terminal event emitted, or completely filled
    ts_closed: Optional[int] = None
    filled_qty: float = 0.0                 # sum of the OrderFilled events emitted
    filled_notional: float = 0.0
    venue_dealt_qty: float = 0.0            # the most OpenD has reported filled (dealt_qty)
    venue_dealt_avg: Optional[float] = None
    deal_ids: Set[str] = field(default_factory=set)
    deal_qty_seen: float = 0.0              # sum over every deal id seen (booked or not)
    capped: bool = False                    # after a synthetic fill or an adoption: deals capped by quantity
    gap_since: Optional[int] = None         # ns since OpenD reported more filled than we emitted
    pending_terminal: Optional[Tuple[str, str]] = None   # (kind, reason) waiting for fills
    cancel_requested: bool = False
    unconfirmed_since: Optional[int] = None # place_order's outcome unknown since (ns)
    unconfirmed_reason: str = ""


# ── the client ─────────────────────────────────────────────────────────────────────────────────

class MoomooExecutionClient(ExecutionClient):
    """
    Orders for one Moomoo trade account of one market through OpenD.

    Options (all optional): market ('US' or 'HK', default 'US'); trading_env ('simulate'/'paper'
    or 'real'/'live'; default from the `moomoo` connection, else simulate); security_firm (default
    from the connection, else FUTUSECURITIES); acc_id (0 = OpenD's first account of that
    environment); poll_interval (seconds between safety-net polls while orders are open, default
    10; 0 turns the thread off); fill_grace (seconds a reported fill may wait for its deal before
    it is booked from the order's status, default 5); unconfirmed_grace (seconds a place_order with
    an unknown outcome is looked for by its remark before it counts as rejected, default 30);
    probe_timeout, connect_timeout, query_timeout (seconds).
    """

    DEFAULTS: Dict[str, Any] = dict(
        market="US", trading_env=None, security_firm=None, acc_id=0, poll_interval=10.0,
        fill_grace=5.0, unconfirmed_grace=30.0, probe_timeout=1.5, connect_timeout=10.0,
        query_timeout=10.0,
    )

    def __init__(self, connector: "MoomooConnector", options: Optional[Dict[str, Any]] = None,
                 sdk: Any = None, probe: Optional[Callable[[str, int, float], str]] = None):
        super().__init__(connector, options)
        unknown = set(self.options) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"unknown moomoo execution options {sorted(unknown)}; allowed: {sorted(self.DEFAULTS)}")
        opts = {**self.DEFAULTS, **self.options}
        self.market = str(opts["market"]).upper()
        specs = [s for s in connector.venues.served_by(connector.name)
                 if str(s.connector_options(connector.name).get("market", "")).upper() == self.market]
        if not specs:
            raise ConnectorError(f"no venue in config/venues.json has moomoo market {self.market!r}")
        self.timezone = specs[0].timezone
        self.currency = specs[0].currency
        conn = connection_settings("moomoo", {"trading_env": "simulate", "security_firm": "FUTUSECURITIES"},
                                   trading_env=opts["trading_env"], security_firm=opts["security_firm"])
        env_word = str(conn["trading_env"] or "simulate").strip().lower()
        if env_word not in TRADING_ENVS:
            raise ValueError(f"moomoo trading_env must be one of {sorted(TRADING_ENVS)}, got {conn['trading_env']!r}")
        self.trd_env = TRADING_ENVS[env_word]
        self.security_firm = str(conn["security_firm"]).upper()
        self.acc_id = int(opts["acc_id"] or 0)
        self.host, self.port = connector.config.host, int(connector.config.port)
        self.poll_interval = float(opts["poll_interval"] or 0.0)
        self.fill_grace_ns = int(float(opts["fill_grace"]) * 1e9)
        self.unconfirmed_grace_ns = int(float(opts["unconfirmed_grace"]) * 1e9)
        self.probe_timeout = float(opts["probe_timeout"])
        self.connect_timeout = float(opts["connect_timeout"])
        self.query_timeout = float(opts["query_timeout"])

        self._sdk = sdk
        self._probe = probe or tcp_probe
        self._now_ns: Callable[[], int] = connector._now_ns
        self._ctx: Any = None
        self._lock = threading.RLock()          # all bookkeeping; events are emitted under it, in order
        self._poll_lock = threading.Lock()      # one poll at a time
        self._by_cid: Dict[ClientOrderId, _Tracked] = {}
        self._by_vid: Dict[str, _Tracked] = {}
        self._by_remark: Dict[str, _Tracked] = {}
        self._orphans: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}   # pushes for order ids not yet known
        self._orphan_order: Deque[str] = deque()
        self._deals_supported: Optional[bool] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._poller: Optional[threading.Thread] = None

    def __repr__(self) -> str:
        return f"MoomooExecutionClient({self.market} {self.environment} via {self.host}:{self.port})"

    # ── identity ─────────────────────────────────────────────────────

    @property
    def environment(self) -> str:
        return "live" if self.trd_env == "REAL" else "paper"

    @property
    def account_id(self) -> AccountId:
        suffix = f"-{self.acc_id}" if self.acc_id else ""
        return AccountId(f"MOOMOO-{self.market}-{'LIVE' if self.trd_env == 'REAL' else 'PAPER'}{suffix}")

    @property
    def is_connected(self) -> bool:
        return self._ctx is not None

    # ── session ──────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._ctx is not None:
            return
        sdk = self._sdk if self._sdk is not None else moomoo_sdk()
        self._sdk = sdk
        reason = self._probe(self.host, self.port, self.probe_timeout)
        if reason:
            raise ConnectorError(reason)
        ctx_class = _trade_context_class(sdk)
        ctx = open_bounded(
            lambda: ctx_class(self, filter_trdmarket=self.market, host=self.host, port=self.port,
                              security_firm=self.security_firm),
            self.connect_timeout, f"a {self.market} trade context on OpenD at {self.host}:{self.port}")
        try:
            ctx.set_sync_query_connect_timeout(self.query_timeout)   # a query while OpenD is away fails, not waits
            order_handler, deal_handler = _trade_handlers(sdk, self)
            ctx.set_handler(order_handler)
            ctx.set_handler(deal_handler)
            self._check_account(ctx)
        except BaseException:
            ctx.close()
            raise
        with self._lock:
            self._ctx = ctx
        self._deals_supported = self._probe_deals(ctx)
        if self.trd_env == "REAL":
            log.warning("moomoo %s execution client connected to the REAL-MONEY account (trading_env=real)", self.market)
        else:
            log.info("moomoo %s execution client connected (paper)", self.market)
        if self.poll_interval > 0:
            self._stop.clear()
            self._poller = threading.Thread(target=self._poll_loop, name=f"moomoo-exec-poll-{self.market}",
                                            daemon=True)
            self._poller.start()

    def disconnect(self) -> None:
        self._stop.set()
        self._wake.set()
        poller, self._poller = self._poller, None
        if poller is not None and poller is not threading.current_thread():
            poller.join(timeout=max(1.0, self.query_timeout))
        with self._lock:
            ctx, self._ctx = self._ctx, None
        if ctx is not None:
            ctx._mf_client = None   # no more callbacks into this client
            ctx.close()

    def _check_account(self, ctx) -> None:
        """Refuse to start without an account of the configured environment (and acc_id)."""
        ret, data = ctx.get_acc_list()
        if ret != RET_OK:
            raise ConnectorError(f"moomoo account list failed: {data}")
        rows = [r for r in _records(data) if str(r.get("trd_env", "")).upper() == self.trd_env]
        if self.acc_id:
            rows = [r for r in rows if int(r.get("acc_id") or 0) == self.acc_id]
        if not rows:
            which = f"account {self.acc_id}" if self.acc_id else "account"
            raise ConnectorError(f"OpenD has no {self.trd_env} {which} for the {self.market} market "
                                 f"(security firm {self.security_firm})")

    def _probe_deals(self, ctx) -> Optional[bool]:
        """Whether OpenD answers deal queries in this environment (some paper accounts do not)."""
        ret, data = self._call(ctx, "deal_list_query", trd_env=self.trd_env, acc_id=self.acc_id)
        if ret == RET_OK:
            return True
        if any(m in str(data).lower() for m in _UNSUPPORTED):
            log.warning("moomoo %s %s: deal queries are not supported (%s); fills will be taken from order "
                        "status", self.market, self.environment, data)
            return False
        return None

    # ── orders ───────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> None:
        try:
            code, price, order_type, tif = self._validate(order)
        except ValueError as exc:
            self._emit(self._event(OrderRejected, self._new_tracked(order, "", ""), reason=f"refused by the client: {exc}"))
            return
        ctx = self._ctx
        if ctx is None:
            self._emit(self._event(OrderRejected, self._new_tracked(order, code, ""),
                                   reason="moomoo execution client is not connected"))
            return
        remark = remark_for(order.client_order_id)
        with self._lock:
            if order.client_order_id in self._by_cid:
                raise ConnectorError(f"order {order.client_order_id} was already submitted to moomoo")
            t = self._new_tracked(order, code, remark)
            self._by_cid[t.client_order_id] = t
            self._by_remark[remark] = t
        try:
            ret, data = ctx.place_order(price=price, qty=float(order.quantity), code=code,
                                        trd_side=order.side.value, order_type=order_type,
                                        trd_env=self.trd_env, acc_id=self.acc_id, remark=remark,
                                        time_in_force=tif)
        except Exception as exc:   # the request may or may not have reached OpenD
            ret, data = None, f"{type(exc).__name__}: {exc}"
        rows = _records(data) if ret == RET_OK else []
        vid = _text(rows[0].get("order_id")) if rows else ""
        if vid:
            order.tags["venue_order_id"] = vid
            with self._lock:
                self._bind(t, vid)
                self._apply_order_row(t, rows[0])
            return
        message = str(data) if ret != RET_OK else "place_order returned no order id"
        with self._lock:
            if t.venue_order_id is not None:   # a push with our remark already told us it exists
                order.tags["venue_order_id"] = t.venue_order_id
                log.warning("moomoo place_order for %s reported %r, but the order exists as %s",
                            t.client_order_id, message, t.venue_order_id)
                return
            low = message.lower()
            if ret != RET_OK and any(m in low for m in _NOT_SENT):
                self._close(t, OrderRejected, f"not sent: {message}")
            elif ret is None or ret == RET_OK or any(m in low for m in _MAYBE_SENT):
                t.unconfirmed_since = self._now_ns()
                t.unconfirmed_reason = message
                log.warning("moomoo place_order for %s: %s; looking for it by remark %r", t.client_order_id,
                            message, remark)
            else:
                self._close(t, OrderRejected, message)
        if t.unconfirmed_since is not None:
            self._wake.set()

    def _validate(self, order: Order) -> Tuple[str, float, str, str]:
        code = self.connector.to_native(order.instrument_id)
        if not code.upper().startswith(self.market + "."):
            raise ValueError(f"{order.instrument_id} is not on the {self.market} market this client trades")
        if order.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"unsupported side {order.side}")
        qty = float(order.quantity)
        if not math.isfinite(qty) or qty <= 0 or abs(qty - round(qty)) > _EPS:
            raise ValueError(f"quantity {order.quantity!r} is not a whole positive number of shares")
        inst = self.connector.instrument_provider.find(order.instrument_id)
        if inst is not None and not inst.is_valid_qty(qty):
            raise ValueError(f"quantity {qty:g} is not a whole number of board lots ({inst.lot_size:g})")
        if order.order_type is OrderType.LIMIT:
            if order.price is None or not math.isfinite(float(order.price)) or float(order.price) <= 0:
                raise ValueError(f"limit order without a valid price ({order.price!r})")
            price, order_type = float(order.price), "NORMAL"
        elif order.order_type is OrderType.MARKET:
            price, order_type = 0.0, "MARKET"   # OpenD ignores the price of a market order
        else:
            raise ValueError(f"unsupported order type {order.order_type}")
        if order.time_in_force is TimeInForce.DAY:
            tif = "DAY"
        elif order.time_in_force is TimeInForce.GTC:
            tif = "GTC"
        else:
            raise ValueError(f"moomoo has no {order.time_in_force.value} orders (only DAY and GTC)")
        return code, price, order_type, tif

    def cancel_order(self, order: Order) -> None:
        ctx = self._ctx
        if ctx is None:
            raise ConnectorError("moomoo execution client is not connected")
        t = self._lookup(order, adopt=True)
        if t is None:
            log.warning("cannot cancel %s: moomoo's order id is not known", order.client_order_id)
            return
        with self._lock:
            if t.closed:
                return
            if t.venue_order_id is None:   # placed with an unknown outcome: cancel once it is found
                t.cancel_requested = True
                return
            vid = t.venue_order_id
        try:
            ret, data = ctx.modify_order("CANCEL", vid, 0, 0, trd_env=self.trd_env, acc_id=self.acc_id)
        except Exception as exc:
            ret, data = None, f"{type(exc).__name__}: {exc}"
        if ret == RET_OK:
            with self._lock:
                t.cancel_requested = True
            return
        # Refused, typically because it has already filled; the status may lag, so look again
        # (order and deals, uncached) and report what OpenD says now.
        log.warning("moomoo refused to cancel %s (%s): %s; querying the order", t.client_order_id, vid, data)
        self._refresh(t)

    def query_order(self, order: Order) -> Optional[OrderReport]:
        ctx = self._require_ctx()
        with self._lock:
            t = self._by_cid.get(order.client_order_id)
        vid = (t.venue_order_id if t is not None else None) or _text(order.tags.get("venue_order_id")) or None
        if vid is None:
            remark = remark_for(order.client_order_id)
            rows = [r for r in self._query_orders(ctx) if _text(r.get("remark")) == remark]
        else:
            rows = self._query_orders(ctx, order_id=vid)
            if not rows:   # not today's: look back a week
                rows = [r for r in self._query_history_orders(ctx, days=7) if _text(r.get("order_id")) == vid]
        if not rows:
            return None
        row = rows[-1]
        if t is not None:
            with self._lock:
                if t.venue_order_id is None:
                    self._bind(t, _text(row.get("order_id")))
                self._apply_order_row(t, row)
        return self._report(row)

    def open_orders(self) -> List[OrderReport]:
        ctx = self._require_ctx()
        rows = self._query_orders(ctx)
        return [self._report(r) for r in rows
                if ORDER_STATUS.get(_text(r.get("order_status")), UNKNOWN) in OPEN_KINDS]

    def fills(self, since: pd.Timestamp) -> List[FillReport]:
        ctx = self._require_ctx()
        since_ts = to_utc_timestamp(since)
        now = ns_to_timestamp(self._now_ns())
        today = now.tz_convert(self.timezone).strftime("%Y-%m-%d")
        start = since_ts.tz_convert(self.timezone).strftime("%Y-%m-%d")
        orders = {_text(r.get("order_id")): r for r in self._query_orders(ctx)}
        if start < today:
            for r in self._query_history_orders(ctx, start=start, end=today):
                orders.setdefault(_text(r.get("order_id")), r)
        if self._deals_supported is False:
            reports = [self._fill_from_order(r) for r in orders.values()
                       if (_num(r.get("dealt_qty")) or 0) > 0]
        else:
            ret, data = self._call(ctx, "deal_list_query", trd_env=self.trd_env, acc_id=self.acc_id,
                                   refresh_cache=True)
            if ret != RET_OK:
                if any(m in str(data).lower() for m in _UNSUPPORTED):
                    self._deals_supported = False
                    return self.fills(since)
                raise ConnectorError(f"moomoo deal list ({self.market}) failed: {data}")
            deals = {_text(r.get("deal_id")): r for r in _records(data)}
            if start < today:
                ret, data = self._call(ctx, "history_deal_list_query", start=start, end=today,
                                       trd_env=self.trd_env, acc_id=self.acc_id)
                if ret != RET_OK:
                    raise ConnectorError(f"moomoo deal history ({self.market}) failed: {data}")
                for r in _records(data):
                    deals.setdefault(_text(r.get("deal_id")), r)
            reports = [self._fill_report(r, orders.get(_text(r.get("order_id")))) for r in deals.values()
                       if _text(r.get("status")).upper() in ("", "OK")]
        reports = [r for r in reports if r is not None and r.ts >= since_ts]
        return sorted(reports, key=lambda r: (r.ts, r.trade_id))

    # ── polling (the safety net behind the pushes) ───────────────────

    def poll_now(self) -> None:
        """One safety-net pass: today's orders and deals, uncached, applied to every order still
        open here (deduplicated against the pushes). Also books fills reported only by status
        after `fill_grace`, and resolves place_orders with an unknown outcome."""
        ctx = self._ctx
        if ctx is None:
            return
        with self._poll_lock:
            with self._lock:
                active = [t for t in self._by_cid.values() if not t.closed]
                if not active:
                    self._prune()
                    return
            try:
                orders = self._query_orders(ctx)
            except ConnectorError as exc:
                log.warning("moomoo %s order poll: %s", self.market, exc)
                orders = None
            deals = self._query_deals(ctx) if self._deals_supported is not False else None
            now = self._now_ns()
            to_cancel: List[_Tracked] = []
            with self._lock:
                if orders is not None:
                    for row in orders:
                        t = self._match(row)
                        if t is None:
                            continue
                        if t.venue_order_id is None:
                            self._bind(t, _text(row.get("order_id")))
                            t.unconfirmed_since = None
                            log.info("moomoo order %s found by remark as %s", t.client_order_id, t.venue_order_id)
                            if t.cancel_requested:
                                to_cancel.append(t)
                        self._apply_order_row(t, row)
                if deals is not None:
                    for row in deals:
                        t = self._by_vid.get(_text(row.get("order_id")))
                        if t is not None:
                            self._apply_deal_row(t, row)
                for t in active:
                    if (t.unconfirmed_since is not None and t.venue_order_id is None and orders is not None
                            and now - t.unconfirmed_since >= self.unconfirmed_grace_ns):
                        self._close(t, OrderRejected, f"no order with remark {t.remark!r} reached moomoo "
                                                      f"({t.unconfirmed_reason})")
                        continue
                    self._check_gap(t, now)
                self._prune()
            for t in to_cancel:
                self._cancel_found(t)

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.poll_interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.poll_now()
            except Exception:   # never let the safety net die
                log.exception("moomoo %s order poll failed", self.market)

    def _refresh(self, t: _Tracked) -> None:
        """Look at one order again, uncached: its row and its deals."""
        ctx = self._ctx
        if ctx is None or t.venue_order_id is None:
            return
        try:
            rows = self._query_orders(ctx, order_id=t.venue_order_id)
        except ConnectorError as exc:
            log.warning("moomoo order %s could not be queried: %s", t.venue_order_id, exc)
            rows = []
        deals = self._query_deals(ctx) if self._deals_supported is not False else None
        with self._lock:
            for row in deals or []:
                if _text(row.get("order_id")) == t.venue_order_id:
                    self._apply_deal_row(t, row)
            for row in rows:
                self._apply_order_row(t, row)

    def _cancel_found(self, t: _Tracked) -> None:
        ctx = self._ctx
        if ctx is None or t.closed:
            return
        ret, data = self._call(ctx, "modify_order", "CANCEL", t.venue_order_id, 0, 0,
                               trd_env=self.trd_env, acc_id=self.acc_id)
        if ret != RET_OK:
            log.warning("moomoo refused to cancel %s (%s): %s", t.client_order_id, t.venue_order_id, data)

    # ── SDK callbacks (SDK threads) ──────────────────────────────────

    def _on_order_push(self, data: Any) -> None:
        with self._lock:
            for row in _records(data):
                t = self._match(row)
                if t is None:
                    self._park(_text(row.get("order_id")), "order", row)
                    continue
                if t.venue_order_id is None:
                    self._bind(t, _text(row.get("order_id")))
                self._apply_order_row(t, row)

    def _on_deal_push(self, data: Any) -> None:
        with self._lock:
            for row in _records(data):
                vid = _text(row.get("order_id"))
                t = self._by_vid.get(vid)
                if t is None:
                    self._park(vid, "deal", row)
                    continue
                self._apply_deal_row(t, row)

    def _on_reconnected(self, ctx) -> None:
        """OpenD is back (the SDK has re-subscribed the account's pushes). Pushes sent while we were
        away are lost, so catch up at once: wake the poller, or poll here if there is none."""
        log.warning("moomoo %s trade connection restored; catching up on orders", self.market)
        if self._poller is not None and self._poller.is_alive():
            self._wake.set()
            return
        try:
            self.poll_now()
        except Exception:
            log.exception("moomoo %s catch-up poll after reconnect failed", self.market)

    def _on_disconnected(self, ctx, detail: Any) -> None:
        log.warning("moomoo %s trade connection lost (%s); the SDK reconnects every 6 s", self.market, detail)

    # ── applying what OpenD says (always under self._lock) ───────────

    def _match(self, row: Dict[str, Any]) -> Optional[_Tracked]:
        t = self._by_vid.get(_text(row.get("order_id")))
        if t is None:
            cand = self._by_remark.get(_text(row.get("remark")))
            if cand is not None and cand.venue_order_id is None:
                t = cand
        return t

    def _bind(self, t: _Tracked, vid: str) -> None:
        if not vid:
            return
        t.venue_order_id = vid
        self._by_vid[vid] = t
        for kind, row in self._orphans.pop(vid, []):
            if kind == "deal":
                self._apply_deal_row(t, row)
            else:
                self._apply_order_row(t, row)

    def _park(self, vid: str, kind: str, row: Dict[str, Any]) -> None:
        """A push for an order id not known (yet): ours before place_order returned, or another
        session's order on the same account. Kept briefly, replayed if the id becomes ours."""
        if not vid:
            return
        self._orphans.setdefault(vid, []).append((kind, row))
        self._orphan_order.append(vid)
        while len(self._orphan_order) > 1000:
            old = self._orphan_order.popleft()
            rows = self._orphans.get(old)
            if rows:
                rows.pop(0)
                if not rows:
                    del self._orphans[old]

    def _apply_order_row(self, t: _Tracked, row: Dict[str, Any]) -> None:
        status = _text(row.get("order_status"))
        kind = ORDER_STATUS.get(status)
        if kind is None:
            log.warning("moomoo order %s has status %r, which this client does not know; treated as unknown",
                        t.venue_order_id, status)
            kind = UNKNOWN
        if status:
            t.status = status
        err = _text(row.get("last_err_msg"))
        if err:
            t.last_err = err
        dealt = _num(row.get("dealt_qty")) or 0.0
        if dealt > t.venue_dealt_qty + _EPS:
            t.venue_dealt_qty = dealt
            t.venue_dealt_avg = _positive(row.get("dealt_avg_price")) or t.venue_dealt_avg
        elif abs(dealt - t.venue_dealt_qty) <= _EPS and dealt > 0:
            t.venue_dealt_avg = _positive(row.get("dealt_avg_price")) or t.venue_dealt_avg
        if kind == FILLED and t.venue_dealt_qty < t.quantity - _EPS:
            t.venue_dealt_qty = t.quantity   # FILLED_ALL whatever a lagging dealt_qty says
        if t.closed:
            if kind == BUSTED:
                log.error("moomoo reversed the fills of order %s (%s): reconcile positions", t.venue_order_id, status)
            return
        reason = f"{status}: {t.last_err}" if t.last_err else status
        if kind in (WORKING, FILLED, CANCELED):
            self._accept(t)
        if kind == CANCELED:
            self._terminal(t, "canceled", reason)
        elif kind == REJECTED:
            self._terminal(t, "canceled" if t.accepted or t.venue_dealt_qty > 0 else "rejected", reason)
        elif kind == EXPIRED:
            self._terminal(t, "canceled" if t.accepted else "rejected", reason)
        elif kind == BUSTED:
            log.error("moomoo reversed the fills of order %s (%s): reconcile positions", t.venue_order_id, status)
            self._close(t, OrderCanceled, f"{reason} (the venue cancelled this order's fills)")
            return
        self._check_gap(t, self._now_ns())

    def _apply_deal_row(self, t: _Tracked, row: Dict[str, Any]) -> None:
        deal_id = _text(row.get("deal_id"))
        if not deal_id or deal_id in t.deal_ids:
            return   # a repeated push, or a poll that sees what a push reported
        t.deal_ids.add(deal_id)
        dstatus = _text(row.get("status")).upper()
        if dstatus not in ("", "OK"):
            log.error("moomoo deal %s of order %s is %s: the venue changed or cancelled an execution; "
                      "reconcile positions", deal_id, t.venue_order_id, dstatus)
            return
        qty, px = _num(row.get("qty")), _num(row.get("price"))
        if qty is None or qty <= 0 or px is None:
            log.error("moomoo deal %s of order %s cannot be read: qty=%r price=%r", deal_id, t.venue_order_id,
                      row.get("qty"), row.get("price"))
            return
        t.deal_qty_seen += qty
        if t.capped and t.filled_qty + qty > max(t.venue_dealt_qty, t.deal_qty_seen) + _EPS:
            log.info("moomoo deal %s of order %s is already booked (from status or before adoption)",
                     deal_id, t.venue_order_id)
            return
        if t.filled_qty + qty > t.quantity + _EPS:
            log.error("moomoo deal %s takes order %s past its quantity (%g + %g > %g)", deal_id,
                      t.venue_order_id, t.filled_qty, qty, t.quantity)
        if t.filled_qty + qty > t.venue_dealt_qty:
            t.venue_dealt_qty = t.filled_qty + qty
        self._accept(t)   # an execution implies the order was accepted
        ts = local_time_ns(row.get("create_time"), self.timezone) or self._now_ns()
        self._fill(t, deal_id, qty, px, ts, {})
        self._check_gap(t, self._now_ns())

    def _check_gap(self, t: _Tracked, now: int) -> None:
        """OpenD reports more filled than the deals we have: wait `fill_grace` for the deal, then
        book the difference from the order's dealt quantity and average price."""
        missing = t.venue_dealt_qty - t.filled_qty
        if missing <= _EPS:
            t.gap_since = None
            self._flush_terminal(t)
            return
        if t.gap_since is None:
            t.gap_since = now
        if self._deals_supported is False or now - t.gap_since >= self.fill_grace_ns:
            self._synthesize(t, missing, now)
            self._flush_terminal(t)

    def _synthesize(self, t: _Tracked, qty: float, now: int) -> None:
        if t.venue_dealt_avg is None:
            log.error("moomoo order %s reports %g filled but no average price; fill not booked",
                      t.venue_order_id, t.venue_dealt_qty)
            return
        px = (t.venue_dealt_avg * t.venue_dealt_qty - t.filled_notional) / qty
        if not math.isfinite(px) or px <= 0:
            px = t.venue_dealt_avg
        t.capped = True
        self._accept(t)
        self._fill(t, f"{t.venue_order_id}#{t.venue_dealt_qty:g}", qty, px, now,
                   {"synthetic_fill": True, "source": "order_status"})
        t.gap_since = None

    def _accept(self, t: _Tracked) -> None:
        if not t.accepted and not t.closed:
            t.accepted = True
            self._emit(self._event(OrderAccepted, t))

    def _fill(self, t: _Tracked, trade_id: str, qty: float, px: float, ts: int, tags: Dict[str, Any]) -> None:
        self._emit(self._event(
            OrderFilled, t, ts_event=ts, trade_id=TradeId(trade_id), side=t.side, last_qty=float(qty),
            last_px=float(px), commission=0.0, currency=t.currency,
            tags={"venue_order_id": t.venue_order_id, "commission_unknown": True,
                  "ts_received": self._now_ns(), **tags}))
        t.filled_qty += qty
        t.filled_notional += qty * px
        if t.filled_qty >= t.quantity - _EPS and not t.closed:
            t.closed = True
            t.ts_closed = self._now_ns()

    def _terminal(self, t: _Tracked, kind: str, reason: str) -> None:
        if t.closed:
            return
        t.pending_terminal = (kind, reason)
        if t.venue_dealt_qty - t.filled_qty <= _EPS:
            self._flush_terminal(t)

    def _flush_terminal(self, t: _Tracked) -> None:
        if t.pending_terminal is None or t.closed or t.venue_dealt_qty - t.filled_qty > _EPS:
            return
        kind, reason = t.pending_terminal
        t.pending_terminal = None
        self._close(t, OrderCanceled if kind == "canceled" else OrderRejected, reason)

    def _close(self, t: _Tracked, event_cls, reason: str) -> None:
        if t.closed:
            return
        t.closed = True
        t.ts_closed = self._now_ns()
        t.pending_terminal = None
        self._emit(self._event(event_cls, t, reason=reason))

    def _prune(self, keep_s: float = 6 * 3600) -> None:
        cutoff = self._now_ns() - int(keep_s * 1e9)
        for cid, t in list(self._by_cid.items()):
            if t.closed and t.ts_closed is not None and t.ts_closed < cutoff:
                del self._by_cid[cid]
                self._by_remark.pop(t.remark, None)
                if t.venue_order_id:
                    self._by_vid.pop(t.venue_order_id, None)

    # ── tracking ─────────────────────────────────────────────────────

    def _new_tracked(self, order: Order, code: str, remark: str) -> _Tracked:
        try:
            currency = self.connector.venue_spec(order.instrument_id.venue).currency
        except Exception:
            currency = self.currency
        return _Tracked(order.client_order_id, order.strategy_id, order.instrument_id, order.side,
                        float(order.quantity), code, remark, currency)

    def _lookup(self, order: Order, adopt: bool) -> Optional[_Tracked]:
        """Our record of `order`; with `adopt`, an order this client did not submit (a previous
        process's, known by order.tags['venue_order_id']) is taken on, so its outcome is reported."""
        with self._lock:
            t = self._by_cid.get(order.client_order_id)
        if t is not None or not adopt:
            return t
        vid = _text(order.tags.get("venue_order_id"))
        if not vid:
            return None
        t = self._new_tracked(order, self.connector.to_native(order.instrument_id), remark_for(order.client_order_id))
        t.accepted = order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
        t.filled_qty = float(order.filled_qty or 0.0)
        t.filled_notional = t.filled_qty * float(order.avg_px or 0.0)
        t.venue_dealt_qty = t.filled_qty
        # Deals already booked by the OMS: the earliest ones, up to what it has filled.
        deals = self._query_deals(self._ctx) if self._deals_supported is not False and self._ctx is not None else None
        if deals is None:
            t.capped = True
        else:
            mine = sorted((r for r in deals if _text(r.get("order_id")) == vid),
                          key=lambda r: (_text(r.get("create_time")), _text(r.get("deal_id"))))
            covered = 0.0
            for r in mine:
                q = _num(r.get("qty")) or 0.0
                if covered + q <= t.filled_qty + _EPS:
                    covered += q
                    t.deal_ids.add(_text(r.get("deal_id")))
                    t.deal_qty_seen += q
            if covered < t.filled_qty - _EPS:
                t.capped = True
        with self._lock:
            existing = self._by_cid.get(order.client_order_id)
            if existing is not None:
                return existing
            self._by_cid[t.client_order_id] = t
            self._by_remark[t.remark] = t
            self._bind(t, vid)
        log.info("moomoo order %s (%s) adopted from a previous session", t.client_order_id, vid)
        return t

    # ── queries ──────────────────────────────────────────────────────

    def _require_ctx(self):
        if self._ctx is None:
            raise ConnectorError("moomoo execution client is not connected")
        return self._ctx

    def _call(self, ctx, method: str, *args, **kwargs) -> Tuple[Any, Any]:
        try:
            return getattr(ctx, method)(*args, **kwargs)
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _query_orders(self, ctx, order_id: str = "") -> List[Dict[str, Any]]:
        """Today's orders (or one of them), uncached: the status OpenD caches can lag."""
        ret, data = self._call(ctx, "order_list_query", order_id=order_id, trd_env=self.trd_env,
                               acc_id=self.acc_id, refresh_cache=True)
        if ret != RET_OK:
            raise ConnectorError(f"moomoo order list ({self.market}) failed: {data}")
        rows = _records(data)
        if order_id:
            rows = [r for r in rows if _text(r.get("order_id")) == order_id]
        return rows

    def _query_history_orders(self, ctx, days: int = 7, start: str = "", end: str = "") -> List[Dict[str, Any]]:
        if not start:
            local = ns_to_timestamp(self._now_ns()).tz_convert(self.timezone)
            start = (local - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
            end = local.strftime("%Y-%m-%d")
        ret, data = self._call(ctx, "history_order_list_query", start=start, end=end, trd_env=self.trd_env,
                               acc_id=self.acc_id)
        if ret != RET_OK:
            raise ConnectorError(f"moomoo order history ({self.market}) failed: {data}")
        return _records(data)

    def _query_deals(self, ctx) -> Optional[List[Dict[str, Any]]]:
        ret, data = self._call(ctx, "deal_list_query", trd_env=self.trd_env, acc_id=self.acc_id,
                               refresh_cache=True)
        if ret == RET_OK:
            self._deals_supported = True
            return _records(data)
        if any(m in str(data).lower() for m in _UNSUPPORTED):
            self._deals_supported = False
        else:
            log.warning("moomoo %s deal list failed: %s", self.market, data)
        return None

    # ── reports ──────────────────────────────────────────────────────

    def _utc(self, value: Any) -> Optional[pd.Timestamp]:
        ns = local_time_ns(value, self.timezone)
        return None if ns is None else ns_to_timestamp(ns)

    def _client_order_id(self, remark: str) -> Optional[str]:
        if not remark:
            return None
        with self._lock:
            t = self._by_remark.get(remark)
        return str(t.client_order_id) if t is not None else remark

    def _report(self, row: Dict[str, Any]) -> OrderReport:
        filled = _num(row.get("dealt_qty")) or 0.0
        return OrderReport(
            order_id=_text(row.get("order_id")), symbol=_text(row.get("code")), side=_side(row.get("trd_side")),
            quantity=_num(row.get("qty")) or 0.0, filled_quantity=filled,
            price=_positive(row.get("price")),
            average_fill_price=_positive(row.get("dealt_avg_price")) if filled > 0 else None,
            status=_text(row.get("order_status")),
            created=self._utc(row.get("create_time")) or ns_to_timestamp(self._now_ns()),
            updated=self._utc(row.get("updated_time")),
            client_order_id=self._client_order_id(_text(row.get("remark"))),
            remark=_text(row.get("last_err_msg")) or None)

    def _instrument(self, code: str) -> Optional[str]:
        try:
            return str(self.connector.from_native(code))
        except (ValueError, KeyError):
            return None

    def _fill_report(self, row: Dict[str, Any], order_row: Optional[Dict[str, Any]]) -> Optional[FillReport]:
        ts = self._utc(row.get("create_time"))
        qty, px = _num(row.get("qty")), _num(row.get("price"))
        if ts is None or qty is None or px is None:
            return None
        code = _text(row.get("code"))
        return FillReport(
            trade_id=_text(row.get("deal_id")), order_id=_text(row.get("order_id")), symbol=code,
            side=_side(row.get("trd_side")), quantity=qty, price=px, ts=ts, commission=None,
            currency=self.currency,
            client_order_id=self._client_order_id(_text(order_row.get("remark"))) if order_row else None,
            instrument_id=self._instrument(code))

    def _fill_from_order(self, row: Dict[str, Any]) -> Optional[FillReport]:
        """Where OpenD has no deals: one fill per order, its dealt quantity at its average price,
        with the same trade id a status-derived OrderFilled for the whole quantity carries."""
        ts = self._utc(row.get("updated_time")) or self._utc(row.get("create_time"))
        qty, px = _num(row.get("dealt_qty")), _positive(row.get("dealt_avg_price"))
        if ts is None or not qty or px is None:
            return None
        vid, code = _text(row.get("order_id")), _text(row.get("code"))
        return FillReport(
            trade_id=f"{vid}#{qty:g}", order_id=vid, symbol=code, side=_side(row.get("trd_side")),
            quantity=qty, price=px, ts=ts, commission=None, currency=self.currency,
            client_order_id=self._client_order_id(_text(row.get("remark"))), instrument_id=self._instrument(code))

    # ── events ───────────────────────────────────────────────────────

    def _event(self, cls, t: _Tracked, ts_event: Optional[int] = None, **fields):
        return cls(client_order_id=t.client_order_id, strategy_id=t.strategy_id, instrument_id=t.instrument_id,
                   ts_event=ts_event if ts_event is not None else self._now_ns(), **fields)


# ── SDK subclasses (built per SDK module, so a fake SDK works the same way) ────────────────────

def _trade_context_class(sdk: Any):
    """OpenSecTradeContext with two hooks: OpenD connection lost, and connection restored (after
    the SDK's own re-subscription of the account's pushes)."""

    class _TradeContext(sdk.OpenSecTradeContext):
        def __init__(self, client: MoomooExecutionClient, **kwargs):
            self._mf_client = client
            self._mf_ready = False            # the constructor's first connect is not a reconnect
            super().__init__(**kwargs)
            self._mf_ready = True

        def on_api_socket_reconnected(self):
            result = super().on_api_socket_reconnected()
            client = getattr(self, "_mf_client", None)
            if client is not None and getattr(self, "_mf_ready", False):
                client._on_reconnected(self)
            return result

        def on_disconnect(self, *args, **kwargs):
            result = super().on_disconnect(*args, **kwargs)
            client = getattr(self, "_mf_client", None)
            if client is not None:
                client._on_disconnected(self, args[1:] if len(args) > 1 else args)
            return result

    return _TradeContext


def _trade_handlers(sdk: Any, client: MoomooExecutionClient):
    """Push handlers for order updates and deals. They run on the SDK's callback thread and only
    hand the parsed rows to the client, which updates its bookkeeping and emits events."""
    ok = sdk.RET_OK

    class _OrderHandler(sdk.TradeOrderHandlerBase):
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != ok:
                log.warning("moomoo order push error: %s", data)
                return ret, data
            try:
                client._on_order_push(data)
            except Exception:
                log.exception("moomoo order push could not be applied")
            return ret, data

    class _DealHandler(sdk.TradeDealHandlerBase):
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != ok:
                log.warning("moomoo deal push error: %s", data)
                return ret, data
            try:
                client._on_deal_push(data)
            except Exception:
                log.exception("moomoo deal push could not be applied")
            return ret, data

    return _OrderHandler(), _DealHandler()
