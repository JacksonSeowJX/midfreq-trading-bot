"""
Connection settings (hosts, ports, client ids, base URLs, paper or real trading) from
config/connections.yaml, with environment-variable overrides.

    from midfreq.core.connections import get_connection
    moomoo = get_connection("moomoo")
    MoomooProvider(host=moomoo["host"], port=moomoo["port"])

An override is MIDFREQ_<NAME>_<KEY> (upper case): MIDFREQ_MOOMOO_PORT=11112. Overrides are
parsed as YAML scalars, so numbers stay numbers. Secrets are not stored in the file; a
connection lists the environment variables that hold them under `secrets`, and
`get_secret()` reads them (after loading an untracked .env at the repository root, if any).
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from midfreq.core.paths import config_dir, repo_root

ENV_PREFIX = "MIDFREQ_"


def _default_path() -> Path:
    return Path(os.environ.get("MIDFREQ_CONNECTIONS", config_dir() / "connections.yaml"))


def load_connections(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    path = Path(path) if path else _default_path()
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return {name: dict(cfg or {}) for name, cfg in (raw or {}).items()}


def get_connection(name: str, path: Optional[Path] = None,
                   env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Settings for one connection, file values overridden by MIDFREQ_<NAME>_<KEY> variables."""
    env = os.environ if env is None else env
    conns = load_connections(path)
    if name not in conns:
        raise KeyError(f"unknown connection {name!r}; config has {sorted(conns)}")
    cfg = dict(conns[name])
    prefix = f"{ENV_PREFIX}{name.upper()}_"
    for var, value in env.items():
        if var.startswith(prefix):
            cfg[var[len(prefix):].lower()] = yaml.safe_load(value)
    return cfg


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(repo_root() / ".env", override=False)
    except RuntimeError:
        pass


def get_secret(var: str) -> Optional[str]:
    """A secret from the environment (or the untracked .env); never from the config file."""
    _load_dotenv()
    return os.environ.get(var)
