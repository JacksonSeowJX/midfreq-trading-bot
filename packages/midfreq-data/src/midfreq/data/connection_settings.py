"""
Connection settings for providers and connectors: an explicit argument wins, then
config/connections.yaml (with its MIDFREQ_<NAME>_<KEY> environment overrides), then the
built-in default. The built-in defaults equal the values shipped in connections.yaml, so a
missing file or entry behaves exactly as before the file existed.
"""
from __future__ import annotations

from typing import Any, Dict

from midfreq.core.connections import get_connection


def connection_settings(name: str, defaults: Dict[str, Any], **explicit: Any) -> Dict[str, Any]:
    """One value per key of `defaults`: `explicit[key]` if given (not None), else the
    connection's setting, else the default."""
    missing = [k for k in defaults if explicit.get(k) is None]
    conn: Dict[str, Any] = {}
    if missing:
        try:
            conn = get_connection(name)
        except KeyError:   # no such connection in the file (or no file): use the defaults
            conn = {}
    return {k: explicit[k] if explicit.get(k) is not None else conn.get(k, defaults[k]) for k in defaults}
