"""Fill models, called directly on hand-built bars, quotes, trades and books."""
import pytest

from midfreq.backtest.fill_models import (
    AutoFillModel, BarFillModel, Fill, FillModel, LegacyFillModel, OrderBookFillModel,
    QuoteFillModel, TradeFillModel,
)
from midfreq.core.model import LiquiditySide, OrderSide, TimeInForce

from mft_market import bar, book, limit, market, quote, state, submitted, trade

MAKER, TAKER = LiquiditySide.MAKER, LiquiditySide.TAKER


def book_fill(order, fills):
    """Apply fills to the order the way the execution engine does (for multi-step tests)."""
    order.filled_qty += sum(f.quantity for f in fills)
    return fills


class TestCompatibility:
    def test_fill_model_called_directly_builds_the_bar_model(self):
        fm = FillModel(slippage_bps=5)
        assert isinstance(fm, BarFillModel) and fm.timing == "close" and fm.slippage_bps == 5
        assert FillModel(timing="next_open") == BarFillModel(timing="next_open")
        assert fm.price(100.0, OrderSide.BUY) == 100.0 * (1.0 + 5 / 10000.0)
        with pytest.raises(ValueError):
            FillModel(timing="whenever")

    def test_legacy_fills_at_the_reference_price_without_lots(self):
        fm = LegacyFillModel(slippage_bps=5)
        assert fm.timing == "reference" and fm.round_lots is False
        o = market("BUY", 37)
        o.reference_price = 50.0
        assert fm.fill(o, submitted(state(bar(1, 1, 1, 1), lot=100))) == [Fill(37, 50.0, TAKER, True)]


class TestBarFillModel:
    def test_close_and_next_open_timing(self):
        s = submitted(state(bar(10, 12, 9, 11)))
        assert BarFillModel().fill(market("BUY", 100), s) == [Fill(100, 11.0, TAKER, True)]
        nxt = BarFillModel(timing="next_open")
        o = market("BUY", 100)
        assert nxt.fill(o, s) == []                                  # waits for the next bar
        s.update(bar(11.5, 12, 11, 11.8, i=1))
        assert nxt.fill(o, s) == [Fill(100, 11.5, TAKER, True)]

    def test_intrabar_path_visits_the_nearer_extreme_first(self):
        assert BarFillModel.intrabar_path(bar(10, 10.5, 8, 9)) == [10, 10.5, 8, 9]     # high nearer
        assert BarFillModel.intrabar_path(bar(10, 13, 9.5, 12)) == [10, 9.5, 13, 12]  # low nearer
        fm = BarFillModel()
        b = bar(10, 10.5, 8, 9)
        sell, buy = limit("SELL", 1, 10.25), limit("BUY", 1, 8.5)
        assert fm.sequence(sell, b) < fm.sequence(buy, b) < float("inf")
        assert fm.sequence(limit("BUY", 1, 7), b) == float("inf")

    def test_limit_through_fills_at_the_limit_as_maker_and_gaps_fill_at_the_open(self):
        fm = BarFillModel()
        o = limit("BUY", 100, 9.5)
        s = state(bar(10, 10.5, 9.0, 9.8))
        assert fm.fill(o, s) == [Fill(100, 9.5, MAKER, False)]
        s.update(bar(9.2, 9.6, 9.0, 9.4, i=1))                       # opened below the limit
        assert fm.fill(limit("BUY", 100, 9.5), s) == [Fill(100, 9.2, TAKER, False)]
        assert fm.fill(limit("SELL", 100, 11), s) == []

    def test_touch_probability_is_seeded_and_reproducible(self):
        draws = []
        for _ in range(2):
            fm = BarFillModel(prob_fill_on_touch=0.5, seed=42)
            draws.append([bool(fm.fill(limit("BUY", 1, 9.0), state(bar(10, 10.5, 9.0, 9.8, i=k))))
                          for k in range(40)])
        assert draws[0] == draws[1] and 5 < sum(draws[0]) < 35
        # Trading through the limit always fills, whatever the probability.
        never = BarFillModel(prob_fill_on_touch=0.0)
        assert never.fill(limit("BUY", 1, 9.0), state(bar(10, 10.5, 9.0, 9.8))) == []
        assert never.fill(limit("BUY", 1, 9.5), state(bar(10, 10.5, 9.0, 9.8)))

    def test_volume_cap_leaves_the_rest_open_and_is_shared(self):
        fm = BarFillModel(timing="next_open", max_volume_share=0.1)
        s = submitted(state(bar(10, 10, 10, 10, v=1000), lot=10))
        a, b = market("BUY", 150), market("BUY", 50)
        s.update(bar(10, 10, 10, 10, v=1000, i=1))
        assert book_fill(a, fm.fill(a, s)) == [Fill(100, 10.0, TAKER, True)]   # 10% of 1,000
        assert fm.fill(b, s) == []                                            # the bar's share is used
        s.update(bar(10, 10, 10, 10, v=300, i=2))
        assert fm.fill(a, s) == [Fill(30, 10.0, TAKER, True)]                 # 30, rounded to lots

    def test_ioc_limit_on_arrival(self):
        s = submitted(state(bar(10, 10, 10, 10)))
        o = limit("BUY", 5, 10.5)
        o.time_in_force = TimeInForce.IOC
        assert BarFillModel().fill(o, s) == [Fill(5, 10.0, TAKER, False)]
        assert BarFillModel().fill(limit("BUY", 5, 10.5), s) == []           # GTC waits


class TestQuoteFillModel:
    def test_market_pays_the_spread_capped_by_displayed_size(self):
        fm = QuoteFillModel()
        s = submitted(state(quote(99.9, 100.1, bid_size=50, ask_size=300)))
        o = market("BUY", 500)
        assert book_fill(o, fm.fill(o, s)) == [Fill(300, 100.1, TAKER, False)]
        assert fm.fill(market("SELL", 80), s) == [Fill(50, 99.9, TAKER, False)]
        assert fm.fill(market("BUY", 10), s) == []                           # the ask size is used up
        s.update(quote(100.0, 100.2, ask_size=150, i=1))
        assert fm.fill(o, s) == [Fill(150, 100.2, TAKER, False)]             # the rest at the next quote

    def test_limits_fill_when_the_opposite_quote_crosses(self):
        fm = QuoteFillModel()
        s = submitted(state(quote(99.9, 100.1)))
        o = limit("BUY", 100, 100.0)
        assert fm.fill(o, s) == []                                            # resting below the ask
        assert fm.fill(limit("BUY", 10, 100.2), s) == [Fill(10, 100.1, TAKER, False)]   # marketable
        s.update(quote(99.7, 99.9, i=1))
        assert fm.fill(o, s) == [Fill(100, 100.0, MAKER, False)]             # hit at its own price
        s.update(bar(1, 1, 1, 1, i=2))
        assert fm.fill(limit("BUY", 1, 101), s) == []                         # bars do not move quotes

    def test_no_quote_no_fill(self):
        assert QuoteFillModel().fill(market("BUY", 1), submitted(state(bar(1, 1, 1, 1)))) == []


class TestTradeFillModel:
    def test_market_takes_the_next_print(self):
        fm = TradeFillModel(participation=0.5)
        s = submitted(state(trade(100.0, 1000)))
        o = market("BUY", 800)
        assert fm.fill(o, s) == []                                            # waits for a new print
        s.update(trade(100.2, 1000, i=1))
        assert book_fill(o, fm.fill(o, s)) == [Fill(500, 100.2, TAKER, True)]
        s.update(trade(100.3, 1000, i=2))
        assert fm.fill(o, s) == [Fill(300, 100.3, TAKER, True)]

    def test_limit_needs_a_print_at_or_through_it(self):
        fm = TradeFillModel()
        o = limit("BUY", 100, 99.5)
        s = state(trade(99.6, 500))
        assert fm.fill(o, s) == []
        s.update(trade(99.5, 40, OrderSide.SELL, i=1))
        assert book_fill(o, fm.fill(o, s)) == [Fill(40, 99.5, MAKER, False)]
        s.update(trade(99.5, 500, OrderSide.BUY, i=2))
        assert fm.fill(o, s) == []                       # a buyer lifting an offer at 99.5 did not hit our bid
        s.update(trade(99.4, 500, OrderSide.BUY, i=3))
        assert fm.fill(o, s) == [Fill(60, 99.5, MAKER, False)]                # printed through


class TestOrderBookFillModel:
    def test_market_walks_the_levels_and_runs_out_of_depth(self):
        fm = OrderBookFillModel()
        s = submitted(state(book([(99.9, 100)], [(100.1, 100), (100.2, 200), (100.5, 50)])))
        o = market("BUY", 400)
        fills = book_fill(o, fm.fill(o, s))
        assert fills == [Fill(100, 100.1, TAKER, False), Fill(200, 100.2, TAKER, False),
                         Fill(50, 100.5, TAKER, False)]
        vwap = sum(f.quantity * f.price for f in fills) / sum(f.quantity for f in fills)
        assert vwap == pytest.approx((100 * 100.1 + 200 * 100.2 + 50 * 100.5) / 350)
        assert o.leaves_qty == 50
        assert fm.fill(market("BUY", 10), s) == []                             # the book is used up
        s.update(book([(99.9, 100)], [(100.3, 500)], i=1))
        assert fm.fill(o, s) == [Fill(50, 100.3, TAKER, False)]

    def test_lot_rounding_while_walking(self):
        s = submitted(state(book([], [(10.0, 150), (10.1, 120)]), lot=100))
        assert OrderBookFillModel().fill(market("BUY", 1000), s) == [
            Fill(150, 10.0, TAKER, False), Fill(50, 10.1, TAKER, False)]      # 200 in all: two lots

    def test_limit_queues_behind_displayed_size(self):
        fm = OrderBookFillModel()
        s = submitted(state(book([(99.9, 300), (99.8, 100)], [(100.1, 100)])))
        o = limit("BUY", 100, 99.9)
        assert fm.fill(o, s) == [] and fm.queue_ahead(o) == 300
        s.update(trade(99.9, 250, OrderSide.SELL, i=1))
        assert fm.fill(o, s) == [] and fm.queue_ahead(o) == 50                # 250 of the 300 ahead
        s.update(book([(99.9, 20)], [(100.1, 100)], i=2))
        assert fm.fill(o, s) == [] and fm.queue_ahead(o) == 20                # orders ahead cancelled
        s.update(trade(99.9, 70, OrderSide.SELL, i=3))
        assert book_fill(o, fm.fill(o, s)) == [Fill(50, 99.9, MAKER, False)]  # 20 ahead, then us
        s.update(trade(99.7, 500, OrderSide.SELL, i=4))
        assert fm.fill(o, s) == [Fill(50, 99.9, MAKER, False)]                # printed through

    def test_marketable_limit_takes_what_it_can_then_rests(self):
        fm = OrderBookFillModel()
        s = submitted(state(book([(99.9, 300)], [(100.0, 60), (100.1, 100)])))
        o = limit("BUY", 100, 100.0)
        assert book_fill(o, fm.fill(o, s)) == [Fill(60, 100.0, TAKER, False)]
        assert fm.queue_ahead(o) == 0                                          # nothing bid at 100.0
        s.update(book([(99.9, 300)], [(99.95, 500)], i=1))
        assert fm.fill(o, s) == [Fill(40, 100.0, MAKER, False)]               # the offer came to us


class TestAutoFillModel:
    def test_finest_data_wins(self):
        auto = AutoFillModel()
        s = submitted(state(bar(10, 10, 10, 10)))
        assert auto.choose(s) is auto.bar
        s.update(trade(10, 5, i=1))
        assert auto.choose(s) is auto.trade
        s.update(quote(9.9, 10.1, i=2))
        assert auto.choose(s) is auto.quote
        assert auto.fill(market("BUY", 5), submitted(s)) == [Fill(5, 10.1, TAKER, False)]
        s.update(book([(9.95, 10)], [(10.05, 10)], i=3))
        assert auto.choose(s) is auto.book
