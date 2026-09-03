"""
Bounded Experiment: Does Adding Fed Funds Rate Data Help the ML Classifier?
===============================================================================
Time-boxed test of a specific idea: mid-frequency strategies are plausibly
more exposed to macro conditions than the pure price-action features
currently used. This adds the daily Federal Funds Effective Rate (FRED
series DFF, free public endpoint, no API key needed) as two extra features,
the current rate level and its 90-day change (a hiking/cutting-cycle
proxy), to the existing ML Direction Classifier, and reruns the same
robust two-config walk-forward validation used throughout this project on
the 15 US stocks only (Fed data is US-specific).

This is deliberately scoped as a standalone research script, not a change
to the core strategy module, since it is a bounded experiment and not a
reframing of the project. The macro-augmented classifier is defined here,
not in src/core/strategy.py.

No-lookahead: the macro feature lookup for a candle at date T only ever
uses DFF observations dated on or before T (pandas Series.asof), matching
the no-lookahead discipline used everywhere else in this project. Note:
DFF's real-world publication lag (same-day vs next-day availability) was
not independently verified for this bounded experiment; a T-vs-T lookup
is a conservative simplification, not confirmed production-grade timing.

Usage:
    python3 scripts/ml_classifier_macro_research.py
"""
import sys
import time
import io
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import pandas as pd
from datetime import datetime, timedelta

from core.models import Timeframe
from core.storage import DataStorage
from core.config import ConfigLoader
from core.portfolio import Portfolio
from core.strategy import MLDirectionClassifier, STRATEGY_REGISTRY

N_SPLITS_CONFIGS = [
    {'name': 'A (9 windows)', 'n_splits': 9, 'train_pct': 0.7},
    {'name': 'B (15 windows)', 'n_splits': 15, 'train_pct': 0.7},
]
OBJECTIVE = 'sharpe_ratio'
MIN_CONSISTENCY = 50.0
HISTORY_DAYS = 3 * 365
SLIPPAGE_BPS = 5.0


def fetch_fed_funds_rate() -> pd.Series:
    """Daily Federal Funds Effective Rate from FRED's free public CSV endpoint."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(raw), parse_dates=['observation_date'])
    df = df.rename(columns={'observation_date': 'date', 'DFF': 'rate'})
    df = df.dropna(subset=['rate']).sort_values('date')
    series = df.set_index('date')['rate']
    series.index = pd.to_datetime(series.index).tz_localize(None)
    return series


class MLDirectionClassifierMacro(MLDirectionClassifier):
    """MLDirectionClassifier + 2 Fed funds rate features. Bounded experiment
    only — not registered in STRATEGY_REGISTRY / not used by the live roster."""
    macro_series: pd.Series = None  # set once before use, shared across instances

    def __init__(self, portfolio, **kwargs):
        super().__init__(portfolio, **kwargs)
        self.timestamps = {}
        self._current_symbol = None

    def _macro_features(self, symbol, i):
        ts_list = self.timestamps.get(symbol, [])
        if i >= len(ts_list) or self.macro_series is None:
            return [0.0, 0.0]
        ts = ts_list[i]
        ts_naive = ts.tz_localize(None) if getattr(ts, 'tzinfo', None) else pd.Timestamp(ts)
        rate_now = self.macro_series.asof(ts_naive)
        rate_90d = self.macro_series.asof(ts_naive - pd.Timedelta(days=90))
        rate_now = 0.0 if pd.isna(rate_now) else float(rate_now)
        rate_90d = 0.0 if pd.isna(rate_90d) else float(rate_90d)
        return [rate_now, rate_now - rate_90d]

    def _feature_row(self, prices, i):
        base = super()._feature_row(prices, i)
        if base is None:
            return None
        # self._current_symbol is set by _fit_model/on_data below before calling this
        return base + self._macro_features(self._current_symbol, i)

    def _fit_model(self, symbol, prices):
        self._current_symbol = symbol
        super()._fit_model(symbol, prices)

    def on_data(self, symbol, candle):
        self._current_symbol = symbol
        ts_list = self.timestamps.setdefault(symbol, [])
        ts_list.append(candle.timestamp)
        if len(ts_list) > self._history_keep:
            ts_list.pop(0)
        MLDirectionClassifier.on_data(self, symbol, candle)


def qualifies(oos_return, consistency):
    return oos_return > 0 and consistency >= MIN_CONSISTENCY


def run_walk_forward(strategy_class, symbol, storage, start, end, n_splits, train_pct):
    """Minimal inline walk-forward, mirroring core.optimizer.walk_forward, so we
    can pass a class directly instead of a STRATEGY_REGISTRY name."""
    from core.backtester import Backtester
    from core.optimizer import generate_param_grid, _get_objective_value

    total_days = (end - start).days
    window_size = total_days // n_splits
    param_grid = generate_param_grid('ML Direction Classifier')  # same tunable params

    oos_returns = []
    for w in range(n_splits):
        w_start = start + timedelta(days=w * window_size)
        w_end = w_start + timedelta(days=window_size)
        train_days = int(window_size * train_pct)
        train_start, train_end = w_start, w_start + timedelta(days=train_days)
        test_start, test_end = train_end, w_end

        best_obj, best_params, best_traded = float('-inf'), {}, False
        for params in param_grid:
            p = Portfolio(initial_cash=100_000.0)
            bt = Backtester(storage=storage, portfolio=p, slippage_bps=SLIPPAGE_BPS)
            f = io.StringIO()
            import contextlib
            with contextlib.redirect_stdout(f):
                try:
                    m = bt.run(strategy_class, symbols=[symbol], timeframe=Timeframe.HOUR_1,
                               start_date=train_start, end_date=train_end, **params)
                except Exception:
                    m = {}
            if m:
                obj_val = _get_objective_value(m, OBJECTIVE)
                traded = m.get('total_trades', 0) > 0
                if (traded, obj_val) > (best_traded, best_obj):
                    best_obj, best_params, best_traded = obj_val, params.copy(), traded

        p = Portfolio(initial_cash=100_000.0)
        bt = Backtester(storage=storage, portfolio=p, slippage_bps=SLIPPAGE_BPS)
        f = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(f):
            try:
                test_m = bt.run(strategy_class, symbols=[symbol], timeframe=Timeframe.HOUR_1,
                                start_date=test_start, end_date=test_end, **best_params)
            except Exception:
                test_m = {}
        oos_returns.append(test_m.get('return_pct', 0.0) if test_m else 0.0)

    n = len(oos_returns)
    avg = sum(oos_returns) / n if n else 0.0
    consistency = 100 * sum(1 for r in oos_returns if r > 0) / n if n else 0.0
    return avg, consistency


def main():
    print("Fetching Fed Funds Rate (FRED DFF)...")
    macro_series = fetch_fed_funds_rate()
    print(f"  {len(macro_series)} daily observations, "
          f"{macro_series.index.min().date()} to {macro_series.index.max().date()}")
    MLDirectionClassifierMacro.macro_series = macro_series

    storage = DataStorage()
    config = ConfigLoader()
    symbols = config.get_all_symbols(market="US")

    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)

    rows = []
    t0 = time.time()
    for sym in symbols:
        t_sym = time.time()
        result = {'symbol': sym}
        for cfg, key in zip(N_SPLITS_CONFIGS, ['a', 'b']):
            avg, cons = run_walk_forward(MLDirectionClassifierMacro, sym, storage, start, end,
                                          cfg['n_splits'], cfg['train_pct'])
            result[f'config_{key}_oos'] = round(avg, 3)
            result[f'config_{key}_consistency'] = round(cons, 1)
        result['robust'] = all(
            qualifies(result[f'config_{k}_oos'], result[f'config_{k}_consistency'])
            for k in ['a', 'b']
        )
        rows.append(result)
        print(f"{sym} | A {result['config_a_oos']:+.2f}%/{result['config_a_consistency']:.0f}% | "
              f"B {result['config_b_oos']:+.2f}%/{result['config_b_consistency']:.0f}% | "
              f"robust={result['robust']} | {time.time()-t_sym:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(__file__).resolve().parent.parent / 'results' / f'ml_classifier_macro_{stamp}.csv'
    df.to_csv(out, index=False)

    df['avg_oos'] = (df.config_a_oos + df.config_b_oos) / 2
    print(f"\nDone in {(time.time()-t0)/60:.1f} min — saved {out}\n")
    print(df.to_string(index=False))
    print(f"\nMean OOS (with Fed rate features): {df['avg_oos'].mean():+.3f}%")
    print(f"Robust pairs: {df['robust'].sum()}/{len(df)}")
    print(f"\nBaseline (no macro features, from earlier US validation study): "
          f"mean -0.389%, robust 0/15")


if __name__ == "__main__":
    main()
