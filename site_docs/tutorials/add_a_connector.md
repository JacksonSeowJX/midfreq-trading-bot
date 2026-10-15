# Add a connector

This tutorial adds a connector for a new data source, makes the framework use it for a venue,
fetches bars through it into the data catalog, and puts it through the contract tests every
connector passes. The source is a hypothetical REST API, so that the whole tutorial runs offline
against a stand-in for it; the steps are the ones the five built-in connectors follow.

**Needs:** the [installation](../getting_started/installation.md). No price cache and no network.

## The contract

A connector is everything source-specific in one place ([Data and connectors](../concepts/data.md#connectors)):
its configuration and capabilities, the spelling of its symbols, and two clients. The
`DataClient` base class checks a request against the connector's capabilities and normalizes
whatever the subclass returns: sorted, without duplicates, inside the requested window, and only
bars that have closed. A subclass supplies:

| Piece | What it must do |
|---|---|
| `Connector.name`, `config_class`, `capabilities` | the name the registry knows it by, its settings, the intervals it serves |
| `to_native`, `from_native` | translate an `InstrumentId` to the source's symbol and back |
| `DataClient._request_bars(bar_type, venue, start, end)` | fetch every page and return `Bar`s whose `ts_event` is the bar's **close in UTC** |
| `InstrumentProvider._load(instrument_ids)` | return each instrument's trading rules |
| `is_rate_limit` (optional) | recognize the source's own "slow down" signal, so that `call` retries it |

## The source

The hypothetical source answers `GET /bars?symbol=AAPL:US&interval=60&from=<ms>&to=<ms>&limit=<n>`
with a page of bars and a cursor to the next page:

```json
{"bars": [{"t": 1709908200000, "o": 100.1, "h": 100.5, "l": 99.9, "c": 100.3, "v": 1000.0}], "next": 5}
```

`t` is the bar's **open** time in UTC milliseconds. Symbols are `<code>:<country>`, with Hong
Kong codes written without leading zeros (`700:HK`). A client that calls too often gets HTTP 429
with a `Retry-After` header.

## 1. Write the connector

Save this as `example_rest.py`:

```python
"""
A connector for a REST source that serves bars as JSON.

The source answers GET /bars?symbol=AAPL:US&interval=60&from=<ms>&to=<ms>&limit=<n>[&cursor=<c>]
with {"bars": [{"t": <open time, UTC ms>, "o": ..., "h": ..., "l": ..., "c": ..., "v": ...}],
"next": <cursor or null>}, and with HTTP 429 and a Retry-After header when called too often.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from midfreq.core.model.identifiers import InstrumentId, as_instrument_id, as_venue
from midfreq.data.adapters.base import (
    Connector, ConnectorCapabilities, ConnectorConfig, ConnectorError, DataClient,
    InstrumentProvider, RateLimitError, bars_from_frame, utc_dates_or_local,
)

INTERVALS = {"1-HOUR": "60", "1-DAY": "D"}          # the framework's interval -> the source's


@dataclass
class ExampleRestConfig(ConnectorConfig):
    base_url: str = "https://api.example.com"
    page_size: int = 500
    min_request_interval: float = 0.2                # the source allows five requests a second


class ExampleRestConnector(Connector):
    name = "examplerest"
    config_class = ExampleRestConfig

    def __init__(self, config=None, venues=None, transport=None, **kwargs):
        """`transport.get(url, params)` returns (status, headers, payload)."""
        super().__init__(config, venues, **kwargs)
        self.transport = transport

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(intervals=tuple(INTERVALS), max_bars_per_request=self.config.page_size)

    # symbology: 00700.XHKG <-> 700:HK, AAPL.XNAS <-> AAPL:US (the rules are in config/venues.json)
    def to_native(self, instrument_id) -> str:
        iid = as_instrument_id(instrument_id)
        opts = self.venue_spec(iid.venue).connector_options(self.name)
        symbol = str(iid.symbol)
        if opts.get("strip_leading_zeros"):
            symbol = symbol.lstrip("0") or "0"
        return f"{symbol}:{opts['country']}"

    def from_native(self, native: str, venue=None) -> InstrumentId:
        if venue is None:
            raise ValueError("a country code can mean several venues (US: XNAS or XNYS); pass the venue")
        v = as_venue(venue)
        opts = self.venue_spec(v).connector_options(self.name)
        symbol, _, country = native.partition(":")
        if country != opts["country"]:
            raise ValueError(f"{native!r} is not a symbol of {v}")
        if opts.get("strip_leading_zeros"):
            symbol = symbol.zfill(int(opts.get("code_width", 5)))
        return InstrumentId.of(symbol, v)

    def get(self, path: str, params: dict):
        """One request: spaced by the throttle, and retried with backoff on HTTP 429."""
        def fetch():
            status, headers, payload = self.transport.get(self.config.base_url + path, params)
            if status == 429:
                raise RateLimitError(f"HTTP 429 from {path}", retry_after=float(headers.get("Retry-After", 1)))
            if status >= 400:
                raise ConnectorError(f"{self.name} {path}: HTTP {status}")
            return payload
        return self.call(fetch, path)

    def _create_data_client(self) -> DataClient:
        return ExampleRestDataClient(self, now_ns=self._now_ns)

    def _create_instrument_provider(self) -> InstrumentProvider:
        return ExampleRestInstrumentProvider(self)


class ExampleRestDataClient(DataClient):
    def _request_bars(self, bar_type, venue, start, end):
        conn = self.connector
        # The source stamps a bar with its OPEN, so ask from one interval before `start`.
        params = {"symbol": conn.to_native(bar_type.instrument_id),
                  "interval": INTERVALS[bar_type.spec.interval],
                  "from": (start - bar_type.spec.timedelta).value // 1_000_000,
                  "to": end.value // 1_000_000, "limit": conn.config.page_size}
        rows = []
        while True:
            page = conn.get("/bars", params)
            rows += page["bars"]
            if not page.get("next"):
                break
            params = {**params, "cursor": page["next"]}
        if not rows:
            return []
        frame = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close",
                                                   "v": "volume"})
        opens = pd.DatetimeIndex(pd.to_datetime(frame["t"], unit="ms", utc=True))
        if bar_type.spec.is_intraday:
            closes = venue.bar_close_from_open(opens, bar_type.spec)   # clamped to the session close
        else:
            closes = venue.daily_close(utc_dates_or_local(opens, venue))
        return bars_from_frame(frame, bar_type, closes)


class ExampleRestInstrumentProvider(InstrumentProvider):
    """The source publishes no trading rules, so instruments get the venue's defaults."""

    def _load(self, instrument_ids):
        out = []
        for iid in instrument_ids:
            instrument = self.connector.venue_spec(iid.venue).default_instrument(str(iid.symbol))
            instrument.info["native_symbol"] = self.connector.to_native(iid)
            out.append(instrument)
        return out
```

Three things carry the weight:

- **Time.** The source stamps a bar with its open; the framework stamps it with its close.
  `VenueSpec.bar_close_from_open` adds the interval and clamps a bar that runs into a session
  close to that close, so the New York bar opening at 15:30 closes at 16:00, exactly as moomoo
  stamps it, and the same bar has the same `ts_event` whichever connector fetched it.
- **Paging.** `_request_bars` follows the cursor until the source has no more pages. Pages may
  overlap at their edges; the base class keeps one bar per close time.
- **Rate limits.** `get` turns HTTP 429 into a `RateLimitError` carrying `Retry-After`, and
  `Connector.call` waits and retries, with exponential backoff when no wait is given. The
  throttle spaces requests by `min_request_interval` before they are sent.

## 2. Declare the venues it serves

A connector serves a venue only if the venue lists it, with the venue's settings for it. In
[`config/venues.json`](repo:config/venues.json), add an `examplerest` entry to the `connectors`
object of each venue:

```json
{
  "XHKG": {"examplerest": {"country": "HK", "strip_leading_zeros": true, "code_width": 5}},
  "XNAS": {"examplerest": {"country": "US"}},
  "XNYS": {"examplerest": {"country": "US"}}
}
```

Symbology rules belong here rather than in code, so that a new venue for an existing source is
a configuration change. A request for a venue that does not list the connector raises
`UnsupportedVenueError`.

## 3. Use it

The stand-in source below serves synthetic hourly bars of one New York stock, over a weekend on
which US clocks moved to daylight saving time, and refuses the first request with HTTP 429:

```python
import numpy as np
import pandas as pd


class FakeExampleSource:
    """Plays the REST source offline: synthetic hourly bars of one New York stock, stamped with
    their open in UTC ms, served in pages; the first request is refused with HTTP 429."""

    def __init__(self, first_day: str, last_day: str):
        opens = []
        for day in pd.bdate_range(first_day, last_day):
            local = pd.date_range(f"{day:%Y-%m-%d} 09:30", periods=7, freq="h", tz="America/New_York")
            opens += list(local.tz_convert("UTC"))
        closes = 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.004, len(opens))))
        self.bars = [{"t": t.value // 1_000_000, "o": c * 0.999, "h": c * 1.002, "l": c * 0.997,
                      "c": c, "v": 1_000.0} for t, c in zip(opens, closes)]
        self.requests = 0

    def get(self, url, params):
        self.requests += 1
        if self.requests == 1:
            return 429, {"Retry-After": "0.5"}, None
        rows = [b for b in self.bars if params["from"] <= b["t"] <= params["to"]]
        first = int(params.get("cursor", 0))
        last = first + params["limit"]
        return 200, {}, {"bars": rows[first:last], "next": last if last < len(rows) else None}
```

Register the connector with a `ConnectorRegistry` and create it for a venue. Extra keyword
arguments reach the connector's constructor, which is how the transport is passed in:

```python
from midfreq.core.model import BarType
from midfreq.data.adapters import ConnectorRegistry

from example_rest import ExampleRestConnector

registry = ConnectorRegistry()                            # the built-in five, plus ours
registry.register("examplerest", ExampleRestConnector)

source = FakeExampleSource("2024-03-08", "2024-03-11")    # US clocks went forward on 10 March
conn = registry.for_venue("XNAS", "examplerest", {"page_size": 5}, transport=source)
print(conn.to_native("AAPL.XNAS"), conn.to_native("00700.XHKG"), conn.from_native("700:HK", "XHKG"))

bars = conn.data_client.request_bars(BarType.of("AAPL.XNAS", "1-HOUR"), "2024-03-08", "2024-03-12")
print(len(bars), "bars in", source.requests, "requests")
for bar in bars[5:9]:
    print(bar.timestamp, round(bar.close, 4))
```

```text
AAPL:US 700:HK 00700.XHKG
examplerest /bars rate-limited (HTTP 429 from /bars); retry 1/5 in 0.5s
14 bars in 4 requests
2024-03-08 20:30:00+00:00 100.6204
2024-03-08 21:00:00+00:00 100.4045
2024-03-11 14:30:00+00:00 100.6382
2024-03-11 15:30:00+00:00 100.785
```

The second line is the retry's warning, logged to standard error. The 14 bars came in three
pages of at most five, after the refused request. Friday's last bar, opening at 15:30 New York
time, closes at the 16:00 close (21:00 UTC under standard time); Monday's first closes at 10:30,
now 14:30 UTC.

## 4. Fetch into the catalog

`fetch_bars`, the function behind `python -m midfreq.data fetch`, resolves the venue's
connector through the registry, loads the instrument definitions, fetches each symbol and
writes the bars to the catalog under their bar type:

```python
import tempfile

from midfreq.data.persistence import ParquetDataCatalog
from midfreq.data.service import fetch_bars

catalog = ParquetDataCatalog(tempfile.mkdtemp())         # a throwaway catalog
(fetched,) = fetch_bars("XNAS", ["AAPL"], "1-HOUR", "2024-03-08", "2024-03-11", connector="examplerest",
                        catalog=catalog, registry=registry,
                        transport=FakeExampleSource("2024-03-08", "2024-03-11"))
print(fetched.bar_type, fetched.native_symbol, fetched.bars, "bars,", fetched.first, "to", fetched.last)
print([str(t) for t in catalog.bar_types()], [str(i.id) for i in catalog.instruments()])
```

```text
examplerest /bars rate-limited (HTTP 429 from /bars); retry 1/5 in 0.5s
AAPL.XNAS-1-HOUR-LAST-EXTERNAL AAPL:US 14 bars, 2024-03-08 15:30:00+00:00 to 2024-03-11 20:00:00+00:00
['AAPL.XNAS-1-HOUR-LAST-EXTERNAL'] ['AAPL.XNAS']
```

A bare end date means the whole of that day. The bars in the catalog are ready for the venue
engine ([Backtesting](../concepts/backtesting.md#the-venue-engine)).

## 5. Make it a built-in connector

To ship the connector with `midfreq-data`:

1. Move the module to `packages/midfreq-data/src/midfreq/data/adapters/examplerest/connector.py`,
   with an `__init__.py` that re-exports its classes, as the other connectors' packages do.
2. Add it to `BUILTIN_CONNECTORS` in `midfreq/data/adapters/registry.py`, by import path, so
   that importing `midfreq.data` does not import it (or any SDK it needs) until it is used:
   `"examplerest": "midfreq.data.adapters.examplerest.connector:ExampleRestConnector"`.
3. Keep the venue settings of step 2 in `config/venues.json`. To make it a venue's default, set
   that venue's `default_connector`; otherwise it is chosen with `--client examplerest`.
4. If it needs a vendor SDK, add an optional extra to `packages/midfreq-data/pyproject.toml` and
   import the SDK inside the connector, where it is first used.

## 6. Pass the contract tests

[`packages/midfreq-data/tests/test_connector_contract.py`](repo:packages/midfreq-data/tests/test_connector_contract.py)
runs the same seven checks on every connector, each against a fake transport that speaks the
source's own format: symbol round trips, pagination without gaps or duplicates, close times in
true UTC (with a daylight-saving change), the instrument provider's rules, refusal of an
unsupported interval and of an unserved venue, rate-limit retry after a wait, and the
capability flags. Add, above `CASES`, a fake for the source and a builder for the connector:

```python
from midfreq.data.adapters.examplerest.connector import ExampleRestConnector


class FakeExampleTransport:
    """The source's JSON over the test market: open times in UTC ms, pages linked by a cursor."""

    def __init__(self, bars, rate_limit_first=False):
        self.bars, self.rate_limit_first, self.calls = bars, rate_limit_first, []

    def get(self, url, params):
        self.calls.append(dict(params))
        if self.rate_limit_first and len(self.calls) == 1:
            return 429, {"Retry-After": "2"}, None
        rows = [{"t": b.open_utc.value // 1_000_000, "o": b.open, "h": b.high, "l": b.low,
                 "c": b.close, "v": b.volume}
                for b in self.bars if params["from"] <= b.open_utc.value // 1_000_000 <= params["to"]]
        first = int(params.get("cursor", 0))
        last = first + params["limit"]
        return 200, {}, {"bars": rows[first:last], "next": last if last < len(rows) else None}


def _examplerest(truth, rate_limit_first, sleep):
    fake = FakeExampleTransport(truth, rate_limit_first)
    return _track(ExampleRestConnector({**QUIET, "page_size": 5}, transport=fake, sleep=sleep, now_ns=NOW),
                  fake.calls)
```

and a case to the `CASES` list: the instrument and interval to fetch, the market the fake
serves (two New York days across a daylight-saving change), the expected native spellings, an
interval the connector must refuse, the expected instrument rules, and a venue it does not
serve:

```python
    Case("examplerest-XNAS-dst", "AAPL.XNAS", "1-HOUR",
         equity_intraday("America/New_York", US_DST_DAYS, US_HOURS), _examplerest,
         {"AAPL.XNAS": "AAPL:US", "JPM.XNYS": "JPM:US", "00700.XHKG": "700:HK"},
         "5-MINUTE", {"currency": "USD", "lot_size": 1.0}, "BINANCE"),
```

```bash
uv run pytest -q packages/midfreq-data/tests/test_connector_contract.py -k examplerest
```

```text
.......                                                                  [100%]
7 passed, 42 deselected in 0.33s
```

The test's `RecordingSleep` stands in for `time.sleep`, so the retry waits are recorded rather
than slept, and the suite runs in a fraction of a second with no network.

## Reading accounts

A connector can also offer a read-only view of an account, for the dashboard's broker panels:
give it an `account_client(**options, secrets=())` that returns an `AccountClient` calling only
the venue's read methods, and add the account to the `accounts` section of
`config/connections.yaml`. The steps are in
[Data and connectors](../concepts/data.md#account-clients).
