"""
midfreq.strategies: the trading rules, with no knowledge of how they are run.

- candle_strategies: the candle-based strategy library (BaseStrategy and its subclasses) and
  STRATEGY_REGISTRY, the names and parameter ranges the dashboard, optimizer and live bot use.
  These are run by midfreq.backtest.backtester.Backtester, or unchanged inside BacktestEngine via
  midfreq.backtest.legacy_adapter.
- trading: the event-driven Strategy API (Strategy, StrategyConfig) that BacktestEngine drives.
- event_driven: strategies written natively against that API (MACrossover, CrossSectionalRanker,
  EqualWeightBasket).
"""
