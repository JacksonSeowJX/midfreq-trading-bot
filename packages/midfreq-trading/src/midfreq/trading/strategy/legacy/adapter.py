"""
`LiveLegacyAdapter`: runs one candle strategy live, created by the node (or a replay) per
strategy and session. Here: opening the portfolio and constructing the strategy, warm-up, the
bus (bars and cross-sections fed to the strategy in its own convention), and stopping and the
records. Its orders are in orders.py and its fills in fills.py, as mixins.
"""
from __future__ import annotations

import contextlib
import io
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd

from midfreq.core.candle import Candle
from midfreq.core.model.data import Bar, BarSpecification, BarType
from midfreq.core.model.identifiers import ClientOrderId, InstrumentId, StrategyId
from midfreq.core.model.orders import Order, OrderFactory
from midfreq.core.risk_manager import RiskManager
from midfreq.trading.persistence import state as state_mod
from midfreq.trading.config.settings import StrategySettings, instrument_for_symbol
from midfreq.trading.core.interfaces import (
    TOPIC_BAR, TOPIC_CROSS_SECTION, TOPIC_ORDER_EVENTS, CrossSection,
)
from midfreq.trading.ops.logs import log_context
from midfreq.trading.persistence.session_log import SessionLog
from midfreq.trading.strategy.legacy.fills import FillBookingMixin

# A broker's position list can lag its fills: the Hong Kong paper account still listed 0 shares a
# second after a 400-share fill had been reported (23 Oct 2026). A symbol filled this recently is
# not shrunk to the broker's view; a real difference is still taken once this has passed.
FILL_SETTLE_S = 600.0
from midfreq.trading.strategy.legacy.orders import OrderRoutingMixin, _OrderMeta
from midfreq.trading.strategy.legacy.portfolio import LiveLegacyPortfolio
from midfreq.trading.strategy.services import NodeServices


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


class LiveLegacyAdapter(OrderRoutingMixin, FillBookingMixin):
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
        self._orders: Dict[ClientOrderId, _OrderMeta] = {}    # by every id its orders had or have
        self._by_object: Dict[int, _OrderMeta] = {}             # id(order) -> its meta
        self._recovered: List[Dict[str, Any]] = []              # fills booked at resume (trade lines)
        self._rebalance_marked: Optional[int] = None            # cross-section whose rebalance is saved as done
        self._booked: Set[tuple] = set()
        self._filled_at: Dict[str, int] = {}                    # symbol -> clock time of its last booked fill
        self.fill_settle_s = FILL_SETTLE_S
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
            self._check_state_market(source, saved)
        else:
            # No state yet: every fill the journal has for this strategy is one its book lacks.
            saved = {"saved_at": "1970-01-01 00:00:00", "positions": {}, "peak_prices": {},
                     "cross_section_count": 0, "next_rebalance_at": None,
                     "cash": cash if self.capital_cap is not None else None, "_empty": True}
        saved = self._replay_unbooked_fills(saved, opening_cash=cash)
        if saved.get("_empty"):
            saved = None
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

    def _check_state_market(self, source: Path, saved: Mapping[str, Any]) -> None:
        """A state whose positions are not on the venues this strategy trades is another
        strategy's (a Hong Kong test pointed at the US forward test's file): refuse to start
        rather than take it over, or overwrite it."""
        mine = {iid.venue for iid in self.instrument_for.values()}
        foreign = []
        for sym in saved.get("positions", {}) or {}:
            try:
                venue = instrument_for_symbol(sym, self.services.venues).venue
            except (ValueError, KeyError):
                venue = None
            if venue not in mine:
                foreign.append(sym)
        if foreign:
            raise state_mod.StateFileError(
                f"{source} holds positions in {sorted(foreign)}, not on {sorted(map(str, mine))} where {self.id} "
                f"trades: it is another strategy's state. Refusing to start; give this strategy its own state_name.")

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
        for t in self._recovered:                                # fills booked from the journal at resume
            self.session_log.trade(t["timestamp"], t["symbol"], t["side"], t["qty"], t["price"], t["order_id"],
                                   t["exit_reason"])
        if self._recovered:
            self.save_state()
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
        for bt in self.bar_types.values():
            bus.subscribe(TOPIC_BAR.format(bar_type=bt), self._on_bar)
        bus.subscribe(TOPIC_CROSS_SECTION.format(universe=self.universe), self._on_cross_section)
        bus.subscribe(TOPIC_ORDER_EVENTS.format(strategy_id=self.id), self._on_order_event)
        self._subscribed = True

    def unsubscribe(self) -> None:
        if not self._subscribed:
            return
        bus = self.services.msgbus
        for bt in self.bar_types.values():
            bus.unsubscribe(TOPIC_BAR.format(bar_type=bt), self._on_bar)
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
            if self._rebalance_marked is not None and self._rebalance_marked == s._cross_section_count:
                p.next_rebalance_at = self._rebalance_due_after() or p.next_rebalance_at
        return state_mod.save_state(self.state_file, p, getattr(p, "cross_section_count", 0),
                                    getattr(p, "next_rebalance_at", None), self.capital_cap,
                                    self.services.local_now())

    @property
    def cross_section_count(self) -> Optional[int]:
        return getattr(self.strategy, "_cross_section_count", None)

    def reconcile(self, account: Optional[Mapping[str, Any]], broker_positions: state_mod.BrokerPositions) -> bool:
        """The periodic shrink-only check against the broker (the original's sync_with_broker).
        A symbol with an order still open is left alone: the broker may already show the fill
        the strategy has not booked yet, and shrinking the claim then would refuse that fill.
        A symbol filled within `fill_settle_s` is left alone too: the broker's position list may
        not show the fill yet, and shrinking the claim then would erase a fill that happened."""
        def shrunk(sym, local, broker):
            self.log.warning("[sync] %s: claimed %s but broker holds %s — shrinking claim", sym, local, broker)
        in_flight = {m.symbol for m in self._by_object.values() if not self._resolved(m)}
        now = self.services.clock.timestamp_ns()
        settling = {s for s, t in self._filled_at.items() if now - t < self.fill_settle_s * 1e9} - in_flight
        if broker_positions is not None and (in_flight or settling):
            view = {s: dict(p) for s, p in broker_positions.items()}
            for sym in in_flight | settling:
                local = self.portfolio.get_position_qty(sym)
                view[sym] = {**view.get(sym, {}), "qty": max(local, view.get(sym, {}).get("qty", 0))}
            if in_flight:
                self.log.info("[sync] %s: order in flight, not reconciled this time", ", ".join(sorted(in_flight)))
            lagging = [s for s in sorted(settling)
                       if self.portfolio.get_position_qty(s) > (broker_positions.get(s) or {}).get("qty", 0)]
            if lagging:
                self.log.info("[sync] %s: filled less than %.0f s ago and the broker does not show it yet; not "
                              "reconciled this time", ", ".join(lagging), self.fill_settle_s)
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
        return [m.order for m in self._by_object.values() if not self._resolved(m)]

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
