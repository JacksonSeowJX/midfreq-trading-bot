"""midfreq.core.connections: file values, environment overrides and secrets, on temporary files only."""
import pytest

from midfreq.core import connections
from midfreq.core.connections import get_connection, get_secret, load_connections

CONFIG = """
moomoo:
  host: 127.0.0.1
  port: 11111
  trading_env: simulate
ibkr:
  host: 127.0.0.1
  port: 7497
  client_id: 1
yfinance: {}
binance:
  base_url: https://api.binance.com
  secrets: [TEST_BINANCE_KEY]
"""


@pytest.fixture
def conn_file(tmp_path):
    p = tmp_path / "connections.yaml"
    p.write_text(CONFIG)
    return p


def test_file_values(conn_file):
    m = get_connection("moomoo", path=conn_file, env={})
    assert m == {"host": "127.0.0.1", "port": 11111, "trading_env": "simulate"}
    assert isinstance(m["port"], int)
    assert get_connection("yfinance", path=conn_file, env={}) == {}


def test_env_override_is_typed(conn_file):
    env = {"MIDFREQ_MOOMOO_PORT": "11112", "MIDFREQ_MOOMOO_TRADING_ENV": "real",
           "MIDFREQ_IBKR_CLIENT_ID": "7", "MIDFREQ_MOOMOO_NEW_KEY": "true"}
    m = get_connection("moomoo", path=conn_file, env=env)
    assert m["port"] == 11112 and isinstance(m["port"], int)
    assert m["trading_env"] == "real"
    assert m["new_key"] is True                       # a key the file does not have is added
    assert get_connection("ibkr", path=conn_file, env=env)["client_id"] == 7


def test_override_only_touches_its_own_connection(conn_file):
    env = {"MIDFREQ_MOOMOO_PORT": "1", "MIDFREQ_IBKRX_PORT": "2"}
    assert get_connection("ibkr", path=conn_file, env=env)["port"] == 7497


def test_unknown_connection(conn_file):
    with pytest.raises(KeyError, match="bloomberg"):
        get_connection("bloomberg", path=conn_file, env={})


def test_missing_file_has_no_connections(tmp_path):
    assert load_connections(tmp_path / "absent.yaml") == {}
    with pytest.raises(KeyError):
        get_connection("moomoo", path=tmp_path / "absent.yaml", env={})


def test_path_from_environment(conn_file, monkeypatch):
    monkeypatch.setenv("MIDFREQ_CONNECTIONS", str(conn_file))
    assert get_connection("ibkr", env={})["port"] == 7497


def test_secrets_come_from_the_environment_not_the_file(conn_file, monkeypatch):
    monkeypatch.setattr(connections, "_load_dotenv", lambda: None)   # never read the real .env
    b = get_connection("binance", path=conn_file, env={})
    assert b["secrets"] == ["TEST_BINANCE_KEY"]         # the file names the variable only
    monkeypatch.delenv("TEST_BINANCE_KEY", raising=False)
    assert get_secret("TEST_BINANCE_KEY") is None
    monkeypatch.setenv("TEST_BINANCE_KEY", "abc")
    assert get_secret("TEST_BINANCE_KEY") == "abc"
