import pandas as pd
from datetime import datetime
from typing import Optional, Callable
from moomoo import (
    OpenQuoteContext, RET_OK, SubType, KLType, KL_FIELD,
    CurKlineHandlerBase
)
from core.base_provider import BaseDataProvider
from core.models import Timeframe, Candle


class MoomooProvider(BaseDataProvider):
    """
    Market data provider using Moomoo (Futu) OpenD gateway.
    Supports free live data for SG and HK markets.
    
    Requires:
        - OpenD gateway running locally (default: 127.0.0.1:11111)
        - Moomoo account logged in on OpenD
    """
    
    def __init__(self, host: str = '127.0.0.1', port: int = 11111):
        self.host = host
        self.port = port
        self._quote_ctx = None

    # Mapping our Timeframe enum to Moomoo's KLType
    _kl_type_map = {
        Timeframe.MIN_1: KLType.K_1M,
        Timeframe.MIN_5: KLType.K_5M,
        Timeframe.HOUR_1: KLType.K_60M,
        Timeframe.HOUR_4: KLType.K_4H,
        Timeframe.DAY_1: KLType.K_DAY,
    }

    def _get_context(self) -> OpenQuoteContext:
        """Get or create a quote context connection to OpenD."""
        if self._quote_ctx is None:
            self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            print(f"Connected to Moomoo OpenD on {self.host}:{self.port}")
        return self._quote_ctx

    def close(self):
        """Close the connection to OpenD."""
        if self._quote_ctx is not None:
            self._quote_ctx.close()
            self._quote_ctx = None
            print("Disconnected from Moomoo OpenD.")

    def get_historical_data(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_date: datetime,
        end_date: datetime
    ) -> pd.DataFrame:
        ctx = self._get_context()
        kl_type = self._kl_type_map.get(timeframe, KLType.K_DAY)

        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')

        ret, data, _ = ctx.request_history_kline(
            symbol,
            start=start_str,
            end=end_str,
            ktype=kl_type,
            fields=[
                KL_FIELD.DATE_TIME,
                KL_FIELD.OPEN,
                KL_FIELD.HIGH,
                KL_FIELD.LOW,
                KL_FIELD.CLOSE,
                KL_FIELD.VOLUME,
            ],
            max_count=1000
        )

        if ret != RET_OK:
            print(f"Error fetching historical data: {data}")
            return pd.DataFrame()

        # Standardize columns
        df = data.rename(columns={
            'time_key': 'timestamp',
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
        ctx = self._get_context()

        # Subscribe first (required by Moomoo API)
        ret, err = ctx.subscribe([symbol], [SubType.QUOTE])
        if ret != RET_OK:
            print(f"Subscribe error: {err}")
            return {"symbol": symbol, "error": str(err)}

        ret, data = ctx.get_stock_quote([symbol])
        if ret != RET_OK:
            print(f"Quote error: {data}")
            return {"symbol": symbol, "error": str(data)}

        row = data.iloc[0]
        return {
            "symbol": symbol,
            "last_price": row.get('last_price'),
            "bid": row.get('bid_price'),
            "ask": row.get('ask_price'),
            "volume": row.get('volume'),
            "timestamp": datetime.now()
        }

    def get_latest_candle(self, symbol: str, timeframe: Timeframe) -> Optional[Candle]:
        df = self.get_historical_data(
            symbol, timeframe,
            datetime.now() - pd.Timedelta(days=7),
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

    def start_live_streaming(
        self,
        symbol: str,
        timeframe: Timeframe,
        callback: Callable[[Candle], None]
    ):
        """
        Subscribe to live candlestick (K-line) push updates from Moomoo.
        """
        ctx = self._get_context()
        kl_type = self._kl_type_map.get(timeframe, KLType.K_1M)

        # Map KLType to SubType for subscription
        kl_sub_map = {
            KLType.K_1M: SubType.K_1M,
            KLType.K_5M: SubType.K_5M,
            KLType.K_60M: SubType.K_60M,
            KLType.K_DAY: SubType.K_DAY,
        }
        sub_type = kl_sub_map.get(kl_type, SubType.K_1M)

        # Create handler class
        class LiveCandleHandler(CurKlineHandlerBase):
            def on_recv_rsp(self, rsp_pb):
                ret_code, data = super().on_recv_rsp(rsp_pb)
                if ret_code != RET_OK:
                    return ret_code, data

                for _, row in data.iterrows():
                    candle = Candle(
                        timestamp=pd.to_datetime(row['time_key'], utc=True),
                        open=row['open'],
                        high=row['high'],
                        low=row['low'],
                        close=row['close'],
                        volume=row['volume']
                    )
                    callback(candle)
                return ret_code, data

        ctx.set_handler(LiveCandleHandler())

        ret, err = ctx.subscribe([symbol], [sub_type])
        if ret != RET_OK:
            print(f"Streaming subscription error: {err}")
        else:
            print(f"Started live {timeframe.value} candle streaming for {symbol}")
