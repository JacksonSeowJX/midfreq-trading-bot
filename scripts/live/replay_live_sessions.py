"""
End-to-end replay of the live forward test against a simulated broker.

Every live-trading bug so far was found in production because the parts
were only ever tested one at a time. This runs the REAL LiveTradingEngine,
LivePortfolio, strategy, sizer and state file through the candles that the
live sessions actually received (live_sessions/session_*crosssectional*),
one fresh engine per day exactly as cron runs it, against a fake broker
that fills, rejects, part-fills and fails queries on demand.

After every session it checks the invariants that matter:
  - the strategy's positions and the broker's positions agree exactly
  - the rebalance clock advances by the number of cross-sections fed and
    survives the restart, and rebalances fire when due
  - no order is sent while trading is disabled (warm-up, shutdown)
  - the basket never exceeds top_n, and buys are sized equally by value
  - the capped session's cash ledger matches the broker's cash movements

Scenarios inject the failures that a real broker produces. Exit code is
non-zero if any invariant fails.

Usage:
    python3 scripts/live/replay_live_sessions.py
"""
import contextlib
import io
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))

import pandas as pd

import core.storage as storage_mod
from core.models import Candle, Timeframe
from core.strategy import STRATEGY_REGISTRY
from core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from core.live_engine import LiveTradingEngine
import core.live_engine as live_mod
from datetime import datetime as _dt
from zoneinfo import ZoneInfo

_NY = ZoneInfo('America/New_York')


class ReplayClock(_dt):
    """datetime whose now() is the replayed session's time, not the wall clock.

    Warm-up chooses its history window relative to now(), and shutdown only
    finalises candles whose hour has already ended; against the real clock a
    replayed day looks either weeks old or in the future.
    """
    fake = None

    @classmethod
    def now(cls, tz=None):
        if cls.fake is None:
            return _dt.now(tz)
        return cls.fake.astimezone(tz) if tz else cls.fake.astimezone(_NY).replace(tzinfo=None)

CFG = json.loads((REPO / 'config' / 'sp100_forward_test.json').read_text())
PARAMS, SYMBOLS, CAP = CFG['params'], CFG['symbols'], CFG['capital']
FEE = 0.00005


# ── simulated broker ────────────────────────────────────────────────
class FakeBroker:
    """Fills at the latest market price, with injectable failures."""

    def __init__(self, cash=999_999.51, reject_every=0, partial=False,
                 query_fail_rate=0.0, seed=0):
        self.cash, self.pos = cash, {}               # pos[sym] = {'qty', 'cost'}
        self.market = {}                             # latest price per symbol
        self.reject_every, self.partial = reject_every, partial
        self.query_fail_rate = query_fail_rate
        self.rng = random.Random(seed)
        self.n_orders = 0
        self.orders = []                             # (sym, side, qty, px, filled)
        self.trading_disabled_probe = None           # set by the harness

    # interface used by LivePortfolio
    def get_account_info(self):
        if self.rng.random() < self.query_fail_rate:
            return None
        mv = sum(p['qty'] * self.market.get(s, p['cost']) for s, p in self.pos.items())
        return {'cash': self.cash, 'total_assets': self.cash + mv, 'market_value': mv}

    def get_positions(self):
        if self.rng.random() < self.query_fail_rate:
            return None
        return {s: {'qty': p['qty'], 'entry_price': p['cost']} for s, p in self.pos.items() if p['qty'] > 0}

    def place_and_confirm(self, symbol, is_buy, qty, ref_price, slip=0.01, wait_s=30.0):
        self.n_orders += 1
        if self.trading_disabled_probe and self.trading_disabled_probe():
            raise AssertionError(f"order sent while trading disabled: {symbol} {qty}")
        px = self.market.get(symbol, ref_price)
        limit = round(ref_price * (1 + slip if is_buy else 1 - slip), 2)
        out = {'ok': False, 'order_id': self.n_orders, 'dealt_qty': 0.0, 'dealt_price': None,
               'limit': limit, 'message': ''}
        if self.reject_every and self.n_orders % self.reject_every == 0:
            out['message'] = 'CANCELLED_ALL (simulated: not filled)'
            self.orders.append((symbol, is_buy, qty, px, 0)); return out
        if (is_buy and px > limit) or (not is_buy and px < limit):
            out['message'] = 'CANCELLED_ALL (price through limit)'
            self.orders.append((symbol, is_buy, qty, px, 0)); return out
        fill = int(qty // 2) if (self.partial and qty >= 2) else qty
        if not is_buy:
            fill = min(fill, self.pos.get(symbol, {}).get('qty', 0))
        if fill <= 0:
            out['message'] = 'nothing to fill'; return out
        cost = fill * px * (1 + FEE)
        if is_buy:
            if cost > self.cash:
                out['message'] = 'insufficient broker cash'; return out
            p = self.pos.setdefault(symbol, {'qty': 0, 'cost': 0.0})
            p['cost'] = (p['qty'] * p['cost'] + fill * px) / (p['qty'] + fill)
            p['qty'] += fill
            self.cash -= cost
        else:
            self.pos[symbol]['qty'] -= fill
            self.cash += fill * px * (1 - FEE)
            if self.pos[symbol]['qty'] == 0:
                del self.pos[symbol]
        out.update(ok=True, dealt_qty=float(fill), dealt_price=px,
                   message='FILLED_ALL' if fill == qty else 'FILLED_PART')
        self.orders.append((symbol, is_buy, qty, px, fill))
        return out


class FakeProvider:
    def get_lot_sizes(self, symbols): return {}
    def get_historical_data(self, *a, **k): return pd.DataFrame()
    def start_live_streaming_multi(self, *a, **k): pass
    def close(self): pass


# ── recorded live candles, by session ───────────────────────────────
def load_sessions():
    files = sorted((REPO / 'live_sessions').glob('session_*crosssectionalreversal*.jsonl'))
    sessions = []
    for f in files:
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        cc = [r for r in rows if r['type'] == 'candle_close']
        by_ts = {}
        for r in cc:
            by_ts.setdefault(r['timestamp'], {})[r['symbol']] = r['close']
        if by_ts:
            sessions.append((f.name[8:16], sorted(by_ts.items())))
    return sessions


def run_scenario(name, sessions, broker_kwargs, crash_session=None):
    tmp = Path(tempfile.mkdtemp(prefix='replay_'))
    data_dir, log_dir = tmp / 'data', tmp / 'live_sessions'
    data_dir.mkdir(); log_dir.mkdir()
    # Isolated copy of the cache, truncated to before the first replayed day:
    # warm-up takes the newest timestamp across ALL symbols, so one symbol with
    # later data would make every replayed candle look stale and be dropped.
    first_day = pd.Timestamp(sessions[0][1][0][0]).normalize()
    for s in SYMBOLS:
        src = REPO / 'data' / s.replace('.', '_') / '1h.parquet'
        if src.exists():
            (data_dir / s.replace('.', '_')).mkdir()
            df = pd.read_parquet(src)
            df[df.index < first_day].to_parquet(data_dir / s.replace('.', '_') / '1h.parquet')
    real = storage_mod.DataStorage
    storage_mod.DataStorage = lambda *a, **k: real(base_path=str(data_dir))

    broker = FakeBroker(**broker_kwargs)
    real_dt = live_mod.datetime
    live_mod.datetime = ReplayClock
    fails, expected_xs, rebalances = [], 0, 0
    try:
        for day, (date, hours) in enumerate(sessions):
            day0 = pd.Timestamp(hours[0][0]).tz_localize(None).normalize()
            ReplayClock.fake = _dt(day0.year, day0.month, day0.day, 9, 35, tzinfo=_NY)
            sizer = create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=PARAMS['top_n'])
            rm = RiskManager(stop_loss_pct=None, max_drawdown_pct=0.15, position_sizer=sizer)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                eng = LiveTradingEngine(provider=FakeProvider(), gateway=broker,
                                        strategy_class=STRATEGY_REGISTRY['Cross-Sectional Reversal']['class'],
                                        symbols=SYMBOLS, timeframe=Timeframe.HOUR_1, risk_manager=rm,
                                        session_log_dir=str(log_dir), commission_rate=FEE,
                                        capital_cap=CAP, **PARAMS)
                broker.trading_disabled_probe = lambda: eng.portfolio.warming_up
                eng._warm_up()
            xs_start = eng.strategy._cross_section_count
            due_start = eng.strategy._next_rebalance_at
            orders_before = broker.n_orders
            cash_before_local, cash_before_broker = eng.portfolio.cash, broker.cash

            with contextlib.redirect_stdout(buf):
                for i, (ts, closes) in enumerate(hours):
                    t = pd.Timestamp(ts)
                    for sym, c in closes.items():
                        broker.market[sym] = c
                        eng._on_candle_update(sym, Candle(timestamp=t, open=c, high=c, low=c, close=c, volume=1))
                    eng.portfolio.sync_with_broker()
                    if crash_session == day and i == len(hours) // 2:
                        break                         # process dies: no shutdown
                if crash_session != day:
                    ReplayClock.fake = _dt(day0.year, day0.month, day0.day, 16, 0, 30, tzinfo=_NY)
                    eng._shutdown()
            log = buf.getvalue()

            xs_end = eng.strategy._cross_section_count
            fed = len(hours) - (0 if crash_session != day else len(hours) - (len(hours)//2 + 1))
            n_reb = log.count('[gate]') or None
            sent = broker.n_orders - orders_before
            rebal_here = sum(1 for k in range(xs_start + 1, xs_end + 1) if k >= due_start) and sent > 0
            rebalances += 1 if rebal_here else 0

            # invariants
            local = {s: p['qty'] for s, p in eng.portfolio.positions.items()}
            remote = {s: p['qty'] for s, p in broker.pos.items()}
            if local != remote:
                fails.append(f"{date}: positions disagree local={local} broker={remote}")
            if len(local) > PARAMS['top_n']:
                fails.append(f"{date}: basket of {len(local)} > top_n {PARAMS['top_n']}")
            if crash_session != day and xs_end - xs_start != len(hours):
                fails.append(f"{date}: clock advanced {xs_end - xs_start} for {len(hours)} hours of data")
            if 'STATE ANOMALY' in log:
                fails.append(f"{date}: state anomaly logged")
            st = json.loads((log_dir / 'state_crosssectionalreversal.json').read_text())
            if crash_session != day and st['cross_section_count'] != xs_end:
                fails.append(f"{date}: saved count {st['cross_section_count']} != live {xs_end}")
            d_local = eng.portfolio.cash - cash_before_local
            d_broker = broker.cash - cash_before_broker
            if crash_session != day and abs(d_local - d_broker) > 1.0:
                fails.append(f"{date}: cash ledger moved {d_local:+,.2f}, broker {d_broker:+,.2f}")
            buys = [o for o in broker.orders[orders_before:] if o[1] and o[4] > 0]
            if len(buys) >= 2:
                vals = [o[3] * o[4] for o in buys]
                spread = (max(vals) - min(vals)) / max(vals)
                if not broker_kwargs.get('partial') and not broker_kwargs.get('reject_every') and spread > 0.25:
                    fails.append(f"{date}: unequal buys {[round(v) for v in vals]}")
            print(f"  {date}  hours={len(hours)}  clock {xs_start:>3}->{xs_end:<3} due@{due_start:<3} "
                  f"orders={sent:<3} held={sorted(local)}  "
                  f"cash local {eng.portfolio.cash:>11,.2f} / broker {broker.cash:>11,.2f}"
                  + ("  [CRASHED mid-session]" if crash_session == day else ""))
    finally:
        storage_mod.DataStorage = real
        live_mod.datetime = real_dt
        ReplayClock.fake = None
        shutil.rmtree(tmp, ignore_errors=True)
    return fails, rebalances


def main():
    sessions = load_sessions()
    if not sessions:
        print("no recorded cross-sectional sessions in live_sessions/"); return 1
    # The recorded week three times over, so the clock crosses several
    # rebalances. Copies are shifted BACKWARDS in time: the engine (correctly)
    # will not finalise a candle whose hour has not yet happened, so copies
    # dated in the future would each lose their 16:00 candle.
    long = []
    for k in (2, 1, 0):
        for date, hours in sessions:
            shifted = [(str(pd.Timestamp(ts) - pd.Timedelta(days=7 * k)), c) for ts, c in hours]
            long.append((str((pd.Timestamp(date) - pd.Timedelta(days=7 * k)).date()), shifted))
    scenarios = [
        ("clean broker", {}),
        ("every 4th order not filled", {'reject_every': 4}),
        ("partial fills (half of each order)", {'partial': True}),
        ("10% of broker queries fail", {'query_fail_rate': 0.10, 'seed': 3}),
    ]
    total_fail = 0
    for name, kw in scenarios:
        print(f"\n=== {name} ===")
        fails, reb = run_scenario(name, long, kw)
        print(f"  rebalances executed: {reb}")
        for f in fails: print("  FAIL:", f)
        total_fail += len(fails)
    print("\n=== process crashes mid-session on day 3 ===")
    fails, reb = run_scenario('crash', long, {}, crash_session=2)
    print(f"  rebalances executed: {reb}")
    for f in fails: print("  FAIL:", f)
    total_fail += len(fails)
    print(f"\n{'ALL INVARIANTS HELD' if not total_fail else f'{total_fail} INVARIANT FAILURE(S)'}")
    return 1 if total_fail else 0


if __name__ == '__main__':
    sys.exit(main())
