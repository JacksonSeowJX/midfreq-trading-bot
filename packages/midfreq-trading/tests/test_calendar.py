"""VenueCalendar: sessions with Hong Kong's lunch break, holidays and early closes from
config/venues.json, next_open, and the holiday data itself."""
import json
import logging
from datetime import date, time

import pandas as pd
import pytest

from midfreq.core.paths import config_dir
from midfreq.core.venues.spec import VenueRegistry, default_venues
from midfreq.trading.calendar import HolidayData, VenueCalendar, load_calendars, load_holiday_data


def et(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York").tz_convert("UTC")


def hkt(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="Asia/Hong_Kong").tz_convert("UTC")


@pytest.fixture(scope="module")
def cals():
    return load_calendars()


def test_weekends_are_closed(cals):
    for mic in ("XNYS", "XNAS", "XHKG"):
        cal = cals[mic]
        assert not cal.is_trading_day(date(2026, 10, 17)) and not cal.is_trading_day(date(2026, 10, 18))
        assert cal.session(date(2026, 10, 17)) is None and cal.sessions(date(2026, 10, 18)) == []
        assert cal.is_trading_day(date(2026, 10, 16))


def test_us_holidays(cals):
    for mic in ("XNYS", "XNAS"):
        cal = cals[mic]
        for d in ("2026-07-03", "2026-11-26", "2026-12-25", "2026-01-19"):
            assert not cal.is_trading_day(date.fromisoformat(d)), (mic, d)
            assert cal.session(d) is None
    nyse = cals["XNYS"]
    for d in ("2027-03-26", "2027-06-18", "2027-07-05", "2027-12-24"):
        assert not nyse.is_trading_day(date.fromisoformat(d))
    assert nyse.is_trading_day(date(2027, 12, 31))   # New Year's Day 2028 is a Saturday, not observed


def test_hong_kong_holidays(cals):
    hk = cals["XHKG"]
    for d in ("2026-10-19", "2026-12-25", "2027-02-08", "2027-02-09", "2027-09-16", "2027-12-27"):
        assert not hk.is_trading_day(date.fromisoformat(d)), d
    assert hk.is_trading_day(date(2026, 10, 20))


def test_us_early_closes_end_at_13_00_eastern(cals):
    nyse = cals["XNYS"]
    assert nyse.session(date(2026, 11, 27)) == (et("2026-11-27 09:30"), et("2026-11-27 13:00"))
    assert nyse.session(date(2026, 11, 27))[1] == pd.Timestamp("2026-11-27 18:00", tz="UTC")   # EST
    assert nyse.session("2026-12-24") == (et("2026-12-24 09:30"), et("2026-12-24 13:00"))
    assert nyse.early_close("2026-12-24") == time(13, 0) and nyse.early_close("2026-12-23") is None
    assert cals["XNAS"].session("2026-11-27")[1] == et("2026-11-27 13:00")
    assert not nyse.is_open(et("2026-11-27 13:30")) and nyse.is_open(et("2026-11-27 12:59"))


def test_hong_kong_half_days_keep_the_morning_session_only(cals):
    hk = cals["XHKG"]
    assert hk.sessions("2026-12-24") == [(hkt("2026-12-24 09:30"), hkt("2026-12-24 12:00"))]
    assert hk.session("2027-02-05") == (hkt("2027-02-05 09:30"), hkt("2027-02-05 12:00"))
    assert not hk.is_open(hkt("2026-12-31 13:30"))


def test_hong_kong_lunch_break(cals):
    hk = cals["XHKG"]
    day = date(2026, 10, 16)
    assert hk.sessions(day) == [(pd.Timestamp("2026-10-16 01:30", tz="UTC"), pd.Timestamp("2026-10-16 04:00", tz="UTC")),
                                (pd.Timestamp("2026-10-16 05:00", tz="UTC"), pd.Timestamp("2026-10-16 08:00", tz="UTC"))]
    assert hk.session(day) == (hkt("2026-10-16 09:30"), hkt("2026-10-16 16:00"))
    assert hk.is_open(hkt("2026-10-16 10:00")) and not hk.is_open(hkt("2026-10-16 12:30"))
    assert hk.is_open(hkt("2026-10-16 13:00")) and not hk.is_open(hkt("2026-10-16 16:00"))
    assert hk.next_open(hkt("2026-10-16 12:30")) == hkt("2026-10-16 13:00")
    assert hk.next_close(hkt("2026-10-16 10:00")) == hkt("2026-10-16 12:00")
    assert hk.open_duration(hkt("2026-10-16 11:00"), hkt("2026-10-16 14:00")) == pd.Timedelta(hours=2)


def test_next_open_skips_weekends_holidays_and_follows_daylight_saving(cals):
    hk = cals["XHKG"]
    # Friday after the close -> Tuesday (Monday 19 October is Chung Yeung)
    assert hk.next_open(hkt("2026-10-16 17:00")) == hkt("2026-10-20 09:30")
    nyse = cals["XNYS"]
    assert nyse.next_open(et("2026-11-25 16:30")) == et("2026-11-27 09:30")     # Thanksgiving
    assert nyse.next_open(et("2026-10-16 09:30")) == et("2026-10-19 09:30")     # strictly after
    # US clocks go back on 1 November 2026: 09:30 ET is 13:30 UTC before, 14:30 UTC after
    assert nyse.next_open(pd.Timestamp("2026-10-30 21:00", tz="UTC")) == pd.Timestamp("2026-11-02 14:30", tz="UTC")
    assert nyse.session("2026-10-30")[0] == pd.Timestamp("2026-10-30 13:30", tz="UTC")
    assert nyse.next_open("2026-12-24 18:00") == et("2026-12-28 09:30")          # naive = UTC


def test_open_intervals_merge_and_clip(cals):
    nyse = cals["XNYS"]
    spans = nyse.open_intervals(et("2026-10-16 15:00"), et("2026-10-19 10:00"))
    assert spans == [(et("2026-10-16 15:00"), et("2026-10-16 16:00")), (et("2026-10-19 09:30"), et("2026-10-19 10:00"))]
    assert nyse.open_intervals(et("2026-10-17 10:00"), et("2026-10-17 12:00")) == []


def test_a_24_7_venue_is_always_open(cals):
    okx = cals["OKX"]
    assert okx.is_trading_day(date(2026, 10, 17))
    assert okx.is_open(pd.Timestamp("2026-10-17 03:00", tz="UTC"))
    assert okx.open_duration(pd.Timestamp("2026-10-17 22:00", tz="UTC"),
                             pd.Timestamp("2026-10-18 02:00", tz="UTC")) == pd.Timedelta(hours=4)
    assert len(okx.open_intervals(pd.Timestamp("2026-10-17 22:00", tz="UTC"),
                                  pd.Timestamp("2026-10-19 02:00", tz="UTC"))) == 1


def test_a_year_without_a_holiday_list_warns_once(caplog):
    nasdaq = VenueCalendar.for_venue("XNAS")
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.calendar"):
        assert nasdaq.is_trading_day(date(2027, 11, 25))      # Thanksgiving 2027: not listed for Nasdaq yet
        nasdaq.is_trading_day(date(2027, 11, 26))
    assert len(caplog.records) == 1 and "2027" in caplog.records[0].getMessage()
    assert nasdaq.knows_holidays(2026) and not nasdaq.knows_holidays(2027)
    caplog.clear()
    tokyo = VenueCalendar.for_venue("XTKS")
    with caplog.at_level(logging.WARNING, logger="midfreq.trading.calendar"):
        tokyo.is_trading_day(date(2026, 10, 16))
        tokyo.is_trading_day(date(2027, 10, 15))
    assert len(caplog.records) == 1 and "no holiday list" in caplog.records[0].getMessage()


def test_holiday_data_in_venues_json_is_sound():
    raw = json.loads((config_dir() / "venues.json").read_text())["venues"]
    data = load_holiday_data()
    for mic in ("XNYS", "XNAS", "XHKG"):
        d = data[mic]
        assert d.holidays and d.covered_years and all(year in d.sources for year in d.covered_years)
        for day in d.holidays | set(d.early_closes):
            assert day.weekday() < 5 and day.year in d.covered_years, (mic, day)
        assert all(url.startswith("https://") for urls in d.sources.values() for url in urls)
        assert set(raw[mic]["holiday_sources"]) == {str(y) for y in d.covered_years}
    assert data["XNYS"].covered_years == {2026, 2027} and data["XHKG"].covered_years == {2026, 2027}
    assert data["XNAS"].covered_years == {2026}
    us26 = {d for d in data["XNYS"].holidays if d.year == 2026}
    assert us26 == {d for d in data["XNAS"].holidays if d.year == 2026}
    assert len(us26) == 10 and len([d for d in data["XHKG"].holidays if d.year == 2026]) == 14
    assert len([d for d in data["XHKG"].holidays if d.year == 2027]) == 13
    assert data["OKX"].empty


def test_venue_specs_still_load_with_the_new_fields():
    reg = VenueRegistry.load(config_dir() / "venues.json")
    hk = reg.get("XHKG")
    assert [(s.open.strftime("%H:%M"), s.close.strftime("%H:%M")) for s in hk.sessions] == [("09:30", "12:00"), ("13:00", "16:00")]
    assert reg.get("XNYS").weekdays == (0, 1, 2, 3, 4) and default_venues().get("XNAS").currency == "USD"


def test_bad_holiday_data_is_refused():
    with pytest.raises(ValueError, match="both as a holiday and as an early close"):
        HolidayData.from_dict("XNYS", {"holidays": ["2026-12-24"], "early_closes": {"2026-12-24": "13:00"}})
    with pytest.raises(ValueError, match="XNYS"):
        HolidayData.from_dict("XNYS", {"holidays": ["24/12/2026"]})


def test_calendars_from_another_file(tmp_path):
    raw = json.loads((config_dir() / "venues.json").read_text())
    raw["venues"]["XNYS"]["holidays"] = ["2026-10-16"]
    raw["venues"]["XNYS"]["early_closes"] = {}
    raw["venues"]["XNYS"]["holiday_sources"] = {"2026": "https://example.org"}
    path = tmp_path / "venues.json"
    path.write_text(json.dumps(raw))
    cal = VenueCalendar.for_venue("XNYS", path=path)
    assert not cal.is_trading_day(date(2026, 10, 16)) and cal.is_trading_day(date(2026, 11, 26))
