import pandas as pd
from datetime import datetime
from typing import Optional, List, Callable
from ib_insync import IB, Stock, util
from core.base_provider import BaseDataProvider
from core.models import Timeframe, Candle
from core.aggregator import TickAggregator

# Enable ib_insync's own event loop integration
util.startLoop()

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
            # Request delayed frozen data as fallback if live subscription is unavailable
            # Type 1 = Live, Type 3 = Delayed, Type 4 = Delayed Frozen
            self.ib.reqMarketDataType(4)
            print(f"Connected to IB on {self.host}:{self.port}")
            print("Market data type: Delayed Frozen (will auto-upgrade to Live if subscribed)")


    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            print("Disconnected from IB.")

    def _get_contract(self, symbol: str) -> Stock:
        return Stock(symbol, 'ISLAND', 'USD')

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

        bar_size_map = {
            Timeframe.MIN_1: "1 min",
            Timeframe.MIN_5: "5 mins",
            Timeframe.HOUR_1: "1 hour",
            Timeframe.HOUR_4: "4 hours",
            Timeframe.DAY_1: "1 day"
        }
        bar_size = bar_size_map.get(timeframe, "1 day")
        
        # Calculate duration string
        delta = end_date - start_date
        if delta.days > 365:
            duration = f"{delta.days // 365 + 1} Y"
        elif delta.days > 0:
            duration = f"{delta.days + 1} D"
        else:
            duration = "1 D"

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
        df = df.rename(columns={
            'date': 'timestamp',
        })
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]

    def get_latest_quote(self, symbol: str) -> dict:
        self.connect()
        contract = self._get_contract(symbol)
        self.ib.qualifyContracts(contract)
        
        # Use streaming subscription (not snapshot) to avoid regulatory snapshot permission error
        self.ib.reqMktData(contract, '', False, False)
        self.ib.sleep(3)  # Give IB time to return delayed/live data
        ticker = self.ib.ticker(contract)
        
        # Extract prices — use delayed fields as fallback
        last = ticker.last if ticker.last == ticker.last else None
        bid = ticker.bid if ticker.bid == ticker.bid else None
        ask = ticker.ask if ticker.ask == ticker.ask else None
        close = ticker.close if ticker.close == ticker.close else None
        
        # Cancel the market data subscription after getting the quote
        self.ib.cancelMktData(contract)
        
        return {
            "symbol": symbol,
            "last_price": last or close,
            "bid": bid,
            "ask": ask,
            "timestamp": datetime.now()
        }


    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        df = self.get_historical_data(
            symbol, timeframe, 
            datetime.now() - pd.Timedelta(days=1), 
            datetime.now()
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
        self.ib.pendingTickersEvent += self._on_pending_tickers
        self._streaming_contracts[symbol] = contract
        print(f"Started live streaming for {symbol}")

    def _on_pending_tickers(self, tickers):
        for ticker in tickers:
            if not hasattr(ticker, 'contract') or ticker.contract is None:
                continue
            symbol = ticker.contract.symbol
            if symbol in self._aggregators:
                price = ticker.last if ticker.last == ticker.last else None
                volume = ticker.lastSize if ticker.lastSize == ticker.lastSize else 0
                if price and price > 0:
                    candle = self._aggregators[symbol].on_tick(price, volume, datetime.now())
                    if candle:
                        for cb in self._on_candle_callbacks:
                            cb(candle)

    def run_live(self, duration_seconds: int = 60):
        """
        Run the IB event loop for a specified duration to receive live data.
        """
        print(f"Listening for live data for {duration_seconds} seconds...")
        self.ib.sleep(duration_seconds)
