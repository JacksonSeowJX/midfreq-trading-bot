"""
Where the API reads from, in one object.

The defaults come from midfreq.core.paths (so MIDFREQ_ROOT moves everything at once). Routes
receive the Settings through the `get_settings` dependency, which tests and other deployments
replace with `app.dependency_overrides[get_settings] = lambda: Settings(...)`, or by passing
Settings to `create_app`.
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

from midfreq.core import paths

# The Vite dev server (apps/web: npm run dev).
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


@dataclass(frozen=True)
class Settings:
    results_dir: Path
    live_sessions: str                    # the bot's record: a local folder or gs://bucket/prefix (store.py)
    data_dir: Path                        # the candle cache, data/<MARKET>_<CODE>/<interval>.parquet
    config_dir: Path
    web_dist: Optional[Path] = None       # the built web app (apps/web/dist), served at / when it exists
    # The session logs' own times (session_start, session_end) are the VM's wall clock without a
    # zone; the VM runs on Singapore time (ops/vm/crontab.txt). The API returns them in UTC.
    record_timezone: str = "Asia/Singapore"
    probe_timeout: float = 3.0            # seconds an account's availability probe may take
    account_timeout: float = 20.0         # seconds reading one account's snapshot, positions and orders may take
    cors_origins: Tuple[str, ...] = DEV_ORIGINS
    accounts_enabled: bool = True         # off where no broker gateway exists (the hosted dashboard)
    snapshot_accounts_only: bool = False  # only accounts the VM publishes as snapshots (the hosted dashboard)
    job_kinds: Tuple[str, ...] = ("backtest", "compare", "optimize")   # runs this deployment allows
    max_concurrent_jobs: int = 1

    @property
    def connections_file(self) -> Path:
        return self.config_dir / "connections.yaml"

    @property
    def forward_test_file(self) -> Path:
        return self.config_dir / "sp100_forward_test.json"


def default_settings() -> Settings:
    """The repository's folders; two environment variables adapt it to a deployment:
    MIDFREQ_LIVE_SESSIONS (a folder or gs://bucket/prefix for the bot's record),
    MIDFREQ_DASHBOARD_ACCOUNTS=on|snapshots|off (live gateways, only VM-published snapshots, none),
    MIDFREQ_DASHBOARD_JOBS (comma-separated run kinds allowed) and MIDFREQ_DASHBOARD_MAX_JOBS."""
    return Settings(results_dir=paths.results_dir(),
                    live_sessions=os.environ.get("MIDFREQ_LIVE_SESSIONS") or str(paths.live_sessions_dir()),
                    data_dir=paths.data_dir(), config_dir=paths.config_dir(),
                    web_dist=paths.repo_root() / "apps" / "web" / "dist",
                    accounts_enabled=os.environ.get("MIDFREQ_DASHBOARD_ACCOUNTS", "on").lower() not in ("off", "0", "false"),
                    snapshot_accounts_only=os.environ.get("MIDFREQ_DASHBOARD_ACCOUNTS", "on").lower() == "snapshots",
                    job_kinds=tuple(k for k in os.environ.get("MIDFREQ_DASHBOARD_JOBS", "backtest,compare,optimize").split(",") if k),
                    max_concurrent_jobs=int(os.environ.get("MIDFREQ_DASHBOARD_MAX_JOBS", "1")))


@lru_cache(maxsize=None)
def get_settings() -> Settings:
    """The dependency every route takes its Settings from."""
    return default_settings()
