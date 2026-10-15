"""
The connector contract: every connector must pass these, each against a fake transport that speaks
its source's native format (no network, no gateway).

1. symbols round-trip InstrumentId -> native -> InstrumentId, with the expected native spelling;
2. paginated history is stitched with no gaps and no duplicates;
3. bar close times are true UTC (ts_event = bar close, HKEX lunch and US daylight saving included);
4. the instrument provider returns the venue's trading rules;
5. an unsupported interval raises UnsupportedIntervalError (and an unserved venue is refused);
6. a rate-limit response is retried after a backoff and the result is still complete.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict

import pytest

from mft.adapters.base import UnsupportedIntervalError, UnsupportedVenueError
from mft.adapters.binance.connector import BinanceConnector
from mft.adapters.ccxt.connector import CcxtConnector, ccxt_available
from mft.adapters.ibkr.connector import IBKRConnector
from mft.adapters.moomoo.connector import MoomooConnector
from mft.adapters.yfinance.connector import YFinanceConnector
from mft.model import BarSpecification, BarType, InstrumentId

from mft_fakes import (
    BINANCE_BTCUSDT_INFO, HK_HOURS, US_HOURS, FakeBinanceTransport, FakeCcxtExchange, FakeIB,
    FakeIBProvider, FakeMoomooContext, FakeMoomooProvider, FakeYFinanceProvider, RecordingSleep,
    crypto_hourly, ccxt_market, equity_intraday, now_ns_at,
)

NOW = now_ns_at("2024-06-01")
HK_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04"]
US_DST_DAYS = ["2024-03-08", "2024-03-11"]          # EST on Friday, EDT on Monday
QUIET = {"min_request_interval": 0.0}


@dataclass
class Case:
    id: str
    instrument: str
    bar: str
    truth: list
    build: Callable                      # (truth, rate_limit_first, sleep) -> connector
    natives: Dict[str, str]              # InstrumentId -> expected native symbol
    unsupported: str
    instrument_expect: Dict[str, object]
    unserved_venue: str
    marks: list = field(default_factory=list)


def _track(conn, calls):
    conn.fake_calls = calls      # the fake transport's request log, for the pagination check
    return conn


def _moomoo(truth, rate_limit_first, sleep):
    ctx = FakeMoomooContext(truth, "Asia/Hong_Kong", rate_limit_first=rate_limit_first,
                            lot_sizes={"HK.00700": 100})
    return _track(MoomooConnector({**QUIET, "max_count": 4}, provider=FakeMoomooProvider(ctx), sleep=sleep,
                                  now_ns=NOW), ctx.calls)


def _moomoo_us(truth, rate_limit_first, sleep):
    ctx = FakeMoomooContext(truth, "America/New_York", rate_limit_first=rate_limit_first,
                            lot_sizes={"US.AAPL": 1})
    return _track(MoomooConnector({**QUIET, "max_count": 3}, provider=FakeMoomooProvider(ctx), sleep=sleep,
                                  now_ns=NOW), ctx.calls)


def _yfinance(truth, rate_limit_first, sleep):
    fake = FakeYFinanceProvider(truth, max_window_days=1, rate_limit_first=rate_limit_first)
    return _track(YFinanceConnector({**QUIET, "window_days": 1}, provider=fake, sleep=sleep, now_ns=NOW), fake.calls)


def _ibkr(truth, rate_limit_first, sleep):
    fake = FakeIBProvider(FakeIB(truth, pacing_first=rate_limit_first, min_tick=0.2))
    return _track(IBKRConnector({**QUIET, "chunk_days": 1}, provider=fake, sleep=sleep, now_ns=NOW), fake.ib.calls)


def _binance(truth, rate_limit_first, sleep):
    fake = FakeBinanceTransport(truth, 3_600_000, rate_limit_first=rate_limit_first,
                                symbols_info=[BINANCE_BTCUSDT_INFO])
    return _track(BinanceConnector({**QUIET, "klines_limit": 5}, transport=fake, sleep=sleep, now_ns=NOW), fake.calls)


def _ccxt(truth, rate_limit_first, sleep):
    import ccxt
    ex = FakeCcxtExchange(truth, rate_limit_first=rate_limit_first, rate_limit_exc=ccxt.RateLimitExceeded,
                          markets={"BTC/USD": ccxt_market("BTC", "USD", "XXBTZUSD", tick=0.1, min_cost=0.5)})
    return _track(CcxtConnector({**QUIET, "bars_per_request": 5}, exchanges={"KRAKEN": ex}, sleep=sleep,
                                now_ns=NOW), ex.calls)


CASES = [
    Case("moomoo-XHKG", "00700.XHKG", "1-HOUR",
         equity_intraday("Asia/Hong_Kong", HK_DAYS, HK_HOURS), _moomoo,
         {"00700.XHKG": "HK.00700", "00005.XHKG": "HK.00005", "AAPL.XNAS": "US.AAPL",
          "JPM.XNYS": "US.JPM", "600519.XSHG": "SH.600519", "000001.XSHE": "SZ.000001",
          "D05.XSES": "SG.D05", "7203.XTKS": "JP.7203"},
         "4-HOUR", {"currency": "HKD", "lot_size": 100.0, "price_increment": 0.001}, "BINANCE"),
    Case("moomoo-XNAS-dst", "AAPL.XNAS", "1-HOUR",
         equity_intraday("America/New_York", US_DST_DAYS, US_HOURS), _moomoo_us,
         {"AAPL.XNAS": "US.AAPL", "BRK.B.XNYS": "US.BRK.B"},
         "4-HOUR", {"currency": "USD", "lot_size": 1.0}, "KRAKEN"),
    Case("yfinance-XNAS-dst", "AAPL.XNAS", "1-HOUR",
         equity_intraday("America/New_York", US_DST_DAYS, US_HOURS), _yfinance,
         {"AAPL.XNAS": "AAPL", "BRK.B.XNYS": "BRK-B", "00700.XHKG": "0700.HK",
          "09988.XHKG": "9988.HK", "D05.XSES": "D05.SI", "600519.XSHG": "600519.SS",
          "000001.XSHE": "000001.SZ", "7203.XTKS": "7203.T"},
         "4-HOUR", {"currency": "USD", "lot_size": 1.0}, "BINANCE"),
    Case("ibkr-XHKG", "00700.XHKG", "1-HOUR",
         equity_intraday("Asia/Hong_Kong", HK_DAYS, HK_HOURS), _ibkr,
         {"00700.XHKG": "700", "00005.XHKG": "5", "AAPL.XNAS": "AAPL", "BRK.B.XNYS": "BRK B"},
         "7-MINUTE", {"currency": "HKD", "price_increment": 0.2}, "COINBASE"),
    Case("binance", "BTCUSDT.BINANCE", "1-HOUR", crypto_hourly("2024-01-01", 30), _binance,
         {"BTCUSDT.BINANCE": "BTCUSDT", "ETHUSDT.BINANCE": "ETHUSDT"},
         "7-MINUTE", {"currency": "USDT", "price_increment": 0.01, "size_increment": 0.00001,
                      "min_quantity": 0.00001, "min_notional": 5.0, "base_currency": "BTC"}, "XHKG"),
    Case("ccxt-KRAKEN", "BTC-USD.KRAKEN", "1-HOUR", crypto_hourly("2024-01-01", 30), _ccxt,
         {"BTC-USD.KRAKEN": "BTC/USD", "ETH-USD.KRAKEN": "ETH/USD"},
         "4-HOUR", {"currency": "USD", "price_increment": 0.1, "size_increment": 1e-8,
                    "min_notional": 0.5, "base_currency": "BTC"}, "XNAS",
         marks=[pytest.mark.skipif(not ccxt_available(), reason="ccxt not installed")]),
]

PARAMS = [pytest.param(c, id=c.id, marks=c.marks) for c in CASES]


def _request_all(conn, case: Case):
    bar_type = BarType.of(case.instrument, case.bar)
    return conn.data_client.request_bars(bar_type, case.truth[0].close_utc, case.truth[-1].close_utc)


@pytest.mark.parametrize("case", PARAMS)
def test_symbols_round_trip(case):
    conn = case.build(case.truth, False, RecordingSleep())
    for iid_str, native in case.natives.items():
        iid = InstrumentId.from_str(iid_str)
        assert conn.to_native(iid) == native
        assert conn.from_native(native, iid.venue) == iid


@pytest.mark.parametrize("case", PARAMS)
def test_pagination_has_no_gaps_or_duplicates(case):
    conn = case.build(case.truth, False, RecordingSleep())
    bars = _request_all(conn, case)
    assert len(conn.fake_calls) >= 3, "the case should need several pages or windows"
    stamps = [b.ts_event for b in bars]
    assert stamps == sorted(set(stamps)), "out of order or duplicated"
    assert stamps == [t.close_ns for t in case.truth], "missing or extra bars"
    assert [b.close for b in bars] == [t.close for t in case.truth]
    assert [b.volume for b in bars] == [t.volume for t in case.truth]


@pytest.mark.parametrize("case", PARAMS)
def test_bar_close_times_are_utc(case):
    conn = case.build(case.truth, False, RecordingSleep())
    bars = _request_all(conn, case)
    for bar, truth in zip(bars, case.truth):
        assert bar.timestamp == truth.close_utc
        assert bar.timestamp.tzinfo is not None and str(bar.timestamp.tz) == "UTC"
        assert bar.ts_init == bar.ts_event
    # A window that ends mid-history returns only bars closed by then.
    mid = case.truth[len(case.truth) // 2]
    bar_type = BarType.of(case.instrument, case.bar)
    part = conn.data_client.request_bars(bar_type, case.truth[0].close_utc, mid.close_utc)
    assert part[-1].ts_event == mid.close_ns


@pytest.mark.parametrize("case", PARAMS)
def test_instrument_provider_returns_trading_rules(case):
    conn = case.build(case.truth, False, RecordingSleep())
    (inst,) = conn.instrument_provider.load([case.instrument])
    assert str(inst.id) == case.instrument
    for attr, expected in case.instrument_expect.items():
        assert getattr(inst, attr) == pytest.approx(expected) if isinstance(expected, float) \
            else getattr(inst, attr) == expected, attr
    assert conn.instrument_provider.find(case.instrument) is inst


@pytest.mark.parametrize("case", PARAMS)
def test_unsupported_interval_and_venue_are_refused(case):
    conn = case.build(case.truth, False, RecordingSleep())
    with pytest.raises(UnsupportedIntervalError, match=case.unsupported):
        conn.data_client.request_bars(BarType.of(case.instrument, case.unsupported),
                                      case.truth[0].close_utc, case.truth[-1].close_utc)
    other = InstrumentId.of("X", case.unserved_venue)
    with pytest.raises(UnsupportedVenueError):
        conn.data_client.request_bars(BarType(other, BarSpecification.from_str(case.bar)),
                                      case.truth[0].close_utc, case.truth[-1].close_utc)


@pytest.mark.parametrize("case", PARAMS)
def test_rate_limit_is_retried_with_backoff(case):
    sleep = RecordingSleep()
    conn = case.build(case.truth, True, sleep)
    bars = _request_all(conn, case)
    assert [b.ts_event for b in bars] == [t.close_ns for t in case.truth]
    assert sleep.calls, "the connector did not wait before retrying"


@pytest.mark.parametrize("case", PARAMS)
def test_capabilities_flags(case):
    conn = case.build(case.truth, False, RecordingSleep())
    caps = conn.capabilities
    assert caps.historical_bars and not caps.execution and not caps.live_data
    assert conn.execution_client is None
    assert BarSpecification.from_str(case.bar).interval in conn.capabilities_for(
        conn.venues.get(InstrumentId.from_str(case.instrument).venue)).intervals
