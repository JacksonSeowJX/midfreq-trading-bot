"""
Connectors for data sources (the adapter pattern). Import a specific connector from its
subpackage, or create one by name through `default_registry()`; nothing here imports a vendor SDK.
"""
from mft.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, ConnectorError, DataClient,
    ExecutionClient, InstrumentProvider, QuotaExceededError, RateLimitError,
    UnsupportedIntervalError, UnsupportedVenueError,
)
from mft.adapters.registry import ConnectorRegistry, default_registry

__all__ = [
    "Connector", "ConnectorCapabilities", "ConnectorConfig", "ConnectorError",
    "ConnectorRegistry", "DataClient", "ExecutionClient", "InstrumentProvider",
    "QuotaExceededError", "RateLimitError", "UnsupportedIntervalError", "UnsupportedVenueError",
    "default_registry",
]
