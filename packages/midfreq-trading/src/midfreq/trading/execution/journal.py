"""
OrderJournal: the OMS's append-only record of every order and everything that happened to it.

One file per UTC day, `orders_<YYYYMMDD>.jsonl`, in the directory the node gives it (normally
`MIDFREQ_ROOT/live_sessions/`). Each line is one JSON object with

    kind      'order' (an order as about to be sent, or as denied), 'event' (an order event the
              OMS received or generated, with `applied` and its `outcome` in the state machine,
              unknown orders included), or a step of the OMS's own: 'sent' (the venue has it),
              'cancel_request', 'policy' (an unfilled-order step: re-price, child, cancel, done),
              'venue_reply' (the answer to a query, or a refused call), 'reconcile'
    time      UTC wall-clock time of the write (ISO 8601), whatever clock the node runs on
    ts        the node clock's time in ns (the test clock in a replay)
    order     the order's full state after the record (where there is an order), including the
              venue's order id in its tags and the venue's message in `reason`

so `load()` can rebuild every order at start-up without replaying the state machine: the last
snapshot of an order is its state. A write is flushed to the operating system (and, with
`fsync=True`, to the disk) before the call returns, so a crash loses at most the line being
written; `load()` skips a torn last line.

Client order ids are `<strategy>-<YYYYMMDD>-<seq>`; `load()` also returns the highest sequence
used per strategy and day, so ids never repeat across restarts.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Optional, Set, Tuple

from midfreq.core.model.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from midfreq.core.model.events import OrderEvent, OrderFilled
from midfreq.core.model.identifiers import ClientOrderId, Identifier, InstrumentId, StrategyId
from midfreq.core.model.orders import Order

log = logging.getLogger("midfreq.trading.journal")

__all__ = ["JournalState", "OrderJournal", "format_order_id", "order_from_dict", "order_to_dict",
           "parse_order_id"]

_FILE_RE = re.compile(r"^orders_(\d{8})\.jsonl$")
_ORDER_ID_RE = re.compile(r"^(?P<strategy>\S+)-(?P<day>\d{8})-(?P<seq>\d+)$")


# ── client order ids ───────────────────────────────────────────────────────────────────────────

def format_order_id(strategy_id: "StrategyId | str", day: str, seq: int) -> ClientOrderId:
    """`<strategy>-<YYYYMMDD>-<seq>`, the sequence zero-padded to four digits (wider if needed)."""
    return ClientOrderId(f"{strategy_id}-{day}-{seq:04d}")


def parse_order_id(value: "ClientOrderId | str") -> Optional[Tuple[str, str, int]]:
    """(strategy, YYYYMMDD, seq) of an id in the OMS's format, or None for any other id."""
    m = _ORDER_ID_RE.match(str(value))
    if m is None:
        return None
    return m.group("strategy"), m.group("day"), int(m.group("seq"))


# ── serialisation ──────────────────────────────────────────────────────────────────────────────

def _plain(value: Any) -> Any:
    """A JSON-friendly form of a model value (identifiers and enums as their strings)."""
    if isinstance(value, (Identifier, InstrumentId)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def order_to_dict(order: Order) -> Dict[str, Any]:
    return {
        "client_order_id": str(order.client_order_id),
        "strategy_id": str(order.strategy_id),
        "instrument_id": str(order.instrument_id),
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": order.quantity,
        "price": order.price,
        "time_in_force": order.time_in_force.value,
        "reference_price": order.reference_price,
        "tags": _plain(dict(order.tags)),
        "ts_init": order.ts_init,
        "status": order.status.value,
        "filled_qty": order.filled_qty,
        "avg_px": order.avg_px,
        "commission": order.commission,
        "reason": order.reason,
        "ts_last": order.ts_last,
    }


def order_from_dict(d: Dict[str, Any]) -> Order:
    return Order(
        client_order_id=ClientOrderId(d["client_order_id"]),
        strategy_id=StrategyId(d["strategy_id"]),
        instrument_id=InstrumentId.from_str(d["instrument_id"]),
        side=OrderSide(d["side"]),
        order_type=OrderType(d["order_type"]),
        quantity=float(d["quantity"]),
        price=None if d.get("price") is None else float(d["price"]),
        time_in_force=TimeInForce(d.get("time_in_force", TimeInForce.GTC.value)),
        reference_price=None if d.get("reference_price") is None else float(d["reference_price"]),
        tags=dict(d.get("tags") or {}),
        ts_init=int(d.get("ts_init") or 0),
        status=OrderStatus(d.get("status", OrderStatus.INITIALIZED.value)),
        filled_qty=float(d.get("filled_qty") or 0.0),
        avg_px=None if d.get("avg_px") is None else float(d["avg_px"]),
        commission=float(d.get("commission") or 0.0),
        reason=d.get("reason"),
        ts_last=int(d.get("ts_last") or 0),
    )


def event_to_dict(event: OrderEvent) -> Dict[str, Any]:
    d: Dict[str, Any] = {"event": type(event).__name__}
    for f in dataclasses.fields(event):
        d[f.name] = _plain(getattr(event, f.name))
    return d


# ── what load() returns ────────────────────────────────────────────────────────────────────────

@dataclass
class JournalState:
    """Everything the OMS needs back at start-up."""
    orders: Dict[ClientOrderId, Order] = field(default_factory=dict)   # in the order first seen
    # (venue, venue order id or else client order id, trade id) of every applied fill: the OMS's
    # fill key (OrderManagementSystem.fill_key)
    trade_ids: Set[Tuple[str, str, str]] = field(default_factory=set)
    sequences: Dict[Tuple[str, str], int] = field(default_factory=dict)  # (strategy, day) -> highest seq
    # every applied fill, oldest first: the event's fields, `ts` (when the OMS applied it, node
    # clock) and `order_tags` (the order's tags then: decision_ts, legacy_symbol, exit_reason)
    fills: List[Dict[str, Any]] = field(default_factory=list)
    records: int = 0
    bad_lines: int = 0
    files: List[Path] = field(default_factory=list)

    def open_orders(self) -> List[Order]:
        return [o for o in self.orders.values() if o.is_open and o.status is not OrderStatus.INITIALIZED]


# ── the journal ────────────────────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderJournal:
    def __init__(self, directory: "str | os.PathLike[str]", fsync: bool = False,
                 wall_clock: Callable[[], datetime] = _utc_now):
        self.directory = Path(directory)
        self.fsync = fsync
        self._wall_clock = wall_clock
        self._file: Optional[IO[str]] = None
        self._file_day: Optional[str] = None
        self.written = 0

    # ── writing ───────────────────────────────────────────────────────────────────────────────

    def path_for(self, day: str) -> Path:
        return self.directory / f"orders_{day}.jsonl"

    @property
    def path(self) -> Path:
        """Today's file (UTC wall-clock date)."""
        return self.path_for(self._wall_clock().astimezone(timezone.utc).strftime("%Y%m%d"))

    def _handle(self, now: datetime) -> IO[str]:
        day = now.strftime("%Y%m%d")
        if self._file is None or day != self._file_day:
            self.close()
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.path_for(day)
            torn = False
            if path.exists() and path.stat().st_size > 0:
                with open(path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    torn = f.read(1) != b"\n"
            self._file = open(path, "a", encoding="utf-8")
            if torn:                                   # a crash mid-write: end that line first
                self._file.write("\n")
            self._file_day = day
        return self._file

    def write(self, kind: str, ts_ns: int, order: Optional[Order] = None, **fields: Any) -> Dict[str, Any]:
        """Append one record and return it. Raises OSError if the line could not be written."""
        now = self._wall_clock().astimezone(timezone.utc)
        record: Dict[str, Any] = {"kind": kind, "time": now.isoformat(timespec="microseconds"),
                                  "ts": int(ts_ns)}
        if order is not None:
            record["client_order_id"] = str(order.client_order_id)
            record["strategy_id"] = str(order.strategy_id)
            record["instrument_id"] = str(order.instrument_id)
            venue_order_id = order.tags.get("venue_order_id")
            if venue_order_id is not None:
                record["venue_order_id"] = str(venue_order_id)
        for key, value in fields.items():
            record[key] = _plain(value)
        if order is not None:
            record["order"] = order_to_dict(order)
        line = json.dumps(record, default=str)
        f = self._handle(now)
        f.write(line + "\n")
        f.flush()
        if self.fsync:
            os.fsync(f.fileno())
        self.written += 1
        return record

    def record_order(self, order: Order, ts_ns: int, note: str = "") -> Dict[str, Any]:
        """An order as the OMS is about to send it (written before it is sent) or as denied."""
        return self.write("order", ts_ns, order, note=note) if note else self.write("order", ts_ns, order)

    def record_event(self, event: OrderEvent, ts_ns: int, order: Optional[Order] = None,
                     applied: bool = True, outcome: str = "", previous_status: Optional[str] = None,
                     message: str = "") -> Dict[str, Any]:
        fields: Dict[str, Any] = {"event": event_to_dict(event), "applied": applied}
        if outcome:
            fields["outcome"] = outcome
        if previous_status is not None:
            fields["previous_status"] = previous_status
        if message:
            fields["message"] = message
        if order is None:
            fields["client_order_id"] = str(event.client_order_id)
            fields["strategy_id"] = str(event.strategy_id)
            fields["instrument_id"] = str(event.instrument_id)
        return self.write("event", ts_ns, order, **fields)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
                self._file_day = None

    # ── reading ───────────────────────────────────────────────────────────────────────────────

    def files(self, lookback_days: Optional[int] = None) -> List[Path]:
        """Journal files oldest first; with `lookback_days`, only those dated within that many
        days of today (UTC wall clock)."""
        if not self.directory.is_dir():
            return []
        found = sorted((m.group(1), p) for p in self.directory.iterdir()
                       if (m := _FILE_RE.match(p.name)) is not None)
        if lookback_days is not None:
            first = (self._wall_clock().astimezone(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y%m%d")
            found = [(d, p) for d, p in found if d >= first]
        return [p for _, p in found]

    def load(self, lookback_days: Optional[int] = 30) -> JournalState:
        """Rebuild the orders (each from its last snapshot), the applied fills' trade ids and the
        id sequences from the journal files. Unreadable lines are skipped and counted."""
        state = JournalState()
        for path in self.files(lookback_days):
            state.files.append(path)
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        self._absorb(state, record)
                    except (ValueError, KeyError, TypeError) as exc:
                        state.bad_lines += 1
                        log.warning("order journal %s line %d unreadable (%s); skipped", path.name, n, exc)
                        continue
                    state.records += 1
        if state.files:
            log.info("order journal: %d records from %d files, %d orders (%d open)", state.records,
                     len(state.files), len(state.orders), len(state.open_orders()))
        return state

    @staticmethod
    def _absorb(state: JournalState, record: Dict[str, Any]) -> None:
        snapshot = record.get("order")
        if snapshot is not None:
            order = order_from_dict(snapshot)
            state.orders[order.client_order_id] = order
        for key in ("client_order_id",):
            cid = record.get(key) or (snapshot or {}).get(key)
            if cid:
                parsed = parse_order_id(cid)
                if parsed is not None:
                    strategy, day, seq = parsed
                    k = (strategy, day)
                    if seq > state.sequences.get(k, 0):
                        state.sequences[k] = seq
        event = record.get("event")
        if (record.get("kind") == "event" and record.get("applied") and isinstance(event, dict)
                and event.get("event") == OrderFilled.__name__ and event.get("trade_id")):
            venue = InstrumentId.from_str(event["instrument_id"]).venue.value
            order_key = (record.get("venue_order_id") or (event.get("tags") or {}).get("venue_order_id")
                         or event.get("client_order_id"))
            key = (venue, str(order_key), str(event["trade_id"]))
            if key not in state.trade_ids:
                state.fills.append({**event, "ts": int(record.get("ts") or 0),
                                    "order_tags": dict((snapshot or {}).get("tags") or {})})
            state.trade_ids.add(key)
