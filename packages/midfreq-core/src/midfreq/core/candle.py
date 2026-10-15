from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field

class Timeframe(str, Enum):
    MIN_1 = "1m"
    MIN_5 = "5m"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"

class Candle(BaseModel):
    timestamp: datetime = Field(..., description="UTC timezone-aware timestamp")
    open: float
    high: float
    low: float
    close: float
    volume: float

    model_config = {
        "populate_by_name": True
    }
