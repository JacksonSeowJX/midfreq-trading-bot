"""
SimulatedExchange: one per venue, after NautilusTrader's `backtest.exchange`.

It keeps a MarketState per instrument, updated by every bar, quote, trade and book snapshot, and
applies the venue's rules to every order the RiskEngine lets through:

- the order's quantity is rounded down to whole board lots (or crypto size steps);
- the venue's FillModel decides what fills and at what price, on arrival and then on each new data
  item while the order rests, which allows partial fills;
- the SlippageModel adds trading costs to fills whose price does not already include them (bar and
  trade fills);
- each fill must meet the minimum notional, be paid for from the currency account (no margin) and
  not sell more than is held (no short selling); the fee model charges commission per fill;
- what is left of an IOC order is cancelled, other orders rest; DAY orders are cancelled when the
  venue's local trading date rolls over.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pandas as pd

from mft.backtest.fill_models import CLOSE, REFERENCE, Fill, FillModel
from mft.backtest.market_state import MarketState
from mft.backtest.slippage import FixedBpsSlippage, NoSlippage, SlippageModel
from mft.model.data import Bar
from mft.model.enums import LiquiditySide, OrderSide, OrderType, TimeInForce
from mft.model.events import OrderAccepted, OrderCanceled, OrderFilled, OrderRejected
from mft.model.fees import FeeModel
from mft.model.identifiers import InstrumentId, TradeId
from mft.model.orders import Order
from mft.venues.spec import VenueSpec


def default_slippage(fill_model: FillModel) -> SlippageModel:
    """A fill model built with `slippage_bps` (FillModel(slippage_bps=5)) keeps that cost as
    FixedBpsSlippage; otherwise no slippage, which is the engine's default."""
    if hasattr(fill_model, "slippage_bps"):
        return FixedBpsSlippage(fill_model.slippage_bps or 0.0)
    return NoSlippage()


class SimulatedExchange:
    def __init__(self, spec: VenueSpec, fill_model: FillModel, fee_model: FeeModel,
                 cache, portfolio, clock, emit: Callable[[object], None],
                 slippage_model: Optional[SlippageModel] = None):
        self.spec = spec
        self.venue = spec.venue
        self.fill_model = fill_model
        self.fee_model = fee_model
        self.slippage_model = slippage_model if slippage_model is not None else default_slippage(fill_model)
        self._cache = cache
        self._portfolio = portfolio
        self._clock = clock
        self._emit = emit
        self._resting: List[Order] = []
        self._states: Dict[InstrumentId, MarketState] = {}
        self._trade_count = 0
        self._day_orders: Dict[Order, pd.Timestamp] = {}   # resting DAY orders -> local trading date

    def market_state(self, instrument_id: InstrumentId) -> MarketState:
        st = self._states.get(instrument_id)
        if st is None:
            st = self._states[instrument_id] = MarketState(instrument_id, self._cache.instrument(instrument_id))
        elif st.instrument is None:
            st.instrument = self._cache.instrument(instrument_id)
        return st

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

        state = self.market_state(order.instrument_id)
        state.trigger = None
        fills = self.fill_model.fill(order, state)
        if (not fills and order.order_type is OrderType.MARKET and state.last_bar is None
                and order.reference_price is None and getattr(self.fill_model, "timing", None) in (CLOSE, REFERENCE)):
            return self._reject(order, f"no market yet for {order.instrument_id}")
        if not self._apply(order, fills, state):
            return None
        if order.leaves_qty <= 0:
            return None
        if order.time_in_force is TimeInForce.IOC:
            return self._cancel(order, f"IOC: {order.leaves_qty:g} not immediately fillable")
        if order not in self._resting:
            self._accept(order)
        return None

    def cancel_order(self, order: Order, reason: str = "cancelled by strategy") -> None:
        if order in self._resting:
            self._cancel(order, reason)

    # ── market data in ───────────────────────────────────────────────

    def process_data(self, item) -> None:
        """A new bar, quote, trade or book snapshot for one of this venue's instruments: expire DAY
        orders on a new trading day, update the instrument's MarketState, then offer every resting
        order of that instrument to the fill model. Runs before strategies see the item."""
        if self._day_orders:
            today = self._local_date_of(item.ts_event)
            for order in [o for o, day in self._day_orders.items() if day < today]:
                self._cancel(order, "DAY order expired at the end of the trading day")
        state = self.market_state(item.instrument_id)
        state.update(item)
        if not self._resting:
            return
        orders = [o for o in self._resting if o.instrument_id == item.instrument_id]
        sequence = getattr(self.fill_model, "sequence", None)
        if isinstance(item, Bar) and sequence is not None and len(orders) > 1:
            orders.sort(key=lambda o: sequence(o, item))      # in the order the bar reached them
        for order in orders:
            if order.is_open and order in self._resting:
                fills = self.fill_model.fill(order, state)
                if fills:
                    self._apply(order, fills, state)

    process_bar = process_data

    # ── internals ────────────────────────────────────────────────────

    def _local_date_of(self, ts_ns: int) -> pd.Timestamp:
        return pd.Timestamp(ts_ns, unit="ns", tz="UTC").tz_convert(self.spec.timezone).normalize()

    def _apply(self, order: Order, fills: List[Fill], state: MarketState) -> bool:
        """Book each fill; False if the order was ended (rejected or cancelled) on the way."""
        instrument = state.instrument or self._cache.instrument(order.instrument_id)
        for f in fills:
            qty = min(f.quantity, order.leaves_qty)
            if qty <= 0:
                continue
            price = (self.slippage_model.adjust(f.price, order.side, qty, state, instrument)
                     if f.apply_slippage else f.price)
            if not self._fill(order, qty, price, f.liquidity_side):
                return False
        return True

    def _fill(self, order: Order, qty: float, price: float, liquidity: LiquiditySide) -> bool:
        instrument = self._cache.instrument(order.instrument_id)
        trade_value = qty * price
        commission = self.fee_model.commission(qty, price, liquidity, instrument)
        reason = None
        if instrument.min_notional and trade_value < instrument.min_notional:
            reason = f"notional {trade_value:g} below the minimum {instrument.min_notional:g}"
        elif order.side is OrderSide.BUY:
            acct = self._portfolio.account(instrument.currency)
            total_cost = trade_value + commission
            if acct.balance < total_cost:
                reason = (f"insufficient {instrument.currency} cash "
                          f"({acct.balance:,.2f} < {total_cost:,.2f})")
        else:
            held = self._portfolio.net_qty(order.instrument_id)
            if held < qty:
                reason = f"insufficient position: hold {held:g}, selling {qty:g} (short selling is not supported)"
        if reason is not None:
            if order.filled_qty > 0:
                self._cancel(order, f"remaining {order.leaves_qty:g} cancelled: {reason}")
            else:
                self._reject(order, reason)
            return False
        if qty >= order.leaves_qty:
            self._remove(order)
        self._trade_count += 1
        self._emit(OrderFilled(
            order.client_order_id, order.strategy_id, order.instrument_id, self._clock.timestamp_ns(),
            trade_id=TradeId(f"{self.venue}-{self._trade_count}"), side=order.side, last_qty=qty,
            last_px=price, commission=commission, currency=instrument.currency,
            liquidity_side=liquidity, tags=dict(order.tags)))
        return True

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
        forget = getattr(self.fill_model, "forget", None)
        if forget is not None:
            forget(order)

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
