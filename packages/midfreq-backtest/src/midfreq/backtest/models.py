"""
Kept for compatibility: the fill models now live in midfreq.backtest.fill_models.

`FillModel(timing=..., slippage_bps=..., round_lots=...)` still builds the bar-based model:
- timing="close" (the default): fill at the close of the bar the decision was made on, as
  midfreq.backtest.backtester does;
- timing="next_open": fill at the instrument's next bar's open;
- LegacyFillModel: fill at the price the core strategy passed, without lot rounding.
"""
from midfreq.backtest.fill_models import (
    CLOSE, NEXT_OPEN, REFERENCE, AutoFillModel, BarFillModel, Fill, FillModel, LegacyFillModel,
    OrderBookFillModel, QuoteFillModel, TradeFillModel,
)

__all__ = ["CLOSE", "NEXT_OPEN", "REFERENCE", "AutoFillModel", "BarFillModel", "Fill", "FillModel",
           "LegacyFillModel", "OrderBookFillModel", "QuoteFillModel", "TradeFillModel"]
