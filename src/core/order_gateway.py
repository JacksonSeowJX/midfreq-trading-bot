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
    OpenSecTradeContext, TrdEnv, TrdMarket, TrdSide, OrderType, ModifyOrderOp,
    SecurityFirm, RET_OK
)


class OrderGateway(ABC):
    """Abstract broker gateway — place orders and query account state."""

    @abstractmethod
    def place_order(self, symbol: str, is_buy: bool, qty: float, price: float) -> Dict[str, Any]:
        """Place an order. Returns dict with 'ok', 'order_id', 'message'."""
        ...

    @abstractmethod
    def get_positions(self) -> Optional[Dict[str, Dict[str, float]]]:
        """
        Returns { symbol: {'qty': float, 'entry_price': float} }.
        Returns None if the query FAILED — callers must treat None as
        "no information", never as "no positions".
        """
        ...

    @abstractmethod
    def get_account_info(self) -> Optional[Dict[str, float]]:
        """
        Returns {'cash': float, 'total_assets': float, 'market_value': float}.
        Returns None if the query failed.
        """
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

    @staticmethod
    def _is_final(status: str) -> bool:
        """Filled, cancelled or rejected: nothing about the order can change any more."""
        return status.startswith('FILLED_ALL') or any(
            s in status for s in ('CANCELLED', 'FAILED', 'DELETED', 'DISABLED'))

    def place_and_confirm(self, symbol: str, is_buy: bool, qty: float, ref_price: float,
                          slip: float = 0.01, wait_s: float = 30.0,
                          cancel_wait_s: float = 30.0) -> Dict[str, Any]:
        """
        Place a marketable limit order and wait for the broker to fill it.

        place_order() submits a limit at the price given, and callers used to
        treat "accepted" as "done". A limit at a candle's close does not fill
        if the price has moved away, and the first rebalance of each session
        is priced at the PREVIOUS day's close, so unfilled orders were likely:
        an unfilled sell left shares at the broker that the strategy believed
        it had sold, which the position sync never adopts back.

        This prices the limit `slip` through the reference price (above it for
        a buy, below for a sell) so it executes at the market, polls until it
        fills or `wait_s` passes, cancels any unfilled remainder, and returns
        what ACTUALLY filled. Callers must book dealt_qty at dealt_price, and
        nothing at all when dealt_qty is 0.
        """
        import time as _time
        limit = round(ref_price * (1 + slip if is_buy else 1 - slip), 2)
        sub = self.place_order(symbol, is_buy, qty, limit)
        out = {'ok': False, 'order_id': sub.get('order_id'), 'dealt_qty': 0.0,
               'dealt_price': None, 'limit': limit, 'message': sub.get('message')}
        if not sub.get('ok') or sub.get('order_id') is None:
            return out
        ctx = self._get_context()
        oid = sub['order_id']

        def poll(seconds, row=None):
            """Latest known state of the order, returning as soon as it is final."""
            deadline = _time.time() + seconds
            while True:
                ret, data = ctx.order_list_query(order_id=oid, trd_env=TrdEnv.SIMULATE,
                                                 refresh_cache=True)
                if ret == RET_OK and len(data):
                    row = data.iloc[0]
                    if self._is_final(str(row['order_status'])):
                        return row
                if _time.time() >= deadline:
                    return row
                _time.sleep(1.0)

        row = poll(wait_s)
        if row is None or not self._is_final(str(row['order_status'])):
            # Do not leave a live remainder behind: it could fill later, after
            # the strategy has moved on, as shares nobody is managing. Cancel
            # even when every status query failed, since then nothing is known.
            try:
                ret, msg = ctx.modify_order(ModifyOrderOp.CANCEL, oid, 0, 0, trd_env=TrdEnv.SIMULATE)
                if ret != RET_OK:
                    print(f"  [!] cancel of unfilled remainder {oid} refused: {msg}")
            except Exception as e:
                print(f"  [!] cancel of unfilled remainder {oid} failed: {e}")
            # A cancel is asynchronous, and the status can lag the broker's own
            # records. On 2026-10-05 the HK paper broker marked a cancelled order
            # CANCELLED_ALL about a second after the request, so a single re-query
            # 1s later still read SUBMITTED; and that night every US paper order
            # read SUBMITTED for the whole 30s wait although the broker timestamps
            # its fill 16-18s in, and showed FILLED_ALL only in the query after
            # the (refused) cancel. Wait for the final status, which also picks up
            # anything that filled before the cancel landed.
            row = poll(cancel_wait_s, row)
            if row is None or not self._is_final(str(row['order_status'])):
                print(f"  [!] order {oid} not final {cancel_wait_s:.0f}s after cancel "
                      f"({'unknown' if row is None else row['order_status']}); "
                      f"booking only what has filled so far")
        if row is not None:
            dq = float(row['dealt_qty'] or 0)
            dp = float(row['dealt_avg_price'] or 0)
            out.update(ok=dq > 0, dealt_qty=dq, dealt_price=dp if dq > 0 else None,
                       message=str(row['order_status']))
        return out

    def get_positions(self) -> Optional[Dict[str, Dict[str, float]]]:
        ctx = self._get_context()
        ret, data = ctx.position_list_query(trd_env=TrdEnv.SIMULATE)
        if ret != RET_OK:
            # None = query failed = "no information". NEVER return {} here:
            # callers would interpret it as "account holds nothing" and
            # wrongly drop their position claims (2026-07-15 incident).
            print(f"  [!] Position query failed: {data}")
            return None

        positions = {}
        for _, row in data.iterrows():
            if row['qty'] > 0:
                positions[row['code']] = {
                    'qty': float(row['qty']),
                    'entry_price': float(row['cost_price']) if row['cost_price'] == row['cost_price'] else 0.0,
                }
        return positions

    def get_account_info(self) -> Optional[Dict[str, float]]:
        ctx = self._get_context()
        ret, data = ctx.accinfo_query(trd_env=TrdEnv.SIMULATE)
        if ret != RET_OK:
            print(f"  [!] Account query failed: {data}")
            return None
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
