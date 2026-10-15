"""Turning pandas and numpy values into what JSON can carry."""
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def plain(value: Any) -> Any:
    """`value` with numpy scalars made Python scalars and NaN or infinity made None, recursively."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """A frame's rows as plain dicts (column name -> value), NaN as None."""
    cols = [str(c) for c in df.columns]
    columns = [df.iloc[:, i].tolist() for i in range(df.shape[1])]     # tolist: Python scalars
    return [{c: plain(v) for c, v in zip(cols, row)} for row in zip(*columns)]


def utc(value: Any, naive_tz: str = "UTC") -> Optional[datetime]:
    """A timestamp in UTC. A time without a zone is read in `naive_tz`. None if unparseable."""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if ts is pd.NaT:
        return None
    if ts.tzinfo is None:
        try:
            ts = ts.tz_localize(naive_tz, ambiguous="NaT", nonexistent="NaT")
        except (TypeError, ValueError):
            return None
        if ts is pd.NaT:
            return None
    return ts.tz_convert("UTC").to_pydatetime()
