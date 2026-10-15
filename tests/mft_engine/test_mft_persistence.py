"""ParquetDataCatalog, the read-only LegacyDataReader, and the copy-only migration."""
from datetime import datetime

import pandas as pd
import pytest

from midfreq.data.storage import DataStorage
from midfreq.core.model import Bar, BarSpecification, BarType, CurrencyPair, Equity, InstrumentId
from midfreq.data.persistence import LegacyDataReader, ParquetDataCatalog, migrate_legacy

HOUR = BarSpecification.from_str("1-HOUR")
DAY = BarSpecification.from_str("1-DAY")


def _bars(bt, start, n, step=pd.Timedelta(hours=1), base=100.0):
    t0 = pd.Timestamp(start, tz="UTC")
    return [Bar(bt, base + i, base + i + 1, base + i - 1, base + i + 0.5, 10.0 * i,
                (t0 + i * step).value, (t0 + i * step).value) for i in range(n)]


class TestCatalog:
    def test_write_read_round_trip(self, tmp_path):
        cat = ParquetDataCatalog(tmp_path / "catalog")
        bt = BarType.from_str("00700.XHKG-1-HOUR-LAST-EXTERNAL")
        bars = _bars(bt, "2024-01-02 02:30", 5)
        assert cat.write_bars(bars) == {str(bt): 5}
        assert cat.bars(bt) == bars
        files = list((tmp_path / "catalog" / "data" / "bar" / str(bt)).glob("*.parquet"))
        assert len(files) == 1 and files[0].name == f"{bars[0].ts_event}-{bars[-1].ts_event}.parquet"
        assert (tmp_path / "catalog" / ".gitignore").read_text().strip().endswith("*")

    def test_merge_replaces_overlap_and_keeps_one_file(self, tmp_path):
        cat = ParquetDataCatalog(tmp_path)
        bt = BarType.from_str("BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL")
        cat.write_bars(_bars(bt, "2024-01-01 01:00", 5))
        revised = _bars(bt, "2024-01-01 04:00", 4, base=500.0)       # overlaps the last two
        assert cat.write_bars(revised) == {str(bt): 7}
        got = cat.bars(bt)
        assert [b.ts_event for b in got] == sorted({b.ts_event for b in got})
        assert got[3].open == 500.0                                  # the re-fetched bar won
        assert len(list(cat.bar_dir(bt).glob("*.parquet"))) == 1

    def test_time_filter_and_bar_type_listing(self, tmp_path):
        cat = ParquetDataCatalog(tmp_path)
        hk = BarType.from_str("00700.XHKG-1-HOUR-LAST-EXTERNAL")
        us = BarType.from_str("AAPL.XNAS-1-DAY-LAST-EXTERNAL")
        cat.write_bars(_bars(hk, "2024-01-02 02:30", 6) + _bars(us, "2024-01-02 21:00", 3, pd.Timedelta(days=1)))
        assert set(map(str, cat.bar_types())) == {str(hk), str(us)}
        assert cat.bar_types("AAPL.XNAS") == [us]
        part = cat.bars(hk, start="2024-01-02 04:30", end="2024-01-02 06:30")
        assert [b.timestamp.strftime("%H:%M") for b in part] == ["04:30", "05:30", "06:30"]

    def test_instruments_round_trip(self, tmp_path):
        cat = ParquetDataCatalog(tmp_path)
        eq = Equity(id=InstrumentId.from_str("00005.XHKG"), raw_symbol="00005", currency="HKD", lot_size=400)
        cp = CurrencyPair(id=InstrumentId.from_str("BTCUSDT.BINANCE"), raw_symbol="BTCUSDT", currency="USDT",
                          base_currency="BTC", quote_currency="USDT", size_increment=1e-5, min_notional=5.0)
        assert cat.write_instruments([eq, cp]) == 2
        assert cat.instrument("00005.XHKG") == eq
        assert cat.instruments("BINANCE") == [cp]

    def test_empty_catalog(self, tmp_path):
        cat = ParquetDataCatalog(tmp_path / "nothing")
        assert cat.bar_types() == [] and cat.instruments() == []
        assert cat.bars("00700.XHKG-1-HOUR-LAST-EXTERNAL") == []
        assert not (tmp_path / "nothing").exists()


def _write_legacy(storage, symbol, stamps, closes):
    idx = pd.DatetimeIndex(stamps, tz="UTC", name="timestamp")
    df = pd.DataFrame({"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                       "close": closes, "volume": [1000.0] * len(closes)}, index=idx)
    storage.save_data(df, symbol.replace(".", "_"), "1h" if " " in stamps[0] else "1d")


@pytest.fixture
def legacy_dir(tmp_path):
    """A small cache in the original layout, written with core's own DataStorage."""
    d = tmp_path / "data"
    st = DataStorage(str(d))
    # Moomoo time_keys: exchange-local bar CLOSE times, stored as if they were UTC.
    _write_legacy(st, "HK.00700", ["2024-01-02 10:30", "2024-01-02 11:30", "2024-01-02 12:00",
                                   "2024-01-02 16:00"], [300.0, 301.0, 302.0, 303.0])
    _write_legacy(st, "US.AAPL", ["2024-01-02 10:30", "2024-01-02 16:00"], [190.0, 191.0])
    _write_legacy(st, "US.JPM", ["2024-07-01 10:30"], [200.0])
    _write_legacy(st, "HK.00700", ["2024-01-02", "2024-01-03"], [303.0, 305.0])
    (d / "AAPL").mkdir()                    # an old yfinance-era folder: skipped
    return d


def _snapshot(path):
    return {(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in path.rglob("*")}


class TestLegacyReader:
    def test_entries_and_symbol_mapping(self, legacy_dir):
        r = LegacyDataReader(legacy_dir)
        entries = {e.folder: e for e in r.entries()}
        assert set(entries) == {"HK_00700", "US_AAPL", "US_JPM"}
        assert str(entries["HK_00700"].instrument_id) == "00700.XHKG"
        assert entries["HK_00700"].timeframes == ("1d", "1h")
        assert str(entries["US_AAPL"].instrument_id) == "AAPL.XNAS"
        assert str(entries["US_JPM"].instrument_id) == "JPM.XNYS"
        assert r.legacy_symbol("00700.XHKG") == "HK.00700"

    def test_local_times_are_relocalised_to_true_utc(self, legacy_dir):
        r = LegacyDataReader(legacy_dir)
        hk = r.read_frame("00700.XHKG", HOUR)
        assert list(hk.index.strftime("%H:%M")) == ["02:30", "03:30", "04:00", "08:00"]
        us = r.read_frame("AAPL.XNAS", HOUR)
        assert list(us.index.strftime("%H:%M")) == ["15:30", "21:00"]          # EST in January
        assert list(r.read_frame("JPM.XNYS", HOUR).index.strftime("%H:%M")) == ["14:30"]   # EDT in July
        daily = r.read_frame("00700.XHKG", DAY)
        assert [str(t) for t in daily.index] == ["2024-01-02 08:00:00+00:00", "2024-01-03 08:00:00+00:00"]

    def test_multi_venue_order_is_correct_only_after_relocalising(self, legacy_dir):
        r = LegacyDataReader(legacy_dir)
        raw_hk, raw_us = r.read_raw("00700.XHKG", HOUR), r.read_raw("AAPL.XNAS", HOUR)
        # As stored, the US 10:30 bar sorts before the HK 11:30 bar, although HK closed hours earlier.
        assert raw_us.index[0] < raw_hk.index[1]
        hk, us = r.read_frame("00700.XHKG", HOUR), r.read_frame("AAPL.XNAS", HOUR)
        assert hk.index.max() < us.index.min()                                 # all of HK's day first

    def test_bars_carry_the_bar_type(self, legacy_dir):
        bars = LegacyDataReader(legacy_dir).read_bars("00700.XHKG", HOUR)
        assert str(bars[0].bar_type) == "00700.XHKG-1-HOUR-LAST-EXTERNAL"
        assert bars[0].close == 300.0

    def test_date_filter_matches_the_backtester(self, legacy_dir):
        r = LegacyDataReader(legacy_dir)
        df = r.read_raw("00700.XHKG", HOUR, start=datetime(2024, 1, 2, 11), end=datetime(2024, 1, 2))
        assert list(df.index.strftime("%H:%M")) == ["11:30", "12:00", "16:00"]
        df = r.read_raw("00700.XHKG", HOUR, end=datetime(2024, 1, 2), end_inclusive=False)
        assert df.empty

    def test_reader_never_writes(self, legacy_dir):
        before = _snapshot(legacy_dir)
        r = LegacyDataReader(legacy_dir)
        r.entries()
        r.read_bars("00700.XHKG", HOUR)
        r.read_frame("AAPL.XNAS", HOUR)
        r.read_frame("09988.XHKG", HOUR)                                       # missing: no folder made
        assert _snapshot(legacy_dir) == before
        assert not (legacy_dir / "HK_09988").exists()


class TestMigration:
    def test_copy_only(self, legacy_dir, tmp_path):
        before = _snapshot(legacy_dir)
        rows = migrate_legacy(legacy_dir, tmp_path / "catalog")
        assert _snapshot(legacy_dir) == before
        assert {(r.folder, r.timeframe, r.rows) for r in rows} == {
            ("HK_00700", "1d", 2), ("HK_00700", "1h", 4), ("US_AAPL", "1h", 2), ("US_JPM", "1h", 1)}
        cat = ParquetDataCatalog(tmp_path / "catalog")
        hk = cat.bars("00700.XHKG-1-HOUR-LAST-EXTERNAL")
        assert [b.timestamp.strftime("%H:%M") for b in hk] == ["02:30", "03:30", "04:00", "08:00"]
        assert cat.instrument("00700.XHKG").currency == "HKD"

    def test_dry_run_writes_nothing(self, legacy_dir, tmp_path):
        rows = migrate_legacy(legacy_dir, tmp_path / "catalog", timeframes=["1h"], folders=["HK.00700"])
        assert [(r.folder, r.timeframe) for r in rows] == [("HK_00700", "1h")]
        dry = migrate_legacy(legacy_dir, tmp_path / "dry", dry_run=True)
        assert len(dry) == 4 and not (tmp_path / "dry").exists()

    def test_refuses_a_catalog_inside_the_legacy_directory(self, legacy_dir):
        with pytest.raises(ValueError):
            migrate_legacy(legacy_dir, legacy_dir / "catalog")
        with pytest.raises(ValueError):
            migrate_legacy(legacy_dir, legacy_dir)
