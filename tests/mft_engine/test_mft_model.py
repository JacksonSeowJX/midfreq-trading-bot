"""mft.model: identifiers, bar types, instruments, fee models, orders."""
import pandas as pd
import pytest

from mft.model import (
    Bar, BarSpecification, BarType, CurrencyPair, Equity, InstrumentId, LiquiditySide,
    MakerTakerFeeModel, OrderFactory, OrderSide, OrderType, PercentFeeModel, StrategyId, Venue,
    bars_to_frame, frame_to_bars,
)
from mft.model.identifiers import ClientId


class TestIdentifiers:
    @pytest.mark.parametrize("text", ["00700.XHKG", "AAPL.XNAS", "BTCUSDT.BINANCE", "BTC-USD.COINBASE"])
    def test_instrument_id_round_trip(self, text):
        iid = InstrumentId.from_str(text)
        assert str(iid) == text and InstrumentId.from_str(str(iid)) == iid

    def test_share_class_dot_stays_in_the_symbol(self):
        iid = InstrumentId.from_str("BRK.B.XNYS")
        assert str(iid.symbol) == "BRK.B" and iid.venue == Venue("XNYS")

    @pytest.mark.parametrize("bad", ["00700", "", "AAPL.xnas", "A B.XNAS"])
    def test_invalid_ids_are_rejected(self, bad):
        with pytest.raises(ValueError):
            InstrumentId.from_str(bad)

    def test_ids_are_hashable_and_ordered(self):
        a, b = InstrumentId.from_str("00005.XHKG"), InstrumentId.from_str("00700.XHKG")
        assert len({a, b, InstrumentId.from_str("00005.XHKG")}) == 2
        assert sorted([b, a]) == [a, b]

    def test_client_id_is_upper_case(self):
        assert str(ClientId("moomoo")) == "MOOMOO"


class TestBarTypes:
    @pytest.mark.parametrize("text", [
        "00700.XHKG-1-HOUR-LAST-EXTERNAL",
        "AAPL.XNAS-1-DAY-LAST-EXTERNAL",
        "BTC-USD.COINBASE-5-MINUTE-LAST-EXTERNAL",
        "BRK.B.XNYS-1-HOUR-LAST-EXTERNAL",
    ])
    def test_bar_type_round_trip(self, text):
        bt = BarType.from_str(text)
        assert str(bt) == text and BarType.from_str(str(bt)) == bt

    def test_specification_parsing_and_duration(self):
        spec = BarSpecification.from_str("1-HOUR")
        assert str(spec) == "1-HOUR-LAST" and spec.interval == "1-HOUR"
        assert spec.timedelta == pd.Timedelta(hours=1).to_pytimedelta()
        assert spec.is_intraday and not BarSpecification.from_str("1-DAY").is_intraday

    @pytest.mark.parametrize("tf, spec", [("1m", "1-MINUTE"), ("5m", "5-MINUTE"), ("1h", "1-HOUR"),
                                          ("4h", "4-HOUR"), ("1d", "1-DAY")])
    def test_legacy_timeframes_map_both_ways(self, tf, spec):
        s = BarSpecification.from_legacy(tf)
        assert s.interval == spec and s.legacy_timeframe == tf

    @pytest.mark.parametrize("bad", ["HOUR", "0-HOUR", "1-FORTNIGHT", "1-HOUR-LAST-EXTRA"])
    def test_bad_specifications(self, bad):
        with pytest.raises(ValueError):
            BarSpecification.from_str(bad)

    def test_frame_round_trip(self):
        bt = BarType.from_str("00700.XHKG-1-HOUR-LAST-EXTERNAL")
        t = pd.Timestamp("2024-01-02 02:30", tz="UTC").value
        bars = [Bar(bt, 1.0, 2.0, 0.5, 1.5, 100.0, t, t), Bar(bt, 1.5, 2.5, 1.0, 2.0, 200.0, t + 1, t + 1)]
        df = bars_to_frame(bars)
        assert str(df.index[0]) == "2024-01-02 02:30:00+00:00"
        assert frame_to_bars(df, bt) == bars


class TestInstruments:
    def test_hk_board_lot_rounds_down(self):
        tencent = Equity(id=InstrumentId.from_str("00700.XHKG"), raw_symbol="00700", currency="HKD", lot_size=100)
        assert tencent.make_qty(250) == 200 and tencent.make_qty(99) == 0
        assert tencent.is_valid_qty(300) and not tencent.is_valid_qty(150)

    def test_crypto_quantity_is_fractional_on_the_size_step(self):
        btc = CurrencyPair(id=InstrumentId.from_str("BTCUSDT.BINANCE"), raw_symbol="BTCUSDT",
                           currency="USDT", base_currency="BTC", quote_currency="USDT",
                           size_increment=0.00001, min_notional=5.0)
        assert btc.make_qty(0.123456789) == pytest.approx(0.12345)
        assert btc.make_qty(0.3) == 0.3          # no float drift from 0.3 / 0.00001

    def test_dict_round_trip(self):
        btc = CurrencyPair(id=InstrumentId.from_str("BTCUSDT.BINANCE"), raw_symbol="BTCUSDT",
                           currency="USDT", base_currency="BTC", quote_currency="USDT",
                           size_increment=0.00001, min_notional=5.0)
        again = CurrencyPair.from_dict(btc.to_dict())
        assert again == btc and isinstance(again, CurrencyPair)


class TestFees:
    def test_hk_all_in_rate(self):
        assert PercentFeeModel(0.0016).commission(100, 400.0) == pytest.approx(64.0)

    def test_us_rate(self):
        assert PercentFeeModel(0.00005).commission(100, 200.0) == pytest.approx(1.0)

    def test_percent_fee_matches_legacy_float_maths(self):
        qty, price, rate = 137, 312.47 * 1.0005, 0.0016
        assert PercentFeeModel(rate).commission(qty, price) == (qty * price) * rate

    def test_maker_taker_and_instrument_override(self):
        fm = MakerTakerFeeModel(maker=0.001, taker=0.002)
        assert fm.commission(1, 1000, LiquiditySide.MAKER) == pytest.approx(1.0)
        assert fm.commission(1, 1000, LiquiditySide.TAKER) == pytest.approx(2.0)
        vip = CurrencyPair(id=InstrumentId.from_str("BTCUSDT.BINANCE"), raw_symbol="BTCUSDT",
                           currency="USDT", taker_fee=0.0005)
        assert fm.commission(1, 1000, LiquiditySide.TAKER, vip) == pytest.approx(0.5)


class TestOrders:
    def test_factory_assigns_unique_readable_ids(self):
        f = OrderFactory(StrategyId("MA-001"), clock=lambda: 42)
        a = f.market(InstrumentId.from_str("AAPL.XNAS"), OrderSide.BUY, 10)
        b = f.limit(InstrumentId.from_str("AAPL.XNAS"), OrderSide.SELL, 10, 199.5)
        assert str(a.client_order_id) == "O-MA-001-000001" and str(b.client_order_id) == "O-MA-001-000002"
        assert a.order_type is OrderType.MARKET and b.price == 199.5 and a.ts_init == 42
        assert a.is_open and a.is_buy and not b.is_buy
