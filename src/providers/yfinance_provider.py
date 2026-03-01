import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Optional
from core.base_provider import BaseDataProvider
from core.models import Timeframe, Candle

class YFinanceProvider(BaseDataProvider):
    def __init__(self):
        # Mapping our internal Timeframe enum to yfinance intervals
        self._interval_map = {
            Timeframe.MIN_1: "1m",
            Timeframe.MIN_5: "5m",
            Timeframe.HOUR_1: "1h",
            Timeframe.HOUR_4: "1h",  # yfinance doesn't natively support 4h easily, might need resampling
            Timeframe.DAY_1: "1d",
        }

    def get_historical_data(
        self, 
        symbol: str, 
        timeframe: Timeframe, 
        start_date: datetime, 
        end_date: datetime
    ) -> pd.DataFrame:
        interval = self._interval_map.get(timeframe, "1d")
        
        # yfinance download
        df = yf.download(
            tickers=symbol,
            start=start_date,
            end=end_date,
            interval=interval,
            progress=False
        )
        
        if df.empty:
            return pd.DataFrame()

        # Standardize columns
        df = df.reset_index()
        
        # yfinance columns can be MultiIndex or simple depending on version/tickers
        # We ensure they are flattened
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        standard_columns = {
            'Date': 'timestamp',
            'Datetime': 'timestamp',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        }
        
        df = df.rename(columns=standard_columns)
        
        # Filter only needed columns
        needed = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        df = df[needed]
        
        # Ensure timestamp is UTC
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df.set_index('timestamp', inplace=True)
        
        return df

    def get_latest_quote(self, symbol: str) -> dict:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        return {
            "symbol": symbol,
            "last_price": info.last_price,
            "timestamp": datetime.now()
        }

    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        # Implementation to get the most recent candle
        df = self.get_historical_data(
            symbol, 
            timeframe, 
            start_date=datetime.now() - pd.Timedelta(days=7), # Fetch recent chunk
            end_date=datetime.now()
        )
        
        if df.empty:
            return None
            
        last_row = df.iloc[-1]
        return Candle(
            timestamp=df.index[-1],
            open=last_row['open'],
            high=last_row['high'],
            low=last_row['low'],
            close=last_row['close'],
            volume=last_row['volume']
        )
