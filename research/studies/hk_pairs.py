"""
Hong Kong pairs / statistical arbitrage within sector groups (pre-registered)
=============================================================================

Written and saved on 18 Oct 2026 before any of its numbers were computed. The
candidates, grids, universe, pair-selection rule and acceptance rule below are
fixed; nothing is added or tuned after seeing results. Anything tried later goes
in a separate, labelled exploratory section and is never called a pass.

Question. Same-sector Hong Kong stocks co-move. Does a spread trade between two
of them, chosen and tuned on training data only, make money out of sample after
Hong Kong's 0.16%/side all-in cost, under the project's dual-configuration
walk-forward standard?

Earlier pairs work (pairs_research.py, Jul 2026) tested 5 pairs from the 19-name
HK live list over 1 year, chose the pairs on the full sample (look-ahead), traded
a flat 100 shares, and none generalised. This study fixes all three.

Universe: 8 economic groups, fixed here from what the companies do, not from any
result. Every name is in data/HK_<code>/1h.parquet (all share the same 4,381
hourly bars, 2023-09-04 -> 2026-08-31).
  banks        HK.00005 HSBC, HK.00939 CCB, HK.01398 ICBC, HK.03988 Bank of China,
               HK.03968 China Merchants Bank, HK.02388 BOC Hong Kong     (15 pairs)
  insurers     HK.01299 AIA, HK.02318 Ping An, HK.02628 China Life       (3 pairs)
  telecoms     HK.00941 China Mobile, HK.00762 China Unicom              (1 pair)
  oil          HK.00857 PetroChina, HK.00883 CNOOC, HK.00386 Sinopec     (3 pairs)
  hk_property  HK.00016 SHKP, HK.00012 Henderson Land, HK.01113 CK Asset,
               HK.00101 Hang Lung Properties                             (6 pairs)
  cn_property  HK.00688 China Overseas Land, HK.01109 China Resources Land,
               HK.00960 Longfor                                          (3 pairs)
  utilities    HK.00002 CLP, HK.00003 HK & China Gas, HK.00006 Power Assets,
               HK.01038 CK Infrastructure                                (6 pairs)
  casinos      HK.00027 Galaxy Entertainment, HK.01928 Sands China       (1 pair)
  38 possible pairs in all. HK.00001 (CK Hutchison) is left out of property: since
  the 2015 reorganisation it is a ports/retail/telecoms/infrastructure
  conglomerate and CK Asset (01113) holds the property. HK.01109 (CR Land) is a
  mainland developer, so it sits with the mainland developers, not the HK
  landlords.

Pair selection, per group, per window, on the TRAINING segment only: the pair
whose unit log-ratio spread log(A/B) has the lowest ADF p-value (statsmodels
adfuller, autolag='AIC') on the training segment's hourly closes, the
decision-relevant test pairs_research.py uses, since that is the spread the
strategy trades. Ties go to the higher correlation of hourly log returns.
Test-segment data is never used for selection.

Variants (3), each sized so the position is a share of EQUITY, not a share count
(a flat 100 shares makes the return track the share price, the defect the
2026-09-08 audit found; HK.01398 at HK$5 would be 0.5% of the account):
  V1 registry    STRATEGY_REGISTRY['Pairs Trading'] (PairsTradingStrategy),
                 unchanged. It is long/flat on leg A only: buy A when
                 z < -entry_z, sell when z > -exit_z. It never shorts and never
                 buys B. Which leg is A is chosen on training as part of the
                 grid (2 orientations). Sized by a plain RiskManager with
                 EqualDollarSizer(n_positions=1): all equity in A while held; no
                 stops, drawdown breaker off (max_drawdown_pct=1.0). Runs on the
                 C++ BatchRunner.
  V2 long_cheap  buy the cheap leg only, either side: A when z < -entry_z, B when
                 z > +entry_z; sell A when z > -exit_z, sell B when z < +exit_z.
                 Same z-score as V1 (rolling mean and population std of
                 log(A/B) over `lookback` bars, evaluated on B's bar), same
                 sizing. Symmetric, so orientation does not matter. Long only:
                 tradable by the project's node as it stands.
  V3 long_short  the textbook market-neutral trade: z < -entry_z -> long A,
                 short B; z > +entry_z -> short A, long B; close both when z
                 crosses back inside -/+exit_z. 50% of equity per leg (gross
                 100%, the same gross exposure and the same commission per round
                 trip as V1/V2). Short leg pays the same 0.16%/side (HK stamp
                 duty applies to short sales) plus an ASSUMED borrow fee of 5%
                 a year on the short notional, charged on cover. Not tradable as
                 the project stands: the trading node's PreTradeRisk refuses
                 short sales and the backtest exchange does too, and HK short
                 selling needs a borrow, a designated security and the tick
                 rule. Reported as the reference for "does the spread itself
                 pay", never as a deployable result.

Grid (pre-registered, inside the registry's Pairs Trading ranges):
  lookback {20, 40, 60} x entry_z {1.5, 2.0, 2.5} x exit_z {0.0, 0.5} = 18
  V1 adds orientation {A=first, A=second}: 36.
  Lookback stops at 60, not 100: every test segment starts cold (the
  backtester's convention), and a Config B test segment holds only ~90 hourly
  bars, so a 100-bar lookback could never warm up and would score a forced 0.0%.

Candidates: 3 variants x 8 groups = 24, plus one pooled book per variant (all 8
groups traded at once, 1/8 of capital each; window return = mean of the 8 group
returns) = 27 candidates. Configurations searched: (36 + 18 + 18) x 8 groups =
576 grid points. The pair choice inside a group is a training-only statistical
rule, not a search on returns.

Validation (the project's standard, reused): 3 years of 1h data ending at
DataStorage.latest_common_timestamp of the 27 names; Config A 9 windows (~121
days), Config B 15 windows (~73 days), 70% train; per window grid-search the
training segment by Sharpe (combinations that traded rank above any that did not;
train end-exclusive, so no boundary day leaks), trade the winner unchanged on the
test segment. Window bounds and the stub-window skip rule are walk_forward's own.
Costs: commission 0.0016/side (Portfolio.HK_FEE_RATE, calibrated on live HK
fills) plus 5 bps slippage per side (SLIPPAGE_BPS = 5.0, the convention in every
earlier HK study; the measured HK half-spread median is 3.33 bps). Capital
HK$100,000. Board lots are not modelled (the backtester's convention).

Acceptance rule: a candidate PASSES only if, under BOTH configurations, its
mean out-of-sample return per window is > 0 AND at least 50% of its windows are
> 0, at 0.0016/side.

Also reported, none of them part of the pass decision:
  - 0.0008/side: the whole study re-run at half the commission (sensitivity).
  - Alpha view, as alpha_vs_market.py: the equal-weight buy-and-hold of the pair
    traded in each test window (the backtester's own benchmark), per-window
    excess, and the pooled daily regression r = alpha + beta * r_bench with
    Newey-West (5 lags) t-statistics.
  - Turnover: fills and round trips per test window.
  - White's Reality Check (midfreq.research.reality_check), over all 27
    candidates' per-window returns, under each configuration, IF any passes.
  - Live settings for any candidate that passes, by pick_sp100_live_config.py's
    procedure: select the pair and grid-search on the most recent 84 days,
    report the winner.

Compute: worker processes (default 3) under nice, one C++ thread each.

Usage:
    nice -n 10 uv run python research/studies/hk_pairs.py [--jobs 3] [--smoke]
Outputs: results/hk_pairs_<stamp>.csv (one row per candidate and fee) and
         results/hk_pairs_<stamp>.json (every window: pair, params, returns).
"""
import os

os.environ.setdefault('MIDFREQ_ENGINE_THREADS', '1')   # one C++ thread per worker process

import argparse
import contextlib
import io
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from itertools import combinations, product
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from midfreq.core import paths
from midfreq.core.candle import Candle, Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.core.risk_manager import EqualDollarSizer, RiskManager
from midfreq.data.storage import DataStorage
from midfreq.backtest.backtester import Backtester, PythonBacktester
from midfreq.backtest.native import BatchRunner
from midfreq.backtest.optimizer import _get_objective_value
from midfreq.strategies.candle_strategies import BaseStrategy, PairsTradingStrategy, STRATEGY_REGISTRY
from midfreq.research.reality_check import reality_check, naive_single_test
from alpha_vs_market import segment_prices, daily_paths, analyse

REPO = paths.repo_root()
TF = Timeframe.HOUR_1

GROUPS: Dict[str, List[str]] = {
    'banks':       ['HK.00005', 'HK.00939', 'HK.01398', 'HK.03988', 'HK.03968', 'HK.02388'],
    'insurers':    ['HK.01299', 'HK.02318', 'HK.02628'],
    'telecoms':    ['HK.00941', 'HK.00762'],
    'oil':         ['HK.00857', 'HK.00883', 'HK.00386'],
    'hk_property': ['HK.00016', 'HK.00012', 'HK.01113', 'HK.00101'],
    'cn_property': ['HK.00688', 'HK.01109', 'HK.00960'],
    'utilities':   ['HK.00002', 'HK.00003', 'HK.00006', 'HK.01038'],
    'casinos':     ['HK.00027', 'HK.01928'],
}
VARIANTS = ['registry', 'long_cheap', 'long_short']
GRID = [{'lookback': lb, 'entry_z': ez, 'exit_z': xz}
        for lb, ez, xz in product([20, 40, 60], [1.5, 2.0, 2.5], [0.0, 0.5])]
CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0
FEE = Portfolio.HK_FEE_RATE          # 0.0016, the pass decision
FEE_SENS = 0.0008                    # sensitivity only
BORROW_RATE = 0.05                   # V3 short leg, assumed, a year
CAPITAL = 100_000.0
LIVE_TRAIN_DAYS = 84


# ─── Strategies (research-only; nothing under packages/ is changed) ──────────────────────────

class _PairZ(BaseStrategy):
    """The registry PairsTradingStrategy's z-score, verbatim: rolling log(A/B) over `lookback`
    bars, population std, evaluated on leg B's bar. Subclasses decide what to trade."""

    def __init__(self, portfolio: Portfolio, lookback: int = 30, entry_z: float = 2.0,
                 exit_z: float = 0.5, risk_manager: Optional[RiskManager] = None):
        super().__init__(portfolio, risk_manager=risk_manager, lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        self.lookback, self.entry_z, self.exit_z = lookback, entry_z, exit_z
        self.prices: Dict[str, list] = {}
        self._symbols_list: list = []

    def _z(self, symbol: str, candle: Candle):
        if symbol not in self.prices:
            self.prices[symbol] = []
            self._symbols_list.append(symbol)
        self.prices[symbol].append(candle.close)
        if len(self._symbols_list) < 2:
            return None
        sym_a, sym_b = self._symbols_list[0], self._symbols_list[1]
        if symbol != sym_b:
            return None
        pa, pb = self.prices[sym_a], self.prices[sym_b]
        n = min(len(pa), len(pb))
        if n < self.lookback:
            return None
        spread = [math.log(pa[i] / pb[i]) for i in range(n - self.lookback, n) if pb[i] != 0]
        if len(spread) < self.lookback:
            return None
        mean = sum(spread) / len(spread)
        std = (sum((s - mean) ** 2 for s in spread) / len(spread)) ** 0.5
        if std == 0:
            return None
        return (spread[-1] - mean) / std, sym_a, sym_b, pa[-1], pb[-1]


class LongCheapLegPairs(_PairZ):
    """V2: hold the cheap leg only, either side; never short."""

    def on_data(self, symbol: str, candle: Candle):
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return
        out = self._z(symbol, candle)
        if out is None:
            return
        z, a, b, pa, pb = out
        qa, qb = self.portfolio.get_position_qty(a), self.portfolio.get_position_qty(b)
        if qa > 0 and z > -self.exit_z:
            self.portfolio.execute_trade(a, False, qa, pa, candle.timestamp, exit_reason='signal')
            qa = 0
        if qb > 0 and z < self.exit_z:
            self.portfolio.execute_trade(b, False, qb, pb, candle.timestamp, exit_reason='signal')
            qb = 0
        if qa == 0 and qb == 0:
            if z < -self.entry_z:
                q = self._get_trade_qty(a, pa)
                if q > 0:
                    self.portfolio.execute_trade(a, True, q, pa, candle.timestamp)
            elif z > self.entry_z:
                q = self._get_trade_qty(b, pb)
                if q > 0:
                    self.portfolio.execute_trade(b, True, q, pb, candle.timestamp)


class LongShortPairs(_PairZ):
    """V3: dollar-neutral long/short, half of equity per leg. Needs ShortablePortfolio."""

    LEG_SHARE = 0.5
    COST_BUFFER = 0.004

    def on_data(self, symbol: str, candle: Candle):
        out = self._z(symbol, candle)
        if out is None:
            return
        z, a, b, pa, pb = out
        pf, ts = self.portfolio, candle.timestamp
        qa, qb = pf.get_position_qty(a), pf.get_position_qty(b)
        if qa > 0 and z > -self.exit_z:            # long A / short B reverted
            pf.execute_trade(a, False, qa, pa, ts, exit_reason='signal')
            pf.execute_trade(b, True, -qb, pb, ts, exit_reason='signal')
            qa = qb = 0
        elif qa < 0 and z < self.exit_z:           # short A / long B reverted
            pf.execute_trade(b, False, qb, pb, ts, exit_reason='signal')
            pf.execute_trade(a, True, -qa, pa, ts, exit_reason='signal')
            qa = qb = 0
        if qa == 0 and qb == 0 and abs(z) > self.entry_z:
            budget = pf.cash * self.LEG_SHARE * (1.0 - self.COST_BUFFER)   # flat: equity == cash
            na, nb = int(budget / pa), int(budget / pb)
            if na <= 0 or nb <= 0:
                return
            if z < -self.entry_z:                  # A cheap: short B first (raises cash), buy A
                pf.execute_trade(b, False, nb, pb, ts)
                pf.execute_trade(a, True, na, pa, ts)
            else:                                  # A dear: short A, buy B
                pf.execute_trade(a, False, na, pa, ts)
                pf.execute_trade(b, True, nb, pb, ts)


class ShortablePortfolio(Portfolio):
    """Portfolio that may hold negative quantities (V3 only). Commission on every fill as
    Portfolio; a borrow fee on the short notional at entry, pro rata to calendar time held,
    deducted from cash when the short is covered. Opens only from flat and closes in full,
    which is all LongShortPairs does."""

    def __init__(self, initial_cash: float, commission_rate: float, borrow_rate: float):
        super().__init__(initial_cash=initial_cash, commission_rate=commission_rate)
        self.borrow_rate = borrow_rate
        self.borrow_paid = 0.0
        self._short_since: Dict[str, pd.Timestamp] = {}

    def execute_trade(self, symbol, is_buy, qty, price, timestamp, exit_reason=None):
        if qty <= 0:
            return
        value = qty * price
        commission = value * self.commission_rate
        held = self.get_position_qty(symbol)
        new = held + qty if is_buy else held - qty
        borrow = 0.0
        if is_buy and held < 0:
            if new != 0:
                raise NotImplementedError("partial cover")
            days = (pd.Timestamp(timestamp) - pd.Timestamp(self._short_since[symbol])).total_seconds() / 86400
            borrow = -held * self.positions[symbol]['entry_price'] * self.borrow_rate * max(days, 0.0) / 365
        elif held != 0 and (held > 0) != (new > 0) and new != 0:
            raise NotImplementedError("crossing zero")
        elif held != 0 and abs(new) > abs(held):
            raise NotImplementedError("adding to a position")
        cash_change = (-(value + commission) - borrow) if is_buy else (value - commission)
        if is_buy and self.cash + cash_change < 0:
            return
        self.cash += cash_change
        self.borrow_paid += borrow
        if new == 0:
            self.positions.pop(symbol, None)
            self._short_since.pop(symbol, None)
        elif held == 0:
            self.positions[symbol] = {'qty': new, 'entry_price': price}
            if new < 0:
                self._short_since[symbol] = timestamp
        else:
            self.positions[symbol]['qty'] = new
        record = {'timestamp': timestamp, 'symbol': symbol, 'action': 'BUY' if is_buy else 'SELL',
                  'qty': qty, 'price': price, 'commission': commission, 'borrow_fee': borrow,
                  'cash_after': self.cash}
        if exit_reason:
            record['exit_reason'] = exit_reason
        self.trade_history.append(record)
        self.equity_curve.append({'timestamp': timestamp,
                                  'equity': self.cash + sum(p['qty'] * price for p in self.positions.values())})


# ─── Helpers ─────────────────────────────────────────────────────────────────────────────────

def _risk():
    """Full-equity sizing for the held leg; no stops; drawdown breaker off."""
    return RiskManager(max_drawdown_pct=1.0, position_sizer=EqualDollarSizer(n_positions=1))


def _utc(t):
    return pd.to_datetime(t, utc=True)


def load_closes(storage: DataStorage, symbols: List[str]) -> pd.DataFrame:
    return pd.DataFrame({s: storage.load_data(s.replace('.', '_'), TF.value)['close'] for s in symbols}).dropna()


def window_bounds(start, end, n_splits):
    """walk_forward's own window arithmetic."""
    ws = (end - start).days // n_splits
    for w in range(n_splits):
        w_start = start + timedelta(days=w * ws)
        w_end = w_start + timedelta(days=ws)
        train_end = w_start + timedelta(days=int(ws * TRAIN_PCT))
        yield w + 1, w_start, train_end, train_end, w_end


def select_pair(closes: pd.DataFrame, group: List[str]):
    """Lowest ADF p-value of the unit log-ratio spread on these (training) closes."""
    stats = []
    for a, b in combinations(group, 2):
        la, lb = np.log(closes[a]), np.log(closes[b])
        p = float(adfuller(la - lb, autolag='AIC')[1])
        corr = float(la.diff().corr(lb.diff()))
        stats.append({'a': a, 'b': b, 'adf_p': p, 'ret_corr': corr})
    best = min(stats, key=lambda r: (r['adf_p'], -r['ret_corr']))
    return best, stats


def _quiet():
    return contextlib.redirect_stdout(io.StringIO())


def _run_v3(storage, symbols, params, start, end, fee, end_inclusive=True):
    pf = ShortablePortfolio(CAPITAL, fee, BORROW_RATE)
    bt = PythonBacktester(storage=storage, portfolio=pf, risk_manager=None, slippage_bps=SLIPPAGE_BPS)
    with _quiet():
        try:
            m = bt.run(LongShortPairs, symbols=symbols, timeframe=TF, start_date=start, end_date=end,
                       end_inclusive=end_inclusive, **params)
        except Exception:
            m = {}
    return m, pf


def train_search(variant, storage, a, b, start, end, fee, end_inclusive=False):
    """Grid-search one segment by Sharpe, combinations that traded first (walk_forward's rule).
    Returns (order, params, train metrics, number of grid points)."""
    best_key, best = (False, float('-inf')), None
    if variant == 'registry':
        orders, cls = [[a, b], [b, a]], PairsTradingStrategy
    else:
        orders, cls = [[a, b]], LongCheapLegPairs
    for order in orders:
        if variant == 'long_short':
            results = [_run_v3(storage, order, p, start, end, fee, end_inclusive)[0] for p in GRID]
        else:
            runner = BatchRunner(None, cls, order, TF, storage, CAPITAL, fee, _risk(), SLIPPAGE_BPS)
            results = [m for m, _ in runner.run(GRID, start, end, end_inclusive=end_inclusive)]
        for p, m in zip(GRID, results):
            if not m:
                continue
            key = (m.get('total_trades', 0) > 0, _get_objective_value(m, OBJECTIVE))
            if key > best_key:
                best_key = key
                best = (order, dict(p), {'return_pct': m['return_pct'], 'sharpe_ratio': m['sharpe_ratio'],
                                         'total_trades': m['total_trades']})
    return best, len(orders) * len(GRID)


def run_test(variant, storage, order, params, start, end, fee):
    """The chosen setting on the test segment: metrics, the portfolio, and which engine ran."""
    if variant == 'long_short':
        m, pf = _run_v3(storage, order, params, start, end, fee)
        return m, pf, 'python'
    pf = Portfolio(initial_cash=CAPITAL, commission_rate=fee)
    cls = PairsTradingStrategy if variant == 'registry' else LongCheapLegPairs
    bt = Backtester(storage=storage, portfolio=pf, risk_manager=_risk(), slippage_bps=SLIPPAGE_BPS)
    with _quiet():
        m = bt.run(cls, symbols=order, timeframe=TF, start_date=start, end_date=end, **params)
    # the optimiser's path must give the same number
    runner = BatchRunner(None, cls, order, TF, storage, CAPITAL, fee, _risk(), SLIPPAGE_BPS)
    [(m2, _)] = runner.run([params], start, end)
    if abs(m2.get('return_pct', 0.0) - m.get('return_pct', 0.0)) > 1e-9:
        raise RuntimeError(f"test re-run mismatch {order} {params}: {m2.get('return_pct')} vs {m.get('return_pct')}")
    return m, pf, bt.engine_used


def run_task(task):
    """One (variant, group, configuration, fee): the whole walk-forward."""
    variant, group, cname, n_splits, fee, start, end = task
    t0 = time.time()
    storage = DataStorage(str(REPO / 'data'))
    syms = GROUPS[group]
    closes = load_closes(storage, syms)
    last_available = closes.index.max().to_pydatetime().replace(tzinfo=None)
    windows, skipped, grid_points = [], [], 0
    for wid, train_start, train_end, test_start, test_end in window_bounds(start, end, n_splits):
        if test_start < last_available < test_end:
            covered, total = (last_available - test_start).days, max(1, (test_end - test_start).days)
            if covered / total < 0.5:
                skipped.append(wid)
                continue
        elif test_start >= last_available:
            skipped.append(wid)
            continue
        tr = closes[(closes.index >= _utc(train_start)) & (closes.index < _utc(train_end))]
        pick, pair_stats = select_pair(tr, syms)
        best, grid_points = train_search(variant, storage, pick['a'], pick['b'], train_start, train_end, fee)
        if best is None:
            order, params, train_m = [pick['a'], pick['b']], dict(GRID[0]), {}
            m, pf, engine = {'return_pct': 0.0, 'benchmark_return_pct': 0.0}, None, None
        else:
            order, params, train_m = best
            m, pf, engine = run_test(variant, storage, order, params, test_start, test_end, fee)
        trades = pf.trade_history if pf is not None else []
        prices = segment_prices(storage, order, test_start, test_end)
        path = daily_paths(prices, trades, CAPITAL)
        closing = [t for t in trades if t.get('exit_reason')]
        windows.append({
            'window': wid, 'train_start': str(train_start), 'train_end': str(train_end),
            'test_start': str(test_start), 'test_end': str(test_end),
            'pair': f"{pick['a']}/{pick['b']}", 'pair_adf_p': pick['adf_p'], 'pair_ret_corr': pick['ret_corr'],
            'order': order, 'params': params, 'train': train_m, 'engine': engine,
            'return': m.get('return_pct', 0.0), 'benchmark_return': m.get('benchmark_return_pct', 0.0),
            'fills': len(trades), 'round_trips': len(closing) if variant != 'long_short' else len(closing) // 2,
            'borrow_paid': getattr(pf, 'borrow_paid', 0.0) if pf is not None else 0.0,
            'path_return': (path['strategy'].iloc[-1] / CAPITAL - 1) * 100,
            'path_benchmark_return': (path['benchmark'].iloc[-1] - 1) * 100,
            'daily': [[str(d), float(r['strategy']), float(r['benchmark'])] for d, r in path.iterrows()],
            'all_pairs_train': pair_stats,
        })
    return {'variant': variant, 'group': group, 'config': cname, 'fee': fee, 'windows': windows,
            'skipped': skipped, 'grid_points': grid_points, 'secs': round(time.time() - t0, 1)}


def summarise(windows):
    r = [w['return'] for w in windows]
    per, alpha = analyse(windows)
    return {
        'n_windows': len(r), 'mean_oos': float(np.mean(r)), 'consistency': float(np.mean([x > 0 for x in r]) * 100),
        'oos_returns': r, 'mean_benchmark': alpha['mean_benchmark'], 'mean_excess': alpha['mean_excess'],
        'pct_windows_excess_positive': alpha['pct_windows_excess_positive'],
        'alpha_daily_pct': alpha['alpha_daily_pct'], 'alpha_t': alpha['alpha_t'], 'beta': alpha['beta'],
        'excess_daily_pct': alpha['excess_daily_pct'], 'excess_t': alpha['excess_t'],
        'fills_per_window': float(np.mean([w['fills'] for w in windows])),
        'round_trips_per_window': float(np.mean([w['round_trips'] for w in windows])),
        'zero_trade_windows': int(sum(w['fills'] == 0 for w in windows)),
        'path_mismatch_windows': [w['window'] for w in windows
                                  if abs(w['path_return'] - w['return']) > 0.01
                                  or abs(w['path_benchmark_return'] - w['benchmark_return']) > 0.01],
    }


def pooled(group_runs):
    """The pooled book: all groups at once, equal capital, window by window."""
    by_w = {}
    for run in group_runs:
        for w in run['windows']:
            by_w.setdefault(w['window'], []).append(w)
    out = []
    for wid in sorted(by_w):
        ws = by_w[wid]
        if len(ws) != len(group_runs):
            continue
        daily = {}
        for w in ws:
            for d, s, b in w['daily']:
                daily.setdefault(d, []).append((s, b))
        out.append({
            'window': wid, 'test_start': ws[0]['test_start'], 'test_end': ws[0]['test_end'],
            'return': float(np.mean([w['return'] for w in ws])),
            'benchmark_return': float(np.mean([w['benchmark_return'] for w in ws])),
            'fills': sum(w['fills'] for w in ws), 'round_trips': sum(w['round_trips'] for w in ws),
            'path_return': float(np.mean([w['path_return'] for w in ws])),
            'path_benchmark_return': float(np.mean([w['path_benchmark_return'] for w in ws])),
            'daily': [[d, float(np.mean([s for s, _ in v])), float(np.mean([b for _, b in v]))]
                      for d, v in sorted(daily.items()) if len(v) == len(ws)],
        })
    return out


def live_pick(variant, group, storage, end, fee=FEE):
    """pick_sp100_live_config.py's procedure on this candidate: the last 84 days, through the
    close of the last day; select the pair, grid-search by Sharpe, report the winner."""
    syms = GROUPS[group]
    start = end - timedelta(days=LIVE_TRAIN_DAYS)
    closes = load_closes(storage, syms)
    seg = closes[(closes.index >= _utc(start)) & (closes.index < _utc(end.date()) + pd.Timedelta(days=1))]
    pick, stats = select_pair(seg, syms)
    best, n = train_search(variant, storage, pick['a'], pick['b'], start, end.date(), fee, end_inclusive=True)
    return {'variant': variant, 'group': group, 'train_start': str(start), 'train_end': str(end),
            'pair': f"{pick['a']}/{pick['b']}", 'pair_adf_p': pick['adf_p'],
            'order': best[0] if best else None, 'params': best[1] if best else None,
            'in_sample': best[2] if best else None, 'grid_points': n}


def passes(a, b):
    return all(x['mean_oos'] > 0 and x['consistency'] >= MIN_CONSISTENCY for x in (a, b))


def main():
    ap = argparse.ArgumentParser(description='HK pairs within sector groups (pre-registered)')
    ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--smoke', action='store_true',
                    help='2 symbols (telecoms), 0.16%% only, nothing written to results/')
    ap.add_argument('--n-boot', type=int, default=5000)
    args = ap.parse_args()

    storage = DataStorage(str(REPO / 'data'))
    all_syms = sorted({s for g in GROUPS.values() for s in g})
    end = storage.latest_common_timestamp(all_syms, TF.value)
    start = end - timedelta(days=HISTORY_DAYS)
    groups = ['telecoms'] if args.smoke else list(GROUPS)
    fees = [FEE] if args.smoke else [FEE, FEE_SENS]
    print(f"HK pairs | {len(groups)} groups, {len(all_syms)} names | {start} -> {end} | "
          f"slippage {SLIPPAGE_BPS} bps | fees {fees} | jobs {args.jobs}", flush=True)

    # the slow ones (the most pairs, Python variants) first
    weight = {g: len(list(combinations(GROUPS[g], 2))) for g in groups}
    tasks = [(v, g, cname, n, fee, start, end) for fee in fees for v in VARIANTS for g in groups
             for cname, n in CONFIGS]
    tasks.sort(key=lambda t: (-(t[4] == FEE), -(t[0] != 'registry'), -weight[t[1]], -t[3]))
    runs = []
    t_all = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futs = [pool.submit(run_task, t) for t in tasks]
        for f in as_completed(futs):
            r = f.result()
            runs.append(r)
            s = summarise(r['windows'])
            print(f"  [{len(runs):3d}/{len(tasks)}] {r['variant']:10s} {r['group']:11s} {r['config']:15s} "
                  f"fee {r['fee']*100:.2f}% | OOS {s['mean_oos']:+7.3f}% | win {s['consistency']:5.1f}% | "
                  f"fills/w {s['fills_per_window']:5.1f} | {r['secs']:.0f}s", flush=True)
    wall = time.time() - t_all

    rows, detail = [], {}
    for fee in fees:
        for v in VARIANTS:
            for g in groups + ['pooled']:
                per_cfg = {}
                for cname, _ in CONFIGS:
                    if g == 'pooled':
                        grp_runs = [r for r in runs if r['variant'] == v and r['fee'] == fee and r['config'] == cname]
                        if len(grp_runs) != len(groups):
                            continue
                        wins = pooled(grp_runs)
                    else:
                        [r] = [r for r in runs if r['variant'] == v and r['group'] == g and r['fee'] == fee
                               and r['config'] == cname]
                        wins = r['windows']
                    per_cfg[cname] = (summarise(wins), wins)
                if len(per_cfg) != len(CONFIGS):
                    continue
                (sa, wa), (sb, wb) = per_cfg[CONFIGS[0][0]], per_cfg[CONFIGS[1][0]]
                pairs_a = pd.Series([w['pair'] for w in wa]).value_counts().to_dict() if g != 'pooled' else {}
                rows.append({
                    'variant': v, 'group': g, 'commission': fee, 'slippage_bps': SLIPPAGE_BPS,
                    'config_a_oos': round(sa['mean_oos'], 4), 'config_a_consistency': round(sa['consistency'], 1),
                    'config_b_oos': round(sb['mean_oos'], 4), 'config_b_consistency': round(sb['consistency'], 1),
                    'passes': passes(sa, sb) if fee == FEE else None,
                    'config_a_benchmark': round(sa['mean_benchmark'], 4), 'config_b_benchmark': round(sb['mean_benchmark'], 4),
                    'config_a_excess': round(sa['mean_excess'], 4), 'config_b_excess': round(sb['mean_excess'], 4),
                    'config_a_pct_excess_pos': round(sa['pct_windows_excess_positive'], 1),
                    'config_b_pct_excess_pos': round(sb['pct_windows_excess_positive'], 1),
                    'config_a_alpha_daily_pct': round(sa['alpha_daily_pct'], 5), 'config_a_alpha_t': round(sa['alpha_t'], 2),
                    'config_a_beta': round(sa['beta'], 3),
                    'config_b_alpha_daily_pct': round(sb['alpha_daily_pct'], 5), 'config_b_alpha_t': round(sb['alpha_t'], 2),
                    'config_b_beta': round(sb['beta'], 3),
                    'config_a_fills_per_window': round(sa['fills_per_window'], 1),
                    'config_b_fills_per_window': round(sb['fills_per_window'], 1),
                    'config_a_round_trips_per_window': round(sa['round_trips_per_window'], 1),
                    'config_b_round_trips_per_window': round(sb['round_trips_per_window'], 1),
                    'config_a_zero_trade_windows': sa['zero_trade_windows'], 'config_b_zero_trade_windows': sb['zero_trade_windows'],
                    'config_a_pairs': json.dumps(pairs_a),
                    'path_mismatches': json.dumps({'A': sa['path_mismatch_windows'], 'B': sb['path_mismatch_windows']}),
                })
                detail[f"{v} | {g} | {fee}"] = {
                    c: {'summary': {k: x for k, x in s.items()},
                        'windows': [{k: x for k, x in w.items() if k not in ('daily', 'all_pairs_train')} for w in ws]}
                    for c, (s, ws) in per_cfg.items()}

    df = pd.DataFrame(rows)
    main_df = df[df.commission == FEE]
    winners = main_df[main_df.passes == True]   # noqa: E712

    rc_out, live_out = {}, []
    if len(winners):
        for cname, _ in CONFIGS:
            cands = {f"{r['variant']} | {r['group']}": detail[f"{r['variant']} | {r['group']} | {FEE}"][cname]['summary']['oos_returns']
                     for _, r in main_df.iterrows()}
            rc = reality_check(cands, n_boot=args.n_boot)
            rc['naive_p_value'] = naive_single_test(cands[rc['best_candidate']], n_boot=args.n_boot)
            rc_out[cname] = rc
        for _, r in winners.iterrows():
            if r['group'] == 'pooled':
                live_out += [live_pick(r['variant'], g, storage, end) for g in groups]
            else:
                live_out.append(live_pick(r['variant'], r['group'], storage, end))

    print(f"\nDone in {wall/60:.1f} min (wall, {args.jobs} workers)\n")
    show = ['variant', 'group', 'config_a_oos', 'config_a_consistency', 'config_b_oos', 'config_b_consistency',
            'passes', 'config_a_fills_per_window', 'config_b_fills_per_window']
    for fee in fees:
        print(f"--- commission {fee*100:.2f}%/side ---")
        print(df[df.commission == fee][show].to_string(index=False))
    print(f"\n{len(winners)}/{len(main_df)} candidates pass at {FEE*100:.2f}%/side")
    for c, rc in rc_out.items():
        print(f"Reality Check {c}: p = {rc['p_value']:.4f} (best {rc['best_candidate']}, naive p {rc['naive_p_value']:.4f})")
    for lp in live_out:
        print(f"live pick {lp['variant']} {lp['group']}: {lp['pair']} {lp['order']} {lp['params']} {lp['in_sample']}")

    if args.smoke:
        print("\n(smoke run: nothing written)")
        return
    stamp = time.strftime('%Y%m%d_%H%M%S')
    # Results go to results/hk_search/, outside the results index (midfreq.research.results_index reads
    # results/*.csv only): the record of 217 results the report, the dashboard and the demo cite stays as
    # it is until the amended report decides how the Hong Kong search enters it.
    out = REPO / 'results' / 'hk_search' / f'hk_pairs_{stamp}.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    out.with_suffix('.json').write_text(json.dumps({
        'study': 'hk_pairs', 'start': str(start), 'end': str(end), 'groups': GROUPS, 'variants': VARIANTS,
        'grid': GRID, 'configs': CONFIGS, 'train_pct': TRAIN_PCT, 'objective': OBJECTIVE,
        'commission_pass': FEE, 'commission_sensitivity': FEE_SENS, 'slippage_bps': SLIPPAGE_BPS,
        'borrow_rate_v3': BORROW_RATE, 'capital': CAPITAL,
        'n_candidates': len(main_df), 'grid_points_searched': sum(
            (2 if v == 'registry' else 1) * len(GRID) for v in VARIANTS) * len(groups),
        'wall_minutes': round(wall / 60, 1), 'task_seconds': {f"{r['variant']}|{r['group']}|{r['config']}|{r['fee']}": r['secs'] for r in runs},
        'reality_check': rc_out, 'live_picks': live_out, 'detail': detail,
        'pair_selection_all_windows': {f"{r['variant']}|{r['group']}|{r['config']}|{r['fee']}":
                                       [{'window': w['window'], 'pairs': w['all_pairs_train']} for w in r['windows']]
                                       for r in runs if r['variant'] == 'registry'},
    }, indent=1, default=float))
    print(f"\nSaved {out} and {out.with_suffix('.json').name}")


if __name__ == '__main__':
    main()
