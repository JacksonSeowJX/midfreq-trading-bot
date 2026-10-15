"""
Read-only view of a Moomoo account through OpenD's trade context (balance, positions, orders).

The queries are the ones midfreq.live.order_gateway.MoomooPaperGateway makes (accinfo_query,
position_list_query, order_list_query, history_order_list_query), reimplemented here because the
data layer may not import the live one. Nothing that places, changes or cancels an order is ever
called, and the trade account is never unlocked: reading needs no unlock.

Never hanging. The SDK's trade-context constructor retries a refused or failed connection every
6 seconds forever and never raises, so the client first probes OpenD's TCP port (1.5 s by default)
and only then creates the context, on a worker thread that is abandoned after `connect_timeout`
seconds (a context that arrives later is closed at once). Queries on an open context are bounded
by the SDK's own request timeout and `set_sync_query_connect_timeout`.

Times. OpenD reports order times as wall-clock strings in the market's own timezone (an HK order
at the 09:30 open reads '09:32:00'); they are read in the timezone of the market's venue in
config/venues.json (or the `timezone` option) and returned in UTC.
"""
from __future__ import annotations

import math
import socket
import threading
import time
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from midfreq.data.adapters.base import (
    AccountClient, AccountSnapshot, AccountUnavailableError, ConnectorError, OrderReport, PositionReport,
)
from midfreq.core.common.timestamps import ns_to_timestamp

RET_OK = 0   # moomoo.RET_OK; the SDK's TrdEnv/TrdMarket/SecurityFirm constants are plain strings

# Accepted spellings of connections.yaml's moomoo.trading_env, as in midfreq.live.order_gateway.
TRADING_ENVS = {"simulate": "SIMULATE", "paper": "SIMULATE", "real": "REAL"}

# The only trade-context methods this client calls.
READ_METHODS = ("accinfo_query", "position_list_query", "order_list_query", "history_order_list_query")


class OpenDTransport:
    """How the client reaches OpenD: a TCP probe of the gateway's port, and the SDK's trade context.
    Tests pass a fake with the same two methods."""

    def probe(self, host: str, port: int, timeout: float) -> str:
        """'' if something accepts connections on host:port within `timeout` seconds, else why not."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return ""
        except OSError as exc:
            return f"OpenD is not reachable at {host}:{port} ({exc.strerror or exc})"

    def open_context(self, market: str, host: str, port: int, security_firm: str, query_timeout: float):
        try:
            from moomoo import OpenSecTradeContext   # imported here: importing midfreq.data must not import the SDK
        except ImportError:
            raise AccountUnavailableError("the moomoo SDK is not installed (the `moomoo` extra)") from None
        ctx = OpenSecTradeContext(filter_trdmarket=market, host=host, port=port, security_firm=security_firm)
        ctx.set_sync_query_connect_timeout(query_timeout)   # a query while OpenD is away fails instead of waiting
        return ctx


class MoomooAccountClient(AccountClient):
    """
    One Moomoo trade account (paper or real) of one market, as OpenD reports it.

    `currency` is the market's account currency (the venue's in config/venues.json), asked of
    accinfo_query and used when OpenD's reply does not name one. Positions with zero quantity
    (closed today, still listed by OpenD) are left out; a SHORT position has a negative quantity.
    `average_price` is OpenD's average_cost (cost_price on older SDKs), None if it reports none.
    """

    def __init__(self, market: str, host: str = "127.0.0.1", port: int = 11111,
                 trading_env: str = "simulate", security_firm: str = "FUTUSECURITIES",
                 currency: str = "USD", timezone: str = "America/New_York", acc_id: int = 0,
                 transport: Optional[OpenDTransport] = None,
                 instrument_id: Optional[Callable[[str], Optional[str]]] = None,
                 probe_timeout: float = 1.5, connect_timeout: float = 5.0, query_timeout: float = 10.0,
                 now_ns: Callable[[], int] = time.time_ns):
        env = str(trading_env or "simulate").strip().lower()
        if env not in TRADING_ENVS:
            raise ValueError(f"moomoo trading_env must be one of {sorted(TRADING_ENVS)}, got {trading_env!r}")
        self.market = str(market).upper()
        self.host, self.port = str(host), int(port)
        self.trd_env = TRADING_ENVS[env]
        self.security_firm = str(security_firm).upper()
        self.currency = currency
        self.timezone = timezone
        self.acc_id = int(acc_id)
        self.transport = transport or OpenDTransport()
        self._instrument_id = instrument_id
        self.probe_timeout = probe_timeout
        self.connect_timeout = connect_timeout
        self.query_timeout = query_timeout
        self._now_ns = now_ns
        self._ctx = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"MoomooAccountClient({self.market} {self.trd_env} via {self.host}:{self.port})"

    # ── session ──────────────────────────────────────────────────────

    def available(self):
        try:
            self._context()
        except ConnectorError as exc:
            return False, str(exc)
        except Exception as exc:   # never raise from a probe
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    def close(self) -> None:
        with self._lock:
            ctx, self._ctx = self._ctx, None
        if ctx is not None:
            ctx.close()

    def _context(self):
        """The trade context, after checking OpenD's port is open (every time: it is cheap, and a
        context left over from before OpenD went down would otherwise wait for it)."""
        reason = self.transport.probe(self.host, self.port, self.probe_timeout)
        if reason:
            raise AccountUnavailableError(reason)
        with self._lock:
            if self._ctx is None:
                self._ctx = self._open_bounded()
            return self._ctx

    def _open_bounded(self):
        """Create the context on a worker thread and give up after `connect_timeout` seconds: the
        port can be open while the handshake never completes (OpenD still starting, or something
        else listening), and the SDK then retries forever."""
        box: Dict[str, Any] = {}
        box_lock = threading.Lock()

        def work():
            try:
                ctx = self.transport.open_context(self.market, self.host, self.port,
                                                  self.security_firm, self.query_timeout)
            except Exception as exc:
                with box_lock:
                    box["error"] = exc
                return
            with box_lock:
                late = box.get("abandoned", False)
                if not late:
                    box["ctx"] = ctx
            if late:
                ctx.close()   # the caller gave up waiting; do not leave a session open

        worker = threading.Thread(target=work, name=f"moomoo-account-{self.market}", daemon=True)
        worker.start()
        worker.join(self.connect_timeout)
        with box_lock:
            if "ctx" in box:
                return box["ctx"]
            if "error" in box:
                exc = box["error"]
                if isinstance(exc, AccountUnavailableError):
                    raise exc
                raise AccountUnavailableError(f"cannot open a trade context on OpenD at "
                                              f"{self.host}:{self.port}: {exc}") from exc
            box["abandoned"] = True
        raise AccountUnavailableError(f"OpenD at {self.host}:{self.port} accepted the connection but did "
                                      f"not complete the handshake within {self.connect_timeout:g} s")

    def _query(self, name: str, what: str, **kwargs) -> pd.DataFrame:
        method = getattr(self._context(), name)
        try:
            ret, data = method(trd_env=self.trd_env, acc_id=self.acc_id, **kwargs)
        except Exception as exc:
            raise AccountUnavailableError(f"moomoo {what} ({self.market}): {exc}") from exc
        if ret != RET_OK:
            raise AccountUnavailableError(f"moomoo {what} ({self.market}) failed: {data}")
        return data

    # ── reads ────────────────────────────────────────────────────────

    def snapshot(self) -> AccountSnapshot:
        ts = ns_to_timestamp(self._now_ns())
        data = self._query("accinfo_query", "account info", currency=self.currency)
        if data is None or not len(data):
            raise AccountUnavailableError(f"moomoo account info ({self.market}) came back empty")
        row = data.iloc[0]

        def required(col):
            value = _num(row.get(col))
            if value is None:
                raise AccountUnavailableError(f"moomoo account info ({self.market}) has no {col}: {row.get(col)!r}")
            return value
        currency = str(row.get("currency") or "").strip().upper()
        return AccountSnapshot(
            currency=currency if currency and currency != "N/A" else self.currency,
            total_assets=required("total_assets"), cash=required("cash"),
            market_value=required("market_val"), ts=ts)

    def positions(self) -> List[PositionReport]:
        data = self._query("position_list_query", "positions")
        out = []
        for row in data.to_dict("records"):
            qty = _num(row.get("qty")) or 0.0
            if qty == 0:
                continue
            if str(row.get("position_side") or "").upper() == "SHORT":
                qty = -abs(qty)
            pnl = _num(row.get("unrealized_pl"))     # 'N/A' on some account types; then OpenD's P/L
            if pnl is None and bool(row.get("pl_val_valid", True)):
                pnl = _num(row.get("pl_val"))
            code = str(row["code"])
            out.append(PositionReport(
                symbol=code, quantity=qty,
                average_price=_positive(row.get("average_cost")) or _positive(row.get("cost_price")),
                market_price=_num(row.get("nominal_price")), market_value=_num(row.get("market_val")),
                unrealized_pnl=pnl, instrument_id=self._map(code)))
        return out

    def orders(self, days: int = 14) -> List[OrderReport]:
        """Today's orders (order_list_query) and the history (history_order_list_query) merged by
        order id, the live list winning; then only those created in the last `days` days."""
        if days < 0:
            raise ValueError(f"days must be >= 0, got {days}")
        now = ns_to_timestamp(self._now_ns())
        cutoff = now - timedelta(days=days)
        local = now.tz_convert(self.timezone)
        history = self._query("history_order_list_query", "order history",
                              start=(local - timedelta(days=days)).strftime("%Y-%m-%d"),
                              end=local.strftime("%Y-%m-%d"))
        today = self._query("order_list_query", "today's orders")
        rows: Dict[str, Dict[str, Any]] = {}
        for frame in (history, today):
            for row in frame.to_dict("records"):
                rows[str(row["order_id"])] = row
        reports = [self._order(oid, row) for oid, row in rows.items()]
        reports = [r for r in reports if r.created >= cutoff]
        return sorted(reports, key=lambda r: (r.created, r.order_id), reverse=True)

    # ── mapping ──────────────────────────────────────────────────────

    def _order(self, order_id: str, row: Dict[str, Any]) -> OrderReport:
        filled = _num(row.get("dealt_qty")) or 0.0
        return OrderReport(
            order_id=order_id, symbol=str(row["code"]), side=_side(row.get("trd_side")),
            quantity=_num(row.get("qty")) or 0.0, filled_quantity=filled,
            price=_positive(row.get("price")),                       # 0 on a market order: no limit price
            average_fill_price=_positive(row.get("dealt_avg_price")) if filled > 0 else None,
            status=str(row.get("order_status") or ""),
            created=self._utc(row.get("create_time")), updated=self._utc(row.get("updated_time"), optional=True))

    def _utc(self, local: Any, optional: bool = False) -> Optional[pd.Timestamp]:
        """An OpenD wall-clock string in the market's timezone, as UTC. In the hour a DST change
        repeats, the summer-time reading is taken."""
        try:
            ts = pd.Timestamp(local) if local not in (None, "", "N/A") else pd.NaT
        except (TypeError, ValueError):
            ts = pd.NaT
        if pd.isna(ts):
            if optional:
                return None
            raise AccountUnavailableError(f"moomoo order time {local!r} cannot be read")
        if ts.tzinfo is None:
            ts = ts.tz_localize(self.timezone, ambiguous=True, nonexistent="shift_forward")
        return ts.tz_convert("UTC")

    def _map(self, code: str) -> Optional[str]:
        if self._instrument_id is None:
            return None
        try:
            return self._instrument_id(code)
        except (ValueError, KeyError):   # a market or listing the venue table does not know
            return None


def _num(value: Any) -> Optional[float]:
    """A float, or None for the SDK's 'N/A', NaN, None or anything else not numeric."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(value: Any) -> Optional[float]:
    out = _num(value)
    return out if out is not None and out > 0 else None


def _side(trd_side: Any) -> str:
    """BUY and BUY_BACK -> BUY; SELL and SELL_SHORT -> SELL."""
    s = str(trd_side or "").upper()
    if s.startswith("BUY"):
        return "BUY"
    if s.startswith("SELL"):
        return "SELL"
    return s
