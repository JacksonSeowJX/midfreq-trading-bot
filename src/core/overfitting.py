"""
Probability of Backtest Overfitting (PBO)
=========================================================================
Implements Combinatorially Symmetric Cross-Validation (CSCV) from
Bailey, Borwein, Lopez de Prado & Zhu (2017), "The probability of
backtest overfitting", which this project's literature review already
cites as the formal treatment of the selection-bias problem.

The question PBO answers is different from the one walk-forward answers.
Walk-forward asks whether the configuration we chose holds up on unseen
data. PBO asks a harder question: given that we searched a grid and kept
the best, how often would the in-sample winner have turned out to be a
below-median performer out-of-sample? If that happens most of the time,
the search itself is manufacturing the result and the "best" parameters
carry no information.

Method:
  1. Split the period into S disjoint subsets of equal length.
  2. For every way of choosing S/2 subsets as in-sample (the remainder
     being out-of-sample), find the configuration that scores highest
     in-sample, then look up where that same configuration ranks
     out-of-sample.
  3. PBO is the fraction of those splits where the in-sample winner
     lands below the out-of-sample median.

PBO near 0 means the search is finding something real. PBO near 0.5 is
what a coin flip produces. PBO above 0.5 means the selection is actively
counterproductive: the in-sample winner is usually a below-median
performer afterwards.

The expensive part is building the performance matrix, which costs
(number of configurations x S) backtests. Everything after that is
arithmetic on that matrix, not further simulation.
"""
from itertools import combinations
from math import log
from typing import Any, Dict, List, Optional

from datetime import datetime, timedelta
import io
import contextlib

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.backtester import Backtester
from core.strategy import STRATEGY_REGISTRY
from core.optimizer import generate_param_grid, _get_objective_value


def build_performance_matrix(
    strategy_name: str,
    symbols: List[str],
    timeframe: Timeframe,
    start_date: datetime,
    end_date: datetime,
    storage: DataStorage,
    n_subsets: int = 10,
    objective: str = 'sharpe_ratio',
    max_configs: int = 60,
    initial_capital: float = 100000.0,
    slippage_bps: float = 0.0,
    commission_rate: float = Portfolio.HK_FEE_RATE,
    progress: bool = True,
) -> Dict[str, Any]:
    """
    Score every candidate configuration in every subset.

    Returns {'matrix': [[score per config] per subset], 'params': [...]}.
    """
    if strategy_name not in STRATEGY_REGISTRY:
        raise ValueError(f"unknown strategy {strategy_name}")
    strategy_class = STRATEGY_REGISTRY[strategy_name]['class']

    grid = generate_param_grid(strategy_name)
    if len(grid) > max_configs:
        step = max(1, len(grid) // max_configs)
        grid = grid[::step][:max_configs]

    total_days = (end_date - start_date).days
    sub_days = total_days // n_subsets
    if sub_days < 5:
        raise ValueError("period too short to split into that many subsets")

    matrix: List[List[float]] = []
    for s in range(n_subsets):
        s_start = start_date + timedelta(days=s * sub_days)
        s_end = s_start + timedelta(days=sub_days)
        row: List[float] = []
        for params in grid:
            portfolio = Portfolio(initial_cash=initial_capital, commission_rate=commission_rate)
            bt = Backtester(storage=storage, portfolio=portfolio, slippage_bps=slippage_bps)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    m = bt.run(strategy_class, symbols=symbols, timeframe=timeframe,
                               start_date=s_start, end_date=s_end,
                               end_inclusive=False, **params)
                except Exception:
                    m = {}
            row.append(_get_objective_value(m, objective) if m else 0.0)
        matrix.append(row)
        if progress:
            print(f"    subset {s+1}/{n_subsets} scored ({len(grid)} configs)", flush=True)

    return {'matrix': matrix, 'params': grid, 'n_configs': len(grid), 'n_subsets': n_subsets}


def pbo_from_matrix(matrix: List[List[float]]) -> Dict[str, Any]:
    """
    Run CSCV over a subset-by-configuration performance matrix.

    matrix[s][c] = score of configuration c in subset s.
    """
    n_subsets = len(matrix)
    if n_subsets < 4 or n_subsets % 2 != 0:
        raise ValueError("need an even number of subsets, at least 4")
    n_configs = len(matrix[0])
    if n_configs < 2:
        raise ValueError("need at least 2 configurations to rank")

    half = n_subsets // 2
    logits: List[float] = []
    below_median = 0
    trials = 0

    for is_idx in combinations(range(n_subsets), half):
        oos_idx = [s for s in range(n_subsets) if s not in is_idx]

        is_score = [sum(matrix[s][c] for s in is_idx) for c in range(n_configs)]
        oos_score = [sum(matrix[s][c] for s in oos_idx) for c in range(n_configs)]

        best = max(range(n_configs), key=lambda c: is_score[c])

        # Relative rank of the in-sample winner among out-of-sample results.
        # rank 1 = worst, n_configs = best.
        order = sorted(range(n_configs), key=lambda c: oos_score[c])
        rank = order.index(best) + 1
        w = rank / (n_configs + 1)
        w = min(max(w, 1e-9), 1 - 1e-9)
        logits.append(log(w / (1 - w)))

        if w <= 0.5:
            below_median += 1
        trials += 1

    return {
        'pbo': below_median / trials,
        'n_trials': trials,
        'n_configs': n_configs,
        'n_subsets': n_subsets,
        'mean_logit': sum(logits) / len(logits),
    }


def compute_pbo(**kwargs) -> Dict[str, Any]:
    """Build the matrix and run CSCV over it."""
    built = build_performance_matrix(**kwargs)
    result = pbo_from_matrix(built['matrix'])
    result['params_tested'] = built['n_configs']
    return result
