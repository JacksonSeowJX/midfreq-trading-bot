"""
PreTradeRiskEngine: the checks every order passes before the OMS sends it to a venue.

In order, the first failing check denies the order (the OMS turns the reason into OrderDenied):

1. kill switch: while halted (`halt(reason)`), every new order is denied, reducing ones included;
2. the order itself: a positive quantity, a positive limit price on a LIMIT order;
3. the instrument is known (the lot-size source has it) and the quantity is a whole number of
   lots (HK board lots; a size step for crypto);
4. the venue's trading session is open (the calendar's `is_open(venue, ts_ns)`);
5. a price to value the order: the limit price, else the order's reference price (the decision
   candle's close), else the reference price source;
6. price band (fat finger): a limit price within `price_band_pct` of the reference price;
7. order notional within `max_order_notional`;
8. no short selling (unless allowed): a sell no larger than the position less what open sells
   are already selling, the original engine's sell cap;
9. orders that add exposure: the position's notional (with open orders) within
   `max_position_notional`, and the strategy's gross notional within `max_gross_notional`
   (a `limit_warning` RiskEvent once usage passes `warn_at`);
10. a buy's worst-case cost within the strategy's cash less what its open buys have reserved.
    Worst case, as the original engine sized its buys (LivePortfolio.execute_trade): a limit
    order at its limit, a market order at the reference price `market_slippage_pct` through,
    either plus `fee_rate` of commission. Sells credit nothing until they fill.

Reference data (lot sizes, calendars, reference prices) is injected as callables, so this module
depends on no particular source. Denials, halts and resumes are published as RiskEvents on
TOPIC_RISK. Limits apply per strategy (`strategy_limits`), falling back to `limits`.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, List, Mapping, Optional

from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.enums import OrderSide, OrderType
from midfreq.core.model.identifiers import InstrumentId, StrategyId, Venue
from midfreq.core.model.orders import Order
from midfreq.trading.interfaces import TOPIC_RISK, PositionKeeper, RiskEvent

log = logging.getLogger(__name__)

__all__ = ["PreTradeRiskEngine", "RiskLimits", "affordable_quantity", "is_whole_lots"]

_EPS = 1e-9

PriceSource = Callable[[InstrumentId], Optional[float]]
LotSource = Callable[[InstrumentId], Optional[float]]
SessionOpen = Callable[[Venue, int], bool]
OpenOrders = Callable[[Optional[StrategyId]], List[Order]]


@dataclass(frozen=True)
class RiskLimits:
    """None switches a limit off. Notionals are in the instrument's currency."""
    max_order_notional: Optional[float] = None
    max_position_notional: Optional[float] = None   # per instrument, per strategy
    max_gross_notional: Optional[float] = None      # per strategy, every instrument
    price_band_pct: Optional[float] = None          # 0.05: a limit price within 5% of the reference
    check_cash: bool = True
    allow_short: bool = False
    fee_rate: float = 0.0                           # commission per side assumed for the cash check
    market_slippage_pct: float = 0.01               # a market buy valued this far above the reference
    warn_at: float = 0.9                            # share of max_gross_notional that raises a warning


def is_whole_lots(quantity: float, lot: float) -> bool:
    """Whether `quantity` is a whole number of `lot`s, without float drift (0.3 / 0.1)."""
    if lot <= 0:
        return True
    units = Decimal(repr(float(quantity))) / Decimal(repr(float(lot)))
    return units == units.to_integral_value()


def affordable_quantity(cash: float, price: float, slip: float = 0.01, fee_rate: float = 0.0,
                        lot: Optional[float] = None) -> float:
    """The largest whole-share (and whole-lot) buy whose worst-case cost fits `cash`: the
    original engine's sizing (LivePortfolio.execute_trade): worst = price x (1 + slip) x (1 + fee)."""
    worst = price * (1 + slip) * (1 + fee_rate)
    affordable = int(cash // worst) if worst > 0 else 0
    if lot and lot > 1:
        affordable = int(affordable // lot) * int(lot)
    return float(max(affordable, 0))


class PreTradeRiskEngine:
    def __init__(self, clock: Clock, msgbus: MessageBus, positions: PositionKeeper,
                 limits: Optional[RiskLimits] = None,
                 reference_price: Optional[PriceSource] = None,
                 lot_size: Optional[LotSource] = None,
                 is_open: Optional[SessionOpen] = None,
                 strategy_limits: Optional[Mapping["StrategyId | str", RiskLimits]] = None,
                 open_orders: Optional[OpenOrders] = None):
        self._clock = clock
        self._msgbus = msgbus
        self._positions = positions
        self.limits = limits or RiskLimits()
        self._strategy_limits: Dict[str, RiskLimits] = {str(k): v for k, v in (strategy_limits or {}).items()}
        self._reference_price = reference_price
        self._lot_size = lot_size
        self._is_open = is_open
        self._open_orders = open_orders
        self._halted = False
        self._halt_reason = ""
        self._warned: Dict[str, bool] = {}
        self.denied = 0

    def bind_open_orders(self, open_orders: OpenOrders) -> None:
        """The OMS's open orders, for reservations (the OMS binds itself when it is built)."""
        self._open_orders = open_orders

    def limits_for(self, strategy_id: StrategyId) -> RiskLimits:
        return self._strategy_limits.get(str(strategy_id), self.limits)

    # ── kill switch ───────────────────────────────────────────────────────────────────────────

    def halt(self, reason: str, planned: bool = False) -> None:
        """Deny every new order until resume(). `planned` (the end of a session) logs a
        warning; anything else is the kill switch and logs CRITICAL."""
        self._halted = True
        self._halt_reason = reason or "halted"
        if planned:
            log.warning("trading halted (%s); every new order will be denied", self._halt_reason)
        else:
            log.critical("KILL SWITCH: trading halted (%s); every new order will be denied", self._halt_reason)
        self._msgbus.publish(TOPIC_RISK, RiskEvent("halted", self._halt_reason, self._clock.timestamp_ns()))

    def resume(self) -> None:
        if not self._halted:
            return
        self._halted = False
        log.warning("trading resumed (was halted: %s)", self._halt_reason)
        self._msgbus.publish(TOPIC_RISK, RiskEvent("resumed", f"was halted: {self._halt_reason}",
                                                   self._clock.timestamp_ns()))
        self._halt_reason = ""

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    # ── the check ─────────────────────────────────────────────────────────────────────────────

    def check(self, order: Order) -> Optional[str]:
        now = self._clock.timestamp_ns()
        try:
            reason = self._check(order, now)
        except Exception as exc:  # noqa: BLE001 - a failing source must deny, never let an order through
            log.exception("risk check of %s failed", order.client_order_id)
            reason = f"risk check failed: {exc}"
        if reason is not None:
            self.denied += 1
            log.warning("order %s denied: %s (%s %g %s)", order.client_order_id, reason, order.side.value,
                        order.quantity, order.instrument_id,
                        extra={"client_order_id": str(order.client_order_id), "strategy_id": str(order.strategy_id)})
            self._msgbus.publish(TOPIC_RISK, RiskEvent("denied", reason, now, order.client_order_id,
                                                       order.strategy_id))
        return reason

    def _check(self, order: Order, now: int) -> Optional[str]:
        limits = self.limits_for(order.strategy_id)
        if self._halted:
            return f"trading halted: {self._halt_reason}"
        qty = order.quantity
        if not (isinstance(qty, (int, float)) and math.isfinite(qty) and qty > 0):
            return f"quantity must be positive, got {qty!r}"
        if order.order_type is OrderType.LIMIT and not (order.price is not None and math.isfinite(order.price)
                                                        and order.price > 0):
            return f"a limit order needs a positive price, got {order.price!r}"

        iid = order.instrument_id
        lot = self._lot(iid)
        if lot is None:
            return f"unknown instrument {iid}"
        if not is_whole_lots(qty, lot):
            return f"quantity {qty:g} is not a whole number of lots of {lot:g}"
        if self._is_open is not None and not self._is_open(iid.venue, now):
            return f"{iid.venue} is not open for trading"

        ref = self._ref(iid)
        price = order.price or order.reference_price or ref
        if price is None or not price > 0:
            return f"no price to value the order on {iid}"
        if limits.price_band_pct is not None and order.order_type is OrderType.LIMIT:
            if ref is None or not ref > 0:
                return f"no reference price for {iid} to check the {limits.price_band_pct:.0%} price band"
            deviation = abs(order.price / ref - 1.0)
            if deviation > limits.price_band_pct + _EPS:
                return (f"limit {order.price:g} is {deviation:.1%} from the reference {ref:g}, outside the "
                        f"{limits.price_band_pct:.0%} band")
        notional = qty * price
        if limits.max_order_notional is not None and notional > limits.max_order_notional + _EPS:
            return f"order notional {notional:,.2f} exceeds the limit {limits.max_order_notional:,.2f}"

        sid = order.strategy_id
        open_orders = [o for o in self._strategy_open_orders(sid) if o is not order]
        pos = self._positions.position(sid, iid)
        pending_buy = sum(o.leaves_qty for o in open_orders if o.instrument_id == iid and o.side is OrderSide.BUY)
        pending_sell = sum(o.leaves_qty for o in open_orders if o.instrument_id == iid and o.side is OrderSide.SELL)
        is_buy = order.side is OrderSide.BUY
        if not is_buy and not limits.allow_short:
            sellable = pos - pending_sell
            if qty > sellable + _EPS:
                return (f"sell {qty:g} exceeds the {max(sellable, 0.0):g} held (position {pos:g}, "
                        f"{pending_sell:g} already being sold)")

        signed = qty if is_buy else -qty
        before = pos + pending_buy - pending_sell
        after = before + signed
        if abs(after) > abs(before) + _EPS:
            if limits.max_position_notional is not None and abs(after) * price > limits.max_position_notional + _EPS:
                return (f"position in {iid} would be {abs(after) * price:,.2f}, above the limit "
                        f"{limits.max_position_notional:,.2f}")
            if limits.max_gross_notional is not None:
                gross = self._gross(sid, open_orders, iid) + abs(after) * price
                if gross > limits.max_gross_notional + _EPS:
                    return (f"{sid} gross notional would be {gross:,.2f}, above the limit "
                            f"{limits.max_gross_notional:,.2f}")
                self._maybe_warn(sid, gross, limits, now)

        if is_buy and limits.check_cash:
            cost = self._worst_cost(order, ref, limits)
            reserved = sum(self._worst_cost(o, self._ref(o.instrument_id), limits, o.leaves_qty)
                           for o in open_orders if o.side is OrderSide.BUY)
            cash = self._positions.cash(sid)
            if cost + reserved > cash + _EPS:
                return (f"buy needs up to {cost:,.2f} with {reserved:,.2f} reserved by open buys; "
                        f"{sid} has {cash:,.2f} cash")
        return None

    # ── helpers ───────────────────────────────────────────────────────────────────────────────

    def _lot(self, iid: InstrumentId) -> Optional[float]:
        if self._lot_size is None:
            return 0.0
        try:
            lot = self._lot_size(iid)
        except (KeyError, LookupError, ValueError):
            return None
        return None if lot is None else float(lot)

    def _ref(self, iid: InstrumentId) -> Optional[float]:
        if self._reference_price is None:
            return None
        try:
            px = self._reference_price(iid)
        except (KeyError, LookupError, ValueError):
            return None
        return float(px) if px is not None and px > 0 else None

    def _strategy_open_orders(self, sid: StrategyId) -> List[Order]:
        if self._open_orders is None:
            return []
        return [o for o in self._open_orders(sid) if o.strategy_id == sid]

    def _worst_cost(self, order: Order, ref: Optional[float], limits: RiskLimits,
                    qty: Optional[float] = None) -> float:
        qty = order.quantity if qty is None else qty
        if order.order_type is OrderType.LIMIT and order.price:
            px = order.price
        else:
            base = ref or order.reference_price or 0.0
            px = base * (1 + limits.market_slippage_pct)
        return qty * px * (1 + limits.fee_rate)

    def _value_px(self, sid: StrategyId, iid: InstrumentId) -> float:
        ref = self._ref(iid)
        if ref is not None:
            return ref
        avg = getattr(self._positions, "avg_price", None)
        if callable(avg):
            px = avg(sid, iid)
            if px:
                return float(px)
        return 0.0

    def _gross(self, sid: StrategyId, open_orders: List[Order], exclude: InstrumentId) -> float:
        """Gross notional of the strategy's positions and open orders, except on `exclude`."""
        net: Dict[InstrumentId, float] = dict(self._positions.positions(sid))
        for o in open_orders:
            net[o.instrument_id] = net.get(o.instrument_id, 0.0) + (o.leaves_qty if o.is_buy else -o.leaves_qty)
        return sum(abs(q) * self._value_px(sid, iid) for iid, q in net.items() if iid != exclude)

    def _maybe_warn(self, sid: StrategyId, gross: float, limits: RiskLimits, now: int) -> None:
        if limits.max_gross_notional is None or limits.max_gross_notional <= 0:
            return
        over = gross >= limits.warn_at * limits.max_gross_notional
        if over and not self._warned.get(str(sid)):
            reason = (f"{sid} gross notional {gross:,.2f} is {gross / limits.max_gross_notional:.0%} of its "
                      f"limit {limits.max_gross_notional:,.2f}")
            log.warning("risk: %s", reason)
            self._msgbus.publish(TOPIC_RISK, RiskEvent("limit_warning", reason, now, strategy_id=sid))
        self._warned[str(sid)] = over
