"""
PositionLedger: positions, cash, realised P&L and commissions per strategy, from fills.

Every strategy has its own book: a cash balance that starts at its capital cap (the original
engine's `capital_cap`: the session deploys that much, whatever the account holds), and its
positions. Several strategies may share one broker account, so a book is never derived from the
broker's holdings; the broker only ever shrinks it (`reconcile`).

Arithmetic, kept operation for operation the same as midfreq.core.portfolio.Portfolio
(and midfreq.core.accounting, which repeats it), so a fill books the same cash to the last bit:

    trade_value = qty x price
    buy:   cash += -(trade_value + commission)
    sell:  cash +=  (trade_value - commission)
    average price on a buy = ((held x avg) + (qty x price)) / (held + qty); unchanged on a sell
    realised P&L on a sell = (price - avg) x qty, gross of commissions (as core accounting);
    commissions are kept separately

So, for a book with no corporate actions and no reconciliation adjustments,
    cash = capital - sum(qty x avg) + realised - commissions.

The commission is the fill's own (the venue's report), in the instrument's quote currency:
- a venue that reports no fee marks the fill `tags["commission_unknown"]` (Moomoo's fills carry
  no fees); then, and only then, the venue's fee model's estimate is booked, as the original
  engine did with its commission rate. A reported 0 without the tag is a real zero and stays 0;
- a fee charged in the base asset (OKX takes a spot buy's fee out of the coins bought) is paid
  in coins, not cash: the book holds the quantity less the fee (what the account holds), at a
  cost basis that includes the fee, the cash pays the trade value only, and the fee is recorded
  as commission at the fill's price (base-asset fee x price). A fee in any other currency cannot
  be converted from the fill alone: the fee model's estimate is booked and the log says so.
Every booking is kept (`bookings()`), with the venue's own amount and currency next to what was
booked and how (`reported`, `estimate`, `converted`).

A fill is always booked: the venue's fill is the truth. A buy beyond the book's cash or a sell
beyond its position is booked and logged as a WARNING (the backtest Portfolio would refuse it;
the broker did not).

Corporate actions: a dividend books held quantity x amount to cash (and to `dividends`); a split
multiplies the quantity by the ratio and divides the average price by it (cost basis unchanged).
Each action is booked once per strategy.

Reconciliation with the broker (`reconcile`) is the original engine's rule
(midfreq.live.live_engine.LivePortfolio.sync_with_broker and its resume):

- a strategy's claim never grows from the broker's view; it shrinks to the broker's quantity when
  the broker holds less, and is removed when the broker holds none; cash and average price are
  left as they are;
- every claim is compared with the broker's aggregate holding on its own. When several
  strategies' claims together exceed the broker's holding but each alone does not, none is
  shrunk (the original engine has no rule to apportion the shortfall); the excess is reported as
  `unexplained` for a person to resolve;
- a broker surplus over the claims belongs to other sessions (or to manual trades) and is
  never adopted;
- the caller passes the broker's positions only when its query succeeded: a failed query is "no
  information", never "holds nothing" (the 2026-07-15 incident);
- an instrument a strategy has an order working on is left alone (the broker may already show a
  fill that has not reached us; the OMS's open orders, bound with `bind_open_orders`).
`bound_cash` applies the original engine's cash rule: under a cap, a book's cash is its own
ledger, bounded from above by the broker's cash.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Tuple

from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.enums import OrderSide
from midfreq.core.model.events import OrderFilled
from midfreq.core.model.fees import FeeModel
from midfreq.core.model.identifiers import InstrumentId, StrategyId, Venue, as_instrument_id, as_venue
from midfreq.core.model.instruments import CurrencyPair, Instrument
from midfreq.data.adapters.base import PositionReport
from midfreq.trading.core.interfaces import TOPIC_POSITIONS, CorporateAction, PositionChanged, ReconcileReport

log = logging.getLogger("midfreq.trading.positions")

__all__ = ["FillBooking", "PositionLedger", "PositionState"]

_QTY_EPS = 1e-9


def _is_zero(qty: float) -> bool:
    return abs(qty) <= _QTY_EPS


@dataclass(frozen=True)
class PositionState:
    """A read-only view of one position in a strategy's book."""
    strategy_id: StrategyId
    instrument_id: InstrumentId
    quantity: float
    avg_px: Optional[float]
    realized_pnl: float
    commissions: float
    dividends: float


@dataclass(frozen=True)
class FillBooking:
    """One fill as the ledger booked it: the commission booked (quote currency) and the venue's
    own figure."""
    strategy_id: StrategyId
    instrument_id: InstrumentId
    trade_id: Optional[str]
    side: str
    quantity: float
    price: float
    commission: float                   # booked, in the quote currency
    commission_source: str              # 'reported' | 'estimate' | 'converted'
    venue_commission: float             # the fill's own amount
    venue_currency: str                 # and its currency ('' if the venue gave none)
    ts_event: int


@dataclass
class _Position:
    quantity: float = 0.0
    avg_px: float = 0.0
    realized_pnl: float = 0.0
    commissions: float = 0.0
    dividends: float = 0.0

    def apply(self, signed_qty: float, px: float) -> float:
        """Book a fill of `signed_qty` (+ buy, - sell) at `px`; returns the realised P&L."""
        q = self.quantity
        if q == 0 or (q > 0) == (signed_qty > 0):
            # Opening or adding: the weighted average, the same expression as core Portfolio.
            new_qty = q + signed_qty
            self.avg_px = ((abs(q) * self.avg_px) + (abs(signed_qty) * px)) / abs(new_qty)
            self.quantity = new_qty
            return 0.0
        closed = min(abs(signed_qty), abs(q))
        realized = (px - self.avg_px) * closed if q > 0 else (self.avg_px - px) * closed
        self.realized_pnl += realized
        self.quantity = q + signed_qty
        if _is_zero(self.quantity):
            self.quantity = 0.0
            self.avg_px = 0.0
        elif abs(signed_qty) > abs(q):
            self.avg_px = px            # crossed through flat: the rest opens at this price
        return realized


@dataclass
class _Book:
    strategy_id: StrategyId
    capital: float
    cash: float
    realized_pnl: float = 0.0
    commissions: float = 0.0
    dividends: float = 0.0
    positions: Dict[InstrumentId, _Position] = field(default_factory=dict)
    corporate_actions: List[str] = field(default_factory=list)


def _action_key(action: CorporateAction) -> str:
    return f"{action.instrument_id}|{action.kind}|{action.ex_date.isoformat()}|{action.amount!r}"


class PositionLedger:
    """
    The node's PositionKeeper.

    capital: each strategy's capital cap (its starting cash); strategies not listed start at
    `default_capital` (0 if None, with a warning). fee_models: per venue, for fills whose venue
    reports no commission (and fees in a currency that cannot be converted). instrument_of: maps a
    broker's own symbol ('US.AAPL') to an InstrumentId for PositionReports that carry none.
    instrument: the Instrument of an id (reference data), for its quote and base currencies;
    without it they are read from the id ('BTC-USDT' -> BTC, USDT; an equity has no base asset).
    """

    def __init__(self, clock: Clock, msgbus: MessageBus,
                 capital: Optional[Mapping["StrategyId | str", float]] = None,
                 default_capital: Optional[float] = None,
                 fee_models: Optional[Mapping["Venue | str", FeeModel]] = None,
                 instrument_of: Optional[Callable[[str], Optional[InstrumentId]]] = None,
                 instrument: Optional[Callable[[InstrumentId], Optional[Instrument]]] = None,
                 keep_bookings: int = 10_000):
        self._clock = clock
        self._msgbus = msgbus
        self._default_capital = default_capital
        self._fee_models: Dict[Venue, FeeModel] = {as_venue(v): m for v, m in (fee_models or {}).items()}
        self._instrument_of = instrument_of
        self._instrument = instrument
        self._books: Dict[StrategyId, _Book] = {}
        self._bookings: Deque[FillBooking] = deque(maxlen=keep_bookings)
        self._open_orders: Optional[Callable[[], List[Any]]] = None
        for sid, cap in (capital or {}).items():
            self.add_strategy(sid, cap)

    def bind_open_orders(self, open_orders: Callable[..., List[Any]]) -> None:
        """The OMS's open orders (the OMS binds itself when it is built): reconciliation leaves
        alone an instrument a strategy has an order working on."""
        self._open_orders = open_orders

    # ── books ─────────────────────────────────────────────────────────────────────────────────

    def add_strategy(self, strategy_id: "StrategyId | str", capital: float) -> None:
        sid = strategy_id if isinstance(strategy_id, StrategyId) else StrategyId(strategy_id)
        if sid in self._books:
            raise ValueError(f"strategy {sid} already has a book")
        self._books[sid] = _Book(sid, float(capital), float(capital))

    def strategies(self) -> List[StrategyId]:
        return list(self._books)

    def _book(self, strategy_id: StrategyId) -> _Book:
        book = self._books.get(strategy_id)
        if book is None:
            capital = self._default_capital
            if capital is None:
                log.warning("strategy %s has no capital configured; its book starts with no cash", strategy_id)
                capital = 0.0
            book = self._books[strategy_id] = _Book(strategy_id, float(capital), float(capital))
        return book

    def restore(self, strategy_id: "StrategyId | str", cash: float,
                positions: Mapping["InstrumentId | str", Any],
                capital: Optional[float] = None, realized_pnl: float = 0.0, commissions: float = 0.0,
                dividends: float = 0.0, corporate_actions: Iterable[str] = ()) -> None:
        """Load a strategy's book saved by a previous session (see `snapshot`). A position is
        (qty, avg_px), or a dict with 'qty'/'quantity' and 'avg_px'/'entry_price'."""
        sid = strategy_id if isinstance(strategy_id, StrategyId) else StrategyId(strategy_id)
        old = self._books.get(sid)
        cap = capital if capital is not None else (old.capital if old is not None else self._default_capital)
        book = _Book(sid, float(cap if cap is not None else cash), float(cash), float(realized_pnl),
                     float(commissions), float(dividends))
        for iid, pos in positions.items():
            if isinstance(pos, Mapping):
                qty = pos.get("qty", pos.get("quantity", 0.0))
                avg = pos.get("avg_px", pos.get("entry_price", 0.0))
            else:
                qty, avg = pos
            if not _is_zero(float(qty)):
                book.positions[as_instrument_id(iid)] = _Position(float(qty), float(avg or 0.0))
        book.corporate_actions = list(corporate_actions)
        self._books[sid] = book
        log.info("restored %s: cash %.2f, positions %s", sid, book.cash,
                 {str(i): p.quantity for i, p in book.positions.items()})

    def snapshot(self, strategy_id: StrategyId) -> Dict[str, Any]:
        """The book as plain data, for the node's state file; `restore` reads it back."""
        book = self._book(strategy_id)
        return {
            "strategy_id": str(book.strategy_id), "capital": book.capital, "cash": book.cash,
            "realized_pnl": book.realized_pnl, "commissions": book.commissions,
            "dividends": book.dividends,
            "positions": {str(iid): {"qty": p.quantity, "avg_px": p.avg_px, "realized_pnl": p.realized_pnl,
                                     "commissions": p.commissions, "dividends": p.dividends}
                          for iid, p in book.positions.items()},
            "corporate_actions": list(book.corporate_actions),
        }

    # ── PositionKeeper ────────────────────────────────────────────────────────────────────────

    def on_fill(self, fill: OrderFilled) -> None:
        if not fill.last_qty > 0:
            log.error("fill %s for %s has quantity %r; not booked", fill.trade_id, fill.client_order_id, fill.last_qty)
            return
        book = self._book(fill.strategy_id)
        commission, source = self._commission(fill)
        base_fee = float(fill.commission) if source == "converted" else 0.0
        self._bookings.append(FillBooking(
            fill.strategy_id, fill.instrument_id, str(fill.trade_id) if fill.trade_id is not None else None,
            fill.side.value, fill.last_qty, fill.last_px, commission, source, fill.commission, fill.currency,
            fill.ts_event))
        # The same operations, in the same order, as midfreq.core.portfolio.Portfolio.execute_trade.
        trade_value = fill.last_qty * fill.last_px
        is_buy = fill.side is OrderSide.BUY
        if base_fee:
            # Paid in coins: the cash pays (or receives) the trade value only.
            book.cash += -trade_value if is_buy else trade_value
        elif is_buy:
            total_cost = trade_value + commission
            book.cash += -total_cost
        else:
            total_cost = trade_value - commission
            book.cash += total_cost
        book.commissions += commission

        pos = book.positions.get(fill.instrument_id)
        if pos is None:
            pos = book.positions[fill.instrument_id] = _Position()
        held = pos.quantity
        if not is_buy and fill.last_qty > held + _QTY_EPS:
            log.warning("%s sold %g %s but its book held %g; booked as the venue reported it",
                        fill.strategy_id, fill.last_qty, fill.instrument_id, held)
        if base_fee and is_buy:
            received = fill.last_qty - base_fee           # the coins the account received
            realized = pos.apply(received, trade_value / received)
        else:
            realized = pos.apply(fill.last_qty if is_buy else -fill.last_qty, fill.last_px)
            if base_fee:                                  # a sell's fee in coins: more coins leave
                pos.quantity -= base_fee
                if _is_zero(pos.quantity):
                    pos.quantity, pos.avg_px = 0.0, 0.0
        pos.commissions += commission
        book.realized_pnl += realized
        if book.cash < -_QTY_EPS:
            log.warning("%s cash is negative (%.2f) after %s %g %s @ %g", fill.strategy_id, book.cash,
                        fill.side.value, fill.last_qty, fill.instrument_id, fill.last_px)
        self._publish(book, fill.instrument_id, pos, fill.ts_event)
        if pos.quantity == 0:
            del book.positions[fill.instrument_id]

    def _currencies(self, iid: InstrumentId) -> Tuple[Optional[str], Optional[str]]:
        """(quote, base) currency of an instrument; base is None for equities."""
        inst = None
        if self._instrument is not None:
            try:
                inst = self._instrument(iid)
            except Exception:  # noqa: BLE001 - fall back to reading the id
                inst = None
        if isinstance(inst, CurrencyPair):
            return (inst.quote_currency or inst.currency or None), (inst.base_currency or None)
        if inst is not None:
            return inst.currency or None, None
        base, sep, quote = str(iid.symbol).partition("-")
        return (quote, base) if sep and quote else (None, None)

    def _estimate(self, fill: OrderFilled) -> Optional[float]:
        model = self._fee_models.get(fill.instrument_id.venue)
        if model is None:
            return None
        inst = None
        if self._instrument is not None:
            try:
                inst = self._instrument(fill.instrument_id)
            except Exception:  # noqa: BLE001
                inst = None
        return model.commission(fill.last_qty, fill.last_px, fill.liquidity_side, instrument=inst)

    def _commission(self, fill: OrderFilled) -> Tuple[float, str]:
        """(commission in the quote currency, how it was found): see the module docstring."""
        if fill.tags.get("commission_unknown"):
            estimate = self._estimate(fill)
            if estimate is None:
                log.warning("fill %s of %s reports no fee and %s has no fee model; booked with none",
                            fill.trade_id, fill.instrument_id, fill.instrument_id.venue)
                return 0.0, "reported"
            return estimate, "estimate"
        commission = fill.commission
        currency = (fill.currency or "").upper()
        if not commission or not currency:
            return commission, "reported"
        quote, base = self._currencies(fill.instrument_id)
        if quote is None or currency == quote.upper():
            return commission, "reported"
        if base is not None and currency == base.upper():
            return commission * fill.last_px, "converted"
        estimate = self._estimate(fill)
        log.warning("fill %s of %s: fee %g %s is neither in %s nor in %s; %s booked", fill.trade_id,
                    fill.instrument_id, commission, currency, quote, base or "a base asset",
                    f"the fee model's estimate {estimate:g} {quote}" if estimate is not None else "no fee")
        return (estimate, "estimate") if estimate is not None else (0.0, "reported")

    def bookings(self, strategy_id: Optional[StrategyId] = None) -> List[FillBooking]:
        """The fills booked (the most recent `keep_bookings`), oldest first."""
        return [b for b in self._bookings if strategy_id is None or b.strategy_id == strategy_id]

    def position(self, strategy_id: StrategyId, instrument_id: InstrumentId) -> float:
        book = self._books.get(strategy_id)
        if book is None:
            return 0.0
        pos = book.positions.get(instrument_id)
        return pos.quantity if pos is not None else 0.0

    def positions(self, strategy_id: StrategyId) -> Dict[InstrumentId, float]:
        book = self._books.get(strategy_id)
        if book is None:
            return {}
        return {iid: p.quantity for iid, p in book.positions.items()}

    def cash(self, strategy_id: StrategyId) -> float:
        book = self._books.get(strategy_id)
        if book is None:
            return float(self._default_capital or 0.0)
        return book.cash

    # ── more of the book ──────────────────────────────────────────────────────────────────────

    def avg_price(self, strategy_id: StrategyId, instrument_id: InstrumentId) -> Optional[float]:
        book = self._books.get(strategy_id)
        pos = book.positions.get(instrument_id) if book is not None else None
        return pos.avg_px if pos is not None else None

    def position_state(self, strategy_id: StrategyId, instrument_id: InstrumentId) -> Optional[PositionState]:
        book = self._books.get(strategy_id)
        pos = book.positions.get(instrument_id) if book is not None else None
        if pos is None:
            return None
        return PositionState(strategy_id, instrument_id, pos.quantity, pos.avg_px, pos.realized_pnl,
                             pos.commissions, pos.dividends)

    def capital(self, strategy_id: StrategyId) -> float:
        return self._book(strategy_id).capital if strategy_id in self._books else float(self._default_capital or 0.0)

    def realized_pnl(self, strategy_id: StrategyId) -> float:
        book = self._books.get(strategy_id)
        return book.realized_pnl if book is not None else 0.0

    def commissions(self, strategy_id: StrategyId) -> float:
        book = self._books.get(strategy_id)
        return book.commissions if book is not None else 0.0

    def dividends(self, strategy_id: StrategyId) -> float:
        book = self._books.get(strategy_id)
        return book.dividends if book is not None else 0.0

    def equity(self, strategy_id: StrategyId, prices: Mapping[InstrumentId, float]) -> float:
        """Cash plus positions at `prices` (average price where a price is missing)."""
        book = self._books.get(strategy_id)
        if book is None:
            return self.cash(strategy_id)
        value = sum(p.quantity * prices.get(iid, p.avg_px) for iid, p in book.positions.items())
        return book.cash + value

    # ── corporate actions ─────────────────────────────────────────────────────────────────────

    def apply_corporate_action(self, strategy_id: "StrategyId | str", action: CorporateAction) -> float:
        """Book a dividend or split on the strategy's current holding; returns the cash booked
        (0 for a split). The same action is booked at most once per strategy."""
        sid = strategy_id if isinstance(strategy_id, StrategyId) else StrategyId(strategy_id)
        book = self._book(sid)
        key = _action_key(action)
        if key in book.corporate_actions:
            log.warning("%s: corporate action %s already booked; ignored", sid, key)
            return 0.0
        kind = action.kind.lower()
        if kind not in ("dividend", "split"):
            raise ValueError(f"unknown corporate action kind {action.kind!r}")
        if kind == "split" and not action.amount > 0:
            raise ValueError(f"split ratio must be positive, got {action.amount!r}")
        book.corporate_actions.append(key)
        pos = book.positions.get(action.instrument_id)
        if pos is None or pos.quantity == 0:
            log.info("%s: %s on %s (ex %s) recorded; no holding", sid, kind, action.instrument_id, action.ex_date)
            return 0.0
        now = self._clock.timestamp_ns()
        if kind == "dividend":
            cash = pos.quantity * action.amount
            book.cash += cash
            book.dividends += cash
            pos.dividends += cash
            log.info("%s: dividend %g x %g %s = %.2f booked (ex %s)", sid, pos.quantity, action.amount,
                     action.instrument_id, cash, action.ex_date)
            self._publish(book, action.instrument_id, pos, now)
            return cash
        old_qty, old_avg = pos.quantity, pos.avg_px
        pos.quantity = old_qty * action.amount
        pos.avg_px = old_avg / action.amount
        if pos.quantity != round(pos.quantity):
            log.warning("%s: split of %s leaves a fractional holding %g (cash in lieu not modelled)",
                        sid, action.instrument_id, pos.quantity)
        log.info("%s: split %g on %s: %g @ %g -> %g @ %g (ex %s)", sid, action.amount, action.instrument_id,
                 old_qty, old_avg, pos.quantity, pos.avg_px, action.ex_date)
        self._publish(book, action.instrument_id, pos, now)
        return 0.0

    # ── reconciliation with the broker ────────────────────────────────────────────────────────

    def _report_instrument(self, report: PositionReport) -> Optional[InstrumentId]:
        if report.instrument_id:
            try:
                return as_instrument_id(report.instrument_id)
            except ValueError:
                pass
        if self._instrument_of is not None:
            try:
                return self._instrument_of(report.symbol)
            except Exception:  # noqa: BLE001 - a mapping failure leaves the report unidentified
                log.exception("cannot map broker symbol %r", report.symbol)
        return None

    def reconcile(self, venue: "Venue | str", broker: List[PositionReport],
                  strategy_ids: Optional[Iterable["StrategyId | str"]] = None) -> ReconcileReport:
        """Shrink-only, the original engine's rule (module docstring). `broker` must be the
        venue's answer to a successful query. With `strategy_ids`, only those books are
        reconciled (the others still count toward the claims on the broker's holdings)."""
        venue = as_venue(venue)
        only = None if strategy_ids is None else {str(s) for s in strategy_ids}
        in_flight = set()
        if self._open_orders is not None:
            try:
                in_flight = {(str(o.strategy_id), o.instrument_id) for o in self._open_orders()}
            except Exception:  # noqa: BLE001 - without the OMS's view nothing is skipped
                log.exception("cannot read the open orders; reconciling every position")
        now = self._clock.timestamp_ns()
        held: Dict[InstrumentId, float] = {}
        unidentified: List[str] = []
        for report in broker:
            iid = self._report_instrument(report)
            if iid is None:
                unidentified.append(report.symbol)
                continue
            if iid.venue != venue:
                continue
            held[iid] = held.get(iid, 0.0) + float(report.quantity or 0.0)

        matched = 0
        adjustments: List[str] = []
        unexplained: List[str] = []
        if unidentified:
            unexplained.append(f"broker positions with no instrument id: {sorted(unidentified)}; "
                               f"claims on instruments the broker did not identify were left as they are")
        claimed: Dict[InstrumentId, float] = {}
        for book in self._books.values():
            for iid, pos in list(book.positions.items()):
                if iid.venue != venue:
                    continue
                if only is not None and str(book.strategy_id) not in only:
                    claimed[iid] = claimed.get(iid, 0.0) + pos.quantity
                    continue
                if (str(book.strategy_id), iid) in in_flight:
                    # An order is working: the broker may already show a fill we have not had yet.
                    log.info("position reconciliation: %s %s skipped, an order is in flight", book.strategy_id, iid)
                    claimed[iid] = claimed.get(iid, 0.0) + pos.quantity
                    continue
                broker_qty = held.get(iid)
                if broker_qty is None:
                    if unidentified:
                        unexplained.append(f"{book.strategy_id} {iid}: claims {pos.quantity:g}; "
                                           f"not identified among the broker's positions")
                        claimed[iid] = claimed.get(iid, 0.0) + pos.quantity
                        continue
                    broker_qty = 0.0
                new_qty = _shrink(pos.quantity, broker_qty)
                if new_qty == pos.quantity:
                    matched += 1
                else:
                    msg = (f"{book.strategy_id} {iid}: claimed {pos.quantity:g} but the broker holds "
                           f"{broker_qty:g}; claim shrunk to {new_qty:g}")
                    log.warning("position reconciliation: %s", msg)
                    adjustments.append(msg)
                    pos.quantity = new_qty
                    self._publish(book, iid, pos, now)
                    if pos.quantity == 0:
                        del book.positions[iid]
                claimed[iid] = claimed.get(iid, 0.0) + new_qty
        for iid, total in claimed.items():
            broker_qty = held.get(iid, 0.0)
            if (total > 0 and total > broker_qty + _QTY_EPS) or (total < 0 and total < broker_qty - _QTY_EPS):
                unexplained.append(f"{iid}: the strategies' claims total {total:g} but the broker holds "
                                   f"{broker_qty:g}; each claim alone is within it, so none was shrunk")
        for iid, broker_qty in held.items():
            surplus = broker_qty - claimed.get(iid, 0.0)
            if surplus > _QTY_EPS:
                log.debug("%s: broker holds %g more than the strategies claim (other sessions)", iid, surplus)
        report = ReconcileReport("positions", now, matched, tuple(adjustments), tuple(unexplained))
        level = logging.WARNING if (adjustments or unexplained) else logging.INFO
        log.log(level, "position reconciliation %s: %d matched, %d shrunk, %d unexplained", venue,
                matched, len(adjustments), len(unexplained))
        for line in unexplained:
            log.warning("position reconciliation: %s", line)
        return report

    def bound_cash(self, broker_cash: float,
                   strategy_ids: Optional[Iterable[StrategyId]] = None) -> ReconcileReport:
        """The original engine's cash rule under a capital cap: a book's cash is its own ledger,
        lowered to the broker's cash when the broker has less, never raised to it."""
        now = self._clock.timestamp_ns()
        matched = 0
        adjustments: List[str] = []
        for sid in (list(strategy_ids) if strategy_ids is not None else list(self._books)):
            book = self._book(sid)
            if book.cash > broker_cash:
                msg = f"{sid}: cash {book.cash:,.2f} above the broker's {broker_cash:,.2f}; lowered to it"
                log.warning("cash reconciliation: %s", msg)
                adjustments.append(msg)
                book.cash = float(broker_cash)
            else:
                matched += 1
        return ReconcileReport("cash", now, matched, tuple(adjustments))

    # ── helpers ───────────────────────────────────────────────────────────────────────────────

    def _publish(self, book: _Book, instrument_id: InstrumentId, pos: _Position, ts_event: int) -> None:
        msg = PositionChanged(book.strategy_id, instrument_id, pos.quantity,
                              pos.avg_px if pos.quantity != 0 else None, pos.realized_pnl, ts_event)
        self._msgbus.publish(TOPIC_POSITIONS.format(strategy_id=book.strategy_id), msg)


def _shrink(claim: float, broker_qty: float) -> float:
    """The claim moved toward the broker's quantity, never past zero and never away from it."""
    if claim > 0:
        return max(0.0, min(claim, broker_qty))
    if claim < 0:
        return min(0.0, max(claim, broker_qty))
    return claim
