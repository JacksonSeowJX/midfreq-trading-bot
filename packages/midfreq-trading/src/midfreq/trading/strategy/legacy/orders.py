"""
A candle strategy's execute_trade -> an order for the OMS: the trade rounded to the lot, priced
at the live market under the pricing rule, trimmed to the strategy's cash or holding, sent as one
LIMIT DAY order, and (with confirm_fills) waited on until its family has its outcome. Also the
rebalance bookkeeping done before a rebalance's first order leaves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from midfreq.core.model.enums import OrderSide, TimeInForce
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId
from midfreq.core.model.orders import Order
from midfreq.trading.ops.logs import log_context
from midfreq.trading.strategy.legacy.portfolio import round_to_tick


@dataclass
class _OrderMeta:
    order: Order
    symbol: str
    is_buy: bool
    decision_ts: Any
    exit_reason: Optional[str]
    filled: float = 0.0
    resolved: bool = False
    reason: Optional[str] = None


class OrderRoutingMixin:
    """LiveLegacyAdapter's orders: what LiveLegacyPortfolio.execute_trade calls (`_route_trade`),
    and how the adapter follows an order to its outcome.

    Relies on the adapter's settings, services, strategy, portfolio, instrument_for, order_factory,
    id, log, on_order_sent, orders_sent, _orders, _by_object and _rebalance_marked, and on its
    save_state()."""

    # ── execute_trade -> an order ─────────────────────────────────────────────────────────────
    def _price_ref(self, symbol: str) -> Optional[float]:
        iid = self.instrument_for.get(symbol)
        return self.services.latest_price(iid) if iid is not None else None

    def _limit_price(self, iid: InstrumentId, is_buy: bool, ref: float) -> Tuple[float, float]:
        """(limit price, worst-case cost per share before commission) under the pricing rule."""
        rule, slip = self.settings.pricing.rule, self.settings.pricing.slip
        base = ref
        if rule in ("quote", "mid"):
            q = self.services.quote(iid)
            if q is not None and q.bid > 0 and q.ask > 0:
                base = (q.ask if is_buy else q.bid) if rule == "quote" else q.mid
        raw = base * (1 + slip if is_buy else 1 - slip)
        tick = None
        try:
            if self.services.tick_size is not None:
                tick = self.services.tick_size(iid, raw)            # HKEX: the tick of the price's band
            else:
                inst = self.services.instrument(iid)
                tick = inst.price_increment if inst is not None else None
        except Exception:
            tick = None
        limit = round_to_tick(raw, tick)
        return limit, raw

    def _route_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason) -> None:
        p = self.portfolio
        side = "BUY" if is_buy else "SELL"
        iid = self.instrument_for.get(symbol)
        if iid is None:
            self.log.error("strategy traded %s, which is not in its universe; ignored", symbol)
            return
        lot = p.lot_sizes.get(symbol)
        if is_buy and lot and lot > 1:
            rounded = int(qty // lot) * lot
            if rounded != qty:
                if rounded <= 0:
                    self.log.info("[%s] SKIPPED BUY %s %s: below board lot of %s", timestamp, qty, symbol, lot)
                    return
                self.log.info("[%s] Lot-adjusted BUY %s: %s -> %s (lot %s)", timestamp, symbol, qty, rounded, lot)
                qty = rounded
        try:
            ref = p.price_ref(symbol) if p.price_ref is not None else None
        except Exception:
            ref = None
        ref = ref or price
        limit, worst_px = self._limit_price(iid, is_buy, ref)
        if is_buy:
            # Size against the worst-case fill (the limit, plus commission), so a fill can never be
            # refused by the strategy's own cash check and leave it and the broker disagreeing.
            worst = worst_px * (1 + p.commission_rate)
            affordable = int(p.cash // worst) if worst > 0 else 0
            if lot and lot > 1:
                affordable = (affordable // lot) * lot
            if affordable < qty:
                self.log.info("[%s] BUY %s trimmed %s -> %s to fit cash at the limit", timestamp, symbol, qty, affordable)
                qty = affordable
            if qty <= 0:
                return
        else:
            qty = min(qty, p.get_position_qty(symbol))
            if qty <= 0:
                return
        tags = {"decision_ts": str(timestamp), "legacy_symbol": symbol}
        if exit_reason:
            tags["exit_reason"] = exit_reason
        self._mark_rebalance_in_progress()
        # DAY: a Moomoo GTC order would outlive the session (config.validate refuses anything else).
        order = self.order_factory.limit(iid, OrderSide.BUY if is_buy else OrderSide.SELL, qty, limit,
                                         TimeInForce.DAY, tags)
        if self.services.next_order_id is not None:
            # The OMS's own id, taken before the record is stored, so the OMS never renames it.
            order.tags["strategy_order_id"] = str(order.client_order_id)
            order.client_order_id = self.services.next_order_id(self.id)
        order.reference_price = float(price)
        meta = _OrderMeta(order, symbol, is_buy, timestamp, exit_reason)
        self._orders[order.client_order_id] = meta
        self._by_object[id(order)] = meta
        self.orders_sent += 1
        with log_context(client_order_id=str(order.client_order_id), strategy=str(self.id)):
            self.log.info("[%s] %s %g %s limit %s (live %s, decided at %s)", timestamp, side, qty, symbol,
                          limit, ref, price)
            if self.on_order_sent is not None:
                self.on_order_sent(order)
            self.services.submit(order)
            if not self.settings.confirm_fills:
                return
            done = self.services.wait_until(lambda: self._resolved(meta), self.settings.confirm_timeout_s,
                                            lambda: self._give_up(meta))
            if not done:
                self.log.warning("[%s] %s %s %s still open after %.0fs; its fills will be booked when they arrive",
                                 timestamp, side, qty, symbol, self.settings.confirm_timeout_s)
            if meta.filled <= 0 and meta.resolved:
                self.log.warning("[%s] NOT FILLED %s %s %s (limit %s): %s — nothing booked",
                                 timestamp, side, qty, symbol, limit, meta.reason or "no fill")
            elif 0 < meta.filled < qty and meta.resolved:
                self.log.warning("[%s] PARTIAL %s: %g of %g filled, remainder cancelled", timestamp, symbol,
                                 meta.filled, qty)

    def _rebalance_due_after(self) -> Optional[int]:
        """While the strategy is running a rebalance (due at the cross-section it just counted),
        the next one's due count, as the strategy will set it once this one returns."""
        s = self.strategy
        count, nra = getattr(s, "_cross_section_count", None), getattr(s, "_next_rebalance_at", None)
        if count is None or nra is None or count < nra:
            return None
        n = max(1, int(getattr(s, "rebalance_every", 1) or 1))
        while nra <= count:
            nra += n
        return nra

    def _mark_rebalance_in_progress(self) -> None:
        """Before a rebalance's first order leaves, save the state with that rebalance done: a
        crash in the middle of it must not make the next session run it again over the orders
        already sent (the journal and the broker have those)."""
        count = getattr(self.strategy, "_cross_section_count", None)
        if self._rebalance_due_after() is None or self._rebalance_marked == count:
            return
        self._rebalance_marked = count
        try:
            self.save_state()
        except Exception as exc:
            self.log.error("state save before the rebalance's first order failed: %s", exc)

    def _resolved(self, meta: _OrderMeta) -> bool:
        """Whether the order's intent has its outcome: its family done at the OMS (every order of
        it closed, no re-priced child still to come); without an OMS that reports families, the
        order's own terminal event or complete fill."""
        if meta.resolved:
            return True
        family_done = self.services.family_done
        if family_done is not None:
            try:
                meta.resolved = bool(family_done(meta.order.client_order_id))
            except Exception as exc:
                self.log.error("cannot ask the OMS about %s: %s", meta.order.client_order_id, exc)
        return meta.resolved

    def _meta_for(self, client_order_id: ClientOrderId) -> Optional[_OrderMeta]:
        """The intent an order event belongs to: the order itself (under its own id, or the id
        the OMS gave it), or the order its family started from (a re-priced child)."""
        meta = self._orders.get(client_order_id)
        if meta is not None or self.services.order is None:
            return meta
        order = self.services.order(client_order_id)
        if order is None:
            return None
        meta = self._by_object.get(id(order))
        if meta is None and order.tags.get("root"):
            root = self.services.order(ClientOrderId(str(order.tags["root"])))
            meta = self._by_object.get(id(root)) if root is not None else None
        if meta is not None:
            self._orders[client_order_id] = meta
        return meta

    def _give_up(self, meta: _OrderMeta) -> None:
        """The confirm wait ran out: ask the OMS to cancel what is left."""
        if not self._resolved(meta):
            self.log.warning("order %s not resolved in time: cancelling the remainder", meta.order.client_order_id)
            try:
                self.services.cancel(meta.order.client_order_id)
            except Exception as exc:
                self.log.error("cancel of %s failed: %s", meta.order.client_order_id, exc)
