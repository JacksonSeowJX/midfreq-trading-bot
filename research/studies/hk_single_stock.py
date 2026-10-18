"""
Hong Kong single-stock family: low-turnover registry strategies, an opening-gap reversal, ML re-check
====================================================================================================

PRE-REGISTRATION. Written and saved on 18 Oct 2026, before any backtest of the candidates below was
run. Only turnover and data-coverage statistics had been looked at (to fix the universe). Nothing
below may be added to or tuned after the results are seen; anything tried afterwards goes in a
clearly labelled exploratory section, counts toward the search total, and is never called a pass.

Question
--------
Hong Kong costs 0.16% per side all-in (Portfolio.HK_FEE_RATE, calibrated on paper fills), so the
round trip is 0.32% plus spread, and the project's hourly single-stock strategies died of cost in
best_tool_robust_v2 / best_tool_new_stocks. Do the same strategies survive when restricted to the
slow end of their grids (few trades), and does a Hong Kong-specific idea (fading opening gaps) work?

Universe (fixed)
----------------
The 10 most liquid names in the 88-stock HK cache, by MEDIAN HOURLY TURNOVER (close x volume, bars
with volume > 0), measured over the first 51 days of the study window only (2023-08-29 16:00 to
2023-10-19 16:00, legacy stamps). That span is config B's first training segment and precedes every
test segment of both configurations, so the choice uses no information from any test period.
(Ranking over the whole three years instead would favour names whose PRICE rose, e.g. Xiaomi and
SMIC, because turnover is price x volume: that is look-ahead, so it is not used.) All 10 have
complete hourly data over the window.

    rank  symbol    name                     median HK$m/hour (first 51 days)
     1    HK.00700  Tencent                  591.0
     2    HK.03690  Meituan                  337.3
     3    HK.09988  Alibaba                  285.7
     4    HK.01299  AIA                      202.7
     5    HK.01211  BYD                      185.1
     6    HK.02318  Ping An                  135.5
     7    HK.00388  HKEX                     131.3
     8    HK.09618  JD.com                   129.0
     9    HK.01024  Kuaishou                 124.4
    10    HK.00883  CNOOC                    117.3
    (11 HK.00939 CCB 110.3, 12 HK.01810 Xiaomi 106.9: not included)

The script recomputes this ranking and stops if it does not reproduce the list.

Window and validation standard (the project's, unchanged)
---------------------------------------------------------
* Hourly bars, window anchored to the data: end = DataStorage.latest_common_timestamp of the 10
  names (2026-08-28 16:00, Tencent's last bar), start = end - 3 x 365 days.
* Config A: 9 windows (~121 days), 70% train. Config B: 15 windows (~73 days), 70% train.
* Each window: grid-search the TRAINING segment by Sharpe ratio (configurations that traded rank
  first), then trade the chosen setting, unchanged, on the TEST segment. This is
  midfreq.backtest.optimizer.walk_forward itself, called verbatim; the only change is that
  generate_param_grid is patched in-process to return the grids below for the candidates' names.
  The C++ engine (BatchRunner) runs SMA, MACD, Regime Switch and Buy & Hold + Risk Overlay; the gap
  strategy and the ML classifier have no C++ port and run on the Python backtester.
* Costs: commission 0.0016 per side + slippage 5.0 bps per side (SLIPPAGE_BPS = 5.0, the value in
  best_tool_allocation_robust_v2.py and best_tool_allocation_new_stocks.py; HK's measured median
  half-spread is 3.33 bps, so 5 is conservative). Cost-sensitivity line only: the whole
  walk-forward re-run at 0.0008 per side, same slippage.
* Sizing: the project's single-stock convention (robust_v2, and the ML pass it found): no risk
  manager, so each entry buys 100 shares on HK$100,000. Return magnitudes therefore scale with the
  share price; the pass rule is sign-based and is unaffected. The alpha view normalises (below).

Candidates and grids (exact)
----------------------------
"Slowest third" of a registry parameter = the registry's values v with v >= max - (max - min) / 3
(longer periods, wider thresholds). Where that leaves more than ~50 combinations, an evenly spaced
lattice inside it is used (endpoints included). Unlisted parameters stay at their defaults.

 1. SMA Crossover [slow third]. Registry: fast 2-50, slow 5-200 (step 1).
    fast_period in {34, 38, 42, 46, 50} x slow_period in {135, 148, 161, 174, 187, 200} = 30.
 2. MACD [slow third]. Registry: fast 2-50, slow 10-100, signal 2-30 (step 1).
    fast_ema in {34, 42, 50} x slow_ema in {70, 80, 90, 100} x signal_period in {21, 24, 27, 30} = 48.
 3. Regime Switch [slow third]. Registry: regime_lookback 10-50 step 5, er_threshold 0.15-0.60
    step 0.05. regime_lookback in {40, 45, 50} x er_threshold in {0.45, 0.50, 0.55, 0.60} = 12.
    (A HIGH threshold is the slow side: the trend leg, an SMA 5/20 crossover that trades every few
    days, engages less, and the strategy mostly waits in its Bollinger 14 / 2.5 sigma leg.)
 4. Buy & Hold + Risk Overlay [wide third]. Registry: stop_loss 0.02-0.10 step 0.02, trailing
    0.03-0.15 step 0.03. stop_loss_pct in {0.08, 0.10} x trailing_stop_pct in {0.12, 0.15} = 4.
 5. HK Opening-Gap Reversal (new; OpeningGapReversal below). gap_pct in {0.015, 0.025, 0.04} = 3.
    Rules, long only, 100 shares:
      - previous close = close of the previous trading day's last traded bar;
      - the day's first traded bar (first bar with volume > 0; normally the 09:30-10:30 bar,
        stamped 10:30) opens more than gap_pct below the previous close -> buy at that bar's close;
      - sell at the close of that day's last bar: the bar stamped 16:00, or 12:00 on a half day
        (morning-only eves of Lunar New Year, Christmas and New Year). Half days come from
        config/venues.json's early_closes (2026 on) and HKEX's circulars for 2024-2025
        (HISTORICAL_HALF_DAYS, checked against the cache: exactly the days with morning bars only).
        Hourly bars follow the legacy loader: exchange-local bar-END times stamped as UTC, 10:30,
        11:30, 12:00 (the half-hour bar before lunch), 14:00, 15:00, 16:00; no bars over lunch.
      - a position still open when a new day starts (an unscheduled early close) is sold at the
        new day's first bar (guard only; no such day exists in the cache).
 6. Re-check: ML Direction Classifier on HK.00005 only, its full registry grid
    (refit_every {20, 60, 100} x buy_threshold {0.52, 0.65} x sell_threshold {0.35, 0.50} = 12),
    through the same walk_forward call, so its numbers are comparable with 1-5. The 9 Sep 2026
    robust_v2 run, which had the same window, reported +0.055% / 66.7% (A), +0.008% / 53.3% (B).

Search size: (30 + 48 + 12 + 4 + 3) x 10 names + 12 = 982 configurations, in 51 walk-forward
candidates (strategy x symbol), each run under both configurations. (The diagnostic below adds 942,
for a grand total of 1,924 configurations searched.)

Acceptance rule (per candidate = strategy x symbol), at 0.0016 per side
----------------------------------------------------------------------
PASS only if, under BOTH configurations, the mean out-of-sample (test) return per window is > 0
AND at least 50% of the windows have a test return > 0. Nothing else decides a pass.

Also reported per candidate
---------------------------
* Turnover: fills (buys + sells) per test window.
* Alpha view: per test window, the stock's buy-and-hold (bought at the segment's first candle, no
  fees: the backtester's benchmark_return_pct). Because the strategy holds 100 shares rather than
  being fully invested, its return is also expressed per unit of that position's notional:
  normalised = test return x 100,000 / (100 x first close of the segment), and the excess is
  normalised - buy-and-hold. Per strategy, the mean over the 10 names is compared with the
  equal-weight buy-and-hold of the same 10 names over the same windows.
* The 0.0008 cost line.
* Multiple testing: if ANY candidate passes, White's Reality Check (midfreq.research.reality_check;
  stationary bootstrap, mean block 2, 5,000 resamples, seed 12345) over all 51 candidates'
  per-window test returns at 0.0016, separately for config A and config B; p-values reported.
* Live settings, for any candidate that passes: the procedure of scripts/live/pick_sp100_live_config.py
  (grid-search the most recent 84 days, through the close of the last day, by Sharpe, among
  settings that traded; edge-of-grid warnings). Reported in the results JSON, never written to config/.
* Verdict rule for "fit to trade live": PASS and a Reality Check p-value below 0.05 under both
  configurations. A pass without that is "passes the standard but is indistinguishable from the
  luck of searching this many candidates".

Secondary diagnostic, pre-registered (never a pass)
---------------------------------------------------
walk_forward backtests every segment from a cold start. Config B's test segments are 22 calendar
days, about 90 hourly bars, which is shorter than the warm-up of the slow SMA grid (136-201 bars)
and of the slow MACD grid (92-131 bars), so under the standard those can hardly trade out of sample.
To show whether a failure is that artefact or the strategy, the same walk-forward (same windows,
same selection rule, same costs at 0.0016 only) is repeated with WARM-UP: before every train and
test segment the strategy is fed the 250 preceding bars, observing them but unable to trade (as a
live node warms up from its bar cache). Applied to candidates 1, 2, 3, 5 and 6; not to 4, whose
only look-back is none and whose single entry a warm-up would consume. Its harness is checked to
reproduce walk_forward exactly when the warm-up is zero (harness_check in the JSON).
942 configurations: (30 + 48 + 12 + 3) x 10 + 12.

Outputs
-------
results/hk_single_stock_<stamp>.csv           one row per candidate
results/hk_single_stock_windows_<stamp>.csv   one row per candidate x config x cost x harness x window
results/hk_single_stock_<stamp>.json          everything, including runtimes and data checks

Usage (laptop: niced, at most 3 worker processes):
    nice -n 10 uv run python research/studies/hk_single_stock.py --smoke --out-dir <scratch>
    nice -n 10 uv run python research/studies/hk_single_stock.py
"""
import argparse
import contextlib
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, time as dtime, timedelta
from typing import Any, Dict, List, Optional

# One engine thread and one BLAS thread per worker: three workers then use three cores.
for _k in ('MIDFREQ_ENGINE_THREADS', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
           'VECLIB_MAXIMUM_THREADS'):
    os.environ.setdefault(_k, '1')

import numpy as np
import pandas as pd

from midfreq.core import paths
from midfreq.core.candle import Candle, Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.backtest import optimizer
from midfreq.backtest.optimizer import walk_forward, _get_objective_value
from midfreq.backtest.backtester import PythonBacktester
from midfreq.backtest.native import BatchRunner
from midfreq.strategies import candle_strategies as cs
from midfreq.strategies.candle_strategies import BaseStrategy, STRATEGY_REGISTRY
from midfreq.research.reality_check import reality_check, naive_single_test
from midfreq.trading.refdata.calendar import VenueCalendar

REPO = paths.repo_root()

# ─── Pre-registered constants ────────────────────────────────────────────────────────────────
UNIVERSE = ['HK.00700', 'HK.03690', 'HK.09988', 'HK.01299', 'HK.01211',
            'HK.02318', 'HK.00388', 'HK.09618', 'HK.01024', 'HK.00883']
NAMES = {'HK.00700': 'Tencent', 'HK.03690': 'Meituan', 'HK.09988': 'Alibaba', 'HK.01299': 'AIA',
         'HK.01211': 'BYD', 'HK.02318': 'Ping An', 'HK.00388': 'HKEX', 'HK.09618': 'JD.com',
         'HK.01024': 'Kuaishou', 'HK.00883': 'CNOOC', 'HK.00005': 'HSBC'}
ML_SYMBOL = 'HK.00005'
RANK_DAYS = 51
HISTORY_DAYS = 3 * 365
CONFIGS = [('A', 9), ('B', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
SLIPPAGE_BPS = 5.0
RATE = Portfolio.HK_FEE_RATE          # 0.0016, decides the pass
RATE_SENS = 0.0008                    # cost-sensitivity line only
MIN_CONSISTENCY = 50.0
CAPITAL = 100_000.0
DEFAULT_QTY = 100                     # BaseStrategy._get_trade_qty without a risk manager
WARMUP_BARS = 250
LIVE_TRAIN_DAYS = 84
N_BOOT = 5000
MAX_JOBS = 3

# HKEX half days (morning session only) before config/venues.json's coverage starts in 2026.
# Sources: HKEX circulars on trading days for 2024 and 2025 (eves of Lunar New Year, Christmas and
# New Year; in 2023 both Christmas Eve and New Year's Eve fell on a Sunday).
HISTORICAL_HALF_DAYS = {date(2024, 2, 9), date(2024, 12, 24), date(2024, 12, 31),
                        date(2025, 1, 28), date(2025, 12, 24), date(2025, 12, 31)}


def _lattice(lo, hi, n):
    return [int(round(lo + (hi - lo) * i / (n - 1))) for i in range(n)]


def _grid(**axes) -> List[Dict[str, Any]]:
    import itertools
    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(axes[k] for k in keys))]


# ─── Candidate 5: the opening-gap reversal ───────────────────────────────────────────────────

_HK_CAL = VenueCalendar.for_venue('XHKG')
FULL_DAY_LAST_BAR = _HK_CAL.spec.sessions[-1].close          # 16:00: the afternoon session's close
HALF_DAYS = {d: t for d, t in _HK_CAL.early_closes.items()}  # 2026 on, from config/venues.json
HALF_DAYS.update({d: dtime(12, 0) for d in HISTORICAL_HALF_DAYS})


def is_last_bar_of_day(ts) -> bool:
    """A legacy hourly stamp is the bar's exchange-local END time tagged as UTC, so the day's last
    bar is the one stamped at the close: 16:00, or the early close (12:00) on a half day."""
    t = dtime(ts.hour, ts.minute)
    early = HALF_DAYS.get(ts.date())
    return t == (early if early is not None else FULL_DAY_LAST_BAR)


class OpeningGapReversal(BaseStrategy):
    """
    Fade an opening gap-down in a Hong Kong stock, intraday only.

    The day's first traded hourly bar (normally 09:30-10:30, stamped 10:30) opening more than
    `gap_pct` below the previous trading day's last close is bought at that bar's close; the
    position is sold at the close of the same day's last bar (16:00, or 12:00 on a half day).
    Long only, flat overnight, at most one entry per symbol per day. Bars with zero volume
    (the cache's placeholders for a typhoon-cancelled session) are neither a day's first bar nor
    a close. Prototype for research/studies/hk_single_stock.py; not part of the packages.
    """

    def __init__(self, portfolio: Portfolio, gap_pct: float = 0.025, risk_manager=None):
        super().__init__(portfolio, risk_manager=risk_manager, gap_pct=gap_pct)
        self.gap_pct = gap_pct
        self._day: Dict[str, date] = {}
        self._last_close: Dict[str, float] = {}
        self._prev_close: Dict[str, float] = {}
        self._opened: Dict[str, bool] = {}

    def on_start(self):
        print(f"Starting Opening-Gap Reversal (gap > {self.gap_pct:.2%})")

    def on_data(self, symbol: str, candle: Candle):
        if self._check_risk_exits(symbol, candle):
            return
        if self._is_halted(candle.close):
            return
        ts = candle.timestamp
        day = ts.date()
        position = self.portfolio.get_position_qty(symbol)

        if self._day.get(symbol) != day:
            if symbol in self._last_close:
                self._prev_close[symbol] = self._last_close[symbol]
            self._day[symbol] = day
            self._opened[symbol] = False
            if position > 0:   # held past a close it did not see (unscheduled early close)
                self.portfolio.execute_trade(symbol, False, position, candle.close, ts,
                                             exit_reason='stale')
                if self.risk_manager:
                    self.risk_manager.clear_position(symbol)
                position = 0

        last_bar = is_last_bar_of_day(ts)
        if not self._opened[symbol] and candle.volume > 0:
            self._opened[symbol] = True
            prev = self._prev_close.get(symbol)
            if (prev and position == 0 and not last_bar
                    and candle.open < prev * (1.0 - self.gap_pct)):
                qty = self._get_trade_qty(symbol, candle.close)
                if qty > 0:
                    self.portfolio.execute_trade(symbol, True, qty, candle.close, ts)
                    if self.risk_manager:
                        self.risk_manager.register_entry(symbol, candle.close)
        elif position > 0 and last_bar:
            self.portfolio.execute_trade(symbol, False, position, candle.close, ts, exit_reason='signal')
            if self.risk_manager:
                self.risk_manager.clear_position(symbol)

        if candle.volume > 0:
            self._last_close[symbol] = candle.close


# ─── Candidates: registry name, class, exact grid ────────────────────────────────────────────

CANDIDATES = {
    'SMA Crossover [slow third]': {
        'class': cs.MovingAverageCrossover, 'short': 'SMA',
        'grid': _grid(fast_period=[34, 38, 42, 46, 50], slow_period=_lattice(135, 200, 6))},
    'MACD [slow third]': {
        'class': cs.MACDStrategy, 'short': 'MACD',
        'grid': _grid(fast_ema=[34, 42, 50], slow_ema=[70, 80, 90, 100], signal_period=[21, 24, 27, 30])},
    'Regime Switch [slow third]': {
        'class': cs.RegimeSwitchStrategy, 'short': 'Regime',
        'grid': _grid(regime_lookback=[40, 45, 50], er_threshold=[0.45, 0.50, 0.55, 0.60])},
    'Buy & Hold + Risk Overlay [wide third]': {
        'class': cs.BuyAndHoldRiskOverlay, 'short': 'B&H overlay',
        'grid': _grid(stop_loss_pct=[0.08, 0.10], trailing_stop_pct=[0.12, 0.15])},
    'HK Opening-Gap Reversal': {
        'class': OpeningGapReversal, 'short': 'Gap reversal',
        'grid': _grid(gap_pct=[0.015, 0.025, 0.04])},
}
ML_NAME = 'ML Direction Classifier'
WARMUP_CANDIDATES = ['SMA Crossover [slow third]', 'MACD [slow third]', 'Regime Switch [slow third]',
                     'HK Opening-Gap Reversal', ML_NAME]

assert [len(c['grid']) for c in CANDIDATES.values()] == [30, 48, 12, 4, 3]
assert CANDIDATES['SMA Crossover [slow third]']['grid'][-1] == {'fast_period': 50, 'slow_period': 200}
assert sorted({p['slow_period'] for p in CANDIDATES['SMA Crossover [slow third]']['grid']}) == \
    [135, 148, 161, 174, 187, 200]

_original_generate_param_grid = optimizer.generate_param_grid


def _generate_param_grid(strategy_name: str):
    if strategy_name in CANDIDATES:
        return [dict(p) for p in CANDIDATES[strategy_name]['grid']]
    return _original_generate_param_grid(strategy_name)


def register_candidates():
    """In-process only: name the candidates in the registry and give walk_forward their grids."""
    for name, c in CANDIDATES.items():
        STRATEGY_REGISTRY.setdefault(name, {'class': c['class'], 'params': {}})
    optimizer.generate_param_grid = _generate_param_grid


register_candidates()


def grid_of(name):
    return optimizer.generate_param_grid(name)


def class_of(name):
    return STRATEGY_REGISTRY[name]['class']


# ─── Helpers ─────────────────────────────────────────────────────────────────────────────────

def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def qualifies(mean_oos, consistency):
    return mean_oos > 0 and consistency >= MIN_CONSISTENCY


class Bars:
    """One symbol's hourly bars, with the Candle objects built once (the warm-up harness)."""

    def __init__(self, storage: DataStorage, symbol: str):
        df = storage.load_data(symbol.replace('.', '_'), Timeframe.HOUR_1.value)
        self.symbol = symbol
        self.index = df.index
        self.close = df['close'].to_numpy(dtype=float)
        self.candles = [Candle(timestamp=t, open=o, high=h, low=lo, close=c, volume=v)
                        for t, o, h, lo, c, v in zip(df.index, df['open'], df['high'], df['low'],
                                                     df['close'], df['volume'])]
        self.last_available = df.index.max().to_pydatetime().replace(tzinfo=None)

    def bounds(self, start, end, end_inclusive):
        """[lo, hi) of the bars Backtester.run keeps for (start, end, end_inclusive)."""
        lo = self.index.searchsorted(pd.to_datetime(start, utc=True), side='left')
        cutoff = pd.to_datetime(end, utc=True)
        if end_inclusive:
            cutoff = cutoff + pd.Timedelta(days=1)
        hi = self.index.searchsorted(cutoff, side='left')
        return int(lo), int(hi)


def _no_trade(*args, **kwargs):
    return None


def run_segment(cls, params, bars: Bars, lo, hi, warm, rate, slippage_bps):
    """PythonBacktester.run over bars[lo:hi], after feeding bars[lo-warm:lo] to the strategy with
    trading disabled. With warm=0 this is PythonBacktester.run (checked: harness_check)."""
    if hi <= lo:
        return {}
    pf = Portfolio(initial_cash=CAPITAL, commission_rate=rate)
    bt = PythonBacktester(storage=None, portfolio=pf, risk_manager=None, slippage_bps=slippage_bps)
    sym = bars.symbol
    with quiet():
        try:
            strategy = cls(pf, risk_manager=None, **params)
            strategy.on_start()
            wlo = max(0, lo - warm)
            if wlo < lo:
                real = pf.execute_trade
                pf.execute_trade = _no_trade
                try:
                    for i in range(wlo, lo):
                        strategy.on_data(sym, bars.candles[i])
                finally:
                    pf.execute_trade = real
            latest, first, last_ts = {}, {}, None
            for i in range(lo, hi):
                c = bars.candles[i]
                latest[sym] = c.close
                if sym not in first:
                    first[sym] = c.close
                strategy.on_data(sym, c)
                ts = bars.index[i]
                if ts != last_ts:
                    pf.equity_curve_detailed.append({'timestamp': ts, 'equity': pf.get_current_equity(latest)})
                    last_ts = ts
            bench = bt._calculate_benchmark(first, latest)
            m = pf.calculate_metrics(latest)
            m['benchmark_return_pct'] = bench
            m['slippage_bps'] = slippage_bps
            m['total_events'] = hi - lo
            m['alpha'] = m['return_pct'] - bench
            return m
        except Exception:
            return {}


def warm_walk_forward(name, bars: Bars, start, end, n_splits, rate, warm):
    """optimizer.walk_forward's windows, selection rule and summary, on run_segment."""
    cls, grid = class_of(name), grid_of(name)
    if len(grid) > 200:
        step = len(grid) // 200
        grid = grid[::step][:200]
    window_size = (end - start).days // n_splits
    windows, oos = [], []
    for w in range(n_splits):
        w_start = start + timedelta(days=w * window_size)
        w_end = w_start + timedelta(days=window_size)
        train_start, train_end = w_start, w_start + timedelta(days=int(window_size * TRAIN_PCT))
        test_start, test_end = train_end, w_end
        la = bars.last_available
        if test_start < la < test_end:
            if (la - test_start).days / max(1, (test_end - test_start).days) < 0.5:
                continue
        elif test_start >= la:
            continue
        lo, hi = bars.bounds(train_start, train_end, end_inclusive=False)
        best_obj, best_params, best_train, best_traded = float('-inf'), {}, {}, False
        for params in grid:
            m = run_segment(cls, params, bars, lo, hi, warm, rate, SLIPPAGE_BPS)
            if m:
                obj = _get_objective_value(m, OBJECTIVE)
                traded = m.get('total_trades', 0) > 0
                if (traded, obj) > (best_traded, best_obj):
                    best_obj, best_params, best_train, best_traded = obj, dict(params), dict(m), traded
        lo, hi = bars.bounds(test_start, test_end, end_inclusive=True)
        tm = run_segment(cls, best_params, bars, lo, hi, warm, rate, SLIPPAGE_BPS) or \
            {'return_pct': 0.0, 'sharpe_ratio': 0.0, 'max_drawdown': 0.0}
        oos.append(tm.get('return_pct', 0.0))
        windows.append(optimizer.WalkForwardWindow(w + 1, train_start, train_end, test_start, test_end,
                                                   best_params, best_train, tm))
    pos = sum(1 for r in oos if r > 0)
    return {'windows': windows, 'summary': {
        'avg_oos_return': sum(oos) / len(oos) if oos else 0.0,
        'consistency_pct': pos / len(oos) * 100 if oos else 0.0,
        'oos_returns': oos, 'total_windows': len(oos)}}


def window_rows(res, bars: Bars):
    rows = []
    for w in res['windows']:
        tm = w.test_metrics
        lo, hi = bars.bounds(w.test_start, w.test_end, end_inclusive=True)
        p0, p1 = (bars.close[lo], bars.close[hi - 1]) if hi > lo else (float('nan'), float('nan'))
        bench = (p1 / p0 - 1.0) * 100.0
        ret = float(tm.get('return_pct', 0.0))
        norm = ret * CAPITAL / (DEFAULT_QTY * p0)
        rows.append({
            'window': w.window_id, 'test_start': str(w.test_start), 'test_end': str(w.test_end),
            'test_bars': hi - lo, 'params': json.dumps(w.best_params, sort_keys=True),
            'train_return': float(w.train_metrics.get('return_pct', 0.0)),
            'train_sharpe': float(w.train_metrics.get('sharpe_ratio', 0.0)),
            'train_trades': int(w.train_metrics.get('total_trades', 0)),
            'test_return': ret, 'test_trades': int(tm.get('total_trades', 0)),
            'test_sharpe': float(tm.get('sharpe_ratio', 0.0)),
            'first_close': float(p0), 'notional_pct': DEFAULT_QTY * p0 / CAPITAL * 100.0,
            'bh_return': bench,
            'bh_return_backtester': tm.get('benchmark_return_pct'),
            'norm_return': norm, 'norm_excess': norm - bench,
        })
    return rows


# ─── Tasks (run in worker processes) ─────────────────────────────────────────────────────────

def task(spec):
    name, sym, cfg, n_splits, rate, harness, start, end = spec
    storage = DataStorage()
    t0 = time.time()
    bars = Bars(storage, sym)
    if harness == 'standard':
        with quiet():
            res = walk_forward(strategy_name=name, symbols=[sym], timeframe=Timeframe.HOUR_1,
                               start_date=start, end_date=end, storage=storage, n_splits=n_splits,
                               train_pct=TRAIN_PCT, objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS,
                               commission_rate=rate)
        engine = BatchRunner(None, class_of(name), [sym], Timeframe.HOUR_1, storage, CAPITAL, rate,
                             None, SLIPPAGE_BPS).engine
    else:
        res = warm_walk_forward(name, bars, start, end, n_splits, rate, WARMUP_BARS)
        engine = 'python (warm-up harness)'
    s = res.get('summary', {})
    return {'candidate': name, 'symbol': sym, 'config': cfg, 'rate': rate, 'harness': harness,
            'engine': engine, 'seconds': round(time.time() - t0, 1),
            'skipped_windows': len(s.get('skipped_windows', [])),
            'windows': window_rows(res, bars)}


def harness_check(spec):
    """run_segment / warm_walk_forward with no warm-up must reproduce walk_forward exactly."""
    name, sym, n_splits, start, end = spec
    storage = DataStorage()
    bars = Bars(storage, sym)
    with quiet():
        ref = walk_forward(strategy_name=name, symbols=[sym], timeframe=Timeframe.HOUR_1,
                           start_date=start, end_date=end, storage=storage, n_splits=n_splits,
                           train_pct=TRAIN_PCT, objective=OBJECTIVE, slippage_bps=SLIPPAGE_BPS,
                           commission_rate=RATE)
    mine = warm_walk_forward(name, bars, start, end, n_splits, RATE, 0)
    a, b = ref['summary']['oos_returns'], mine['summary']['oos_returns']
    pa = [w.best_params for w in ref['windows']]
    pb = [w.best_params for w in mine['windows']]
    ta = [w.test_metrics.get('total_trades', 0) for w in ref['windows']]
    tb = [w.test_metrics.get('total_trades', 0) for w in mine['windows']]
    return {'candidate': name, 'symbol': sym, 'n_splits': n_splits,
            'identical': a == b and pa == pb and ta == tb,
            'max_abs_diff': max((abs(x - y) for x, y in zip(a, b)), default=0.0) if len(a) == len(b) else None,
            'windows': [len(a), len(b)]}


def picker(name, sym):
    """scripts/live/pick_sp100_live_config.py's procedure for one candidate."""
    storage = DataStorage()
    end = storage.latest_common_timestamp([sym], Timeframe.HOUR_1.value)
    start = end - timedelta(days=LIVE_TRAIN_DAYS)
    grid = grid_of(name)
    runner = BatchRunner(None, class_of(name), [sym], Timeframe.HOUR_1, storage, CAPITAL, RATE, None,
                         SLIPPAGE_BPS)
    with quiet():
        runs = runner.run(grid, start, end.date())
    rows = []
    for params, (m, _) in zip(grid, runs):
        rows.append({**params, 'objective': _get_objective_value(m, OBJECTIVE) if m else 0.0,
                     'return_pct': m.get('return_pct', 0.0) if m else 0.0,
                     'trades': m.get('total_trades', 0) if m else 0})
    traded = [r for r in rows if r['trades'] > 0]
    if not traded:
        return {'candidate': name, 'symbol': sym, 'train_window': [str(start), str(end)],
                'winner': None, 'note': 'no setting traded in the last 84 days'}
    best = max(traded, key=lambda r: r['objective'])
    keys = list(grid[0])
    boundary = []
    for k in keys:
        vals = sorted({g[k] for g in grid})
        if len(vals) > 1 and best[k] in (vals[0], vals[-1]):
            boundary.append(f"{k}={best[k]} is the grid {'MINIMUM' if best[k] == vals[0] else 'MAXIMUM'} {vals}")
    return {'candidate': name, 'symbol': sym, 'train_window': [str(start), str(end)],
            'train_days': LIVE_TRAIN_DAYS, 'configs_searched': len(grid), 'configs_that_traded': len(traded),
            'winner': {k: best[k] for k in keys},
            'in_sample': {'sharpe': best['objective'], 'return_pct': best['return_pct'], 'trades': best['trades']},
            'top5': sorted(traded, key=lambda r: -r['objective'])[:5], 'boundary_warnings': boundary,
            'data_ends': str(end)}


# ─── Universe and data checks (main process) ─────────────────────────────────────────────────

def all_hk_symbols():
    return sorted(p.parent.name.replace('_', '.', 1) for p in (REPO / 'data').glob('HK_*/1h.parquet'))


def verify_universe(storage, start, end):
    """Recompute the pre-registered liquidity ranking over [start, start + RANK_DAYS)."""
    lo, hi = pd.Timestamp(start, tz='UTC'), pd.Timestamp(start + timedelta(days=RANK_DAYS), tz='UTC')
    t_end = pd.Timestamp(end, tz='UTC')
    rows = []
    for sym in all_hk_symbols():
        df = storage.load_data(sym.replace('.', '_'), '1h')
        full = df.index.min() <= lo + pd.Timedelta(days=7) and df.index.max() >= t_end
        s = df[(df.index >= lo) & (df.index < hi) & (df['volume'] > 0)]
        rows.append({'symbol': sym, 'median_turnover_hkd_m': float((s['close'] * s['volume']).median() / 1e6)
                     if len(s) else float('nan'), 'full_coverage': bool(full)})
    r = pd.DataFrame(rows).sort_values('median_turnover_hkd_m', ascending=False)
    top = r[r['full_coverage']].head(10)['symbol'].tolist()
    return top, r.head(15).to_dict('records')


def data_checks(storage, symbols):
    """What looked wrong, or might, in the cache for these names."""
    out = {}
    bands = [(0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05), (200, 0.1), (500, 0.2),
             (1000, 0.5), (2000, 1), (5000, 2), (None, 5)]

    def on_tick(p):
        for upto, tick in bands:
            if upto is None or p < upto:
                return abs(p / tick - round(p / tick)) < 1e-4
        return False

    for sym in symbols:
        df = storage.load_data(sym.replace('.', '_'), '1h')
        d = df.index.normalize()
        per_day = pd.Series(1, index=df.index).groupby(d).sum()
        last_bar = pd.Series(df.index.strftime('%H:%M'), index=df.index).groupby(d).last()
        odd_days = [str(x.date()) for x, t in last_bar.items()
                    if not (t == '16:00' or (t == '12:00' and x.date() in HALF_DAYS))]
        half_in_data = sorted(str(x.date()) for x, t in last_bar.items() if t == '12:00')
        zero = df[df['volume'] <= 0]
        early, late = df['close'].iloc[:200], df['close'].iloc[-200:]
        rets = df['close'].pct_change().abs()
        out[sym] = {
            'bars': len(df), 'first': str(df.index.min()), 'last': str(df.index.max()),
            'bars_per_day': {int(k): int(v) for k, v in per_day.value_counts().items()},
            'half_days_in_data': half_in_data,
            'days_ending_off_schedule': odd_days,
            'zero_volume_bars': [str(t) for t in zero.index][:12],
            'n_zero_volume_bars': int(len(zero)),
            'share_on_tick_first_200': float(np.mean([on_tick(p) for p in early])),
            'share_on_tick_last_200': float(np.mean([on_tick(p) for p in late])),
            'hourly_moves_over_10pct': [str(t) for t in rets[rets > 0.10].index],
        }
    return out


# ─── Aggregation ─────────────────────────────────────────────────────────────────────────────

def summarise(results):
    """One record per (candidate, symbol, config, rate, harness)."""
    out = {}
    for r in results:
        ws = r['windows']
        rets = [w['test_return'] for w in ws]
        key = (r['candidate'], r['symbol'], r['config'], r['rate'], r['harness'])
        out[key] = {
            'mean_oos': float(np.mean(rets)) if rets else float('nan'),
            'consistency': (sum(1 for x in rets if x > 0) / len(rets) * 100) if rets else 0.0,
            'windows': len(rets), 'skipped': r['skipped_windows'],
            'trades_per_window': float(np.mean([w['test_trades'] for w in ws])) if ws else 0.0,
            'zero_trade_windows': sum(1 for w in ws if w['test_trades'] == 0),
            'mean_bh': float(np.mean([w['bh_return'] for w in ws])) if ws else float('nan'),
            'mean_norm': float(np.mean([w['norm_return'] for w in ws])) if ws else float('nan'),
            'mean_norm_excess': float(np.mean([w['norm_excess'] for w in ws])) if ws else float('nan'),
            'pct_excess_pos': (sum(1 for w in ws if w['norm_excess'] > 0) / len(ws) * 100) if ws else 0.0,
            'oos_returns': rets, 'seconds': r['seconds'], 'engine': r['engine'],
        }
    return out


def candidate_table(S, candidates):
    rows = []
    for name, sym in candidates:
        def g(cfg, rate=RATE, harness='standard', field='mean_oos'):
            v = S.get((name, sym, cfg, rate, harness))
            return None if v is None else v[field]
        a, ac = g('A'), g('A', field='consistency')
        b, bc = g('B'), g('B', field='consistency')
        passed = None not in (a, ac, b, bc) and qualifies(a, ac) and qualifies(b, bc)
        sa, sac = g('A', RATE_SENS), g('A', RATE_SENS, field='consistency')
        sb, sbc = g('B', RATE_SENS), g('B', RATE_SENS, field='consistency')
        row = {
            'candidate': name, 'symbol': sym, 'name': NAMES.get(sym, ''),
            'grid_points': len(grid_of(name)),
            'A_mean_oos_pct': a, 'A_pct_windows_profitable': ac,
            'B_mean_oos_pct': b, 'B_pct_windows_profitable': bc,
            'PASS': passed,
            'A_trades_per_window': g('A', field='trades_per_window'),
            'B_trades_per_window': g('B', field='trades_per_window'),
            'A_zero_trade_windows': g('A', field='zero_trade_windows'),
            'B_zero_trade_windows': g('B', field='zero_trade_windows'),
            'A_mean_bh_pct': g('A', field='mean_bh'), 'B_mean_bh_pct': g('B', field='mean_bh'),
            'A_mean_norm_pct': g('A', field='mean_norm'), 'B_mean_norm_pct': g('B', field='mean_norm'),
            'A_mean_norm_excess_pct': g('A', field='mean_norm_excess'),
            'B_mean_norm_excess_pct': g('B', field='mean_norm_excess'),
            'A_pct_windows_excess_pos': g('A', field='pct_excess_pos'),
            'B_pct_windows_excess_pos': g('B', field='pct_excess_pos'),
            'A_mean_oos_pct_at_0.08': sa, 'A_pct_profitable_at_0.08': sac,
            'B_mean_oos_pct_at_0.08': sb, 'B_pct_profitable_at_0.08': sbc,
            'would_pass_at_0.08': None not in (sa, sac, sb, sbc) and qualifies(sa, sac) and qualifies(sb, sbc),
        }
        wa, wac = g('A', RATE, 'warmup'), g('A', RATE, 'warmup', 'consistency')
        wb, wbc = g('B', RATE, 'warmup'), g('B', RATE, 'warmup', 'consistency')
        row.update({
            'DIAG_warmup_A_mean_oos_pct': wa, 'DIAG_warmup_A_pct_profitable': wac,
            'DIAG_warmup_B_mean_oos_pct': wb, 'DIAG_warmup_B_pct_profitable': wbc,
            'DIAG_warmup_A_trades_per_window': g('A', RATE, 'warmup', 'trades_per_window'),
            'DIAG_warmup_B_trades_per_window': g('B', RATE, 'warmup', 'trades_per_window'),
            'DIAG_warmup_would_meet_rule': (None not in (wa, wac, wb, wbc)
                                            and qualifies(wa, wac) and qualifies(wb, wbc)),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def ew_view(results, symbols, rate=RATE, harness='standard'):
    """Per strategy and config: mean over the names of the normalised return vs the equal-weight
    buy-and-hold of the same names, window by window."""
    by = {}
    for r in results:
        if r['rate'] != rate or r['harness'] != harness or r['symbol'] not in symbols:
            continue
        for w in r['windows']:
            by.setdefault((r['candidate'], r['config']), {}).setdefault(w['window'], []).append(w)
    out = {}
    for (name, cfg), wins in by.items():
        per = []
        for wid, ws in sorted(wins.items()):
            if len(ws) != len(symbols):
                continue
            s = float(np.mean([w['norm_return'] for w in ws]))
            b = float(np.mean([w['bh_return'] for w in ws]))
            raw = float(np.mean([w['test_return'] for w in ws]))
            per.append({'window': wid, 'strategy_norm': s, 'ew_bh': b, 'excess': s - b, 'strategy_raw': raw})
        if per:
            out[f'{name} | {cfg}'] = {
                'windows': len(per),
                'mean_strategy_raw_pct': float(np.mean([p['strategy_raw'] for p in per])),
                'mean_strategy_norm_pct': float(np.mean([p['strategy_norm'] for p in per])),
                'mean_ew_bh_pct': float(np.mean([p['ew_bh'] for p in per])),
                'mean_excess_pct': float(np.mean([p['excess'] for p in per])),
                'pct_windows_excess_pos': sum(1 for p in per if p['excess'] > 0) / len(per) * 100,
                'per_window': per,
            }
    return out


# ─── Main ────────────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='HK single-stock family (pre-registered)')
    ap.add_argument('--smoke', action='store_true', help='2 symbols, 0.0016 only, no ML: a bug check')
    ap.add_argument('--jobs', type=int, default=MAX_JOBS)
    ap.add_argument('--out-dir', default=None, help='default: results/')
    args = ap.parse_args()
    jobs = max(1, min(MAX_JOBS, args.jobs))
    # Results go to results/hk_search/, outside the results index (midfreq.research.results_index reads
    # results/*.csv only): the record of 217 results the report, the dashboard and the demo cite stays as
    # it is until the amended report decides how the Hong Kong search enters it.
    out_dir = str(paths.results_dir() / 'hk_search') if args.out_dir is None else os.path.abspath(args.out_dir)
    t_all = time.time()

    storage = DataStorage()
    end = storage.latest_common_timestamp(UNIVERSE, Timeframe.HOUR_1.value)
    start = end - timedelta(days=HISTORY_DAYS)
    top, ranking = verify_universe(storage, start, end)
    if top != UNIVERSE:
        sys.exit(f"liquidity ranking does not reproduce the pre-registered universe: {top}")
    print(f"Universe verified ({len(UNIVERSE)} names) | window {start} -> {end} | "
          f"{RATE:.4f}/side (+{RATE_SENS:.4f} line) + {SLIPPAGE_BPS} bps | jobs {jobs}", flush=True)

    symbols = UNIVERSE[:2] if args.smoke else UNIVERSE
    rates = [RATE] if args.smoke else [RATE, RATE_SENS]
    specs = []
    for name in CANDIDATES:
        for sym in symbols:
            for cfg, n in CONFIGS:
                for rate in rates:
                    specs.append((name, sym, cfg, n, rate, 'standard', start, end))
                if name in WARMUP_CANDIDATES:
                    specs.append((name, sym, cfg, n, RATE, 'warmup', start, end))
    if not args.smoke:
        for cfg, n in CONFIGS:
            for rate in rates:
                specs.append((ML_NAME, ML_SYMBOL, cfg, n, rate, 'standard', start, end))
            specs.append((ML_NAME, ML_SYMBOL, cfg, n, RATE, 'warmup', start, end))
    checks = [(name, UNIVERSE[0], 15, start, end) for name in
              ('SMA Crossover [slow third]', 'Regime Switch [slow third]', 'HK Opening-Gap Reversal')]
    # slow Python jobs first, so the pool does not end on one long straggler
    order = {ML_NAME: 0, 'MACD [slow third]': 1, 'HK Opening-Gap Reversal': 2}
    specs.sort(key=lambda s: (s[5] != 'warmup' and s[0] != ML_NAME, order.get(s[0], 3)))
    print(f"{len(specs)} walk-forwards + {len(checks)} harness checks", flush=True)

    results, check_out = [], []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(harness_check, c): ('check', c) for c in checks}
        futs.update({pool.submit(task, s): ('task', s) for s in specs})
        done = 0
        for f in as_completed(futs):
            kind, s = futs[f]
            r = f.result()
            if kind == 'check':
                check_out.append(r)
                print(f"  harness check {r['candidate']} {r['symbol']} B: identical={r['identical']} "
                      f"max diff {r['max_abs_diff']}", flush=True)
                continue
            results.append(r)
            done += 1
            rets = [w['test_return'] for w in r['windows']]
            print(f"  [{done:3d}/{len(specs)}] {r['candidate']:38s} {r['symbol']} {r['config']} "
                  f"{r['rate']:.4f} {r['harness']:8s} mean {np.mean(rets):+7.3f}% "
                  f"pos {sum(x > 0 for x in rets)}/{len(rets)} "
                  f"trades/w {np.mean([w['test_trades'] for w in r['windows']]):5.1f} "
                  f"| {r['seconds']:.0f}s {r['engine']}", flush=True)
    t_wf = time.time() - t_all

    S = summarise(results)
    cand_list = [(n, s) for n in CANDIDATES for s in symbols]
    if not args.smoke:
        cand_list.append((ML_NAME, ML_SYMBOL))
    table = candidate_table(S, cand_list)
    passed = table[table['PASS'] == True]  # noqa: E712

    # Reality Check over the whole family, if anything passed
    rc = {}
    if len(passed):
        for cfg, _ in CONFIGS:
            use = {f"{n} x {s}": S[(n, s, cfg, RATE, 'standard')]['oos_returns'] for n, s in cand_list
                   if (n, s, cfg, RATE, 'standard') in S}
            lens = {len(v) for v in use.values()}
            if len(lens) != 1:
                n_use = max(lens, key=lambda L: sum(len(v) == L for v in use.values()))
                use = {k: v for k, v in use.items() if len(v) == n_use}
            res = reality_check(use, n_boot=N_BOOT)
            res['naive_p_value_of_best'] = naive_single_test(use[res['best_candidate']], n_boot=N_BOOT)
            res['p_value_for_each_passing'] = {}
            for _, row in passed.iterrows():
                k = f"{row['candidate']} x {row['symbol']}"
                res['p_value_for_each_passing'][k] = {
                    'naive_p': naive_single_test(use[k], n_boot=N_BOOT) if k in use else None}
            rc[cfg] = res

    pickers = [picker(row['candidate'], row['symbol']) for _, row in passed.iterrows()]
    ew = {'standard_0.0016': ew_view(results, symbols, RATE, 'standard'),
          'standard_0.0008': ew_view(results, symbols, RATE_SENS, 'standard'),
          'warmup_0.0016': ew_view(results, symbols, RATE, 'warmup')}

    n_primary = sum(len(grid_of(n)) for n in CANDIDATES) * len(symbols) + (0 if args.smoke else len(grid_of(ML_NAME)))
    n_diag = sum(len(grid_of(n)) for n in WARMUP_CANDIDATES if n in CANDIDATES) * len(symbols) + \
        (0 if args.smoke else len(grid_of(ML_NAME)))

    stamp = time.strftime('%Y%m%d_%H%M%S')
    tag = 'hk_single_stock_smoke' if args.smoke else 'hk_single_stock'
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f'{tag}_{stamp}.csv')
    win_path = os.path.join(out_dir, f'{tag}_windows_{stamp}.csv')
    json_path = os.path.join(out_dir, f'{tag}_{stamp}.json')
    table.to_csv(csv_path, index=False)
    pd.DataFrame([{**{k: r[k] for k in ('candidate', 'symbol', 'config', 'rate', 'harness', 'engine')}, **w}
                  for r in results for w in r['windows']]).to_csv(win_path, index=False)

    meta = {
        'universe': UNIVERSE, 'ml_symbol': ML_SYMBOL, 'ranking_first_51_days': ranking,
        'start': str(start), 'end': str(end), 'configs': CONFIGS, 'train_pct': TRAIN_PCT,
        'objective': OBJECTIVE, 'commission_per_side': RATE, 'sensitivity_commission_per_side': RATE_SENS,
        'slippage_bps': SLIPPAGE_BPS, 'capital': CAPITAL, 'qty_per_entry': DEFAULT_QTY,
        'warmup_bars_diagnostic': WARMUP_BARS,
        'grids': {n: grid_of(n) for n in list(CANDIDATES) + [ML_NAME]},
        'half_days_used': sorted(str(d) for d in HALF_DAYS),
        'configurations_searched_primary': n_primary, 'configurations_searched_diagnostic': n_diag,
        'configurations_searched_total': n_primary + n_diag,
        'walk_forward_candidates': len(cand_list),
        'smoke': args.smoke,
    }
    blob = {
        'meta': meta,
        'harness_check': check_out,
        'candidates': table.to_dict('records'),
        'passed': passed[['candidate', 'symbol']].to_dict('records'),
        'reality_check': rc or 'not run: no candidate passed',
        'live_settings': pickers,
        'alpha_equal_weight_view': ew,
        'per_run': [{**{k: v for k, v in r.items() if k != 'windows'}} for r in results],
        'data_checks': data_checks(storage, symbols + ([] if args.smoke else [ML_SYMBOL])),
        'runtime_seconds': {'walk_forwards': round(t_wf, 1), 'total': round(time.time() - t_all, 1),
                            'sum_of_task_seconds': round(sum(r['seconds'] for r in results), 1)},
    }
    with open(json_path, 'w') as fh:
        json.dump(blob, fh, indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))

    pd.set_option('display.width', 250)
    cols = ['candidate', 'symbol', 'A_mean_oos_pct', 'A_pct_windows_profitable', 'B_mean_oos_pct',
            'B_pct_windows_profitable', 'PASS', 'A_trades_per_window', 'B_trades_per_window']
    print('\n' + table[cols].round(3).to_string(index=False))
    print(f"\n{int(table['PASS'].sum())}/{len(table)} candidates pass | configurations searched: "
          f"{n_primary} primary + {n_diag} diagnostic = {n_primary + n_diag}")
    if rc:
        for cfg, r in rc.items():
            print(f"  Reality Check {cfg}: p = {r['p_value']:.4f} (best {r['best_candidate']})")
    print(f"\nwrote {csv_path}\n      {win_path}\n      {json_path}\n"
          f"total {(time.time() - t_all) / 60:.1f} min")


if __name__ == '__main__':
    main()
