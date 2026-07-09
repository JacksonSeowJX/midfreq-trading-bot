"""
Strategy Optimizer Module
=========================
Provides grid search and walk-forward optimization for strategy parameters.

Grid Search: Exhaustively tests all parameter combinations to find the best
             configuration for a given objective (Sharpe, Return, etc.)

Walk-Forward: Splits data into rolling train/test windows to validate that
              optimized parameters generalize to unseen data (prevents overfitting).
"""

import itertools
import pandas as pd
from typing import Dict, Any, List, Optional, Callable, Type
from datetime import datetime, timedelta
from dataclasses import dataclass

from core.models import Timeframe
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.strategy import BaseStrategy, STRATEGY_REGISTRY
from core.backtester import Backtester
from core.risk_manager import RiskManager


# ─── Objective Functions ───────────────────────────────────────────

OBJECTIVES = {
    'sharpe_ratio': 'Sharpe Ratio (higher is better)',
    'return_pct': 'Total Return % (higher is better)',
    'profit_factor': 'Profit Factor (higher is better)',
    'max_drawdown': 'Max Drawdown (lower is better — inverted for ranking)',
}


def _get_objective_value(metrics: Dict[str, Any], objective: str) -> float:
    """Extract the objective value from backtest metrics, handling direction."""
    val = metrics.get(objective, 0.0)
    
    # For max_drawdown, lower is better, so negate for ranking
    if objective == 'max_drawdown':
        return -val  # Negate so that lower DD ranks higher
    
    # Handle infinity for profit_factor
    if val == float('inf'):
        return 999.0
    
    return val


# ─── Grid Search ───────────────────────────────────────────────────

def generate_param_grid(strategy_name: str) -> List[Dict[str, Any]]:
    """
    Generate all parameter combinations from the STRATEGY_REGISTRY.
    
    Uses the min/max/step from the registry to create the grid.
    """
    if strategy_name not in STRATEGY_REGISTRY:
        return []
    
    info = STRATEGY_REGISTRY[strategy_name]
    param_ranges = {}
    
    for param_key, meta in info['params'].items():
        start = meta['min']
        stop = meta['max']
        step = meta['step']
        
        # Generate range of values
        values = []
        current = start
        while current <= stop:
            # Round to avoid floating point artifacts
            if isinstance(meta['default'], float):
                values.append(round(current, 4))
            else:
                values.append(int(current))
            current += step
        
        param_ranges[param_key] = values
    
    # Cartesian product of all parameter values
    keys = list(param_ranges.keys())
    value_lists = [param_ranges[k] for k in keys]
    
    grid = []
    for combo in itertools.product(*value_lists):
        grid.append(dict(zip(keys, combo)))
    
    return grid


def grid_search(
    strategy_name: str,
    symbols: List[str],
    timeframe: Timeframe,
    start_date: datetime,
    end_date: datetime,
    storage: DataStorage,
    objective: str = 'sharpe_ratio',
    initial_capital: float = 100000.0,
    risk_manager: Optional[RiskManager] = None,
    slippage_bps: float = 0.0,
    max_combinations: int = 500,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> pd.DataFrame:
    """
    Exhaustive grid search over all parameter combinations.
    
    Args:
        strategy_name: Name from STRATEGY_REGISTRY
        symbols: List of stock symbols
        timeframe: Candle timeframe
        start_date: Start of backtest period
        end_date: End of backtest period
        storage: DataStorage instance
        objective: Metric to optimize ('sharpe_ratio', 'return_pct', etc.)
        initial_capital: Starting portfolio value
        risk_manager: Optional risk manager
        slippage_bps: Slippage in basis points
        max_combinations: Safety limit on grid size
        progress_callback: Called with (progress_float, status_string)
        
    Returns:
        DataFrame with all parameter combos and their metrics, sorted by objective
    """
    if strategy_name not in STRATEGY_REGISTRY:
        return pd.DataFrame()
    
    info = STRATEGY_REGISTRY[strategy_name]
    strategy_class = info['class']
    
    # Generate parameter grid
    param_grid = generate_param_grid(strategy_name)
    
    if len(param_grid) > max_combinations:
        # Sample down to max_combinations (take evenly spaced samples)
        step = len(param_grid) // max_combinations
        param_grid = param_grid[::step][:max_combinations]
    
    total = len(param_grid)
    results = []
    
    import io, contextlib
    
    for i, params in enumerate(param_grid):
        # Run backtest with this parameter set
        portfolio = Portfolio(initial_cash=initial_capital)
        bt = Backtester(storage=storage, portfolio=portfolio, 
                       risk_manager=risk_manager, slippage_bps=slippage_bps)
        
        f_buf = io.StringIO()
        with contextlib.redirect_stdout(f_buf):
            try:
                metrics = bt.run(
                    strategy_class, symbols=symbols, timeframe=timeframe,
                    start_date=start_date, end_date=end_date, **params
                )
            except Exception:
                metrics = {}
        
        if metrics:
            row = {**params}
            row['return_pct'] = metrics.get('return_pct', 0.0)
            row['sharpe_ratio'] = metrics.get('sharpe_ratio', 0.0)
            row['max_drawdown'] = metrics.get('max_drawdown', 0.0)
            row['win_rate'] = metrics.get('win_rate', 0.0)
            row['profit_factor'] = metrics.get('profit_factor', 0.0)
            row['total_trades'] = metrics.get('total_trades', 0)
            row['alpha'] = metrics.get('alpha', 0.0)
            row['_objective'] = _get_objective_value(metrics, objective)
            results.append(row)
        
        if progress_callback:
            progress_callback((i + 1) / total, f"Tested {i+1}/{total} combinations")
    
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    # Rank zero-trade combos below any combo that actually traded — a parameter
    # set that never enters the market scores a "neutral" objective (e.g. Sharpe 0)
    # and would otherwise outrank losing-but-active configurations.
    df['_traded'] = df['total_trades'] > 0
    df = df.sort_values(['_traded', '_objective'], ascending=[False, False]).reset_index(drop=True)
    df = df.drop(columns=['_traded'])
    return df


# ─── Walk-Forward Optimization ─────────────────────────────────────

@dataclass
class WalkForwardWindow:
    """Represents a single train/test window in walk-forward analysis."""
    window_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    best_params: Dict[str, Any]
    train_metrics: Dict[str, Any]
    test_metrics: Dict[str, Any]


def walk_forward(
    strategy_name: str,
    symbols: List[str],
    timeframe: Timeframe,
    start_date: datetime,
    end_date: datetime,
    storage: DataStorage,
    n_splits: int = 5,
    train_pct: float = 0.7,
    objective: str = 'sharpe_ratio',
    initial_capital: float = 100000.0,
    risk_manager: Optional[RiskManager] = None,
    slippage_bps: float = 0.0,
    top_n_params: int = 20,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """
    Walk-forward optimization with rolling train/test windows.
    
    Process:
      1. Divide the data into n_splits rolling windows
      2. For each window: optimize on train portion, validate on test portion
      3. Report consistency of results across windows
    
    Args:
        strategy_name: Name from STRATEGY_REGISTRY
        symbols: List of stock symbols
        timeframe: Candle timeframe
        start_date: Start of full data period
        end_date: End of full data period
        storage: DataStorage instance
        n_splits: Number of rolling windows
        train_pct: Fraction of each window used for training (e.g., 0.7)
        objective: Metric to optimize
        initial_capital: Starting portfolio value
        risk_manager: Optional risk manager
        slippage_bps: Slippage in basis points
        top_n_params: Number of top parameter sets to test from grid search
        progress_callback: Called with (progress_float, status_string)
        
    Returns:
        Dict with 'windows' (list of WalkForwardWindow), 'summary' metrics,
        and 'oos_equity_curves' for plotting
    """
    if strategy_name not in STRATEGY_REGISTRY:
        return {'windows': [], 'summary': {}}
    
    info = STRATEGY_REGISTRY[strategy_name]
    strategy_class = info['class']
    
    # Calculate window boundaries
    total_days = (end_date - start_date).days
    window_size = total_days // n_splits
    
    if window_size < 30:
        return {'windows': [], 'summary': {'error': 'Date range too short for walk-forward analysis'}}
    
    # Generate param grid (but limit to keep it fast)
    param_grid = generate_param_grid(strategy_name)
    if len(param_grid) > 200:
        step = len(param_grid) // 200
        param_grid = param_grid[::step][:200]
    
    windows: List[WalkForwardWindow] = []
    oos_returns = []  # Out-of-sample returns
    
    import io, contextlib
    
    total_steps = n_splits
    
    for w in range(n_splits):
        if progress_callback:
            progress_callback(w / total_steps, f"Window {w+1}/{n_splits}")
        
        # Window boundaries
        w_start = start_date + timedelta(days=w * window_size)
        w_end = w_start + timedelta(days=window_size)
        
        train_days = int(window_size * train_pct)
        train_start = w_start
        train_end = w_start + timedelta(days=train_days)
        test_start = train_end
        test_end = w_end
        
        # ─── Step 1: Grid search on training data ─────────────
        best_obj = float('-inf')
        best_params = {}
        best_train_metrics = {}
        best_traded = False

        for params in param_grid:
            portfolio = Portfolio(initial_cash=initial_capital)
            bt = Backtester(storage=storage, portfolio=portfolio,
                          risk_manager=risk_manager, slippage_bps=slippage_bps)
            
            f_buf = io.StringIO()
            with contextlib.redirect_stdout(f_buf):
                try:
                    metrics = bt.run(
                        strategy_class, symbols=symbols, timeframe=timeframe,
                        start_date=train_start, end_date=train_end, **params
                    )
                except Exception:
                    metrics = {}
            
            if metrics:
                obj_val = _get_objective_value(metrics, objective)
                traded = metrics.get('total_trades', 0) > 0
                # Prefer combos that actually traded; among equals, higher objective wins
                if (traded, obj_val) > (best_traded, best_obj):
                    best_obj = obj_val
                    best_params = params.copy()
                    best_train_metrics = metrics.copy()
                    best_traded = traded
        
        # ─── Step 2: Validate best params on test data ────────
        portfolio = Portfolio(initial_cash=initial_capital)
        bt = Backtester(storage=storage, portfolio=portfolio,
                       risk_manager=risk_manager, slippage_bps=slippage_bps)
        
        f_buf = io.StringIO()
        with contextlib.redirect_stdout(f_buf):
            try:
                test_metrics = bt.run(
                    strategy_class, symbols=symbols, timeframe=timeframe,
                    start_date=test_start, end_date=test_end, **best_params
                )
            except Exception:
                test_metrics = {}
        
        if not test_metrics:
            test_metrics = {'return_pct': 0.0, 'sharpe_ratio': 0.0, 'max_drawdown': 0.0}
        
        oos_returns.append(test_metrics.get('return_pct', 0.0))
        
        window = WalkForwardWindow(
            window_id=w + 1,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            best_params=best_params,
            train_metrics=best_train_metrics,
            test_metrics=test_metrics,
        )
        windows.append(window)
    
    if progress_callback:
        progress_callback(1.0, "Walk-forward complete")
    
    # ─── Summary Statistics ───────────────────────────────────
    avg_oos_return = sum(oos_returns) / len(oos_returns) if oos_returns else 0.0
    positive_windows = sum(1 for r in oos_returns if r > 0)
    consistency = (positive_windows / len(oos_returns) * 100) if oos_returns else 0.0
    
    # Check if train and test results are consistent (not overfitting)
    train_returns = [w.train_metrics.get('return_pct', 0.0) for w in windows]
    avg_train_return = sum(train_returns) / len(train_returns) if train_returns else 0.0
    
    summary = {
        'n_windows': n_splits,
        'avg_train_return': avg_train_return,
        'avg_oos_return': avg_oos_return,
        'consistency_pct': consistency,
        'positive_windows': positive_windows,
        'total_windows': len(oos_returns),
        'oos_returns': oos_returns,
        'train_returns': train_returns,
    }
    
    return {
        'windows': windows,
        'summary': summary,
    }
