"""
The accounts the dashboard shows: the `accounts` section of config/connections.yaml, and a
read-only AccountClient for each, made by the account's connector.

    accounts:
      moomoo-us:
        label: Moomoo paper (US)
        connector: moomoo            # a connector name the registry knows
        options: {market: US}        # passed to that connector's account_client()
      okx:
        label: OKX
        connector: ccxt
        options: {exchange: okx}
        secrets: [OKX_READ_API_KEY, OKX_READ_API_SECRET, OKX_READ_API_PASSPHRASE]

`secrets` are the NAMES of environment variables (or untracked .env entries) that hold a
read-only key; their values never appear in the file. Adding a venue is a connector with an
account_client() and an entry here, no dashboard code.

`connections` (a dict as load_connections() returns, or a path to a YAML file) replaces reading the
accounts section from config/connections.yaml; tests pass one. Connector settings such as OpenD's
host and port still come from the usual places (MIDFREQ_CONNECTIONS points all of them at another
file).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from midfreq.data.adapters.base import AccountClient, ConnectorError
from midfreq.data.adapters.registry import ConnectorRegistry, default_registry
from midfreq.core.connections import load_connections

Connections = Union[Mapping[str, Any], str, Path, None]

__all__ = ["account_config", "account_ids", "create_account_client", "load_accounts"]


def load_accounts(connections: Connections = None) -> Dict[str, Dict[str, Any]]:
    """Account id -> its entry, normalised to {id, label, connector, options, secrets}, in file order."""
    if connections is None or isinstance(connections, (str, Path)):
        connections = load_connections(Path(connections) if connections is not None else None)
    section = connections.get("accounts") or {}
    if not isinstance(section, Mapping):
        raise ValueError(f"the accounts section of connections.yaml must be a mapping, got {type(section).__name__}")
    return {str(account_id): _normalise(str(account_id), entry) for account_id, entry in section.items()}


def _normalise(account_id: str, entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, Mapping) or not entry.get("connector"):
        raise ValueError(f"account {account_id!r} in connections.yaml needs a `connector`")
    options = entry.get("options") or {}
    secrets = entry.get("secrets") or []
    if not isinstance(options, Mapping):
        raise ValueError(f"account {account_id!r}: `options` must be a mapping")
    if not isinstance(secrets, list) or not all(isinstance(s, str) for s in secrets):
        raise ValueError(f"account {account_id!r}: `secrets` must be a list of environment variable names")
    return {"id": account_id, "label": str(entry.get("label") or account_id),
            "connector": str(entry["connector"]), "options": dict(options), "secrets": list(secrets)}


def account_ids(connections: Connections = None) -> List[str]:
    """The configured account ids, in file order."""
    return list(load_accounts(connections))


def account_config(account_id: str, connections: Connections = None) -> Dict[str, Any]:
    """One account's entry: {'id', 'label', 'connector', 'options', 'secrets'}. KeyError if unknown."""
    accounts = load_accounts(connections)
    try:
        return accounts[account_id]
    except KeyError:
        raise KeyError(f"unknown account {account_id!r}; configured: {list(accounts)}") from None


def create_account_client(account_id: str, connections: Connections = None,
                          registry: Optional[ConnectorRegistry] = None) -> AccountClient:
    """
    A read-only AccountClient for a configured account: its connector, created through the
    registry, asked for account_client(**options, secrets=secrets). No request is made; call
    available() before reading. KeyError for an unknown id; ConnectorError for an entry that
    cannot be served (unknown connector, one without account clients, options it does not take,
    a market no venue has).
    """
    cfg = account_config(account_id, connections)
    try:
        connector = (registry or default_registry()).create(cfg["connector"])
        return connector.account_client(**cfg["options"], secrets=cfg["secrets"])
    except ConnectorError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorError(f"account {account_id!r}: {exc.args[0] if exc.args else exc}") from exc
