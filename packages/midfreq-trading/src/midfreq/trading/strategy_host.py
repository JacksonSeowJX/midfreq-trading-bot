"""
The strategy host: runs the node's strategies on the message bus.

Candle strategies (midfreq.strategies.candle_strategies) run unchanged through `LiveLegacyAdapter`,
the live counterpart of midfreq.backtest.legacy_adapter. Event-driven strategies
(midfreq.strategies.trading.Strategy) run natively through `NativeStrategyRunner`.

How a candle strategy trades live
---------------------------------
A candle strategy trades by calling `self.portfolio.execute_trade(symbol, is_buy, qty, price,
timestamp)` and reads `portfolio.cash` and `portfolio.positions` directly. The adapter gives it a
`LiveLegacyPortfolio`, a midfreq.core.portfolio.Portfolio subclass whose `execute_trade`:

1. does nothing while trading is off (warm-up, shutdown) or for a quantity of zero;
2. rounds a buy down to the instrument's lot, and skips it below one lot;
3. prices it at the live market (the latest pushed price of the instrument; the decision
   candle's close only when there is none yet), with the configured rule (`last`: `slip`
   through the last price, as the original engine did; `quote`: through the bid or ask);
4. trims a buy to what the strategy's cash can pay at the worst case (the limit plus
   commission), and a sell to what the strategy holds;
5. sends one LIMIT order to the OMS with `reference_price` = the decision candle's close the
   strategy passed, and the decision candle's timestamp and exit reason in its tags.

The strategy's portfolio is booked only when fills arrive (OrderFilled on the strategy's order
topic), by the original Portfolio bookkeeping, one booking per fill, never on submission. Each
booked fill is written to the session log as a `trade` event and the state file is saved.

By default (`confirm_fills`) `execute_trade` then waits for that order's outcome before it
returns, processing only order events (and the OMS's timers) meanwhile on the node's thread. The
original engine did the same (place, confirm, then book), and a candle strategy depends on it:
CrossSectionalReversal sizes its buys on the cash its sells just raised and stops buying once it
holds `top_n` names, so if a sell were still open when the buys are sized, the basket would
come out different (or, when the sell then fails, larger than top_n). Bars arriving meanwhile
are held and processed afterwards, in order. With `confirm_fills: false` execute_trade returns
at once and the fills are booked whenever they arrive.

Candles reach the strategy as midfreq.core.candle.Candle in the convention it was written for:
the exchange's local close time tagged as UTC (VenueSpec.to_legacy_index), one cross-section at
a time (the MarketDataService's CrossSection for the strategy's universe), in the universe's
order. Closed bars that never make a published cross-section (fewer than the minimum share of
the universe reported) are fed when a newer cross-section arrives, or at shutdown, as the
original engine fed every closed candle. The live settings the original engine gave the strategy
are given here too: `min_cross_section` (90% of the universe), `defer_unready_rebalance`, and
`full_cross_section` (the universe's size; for a cross-section the MarketDataService published
incomplete at its window's end, the number of bars it holds, so it is decided on now rather than
when the next candle arrives an hour later).

Records: `session_*.jsonl` through midfreq.trading.session_log and `state_<strategy>.json`
through midfreq.trading.state, both in the original engine's formats.
"""
from __future__ import annotations

import contextlib
import io
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd

from midfreq.core.accounting import Portfolio as AccountingPortfolio
from midfreq.core.cache import Cache
from midfreq.core.candle import Candle
from midfreq.core.common.clock import Clock
from midfreq.core.common.msgbus import MessageBus
from midfreq.core.model.data import Bar, BarSpecification, BarType, QuoteTick
from midfreq.core.model.enums import OrderSide, TimeInForce
from midfreq.core.model.events import (
    OrderAccepted, OrderCanceled, OrderDenied, OrderEvent, OrderFilled, OrderRejected,
)
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId
from midfreq.core.model.instruments import Instrument
from midfreq.core.model.orders import Order, OrderFactory
from midfreq.core.portfolio import Portfolio as CorePortfolio
from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from midfreq.core.venues.spec import VenueRegistry
from midfreq.trading import state as state_mod
from midfreq.trading.config import RiskManagerSettings, StrategySettings
from midfreq.trading.interfaces import (
    TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_ORDER_EVENTS, CrossSection,
)
from midfreq.trading.ops import get_logger, log_context
from midfreq.trading.session_log import SessionLog

log = get_logger("strategy_host")

_TERMINAL = (OrderCanceled, OrderRejected, OrderDenied)


# ── what the node gives the host ──────────────────────────────────────────────────────────────

@dataclass
class NodeServices:
    """The node's services the strategies need, as plain callables (so the host can be driven
    by the node, a replay or a test alike)."""
    clock: Clock
    msgbus: MessageBus
    cache: Cache
    venues: VenueRegistry
    submit: Callable[[Order], None]                               # OMS.submit
    cancel: Callable[[ClientOrderId], None]                       # OMS.cancel
    wait_until: Callable[[Callable[[], bool], float, Callable[[], None]], bool]
    latest_price: Callable[[InstrumentId], Optional[float]]       # last pushed (forming) bar close
    quote: Callable[[InstrumentId], Optional[QuoteTick]]
    instrument: Callable[[InstrumentId], Optional[Instrument]]
    local_now: Callable[[], datetime]                             # the records' wall clock
    warm_up: Callable[[BarType, int], List[Bar]]                  # MarketDataService.warm_up
    order_prefix: str = "N"
    alert: Callable[[str, str], None] = lambda subject, body: None
    market_open: Callable[[Any], bool] = lambda venue: True               # venue in session now


# ── strategy output into the ops log ──────────────────────────────────────────────────────────

class _LogWriter(io.TextIOBase):
    """What a candle strategy print()s goes to the node's log, one record per line."""

    def __init__(self, logger: logging.Logger, level: int = logging.INFO):
        super().__init__()
        self._logger = logger
        self._level = level
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                level = logging.WARNING if ("[!" in line or "REJECTED" in line) else self._level
                self._logger.log(level, line.strip())
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.log(self._level, self._buf.strip())
        self._buf = ""


# ── candle strategies ─────────────────────────────────────────────────────────────────────────

def legacy_symbol_for(iid: InstrumentId, venues: VenueRegistry) -> str:
    """AAPL.XNAS -> 'US.AAPL' (the Moomoo market code a candle strategy knows), or the instrument
    id itself for venues Moomoo does not serve (crypto)."""
    spec = venues.get(iid.venue)
    market = spec.connectors.get("moomoo", {}).get("market")
    return f"{market}.{iid.symbol}" if market else str(iid)


def build_risk_manager(settings: Optional[RiskManagerSettings], params: Mapping[str, Any]) -> Optional[RiskManager]:
    """The candle strategy's RiskManager as midfreq.live.run_live builds it from its flags."""
    if settings is None:
        return None

    def pct(v):
        return float(v) if v not in (None, 0, 0.0) else None

    if settings.sizing == "equal-dollar":
        n = settings.basket_size or params.get("top_n") or 2
        sizer = create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=int(n))
    elif settings.sizing == "fixed":
        sizer = create_position_sizer(SizingMethod.FIXED_QUANTITY, qty=int(settings.qty))
    else:
        sizer = None
    return RiskManager(stop_loss_pct=pct(settings.stop_loss_pct), trailing_stop_pct=pct(settings.trailing_stop_pct),
                       take_profit_pct=pct(settings.take_profit_pct),
                       max_drawdown_pct=float(settings.max_drawdown_pct), position_sizer=sizer)


def round_to_tick(price: float, tick: Optional[float]) -> float:
    """Round to the instrument's price increment: to decimals when it is a power of ten (0.01 ->
    round(price, 2), the original engine's rounding), else to the nearest multiple."""
    if not tick or tick <= 0:
        return round(price, 2)
    digits = -math.log10(tick)
    if abs(digits - round(digits)) < 1e-9:
        return round(price, int(round(digits)))
    steps = round(price / tick)
    return round(steps * tick, 10)


class LiveLegacyPortfolio(CorePortfolio):
    """The portfolio a candle strategy sees live: the original bookkeeping, whose trades are sent
    to the OMS by the adapter and booked only when they fill."""

    def __init__(self, adapter: "LiveLegacyAdapter", initial_cash: float, commission_rate: float,
                 capital_cap: Optional[float]):
        super().__init__(initial_cash=initial_cash, commission_rate=commission_rate)
        self._adapter = adapter
        self.capital_cap = capital_cap
        self.warming_up = False                 # read by the strategies: trading off
        self.lot_sizes: Dict[str, int] = {}
        self.price_ref: Optional[Callable[[str], Optional[float]]] = None
        self.cross_section_count = 0
        self.next_rebalance_at: Optional[int] = None
        self.resumed_cross_section_count = 0
        self.resumed_next_rebalance_at: Optional[int] = None

    def execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=None):
        if self.warming_up or qty <= 0:
            return
        self._adapter._route_trade(symbol, is_buy, qty, price, timestamp, exit_reason)

    def book(self, symbol, is_buy, qty, price, timestamp, exit_reason=None) -> bool:
        """The original Portfolio bookkeeping, unchanged; True if it booked the trade."""
        before = len(self.trade_history)
        CorePortfolio.execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=exit_reason)
        return len(self.trade_history) == before + 1


@dataclass
class _OrderMeta:
    order: Order
    symbol: str
    is_buy: bool
    decision_ts: Any
    exit_reason: Optional[str]
    filled: float = 0.0
    resolved: bool = False
    reason: Optional[str] = None


class LiveLegacyAdapter:
    """Runs one candle strategy live. Created by the node (or a replay) per strategy and session."""

    kind = "legacy"

    def __init__(self, settings: StrategySettings, services: NodeServices, *, strategy_class: type,
                 params: Mapping[str, Any], symbols: Sequence[str], instrument_ids: Mapping[str, InstrumentId],
                 bar_spec: BarSpecification, commission_rate: float, records_dir: "str | Path",
                 seed_state_dir: Optional["str | Path"] = None, risk_manager: Optional[RiskManager] = None,
                 universe: Optional[str] = None):
        self.settings = settings
        self.services = services
        self.strategy_class = strategy_class
        self.params = dict(params)
        self.symbols = list(symbols)
        self.instrument_for: Dict[str, InstrumentId] = dict(instrument_ids)
        self.symbol_for: Dict[InstrumentId, str] = {i: s for s, i in self.instrument_for.items()}
        self.bar_spec = bar_spec
        self.bar_types = {s: BarType(self.instrument_for[s], bar_spec) for s in self.symbols}
        self.commission_rate = commission_rate
        self.capital_cap = settings.capital
        self.risk_manager = risk_manager
        self.universe = universe or settings.universe
        self.id = StrategyId(settings.id)
        self.slug = settings.state_name or strategy_class.__name__.lower()
        self.records_dir = Path(records_dir)
        self.state_file = state_mod.state_path(self.records_dir, self.slug)
        self.seed_state_file = state_mod.state_path(seed_state_dir, self.slug) if seed_state_dir else None
        self.log = logging.getLogger(f"midfreq.strategy.{self.id}")
        self.order_factory = OrderFactory(self.id, services.clock.timestamp_ns, prefix=services.order_prefix)
        self.portfolio: Optional[LiveLegacyPortfolio] = None
        self.strategy = None
        self.session_log: Optional[SessionLog] = None
        self.resumed: Optional[state_mod.Resumed] = None
        self.phase = "created"                  # created -> warmup -> live -> stopping -> stopped
        self.candles_processed = 0
        self.orders_sent = 0
        self._orders: Dict[ClientOrderId, _OrderMeta] = {}
        self._booked: Set[tuple] = set()
        self._buffer: Dict[int, Dict[InstrumentId, Bar]] = {}
        self._published: Set[int] = set()       # buffered cross-sections the MarketDataService published
        self._fed_ts: Optional[int] = None      # newest ts_event fed to the strategy
        self._last_saved_xs: Optional[int] = None
        self._legacy_ts: Dict[int, pd.Timestamp] = {}
        self._subscribed = False
        self._writer = _LogWriter(self.log)
        self.on_order_sent: Optional[Callable[[Order], None]] = None

    # ── opening ──────────────────────────────────────────────────────────────────────────────
    def open(self, account: Optional[Mapping[str, Any]], broker_positions: state_mod.BrokerPositions) -> state_mod.Resumed:
        """Open the portfolio (opening cash, the saved state resumed shrink-only) and construct the
        strategy, as the original engine's constructor did."""
        initial_cash, cash = state_mod.opening_cash(account, self.capital_cap)
        p = LiveLegacyPortfolio(self, initial_cash, self.commission_rate, self.capital_cap)
        p.cash = cash
        if self.capital_cap is not None:
            self.log.info("capital capped at %s (account holds %s)", f"{self.capital_cap:,.2f}",
                          f"{(account or {}).get('total_assets', 0):,.2f}")
        source = self.state_file
        if not source.exists() and self.seed_state_file is not None and self.seed_state_file.exists():
            source = self.seed_state_file
            self.log.info("no state of its own yet: starting from %s (read only)", source)
        saved = state_mod.load_state(source)
        if saved is not None:
            self.log.info("[resume] raw state on disk: %s", saved.get("positions", {}))
        resumed = state_mod.resume(p, saved, account, broker_positions, self.capital_cap)
        if resumed.had_state:
            if broker_positions is None:
                self.log.warning("[resume] broker positions unavailable: trusting the state file")
            for sym, (was, now) in resumed.shrunk.items():
                self.log.warning("[resume] %s: state says %s but broker holds %s — resuming with %s",
                                 sym, was, now, now)
            if resumed.cross_section_count:
                self.log.info("[resume] rebalance clock at %d cross-sections", resumed.cross_section_count)
            if self.capital_cap is not None:
                self.log.info("[resume] session cash %s (cap %s)", f"{p.cash:,.2f}", f"{self.capital_cap:,.2f}")
            if p.positions:
                self.log.info("[resume] restored positions: %s", {s: q["qty"] for s, q in p.positions.items()})
        p.resumed_cross_section_count = resumed.cross_section_count
        p.resumed_next_rebalance_at = resumed.next_rebalance_at
        p.price_ref = self._price_ref
        p.lot_sizes = self._lot_sizes()
        self.portfolio = p
        self.resumed = resumed
        with self._quiet():
            self.strategy = self.strategy_class(p, risk_manager=self.risk_manager, **self.params)
        if hasattr(self.strategy, "min_cross_section"):
            self.strategy.min_cross_section = math.ceil(0.9 * len(self.symbols))
        if hasattr(self.strategy, "defer_unready_rebalance"):
            self.strategy.defer_unready_rebalance = True
        if hasattr(self.strategy, "full_cross_section"):
            self.strategy.full_cross_section = len(self.symbols)
        return resumed

    def _lot_sizes(self) -> Dict[str, int]:
        lots = {}
        for sym, iid in self.instrument_for.items():
            try:
                inst = self.services.instrument(iid)
            except Exception:
                inst = None
            if inst is not None and inst.lot_size and inst.lot_size > 1:
                lots[sym] = int(inst.lot_size)
        return lots

    def start_session(self, account: Optional[Mapping[str, Any]], timeframe_label: str) -> SessionLog:
        """Open the session log, write session_start, call the strategy's on_start."""
        self.session_log = SessionLog(self.records_dir, self.slug, now=self.services.local_now)
        self.session_log.session_start(self.strategy.__class__.__name__, self.symbols, timeframe_label,
                                       account or {})
        self.log.info("Session log: %s", self.session_log.path)
        with self._quiet():
            self.strategy.on_start()
        return self.session_log

    # ── warm-up ──────────────────────────────────────────────────────────────────────────────
    def warm_up(self, count: int) -> Tuple[int, int]:
        """Feed the last `count` stored bars of every symbol to the strategy, in timestamp order
        across the universe, with trading off; then restore the rebalance clock the last real
        session left (warm-up is replay, not market activity). Returns (symbols, cross-sections)."""
        p = self.portfolio
        p.warming_up = True
        self.phase = "warmup"
        frames: Dict[str, Dict[int, Bar]] = {}
        for sym in self.symbols:
            try:
                bars = self.services.warm_up(self.bar_types[sym], count) if count > 0 else []
            except Exception as exc:
                self.log.warning("[warmup] %s: no history (%s)", sym, exc)
                bars = []
            if not bars:
                continue
            frames[sym] = {b.ts_event: b for b in bars[-count:]}
        all_ts = sorted({ts for f in frames.values() for ts in f})
        for ts in all_ts:
            for sym, f in frames.items():
                bar = f.get(ts)
                if bar is not None:
                    self._feed_candle(sym, bar)
        if all_ts:
            self._fed_ts = all_ts[-1]
            first, last = self._legacy_timestamp(self.bar_types[self.symbols[0]], all_ts[0]), \
                self._legacy_timestamp(self.bar_types[self.symbols[0]], all_ts[-1])
            self.log.info("[warmup] %d symbols over %d cross-sections (%s -> %s)", len(frames), len(all_ts),
                          str(first)[:16], str(last)[:16])
        s = self.strategy
        if hasattr(s, "_cross_section_count"):
            resumed = p.resumed_cross_section_count
            s._cross_section_count = resumed
            n = getattr(s, "rebalance_every", 1)
            nra = p.resumed_next_rebalance_at
            if nra is None:
                nra = (resumed // n + 1) * n
            s._next_rebalance_at = nra
            self.log.info("[warmup] rebalance clock resumed at %d cross-sections, next rebalance due at %d "
                          "(rebalance_every=%d)", resumed, nra, n)
        self.log.info("[warmup] complete — %d of %d symbols from the local store", len(frames), len(self.symbols))
        # Live from here (candles are counted and logged), but trading stays off until the node
        # is running (trading_on), so nothing decided during start-up can send an order.
        self.phase = "live"
        return len(frames), len(all_ts)

    def trading_on(self) -> None:
        if self.phase == "live" and self.portfolio is not None:
            self.portfolio.warming_up = False
            self.log.info("trading enabled")

    # ── bus ──────────────────────────────────────────────────────────────────────────────────
    def subscribe(self) -> None:
        if self._subscribed:
            return
        bus = self.services.msgbus
        for iid in self.instrument_for.values():
            bus.subscribe(TOPIC_BAR.format(instrument_id=iid), self._on_bar)
        bus.subscribe(TOPIC_CROSS_SECTION.format(universe=self.universe), self._on_cross_section)
        bus.subscribe(TOPIC_ORDER_EVENTS.format(strategy_id=self.id), self._on_order_event)
        self._subscribed = True

    def unsubscribe(self) -> None:
        if not self._subscribed:
            return
        bus = self.services.msgbus
        for iid in self.instrument_for.values():
            bus.unsubscribe(TOPIC_BAR.format(instrument_id=iid), self._on_bar)
        bus.unsubscribe(TOPIC_CROSS_SECTION.format(universe=self.universe), self._on_cross_section)
        bus.unsubscribe(TOPIC_ORDER_EVENTS.format(strategy_id=self.id), self._on_order_event)
        self._subscribed = False

    def _on_bar(self, bar: Bar) -> None:
        """A closed bar: kept until its cross-section is published (or never is)."""
        iid = bar.bar_type.instrument_id
        if iid not in self.symbol_for or bar.bar_type.spec != self.bar_spec:
            return
        if self._fed_ts is not None and bar.ts_event <= self._fed_ts:
            return
        self._buffer.setdefault(bar.ts_event, {})[iid] = bar

    def _on_cross_section(self, xs: CrossSection) -> None:
        if self.phase not in ("live", "stopping"):
            return
        if self._fed_ts is not None and xs.ts_event <= self._fed_ts:
            self.log.warning("cross-section %s arrived after a newer one was fed; ignored",
                             pd.Timestamp(xs.ts_event, unit="ns", tz="UTC"))
            return
        bars = dict(self._buffer.get(xs.ts_event, {}))
        for iid, bar in xs.bars.items():
            if iid in self.symbol_for:
                bars[iid] = bar
        if self.phase == "live" and not self._market_open():
            # Final while the market is closed (the close, a lunch break): a decision now would
            # send orders to a closed market. Held until the market reopens, or fed with trading
            # off at shutdown, so a rebalance falling due on it stays due, as in the original
            # engine (which only ever finalised the closing candle at shutdown, trading off).
            self._buffer[xs.ts_event] = bars
            self._published.add(xs.ts_event)
            self.log.info("cross-section %s final while the market is closed: held",
                          self._legacy_timestamp(next(iter(bars.values())).bar_type, xs.ts_event) if bars else xs.ts_event)
            return
        self._feed_buffered(before=xs.ts_event)
        self._buffer.pop(xs.ts_event, None)
        self._feed_cross_section(xs.ts_event, bars, complete=xs.complete, published=True)

    def _market_open(self) -> bool:
        venue = next(iter(self.instrument_for.values())).venue
        try:
            return bool(self.services.market_open(venue))
        except Exception:
            return True

    def on_timer(self) -> None:
        """Release cross-sections held while the market was closed, once it is open."""
        if self.phase == "live" and self._published and self._market_open():
            self._feed_buffered()

    def _feed_buffered(self, before: Optional[int] = None) -> None:
        """Held cross-sections, and closed bars whose cross-section was never published, oldest
        first."""
        for ts in sorted(self._buffer):
            if before is not None and ts >= before:
                break
            bars = self._buffer.pop(ts)
            published = ts in self._published
            self._published.discard(ts)
            self._feed_cross_section(ts, bars, complete=len(bars) >= len(self.symbols), published=published)

    def _feed_cross_section(self, ts: int, bars: Mapping[InstrumentId, Bar], complete: bool, published: bool) -> None:
        s = self.strategy
        # A published cross-section is final: let the strategy close it on its last bar even when
        # it holds fewer than the whole universe (the MarketDataService's minimum share applied).
        override = published and hasattr(s, "full_cross_section") and 0 < len(bars) < len(self.symbols)
        saved_full = getattr(s, "full_cross_section", None)
        if override:
            s.full_cross_section = len(bars)
        try:
            for sym in self.symbols:
                bar = bars.get(self.instrument_for[sym])
                if bar is not None:
                    self._feed_candle(sym, bar)
        finally:
            if override:
                s.full_cross_section = saved_full
        if self._fed_ts is None or ts > self._fed_ts:
            self._fed_ts = ts
        for old in [t for t in self._buffer if t <= ts]:
            self._buffer.pop(old, None)
            self._published.discard(old)

    def _legacy_timestamp(self, bar_type: BarType, ts_event: int) -> pd.Timestamp:
        ts = self._legacy_ts.get(ts_event)
        if ts is None:
            spec = self.services.venues.get(bar_type.instrument_id.venue)
            ts = self._legacy_ts[ts_event] = spec.to_legacy_index([ts_event], bar_type.spec)[0]
        return ts

    def _feed_candle(self, symbol: str, bar: Bar) -> None:
        ts = self._legacy_timestamp(bar.bar_type, bar.ts_event)
        candle = Candle(timestamp=ts, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                        volume=bar.volume)
        with log_context(strategy=str(self.id), cross_section=str(ts)[:16]), self._quiet():
            self.strategy.on_data(symbol, candle)
        if self.phase in ("live", "stopping"):
            self._after_live_candle(symbol, candle)

    def _after_live_candle(self, symbol: str, candle: Candle) -> None:
        """What the original engine did after each closed candle: count it, save the rebalance
        clock the moment it advances, log the candle with the strategy's equity."""
        self.candles_processed += 1
        xs = getattr(self.strategy, "_cross_section_count", None)
        if xs is not None and xs != self._last_saved_xs:
            try:
                self.save_state()
                self._last_saved_xs = xs
            except Exception as exc:
                self.log.error("periodic state save failed: %s", exc)
        prices = self._prices()
        prices[symbol] = candle.close
        equity = self.portfolio.get_current_equity(prices)
        if self.session_log is not None:
            self.session_log.candle_close(symbol, candle.timestamp, candle.close, equity)

    def _prices(self) -> Dict[str, float]:
        out = {}
        for sym, iid in self.instrument_for.items():
            px = self.services.latest_price(iid)
            if px is not None:
                out[sym] = px
        return out

    # ── execute_trade -> an order ─────────────────────────────────────────────────────────────
    def _price_ref(self, symbol: str) -> Optional[float]:
        iid = self.instrument_for.get(symbol)
        return self.services.latest_price(iid) if iid is not None else None

    def _limit_price(self, iid: InstrumentId, is_buy: bool, ref: float) -> Tuple[float, float]:
        """(limit price, worst-case cost per share before commission) under the pricing rule."""
        rule, slip = self.settings.pricing.rule, self.settings.pricing.slip
        base = ref
        if rule in ("quote", "mid"):
            q = self.services.quote(iid)
            if q is not None and q.bid > 0 and q.ask > 0:
                base = (q.ask if is_buy else q.bid) if rule == "quote" else q.mid
        raw = base * (1 + slip if is_buy else 1 - slip)
        inst = None
        try:
            inst = self.services.instrument(iid)
        except Exception:
            pass
        limit = round_to_tick(raw, inst.price_increment if inst is not None else None)
        return limit, raw

    def _route_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason) -> None:
        p = self.portfolio
        side = "BUY" if is_buy else "SELL"
        iid = self.instrument_for.get(symbol)
        if iid is None:
            self.log.error("strategy traded %s, which is not in its universe; ignored", symbol)
            return
        lot = p.lot_sizes.get(symbol)
        if is_buy and lot and lot > 1:
            rounded = int(qty // lot) * lot
            if rounded != qty:
                if rounded <= 0:
                    self.log.info("[%s] SKIPPED BUY %s %s: below board lot of %s", timestamp, qty, symbol, lot)
                    return
                self.log.info("[%s] Lot-adjusted BUY %s: %s -> %s (lot %s)", timestamp, symbol, qty, rounded, lot)
                qty = rounded
        try:
            ref = p.price_ref(symbol) if p.price_ref is not None else None
        except Exception:
            ref = None
        ref = ref or price
        limit, worst_px = self._limit_price(iid, is_buy, ref)
        if is_buy:
            # Size against the worst-case fill (the limit, plus commission), so a fill can never be
            # refused by the strategy's own cash check and leave it and the broker disagreeing.
            worst = worst_px * (1 + p.commission_rate)
            affordable = int(p.cash // worst) if worst > 0 else 0
            if lot and lot > 1:
                affordable = (affordable // lot) * lot
            if affordable < qty:
                self.log.info("[%s] BUY %s trimmed %s -> %s to fit cash at the limit", timestamp, symbol, qty, affordable)
                qty = affordable
            if qty <= 0:
                return
        else:
            qty = min(qty, p.get_position_qty(symbol))
            if qty <= 0:
                return
        tags = {"decision_ts": str(timestamp), "legacy_symbol": symbol}
        if exit_reason:
            tags["exit_reason"] = exit_reason
        order = self.order_factory.limit(iid, OrderSide.BUY if is_buy else OrderSide.SELL, qty, limit,
                                         TimeInForce(self.settings.pricing.time_in_force), tags)
        order.reference_price = float(price)
        meta = _OrderMeta(order, symbol, is_buy, timestamp, exit_reason)
        self._orders[order.client_order_id] = meta
        self.orders_sent += 1
        with log_context(client_order_id=str(order.client_order_id), strategy=str(self.id)):
            self.log.info("[%s] %s %g %s limit %s (live %s, decided at %s)", timestamp, side, qty, symbol,
                          limit, ref, price)
            if self.on_order_sent is not None:
                self.on_order_sent(order)
            self.services.submit(order)
            if not self.settings.confirm_fills:
                return
            done = self.services.wait_until(lambda: meta.resolved, self.settings.confirm_timeout_s,
                                            lambda: self._give_up(meta))
            if not done:
                self.log.warning("[%s] %s %s %s still open after %.0fs; its fills will be booked when they arrive",
                                 timestamp, side, qty, symbol, self.settings.confirm_timeout_s)
            if meta.filled <= 0 and meta.resolved:
                self.log.warning("[%s] NOT FILLED %s %s %s (limit %s): %s — nothing booked",
                                 timestamp, side, qty, symbol, limit, meta.reason or "no fill")
            elif 0 < meta.filled < qty and meta.resolved:
                self.log.warning("[%s] PARTIAL %s: %g of %g filled, remainder cancelled", timestamp, symbol,
                                 meta.filled, qty)

    def _give_up(self, meta: _OrderMeta) -> None:
        """The confirm wait ran out: ask the OMS to cancel what is left."""
        if not meta.resolved:
            self.log.warning("order %s not resolved in time: cancelling the remainder", meta.order.client_order_id)
            try:
                self.services.cancel(meta.order.client_order_id)
            except Exception as exc:
                self.log.error("cancel of %s failed: %s", meta.order.client_order_id, exc)

    # ── fills -> the strategy's book ─────────────────────────────────────────────────────────
    def _on_order_event(self, event: OrderEvent) -> None:
        meta = self._orders.get(event.client_order_id)
        if isinstance(event, OrderFilled):
            self._book_fill(event, meta)
        elif isinstance(event, _TERMINAL):
            if meta is not None:
                meta.resolved = True
                meta.reason = getattr(event, "reason", "") or type(event).__name__
            level = logging.WARNING if isinstance(event, (OrderRejected, OrderDenied)) else logging.INFO
            with log_context(client_order_id=str(event.client_order_id), strategy=str(self.id)):
                self.log.log(level, "%s %s: %s", type(event).__name__, event.client_order_id,
                             getattr(event, "reason", ""))
        elif isinstance(event, OrderAccepted):
            pass

    def _book_fill(self, fill: OrderFilled, meta: Optional[_OrderMeta]) -> None:
        key = (str(fill.client_order_id), str(fill.trade_id) if fill.trade_id is not None
               else f"{fill.ts_event}:{fill.last_qty}:{fill.last_px}")
        if key in self._booked:
            return
        self._booked.add(key)
        symbol = meta.symbol if meta is not None else self.symbol_for.get(fill.instrument_id)
        if symbol is None:
            self.log.error("fill for %s, which is not in this strategy's universe: not booked", fill.instrument_id)
            return
        is_buy = fill.side is OrderSide.BUY
        decision_ts = meta.decision_ts if meta is not None else fill.tags.get("decision_ts", "")
        exit_reason = meta.exit_reason if meta is not None else fill.tags.get("exit_reason")
        qty, px = fill.last_qty, fill.last_px
        with log_context(client_order_id=str(fill.client_order_id), strategy=str(self.id)):
            ok = self.portfolio.book(symbol, is_buy, qty, px, decision_ts, exit_reason)
            if not ok:
                # The original bookkeeping leaves an empty entry behind a refused sell; drop it so
                # the state file never records a zero position.
                pos = self.portfolio.positions.get(symbol)
                if pos is not None and pos.get("qty", 0) == 0:
                    del self.portfolio.positions[symbol]
                msg = (f"filled {'BUY' if is_buy else 'SELL'} {qty:g} {symbol} @ {px} could not be booked "
                       f"(the strategy's cash or position refused it): the bot and the broker now disagree")
                self.log.error(msg)
                self.services.alert(f"{self.id}: fill not booked", msg)
            if meta is not None:
                meta.filled += qty
                if meta.filled >= meta.order.quantity - 1e-12:
                    meta.resolved = True
            order_id = fill.tags.get("venue_order_id") or (meta.order.tags.get("venue_order_id") if meta else None) \
                or str(fill.client_order_id)
            self.log.info("[%s] %s %g %s @ %s (order %s%s)", decision_ts, "BUY" if is_buy else "SELL", qty, symbol,
                          px, order_id, f", {exit_reason}" if exit_reason else "")
            if ok and self.session_log is not None:
                self.session_log.trade(decision_ts, symbol, "BUY" if is_buy else "SELL", qty, px, order_id, exit_reason)
            try:
                self.save_state()
            except Exception as exc:
                self.log.error("state save after a fill failed: %s", exc)

    # ── stopping and records ────────────────────────────────────────────────────────────────
    def trading_off(self) -> None:
        if self.portfolio is not None:
            self.portfolio.warming_up = True
        if self.phase == "live":
            self.phase = "stopping"

    def finalize(self) -> None:
        """Feed every closed bar still waiting for a cross-section (trading is already off)."""
        self._feed_buffered()

    def save_state(self) -> Dict[str, Any]:
        p, s = self.portfolio, self.strategy
        if hasattr(s, "_cross_section_count"):
            p.cross_section_count = s._cross_section_count
            p.next_rebalance_at = getattr(s, "_next_rebalance_at", None)
        return state_mod.save_state(self.state_file, p, getattr(p, "cross_section_count", 0),
                                    getattr(p, "next_rebalance_at", None), self.capital_cap,
                                    self.services.local_now())

    @property
    def cross_section_count(self) -> Optional[int]:
        return getattr(self.strategy, "_cross_section_count", None)

    def reconcile(self, account: Optional[Mapping[str, Any]], broker_positions: state_mod.BrokerPositions) -> bool:
        """The periodic shrink-only check against the broker (the original's sync_with_broker).
        A symbol with an order still open is left alone: the broker may already show the fill
        the strategy has not booked yet, and shrinking the claim then would refuse that fill."""
        def shrunk(sym, local, broker):
            self.log.warning("[sync] %s: claimed %s but broker holds %s — shrinking claim", sym, local, broker)
        in_flight = {m.symbol for m in self._orders.values() if not m.resolved}
        if broker_positions is not None and in_flight:
            view = {s: dict(p) for s, p in broker_positions.items()}
            for sym in in_flight:
                local = self.portfolio.get_position_qty(sym)
                view[sym] = {**view.get(sym, {}), "qty": max(local, view.get(sym, {}).get("qty", 0))}
            self.log.info("[sync] %s: order in flight, not reconciled this time", ", ".join(sorted(in_flight)))
            broker_positions = view
        ok = state_mod.shrink_to_broker(self.portfolio, account, broker_positions, self.capital_cap, shrunk)
        if not ok:
            self.log.warning("[sync] position query failed — skipping reconciliation")
        return ok

    def end_session(self, account: Optional[Mapping[str, Any]], broker_positions: Optional[Mapping[str, Any]]) -> None:
        if self.session_log is not None:
            self.session_log.session_end(self.candles_processed, len(self.portfolio.trade_history),
                                         account or {}, broker_positions or {})
        self.phase = "stopped"

    def open_orders(self) -> List[Order]:
        return [m.order for m in self._orders.values() if not m.resolved]

    def positions(self) -> Dict[str, float]:
        return {s: p["qty"] for s, p in (self.portfolio.positions if self.portfolio else {}).items()}

    @contextlib.contextmanager
    def _quiet(self):
        """A candle strategy's print()s go to the log, not the console."""
        with contextlib.redirect_stdout(self._writer):
            try:
                yield
            finally:
                self._writer.flush()


# ── event-driven strategies ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiveTimeSlice:
    """Every bar of one cross-section, for Strategy.on_slice (the live counterpart of
    midfreq.backtest.data_engine.TimeSlice)."""
    ts_event: int
    bars: Tuple[Bar, ...]
    data: Tuple[object, ...] = ()
    complete: bool = True

    def __iter__(self):
        return iter(self.bars)

    def __len__(self) -> int:
        return len(self.bars)

    def get(self, instrument_id: InstrumentId) -> Optional[Bar]:
        for bar in self.bars:
            if bar.bar_type.instrument_id == instrument_id:
                return bar
        return None

    @property
    def instrument_ids(self) -> List[InstrumentId]:
        return [b.bar_type.instrument_id for b in self.bars]


class _NativeExecution:
    """What a native Strategy calls submit/cancel on: the OMS, while trading is on."""

    def __init__(self, runner: "NativeStrategyRunner"):
        self.runner = runner

    def submit(self, order: Order) -> None:
        r = self.runner
        if not r.trading:
            event = OrderDenied(order.client_order_id, order.strategy_id, order.instrument_id,
                                r.services.clock.timestamp_ns(), reason="trading is off (warm-up or shutdown)")
            r.services.msgbus.publish(TOPIC_ORDER_EVENTS.format(strategy_id=order.strategy_id), event)
            return
        r.orders_sent += 1
        r.services.submit(order)

    def cancel(self, order: Order) -> None:
        self.runner.services.cancel(order.client_order_id)


class NativeStrategyRunner:
    """Runs one midfreq.strategies.trading.Strategy: registered with the node's clock, bus and
    cache, a portfolio of its own fed by its fills, and the OMS as its execution."""

    kind = "native"

    def __init__(self, strategy: Any, settings: StrategySettings, services: NodeServices,
                 instrument_ids: Sequence[InstrumentId], bar_spec: BarSpecification, currency: str):
        self.strategy = strategy
        self.settings = settings
        self.services = services
        self.instrument_ids = list(instrument_ids)
        self.bar_spec = bar_spec
        self.currency = currency
        self.id = strategy.id
        self.portfolio = AccountingPortfolio(services.cache)
        if settings.capital:
            self.portfolio.fund(currency, settings.capital)
        self.trading = False
        self.orders_sent = 0
        self.phase = "created"
        self.log = logging.getLogger(f"midfreq.strategy.{self.id}")

    def register(self) -> None:
        bus = self.services.msgbus
        # The portfolio first, so the strategy sees its fill already applied.
        bus.subscribe(TOPIC_ORDER_EVENTS.format(strategy_id=self.id), self._on_order_event)
        self.strategy.register(clock=self.services.clock, msgbus=bus, cache=self.services.cache,
                               portfolio=self.portfolio, execution=_NativeExecution(self))

    def _on_order_event(self, event: OrderEvent) -> None:
        if isinstance(event, OrderFilled):
            fill = event
            if not fill.currency:
                import dataclasses
                fill = dataclasses.replace(fill, currency=self.currency)
            self.portfolio.apply_fill(fill)

    def start(self) -> None:
        self.strategy.on_start()

    def warm_up(self, count: int) -> int:
        """Publish the stored bars on the bar-type topics the strategy subscribed to, with
        trading off."""
        self.phase = "warmup"
        fed = 0
        bars: List[Bar] = []
        for iid in self.instrument_ids:
            try:
                bars += self.services.warm_up(BarType(iid, self.bar_spec), count) if count > 0 else []
            except Exception as exc:
                self.log.warning("[warmup] %s: no history (%s)", iid, exc)
        for bar in sorted(bars, key=lambda b: b.ts_event):
            self.services.cache.add_bar(bar)
            self.services.msgbus.publish(f"data.bars.{bar.bar_type}", bar)
            fed += 1
        self.phase = "live"
        return fed

    def trading_on(self) -> None:
        self.trading = True

    def trading_off(self) -> None:
        self.trading = False
        self.phase = "stopping"

    def stop(self) -> None:
        try:
            self.strategy.on_stop()
        finally:
            self.phase = "stopped"


class StrategyHost:
    """Every strategy of the node, and the bridges from the MarketDataService's topics to the
    topics the Strategy API subscribes to (bars by bar type, slices)."""

    def __init__(self, services: NodeServices):
        self.services = services
        self.legacy: List[LiveLegacyAdapter] = []
        self.native: List[NativeStrategyRunner] = []
        self._bridged_bars: Set[InstrumentId] = set()
        self._bridged_universes: Set[str] = set()

    @property
    def all(self) -> List[Any]:
        return [*self.legacy, *self.native]

    def add_legacy(self, adapter: LiveLegacyAdapter) -> LiveLegacyAdapter:
        self.legacy.append(adapter)
        return adapter

    def add_native(self, runner: NativeStrategyRunner, universe: Optional[str] = None) -> NativeStrategyRunner:
        self.native.append(runner)
        bus = self.services.msgbus
        for iid in runner.instrument_ids:
            if iid not in self._bridged_bars:
                bus.subscribe(TOPIC_BAR.format(instrument_id=iid), self._bridge_bar)
                self._bridged_bars.add(iid)
        if universe and universe not in self._bridged_universes:
            bus.subscribe(TOPIC_CROSS_SECTION.format(universe=universe), self._bridge_slice)
            self._bridged_universes.add(universe)
        runner.register()
        return runner

    def _bridge_bar(self, bar: Bar) -> None:
        self.services.msgbus.publish(f"data.bars.{bar.bar_type}", bar)

    def _bridge_slice(self, xs: CrossSection) -> None:
        bars = tuple(sorted(xs.bars.values(), key=lambda b: str(b.bar_type.instrument_id)))
        self.services.msgbus.publish("data.slices", LiveTimeSlice(xs.ts_event, bars, bars, xs.complete))

    def subscribe(self) -> None:
        for a in self.legacy:
            a.subscribe()

    def trading_on(self) -> None:
        for a in self.legacy:
            a.trading_on()
        for r in self.native:
            r.trading_on()

    def trading_off(self) -> None:
        for a in self.legacy:
            a.trading_off()
        for r in self.native:
            r.trading_off()

    @property
    def trading(self) -> bool:
        return (any(a.phase == "live" and a.portfolio is not None and not a.portfolio.warming_up for a in self.legacy)
                or any(r.trading for r in self.native))

    def finalize(self) -> None:
        for a in self.legacy:
            a.finalize()

    def on_timer(self) -> None:
        for a in self.legacy:
            a.on_timer()

    def save_states(self) -> Dict[str, Optional[int]]:
        """Save every candle strategy's state; returns its rebalance clock per strategy."""
        out = {}
        for a in self.legacy:
            try:
                a.save_state()
            except Exception as exc:
                a.log.error("state save failed: %s", exc)
            out[str(a.id)] = a.cross_section_count
        return out

    def clocks(self) -> Dict[str, Optional[int]]:
        return {str(a.id): a.cross_section_count for a in self.legacy}

    def stop_native(self) -> None:
        for r in self.native:
            try:
                r.stop()
            except Exception as exc:
                r.log.error("on_stop failed: %s", exc)

    def positions(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for a in self.legacy:
            for sym, qty in a.positions().items():
                out[f"{a.id}:{sym}"] = qty
        for r in self.native:
            for pos in r.portfolio.positions.values():
                out[f"{r.id}:{pos.instrument_id}"] = pos.quantity
        return out
