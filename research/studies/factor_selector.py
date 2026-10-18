"""
Factor ETFs: which strategy family fits each ETF, chosen per walk-forward window (adaptive selector)
====================================================================================================

PRE-REGISTRATION. Written and saved on 18 Oct 2026, before any backtest of the candidates below
was run. Looked at beforehand: data spans, bar counts, the window boundaries (pure date
arithmetic), and whether the closes are dividend-adjusted. Nothing below may be added to or tuned
after the results are seen; anything tried afterwards goes in a labelled exploratory section,
counts toward the search total, and is never called a pass.

Question
--------
"Which strategy best applies to this ETF AT THIS POINT IN TIME?", asked honestly. For the seven
factor ETFs of the NUSSIF equity-factor project (MTUM, QUAL, VLUE, USMV, IWF, IWD, SPHD), in every
walk-forward window choose ONE family from a fixed menu using the training segment only, trade the
choice unchanged on the test segment, and see whether choosing beats (a) any one family used
always, (b) simply holding the ETF, and how close it gets to (c) the hindsight-best family.
This extends research/studies/model_selector_research.py (which chose between two families,
Regime Switch and HMM, per window by training Sharpe) to a four-family menu that includes holding.

Data
----
data/US_<T>/1d.parquet, read through DataStorage. Common span of the seven ETFs:
2013-07-18 (QUAL's first bar) -> 2026-10-16 (DataStorage.latest_common_timestamp), 3,333 daily
bars each. SPY (data/US_SPY/1d.parquet, from 2000-01-03) is used only for the market state.
Closes are split- AND dividend-adjusted: the project's YFinanceProvider calls yf.download without
an auto_adjust argument, and the installed yfinance (1.2.0) defaults to auto_adjust=True. Evidence:
SPY's cached close on 2000-01-03 is 90.91 against its traded close of 145.44 (SPY has never split,
so the 0.625 factor is 26 years of reinvested dividends). Buy-and-hold here is therefore a
total-return comparison, which is the right one. The script re-checks the spans, missing values
and date alignment and stores them under data_checks.

Menu (fixed). The reversal member is RSI, not Bollinger Bands, because Regime Switch already
trades a Bollinger leg as its range-bound half; RSI keeps the reversal member a distinct signal.
Each family has its own small DAILY grid. The registry ranges were built for hourly bars (SMA slow
up to 200, RSI period up to 50); on daily bars walk_forward starts every test segment cold, and a
test segment is ~111 trading days (config A) or ~67 (config B), so every look-back below is capped
so the strategy can warm up inside the shorter segment (longest warm-up: 41 bars).

  1. Trend      SMA Crossover    fast_period in {3, 5, 10} x slow_period in {20, 30, 40}       =  9
  2. Reversal   RSI              rsi_period in {2, 5, 14} x oversold in {15, 30}
                                 x overbought in {70, 85}                                     = 12
  3. Regime     Regime Switch    regime_lookback in {10, 20, 30} x er_threshold in {0.2, 0.3, 0.4}
                                 (inner legs at the class defaults: SMA 5/20 trend leg,
                                 Bollinger 14 / 2.5 sigma range leg)                         =  9
  4. Hold       Buy & Hold       BuyAndHoldFull below: buy at the first bar, hold; no parameters =  1

Per ETF 31 configurations; 7 ETFs -> 217 configurations in the primary search.

Sizing. The project's single-stock convention (a flat 100 shares) would make the invested fraction
depend on the ETF's price (SPHD ~$50 vs IWF ~$450), which breaks every comparison with holding.
Every family therefore runs with a plain RiskManager whose only job is sizing: EqualDollarSizer
(n_positions=1, cost_buffer=0.001), i.e. 99.9% of equity in each entry, no stops, and the
drawdown circuit breaker disabled (max_drawdown_pct=1.0). Capital $1,000,000 (integer-share
rounding < 0.05%). The C++ engine supports this risk manager, so SMA, RSI and Regime Switch run
natively; BuyAndHoldFull has no port and runs on the Python backtester (identical semantics).

Validation standard (the project's, unchanged)
----------------------------------------------
* midfreq.backtest.optimizer.walk_forward, called verbatim on DAILY bars over the common span;
  generate_param_grid is patched in-process to return the grids above for the families' names
  (as research/studies/hk_single_stock.py does). Config A: 9 windows (537 days; train 375, test
  162 calendar days). Config B: 15 windows (322 days; train 225, test 97). train_pct 0.7.
* Each window: grid-search the training segment by Sharpe ratio (settings that traded rank
  first), trade the chosen setting unchanged on the test segment.
* Costs: commission 0.00005 per side (moomoo US, calibrated) + slippage 2 bps per side. Cost line:
  the whole walk-forward re-run at 5 bps slippage.

Candidates (per ETF), all from the same windows
-----------------------------------------------
  * the four families, each used always (its own walk-forward);
  * Selector: in each window, the family whose chosen setting had the highest TRAINING Sharpe
    (train_metrics['sharpe_ratio'] from walk_forward); that family's test return is the
    selector's. Ties go to the earlier family in the order Buy & Hold, SMA Crossover, RSI,
    Regime Switch. A family whose best training setting never traded scores the backtester's
    Sharpe for a flat curve (0) and stays eligible;
  * Selector-R (regime-conditioned, the out-of-sample test of part 2): in window k, take the
    earlier windows j < k of the same configuration whose test segment started in the same SPY
    state as window k's (states below). If there are at least 2, pick the family with the
    highest MEAN TEST return over those windows (all of them ended before window k's test
    starts); otherwise use the Selector's pick. Same tie order.
  * Oracle (hindsight, NOT a candidate, never in a test): the family with the highest test return
    in each window. It bounds what selection could add.
Daily candidates: 6 per ETF x 7 = 42, each under both configurations.

Acceptance rules (2 bps, standard walk-forward)
-----------------------------------------------
* PASS: under BOTH configurations, mean test return per window > 0 AND >= 50% of windows > 0.
* Excess over buy-and-hold, per window: candidate test return minus the ETF's frictionless
  buy-and-hold over the same test segment (first close to last close; walk_forward's
  benchmark_return_pct). "Beats holding": under BOTH configurations mean excess > 0 AND >= 50% of
  windows with excess > 0.
* "Selection adds value over a fixed family" (model_selector_research's selector_beats_both,
  widened): under BOTH configurations the Selector's mean test return exceeds that of each of the
  four families used always, including Buy & Hold.
* "Worth trading instead of holding": PASS and "beats holding" and the excess Reality Check
  (below) p < 0.05 under both configurations.
* Selector diagnostics: share of windows where the Selector's pick is the Oracle's, against the
  chance rate sum_f P(Selector picks f) x P(Oracle is f) of the same ETF and configuration;
  capture = (Selector - Buy & Hold) / (Oracle - Buy & Hold), mean test returns.

Multiple testing (midfreq.research.reality_check; stationary bootstrap, mean block 2,
5,000 resamples, seed 12345), separately for config A and config B, at 2 bps:
  RC-1 (the brief's): per-window RAW test returns of all 42 daily candidates. Buy & Hold is in it,
       so a low p here can just be the equity premium; it is not evidence of timing skill.
  RC-2 (the timing question): per-window EXCESS over buy-and-hold of the 35 candidates that are
       not Buy & Hold (SMA, RSI, Regime Switch, Selector, Selector-R per ETF).
The naive single-test p of each RC's best candidate is reported beside it.

Risk view (2 bps, standard walk-forward), per candidate and for buy-and-hold
------------------------------------------------------------------------------
* best and worst test window, per configuration;
* max drawdown and CVaR 5% (mean of the worst 5% of daily returns) of the stitched out-of-sample
  path: the test segments' daily returns chained (each segment starts from capital, so a
  segment's first daily return includes its entry cost);
* share of windows that beat buy-and-hold;
* named stress periods, close to close: COVID crash 2020-02-19 -> 2020-03-23; 2022 rate shock
  2021-12-31 -> 2022-10-31 (Jan-Oct); Apr 2025 tariff shock 2025-04-02 -> 2025-04-08. Most of
  these fall in TRAINING segments (config A and B test segments cover none of the COVID crash,
  part of 2022 and, in A only, April 2025), so each is measured by a STRESS REPLAY: the setting
  (and, for the selectors, the family) chosen in the latest window whose training segment ended
  on or before the stress start, run with no re-optimisation from that window's test start
  through the stress end; the figure is the equity change over the stress dates. For Buy & Hold
  this is the ETF's own move. Reported per configuration.
* Also, descriptively, each ETF's own full-span buy-and-hold: total return, CAGR, max drawdown,
  CVaR 5%, best and worst rolling 252-day return, and the three stress moves.

Part 2: regime conditioning (states fixed here)
-----------------------------------------------
SPY state at each window's TEST START, from SPY daily closes strictly before the test start date:
  * trend: UP if the last close > its 200-day simple average, else DOWN;
  * volatility: HIGH if the 63-day realised volatility (std of the last 63 daily log returns x
    sqrt 252) > the median of that same series from SPY's first available value (Apr 2000) up to
    that day (an expanding, point-in-time median), else LOW.
Four states: UP/LOW, UP/HIGH, DOWN/LOW, DOWN/HIGH. Tabulated per ETF and configuration: the
Oracle family in each window, counted by state; and, pooled, each family's mean test return by
state (descriptive). Whether conditioning on the state helps out of sample is Selector-R above.

Part 3: "as of 2026-10-16" readout (DESCRIPTIVE ONLY; not a recommendation)
--------------------------------------------------------------------------
Per ETF, and per configuration length (training segment = the last 375 / 225 calendar days
through the 2026-10-16 close, inclusive):
  * each family's setting chosen by the same rule on that segment (BatchRunner, end inclusive),
    its training Sharpe, and its current signal: the position (LONG / FLAT) that setting holds at
    the 2026-10-16 close when run over that segment, with its indicator state (SMA fast vs slow,
    RSI value, Regime Switch's efficiency ratio and leg);
  * the family the Selector would pick now (highest training Sharpe; same tie order);
  * SPY's state on 2026-10-16 (same definitions), and, over the past test windows (A and B) that
    started in that same state, each candidate's best, worst and mean test return.
The readout carries the multiple-testing caveat: it is the output of a 42-candidate search, and
nothing in it has passed the bar for trading.

Secondary analyses, pre-registered (never a pass)
-------------------------------------------------
* WARM-UP DIAGNOSTIC. walk_forward starts every segment cold, which handicaps the timing families
  against Buy & Hold (invested from bar 1) by up to 41 of config B's ~67 test bars. The same
  walk-forward (same windows, rule, costs at 2 bps) is repeated with WARM-UP: before every train
  and test segment the strategy is fed the 60 preceding bars, observing but unable to trade. Its
  harness reproduces walk_forward exactly when the warm-up is zero (harness_check in the JSON).
  217 configurations re-evaluated.
* HOURLY CROSS-CHECK (mid-frequency). The same four families, the same grids read as hourly bars,
  the Selector (not Selector-R: too few windows for states), 2 bps, configs A and B on
  data/US_<T>/1h.parquet over the common hourly span 2024-10-21 -> 2026-10-16 (true UTC bars,
  7 per session). 217 configurations, 35 candidates. Reported for whether the daily conclusion
  holds; no pass, no Reality Check.

Search size: 217 daily configurations (42 candidates) in the primary search; 217 more in the
warm-up diagnostic and 217 in the hourly cross-check: 651 configuration evaluations in all (the
5 bps cost line re-runs the primary 217 at a different cost and adds no new configuration).

Literature, checked against the results in the report: factor momentum (Gupta & Kelly 2019,
"Factor Momentum Everywhere"; Ehsani & Linnainmaa 2022, "Factor Momentum and the Momentum Factor")
predicts that factors' own recent returns persist over 1-12 months, which would favour the trend
family; Asness, Chandra, Ilmanen & Israel 2017 ("Contrarian Factor Timing is Deceptively
Difficult") predicts that timing factor exposure adds little beyond holding. The horizons tested
here (days to a few weeks) are shorter than the factor-momentum literature's.

Outputs
-------
results/factor_search/factor_selector_<stamp>.csv           one row per ETF x candidate
results/factor_search/factor_selector_windows_<stamp>.csv   one row per ETF x candidate x config
                                                            x cost x harness x timeframe x window
results/factor_search/factor_selector_<stamp>.json          everything else

Usage (laptop: niced, at most 3 worker processes):
    nice -n 10 uv run python research/studies/factor_selector.py --smoke --out-dir <scratch>
    nice -n 10 uv run python research/studies/factor_selector.py
"""
import argparse
import contextlib
import io
import itertools
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
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
from midfreq.core.risk_manager import EqualDollarSizer, RiskManager
from midfreq.data.storage import DataStorage
from midfreq.backtest import optimizer
from midfreq.backtest.optimizer import walk_forward, _get_objective_value
from midfreq.backtest.backtester import PythonBacktester
from midfreq.backtest.native import BatchRunner
from midfreq.strategies.candle_strategies import (BaseStrategy, STRATEGY_REGISTRY, MovingAverageCrossover,
                                                  RSIStrategy, RegimeSwitchStrategy)
from midfreq.research.reality_check import reality_check, naive_single_test

REPO = paths.repo_root()

# ─── Pre-registered constants ────────────────────────────────────────────────────────────────
ETFS = ['MTUM', 'QUAL', 'VLUE', 'USMV', 'IWF', 'IWD', 'SPHD']
SMOKE_ETFS = ['MTUM', 'USMV']
MARKET = 'SPY'
CONFIGS = [('A', 9), ('B', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
RATE = 0.00005                 # moomoo US, per side
SLIP = 2.0                     # bps per side, decides the pass
SLIP_SENS = 5.0                # cost line only
CAPITAL = 1_000_000.0
CASH_BUFFER = 0.001
WARMUP_BARS = 60
MIN_PRIOR = 2                  # Selector-R: same-state earlier windows needed
N_BOOT = 5000
MAX_JOBS = 3
AS_OF = datetime(2026, 10, 16)
STRESS = [('COVID crash', '2020-02-19', '2020-03-23'),
          ('2022 rate shock', '2021-12-31', '2022-10-31'),
          ('Apr 2025 tariff shock', '2025-04-02', '2025-04-08')]
STATES = ['UP/LOW', 'UP/HIGH', 'DOWN/LOW', 'DOWN/HIGH']


def _grid(**axes) -> List[Dict[str, Any]]:
    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(axes[k] for k in keys))]


class BuyAndHoldFull(BaseStrategy):
    """
    Buy at the first bar it can and hold to the end of the segment, sized by the risk manager
    (99.9% of equity here). Never sells. While a warm-up suppresses orders the position stays
    flat, so it buys at the first bar it is allowed to trade. Prototype for
    research/studies/factor_selector.py; not part of the packages.
    """

    def __init__(self, portfolio: Portfolio, risk_manager: Optional[RiskManager] = None, **kwargs):
        super().__init__(portfolio, risk_manager=risk_manager)

    def on_start(self):
        print("Starting Buy & Hold (full)")

    def on_data(self, symbol: str, candle: Candle):
        if self.portfolio.get_position_qty(symbol) > 0:
            return
        qty = self._get_trade_qty(symbol, candle.close)
        if qty > 0:
            self.portfolio.execute_trade(symbol, True, qty, candle.close, candle.timestamp)


FAMILY_ORDER = ['Buy & Hold', 'SMA Crossover', 'RSI', 'Regime Switch']   # also the tie order
FAMILIES = {
    'Buy & Hold': {'registry': 'Buy & Hold [factor selector]', 'class': BuyAndHoldFull,
                   'grid': [{}], 'warmup_bars': 1},
    'SMA Crossover': {'registry': 'SMA Crossover [factor selector daily]', 'class': MovingAverageCrossover,
                      'grid': _grid(fast_period=[3, 5, 10], slow_period=[20, 30, 40]), 'warmup_bars': 41},
    'RSI': {'registry': 'RSI [factor selector daily]', 'class': RSIStrategy,
            'grid': _grid(rsi_period=[2, 5, 14], oversold=[15, 30], overbought=[70, 85]), 'warmup_bars': 15},
    'Regime Switch': {'registry': 'Regime Switch [factor selector daily]', 'class': RegimeSwitchStrategy,
                      'grid': _grid(regime_lookback=[10, 20, 30], er_threshold=[0.2, 0.3, 0.4]),
                      'warmup_bars': 31},
}
SELECTORS = ['Selector', 'Selector-R']
CANDIDATES = FAMILY_ORDER + SELECTORS
assert [len(FAMILIES[f]['grid']) for f in FAMILY_ORDER] == [1, 9, 12, 9]
N_CONFIGS_PER_ETF = sum(len(FAMILIES[f]['grid']) for f in FAMILY_ORDER)     # 31

_original_generate_param_grid = optimizer.generate_param_grid
_GRIDS = {FAMILIES[f]['registry']: FAMILIES[f]['grid'] for f in FAMILY_ORDER}


def _generate_param_grid(strategy_name: str):
    if strategy_name in _GRIDS:
        return [dict(p) for p in _GRIDS[strategy_name]]
    return _original_generate_param_grid(strategy_name)


def register_families():
    """In-process only: name the families in the registry and give walk_forward their grids."""
    for f in FAMILY_ORDER:
        STRATEGY_REGISTRY.setdefault(FAMILIES[f]['registry'], {'class': FAMILIES[f]['class'], 'params': {}})
    optimizer.generate_param_grid = _generate_param_grid


register_families()


def sizing_rm() -> RiskManager:
    """Sizing only: 99.9% of equity per entry, no stops, circuit breaker off."""
    return RiskManager(max_drawdown_pct=1.0,
                       position_sizer=EqualDollarSizer(n_positions=1, cost_buffer=CASH_BUFFER))


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def tf_of(name: str) -> Timeframe:
    return Timeframe.DAY_1 if name == '1d' else Timeframe.HOUR_1


def sym_of(etf: str) -> str:
    return f'US.{etf}'


# ─── Bars and the path harness ───────────────────────────────────────────────────────────────

class Bars:
    """One symbol's bars, with the Candle objects built once."""

    def __init__(self, storage: DataStorage, etf: str, tf: str):
        df = storage.load_data(f'US_{etf}', tf)
        self.symbol = sym_of(etf)
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


def run_path(cls, params, bars: Bars, lo, hi, warm, slip, rate=RATE):
    """PythonBacktester.run over bars[lo:hi] with the sizing risk manager, after feeding
    bars[lo-warm:lo] to the strategy with trading disabled. With warm=0 this is
    PythonBacktester.run (checked against walk_forward: path_check, harness_check).
    Returns (metrics, equity points [(ts, equity)], portfolio, strategy)."""
    if hi <= lo:
        return {}, [], None, None
    pf = Portfolio(initial_cash=CAPITAL, commission_rate=rate)
    rm = sizing_rm()
    bt = PythonBacktester(storage=None, portfolio=pf, risk_manager=rm, slippage_bps=slip)
    sym = bars.symbol
    with quiet():
        strategy = cls(pf, risk_manager=rm, **params)
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
        m['alpha'] = m['return_pct'] - bench
    eq = [(e['timestamp'], e['equity']) for e in pf.equity_curve_detailed]
    return m, eq, pf, strategy


def window_bounds(start, end, n_splits):
    """walk_forward's window arithmetic."""
    ws = (end - start).days // n_splits
    out = []
    for w in range(n_splits):
        w_start = start + timedelta(days=w * ws)
        w_end = w_start + timedelta(days=ws)
        train_end = w_start + timedelta(days=int(ws * TRAIN_PCT))
        out.append((w + 1, w_start, train_end, train_end, w_end))
    return out


def warm_walk_forward(fam, bars: Bars, start, end, n_splits, slip, warm):
    """optimizer.walk_forward's windows, skip rules and selection rule, on run_path."""
    cls, grid = FAMILIES[fam]['class'], FAMILIES[fam]['grid']
    windows = []
    for wid, train_start, train_end, test_start, test_end in window_bounds(start, end, n_splits):
        la = bars.last_available
        if test_start < la < test_end:
            if (la - test_start).days / max(1, (test_end - test_start).days) < 0.5:
                continue
        elif test_start >= la:
            continue
        lo, hi = bars.bounds(train_start, train_end, end_inclusive=False)
        best_obj, best_params, best_train, best_traded = float('-inf'), {}, {}, False
        for params in grid:
            m = run_path(cls, params, bars, lo, hi, warm, slip)[0]
            if m:
                obj = _get_objective_value(m, OBJECTIVE)
                traded = m.get('total_trades', 0) > 0
                if (traded, obj) > (best_traded, best_obj):
                    best_obj, best_params, best_train, best_traded = obj, dict(params), dict(m), traded
        lo, hi = bars.bounds(test_start, test_end, end_inclusive=True)
        tm = run_path(cls, best_params, bars, lo, hi, warm, slip)[0] or \
            {'return_pct': 0.0, 'sharpe_ratio': 0.0, 'max_drawdown': 0.0}
        windows.append(optimizer.WalkForwardWindow(wid, train_start, train_end, test_start, test_end,
                                                   best_params, best_train, tm))
    return {'windows': windows}


def window_rows(res, bars: Bars):
    rows = []
    for w in res['windows']:
        tm, trm = w.test_metrics, w.train_metrics
        lo, hi = bars.bounds(w.test_start, w.test_end, end_inclusive=True)
        p0, p1 = bars.close[lo], bars.close[hi - 1]
        bench = (p1 / p0 - 1.0) * 100.0
        ret = float(tm.get('return_pct', 0.0))
        rows.append({
            'window': w.window_id, 'train_start': str(w.train_start.date()),
            'test_start': str(w.test_start.date()), 'test_end': str(w.test_end.date()),
            'test_bars': hi - lo, 'params': w.best_params,
            'train_sharpe': float(trm.get('sharpe_ratio', 0.0)) if trm else 0.0,
            'train_return': float(trm.get('return_pct', 0.0)) if trm else 0.0,
            'train_trades': int(trm.get('total_trades', 0)) if trm else 0,
            'test_return': ret, 'test_trades': int(tm.get('total_trades', 0)),
            'test_sharpe': float(tm.get('sharpe_ratio', 0.0)),
            'test_max_dd': float(tm.get('max_drawdown', 0.0)),
            'bh_return': bench,
            'bh_return_backtester': tm.get('benchmark_return_pct'),
            'excess': ret - bench,
        })
    return rows


# ─── Worker tasks ────────────────────────────────────────────────────────────────────────────

def wf_task(spec):
    """One family's walk-forward on one ETF: standard (walk_forward verbatim) or warm-up."""
    fam, etf, cfg, n_splits, slip, tf, harness, start, end = spec
    storage = DataStorage()
    t0 = time.time()
    bars = Bars(storage, etf, tf)
    cls = FAMILIES[fam]['class']
    if harness == 'standard':
        with quiet():
            res = walk_forward(strategy_name=FAMILIES[fam]['registry'], symbols=[sym_of(etf)],
                               timeframe=tf_of(tf), start_date=start, end_date=end, storage=storage,
                               n_splits=n_splits, train_pct=TRAIN_PCT, objective=OBJECTIVE,
                               initial_capital=CAPITAL, risk_manager=sizing_rm(), slippage_bps=slip,
                               commission_rate=RATE)
        engine = BatchRunner(None, cls, [sym_of(etf)], tf_of(tf), storage, CAPITAL, RATE, sizing_rm(),
                             slip).engine
    else:
        res = warm_walk_forward(fam, bars, start, end, n_splits, slip, WARMUP_BARS)
        engine = 'python (warm-up harness)'
    rows = window_rows(res, bars)
    out = {'family': fam, 'etf': etf, 'config': cfg, 'slip': slip, 'tf': tf, 'harness': harness,
           'engine': engine, 'seconds': round(time.time() - t0, 2),
           'skipped_windows': len(res.get('summary', {}).get('skipped_windows', [])), 'windows': rows}
    # Daily standard 2 bps: re-run every test segment on the path harness for the daily path
    # (risk view) and check it reproduces walk_forward; then the stress replays.
    if harness == 'standard' and tf == '1d' and slip == SLIP:
        paths_, diffs = [], []
        for r in rows:
            lo, hi = bars.bounds(pd.Timestamp(r['test_start']), pd.Timestamp(r['test_end']), True)
            m, eq, _, _ = run_path(cls, r['params'], bars, lo, hi, 0, slip)
            diffs.append(abs(m.get('return_pct', 0.0) - r['test_return']))
            paths_.append([[str(t.date()), float(e)] for t, e in eq])
        out['paths'] = paths_
        out['path_check_max_abs_diff'] = max(diffs) if diffs else None
        out['stress'] = stress_replays(cls, rows, bars, slip)
    return out


def stress_replays(cls, rows, bars: Bars, slip):
    """Per stress period: the setting of the latest window whose training ended on or before the
    stress start, run from that window's test start to the stress end; equity change over the
    stress dates."""
    out = {}
    for name, s0, s1 in STRESS:
        s0t, s1t = pd.Timestamp(s0), pd.Timestamp(s1)
        dep = [r for r in rows if pd.Timestamp(r['test_start']) <= s0t]
        if not dep:
            out[name] = None
            continue
        r = dep[-1]
        lo, hi = bars.bounds(pd.Timestamp(r['test_start']), s1t, True)
        m, eq, pf, _ = run_path(cls, r['params'], bars, lo, hi, 0, slip)
        e = {t.date(): v for t, v in eq}
        e0, e1 = e.get(s0t.date()), e.get(s1t.date())
        out[name] = {'deploy_window': r['window'], 'deploy_from': r['test_start'], 'params': r['params'],
                     'return_pct': (e1 / e0 - 1.0) * 100.0 if e0 and e1 else None}
    return out


def harness_check(spec):
    """warm_walk_forward with no warm-up must reproduce walk_forward exactly."""
    fam, etf, n_splits, start, end = spec
    storage = DataStorage()
    bars = Bars(storage, etf, '1d')
    with quiet():
        ref = walk_forward(strategy_name=FAMILIES[fam]['registry'], symbols=[sym_of(etf)],
                           timeframe=Timeframe.DAY_1, start_date=start, end_date=end, storage=storage,
                           n_splits=n_splits, train_pct=TRAIN_PCT, objective=OBJECTIVE,
                           initial_capital=CAPITAL, risk_manager=sizing_rm(), slippage_bps=SLIP,
                           commission_rate=RATE)
    mine = warm_walk_forward(fam, bars, start, end, n_splits, SLIP, 0)
    a = [w.test_metrics.get('return_pct', 0.0) for w in ref['windows']]
    b = [w.test_metrics.get('return_pct', 0.0) for w in mine['windows']]
    pa = [w.best_params for w in ref['windows']]
    pb = [w.best_params for w in mine['windows']]
    return {'family': fam, 'etf': etf, 'n_splits': n_splits,
            'same_params': pa == pb, 'windows': [len(a), len(b)],
            'max_abs_diff': max((abs(x - y) for x, y in zip(a, b)), default=0.0) if len(a) == len(b) else None}


def asof_task(spec):
    """Part 3: each family's setting on the training segment ending 2026-10-16, its signal now."""
    fam, etf, cfg, train_days = spec
    storage = DataStorage()
    bars = Bars(storage, etf, '1d')
    cls, grid = FAMILIES[fam]['class'], FAMILIES[fam]['grid']
    start = AS_OF - timedelta(days=train_days)
    runner = BatchRunner(None, cls, [sym_of(etf)], Timeframe.DAY_1, storage, CAPITAL, RATE, sizing_rm(), SLIP)
    with quiet():
        runs = runner.run(grid, start, AS_OF, end_inclusive=True)
    best_obj, best_params, best_m, best_traded = float('-inf'), {}, {}, False
    for params, (m, _) in zip(grid, runs):
        if m:
            obj = _get_objective_value(m, OBJECTIVE)
            traded = m.get('total_trades', 0) > 0
            if (traded, obj) > (best_traded, best_obj):
                best_obj, best_params, best_m, best_traded = obj, dict(params), dict(m), traded
    lo, hi = bars.bounds(start, AS_OF, True)
    m, eq, pf, strat = run_path(cls, best_params, bars, lo, hi, 0, SLIP)
    sym = bars.symbol
    pos = pf.get_position_qty(sym) if pf else 0
    ind: Dict[str, Any] = {}
    hist = getattr(strat, 'history', {}).get(sym, []) if strat else []
    if fam == 'SMA Crossover' and len(hist) >= strat.slow:
        fast = sum(hist[-strat.fast:]) / strat.fast
        slow = sum(hist[-strat.slow:]) / strat.slow
        ind = {'fast_ma': fast, 'slow_ma': slow, 'fast_above_slow': fast > slow}
    elif fam == 'RSI' and hist:
        ind = {'rsi': strat._compute_rsi(hist), 'oversold': strat.oversold, 'overbought': strat.overbought}
    elif fam == 'Regime Switch' and hist:
        ind = {'efficiency_ratio': strat._efficiency_ratio(hist), 'er_threshold': strat.er_threshold,
               'leg': strat.regime.get(sym)}
    last_trade = pf.trade_history[-1] if pf and pf.trade_history else None
    return {'family': fam, 'etf': etf, 'config': cfg, 'train_start': str(start.date()),
            'train_end': str(AS_OF.date()), 'params': best_params,
            'train_sharpe': float(best_m.get('sharpe_ratio', 0.0)) if best_m else 0.0,
            'train_return': float(best_m.get('return_pct', 0.0)) if best_m else 0.0,
            'train_trades': int(best_m.get('total_trades', 0)) if best_m else 0,
            'check_abs_diff': abs(m.get('return_pct', 0.0) - best_m.get('return_pct', 0.0)) if best_m else None,
            'signal': 'LONG' if pos > 0 else 'FLAT', 'indicators': ind,
            'last_trade': {'action': last_trade['action'], 'date': str(pd.Timestamp(last_trade['timestamp']).date())}
            if last_trade else None,
            'engine': runner.engine}


# ─── Market state (part 2) ───────────────────────────────────────────────────────────────────

class MarketState:
    """SPY's trend (200-day average) and volatility (63-day realised vs its expanding median)."""

    def __init__(self, storage: DataStorage):
        c = storage.load_data(f'US_{MARKET}', '1d')['close'].astype(float)
        lr = np.log(c).diff()
        self.close = c
        self.sma200 = c.rolling(200).mean()
        self.vol63 = lr.rolling(63).std() * math.sqrt(252)
        self.vol_med = self.vol63.expanding().median()

    def at(self, before) -> Dict[str, Any]:
        """State from the closes strictly before `before` (a date)."""
        idx = self.close.index[self.close.index < pd.Timestamp(before, tz='UTC')]
        t = idx[-1]
        up = bool(self.close[t] > self.sma200[t])
        high = bool(self.vol63[t] > self.vol_med[t])
        return {'as_of_close': str(t.date()), 'state': f"{'UP' if up else 'DOWN'}/{'HIGH' if high else 'LOW'}",
                'close': float(self.close[t]), 'sma200': float(self.sma200[t]),
                'vol63': float(self.vol63[t]), 'vol63_median': float(self.vol_med[t])}


# ─── Selection, risk, summaries ──────────────────────────────────────────────────────────────

def _argmax(scores: Dict[str, float]) -> str:
    return max(FAMILY_ORDER, key=lambda f: (scores[f], -FAMILY_ORDER.index(f)))


def build_selectors(fam_rows: Dict[str, List[dict]], states: Optional[List[str]]):
    """Selector, Selector-R and Oracle per window, from the families' aligned windows."""
    n = len(fam_rows[FAMILY_ORDER[0]])
    for f in FAMILY_ORDER:
        assert len(fam_rows[f]) == n and [r['test_start'] for r in fam_rows[f]] == \
            [r['test_start'] for r in fam_rows[FAMILY_ORDER[0]]], 'windows do not line up'
    out = {'Selector': [], 'Selector-R': [], 'Oracle': []}
    for k in range(n):
        tr = {f: fam_rows[f][k]['train_sharpe'] for f in FAMILY_ORDER}
        te = {f: fam_rows[f][k]['test_return'] for f in FAMILY_ORDER}
        pick, orc = _argmax(tr), _argmax(te)
        pick_r, basis = pick, 'fallback: Selector'
        if states is not None:
            same = [j for j in range(k) if states[j] == states[k]]
            if len(same) >= MIN_PRIOR:
                pick_r = _argmax({f: float(np.mean([fam_rows[f][j]['test_return'] for j in same]))
                                  for f in FAMILY_ORDER})
                basis = f'{len(same)} earlier {states[k]} windows'
        base = fam_rows[FAMILY_ORDER[0]][k]
        for name, f, why in (('Selector', pick, 'highest training Sharpe'), ('Selector-R', pick_r, basis),
                             ('Oracle', orc, 'hindsight')):
            r = fam_rows[f][k]
            out[name].append({'window': base['window'], 'test_start': base['test_start'],
                              'test_end': base['test_end'], 'picked': f, 'basis': why,
                              'params': r['params'], 'train_sharpe': r['train_sharpe'],
                              'test_return': r['test_return'], 'bh_return': r['bh_return'],
                              'excess': r['excess'], 'test_trades': r['test_trades'],
                              'state': states[k] if states else None})
    if states is None:
        out.pop('Selector-R')
    return out


def stats(rows: List[dict]) -> Dict[str, Any]:
    r = np.array([x['test_return'] for x in rows], dtype=float)
    ex = np.array([x['excess'] for x in rows], dtype=float)
    return {'n': len(r), 'mean_oos': float(r.mean()), 'pct_profitable': float((r > 0).mean() * 100),
            'mean_excess': float(ex.mean()), 'pct_beat_bh': float((ex > 0).mean() * 100),
            'best': float(r.max()), 'worst': float(r.min()),
            'oos_returns': [float(x) for x in r], 'excess_returns': [float(x) for x in ex]}


def stitched_risk(window_paths: List[List[list]]) -> Dict[str, Any]:
    """Max drawdown and CVaR 5% of the chained test-segment daily returns."""
    rets = []
    for p in window_paths:
        eq = pd.Series([e for _, e in p], index=[d for d, _ in p])
        eq = eq.groupby(level=0).last()
        v = np.concatenate([[CAPITAL], eq.to_numpy()])
        rets.extend((v[1:] / v[:-1] - 1.0).tolist())
    rets = np.array(rets)
    curve = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(np.concatenate([[1.0], curve]))[1:]
    dd = float(((peak - curve) / peak).max() * 100)
    q = np.quantile(rets, 0.05)
    cvar = float(rets[rets <= q].mean() * 100)
    return {'days': len(rets), 'max_drawdown_pct': dd, 'cvar5_daily_pct': cvar,
            'stitched_return_pct': float((curve[-1] - 1) * 100)}


def full_span_bh(storage: DataStorage, etf: str, start, end) -> Dict[str, Any]:
    c = storage.load_data(f'US_{etf}', '1d')['close'].astype(float)
    c = c[(c.index >= pd.Timestamp(start, tz='UTC')) & (c.index <= pd.Timestamp(end, tz='UTC'))]
    r = c.pct_change().dropna()
    years = (c.index[-1] - c.index[0]).days / 365.25
    dd = float(((c.cummax() - c) / c.cummax()).max() * 100)
    q = r.quantile(0.05)
    roll = (c / c.shift(252) - 1).dropna() * 100
    stress = {}
    for name, s0, s1 in STRESS:
        a, b = c.get(pd.Timestamp(s0, tz='UTC')), c.get(pd.Timestamp(s1, tz='UTC'))
        stress[name] = float((b / a - 1) * 100) if a is not None and b is not None else None
    return {'total_return_pct': float((c.iloc[-1] / c.iloc[0] - 1) * 100),
            'cagr_pct': float(((c.iloc[-1] / c.iloc[0]) ** (1 / years) - 1) * 100),
            'max_drawdown_pct': dd, 'cvar5_daily_pct': float(r[r <= q].mean() * 100),
            'best_252d_pct': float(roll.max()), 'best_252d_end': str(roll.idxmax().date()),
            'worst_252d_pct': float(roll.min()), 'worst_252d_end': str(roll.idxmin().date()),
            'stress_pct': stress}


def data_checks(storage: DataStorage, etfs, start, end) -> Dict[str, Any]:
    out = {}
    dates = None
    for t in etfs + [MARKET]:
        d = storage.load_data(f'US_{t}', '1d')
        sub = d[(d.index >= pd.Timestamp(start, tz='UTC')) & (d.index <= pd.Timestamp(end, tz='UTC'))]
        h = storage.load_data(f'US_{t}', '1h') if t != MARKET else pd.DataFrame()
        info = {'first_1d': str(d.index.min()), 'last_1d': str(d.index.max()), 'bars_in_span': len(sub),
                'nan_closes': int(sub['close'].isna().sum()), 'dup_index': int(sub.index.duplicated().sum())}
        if not h.empty:
            # Daily (adjusted) close vs the hourly last bar's close on the first and last common
            # day: a ratio below 1 early on means the hourly series is NOT dividend-adjusted.
            hd = h['close'].groupby(h.index.normalize()).last()
            common = hd.index.intersection(d.index)
            info['hourly_first'] = str(h.index.min())
            info['hourly_bars'] = len(h)
            info['daily_over_hourly_close_first_day'] = float(d['close'][common[0]] / hd[common[0]])
            info['daily_over_hourly_close_last_day'] = float(d['close'][common[-1]] / hd[common[-1]])
        if t != MARKET:
            dates = set(sub.index) if dates is None else dates
            info['dates_match_first_etf'] = set(sub.index) == dates
        out[t] = info
    return out


def qualifies(s):
    return s['mean_oos'] > 0 and s['pct_profitable'] >= 50.0


def beats_holding(s):
    return s['mean_excess'] > 0 and s['pct_beat_bh'] >= 50.0


# ─── Main ────────────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--smoke', action='store_true', help='2 ETFs only, 500 bootstrap draws')
    ap.add_argument('--out-dir', default=None, help='where to write (default results/factor_search)')
    ap.add_argument('--jobs', type=int, default=MAX_JOBS)
    args = ap.parse_args()
    jobs = min(args.jobs, MAX_JOBS)
    etfs = SMOKE_ETFS if args.smoke else ETFS
    n_boot = 500 if args.smoke else N_BOOT
    out_dir = paths.results_dir() / 'factor_search' if args.out_dir is None else \
        __import__('pathlib').Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t_all = time.time()
    storage = DataStorage()

    # Spans, anchored to the data.
    firsts = [storage.load_data(f'US_{t}', '1d').index.min() for t in ETFS]
    start = max(firsts).tz_convert(None).to_pydatetime()
    end = storage.latest_common_timestamp([sym_of(t) for t in ETFS], '1d')
    hfirsts = [storage.load_data(f'US_{t}', '1h').index.min() for t in ETFS]
    hstart = max(hfirsts).tz_convert(None).to_pydatetime()
    hend = storage.latest_common_timestamp([sym_of(t) for t in ETFS], '1h')
    print(f"Daily span {start.date()} -> {end.date()} | hourly span {hstart} -> {hend} | "
          f"ETFs {etfs} | {jobs} workers", flush=True)
    checks = data_checks(storage, etfs, start, end)

    specs = []
    for fam in FAMILY_ORDER:
        for etf in etfs:
            for cfg, n in CONFIGS:
                specs.append((fam, etf, cfg, n, SLIP, '1d', 'standard', start, end))
                specs.append((fam, etf, cfg, n, SLIP_SENS, '1d', 'standard', start, end))
                specs.append((fam, etf, cfg, n, SLIP, '1d', 'warm', start, end))
                specs.append((fam, etf, cfg, n, SLIP, '1h', 'standard', hstart, hend))
    hc_specs = [(fam, etfs[0], n, start, end) for fam in FAMILY_ORDER for _, n in CONFIGS]
    asof_specs = [(fam, etf, cfg, int(((end - start).days // n) * TRAIN_PCT))
                  for fam in FAMILY_ORDER for etf in etfs for cfg, n in CONFIGS]

    results, hc, asof = [], [], []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(wf_task, s): ('wf', s) for s in specs}
        futs.update({pool.submit(harness_check, s): ('hc', s) for s in hc_specs})
        futs.update({pool.submit(asof_task, s): ('asof', s) for s in asof_specs})
        done = 0
        for fut in as_completed(futs):
            kind, s = futs[fut]
            r = fut.result()
            done += 1
            if kind == 'wf':
                results.append(r)
                if done % 20 == 0 or done == len(futs):
                    print(f"  {done}/{len(futs)} tasks, {time.time() - t0:.0f}s", flush=True)
            elif kind == 'hc':
                hc.append(r)
            else:
                asof.append(r)
    runtime_tasks = time.time() - t0

    def get(fam, etf, cfg, slip=SLIP, tf='1d', harness='standard'):
        for r in results:
            if (r['family'], r['etf'], r['config'], r['slip'], r['tf'], r['harness']) == \
                    (fam, etf, cfg, slip, tf, harness):
                return r
        raise KeyError((fam, etf, cfg, slip, tf, harness))

    mstate = MarketState(storage)
    win_states = {cfg: [mstate.at(wb[3]) for wb in window_bounds(start, end, n)] for cfg, n in CONFIGS}
    now_state = mstate.at(AS_OF + timedelta(days=1))

    # ── Candidates per (ETF, config, cost, harness, timeframe) ──
    cand_rows = {}      # (etf, cfg, slip, tf, harness) -> {candidate: [window rows]}
    for etf in etfs:
        for cfg, _ in CONFIGS:
            for slip, tf, harness in ((SLIP, '1d', 'standard'), (SLIP_SENS, '1d', 'standard'),
                                      (SLIP, '1d', 'warm'), (SLIP, '1h', 'standard')):
                fam_rows = {f: get(f, etf, cfg, slip, tf, harness)['windows'] for f in FAMILY_ORDER}
                states = [s['state'] for s in win_states[cfg]] if tf == '1d' else None
                if states is not None:
                    for f in FAMILY_ORDER:
                        assert len(fam_rows[f]) == len(states)
                sel = build_selectors(fam_rows, states)
                d = {f: fam_rows[f] for f in FAMILY_ORDER}
                d.update(sel)
                cand_rows[(etf, cfg, slip, tf, harness)] = d

    # ── Risk view (daily, standard, 2 bps) ──
    risk = {}
    for etf in etfs:
        for cfg, _ in CONFIGS:
            fam_res = {f: get(f, etf, cfg) for f in FAMILY_ORDER}
            d = cand_rows[(etf, cfg, SLIP, '1d', 'standard')]
            for cand in CANDIDATES + ['Oracle']:
                if cand in FAMILY_ORDER:
                    wpaths = fam_res[cand]['paths']
                    stress = {k: (v['return_pct'] if v else None) for k, v in fam_res[cand]['stress'].items()}
                else:
                    wpaths = [fam_res[r['picked']]['paths'][k] for k, r in enumerate(d[cand])]
                    stress = {}
                    for name, s0, _ in STRESS:
                        dep = [k for k, r in enumerate(d[cand]) if pd.Timestamp(r['test_start']) <= pd.Timestamp(s0)]
                        if not dep or cand == 'Oracle':
                            stress[name] = None
                            continue
                        fam = d[cand][dep[-1]]['picked']
                        v = fam_res[fam]['stress'][name]
                        stress[name] = v['return_pct'] if v else None
                risk[(etf, cfg, cand)] = {**stitched_risk(wpaths), 'stress_pct': stress}

    # ── Summary table ──
    summary_rows, S = [], {}
    for etf in etfs:
        for cand in CANDIDATES + ['Oracle']:
            row = {'etf': etf, 'candidate': cand, 'hindsight': cand == 'Oracle'}
            per = {}
            for cfg, _ in CONFIGS:
                s = stats(cand_rows[(etf, cfg, SLIP, '1d', 'standard')][cand])
                s5 = stats(cand_rows[(etf, cfg, SLIP_SENS, '1d', 'standard')][cand])
                sw = stats(cand_rows[(etf, cfg, SLIP, '1d', 'warm')][cand])
                rk = risk[(etf, cfg, cand)]
                per[cfg] = s
                row.update({
                    f'{cfg}_mean_oos': s['mean_oos'], f'{cfg}_pct_profitable': s['pct_profitable'],
                    f'{cfg}_mean_excess': s['mean_excess'], f'{cfg}_pct_beat_bh': s['pct_beat_bh'],
                    f'{cfg}_best': s['best'], f'{cfg}_worst': s['worst'],
                    f'{cfg}_max_dd': rk['max_drawdown_pct'], f'{cfg}_cvar5': rk['cvar5_daily_pct'],
                    **{f'{cfg}_stress_{k}': v for k, v in rk['stress_pct'].items()},
                    f'{cfg}_mean_oos_5bps': s5['mean_oos'], f'{cfg}_pct_profitable_5bps': s5['pct_profitable'],
                    f'{cfg}_mean_excess_5bps': s5['mean_excess'],
                    f'{cfg}_warm_mean_oos': sw['mean_oos'], f'{cfg}_warm_mean_excess': sw['mean_excess'],
                    f'{cfg}_warm_pct_beat_bh': sw['pct_beat_bh'],
                })
                if cand in FAMILY_ORDER or cand == 'Selector':
                    sh = stats(cand_rows[(etf, cfg, SLIP, '1h', 'standard')][cand])
                    row.update({f'{cfg}_hourly_mean_oos': sh['mean_oos'],
                                f'{cfg}_hourly_pct_profitable': sh['pct_profitable'],
                                f'{cfg}_hourly_mean_excess': sh['mean_excess']})
            row['pass'] = (not row['hindsight']) and all(qualifies(per[c]) for c, _ in CONFIGS)
            row['beats_holding'] = (not row['hindsight']) and all(beats_holding(per[c]) for c, _ in CONFIGS)
            row['pass_5bps'] = (not row['hindsight']) and all(
                row[f'{c}_mean_oos_5bps'] > 0 and row[f'{c}_pct_profitable_5bps'] >= 50 for c, _ in CONFIGS)
            S[(etf, cand)] = row
            summary_rows.append(row)

    # Selector diagnostics
    sel_diag = {}
    for etf in etfs:
        for cfg, _ in CONFIGS:
            d = cand_rows[(etf, cfg, SLIP, '1d', 'standard')]
            picks = [r['picked'] for r in d['Selector']]
            orc = [r['picked'] for r in d['Oracle']]
            n = len(picks)
            hit = sum(a == b for a, b in zip(picks, orc)) / n
            chance = sum((picks.count(f) / n) * (orc.count(f) / n) for f in FAMILY_ORDER)
            m_sel, m_bh, m_orc = (np.mean([r['test_return'] for r in d[c]]) for c in ('Selector', 'Buy & Hold', 'Oracle'))
            fam_means = {f: float(np.mean([r['test_return'] for r in d[f]])) for f in FAMILY_ORDER}
            sel_diag[(etf, cfg)] = {
                'picks': {f: picks.count(f) for f in FAMILY_ORDER},
                'oracle': {f: orc.count(f) for f in FAMILY_ORDER},
                'picks_r': {f: [r['picked'] for r in d['Selector-R']].count(f) for f in FAMILY_ORDER},
                'hit_rate': hit, 'chance_hit_rate': chance,
                'capture': float((m_sel - m_bh) / (m_orc - m_bh)) if m_orc != m_bh else None,
                'selector_beats_all_families': bool(m_sel > max(fam_means.values())),
                'sequence': picks, 'oracle_sequence': orc}
    selection_adds_value = {etf: all(sel_diag[(etf, c)]['selector_beats_all_families'] for c, _ in CONFIGS)
                            for etf in etfs}

    # ── Reality Checks ──
    rc_out = {}
    for cfg, _ in CONFIGS:
        raw = {f'{etf} | {c}': stats(cand_rows[(etf, cfg, SLIP, '1d', 'standard')][c])['oos_returns']
               for etf in etfs for c in CANDIDATES}
        exc = {f'{etf} | {c}': stats(cand_rows[(etf, cfg, SLIP, '1d', 'standard')][c])['excess_returns']
               for etf in etfs for c in CANDIDATES if c != 'Buy & Hold'}
        rc_out[cfg] = {}
        for label, cands in (('RC-1 raw', raw), ('RC-2 excess', exc)):
            rc = reality_check(cands, n_boot=n_boot)
            rc['naive_p_value'] = naive_single_test(cands[rc['best_candidate']], n_boot=n_boot)
            rc_out[cfg][label] = rc
    worth = {etf: {c: bool(S[(etf, c)]['pass'] and S[(etf, c)]['beats_holding'] and
                           all(rc_out[cfg]['RC-2 excess']['p_value'] < 0.05 for cfg, _ in CONFIGS))
                   for c in CANDIDATES if c != 'Buy & Hold'} for etf in etfs}

    # ── Part 2: regime tables ──
    regime_counts, regime_means = {}, {}
    for etf in etfs:
        for cfg, _ in CONFIGS:
            d = cand_rows[(etf, cfg, SLIP, '1d', 'standard')]
            tab = {s: {f: 0 for f in FAMILY_ORDER} for s in STATES}
            for r in d['Oracle']:
                tab[r['state']][r['picked']] += 1
            regime_counts[(etf, cfg)] = tab
    for cfg, _ in CONFIGS:
        for s in STATES:
            for f in FAMILY_ORDER:
                v = [r['test_return'] for etf in etfs
                     for r in cand_rows[(etf, cfg, SLIP, '1d', 'standard')][f] if
                     win_states[cfg][r['window'] - 1]['state'] == s]
                ex = [r['excess'] for etf in etfs
                      for r in cand_rows[(etf, cfg, SLIP, '1d', 'standard')][f] if
                      win_states[cfg][r['window'] - 1]['state'] == s]
                regime_means[(cfg, s, f)] = {'n_window_etf': len(v), 'n_windows': len(v) // max(1, len(etfs)),
                                             'mean_oos': float(np.mean(v)) if v else None,
                                             'mean_excess': float(np.mean(ex)) if ex else None}

    # ── Part 3: as-of readout ──
    readout = {}
    for etf in etfs:
        e = {'spy_state_now': now_state, 'by_config': {}, 'same_state_history': {}}
        for cfg, _ in CONFIGS:
            fams = {r['family']: r for r in asof if r['etf'] == etf and r['config'] == cfg}
            pick = _argmax({f: fams[f]['train_sharpe'] for f in FAMILY_ORDER})
            e['by_config'][cfg] = {'families': fams, 'selector_pick_now': pick,
                                   'selector_signal_now': fams[pick]['signal']}
        for cand in CANDIDATES:
            vals = []
            for cfg, _ in CONFIGS:
                for r in cand_rows[(etf, cfg, SLIP, '1d', 'standard')][cand]:
                    if win_states[cfg][r['window'] - 1]['state'] == now_state['state']:
                        vals.append((cfg, r['window'], r['test_start'], r['test_return'], r['excess']))
            e['same_state_history'][cand] = {
                'n': len(vals), 'n_A': sum(v[0] == 'A' for v in vals), 'n_B': sum(v[0] == 'B' for v in vals),
                'best': max(vals, key=lambda v: v[3]) if vals else None,
                'worst': min(vals, key=lambda v: v[3]) if vals else None,
                'mean': float(np.mean([v[3] for v in vals])) if vals else None,
                'mean_excess': float(np.mean([v[4] for v in vals])) if vals else None}
        readout[etf] = e

    # ── Checks ──
    path_diffs = [r['path_check_max_abs_diff'] for r in results if r.get('path_check_max_abs_diff') is not None]
    asof_diffs = [r['check_abs_diff'] for r in asof if r['check_abs_diff'] is not None]
    bench_diffs = [abs(w['bh_return'] - w['bh_return_backtester']) for r in results for w in r['windows']
                   if w.get('bh_return_backtester') is not None]
    engines = {f: sorted({r['engine'] for r in results if r['family'] == f and r['harness'] == 'standard'})
               for f in FAMILY_ORDER}
    test_bars = {cfg: {tf: sorted({w['test_bars'] for r in results if r['config'] == cfg and r['tf'] == tf
                                    for w in r['windows']}) for tf in ('1d', '1h')} for cfg, _ in CONFIGS}
    bh_full = {etf: full_span_bh(storage, etf, start, end) for etf in etfs}
    bh_full[MARKET] = full_span_bh(storage, MARKET, start, end)

    # ── Write ──
    stamp = time.strftime('%Y%m%d_%H%M%S')
    base = out_dir / f'factor_selector_{stamp}'
    pd.DataFrame(summary_rows).to_csv(base.with_suffix('.csv'), index=False)
    wrows = []
    for (etf, cfg, slip, tf, harness), d in cand_rows.items():
        for cand, rows in d.items():
            for r in rows:
                wrows.append({'etf': etf, 'candidate': cand, 'config': cfg, 'slippage_bps': slip, 'timeframe': tf,
                              'harness': harness, 'window': r['window'], 'test_start': r['test_start'],
                              'test_end': r['test_end'],
                              'spy_state': win_states[cfg][r['window'] - 1]['state'] if tf == '1d' else None,
                              'picked': r.get('picked', cand), 'params': json.dumps(r['params'], sort_keys=True),
                              'train_sharpe': r['train_sharpe'], 'test_return': r['test_return'],
                              'bh_return': r['bh_return'], 'excess': r['excess'], 'test_trades': r['test_trades']})
    pd.DataFrame(wrows).to_csv(out_dir / f'factor_selector_windows_{stamp}.csv', index=False)

    def jkey(k):
        return ' | '.join(str(x) for x in k) if isinstance(k, tuple) else k

    payload = {
        'study': 'factor_selector', 'smoke': args.smoke, 'etfs': etfs,
        'span_daily': [str(start), str(end)], 'span_hourly': [str(hstart), str(hend)],
        'configs': CONFIGS, 'train_pct': TRAIN_PCT, 'commission_per_side': RATE, 'slippage_bps': SLIP,
        'slippage_bps_sensitivity': SLIP_SENS, 'capital': CAPITAL, 'cash_buffer': CASH_BUFFER,
        'warmup_bars': WARMUP_BARS, 'grids': {f: FAMILIES[f]['grid'] for f in FAMILY_ORDER},
        'configurations_per_etf': N_CONFIGS_PER_ETF,
        'search_size': {'daily_primary_configurations': N_CONFIGS_PER_ETF * len(etfs),
                        'daily_candidates': len(CANDIDATES) * len(etfs),
                        'warmup_diagnostic_configurations': N_CONFIGS_PER_ETF * len(etfs),
                        'hourly_configurations': N_CONFIGS_PER_ETF * len(etfs),
                        'total_configuration_evaluations': 3 * N_CONFIGS_PER_ETF * len(etfs)},
        'data_checks': checks, 'engines': engines, 'test_bars': test_bars,
        'path_check_max_abs_diff': max(path_diffs) if path_diffs else None,
        'asof_check_max_abs_diff': max(asof_diffs) if asof_diffs else None,
        'benchmark_check_max_abs_diff': max(bench_diffs) if bench_diffs else None,
        'harness_check': hc,
        'window_states': win_states, 'state_now': now_state,
        'summary': summary_rows,
        'selector_diagnostics': {jkey(k): v for k, v in sel_diag.items()},
        'selection_adds_value': selection_adds_value,
        'reality_check': rc_out, 'worth_trading_instead_of_holding': worth,
        'regime_oracle_counts': {jkey(k): v for k, v in regime_counts.items()},
        'regime_family_means': {jkey(k): v for k, v in regime_means.items()},
        'asof_readout': readout,
        'asof_note': 'DESCRIPTIVE ONLY, not a recommendation: the output of a 42-candidate search in which '
                     'nothing passed the bar for trading instead of holding (see worth_trading_instead_of_holding).',
        'buy_and_hold_full_span': bh_full,
        'stress_replays': {f'{r["family"]} | {r["etf"]} | {r["config"]}': r['stress'] for r in results
                           if 'stress' in r},
        'runtimes': {'tasks_seconds': round(runtime_tasks, 1), 'total_seconds': round(time.time() - t_all, 1),
                     'per_task': [{k: r[k] for k in ('family', 'etf', 'config', 'slip', 'tf', 'harness', 'seconds')}
                                  for r in results]},
    }
    base.with_suffix('.json').write_text(json.dumps(payload, indent=1, default=str))

    # ── Print ──
    def f2(x):
        return '   n/a' if x is None else f'{x:+6.2f}'

    print(f"\nEngines: {engines}\nTest bars per window: {test_bars}")
    print(f"Checks: path re-run max |diff| {payload['path_check_max_abs_diff']}, as-of {payload['asof_check_max_abs_diff']}, "
          f"benchmark {payload['benchmark_check_max_abs_diff']}, harness "
          f"{[(h['family'], h['n_splits'], h['same_params'], h['max_abs_diff']) for h in hc]}")
    print("\nPer ETF (daily, 2 bps): A/B mean OOS %/window, % windows profitable, mean excess over B&H, "
          "% windows beating B&H, pass, beats holding")
    for etf in etfs:
        print(f"\n{etf}")
        for cand in CANDIDATES + ['Oracle']:
            r = S[(etf, cand)]
            print(f"  {cand:14s} A {f2(r['A_mean_oos'])} {r['A_pct_profitable']:5.1f}% ex {f2(r['A_mean_excess'])} "
                  f"{r['A_pct_beat_bh']:5.1f}% | B {f2(r['B_mean_oos'])} {r['B_pct_profitable']:5.1f}% ex "
                  f"{f2(r['B_mean_excess'])} {r['B_pct_beat_bh']:5.1f}% | "
                  f"{'hindsight' if r['hindsight'] else ('PASS' if r['pass'] else 'fail')}"
                  f"{' beats-BH' if r['beats_holding'] else ''} | 5bps A {f2(r['A_mean_oos_5bps'])} "
                  f"B {f2(r['B_mean_oos_5bps'])} | warm ex A {f2(r['A_warm_mean_excess'])} B {f2(r['B_warm_mean_excess'])}")
        for cfg, _ in CONFIGS:
            sd = sel_diag[(etf, cfg)]
            print(f"  [{cfg}] picks {sd['picks']} oracle {sd['oracle']} hit {sd['hit_rate']:.0%} "
                  f"(chance {sd['chance_hit_rate']:.0%}) capture {sd['capture']}")
    print("\nReality Check:")
    for cfg, _ in CONFIGS:
        for label, rc in rc_out[cfg].items():
            print(f"  {cfg} {label}: best {rc['best_candidate']} {rc['observed_max_mean']:+.3f}% | "
                  f"p {rc['p_value']:.4f} | naive p {rc['naive_p_value']:.4f} | "
                  f"null best-of-{rc['n_candidates']} 95th {rc['bootstrap_max_quantiles']['p95']:+.3f}%")
    print(f"\nSPY state now ({now_state['as_of_close']}): {now_state['state']}")
    for etf in etfs:
        e = readout[etf]
        a, b = e['by_config']['A'], e['by_config']['B']
        sigs = ' '.join(f"{f.split()[0]}:{a['families'][f]['signal'][0]}/{b['families'][f]['signal'][0]}"
                        for f in FAMILY_ORDER)
        h = e['same_state_history']['Selector']
        print(f"  {etf}: selector now A {a['selector_pick_now']} ({a['selector_signal_now']}), "
              f"B {b['selector_pick_now']} ({b['selector_signal_now']}) | signals A/B {sigs} | "
              f"same-state Selector n={h['n']} best {h['best'][3] if h['best'] else None} "
              f"worst {h['worst'][3] if h['worst'] else None}")
    print(f"\nWrote {base.with_suffix('.json')}\n      {base.with_suffix('.csv')}\n      "
          f"{out_dir / f'factor_selector_windows_{stamp}.csv'}\nTotal {(time.time() - t_all) / 60:.1f} min")


if __name__ == '__main__':
    main()
