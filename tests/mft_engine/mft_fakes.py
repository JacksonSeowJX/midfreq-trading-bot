"""
Fake transports for the connector tests: no network, no broker gateway.

Each fake serves the same small "market" (a list of TruthBar with known open and close times in
UTC) in its source's own native format and pagination style:
- Moomoo: exchange-local CLOSE times as strings, pages with a page_req_key;
- yfinance: frames indexed by OPEN time (UTC), exclusive end, a maximum window per request;
- IBKR: bar objects stamped with OPEN time (UTC with formatDate=2), windows by duration;
- Binance: kline arrays with OPEN time in ms, `limit` rows per call;
- ccxt: OHLCV arrays with OPEN time in ms, `limit` rows per call.
Each can fail its first request with the source's own rate-limit signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional

import pandas as pd

from mft.adapters.binance.connector import HttpResponse


@dataclass(frozen=True)
class TruthBar:
    open_utc: pd.Timestamp
    close_utc: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    trading_date: Optional[date] = None   # set for daily bars

    @property
    def close_ns(self) -> int:
        return int(self.close_utc.value)


def _bar(i: int, open_utc, close_utc, trading_date=None) -> TruthBar:
    px = 100.0 + i
    return TruthBar(pd.Timestamp(open_utc), pd.Timestamp(close_utc), px, px + 2, px - 1, px + 1,
                    1000.0 + i, trading_date)


def equity_intraday(tz: str, days: List[str], sessions_local: List[tuple]) -> List[TruthBar]:
    """Hourly bars for each day, given (open, close) local times per bar."""
    out = []
    for d in days:
        for o, c in sessions_local:
            o_utc = pd.Timestamp(f"{d} {o}").tz_localize(tz).tz_convert("UTC")
            c_utc = pd.Timestamp(f"{d} {c}").tz_localize(tz).tz_convert("UTC")
            out.append(_bar(len(out), o_utc, c_utc))
    return out


def equity_daily(tz: str, days: List[str], close_local: str) -> List[TruthBar]:
    out = []
    for d in days:
        o_utc = pd.Timestamp(f"{d} 00:00").tz_localize(tz).tz_convert("UTC")
        c_utc = pd.Timestamp(f"{d} {close_local}").tz_localize(tz).tz_convert("UTC")
        out.append(_bar(len(out), o_utc, c_utc, pd.Timestamp(d).date()))
    return out


def crypto_hourly(start: str, n: int) -> List[TruthBar]:
    t0 = pd.Timestamp(start, tz="UTC")
    return [_bar(i, t0 + pd.Timedelta(hours=i), t0 + pd.Timedelta(hours=i + 1)) for i in range(n)]


HK_HOURS = [("09:30", "10:30"), ("10:30", "11:30"), ("11:30", "12:00"),
            ("13:00", "14:00"), ("14:00", "15:00"), ("15:00", "16:00")]
US_HOURS = [("09:30", "10:30"), ("10:30", "11:30"), ("11:30", "12:30"), ("12:30", "13:30"),
            ("13:30", "14:30"), ("14:30", "15:30"), ("15:30", "16:00")]


class RecordingSleep:
    def __init__(self):
        self.calls: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ── Moomoo ───────────────────────────────────────────────────────────

class FakeMoomooContext:
    def __init__(self, bars: List[TruthBar], tz: str, daily: bool = False, rate_limit_first: bool = False,
                 lot_sizes: Optional[Dict[str, int]] = None):
        self.bars, self.tz, self.daily = bars, tz, daily
        self.rate_limit_first = rate_limit_first
        self.calls: List[dict] = []
        self.lot_sizes = lot_sizes or {}

    def request_history_kline(self, code, start=None, end=None, ktype=None, autype=None,
                              max_count=1000, page_req_key=None, **kwargs):
        self.calls.append(dict(code=code, start=start, end=end, ktype=ktype, page_req_key=page_req_key))
        if self.rate_limit_first and len(self.calls) == 1:
            return -1, "Request too frequent: at most 60 history kline requests per 30 seconds", None
        rows = []
        for b in self.bars:
            local_close = b.close_utc.tz_convert(self.tz)
            day = (b.trading_date.isoformat() if self.daily else local_close.strftime("%Y-%m-%d"))
            if start <= day <= end:
                key = f"{day} 00:00:00" if self.daily else local_close.strftime("%Y-%m-%d %H:%M:%S")
                rows.append(dict(code=code, time_key=key, open=b.open, high=b.high, low=b.low,
                                 close=b.close, volume=b.volume))
        offset = page_req_key or 0
        page = rows[offset: offset + max_count]
        nxt = offset + max_count if offset + max_count < len(rows) else None
        return 0, pd.DataFrame(page), nxt

    def get_stock_basicinfo(self, market, stock_type="STOCK"):
        rows = [dict(code=c, lot_size=l, name=c) for c, l in self.lot_sizes.items() if c.startswith(market + ".")]
        return 0, pd.DataFrame(rows)


class FakeMoomooProvider:
    def __init__(self, ctx: FakeMoomooContext):
        self.ctx = ctx
        self.closed = False

    def _get_context(self):
        return self.ctx

    def get_lot_sizes(self, symbols):
        return {s: self.ctx.lot_sizes[s] for s in symbols if s in self.ctx.lot_sizes}

    def close(self):
        self.closed = True


# ── yfinance ─────────────────────────────────────────────────────────

class YFRateLimitError(Exception):
    """Same name as yfinance.exceptions.YFRateLimitError."""


class FakeYFinanceProvider:
    def __init__(self, bars: List[TruthBar], daily: bool = False, max_window_days: Optional[float] = None,
                 rate_limit_first: bool = False):
        self.bars, self.daily = bars, daily
        self.max_window = pd.Timedelta(days=max_window_days) if max_window_days else None
        self.rate_limit_first = rate_limit_first
        self.calls: List[tuple] = []

    def get_historical_data(self, symbol, timeframe, start_date, end_date):
        self.calls.append((symbol, timeframe, start_date, end_date))
        if self.rate_limit_first and len(self.calls) == 1:
            raise YFRateLimitError("Too Many Requests. Rate limited. Try after a while.")
        start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
        if self.max_window is not None:
            assert end - start <= self.max_window, "connector exceeded the per-request window"
        rows, index = [], []
        for b in self.bars:
            if self.daily:
                stamp = pd.Timestamp(b.trading_date).tz_localize("UTC")   # naive date tagged UTC
                key = pd.Timestamp(b.trading_date).tz_localize("UTC")
            else:
                stamp = key = b.open_utc
            if start <= key < end:   # yfinance's end is exclusive
                index.append(stamp)
                rows.append(dict(open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume))
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="timestamp"))
        return df


# ── IBKR ─────────────────────────────────────────────────────────────

class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def __isub__(self, h):
        self.handlers.remove(h)
        return self

    def emit(self, *args):
        for h in list(self.handlers):
            h(*args)


class FakeIB:
    def __init__(self, bars: List[TruthBar], daily: bool = False, pacing_first: bool = False, min_tick=0.05):
        self.bars, self.daily = bars, daily
        self.pacing_first = pacing_first
        self.errorEvent = FakeEvent()
        self.calls: List[dict] = []
        self.min_tick = min_tick

    def qualifyContracts(self, *contracts):
        return list(contracts)

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow,
                          useRTH, formatDate=1, **kwargs):
        assert formatDate == 2, "the connector must ask IB for UTC timestamps"
        self.calls.append(dict(contract=contract, end=endDateTime, duration=durationStr, bar_size=barSizeSetting))
        if self.pacing_first and len(self.calls) == 1:
            self.errorEvent.emit(7, 162, "Historical Market Data Service error message:API historical data query cancelled: pacing violation", contract)
            return []
        n, unit = durationStr.split()
        assert unit == "D"
        end = pd.Timestamp(endDateTime)
        end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
        start = end - pd.Timedelta(days=int(n))
        out = []
        for b in self.bars:
            if start <= b.open_utc < end:
                stamp = b.trading_date if self.daily else b.open_utc.to_pydatetime()
                out.append(SimpleNamespace(date=stamp, open=b.open, high=b.high, low=b.low,
                                           close=b.close, volume=b.volume))
        return out

    def reqContractDetails(self, contract):
        return [SimpleNamespace(minTick=self.min_tick, contract=contract)]


class FakeIBProvider:
    def __init__(self, ib: FakeIB):
        self.ib = ib
        self.contracts = {}
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def _get_contract(self, symbol):
        spec = self.contracts.get(symbol, {"symbol": symbol, "exchange": "ISLAND", "currency": "USD"})
        return SimpleNamespace(**spec)


# ── Binance ──────────────────────────────────────────────────────────

class FakeBinanceTransport:
    def __init__(self, bars: List[TruthBar], interval_ms: int, rate_limit_first: bool = False,
                 symbols_info: Optional[list] = None):
        self.bars, self.interval_ms = bars, interval_ms
        self.rate_limit_first = rate_limit_first
        self.calls: List[tuple] = []
        self.symbols_info = symbols_info or []

    def get(self, url, params):
        self.calls.append((url, dict(params)))
        if self.rate_limit_first and len(self.calls) == 1:
            return HttpResponse(429, {"code": -1003, "msg": "Too much request weight used"}, {"Retry-After": "3"})
        if url.endswith("/api/v3/klines"):
            rows = []
            for b in self.bars:
                o = int(b.open_utc.value // 1_000_000)
                if params["startTime"] <= o <= params["endTime"]:
                    rows.append([o, str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume),
                                 o + self.interval_ms - 1, "0", 10, "0", "0", "0"])
            return HttpResponse(200, rows[: params["limit"]], {})
        if url.endswith("/api/v3/exchangeInfo"):
            return HttpResponse(200, {"symbols": self.symbols_info}, {})
        return HttpResponse(404, {"msg": "not found"}, {})


BINANCE_BTCUSDT_INFO = {
    "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000.00", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000.0", "stepSize": "0.00001"},
        {"filterType": "NOTIONAL", "minNotional": "5.00", "applyMinToMarket": True},
    ],
}


# ── ccxt ─────────────────────────────────────────────────────────────

class FakeCcxtExchange:
    def __init__(self, bars: List[TruthBar], rate_limit_first: bool = False, page_cap: int = 1000,
                 markets: Optional[dict] = None, rate_limit_exc: type = Exception):
        self.bars = bars
        self.rate_limit_first = rate_limit_first
        self.page_cap = page_cap
        self.timeframes = {"1m": "1m", "5m": "5m", "1h": "1h", "1d": "1d"}
        self.precisionMode = 4
        self.markets = markets or {}
        self.calls: List[dict] = []
        self.rate_limit_exc = rate_limit_exc

    def load_markets(self):
        return self.markets

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None):
        self.calls.append(dict(symbol=symbol, timeframe=timeframe, since=since, limit=limit))
        if self.rate_limit_first and len(self.calls) == 1:
            raise self.rate_limit_exc("kraken {\"error\":[\"EAPI:Rate limit exceeded\"]}")
        rows = [[int(b.open_utc.value // 1_000_000), b.open, b.high, b.low, b.close, b.volume]
                for b in self.bars if since is None or b.open_utc.value // 1_000_000 >= since]
        return rows[: min(limit or self.page_cap, self.page_cap)]


def ccxt_market(base: str, quote: str, market_id: str, tick: float = 0.1, step: float = 1e-8,
                min_amount: float = 1e-4, min_cost: float = 0.5) -> dict:
    return {
        "id": market_id, "symbol": f"{base}/{quote}", "base": base, "quote": quote, "spot": True,
        "precision": {"price": tick, "amount": step},
        "limits": {"amount": {"min": min_amount}, "cost": {"min": min_cost}},
        "maker": 0.0025, "taker": 0.004,
    }


def utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def now_ns_at(s: str):
    ns = int(pd.Timestamp(s, tz="UTC").value)
    return lambda: ns


def as_naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)
