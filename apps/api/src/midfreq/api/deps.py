"""
FastAPI dependencies: the seams tests and deployments replace (app.dependency_overrides).

    get_settings          -> Settings (folders, timeouts)
    get_record_store      -> the bot's record (a RecordStore over Settings.live_sessions)
    get_account_provider  -> broker and exchange accounts (an AccountProvider)
    get_run_store         -> saved runs (midfreq.backtest.runstore: MIDFREQ_RUNS, else <repo>/runs)
"""
import logging
import threading
from typing import Optional

from fastapi import Depends

from midfreq.api.account_provider import AccountProvider, ConfiguredAccounts, NoAccounts
from midfreq.api.saved_runs import UnavailableRunStore
from midfreq.api.settings import Settings, get_settings
from midfreq.api.store import RecordStore, record_store_for
from midfreq.backtest.runstore import RunStore

__all__ = ["get_settings", "get_record_store", "get_account_provider", "get_run_store"]

log = logging.getLogger(__name__)


def get_record_store(settings: Settings = Depends(get_settings)) -> RecordStore:
    return record_store_for(settings.live_sessions)


def get_account_provider(settings: Settings = Depends(get_settings)) -> AccountProvider:
    if not settings.accounts_enabled:
        return NoAccounts()
    return ConfiguredAccounts(settings.connections_file)


_run_store: Optional[RunStore] = None
_run_store_lock = threading.Lock()


def get_run_store() -> RunStore:
    """The process's run store, made once (a GcsRunStore holds a client). A store that cannot be
    made (a bucket without credentials) is an UnavailableRunStore with the reason: the Run
    history answers 503 with it, and jobs still run, unsaved; the next request tries again."""
    global _run_store
    with _run_store_lock:
        if _run_store is None:
            try:
                from midfreq.backtest.runstore import default_run_store    # lazily: it may import a cloud client
                _run_store = default_run_store()
            except Exception as e:                                         # noqa: BLE001 (any reason is shown)
                log.warning("the run store cannot be opened: %s: %s", type(e).__name__, e)
                return UnavailableRunStore(f"the run store cannot be opened: {type(e).__name__}: {e}")
        return _run_store
