"""
Fills -> a candle strategy's book: each OrderFilled on the strategy's order topic booked once, by
the original Portfolio bookkeeping, written to the session log and saved; terminal order events
recorded on the order's intent. Also, at resume, the fills the OMS journaled after the state was
last saved, booked into that state before it is resumed against the broker.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from midfreq.core.model.enums import OrderSide
from midfreq.core.model.events import (
    OrderAccepted, OrderCanceled, OrderDenied, OrderEvent, OrderFilled, OrderRejected,
)
from midfreq.core.model.identifiers import InstrumentId
from midfreq.core.portfolio import Portfolio as CorePortfolio
from midfreq.trading.ops.logs import log_context
from midfreq.trading.strategy.legacy.orders import _OrderMeta

_TERMINAL = (OrderCanceled, OrderRejected, OrderDenied)


class FillBookingMixin:
    """LiveLegacyAdapter's fills: the order events of its strategy (`_on_order_event`), and the
    journaled fills its saved state lacks (`_replay_unbooked_fills`).

    Relies on the adapter's services, portfolio, session_log, symbol_for, commission_rate,
    capital_cap, id, log, _booked and _recovered, and on its _meta_for() and save_state()."""

    # ── fills -> the strategy's book ─────────────────────────────────────────────────────────
    def _on_order_event(self, event: OrderEvent) -> None:
        meta = self._meta_for(event.client_order_id)
        if isinstance(event, OrderFilled):
            self._book_fill(event, meta)
        elif isinstance(event, _TERMINAL):
            if meta is not None:
                meta.reason = getattr(event, "reason", "") or type(event).__name__
                if self.services.family_done is None:
                    meta.resolved = True
            level = logging.WARNING if isinstance(event, (OrderRejected, OrderDenied)) else logging.INFO
            with log_context(client_order_id=str(event.client_order_id), strategy=str(self.id)):
                self.log.log(level, "%s %s: %s", type(event).__name__, event.client_order_id,
                             getattr(event, "reason", ""))
        elif isinstance(event, OrderAccepted):
            pass

    def _book_fill(self, fill: OrderFilled, meta: Optional[_OrderMeta]) -> None:
        key = (str(fill.client_order_id), str(fill.trade_id) if fill.trade_id is not None
               else f"{fill.ts_event}:{fill.last_qty}:{fill.last_px}")
        if key in self._booked:
            return
        self._booked.add(key)
        symbol = meta.symbol if meta is not None else self.symbol_for.get(fill.instrument_id)
        if symbol is None:
            self.log.error("fill for %s, which is not in this strategy's universe: not booked", fill.instrument_id)
            return
        is_buy = fill.side is OrderSide.BUY
        order_tags: Mapping[str, Any] = {}
        if meta is None and self.services.order is not None:
            # An order of a previous session (resumed by the OMS): its decision is in its tags.
            known = self.services.order(fill.client_order_id)
            order_tags = known.tags if known is not None else {}
        decision_ts = meta.decision_ts if meta is not None else order_tags.get("decision_ts", "")
        exit_reason = meta.exit_reason if meta is not None else order_tags.get("exit_reason")
        qty, px = fill.last_qty, fill.last_px
        with log_context(client_order_id=str(fill.client_order_id), strategy=str(self.id)):
            ok = self.portfolio.book(symbol, is_buy, qty, px, decision_ts, exit_reason)
            if not ok:
                # The original bookkeeping leaves an empty entry behind a refused sell; drop it so
                # the state file never records a zero position.
                pos = self.portfolio.positions.get(symbol)
                if pos is not None and pos.get("qty", 0) == 0:
                    del self.portfolio.positions[symbol]
                msg = (f"filled {'BUY' if is_buy else 'SELL'} {qty:g} {symbol} @ {px} could not be booked "
                       f"(the strategy's cash or position refused it): the bot and the broker now disagree")
                self.log.error(msg)
                self.services.alert(f"{self.id}: fill not booked", msg)
            if meta is not None:
                meta.filled += qty
                if self.services.family_done is None and meta.filled >= meta.order.quantity - 1e-12:
                    meta.resolved = True
            order_id = fill.tags.get("venue_order_id") or (meta.order.tags.get("venue_order_id") if meta else None) \
                or str(fill.client_order_id)
            self.log.info("[%s] %s %g %s @ %s (order %s%s)", decision_ts, "BUY" if is_buy else "SELL", qty, symbol,
                          px, order_id, f", {exit_reason}" if exit_reason else "")
            if ok and self.session_log is not None:
                self.session_log.trade(decision_ts, symbol, "BUY" if is_buy else "SELL", qty, px, order_id, exit_reason)
            try:
                self.save_state()
            except Exception as exc:
                self.log.error("state save after a fill failed: %s", exc)

    # ── fills journaled but never booked ─────────────────────────────────────────────────────
    def _replay_unbooked_fills(self, saved: Dict[str, Any], opening_cash: float = 0.0) -> Dict[str, Any]:
        """Fills the OMS journaled after the state was last saved never reached the strategy's
        book (the process died between the two, or they were found by the start-up
        reconciliation while the node was down): book them into the saved state, with the
        original bookkeeping, before it is resumed against the broker."""
        if self.services.journal_fills is None or self.services.local_to_ns is None:
            return saved
        try:
            saved_ns = self.services.local_to_ns(pd.Timestamp(saved["saved_at"]).to_pydatetime())
        except (KeyError, ValueError, TypeError):
            self.log.warning("[resume] the state's saved_at %r cannot be read: journaled fills not checked",
                             saved.get("saved_at"))
            return saved
        late = [f for f in self.services.journal_fills(self.id) if f["ts"] > saved_ns]
        if not late:
            return saved
        start_cash = float(saved["cash"]) if saved.get("cash") is not None else float(opening_cash)
        book = CorePortfolio(initial_cash=start_cash, commission_rate=self.commission_rate)
        book.positions = {s: dict(p) for s, p in (saved.get("positions") or {}).items()}
        book.cash = start_cash
        for f in late:
            tags = f.get("order_tags") or {}
            symbol = tags.get("legacy_symbol") or self.symbol_for.get(InstrumentId.from_str(f["instrument_id"]))
            if symbol is None:
                self.log.error("[resume] journaled fill %s for %s is not in this universe: not booked",
                               f.get("trade_id"), f.get("instrument_id"))
                continue
            is_buy = str(f.get("side")) == "BUY"
            book.execute_trade(symbol, is_buy, float(f["last_qty"]), float(f["last_px"]), tags.get("decision_ts", ""),
                               exit_reason=tags.get("exit_reason"))
            self.log.warning("[resume] %s %g %s @ %s (trade %s) was journaled after the state was saved: booked now",
                             "BUY" if is_buy else "SELL", float(f["last_qty"]), symbol, f["last_px"], f.get("trade_id"))
            self._recovered.append({"timestamp": tags.get("decision_ts", ""), "symbol": symbol,
                                    "side": "BUY" if is_buy else "SELL", "qty": float(f["last_qty"]),
                                    "price": float(f["last_px"]), "order_id": (f.get("tags") or {}).get("venue_order_id")
                                    or f.get("client_order_id"), "exit_reason": tags.get("exit_reason")})
        out = dict(saved)
        out.pop("_empty", None)
        out["positions"] = book.positions
        if saved.get("cash") is not None or self.capital_cap is not None:
            out["cash"] = book.cash
        return out
