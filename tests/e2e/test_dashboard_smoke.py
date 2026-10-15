"""The dashboard starts, and its read-only views render, without OpenD or the network.

The live view is left out on purpose: it probes the OpenD port. The backtest views
are left out too, as they need the data/ cache; the landing page, the results
overview and the research browser only read config/ and results/.
"""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from midfreq.core.paths import repo_root

APP = repo_root() / "apps" / "dashboard" / "app.py"


@pytest.fixture(autouse=True)
def _no_real_data_cache(tmp_path, monkeypatch):
    # the app opens DataStorage() at start-up; keep it off the real data/ cache
    monkeypatch.setattr("midfreq.data.storage.data_dir", lambda: tmp_path / "data")


def _start():
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _click(at, label):
    btn = next(b for b in at.sidebar.button if label in b.label)
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]


def test_the_landing_page_renders():
    at = _start()
    assert any("Run Backtest" in b.label for b in at.sidebar.button)


@pytest.mark.parametrize("label", ["All Results Overview", "Browse Research Results"])
def test_a_results_view_renders(label):
    _click(_start(), label)
