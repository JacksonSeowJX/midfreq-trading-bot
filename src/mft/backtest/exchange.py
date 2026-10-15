"""
SimulatedExchange: one per venue, after NautilusTrader's `backtest.exchange`.

It applies the venue's rules to every order the RiskEngine lets through: quantities are rounded
down to whole board lots (or crypto size steps), orders below a minimum notional are refused, fees
come from the venue's fee model, and a cash account cannot go negative or sell what it does not
hold (no margin, no short selling in this version). Market orders fill according to the venue's
FillModel; limit orders rest and fill when a later bar trades through the limit; DAY orders are
cancelled when the venue's local trading date rolls over.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pandas as pd

from mft.backtest.models import CLOSE, REFERENCE, FillModel
from mft.model.data import Bar
from mft.model.enums import LiquiditySide, OrderSide, OrderType, TimeInForce
from mft.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from mft.model.fees import FeeModel
from mft.model.identifiers import InstrumentId, TradeId
from mft.model.orders import Order
from mft.venues.spec import VenueSpec


class SimulatedExchange:
    def __init__(self, spec: VenueSpec, fill_model: FillModel, fee_model: FeeModel,
                 cache, portfolio, clock, emit: Callable[[object], None]):
        self.spec = spec
        self.venue = spec.venue
        self.fill_model = fill_model
        self.fee_model = fee_model
        self._cache = cache
        self._portfolio = portfolio
        self._clock = clock
        self._emit = emit
        self._resting: List[Order] = []
        self._last_bar: Dict[InstrumentId, Bar] = {}
        self._trade_count = 0
        self._day_orders: Dict[Order, pd.Timestamp] = {}   # resting DAY orders -> local trading date

    # ── orders in ────────────────────────────────────────────────────

    def process_order(self, order: Order) -> None:
        instrument = self._cache.instrument(order.instrument_id)
        if self.fill_model.round_lots:
            qty = instrument.make_qty(order.quantity)
            if qty <= 0:
                return self._reject(order, f"quantity {order.quantity:g} is below one lot "
                                           f"({instrument.quantity_step:g}) of {instrument.id}")
            if qty != order.quantity:
                order.tags.setdefault("requested_qty", order.quantity)
                order.quantity = qty

        if order.order_type is OrderType.MARKET:
            if self.fill_model.timing == REFERENCE:
                base = order.reference_price if order.reference_price is not None else self._last_close(order)
                if base is None:
                    return self._reject(order, f"no price for {order.instrument_id}")
                return self._fill(order, self.fill_model.price(base, order.side), LiquiditySide.TAKER)
            if self.fill_model.timing == CLOSE:
                base = self._last_close(order)
                if base is None:
                    return self._reject(order, f"no market yet for {order.instrument_id}")
                return self._fill(order, self.fill_model.price(base, order.side), LiquiditySide.TAKER)
            # NEXT_OPEN: wait for the instrument's next bar
            if order.time_in_force is TimeInForce.IOC:
                return self._cancel(order, "IOC market order cannot wait for the next bar")
            self._accept(order)
            return None

        # LIMIT
        last = self._last_close(order)
        marketable = last is not None and (last <= order.price if order.side is OrderSide.BUY else last >= order.price)
        if order.time_in_force is TimeInForce.IOC:
            if marketable:
                return self._fill(order, last, LiquiditySide.TAKER)
            return self._cancel(order, "IOC limit order not marketable")
        self._accept(order)
        return None

    def cancel_order(self, order: Order, reason: str = "cancelled by strategy") -> None:
        if order in self._resting:
            self._cancel(order, reason)

    # ── bars in ──────────────────────────────────────────────────────

    def process_bar(self, bar: Bar) -> None:
        """A new bar for one of this venue's instruments: expire DAY orders on a new trading day,
        then fill resting orders the bar reaches. Runs before strategies see the bar."""
        if self._day_orders:
            today = self._local_date_of(bar.ts_event)
            for order in [o for o, day in self._day_orders.items() if day < today]:
                self._cancel(order, "DAY order expired at the end of the trading day")
        self._last_bar[bar.bar_type.instrument_id] = bar
        if not self._resting:
            return
        for order in [o for o in self._resting if o.instrument_id == bar.bar_type.instrument_id]:
            if order.order_type is OrderType.MARKET:
                self._fill(order, self.fill_model.price(bar.open, order.side), LiquiditySide.TAKER)
            elif order.side is OrderSide.BUY and bar.low <= order.price:
                gapped = bar.open < order.price
                self._fill(order, bar.open if gapped else order.price,
                           LiquiditySide.TAKER if gapped else LiquiditySide.MAKER)
            elif order.side is OrderSide.SELL and bar.high >= order.price:
                gapped = bar.open > order.price
                self._fill(order, bar.open if gapped else order.price,
                           LiquiditySide.TAKER if gapped else LiquiditySide.MAKER)

    # ── internals ────────────────────────────────────────────────────

    def _local_date_of(self, ts_ns: int) -> pd.Timestamp:
        return pd.Timestamp(ts_ns, unit="ns", tz="UTC").tz_convert(self.spec.timezone).normalize()

    def _last_close(self, order: Order) -> Optional[float]:
        bar = self._last_bar.get(order.instrument_id)
        return bar.close if bar is not None else None

    def _fill(self, order: Order, price: float, liquidity: LiquiditySide) -> None:
        instrument = self._cache.instrument(order.instrument_id)
        qty = order.quantity
        trade_value = qty * price
        commission = self.fee_model.commission(qty, price, liquidity, instrument)
        if instrument.min_notional and trade_value < instrument.min_notional:
            return self._reject(order, f"notional {trade_value:g} below the minimum {instrument.min_notional:g}")
        if order.side is OrderSide.BUY:
            acct = self._portfolio.account(instrument.currency)
            total_cost = trade_value + commission
            if acct.balance < total_cost:
                return self._reject(order, f"insufficient {instrument.currency} cash "
                                           f"({acct.balance:,.2f} < {total_cost:,.2f})")
        else:
            held = self._portfolio.net_qty(order.instrument_id)
            if held < qty:
                return self._reject(order, f"insufficient position: hold {held:g}, selling {qty:g} "
                                           f"(short selling is not supported)")
        self._remove(order)
        self._trade_count += 1
        self._emit(OrderFilled(
            order.client_order_id, order.strategy_id, order.instrument_id, self._clock.timestamp_ns(),
            trade_id=TradeId(f"{self.venue}-{self._trade_count}"), side=order.side, last_qty=qty,
            last_px=price, commission=commission, currency=instrument.currency,
            liquidity_side=liquidity, tags=dict(order.tags)))

    def _accept(self, order: Order) -> None:
        self._resting.append(order)
        if order.time_in_force is TimeInForce.DAY:
            self._day_orders[order] = self._local_date_of(self._clock.timestamp_ns())
        self._emit(OrderAccepted(order.client_order_id, order.strategy_id, order.instrument_id,
                                 self._clock.timestamp_ns()))

    def _remove(self, order: Order) -> None:
        if order in self._resting:
            self._resting.remove(order)
        self._day_orders.pop(order, None)

    def _reject(self, order: Order, reason: str) -> None:
        self._remove(order)
        self._emit(OrderRejected(order.client_order_id, order.strategy_id, order.instrument_id,
                                 self._clock.timestamp_ns(), reason=reason))

    def _cancel(self, order: Order, reason: str) -> None:
        self._remove(order)
        self._emit(OrderCanceled(order.client_order_id, order.strategy_id, order.instrument_id,
                                 self._clock.timestamp_ns(), reason=reason))

    @property
    def resting_orders(self) -> List[Order]:
        return list(self._resting)
