"""
Factor rotation on seven factor ETFs, and the momentum-versus-reversal disagreement test (pre-registered)
=========================================================================================================
Written and saved on 18 Oct 2026, before any of its numbers were computed. The candidates, grids,
costs, signal definitions, statistics and acceptance rules below are fixed; nothing is added or tuned
after seeing results. Anything tried afterwards goes in a separately labelled exploratory section, is
counted, and is never called a pass. The only thing run before this was written was a mechanics check
that printed fills and cash only, no returns (see "Sizing" below).

Question. NUSSIF's research tracker lists "equity factors: momentum / value / quality / low-risk premia
-> factor rotation" on MTUM, QUAL, VLUE, USMV, IWF, IWD and SPHD.
  A. Rank the seven against each other and hold a few: does factor momentum (hold the leaders) or
     contrarian factor timing (hold the laggards) earn more than simply holding all seven?
  B. When a short-term reversal signal and a medium-term momentum signal disagree on one of these ETFs,
     which has been right? ("If the market thinks a reversal is happening on QUAL but our strategy thinks
     momentum, we are betting against those reversal traders.")

Data (read-only). data/US_<T>/1d.parquet, written by the project's yfinance provider, which calls
yf.download without auto_adjust. yfinance 1.2.0 (installed) defaults to auto_adjust=True, so open, high,
low and close are split- AND dividend-adjusted, i.e. total-return closes. Checked: SPY's cached close on
2020-03-23 is 203.91 against the 222.95 printed that day. That is right for comparing high-dividend SPHD
with low-dividend MTUM or IWF. The seven share an identical daily calendar from QUAL's first bar
(2013-07-18) to 2026-10-16, with no missing or non-positive closes. SPY is the market benchmark. Hourly
bars are not used: two years of them cannot host 6- to 12-month signals, and the weekly and monthly
questions here are answered at daily resolution.

Span. 2014-07-21 -> 2026-10-16. 2014-07-21 is the first date with 253 earlier closes for all seven
(QUAL's 254th bar), so the longest lookback (252 days) has its full history in every segment from the
first, and every grid point is measured on the same days.

PART A: FACTOR ROTATION (walk-forward)
--------------------------------------
Strategies. The registry classes Cross-Sectional Momentum (hold the top_n leaders by trailing return)
and Cross-Sectional Reversal (hold the top_n laggards), unchanged in their ranking and selection, on
daily bars of the seven ETFs.

Two mechanical adaptations, made in study-local subclasses (nothing in packages/ changes), because on
daily bars with lookbacks of up to a year the registry mechanics do not measure the strategy:
  1. Warm-up. The backtester starts every segment with no history, so the class cannot rank until
     lookback+1 bars of the segment have passed. The test segments here are about 102 (A) and 62 (B)
     trading days, so lookbacks of 126 and 252 days would never trade out of sample and 63 would sit in
     cash for most of each segment. The subclass seeds each symbol's history with the lookback+1 closes
     before the segment's first bar (data known at that time, no look-ahead), forms the basket at the
     segment's first close, and rebalances every rebalance_every bars after that.
  2. Sizing. The registry basket sells the names leaving the basket, then buys each entrant with
     equity/top_n, and Portfolio rejects a buy that exceeds the cash on hand. When the names kept have
     risen relative to the names sold, the cash from the sales is less than equity/top_n and the entrant
     is rejected; the slot stays in cash until a kept name leaves. The mechanics check (registry
     classes, full span, lookback 63, 2 bps) found 362 rejected buys against 140 fills for momentum
     top_n=2 rebalanced every 5 days, and on average 24-27% of capital in cash for momentum at top_n 2
     and 3, against 2-7% for reversal. The asymmetry is structural: momentum keeps the winners. The
     subclass splits the cash on hand after the sales equally among the entrants (identical to the
     registry budget whenever the basket starts from cash), so the basket is always fully invested.
     Kept names are not re-weighted, as in the registry class.
  The adapted classes run on the Python backtester (the C++ engine runs only the registry classes).
  Checked before any result is read: (i) with both adaptations switched off, the subclass on the Python
  backtester reproduces the registry class on the C++ engine exactly, for every setting on every
  Config A test segment; (ii) with the warm-up on, the basket formed at each segment's first close is
  exactly the top_n by trailing return computed directly from the parquet closes; (iii) with the sizing
  on, no buy is ever rejected.

Grid (30 settings per strategy, 60 in all), as the brief fixes it:
  lookback          {5, 21, 63, 126, 252} trading days (1 week; 1, 3, 6 and 12 months)
  top_n             {1, 2, 3} of the seven
  rebalance_every   {5, 21} trading days (weekly, monthly)
  The "12-1" (skip the latest month) momentum variant is NOT run in Part A: the class ranks on
  close_t / close_{t-lookback} - 1 and has no skip parameter. Part B tests 12-1 directly.

Validation (the project's dual-configuration standard; optimizer.walk_forward is called unchanged, and
the study's grid and classes are supplied to it inside this process only):
  Config A: 9 windows (496 calendar days each: train 347, test 149, about 102 trading days)
  Config B: 15 windows (298 calendar days each: train 208, test 90, about 62 trading days)
  70% train. Each window grid-searches its training segment by Sharpe ratio and trades the chosen
  setting unchanged on its test segment. Capital 100,000; no risk manager.

Costs. Commission 0.00005 per side (the project's calibrated moomoo US rate) plus 2 bps slippage per
side; this decides. One sensitivity line at 5 bps slippage (selection redone at that cost).

Benchmarks, per test segment, from the same closes, bought at the segment's first close:
  EW7-monthly: the equal-weight portfolio of the seven, re-weighted to equal every 21 trading days,
     paying 2 bps + 0.00005 on the value traded at each re-weighting (entry fee-free, like the
     backtester's own benchmark). THE benchmark for "earns its place".
  EW7 buy-and-hold: the backtester's benchmark_return_pct (recomputed here and checked to match).
  SPY buy-and-hold, and buy-and-hold of each of the seven.

Acceptance (decided at 2 bps only).
  PASS (the brief's rule): under BOTH configurations, mean OOS return per window > 0 AND >= 50% of
     windows > 0. Over 2014-2026 nearly any fully invested long-only basket of US equity ETFs meets this,
     so on its own it says little. A rotation earns its place only if it also beats holding all seven:
  EARNS ITS PLACE: PASS, and the mean per-window excess over EW7-monthly > 0 under BOTH configurations.
  WORTH TRADING: EARNS ITS PLACE, and White's Reality Check on the per-window EXCESS over EW7-monthly
     (White 2000's benchmark-relative form) gives p < 0.05 under BOTH configurations.

Also reported (pre-registered, descriptive):
  * Every one of the 60 fixed settings traded unchanged on every test segment (no selection), with the
    same rules applied. A fixed setting is picked in hindsight from 60, so it is never a pass on its
    own: it counts only if it survives the Reality Check across all 60.
  * White's Reality Check (midfreq.research.reality_check; 5000 resamples, mean block 2, seed 12345),
    each configuration separately, at 2 bps, over (i) the 60 fixed settings and (ii) the 2 walk-forward
    candidates, each on raw per-window returns (the project's usual form) and on per-window excess over
    EW7-monthly. The naive single-test p of the best candidate alongside.
  * Registry-exact diagnostic: optimizer.walk_forward on the UNMODIFIED registry classes on the C++
    engine (cold start, registry sizing), same grid, windows and 2 bps costs. Two more walk-forward
    selections, counted, never a pass; they show what the project's machinery does with these ETFs as
    it stands.
  * Risk view, for each walk-forward candidate and for EW7-monthly, EW7 buy-and-hold, SPY and each of
    the seven, on the same test segments: best and worst test window; max drawdown of the test segments
    chained together, and the worst drawdown inside one window; CVaR 5% (mean of the worst 5% of daily
    returns, pooled over test days); returns over the stress periods where they fall inside a test
    segment (with the coverage); the share of windows beating EW7-monthly and SPY. Daily values come
    from re-running each test segment for its trades and marking the holdings at each day's closes
    (checked to reproduce the walk-forward's return).
  * Turnover (fills per window) and the share of out-of-sample days each ETF is held.
  * Full-span view, HINDSIGHT, never a pass: each of the 60 settings traded continuously over the whole
    span at 2 bps: CAGR, volatility, Sharpe, max drawdown, CVaR 5%, best and worst rolling 12 months,
    worst calendar year, and the three stress periods; the same for EW7-monthly, SPY and each of the
    seven. The test segments cover only 30% of the span and can miss a stress period; this is where the
    stress periods are always measured.
  Stress periods (S&P 500 peak to trough, close to close): Feb-Mar 2020 crash 2020-02-19 -> 2020-03-23;
  2022 rate shock 2022-01-03 -> 2022-10-12; April 2025 tariff shock 2025-04-02 -> 2025-04-08.

Count, Part A: 60 parameter settings (30 per strategy). Walk-forward selections: 2 primary + 2
registry-exact diagnostic. Configuration-cost pairs evaluated: 60 (primary, 2 bps) + 60 (registry-exact,
2 bps) + 60 (primary, 5 bps) = 180. Reality Check families: the 60 fixed settings; the 2 walk-forwards.

PART B: THE DISAGREEMENT TEST (weekly panel)
--------------------------------------------
Signal dates: the last trading day of each calendar week (W-FRI). Returns are close to close on the
adjusted daily closes, counted in trading days on each ETF's own series (the calendars are identical).
At signal date t (bar i):
  1-week return       r_1w   = C[i] / C[i-5]   - 1
  1-month return      r_1m   = C[i] / C[i-21]  - 1
  6-month return      r_6m   = C[i] / C[i-126] - 1
  12-1-month return   r_12_1 = C[i-21] / C[i-252] - 1     (12 months, skipping the latest month)
  forward 1 week      f_1w   = C[i+5]  / C[i] - 1
  forward 1 month     f_1m   = C[i+21] / C[i] - 1
Signals (+1 = buy, -1 = sell; a return of exactly 0 gives no signal and the observation is dropped):
  reversal   REV_1w = sign(-r_1w),   REV_1m   = sign(-r_1m)
  momentum   MOM_6m = sign(r_6m),    MOM_12_1 = sign(r_12_1)
Cases, for each of the four (reversal, momentum) pairs:
  both buy            MOM = +1, REV = +1   (up over the long window, down over the short: a pullback in an uptrend)
  both sell           MOM = -1, REV = -1   (down over the long window, up over the short: a bounce in a downtrend)
  mom buy / rev sell  MOM = +1, REV = -1   (up over both: reversal traders sell the recent winner)
  mom sell / rev buy  MOM = -1, REV = +1   (down over both: reversal traders buy the recent loser)
  The two signals disagree exactly when the short and the long return have the SAME sign.
Two versions:
  absolute         the ETF's own returns, as above;
  cross-sectional  every return, past and forward, is the ETF's return minus the equal-weight average
                   of the seven ETFs' returns over the same interval ("EW7"), so a signal says the ETF
                   led or lagged the group, and the forward return is its performance against the group.
Statistics, for each version x reversal signal x momentum signal x horizon (16 combinations):
  * Mean forward return (%) in each case, the number of observations, the share positive, a t-statistic.
  * Who wins the disagreement: D = mean(mom buy / rev sell) - mean(mom sell / rev buy). D > 0: momentum
    was right; D < 0: the reversal traders were right. Also the agreement spread
    mean(both buy) - mean(both sell).
  * Pooled across ETFs: OLS of the forward return on the four case dummies (no constant) over the stacked
    ETF-week panel, with Driscoll-Kraay standard errors, i.e. Newey-West (Bartlett) applied to the weekly
    cross-sectional sums of the scores, so the strong correlation between the seven in the same week and
    the serial correlation are both allowed for. Per ETF: the same regression on that ETF's weekly series
    with Newey-West standard errors. Lags: 4 weeks for the 1-week horizon, 8 for the 1-month horizon
    (21-day forward returns sampled weekly overlap about 4 observations). No small-sample correction.
  * Sample: signal dates with 252 earlier closes for all seven (from 2014-07-18) and an existing forward
    return.
Primary family: the pooled D for the 16 combinations, Holm-Bonferroni at 5% family-wise over these 16.
  Reading: "momentum wins" for a combination only if D > 0 and it survives Holm; "reversal wins" only if
  D < 0 and it survives; otherwise no reliable winner. Everything else (case means, agreement spreads,
  per-ETF numbers) is descriptive.
Worst periods of "betting with momentum against the reversal traders": a daily path for each of the 16
  combinations. At each signal date every ETF where the two signals disagree gets weight
  MOM / (number of such ETFs). Absolute version: a long or short position in the ETF. Cross-sectional
  version: the same position held against the EW7 (net weight w_j - sum(w)/7 on each of the seven).
  The 1-week path holds each week's weights until the next signal date; the 1-month path holds each
  week's weights for 21 trading days and averages the overlapping cohorts (Jegadeesh-Titman). Weights
  are constant within a holding period. Reported gross and net of 2 bps + 0.00005 per side on the change
  in target weights (shorting costs ignored): annualised mean, volatility, Sharpe, max drawdown, worst
  week (5 days), worst quarter (63 days), worst year (252 days), worst calendar year, the three stress
  periods, the share of days invested, and for the absolute version its beta to SPY.
Secondary, pre-registered, descriptive: the same statistics and paths on the longer histories: the
  absolute version on IWF, IWD and SPY from the first week with 252 earlier closes for all three (June
  2001); the cross-sectional version on IWF vs IWD (each against their equal-weight average; the two are
  mirror images). Extra stress periods there: 2001-06-01 -> 2002-10-09 (dot-com bear), 2007-10-09 ->
  2009-03-09 (financial crisis), 2009-03-09 -> 2009-05-29 (the 2009 momentum crash).
Count, Part B: 16 primary tests; every t-statistic computed is counted and printed.

Literature the results are checked against: Gupta & Kelly (2019), "Factor Momentum Everywhere", Journal
of Portfolio Management; Ehsani & Linnainmaa (2022), "Factor Momentum and the Momentum Factor", Journal
of Finance; Asness, Chandra, Ilmanen & Israel (2017), "Contrarian Factor Timing is Deceptively
Difficult", Journal of Portfolio Management. Those papers study long-short factor portfolios. These ETFs
are long-only and each is mostly the market (the factor tilt is a small part of its variance), so the
absolute version is dominated by the market, and the cross-sectional version is the closer analogue of a
factor return.

Compute: one process under nice -n 10; the Python backtester for the adapted classes, the C++ engine
(3 threads) for the registry-exact diagnostic. Reads data/ only; writes results/factor_search/ only (or
--out-dir), outside the results index (midfreq.research.results_index reads results/*.csv).

EXPLORATORY SECTION (added 18 Oct 2026 while the pre-registered run was in progress, before its
results were read, prompted by a mechanics finding, not by results; run only with --exploratory; adds
NO parameter settings; its two re-selections are counted as walk-forward selections 5 and 6; nothing in
it can be a pass):
  E1. The selection Sharpe. On multi-symbol bars the backtester records its per-candle equity at the
      first symbol's candle of each timestamp, so every other holding is marked at the PREVIOUS close
      (checked: the recorded curve equals that stale valuation exactly; its daily returns correlate 0.37
      with the day-end values in a 2018 test). Portfolio.calculate_metrics takes the Sharpe ratio, which
      walk_forward selects on, from that curve; return_pct is unaffected. E1 recomputes, for every
      training segment at 2 bps, each of the 30 settings' Sharpe from day-end values marked at that day's
      closes, reports how often the selection changes and the rank correlation of the two Sharpes, and
      reads the re-selected setting's out-of-sample return from the fixed-setting runs already made
      (same test segments, same costs).

Usage:
    nice -n 10 uv run python research/studies/factor_rotation.py [--smoke] [--out-dir DIR]
        [--n-boot 5000] [--part A|B|both]
    nice -n 10 uv run python research/studies/factor_rotation.py --exploratory --from-json RESULT.json
"""
import os
os.environ.setdefault('MIDFREQ_ENGINE_THREADS', '3')

import argparse
import contextlib
import io
import itertools
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from midfreq.core import paths
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.data.storage import DataStorage
from midfreq.backtest import optimizer
from midfreq.backtest.backtester import PythonBacktester
from midfreq.backtest.native import BatchRunner
from midfreq.research.reality_check import reality_check, naive_single_test
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY

DATA = paths.data_dir()
TF = Timeframe.DAY_1

# ── Pre-registered (see the docstring) ───────────────────────────────────────
TICKERS = ['MTUM', 'QUAL', 'VLUE', 'USMV', 'IWF', 'IWD', 'SPHD']
MARKET = 'US.SPY'
STRATEGIES = ['Cross-Sectional Momentum', 'Cross-Sectional Reversal']
GRID_AXES = {'lookback': [5, 21, 63, 126, 252], 'top_n': [1, 2, 3], 'rebalance_every': [5, 21]}
GRID = [dict(zip(GRID_AXES, combo)) for combo in itertools.product(*GRID_AXES.values())]
MAX_LOOKBACK = 252
CONFIGS = [('A (9 windows)', 9), ('B (15 windows)', 15)]
TRAIN_PCT = 0.7
OBJECTIVE = 'sharpe_ratio'
COMMISSION = 0.00005
SLIP_DECISION, SLIP_SENSITIVITY = 2.0, 5.0
MIN_CONSISTENCY = 50.0
CAPITAL = 100_000.0
EW_REBALANCE = 21
RC_BLOCK, RC_SEED = 2.0, 12345
STRESS = {'covid_crash_2020': ('2020-02-19', '2020-03-23'),
          'rate_shock_2022': ('2022-01-03', '2022-10-12'),
          'tariff_shock_2025': ('2025-04-02', '2025-04-08')}
STRESS_LONG = {'dotcom_2001_02': ('2001-06-01', '2002-10-09'),
               'financial_crisis_2007_09': ('2007-10-09', '2009-03-09'),
               'momentum_crash_2009': ('2009-03-09', '2009-05-29')}

# Part B
REV = {'rev_1w': 5, 'rev_1m': 21}
MOM = {'mom_6m': (126, 0), 'mom_12_1': (252, 21)}
HORIZONS = {'1w': 5, '1m': 21}
NW_LAGS = {'1w': 4, '1m': 8}
CASES = ['both_buy', 'both_sell', 'mom_buy_rev_sell', 'mom_sell_rev_buy']
VERSIONS = ['absolute', 'cross_sectional']


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def short(strategy):
    return strategy.replace('Cross-Sectional ', 'CS-')


def pkey(p):
    return f"lb{p['lookback']}_n{p['top_n']}_rb{p['rebalance_every']}"


def qualifies(mean, consistency):
    return mean > 0 and consistency >= MIN_CONSISTENCY


def sym(t):
    return f'US.{t}'


# ── Data ─────────────────────────────────────────────────────────────────────

CLOSES = {}   # symbol -> adjusted daily closes (UTC index); the warm-up reads these


def load_closes(storage, symbols):
    for s in symbols:
        if s not in CLOSES:
            df = storage.load_data(s.replace('.', '_'), TF.value)
            CLOSES[s] = df['close'].astype(float)
    return pd.DataFrame({s: CLOSES[s] for s in symbols}).sort_index()


def segment(panel, ts, te, end_inclusive=True):
    """The panel rows the backtester replays for [ts, te] (its own date filter)."""
    lo = pd.to_datetime(ts, utc=True)
    hi = pd.to_datetime(te, utc=True) + (pd.Timedelta(days=1) if end_inclusive else pd.Timedelta(0))
    return panel[(panel.index >= lo) & (panel.index < hi)]


def span_start(panel):
    """First date with MAX_LOOKBACK + 1 earlier closes for every column."""
    firsts = [panel.index.get_loc(panel[c].first_valid_index()) for c in panel.columns]
    return panel.index[max(firsts) + MAX_LOOKBACK + 1]


def naive(ts):
    return pd.Timestamp(ts).tz_convert(None).to_pydatetime() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).to_pydatetime()


def wf_bounds(start, end, n_splits):
    """optimizer.walk_forward's window date math (pure date arithmetic), for the validation runs."""
    size = (end - start).days // n_splits
    out = []
    for w in range(n_splits):
        ws = start + timedelta(days=w * size)
        tr_end = ws + timedelta(days=int(size * TRAIN_PCT))
        out.append((ws, tr_end, tr_end, ws + timedelta(days=size)))
    return out


# ── The study's adaptation of the registry classes (see the docstring) ───────

def rotation_class(base, warm=True, full=True):
    """The registry class with (1) warm-up and (2) fully invested sizing; either can be switched off."""
    class Rotation(base):
        WARM, FULL = warm, full

        def __init__(self, portfolio, **kwargs):
            super().__init__(portfolio, **kwargs)
            self._basket = []
            if self.WARM:
                self._next_rebalance_at = 1          # form the basket at the segment's first close

        def on_data(self, symbol, candle):
            if self.WARM and symbol not in self.history:
                closes = CLOSES[symbol]
                prior = closes[closes.index < candle.timestamp]
                if len(prior) < self.lookback + 1:
                    raise RuntimeError(f"warm-up: {symbol} has {len(prior)} closes before "
                                       f"{candle.timestamp}, needs {self.lookback + 1}")
                self.history[symbol] = [float(x) for x in prior.iloc[-(self.lookback + 1):]]
            super().on_data(symbol, candle)

        def _select_basket(self, ranked):
            self._basket = super()._select_basket(ranked)
            return self._basket

        def _position_budget(self):
            if not self.FULL:
                return super()._position_budget()
            held = [s for s, p in self.portfolio.positions.items() if p.get('qty', 0) > 0]
            entrants = [s for s in self._basket if s not in held]
            k = min(len(entrants), max(0, self.top_n - len(held)))
            return self.portfolio.cash / k if k else 0.0

    Rotation.__name__ = Rotation.__qualname__ = (f"Rotation{base.__name__}"
                                                 f"{'Warm' if warm else ''}{'Full' if full else ''}")
    return Rotation


ROTATION = {s: rotation_class(STRATEGY_REGISTRY[s]['class']) for s in STRATEGIES}


def study_name(strategy):
    return f"{strategy} (factor rotation study)"


@contextlib.contextmanager
def study_registry():
    """walk_forward reads its class from STRATEGY_REGISTRY and its grid from generate_param_grid:
    give it this study's, inside this process only."""
    original = optimizer.generate_param_grid
    optimizer.generate_param_grid = lambda name: [dict(p) for p in GRID]
    for s in STRATEGIES:
        STRATEGY_REGISTRY[study_name(s)] = {'class': ROTATION[s], 'params': STRATEGY_REGISTRY[s]['params']}
    try:
        yield
    finally:
        optimizer.generate_param_grid = original
        for s in STRATEGIES:
            STRATEGY_REGISTRY.pop(study_name(s), None)


def run_backtest(cls, symbols, params, ts, te, slip, storage):
    """One backtest on the Python engine, keeping the portfolio (for its trades) and the printed log."""
    pf = Portfolio(initial_cash=CAPITAL, commission_rate=COMMISSION)
    bt = PythonBacktester(storage=storage, portfolio=pf, risk_manager=None, slippage_bps=slip)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m = bt.run(cls, symbols=symbols, timeframe=TF, start_date=ts, end_date=te, **params)
    return m, pf, buf.getvalue()


# ── Paths and risk measures ──────────────────────────────────────────────────

def daily_book(px, trades, capital):
    """Day-end value and weights of a backtest, from its trades, marked at each day's closes."""
    trades = sorted(trades, key=lambda t: pd.Timestamp(t['timestamp']))
    qty, cash, k, vals, wts = {}, capital, 0, [], []
    for t in px.index:
        while k < len(trades) and pd.Timestamp(trades[k]['timestamp']) <= t:
            tr = trades[k]
            qty[tr['symbol']] = qty.get(tr['symbol'], 0) + (tr['qty'] if tr['action'] == 'BUY' else -tr['qty'])
            cash = tr['cash_after']
            k += 1
        row = px.loc[t]
        pos = {s: q * row[s] for s, q in qty.items() if q}
        v = cash + sum(pos.values())
        vals.append(v)
        wts.append({s: pv / v for s, pv in pos.items()})
    return (pd.Series(vals, index=px.index),
            pd.DataFrame(wts, index=px.index).reindex(columns=px.columns).fillna(0.0))


def ew_rebalanced_path(px, every, cost_rate):
    """Value (1.0 at the first close) of an equal-weight portfolio of px's columns, bought fee-free at
    the first close, re-weighted to equal every `every` bars, paying cost_rate on the value traded."""
    r = px.pct_change().fillna(0.0).values
    n = px.shape[1]
    hold = np.full(n, 1.0 / n)
    vals = [1.0]
    for k in range(1, len(px)):
        hold = hold * (1 + r[k])
        if k % every == 0 and k < len(px) - 1:
            v = hold.sum()
            traded = np.abs(v / n - hold).sum()
            hold = np.full(n, (v - traded * cost_rate) / n)
        vals.append(hold.sum())
    return pd.Series(vals, index=px.index)


def returns_from_values(values, base):
    """Daily returns of a value path, the first measured from `base` (capital, or 1.0 at the first close)."""
    v = pd.concat([pd.Series([base]), pd.Series(values.values)], ignore_index=True)
    return pd.Series(v.pct_change().iloc[1:].values, index=values.index)


def max_drawdown(rets):
    path = (1 + rets).cumprod()
    path = pd.concat([pd.Series([1.0]), pd.Series(path.values)], ignore_index=True)
    return float((1 - path / path.cummax()).max() * 100)


def cvar5(rets):
    q = rets.quantile(0.05)
    return float(rets[rets <= q].mean() * 100)


def stress_returns(rets, calendar, periods):
    """Compound return over each period (close of its first day to close of its last), with coverage:
    the days of the period present in `rets` out of the trading days in the period."""
    out = {}
    for name, (s, e) in periods.items():
        s, e = pd.Timestamp(s, tz='UTC'), pd.Timestamp(e, tz='UTC')
        days = calendar[(calendar > s) & (calendar <= e)]
        r = rets[(rets.index > s) & (rets.index <= e)]
        out[name] = {'return_pct': float(((1 + r).prod() - 1) * 100) if len(r) else None,
                     'days_covered': int(len(r)), 'days_in_period': int(len(days))}
    return out


def window_risk(window_rets, window_returns, calendar):
    """Risk view over walk-forward test segments: daily returns per window, chained across windows."""
    pooled = pd.concat(window_rets)
    wr = np.array(window_returns, dtype=float)
    return {
        'best_window_pct': float(wr.max()), 'worst_window_pct': float(wr.min()),
        'max_drawdown_chained_pct': max_drawdown(pooled),
        'worst_drawdown_in_a_window_pct': max(max_drawdown(r) for r in window_rets),
        'cvar5_daily_pct': cvar5(pooled),
        'days': int(len(pooled)),
        'stress': stress_returns(pooled, calendar, STRESS),
    }


def full_stats(rets, calendar, periods):
    """The full-span (hindsight) measures of one daily return path."""
    n = len(rets)
    growth = float((1 + rets).prod())
    roll = (1 + rets).rolling(252).apply(np.prod, raw=True) - 1
    years = (1 + rets).groupby(rets.index.year).prod() - 1
    sd = rets.std()
    return {
        'total_pct': (growth - 1) * 100, 'cagr_pct': (growth ** (252 / n) - 1) * 100,
        'vol_pct': float(sd * math.sqrt(252) * 100),
        'sharpe': float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0,
        'max_drawdown_pct': max_drawdown(rets), 'cvar5_daily_pct': cvar5(rets),
        'best_12m_pct': float(roll.max() * 100), 'worst_12m_pct': float(roll.min() * 100),
        'worst_year': int(years.idxmin()), 'worst_year_pct': float(years.min() * 100),
        'best_year': int(years.idxmax()), 'best_year_pct': float(years.max() * 100),
        'stress': stress_returns(rets, calendar, periods),
    }


# ── PART A ───────────────────────────────────────────────────────────────────

def run_wf(strategy, symbols, start, end, n_splits, slip, storage, mechanics='study'):
    name, engine = (study_name(strategy), 'python') if mechanics == 'study' else (strategy, 'native')
    with study_registry(), quiet():
        res = optimizer.walk_forward(
            strategy_name=name, symbols=symbols, timeframe=TF, start_date=start, end_date=end,
            storage=storage, n_splits=n_splits, train_pct=TRAIN_PCT, objective=OBJECTIVE,
            initial_capital=CAPITAL, slippage_bps=slip, commission_rate=COMMISSION, engine=engine)
    wins = []
    for w in res['windows']:
        tm = w.test_metrics
        if 'benchmark_return_pct' not in tm or w.best_params not in GRID:
            # the Python engine swallows a run's exception and returns {}: never let that pass silently
            raise RuntimeError(f"{name} window {w.window_id}: empty test run or params {w.best_params}")
        wins.append({'window': w.window_id, 'train_start': w.train_start, 'train_end': w.train_end,
                     'test_start': w.test_start, 'test_end': w.test_end, 'params': dict(w.best_params),
                     'train_return': w.train_metrics.get('return_pct', 0.0),
                     'train_sharpe': w.train_metrics.get('sharpe_ratio', 0.0),
                     'return': tm['return_pct'], 'ew7_bh_backtester': tm['benchmark_return_pct'],
                     'fills': int(tm.get('total_trades', 0)), 'test_sharpe': tm.get('sharpe_ratio', 0.0)})
    return wins, [dict(x) for x in res['summary'].get('skipped_windows', [])]


def run_fixed(strategy, symbols, bounds, slip, storage):
    runner = BatchRunner('python', ROTATION[strategy], symbols, TF, storage, CAPITAL, COMMISSION, None, slip)
    out = {pkey(p): [] for p in GRID}
    for ts, te in bounds:
        for p, (m, _) in zip(GRID, runner.run(GRID, ts, te)):
            if not m:
                raise RuntimeError(f"{strategy} {pkey(p)} {ts}: empty run")
            out[pkey(p)].append({'return': m['return_pct'], 'fills': int(m.get('total_trades', 0)),
                                 'ew7_bh_backtester': m['benchmark_return_pct']})
    return out


def window_benchmarks(panel, universe, ts, te, slip):
    seg = segment(panel, ts, te)
    u = seg[universe]
    ew_m = ew_rebalanced_path(u, EW_REBALANCE, slip / 1e4 + COMMISSION)
    bh = u.iloc[-1] / u.iloc[0] - 1
    spy = seg[MARKET]
    paths_ = {'EW7-monthly': ew_m, 'EW7 buy-and-hold': (u / u.iloc[0]).mean(axis=1),
              'SPY': spy / spy.iloc[0]}
    for s in universe:
        paths_[s.replace('US.', '')] = u[s] / u[s].iloc[0]
    rets = {k: (v.iloc[-1] - 1) * 100 for k, v in paths_.items()}
    rets['EW7 buy-and-hold check'] = float(bh.mean() * 100)
    return rets, {k: returns_from_values(v, 1.0) for k, v in paths_.items()}


def summarise(rets, bench):
    """Per-window strategy returns against the per-window benchmark dicts."""
    r = np.array(rets, dtype=float)
    out = {'windows': len(r), 'oos_mean': float(r.mean()), 'consistency': float((r > 0).mean() * 100),
           'best_window': float(r.max()), 'worst_window': float(r.min()), 'oos_returns': [float(x) for x in r]}
    for b in ('EW7-monthly', 'EW7 buy-and-hold', 'SPY'):
        ex = r - np.array([w[b] for w in bench])
        out[f'excess_{b}'] = float(ex.mean())
        out[f'pct_windows_beating_{b}'] = float((ex > 0).mean() * 100)
        out[f'excess_returns_{b}'] = [float(x) for x in ex]
    return out


def validate(storage, universe, panel, start, end):
    """Checks (i) and (ii) of the docstring, on Config A's test segments. Returns a dict of results."""
    out = {}
    bounds = [(b[2], b[3]) for b in wf_bounds(start, end, CONFIGS[0][1])]
    # (i) both adaptations off == registry class on the C++ engine
    mism = []
    for s in STRATEGIES:
        off = rotation_class(STRATEGY_REGISTRY[s]['class'], warm=False, full=False)
        nat = BatchRunner('native', STRATEGY_REGISTRY[s]['class'], universe, TF, storage, CAPITAL, COMMISSION,
                          None, SLIP_DECISION)
        py = BatchRunner('python', off, universe, TF, storage, CAPITAL, COMMISSION, None, SLIP_DECISION)
        for ts, te in bounds:
            for p, (a, _), (b, _) in zip(GRID, nat.run(GRID, ts, te), py.run(GRID, ts, te)):
                if (not a or not b or abs(a['return_pct'] - b['return_pct']) > 1e-9
                        or a['total_trades'] != b['total_trades']):
                    mism.append(f"{short(s)} {pkey(p)} {ts.date()}")
    out['i_adaptations_off_equals_registry_native'] = {'runs': 2 * len(GRID) * len(bounds), 'mismatches': mism}
    # (ii) warm-up: the first basket is the top_n by trailing return from the parquet closes
    bad, checked = [], 0
    for s in STRATEGIES:
        for ts, te in bounds:
            seg = segment(panel[universe], ts, te)
            t0 = seg.index[0]
            pos0 = panel.index.get_loc(t0)
            for p in GRID:
                if p['top_n'] > len(universe):
                    continue
                m, pf, log = run_backtest(ROTATION[s], universe, p, ts, te, SLIP_DECISION, storage)
                first = sorted(t['symbol'] for t in pf.trade_history
                               if pd.Timestamp(t['timestamp']) == t0 and t['action'] == 'BUY')
                trail = panel[universe].iloc[pos0] / panel[universe].iloc[pos0 - p['lookback']] - 1
                ranked = trail.sort_values(kind='mergesort')
                want = ranked.index[-p['top_n']:] if 'Momentum' in s else ranked.index[:p['top_n']]
                checked += 1
                if first != sorted(want) or 'REJECTED' in log:
                    bad.append(f"{short(s)} {pkey(p)} {ts.date()}: bought {first}, expected {sorted(want)}")
    out['ii_first_basket_equals_trailing_rank'] = {'runs': checked, 'mismatches': bad}
    return out


def part_a(storage, universe, n_boot, smoke):
    t_all = time.time()
    panel = load_closes(storage, universe + [MARKET])
    calendar = panel.index
    start = naive(span_start(panel[universe]))
    end = naive(panel[universe].dropna().index.max())
    print(f"\n=== PART A: factor rotation | {len(universe)} ETFs | {start.date()} -> {end.date()} | "
          f"grid {len(GRID)} per strategy ===", flush=True)
    A = {'span': [str(start), str(end)], 'universe': universe, 'runtime_s': {}}

    t0 = time.time()
    A['validation'] = validate(storage, universe, panel, start, end)
    A['runtime_s']['validation'] = round(time.time() - t0, 1)
    for k, v in A['validation'].items():
        print(f"  validation {k}: {v['runs']} runs, {len(v['mismatches'])} mismatches"
              + (f" e.g. {v['mismatches'][:3]}" if v['mismatches'] else ''), flush=True)
    if any(v['mismatches'] for v in A['validation'].values()):
        raise SystemExit("validation failed: stopping before any result is computed")

    wf, fixed, bench, bench_paths, bounds = {}, {}, {}, {}, {}
    for cname, n_splits in CONFIGS:
        for slip in (SLIP_DECISION, SLIP_SENSITIVITY):
            for s in STRATEGIES:
                t0 = time.time()
                wins, skipped = run_wf(s, universe, start, end, n_splits, slip, storage)
                b = [(w['test_start'], w['test_end']) for w in wins]
                want = [(x[2], x[3]) for x in wf_bounds(start, end, n_splits)]
                if b != want[:len(b)] or skipped:
                    raise RuntimeError(f"window bounds differ from the date math, or skipped: {skipped}")
                bounds.setdefault(cname, b)
                t1 = time.time()
                fx = run_fixed(s, universe, b, slip, storage)
                mism = [w['window'] for i, w in enumerate(wins)
                        if abs(fx[pkey(w['params'])][i]['return'] - w['return']) > 1e-9]
                if mism:
                    raise RuntimeError(f"walk-forward test run != fixed setting run in windows {mism}")
                wf[(s, slip, cname)] = wins
                fixed[(s, slip, cname)] = fx
                A['runtime_s'][f"{short(s)} {slip}bps {cname}"] = {'walk_forward': round(t1 - t0, 1),
                                                                    'fixed': round(time.time() - t1, 1)}
                print(f"  {short(s):6s} {slip:.0f} bps {cname:15s} | walk-forward {t1-t0:5.1f}s | "
                      f"fixed {time.time()-t1:5.1f}s", flush=True)
        for slip in (SLIP_DECISION, SLIP_SENSITIVITY):
            bench[(slip, cname)], bench_paths[(slip, cname)] = zip(*[
                window_benchmarks(panel, universe, ts, te, slip) for ts, te in bounds[cname]])
        # the backtester's own benchmark is the EW buy-and-hold recomputed here
        bad = [i for i, (w, bm) in enumerate(zip(wf[(STRATEGIES[0], SLIP_DECISION, cname)],
                                                 bench[(SLIP_DECISION, cname)]))
               if abs(w['ew7_bh_backtester'] - bm['EW7 buy-and-hold check']) > 1e-6
               or abs(bm['EW7 buy-and-hold'] - bm['EW7 buy-and-hold check']) > 1e-6]
        A['validation'][f'v_benchmark_matches_backtester_{cname}'] = {'runs': len(bounds[cname]), 'mismatches': bad}

    # registry-exact diagnostic (C++ engine, unmodified classes)
    reg = {}
    for cname, n_splits in CONFIGS:
        for s in STRATEGIES:
            t0 = time.time()
            reg[(s, cname)], _ = run_wf(s, universe, start, end, n_splits, SLIP_DECISION, storage,
                                        mechanics='registry')
            A['runtime_s'][f"registry-exact {short(s)} {cname}"] = round(time.time() - t0, 1)

    # summaries and table rows
    rows, A['walk_forward'], A['fixed'], A['registry_exact'] = [], {}, {}, {}
    for slip in (SLIP_DECISION, SLIP_SENSITIVITY):
        for s in STRATEGIES:
            per = {c: summarise([w['return'] for w in wf[(s, slip, c)]], bench[(slip, c)]) for c, _ in CONFIGS}
            for c, _ in CONFIGS:
                per[c]['fills_mean'] = float(np.mean([w['fills'] for w in wf[(s, slip, c)]]))
                per[c]['params_chosen'] = {k: int(v) for k, v in pd.Series(
                    [pkey(w['params']) for w in wf[(s, slip, c)]]).value_counts().items()}
                per[c]['windows'] = [{k: (str(v) if isinstance(v, datetime) else v) for k, v in w.items()}
                                     for w in wf[(s, slip, c)]]
            A['walk_forward'][f"{s} | {slip}bps"] = per
            rows.append(_row('walk_forward', s, '', slip, per))
            fx_s = {}
            for p in GRID:
                k = pkey(p)
                fper = {c: summarise([x['return'] for x in fixed[(s, slip, c)][k]], bench[(slip, c)])
                        for c, _ in CONFIGS}
                for c, _ in CONFIGS:
                    fper[c]['fills_mean'] = float(np.mean([x['fills'] for x in fixed[(s, slip, c)][k]]))
                fx_s[k] = fper
                rows.append(_row('fixed_setting', s, k, slip, fper))
            A['fixed'][f"{s} | {slip}bps"] = fx_s
    for s in STRATEGIES:
        per = {c: summarise([w['return'] for w in reg[(s, c)]], bench[(SLIP_DECISION, c)]) for c, _ in CONFIGS}
        for c, _ in CONFIGS:
            per[c]['fills_mean'] = float(np.mean([w['fills'] for w in reg[(s, c)]]))
            per[c]['zero_fill_windows'] = int(sum(w['fills'] == 0 for w in reg[(s, c)]))
            per[c]['params_chosen'] = {k: int(v) for k, v in pd.Series(
                [pkey(w['params']) for w in reg[(s, c)]]).value_counts().items()}
        A['registry_exact'][s] = per
        rows.append(_row('walk_forward_registry_exact', s, '', SLIP_DECISION, per))
    for b in ['EW7-monthly', 'EW7 buy-and-hold', 'SPY'] + [t.replace('US.', '') for t in universe]:
        per = {c: summarise([w[b] for w in bench[(SLIP_DECISION, c)]], bench[(SLIP_DECISION, c)])
               for c, _ in CONFIGS}
        rows.append(_row('benchmark', b, '', SLIP_DECISION, per))

    # risk view at the decision cost: walk-forward candidates and benchmarks on the same test segments
    A['risk_view'] = {}
    for c, _ in CONFIGS:
        rv = {}
        for s in STRATEGIES:
            wrets, held, mism = [], [], []
            for i, w in enumerate(wf[(s, SLIP_DECISION, c)]):
                m, pf, log = run_backtest(ROTATION[s], universe, w['params'], w['test_start'], w['test_end'],
                                          SLIP_DECISION, storage)
                seg = segment(panel[universe], w['test_start'], w['test_end'])
                vals, wts = daily_book(seg, pf.trade_history, CAPITAL)
                if (abs(m['return_pct'] - w['return']) > 1e-9
                        or abs((vals.iloc[-1] / CAPITAL - 1) * 100 - w['return']) > 1e-6 or 'REJECTED' in log):
                    mism.append(w['window'])
                wrets.append(returns_from_values(vals, CAPITAL))
                held.append(wts)
            r = window_risk(wrets, [w['return'] for w in wf[(s, SLIP_DECISION, c)]], calendar)
            r['rerun_mismatches'] = mism
            h = pd.concat(held)
            r['share_of_oos_days_held_pct'] = {k.replace('US.', ''): float((h[k] > 0).mean() * 100) for k in h}
            r['pct_windows_beating_EW7-monthly'] = A['walk_forward'][f"{s} | {SLIP_DECISION}bps"][c][
                'pct_windows_beating_EW7-monthly']
            r['pct_windows_beating_SPY'] = A['walk_forward'][f"{s} | {SLIP_DECISION}bps"][c]['pct_windows_beating_SPY']
            rv[s] = r
        for b in bench_paths[(SLIP_DECISION, c)][0]:
            wr = [bp[b] for bp in bench_paths[(SLIP_DECISION, c)]]
            r = window_risk(wr, [bm[b] for bm in bench[(SLIP_DECISION, c)]], calendar)
            ex = np.array([bm[b] - bm['EW7-monthly'] for bm in bench[(SLIP_DECISION, c)]])
            exs = np.array([bm[b] - bm['SPY'] for bm in bench[(SLIP_DECISION, c)]])
            r['pct_windows_beating_EW7-monthly'] = float((ex > 0).mean() * 100)
            r['pct_windows_beating_SPY'] = float((exs > 0).mean() * 100)
            rv[b] = r
        A['risk_view'][c] = rv

    # White's Reality Check at the decision cost
    A['reality_check'] = {}
    for c, _ in CONFIGS:
        fams = {}
        for form, key in (('raw', 'oos_returns'), ('excess_EW7-monthly', 'excess_returns_EW7-monthly')):
            fams[f'fixed_60_{form}'] = {f"{short(s)} {k}": A['fixed'][f"{s} | {SLIP_DECISION}bps"][k][c][key]
                                        for s in STRATEGIES for k in A['fixed'][f"{s} | {SLIP_DECISION}bps"]}
            fams[f'walk_forward_2_{form}'] = {short(s): A['walk_forward'][f"{s} | {SLIP_DECISION}bps"][c][key]
                                              for s in STRATEGIES}
        res = {}
        for label, cands in fams.items():
            try:
                rc = reality_check(cands, n_boot=n_boot, mean_block=RC_BLOCK, seed=RC_SEED)
                rc['naive_p_value'] = naive_single_test(cands[rc['best_candidate']], n_boot=n_boot,
                                                        mean_block=RC_BLOCK, seed=RC_SEED)
                rc.pop('candidate_means', None)
            except ValueError as e:
                rc = {'error': str(e)}
            res[label] = rc
        A['reality_check'][c] = res

    # full span, hindsight: every setting traded continuously; benchmarks over the same days
    t0 = time.time()
    A['full_span'], rejected = {}, 0
    full_panel = segment(panel, start, end)
    for s in STRATEGIES:
        for p in GRID:
            m, pf, log = run_backtest(ROTATION[s], universe, p, start, end, SLIP_DECISION, storage)
            rejected += log.count('REJECTED')
            vals, wts = daily_book(full_panel[universe], pf.trade_history, CAPITAL)
            if abs((vals.iloc[-1] / CAPITAL - 1) * 100 - m['return_pct']) > 1e-6:
                raise RuntimeError(f"full-span book != backtester for {s} {pkey(p)}")
            st = full_stats(returns_from_values(vals, CAPITAL), calendar, STRESS)
            st['fills'] = int(m['total_trades'])
            A['full_span'][f"{short(s)} {pkey(p)}"] = st
    u = full_panel[universe]
    ew_m = ew_rebalanced_path(u, EW_REBALANCE, SLIP_DECISION / 1e4 + COMMISSION)
    A['full_span']['EW7-monthly'] = full_stats(returns_from_values(ew_m, 1.0), calendar, STRESS)
    A['full_span']['SPY'] = full_stats(returns_from_values(full_panel[MARKET] / full_panel[MARKET].iloc[0], 1.0),
                                       calendar, STRESS)
    for x in universe:
        A['full_span'][x.replace('US.', '')] = full_stats(returns_from_values(u[x] / u[x].iloc[0], 1.0),
                                                          calendar, STRESS)
    A['validation']['iii_no_rejected_buys_full_span'] = {'runs': len(STRATEGIES) * len(GRID),
                                                         'mismatches': ['REJECTED'] * rejected}
    A['runtime_s']['full_span'] = round(time.time() - t0, 1)
    A['runtime_s']['part_a_total'] = round(time.time() - t_all, 1)
    A['bounds'] = {c: [[str(x), str(y)] for x, y in b] for c, b in bounds.items()}
    A['benchmarks_per_window'] = {f"{c} | {slip}bps": list(bench[(slip, c)])
                                  for c, _ in CONFIGS for slip in (SLIP_DECISION, SLIP_SENSITIVITY)}
    return A, rows


def _row(kind, strategy, setting, slip, per):
    a, b = (per[c] for c, _ in CONFIGS)
    passes = qualifies(a['oos_mean'], a['consistency']) and qualifies(b['oos_mean'], b['consistency'])
    earns = passes and a['excess_EW7-monthly'] > 0 and b['excess_EW7-monthly'] > 0
    r = {'kind': kind, 'strategy': strategy, 'setting': setting, 'slippage_bps': slip, 'commission': COMMISSION}
    for tag, x in (('a', a), ('b', b)):
        r.update({f'config_{tag}_oos': round(x['oos_mean'], 3), f'config_{tag}_consistency': round(x['consistency'], 1),
                  f'config_{tag}_excess_ew7m': round(x['excess_EW7-monthly'], 3),
                  f'config_{tag}_beat_ew7m_pct': round(x['pct_windows_beating_EW7-monthly'], 1),
                  f'config_{tag}_excess_spy': round(x['excess_SPY'], 3),
                  f'config_{tag}_beat_spy_pct': round(x['pct_windows_beating_SPY'], 1),
                  f'config_{tag}_best': round(x['best_window'], 3), f'config_{tag}_worst': round(x['worst_window'], 3),
                  f'config_{tag}_fills': round(x.get('fills_mean', float('nan')), 1)})
    decides = kind == 'walk_forward' and slip == SLIP_DECISION      # 5 bps is a sensitivity line only
    r['passes'] = passes if decides else None
    r['rule_met'] = passes
    r['earns_its_place'] = earns if decides else None
    r['rule_and_excess_met'] = earns
    return r


# ── PART B ───────────────────────────────────────────────────────────────────

def hac(y, X, t, lags):
    """OLS with Newey-West (Bartlett) applied to the per-period sums of the scores (Driscoll-Kraay).
    One observation per period is plain Newey-West. t: integer period codes 0..T-1."""
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    u = X * (y - X @ b)[:, None]
    H = np.zeros((int(t.max()) + 1, X.shape[1]))
    np.add.at(H, t, u)
    S = H.T @ H
    for lag in range(1, lags + 1):
        G = H[lag:].T @ H[:-lag]
        S += (1 - lag / (lags + 1)) * (G + G.T)
    return b, XtX_inv @ S @ XtX_inv


def b_panel(closes, version):
    """One row per (signal date, ETF): signals and forward returns, as the docstring defines them."""
    C = closes.values
    T, N = C.shape
    idx = closes.index
    pos = pd.Series(np.arange(T), index=idx)
    i = pos.groupby(idx.tz_convert(None).to_period('W-FRI')).max().values
    i = i[i >= MAX_LOOKBACK]

    def ratio(a, b):
        out = np.full((len(i), N), np.nan)
        ok = (a < T) & (b >= 0)
        out[ok] = C[a[ok]] / C[b[ok]] - 1
        return out

    q = {'r_1w': ratio(i, i - 5), 'r_1m': ratio(i, i - 21), 'r_6m': ratio(i, i - 126),
         'r_12_1': ratio(i - 21, i - 252), 'f_1w': ratio(i + 5, i), 'f_1m': ratio(i + 21, i)}
    if version == 'cross_sectional':
        q = {k: v - v.mean(axis=1, keepdims=True) for k, v in q.items()}
    rows = pd.DataFrame({
        'date': np.repeat(idx[i].values, N), 'week': np.repeat(np.arange(len(i)), N),
        'pos': np.repeat(i, N), 'ticker': np.tile([c.replace('US.', '') for c in closes.columns], len(i)),
        'rev_1w': np.sign(-q['r_1w']).ravel(), 'rev_1m': np.sign(-q['r_1m']).ravel(),
        'mom_6m': np.sign(q['r_6m']).ravel(), 'mom_12_1': np.sign(q['r_12_1']).ravel(),
        'f_1w': q['f_1w'].ravel(), 'f_1m': q['f_1m'].ravel()})
    return rows


def case_table(rows, rev, mom, fwd, lags):
    d = rows[['week', rev, mom, fwd]].dropna()
    d = d[(d[rev] != 0) & (d[mom] != 0)]
    case = np.select([(d[mom] > 0) & (d[rev] > 0), (d[mom] < 0) & (d[rev] < 0),
                      (d[mom] > 0) & (d[rev] < 0), (d[mom] < 0) & (d[rev] > 0)], CASES, default='')
    present = [c for c in CASES if (case == c).any()]
    X = np.column_stack([(case == c).astype(float) for c in present])
    y = d[fwd].values * 100
    wk = d['week'].values - d['week'].values.min()
    b, V = hac(y, X, wk, lags)
    out, n_t = {'n_obs': int(len(y)), 'n_weeks': int(len(np.unique(wk)))}, 0
    for j, c in enumerate(present):
        m = case == c
        out[c] = {'mean_pct': float(b[j]), 't': float(b[j] / math.sqrt(V[j, j])) if V[j, j] > 0 else float('nan'),
                  'n': int(m.sum()), 'hit_pct': float((y[m] > 0).mean() * 100)}
        n_t += 1
    for name, (p, q) in (('D_mom_minus_rev', ('mom_buy_rev_sell', 'mom_sell_rev_buy')),
                         ('agreement_spread', ('both_buy', 'both_sell'))):
        if p in present and q in present:
            cvec = np.zeros(len(present))
            cvec[present.index(p)], cvec[present.index(q)] = 1, -1
            est, se = float(cvec @ b), math.sqrt(float(cvec @ V @ cvec))
            out[name] = {'est_pct': est, 't': est / se if se > 0 else float('nan')}
            n_t += 1
    out['n_t_stats'] = n_t
    return out


def bet_path(closes, rows, rev, mom, horizon, version, market_rets=None, periods=STRESS):
    """Daily path of 'with momentum against the reversal traders' (see the docstring)."""
    C = closes.values
    T, N = C.shape
    R = np.vstack([np.zeros(N), C[1:] / C[:-1] - 1])
    order = [c.replace('US.', '') for c in closes.columns]      # pivot sorts tickers; R is in closes' order
    sig = rows.pivot(index='pos', columns='ticker', values=[rev, mom])
    sig_pos = sig.index.values
    Rv, Mv = sig[rev][order].values, sig[mom][order].values
    dis = (Rv * Mv == -1)
    w = np.where(dis, Mv, 0.0) / np.maximum(dis.sum(axis=1, keepdims=True), 1)
    if version == 'cross_sectional':
        w = w - w.sum(axis=1, keepdims=True) / N
    W = np.zeros((T, N))
    if horizon == '1w':
        for k, p in enumerate(sig_pos):
            nxt = sig_pos[k + 1] if k + 1 < len(sig_pos) else T - 1
            W[p + 1:nxt + 1] = w[k]
    else:
        acc, cnt = np.zeros((T, N)), np.zeros(T)
        for k, p in enumerate(sig_pos):
            acc[p + 1:p + 1 + HORIZONS['1m']] += w[k]
            cnt[p + 1:p + 1 + HORIZONS['1m']] += 1
        W = acc / np.maximum(cnt, 1)[:, None]
    first = sig_pos[0] + 1
    gross = (W * R).sum(axis=1)[first:]
    turnover = np.abs(np.diff(W, axis=0, prepend=np.zeros((1, N)))).sum(axis=1)[first:]
    net = gross - turnover * (SLIP_DECISION / 1e4 + COMMISSION)
    index = closes.index[first:]
    out = {}
    for label, series in (('gross', gross), ('net', net)):
        r = pd.Series(series, index=index)
        st = full_stats(r, closes.index, periods)
        st['ann_mean_pct'] = float(r.mean() * 252 * 100)
        st['worst_week_pct'] = float(((1 + r).rolling(5).apply(np.prod, raw=True) - 1).min() * 100)
        st['worst_quarter_pct'] = float(((1 + r).rolling(63).apply(np.prod, raw=True) - 1).min() * 100)
        out[label] = st
    out['share_days_invested_pct'] = float((np.abs(W[first:]).sum(axis=1) > 0).mean() * 100)
    out['mean_gross_exposure'] = float(np.abs(W[first:]).sum(axis=1).mean())
    out['annual_turnover'] = float(turnover.mean() * 252)
    if market_rets is not None and version == 'absolute':
        mk = market_rets.reindex(index).values
        ok = np.isfinite(mk)
        x = np.column_stack([np.ones(ok.sum()), mk[ok]])
        out['beta_to_spy'] = float(np.linalg.lstsq(x, gross[ok], rcond=None)[0][1])
    return out


def holm(pvals):
    order = np.argsort(pvals)
    m, adj, running = len(pvals), np.empty(len(pvals)), 0.0
    for rank, k in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[k]))
        adj[k] = running
    return adj


def p_two_sided(t):
    return math.erfc(abs(t) / math.sqrt(2))


def part_b_sample(closes, label, market_rets, periods, versions=VERSIONS, per_etf=True):
    res, rows_out, n_t = {}, [], 0
    for version in versions:
        rows = b_panel(closes, version)
        for rev, mom, h in itertools.product(REV, MOM, HORIZONS):
            key = f"{version} | {rev} x {mom} | {h}"
            fwd = f'f_{h}'
            entry = {'pooled': case_table(rows, rev, mom, fwd, NW_LAGS[h])}
            n_t += entry['pooled']['n_t_stats']
            if per_etf:
                for t in rows['ticker'].unique():
                    entry[t] = case_table(rows[rows.ticker == t], rev, mom, fwd, NW_LAGS[h])
                    n_t += entry[t]['n_t_stats']
            entry['bet_path'] = bet_path(closes, rows, rev, mom, h, version, market_rets, periods)
            res[key] = entry
            for g, ct in entry.items():
                if g == 'bet_path':
                    continue
                for c in CASES + ['D_mom_minus_rev', 'agreement_spread']:
                    if c in ct:
                        x = ct[c]
                        rows_out.append({'sample': label, 'version': version, 'reversal': rev, 'momentum': mom,
                                         'horizon': h, 'group': g, 'case': c,
                                         'mean_pct': round(x.get('mean_pct', x.get('est_pct')), 4),
                                         't': round(x['t'], 3), 'n': x.get('n'),
                                         'hit_pct': round(x['hit_pct'], 1) if 'hit_pct' in x else None})
    return res, rows_out, n_t


def part_b(storage, universe, smoke):
    t0 = time.time()
    B = {}
    closes = load_closes(storage, universe).dropna()
    spy = load_closes(storage, [MARKET])[MARKET]
    spy_rets = spy.pct_change()
    print(f"\n=== PART B: disagreement test | {len(universe)} ETFs | {closes.index[0].date()} -> "
          f"{closes.index[-1].date()} ===", flush=True)
    # check the hand-rolled Newey-West against statsmodels on one ETF (one observation per week)
    import statsmodels.api as sm
    rows = b_panel(closes, 'absolute')
    d = rows[rows.ticker == rows.ticker.iloc[0]][['week', 'f_1m']].dropna()
    y = d['f_1m'].values
    X = np.ones((len(y), 1))
    _, V = hac(y, X, d['week'].values - d['week'].values.min(), 8)
    ref = sm.OLS(y, X).fit(cov_type='HAC', cov_kwds={'maxlags': 8, 'use_correction': False})
    B['validation_nw_vs_statsmodels'] = {'mine_se': float(math.sqrt(V[0, 0])), 'statsmodels_se': float(ref.bse[0])}
    if abs(math.sqrt(V[0, 0]) - ref.bse[0]) > 1e-10:
        raise SystemExit("Newey-West check failed")

    res, rows_out, n_t = part_b_sample(closes, 'common_7', spy_rets, STRESS)
    B['common'] = res
    keys = [k for k in res if 'D_mom_minus_rev' in res[k]['pooled']]
    ts_ = [res[k]['pooled']['D_mom_minus_rev']['t'] for k in keys]
    ps = [p_two_sided(t) for t in ts_]
    adj = holm(np.array(ps))
    B['primary'] = [{'combination': k, 'D_pct': res[k]['pooled']['D_mom_minus_rev']['est_pct'], 't': t,
                     'p': p, 'p_holm': float(a),
                     'reading': ('momentum wins' if a < 0.05 and t > 0 else 'reversal wins' if a < 0.05 else
                                 'no reliable winner')}
                    for k, t, p, a in zip(keys, ts_, ps, adj)]
    B['n_primary_tests'] = len(keys)

    # secondary: longer histories
    long_closes = load_closes(storage, ['US.IWF', 'US.IWD', MARKET]).dropna()
    periods = {**STRESS_LONG, **STRESS}
    B['long_absolute'], abs_rows, n1 = part_b_sample(long_closes, 'long_IWF_IWD_SPY', spy_rets, periods,
                                                     versions=['absolute'])
    B['long_cross_sectional'], rel_rows, n2 = part_b_sample(long_closes[['US.IWF', 'US.IWD']], 'long_IWF_vs_IWD',
                                                            spy_rets, periods, versions=['cross_sectional'])
    B['long_span'] = [str(long_closes.index[0].date()), str(long_closes.index[-1].date())]
    B['first_signal_dates'] = {'common': str(b_panel(closes, 'absolute')['date'].min())[:10],
                               'long': str(b_panel(long_closes, 'absolute')['date'].min())[:10]}
    B['t_stats_computed'] = {'common': n_t, 'long_absolute': n1, 'long_cross_sectional': n2,
                             'total': n_t + n1 + n2}
    B['runtime_s'] = round(time.time() - t0, 1)
    return B, rows_out + abs_rows + rel_rows


# ── EXPLORATORY (post hoc, see the docstring): no parameter settings added, never a pass ─────

def exploratory(storage, universe, main_json):
    """E1: the walk-forward re-selected by a Sharpe from correctly marked day-end values."""
    prior = json.loads(Path(main_json).read_text())['part_a']
    panel = load_closes(storage, universe + [MARKET])
    start, end = (datetime.fromisoformat(x) for x in prior['span'])
    out = {'label': 'EXPLORATORY, post hoc. Adds no parameter settings; its re-selections are walk-forward '
                    'selections 5 and 6 of the search; nothing here is a pass.', 'from': str(main_json), 'E1': {}}
    for cname, n_splits in CONFIGS:
        for s in STRATEGIES:
            fixed = prior['fixed'][f"{s} | {SLIP_DECISION}bps"]
            wf = prior['walk_forward'][f"{s} | {SLIP_DECISION}bps"][cname]
            bench = prior['benchmarks_per_window'][f"{cname} | {SLIP_DECISION}bps"]
            wins = []
            for i, (ws, tr_end, _, _) in enumerate(wf_bounds(start, end, n_splits)):
                seg = segment(panel[universe], ws, tr_end, end_inclusive=False)
                recorded, dayend = {}, {}
                for p in GRID:
                    # walk_forward's training runs are end-exclusive (run_backtest's are inclusive)
                    pf2 = Portfolio(initial_cash=CAPITAL, commission_rate=COMMISSION)
                    bt = PythonBacktester(storage=storage, portfolio=pf2, risk_manager=None, slippage_bps=SLIP_DECISION)
                    with quiet():
                        m = bt.run(ROTATION[s], symbols=universe, timeframe=TF, start_date=ws, end_date=tr_end,
                                   end_inclusive=False, **p)
                    vals, _ = daily_book(seg, pf2.trade_history, CAPITAL)
                    r = vals.pct_change().dropna()
                    if m.get('total_trades', 0) == 0:
                        continue                     # walk_forward ranks settings that traded first
                    recorded[pkey(p)] = m['sharpe_ratio']
                    dayend[pkey(p)] = float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else 0.0
                rec, de = pd.Series(recorded, dtype=float), pd.Series(dayend, dtype=float)
                chosen_wf = pkey(wf['windows'][i]['params'])
                if rec.idxmax() != chosen_wf:
                    raise RuntimeError(f"E1 could not reproduce walk_forward's choice in {cname} window {i + 1}")
                pick = de.idxmax()
                ret = fixed[pick][cname]['oos_returns'][i]
                wins.append({'window': i + 1, 'chosen_recorded_sharpe': chosen_wf, 'chosen_dayend_sharpe': pick,
                             'changed': pick != chosen_wf, 'spearman': float(rec.rank().corr(de.rank())),
                             'return': ret, 'excess_EW7-monthly': ret - bench[i]['EW7-monthly'],
                             'walk_forward_return': wf['windows'][i]['return']})
            r = np.array([w['return'] for w in wins])
            ex = np.array([w['excess_EW7-monthly'] for w in wins])
            agg = {'oos_mean': float(r.mean()), 'consistency': float((r > 0).mean() * 100),
                   'excess_EW7-monthly': float(ex.mean()), 'pct_windows_beating_EW7-monthly': float((ex > 0).mean() * 100),
                   'walk_forward_oos_mean': wf['oos_mean'], 'walk_forward_excess_EW7-monthly': wf['excess_EW7-monthly'],
                   'windows_changed': int(sum(w['changed'] for w in wins)),
                   'median_spearman': float(np.median([w['spearman'] for w in wins])), 'windows': wins}
            out['E1'][f"{s} | {cname}"] = agg
            print(f"  E1 {short(s):12s} {cname:15s} | choice changed in {agg['windows_changed']}/{len(wins)} windows, "
                  f"median rank corr {agg['median_spearman']:.2f} | re-selected OOS {agg['oos_mean']:+.3f}% "
                  f"({agg['consistency']:.0f}% > 0), excess EW7m {agg['excess_EW7-monthly']:+.3f}% | as run "
                  f"{agg['walk_forward_oos_mean']:+.3f}%, excess {agg['walk_forward_excess_EW7-monthly']:+.3f}%", flush=True)
    return out


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--smoke', action='store_true', help='2 ETFs (MTUM, QUAL), plumbing check only')
    ap.add_argument('--out-dir', default=None, help='where to write (default results/factor_search/)')
    ap.add_argument('--n-boot', type=int, default=5000)
    ap.add_argument('--part', default='both', choices=['A', 'B', 'both'])
    ap.add_argument('--exploratory', action='store_true', help='run ONLY the post-hoc section (see the docstring)')
    ap.add_argument('--from-json', default=None, help='the pre-registered run the exploratory section reads')
    args = ap.parse_args()

    t_all = time.time()
    storage = DataStorage(str(DATA))
    universe = [sym(t) for t in (TICKERS[:2] if args.smoke else TICKERS)]
    out_dir = Path(args.out_dir) if args.out_dir else paths.results_dir() / 'factor_search'
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    if args.exploratory:
        print("=== EXPLORATORY (post hoc, adds no settings, never a pass) ===", flush=True)
        res = exploratory(storage, universe, args.from_json)
        res['runtime_min'] = round((time.time() - t_all) / 60, 2)
        p = out_dir / f"factor_rotation_exploratory_{'smoke_' if args.smoke else ''}{stamp}.json"
        p.write_text(json.dumps(res, indent=1, default=str))
        print(f"\nDone in {res['runtime_min']:.1f} min | saved {p}", flush=True)
        return
    name = f"factor_rotation_{'smoke_' if args.smoke else ''}{stamp}"
    detail = {'pre_registered': {
        'universe': universe, 'market': MARKET, 'strategies': STRATEGIES, 'grid': GRID_AXES,
        'configs': CONFIGS, 'train_pct': TRAIN_PCT, 'objective': OBJECTIVE, 'commission': COMMISSION,
        'slippage_bps_decision': SLIP_DECISION, 'slippage_bps_sensitivity': SLIP_SENSITIVITY,
        'ew_rebalance_bars': EW_REBALANCE, 'stress': STRESS, 'stress_long': STRESS_LONG,
        'reversal': REV, 'momentum': MOM, 'horizons': HORIZONS, 'nw_lags': NW_LAGS,
        'reality_check': {'n_boot': args.n_boot, 'mean_block': RC_BLOCK, 'seed': RC_SEED},
        'smoke': args.smoke}}
    print(f"factor rotation study | {len(universe)} ETFs | engine threads {os.environ['MIDFREQ_ENGINE_THREADS']}",
          flush=True)

    if args.part in ('A', 'both'):
        A, rows = part_a(storage, universe, args.n_boot, args.smoke)
        detail['part_a'] = A
        pd.DataFrame(rows).to_csv(out_dir / f"{name}.csv", index=False)
    if args.part in ('B', 'both'):
        B, brows = part_b(storage, universe, args.smoke)
        detail['part_b'] = B
        pd.DataFrame(brows).to_csv(out_dir / f"{name}_disagreement.csv", index=False)
    detail['runtime_min'] = round((time.time() - t_all) / 60, 2)
    (out_dir / f"{name}.json").write_text(json.dumps(detail, indent=1, default=str))
    print(f"\nDone in {(time.time() - t_all) / 60:.1f} min | saved {out_dir / name}.(csv|json)", flush=True)
    report(detail)


def report(detail):
    """The headline numbers, printed (everything is in the JSON)."""
    A = detail.get('part_a')
    if A:
        print("\n--- PART A: walk-forward candidates (2 bps decides; 5 bps sensitivity) and benchmarks ---")
        for key, per in list(A['walk_forward'].items()) + [(f"{k} | registry-exact", v) for k, v in A['registry_exact'].items()]:
            a, b = (per[c] for c, _ in CONFIGS)
            print(f"  {key:52s} A {a['oos_mean']:+6.2f}% ({a['consistency']:3.0f}% >0, ex EW7m {a['excess_EW7-monthly']:+5.2f}%, "
                  f"beat {a['pct_windows_beating_EW7-monthly']:3.0f}%) | B {b['oos_mean']:+6.2f}% ({b['consistency']:3.0f}% >0, "
                  f"ex EW7m {b['excess_EW7-monthly']:+5.2f}%, beat {b['pct_windows_beating_EW7-monthly']:3.0f}%) | "
                  f"chosen A {list(a.get('params_chosen', {}).items())[:3]}")
        for c, _ in CONFIGS:
            for fam, rc in A['reality_check'][c].items():
                if 'p_value' in rc:
                    print(f"  RC {c:15s} {fam:32s} best {rc['best_candidate']:22s} {rc['observed_max_mean']:+6.3f}% "
                          f"p {rc['p_value']:.4f} (naive {rc['naive_p_value']:.4f})")
        for k, v in A['validation'].items():
            print(f"  validation {k}: {v['runs']} runs, {len(v['mismatches'])} mismatches")
    B = detail.get('part_b')
    if B:
        print("\n--- PART B: primary tests, pooled D = mean(mom buy/rev sell) - mean(mom sell/rev buy) ---")
        for x in B['primary']:
            print(f"  {x['combination']:42s} D {x['D_pct']:+7.3f}% t {x['t']:+6.2f} p {x['p']:.4f} "
                  f"holm {x['p_holm']:.4f} -> {x['reading']}")
        print(f"  t-statistics computed: {B['t_stats_computed']}")


if __name__ == '__main__':
    main()
