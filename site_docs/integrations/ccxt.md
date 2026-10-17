# ccxt

[ccxt](https://github.com/ccxt/ccxt) gives one interface to over a hundred crypto exchanges. The
`ccxt` connector maps that interface onto the framework's, and serves Coinbase, Kraken and OKX
by default; any other exchange ccxt supports can be added as a venue in `config/venues.json`.
It also reads an exchange account, read-only, with a key from the environment, streams live
bars and quotes, and routes orders through an execution client, OKX first.

| | |
|---|---|
| Connector | `ccxt`, `midfreq.data.adapters.ccxt` |
| Wraps | the `ccxt` library (an optional dependency) |
| Default for | COINBASE, KRAKEN, OKX; also configured for BINANCE |
| Historical bars | yes; the intervals each exchange lists, so capabilities are per venue |
| Instruments | yes: price and amount precision, minimum amount and cost from the exchange's markets |
| Accounts | yes, read-only, with a key from the environment |
| Live data | yes: ccxt.pro websockets, else REST polling |
| Orders | yes: `execution_client(exchange=...)`, in the exchange's sandbox or demo environment unless the connection says `live` |
| Needs | the `crypto` extra (`ccxt`), and network access |

Without ccxt installed the package still imports; creating an exchange then raises a clear
error. `uv sync` installs it.

## Symbology

ccxt's unified symbols (`BTC/USDT`) contain a slash, which cannot appear in a catalog path, so
instrument ids spell a pair in one of two ways, chosen per venue by `symbology`:

| `symbology` | Instrument id | ccxt symbol | Used for |
|---|---|---|---|
| `dash` | `BTC-USD.COINBASE`, `BTC-USDT.OKX` | `BTC/USD`, `BTC/USDT` | COINBASE, KRAKEN, OKX: the dash is Coinbase's and OKX's own product id |
| `native_id` | `BTCUSDT.BINANCE` | looked up in the exchange's markets | BINANCE, whose own ids have no separator |

```python
from midfreq.data.adapters import default_registry

registry = default_registry()
ccxt = registry.create("ccxt")                         # exchange objects are made offline
for iid in ("BTC-USD.COINBASE", "BTC-USD.KRAKEN", "BTC-USDT.OKX"):
    venue = registry.venues.get(iid.rsplit(".", 1)[1])
    print(f"{iid:16} -> {ccxt.to_native(iid):9} on {ccxt.exchange_id(venue.venue):8} "
          f"{ccxt.bars_per_request(venue):3} bars a page, {ccxt.capabilities_for(venue).intervals}")
print(ccxt.from_native("ETH/USDT", "OKX"))
```

```text
BTC-USD.COINBASE -> BTC/USD   on coinbase 500 bars a page, ('1-MINUTE', '5-MINUTE', '15-MINUTE', '30-MINUTE', '1-HOUR', '2-HOUR', '6-HOUR', '1-DAY')
BTC-USD.KRAKEN   -> BTC/USD   on kraken   720 bars a page, ('1-MINUTE', '5-MINUTE', '15-MINUTE', '30-MINUTE', '1-HOUR', '4-HOUR', '1-DAY', '1-WEEK', '2-WEEK')
BTC-USDT.OKX     -> BTC/USDT  on okx      100 bars a page, ('1-MINUTE', '3-MINUTE', '5-MINUTE', '15-MINUTE', '30-MINUTE', '1-HOUR', '2-HOUR', '4-HOUR', '6-HOUR', '12-HOUR', '1-DAY', '1-WEEK')
ETH-USDT.OKX
```

The intervals are those each exchange object lists in ccxt 4.5.88, the pinned version; a
`native_id` symbol needs the exchange's market list, which is loaded over the network. A unified
symbol does not name its exchange, so `from_native` needs the venue.

<!-- venues-table: ccxt -->

The exchange for a venue is its `exchange_id`; a venue without one uses `default_exchange` from
the `ccxt` entry of `config/connections.yaml` (`kraken`).

```bash
uv run python -m midfreq.data fetch --venue KRAKEN --symbols BTC-USD --bar 1-HOUR --start 2024-06-01 --dry-run
```

```text
would fetch BTC-USD.KRAKEN-1-HOUR-LAST-EXTERNAL  (native symbol BTC/USD)
```

## Bars

`fetch_ohlcv(symbol, timeframe, since, limit)` pages forward by open time, `limit` bars at a time
(the venue's `max_bars_per_request`, else 500), and `ts_event` is the open plus the interval.
Some exchanges cap history: Kraken's OHLC endpoint returns only the most recent 720 bars of any
interval. When the first page starts later than the requested start, the gap is logged with the
dates, rather than hidden.

## Instruments

From the exchange's markets (`load_markets`, once per venue): the price and amount precision,
read as a tick size or as decimal places according to the exchange's precision mode, the
minimum amount and the minimum cost (notional), and ccxt's maker and taker fees as information.
`load_all(venue)` lists the spot markets.

## Rate limits

ccxt's own limiter (`enableRateLimit`) spaces requests. `RateLimitExceeded` and `DDoSProtection`
are retried with backoff.

## Configuration

`CcxtConnectorConfig`, in addition to the [shared settings](index.md#common-configuration):

| Setting | Default | Meaning |
|---|---|---|
| `options` | `{}` | passed to every ccxt exchange constructor |
| `default_bars_per_request` | `500` | the page size for a venue without `max_bars_per_request` |
| `bars_per_request` | none | overrides every venue's page size |
| `stream_mode` | `"auto"` | live streams: `watch` (ccxt.pro), `poll` (REST), or `auto` (watch where available) |
| `quote_source` | `"auto"` | live quotes from the `ticker` or the order `book` |
| `stream_poll_interval` | `0` | seconds between polls in poll mode; 0 follows the bar interval (2 s for quotes) |
| `stream_reconnect_delay`, `stream_max_reconnect_delay` | `1`, `30` | the first wait after a stream error, doubled up to the maximum |

## Accounts

`account_client(exchange="okx", secrets=[...])` returns a `CcxtAccountClient`: balances,
derivative positions and orders, read with a read-only API key.

- **Credentials** come only from the environment (or the untracked `.env`), under the variable
  names the account entry lists in `secrets`. Each name is matched to a ccxt credential by its
  ending: `..._SECRET` or `..._SECRET_KEY` is the secret, `..._PASSPHRASE` or `..._PASSWORD` the
  password, `..._UID` the uid, and any other `..._KEY` the API key. The client calls only ccxt's
  `fetch_*` and `load_markets` methods, but the guarantee is the key: create one that cannot
  trade or withdraw.
- **Valuation** is in the account's quote currency, by default the currency of the venue whose
  `exchange_id` is the exchange (USDT for OKX). Cash is the quote currency's balance; every other
  asset is a spot holding priced at the last trade of `<asset>/<quote>`, or left unpriced (and
  out of the total) when there is no such market. Nothing is converted through a third currency.
  Spot balances carry no cost basis, so their average price and unrealized profit are unknown
  rather than zero.
- **Derivative positions** are listed, in base units and negative when short, but not added to
  the account's total, since their margin and profit are already in the settlement currency's
  balance on exchanges that report equity there (OKX does).
- **Orders** are the open orders and the closed orders since the window's start, merged; how far
  back an exchange's closed-order endpoint reaches is its own (OKX's default covers 7 days of spot
  orders).

To show an OKX account on the dashboard, uncomment its entry in the `accounts` section of
`config/connections.yaml` and put a read-only key in the environment:

```yaml
accounts:
  okx:
    label: OKX
    connector: ccxt
    options: {exchange: okx}
    secrets: [OKX_READ_API_KEY, OKX_READ_API_SECRET, OKX_READ_API_PASSPHRASE]
```

| Option | Default | Meaning |
|---|---|---|
| `exchange` | required | the ccxt exchange id, for example `okx` |
| `quote` | the venue's currency | the currency values are reported in |
| `exchange_options` | `{}` | passed to the exchange constructor after the connector's `options` |
| `timeout_ms` | `5000` | per request |

## Live data

The data client streams through ccxt.pro, on one background asyncio thread per data client:

- **Bars**: `watch_ohlcv`. Each push of a candle becomes a `BarUpdate` with `ts_event` the
  candle's close (its open plus the interval) in true UTC, and `closed` once that close has passed
  when the push arrives.
- **Quotes**: `watch_ticker`, or the top level of `watch_order_book` (with `quote_source: book`,
  or on an exchange without a ticker stream).
- **Without ccxt.pro**, or on an exchange that lacks the watch method, a subscription polls the
  REST API instead (`fetch_ohlcv` for the last two candles, `fetch_ticker`) and pushes what
  changed.
- A network error is retried with a doubling delay; the stream status handler hears `connected`
  at the first push, `disconnected` at the first failure, `reconnected` once every failing
  subscription pushes again, and `error` for a subscription that cannot work (an unknown symbol).
  ccxt sets no subscription limit, so `subscription_limit()` is `None`.

Market data is public, so the streams use the production exchange whatever the environment of
the account's execution client (OKX's demo trading shares the production order book).

## Orders

`execution_client(exchange="okx")` returns a `CcxtExecutionClient`, which implements the
[execution contract](index.md#trading-through-connectors):

```python
client = CcxtConnector().execution_client(exchange="okx")     # paper: OKX demo trading
client.set_event_handler(queue.put)
client.connect()
client.submit_order(order)                                   # returns at once
```

- **Paper unless told otherwise.** `paper`, the default, is the exchange's sandbox or demo
  environment, through ccxt's sandbox mode; an exchange with no sandbox cannot paper trade, and
  the client refuses to start rather than trade for real. `live` is accepted only when the `ccxt`
  entry of `config/connections.yaml` (or `MIDFREQ_CCXT_TRADING_ENV`) says `trading_env: live`.
- **Credentials** come only from the environment or the untracked `.env`, under separate names
  for paper and live (by default `<EXCHANGE>_PAPER_API_KEY`, `..._API_SECRET` and, for OKX,
  `..._API_PASSPHRASE`), so a live key never reaches a paper session.
- **Orders.** The amount is truncated and the price rounded to the market's precision. The client
  order id goes in the exchange's own client-id field (OKX `clOrdId`), made to fit its rule (OKX:
  at most 32 letters and digits) with a hash of the original, so it stays unique.
- **Updates.** With ccxt.pro (watch mode), `watch_orders` and `watch_my_trades` run on a
  background thread, and the open orders are re-read over REST after an error, when the stream
  resumes and every `resync_interval` (30 seconds). Without it (poll mode, or
  `updates: poll`), `fetch_order` and `fetch_my_trades` are polled for the open orders every
  `poll_interval` (2 seconds). Either way an order's trades are fetched before its terminal
  event, so a fill always precedes the cancel of the rest.
- **Fees.** A trade's fee is reported in the currency the exchange charged it in (OKX takes a spot
  buy's fee in the base asset), which the trading node's position ledger converts; a trade with
  no fee is tagged `commission_unknown`.
- **Lost replies.** A send whose reply is lost to a network error is looked up by its client order
  id until the exchange confirms it, and rejected if it is still unknown after
  `unconfirmed_timeout` (30 seconds). A refused cancel is followed by a re-read of the order, then
  `OrderCancelRejected`.

| Option | Default | Meaning |
|---|---|---|
| `exchange` | `"okx"` | the ccxt exchange id |
| `environment` | `connections.yaml`'s `trading_env`, else `paper` | `paper` or `live` |
| `secrets` | from the exchange and environment | the environment variables holding the key |
| `updates` | `"auto"` | `watch`, `poll`, or `auto` (watch where ccxt.pro supports it) |
| `poll_interval`, `resync_interval` | `2`, `30` | seconds |
| `unconfirmed_timeout` | `30` | seconds before an order the exchange never confirmed is rejected |

## Testing

The contract suite runs the connector against `FakeCcxtExchange` (in `tests/support/`), which
serves OHLCV arrays and markets and can raise `RateLimitExceeded`, on the KRAKEN venue. Further
tests cover `native_id` symbology, the logged history cap, a missing ccxt, and building a real
exchange object offline; the account tests use a fake exchange factory and check that only read
methods are called. The execution client runs the execution contract suite twice against a fake
exchange (`tests/support/fake_ccxt.py`), once with websocket updates and once polling. No test
reaches the network.
