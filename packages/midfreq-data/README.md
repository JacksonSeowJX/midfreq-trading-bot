# midfreq-data

Market data for the framework: the connectors that fetch bars and instrument definitions from
brokers and exchanges, the original bar cache (`data/<MARKET>_<CODE>/<interval>.parquet`) and the
newer Parquet data catalog (`catalog/`), and the `fetch` command line.

| Module | What it holds |
|---|---|
| `midfreq.data.adapters` | `Connector` base class, the registry, and one connector per source |
| `midfreq.data.providers` | The original Moomoo, IBKR and yfinance providers the connectors wrap; the live engine still uses them |
| `midfreq.data.storage` | `DataStorage`, read and write access to the `data/` bar cache |
| `midfreq.data.persistence` | `ParquetDataCatalog`, a read-only reader for the `data/` cache, and the copy-only migration between the two |
| `midfreq.data.service` | `fetch_bars()`, the logic behind the `fetch` command |

## Connectors

| Name | Source | Default for | Needs |
|---|---|---|---|
| `moomoo` | Moomoo OpenD gateway | XHKG, XNAS, XNYS and the other equity venues | `moomoo` extra, OpenD running |
| `ibkr` | TWS or IB Gateway | (pass `--client ibkr`) | `ibkr` extra, TWS or Gateway running |
| `yfinance` | Yahoo Finance | (pass `--client yfinance`) | `yfinance` extra |
| `binance` | Binance public REST API | BINANCE | nothing (no SDK, no API key) |
| `ccxt` | Any exchange the ccxt library supports | COINBASE, KRAKEN, OKX | `crypto` extra |

Each venue's default connector and its per-connector options (market code, exchange id, symbology)
are in `config/venues.json`. Importing the package never imports a vendor SDK; a connector loads
its SDK when it is first used.

## Command line

Run from the repository root:

    uv run python -m midfreq.data venues
    uv run python -m midfreq.data fetch --venue XHKG --symbols 00700 00005 --bar 1-HOUR --start 2024-01-01
    uv run python -m midfreq.data fetch --venue XNAS --symbols AAPL --bar 1-DAY --start 2020-01-01 --client yfinance
    uv run python -m midfreq.data fetch --venue KRAKEN --symbols BTC-USD --start 2024-06-01 --dry-run
    uv run python -m midfreq.data migrate --legacy-dir data --catalog catalog --dry-run
    uv run python -m midfreq.data catalog

`--client` (or `--connector`) overrides the venue's default connector. `--end` defaults to now.

## Connection settings

Hosts, ports, client ids and base URLs are not hardcoded. For each setting the first of these
that is present is used:

1. an argument passed in code, e.g. `MoomooProvider(port=11112)` or `MoomooConnector({"port": 11112})`;
2. an environment variable `MIDFREQ_<CONNECTION>_<KEY>`, e.g. `MIDFREQ_MOOMOO_PORT=11112`;
3. the connection's entry in `config/connections.yaml` (`moomoo`, `ibkr`, `binance`, `ccxt`);
4. the built-in default, which equals the value shipped in that file.

For Binance, a `base_url` in `config/venues.json` is used only when `connections.yaml` has none.
For ccxt, a venue's `exchange_id` in `config/venues.json` takes precedence; `default_exchange`
applies only to a venue that does not name one. API keys are never read from the file: they
come from the environment or the untracked `.env` (see `midfreq.core.connections.get_secret`).

## Tests

    uv run pytest -q -p no:cacheprovider packages/midfreq-data

The tests use fake brokers and temporary folders only: no network, no broker, and nothing in the
real `data/` or `catalog/` folders.
