"""
midfreq.backtest: running strategies over historical data.

Two engines live here:
- backtester.Backtester (with optimizer's grid search and walk-forward on top): the
  candle-by-candle loop for midfreq.strategies.candle_strategies classes. Its core runs in C++
  (packages/midfreq-engine, through pybind11 and native.NativeBacktester) with identical results,
  falling back to backtester.PythonBacktester, the pure-Python reference, wherever the C++ cannot
  be sure to match; the optimiser batches its runs natively (native.BatchRunner).
  MIDFREQ_ENGINE=python turns the C++ off.
- BacktestEngine (re-exported below): the event-driven engine with simulated venues, fill and
  slippage models and a risk engine, for midfreq.strategies.trading.Strategy subclasses.
  legacy_adapter runs the candle strategies inside it with identical results.
"""
from midfreq.backtest.data_engine import DataEngine, TimeSlice
from midfreq.backtest.engine import BacktestEngine, BacktestEngineConfig
from midfreq.backtest.exchange import SimulatedExchange
from midfreq.backtest.fill_models import (
    AutoFillModel, BarFillModel, Fill, FillModel, LegacyFillModel, OrderBookFillModel,
    QuoteFillModel, TradeFillModel,
)
from midfreq.backtest.market_state import MarketState
from midfreq.backtest.slippage import (
    FixedBpsSlippage, FixedTickSlippage, NoSlippage, RandomSlippage, SlippageModel, SpreadSlippage,
    SquareRootImpactSlippage, VolatilitySlippage,
)
from midfreq.backtest.results import BacktestResult

__all__ = ["AutoFillModel", "BacktestEngine", "BacktestEngineConfig", "BacktestResult", "BarFillModel",
           "DataEngine", "Fill", "FillModel", "FixedBpsSlippage", "FixedTickSlippage", "LegacyFillModel",
           "MarketState", "NoSlippage", "OrderBookFillModel", "QuoteFillModel", "RandomSlippage",
           "SimulatedExchange", "SlippageModel", "SpreadSlippage", "SquareRootImpactSlippage",
           "TimeSlice", "TradeFillModel", "VolatilitySlippage"]
