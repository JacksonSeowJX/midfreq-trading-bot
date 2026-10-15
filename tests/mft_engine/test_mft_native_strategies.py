"""
The native ports against the core strategies they replace. Same data, same costs; the native
version runs on the new engine with its default fill model (bar close plus slippage) and the
venue's fee model, the core version on midfreq.backtest.backtester. Trades are compared as (instrument, time in
UTC, side, quantity, price, commission) and final equity exactly.
"""
import contextlib
import io

import pandas as pd
import pytest

from midfreq.backtest.backtester import Backtester
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.strategies.candle_strategies import CrossSectionalMomentum, CrossSectionalReversal, MovingAverageCrossover
from midfreq.backtest import BacktestEngine, FillModel
from midfreq.core.model import BarType, PercentFeeModel
from midfreq.data.persistence import LegacyDataReader
from midfreq.strategies.event_driven import (
    CrossSectionalRanker, CrossSectionalRankerConfig, EqualWeightBasket, EqualWeightBasketConfig,
    MACrossover, MACrossoverConfig,
)

from mft_synthetic import HK_CLOSES, HK_SYMBOLS, US_CLOSES, US_SYMBOLS, write_universe

SLIPPAGE = 5.0
US_DAYS = 61            # 61 x 7 = 427 hourly slices: 427 is not a multiple of 6 (see below)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("native")
    write_universe(d, HK_SYMBOLS, HK_CLOSES, "2024-01-02", 110, "1h", seed=7)
    write_universe(d, US_SYMBOLS, US_CLOSES, "2024-02-20", US_DAYS, "1h", seed=11)
    return d


def run_core(data_dir, cls, symbols, rate, **params):
    pf = Portfolio(initial_cash=100_000.0, commission_rate=rate)
    with contextlib.redirect_stdout(io.StringIO()):
        metrics = Backtester(DataStorage(str(data_dir)), pf, slippage_bps=SLIPPAGE).run(
            cls, symbols, Timeframe.HOUR_1, **params)
    reader = LegacyDataReader(data_dir)
    trades = []
    for t in pf.trade_history:
        iid = reader.instrument_id(t["symbol"])
        utc = reader.venues.get(iid.venue).localize(pd.DatetimeIndex([t["timestamp"]]))[0]
        trades.append((str(iid), utc, t["action"], t["qty"], t["price"], t["commission"]))
    return trades, metrics


def native_engine(data_dir, symbols, rate, venue_currency):
    reader = LegacyDataReader(data_dir)
    engine = BacktestEngine()
    ids = [reader.instrument_id(s) for s in symbols]
    for venue in dict.fromkeys(i.venue for i in ids):
        first = venue == ids[0].venue
        engine.add_venue(venue, {venue_currency: 100_000.0} if first else None,
                         fill_model=FillModel(slippage_bps=SLIPPAGE), fee_model=PercentFeeModel(rate))
    for iid in ids:
        engine.add_data(reader.read_bars(iid, "1-HOUR"))
    return engine, ids


def native_trades(result):
    return [(t["instrument_id"], t["timestamp"], t["side"], t["qty"], t["price"], t["commission"])
            for t in result.trade_log]


def test_ma_crossover_port_matches_core(data_dir):
    core_trades, core_metrics = run_core(data_dir, MovingAverageCrossover, HK_SYMBOLS, Portfolio.HK_FEE_RATE,
                                         fast_period=5, slow_period=20)
    engine, ids = native_engine(data_dir, HK_SYMBOLS, Portfolio.HK_FEE_RATE, "HKD")
    engine.add_strategy(MACrossover(MACrossoverConfig(
        bar_types=[str(BarType.of(i, "1-HOUR")) for i in ids], fast_period=5, slow_period=20)))
    result = engine.run()
    assert len(core_trades) > 20
    assert native_trades(result) == core_trades
    assert result.final_equity["HKD"] == core_metrics["final_equity"]
    assert all(t["exit_reason"] == "signal" for t in result.trade_log if t["side"] == "SELL")


def xs_native(data_dir, lookback, top_n, rebalance_every, select):
    engine, ids = native_engine(data_dir, US_SYMBOLS, 0.00005, "USD")
    engine.add_strategy(CrossSectionalRanker(CrossSectionalRankerConfig(
        instrument_ids=[str(i) for i in ids], lookback=lookback, rebalance_every=rebalance_every)))
    basket = EqualWeightBasket(EqualWeightBasketConfig(top_n=top_n, select=select, currency="USD",
                                                       slippage_bps=SLIPPAGE, commission_rate=0.00005))
    engine.add_strategy(basket)
    return engine.run(), basket


@pytest.mark.parametrize("core_cls, select", [(CrossSectionalReversal, "bottom"), (CrossSectionalMomentum, "top")])
def test_cross_sectional_port_matches_core(data_dir, core_cls, select):
    # Registry defaults (lookback 10, top 2, rebalance every 6). The data has 427 slices, not a
    # multiple of 6, so the final slice is not a rebalance and the two versions see the same ones.
    core_trades, core_metrics = run_core(data_dir, core_cls, US_SYMBOLS, 0.00005,
                                         lookback=10, top_n=2, rebalance_every=6)
    result, basket = xs_native(data_dir, 10, 2, 6, select)
    assert len(core_trades) > 20 and basket.rebalances > 50
    assert native_trades(result) == core_trades
    assert result.final_equity["USD"] == core_metrics["final_equity"]


def test_cross_sectional_port_also_rebalances_on_the_final_slice(data_dir):
    """With a rebalance every slice, the only difference is the last slice, which the core version
    never closes (it waits for a next timestamp that does not come)."""
    core_trades, _ = run_core(data_dir, CrossSectionalReversal, US_SYMBOLS, 0.00005,
                              lookback=5, top_n=1, rebalance_every=1)
    result, _ = xs_native(data_dir, 5, 1, 1, "bottom")
    native = native_trades(result)
    last = result.last_ts
    assert [t for t in native if t[1] < last] == core_trades
    assert all(t[1] == last for t in native[len(core_trades):])


def test_ranking_is_published_as_a_signal(data_dir):
    engine, ids = native_engine(data_dir, US_SYMBOLS, 0.00005, "USD")
    engine.add_strategy(CrossSectionalRanker(CrossSectionalRankerConfig(
        instrument_ids=[str(i) for i in ids], lookback=10, rebalance_every=7)))
    seen = []

    class Listener(EqualWeightBasket):
        def on_signal(self, signal):
            seen.append(signal)

    engine.add_strategy(Listener(EqualWeightBasketConfig(strategy_id="listener")))
    engine.run()
    # Due every 7 slices (once a day); the first day has 7 closes, short of the 11 a 10-bar return needs.
    assert len(seen) == US_DAYS - 1
    ranking = seen[0].value
    assert len(ranking) == len(US_SYMBOLS)
    assert [score for _, score in ranking] == sorted(score for _, score in ranking)
    assert seen[0].source == "CrossSectionalRanker-001"


def test_native_port_respects_hong_kong_board_lots(data_dir):
    """The core backtester traded odd lots in Hong Kong; on the new engine the venue rounds down."""
    engine, ids = native_engine(data_dir, HK_SYMBOLS, Portfolio.HK_FEE_RATE, "HKD")
    engine.add_strategy(CrossSectionalRanker(CrossSectionalRankerConfig(
        instrument_ids=[str(i) for i in ids], lookback=10, rebalance_every=6)))
    engine.add_strategy(EqualWeightBasket(EqualWeightBasketConfig(
        top_n=2, currency="HKD", slippage_bps=SLIPPAGE, commission_rate=Portfolio.HK_FEE_RATE)))
    result = engine.run()
    assert result.trade_log and all(t["qty"] % 100 == 0 for t in result.trade_log)
