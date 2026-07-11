"""
Order Gateway Module
====================
Routes strategy signals to a broker for execution.

MoomooPaperGateway targets Moomoo's SIMULATE (paper trading) environment
via the OpenD gateway — real order API, simulated money. The same interface
can later be implemented for TrdEnv.REAL.

Note: Moomoo paper trading fills orders against the real live order book,
so this is the closest possible rehearsal for real execution.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime

from moomoo import (
    OpenSecTradeContext, TrdEnv, TrdMarket, TrdSide, OrderType,
    SecurityFirm, RET_OK
)


class OrderGateway(ABC):
    """Abstract broker gateway — place orders and query account state."""

    @abstractmethod
    def place_order(self, symbol: str, is_buy: bool, qty: float, price: float) -> Dict[str, Any]:
        """Place an order. Returns dict with 'ok', 'order_id', 'message'."""
        ...

    @abstractmethod
    def get_positions(self) -> Dict[str, Dict[str, float]]:
        """Returns { symbol: {'qty': float, 'entry_price': float} }"""
        ...

    @abstractmethod
    def get_account_info(self) -> Dict[str, float]:
        """Returns {'cash': float, 'total_assets': float, 'market_value': float}"""
        ...


class MoomooPaperGateway(OrderGateway):
    """
    Paper trading gateway using Moomoo's SIMULATE environment.

    Requires OpenD running and logged in. Orders are placed as NORMAL
    (limit) orders at the given price — Moomoo paper trading does not
    support true market orders, so the caller should pass the latest
    traded price (e.g. candle close); a marketable limit fills immediately
    against the live book in almost all cases.
    """

    def __init__(self, host: str = '127.0.0.1', port: int = 11111,
                 trd_market: TrdMarket = TrdMarket.HK):
        self.host = host
        self.port = port
        self.trd_market = trd_market
        self._trd_ctx: Optional[OpenSecTradeContext] = None
        self.order_log: List[Dict[str, Any]] = []

    def _get_context(self) -> OpenSecTradeContext:
        if self._trd_ctx is None:
            self._trd_ctx = OpenSecTradeContext(
                filter_trdmarket=self.trd_market,
                host=self.host, port=self.port,
                security_firm=SecurityFirm.FUTUSECURITIES
            )
            print(f"Connected to Moomoo trade context (PAPER) on {self.host}:{self.port}")
        return self._trd_ctx

    def close(self):
        if self._trd_ctx is not None:
            self._trd_ctx.close()
            self._trd_ctx = None
            print("Disconnected trade context.")

    def place_order(self, symbol: str, is_buy: bool, qty: float, price: float) -> Dict[str, Any]:
        ctx = self._get_context()
        side = TrdSide.BUY if is_buy else TrdSide.SELL

        ret, data = ctx.place_order(
            price=price,
            qty=qty,
            code=symbol,
            trd_side=side,
            order_type=OrderType.NORMAL,
            trd_env=TrdEnv.SIMULATE,
        )

        result: Dict[str, Any] = {'timestamp': datetime.now(), 'symbol': symbol,
                                  'side': 'BUY' if is_buy else 'SELL',
                                  'qty': qty, 'price': price}
        if ret == RET_OK:
            result['ok'] = True
            result['order_id'] = data['order_id'].iloc[0] if len(data) else None
            result['message'] = 'submitted'
        else:
            result['ok'] = False
            result['order_id'] = None
            result['message'] = str(data)
            print(f"  [!] Order REJECTED: {data}")

        self.order_log.append(result)
        return result

    def get_positions(self) -> Dict[str, Dict[str, float]]:
        ctx = self._get_context()
        ret, data = ctx.position_list_query(trd_env=TrdEnv.SIMULATE)
        if ret != RET_OK:
            print(f"  [!] Position query failed: {data}")
            return {}

        positions = {}
        for _, row in data.iterrows():
            if row['qty'] > 0:
                positions[row['code']] = {
                    'qty': float(row['qty']),
                    'entry_price': float(row['cost_price']) if row['cost_price'] == row['cost_price'] else 0.0,
                }
        return positions

    def get_account_info(self) -> Dict[str, float]:
        ctx = self._get_context()
        ret, data = ctx.accinfo_query(trd_env=TrdEnv.SIMULATE)
        if ret != RET_OK:
            print(f"  [!] Account query failed: {data}")
            return {}
        row = data.iloc[0]
        return {
            'cash': float(row['cash']),
            'total_assets': float(row['total_assets']),
            'market_value': float(row['market_val']),
        }

    def list_today_orders(self) -> List[Dict[str, Any]]:
        """Query today's orders (for reconciliation / display)."""
        ctx = self._get_context()
        ret, data = ctx.order_list_query(trd_env=TrdEnv.SIMULATE)
        if ret != RET_OK:
            return []
        return data.to_dict('records')

    def list_recent_orders(self, days: int = 14) -> List[Dict[str, Any]]:
        """Query order history over the last `days` days (for display)."""
        from datetime import timedelta
        ctx = self._get_context()
        end = datetime.now()
        start = end - timedelta(days=days)
        ret, data = ctx.history_order_list_query(
            trd_env=TrdEnv.SIMULATE,
            start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
        if ret != RET_OK:
            return []
        return data.to_dict('records')
