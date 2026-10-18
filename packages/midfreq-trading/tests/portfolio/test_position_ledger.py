"""PositionLedger: books per strategy from fills, the same arithmetic as core Portfolio, corporate
actions, and the original engine's shrink-only reconciliation (midfreq.trading.positions)."""
from datetime import date, datetime

import pytest

from midfreq.core.common.clock import TestClock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model import (
    ClientOrderId, InstrumentId, OrderFilled, OrderSide, PercentFeeModel, StrategyId, TradeId,
)
from midfreq.core.portfolio import Portfolio
from midfreq.data.adapters.base import PositionReport
from midfreq.trading.core.interfaces import CorporateAction, PositionChanged
from midfreq.trading.portfolio.ledger import PositionLedger

AAPL = InstrumentId.from_str("AAPL.XNAS")
MSFT = InstrumentId.from_str("MSFT.XNAS")
TENCENT = InstrumentId.from_str("00700.XHKG")
S1, S2 = StrategyId("S1"), StrategyId("S2")

_n = 0


def fill(side, qty, px, iid=AAPL, sid=S1, commission=0.0, currency="USD", unknown=False):
    global _n
    _n += 1
    return OrderFilled(ClientOrderId(f"C{_n}"), sid, iid, _n, trade_id=TradeId(f"T{_n}"),
                       side=OrderSide.BUY if side == "B" else OrderSide.SELL, last_qty=float(qty),
                       last_px=float(px), commission=commission, currency=currency,
                       tags={"commission_unknown": True} if unknown else {})


def ledger(**kw):
    bus = MessageBus()
    seen = []
    bus.subscribe("events.positions.*", seen.append)
    kw.setdefault("capital", {"S1": 100_000.0, "S2": 50_000.0})
    return PositionLedger(TestClock(1), bus, **kw), seen


# Prices with awkward binary fractions, so any change in the order of operations would show.
TRADES = [("B", 100, 150.37), ("B", 37, 151.1), ("S", 60, 152.93), ("B", 13, 149.07),
          ("S", 90, 150.01), ("S", 0 + 0, 0)]


@pytest.mark.parametrize("rate", [0.0, 0.0016, 0.00005])
def test_cash_quantity_and_average_match_core_portfolio_to_the_bit(rate):
    """The fee model fills in the commission the venue did not report (the fill says
    commission_unknown, as Moomoo's do), as Portfolio's rate does."""
    book, _ = ledger(fee_models={"XNAS": PercentFeeModel(rate)})
    legacy = Portfolio(initial_cash=100_000.0, commission_rate=rate)
    for side, qty, px in TRADES:
        if qty == 0:
            continue
        legacy.execute_trade("US.AAPL", side == "B", float(qty), float(px), datetime(2026, 10, 15))
        book.on_fill(fill(side, qty, px, unknown=True))
        assert book.cash(S1) == legacy.cash                    # exactly, not approximately
        assert book.position(S1, AAPL) == legacy.get_position_qty("US.AAPL")
        assert (book.avg_price(S1, AAPL) or 0.0) == legacy.get_entry_price("US.AAPL")
    assert book.commissions(S1) == pytest.approx(sum(t["commission"] for t in legacy.trade_history))


def test_a_reported_commission_is_booked_as_reported_and_matches_portfolio():
    rate = 0.0016
    book, _ = ledger(fee_models={"XNAS": PercentFeeModel(0.5)})     # never used: the fills carry fees
    legacy = Portfolio(initial_cash=100_000.0, commission_rate=rate)
    for side, qty, px in TRADES[:5]:
        legacy.execute_trade("US.AAPL", side == "B", float(qty), float(px), datetime(2026, 10, 15))
        book.on_fill(fill(side, qty, px, commission=float(qty) * float(px) * rate))
    assert book.cash(S1) == legacy.cash


def test_a_reported_zero_fee_stays_zero():
    """Only a fill marked commission_unknown gets the fee model's estimate; a venue that reports
    a fee of 0 (a maker rebate tier, a promotion) is booked at 0."""
    book, _ = ledger(fee_models={"XNAS": PercentFeeModel(0.01)})
    book.on_fill(fill("B", 10, 100.0, commission=0.0))
    assert book.commissions(S1) == 0.0 and book.cash(S1) == 100_000.0 - 1_000.0
    book.on_fill(fill("B", 10, 100.0, unknown=True))
    assert book.commissions(S1) == pytest.approx(10.0)
    assert [b.commission_source for b in book.bookings(S1)] == ["reported", "estimate"]


def test_a_fee_in_the_base_asset_is_converted_at_the_fill_price():
    """OKX charges a spot buy's fee in the base asset: 0.0005 BTC at 60,000 USDT is 30 USDT. The
    booking keeps the venue's own amount and currency."""
    from midfreq.core.model.instruments import CurrencyPair
    btc = InstrumentId.from_str("BTC-USDT.OKX")
    pair = CurrencyPair(id=btc, raw_symbol="BTC-USDT", currency="USDT", base_currency="BTC",
                        quote_currency="USDT", size_increment=0.00001)
    book, _ = ledger(capital={"S1": 100_000.0}, instrument=lambda iid: pair if iid == btc else None)
    book.on_fill(fill("B", 0.5, 60_000.0, iid=btc, commission=0.0005, currency="BTC"))
    assert book.commissions(S1) == pytest.approx(30.0)
    assert book.cash(S1) == pytest.approx(100_000.0 - 30_000.0)        # paid in coins (M8)
    b = book.bookings(S1)[-1]
    assert (b.commission_source, b.venue_commission, b.venue_currency) == ("converted", 0.0005, "BTC")
    book.on_fill(fill("S", 0.5, 61_000.0, iid=btc, commission=30.5, currency="USDT"))   # quote: as is
    assert book.bookings(S1)[-1].commission == 30.5
    # without reference data the currencies come from the id
    plain, _ = ledger(capital={"S1": 100_000.0})
    plain.on_fill(fill("B", 0.5, 60_000.0, iid=btc, commission=0.0005, currency="BTC"))
    assert plain.commissions(S1) == pytest.approx(30.0)


def test_realised_pnl_and_the_cash_identity():
    book, seen = ledger()
    book.on_fill(fill("B", 100, 10.0, commission=1.0))
    book.on_fill(fill("B", 100, 12.0, commission=1.0))     # avg 11
    book.on_fill(fill("S", 50, 13.0, commission=0.5))      # +100
    state = book.position_state(S1, AAPL)
    assert state.quantity == 150 and state.avg_px == 11.0 and state.realized_pnl == 100.0
    assert state.commissions == 2.5
    cost = 150 * 11.0
    assert book.cash(S1) == pytest.approx(100_000 - cost + book.realized_pnl(S1) - book.commissions(S1))
    assert isinstance(seen[-1], PositionChanged) and seen[-1].quantity == 150 and seen[-1].realized_pnl == 100.0
    book.on_fill(fill("S", 150, 9.0))                      # -300, flat
    assert book.position(S1, AAPL) == 0 and book.positions(S1) == {} and book.avg_price(S1, AAPL) is None
    assert book.realized_pnl(S1) == pytest.approx(-200.0)
    assert seen[-1].quantity == 0 and seen[-1].avg_px is None


def test_books_are_per_strategy():
    book, _ = ledger()
    book.on_fill(fill("B", 10, 100.0, sid=S1))
    book.on_fill(fill("B", 5, 100.0, sid=S2))
    assert book.positions(S1) == {AAPL: 10} and book.positions(S2) == {AAPL: 5}
    assert book.cash(S1) == 99_000.0 and book.cash(S2) == 49_500.0


def test_a_strategy_without_capital_uses_the_default_and_a_fill_is_always_booked(caplog):
    book, _ = ledger(capital={}, default_capital=1_000.0)
    assert book.cash(S1) == 1_000.0
    book.on_fill(fill("B", 20, 100.0))                     # beyond its cash: the venue filled it
    assert book.cash(S1) == -1_000.0 and book.position(S1, AAPL) == 20
    assert "negative" in caplog.text


def test_selling_more_than_held_goes_short_and_crossing_reopens_at_the_fill_price(caplog):
    book, _ = ledger()
    book.on_fill(fill("B", 10, 100.0))
    book.on_fill(fill("S", 15, 110.0))
    assert book.position(S1, AAPL) == -5 and book.avg_price(S1, AAPL) == 110.0
    assert book.realized_pnl(S1) == 100.0
    assert "held 10" in caplog.text
    book.on_fill(fill("B", 5, 100.0))
    assert book.position(S1, AAPL) == 0 and book.realized_pnl(S1) == 150.0


def test_dividend_books_cash_once_and_split_keeps_the_cost_basis():
    book, seen = ledger()
    book.on_fill(fill("B", 100, 50.0, iid=TENCENT))
    cash0 = book.cash(S1)
    div = CorporateAction(TENCENT, "dividend", date(2026, 10, 20), 3.4, "HKD")
    assert book.apply_corporate_action(S1, div) == pytest.approx(340.0)
    assert book.cash(S1) == pytest.approx(cash0 + 340.0) and book.dividends(S1) == pytest.approx(340.0)
    assert book.apply_corporate_action(S1, div) == 0.0          # booked once
    assert book.cash(S1) == pytest.approx(cash0 + 340.0)
    split = CorporateAction(TENCENT, "split", date(2026, 10, 21), 2.0)
    book.apply_corporate_action(S1, split)
    assert book.position(S1, TENCENT) == 200 and book.avg_price(S1, TENCENT) == 25.0
    assert seen[-1].quantity == 200 and seen[-1].avg_px == 25.0
    assert book.apply_corporate_action(S2, div) == 0.0           # no holding, nothing booked
    with pytest.raises(ValueError):
        book.apply_corporate_action(S1, CorporateAction(TENCENT, "merger", date(2026, 10, 22), 1.0))


def report(iid, qty, symbol=None):
    return PositionReport(symbol or str(iid), float(qty), None, None, None, None,
                          instrument_id=str(iid) if iid is not None else None)


def test_reconcile_only_ever_shrinks_a_claim():
    book, seen = ledger()
    book.on_fill(fill("B", 100, 10.0, iid=AAPL))
    book.on_fill(fill("B", 50, 10.0, iid=MSFT))
    cash = book.cash(S1)
    out = book.reconcile("XNAS", [report(AAPL, 60), report(MSFT, 500)])
    assert book.position(S1, AAPL) == 60            # the broker holds less: shrunk
    assert book.position(S1, MSFT) == 50            # the broker holds more: never adopted
    assert book.avg_price(S1, AAPL) == 10.0 and book.cash(S1) == cash    # cash and cost untouched
    assert out.source == "positions" and out.matched == 1 and len(out.adjustments) == 1
    assert "shrunk to 60" in out.adjustments[0] and out.unexplained == ()
    assert seen[-1].instrument_id == AAPL and seen[-1].quantity == 60
    out = book.reconcile("XNAS", [report(MSFT, 500)])   # AAPL no longer held at the broker
    assert book.position(S1, AAPL) == 0 and AAPL not in book.positions(S1)


def test_reconcile_compares_each_claim_alone_with_the_broker_original_rule():
    book, _ = ledger()
    book.on_fill(fill("B", 100, 10.0, sid=S1))
    book.on_fill(fill("B", 100, 10.0, sid=S2))
    out = book.reconcile("XNAS", [report(AAPL, 150)])
    # Each claim (100) is within the broker's 150, so neither shrinks; the excess is reported.
    assert book.position(S1, AAPL) == 100 and book.position(S2, AAPL) == 100
    assert out.adjustments == () and len(out.unexplained) == 1 and "total 200" in out.unexplained[0]
    out = book.reconcile("XNAS", [report(AAPL, 40)])
    assert book.position(S1, AAPL) == 40 and book.position(S2, AAPL) == 40
    assert len(out.adjustments) == 2


def test_reconcile_leaves_claims_alone_when_a_broker_position_cannot_be_identified():
    book, _ = ledger(instrument_of=lambda sym: {"US.MSFT": MSFT}.get(sym))
    book.on_fill(fill("B", 100, 10.0, iid=AAPL))
    book.on_fill(fill("B", 10, 10.0, iid=MSFT))
    out = book.reconcile("XNAS", [report(None, 100, symbol="US.AAPL?"), report(None, 5, symbol="US.MSFT")])
    assert book.position(S1, AAPL) == 100            # not reported under an id we know: untouched
    assert book.position(S1, MSFT) == 5              # mapped through instrument_of: shrunk
    assert any("no instrument id" in u for u in out.unexplained)


def test_reconcile_ignores_other_venues():
    book, _ = ledger()
    book.on_fill(fill("B", 100, 10.0, iid=TENCENT))
    out = book.reconcile("XNAS", [])
    assert book.position(S1, TENCENT) == 100 and out.matched == 0 and out.adjustments == ()


def test_bound_cash_lowers_never_raises():
    book, _ = ledger()
    out = book.bound_cash(60_000.0)
    assert book.cash(S1) == 60_000.0 and book.cash(S2) == 50_000.0
    assert out.source == "cash" and len(out.adjustments) == 1 and out.matched == 1


def test_snapshot_and_restore_round_trip():
    book, _ = ledger()
    book.on_fill(fill("B", 100, 10.0, commission=1.0))
    book.on_fill(fill("S", 40, 11.0, commission=1.0))
    book.apply_corporate_action(S1, CorporateAction(AAPL, "dividend", date(2026, 10, 1), 0.5))
    snap = book.snapshot(S1)
    other, _ = ledger(capital={})
    p = snap["positions"]["AAPL.XNAS"]
    other.restore("S1", snap["cash"], {"AAPL.XNAS": (p["qty"], p["avg_px"])}, capital=snap["capital"],
                  realized_pnl=snap["realized_pnl"], commissions=snap["commissions"], dividends=snap["dividends"],
                  corporate_actions=snap["corporate_actions"])
    assert other.cash(S1) == book.cash(S1) and other.positions(S1) == book.positions(S1)
    assert other.avg_price(S1, AAPL) == 10.0 and other.capital(S1) == 100_000.0
    assert other.apply_corporate_action(S1, CorporateAction(AAPL, "dividend", date(2026, 10, 1), 0.5)) == 0.0
    # The original engine's state file shape (qty / entry_price) restores too.
    other.restore("S2", 1_000.0, {"MSFT.XNAS": {"qty": 5, "entry_price": 300.0}})
    assert other.positions(S2) == {MSFT: 5.0} and other.avg_price(S2, MSFT) == 300.0


def test_m8_a_base_asset_fee_reduces_the_position_not_the_cash():
    """M8 (safety review): OKX takes a spot buy's fee out of the coins bought, so the account
    holds 0.4995 BTC after buying 0.5 with a 0.0005 BTC fee, and pays 30,000 USDT, no more. The
    book holds what the account holds; the fee is recorded as commission at the fill price."""
    from midfreq.core.model.instruments import CurrencyPair
    btc = InstrumentId.from_str("BTC-USDT.OKX")
    pair = CurrencyPair(id=btc, raw_symbol="BTC-USDT", currency="USDT", base_currency="BTC",
                        quote_currency="USDT", size_increment=0.00001)
    book, _ = ledger(capital={"S1": 100_000.0}, instrument=lambda iid: pair if iid == btc else None)
    book.on_fill(fill("B", 0.5, 60_000.0, iid=btc, commission=0.0005, currency="BTC"))
    assert book.position(S1, btc) == pytest.approx(0.4995)
    assert book.cash(S1) == pytest.approx(70_000.0)
    assert book.commissions(S1) == pytest.approx(30.0)
    assert book.avg_price(S1, btc) == pytest.approx(30_000.0 / 0.4995)
    book.on_fill(fill("S", 0.4995, 61_000.0, iid=btc, commission=30.47, currency="USDT"))
    assert book.position(S1, btc) == pytest.approx(0.0, abs=1e-12)
    assert book.cash(S1) == pytest.approx(70_000.0 + 0.4995 * 61_000.0 - 30.47)
