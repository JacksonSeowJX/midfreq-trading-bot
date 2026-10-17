# Integrations

The framework reaches brokers and exchanges through **connectors** in `midfreq.data.adapters`,
one per source, all implementing the same contract
([Data and connectors](../concepts/data.md#connectors)). A connector translates the framework's
instrument ids into the source's symbols, fetches historical bars and stamps each with its
close in true UTC, loads instrument definitions, and retries the source's own rate-limit
signals. Two connectors also read accounts, read-only, and trade: they route orders through an
execution client and stream live bars and quotes for the [trading node](../concepts/trading_node.md).

| Connector | Source | Kind | Default for | Needs | Accounts | Orders and live data |
|---|---|---|---|---|---|---|
| [`moomoo`](moomoo.md) | moomoo OpenD gateway | broker gateway | XHKG, XNAS, XNYS, XSES, XSHG, XSHE, XTKS | the `moomoo` extra; OpenD running and logged in | yes, through OpenD | yes, through OpenD |
| [`ibkr`](interactive_brokers.md) | Interactive Brokers TWS or IB Gateway | broker gateway | none (`--client ibkr`) | the `ibkr` extra; TWS or IB Gateway running | no | no |
| [`yfinance`](yfinance.md) | Yahoo Finance | public data | none (`--client yfinance`) | the `yfinance` extra | no | no |
| [`binance`](binance.md) | Binance spot, public REST API | exchange | BINANCE | nothing: no SDK, no API key | no | no |
| [`ccxt`](ccxt.md) | any exchange the ccxt library supports | exchange library | COINBASE, KRAKEN, OKX | the `crypto` extra | yes, with a read-only API key | yes, OKX first; paper on the exchange's demo environment |

`uv sync` installs every extra. Importing `midfreq.data` imports no vendor SDK: each connector
loads its SDK when it is first used, so a missing SDK affects only that connector.

## Status

| Capability | State in this version |
|---|---|
| Historical bars | every connector, tested against the contract with a fake transport for each source |
| Instrument definitions | every connector: from the source where it publishes them, else the venue's defaults |
| Account reads | `moomoo` and `ccxt` |
| Live market data | `moomoo` (K-line and order-book pushes) and `ccxt` (ccxt.pro websockets, else REST polling), on the data client's `subscribe_bars` and `subscribe_quotes` |
| Order execution | `moomoo` (paper by default) and `ccxt` (the exchange's demo environment by default), each an `ExecutionClient` that passes the execution contract; the trading node trades only through them |

## Trading through connectors

A connector that trades provides two things on top of historical data, both defined in
[`midfreq.data.adapters.base`](../api/python/midfreq/data/adapters/base.md):

- **An `ExecutionClient`** for one account: `connector.execution_client(**options)`.
- **Live streams** on its `DataClient`: `subscribe_bars(bar_type, handler)` delivers every push of
  the forming bar and of the bar just closed as a `BarUpdate`, with `ts_event` the bar's close in
  true UTC; `subscribe_quotes` delivers the best bid and ask as `QuoteTick`s; connection changes
  reach `set_stream_status_handler` as `StreamStatus` (`connected`, `disconnected`,
  `reconnected`, `error`); and `subscription_limit()` says how many subscriptions the account may
  hold, counted as one per instrument and data type. Deciding when a bar is final is the trading
  node's job, not the connector's.

The execution client's contract:

| Method or event | Contract |
|---|---|
| `submit_order(order)` | returns at once and never waits for a fill; the venue's order id goes in `order.tags["venue_order_id"]` |
| `cancel_order(order)` | returns at once; the outcome arrives as an event |
| events, to `set_event_handler` | `OrderAccepted`; `OrderRejected` with the venue's reason; one `OrderFilled` per execution, partial fills included, never repeated for a repeated push; `OrderCanceled`; `OrderCancelRejected` when the venue refuses a cancel. They may arrive on the client's own threads |
| fills | identified by (venue, the venue's order id or else the client order id, trade id); `commission` is the venue's fee in the currency it was charged in, or 0 with `tags["commission_unknown"]` when the venue reports none |
| `set_status_handler` | optional: `disconnected` when the client loses the venue, `reconnected` once it has caught up |
| `open_orders()`, `fills(since)`, `query_order(order)` | the venue's view, for reconciliation after a restart or a reconnect |
| `environment` | `paper`, `live` or `sim`, from configuration; a client refuses to start in `live` unless its connection says so explicitly |

Every execution client passes the same 13 scenarios of the execution contract suite, against a
fake of its venue: Moomoo, ccxt with websocket updates, ccxt polling, and the trading node's
simulated venue ([Testing](../developer_guide/testing.md#the-execution-contract)).

**Only connectors import a venue's SDK.** The trading node, the backtesters, the strategies and
the research code never import a broker's or an exchange's SDK (`moomoo`, `ccxt`, `ib_insync`,
...): they reach a venue only through its connector in `midfreq.data.adapters`, which loads the
SDK when it is first needed, so a new venue is a new connector and nothing else changes.
[`tests/integration/test_venue_sdk_boundary.py`](repo:tests/integration/test_venue_sdk_boundary.py)
checks it for `midfreq-core`, `midfreq-strategies`, `midfreq-backtest`, `midfreq-trading` and
`midfreq-research`. Within `midfreq-data`, the original providers in `midfreq.data.providers`,
which the connectors wrap, import their SDKs too; the session runner, `midfreq.live`, calls the
Moomoo SDK in its own order gateway.

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
