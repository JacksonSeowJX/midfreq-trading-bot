"""
FastAPI dependencies: the seams tests and deployments replace (app.dependency_overrides).

    get_settings          -> Settings (folders, timeouts)
    get_record_store      -> the bot's record (a RecordStore over Settings.live_sessions)
    get_account_provider  -> broker and exchange accounts (an AccountProvider)
"""
from fastapi import Depends

from midfreq.api.account_provider import AccountProvider, ConfiguredAccounts
from midfreq.api.settings import Settings, get_settings
from midfreq.api.store import RecordStore, record_store_for

__all__ = ["get_settings", "get_record_store", "get_account_provider"]


def get_record_store(settings: Settings = Depends(get_settings)) -> RecordStore:
    return record_store_for(settings.live_sessions)


def get_account_provider(settings: Settings = Depends(get_settings)) -> AccountProvider:
    return ConfiguredAccounts(settings.connections_file)
