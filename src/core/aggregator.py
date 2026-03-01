from datetime import datetime
from typing import Dict, List, Optional
from .models import Candle, Timeframe

class TickAggregator:
    """
    Aggregates real-time ticks into OHLCV candles for specific timeframes.
    """
    def __init__(self, symbol: str, timeframe: Timeframe):
        self.symbol = symbol
        self.timeframe = timeframe
        self.current_candle: Optional[Dict] = None
        self._interval_seconds = self._get_seconds(timeframe)

    def _get_seconds(self, timeframe: Timeframe) -> int:
        mapping = {
            Timeframe.MIN_1: 60,
            Timeframe.MIN_5: 300,
            Timeframe.HOUR_1: 3600,
            Timeframe.HOUR_4: 14400,
            Timeframe.DAY_1: 86400
        }
        return mapping.get(timeframe, 60)

    def on_tick(self, price: float, volume: float, timestamp: datetime) -> Optional[Candle]:
        """
        Process a new tick and return a completed Candle if the timeframe boundary is crossed.
        """
        # Round timestamp down to the start of the timeframe bucket
        bucket_ts = timestamp.replace(second=0, microsecond=0)
        if self._interval_seconds >= 3600:
             bucket_ts = bucket_ts.replace(minute=0)
        if self._interval_seconds >= 86400:
             bucket_ts = bucket_ts.replace(hour=0)
             
        # Initialization
        if self.current_candle is None:
            self.current_candle = {
                "timestamp": bucket_ts,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume
            }
            return None

        # Check if we are still in the same bucket
        if (timestamp - self.current_candle["timestamp"]).total_seconds() < self._interval_seconds:
            self.current_candle["high"] = max(self.current_candle["high"], price)
            self.current_candle["low"] = min(self.current_candle["low"], price)
            self.current_candle["close"] = price
            self.current_candle["volume"] += volume
            return None
        else:
            # Bucket closed, return the candle and start a new one
            completed_candle = Candle(**self.current_candle)
            
            self.current_candle = {
                "timestamp": bucket_ts,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume
            }
            return completed_candle
