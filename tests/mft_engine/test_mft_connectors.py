"""Connector behaviour beyond the shared contract: errors, quotas, history limits, daily bars,
the registry, and the fetch command line."""
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import midfreq.data.service as service
from midfreq.data.adapters.base import ConnectorError, QuotaExceededError, RateLimitError, UnsupportedVenueError
from midfreq.data.adapters.binance.connector import BinanceConnector, HttpResponse
from midfreq.data.adapters.ccxt import connector as ccxt_module
from midfreq.data.adapters.ccxt.connector import CcxtConnector, ccxt_available
from midfreq.data.adapters.ibkr.connector import IBKRConnector
from midfreq.data.adapters.moomoo.connector import MoomooConnector
from midfreq.data.adapters.registry import ConnectorRegistry
from midfreq.data.adapters.yfinance.connector import YFinanceConnector
from midfreq.data.__main__ import main as cli_main
from midfreq.core.model import BarType
from midfreq.data.persistence import ParquetDataCatalog

from mft_fakes import (
    BINANCE_BTCUSDT_INFO, HK_HOURS, FakeBinanceTransport, FakeCcxtExchange, FakeIB, FakeIBProvider,
    FakeMoomooContext, FakeMoomooProvider, FakeYFinanceProvider, RecordingSleep, ccxt_market,
    crypto_hourly, equity_daily, equity_intraday, now_ns_at, utc,
)

SRC = Path(__file__).resolve().parents[2] / "src"
QUIET = {"min_request_interval": 0.0}
NOW = now_ns_at("2024-06-01")
HK_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04"]


class ErrorContext(FakeMoomooContext):
    def __init__(self, message):
        super().__init__([], "Asia/Hong_Kong")
        self.message = message

    def request_history_kline(self, *a, **k):
        self.calls.append(k)
        return -1, self.message, None


class TestMoomoo:
    def test_quota_exhaustion_is_not_retried(self):
        sleep = RecordingSleep()
        ctx = ErrorContext("Insufficient historical candlestick quota")
        conn = MoomooConnector(QUIET, provider=FakeMoomooProvider(ctx), sleep=sleep, now_ns=NOW)
        with pytest.raises(QuotaExceededError):
            conn.data_client.request_bars(BarType.of("00700.XHKG", "1-HOUR"), "2024-01-02", "2024-01-03")
        assert sleep.calls == [] and len(ctx.calls) == 1

    def test_other_errors_raise_instead_of_returning_partial_history(self):
        conn = MoomooConnector(QUIET, provider=FakeMoomooProvider(ErrorContext("unknown stock")), now_ns=NOW)
        with pytest.raises(ConnectorError, match="unknown stock"):
            conn.data_client.request_bars(BarType.of("00700.XHKG", "1-HOUR"), "2024-01-02", "2024-01-03")

    def test_rate_limit_retries_run_out(self):
        sleep = RecordingSleep()
        ctx = ErrorContext("Request too frequent")
        conn = MoomooConnector({**QUIET, "max_retries": 2}, provider=FakeMoomooProvider(ctx), sleep=sleep, now_ns=NOW)
        with pytest.raises(RateLimitError):
            conn.data_client.request_bars(BarType.of("00700.XHKG", "1-HOUR"), "2024-01-02", "2024-01-03")
        assert sleep.calls == [1.0, 2.0]                     # exponential backoff

    def test_daily_bars_close_at_the_session_close(self):
        truth = equity_daily("Asia/Hong_Kong", HK_DAYS, "16:00")
        ctx = FakeMoomooContext(truth, "Asia/Hong_Kong", daily=True)
        conn = MoomooConnector(QUIET, provider=FakeMoomooProvider(ctx), now_ns=NOW)
        bars = conn.data_client.request_bars(BarType.of("00700.XHKG", "1-DAY"), "2024-01-01", "2024-01-05")
        assert [str(b.timestamp) for b in bars] == [f"{d} 08:00:00+00:00" for d in HK_DAYS]

    def test_load_all_lists_the_venues_stocks(self):
        ctx = FakeMoomooContext([], "Asia/Hong_Kong", lot_sizes={"HK.00700": 100, "HK.00005": 400, "US.AAPL": 1})
        conn = MoomooConnector(QUIET, provider=FakeMoomooProvider(ctx))
        got = {str(i.id): i.lot_size for i in conn.instrument_provider.load_all("XHKG")}
        assert got == {"00700.XHKG": 100.0, "00005.XHKG": 400.0}

    def test_unknown_lot_size_falls_back_to_the_venue_default(self):
        conn = MoomooConnector(QUIET, provider=FakeMoomooProvider(FakeMoomooContext([], "Asia/Hong_Kong")))
        (inst,) = conn.instrument_provider.load(["09988.XHKG"])
        assert inst.lot_size == 100.0 and inst.info["lot_size_source"] == "venue default"

    def test_close_closes_the_provider(self):
        provider = FakeMoomooProvider(FakeMoomooContext([], "Asia/Hong_Kong"))
        with MoomooConnector(QUIET, provider=provider) as conn:
            assert conn.is_connected
        assert provider.closed


class TestYFinance:
    def test_start_beyond_the_history_limit_is_moved_forward(self, caplog):
        truth = crypto_hourly("2024-05-30", 10)       # any open-stamped bars will do
        fake = FakeYFinanceProvider(truth)
        conn = YFinanceConnector(QUIET, provider=fake, now_ns=NOW)
        with caplog.at_level(logging.WARNING):
            conn.data_client.request_bars(BarType.of("AAPL.XNAS", "1-MINUTE"), "2024-01-01", "2024-06-01")
        assert "only serves" in caplog.text
        assert min(c[2] for c in fake.calls) >= pd.Timestamp("2024-05-01", tz="UTC")

    def test_daily_bars_from_naive_dates(self):
        truth = equity_daily("America/New_York", ["2024-03-08", "2024-03-11"], "16:00")
        conn = YFinanceConnector(QUIET, provider=FakeYFinanceProvider(truth, daily=True), now_ns=NOW)
        bars = conn.data_client.request_bars(BarType.of("AAPL.XNAS", "1-DAY"), "2024-03-01", "2024-03-12")
        assert [str(b.timestamp) for b in bars] == ["2024-03-08 21:00:00+00:00", "2024-03-11 20:00:00+00:00"]


class TestIBKR:
    def test_daily_bars_are_dates(self):
        truth = equity_daily("Asia/Hong_Kong", HK_DAYS, "16:00")
        conn = IBKRConnector(QUIET, provider=FakeIBProvider(FakeIB(truth, daily=True)), now_ns=NOW)
        bars = conn.data_client.request_bars(BarType.of("00700.XHKG", "1-DAY"), "2024-01-01", "2024-01-05")
        assert [str(b.timestamp) for b in bars] == [f"{d} 08:00:00+00:00" for d in HK_DAYS]

    def test_contract_follows_the_venue(self):
        provider = FakeIBProvider(FakeIB(equity_intraday("Asia/Hong_Kong", HK_DAYS[:1], HK_HOURS)))
        conn = IBKRConnector(QUIET, provider=provider, now_ns=NOW)
        conn.data_client.request_bars(BarType.of("00700.XHKG", "1-HOUR"), "2024-01-02", "2024-01-03")
        contract = provider.ib.calls[0]["contract"]
        assert (contract.symbol, contract.exchange, contract.currency) == ("700", "SEHK", "HKD")

    def test_from_native_needs_a_venue(self):
        with pytest.raises(ValueError):
            IBKRConnector(QUIET).from_native("700")


class TestBinance:
    def _conn(self, transport, **cfg):
        return BinanceConnector({**QUIET, **cfg}, transport=transport, sleep=RecordingSleep(), now_ns=NOW)

    def test_ip_ban_418_honours_retry_after(self):
        class Banned(FakeBinanceTransport):
            def get(self, url, params):
                if not self.calls:
                    self.calls.append(url)
                    return HttpResponse(418, {"code": -1003}, {"Retry-After": "7"})
                return super().get(url, params)
        conn = self._conn(Banned(crypto_hourly("2024-01-01", 3), 3_600_000))
        conn.data_client.request_bars(BarType.of("BTCUSDT.BINANCE", "1-HOUR"), utc("2024-01-01 01:00"),
                                      utc("2024-01-01 03:00"))
        assert conn.retry.sleep.calls == [7.0]

    def test_client_errors_are_not_retried(self):
        class Bad(FakeBinanceTransport):
            def get(self, url, params):
                self.calls.append(url)
                return HttpResponse(400, {"code": -1121, "msg": "Invalid symbol."}, {})
        bad = Bad([], 3_600_000)
        conn = self._conn(bad)
        with pytest.raises(ConnectorError, match="Invalid symbol"):
            conn.data_client.request_bars(BarType.of("NOPE.BINANCE", "1-HOUR"), "2024-01-01", "2024-01-02")
        assert len(bad.calls) == 1

    def test_exchange_info_request_format(self):
        fake = FakeBinanceTransport([], 3_600_000, symbols_info=[BINANCE_BTCUSDT_INFO])
        self._conn(fake).instrument_provider.load(["BTCUSDT.BINANCE"])
        url, params = fake.calls[0]
        assert url == "https://api.binance.com/api/v3/exchangeInfo" and params == {"symbols": '["BTCUSDT"]'}

    def test_unclosed_bar_is_dropped(self):
        truth = crypto_hourly("2024-05-31 22:00", 3)              # last one closes 2024-06-01 01:00
        conn = self._conn(FakeBinanceTransport(truth, 3_600_000))
        bars = conn.data_client.request_bars(BarType.of("BTCUSDT.BINANCE", "1-HOUR"), "2024-05-31 23:00")
        assert [str(b.timestamp) for b in bars] == ["2024-05-31 23:00:00+00:00", "2024-06-01 00:00:00+00:00"]


@pytest.mark.skipif(not ccxt_available(), reason="ccxt not installed")
class TestCcxt:
    def test_native_id_symbology(self):
        ex = FakeCcxtExchange([], markets={"BTC/USDT": ccxt_market("BTC", "USDT", "BTCUSDT")})
        conn = CcxtConnector(QUIET, exchanges={"BINANCE": ex})
        assert conn.to_native("BTCUSDT.BINANCE") == "BTC/USDT"
        assert str(conn.from_native("BTC/USDT", "BINANCE")) == "BTCUSDT.BINANCE"

    def test_history_cap_is_logged(self, caplog):
        ex = FakeCcxtExchange(crypto_hourly("2024-01-02", 5))       # nothing before 2 Jan
        conn = CcxtConnector(QUIET, exchanges={"KRAKEN": ex}, now_ns=NOW)
        with caplog.at_level(logging.WARNING):
            bars = conn.data_client.request_bars(BarType.of("BTC-USD.KRAKEN", "1-HOUR"), "2024-01-01", "2024-01-03")
        assert len(bars) == 5 and "does not serve older history" in caplog.text

    def test_missing_ccxt_gives_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(ccxt_module, "ccxt", None)
        with pytest.raises(ConnectorError, match="pip install ccxt"):
            CcxtConnector(QUIET).exchange("KRAKEN")

    def test_real_exchange_object_is_built_offline(self):
        ex = CcxtConnector(QUIET).exchange("KRAKEN")
        assert type(ex).__name__ == "kraken" and "1h" in ex.timeframes


class TestRegistry:
    def test_default_connector_per_venue(self):
        reg = ConnectorRegistry()
        assert reg.default_for("XHKG") == "moomoo" and reg.default_for("XNAS") == "moomoo"
        assert reg.default_for("BINANCE") == "binance" and reg.default_for("KRAKEN") == "ccxt"

    def test_override_alias_and_errors(self):
        reg = ConnectorRegistry()
        assert isinstance(reg.for_venue("XNAS", "yahoo"), YFinanceConnector)
        assert isinstance(reg.for_venue("XHKG", "ib"), IBKRConnector)
        with pytest.raises(UnsupportedVenueError):
            reg.for_venue("XHKG", "binance")
        with pytest.raises(KeyError):
            reg.create("bloomberg")

    def test_custom_connector_can_be_registered(self):
        reg = ConnectorRegistry(builtins=False)
        reg.register("moomoo", lambda config=None, **kw: MoomooConnector(config, **kw))
        assert reg.names() == ["moomoo"]
        assert isinstance(reg.for_venue("XHKG"), MoomooConnector)

    def test_importing_mft_does_not_import_vendor_sdks(self):
        code = ("import sys, mft, midfreq.data.adapters, midfreq.data.adapters.registry, midfreq.data, midfreq.data.persistence; "
                "reg = midfreq.data.adapters.registry.default_registry(); reg.names(); "
                "print(sorted(m for m in ('moomoo', 'ib_insync', 'ccxt', 'yfinance') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "[]"


@pytest.fixture
def fake_registry(monkeypatch):
    """The default registry with Moomoo and Binance backed by fakes."""
    hk = equity_intraday("Asia/Hong_Kong", HK_DAYS, HK_HOURS)
    ctx = FakeMoomooContext(hk, "Asia/Hong_Kong", lot_sizes={"HK.00700": 100, "HK.00005": 400})
    btc = crypto_hourly("2024-01-01", 24)
    binance = FakeBinanceTransport(btc, 3_600_000, symbols_info=[BINANCE_BTCUSDT_INFO])
    reg = ConnectorRegistry()
    reg.register("moomoo", lambda config=None, **kw: MoomooConnector(
        {**QUIET, "max_count": 5}, provider=FakeMoomooProvider(ctx), now_ns=NOW, **kw))
    reg.register("binance", lambda config=None, **kw: BinanceConnector(
        QUIET, transport=binance, now_ns=NOW, **kw))
    monkeypatch.setattr(service, "default_registry", lambda: reg)
    return reg


class TestFetch:
    def test_fetch_into_catalog(self, fake_registry, tmp_path):
        results = service.fetch_bars("XHKG", ["700", "00005"], "1-HOUR", "2024-01-02", "2024-01-03",
                                     catalog=tmp_path / "cat")
        assert [str(r.instrument_id) for r in results] == ["00700.XHKG", "00005.XHKG"]
        assert results[0].bars == 12 and results[0].native_symbol == "HK.00700"
        cat = ParquetDataCatalog(tmp_path / "cat")
        assert len(cat.bars("00700.XHKG-1-HOUR-LAST-EXTERNAL")) == 12      # both days: --end is inclusive
        assert cat.instrument("00005.XHKG").lot_size == 400

    def test_cli_fetch_crypto(self, fake_registry, tmp_path, capsys):
        assert cli_main(["fetch", "--venue", "BINANCE", "--symbols", "btcusdt", "--bar", "1-HOUR",
                         "--start", "2024-01-01", "--end", "2024-01-01", "--catalog", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL: 23 bars" in out
        assert ParquetDataCatalog(tmp_path).instrument("BTCUSDT.BINANCE").min_notional == 5.0

    def test_cli_dry_run_and_connector_override(self, tmp_path, capsys):
        assert cli_main(["fetch", "--venue", "XHKG", "--symbols", "700", "--bar", "1-DAY",
                         "--start", "2024-01-01", "--client", "yfinance", "--dry-run",
                         "--catalog", str(tmp_path / "c")]) == 0
        assert "native symbol 0700.HK" in capsys.readouterr().out
        assert not (tmp_path / "c").exists()

    def test_cli_rejects_an_unsupported_interval(self, tmp_path):
        from midfreq.data.adapters.base import UnsupportedIntervalError
        with pytest.raises(UnsupportedIntervalError):
            cli_main(["fetch", "--venue", "XHKG", "--symbols", "700", "--bar", "4-HOUR",
                      "--start", "2024-01-01", "--dry-run"])

    def test_cli_venues_and_catalog(self, capsys, tmp_path):
        assert cli_main(["venues"]) == 0
        out = capsys.readouterr().out
        assert "XHKG" in out and "0.1600%" in out and "BINANCE" in out
        assert cli_main(["catalog", "--catalog", str(tmp_path / "none")]) == 0
        assert "empty" in capsys.readouterr().out

    def test_python_dash_m_entry_point(self):
        out = subprocess.run([sys.executable, "-m", "midfreq.data", "fetch", "--venue", "KRAKEN", "--symbols",
                              "BTC-USD", "--start", "2024-01-01", "--dry-run"],
                             cwd=SRC, capture_output=True, text=True)
        if not ccxt_available():
            pytest.skip("ccxt not installed")
        assert out.returncode == 0, out.stderr
        assert "BTC-USD.KRAKEN-1-HOUR-LAST-EXTERNAL" in out.stdout and "BTC/USD" in out.stdout
