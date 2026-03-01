import pandas as pd
from datetime import datetime
from typing import Optional, List, Callable
from ib_insync import IB, Stock, util, BarDataList
from core.base_provider import BaseDataProvider
from core.models import Timeframe, Candle
from core.aggregator import TickAggregator

class IBProvider(BaseDataProvider):
    def __init__(self, host: str = '127.0.0.1', port: int = 7497, client_id: int = 1):
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self._streaming_contracts = {}
        self._aggregators = {}
        self._on_candle_callbacks: List[Callable[[Candle], None]] = []

    def connect(self):
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            print(f"Connected to IB on {self.host}:{self.port}")

    def disconnect(self):
        self.ib.disconnect()

    def _get_contract(self, symbol: str) -> Stock:
        return Stock(symbol, 'SMART', 'USD')

    def get_historical_data(
        self, 
        symbol: str, 
        timeframe: Timeframe, 
        start_date: datetime, 
        end_date: datetime
    ) -> pd.DataFrame:
        self.connect()
        contract = self._get_contract(symbol)
        self.ib.qualifyContracts(contract)

        # Map timeframe to IB duration/barSize
        # Simplified mapping
        bar_size_map = {
            Timeframe.MIN_1: "1 min",
            Timeframe.MIN_5: "5 mins",
            Timeframe.HOUR_1: "1 hour",
            Timeframe.DAY_1: "1 day"
        }
        bar_size = bar_size_map.get(timeframe, "1 day")
        
        # Calculate duration string (e.g., '1 M', '1 Y')
        delta = end_date - start_date
        if delta.days > 365:
            duration = f"{delta.days // 365 + 1} Y"
        else:
            duration = f"{delta.days + 1} D"

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end_date,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1
        )

        if not bars:
            return pd.DataFrame()

        df = util.df(bars)
        # Standardize columns
        df = df.rename(columns={
            'date': 'timestamp',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume'
        })
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]

    def get_latest_quote(self, symbol: str) -> dict:
        self.connect()
        contract = self._get_contract(symbol)
        self.ib.qualifyContracts(contract)
        ticker = self.ib.reqTickers(contract)[0]
        
        return {
            "symbol": symbol,
            "last_price": ticker.last or ticker.close,
            "timestamp": datetime.now()
        }

    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        # Implementation similar to get_historical_data but fetching just the last bar
        # For simplicity, reuse historical but limited
        df = self.get_historical_data(symbol, timeframe, datetime.now() - pd.Timedelta(days=1), datetime.now())
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

    def start_live_streaming(self, symbol: str, timeframe: Timeframe, callback: Callable[[Candle], None]):
        """
        Subscribe to live ticks and aggregate them into candles.
        """
        self.connect()
        contract = self._get_contract(symbol)
        self.ib.qualifyContracts(contract)
        
        aggregator = TickAggregator(symbol, timeframe)
        self._aggregators[symbol] = aggregator
        self._on_candle_callbacks.append(callback)

        self.ib.reqMktData(contract, '', False, False)
        self.ib.pendingTickHandlers.add(self._on_pending_tick)
        self._streaming_contracts[symbol] = contract
        print(f"Started live streaming for {symbol}")

    def _on_pending_tick(self, ticker):
        symbol = ticker.contract.symbol
        if symbol in self._aggregators:
            price = ticker.last or ticker.close
            volume = ticker.lastSize or 0
            if price:
                candle = self._aggregators[symbol].on_tick(price, volume, datetime.now())
                if candle:
                    for cb in self._on_candle_callbacks:
                        cb(candle)
