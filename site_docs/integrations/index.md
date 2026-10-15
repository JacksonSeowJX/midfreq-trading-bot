# Integrations

The framework reaches brokers and exchanges through **connectors** in `midfreq.data.adapters`,
one per source, all implementing the same contract
([Data and connectors](../concepts/data.md#connectors)). A connector translates the framework's
instrument ids into the source's symbols, fetches historical bars and stamps each with its
close in true UTC, loads instrument definitions, and retries the source's own rate-limit
signals. Two connectors also read accounts, read-only.

| Connector | Source | Kind | Default for | Needs | Accounts |
|---|---|---|---|---|---|
| [`moomoo`](moomoo.md) | moomoo OpenD gateway | broker gateway | XHKG, XNAS, XNYS, XSES, XSHG, XSHE, XTKS | the `moomoo` extra; OpenD running and logged in | yes, through OpenD |
| [`ibkr`](interactive_brokers.md) | Interactive Brokers TWS or IB Gateway | broker gateway | none (`--client ibkr`) | the `ibkr` extra; TWS or IB Gateway running | no |
| [`yfinance`](yfinance.md) | Yahoo Finance | public data | none (`--client yfinance`) | the `yfinance` extra | no |
| [`binance`](binance.md) | Binance spot, public REST API | exchange | BINANCE | nothing: no SDK, no API key | no |
| [`ccxt`](ccxt.md) | any exchange the ccxt library supports | exchange library | COINBASE, KRAKEN, OKX | the `crypto` extra | yes, with a read-only API key |

`uv sync` installs every extra. Importing `midfreq.data` imports no vendor SDK: each connector
loads its SDK when it is first used, so a missing SDK affects only that connector.

## Status

| Capability | State in this version |
|---|---|
| Historical bars | every connector, tested against the contract with a fake transport for each source |
| Instrument definitions | every connector: from the source where it publishes them, else the venue's defaults |
| Account reads | `moomoo` and `ccxt` |
| Live market data | not through connectors; the live engine streams candles from OpenD itself ([Live trading](../concepts/live_trading.md)) |
| Order execution | not through connectors (`ExecutionClient` is reserved); paper orders go through `midfreq.live.order_gateway`, for moomoo only |

## Common configuration

- **Venues.** Which connector serves a venue, which is the default, and how each spells the
  venue's symbols are in [`config/venues.json`](repo:config/venues.json). Each integration page
  shows its connector's entries, generated from that file.
- **Connections.** Hosts, ports, client ids and base URLs are in
  [`config/connections.yaml`](repo:config/connections.yaml). For each setting the first of these
  that is present is used: an argument in code, an environment variable
  `MIDFREQ_<CONNECTION>_<KEY>`, the file, and the built-in default
  ([Connection settings](../concepts/data.md#connection-settings)).
- **Secrets.** API keys never go in either file. A connection or account lists the names of the
  environment variables that hold them, read from the environment or an untracked `.env`.
- **Shared settings.** Every connector's configuration has `max_retries` (5), `backoff_base`
  (1 s), `backoff_max` (60 s) and `min_request_interval` (the client-side spacing of requests).

## Fetching

```bash
uv run python -m midfreq.data fetch --venue XHKG --symbols 00700 00005 --bar 1-HOUR --start 2024-01-01 --dry-run
```

```text
would fetch 00700.XHKG-1-HOUR-LAST-EXTERNAL  (native symbol HK.00700)
would fetch 00005.XHKG-1-HOUR-LAST-EXTERNAL  (native symbol HK.00005)
```

`--dry-run` resolves the venue's connector, the symbols and the interval, and fetches nothing.
Without it, the bars and the instrument definitions are written to the data catalog.
`--client` (or `--connector`) chooses another connector the venue lists.
