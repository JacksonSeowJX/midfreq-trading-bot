# midfreq-core

The domain model that every other package builds on. It depends on no other `midfreq` package.

| Module | What it holds |
|---|---|
| `midfreq.core.candle` | `Candle` and `Timeframe`, used by the candle-based strategies, backtester and live engine |
| `midfreq.core.portfolio`, `midfreq.core.risk_manager` | The cash-and-positions portfolio and the risk rules (sizing, stops, circuit breakers) |
| `midfreq.core.config` | `ConfigLoader` for `config/symbols.json` (markets and universes) |
| `midfreq.core.model` | Identifiers, instruments, bars, orders, events, signals and fee models |
| `midfreq.core.venues` | `VenueSpec` and `VenueRegistry`, loaded from `config/venues.json` (sessions, timezones, fees, connectors) |
| `midfreq.core.accounting`, `midfreq.core.cache` | Per-currency accounts and positions; the engine's in-memory store |
| `midfreq.core.common` | Nanosecond timestamps, the clock, the message bus, retry and throttling |
| `midfreq.core.paths` | Where `config/`, `data/`, `results/`, `live_sessions/` and `catalog/` are (`MIDFREQ_ROOT` overrides the root) |
| `midfreq.core.connections` | `get_connection(name)` and `get_secret(var)` |

## Connection settings

`get_connection("moomoo")` returns that entry of `config/connections.yaml`, with any
`MIDFREQ_<NAME>_<KEY>` environment variable overriding its key (parsed as YAML, so
`MIDFREQ_MOOMOO_PORT=11112` is the integer 11112). `MIDFREQ_CONNECTIONS` points at a different
file. An unknown name raises `KeyError`. Secrets never go in the file: an entry lists the
variables that hold them under `secrets`, and `get_secret(var)` reads them from the environment
or the untracked `.env`.

## Tests

    uv run pytest -q -p no:cacheprovider packages/midfreq-core
