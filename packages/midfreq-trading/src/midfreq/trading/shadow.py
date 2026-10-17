"""
Shadow mode: the node runs exactly as it would trade, on the real market data, but every order
stops here. `ShadowExecutionClient` is an ExecutionClient that never calls a venue: it logs each
intended order, appends it to `shadow_<YYYYmmdd>.jsonl` in the node's records folder, and
answers as a venue would, so the OMS, the strategies and the records carry on normally.

Two behaviours (`fill_mode`):

- `none` (default): the order is acknowledged and then cancelled at once ("shadow: not sent").
  Nothing is ever filled, so the strategies' positions never change; the decisions, the order
  sizes and the limit prices are all in the log.
- `quote`: the order is filled in full at the current quote (the ask for a buy, the bid for a
  sell, or the last price when there is no quote) when its limit is marketable there, and
  otherwise left working until the OMS cancels it. The strategies then evolve as if trading,
  in the shadow records folder, which is never the live one.

`environment` is 'sim': nothing this client does can reach a broker.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from midfreq.core.model.enums import OrderSide, OrderType
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderFilled
from midfreq.core.model.identifiers import AccountId, TradeId
from midfreq.core.model.orders import Order
from midfreq.data.adapters.base import ExecutionClient, FillReport, OrderReport
from midfreq.trading.ops import get_logger, log_context

log = get_logger("shadow")

FILL_MODES = ("none", "quote")

# (bid, ask, last) for an instrument; any may be None.
PriceSource = Callable[[Any], Tuple[Optional[float], Optional[float], Optional[float]]]


@dataclass
class _Working:
    order: Order
    venue_order_id: str
    filled: float = 0.0


class ShadowExecutionClient(ExecutionClient):
    """Logs and journals intended orders; never sends one."""

    def __init__(self, account: str = "shadow", *, fill_mode: str = "none",
                 price_source: Optional[PriceSource] = None,
                 now_ns: Callable[[], int] = lambda: int(datetime.now(timezone.utc).timestamp() * 1e9),
                 journal_dir: Optional["str | Path"] = None, commission_rate: float = 0.0,
                 connector: Any = None):
        if fill_mode not in FILL_MODES:
            raise ValueError(f"fill_mode must be one of {FILL_MODES}, got {fill_mode!r}")
        super().__init__(connector or SimpleNamespace(name="shadow"), {"account": account, "fill_mode": fill_mode})
        self.account = account
        self.fill_mode = fill_mode
        self.price_source = price_source
        self._now_ns = now_ns
        self.journal_dir = Path(journal_dir) if journal_dir is not None else None
        self.commission_rate = commission_rate
        self._ids = itertools.count(1)
        self._trades = itertools.count(1)
        self._orders: Dict[str, _Working] = {}
        self._fills: List[FillReport] = []
        self.intents: List[Dict[str, Any]] = []
        self.connected = False

    # ── ExecutionClient ──────────────────────────────────────────────────────────────────────
    @property
    def environment(self) -> str:
        return "sim"

    @property
    def account_id(self) -> AccountId:
        return AccountId(f"SHADOW-{self.account}")

    def connect(self) -> None:
        self.connected = True
        log.info("shadow execution for account %s: orders are logged, never sent (fill mode %s)",
                 self.account, self.fill_mode)

    def disconnect(self) -> None:
        self.connected = False

    def submit_order(self, order: Order) -> None:
        voi = f"SHADOW-{next(self._ids)}"
        order.tags["venue_order_id"] = voi
        w = _Working(order, voi)
        self._orders[voi] = w
        intent = self._record("intent", order, voi)
        self.intents.append(intent)
        with log_context(client_order_id=str(order.client_order_id), strategy=str(order.strategy_id)):
            px = f" limit {order.price:g}" if order.price is not None else ""
            ref = f" (decided at {order.reference_price:g})" if order.reference_price is not None else ""
            log.info("SHADOW %s %g %s %s%s%s: not sent", order.side.value, order.quantity,
                     order.instrument_id, order.order_type.value, px, ref)
        now = self._now_ns()
        self._emit(OrderAccepted(order.client_order_id, order.strategy_id, order.instrument_id, now))
        if self.fill_mode == "none":
            self._cancel(w, "shadow: not sent")
            return
        self._try_fill(w)

    def cancel_order(self, order: Order) -> None:
        w = self._find(order)
        if w is None or w.filled >= w.order.quantity:
            return
        self._cancel(w, "shadow: cancelled on request")

    def query_order(self, order: Order) -> Optional[OrderReport]:
        w = self._find(order)
        return self._report(w) if w is not None else None

    def open_orders(self) -> List[OrderReport]:
        return [self._report(w) for w in self._orders.values() if w.filled < w.order.quantity
                and w.order.tags.get("shadow_state") != "cancelled"]

    def fills(self, since: pd.Timestamp) -> List[FillReport]:
        since = pd.Timestamp(since)
        if since.tzinfo is None:
            since = since.tz_localize("UTC")
        return [f for f in self._fills if f.ts >= since]

    # ── simulated behaviour ──────────────────────────────────────────────────────────────────
    def on_quote(self, instrument_id: Any = None) -> None:
        """Try again to fill working orders (all, or one instrument's) at the current quote."""
        if self.fill_mode != "quote":
            return
        for w in list(self._orders.values()):
            if w.filled < w.order.quantity and w.order.tags.get("shadow_state") != "cancelled":
                if instrument_id is None or w.order.instrument_id == instrument_id:
                    self._try_fill(w)

    def _try_fill(self, w: _Working) -> None:
        order = w.order
        bid = ask = last = None
        if self.price_source is not None:
            try:
                bid, ask, last = self.price_source(order.instrument_id)
            except Exception:
                bid = ask = last = None
        px = (ask if order.side is OrderSide.BUY else bid) or last
        if px is None or px <= 0:
            return
        if order.order_type is OrderType.LIMIT and order.price is not None:
            if (order.side is OrderSide.BUY and px > order.price) or (order.side is OrderSide.SELL and px < order.price):
                return
        qty = order.quantity - w.filled
        w.filled += qty
        trade_id = f"SHADOW-T{next(self._trades)}"
        now = self._now_ns()
        commission = qty * px * self.commission_rate
        self._fills.append(FillReport(trade_id, w.venue_order_id, str(order.instrument_id), order.side.value,
                                      qty, px, pd.Timestamp(now, unit="ns", tz="UTC"), commission,
                                      client_order_id=str(order.client_order_id),
                                      instrument_id=str(order.instrument_id)))
        self._record("fill", order, w.venue_order_id, qty=qty, price=px)
        self._emit(OrderFilled(order.client_order_id, order.strategy_id, order.instrument_id, now,
                               trade_id=TradeId(trade_id), side=order.side, last_qty=qty, last_px=px,
                               commission=commission, tags={"venue_order_id": w.venue_order_id, "shadow": True}))

    def _cancel(self, w: _Working, reason: str) -> None:
        w.order.tags["shadow_state"] = "cancelled"
        self._record("cancel", w.order, w.venue_order_id, reason=reason)
        self._emit(OrderCanceled(w.order.client_order_id, w.order.strategy_id, w.order.instrument_id,
                                 self._now_ns(), reason=reason))

    def _find(self, order: Order) -> Optional[_Working]:
        voi = order.tags.get("venue_order_id")
        if voi in self._orders:
            return self._orders[voi]
        for w in self._orders.values():
            if w.order.client_order_id == order.client_order_id:
                return w
        return None

    def _report(self, w: _Working) -> OrderReport:
        o = w.order
        status = ("CANCELLED" if o.tags.get("shadow_state") == "cancelled"
                  else "FILLED_ALL" if w.filled >= o.quantity else "SUBMITTED")
        return OrderReport(w.venue_order_id, str(o.instrument_id), o.side.value, o.quantity, w.filled,
                           o.price, None, status, pd.Timestamp(o.ts_init or self._now_ns(), unit="ns", tz="UTC"),
                           client_order_id=str(o.client_order_id), remark="shadow")

    def _record(self, event: str, order: Order, venue_order_id: str, **extra: Any) -> Dict[str, Any]:
        now = self._now_ns()
        ts = datetime.fromtimestamp(now / 1e9, tz=timezone.utc)
        row = {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond:06d}Z",
            "event": event, "account": self.account, "client_order_id": str(order.client_order_id),
            "venue_order_id": venue_order_id, "strategy_id": str(order.strategy_id),
            "instrument_id": str(order.instrument_id), "side": order.side.value,
            "order_type": order.order_type.value, "quantity": order.quantity, "price": order.price,
            "reference_price": order.reference_price, "time_in_force": order.time_in_force.value,
            "tags": {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
                     for k, v in order.tags.items()},
        }
        row.update(extra)
        if self.journal_dir is not None:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            with open(self.journal_dir / f"shadow_{ts:%Y%m%d}.jsonl", "a") as f:
                f.write(json.dumps(row, default=str) + "\n")
        return row
