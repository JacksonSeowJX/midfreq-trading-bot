from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime
import pandas as pd
from .models import Timeframe, Candle

class BaseDataProvider(ABC):
    @abstractmethod
    def get_historical_data(
        self, 
        symbol: str, 
        timeframe: Timeframe, 
        start_date: datetime, 
        end_date: datetime
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data and return as a standardized pandas DataFrame.
        DataFrame should have columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        'timestamp' should be the index or a column with UTC datetime objects.
        """
        pass

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """
        Fetch the latest quote for a symbol.
        """
        pass

    @abstractmethod
    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        """
        Fetch the most recent closed candle.
        """
        pass
