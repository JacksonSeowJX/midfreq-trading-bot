"""config/venues.json and VenueSpec time arithmetic."""
import json
from pathlib import Path

import pandas as pd
import pytest

from midfreq.core.model import BarSpecification, LiquiditySide, MakerTakerFeeModel, PercentFeeModel
from midfreq.core.venues import VenueRegistry, default_venues

HOUR = BarSpecification.from_str("1-HOUR")
DAY = BarSpecification.from_str("1-DAY")


@pytest.fixture(scope="module")
def venues():
    return default_venues()


def test_config_lists_equity_and_crypto_venues(venues):
    names = {str(v) for v in venues.venues()}
    assert {"XHKG", "XNAS", "XNYS", "XSES", "XSHG", "XSHE", "XTKS"} <= names
    assert {"BINANCE", "COINBASE", "KRAKEN", "OKX"} <= names
    for spec in venues:
        assert spec.default_connector in spec.connectors, spec.venue


def test_every_venue_json_entry_parses():
    raw = json.loads((Path(__file__).resolve().parents[2] / "config" / "venues.json").read_text())
    assert set(raw["venues"]) == {str(v) for v in default_venues().venues()}


def test_hk_sessions_include_the_lunch_break(venues):
    hk = venues.get("XHKG")
    assert [(s.open.strftime("%H:%M"), s.close.strftime("%H:%M")) for s in hk.sessions] == \
        [("09:30", "12:00"), ("13:00", "16:00")]
    assert hk.currency == "HKD" and hk.timezone == "Asia/Hong_Kong"


def test_fee_models(venues):
    assert venues.get("XHKG").fee_model() == PercentFeeModel(0.0016)
    assert venues.get("XNAS").fee_model() == PercentFeeModel(0.00005)
    assert venues.get("XNYS").fee_model() == PercentFeeModel(0.00005)
    binance = venues.get("BINANCE").fee_model()
    assert isinstance(binance, MakerTakerFeeModel) and binance.rate(LiquiditySide.TAKER) == 0.001
    assert venues.get("XHKG").fees_calibrated and not venues.get("XTKS").fees_calibrated


def test_crypto_venues_trade_24_7_in_utc(venues):
    for mic, quote in [("BINANCE", "USDT"), ("COINBASE", "USD"), ("KRAKEN", "USD"), ("OKX", "USDT")]:
        spec = venues.get(mic)
        assert spec.is_24_7 and spec.timezone == "UTC" and spec.currency == quote and spec.is_crypto


def test_moomoo_local_close_times_become_utc(venues):
    stored = pd.DatetimeIndex(["2024-01-02 10:30", "2024-01-02 12:00", "2024-01-02 16:00"], tz="UTC")
    assert list(venues.get("XHKG").localize(stored).strftime("%H:%M")) == ["02:30", "04:00", "08:00"]
    us = venues.get("XNAS").localize(pd.DatetimeIndex(["2024-03-08 10:30", "2024-03-11 10:30"]))
    assert list(us.strftime("%m-%d %H:%M")) == ["03-08 15:30", "03-11 14:30"]   # EST then EDT


def test_bar_open_to_close_respects_sessions(venues):
    hk = venues.get("XHKG")
    opens = pd.DatetimeIndex(["2024-01-02 01:30", "2024-01-02 03:30", "2024-01-02 07:00"], tz="UTC")
    assert list(hk.bar_close_from_open(opens, HOUR).strftime("%H:%M")) == ["02:30", "04:00", "08:00"]
    us = venues.get("XNYS")
    last = pd.DatetimeIndex(["2024-03-08 20:30", "2024-03-11 19:30"], tz="UTC")       # 15:30 local
    assert list(us.bar_close_from_open(last, HOUR).strftime("%m-%d %H:%M")) == ["03-08 21:00", "03-11 20:00"]


def test_daily_close_is_the_session_close(venues):
    assert str(venues.get("XHKG").daily_close(pd.DatetimeIndex(["2024-01-02"]))[0]) == "2024-01-02 08:00:00+00:00"
    assert str(venues.get("XNAS").daily_close(pd.DatetimeIndex(["2024-07-01"]))[0]) == "2024-07-01 20:00:00+00:00"
    assert str(venues.get("BINANCE").daily_close(pd.DatetimeIndex(["2024-07-01"]))[0]) == "2024-07-02 00:00:00+00:00"


@pytest.mark.parametrize("mic, spec, stored", [
    ("XHKG", HOUR, ["2024-01-02 10:30", "2024-01-02 12:00", "2024-01-02 16:00"]),
    ("XNAS", HOUR, ["2024-03-08 16:00", "2024-03-11 09:30"]),
    ("XHKG", DAY, ["2024-01-02", "2024-01-03"]),
    ("XNYS", DAY, ["2024-03-08", "2024-03-11"]),
])
def test_legacy_index_is_the_exact_inverse(venues, mic, spec, stored):
    v = venues.get(mic)
    legacy = pd.DatetimeIndex(stored, tz="UTC")
    true_utc = v.localize(legacy) if spec.is_intraday else v.daily_close(legacy.tz_localize(None))
    assert v.to_legacy_index(true_utc.asi8, spec).equals(legacy)


def test_listing_table_places_us_symbols(venues):
    assert str(venues.venue_for_market("moomoo", "US", "AAPL")) == "XNAS"
    assert str(venues.venue_for_market("moomoo", "US", "JPM")) == "XNYS"
    assert str(venues.venue_for_market("moomoo", "HK", "00700")) == "XHKG"


def test_registry_loads_an_alternative_file(tmp_path):
    cfg = {"venues": {"XTST": {"timezone": "UTC", "currency": "USD", "sessions": [["00:00", "23:59"]],
                               "fees": {"model": "percent", "rate": 0.001}, "default_connector": "x",
                               "connectors": {"x": {}}}}}
    p = tmp_path / "v.json"
    p.write_text(json.dumps(cfg))
    reg = VenueRegistry.load(p)
    assert reg.get("XTST").fee_model() == PercentFeeModel(0.001)
    with pytest.raises(KeyError):
        reg.get("XHKG")
