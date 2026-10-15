# Interactive Brokers

[Interactive Brokers](https://www.interactivebrokers.com/) is reached through Trader Workstation
(TWS) or IB Gateway, with the [ib_insync](https://github.com/erdewit/ib_insync) library. The
connector fetches historical bars and instrument definitions for the equity venues; it is not the
default for any venue, so it is chosen explicitly.

| | |
|---|---|
| Connector | `ibkr`, `midfreq.data.adapters.ibkr` (aliases `ib`, `interactive_brokers`) |
| Wraps | `midfreq.data.providers.ib_provider.IBProvider`, through a subclass that sets each contract from the venue |
| Default for | none: `--client ibkr`, or `registry.for_venue(venue, "ibkr")` |
| Historical bars | yes; 1, 5, 15 and 30 minutes, 1 hour, 4 hours, 1 day |
| Instruments | yes: the price increment from IB's contract details; board lots from the venue |
| Accounts, orders | no |
| Needs | the `ibkr` extra (`ib_insync`), and TWS or IB Gateway running and logged in, with API access enabled |

## Setup

1. Start TWS or IB Gateway, log in, and enable API connections in its settings.
2. The `ibkr` entry of [`config/connections.yaml`](repo:config/connections.yaml) gives its address
   and the client id the connector uses:

    ```yaml
    ibkr:
      host: 127.0.0.1          # TWS / IB Gateway
      port: 7497               # 7497 TWS paper, 7496 TWS live, 4002 Gateway paper, 4001 Gateway live
      client_id: 1
      trading_env: paper       # paper | live
    ```

    Override any key with `MIDFREQ_IBKR_<KEY>`, for example `MIDFREQ_IBKR_PORT=4002`.
3. Check what a fetch would do:

    ```bash
    uv run python -m midfreq.data fetch --venue XHKG --symbols 700 --bar 1-DAY --start 2024-01-01 --client ibkr --dry-run
    ```

    ```text
    would fetch 00700.XHKG-1-DAY-LAST-EXTERNAL  (native symbol 700)
    ```

    The fetch command zero-pads a Hong Kong code to five digits, so `700` and `00700` name the
    same instrument.

ib_insync starts an asyncio event loop when it is imported, so the connector imports it only
when it first connects.

## Symbology and contracts

The provider the connector wraps built every contract as a Nasdaq stock in US dollars. The
connector builds each from the venue's settings instead: exchange, currency, primary exchange,
and how IB spells the code (Hong Kong codes without leading zeros, US share classes with a space):

```python
from midfreq.data.adapters import default_registry

ibkr = default_registry().create("ibkr")              # nothing connects until data is requested
for iid in ("00700.XHKG", "AAPL.XNAS", "BRK.B.XNYS", "D05.XSES"):
    print(f"{iid:11} -> {ibkr.contract_spec(iid)}")
print(ibkr.from_native("700", "XHKG"), ibkr.capabilities.intervals)
```

```text
00700.XHKG  -> {'symbol': '700', 'exchange': 'SEHK', 'currency': 'HKD', 'primary_exchange': ''}
AAPL.XNAS   -> {'symbol': 'AAPL', 'exchange': 'SMART', 'currency': 'USD', 'primary_exchange': 'NASDAQ'}
BRK.B.XNYS  -> {'symbol': 'BRK B', 'exchange': 'SMART', 'currency': 'USD', 'primary_exchange': 'NYSE'}
D05.XSES    -> {'symbol': 'D05', 'exchange': 'SGX', 'currency': 'SGD', 'primary_exchange': ''}
00700.XHKG ('1-MINUTE', '5-MINUTE', '15-MINUTE', '30-MINUTE', '1-HOUR', '4-HOUR', '1-DAY')
```

An IB symbol does not name its exchange, so `from_native` needs the venue.

<!-- venues-table: ibkr -->

## Bars

- **Time.** The connector asks for bar times in UTC (`formatDate=2`); the provider's own request
  returned them in the TWS login's timezone and labelled them UTC. IB stamps a bar with its
  **open**, so the connector derives the close from the venue's sessions, clamping a bar that
  runs into a session close (the Hong Kong bar opening at 11:30 closes at 12:00 for lunch).
  Daily bars come back as dates and get the session's close.
- **Windows.** IB caps how long a period one request may cover for each bar size, so history is
  fetched in consecutive windows: 5 days of 1-minute bars, 30 days of 5-minute to 1-hour bars,
  180 days of 4-hour bars, 365 days of daily bars.
- **Data.** Trades (`what_to_show="TRADES"`), regular trading hours only (`use_rth=True`).

## Pacing

IB allows about 60 historical requests in 10 minutes. The connector spaces requests 10 seconds
apart (`min_request_interval`), and a pacing violation, which ib_insync reports through its error
event with an empty result, is retried with backoff.

## Configuration

`IBKRConnectorConfig`, in addition to the [shared settings](index.md#common-configuration):

| Setting | Default | Meaning |
|---|---|---|
| `host`, `port`, `client_id` | from `connections.yaml`, else `127.0.0.1`, `7497`, `1` | TWS or IB Gateway, and the API client id |
| `use_rth` | `True` | regular trading hours only |
| `what_to_show` | `"TRADES"` | the price series IB builds the bars from |
| `min_request_interval` | `10.0` | seconds between requests |
| `chunk_days` | per bar size | days per request; set in tests |

## Limitations

- IB does not publish Hong Kong board lots in its contract details, so instruments get the
  venue's default lot (100 on HKEX).
- No account client and no order routing: the project's broker is moomoo.

## Testing

The contract suite runs the connector against `FakeIB` (in `tests/support/`), which serves bar
objects stamped with their open in UTC, in windows by duration, and can answer with a pacing
violation. Further tests check that the contract follows the venue and that `from_native`
refuses to guess a venue. No test connects to TWS.
