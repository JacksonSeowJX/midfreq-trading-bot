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
from typing import Dict, Any, Optional, Type, Callable
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

    Multiple strategies share ONE paper account, so this portfolio must
    only claim the positions THIS strategy opened. Ownership is persisted
    to a per-strategy state file at session end and reloaded on startup —
    never inferred from the broker's aggregate holdings (which include
    other strategies' shares).
    """

    def __init__(self, gateway: OrderGateway, commission_rate: float = Portfolio.HK_FEE_RATE,
                 state_file: Optional[Path] = None,
                 on_trade: Optional[Callable[[Dict[str, Any]], None]] = None,
                 capital_cap: Optional[float] = None):
        acc = gateway.get_account_info() or {}
        initial_cash = acc.get('total_assets', 0.0) or acc.get('cash', 0.0)

        # The paper account holds ~1,000,000 while the validated backtest was
        # measured on 100,000, so an uncapped session trades the same strategy
        # at 10x the size the result was established at. capital_cap holds the
        # session to a stated figure so the forward test is comparable to the
        # backtest it is testing. The broker still holds the full balance; this
        # only limits what this session will deploy.
        self.capital_cap = capital_cap
        # Rebalance clock carried over from the previous session; the engine
        # reads this after warmup. 0 when there is no prior state.
        self.resumed_cross_section_count = 0
        self.cross_section_count = 0
        self.resumed_next_rebalance_at = None
        self.next_rebalance_at = None
        # Latest live price per symbol, supplied by the engine, used to price
        # orders at the market rather than at a candle close that may be a day old.
        self.price_ref: Optional[Callable[[str], Optional[float]]] = None
        if capital_cap is not None:
            initial_cash = min(initial_cash, capital_cap) if initial_cash else capital_cap
        super().__init__(initial_cash=initial_cash, commission_rate=commission_rate)
        self.cash = acc.get('cash', initial_cash)
        if capital_cap is not None:
            self.cash = min(self.cash, capital_cap)
            print(f"Capital capped at {capital_cap:,.2f} "
                  f"(account holds {acc.get('total_assets', 0):,.2f})")
        self.gateway = gateway
        # While True, execute_trade is a no-op — used to warm up strategy
        # indicator state on historical candles without trading on them.
        self.warming_up = False
        # HK board lots: orders must be multiples of the symbol's lot size
        self.lot_sizes: Dict[str, int] = {}
        self.state_file = state_file
        self.on_trade = on_trade

        # Resume THIS strategy's own positions from its state file
        if state_file is not None and state_file.exists():
            try:
                state = json.loads(state_file.read_text())
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"CRITICAL: state file {state_file} is not valid JSON ({e}). "
                    "Refusing to start with an unreadable position ledger — fix or "
                    "restore the file from git history before retrying."
                ) from e
            print(f"  [resume] raw state on disk: {state.get('positions', {})}")
            self.resumed_cross_section_count = int(state.get('cross_section_count', 0) or 0)
            if self.resumed_cross_section_count:
                print(f"  [resume] rebalance clock at {self.resumed_cross_section_count} cross-sections")
            broker_pos = self._query_positions_with_retry()
            for symbol, pos in state.get('positions', {}).items():
                if broker_pos is None:
                    # Broker unreachable: trust our own ledger rather than
                    # dropping claims (query failure is not "holds nothing")
                    qty = pos['qty']
                else:
                    broker_qty = broker_pos.get(symbol, {}).get('qty', 0)
                    qty = min(pos['qty'], broker_qty)
                    if qty < pos['qty']:
                        print(f"  [resume] {symbol}: state says {pos['qty']} but broker "
                              f"holds {broker_qty} — resuming with {qty}")
                if qty > 0:
                    self.positions[symbol] = {'qty': qty, 'entry_price': pos['entry_price']}
            for symbol, peak in state.get('peak_prices', {}).items():
                if symbol in self.positions:
                    self._peak_prices[symbol] = peak
            nra = state.get('next_rebalance_at')
            self.resumed_next_rebalance_at = int(nra) if nra else None
            # Capped session: its cash is its own ledger, carried across days.
            # Setting it to the full cap on every start ignored any positions
            # carried over, so equity read cap + positions (~2x) until the first
            # sync, and a rebalance in that window would have bought double size.
            if capital_cap is not None:
                if state.get('cash') is not None:
                    self.cash = min(float(state['cash']), acc.get('cash', float(state['cash'])))
                else:
                    own_cost = sum(p['qty'] * p['entry_price'] for p in self.positions.values())
                    self.cash = max(0.0, min(acc.get('cash', capital_cap), capital_cap - own_cost))
                print(f"  [resume] session cash {self.cash:,.2f} (cap {capital_cap:,.2f})")
            if self.positions:
                print(f"  [resume] restored positions: "
                      f"{ {s: p['qty'] for s, p in self.positions.items()} }")

    def _query_positions_with_retry(self, attempts: int = 3, wait_s: float = 2.0):
        """Position query with retries. Returns None if all attempts fail."""
        import time as _time
        for i in range(attempts):
            pos = self.gateway.get_positions()
            if pos is not None:
                return pos
            if i < attempts - 1:
                _time.sleep(wait_s)
        return None

    def save_state(self):
        """
        Persist this strategy's positions for the next session.

        Sanity check first: an existing position's entry_price should never
        change except through a BUY this session (visible in trade_history).
        If it did, something outside execute_trade() mutated self.positions —
        log it loudly rather than silently persist bad data (regression
        guard for the 2026-07-23 unexplained drift: Bollinger's own state
        went from 100 @ 106.9 to 400 @ 107.25 with zero trades that day).
        """
        if self.state_file is None:
            return
        if self.state_file.exists():
            try:
                prior = json.loads(self.state_file.read_text()).get('positions', {})
            except Exception:
                prior = {}
            buys_this_session = {t['symbol'] for t in self.trade_history if t['action'] == 'BUY'}
            for symbol, pos in self.positions.items():
                old = prior.get(symbol)
                if old and old['entry_price'] != pos['entry_price'] and symbol not in buys_this_session:
                    print(f"  [!!] STATE ANOMALY: {symbol} entry_price changed "
                          f"{old['entry_price']} -> {pos['entry_price']} (qty {old['qty']} -> "
                          f"{pos['qty']}) with NO buy this session. trade_history: "
                          f"{self.trade_history}")
        self.state_file.write_text(json.dumps({
            'saved_at': str(datetime.now()),
            'positions': self.positions,
            'peak_prices': {s: p for s, p in self._peak_prices.items()
                            if s in self.positions},
            # Cross-sectional strategies rebalance every N cross-sections, and
            # N was tuned on a continuous backtest. Live restarts daily, so the
            # count has to survive the restart or the rebalance never arrives.
            'cross_section_count': getattr(self, 'cross_section_count', 0),
            'next_rebalance_at': getattr(self, 'next_rebalance_at', None),
            # Session cash ledger under a capital cap (see __init__/sync).
            'cash': self.cash if self.capital_cap is not None else None,
        }, indent=1))

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

        # Price at the live market, not the decision candle's close, which on
        # the first rebalance of a session is the previous day's 16:00 close.
        ref = None
        if self.price_ref is not None:
            try:
                ref = self.price_ref(symbol)
            except Exception:
                ref = None
        ref = ref or price

        if hasattr(self.gateway, 'place_and_confirm'):
            slip = 0.01
            if is_buy:
                # Size against the worst-case fill (the limit), so a confirmed
                # fill can never be rejected by the local cash check and leave
                # the broker and this ledger disagreeing.
                worst = ref * (1 + slip) * (1 + self.commission_rate)
                affordable = int(self.cash // worst) if worst > 0 else 0
                if lot and lot > 1:
                    affordable = (affordable // lot) * lot
                if affordable < qty:
                    print(f"[{timestamp}] BUY {symbol} trimmed {qty} -> {affordable} to fit cash at the limit")
                    qty = affordable
                if qty <= 0:
                    return
            else:
                qty = min(qty, self.get_position_qty(symbol))
                if qty <= 0:
                    return
            result = self.gateway.place_and_confirm(symbol, is_buy, qty, ref, slip=slip)
            if not result['ok']:
                print(f"[{timestamp}] NOT FILLED {'BUY' if is_buy else 'SELL'} {qty} {symbol} "
                      f"(limit {result.get('limit')}): {result['message']} — nothing booked")
                return
            fill_qty, fill_px = result['dealt_qty'], result['dealt_price']
            if fill_qty < qty:
                print(f"[{timestamp}] PARTIAL {symbol}: {fill_qty} of {qty} filled, remainder cancelled")
        else:
            # Legacy gateways (tests): accepted == filled at the given price.
            result = self.gateway.place_order(symbol, is_buy, qty, ref)
            if not result['ok']:
                print(f"[{timestamp}] Broker rejected {'BUY' if is_buy else 'SELL'} "
                      f"{qty} {symbol} @ {ref}: {result['message']}")
                return
            fill_qty, fill_px = qty, ref

        # Mirror exactly what the broker did
        super().execute_trade(symbol, is_buy, fill_qty, fill_px, timestamp, exit_reason=exit_reason)
        qty, price = fill_qty, fill_px
        print(f"[{timestamp}] {'BUY' if is_buy else 'SELL'} {qty} {symbol} @ {price} "
              f"(order {result['order_id']}{', ' + exit_reason if exit_reason else ''})")

        if self.on_trade:
            self.on_trade({
                'type': 'trade', 'timestamp': str(timestamp), 'symbol': symbol,
                'side': 'BUY' if is_buy else 'SELL', 'qty': qty, 'price': price,
                'order_id': result['order_id'], 'exit_reason': exit_reason,
            })

    def sync_with_broker(self):
        """
        Sanity-check local claims against the broker. The broker's holdings
        are the AGGREGATE across all strategies sharing the account, so we
        only shrink local claims (our shares can't exceed what the broker
        holds) — we never adopt the surplus, which belongs to other sessions.
        """
        acc = self.gateway.get_account_info()
        if acc:
            # sync adopts the broker's cash, which is the balance across ALL
            # sessions sharing the account. Under a cap, re-deriving cash as
            # (cap - what this session already holds) keeps total exposure at
            # the cap; adopting the raw balance would quietly lift it back to
            # the full account on the first sync, 5 minutes in.
            # Under a cap the session's cash is its own ledger: proceeds and
            # costs of its confirmed fills. The broker only bounds it from
            # above. Re-deriving it as (cap - cost of open positions), as this
            # used to, reset the session to a fresh cap every 5 minutes and
            # erased every realised gain or loss.
            if self.capital_cap is not None:
                self.cash = min(self.cash, acc['cash'])
            else:
                self.cash = acc['cash']
        broker_pos = self.gateway.get_positions()
        if broker_pos is None:
            # Query failed — no information. Leave all claims untouched.
            # (Treating failure as "holds nothing" wiped legitimate claims
            # and caused duplicate buying on 2026-07-15.)
            print("  [sync] position query failed — skipping reconciliation")
            return
        for symbol in list(self.positions.keys()):
            local_qty = self.get_position_qty(symbol)
            broker_qty = broker_pos.get(symbol, {}).get('qty', 0)
            if broker_qty < local_qty:
                print(f"  [sync] {symbol}: claimed {local_qty} but broker holds "
                      f"{broker_qty} — shrinking claim")
                if broker_qty <= 0:
                    del self.positions[symbol]
                else:
                    self.positions[symbol]['qty'] = broker_qty


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
                 commission_rate: float = Portfolio.HK_FEE_RATE,
                 capital_cap: Optional[float] = None,
                 stall_timeout_s: Optional[float] = None,
                 **strategy_params):
        self.provider = provider
        self.gateway = gateway
        self.symbols = symbols
        self.timeframe = timeframe
        # Silence after which the stream is presumed dead: 1.25 candle periods.
        _period_s = {Timeframe.MIN_1: 60, Timeframe.MIN_5: 300,
                     Timeframe.HOUR_1: 3600, Timeframe.DAY_1: 86400}.get(timeframe, 3600)
        self.stall_timeout_s = stall_timeout_s or _period_s * 1.25

        log_dir = Path(session_log_dir)
        log_dir.mkdir(exist_ok=True)
        strat_slug = strategy_class.__name__.lower()
        # Per-strategy position ownership persists across sessions here
        state_file = log_dir / f"state_{strat_slug}.json"

        # Last-seen (still forming) candle per symbol
        self._forming: Dict[str, Candle] = {}
        self._candles_processed = 0
        self._closed_candles: Dict[str, list] = {}
        self._last_candle_at: Optional[float] = None
        self._last_saved_xs: Optional[int] = None
        self._started_at: Optional[datetime] = None

        self._log_dir = log_dir
        # Strategy name + PID + per-process counter in the filename —
        # parallel sessions launched in the same second must not share a log
        import os
        LiveTradingEngine._session_seq = getattr(LiveTradingEngine, '_session_seq', 0) + 1
        self._session_file = self._log_dir / (
            f"session_{datetime.now():%Y%m%d_%H%M%S}_{strat_slug}"
            f"_{os.getpid()}_{LiveTradingEngine._session_seq}.jsonl")

        self.portfolio = LivePortfolio(gateway, commission_rate=commission_rate,
                                       state_file=state_file, on_trade=self._log_event,
                                       capital_cap=capital_cap)
        # Rebalance clock carried over from the previous session, applied after
        # warmup so replay does not advance it.
        self._resumed_xs_count = self.portfolio.resumed_cross_section_count
        self.portfolio.price_ref = lambda s: (self._forming[s].close if s in self._forming else None)
        self.strategy = strategy_class(self.portfolio, risk_manager=risk_manager,
                                       **strategy_params)
        if hasattr(self.strategy, 'min_cross_section'):
            import math
            self.strategy.min_cross_section = math.ceil(0.9 * len(symbols))
        if hasattr(self.strategy, 'defer_unready_rebalance'):
            self.strategy.defer_unready_rebalance = True
        # Act on a cross-section as soon as the whole universe has reported it,
        # seconds after the candle closes, rather than when the next hourly
        # candle completes an hour later. A backtest books each trade at the
        # close of the candle it ranked on; this is the live equivalent.
        if hasattr(self.strategy, 'full_cross_section'):
            self.strategy.full_cross_section = len(symbols)

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

        # Warm up from the local parquet cache when it has the candles, and
        # only fall back to the broker's historical API when it does not.
        #
        # That API is metered per distinct symbol — 100 symbols per rolling
        # 7 days — and warmup touches every symbol in the session. A basket
        # strategy on 98 names therefore consumed the entire week's quota on
        # a single session start, which made a daily forward test impossible:
        # Monday would spend the quota and Tuesday through Friday would warm
        # up on nothing. The cache holds the same candles and costs nothing.
        from core.storage import DataStorage
        _store = DataStorage()
        _cached = _missed = 0

        def _history(symbol):
            nonlocal _cached, _missed
            want_from = datetime.now() - timedelta(days=lookback_days)
            try:
                df = _store.load_data(symbol.replace('.', '_'), self.timeframe.value)
            except Exception:
                df = None
            if df is not None and not df.empty:
                idx = df.index
                cutoff = want_from.replace(tzinfo=idx.tz) if getattr(idx, 'tz', None) else want_from
                recent = df[idx >= cutoff]
                if len(recent) >= candles:
                    _cached += 1
                    return recent
            _missed += 1
            return self.provider.get_historical_data(
                symbol, self.timeframe, want_from, datetime.now())

        # Feed in TIMESTAMP order across all symbols, not symbol by symbol.
        #
        # A cross-sectional strategy treats one timestamp across the whole
        # universe as a single observation, and detects the boundary by the
        # timestamp changing. Replaying symbol by symbol walks time forward
        # for AAPL, then jumps back to the start for ABBV, so every symbol
        # boundary looks like a new cross-section: warming up 20 symbols x 60
        # candles drove the counter to 1199 instead of 60 and fired 63
        # "rebalances" on single-symbol rankings. Harmless only because
        # trading is disabled here, and wrong in every other respect.
        frames = {}
        for symbol in self.symbols:
            df = _history(symbol)
            if df is None or df.empty:
                print(f"  [warmup] {symbol}: no history available")
                continue
            frames[symbol] = df.tail(candles)

        if frames:
            all_ts = sorted({ts for df in frames.values() for ts in df.index})
            for ts in all_ts:
                for symbol, df in frames.items():
                    if ts in df.index:
                        row = df.loc[ts]
                        self.strategy.on_data(symbol, Candle(
                            timestamp=ts, open=row['open'], high=row['high'],
                            low=row['low'], close=row['close'], volume=row['volume']))
            print(f"  [warmup] {len(frames)} symbols over {len(all_ts)} cross-sections "
                  f"({str(all_ts[0])[:16]} -> {str(all_ts[-1])[:16]})")
        self.portfolio.warming_up = False

        # Warmup is replay, not market activity, so it must not advance the
        # rebalance clock. Restore the counter to where the last real session
        # left it: rebalance_every was grid-searched on a CONTINUOUS backtest,
        # but live runs as a fresh process per day, so a counter that resets
        # each morning can never reach a rebalance_every larger than the ~7
        # hourly candles a US session delivers — the strategy would simply
        # never trade. Persisting it makes the live cadence match the tested one.
        if hasattr(self.strategy, '_cross_section_count'):
            self.strategy._cross_section_count = self._resumed_xs_count
            n = getattr(self.strategy, 'rebalance_every', 1)
            nra = self.portfolio.resumed_next_rebalance_at
            if nra is None:
                # No mark saved yet: next multiple of N strictly above the count.
                nra = (self._resumed_xs_count // n + 1) * n
            self.strategy._next_rebalance_at = nra
            print(f"  [warmup] rebalance clock resumed at {self._resumed_xs_count} "
                  f"cross-sections, next rebalance due at {nra} (rebalance_every={n})")

        print(f"  [warmup] complete — {_cached} from local cache, {_missed} from the broker "
              f"(broker history is quota-metered per symbol) — trading enabled\n")

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
        # Keep the candle so the session can write it back to the local cache
        # on the way out. Without this the cache only ever advances when
        # someone spends broker history quota on a backfill, so each day's
        # warmup primes on data one day older than the last — and a basket
        # strategy on 98 symbols cannot afford a daily backfill (100 symbols
        # per rolling 7 days). A live session already has the candles; this
        # just stops throwing them away.
        self._closed_candles.setdefault(symbol, []).append(candle)
        self._last_candle_at = time.time()

        # Save the rebalance clock the moment it advances, not only at
        # shutdown. The first S&P 100 session (2026-09-28) received three
        # full cross-sections, then hung during shutdown and took the VM with
        # it — so save_state never ran and all three were lost. A crash now
        # costs at most the cross-section in progress.
        xs = getattr(self.strategy, '_cross_section_count', None)
        if xs is not None and xs != self._last_saved_xs:
            self.portfolio.cross_section_count = xs
            self.portfolio.next_rebalance_at = getattr(self.strategy, '_next_rebalance_at', None)
            try:
                self.portfolio.save_state()
                self._last_saved_xs = xs
            except Exception as e:
                print(f"  [!] periodic state save failed: {e}")

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
        acc = self.gateway.get_account_info() or {}
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
        # Start the stall clock now, not at the first candle: a stream that
        # never delivers anything is the worst stall of all, and used to go
        # undetected because the detector only armed after a first candle.
        self._last_candle_at = time.time()
        resubscribed = False

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
                # Stall detector. On 2026-09-28 the stream delivered 3 full
                # hours, then 14 of 98 symbols for the 4th, then nothing; the
                # session sat idle for hours until its scheduled end and then
                # hung on the way out. With hourly candles a healthy stream
                # closes one every 60 minutes, so 75 minutes of silence after
                # at least one candle means it has died: stop now, cleanly,
                # while shutdown can still complete.
                if (self._last_candle_at is not None
                        and time.time() - self._last_candle_at > self.stall_timeout_s):
                    silent = round((time.time() - self._last_candle_at) / 60, 1)
                    self._log_event({'type': 'stall', 'timestamp': str(datetime.now()),
                                     'silent_min': silent, 'resubscribed_before': resubscribed})
                    if not resubscribed:
                        # One reconnect attempt before giving up the day: a
                        # dropped subscription is recoverable, and stopping
                        # cost the 2026-09-28 session its whole afternoon.
                        print(f"\n[!] STALL: no candle for {silent:.0f} min — resubscribing once")
                        try:
                            self.provider.start_live_streaming_multi(self.symbols, self.timeframe,
                                                                     self._on_candle_update)
                            resubscribed = True
                            self._last_candle_at = time.time()
                            continue
                        except Exception as e:
                            print(f"  [!] resubscribe failed: {e}")
                    print(f"\n[!] STALL: no candle for {silent:.0f} min — stopping cleanly")
                    break
        except KeyboardInterrupt:
            print("\nStopped by user.")

        self._shutdown()

    def _finalize_elapsed_candles(self):
        """
        Finalize still-forming candles whose time window has fully elapsed.

        Normally a candle is finalized when the NEXT candle's first push
        arrives — but after the market closes no further push comes, so the
        session's last candle (e.g. the 16:00 close) would silently never
        be evaluated. Called at shutdown.
        """
        from datetime import timedelta, timezone
        spans = {Timeframe.MIN_1: timedelta(minutes=1),
                 Timeframe.MIN_5: timedelta(minutes=5),
                 Timeframe.HOUR_1: timedelta(hours=1),
                 Timeframe.DAY_1: timedelta(days=1)}
        span = spans.get(self.timeframe)
        if span is None:
            return
        # Candle timestamps are the EXCHANGE's wall-clock time labelled as UTC
        # (10:30 for the first US hourly bar, Eastern time). Compare against
        # "now" in the same exchange's local time. This used Hong Kong time for
        # every market, which for a US session is 12-13 hours ahead, so a
        # mid-session stop would finalise still-forming candles as complete.
        from zoneinfo import ZoneInfo
        mkt = (self.symbols[0].split('.')[0].upper() if self.symbols else 'HK')
        tz = ZoneInfo('America/New_York' if mkt == 'US' else 'Asia/Hong_Kong')
        now = datetime.now(tz).replace(tzinfo=None).replace(tzinfo=timezone.utc)
        # Oldest first. In a stalled hour some symbols' forming candle is an
        # hour older than others'; finalising in storage order could feed the
        # newer ones first, after which the older ones arrive "late" and are
        # dropped, losing a whole hour for most of the universe.
        for symbol, candle in sorted(self._forming.items(), key=lambda kv: kv[1].timestamp):
            # Moomoo labels candles by window END time, so an elapsed window
            # means timestamp <= now.
            ts = candle.timestamp if candle.timestamp.tzinfo else candle.timestamp.replace(tzinfo=timezone.utc)
            if ts <= now:
                print(f"  [finalize] {symbol} {candle.timestamp} candle "
                      f"(window elapsed, no successor push)")
                self._on_candle_closed(symbol, candle)
                del self._forming[symbol]

    def _persist_candles(self):
        """Write this session's closed candles into the local parquet cache."""
        if not self._closed_candles:
            return
        try:
            import pandas as pd
            from core.storage import DataStorage
            store = DataStorage()
        except Exception as e:
            print(f"  [cache] not written ({e})")
            return
        written = failed = 0
        for symbol, candles in self._closed_candles.items():
            try:
                df = pd.DataFrame(
                    [{'open': c.open, 'high': c.high, 'low': c.low,
                      'close': c.close, 'volume': c.volume} for c in candles],
                    index=pd.DatetimeIndex([c.timestamp for c in candles], name='timestamp'))
                store.append_data(df, symbol.replace('.', '_'), self.timeframe.value)
                written += len(candles)
            except Exception:
                failed += 1
        print(f"  [cache] wrote {written} candles for "
              f"{len(self._closed_candles) - failed} symbols"
              + (f", {failed} failed" if failed else ""))

    def _shutdown(self):
        # Trading off before the last candles are finalised: they are fed after
        # the deadline (16:00 ET), and a rebalance falling due on them would
        # otherwise send orders at a closed market. It stays due instead, and
        # runs at the first live cross-section of the next session.
        self.portfolio.warming_up = True
        self._finalize_elapsed_candles()
        # State first: it is small, fast, and the only thing tomorrow's session
        # cannot do without. Writing 98 parquet files is slow and optional, so
        # it goes last — a hang there must not cost the rebalance clock again.
        if hasattr(self.strategy, '_cross_section_count'):
            self.portfolio.cross_section_count = self.strategy._cross_section_count
            self.portfolio.next_rebalance_at = getattr(self.strategy, '_next_rebalance_at', None)
        self.portfolio.save_state()
        self._persist_candles()
        acc = self.gateway.get_account_info() or {}
        positions = self.gateway.get_positions() or {}
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
