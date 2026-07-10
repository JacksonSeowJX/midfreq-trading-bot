"""LivePortfolio and LiveTradingEngine: broker routing, lot rounding, candle lifecycle."""
from datetime import datetime, timedelta, timezone

import pytest

from core.live_engine import LivePortfolio, LiveTradingEngine
from core.models import Candle, Timeframe
from core.strategy import STRATEGY_REGISTRY

T0 = datetime(2026, 1, 5, 10, 0)


class FakeGateway:
    def __init__(self, reject=False):
        self.reject = reject
        self.orders = []

    def get_account_info(self):
        return {'cash': 1_000_000.0, 'total_assets': 1_000_000.0, 'market_value': 0.0}

    def get_positions(self):
        return {}

    def place_order(self, symbol, is_buy, qty, price):
        if self.reject:
            return {'ok': False, 'order_id': None, 'message': 'rejected'}
        self.orders.append((symbol, is_buy, qty, price))
        return {'ok': True, 'order_id': len(self.orders), 'message': 'submitted'}


def make_engine(gateway=None, timeframe=Timeframe.HOUR_1):
    info = STRATEGY_REGISTRY['Bollinger Bands']
    defaults = {k: v['default'] for k, v in info['params'].items()}
    return LiveTradingEngine(provider=None, gateway=gateway or FakeGateway(),
                             strategy_class=info['class'], symbols=['HK.09888'],
                             timeframe=timeframe, **defaults)


def candle(ts, close=100.0):
    return Candle(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


class TestLivePortfolio:
    def test_broker_accepted_trade_mirrors_locally(self):
        gw = FakeGateway()
        p = LivePortfolio(gw)
        p.execute_trade('A', True, 100, 50.0, T0)
        assert gw.orders == [('A', True, 100, 50.0)]
        assert p.get_position_qty('A') == 100

    def test_broker_rejection_leaves_local_state_untouched(self):
        p = LivePortfolio(FakeGateway(reject=True))
        cash_before = p.cash
        p.execute_trade('A', True, 100, 50.0, T0)
        assert p.get_position_qty('A') == 0
        assert p.cash == cash_before
        assert p.trade_history == []

    def test_warmup_blocks_orders(self):
        gw = FakeGateway()
        p = LivePortfolio(gw)
        p.warming_up = True
        p.execute_trade('A', True, 100, 50.0, T0)
        assert gw.orders == []

    def test_buy_rounded_down_to_board_lot(self):
        gw = FakeGateway()
        p = LivePortfolio(gw)
        p.lot_sizes = {'A': 400}
        p.execute_trade('A', True, 500, 50.0, T0)
        assert gw.orders == [('A', True, 400, 50.0)]

    def test_buy_below_one_lot_skipped(self):
        gw = FakeGateway()
        p = LivePortfolio(gw)
        p.lot_sizes = {'A': 400}
        p.execute_trade('A', True, 300, 50.0, T0)
        assert gw.orders == []

    def test_adopts_existing_broker_positions(self):
        class GatewayWithPosition(FakeGateway):
            def get_positions(self):
                return {'A': {'qty': 200.0, 'entry_price': 45.0}}
        p = LivePortfolio(GatewayWithPosition())
        assert p.get_position_qty('A') == 200.0


class TestCandleLifecycle:
    def test_newer_timestamp_finalizes_previous_candle(self):
        eng = make_engine()
        eng._on_candle_update('HK.09888', candle(T0))
        assert eng._candles_processed == 0  # still forming
        eng._on_candle_update('HK.09888', candle(T0 + timedelta(hours=1)))
        assert eng._candles_processed == 1

    def test_same_timestamp_update_does_not_finalize(self):
        eng = make_engine()
        eng._on_candle_update('HK.09888', candle(T0, close=100))
        eng._on_candle_update('HK.09888', candle(T0, close=101))
        assert eng._candles_processed == 0

    def test_shutdown_finalizes_elapsed_forming_candle(self):
        eng = make_engine()
        hkt_now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=timezone.utc)
        eng._forming = {'HK.09888': candle(hkt_now - timedelta(minutes=5))}
        eng._finalize_elapsed_candles()
        assert eng._candles_processed == 1
        assert eng._forming == {}

    def test_shutdown_keeps_unelapsed_candle(self):
        eng = make_engine()
        hkt_now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=timezone.utc)
        eng._forming = {'HK.09888': candle(hkt_now + timedelta(minutes=30))}
        eng._finalize_elapsed_candles()
        assert eng._candles_processed == 0
        assert 'HK.09888' in eng._forming

    def test_parallel_sessions_get_distinct_log_files(self):
        e1 = make_engine()
        e2 = make_engine()
        assert e1._session_file != e2._session_file
