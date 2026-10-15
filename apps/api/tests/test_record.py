"""The bot's record: the forward test and the recorded sessions, read through a RecordStore."""
import pandas as pd
import pytest

from midfreq.api.store import LocalDirRecordStore, record_store_for

FIRST = "session_20261005_213502_crosssectionalreversal_1001_1"
CRASHED = "session_20261006_213502_crosssectionalreversal_1002_1"
HK = "session_20260713_092423_rsistrategy_81890_1"


def _utc(text):
    return pd.Timestamp(text, tz="UTC")


# ── the store ───────────────────────────────────────────────────────────────

def test_the_local_store_lists_session_and_console_logs_only(repo):
    store = LocalDirRecordStore(repo / "live_sessions")
    assert sorted(store.session_ids()) == sorted([FIRST, CRASHED, HK])
    assert len(store.console_logs()) == 4
    assert '"session_start"' in store.read_session(FIRST)


def test_the_local_store_refuses_anything_it_did_not_list(repo):
    store = LocalDirRecordStore(repo / "live_sessions")
    for bad in ["../config/connections", "state_crosssectionalreversal", f"{FIRST}.jsonl", ""]:
        with pytest.raises(KeyError):
            store.read_session(bad)
    with pytest.raises(KeyError):
        store.read_console_log("../config/connections.yaml")


def test_a_missing_folder_is_an_empty_unavailable_record(tmp_path):
    store = LocalDirRecordStore(tmp_path / "nowhere")
    assert not store.available() and store.session_ids() == [] and store.console_logs() == []


def test_a_gcs_source_is_reserved_not_implemented():
    with pytest.raises(NotImplementedError):
        record_store_for("gs://bucket/live_sessions")


# ── /api/sessions ───────────────────────────────────────────────────────────

def test_sessions_are_listed_newest_first_with_their_summaries(client):
    r = client.get("/api/sessions")
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [CRASHED, FIRST, HK]
    first = rows[1]
    assert (first["market"], first["strategy"], first["timeframe"], first["symbols"]) == \
        ("US", "CrossSectionalReversal", "1h", 3)
    assert (first["candles"], first["trades"], first["crashed"]) == (2, 1, False)
    assert (first["start_total_assets"], first["end_total_assets"]) == (1_000_000.0, 1_000_499.0)


def test_session_times_from_the_vm_clock_are_returned_in_utc(client):
    first = next(x for x in client.get("/api/sessions").json() if x["id"] == FIRST)
    assert pd.Timestamp(first["started_at"]) == _utc("2026-10-05 13:35:02.481894")    # 21:35 SGT
    assert pd.Timestamp(first["ended_at"]) == _utc("2026-10-05 20:01:05.530682")


def test_a_crash_is_read_from_the_sessions_own_console_log(client):
    rows = {x["id"]: x for x in client.get("/api/sessions").json()}
    crashed = rows[CRASHED]
    assert crashed["crashed"] is True and crashed["ended_at"] is None and crashed["end_total_assets"] is None
    assert crashed["trades"] == 2 and crashed["candles"] == 1         # the line cut short is skipped
    # a console log that crashed before its session began is matched to no session
    assert rows[FIRST]["crashed"] is False and rows[HK]["crashed"] is False


def test_early_sessions_count_trades_from_session_end(client):
    hk = next(x for x in client.get("/api/sessions").json() if x["id"] == HK)
    assert (hk["market"], hk["strategy"], hk["trades"], hk["candles"]) == ("HK", "RSIStrategy", 1, 1)


def test_sessions_filter_by_market_and_limit(client):
    assert [x["id"] for x in client.get("/api/sessions", params={"market": "HK"}).json()] == [HK]
    assert [x["id"] for x in client.get("/api/sessions", params={"market": "US"}).json()] == [CRASHED, FIRST]
    assert [x["id"] for x in client.get("/api/sessions", params={"limit": 1}).json()] == [CRASHED]
    assert client.get("/api/sessions", params={"market": "JP"}).status_code == 422


def test_no_record_folder_means_no_sessions(client, app, settings):
    from dataclasses import replace

    from midfreq.api.settings import get_settings
    app.dependency_overrides[get_settings] = lambda: replace(settings, live_sessions=str(settings.data_dir / "x"))
    assert client.get("/api/sessions").json() == []
    assert client.get("/api/forward-test").status_code == 404


# ── /api/sessions/{id} ──────────────────────────────────────────────────────

def test_session_detail_has_the_broker_totals_trades_and_event_counts(client):
    r = client.get(f"/api/sessions/{FIRST}")
    assert r.status_code == 200
    d = r.json()
    assert d["summary"]["id"] == FIRST
    assert [(p["pnl"], p["value"]) for p in d["equity"]] == [(0.0, 1_000_000.0), (499.0, 1_000_499.0)]
    assert pd.Timestamp(d["equity"][0]["timestamp"]) == _utc("2026-10-05 13:35:02.481894")
    assert d["trades"] == [{"timestamp": "2026-10-05T10:30:00Z", "symbol": "US.AAA", "side": "BUY",
                            "qty": 100.0, "price": 50.0}]
    assert d["event_counts"] == {"session_start": 1, "candle_close": 2, "trade": 1, "session_end": 1}


def test_a_crashed_session_detail_still_reads(client):
    d = client.get(f"/api/sessions/{CRASHED}").json()
    assert d["summary"]["crashed"] is True and len(d["equity"]) == 1
    assert [t["side"] for t in d["trades"]] == ["SELL", "BUY"]


@pytest.mark.parametrize("bad", ["nope", "state_crosssectionalreversal", f"{FIRST}.jsonl",
                                 "..%2Fconfig%2Fconnections", "%2E%2E"])
def test_session_ids_outside_the_listing_are_not_found(client, bad):
    assert client.get(f"/api/sessions/{bad}").status_code == 404


# ── /api/forward-test ───────────────────────────────────────────────────────

def test_the_forward_test_is_the_change_in_the_account_on_the_configured_capital(client):
    r = client.get("/api/forward-test")
    assert r.status_code == 200
    d = r.json()
    assert d["market"] == "US" and d["capital"] == 50_000.0            # config/sp100_forward_test.json
    assert (d["pnl"], d["value"], round(d["pnl_pct"], 3)) == (499.0, 50_499.0, 0.998)
    assert (d["sessions"], d["rebalances"], d["orders"], d["realised"]) == (2, 2, 3, 300.0)
    assert d["started"] == "20261005" and d["first_trade"] == "2026-10-05"
    assert pd.Timestamp(d["as_of"]) == _utc("2026-10-06 13:35:02.1")
    assert [p["pnl"] for p in d["equity"]] == [0.0, 499.0, 499.0]
    assert [p["value"] for p in d["equity"]] == [50_000.0, 50_499.0, 50_499.0]
    assert pd.Timestamp(d["equity"][0]["timestamp"]) == _utc("2026-10-05 13:35:02.481894")


def test_forward_test_trades_and_holdings(client):
    d = client.get("/api/forward-test").json()
    buy, sale, buy2 = d["trades"]
    assert (buy["date"], buy["side"], buy["stock"], buy["value"], buy["realised_pnl"]) == \
        ("2026-10-05", "BUY", "US.AAA", 5000.0, None)
    assert (sale["side"], sale["realised_pnl"], round(sale["realised_pct"], 6)) == ("SELL", 300.0, 6.0)
    assert sale["decided_on_candle"] == "2026-10-06 10:30"
    assert buy2["stock"] == "US.BBB" and buy2["date"] == "2026-10-06"
    # holdings: the positions at the last session_end, valued at the last recorded close
    assert d["holdings"] == [{"stock": "US.AAA", "shares": 100.0, "bought_at": 50.0, "last_price": 55.0,
                              "value": 5500.0, "unrealised_pnl": 500.0, "unrealised_pct": pytest.approx(10.0)}]


def test_no_forward_test_for_a_market_is_404(client):
    assert client.get("/api/forward-test", params={"market": "HK"}).status_code == 404
    assert client.get("/api/forward-test", params={"market": "XX"}).status_code == 422
