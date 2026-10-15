"""
Where the repository's shared folders are, found without counting directory levels.

Modules used to locate `config/` and `data/` with `Path(__file__).parent.parent.parent`, which
broke whenever a file moved. Every package now asks here instead. The root is, in order:
the MIDFREQ_ROOT environment variable; the nearest folder above this file that holds both
`config/` and `packages/` (an editable install, which is how the workspace is installed); the
nearest such folder above the current directory.
"""
import os
from functools import lru_cache
from pathlib import Path


def _is_root(p: Path) -> bool:
    return (p / "config").is_dir() and (p / "packages").is_dir()


@lru_cache(maxsize=None)
def repo_root() -> Path:
    env = os.environ.get("MIDFREQ_ROOT")
    if env:
        return Path(env).resolve()
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for p in (start, *start.parents):
            if _is_root(p):
                return p
    raise RuntimeError("cannot find the repository root; set MIDFREQ_ROOT to the folder holding config/")


def config_dir() -> Path:
    return repo_root() / "config"


def data_dir() -> Path:
    """The legacy bar cache, data/<MARKET>_<CODE>/<interval>.parquet."""
    return repo_root() / "data"


def results_dir() -> Path:
    return repo_root() / "results"


def live_sessions_dir() -> Path:
    return repo_root() / "live_sessions"


def catalog_dir() -> Path:
    """The Parquet data catalog written by `python -m midfreq.data fetch`."""
    return repo_root() / "catalog"
