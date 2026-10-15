"""
ConnectorRegistry: connector name -> factory, and the choice of connector for a venue.

The built-in connectors are registered by import path and only imported when first created, so
`import mft` never pulls in the moomoo SDK, ib_insync (which starts an event loop on import) or
ccxt. Which connector serves a venue by default is `default_connector` in config/venues.json; a
caller (or the CLI's --connector/--client flag) can override it per call.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List, Optional, Union

from midfreq.data.adapters.base import Connector, ConnectorConfig, UnsupportedVenueError
from midfreq.core.model.identifiers import Venue
from midfreq.core.venues.spec import VenueRegistry, default_venues

ConnectorFactory = Callable[..., Connector]

BUILTIN_CONNECTORS = {
    "moomoo": "midfreq.data.adapters.moomoo.connector:MoomooConnector",
    "ibkr": "midfreq.data.adapters.ibkr.connector:IBKRConnector",
    "yfinance": "midfreq.data.adapters.yfinance.connector:YFinanceConnector",
    "binance": "midfreq.data.adapters.binance.connector:BinanceConnector",
    "ccxt": "midfreq.data.adapters.ccxt.connector:CcxtConnector",
}

# Names people are likely to type for the same connector.
ALIASES = {"ib": "ibkr", "interactive_brokers": "ibkr", "futu": "moomoo", "yahoo": "yfinance"}


def _lazy(path: str) -> ConnectorFactory:
    module, _, attr = path.partition(":")

    def factory(*args, **kwargs) -> Connector:
        cls = getattr(importlib.import_module(module), attr)
        return cls(*args, **kwargs)
    factory.__qualname__ = f"lazy({path})"
    return factory


class ConnectorRegistry:
    def __init__(self, venues: Optional[VenueRegistry] = None, builtins: bool = True):
        self._venues = venues
        self._factories: Dict[str, ConnectorFactory] = {}
        if builtins:
            for name, path in BUILTIN_CONNECTORS.items():
                self.register(name, _lazy(path))

    @property
    def venues(self) -> VenueRegistry:
        return self._venues or default_venues()

    @staticmethod
    def canonical(name: str) -> str:
        key = name.strip().lower()
        return ALIASES.get(key, key)

    def register(self, name: str, factory: ConnectorFactory, overwrite: bool = True) -> None:
        key = self.canonical(name)
        if key in self._factories and not overwrite:
            raise ValueError(f"connector {key!r} is already registered")
        self._factories[key] = factory

    def names(self) -> List[str]:
        return sorted(self._factories)

    def create(self, name: str, config: Union[ConnectorConfig, Dict[str, Any], None] = None,
               **kwargs) -> Connector:
        """A new connector by name. `kwargs` reach the connector's constructor, which is how tests
        inject a fake transport (provider=, transport=, exchanges=)."""
        key = self.canonical(name)
        try:
            factory = self._factories[key]
        except KeyError:
            raise KeyError(f"unknown connector {name!r}; registered: {self.names()}") from None
        kwargs.setdefault("venues", self.venues)
        return factory(config, **kwargs)

    def default_for(self, venue: Union[str, Venue]) -> str:
        spec = self.venues.get(venue)
        if not spec.default_connector:
            raise UnsupportedVenueError(f"venue {spec.venue} has no default connector in config/venues.json")
        return spec.default_connector

    def for_venue(self, venue: Union[str, Venue], override: Optional[str] = None,
                  config: Union[ConnectorConfig, Dict[str, Any], None] = None, **kwargs) -> Connector:
        """The connector that serves `venue`: `override` if given, else the venue's default."""
        name = self.canonical(override) if override else self.default_for(venue)
        spec = self.venues.get(venue)
        if name not in spec.connectors:
            raise UnsupportedVenueError(
                f"connector {name!r} is not configured for {spec.venue}; configured: {sorted(spec.connectors)}")
        return self.create(name, config, **kwargs)


_default_registry: Optional[ConnectorRegistry] = None


def default_registry() -> ConnectorRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ConnectorRegistry()
    return _default_registry
