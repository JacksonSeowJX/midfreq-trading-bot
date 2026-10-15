"""Reference data: strategies, universes, cached candles, and the service's health."""
from dataclasses import replace

import pandas as pd
import pytest

from midfreq.api.settings import get_settings
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY


def test_strategies_carry_the_registrys_parameters(client):
    rows = client.get("/api/strategies").json()
    assert [r["name"] for r in rows] == list(STRATEGY_REGISTRY)
    rsi = next(r for r in rows if r["name"] == "RSI")
    assert rsi["params"][0] == {"name": "rsi_period", "label": "RSI Period", "default": 14,
                                "min": 2.0, "max": 50.0, "step": 1.0}
    bb = next(r for r in rows if r["name"] == "Bollinger Bands")
    assert next(p for p in bb["params"] if p["name"] == "num_std")["default"] == 2.0


def test_universes_from_symbols_json_with_the_dashboards_labels(client):
    assert client.get("/api/universes").json() == [
        {"name": "hk_live", "label": "HK — live roster (19)", "market": "HK", "symbols": 2,
         "description": "HK live roster"},
        {"name": "sp100", "label": "US — S&P 100 (100)", "market": "US", "symbols": 3, "description": "S&P 100"},
        {"name": "tiny", "label": "tiny", "market": None, "symbols": 1, "description": ""},
    ]


def _times(rows):
    return [pd.Timestamp(r["t"]) for r in rows]


def test_candles_are_the_most_recent_within_the_limit(client):
    rows = client.get("/api/candles", params={"symbol": "US.AAA"}).json()
    assert len(rows) == 10
    assert rows[0] == {"t": "2026-10-01T10:30:00Z", "o": 0.0, "h": 1.0, "l": 0.0, "c": 0.0, "v": 100.0}
    last3 = client.get("/api/candles", params={"symbol": "US.AAA", "limit": 3}).json()
    assert _times(last3) == list(pd.date_range("2026-10-01 17:30", periods=3, freq="h", tz="UTC"))


def test_candles_filter_by_start_and_end(client):
    def n(**params):
        return len(client.get("/api/candles", params={"symbol": "US.AAA", **params}).json())
    assert n(start="2026-10-01T15:00") == 5
    assert n(end="2026-10-01 12:30") == 3
    assert n(end="2026-10-01") == 10                  # a date alone includes the whole day
    assert n(end="2026-09-30") == 0
    assert n(start="2026-10-01T19:30:00+00:00", end="2026-10-01T19:30:00Z") == 1


def test_candles_without_a_zone_are_utc_and_missing_volume_is_zero(client):
    rows = client.get("/api/candles", params={"symbol": "HK.00700", "timeframe": "1d"}).json()
    assert [r["t"] for r in rows] == ["2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"]
    assert [r["v"] for r in rows] == [5.0, 0.0]


def test_missing_candles_are_404_and_create_nothing(client, settings):
    before = sorted(p.name for p in settings.data_dir.iterdir())
    assert client.get("/api/candles", params={"symbol": "US.ZZZ"}).status_code == 404
    assert client.get("/api/candles", params={"symbol": "US.AAA", "timeframe": "1d"}).status_code == 404
    assert sorted(p.name for p in settings.data_dir.iterdir()) == before


@pytest.mark.parametrize("params", [{"symbol": "../US_AAA"}, {"symbol": "a/b"}, {"symbol": "bad symbol"},
                                    {"symbol": "US.AAA", "start": "not a date"},
                                    {"symbol": "US.AAA", "timeframe": "2h"},
                                    {"symbol": "US.AAA", "limit": 0}])
def test_bad_candle_requests_are_422(client, params):
    assert client.get("/api/candles", params=params).status_code == 422


def test_health_reports_the_version_and_which_sources_exist(client, app, settings):
    h = client.get("/api/health").json()
    assert h == {"status": "ok", "version": "0.1.0",
                 "sources": {"results": True, "live_sessions": True, "data_cache": True, "accounts": True}}
    app.dependency_overrides[get_settings] = lambda: replace(
        settings, results_dir=settings.results_dir / "gone", live_sessions=str(settings.data_dir / "gone"),
        accounts_enabled=False)
    assert client.get("/api/health").json()["sources"] == {"results": False, "live_sessions": False,
                                                           "data_cache": True, "accounts": False}
