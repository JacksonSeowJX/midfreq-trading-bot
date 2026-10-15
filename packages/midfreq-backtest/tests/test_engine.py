"""BacktestEngine and its parts: message bus, data engine, simulated exchange, risk engine,
accounts, and the Strategy API."""
import pandas as pd
import pytest

from midfreq.backtest import BacktestEngine, BacktestEngineConfig, DataEngine, FillModel
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model import (
    Bar, BarType, CurrencyPair, Equity, InstrumentId, OrderSide, OrderStatus, TimeInForce,
)
from midfreq.backtest.risk import RiskConfig
from midfreq.strategies.trading import Strategy, StrategyConfig

TENCENT = InstrumentId.from_str("00700.XHKG")
APPLE = InstrumentId.from_str("AAPL.XNAS")
JPM = InstrumentId.from_str("JPM.XNYS")
BTC = InstrumentId.from_str("BTCUSDT.BINANCE")


def bars_for(iid, closes, start, step=pd.Timedelta(hours=1), opens=None, lows=None, highs=None, spec="1-HOUR"):
    bt = BarType.of(iid, spec)
    t0 = pd.Timestamp(start, tz="UTC")
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        lo = lows[i] if lows else min(o, c) * 0.99
        hi = highs[i] if highs else max(o, c) * 1.01
        ts = (t0 + i * step).value
        out.append(Bar(bt, float(o), float(hi), float(lo), float(c), 1000.0, ts, ts))
    return out


def tencent(lot=100):
    return Equity(id=TENCENT, raw_symbol="00700", currency="HKD", price_increment=0.2, lot_size=lot)


class Recorder(Strategy):
    """Subscribes to bar types and runs a script of actions keyed by bar index."""

    def __init__(self, bar_types, actions=None, config=None, slices=False):
        super().__init__(config)
        self.bar_types = bar_types
        self.actions = actions or {}
        self.seen, self.events, self.slices, self.signals = [], [], [], []
        self.want_slices = slices

    def on_start(self):
        for bt in self.bar_types:
            self.subscribe_bars(bt)
        if self.want_slices:
            self.subscribe_slices()

    def on_bar(self, bar):
        self.seen.append(bar)
        action = self.actions.get(len(self.seen) - 1)
        if action:
            action(self, bar)

    def on_slice(self, s):
        self.slices.append(s)

    def on_signal(self, signal):
        self.signals.append(signal)

    def on_event(self, event):
        self.events.append(event)


def buy(qty, iid=None, **kw):
    return lambda s, bar: s.submit_order(s.order_factory.market(iid or bar.instrument_id, OrderSide.BUY, qty, **kw))


def sell(qty, iid=None):
    return lambda s, bar: s.submit_order(s.order_factory.market(iid or bar.instrument_id, OrderSide.SELL, qty))


def hk_engine(cash=1_000_000, fill_model=None, risk=None, lot=100):
    engine = BacktestEngine(BacktestEngineConfig(risk=risk or RiskConfig()))
    engine.add_venue("XHKG", {"HKD": cash}, fill_model=fill_model)
    engine.add_instrument(tencent(lot))
    return engine


class TestMessageBus:
    def test_wildcards_and_order(self):
        bus, got = MessageBus(), []
        bus.subscribe("data.bars.*", lambda m: got.append(("all", m)))
        bus.subscribe("data.bars.00700.XHKG-*", lambda m: got.append(("hk", m)))
        bus.publish("data.bars.00700.XHKG-1-HOUR-LAST-EXTERNAL", 1)
        bus.publish("data.bars.AAPL.XNAS-1-HOUR-LAST-EXTERNAL", 2)
        bus.publish("events.order.X", 3)
        assert got == [("all", 1), ("hk", 1), ("all", 2)]


class TestDataEngine:
    def test_merge_is_utc_ordered_with_stable_ties(self):
        de = DataEngine()
        de.add(bars_for(APPLE, [1, 2], "2024-01-02 14:30"))
        de.add(bars_for(TENCENT, [3, 4, 5], "2024-01-02 06:00"))
        de.add(bars_for(JPM, [6, 7], "2024-01-02 14:30"))
        order = [(str(b.instrument_id), b.close) for b in de.iter_bars()]
        assert order == [("00700.XHKG", 3), ("00700.XHKG", 4), ("00700.XHKG", 5),
                         ("AAPL.XNAS", 1), ("JPM.XNYS", 6), ("AAPL.XNAS", 2), ("JPM.XNYS", 7)]
        slices = list(de.iter_slices())
        assert [len(s) for s in slices] == [1, 1, 1, 2, 2]
        assert slices[3].get(JPM).close == 6

    def test_window(self):
        de = DataEngine()
        de.add(bars_for(TENCENT, list(range(10)), "2024-01-02 01:00"))
        got = [b.close for b in de.iter_bars(pd.Timestamp("2024-01-02 03:00", tz="UTC").value,
                                             pd.Timestamp("2024-01-02 05:00", tz="UTC").value)]
        assert got == [2, 3, 4]


class TestMultiVenue:
    def test_hk_and_us_in_one_run_two_currencies_utc_order(self):
        engine = BacktestEngine()
        engine.add_venue("XHKG", {"HKD": 1_000_000})
        engine.add_venue("XNAS", {"USD": 100_000})
        engine.add_instrument(tencent())
        # Hong Kong 16:00 HKT is 08:00 UTC; New York 10:30 EST is 15:30 UTC the same day.
        engine.add_data(bars_for(APPLE, [190, 191], "2024-01-02 15:30"))
        engine.add_data(bars_for(TENCENT, [300, 301, 302], "2024-01-02 06:00"))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR"), BarType.of(APPLE, "1-HOUR")],
                       {0: buy(1000), 3: buy(100)})
        engine.add_strategy(rec)
        result = engine.run()
        assert [str(b.instrument_id) for b in rec.seen] == ["00700.XHKG"] * 3 + ["AAPL.XNAS"] * 2
        assert sorted(result.currencies) == ["HKD", "USD"]
        # Each currency is accounted separately: no FX conversion.
        assert result.final_balances["HKD"] == pytest.approx(1_000_000 - 300_000 * 1.0016)
        assert result.final_balances["USD"] == pytest.approx(100_000 - 19_000 * 1.00005)
        assert result.final_equity["HKD"] == pytest.approx(result.final_balances["HKD"] + 1000 * 302)
        assert set(result.equity_curves) == {"HKD", "USD"}
        assert set(result.metrics) == {"HKD", "USD"} and "sharpe_ratio" in result.metrics["USD"]

    def test_xnas_and_xnys_share_one_usd_account(self):
        engine = BacktestEngine()
        engine.add_venue("XNAS", {"USD": 10_000})
        engine.add_venue("XNYS")
        engine.add_data(bars_for(APPLE, [100], "2024-01-02 15:30"))
        engine.add_data(bars_for(JPM, [100], "2024-01-02 15:30"))
        rec = Recorder([BarType.of(APPLE, "1-HOUR"), BarType.of(JPM, "1-HOUR")], {0: buy(60), 1: buy(60)})
        engine.add_strategy(rec)
        result = engine.run()
        statuses = [o.status for o in result.orders]
        assert statuses == [OrderStatus.FILLED, OrderStatus.REJECTED]     # one pot of USD

    def test_double_funding_a_currency_is_refused(self):
        e = BacktestEngine()
        e.add_venue("XNAS", {"USD": 1})
        with pytest.raises(ValueError, match="share one account"):
            e.add_venue("XNYS", {"USD": 1})


class TestSimulatedExchange:
    def test_fill_at_close_with_slippage_and_hk_fees(self):
        engine = hk_engine(fill_model=FillModel(slippage_bps=10))
        engine.add_data(bars_for(TENCENT, [300, 310], "2024-01-02 02:30"))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(200), 1: sell(200)})
        engine.add_strategy(rec)
        result = engine.run()
        t = result.trades()
        assert list(t["price"]) == [pytest.approx(300 * 1.001), pytest.approx(310 * 0.999)]
        assert t["commission"].iloc[0] == pytest.approx(200 * 300 * 1.001 * 0.0016)

    def test_us_fee_rate(self):
        engine = BacktestEngine()
        engine.add_venue("XNAS", {"USD": 100_000})
        engine.add_data(bars_for(APPLE, [200], "2024-01-02 15:30"))
        engine.add_strategy(Recorder([BarType.of(APPLE, "1-HOUR")], {0: buy(100)}))
        assert engine.run().trades()["commission"].iloc[0] == pytest.approx(1.0)   # 0.005% of 20,000

    def test_next_bar_open_timing(self):
        engine = hk_engine(fill_model=FillModel(timing="next_open"))
        engine.add_data(bars_for(TENCENT, [300, 310, 320], "2024-01-02 02:30", opens=[299, 305, 318]))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(100)})
        engine.add_strategy(rec)
        result = engine.run()
        fill = result.trade_log[0]
        assert fill["price"] == 305                                  # the NEXT bar's open
        assert fill["timestamp"] == pd.Timestamp("2024-01-02 03:30", tz="UTC")
        assert [type(e).__name__ for e in rec.events] == ["OrderSubmitted", "OrderAccepted", "OrderFilled"]

    def test_lot_rounding_and_below_one_lot(self):
        engine = hk_engine(lot=100)
        engine.add_data(bars_for(TENCENT, [300, 300], "2024-01-02 02:30"))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(250), 1: buy(50)})
        engine.add_strategy(rec)
        result = engine.run()
        first, second = result.orders
        assert first.status is OrderStatus.FILLED and first.filled_qty == 200
        assert first.tags["requested_qty"] == 250
        assert second.status is OrderStatus.REJECTED and "below one lot" in second.reason

    def test_cash_and_position_checks(self):
        engine = hk_engine(cash=50_000)
        engine.add_data(bars_for(TENCENT, [300, 300], "2024-01-02 02:30"))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(200), 1: sell(100)})
        engine.add_strategy(rec)
        result = engine.run()
        assert [o.status for o in result.orders] == [OrderStatus.REJECTED, OrderStatus.REJECTED]
        assert "insufficient HKD cash" in result.orders[0].reason
        assert "short selling" in result.orders[1].reason
        assert result.final_balances["HKD"] == 50_000

    def test_crypto_fractional_quantity_min_notional_and_taker_fee(self):
        engine = BacktestEngine()
        engine.add_venue("BINANCE", {"USDT": 10_000})
        engine.add_instrument(CurrencyPair(id=BTC, raw_symbol="BTCUSDT", currency="USDT", base_currency="BTC",
                                           quote_currency="USDT", size_increment=0.00001, min_notional=5.0))
        engine.add_data(bars_for(BTC, [40_000, 40_000], "2024-01-01 01:00"))
        rec = Recorder([BarType.of(BTC, "1-HOUR")], {0: buy(0.0123456), 1: buy(0.0001)})
        engine.add_strategy(rec)
        result = engine.run()
        ok, small = result.orders
        assert ok.filled_qty == pytest.approx(0.01234)
        assert result.trade_log[0]["commission"] == pytest.approx(0.01234 * 40_000 * 0.001)
        assert small.status is OrderStatus.REJECTED and "minimum" in small.reason

    def test_limit_order_rests_then_fills_as_maker(self):
        engine = BacktestEngine()
        engine.add_venue("BINANCE", {"USDT": 100_000})
        engine.add_data(bars_for(BTC, [100, 100, 100], "2024-01-01 01:00", lows=[99, 99.5, 94], opens=[100, 100, 100]))
        rec = Recorder([BarType.of(BTC, "1-HOUR")],
                       {0: lambda s, b: s.submit_order(s.order_factory.limit(BTC, OrderSide.BUY, 1, 95))})
        engine.add_strategy(rec)
        result = engine.run()
        fill = result.trade_log[0]
        assert fill["price"] == 95 and fill["timestamp"] == pd.Timestamp("2024-01-01 03:00", tz="UTC")
        assert fill["commission"] == pytest.approx(95 * 0.001)              # Binance maker rate
        assert [type(e).__name__ for e in rec.events][-1] == "OrderFilled"

    def test_day_order_expires_on_the_next_trading_day(self):
        engine = hk_engine()
        engine.add_data(bars_for(TENCENT, [300, 300, 300], "2024-01-02 07:00", step=pd.Timedelta(hours=12)))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: lambda s, b: s.submit_order(
            s.order_factory.limit(TENCENT, OrderSide.BUY, 100, 250, time_in_force=TimeInForce.DAY))})
        engine.add_strategy(rec)
        result = engine.run()
        assert result.orders[0].status is OrderStatus.CANCELED and "DAY" in result.orders[0].reason


class TestRiskEngine:
    def test_drawdown_halt_denies_buys_but_allows_sells(self):
        engine = hk_engine(cash=100_000, risk=RiskConfig(max_drawdown_pct=0.10))
        engine.add_data(bars_for(TENCENT, [300, 200, 200, 200], "2024-01-02 02:30"))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(300), 1: buy(10), 2: sell(300)})
        engine.add_strategy(rec)
        result = engine.run()
        statuses = [o.status for o in result.orders]
        assert statuses == [OrderStatus.FILLED, OrderStatus.DENIED, OrderStatus.FILLED]
        assert "halted" in result.orders[1].reason
        assert any(type(e).__name__ == "OrderDenied" for e in rec.events)

    def test_notional_and_position_limits(self):
        engine = hk_engine(risk=RiskConfig(max_order_notional={"HKD": 70_000}, max_position_pct=0.05))
        engine.add_data(bars_for(TENCENT, [300, 300], "2024-01-02 02:30"))
        # 300 shares = 90,000 HKD > 70,000; 200 shares = 6% of 1,000,000 HKD equity > 5%.
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(300), 1: buy(200)})
        engine.add_strategy(rec)
        result = engine.run()
        assert "exceeds the limit" in result.orders[0].reason
        assert result.orders[1].status is OrderStatus.DENIED and "of HKD equity" in result.orders[1].reason

    def test_engine_managed_stop_loss(self):
        engine = hk_engine(risk=RiskConfig(stop_loss_pct=0.05))
        engine.add_data(bars_for(TENCENT, [300, 290, 280, 270], "2024-01-02 02:30"))
        rec = Recorder([BarType.of(TENCENT, "1-HOUR")], {0: buy(100)})
        engine.add_strategy(rec)
        result = engine.run()
        t = result.trades()
        assert list(t["side"]) == ["BUY", "SELL"] and t["exit_reason"].iloc[1] == "stop_loss"
        assert t["price"].iloc[1] == 280                     # first close at or below 285
        assert str(result.orders[1].client_order_id).startswith("R-")
        assert sum(type(e).__name__ == "OrderFilled" for e in rec.events) == 2   # the owner is told


class TestStrategyAPI:
    def test_views_are_read_only_and_orders_belong_to_their_strategy(self):
        engine = hk_engine()
        engine.add_data(bars_for(TENCENT, [300], "2024-01-02 02:30"))
        a = Recorder([BarType.of(TENCENT, "1-HOUR")], config=StrategyConfig(strategy_id="A"))
        b = Recorder([], config=StrategyConfig(strategy_id="B"))
        engine.add_strategy(a)
        engine.add_strategy(b)
        engine.run()
        with pytest.raises(AttributeError):
            a.cache.add_bar
        assert not hasattr(a.portfolio, "apply_fill")
        assert a.cache.last_price(TENCENT) == 300
        with pytest.raises(ValueError):
            a.submit_order(b.order_factory.market(TENCENT, OrderSide.BUY, 100))

    def test_signals_slices_and_close_position(self):
        engine = hk_engine()
        engine.add_data(bars_for(TENCENT, [300, 305], "2024-01-02 02:30"))

        class Publisher(Recorder):
            def on_bar(self, bar):
                super().on_bar(bar)
                self.publish_signal("momentum", bar.close - 300, bar.instrument_id)

        pub = Publisher([BarType.of(TENCENT, "1-HOUR")], {0: buy(100), 1: lambda s, b: s.close_position(TENCENT)},
                        config=StrategyConfig(strategy_id="P"))
        sub = Recorder([], config=StrategyConfig(strategy_id="S"), slices=True)
        engine.add_strategy(pub)
        engine.add_strategy(sub)
        sub.subscribe_signals("momentum")
        result = engine.run()
        assert [s.value for s in sub.signals] == [0, 5] and sub.signals[0].source == "P"
        assert len(sub.slices) == 2
        assert [t["side"] for t in result.trade_log] == ["BUY", "SELL"]
        assert result.open_positions == {}

    def test_engine_runs_once(self):
        engine = hk_engine()
        engine.run()
        with pytest.raises(RuntimeError):
            engine.run()

    def test_unknown_instrument_gets_venue_defaults(self):
        engine = BacktestEngine()
        engine.add_venue("XHKG", {"HKD": 1})
        engine.add_data(bars_for(InstrumentId.from_str("00005.XHKG"), [60], "2024-01-02 02:30"))
        inst = engine.cache.instrument("00005.XHKG")
        assert inst.lot_size == 100 and inst.currency == "HKD"
        with pytest.raises(ValueError):
            engine.add_data(bars_for(APPLE, [1], "2024-01-02 15:30"))      # XNAS not added
