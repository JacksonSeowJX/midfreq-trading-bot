"""
Live Trading Engine
===================
Runs a strategy against Moomoo's real-time candle stream and routes its
signals to an OrderGateway (paper trading).

Design: strategies are unchanged from backtesting. They call
portfolio.execute_trade() exactly as before — LivePortfolio intercepts the
call, submits a real (paper) order through the gateway, and only records
the trade locally if the broker accepted it.

Candle-completion detection: OpenD pushes an update every time the
current, still-forming candle changes. A candle is only final when a push
arrives with a NEWER timestamp — at that moment the previous candle is
fed to the strategy (same one-decision-per-closed-candle rhythm as the
backtester).
"""
import time
import json
from typing import Dict, Any, Optional, Type
from datetime import datetime
from pathlib import Path

from core.models import Candle, Timeframe
from core.portfolio import Portfolio
from core.strategy import BaseStrategy
from core.risk_manager import RiskManager
from core.order_gateway import OrderGateway


class LivePortfolio(Portfolio):
    """
    Portfolio that routes trades through a broker gateway.

    Local bookkeeping (positions, trade log, peak prices) mirrors the
    broker state so strategies and the RiskManager work unchanged.
    """

    def __init__(self, gateway: OrderGateway, commission_rate: float = Portfolio.HK_FEE_RATE):
        acc = gateway.get_account_info()
        initial_cash = acc.get('total_assets', 0.0) or acc.get('cash', 0.0)
        super().__init__(initial_cash=initial_cash, commission_rate=commission_rate)
        self.cash = acc.get('cash', initial_cash)
        self.gateway = gateway
        # While True, execute_trade is a no-op — used to warm up strategy
        # indicator state on historical candles without trading on them.
        self.warming_up = False
        # HK board lots: orders must be multiples of the symbol's lot size
        self.lot_sizes: Dict[str, int] = {}

        # Adopt any existing broker positions so the strategy knows about them
        for symbol, pos in gateway.get_positions().items():
            self.positions[symbol] = dict(pos)
            self._peak_prices[symbol] = pos['entry_price']

    def execute_trade(self, symbol: str, is_buy: bool, qty: float, price: float,
                      timestamp: datetime, exit_reason: Optional[str] = None):
        if self.warming_up or qty <= 0:
            return

        # Round BUY qty down to the symbol's board lot (SELLs pass through —
        # closing an existing position is always lot-aligned already)
        lot = self.lot_sizes.get(symbol)
        if is_buy and lot and lot > 1:
            rounded = int(qty // lot) * lot
            if rounded != qty:
                if rounded <= 0:
                    print(f"[{timestamp}] SKIPPED BUY {qty} {symbol}: below board lot of {lot}")
                    return
                print(f"[{timestamp}] Lot-adjusted BUY {symbol}: {qty} -> {rounded} (lot {lot})")
                qty = rounded

        # 1. Submit to the broker FIRST
        result = self.gateway.place_order(symbol, is_buy, qty, price)
        if not result['ok']:
            print(f"[{timestamp}] Broker rejected {'BUY' if is_buy else 'SELL'} "
                  f"{qty} {symbol} @ {price}: {result['message']}")
            return

        # 2. Mirror locally (keeps strategy + risk manager state consistent)
        super().execute_trade(symbol, is_buy, qty, price, timestamp, exit_reason=exit_reason)
        print(f"[{timestamp}] {'BUY' if is_buy else 'SELL'} {qty} {symbol} @ {price} "
              f"(order {result['order_id']}{', ' + exit_reason if exit_reason else ''})")

    def sync_with_broker(self):
        """Re-align local cash/positions with the broker's records."""
        acc = self.gateway.get_account_info()
        if acc:
            self.cash = acc['cash']
        broker_pos = self.gateway.get_positions()
        for symbol, pos in broker_pos.items():
            local_qty = self.get_position_qty(symbol)
            if local_qty != pos['qty']:
                print(f"  [sync] {symbol}: local qty {local_qty} -> broker qty {pos['qty']}")
                self.positions[symbol] = dict(pos)
        for symbol in list(self.positions.keys()):
            if symbol not in broker_pos:
                print(f"  [sync] {symbol}: no longer held at broker, removing locally")
                del self.positions[symbol]


class LiveTradingEngine:
    """
    Subscribes to live candles, feeds completed candles to a strategy,
    and periodically snapshots equity to a session log.
    """

    def __init__(self, provider, gateway: OrderGateway,
                 strategy_class: Type[BaseStrategy],
                 symbols: list, timeframe: Timeframe = Timeframe.MIN_1,
                 risk_manager: Optional[RiskManager] = None,
                 session_log_dir: str = "live_sessions",
                 **strategy_params):
        self.provider = provider
        self.gateway = gateway
        self.symbols = symbols
        self.timeframe = timeframe

        self.portfolio = LivePortfolio(gateway)
        self.strategy = strategy_class(self.portfolio, risk_manager=risk_manager,
                                       **strategy_params)

        # Last-seen (still forming) candle per symbol
        self._forming: Dict[str, Candle] = {}
        self._candles_processed = 0
        self._started_at: Optional[datetime] = None

        self._log_dir = Path(session_log_dir)
        self._log_dir.mkdir(exist_ok=True)
        self._session_file = self._log_dir / f"session_{datetime.now():%Y%m%d_%H%M%S}.jsonl"

    def _warm_up(self, candles: int = 60):
        """
        Preload recent historical candles so indicator state (moving
        averages, z-scores, RSI…) is warm from the first live candle.
        Trading is disabled during warmup — stale data can't place orders.
        """
        from datetime import timedelta
        self.portfolio.warming_up = True
        lookback_days = {Timeframe.MIN_1: 3, Timeframe.MIN_5: 10,
                         Timeframe.HOUR_1: 30, Timeframe.DAY_1: 200}.get(self.timeframe, 5)
        for symbol in self.symbols:
            df = self.provider.get_historical_data(
                symbol, self.timeframe,
                datetime.now() - timedelta(days=lookback_days), datetime.now())
            if df.empty:
                print(f"  [warmup] {symbol}: no history available")
                continue
            df = df.tail(candles)
            for idx, row in df.iterrows():
                candle = Candle(timestamp=idx, open=row['open'], high=row['high'],
                                low=row['low'], close=row['close'], volume=row['volume'])
                self.strategy.on_data(symbol, candle)
            print(f"  [warmup] {symbol}: {len(df)} candles "
                  f"({str(df.index.min())[:16]} -> {str(df.index.max())[:16]})")
        self.portfolio.warming_up = False
        print("  [warmup] complete — trading enabled\n")

    # ─── Candle stream handling ────────────────────────────────────

    def _on_candle_update(self, symbol: str, candle: Candle):
        """Called on EVERY push update of the current candle."""
        prev = self._forming.get(symbol)

        if prev is not None and candle.timestamp > prev.timestamp:
            # The previous candle is now final — this is the decision point
            self._on_candle_closed(symbol, prev)

        self._forming[symbol] = candle

    def _on_candle_closed(self, symbol: str, candle: Candle):
        self._candles_processed += 1
        self.strategy.on_data(symbol, candle)

        # Equity snapshot per closed candle
        prices = {s: c.close for s, c in self._forming.items()}
        prices[symbol] = candle.close
        equity = self.portfolio.get_current_equity(prices)
        self._log_event({
            'type': 'candle_close', 'symbol': symbol,
            'timestamp': str(candle.timestamp), 'close': candle.close,
            'equity': equity,
        })

    def _log_event(self, event: Dict[str, Any]):
        with open(self._session_file, 'a') as f:
            f.write(json.dumps(event) + '\n')

    # ─── Main loop ─────────────────────────────────────────────────

    def run(self, duration_minutes: Optional[float] = None, sync_every_s: int = 300):
        """
        Start streaming and trading. Blocks until duration expires or Ctrl-C.
        """
        self._started_at = datetime.now()
        acc = self.gateway.get_account_info()
        print("=" * 60)
        print(f"LIVE PAPER TRADING — {self.strategy.__class__.__name__}")
        print(f"Symbols: {self.symbols} | Timeframe: {self.timeframe.value}")
        print(f"Paper account: cash={acc.get('cash', 0):,.2f} "
              f"total={acc.get('total_assets', 0):,.2f}")
        print(f"Session log: {self._session_file}")
        print("=" * 60)

        self._log_event({'type': 'session_start', 'timestamp': str(self._started_at),
                         'strategy': self.strategy.__class__.__name__,
                         'symbols': self.symbols, 'timeframe': self.timeframe.value,
                         'account': acc})

        self.strategy.on_start()

        # Fetch board lot sizes so buys are exchange-valid
        try:
            self.portfolio.lot_sizes = self.provider.get_lot_sizes(self.symbols)
            print(f"Board lots: {self.portfolio.lot_sizes}")
        except Exception as e:
            print(f"  [!] Lot size lookup failed ({e}) — orders may be rejected as odd lots")

        self._warm_up()
        self.provider.start_live_streaming_multi(self.symbols, self.timeframe,
                                                 self._on_candle_update)

        deadline = (time.time() + duration_minutes * 60) if duration_minutes else None
        last_sync = time.time()
        try:
            while True:
                time.sleep(1)
                if time.time() - last_sync >= sync_every_s:
                    self.portfolio.sync_with_broker()
                    last_sync = time.time()
                if deadline and time.time() >= deadline:
                    print("\nSession duration reached.")
                    break
        except KeyboardInterrupt:
            print("\nStopped by user.")

        self._shutdown()

    def _shutdown(self):
        acc = self.gateway.get_account_info()
        positions = self.gateway.get_positions()
        print("\n" + "=" * 60)
        print("SESSION SUMMARY")
        print("=" * 60)
        print(f"Candles processed: {self._candles_processed}")
        print(f"Trades submitted:  {len(self.portfolio.trade_history)}")
        print(f"Paper account:     cash={acc.get('cash', 0):,.2f} "
              f"total={acc.get('total_assets', 0):,.2f}")
        if positions:
            print("Open positions:")
            for sym, pos in positions.items():
                print(f"  {sym}: {pos['qty']} @ {pos['entry_price']}")
        else:
            print("Open positions:    none")

        self._log_event({'type': 'session_end', 'timestamp': str(datetime.now()),
                         'candles_processed': self._candles_processed,
                         'trades': len(self.portfolio.trade_history),
                         'account': acc, 'positions': positions})
        print(f"Full session log:  {self._session_file}")
