# yfinance

[yfinance](https://github.com/ranaroussi/yfinance) downloads prices from Yahoo Finance with no
account and no key. The project used it in early prototypes; its data is not what any reported
result was computed from. The connector serves the equity venues for checks and for history
that a broker account does not reach.

| | |
|---|---|
| Connector | `yfinance`, `midfreq.data.adapters.yfinance` (alias `yahoo`) |
| Wraps | `midfreq.data.providers.yfinance_provider.YFinanceProvider` |
| Default for | none: `--client yfinance` |
| Historical bars | yes; 1 and 5 minutes, 1 hour, 1 day |
| Instruments | the venue's defaults: Yahoo publishes no lot or tick sizes |
| Accounts, orders | no |
| Needs | the `yfinance` extra, and network access |

## Setup

Nothing beyond the extra, which `uv sync` installs:

```bash
uv run python -m midfreq.data fetch --venue XNYS --symbols BRK.B JPM --bar 1-DAY --start 2020-01-01 --client yfinance --dry-run
```

```text
would fetch BRK.B.XNYS-1-DAY-LAST-EXTERNAL  (native symbol BRK-B)
would fetch JPM.XNYS-1-DAY-LAST-EXTERNAL  (native symbol JPM)
```

## Symbology

Yahoo writes Hong Kong codes as four digits with a suffix, other markets with their own
suffixes, and US share classes with a dash. The rules are per venue in `config/venues.json`:

```python
from midfreq.data.adapters import default_registry

yahoo = default_registry().create("yfinance")
for iid in ("00700.XHKG", "09988.XHKG", "BRK.B.XNYS", "D05.XSES", "600519.XSHG", "7203.XTKS"):
    print(f"{iid:12} -> {yahoo.to_native(iid)}")
print(yahoo.from_native("0700.HK"), yahoo.from_native("AAPL"), yahoo.from_native("BRK-B"))
print({k: (v.days if v else None) for k, v in yahoo.capabilities.max_history.items()})
```

```text
00700.XHKG   -> 0700.HK
09988.XHKG   -> 9988.HK
BRK.B.XNYS   -> BRK-B
D05.XSES     -> D05.SI
600519.XSHG  -> 600519.SS
7203.XTKS    -> 7203.T
00700.XHKG AAPL.XNAS BRK.B.XNYS
{'1-MINUTE': 30, '5-MINUTE': 60, '1-HOUR': 730, '1-DAY': None}
```

`from_native` finds the venue from the suffix, and places an unsuffixed US ticker on XNAS or
XNYS with the listing table in `config/venues.json`.

<!-- venues-table: yfinance -->

## Bars and history limits

| Interval | History Yahoo serves | Requested in windows of |
|---|---|---|
| 1 minute | the last 30 days | 7 days |
| 5 minutes | the last 60 days | 59 days |
| 1 hour | the last 730 days | 365 days |
| 1 day | no limit | 3,650 days |

A start earlier than the interval's limit is moved forward, with a warning, rather than failing.
Yahoo stamps intraday bars with their **open** and treats the end of a request as exclusive, so
each window is padded by one bar and the result trimmed; the close is derived from the venue's
sessions, so the Hong Kong bar opening at 11:30 closes at 12:00, exactly as moomoo stamps it.
The provider answers a 4-hour request with hourly bars; the connector does not offer `4-HOUR`.

## Rate limits

yfinance reports most failures, rate limits included, by printing them and returning an empty
frame rather than raising. When a download comes back empty, the connector looks up the error
yfinance recorded for the symbol and retries it with backoff if it was a rate limit. Requests are
spaced 0.5 s apart.

## Configuration

`YFinanceConnectorConfig` adds `window_days`, which overrides the per-interval request window
(used in tests), and sets `min_request_interval` to 0.5 s.

## Testing

The contract suite runs the connector against `FakeYFinanceProvider` (in `tests/support/`),
which serves frames indexed by open time with an exclusive end and a maximum window per request,
across a US daylight-saving change. Further tests check that a start beyond the history limit is
moved forward and that daily bars from naive dates get the right close.
