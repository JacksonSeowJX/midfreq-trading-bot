"""
Where the API gets broker and exchange accounts from.

An AccountProvider lists the configured accounts and makes a read-only AccountClient
(midfreq.data.adapters.base) for one of them. The default, ConfiguredAccounts, lists the
`accounts` section of config/connections.yaml and builds clients with the connectors' factory,
midfreq.data.adapters.accounts.create_account_client. The factory is imported only when an
account is actually read, so listing accounts never loads a broker SDK. Tests replace the
provider through the `get_account_provider` dependency and use fake clients.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List

from midfreq.core.connections import load_connections
from midfreq.data.adapters.base import AccountClient, AccountUnavailableError


@dataclass(frozen=True)
class AccountEntry:
    id: str
    label: str
    connector: str


class AccountProvider(ABC):

    @abstractmethod
    def accounts(self) -> List[AccountEntry]:
        """The configured accounts, in configuration order."""

    @abstractmethod
    def client(self, account_id: str) -> AccountClient:
        """A new client for one configured account; the caller closes it. Raises
        AccountUnavailableError if no client can be made now (connector missing, SDK absent)."""


class ConfiguredAccounts(AccountProvider):
    """The `accounts` section of connections.yaml, read on every call so edits show up."""

    def __init__(self, connections_file: Path):
        self.connections_file = Path(connections_file)

    def accounts(self) -> List[AccountEntry]:
        section = load_connections(self.connections_file).get("accounts") or {}
        return [AccountEntry(id=str(key), label=str((cfg or {}).get("label") or key),
                             connector=str((cfg or {}).get("connector") or ""))
                for key, cfg in section.items()]

    def client(self, account_id: str) -> AccountClient:
        try:
            from midfreq.data.adapters.accounts import create_account_client
        except ImportError as e:
            raise AccountUnavailableError(f"account clients are not installed ({e})") from e
        try:
            return create_account_client(account_id, connections=self.connections_file)
        except ImportError as e:          # the connector's SDK is not installed here
            raise AccountUnavailableError(f"{account_id}: {e}") from e


class NoAccounts(AccountProvider):
    """No broker accounts: a deployment with no gateway or keys (the hosted dashboard). The
    account panels then say none are configured instead of probing a gateway that is not there."""

    def accounts(self) -> List[AccountEntry]:
        return []

    def client(self, account_id: str) -> AccountClient:
        raise KeyError(account_id)
