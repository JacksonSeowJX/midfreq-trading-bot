# Binance

[Binance](https://www.binance.com/) spot market data, read from Binance's public REST API with
`requests`. The connector needs no SDK and no API key, and is the default for the `BINANCE`
venue.

| | |
|---|---|
| Connector | `binance`, `midfreq.data.adapters.binance` |
| Implements | the public REST API directly: `GET /api/v3/klines` and `GET /api/v3/exchangeInfo` |
| Default for | BINANCE |
| Historical bars | yes; from 1 second to 1 week |
| Instruments | yes: tick size, size step, minimum quantity and minimum notional from `exchangeInfo` |
| Accounts, orders | no (an account at Binance can be read through the [ccxt](ccxt.md) connector) |
| Needs | network access only |

## Setup

The base URL is the `binance` entry of [`config/connections.yaml`](repo:config/connections.yaml)
(`https://api.binance.com`), or the venue's `base_url` in `config/venues.json` when the
connections file has none. The entry also names the variables of an API key,
`BINANCE_API_KEY` and `BINANCE_API_SECRET`, which public market data does not need.

```bash
uv run python -m midfreq.data fetch --venue BINANCE --symbols BTCUSDT ETHUSDT --bar 1-HOUR --start 2024-01-01 --dry-run
```

```text
would fetch BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL  (native symbol BTCUSDT)
would fetch ETHUSDT.BINANCE-1-HOUR-LAST-EXTERNAL  (native symbol ETHUSDT)
```

## Symbology

The instrument id is Binance's own symbol on the `BINANCE` venue: `BTCUSDT.BINANCE`.

```python
from midfreq.data.adapters import default_registry

binance = default_registry().for_venue("BINANCE")      # the venue's default connector
print(type(binance).__name__, binance.base_url(), binance.to_native("BTCUSDT.BINANCE"))
print(binance.capabilities.intervals)
```

```text
BinanceConnector https://api.binance.com BTCUSDT
('1-SECOND', '1-MINUTE', '3-MINUTE', '5-MINUTE', '15-MINUTE', '30-MINUTE', '1-HOUR', '2-HOUR', '4-HOUR', '6-HOUR', '8-HOUR', '12-HOUR', '1-DAY', '3-DAY', '1-WEEK')
```

<!-- venues-table: binance -->

## Bars

`/api/v3/klines` returns at most 1,000 bars a call, each stamped with its open time in UTC
milliseconds. The connector pages forward by `startTime` until the window is covered, and sets
`ts_event` to the open plus the interval, the bar's close. A response that makes no progress ends
the loop rather than repeating it. The bar still forming is never returned.

## Instruments

`exchangeInfo` gives each pair's `PRICE_FILTER.tickSize`, `LOT_SIZE.stepSize` and `minQty`, and
`NOTIONAL` (or the older `MIN_NOTIONAL`) `minNotional`. Instruments are `CurrencyPair`s quoted in
the pair's quote asset. On the venue engine, an order's quantity is rounded down to the size
step, and a fill below the minimum notional is refused, as Binance would refuse it.

## Rate limits

| Response | Handling |
|---|---|
| HTTP 429 (request weight exceeded) | retried after the server's `Retry-After` |
| HTTP 418 (IP banned for ignoring 429s) | retried after `Retry-After` |
| any other 4xx or 5xx | raised as `ConnectorError`, not retried |

Requests are spaced 0.1 s apart (`min_request_interval`), well inside Binance's 6,000 request
weight per minute.

## Configuration

`BinanceConnectorConfig`, in addition to the [shared settings](index.md#common-configuration):

| Setting | Default | Meaning |
|---|---|---|
| `base_url` | `connections.yaml`, then the venue's `base_url` | the REST endpoint |
| `klines_limit` | `1000` | bars per call, Binance's maximum |
| `timeout` | `10.0` | seconds per HTTP request |
| `min_request_interval` | `0.1` | seconds between requests |

## Testing

The HTTP transport is a small injectable object with one method, `get(url, params)`. The
contract suite runs the connector against `FakeBinanceTransport` (in `tests/support/`), which
serves kline arrays and `exchangeInfo` and can answer HTTP 429; further tests check that a 418
honours `Retry-After`, that client errors are not retried, the `exchangeInfo` request format,
and that a bar still forming is dropped. No test reaches the network.
