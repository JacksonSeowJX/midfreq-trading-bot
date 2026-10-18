"""
Equity factor ETFs: which kind of strategy fits each one, and trading it versus holding it
==========================================================================================

PRE-REGISTRATION. Written and saved on 18 Oct 2026, before any backtest of the candidates below
was run. Only the data's coverage, the walk-forward window geometry (bar counts per segment) and
whether the closes are dividend-adjusted had been looked at. Nothing below may be added to or
tuned after the results are seen; anything tried afterwards goes in a clearly labelled
exploratory section, counts toward the search total, and is never called a pass.

Question
--------
For each of the seven factor ETFs a student investment club tracks (MTUM momentum, QUAL quality,
VLUE value, USMV minimum volatility, IWF Russell 1000 Growth, IWD Russell 1000 Value, SPHD S&P 500
high dividend / low volatility):
  1. which strategy family, if any, wins out of sample;
  2. whether TREND (momentum) or REVERSAL (mean reversion) is the better description of the ETF
     at daily frequency; Jackson's framing: a trend strategy on QUAL is a bet against the traders
     who think QUAL is reverting, so which side has been right matters;
  3. the best and worst cases of trading it versus simply holding it.

Data (read-only)
----------------
data/US_<T>/1d.parquet through DataStorage, Timeframe.DAY_1. Daily bars are stamped 00:00 UTC of
the trading date. The closes are split- AND dividend-adjusted: the yfinance provider calls
yf.download without auto_adjust, and in the installed yfinance (1.2.0) auto_adjust defaults to
True. Checked on the cache: SPY's 2000-01-03 close is 90.91, against a raw close of 145.44.
Adjusted closes are what a total-return comparison needs. The script re-checks this and records
it (data_checks). The hourly bars (1h.parquet, 2024-10-21 to 2026-10-16, true UTC, 13:30 = 09:30
ET) are NOT dividend-adjusted: their ratio to the daily close falls from 1.01-1.09 at the start
to 1.00 at the end, stepping down at each ex-dividend date. So the hourly cross-check measures
price return only, for the strategy and for buy-and-hold alike.

Window (fixed): the common span of the seven, start = 2013-07-18 (QUAL's first bar, the latest
start), end = DataStorage.latest_common_timestamp of the seven (2026-10-16). That is 4,838
calendar days, about 3,330 bars.

Validation standard (the project's, unchanged)
----------------------------------------------
* midfreq.backtest.optimizer.walk_forward, called verbatim, on each ETF alone. The only in-process
  change: generate_param_grid is patched to return the grids below for the candidates' names
  (as in hk_single_stock.py). The C++ engine (BatchRunner) runs SMA, MACD, RSI, Bollinger,
  Z-Score and Regime Switch; HMM and the fully invested buy-and-hold variants run on Python.
* Config A: 9 windows, 70% train (537-day windows: ~258 train bars, ~111 test bars).
  Config B: 15 windows, 70% train (322-day windows: ~155 train bars, ~67 test bars).
* Each window: grid-search the TRAINING segment by Sharpe ratio (settings that traded rank
  first), trade the chosen setting unchanged on the TEST segment, from a cold start.
* Costs: commission 0.00005 per side (the project's calibrated moomoo US rate) + slippage 2 bps
  per side. Cost line: the whole walk-forward re-run at 5 bps slippage, same commission.
* Sizing: FULLY INVESTED. The registry strategies without a risk manager buy a flat 100 shares,
  which would put 4% to 40% of the account into an ETF depending on its price, so their returns,
  drawdowns and tail losses would be a diluted mix of ETF and cash and could not be set against
  holding the ETF. Every run here therefore gets a plain RiskManager with
  EqualDollarSizer(n_positions=1, cost_buffer=0.001) (each entry buys as many whole shares as
  99.9% of equity buys), no stop, no take-profit, and max_drawdown_pct=1.0 (the circuit breaker
  can never fire). The C++ engine supports this risk manager. Initial capital US$1,000,000 so
  whole-share rounding leaves under 0.03% in cash. BuyAndHoldRiskOverlay builds its own risk
  manager with a fixed 100 shares (in Python and C++), so this study uses a subclass,
  FullyInvestedOverlay, identical except that its sizer is the same EqualDollarSizer; it runs on
  Python.
* Buy-and-hold benchmark for each test window: the ETF's first close to last close in the
  segment, no fees (the backtester's own benchmark_return_pct). Excess = strategy test return
  minus this.

Candidates, families and grids (exact; rescaled from the registry for daily bars)
-------------------------------------------------------------------------------------
Rescaling rule: walk_forward cold-starts every segment, and config B's test segments hold 66-69
daily bars. A setting whose indicator needs more history than that can never trade out of
sample. So no setting may need more than ~51 bars before its first possible signal; the
registry's long ends (SMA slow up to 200, Z-score lookback up to 100, HMM fit window 300) are
cut. Thresholds are kept where they are meaningful for daily moves (daily factor-ETF volatility
is roughly 0.8-1.3%). Unlisted parameters keep their defaults.

 TREND / MOMENTUM
  1. SMA Crossover            fast_period {5, 10, 15, 20} x slow_period {30, 40, 50}         = 12
                              (registry 2-50 / 5-200; warm-up <= 51 bars)
  2. MACD                     fast_ema {5, 8, 12} x slow_ema {20, 26, 35} x signal {5, 9}    = 18
                              (registry 2-50 / 10-100 / 2-30; 12/26/9 is the daily classic;
                              warm-up <= 45 bars)
 REVERSAL / MEAN REVERSION
  3. RSI                      rsi_period {2, 5, 14} x oversold {10, 20, 30}
                              x overbought {60, 70, 80}                                      = 27
                              (RSI(2) is the known daily ETF reversal rule, 14 Wilder's period;
                              the project's RSI is a simple-average RSI)
  4. Bollinger Bands          bb_period {10, 20, 30} x num_std {1.5, 2.0, 2.5}               =  9
  5. Z-Score Mean Reversion   lookback {10, 20, 40} x entry_z {1.5, 2.0, 2.5}
                              x exit_z {0.0, 0.5}                                            = 18
 REGIME
  6. Regime Switch (ER)       regime_lookback {10, 20, 30, 40}
                              x er_threshold {0.15, 0.25, 0.35, 0.45}                        = 16
                              (legs at defaults: SMA 5/20 trend leg, Bollinger 14 / 2.5 sigma)
  7. HMM Regime Switch        n_states {2, 3} x trend_score {0.02, 0.06, 0.10}
                              x min_dwell {1, 3} x vol_feature {0, 1}                        = 24
                              (the registry grid unchanged), with fit_window = 120 and
                              refit_every = 20 fixed (registry defaults 300 / 60 are hours-scale;
                              300 daily bars is 14 months and would never be reached in a
                              segment). It uses the Efficiency Ratio fallback until 60 bars exist,
                              so in config B's 67-bar test segments it is almost entirely the ER
                              rule at defaults; the warm-up diagnostic shows the HMM itself.
 RISK OVERLAY
  8. Buy & Hold + Risk Overlay (FullyInvestedOverlay)
                              stop_loss_pct {0, 0.05, 0.10} x trailing_stop_pct
                              {0, 0.05, 0.10, 0.15}, without (0, 0)                          = 11
                              (registry 0.02-0.10 / 0.03-0.15; a 2-3% stop on daily closes is
                              noise for a 1%-a-day ETF; never re-enters after a stop in a segment)
 BENCHMARK
  9. Buy & Hold (FullyInvestedOverlay with no stops)                                         =  1

Search size: 135 strategy configurations + 1 benchmark = 136 per ETF, x 7 ETFs = 952 (945
strategy configurations), in 63 walk-forward candidates (candidate x ETF), each under both
configurations. The 5 bps line re-runs the same configurations (not new ones).

Family-level result (pre-registered selection rule, no new configurations)
--------------------------------------------------------------------------
Each family's out-of-sample result on an ETF is a FAMILY SELECTOR: in every window, trade the
family member whose walk-forward picked the setting with the best TRAINING Sharpe (traded
settings first; ties to the member listed first). It uses training data only. Families: trend
(1-2), reversal (3-5), regime (6-7), overlay (8; a one-member family). 4 families x 7 ETFs = 28
selectors.

Acceptance rules (per candidate or selector, at 2 bps)
------------------------------------------------------
* PASS: under BOTH configurations, mean test return per window > 0 AND >= 50% of windows > 0.
* EARNED ITS PLACE: PASS, and under BOTH configurations mean excess over buy-and-hold > 0 AND
  >= 50% of windows beat buy-and-hold. (On a single ETF, a pass that trails holding has not.)
* FIT TO TRADE: earned its place AND Reality Check p < 0.05 on the excess series under both
  configurations. Anything less is reported as such, never as fit to trade.
* Q1, "which family wins on this ETF": the family whose selector earned its place (if several,
  the higher mean excess averaged over A and B). Otherwise: no family; holding is the answer.

Q2, trend versus reversal (pre-registered evidence and labelling)
-----------------------------------------------------------------
* Strategy evidence: trend selector vs reversal selector, mean test return under A and under B,
  % of windows profitable, and the pooled round-trip hit rate (completed round trips with a
  profit / completed round trips, test segments only, the backtester's win_rate convention).
  "trend" if the trend selector's mean is higher under both configurations, "reversal" if the
  reversal selector's is, else "split".
* Statistical evidence (model-free, descriptive, over the common span, daily adjusted closes):
  lag-1 autocorrelation of daily log returns; Lo-MacKinlay variance ratios VR(5), VR(21), VR(63)
  with the heteroskedasticity-robust z*; and a time-series-momentum check at month ends (Moskowitz,
  Ooi & Pedersen style): the next calendar month's return after a positive vs a negative past
  21-, 63- and 252-bar return, with a Welch t, for the ETF itself AND for the ETF relative to SPY
  (ETF / SPY price ratio), which isolates the factor tilt from the market. "trend" if VR(21) > 1
  with z* > 1.96, "reversal" if VR(21) < 1 with z* < -1.96, else "random walk".
* Label: trend or reversal only if both kinds of evidence agree; otherwise "neither clearly",
  stating which way each points.

Risk view (per candidate, selector and buy-and-hold; standard harness, 2 bps)
-----------------------------------------------------------------------------
* Best and worst test window (and buy-and-hold's in the same windows); share of windows beating
  buy-and-hold.
* Max drawdown and CVaR 5% (mean of the worst 5% of days) of the out-of-sample daily returns:
  each test segment's daily equity (re-run with the chosen setting; the re-run must reproduce the
  walk-forward's test return to 1e-6, recorded as rerun_max_abs_diff), segments chained in order.
* Stress periods (S&P 500 closing peak to closing trough; return = equity at the trough close /
  equity at the peak close - 1), plus the RECOVERY that follows (trough close to the close 21
  trading days later):
     COVID crash            2020-02-19 -> 2020-03-23
     2022 rate shock        2022-01-03 -> 2022-10-12
     Apr 2025 tariff shock  2025-04-02 -> 2025-04-08
  None of these lies inside a config-A or config-B test segment except parts of 2022 and April
  2025, so the stress view is its own out-of-sample run: the setting is chosen by Sharpe (traded
  first) on the 375 calendar days before the peak (config A's training length, end-exclusive),
  then run on the peak-to-recovery span. Signal strategies (trend, reversal, regime) run
  continuously from the start of that 375-day training span, so their position at the peak is
  whatever their own rules held, as a node that had been running them would. Buy-and-hold and the
  overlay are bought at the peak close (the worst case of buying). Buy-and-hold's own return over
  the same span is reported beside each.
* Also, descriptively, for each ETF and SPY over the common span: total return, CAGR, volatility,
  max drawdown with dates, CVaR 5%, best / worst calendar year, best / worst rolling 252-bar
  return, beta to SPY.

Multiple testing
----------------
White's Reality Check (midfreq.research.reality_check: stationary bootstrap, mean block 2, 5,000
resamples, seed 12345) over all 56 strategy candidates (8 x 7 ETFs) plus the 28 family selectors
= 84 series, separately for config A and config B, on (i) per-window EXCESS over buy-and-hold
(decides "fit to trade") and (ii) per-window raw test returns (the project's usual series, where
a mostly-long strategy on a rising ETF looks significant from the equity premium alone). Run if
anything passes; it is cheap, so it is reported in any case. Naive single-test p for the best
and for every passing series.

Secondary diagnostics (pre-registered, never a pass)
----------------------------------------------------
* WARM-UP: the same walk-forward (windows, selection rule, grids, 2 bps) with every train and
  test segment preceded by 252 bars the strategy observes but cannot trade on, as a live node
  warms up from its bar cache. Isolates the cold-start artefact, which falls hardest on the slow
  trend and regime settings. Candidates 1-7 (not 8-9, which have no look-back and whose single
  entry a warm-up would consume). Fewer bars where the ETF's history is shorter: QUAL has none
  before 2013-07-18, MTUM and VLUE about 63. Its harness must reproduce walk_forward exactly with
  no warm-up (harness_check). 124 configurations x 7 = 868.
* HOURLY CROSS-CHECK (mid-frequency): for each ETF, the "best family" = the non-benchmark family
  whose selector has the highest mean test return averaged over A and B (standard, 2 bps). Its
  members are re-run with walk_forward on 1h bars over the hourly span (2024-10-21 13:30 to
  2026-10-16 19:30 UTC), configs A and B, the SAME grids counted in bars (so 20 means 20 hours),
  2 bps, same sizing; member and selector results with excess over hourly buy-and-hold. Price
  return only (unadjusted). Counted in the search total.

What the literature predicts (stated before the results)
--------------------------------------------------------
* Gupta & Kelly (2019), "Factor Momentum Everywhere": long-short factor returns are positively
  autocorrelated at 1-12 month horizons; time-series factor momentum is strongest at one month.
* Ehsani & Linnainmaa (2022), "Factor Momentum and the Momentum Factor": factor momentum comes
  from that autocorrelation, and stock momentum largely reflects it.
* Asness, Chandra, Ilmanen & Israel (2017), "Contrarian Factor Timing is Deceptively Difficult":
  timing factors on value spreads adds little beyond a static tilt.
* So: these ETFs are LONG-ONLY factor tilts with a market beta near 1, so the market dominates
  their daily price path; the factor-momentum effect, if present, should show in the ETF relative
  to SPY at 1-12 month horizons, not in daily oscillators. Timing a rising equity ETF in and out
  of cash usually costs the equity premium while flat, so the expectation is that holding beats
  most timing candidates.

Outputs
-------
results/factor_search/factor_strategy_fit_<stamp>.csv           one row per (ETF, candidate/selector)
results/factor_search/factor_strategy_fit_windows_<stamp>.csv   one row per run x window
results/factor_search/factor_strategy_fit_<stamp>.json          everything (meta, grids, counts,
                                                                descriptive stats, stress, Reality
                                                                Check, hourly, checks, runtimes)
results/factor_search/ is outside the dashboard's results index (results/*.csv).

Usage (laptop: niced, at most 3 worker processes):
    nice -n 10 uv run python research/studies/factor_strategy_fit.py --smoke --out-dir <scratch>
    nice -n 10 uv run python research/studies/factor_strategy_fit.py
"""
import argparse
import contextlib
import io
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List

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
from midfreq.strategies import candle_strategies as cs
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY
from midfreq.research.reality_check import reality_check, naive_single_test

REPO = paths.repo_root()

# ─── Pre-registered constants ────────────────────────────────────────────────────────────────
ETFS = ['MTUM', 'QUAL', 'VLUE', 'USMV', 'IWF', 'IWD', 'SPHD']
SMOKE_ETFS = ['QUAL', 'SPHD']
MARKET = 'SPY'
EXPECTED_START = datetime(2013, 7, 18)
CONFIGS = [('A', 9), ('B', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
RATE = 0.00005                 # moomoo US, per side
SLIP = 2.0                     # bps per side, decides the pass
SLIP_SENS = 5.0                # cost line
CAPITAL = 1_000_000.0
COST_BUFFER = 0.001
MIN_CONSISTENCY = 50.0
WARMUP_BARS = 252
STRESS = [('COVID crash', '2020-02-19', '2020-03-23'),
          ('2022 rate shock', '2022-01-03', '2022-10-12'),
          ('Apr 2025 tariff shock', '2025-04-02', '2025-04-08')]
STRESS_TRAIN_DAYS = 375        # config A's training length
RECOVERY_BARS = 21
VR_LAGS = [5, 21, 63]
TSMOM_LOOKBACKS = [21, 63, 252]
N_BOOT = 5000
MAX_JOBS = 3


def sym_of(t):
    return f'US.{t}'


def _grid(**axes) -> List[Dict[str, Any]]:
    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(axes[k] for k in keys))]


def full_invest_rm() -> RiskManager:
    """Plain RiskManager: whole-equity sizing, no stops, a circuit breaker that cannot fire."""
    return RiskManager(max_drawdown_pct=1.0,
                       position_sizer=EqualDollarSizer(n_positions=1, cost_buffer=COST_BUFFER))


class FullyInvestedOverlay(cs.BuyAndHoldRiskOverlay):
    """BuyAndHoldRiskOverlay sized to the whole account (its parent buys a fixed 100 shares).
    Study-local; with no stops it is plain buy-and-hold net of the entry's costs."""

    def __init__(self, portfolio, stop_loss_pct: float = 0.0, trailing_stop_pct: float = 0.0,
                 risk_manager=None, **kwargs):
        super().__init__(portfolio, stop_loss_pct=stop_loss_pct, trailing_stop_pct=trailing_stop_pct,
                         risk_manager=risk_manager, **kwargs)
        self.risk_manager.position_sizer = EqualDollarSizer(n_positions=1, cost_buffer=COST_BUFFER)
        self.risk_manager.max_drawdown_pct = 1.0


# ─── Candidates: registry class, family, exact grid ──────────────────────────────────────────

SMA, MACD, RSI, BB, ZS = ('SMA Crossover [daily]', 'MACD [daily]', 'RSI [daily]',
                          'Bollinger Bands [daily]', 'Z-Score Mean Reversion [daily]')
RS, HMM, OVL, BH = ('Regime Switch [daily]', 'HMM Regime Switch [daily]',
                    'Buy & Hold + Risk Overlay [fully invested]', 'Buy & Hold [fully invested]')

CANDIDATES = {
    SMA: {'class': cs.MovingAverageCrossover, 'family': 'trend',
          'grid': _grid(fast_period=[5, 10, 15, 20], slow_period=[30, 40, 50])},
    MACD: {'class': cs.MACDStrategy, 'family': 'trend',
           'grid': _grid(fast_ema=[5, 8, 12], slow_ema=[20, 26, 35], signal_period=[5, 9])},
    RSI: {'class': cs.RSIStrategy, 'family': 'reversal',
          'grid': _grid(rsi_period=[2, 5, 14], oversold=[10, 20, 30], overbought=[60, 70, 80])},
    BB: {'class': cs.BollingerBandsStrategy, 'family': 'reversal',
         'grid': _grid(bb_period=[10, 20, 30], num_std=[1.5, 2.0, 2.5])},
    ZS: {'class': cs.ZScoreMeanReversion, 'family': 'reversal',
         'grid': _grid(lookback=[10, 20, 40], entry_z=[1.5, 2.0, 2.5], exit_z=[0.0, 0.5])},
    RS: {'class': cs.RegimeSwitchStrategy, 'family': 'regime',
         'grid': _grid(regime_lookback=[10, 20, 30, 40], er_threshold=[0.15, 0.25, 0.35, 0.45])},
    HMM: {'class': cs.HMMRegimeSwitchStrategy, 'family': 'regime',
          'grid': _grid(n_states=[2, 3], trend_score=[0.02, 0.06, 0.10], min_dwell=[1, 3],
                        vol_feature=[0, 1], fit_window=[120], refit_every=[20])},
    OVL: {'class': FullyInvestedOverlay, 'family': 'overlay',
          'grid': [g for g in _grid(stop_loss_pct=[0.0, 0.05, 0.10], trailing_stop_pct=[0.0, 0.05, 0.10, 0.15])
                   if g['stop_loss_pct'] > 0 or g['trailing_stop_pct'] > 0]},
    BH: {'class': FullyInvestedOverlay, 'family': 'benchmark',
         'grid': [{'stop_loss_pct': 0.0, 'trailing_stop_pct': 0.0}]},
}
FAMILIES = {'trend': [SMA, MACD], 'reversal': [RSI, BB, ZS], 'regime': [RS, HMM], 'overlay': [OVL]}
SIGNAL_FAMILIES = ('trend', 'reversal', 'regime')
WARMUP_CANDIDATES = [SMA, MACD, RSI, BB, ZS, RS, HMM]
SHORT = {SMA: 'SMA', MACD: 'MACD', RSI: 'RSI', BB: 'Bollinger', ZS: 'Z-Score', RS: 'Regime (ER)',
         HMM: 'HMM regime', OVL: 'B&H + overlay', BH: 'Buy & Hold'}

assert [len(c['grid']) for c in CANDIDATES.values()] == [12, 18, 27, 9, 18, 16, 24, 11, 1]
assert sum(len(c['grid']) for c in CANDIDATES.values()) == 136

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


def family_of(name):
    return CANDIDATES[name]['family']


# ─── Helpers ─────────────────────────────────────────────────────────────────────────────────

def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def qualifies(mean_oos, consistency):
    return mean_oos > 0 and consistency >= MIN_CONSISTENCY


class Bars:
    """One symbol's bars at one timeframe, with the Candle objects built once."""

    def __init__(self, storage: DataStorage, symbol: str, tf: Timeframe):
        df = storage.load_data(symbol.replace('.', '_'), tf.value)
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

    def pos(self, day):
        """Index of the bar on calendar day `day` (daily bars), or None."""
        ts = pd.Timestamp(day, tz='UTC')
        i = self.index.searchsorted(ts, side='left')
        return int(i) if i < len(self.index) and self.index[i].normalize() == ts.normalize() else None


def _no_trade(*args, **kwargs):
    return None


def run_segment(cls, params, bars: Bars, lo, hi, warm, slip):
    """PythonBacktester.run over bars[lo:hi], after feeding bars[lo-warm:lo] to the strategy with
    trading disabled. With warm=0 this is PythonBacktester.run (checked: harness_check).
    Returns (metrics, equity after each bar of [lo, hi), trade history)."""
    if hi <= lo:
        return {}, [], []
    pf = Portfolio(initial_cash=CAPITAL, commission_rate=RATE)
    rm = full_invest_rm()
    bt = PythonBacktester(storage=None, portfolio=pf, risk_manager=rm, slippage_bps=slip)
    sym = bars.symbol
    with quiet():
        try:
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
            latest, first, last_ts, eq = {}, {}, None, []
            for i in range(lo, hi):
                c = bars.candles[i]
                latest[sym] = c.close
                if sym not in first:
                    first[sym] = c.close
                strategy.on_data(sym, c)
                ts = bars.index[i]
                e = pf.get_current_equity(latest)
                if ts != last_ts:
                    pf.equity_curve_detailed.append({'timestamp': ts, 'equity': e})
                    last_ts = ts
                eq.append(e)
            bench = bt._calculate_benchmark(first, latest)
            m = pf.calculate_metrics(latest)
            m['benchmark_return_pct'] = bench
            m['slippage_bps'] = slip
            m['total_events'] = hi - lo
            m['alpha'] = m['return_pct'] - bench
            return m, eq, list(pf.trade_history)
        except Exception as exc:  # noqa: BLE001
            return {'error': repr(exc)}, [], []


def round_trips(trades):
    """(won, completed): the backtester's win_rate convention (pairs BUY then SELL)."""
    won = done = 0
    buy = {}
    for t in trades:
        if t['action'] == 'BUY':
            buy[t['symbol']] = t['price']
        elif t['symbol'] in buy:
            done += 1
            won += (t['price'] - buy.pop(t['symbol'])) > 0
    return int(won), int(done)


def daily_series(eq, closes):
    """Strategy and buy-and-hold returns per bar of a segment, both from the first close."""
    eq = np.asarray(eq, dtype=float)
    closes = np.asarray(closes, dtype=float)
    s = np.empty(len(eq))
    s[0] = eq[0] / CAPITAL - 1.0
    s[1:] = eq[1:] / eq[:-1] - 1.0
    b = np.zeros(len(closes))
    b[1:] = closes[1:] / closes[:-1] - 1.0
    return s, b


def warm_walk_forward(name, bars: Bars, start, end, n_splits, slip, warm):
    """optimizer.walk_forward's windows, selection rule and summary, on run_segment."""
    cls, grid = class_of(name), grid_of(name)
    if len(grid) > 200:
        step = len(grid) // 200
        grid = grid[::step][:200]
    window_size = (end - start).days // n_splits
    windows = []
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
            m, _, _ = run_segment(cls, params, bars, lo, hi, warm, slip)
            if m and 'error' not in m:
                obj = _get_objective_value(m, OBJECTIVE)
                traded = m.get('total_trades', 0) > 0
                if (traded, obj) > (best_traded, best_obj):
                    best_obj, best_params, best_train, best_traded = obj, dict(params), dict(m), traded
        lo, hi = bars.bounds(test_start, test_end, end_inclusive=True)
        tm, eq, trades = run_segment(cls, best_params, bars, lo, hi, warm, slip)
        if not tm or 'error' in tm:
            tm = {'return_pct': 0.0, 'sharpe_ratio': 0.0, 'max_drawdown': 0.0}
        windows.append(optimizer.WalkForwardWindow(w + 1, train_start, train_end, test_start, test_end,
                                                   best_params, best_train, tm))
        windows[-1]._eq, windows[-1]._trades = eq, trades
    return {'windows': windows}


def window_rows(res, bars: Bars, cls, rerun_slip=None):
    """Per-window records; with rerun_slip, each test segment is re-run for its daily equity."""
    rows = []
    for w in res['windows']:
        tm = w.test_metrics
        lo, hi = bars.bounds(w.test_start, w.test_end, end_inclusive=True)
        p0, p1 = (bars.close[lo], bars.close[hi - 1]) if hi > lo else (float('nan'), float('nan'))
        bh = (p1 / p0 - 1.0) * 100.0
        ret = float(tm.get('return_pct', 0.0))
        row = {
            'window': w.window_id, 'test_start': str(w.test_start), 'test_end': str(w.test_end),
            'test_bars': hi - lo, 'params': json.dumps(w.best_params, sort_keys=True),
            'train_return': float(w.train_metrics.get('return_pct', 0.0)),
            'train_sharpe': float(_get_objective_value(w.train_metrics, OBJECTIVE)) if w.train_metrics else float('-inf'),
            'train_traded': bool(w.train_metrics.get('total_trades', 0) > 0) if w.train_metrics else False,
            'test_return': ret, 'test_trades': int(tm.get('total_trades', 0)),
            'test_sharpe': float(tm.get('sharpe_ratio', 0.0)),
            'test_max_dd': float(tm.get('max_drawdown', 0.0)),
            'bh_return': bh, 'bh_return_backtester': tm.get('benchmark_return_pct'),
            'excess': ret - bh,
        }
        eq, trades = getattr(w, '_eq', None), getattr(w, '_trades', None)
        if rerun_slip is not None and eq is None:
            m, eq, trades = run_segment(cls, w.best_params, bars, lo, hi, 0, rerun_slip)
            row['rerun_abs_diff'] = abs(float(m.get('return_pct', np.nan)) - ret) if m else float('nan')
        if eq:
            s, b = daily_series(eq, bars.close[lo:hi])
            row['_s'], row['_b'] = s.tolist(), b.tolist()
            row['_days'] = [str(t.date()) for t in bars.index[lo:hi]]
        if trades is not None:
            row['rt_won'], row['rt_done'] = round_trips(trades)
        rows.append(row)
    return rows


# ─── Tasks (run in worker processes) ─────────────────────────────────────────────────────────

def task(spec):
    name, etf, cfg, n_splits, slip, harness, tf_value, start, end = spec
    tf = Timeframe(tf_value)
    storage = DataStorage()
    sym = sym_of(etf)
    t0 = time.time()
    bars = Bars(storage, sym, tf)
    rerun = None
    if harness == 'standard':
        with quiet():
            res = walk_forward(strategy_name=name, symbols=[sym], timeframe=tf, start_date=start,
                               end_date=end, storage=storage, n_splits=n_splits, train_pct=TRAIN_PCT,
                               objective=OBJECTIVE, initial_capital=CAPITAL, risk_manager=full_invest_rm(),
                               slippage_bps=slip, commission_rate=RATE)
        engine = BatchRunner(None, class_of(name), [sym], tf, storage, CAPITAL, RATE, full_invest_rm(),
                             slip).engine
        skipped = len(res.get('summary', {}).get('skipped_windows', []))
        if slip == SLIP and tf == Timeframe.DAY_1:
            rerun = slip
    else:
        res = warm_walk_forward(name, bars, start, end, n_splits, slip, WARMUP_BARS)
        engine = 'python (warm-up harness)'
        skipped = None
    rows = window_rows(res, bars, class_of(name), rerun_slip=rerun)
    return {'candidate': name, 'etf': etf, 'config': cfg, 'slip': slip, 'harness': harness,
            'tf': tf_value, 'engine': engine, 'seconds': round(time.time() - t0, 1),
            'skipped_windows': skipped, 'windows': rows}


def harness_check(spec):
    """warm_walk_forward with no warm-up must reproduce walk_forward exactly."""
    name, etf, n_splits, start, end = spec
    storage = DataStorage()
    sym = sym_of(etf)
    bars = Bars(storage, sym, Timeframe.DAY_1)
    with quiet():
        ref = walk_forward(strategy_name=name, symbols=[sym], timeframe=Timeframe.DAY_1, start_date=start,
                           end_date=end, storage=storage, n_splits=n_splits, train_pct=TRAIN_PCT,
                           objective=OBJECTIVE, initial_capital=CAPITAL, risk_manager=full_invest_rm(),
                           slippage_bps=SLIP, commission_rate=RATE)
    mine = warm_walk_forward(name, bars, start, end, n_splits, SLIP, 0)
    a = [w.test_metrics.get('return_pct', 0.0) for w in ref['windows']]
    b = [w.test_metrics.get('return_pct', 0.0) for w in mine['windows']]
    pa = [w.best_params for w in ref['windows']]
    pb = [w.best_params for w in mine['windows']]
    return {'candidate': name, 'etf': etf, 'n_splits': n_splits,
            'same_params': pa == pb, 'windows': [len(a), len(b)],
            'max_abs_diff': max((abs(x - y) for x, y in zip(a, b)), default=0.0) if len(a) == len(b) else None,
            'identical_to_1e-9': len(a) == len(b) and pa == pb and all(abs(x - y) < 1e-9 for x, y in zip(a, b))}


def stress_task(spec):
    """Each stress period: choose the setting on the 375 days before the peak, then run it."""
    name, etf = spec
    storage = DataStorage()
    sym = sym_of(etf)
    bars = Bars(storage, sym, Timeframe.DAY_1)
    cls, grid = class_of(name), grid_of(name)
    out = []
    t0 = time.time()
    for label, s_day, e_day in STRESS:
        s_i, e_i = bars.pos(s_day), bars.pos(e_day)
        if s_i is None or e_i is None:
            out.append({'period': label, 'error': 'peak or trough date not in the data'})
            continue
        r_i = min(e_i + RECOVERY_BARS, len(bars.index) - 1)
        s_dt = datetime.strptime(s_day, '%Y-%m-%d')
        train_start = s_dt - timedelta(days=STRESS_TRAIN_DAYS)
        runner = BatchRunner(None, cls, [sym], Timeframe.DAY_1, storage, CAPITAL, RATE, full_invest_rm(), SLIP)
        with quiet():
            runs = runner.run(grid, train_start, s_dt, end_inclusive=False)
        best, best_key = {}, (False, float('-inf'))
        for params, (m, _) in zip(grid, runs):
            if m:
                key = (m.get('total_trades', 0) > 0, _get_objective_value(m, OBJECTIVE))
                if key > best_key:
                    best, best_key = dict(params), key
        if family_of(name) in SIGNAL_FAMILIES:
            lo = bars.bounds(train_start, s_dt, end_inclusive=False)[0]
        else:
            lo = s_i
        m, eq, trades = run_segment(cls, best, bars, lo, r_i + 1, 0, SLIP)
        if not eq:
            out.append({'period': label, 'error': m.get('error', 'no run')})
            continue
        eq_s, eq_e, eq_r = eq[s_i - lo], eq[e_i - lo], eq[r_i - lo]
        held = None
        qty = 0
        for t in trades:
            if pd.Timestamp(t['timestamp']) <= bars.index[s_i]:
                qty += t['qty'] if t['action'] == 'BUY' else -t['qty']
        held = qty > 0
        c = bars.close
        out.append({
            'period': label, 'peak': s_day, 'trough': e_day, 'recovery_end': str(bars.index[r_i].date()),
            'params': best, 'train_sharpe': best_key[1], 'train_traded': best_key[0],
            'run_from': str(bars.index[lo].date()), 'invested_at_peak': bool(held),
            'shock_return_pct': (eq_e / eq_s - 1) * 100, 'recovery_return_pct': (eq_r / eq_e - 1) * 100,
            'peak_to_recovery_pct': (eq_r / eq_s - 1) * 100,
            'bh_shock_pct': (c[e_i] / c[s_i] - 1) * 100, 'bh_recovery_pct': (c[r_i] / c[e_i] - 1) * 100,
            'bh_peak_to_recovery_pct': (c[r_i] / c[s_i] - 1) * 100,
            'trades_in_span': sum(1 for t in trades if pd.Timestamp(t['timestamp']) > bars.index[s_i]),
        })
    return {'candidate': name, 'etf': etf, 'periods': out, 'seconds': round(time.time() - t0, 1)}


# ─── Descriptive statistics (main process) ───────────────────────────────────────────────────

def variance_ratio(r, q):
    """Lo-MacKinlay VR(q) on overlapping q-sums, with the heteroskedasticity-robust z*."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    mu = r.mean()
    d = r - mu
    var1 = (d ** 2).sum() / (n - 1)
    cs_ = np.concatenate([[0.0], np.cumsum(r)])
    sums = cs_[q:] - cs_[:-q]
    m = q * (n - q + 1) * (1 - q / n)
    varq = ((sums - q * mu) ** 2).sum() / m
    vr = varq / var1
    # Campbell, Lo & MacKinlay (1997) eq. 2.4.44-2.4.45: delta_j without the factor n, so theta is
    # the asymptotic variance of VR itself and z* = (VR - 1) / sqrt(theta).
    denom = (d ** 2).sum() ** 2
    theta = 0.0
    for j in range(1, q):
        delta = ((d[j:] ** 2) * (d[:-j] ** 2)).sum() / denom
        theta += (2 * (q - j) / q) ** 2 * delta
    return float(vr), float((vr - 1) / np.sqrt(theta))


def tsmom(px: pd.Series, k):
    """Month-end sampling: next calendar month's return after a positive vs negative past-k-bar
    return. Returns counts, means, spread, Welch t and hit rate."""
    me = px.groupby([px.index.year, px.index.month]).tail(1)
    pos_in_full = px.index.get_indexer(me.index)
    rows = []
    for j in range(len(me) - 1):
        i = pos_in_full[j]
        if i - k < 0:
            continue
        past = px.iloc[i] / px.iloc[i - k] - 1
        nxt = me.iloc[j + 1] / me.iloc[j] - 1
        rows.append((past, nxt))
    if not rows:
        return {}
    a = np.array(rows)
    up, dn = a[a[:, 0] > 0, 1], a[a[:, 0] <= 0, 1]
    out = {'months': len(a), 'n_after_up': len(up), 'n_after_down': len(dn),
           'mean_next_after_up_pct': float(up.mean() * 100) if len(up) else None,
           'mean_next_after_down_pct': float(dn.mean() * 100) if len(dn) else None,
           'hit_rate_pct': float((np.sign(a[:, 0]) == np.sign(a[:, 1])).mean() * 100),
           'base_rate_up_months_pct': float((a[:, 1] > 0).mean() * 100)}
    if len(up) > 1 and len(dn) > 1:
        se = np.sqrt(up.var(ddof=1) / len(up) + dn.var(ddof=1) / len(dn))
        out['spread_pct'] = float((up.mean() - dn.mean()) * 100)
        out['welch_t'] = float((up.mean() - dn.mean()) / se) if se > 0 else None
    return out


def path_stats(r: np.ndarray):
    """Max drawdown and CVaR 5% of a return series (in %)."""
    r = np.asarray(r, dtype=float)
    if len(r) == 0:
        return {'max_dd_pct': None, 'cvar5_pct': None}
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    mdd = float(((peak - eq) / peak).max() * 100)
    q = np.quantile(r, 0.05)
    cvar = float(r[r <= q].mean() * 100)
    return {'max_dd_pct': mdd, 'cvar5_pct': cvar}


def descriptive(storage, start, end, tickers):
    lo, hi = pd.Timestamp(start, tz='UTC'), pd.Timestamp(end, tz='UTC') + pd.Timedelta(days=1)
    px = {t: storage.load_data(f'US_{t}', '1d')['close'] for t in tickers + [MARKET]}
    px = {t: s[(s.index >= lo) & (s.index < hi)] for t, s in px.items()}
    spy = px[MARKET]
    out = {}
    for t in tickers + [MARKET]:
        p = px[t]
        r = p.pct_change().dropna()
        lr = np.log(p).diff().dropna()
        yrs = (p.index[-1] - p.index[0]).days / 365.25
        eq = p / p.iloc[0]
        peak = eq.cummax()
        dd = eq / peak - 1
        trough = dd.idxmin()
        pk = eq.loc[:trough].idxmax()
        # calendar-year return = last close of year / last close of the previous year
        ye = p.groupby(p.index.year).last()
        cy = (ye / ye.shift(1) - 1).dropna() * 100
        roll = (p / p.shift(252) - 1).dropna() * 100
        x = r.align(spy.pct_change().dropna(), join='inner')
        beta = float(np.cov(x[0], x[1])[0, 1] / np.var(x[1], ddof=1))
        rec = {
            'first': str(p.index[0].date()), 'last': str(p.index[-1].date()), 'bars': int(len(p)),
            'total_return_pct': float((p.iloc[-1] / p.iloc[0] - 1) * 100),
            'cagr_pct': float(((p.iloc[-1] / p.iloc[0]) ** (1 / yrs) - 1) * 100),
            'ann_vol_pct': float(r.std() * np.sqrt(252) * 100),
            'sharpe_rf0': float(r.mean() / r.std() * np.sqrt(252)),
            'max_dd_pct': float(-dd.min() * 100), 'max_dd_peak': str(pk.date()), 'max_dd_trough': str(trough.date()),
            'cvar5_daily_pct': path_stats(r.to_numpy())['cvar5_pct'],
            'best_calendar_year': [int(cy.idxmax()), float(cy.max())],
            'worst_calendar_year': [int(cy.idxmin()), float(cy.min())],
            'calendar_years': {int(k): float(v) for k, v in cy.items()},
            'best_252d_pct': [str(roll.idxmax().date()), float(roll.max())],
            'worst_252d_pct': [str(roll.idxmin().date()), float(roll.min())],
            'beta_to_spy': beta,
            'lag1_autocorr': float(lr.autocorr(1)), 'autocorr_2se': float(2 / np.sqrt(len(lr))),
            'variance_ratio': {str(q): dict(zip(('vr', 'z_robust'), variance_ratio(lr.to_numpy(), q)))
                               for q in VR_LAGS},
            'tsmom': {str(k): tsmom(p, k) for k in TSMOM_LOOKBACKS},
        }
        if t != MARKET:
            rel = (p / spy.reindex(p.index)).dropna()
            rlr = np.log(rel).diff().dropna()
            rec['relative_to_spy'] = {
                'cagr_excess_pct': None,   # filled in below, once SPY's CAGR is known
                'variance_ratio': {str(q): dict(zip(('vr', 'z_robust'), variance_ratio(rlr.to_numpy(), q)))
                                   for q in VR_LAGS},
                'lag1_autocorr': float(rlr.autocorr(1)),
                'tsmom': {str(k): tsmom(rel, k) for k in TSMOM_LOOKBACKS},
            }
        vr21 = rec['variance_ratio']['21']
        rec['statistical_label'] = ('trend' if vr21['vr'] > 1 and vr21['z_robust'] > 1.96 else
                                    'reversal' if vr21['vr'] < 1 and vr21['z_robust'] < -1.96 else 'random walk')
        out[t] = rec
    for t in tickers:
        out[t]['relative_to_spy']['cagr_excess_pct'] = out[t]['cagr_pct'] - out[MARKET]['cagr_pct']
    return out


def data_checks(storage, tickers):
    import yfinance
    import inspect
    out = {'yfinance_version': yfinance.__version__,
           'yf_download_auto_adjust_default': inspect.signature(yfinance.download).parameters['auto_adjust'].default,
           'provider_passes_auto_adjust': 'auto_adjust' in (REPO / 'packages/midfreq-data/src/midfreq/data/providers/yfinance_provider.py').read_text(),
           'spy_2000_01_03_close': float(storage.load_data('US_SPY', '1d')['close'].iloc[0]),
           'spy_2000_01_03_raw_close_reference': 145.4375,
           'per_symbol': {}}
    for t in tickers + [MARKET]:
        d = storage.load_data(f'US_{t}', '1d')
        h = storage.load_data(f'US_{t}', '1h')
        hc = h['close'].groupby(h.index.normalize()).last()
        j = pd.concat([d['close'], hc], axis=1, keys=['d', 'h']).dropna()
        ratio = j['h'] / j['d']
        r = d['close'].pct_change().abs()
        per_day = pd.Series(1, index=h.index).groupby(h.index.normalize()).sum()
        out['per_symbol'][t] = {
            'daily_bars': int(len(d)), 'daily_first': str(d.index.min().date()), 'daily_last': str(d.index.max().date()),
            'daily_nan_closes': int(d['close'].isna().sum()), 'daily_duplicate_stamps': int(d.index.duplicated().sum()),
            'daily_zero_volume': int((d['volume'] <= 0).sum()),
            'daily_moves_over_8pct': [str(x.date()) for x in r[r > 0.08].index],
            'hourly_bars': int(len(h)), 'hourly_first': str(h.index.min()), 'hourly_last': str(h.index.max()),
            'hourly_bars_per_day': {int(k): int(v) for k, v in per_day.value_counts().items()},
            'hourly_to_daily_close_ratio_first_day': float(ratio.iloc[0]),
            'hourly_to_daily_close_ratio_last_day': float(ratio.iloc[-1]),
        }
    return out


# ─── Aggregation ─────────────────────────────────────────────────────────────────────────────

def summarise_windows(ws):
    rets = [w['test_return'] for w in ws]
    ex = [w['excess'] for w in ws]
    won = sum(w.get('rt_won', 0) for w in ws)
    done = sum(w.get('rt_done', 0) for w in ws)
    out = {
        'windows': len(rets),
        'mean_oos': float(np.mean(rets)) if rets else float('nan'),
        'consistency': (sum(1 for x in rets if x > 0) / len(rets) * 100) if rets else 0.0,
        'mean_bh': float(np.mean([w['bh_return'] for w in ws])) if ws else float('nan'),
        'mean_excess': float(np.mean(ex)) if ex else float('nan'),
        'pct_beat_bh': (sum(1 for x in ex if x > 0) / len(ex) * 100) if ex else 0.0,
        'trades_per_window': float(np.mean([w['test_trades'] for w in ws])) if ws else 0.0,
        'zero_trade_windows': sum(1 for w in ws if w['test_trades'] == 0),
        'rt_won': won, 'rt_done': done, 'rt_hit_rate': (won / done * 100) if done else None,
        'best_window': max(rets) if rets else None, 'worst_window': min(rets) if rets else None,
        'bh_best_window': max(w['bh_return'] for w in ws) if ws else None,
        'bh_worst_window': min(w['bh_return'] for w in ws) if ws else None,
        'oos_returns': rets, 'excess_returns': ex,
    }
    if ws and all('_s' in w for w in ws):
        s = np.concatenate([w['_s'] for w in ws])
        b = np.concatenate([w['_b'] for w in ws])
        ps, pb = path_stats(s), path_stats(b)
        out.update({'oos_days': int(len(s)), 'max_dd_pct': ps['max_dd_pct'], 'cvar5_pct': ps['cvar5_pct'],
                    'bh_max_dd_pct': pb['max_dd_pct'], 'bh_cvar5_pct': pb['cvar5_pct']})
    if ws and 'rerun_abs_diff' in ws[0]:
        out['rerun_max_abs_diff'] = float(np.nanmax([w['rerun_abs_diff'] for w in ws]))
    return out


def selector_windows(member_runs: Dict[str, List[dict]]):
    """Per window, the member with the best (traded, training Sharpe); ties to the first listed."""
    names = list(member_runs)
    n = min(len(v) for v in member_runs.values())
    out = []
    for i in range(n):
        best = max(names, key=lambda nm: (member_runs[nm][i]['train_traded'], member_runs[nm][i]['train_sharpe'],
                                          -names.index(nm)))
        w = dict(member_runs[best][i])
        w['picked'] = SHORT.get(best, best)
        out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser(description='Factor ETF strategy fit (pre-registered)')
    ap.add_argument('--smoke', action='store_true', help='2 ETFs, 2 bps only: a bug check')
    ap.add_argument('--jobs', type=int, default=MAX_JOBS)
    ap.add_argument('--out-dir', default=None, help='default: results/factor_search/')
    args = ap.parse_args()
    jobs = max(1, min(MAX_JOBS, args.jobs))
    out_dir = str(paths.results_dir() / 'factor_search') if args.out_dir is None else os.path.abspath(args.out_dir)
    t_all = time.time()

    storage = DataStorage()
    etfs = SMOKE_ETFS if args.smoke else ETFS
    syms = [sym_of(t) for t in ETFS]
    firsts = {t: storage.load_data(f'US_{t}', '1d').index.min() for t in ETFS}
    start = max(firsts.values()).to_pydatetime().replace(tzinfo=None)
    end = storage.latest_common_timestamp(syms, Timeframe.DAY_1.value)
    if start != EXPECTED_START:
        sys.exit(f'common span starts {start}, not the pre-registered {EXPECTED_START}')
    h_start = max(storage.load_data(f'US_{t}', '1h').index.min() for t in ETFS).to_pydatetime().replace(tzinfo=None)
    h_end = storage.latest_common_timestamp(syms, Timeframe.HOUR_1.value)
    print(f"Factor ETFs {etfs} | daily {start.date()} -> {end.date()} | hourly {h_start} -> {h_end} | "
          f"{RATE:.5f}/side + {SLIP} bps (line {SLIP_SENS} bps) | fully invested | jobs {jobs}", flush=True)

    t_desc = time.time()
    desc = descriptive(storage, start, end, etfs)
    checks = data_checks(storage, etfs)
    t_desc = time.time() - t_desc

    slips = [SLIP] if args.smoke else [SLIP, SLIP_SENS]
    D = Timeframe.DAY_1.value
    specs = []
    for name in CANDIDATES:
        for etf in etfs:
            for cfg, n in CONFIGS:
                for slip in slips:
                    specs.append((name, etf, cfg, n, slip, 'standard', D, start, end))
                if name in WARMUP_CANDIDATES:
                    specs.append((name, etf, cfg, n, SLIP, 'warmup', D, start, end))
    stress_specs = [(name, etf) for name in CANDIDATES for etf in etfs]
    checks_specs = [(nm, etfs[0], 15, start, end) for nm in (SMA, RSI, RS, OVL)]
    # slow Python jobs first, so the pool does not end on one long straggler
    order = {HMM: 0, OVL: 2, BH: 2}
    specs.sort(key=lambda s: (order.get(s[0], 1), s[5] != 'warmup'))
    print(f"{len(specs)} walk-forwards + {len(stress_specs)} stress runs + {len(checks_specs)} harness checks",
          flush=True)

    results, stress_out, check_out = [], [], []
    t_wf = time.time()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(harness_check, c): ('check', c) for c in checks_specs}
        futs.update({pool.submit(task, s): ('task', s) for s in specs})
        futs.update({pool.submit(stress_task, s): ('stress', s) for s in stress_specs})
        done = 0
        for f in as_completed(futs):
            kind, s = futs[f]
            r = f.result()
            if kind == 'check':
                check_out.append(r)
                print(f"  harness check {r['candidate']} {r['etf']} B: identical={r['identical_to_1e-9']} "
                      f"max diff {r['max_abs_diff']}", flush=True)
                continue
            if kind == 'stress':
                stress_out.append(r)
                continue
            results.append(r)
            done += 1
            rets = [w['test_return'] for w in r['windows']]
            print(f"  [{done:3d}/{len(specs)}] {SHORT[r['candidate']]:14s} {r['etf']:4s} {r['config']} "
                  f"{r['slip']:.0f}bps {r['harness']:8s} mean {np.mean(rets):+7.3f}% "
                  f"pos {sum(x > 0 for x in rets)}/{len(rets)} trades/w "
                  f"{np.mean([w['test_trades'] for w in r['windows']]):5.1f} | {r['seconds']:.0f}s {r['engine']}",
                  flush=True)
    t_wf = time.time() - t_wf

    # ─── Summaries: candidates, then family selectors ─────────────────────────────────────
    runs = {(r['candidate'], r['etf'], r['config'], r['slip'], r['harness']): r['windows'] for r in results}
    S = {}
    for key, ws in runs.items():
        S[key] = summarise_windows(ws)
    sel_windows = {}
    for fam, members in FAMILIES.items():
        for etf in etfs:
            for cfg, _ in CONFIGS:
                for slip in slips:
                    for harness in ('standard', 'warmup'):
                        mem = {m: runs.get((m, etf, cfg, slip, harness)) for m in members}
                        if any(v is None for v in mem.values()):
                            continue
                        ws = selector_windows(mem)
                        key = (f'[{fam} family selector]', etf, cfg, slip, harness)
                        sel_windows[key] = ws
                        S[key] = summarise_windows(ws)
                        S[key]['picked'] = [w['picked'] for w in ws]

    series_names = list(CANDIDATES) + [f'[{f} family selector]' for f in FAMILIES]

    def g(name, etf, cfg, slip=SLIP, harness='standard', field='mean_oos'):
        v = S.get((name, etf, cfg, slip, harness))
        return None if v is None else v.get(field)

    def passes(name, etf, slip=SLIP, harness='standard'):
        vals = [(g(name, etf, c, slip, harness), g(name, etf, c, slip, harness, 'consistency')) for c, _ in CONFIGS]
        return all(a is not None and qualifies(a, b) for a, b in vals)

    def earned(name, etf, slip=SLIP, harness='standard'):
        if not passes(name, etf, slip, harness):
            return False
        vals = [(g(name, etf, c, slip, harness, 'mean_excess'), g(name, etf, c, slip, harness, 'pct_beat_bh'))
                for c, _ in CONFIGS]
        return all(a is not None and a > 0 and b >= MIN_CONSISTENCY for a, b in vals)

    # ─── Reality Check (84 series per config) ─────────────────────────────────────────────
    rc = {}
    any_pass = any(passes(nm, etf) for nm in series_names if nm != BH for etf in etfs)
    for field, label in (('excess_returns', 'excess_over_buy_and_hold'), ('oos_returns', 'raw_test_return')):
        rc[label] = {}
        for cfg, _ in CONFIGS:
            use = {f'{nm} x {etf}': S[(nm, etf, cfg, SLIP, 'standard')][field]
                   for nm in series_names if nm != BH for etf in etfs if (nm, etf, cfg, SLIP, 'standard') in S}
            lens = {len(v) for v in use.values()}
            if len(lens) != 1:
                n_use = max(lens, key=lambda L: sum(len(v) == L for v in use.values()))
                use = {k: v for k, v in use.items() if len(v) == n_use}
            if len(use) < 2:
                continue
            res = reality_check(use, n_boot=N_BOOT)
            res['naive_p_value_of_best'] = naive_single_test(use[res['best_candidate']], n_boot=N_BOOT)
            res['naive_p_for_each_passing'] = {
                f'{nm} x {etf}': naive_single_test(use[f'{nm} x {etf}'], n_boot=N_BOOT)
                for nm in series_names if nm != BH for etf in etfs
                if passes(nm, etf) and f'{nm} x {etf}' in use}
            res.pop('candidate_means', None)
            rc[label][cfg] = res

    def fit_to_trade(name, etf):
        if not earned(name, etf):
            return False
        r = rc.get('excess_over_buy_and_hold', {})
        return all(cfg in r and r[cfg]['p_value'] < 0.05 for cfg, _ in CONFIGS)

    # ─── Per-ETF answers ─────────────────────────────────────────────────────────────────
    answers = {}
    for etf in etfs:
        fam_ex = {}
        for fam in FAMILIES:
            nm = f'[{fam} family selector]'
            if earned(nm, etf):
                fam_ex[fam] = np.mean([g(nm, etf, c, field='mean_excess') for c, _ in CONFIGS])
        winner = max(fam_ex, key=fam_ex.get) if fam_ex else None
        best_fam = max(FAMILIES, key=lambda f: np.mean([g(f'[{f} family selector]', etf, c) for c, _ in CONFIGS]))
        tr = [g('[trend family selector]', etf, c) for c, _ in CONFIGS]
        rv = [g('[reversal family selector]', etf, c) for c, _ in CONFIGS]
        strat_ev = ('trend' if all(a > b for a, b in zip(tr, rv)) else
                    'reversal' if all(b > a for a, b in zip(tr, rv)) else 'split')
        trw = [g('[trend family selector]', etf, c, harness='warmup') for c, _ in CONFIGS]
        rvw = [g('[reversal family selector]', etf, c, harness='warmup') for c, _ in CONFIGS]
        strat_ev_warm = (None if None in trw + rvw else
                         'trend' if all(a > b for a, b in zip(trw, rvw)) else
                         'reversal' if all(b > a for a, b in zip(trw, rvw)) else 'split')
        stat_ev = desc[etf]['statistical_label']
        label = (strat_ev if strat_ev == stat_ev and strat_ev in ('trend', 'reversal') else 'neither clearly')
        answers[etf] = {
            'q1_winning_family': winner or 'none (holding the ETF is the answer)',
            'families_that_earned_their_place': {k: float(v) for k, v in fam_ex.items()},
            'members_that_passed': [nm for nm in CANDIDATES if nm != BH and passes(nm, etf)],
            'members_that_earned_their_place': [nm for nm in CANDIDATES if nm != BH and earned(nm, etf)],
            'fit_to_trade': [nm for nm in series_names if nm != BH and fit_to_trade(nm, etf)],
            'best_family_for_hourly': best_fam,
            'q2_strategy_evidence': strat_ev, 'q2_strategy_evidence_warmup_diagnostic': strat_ev_warm,
            'q2_statistical_evidence': stat_ev, 'q2_label': label,
            'trend_selector_mean_oos_AB': tr, 'reversal_selector_mean_oos_AB': rv,
            'trend_selector_consistency_AB': [g('[trend family selector]', etf, c, field='consistency') for c, _ in CONFIGS],
            'reversal_selector_consistency_AB': [g('[reversal family selector]', etf, c, field='consistency') for c, _ in CONFIGS],
            'trend_selector_rt_hit_rate_AB': [g('[trend family selector]', etf, c, field='rt_hit_rate') for c, _ in CONFIGS],
            'reversal_selector_rt_hit_rate_AB': [g('[reversal family selector]', etf, c, field='rt_hit_rate') for c, _ in CONFIGS],
        }

    # ─── Hourly cross-check: the best family per ETF ─────────────────────────────────────
    H = Timeframe.HOUR_1.value
    h_specs = []
    for etf in etfs:
        for nm in FAMILIES[answers[etf]['best_family_for_hourly']]:
            for cfg, n in CONFIGS:
                h_specs.append((nm, etf, cfg, n, SLIP, 'standard', H, h_start, h_end))
    h_specs.sort(key=lambda s: order.get(s[0], 1))
    print(f"\nHourly cross-check: {len(h_specs)} walk-forwards", flush=True)
    t_h = time.time()
    h_results = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for f in as_completed([pool.submit(task, s) for s in h_specs]):
            r = f.result()
            h_results.append(r)
            rets = [w['test_return'] for w in r['windows']]
            print(f"  hourly {SHORT[r['candidate']]:14s} {r['etf']:4s} {r['config']} mean {np.mean(rets):+7.3f}% "
                  f"pos {sum(x > 0 for x in rets)}/{len(rets)} | {r['seconds']:.0f}s {r['engine']}", flush=True)
    t_h = time.time() - t_h
    h_runs = {(r['candidate'], r['etf'], r['config']): r['windows'] for r in h_results}
    hourly = {}
    for etf in etfs:
        fam = answers[etf]['best_family_for_hourly']
        rec = {'family': fam, 'members': {}, 'selector': {}}
        for cfg, _ in CONFIGS:
            mem = {m: h_runs.get((m, etf, cfg)) for m in FAMILIES[fam]}
            for m, ws in mem.items():
                if ws is not None:
                    rec['members'].setdefault(SHORT[m], {})[cfg] = {
                        k: v for k, v in summarise_windows(ws).items() if k not in ('oos_returns', 'excess_returns')}
            if all(v is not None for v in mem.values()):
                sw = selector_windows(mem)
                ss = summarise_windows(sw)
                rec['selector'][cfg] = {k: v for k, v in ss.items() if k not in ('oos_returns', 'excess_returns')}
                rec['selector'][cfg]['picked'] = [w['picked'] for w in sw]
        hourly[etf] = rec

    # ─── Tables ──────────────────────────────────────────────────────────────────────────
    stress_by = {(r['candidate'], r['etf']): r['periods'] for r in stress_out}
    rows = []
    for etf in etfs:
        for nm in series_names:
            if (nm, etf, 'A', SLIP, 'standard') not in S:
                continue
            fam = family_of(nm) if nm in CANDIDATES else nm.strip('[]').split()[0]
            row = {'etf': etf, 'candidate': nm, 'family': fam,
                   'grid_points': len(grid_of(nm)) if nm in CANDIDATES else None}
            for c, _ in CONFIGS:
                for fld, col in (('mean_oos', 'mean_oos_pct'), ('consistency', 'pct_windows_profitable'),
                                 ('mean_bh', 'mean_bh_pct'), ('mean_excess', 'mean_excess_pct'),
                                 ('pct_beat_bh', 'pct_windows_beat_bh'), ('trades_per_window', 'trades_per_window'),
                                 ('zero_trade_windows', 'zero_trade_windows'), ('rt_hit_rate', 'round_trip_hit_rate'),
                                 ('best_window', 'best_window_pct'), ('worst_window', 'worst_window_pct'),
                                 ('bh_best_window', 'bh_best_window_pct'), ('bh_worst_window', 'bh_worst_window_pct'),
                                 ('max_dd_pct', 'oos_max_dd_pct'), ('cvar5_pct', 'oos_cvar5_pct'),
                                 ('bh_max_dd_pct', 'bh_oos_max_dd_pct'), ('bh_cvar5_pct', 'bh_oos_cvar5_pct'),
                                 ('rerun_max_abs_diff', 'rerun_max_abs_diff')):
                    row[f'{c}_{col}'] = g(nm, etf, c, field=fld)
            row['PASS'] = passes(nm, etf)
            row['EARNED_vs_BH'] = earned(nm, etf)
            row['FIT_TO_TRADE'] = fit_to_trade(nm, etf) if nm != BH else None
            for c, _ in CONFIGS:
                row[f'{c}_mean_oos_pct_at_5bps'] = g(nm, etf, c, SLIP_SENS)
                row[f'{c}_pct_profitable_at_5bps'] = g(nm, etf, c, SLIP_SENS, field='consistency')
                row[f'{c}_mean_excess_pct_at_5bps'] = g(nm, etf, c, SLIP_SENS, field='mean_excess')
            row['PASS_at_5bps'] = passes(nm, etf, SLIP_SENS) if not args.smoke else None
            for c, _ in CONFIGS:
                row[f'DIAG_warmup_{c}_mean_oos_pct'] = g(nm, etf, c, harness='warmup')
                row[f'DIAG_warmup_{c}_pct_profitable'] = g(nm, etf, c, harness='warmup', field='consistency')
                row[f'DIAG_warmup_{c}_mean_excess_pct'] = g(nm, etf, c, harness='warmup', field='mean_excess')
                row[f'DIAG_warmup_{c}_trades_per_window'] = g(nm, etf, c, harness='warmup', field='trades_per_window')
            row['DIAG_warmup_would_meet_rule'] = (passes(nm, etf, harness='warmup')
                                                  if (nm, etf, 'A', SLIP, 'warmup') in S else None)
            for p in stress_by.get((nm, etf), []):
                k = p['period'].split()[0] if not p['period'].startswith('Apr') else 'Apr2025'
                k = {'COVID': 'covid', '2022': 'y2022', 'Apr2025': 'apr2025'}.get(k, k)
                row[f'stress_{k}_pct'] = p.get('shock_return_pct')
                row[f'stress_{k}_bh_pct'] = p.get('bh_shock_pct')
                row[f'stress_{k}_recovery_pct'] = p.get('recovery_return_pct')
                row[f'stress_{k}_bh_recovery_pct'] = p.get('bh_recovery_pct')
                row[f'stress_{k}_invested_at_peak'] = p.get('invested_at_peak')
            rows.append(row)
    table = pd.DataFrame(rows)

    n_primary = sum(len(grid_of(n)) for n in CANDIDATES) * len(etfs)
    n_diag = sum(len(grid_of(n)) for n in WARMUP_CANDIDATES) * len(etfs)
    n_hourly = sum(len(grid_of(m)) for etf in etfs for m in FAMILIES[answers[etf]['best_family_for_hourly']])

    stamp = time.strftime('%Y%m%d_%H%M%S')
    tag = 'factor_strategy_fit_smoke' if args.smoke else 'factor_strategy_fit'
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f'{tag}_{stamp}.csv')
    win_path = os.path.join(out_dir, f'{tag}_windows_{stamp}.csv')
    json_path = os.path.join(out_dir, f'{tag}_{stamp}.json')
    table.to_csv(csv_path, index=False)
    wrows = []
    for key, ws in list(runs.items()) + list(sel_windows.items()):
        for w in ws:
            wrows.append({'candidate': key[0], 'etf': key[1], 'config': key[2], 'slip_bps': key[3],
                          'harness': key[4], 'tf': '1d',
                          **{k: v for k, v in w.items() if not k.startswith('_')}})
    for r in h_results:
        for w in r['windows']:
            wrows.append({'candidate': r['candidate'], 'etf': r['etf'], 'config': r['config'], 'slip_bps': r['slip'],
                          'harness': 'standard', 'tf': '1h', **{k: v for k, v in w.items() if not k.startswith('_')}})
    pd.DataFrame(wrows).to_csv(win_path, index=False)

    summary_json = {f'{k[0]} | {k[1]} | {k[2]} | {k[3]}bps | {k[4]}':
                    {kk: vv for kk, vv in v.items()} for k, v in S.items()}
    meta = {
        'etfs': etfs, 'market': MARKET, 'start': str(start), 'end': str(end),
        'hourly_start': str(h_start), 'hourly_end': str(h_end),
        'configs': CONFIGS, 'train_pct': TRAIN_PCT, 'objective': OBJECTIVE,
        'commission_per_side': RATE, 'slippage_bps': SLIP, 'sensitivity_slippage_bps': SLIP_SENS,
        'capital': CAPITAL, 'sizing': f'RiskManager(max_drawdown_pct=1.0, EqualDollarSizer(1, {COST_BUFFER}))',
        'warmup_bars_diagnostic': WARMUP_BARS, 'stress_periods': STRESS, 'stress_train_days': STRESS_TRAIN_DAYS,
        'recovery_bars': RECOVERY_BARS,
        'grids': {n: grid_of(n) for n in CANDIDATES}, 'families': FAMILIES,
        'configurations_searched_primary': n_primary,
        'configurations_searched_primary_strategies_only': n_primary - len(etfs),
        'configurations_searched_warmup_diagnostic': n_diag,
        'configurations_searched_hourly': n_hourly,
        'configurations_searched_total': n_primary + n_diag + n_hourly,
        'walk_forward_candidates': len(CANDIDATES) * len(etfs), 'family_selectors': len(FAMILIES) * len(etfs),
        'reality_check_series_per_config': (len(series_names) - 1) * len(etfs),
        'any_candidate_or_selector_passed': any_pass,
        'smoke': args.smoke,
    }
    blob = {
        'meta': meta, 'answers': answers, 'descriptive': desc, 'data_checks': checks,
        'harness_check': check_out, 'reality_check': rc, 'stress': [r for r in stress_out],
        'hourly_cross_check': hourly, 'summaries': summary_json,
        'per_run': [{k: v for k, v in r.items() if k != 'windows'} for r in results],
        'runtime_seconds': {'descriptive': round(t_desc, 1), 'daily_walk_forwards_and_stress': round(t_wf, 1),
                            'hourly': round(t_h, 1), 'total': round(time.time() - t_all, 1),
                            'sum_of_task_seconds': round(sum(r['seconds'] for r in results), 1),
                            'sum_of_stress_seconds': round(sum(r['seconds'] for r in stress_out), 1),
                            'sum_of_hourly_task_seconds': round(sum(r['seconds'] for r in h_results), 1)},
    }
    with open(json_path, 'w') as fh:
        json.dump(blob, fh, indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))

    pd.set_option('display.width', 250)
    pd.set_option('display.max_rows', 500)
    cols = ['etf', 'candidate', 'A_mean_oos_pct', 'A_pct_windows_profitable', 'B_mean_oos_pct',
            'B_pct_windows_profitable', 'A_mean_excess_pct', 'B_mean_excess_pct', 'A_pct_windows_beat_bh',
            'B_pct_windows_beat_bh', 'PASS', 'EARNED_vs_BH']
    print('\n' + table[cols].round(2).to_string(index=False))
    for etf in etfs:
        a = answers[etf]
        print(f"{etf}: Q1 {a['q1_winning_family']} | Q2 {a['q2_label']} (strategies {a['q2_strategy_evidence']}, "
              f"warm {a['q2_strategy_evidence_warmup_diagnostic']}, statistics {a['q2_statistical_evidence']}) | "
              f"hourly family {a['best_family_for_hourly']}")
    for label, r in rc.items():
        for cfg, v in r.items():
            print(f"  Reality Check [{label}] {cfg}: p = {v['p_value']:.4f} (best {v['best_candidate']}, "
                  f"{v['observed_max_mean']:+.3f}%)")
    print(f"\nconfigurations: {n_primary} primary + {n_diag} warm-up diagnostic + {n_hourly} hourly = "
          f"{n_primary + n_diag + n_hourly}")
    print(f"wrote {csv_path}\n      {win_path}\n      {json_path}\ntotal {(time.time() - t_all) / 60:.1f} min")


if __name__ == '__main__':
    main()
