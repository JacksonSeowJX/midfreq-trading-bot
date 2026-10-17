"""
Order routing to a crypto exchange through ccxt (OKX first): the ccxt connector's ExecutionClient.

    client = CcxtConnector().execution_client(exchange="okx")      # paper: OKX demo trading
    client.set_event_handler(queue.put)
    client.connect()
    client.submit_order(order)                                    # returns at once

Environment. `paper` (the default) is the exchange's sandbox or demo-trading environment, through
ccxt's set_sandbox_mode(True) (OKX: demo trading, with a demo-trading key). An exchange that ccxt
gives no sandbox cannot paper trade here: the client refuses to start rather than trade for real.
The environment is the option `environment`, else `trading_env` of the `ccxt` entry in
config/connections.yaml, else paper; `live` is accepted only when connections.yaml (or
MIDFREQ_CCXT_TRADING_ENV) says `trading_env: live`, and anything else asking for live raises
ConnectorError.

Credentials come only from the environment (or the untracked .env) through get_secret, under the
variable names of option `secrets`; by default <EXCHANGE>_<PAPER|LIVE>_API_KEY, ..._API_SECRET and,
for exchanges that need one (OKX), ..._API_PASSPHRASE. Separate names for paper and live keep a
live key out of a paper session.

Orders. The amount is truncated and the price rounded to the market's precision; an order whose
quantity the rounding changes keeps the requested one in tags['requested_qty']. Our ClientOrderId
goes in the exchange's client-id parameter (OKX clOrdId, Binance newClientOrderId, ccxt's unified
clientOrderId elsewhere), made to fit the exchange's rule (OKX: at most 32 letters and digits) by
dropping other characters and appending a hash of the original, so it stays unique and can be
recomputed. The id sent is tags['venue_client_order_id'], the exchange's order id
tags['venue_order_id'].

Updates. With ccxt.pro (part of ccxt >= 4) and an exchange that has watchOrders, the client runs
watch_orders and watch_my_trades on a background asyncio thread; otherwise it polls fetch_order
and fetch_my_trades for its open orders on that thread (option updates='auto'|'watch'|'poll').
After a stream error, when the stream resumes, and every `resync_interval` seconds in any case,
it re-reads its open orders over REST, so fills missed while disconnected are emitted then. The
status handler (`set_status_handler`) hears 'disconnected' at the first stream error (or failed
poll) and 'reconnected' once the stream (or polling) works again and the open orders have been
re-read. A cancel the exchange refuses (anything but a network error) is followed by a re-read
of the order and then OrderCancelRejected. A trade that carries no fee is an OrderFilled with
commission 0 and tags['commission_unknown']; a fee is reported in the currency the exchange
charged it in (OKX: the base asset on a spot buy), which the PositionKeeper converts.

ccxt order status -> event (each at most once per order):
    open      -> OrderAccepted
    closed    -> none by itself: the fills come from trades, one OrderFilled per trade id
    canceled  -> OrderCanceled(reason)
    expired   -> OrderCanceled(reason 'expired: ...')
    rejected  -> OrderRejected(reason)
Before a terminal event, a fill the order's `filled` shows but no trade has brought yet is fetched
(fetch_order_trades or fetch_my_trades), so the fill always precedes the cancel of the remainder.
On an exchange with no trade data at all, fills come from the change in the order's `filled`.
A create_order the exchange refuses (InsufficientFunds, InvalidOrder, ...) is OrderRejected with
ccxt's error; one whose reply is lost (a NetworkError) is looked up by its client order id until
the exchange confirms it, and rejected if it is still unknown after `unconfirmed_timeout` seconds.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from midfreq.core.common.timestamps import ms_to_ns, ns_to_ms, ns_to_timestamp, to_utc_timestamp
from midfreq.core.connections import get_secret as _env_secret
from midfreq.core.model.enums import LiquiditySide, OrderSide, OrderStatus, OrderType, TimeInForce
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderCancelRejected, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import AccountId, InstrumentId, TradeId, as_instrument_id
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import ConnectorError, ExecutionClient, FillReport, OrderReport
from midfreq.data.adapters.ccxt.account import ccxt_exchange, credential_field
from midfreq.data.connection_settings import connection_settings

try:  # optional dependency
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover - exercised only where ccxt is absent
    ccxt = None
try:  # ccxt.pro (websocket streams) ships inside ccxt >= 4
    import ccxt.pro as ccxt_pro  # type: ignore
except ImportError:  # pragma: no cover
    ccxt_pro = None

log = logging.getLogger(__name__)

PAPER, LIVE = "paper", "live"
_ENVIRONMENT_WORDS = {"paper": PAPER, "sandbox": PAPER, "testnet": PAPER, "demo": PAPER, "simulate": PAPER,
                      "live": LIVE, "real": LIVE, "production": LIVE}

# exchange -> (its own client-id parameter, characters it refuses in one, longest id it takes)
CLIENT_ID_RULES: Dict[str, Tuple[str, str, int]] = {
    "okx": ("clOrdId", r"[^A-Za-z0-9]", 32),
    "binance": ("newClientOrderId", r"[^A-Za-z0-9._:/-]", 36),
}
DEFAULT_CLIENT_ID_RULE = ("clientOrderId", r"[^A-Za-z0-9_-]", 32)

# ccxt errors (matched by class name along the MRO, so fakes with the same names behave the same)
_TRANSIENT = frozenset({"NetworkError", "RequestTimeout", "ExchangeNotAvailable", "DDoSProtection",
                        "RateLimitExceeded", "OnMaintenance", "ConnectionError", "TimeoutError"})
TERMINAL_STATUSES = ("closed", "canceled", "cancelled", "expired", "rejected")

OPTIONS = {
    "exchange": "okx",              # ccxt exchange id
    "environment": None,            # paper | live (None: connections.yaml's ccxt.trading_env, else paper)
    "secrets": None,                # environment variables holding the key (default from exchange and environment)
    "exchange_options": None,       # extra ccxt constructor settings
    "updates": "auto",              # auto | watch | poll
    "poll_interval": 2.0,           # seconds between polls (updates='poll')
    "housekeeping_interval": 1.0,   # seconds between checks of unconfirmed orders and pending resyncs
    "resync_interval": 30.0,        # seconds between REST re-reads of open orders while streaming
    "reconnect_delay": 1.0,         # first wait after a stream error, doubled up to max_reconnect_delay
    "max_reconnect_delay": 30.0,
    "unconfirmed_timeout": 30.0,    # seconds before an order the exchange never confirmed is rejected
    "timeout_ms": 10_000,           # REST request timeout
    "account": None,                # AccountId (default '<EXCHANGE>-<ENVIRONMENT>')
    "symbols": (),                  # unified symbols, for exchanges that list trades per symbol only
    "trades_page_limit": 100,
}


def environment_word(value: Any) -> str:
    key = str(value or "").strip().lower()
    if key not in _ENVIRONMENT_WORDS:
        raise ValueError(f"unknown trading environment {value!r}; use paper or live")
    return _ENVIRONMENT_WORDS[key]


def resolve_environment(requested: Any, configured: Any) -> str:
    """The client's environment: `requested` (the option) if given, else `configured`
    (connections.yaml), else paper. Live only if connections.yaml says live."""
    conf = environment_word(configured or PAPER)
    env = environment_word(requested) if requested else conf
    if env == LIVE and conf != LIVE:
        raise ConnectorError("refusing to trade live through ccxt: live needs `trading_env: live` under `ccxt` "
                             "in config/connections.yaml (or MIDFREQ_CCXT_TRADING_ENV=live)")
    return env


def client_id_param(exchange_id: str) -> str:
    return CLIENT_ID_RULES.get(exchange_id, DEFAULT_CLIENT_ID_RULE)[0]


def venue_client_order_id(client_order_id: str, exchange_id: str) -> str:
    """Our ClientOrderId as the exchange accepts it. Unchanged where it already fits; otherwise the
    allowed characters plus 8 hex digits of a hash of the original, so two ids never collide and the
    same id always maps to the same value ('O-XSReversal-000001' -> 'OXSReversal000001<hash8>')."""
    _, refused, longest = CLIENT_ID_RULES.get(exchange_id, DEFAULT_CLIENT_ID_RULE)
    clean = re.sub(refused, "", client_order_id)
    if clean == client_order_id and 0 < len(clean) <= longest:
        return clean
    digest = hashlib.sha1(client_order_id.encode()).hexdigest()[:8]
    return clean[: longest - len(digest)] + digest


def error_is(exc: BaseException, *names: str) -> bool:
    return any(cls.__name__ in names for cls in type(exc).__mro__)


def is_transient(exc: BaseException) -> bool:
    """A network problem: the request may or may not have reached the exchange."""
    return error_is(exc, *_TRANSIENT)


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(value: Any) -> Optional[float]:
    out = _num(value)
    return out if out is not None and out > 0 else None


def _close_enough(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def order_reason(status: str, order: Dict[str, Any]) -> str:
    """The exchange's status word and whatever message it gave ('canceled: ...')."""
    info = order.get("info") if isinstance(order.get("info"), dict) else {}
    words = []
    for key in ("sMsg", "msg", "reason", "rejectReason", "cancelSourceReason"):
        value = info.get(key)
        if value not in (None, "") and str(value) not in words:
            words.append(str(value))
    if info.get("cancelSource") not in (None, ""):
        words.append(f"cancelSource={info['cancelSource']}")
    return f"{status}: {'; '.join(words)}" if words else status


@dataclass(eq=False)
class _Tracked:
    """What the client knows of one of its orders."""
    order: Order
    symbol: str
    venue_client_id: str
    venue_order_id: Optional[str] = None
    created_ms: Optional[int] = None    # the exchange's time of the order, once it gave one
    submitted_ns: int = 0
    accepted: bool = False
    terminal: bool = False          # no further status events (fills may still arrive)
    closed_seen: bool = False       # the exchange said 'closed'; terminal once the trades cover `filled`
    emitted_qty: float = 0.0        # quantity of the OrderFilled events emitted
    venue_filled: float = 0.0       # the largest `filled` the exchange reported
    venue_cost: float = 0.0         # filled x average at the last synthesized fill
    unconfirmed: Optional[str] = None   # why create_order's reply is missing
    cancel_pending: bool = False    # cancel asked while unconfirmed
    guessed: bool = False           # rejected because the exchange never confirmed it in time


class CcxtExecutionClient(ExecutionClient):
    """One account at one ccxt exchange. See the module docstring for the rules it follows."""

    def __init__(self, connector, options: Optional[Dict[str, Any]] = None, *,
                 exchange: Any = None, stream_exchange: Any = None,
                 exchange_factory: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                 stream_exchange_factory: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                 get_secret: Callable[[str], Optional[str]] = _env_secret,
                 now_ns: Callable[[], int] = time.time_ns):
        """`exchange` / `stream_exchange` are ready exchange objects (tests pass fakes); otherwise
        `exchange_factory(exchange_id, config)` builds the REST one (ccxt's class by default) and
        `stream_exchange_factory` the ccxt.pro one."""
        super().__init__(connector, options)
        unknown = set(self.options) - set(OPTIONS)
        if unknown:
            raise ValueError(f"unknown ccxt execution options {sorted(unknown)}; allowed: {sorted(OPTIONS)}")
        o = {**OPTIONS, **{k: v for k, v in self.options.items() if v is not None}}
        self.exchange_id = str(o["exchange"]).lower()
        configured = connection_settings("ccxt", {"trading_env": PAPER})["trading_env"]
        self._environment = resolve_environment(self.options.get("environment"), configured)
        self._updates = str(o["updates"]).lower()
        if self._updates not in ("auto", "watch", "poll"):
            raise ValueError(f"updates must be auto, watch or poll, got {o['updates']!r}")
        self._poll_interval = float(o["poll_interval"])
        self._housekeeping_interval = float(o["housekeeping_interval"])
        self._resync_interval = float(o["resync_interval"])
        self._reconnect_delay = float(o["reconnect_delay"])
        self._max_reconnect_delay = float(o["max_reconnect_delay"])
        self._unconfirmed_timeout = float(o["unconfirmed_timeout"])
        self.unconfirmed_window_s = self._unconfirmed_timeout
        self._timeout_ms = int(o["timeout_ms"])
        self._symbols = [str(s) for s in (o["symbols"] or ())]
        self._page_limit = int(o["trades_page_limit"])
        self._account_id = AccountId(str(o["account"] or f"{self.exchange_id.upper()}-{self._environment.upper()}"))
        self._client_id_param = client_id_param(self.exchange_id)

        self._ex = exchange
        self._owns_ex = exchange is None
        self._stream_ex = stream_exchange
        self._owns_stream_ex = stream_exchange is None
        self._factory = exchange_factory or ccxt_exchange
        self._stream_factory = stream_exchange_factory
        self._get_secret = get_secret
        self._now_ns = now_ns

        self._lock = threading.RLock()        # the order maps; events are emitted holding it, in order
        self._rest_lock = threading.Lock()    # one REST request at a time (never taken before _lock)
        self._by_client_id: Dict[str, _Tracked] = {}
        self._by_venue_cid: Dict[str, _Tracked] = {}
        self._by_venue_id: Dict[str, _Tracked] = {}
        self._seen_trades: set = set()
        self._orphans: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        self._in_flight = 0
        self._resync_needed = False
        self._last_resync_ns = 0
        self._trade_stream = False
        self._link_down = False             # a 'disconnected' status was reported, no 'reconnected' yet
        self._reconnect_due = False         # the stream resumed: report 'reconnected' after the resync
        self._rest_ok = 0                   # REST requests that succeeded (evidence the link works)
        self._rest_ok_at_loss = 0
        self._mode: Optional[str] = None
        self._connected = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __repr__(self) -> str:
        return f"CcxtExecutionClient({self.exchange_id}, {self._environment})"

    # ── identity ─────────────────────────────────────────────────────

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def account_id(self) -> AccountId:
        return self._account_id

    @property
    def update_mode(self) -> Optional[str]:
        """'watch' or 'poll' once connected."""
        return self._mode

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── session ──────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._connected:
            return
        if self._ex is None:
            self._ex = self._build_exchange()
        elif self._environment == PAPER:
            self._sandbox(self._ex, "REST")
        try:
            self._rest("load_markets")
        except Exception as exc:
            raise ConnectorError(f"{self.exchange_id}: cannot load markets: {type(exc).__name__}: {exc}") from exc
        self._mode = self._choose_mode()
        if self._mode == "watch" and self._stream_ex is not None and self._environment == PAPER:
            self._sandbox(self._stream_ex, "stream")
        self._stop.clear()
        self._connected = True
        self._thread = threading.Thread(target=self._run_updates, name=f"ccxt-exec-{self.exchange_id}", daemon=True)
        self._thread.start()
        log.info("%s connected (%s, updates by %s)", self, self._account_id, self._mode)

    def disconnect(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=10)
        self._connected = False
        if self._owns_ex and self._ex is not None:
            ex, self._ex = self._ex, None
            try:
                ex.close()
            except Exception as exc:   # closing must not fail a shutdown
                log.debug("%s close: %s", self.exchange_id, exc)

    def _build_exchange(self):
        config = {"enableRateLimit": True, "timeout": self._timeout_ms, **self.connector.config.options,
                  **(self.options.get("exchange_options") or {}), **self._credentials()}
        ex = self._factory(self.exchange_id, config)
        if self._environment == PAPER:
            self._sandbox(ex, "REST")
        return ex

    def _stream_config(self) -> Dict[str, Any]:
        return {"enableRateLimit": True, "timeout": self._timeout_ms, **self.connector.config.options,
                **(self.options.get("exchange_options") or {}), **self._credentials()}

    def _sandbox(self, ex, what: str) -> None:
        try:
            ex.set_sandbox_mode(True)
        except Exception as exc:
            raise ConnectorError(f"{self.exchange_id} has no sandbox in ccxt ({what}: {type(exc).__name__}: {exc}), "
                                 "so it cannot paper trade here") from exc

    def _credentials(self) -> Dict[str, str]:
        names = [str(v) for v in (self.options.get("secrets") or self._default_secret_names())]
        fields = {var: credential_field(var) for var in names}
        values = {var: self._get_secret(var) for var in names}
        missing = [var for var, value in values.items() if not value]
        if missing:
            raise ConnectorError(f"missing {', '.join(missing)}: the {self.exchange_id} {self._environment} API key, "
                                 "in the environment or .env")
        return {fields[var]: values[var] for var in names}

    def _default_secret_names(self) -> List[str]:
        prefix = f"{self.exchange_id.upper()}_{self._environment.upper()}_API"
        names = [f"{prefix}_KEY", f"{prefix}_SECRET"]
        if self._needs_password():
            names.append(f"{prefix}_PASSPHRASE")
        return names

    def _needs_password(self) -> bool:
        cls = getattr(ccxt, self.exchange_id, None) if ccxt is not None else None
        if cls is None:
            return False
        try:
            return bool((cls().requiredCredentials or {}).get("password"))
        except Exception:
            return False

    def _choose_mode(self) -> str:
        if self._updates == "poll":
            return "poll"
        available = self._watch_available()
        if self._updates == "watch" and not available:
            raise ConnectorError(f"{self.exchange_id}: watch_orders is not available (ccxt.pro); use updates='poll'")
        return "watch" if available else "poll"

    def _watch_available(self) -> bool:
        if self._stream_ex is not None:
            return hasattr(self._stream_ex, "watch_orders") and _has(self._stream_ex, "watchOrders", default=True)
        if self._stream_factory is not None:
            return True
        cls = getattr(ccxt_pro, self.exchange_id, None) if ccxt_pro is not None else None
        if cls is None:
            return False
        try:
            return bool((cls().has or {}).get("watchOrders"))
        except Exception:
            return False

    def _require_connected(self) -> None:
        if not self._connected or self._ex is None:
            raise ConnectorError(f"{self}: not connected")

    def _rest(self, name: str, *args, **kwargs):
        with self._rest_lock:
            result = getattr(self._ex, name)(*args, **kwargs)
            self._rest_ok += 1
            return result

    def _rest_has(self, feature: str) -> bool:
        return _has(self._ex, feature)

    # ── orders ───────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> None:
        self._require_connected()
        cid = str(order.client_order_id)
        with self._lock:
            if cid in self._by_client_id:
                log.warning("%s: %s was already sent; not sending it again", self.exchange_id, cid)
                return
        try:
            symbol = self._symbol(order.instrument_id)
            amount, price = self._precise(symbol, order)
        except Exception as exc:
            with self._lock:
                self._emit(OrderRejected(order.client_order_id, order.strategy_id, order.instrument_id,
                                         self._now_ns(), reason=f"not sent: {type(exc).__name__}: {exc}"))
            return
        vcid = venue_client_order_id(cid, self.exchange_id)
        params: Dict[str, Any] = {self._client_id_param: vcid}
        if order.time_in_force is TimeInForce.IOC:
            params["timeInForce"] = "IOC"
        elif order.time_in_force is TimeInForce.DAY:
            log.info("%s: crypto exchanges have no trading day; %s is sent good-till-cancelled", self.exchange_id, cid)
        tr = _Tracked(order, symbol, vcid, submitted_ns=self._now_ns())
        with self._lock:
            self._by_client_id[cid] = tr
            self._by_venue_cid[vcid] = tr
            self._in_flight += 1
            order.tags["venue_client_order_id"] = vcid
        otype = "market" if order.order_type is OrderType.MARKET else "limit"
        side = "buy" if order.side is OrderSide.BUY else "sell"
        try:
            reply = self._rest("create_order", symbol, otype, side, amount, price, params)
        except Exception as exc:
            with self._lock:
                self._in_flight -= 1
                if is_transient(exc):
                    tr.unconfirmed = f"{type(exc).__name__}: {exc}"
                    log.warning("%s: no reply to %s (%s); looking it up by client id %s",
                                self.exchange_id, cid, tr.unconfirmed, vcid)
                else:
                    tr.terminal = True
                    self._emit(OrderRejected(order.client_order_id, order.strategy_id, order.instrument_id,
                                             self._now_ns(), reason=f"{type(exc).__name__}: {exc}"))
            return
        with self._lock:
            self._in_flight -= 1
            vid = str((reply or {}).get("id") or "")
            if not vid:
                tr.unconfirmed = "create_order returned no order id"
                return
            tr.created_ms = _int((reply or {}).get("timestamp"))
            self._bind(tr, vid)
            if reply.get("status"):
                self._apply_order(tr, reply)
            cancel = tr.cancel_pending and not tr.terminal
            tr.cancel_pending = False
        if cancel:     # asked (from another thread) while the order was on its way
            self.cancel_order(order)

    def cancel_order(self, order: Order) -> None:
        self._require_connected()
        with self._lock:
            tr = self._by_client_id.get(str(order.client_order_id))
            if tr is None and order.tags.get("venue_order_id"):
                tr = self._adopt(order, str(order.tags["venue_order_id"]), self._symbol(order.instrument_id))
            if tr is None:
                log.warning("%s: cancel of %s, which this client never sent; ignored",
                            self.exchange_id, order.client_order_id)
                return
            if tr.terminal and not tr.guessed:
                return
            if tr.venue_order_id is None and order.tags.get("venue_order_id"):
                self._bind(tr, str(order.tags["venue_order_id"]))   # learned by the OMS's reconciliation
            if tr.guessed:
                # Rejected by a guess, but the OMS has found it: take it back and cancel it.
                log.warning("%s: %s, rejected when the exchange never confirmed it, is being cancelled",
                            self.exchange_id, order.client_order_id)
                tr.guessed = False
                tr.terminal = False
                tr.accepted = True
                tr.unconfirmed = None
            if tr.venue_order_id is None:
                if tr.unconfirmed is not None:
                    tr.cancel_pending = True    # sent once the exchange confirms the order
                    return
                vid, symbol = None, tr.symbol   # cancel by our client order id
            else:
                vid, symbol = tr.venue_order_id, tr.symbol
        if vid is None:
            try:
                self._rest("cancel_order", None, symbol, {self._client_id_param: tr.venue_client_id})
            except Exception as exc:
                log.warning("%s: cancel of %s by client id failed: %s: %s", self.exchange_id, order.client_order_id,
                            type(exc).__name__, exc)
            return
        with self._lock:
            vid, symbol = tr.venue_order_id, tr.symbol
        try:
            reply = self._rest("cancel_order", vid, symbol)
        except Exception as exc:
            log.warning("%s refused to cancel %s (%s): %s: %s", self.exchange_id, order.client_order_id, vid,
                        type(exc).__name__, exc)
            if not is_transient(exc):     # refused (a network error leaves the outcome unknown)
                self._refresh(tr)         # the order may have filled or ended: learn how, then say so
                with self._lock:
                    self._emit(OrderCancelRejected(order.client_order_id, order.strategy_id, order.instrument_id,
                                                   self._now_ns(),
                                                   reason=f"cancel refused by {self.exchange_id}: "
                                                          f"{type(exc).__name__}: {exc}"))
            return
        if isinstance(reply, dict) and reply.get("status"):
            with self._lock:
                self._apply_order(tr, reply)

    def query_order(self, order: Order) -> Optional[OrderReport]:
        self._require_connected()
        symbol = self._symbol(order.instrument_id)
        with self._lock:
            tr = self._by_client_id.get(str(order.client_order_id))
            vid = (tr.venue_order_id if tr else None) or order.tags.get("venue_order_id")
        try:
            if vid:
                o = self._rest("fetch_order", str(vid), symbol)
            else:
                vcid = tr.venue_client_id if tr else venue_client_order_id(str(order.client_order_id), self.exchange_id)
                o = self._find_by_client_id(symbol, vcid)
        except Exception as exc:
            if error_is(exc, "OrderNotFound"):
                return None
            raise ConnectorError(f"{self.exchange_id} fetch_order {vid}: {type(exc).__name__}: {exc}") from exc
        if not o:
            return None
        with self._lock:
            if tr is None and o.get("id"):
                self._adopt(order, str(o["id"]), symbol)
            return self._order_report(o)

    def open_orders(self) -> List[OrderReport]:
        self._require_connected()
        orders = self._per_symbol("fetch_open_orders")
        with self._lock:
            return [self._order_report(o) for o in orders]

    def fills(self, since: pd.Timestamp) -> List[FillReport]:
        self._require_connected()
        since_ms = ns_to_ms(to_utc_timestamp(since).value)
        try:
            trades = self._paged_trades(None, since_ms)
        except Exception as exc:
            if not error_is(exc, "ArgumentsRequired"):
                raise ConnectorError(f"{self.exchange_id} fetch_my_trades: {type(exc).__name__}: {exc}") from exc
            trades = []
            for symbol in self._known_symbols():
                trades += self._paged_trades(symbol, since_ms)
        with self._lock:
            reports = [self._fill_report(t) for t in trades]
        reports = [r for r in reports if r is not None and r.ts >= to_utc_timestamp(since)]
        return sorted(reports, key=lambda r: (r.ts, r.trade_id))

    # ── symbols and precision ────────────────────────────────────────

    def _symbol(self, instrument_id: "str | InstrumentId") -> str:
        iid = as_instrument_id(instrument_id)
        spec = self.connector.venue_spec(iid.venue)
        opts = spec.connector_options(self.connector.name)
        exchange_id = str(opts.get("exchange_id") or self.exchange_id).lower()
        if exchange_id != self.exchange_id:
            raise ConnectorError(f"{iid} trades on {exchange_id}; this client trades on {self.exchange_id}")
        if opts.get("symbology", "dash") == "native_id":
            for market in (getattr(self._ex, "markets", None) or {}).values():
                if market.get("id") == str(iid.symbol):
                    return market["symbol"]
            raise ConnectorError(f"{self.exchange_id} has no market with id {iid.symbol}")
        base, _, quote = str(iid.symbol).partition("-")
        if not quote:
            raise ConnectorError(f"{iid}: expected '<BASE>-<QUOTE>'")
        return f"{base}/{quote}"

    def _instrument_id(self, symbol: str) -> Optional[str]:
        for spec in self.connector.venues.served_by(self.connector.name):
            opts = spec.connector_options(self.connector.name)
            if str(opts.get("exchange_id", "")).lower() != self.exchange_id or ":" in symbol:
                continue
            if opts.get("symbology", "dash") == "native_id":
                market = (getattr(self._ex, "markets", None) or {}).get(symbol)
                return str(InstrumentId.of(market["id"], spec.venue)) if market else None
            return str(InstrumentId.of(symbol.replace("/", "-"), spec.venue))
        return None

    def _precise(self, symbol: str, order: Order) -> Tuple[float, Optional[float]]:
        if order.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {order.quantity:g}")
        amount = float(self._ex.amount_to_precision(symbol, order.quantity))
        if amount <= 0:
            raise ValueError(f"{order.quantity:g} is below {symbol}'s amount precision")
        price = None
        if order.order_type is OrderType.LIMIT:
            if order.price is None or order.price <= 0:
                raise ValueError("a limit order needs a positive price")
            price = float(self._ex.price_to_precision(symbol, order.price))
            if price != order.price:
                order.tags["venue_price"] = price
        if amount != order.quantity:
            log.warning("%s: %s quantity %g rounded to %g (%s precision)", self.exchange_id,
                        order.client_order_id, order.quantity, amount, symbol)
            order.tags.setdefault("requested_qty", order.quantity)
            order.quantity = amount
        return amount, price

    # ── updates in (any thread; everything under _lock) ──────────────

    def _find(self, venue_order_id: Any, venue_client_id: Any = None) -> Optional[_Tracked]:
        tr = self._by_venue_id.get(str(venue_order_id)) if venue_order_id else None
        if tr is None and venue_client_id:
            tr = self._by_venue_cid.get(str(venue_client_id))
        return tr

    def _bind(self, tr: _Tracked, venue_order_id: str) -> None:
        tr.venue_order_id = venue_order_id
        self._by_venue_id[venue_order_id] = tr
        tr.order.tags["venue_order_id"] = venue_order_id
        for kind, item in self._orphans.pop(venue_order_id, []):
            if kind == "order":
                self._apply_order(tr, item)
            else:
                self._apply_trade(tr, item)

    def _adopt(self, order: Order, venue_order_id: str, symbol: str) -> Optional[_Tracked]:
        """Track an order this client did not send in this session (after a restart), so that
        what the exchange says about it from now on becomes events. Its earlier trades may be
        reported again (once each): the OMS knows them by trade id from its journal."""
        if order.is_closed:
            return None
        cid = str(order.client_order_id)
        vcid = str(order.tags.get("venue_client_order_id") or venue_client_order_id(cid, self.exchange_id))
        tr = _Tracked(order, symbol, vcid, submitted_ns=self._now_ns(),
                      accepted=order.status not in (OrderStatus.INITIALIZED, OrderStatus.SUBMITTED))
        self._by_client_id[cid] = tr
        self._by_venue_cid[vcid] = tr
        self._bind(tr, venue_order_id)
        return tr

    def _park(self, venue_order_id: Any, kind: str, item: Dict[str, Any]) -> None:
        """An update for an order id we do not know yet: kept while a submit's reply may still be
        on its way (the stream can be faster than create_order's reply), else not ours."""
        if not venue_order_id or (self._in_flight == 0 and not any(t.unconfirmed for t in self._by_client_id.values())):
            return
        parked = self._orphans.setdefault(str(venue_order_id), [])
        parked.append((kind, item))
        if len(self._orphans) > 500:
            self._orphans.pop(next(iter(self._orphans)))

    def _on_orders(self, orders: Iterable[Dict[str, Any]]) -> None:
        for o in orders or []:
            with self._lock:
                tr = self._find(o.get("id"), o.get("clientOrderId"))
                if tr is None:
                    self._park(o.get("id"), "order", o)
                    continue
                if tr.venue_order_id is None and o.get("id"):
                    tr.unconfirmed = None
                    self._bind(tr, str(o["id"]))
                self._apply_order(tr, o)

    def _on_trades(self, trades: Iterable[Dict[str, Any]]) -> None:
        for t in trades or []:
            with self._lock:
                tr = self._find(t.get("order"), t.get("clientOrderId"))
                if tr is None:
                    self._park(t.get("order"), "trade", t)
                    continue
                self._apply_trade(tr, t)

    def _apply_order(self, tr: _Tracked, o: Dict[str, Any]) -> None:
        status = str(o.get("status") or "").lower()
        filled = _num(o.get("filled"))
        if filled is not None and filled > tr.venue_filled:
            tr.venue_filled = filled
        if tr.created_ms is None:
            tr.created_ms = _int(o.get("timestamp"))
        for t in o.get("trades") or []:      # some exchanges return the order's trades with it
            if isinstance(t, dict) and t.get("id") and t.get("amount"):
                self._apply_trade(tr, {"order": tr.venue_order_id, **t})
        if self._synthesize_fills():
            self._synthesize(tr, o)
        if tr.terminal or not status:
            return
        order = tr.order
        if status == "open":
            if not tr.accepted:
                tr.accepted = True
                self._emit(OrderAccepted(order.client_order_id, order.strategy_id, order.instrument_id,
                                         self._ts(o.get("timestamp"))))
            return
        if status not in TERMINAL_STATUSES:
            log.debug("%s order %s: status %r ignored", self.exchange_id, tr.venue_order_id, status)
            return
        if not self._trades_cover(tr):
            self._catch_up(tr)
        if status == "closed":
            tr.closed_seen = True
            if self._trades_cover(tr):
                tr.terminal = True
            return
        tr.terminal = True
        if not self._trades_cover(tr):
            log.warning("%s order %s is %s with %g filled, but trades for only %g arrived so far",
                        self.exchange_id, tr.venue_order_id, status, tr.venue_filled, tr.emitted_qty)
        ts = self._ts(o.get("lastUpdateTimestamp") or o.get("lastTradeTimestamp"))
        status_word = "canceled" if status == "cancelled" else status
        reason = order_reason(status_word, o)
        if status == "rejected":
            self._emit(OrderRejected(order.client_order_id, order.strategy_id, order.instrument_id, ts, reason=reason))
        else:
            self._emit(OrderCanceled(order.client_order_id, order.strategy_id, order.instrument_id, ts, reason=reason))

    def _apply_trade(self, tr: _Tracked, t: Dict[str, Any]) -> None:
        qty, px = _num(t.get("amount")), _num(t.get("price"))
        if not qty or px is None:
            log.warning("%s trade without amount or price ignored: %s", self.exchange_id, t.get("id"))
            return
        trade_id = re.sub(r"\s", "_", str(t.get("id") or "")) or \
            f"{tr.venue_order_id}-{t.get('timestamp')}-{qty:g}-{px:g}"
        key = (tr.venue_client_id, trade_id)
        if key in self._seen_trades:
            return
        self._seen_trades.add(key)
        if tr.terminal and not tr.closed_seen:
            log.warning("%s: fill %s of %s arrived after its end event", self.exchange_id, trade_id,
                        tr.order.client_order_id)
        commission, currency = _fee(t)
        liquidity = LiquiditySide.MAKER if str(t.get("takerOrMaker") or "").lower() == "maker" else LiquiditySide.TAKER
        tr.emitted_qty += qty
        order = tr.order
        tags = {"venue_order_id": tr.venue_order_id, "venue_client_order_id": tr.venue_client_id}
        if not _has_fee(t):
            tags["commission_unknown"] = True     # the PositionKeeper books its fee model's estimate
        self._emit(OrderFilled(
            order.client_order_id, order.strategy_id, order.instrument_id, self._ts(t.get("timestamp")),
            trade_id=TradeId(trade_id), side=order.side, last_qty=qty, last_px=px, commission=commission,
            currency=currency, liquidity_side=liquidity, tags=tags))
        if tr.closed_seen and self._trades_cover(tr):
            tr.terminal = True

    def _trades_cover(self, tr: _Tracked) -> bool:
        return tr.emitted_qty >= tr.venue_filled or _close_enough(tr.emitted_qty, tr.venue_filled)

    def _synthesize_fills(self) -> bool:
        """No trade data on this exchange: fills come from the change in the order's `filled`."""
        return not (self._trade_stream or self._rest_has("fetchMyTrades") or self._rest_has("fetchOrderTrades"))

    def _synthesize(self, tr: _Tracked, o: Dict[str, Any]) -> None:
        filled, avg = _num(o.get("filled")) or 0.0, _num(o.get("average"))
        delta = filled - tr.emitted_qty
        if delta <= 0 or _close_enough(filled, tr.emitted_qty) or avg is None:
            return
        cost = filled * avg
        price = (cost - tr.venue_cost) / delta
        tr.venue_cost = cost
        self._apply_trade(tr, {"id": f"{tr.venue_order_id}-{filled:.12g}", "amount": delta, "price": price,
                               "timestamp": o.get("lastTradeTimestamp") or o.get("lastUpdateTimestamp"),
                               "fee": None})

    def _catch_up(self, tr: _Tracked) -> None:
        """Fetch the trades of one order over REST (called holding _lock, which is allowed: the REST
        lock is never held while waiting for _lock)."""
        if not tr.venue_order_id:
            return
        try:
            if self._rest_has("fetchOrderTrades"):
                trades = self._rest("fetch_order_trades", tr.venue_order_id, tr.symbol)
            elif self._rest_has("fetchMyTrades"):
                trades = self._rest("fetch_my_trades", tr.symbol, self._since_ms(tr))
            else:
                return
        except Exception as exc:
            log.warning("%s: cannot fetch the trades of order %s: %s: %s", self.exchange_id, tr.venue_order_id,
                        type(exc).__name__, exc)
            return
        for t in sorted(trades or [], key=lambda t: _int(t.get("timestamp")) or 0):
            if str(t.get("order") or tr.venue_order_id) == tr.venue_order_id:
                self._apply_trade(tr, t)

    def _since_ms(self, tr: _Tracked) -> int:
        """Where to start looking for an order's trades: a minute before the exchange's time of
        the order (or, before it gave one, our time of sending it)."""
        start = tr.created_ms if tr.created_ms else ns_to_ms(tr.submitted_ns)
        return start - 60_000

    def _refresh(self, tr: _Tracked) -> bool:
        """Re-read one order over REST and apply what changed. False on a request failure."""
        if not tr.venue_order_id:
            return True
        try:
            o = self._rest("fetch_order", tr.venue_order_id, tr.symbol)
        except Exception as exc:
            log.debug("%s fetch_order %s: %s", self.exchange_id, tr.venue_order_id, exc)
            return False
        with self._lock:
            self._apply_order(tr, o or {})
        return True

    # ── background updates ───────────────────────────────────────────

    def _run_updates(self) -> None:
        try:
            asyncio.run(self._updates_main())
        except Exception:
            log.exception("%s: the order-update thread stopped", self)

    async def _updates_main(self) -> None:
        tasks = []
        stream = None
        if self._mode == "watch":
            try:
                stream = self._stream_ex if self._stream_ex is not None else self._build_stream_exchange()
            except Exception as exc:
                log.warning("%s: no order stream (%s); polling instead", self.exchange_id, exc)
                self._mode = "poll"
        if self._mode == "watch":
            self._trade_stream = hasattr(stream, "watch_my_trades") and _has(stream, "watchMyTrades", default=True)
            tasks.append(asyncio.create_task(self._watch(stream, "watch_orders", self._on_orders)))
            if self._trade_stream:
                tasks.append(asyncio.create_task(self._watch(stream, "watch_my_trades", self._on_trades)))
            tasks.append(asyncio.create_task(self._housekeeping()))
        else:
            tasks.append(asyncio.create_task(self._poll()))
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.02)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if stream is not None and self._owns_stream_ex:
                try:
                    result = stream.close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    log.debug("%s stream close: %s", self.exchange_id, exc)

    def _build_stream_exchange(self):
        config = self._stream_config()
        if self._stream_factory is not None:
            ex = self._stream_factory(self.exchange_id, config)
        else:
            ex = getattr(ccxt_pro, self.exchange_id)(config)
        if self._environment == PAPER:
            self._sandbox(ex, "stream")
        return ex

    async def _watch(self, stream, method: str, on_items: Callable[[Any], None]) -> None:
        delay, failing = self._reconnect_delay, False
        while True:
            try:
                items = await getattr(stream, method)()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if error_is(exc, "NotSupported"):
                    if method == "watch_my_trades":
                        self._trade_stream = False
                        return
                    log.warning("%s: %s not supported; polling instead", self.exchange_id, method)
                    self._mode = "poll"
                    return await self._poll()
                if not failing:
                    log.warning("%s %s failed (%s: %s); retrying", self.exchange_id, method, type(exc).__name__, exc)
                    self._link_lost(f"{method}: {type(exc).__name__}: {exc}")
                failing = True
                self._resync_needed = True
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)
                continue
            if failing:
                log.info("%s %s resumed", self.exchange_id, method)
                failing, delay = False, self._reconnect_delay
                self._resync_needed = True     # anything between the last re-read and the resubscription
                self._reconnect_due = self._link_down
            try:
                on_items(items)
            except Exception:
                log.exception("%s: could not apply a %s update", self.exchange_id, method)

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(self._housekeeping_interval)
            try:
                self._resolve_unconfirmed()
                due = self._now_ns() - self._last_resync_ns >= self._resync_interval * 1e9
                if self._resync_needed or due or self._link_down:
                    self._resync_needed = False
                    ok = self._resync()
                    if not ok:
                        self._resync_needed = True
                    self._after_resync(ok)
            except Exception:
                log.exception("%s housekeeping failed", self.exchange_id)

    async def _poll(self) -> None:
        while True:
            try:
                self._resolve_unconfirmed()
                self._after_resync(self._resync())
            except Exception:
                log.exception("%s: polling failed", self.exchange_id)
            await asyncio.sleep(self._poll_interval)

    def _after_resync(self, ok: bool) -> None:
        """The link's state from a REST re-read of the open orders: a failed one means the
        exchange is out of reach; after a loss, a REST request that worked (the re-read, or a
        probe when nothing was open) means it is back and what was missed has been re-read."""
        if not ok:
            self._link_lost("a REST re-read of the open orders failed")
            return
        if not (self._link_down or self._reconnect_due):
            return
        if self._link_down and self._rest_ok <= self._rest_ok_at_loss and not self._probe_link():
            return
        self._link_back("the exchange answers again; open orders re-read")

    def _probe_link(self) -> bool:
        try:
            self._rest("fetch_open_orders")
        except Exception as exc:
            return not is_transient(exc)      # an answer, even a refusal, means the link works
        return True

    def _link_lost(self, detail: str) -> None:
        if not self._link_down:
            self._link_down = True
            self._rest_ok_at_loss = self._rest_ok
            self._status("disconnected", f"{self.exchange_id}: {detail}", self._now_ns())

    def _link_back(self, detail: str) -> None:
        self._reconnect_due = False
        if self._link_down:
            self._link_down = False
            self._status("reconnected", f"{self.exchange_id}: {detail}", self._now_ns())

    def _resync(self) -> bool:
        """Re-read every open order over REST: its trades first, then its status, so fills come
        before the order's end. False if a request failed."""
        with self._lock:
            open_ = [tr for tr in self._by_client_id.values()
                     if tr.venue_order_id and not tr.terminal]
        self._last_resync_ns = self._now_ns()
        ok = True
        if not open_:
            return ok
        if self._rest_has("fetchMyTrades"):
            by_symbol: Dict[str, List[_Tracked]] = {}
            for tr in open_:
                by_symbol.setdefault(tr.symbol, []).append(tr)
            for symbol, trs in by_symbol.items():
                since = min(self._since_ms(tr) for tr in trs)
                try:
                    trades = self._rest("fetch_my_trades", symbol, since)
                except Exception as exc:
                    log.debug("%s fetch_my_trades %s: %s", self.exchange_id, symbol, exc)
                    ok = False
                    continue
                self._on_trades(sorted((t for t in trades or [] if t.get("order")),
                                       key=lambda t: _int(t.get("timestamp")) or 0))
        for tr in open_:
            ok = self._refresh(tr) and ok
        return ok

    def _resolve_unconfirmed(self) -> None:
        with self._lock:
            pending = [tr for tr in self._by_client_id.values() if tr.unconfirmed and not tr.terminal]
        for tr in pending:
            try:
                found = self._find_by_client_id(tr.symbol, tr.venue_client_id)
            except Exception as exc:
                if not error_is(exc, "OrderNotFound"):
                    log.debug("%s lookup of %s: %s", self.exchange_id, tr.venue_client_id, exc)
                    continue
                found = None
            cancel = False
            with self._lock:
                if tr.venue_order_id is not None or tr.terminal:   # the stream found it meanwhile
                    tr.unconfirmed = None
                    cancel = tr.cancel_pending
                elif found and found.get("id"):
                    log.info("%s: %s found at the exchange as %s", self.exchange_id, tr.venue_client_id, found["id"])
                    tr.unconfirmed = None
                    tr.created_ms = tr.created_ms or _int(found.get("timestamp"))
                    self._bind(tr, str(found["id"]))
                    self._apply_order(tr, found)
                    cancel = tr.cancel_pending
                elif (self._now_ns() - tr.submitted_ns) / 1e9 >= self._unconfirmed_timeout:
                    tr.terminal = True
                    tr.guessed = True       # a guess: should the order exist, it can still be cancelled
                    order = tr.order
                    self._emit(OrderRejected(order.client_order_id, order.strategy_id, order.instrument_id,
                                             self._now_ns(),
                                             reason=f"no reply from {self.exchange_id} ({tr.unconfirmed}) and no "
                                                    f"order with client id {tr.venue_client_id} after "
                                                    f"{self._unconfirmed_timeout:g}s"))
            if cancel and not tr.terminal:
                tr.cancel_pending = False
                self.cancel_order(tr.order)

    def _find_by_client_id(self, symbol: str, venue_client_id: str) -> Optional[Dict[str, Any]]:
        if self.exchange_id in CLIENT_ID_RULES:     # their fetch_order takes a client order id
            try:
                return self._rest("fetch_order", None, symbol, {"clientOrderId": venue_client_id})
            except Exception as exc:
                if error_is(exc, "OrderNotFound"):
                    return None
                raise
        for o in self._rest("fetch_open_orders", symbol) or []:
            if o.get("clientOrderId") == venue_client_id:
                return o
        if self._rest_has("fetchClosedOrders"):
            for o in self._rest("fetch_closed_orders", symbol) or []:
                if o.get("clientOrderId") == venue_client_id:
                    return o
        return None

    # ── reconciliation reads ─────────────────────────────────────────

    def _known_symbols(self) -> List[str]:
        with self._lock:
            symbols = {tr.symbol for tr in self._by_client_id.values()}
        return sorted(symbols | set(self._symbols))

    def _per_symbol(self, method: str) -> List[Dict[str, Any]]:
        """`method` for the whole account, or symbol by symbol where the exchange needs one."""
        try:
            return list(self._rest(method) or [])
        except Exception as exc:
            if not error_is(exc, "ArgumentsRequired"):
                raise ConnectorError(f"{self.exchange_id} {method}: {type(exc).__name__}: {exc}") from exc
        out: List[Dict[str, Any]] = []
        for symbol in self._known_symbols():
            try:
                out += list(self._rest(method, symbol) or [])
            except Exception as exc:
                raise ConnectorError(f"{self.exchange_id} {method} {symbol}: {type(exc).__name__}: {exc}") from exc
        return out

    def _paged_trades(self, symbol: Optional[str], since_ms: int) -> List[Dict[str, Any]]:
        seen: Dict[str, Dict[str, Any]] = {}
        cursor = since_ms
        for _ in range(200):
            page = self._rest("fetch_my_trades", symbol, cursor, self._page_limit) or []
            new = [t for t in page if str(t.get("id")) not in seen]
            for t in new:
                seen[str(t.get("id"))] = t
            if len(page) < self._page_limit or not new:
                break
            newest = max(_int(t.get("timestamp")) or 0 for t in page)
            if newest < cursor:
                break
            cursor = newest
        return list(seen.values())

    def _order_report(self, o: Dict[str, Any]) -> OrderReport:
        tr = self._find(o.get("id"), o.get("clientOrderId"))
        filled = _num(o.get("filled")) or 0.0
        created = _int(o.get("timestamp"))
        updated = _int(o.get("lastUpdateTimestamp") or o.get("lastTradeTimestamp"))
        status = str(o.get("status") or "")
        remark = order_reason(status, o) if status else None
        return OrderReport(
            order_id=str(o.get("id") or ""), symbol=str(o.get("symbol") or ""),
            side=str(o.get("side") or "").upper(), quantity=_num(o.get("amount")) or 0.0, filled_quantity=filled,
            price=_positive(o.get("price")), average_fill_price=_positive(o.get("average")) if filled > 0 else None,
            status=status, created=ns_to_timestamp(ms_to_ns(created) if created else self._now_ns()),
            updated=ns_to_timestamp(ms_to_ns(updated)) if updated else None,
            client_order_id=str(tr.order.client_order_id) if tr else o.get("clientOrderId"),
            remark=remark if remark != status else None)

    def _fill_report(self, t: Dict[str, Any]) -> Optional[FillReport]:
        qty, px, ts = _num(t.get("amount")), _num(t.get("price")), _int(t.get("timestamp"))
        if not qty or px is None or ts is None:
            return None
        tr = self._find(t.get("order"), t.get("clientOrderId"))
        commission, currency = _fee(t)
        symbol = str(t.get("symbol") or "")
        return FillReport(
            trade_id=str(t.get("id") or ""), order_id=str(t.get("order") or ""), symbol=symbol,
            side=str(t.get("side") or "").upper(), quantity=qty, price=px, ts=ns_to_timestamp(ms_to_ns(ts)),
            commission=commission if (t.get("fee") or t.get("fees")) else None, currency=currency,
            client_order_id=str(tr.order.client_order_id) if tr else t.get("clientOrderId"),
            instrument_id=str(tr.order.instrument_id) if tr else self._instrument_id(symbol))

    def _ts(self, ms: Any) -> int:
        """The exchange's epoch-ms time as UTC ns, or now where it gave none."""
        value = _int(ms)
        return ms_to_ns(value) if value else self._now_ns()


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _has(ex: Any, feature: str, default: bool = False) -> bool:
    has = getattr(ex, "has", None)
    if not isinstance(has, dict) or feature not in has:
        return default
    return bool(has.get(feature))


def _has_fee(t: Dict[str, Any]) -> bool:
    """Whether a ccxt trade reports its fee at all (a reported 0 counts)."""
    fee = t.get("fee")
    if isinstance(fee, dict) and fee.get("cost") is not None:
        return True
    return any(isinstance(f, dict) and f.get("cost") is not None for f in (t.get("fees") or []))


def _fee(t: Dict[str, Any]) -> Tuple[float, str]:
    """(cost, currency) of a ccxt trade's fee: `fee`, else the `fees` in the first one's currency."""
    fee = t.get("fee")
    if isinstance(fee, dict) and fee.get("cost") is not None:
        return float(_num(fee.get("cost")) or 0.0), str(fee.get("currency") or "")
    fees = [f for f in (t.get("fees") or []) if isinstance(f, dict) and f.get("cost") is not None]
    if not fees:
        return 0.0, ""
    currency = str(fees[0].get("currency") or "")
    other = [f for f in fees if str(f.get("currency") or "") != currency]
    if other:
        log.warning("trade %s pays fees in several currencies; %s only counted", t.get("id"), currency)
    return sum(float(_num(f.get("cost")) or 0.0) for f in fees if str(f.get("currency") or "") == currency), currency
