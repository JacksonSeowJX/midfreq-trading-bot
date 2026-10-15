"""Research results: the overview of validated results and the raw result files."""
import pytest

from midfreq.api.main import create_app
from midfreq.core.paths import results_dir

NEW = "cross_sectional_corrected_20260909_050515.csv"


def test_the_overview_stacks_the_newest_current_result_of_each_study(client):
    r = client.get("/api/results/overview")
    assert r.status_code == 200
    d = r.json()
    assert d["summary"] == {"total": 2, "passed": 1, "studies": 1, "families": 1, "pass_rate": 50.0}
    top = d["rows"][0]
    assert (top["family"], top["scope"], top["strategy"], top["subject"]) == \
        ("Cross-Sectional", "universe", "CS-Reversal", "sp100")
    assert (top["config_a_oos"], top["config_b_consistency"], top["worst_config"], top["robust"]) == \
        (5.7, 53.3, 0.6, True)
    assert (top["study"], top["file"]) == ("cross_sectional_corrected", NEW)


def test_superseded_studies_are_included_on_request(client):
    d = client.get("/api/results/overview", params={"current_only": "false"}).json()
    assert d["summary"]["total"] == 3 and d["summary"]["studies"] == 2
    assert "cross_sectional_sp100" in {r["study"] for r in d["rows"]}


def test_the_overview_carries_the_search_correction(client):
    sc = client.get("/api/results/overview").json()["search_correction"]
    assert (sc["file"], sc["end"], sc["n_boot"]) == ("reality_check_20260929_050842.json", "2026-08-28", 2000)
    a = sc["configs"][0]
    assert (a["name"], a["p_value"], a["best"], a["luck_p95"], a["n_windows"]) == \
        ("A (9 windows)", 0.12, "Reversal, S&P 100", 6.1, 9)
    assert a["means"] == {"Momentum, Hang Seng": -0.3, "Reversal, S&P 100": 5.7}
    assert sc["pbo"] == {"pbo": 0.31, "n_configs": 96}


def test_an_empty_results_folder_is_an_empty_overview(client, settings):
    for p in settings.results_dir.iterdir():
        if p.is_file():
            p.unlink()
    d = client.get("/api/results/overview").json()
    assert d["rows"] == [] and d["summary"]["total"] == 0 and d["search_correction"] is None


def test_result_files_are_listed_newest_first_with_the_research_views_labels(client):
    files = client.get("/api/research/files").json()
    assert [f["name"] for f in files] == [
        # by the run timestamp in the name; a file without one (the PBO result) comes last
        "alpha_vs_market_20260821_20261015_185634.json", "reality_check_20260929_050842.json",
        "spread_hk_20260923_115005.csv", NEW,
        "cross_sectional_corrected_20260901_000000.csv", "cross_sectional_sp100_20260824_142925.csv",
        "pbo_sp100_reversal_full96.json"]
    by_name = {f["name"]: f for f in files}
    assert by_name[NEW]["study"] == "cross_sectional_corrected"
    assert by_name[NEW]["label"].startswith("Cross-Sectional, corrected")
    assert by_name["cross_sectional_sp100_20260824_142925.csv"]["label"] == \
        "Cross-Sectional Reversal — Robust, US S&P 100"
    assert by_name["reality_check_20260929_050842.json"]["label"] == "Reality Check"
    assert by_name["pbo_sp100_reversal_full96.json"]["study"] == "pbo_sp100_reversal_full96"
    assert by_name[NEW]["kind"] == "csv" and by_name[NEW]["size"] > 0
    assert by_name[NEW]["modified"].endswith("Z")


def test_a_csv_file_comes_back_as_columns_and_rows_with_blanks_as_null(client):
    d = client.get("/api/research/files/spread_hk_20260923_115005.csv").json()
    assert d["kind"] == "csv" and d["columns"] == ["symbol", "spread_bps", "note"] and d["data"] is None
    assert d["rows"] == [{"symbol": "HK.00700", "spread_bps": 4.5, "note": None},
                         {"symbol": "HK.00005", "spread_bps": None, "note": "thin"}]
    limited = client.get("/api/research/files/spread_hk_20260923_115005.csv", params={"limit": 1}).json()
    assert len(limited["rows"]) == 1


def test_a_json_file_comes_back_parsed_with_nan_as_null(client):
    d = client.get("/api/research/files/alpha_vs_market_20260821_20261015_185634.json").json()
    assert d["kind"] == "json" and d["data"] == {"alpha": None, "beta": 1.1} and d["rows"] is None


def test_an_unreadable_json_file_is_422(client, settings):
    (settings.results_dir / "broken_20260101.json").write_text("{not json")
    assert client.get("/api/research/files/broken_20260101.json").status_code == 422


@pytest.mark.parametrize("bad", ["notes.txt", "missing.csv", "..%2Fconfig%2Fconnections.yaml", "%2E%2E", "charts"])
def test_names_outside_the_listing_are_not_found(client, bad):
    assert client.get(f"/api/research/files/{bad}").status_code == 404


@pytest.mark.skipif(not results_dir().is_dir(), reason="no results/ in this checkout")
def test_smoke_the_repositorys_own_results_read(client_for):
    """Reads (never writes) the real results/ through the default settings."""
    client = client_for(create_app())
    d = client.get("/api/results/overview").json()
    assert d["summary"]["total"] == len(d["rows"]) > 0
    files = client.get("/api/research/files").json()
    assert files and all(f["kind"] in ("csv", "json") for f in files)
    first_csv = next(f["name"] for f in files if f["kind"] == "csv")
    assert client.get(f"/api/research/files/{first_csv}").status_code == 200


def test_the_newest_run_is_chosen_by_the_name_not_the_file_time(tmp_path):
    """A git checkout or a copy gives every file the same mtime (the hosted image); the run
    timestamp in the name must still pick the newest run of a study."""
    import os
    from midfreq.research.results_index import _newest_per_prefix
    old = tmp_path / "study_x_20260802_054116.csv"; new = tmp_path / "study_x_20260909_040502.csv"
    for f in (old, new):
        f.write_text("a\n1\n")
    os.utime(new, (1_000_000, 1_000_000)); os.utime(old, (2_000_000, 2_000_000))   # older run, newer mtime
    assert _newest_per_prefix([old, new])["study_x"] == new
