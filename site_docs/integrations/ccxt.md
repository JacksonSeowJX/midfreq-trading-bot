# ccxt

[ccxt](https://github.com/ccxt/ccxt) gives one interface to over a hundred crypto exchanges. The
`ccxt` connector maps that interface onto the framework's, and serves Coinbase, Kraken and OKX
by default; any other exchange ccxt supports can be added as a venue in `config/venues.json`.
It also reads an exchange account, read-only, with a key from the environment.

| | |
|---|---|
| Connector | `ccxt`, `midfreq.data.adapters.ccxt` |
| Wraps | the `ccxt` library (an optional dependency) |
| Default for | COINBASE, KRAKEN, OKX; also configured for BINANCE |
| Historical bars | yes; the intervals each exchange lists, so capabilities are per venue |
| Instruments | yes: price and amount precision, minimum amount and cost from the exchange's markets |
| Accounts | yes, read-only, with a key from the environment |
| Orders | no |
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

## Testing

The contract suite runs the connector against `FakeCcxtExchange` (in `tests/support/`), which
serves OHLCV arrays and markets and can raise `RateLimitExceeded`, on the KRAKEN venue. Further
tests cover `native_id` symbology, the logged history cap, a missing ccxt, and building a real
exchange object offline; the account tests use a fake exchange factory and check that only read
methods are called. No test reaches the network.
