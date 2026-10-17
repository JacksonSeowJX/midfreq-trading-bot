"""Shared set-up for the trading node's tests. Nothing here touches a network, a broker, the real
data/ cache or live_sessions/ (recorded sessions are copied into a temporary folder first)."""
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))          # _node_testkit, with --import-mode=importlib

from midfreq.core.paths import repo_root  # noqa: E402

REPO = repo_root()


@pytest.fixture
def recorded_sessions(tmp_path):
    """The recorded cross-sectional sessions, copied from live_sessions/ (read only)."""
    dest = tmp_path / "recorded"
    dest.mkdir()
    for f in sorted((REPO / "live_sessions").glob("session_*crosssectionalreversal*.jsonl")):
        shutil.copy2(f, dest / f.name)
    return dest
