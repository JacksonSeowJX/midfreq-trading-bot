"""
Replay recorded live sessions through the trading node, in sim mode with a test clock.

The node counterpart of scripts/live/replay_live_sessions.py, which drives the original engine:
the candles the live sessions received (`candle_close` events of `live_sessions/session_*.jsonl`)
are pushed to a fresh node per recorded day, as cron starts one, against `ReplayBroker`, a
simulated broker with the harness's FakeBroker rules (fills at the latest market price unless the
limit is crossed; on demand: every Nth order unfilled, half fills, failing account queries). The
node's own MarketDataService finalises bars from the pushes, its strategies decide, its OMS sends
the orders, and the state file and session logs are written as live.

Pushes follow the harness: each recorded hour is pushed once per symbol at the start of its
window (09:35 for the first), so pushing hour i completes hour i-1; the session ends at 16:02 New
York time, two minutes after the close, as the launcher stops it.

After every session the harness's invariants are checked: the strategy's positions equal the
broker's; no basket larger than top_n; every order decided on the cross-section just completed;
the rebalance clock advanced by the cross-sections fed and saved; the capped cash ledger moved as
the broker's cash did; no order sent while trading was off.

    midfreq-node replay --config config/node_sp100.yaml --data-dir <a checkout's data/>

`compare_decisions` puts two runs' trades side by side (the same baskets at the same decision
candles), which is how the node is checked against the original engine (the tests drive both).
"""
from __future__ import annotations

import copy
import json
import logging
import random
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from midfreq.core.common.clock import TestClock
from midfreq.core.model.data import Bar, BarType
from midfreq.core.model.enums import OrderSide
from midfreq.core.model.events import OrderAccepted, OrderCanceled, OrderCancelRejected, OrderFilled, OrderRejected
from midfreq.core.model.identifiers import AccountId, InstrumentId, TradeId
from midfreq.core.model.orders import Order
from midfreq.core.venues.spec import VenueRegistry, default_venues
from midfreq.data.adapters.base import (
    AccountSnapshot, AccountUnavailableError, BarUpdate, DataClient, ExecutionClient, FillReport, OrderReport,
    PositionReport,
)
from midfreq.trading.config import NodeConfig, StrategySettings, UniverseConfig, instrument_for_symbol
from midfreq.trading.ops import get_logger
from midfreq.trading.session_log import read_events
from midfreq.trading.state import load_state, state_path

log = get_logger("replay")

NANOS = 1_000_000_000


# ── the recordings ────────────────────────────────────────────────────────────────────────────

@dataclass
class RecordedSession:
    path: Optional[Path]
    date: str                                       # YYYYmmdd, from the file name (or shifted)
    strategy: str
    symbols: List[str]
    timeframe: str
    hours: List[Tuple[str, Dict[str, float]]]       # (candle timestamp, {symbol: close}), oldest first
    trades: List[Dict[str, Any]] = field(default_factory=list)
    start: Dict[str, Any] = field(default_factory=dict)
    end: Optional[Dict[str, Any]] = None


def load_recorded_sessions(directory: "str | Path", strategy_slug: str = "crosssectionalreversal",
                           market: Optional[str] = None) -> List[RecordedSession]:
    """Every recorded session of one strategy, oldest first, with its candles grouped by
    timestamp in the order they were recorded."""
    out = []
    for f in sorted(Path(directory).glob(f"session_*{strategy_slug}*.jsonl")):
        events = read_events(f)
        start = next((e for e in events if e.get("type") == "session_start"), {})
        symbols = list(start.get("symbols") or [])
        if market and (not symbols or symbols[0].split(".")[0].upper() != market.upper()):
            continue
        by_ts: Dict[str, Dict[str, float]] = {}
        for e in events:
            if e.get("type") == "candle_close":
                by_ts.setdefault(e["timestamp"], {})[e["symbol"]] = float(e["close"])
        if not by_ts:
            continue
        out.append(RecordedSession(
            path=f, date=f.name[8:16], strategy=str(start.get("strategy", "")), symbols=symbols,
            timeframe=str(start.get("timeframe", "1h")), hours=sorted(by_ts.items()),
            trades=[e for e in events if e.get("type") == "trade"], start=start,
            end=next((e for e in events if e.get("type") == "session_end"), None)))
    return out


def repeat_backwards(sessions: Sequence[RecordedSession], copies: int = 3) -> List[RecordedSession]:
    """The recordings `copies` times over, earlier copies shifted back by a whole number of weeks
    covering everything recorded (the harness's construction), so the rebalance clock crosses
    several rebalances. Recorded trades are not carried into the shifted copies."""
    if not sessions:
        return []
    first, last = pd.Timestamp(sessions[0].date), pd.Timestamp(sessions[-1].date)
    period = 7 * ((last - first).days // 7 + 1)
    out = []
    for k in range(copies - 1, -1, -1):
        shift = pd.Timedelta(days=period * k)
        for s in sessions:
            hours = [(str(pd.Timestamp(ts) - shift), dict(c)) for ts, c in s.hours]
            out.append(RecordedSession(None if k else s.path, (pd.Timestamp(s.date) - shift).strftime("%Y%m%d"),
                                       s.strategy, list(s.symbols), s.timeframe, hours,
                                       [] if k else list(s.trades), dict(s.start), s.end if not k else None))
    return out


def decisions(trades: Iterable[Mapping[str, Any]], with_qty: bool = False) -> Dict[str, Dict[str, list]]:
    """Trades grouped by decision candle: {timestamp: {'SELL': [...], 'BUY': [...]}}, each list
    sorted (symbols, or (symbol, qty) with `with_qty`)."""
    out: Dict[str, Dict[str, list]] = {}
    for t in trades:
        key = str(pd.Timestamp(t["timestamp"]))
        side = out.setdefault(key, {"SELL": [], "BUY": []})
        side[t["side"]].append((t["symbol"], float(t["qty"])) if with_qty else t["symbol"])
    for v in out.values():
        v["SELL"].sort()
        v["BUY"].sort()
    return dict(sorted(out.items()))


def compare_decisions(expected: Iterable[Mapping[str, Any]], actual: Iterable[Mapping[str, Any]],
                      with_qty: bool = False) -> List[str]:
    """The differences between two runs' decisions, as sentences; [] when they agree."""
    a, b = decisions(expected, with_qty), decisions(actual, with_qty)
    diffs = []
    for ts in sorted(set(a) | set(b)):
        if a.get(ts) != b.get(ts):
            diffs.append(f"{ts[:16]}: expected {a.get(ts)} got {b.get(ts)}")
    return diffs


# ── the simulated broker and data client ──────────────────────────────────────────────────────

class ReplayBroker(ExecutionClient):
    """The replay harness's FakeBroker as an ExecutionClient: fills a marketable limit in full at
    the latest market price, and answers the account queries an AccountClient would. Events are
    emitted synchronously from submit_order, through the handler (the node's queue)."""

    def __init__(self, cash: float = 999_999.51, reject_every: int = 0, partial: bool = False,
                 query_fail_rate: float = 0.0, seed: int = 0, fee: float = 0.00005,
                 symbol_of: Optional[Callable[[InstrumentId], str]] = None, currency: str = "USD",
                 now_ns: Callable[[], int] = lambda: 0):
        super().__init__(SimpleNamespace(name="replay"), {})
        self.cash, self.pos = cash, {}                  # pos[symbol] = {'qty', 'cost'}
        self.market: Dict[str, float] = {}              # latest price per symbol
        self.reject_every, self.partial = reject_every, partial
        self.query_fail_rate = query_fail_rate
        self.rng = random.Random(seed)
        self.fee = fee
        self.currency = currency
        self.symbol_of = symbol_of or (lambda iid: str(iid))
        self.now_ns = now_ns
        self.n_orders = 0
        self.orders: List[tuple] = []                   # (symbol, is_buy, qty, px, filled)
        self.trading_disabled_probe: Optional[Callable[[], bool]] = None
        self.violations: List[str] = []
        self._fills: List[FillReport] = []
        self._ids: Dict[Any, Order] = {}

    @property
    def environment(self) -> str:
        return "sim"

    @property
    def account_id(self) -> AccountId:
        return AccountId("REPLAY")

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def _event(self, cls, order: Order, **kw):
        self._emit(cls(order.client_order_id, order.strategy_id, order.instrument_id, self.now_ns(), **kw))

    def submit_order(self, order: Order) -> None:
        self.n_orders += 1
        n = self.n_orders
        voi = str(n)
        order.tags["venue_order_id"] = voi
        self._ids[voi] = order
        symbol = self.symbol_of(order.instrument_id)
        is_buy = order.side is OrderSide.BUY
        qty = order.quantity
        if self.trading_disabled_probe is not None and self.trading_disabled_probe():
            self.violations.append(f"order sent while trading disabled: {symbol} {qty}")
        px = self.market.get(symbol, order.reference_price or order.price)
        limit = order.price if order.price is not None else px
        self._event(OrderAccepted, order)
        if self.reject_every and n % self.reject_every == 0:
            self.orders.append((symbol, is_buy, qty, px, 0))
            self._event(OrderCanceled, order, reason="CANCELLED_ALL (simulated: not filled)")
            return
        if (is_buy and px > limit) or (not is_buy and px < limit):
            self.orders.append((symbol, is_buy, qty, px, 0))
            self._event(OrderCanceled, order, reason="CANCELLED_ALL (price through limit)")
            return
        fill = int(qty // 2) if (self.partial and qty >= 2) else qty
        if not is_buy:
            fill = min(fill, self.pos.get(symbol, {}).get("qty", 0))
        if fill <= 0:
            self._event(OrderRejected, order, reason="nothing to fill")
            return
        cost = fill * px * (1 + self.fee)
        if is_buy:
            if cost > self.cash:
                self._event(OrderRejected, order, reason="insufficient broker cash")
                return
            p = self.pos.setdefault(symbol, {"qty": 0, "cost": 0.0, "iid": order.instrument_id})
            p["cost"] = (p["qty"] * p["cost"] + fill * px) / (p["qty"] + fill)
            p["qty"] += fill
            self.cash -= cost
        else:
            self.pos[symbol]["qty"] -= fill
            self.cash += fill * px * (1 - self.fee)
            if self.pos[symbol]["qty"] == 0:
                del self.pos[symbol]
        self.orders.append((symbol, is_buy, qty, px, fill))
        trade_id = f"T{n}"
        self._fills.append(FillReport(trade_id, voi, symbol, "BUY" if is_buy else "SELL", float(fill), px,
                                      pd.Timestamp(self.now_ns(), unit="ns", tz="UTC"), fill * px * self.fee,
                                      self.currency, str(order.client_order_id), str(order.instrument_id)))
        self._event(OrderFilled, order, trade_id=TradeId(trade_id), side=order.side, last_qty=float(fill), last_px=px,
                    commission=fill * px * self.fee, currency=self.currency, tags={"venue_order_id": voi})
        if fill < qty:
            self._event(OrderCanceled, order, reason="FILLED_PART: remainder cancelled")

    def cancel_order(self, order: Order) -> None:
        # Every order is final when submit returns, so a cancel always comes too late.
        self._event(OrderCancelRejected, order, reason="already final")

    def query_order(self, order: Order) -> Optional[OrderReport]:
        return None

    def open_orders(self) -> List[OrderReport]:
        return []

    def fills(self, since: pd.Timestamp) -> List[FillReport]:
        since = pd.Timestamp(since)
        since = since.tz_localize("UTC") if since.tzinfo is None else since
        return [f for f in self._fills if f.ts >= since]

    # the AccountClient side
    def snapshot(self) -> AccountSnapshot:
        if self.rng.random() < self.query_fail_rate:
            raise AccountUnavailableError("simulated query failure")
        mv = sum(p["qty"] * self.market.get(s, p["cost"]) for s, p in self.pos.items())
        return AccountSnapshot(self.currency, self.cash + mv, self.cash, mv,
                               pd.Timestamp(self.now_ns(), unit="ns", tz="UTC"))

    def positions(self) -> List[PositionReport]:
        if self.rng.random() < self.query_fail_rate:
            raise AccountUnavailableError("simulated query failure")
        out = []
        for s, p in self.pos.items():
            if p["qty"] > 0:
                px = self.market.get(s, p["cost"])
                out.append(PositionReport(s, float(p["qty"]), p["cost"], px, p["qty"] * px, None,
                                          str(p["iid"]) if p.get("iid") is not None else None))
        return out

    def close(self) -> None:
        pass

    def held(self) -> Dict[str, float]:
        return {s: p["qty"] for s, p in self.pos.items()}


class ReplayDataClient(DataClient):
    """A data client whose stream is the replay itself: subscriptions are recorded, and the
    replay puts the pushes on the node's queue."""

    def __init__(self):
        super().__init__(SimpleNamespace(name="replay"))
        self.subscriptions: List[Any] = []
        self.quote_subscriptions: List[Any] = []

    def subscribe_bars(self, bar_type, handler) -> None:
        self.subscriptions.append(bar_type)

    def unsubscribe_bars(self, bar_type) -> None:
        if bar_type in self.subscriptions:
            self.subscriptions.remove(bar_type)

    def subscribe_quotes(self, instrument_id, handler) -> None:
        self.quote_subscriptions.append(instrument_id)

    def unsubscribe_quotes(self, instrument_id) -> None:
        if instrument_id in self.quote_subscriptions:
            self.quote_subscriptions.remove(instrument_id)

    def _request_bars(self, bar_type, venue, start, end) -> List[Bar]:
        return []


# ── the local store ───────────────────────────────────────────────────────────────────────────

def prepare_store(source: "str | Path", dest: "str | Path", symbols: Iterable[str], timeframe: str,
                  before: pd.Timestamp) -> int:
    """Copy each symbol's cached bars (data/<MKT>_<CODE>/<tf>.parquet) from before the first
    replayed day into `dest`; only reads `source`. Returns how many symbols were copied."""
    source, dest = Path(source), Path(dest)
    n = 0
    for sym in symbols:
        folder = sym.replace(".", "_", 1)
        src = source / folder / f"{timeframe}.parquet"
        if not src.exists():
            continue
        df = pd.read_parquet(src)
        cutoff = before if before.tzinfo is not None or df.index.tz is None else before.tz_localize(df.index.tz)
        (dest / folder).mkdir(parents=True, exist_ok=True)
        df[df.index < cutoff].to_parquet(dest / folder / f"{timeframe}.parquet")
        n += 1
    return n


# ── running a replay ──────────────────────────────────────────────────────────────────────────

@dataclass
class SessionResult:
    date: str
    hours: int
    xs_start: Optional[int]
    xs_end: Optional[int]
    due_start: Optional[int]
    orders: int
    trades: List[Dict[str, Any]]
    held: Dict[str, float]
    broker_held: Dict[str, float]
    cash_local: float
    cash_broker: float
    crashed: bool
    fails: List[str]
    session_log: Optional[Path]

    def line(self) -> str:
        return (f"  {self.date}  hours={self.hours}  clock {self.xs_start!s:>3}->{self.xs_end!s:<3} "
                f"due@{self.due_start!s:<3} orders={self.orders:<3} held={sorted(self.held)}  cash local "
                f"{self.cash_local:>11,.2f} / broker {self.cash_broker:>11,.2f}"
                + ("  [CRASHED mid-session]" if self.crashed else ""))


@dataclass
class ReplayReport:
    sessions: List[SessionResult] = field(default_factory=list)
    rebalances: int = 0

    @property
    def fails(self) -> List[str]:
        return [f for s in self.sessions for f in s.fails]

    @property
    def trades(self) -> List[Dict[str, Any]]:
        return [t for s in self.sessions for t in s.trades]

    @property
    def ok(self) -> bool:
        return not self.fails


class _ErrorCollector(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


def replay_config(config: NodeConfig, strategy: StrategySettings, symbols: Sequence[str],
                  unfilled_order: Any = "original_engine") -> NodeConfig:
    """The configuration a replay runs: this one strategy, on the recorded universe, in sim mode,
    with the original engine's unfilled-order behaviour (wait 30 s, no re-price, cancel) unless
    `unfilled_order` says otherwise (None: the configuration's own policy), so the replay
    reproduces what the original engine decided."""
    cfg = copy.deepcopy(config)
    s = copy.deepcopy(strategy)
    universe = f"{s.universe or s.id}-replay"
    cfg.universes = {universe: UniverseConfig(universe, list(symbols))}
    s.universe = universe
    cfg.strategies = [s]
    cfg.accounts = {s.account: cfg.accounts[s.account]} if s.account in cfg.accounts else cfg.accounts
    cfg.node.mode = "sim"
    cfg.data.persist_bars = True
    if unfilled_order is not None:
        cfg.oms = {**cfg.oms, "unfilled_order": unfilled_order}
    return cfg


def replay_sessions(config: NodeConfig, sessions: Sequence[RecordedSession], *, records_dir: "str | Path",
                    store_dir: "str | Path", strategy_id: Optional[str] = None, broker: Optional[ReplayBroker] = None,
                    broker_kwargs: Optional[Mapping[str, Any]] = None, components: Optional[Mapping[str, Any]] = None,
                    crash_session: Optional[int] = None, venues: Optional[VenueRegistry] = None,
                    start_time: Tuple[int, int] = (9, 35), stop_time: Tuple[int, int] = (16, 2),
                    check: bool = True, print_lines: bool = False,
                    unfilled_order: Any = "original_engine") -> ReplayReport:
    """Replay `sessions` through a fresh TradingNode per session (sim mode, TestClock), against
    one ReplayBroker kept across sessions. `components` are passed to the node (the OMS, risk,
    positions, market data and reference data to use; the defaults are the real ones)."""
    from midfreq.trading.node import TradingNode     # here: node imports this module's neighbours

    venues = venues or default_venues()
    strategy = config.strategy(strategy_id) if strategy_id else config.strategies[0]
    symbols = sessions[0].symbols if sessions and sessions[0].symbols else config.universes[strategy.universe].symbols
    cfg = replay_config(config, strategy, symbols, unfilled_order)
    s = cfg.strategies[0]
    iids = {sym: instrument_for_symbol(sym, venues) for sym in symbols}
    sym_of = {iid: sym for sym, iid in iids.items()}
    venue_spec = venues.get(next(iter(iids.values())).venue)
    spec = cfg.bar_spec(s)
    top_n = int(s.params.get("top_n", 0) or 0)
    records_dir, store_dir = Path(records_dir), Path(store_dir)
    records_dir.mkdir(parents=True, exist_ok=True)
    store_dir.mkdir(parents=True, exist_ok=True)
    clock_holder: Dict[str, Any] = {}
    if broker is None:
        fee = s.commission_rate if s.commission_rate is not None else float(venue_spec.fees.get("rate", 0.0))
        broker = ReplayBroker(**{"fee": fee, **dict(broker_kwargs or {})})
    broker.symbol_of = lambda iid: sym_of.get(iid, str(iid))
    broker.now_ns = lambda: clock_holder["clock"].timestamp_ns() if "clock" in clock_holder else 0
    utc_cache: Dict[str, int] = {}

    def utc_ns(ts: str) -> int:
        v = utc_cache.get(ts)
        if v is None:
            v = utc_cache[ts] = int(venue_spec.localize(pd.DatetimeIndex([pd.Timestamp(ts)]))[0].value)
        return v

    def local_ns(day: pd.Timestamp, hm: Tuple[int, int]) -> int:
        local = pd.Timestamp(day.year, day.month, day.day, hm[0], hm[1]).tz_localize(venue_spec.timezone)
        return int(local.tz_convert("UTC").value)

    report = ReplayReport()
    collector = _ErrorCollector()
    logging.getLogger("midfreq").addHandler(collector)
    carried = 0
    try:
        for day, sess in enumerate(sessions):
            hours = sess.hours
            day0 = pd.Timestamp(hours[0][0]).tz_localize(None).normalize()
            start_ns = local_ns(day0, start_time)
            clock = TestClock(start_ns)
            clock_holder["clock"] = clock
            comps = {**dict(components or {}), f"execution_client:{s.account}": broker,
                     "data_client": ReplayDataClient()}
            node = TradingNode(cfg, mode="sim", clock=clock, components=comps, records_dir=records_dir,
                               store_dir=store_dir, records_tz=venue_spec.timezone, venues=venues)
            errors_before = len(collector.records)
            broker.trading_disabled_probe = lambda: not node.trading_enabled
            node.start()
            adapter = node.host.legacy[0]
            xs_start = adapter.cross_section_count
            due_start = getattr(adapter.strategy, "_next_rebalance_at", None)
            orders_before = broker.n_orders
            cash_local0, cash_broker0 = adapter.portfolio.cash, broker.cash
            feeding: Dict[str, Optional[pd.Timestamp]] = {"done": None}
            late: List[tuple] = []

            def spy(order: Order, feeding=feeding, late=late):
                decided = pd.Timestamp(order.tags.get("decision_ts"))
                if feeding["done"] is None or decided != feeding["done"]:
                    late.append((order.tags.get("legacy_symbol"), str(decided)[:16], str(feeding["done"])[:16]))
            adapter.on_order_sent = spy

            interval = spec.nanos
            prev_push = start_ns - NANOS
            crashed = False
            for i, (ts, closes) in enumerate(hours):
                t = utc_ns(ts)
                push = max(start_ns, prev_push + NANOS, t - interval + NANOS)
                prev_push = push
                feeding["done"] = pd.Timestamp(hours[i - 1][0]) if i else None
                for sym, c in closes.items():
                    broker.market[sym] = c
                for sym, c in closes.items():
                    iid = iids.get(sym)
                    if iid is None:
                        continue
                    bar = Bar(BarType(iid, spec), c, c, c, c, 1.0, t, push)
                    node.queue.put(BarUpdate(bar, False, push))
                node.advance_to(push)
                if crash_session == day and i == len(hours) // 2:
                    crashed = True
                    break
            feeding["done"] = None
            if crashed:
                node.abandon()
            else:
                node.advance_to(max(local_ns(day0, stop_time), node.now_ns()))
                node.stop("replay: end of the recorded session")
            xs_end = adapter.cross_section_count
            session_log = adapter.session_log.path if adapter.session_log is not None else None
            trades = [e for e in read_events(session_log) if e.get("type") == "trade"] if session_log else []
            for tr in trades:
                tr["session"] = sess.date
            sent = broker.n_orders - orders_before
            held = adapter.positions()
            fails: List[str] = []
            if check:
                date = sess.date
                if held != broker.held():
                    fails.append(f"{date}: positions disagree local={held} broker={broker.held()}")
                if top_n and len(held) > top_n:
                    fails.append(f"{date}: basket of {len(held)} > top_n {top_n}")
                if late:
                    sym, decided, done = late[0]
                    fails.append(f"{date}: {len(late)} order(s) not on the cross-section just completed, "
                                 f"e.g. {sym} decided on {decided} when {done} had completed")
                incomplete_last = len(hours[-1][1]) < len(symbols)
                if not crashed and xs_start is not None and xs_end is not None:
                    expected = len(hours) + carried - (1 if incomplete_last else 0)
                    if xs_end - xs_start != expected:
                        fails.append(f"{date}: clock advanced {xs_end - xs_start}, expected {expected} "
                                     f"({len(hours)} hours of data, {carried} carried in, "
                                     f"{int(incomplete_last)} left incomplete)")
                    carried = 1 if incomplete_last else 0
                else:
                    carried = 0
                errors = collector.records[errors_before:]
                anomalies = [e for e in errors if "STATE ANOMALY" in e]
                if anomalies:
                    fails.append(f"{date}: state anomaly logged: {anomalies[0][:120]}")
                st = load_state(state_path(records_dir, adapter.slug))
                if not crashed and st is not None and st.get("cross_section_count") != xs_end:
                    fails.append(f"{date}: saved count {st.get('cross_section_count')} != live {xs_end}")
                d_local = adapter.portfolio.cash - cash_local0
                d_broker = broker.cash - cash_broker0
                if not crashed and abs(d_local - d_broker) > 1.0:
                    fails.append(f"{date}: cash ledger moved {d_local:+,.2f}, broker {d_broker:+,.2f}")
                if broker.violations:
                    fails.append(f"{date}: {broker.violations[0]}")
                    broker.violations.clear()
                if node.errors:
                    fails.append(f"{date}: {node.errors} dispatch error(s), e.g. {errors[:1]}")
            if xs_start is not None and xs_end is not None and due_start is not None:
                if any(k >= due_start for k in range(xs_start + 1, xs_end + 1)) and sent > 0:
                    report.rebalances += 1
            result = SessionResult(sess.date, len(hours), xs_start, xs_end, due_start, sent, trades, held,
                                   broker.held(), adapter.portfolio.cash, broker.cash, crashed, fails, session_log)
            report.sessions.append(result)
            if print_lines:
                print(result.line())
    finally:
        logging.getLogger("midfreq").removeHandler(collector)
    return report


# ── the command line ──────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    """`midfreq-node replay`: replay the recorded sessions of one strategy and check the harness's
    invariants; with --compare-recorded, list where the node's decisions differ from the trades
    the recordings hold."""
    import argparse
    from midfreq.core.paths import data_dir, live_sessions_dir
    from midfreq.trading.config import load_config

    ap = argparse.ArgumentParser(prog="midfreq-node replay", description=main.__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--strategy", default=None, help="strategy id (default: the first)")
    ap.add_argument("--sessions", default=None, help="folder of recorded session logs (default: live_sessions/)")
    ap.add_argument("--slug", default=None, help="session file name part (default: the strategy class, lower case)")
    ap.add_argument("--data-dir", default=None, help="bar cache to warm up from, read only (default: data/)")
    ap.add_argument("--out", default=None, help="where the replay writes its records (default: a temporary folder)")
    ap.add_argument("--repeat", type=int, default=1, help="replay the recordings this many times over (the harness uses 3)")
    ap.add_argument("--reject-every", type=int, default=0)
    ap.add_argument("--partial", action="store_true")
    ap.add_argument("--query-fail-rate", type=float, default=0.0)
    ap.add_argument("--crash-session", type=int, default=None)
    ap.add_argument("--compare-recorded", action="store_true")
    ap.add_argument("--unfilled-order", default="original_engine",
                    help="the OMS's unfilled-order policy: original_engine (default, reproduces the original "
                         "engine) or configured (the configuration's own)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config, mode="sim")
    s = cfg.strategy(args.strategy) if args.strategy else cfg.strategies[0]
    from midfreq.trading.config import resolve_strategy_class
    slug = args.slug or s.state_name or resolve_strategy_class(s.cls)[0].__name__.lower()
    sessions = load_recorded_sessions(args.sessions or live_sessions_dir(), slug)
    if not sessions:
        print(f"no recorded sessions of {slug} in {args.sessions or live_sessions_dir()}")
        return 1
    if args.repeat > 1:
        sessions = repeat_backwards(sessions, args.repeat)
    tmp = Path(tempfile.mkdtemp(prefix="node_replay_"))
    out = Path(args.out) if args.out else tmp / "live_sessions"
    store = tmp / "data"
    first_day = pd.Timestamp(sessions[0].hours[0][0]).normalize()
    tf = s.timeframe or cfg.node.timeframe
    copied = prepare_store(args.data_dir or data_dir(), store, sessions[0].symbols, tf, first_day)
    if not copied:
        print(f"[!] no bar cache under {args.data_dir or data_dir()}; warm-up will be empty")
    kwargs = {"reject_every": args.reject_every, "partial": args.partial, "query_fail_rate": args.query_fail_rate}
    try:
        policy = None if args.unfilled_order == "configured" else args.unfilled_order
        report = replay_sessions(cfg, sessions, records_dir=out, store_dir=store, strategy_id=s.id,
                                 broker_kwargs=kwargs, crash_session=args.crash_session, print_lines=True,
                                 unfilled_order=policy)
    finally:
        if not args.out:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"  rebalances executed: {report.rebalances}")
    for f in report.fails:
        print("  FAIL:", f)
    if args.compare_recorded:
        recorded = [t for sess in sessions for t in sess.trades]
        diffs = compare_decisions(recorded, report.trades)
        print(f"\ndecisions against the recorded trades: {'the same' if not diffs else f'{len(diffs)} differ'}")
        for d in diffs:
            print("  ", d)
    print(f"\n{'ALL INVARIANTS HELD' if report.ok else f'{len(report.fails)} INVARIANT FAILURE(S)'}")
    return 0 if report.ok else 1
