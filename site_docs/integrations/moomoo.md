# Moomoo (OpenD)

[moomoo](https://www.moomoo.com/) (Futu) is the broker the project researched and paper traded
with. Its OpenAPI is reached through **OpenD**, a gateway program that holds the broker login
and serves market data and the trade account over a local socket. Every reported result was
computed from moomoo data, and the live forward test trades on a moomoo paper account.

| | |
|---|---|
| Connector | `moomoo`, `midfreq.data.adapters.moomoo` (alias `futu`) |
| Wraps | `midfreq.data.providers.moomoo_provider.MoomooProvider` and its OpenD quote context |
| Default for | XHKG, XNAS, XNYS, XSES, XSHG, XSHE, XTKS |
| Historical bars | yes; 1, 3, 5, 15 and 30 minutes, 1 hour, 1 day |
| Instruments | yes: board lots from moomoo's market snapshot |
| Accounts | yes, read-only, through OpenD's trade context |
| Orders | not through the connector; paper orders go through `midfreq.live.order_gateway.MoomooPaperGateway` |
| Needs | the `moomoo` extra (the `moomoo-api` SDK), and OpenD running and logged in |

## Setup

1. Install OpenD from moomoo and log in with the moomoo account. It listens on
   `127.0.0.1:11111` by default.
2. The `moomoo` entry of [`config/connections.yaml`](repo:config/connections.yaml) points at it:

    ```yaml
    moomoo:
      host: 127.0.0.1          # Moomoo OpenD gateway
      port: 11111
      trading_env: simulate    # simulate (paper) | real
      security_firm: FUTUSECURITIES
    ```

    Any key can be overridden with an environment variable, for example `MIDFREQ_MOOMOO_PORT=11112`.
    OpenD holds the login, so the framework needs no moomoo credentials.
3. Check what a fetch would do without making one:

    ```bash
    uv run python -m midfreq.data fetch --venue XHKG --symbols 00700 00005 --bar 1-HOUR --start 2024-01-01 --dry-run
    ```

    ```text
    would fetch 00700.XHKG-1-HOUR-LAST-EXTERNAL  (native symbol HK.00700)
    would fetch 00005.XHKG-1-HOUR-LAST-EXTERNAL  (native symbol HK.00005)
    ```

## Symbology

moomoo writes a symbol as `<market>.<code>`. The connector takes the market from the venue's
settings, and places a US symbol on XNAS or XNYS with the `listings` table in
`config/venues.json`, since moomoo's `US` market does not say which exchange lists a stock:

```python
from midfreq.data.adapters import default_registry

moomoo = default_registry().create("moomoo")          # nothing connects until data is requested
for iid in ("00700.XHKG", "AAPL.XNAS", "BRK.B.XNYS", "600519.XSHG", "D05.XSES", "7203.XTKS"):
    print(f"{iid:12} -> {moomoo.to_native(iid)}")
print(moomoo.from_native("US.AAPL"), moomoo.from_native("US.JPM"))   # the listing table picks the venue
print(moomoo.capabilities.intervals)
```

```text
00700.XHKG   -> HK.00700
AAPL.XNAS    -> US.AAPL
BRK.B.XNYS   -> US.BRK.B
600519.XSHG  -> SH.600519
D05.XSES     -> SG.D05
7203.XTKS    -> JP.7203
AAPL.XNAS JPM.XNYS
('1-MINUTE', '3-MINUTE', '5-MINUTE', '15-MINUTE', '30-MINUTE', '1-HOUR', '1-DAY')
```

<!-- venues-table: moomoo -->

## Bars

- **Paging.** Requests ask for up to 1,000 bars a page (`max_count`) and follow OpenD's
  `page_req_key` to the end. Each page is retried on its own, so a rate limit part-way through a
  download cannot truncate the history.
- **Prices.** Forward-adjusted (`autype="qfq"`), as the price cache was downloaded.
- **Time.** moomoo stamps a bar with its **close** in the exchange's local time (a Hong Kong bar
  stamped 10:30 covers 09:30 to 10:30). The connector reads the stamp in the venue's timezone, so
  `ts_event` is the true UTC close; a daily bar, stamped with its trading date, gets the
  session's close.
- **Intervals.** Only what OpenD serves natively. The provider answers a 4-hour request with
  hourly bars; the connector refuses `4-HOUR` with `UnsupportedIntervalError` instead.

## Instruments

`instrument_provider.load(ids)` reads each stock's board lot from moomoo's market snapshot, in
batches of 400 codes (100 shares for Tencent, 400 for HSBC); a stock the snapshot does not cover
gets the venue's default lot. `load_all(venue)` lists every stock moomoo has on the venue.

## Rate limits and quotas

| Limit | Handling |
|---|---|
| Frequency: about 60 historical requests per 30 seconds | requests are spaced 0.5 s apart (`min_request_interval`), and a frequency-limit error is retried with backoff |
| Historical candle quota, metered by distinct symbol over a rolling period | an exhausted quota raises `QuotaExceededError`, which is not retried; the live engine warms up from the price cache for this reason |
| Live candle subscriptions: 100 symbols for the account | not the connector's concern: the Hong Kong session is paused while the S&P 100 forward test runs, because the two together would exceed it |

## Configuration

`MoomooConnectorConfig`, in addition to the [shared settings](index.md#common-configuration):

| Setting | Default | Meaning |
|---|---|---|
| `host`, `port` | from `connections.yaml`, else `127.0.0.1`, `11111` | OpenD's address |
| `max_count` | `1000` | bars per page, OpenD's maximum |
| `autype` | `"qfq"` | price adjustment |
| `min_request_interval` | `0.5` | seconds between requests |

## Accounts

`account_client(market="US")` returns a `MoomooAccountClient`, a read-only view of the trade
account of one market through OpenD. It calls only `accinfo_query`, `position_list_query`,
`order_list_query` and `history_order_list_query`, and never unlocks the account, because reading
needs no unlock. The SDK's trade-context constructor retries a refused connection forever, so the
client first probes OpenD's port (1.5 s) and abandons a handshake that does not complete, which
lets `available()` answer within seconds when OpenD is down. Order times, which OpenD reports as
local wall-clock strings, are returned in UTC.

| Option | Default | Meaning |
|---|---|---|
| `market` | `"US"` | `US` or `HK` |
| `trading_env` | from `connections.yaml` (`simulate`) | the paper or the real account; `real` only when set explicitly |
| `acc_id` | `0` | the account to read; 0 leaves the choice to OpenD, which takes its first account for the market and environment |
| `timezone` | the market's venue | the timezone of OpenD's order times |

The configured accounts are `moomoo-us` and `moomoo-hk` in the `accounts` section of
`config/connections.yaml`; the trading VM publishes both as snapshots for the hosted dashboard
([Broker snapshots](../concepts/live_trading.md#broker-snapshots)).

## Paper trading

Orders do not go through the connector. The live engine's `MoomooPaperGateway` places them in
moomoo's paper environment as marketable limit orders, since paper trading does not support true
market orders, and books only confirmed fills ([Live trading](../concepts/live_trading.md)).
The fee the backtests use for Hong Kong, 0.16% per side, was calibrated against these paper
fills.

## Testing

The contract suite runs the connector against `FakeMoomooContext` (in `tests/support/`), which
serves moomoo's own format: local close times as strings, pages with a `page_req_key`, and a
frequency-limit error on request. Two cases cover Hong Kong (with the lunch break) and the US
across a daylight-saving change; further tests check that an exhausted quota is not retried and
that an error never returns a partial history. No test connects to OpenD.
