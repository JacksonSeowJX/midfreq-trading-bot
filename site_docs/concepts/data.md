# Data and connectors

Market data enters the framework through **connectors**, one per broker or exchange, which
share a single contract. It is kept in two stores:

| Store | Layout | Written by | Read by |
|---|---|---|---|
| The **price cache**, `data/` | `data/<MARKET>_<CODE>/<timeframe>.parquet` | the download scripts in `scripts/data/`, and the live sessions, which add the bars they received | the candle backtester, the optimizer, the studies, the dashboard, the live sessions' warm-up |
| The **data catalog**, `catalog/` | `catalog/data/bar/<bar_type>/*.parquet` and `catalog/instruments/*.json` | `python -m midfreq.data fetch` and `migrate` | the venue engine |

Every reported result was computed from the price cache. The catalog is the venue engine's
store: keyed by bar type, so bars of different intervals or sources cannot be mixed up, and
stamped in true UTC. Neither folder is in the repository.

Accounts are read through the same connectors, by read-only **account clients**.

## Connectors

<!-- include: docs/ARCHITECTURE.md#connectors strip-title -->

### The registry

`ConnectorRegistry` (`midfreq.data.adapters.registry`) maps a connector's name to a factory and
picks the connector for a venue: `for_venue("XHKG")` creates the venue's `default_connector`
from `config/venues.json`, and an override (`for_venue("XHKG", "yfinance")`, or `--client` on
the command line) must be one of the connectors the venue lists. The five built-in connectors
are registered by import path and imported only when first created, so `import midfreq.data`
never imports the moomoo SDK, ib_insync (which starts an event loop on import) or ccxt. Common
spellings are accepted as aliases: `ib` and `interactive_brokers` for `ibkr`, `futu` for
`moomoo`, `yahoo` for `yfinance`.

```python
from midfreq.data.adapters import default_registry

registry = default_registry()
print(registry.names())
print(registry.default_for("XNAS"))
yahoo = registry.for_venue("XNAS", "yahoo")      # an alias of yfinance
print(type(yahoo).__name__, yahoo.to_native("BRK.B.XNYS"))
```

```text
['binance', 'ccxt', 'ibkr', 'moomoo', 'yfinance']
moomoo
YFinanceConnector BRK-B
```

Each connector has its own page under [Integrations](../integrations/index.md), and
[Add a connector](../tutorials/add_a_connector.md) builds a new one against the contract tests.

<!-- include: packages/midfreq-data/README.md#connection-settings level=3 -->

## The price cache

The price cache is the original store, and the one every study, the dashboard and the live
engine use. It holds one Parquet file per symbol and timeframe, under a folder named after the
symbol as moomoo writes it (`HK.00700` becomes `HK_00700`):

```text
data/HK_00700/1h.parquet      data/HK_00700/1d.parquet      data/US_AAPL/1h.parquet
```

Every file has a `timestamp` index and the columns `open`, `high`, `low`, `close` and `volume`.
The timestamp is the candle's **close** in the exchange's **local** time, stored with a UTC
label: moomoo stamps a bar with its local close time, and the download wrote it with
`pd.to_datetime(..., utc=True)`. A Hong Kong bar that closed at 10:30 HKT is stored as
10:30 UTC. Within one market this shifts every bar by the same offset, so single-market
backtests are unaffected; across markets it orders bars wrongly, which is why the venue engine
re-reads the files in each venue's timezone
([Instruments and venues](instruments_and_venues.md#time)).

There are two ways to read it:

- `DataStorage` (`midfreq.data.storage`) reads and writes it: `load_data`, `save_data`,
  `append_data` (merging on the timestamp) and `latest_common_timestamp`, which anchors a study
  to the data rather than to the day it is run. Its constructor creates the folder, and
  `load_data` creates a symbol's folder if it is missing, so point it at a copy when a run must
  not touch the cache.
- `LegacyDataReader` (`midfreq.data.persistence`) only reads, and returns bars stamped with their
  true UTC close, with the same date filtering as the backtester:

```python
from midfreq.core.paths import data_dir
from midfreq.data.persistence import LegacyDataReader

reader = LegacyDataReader(data_dir())               # read-only; never writes under data/
raw = reader.read_raw("00700.XHKG", "1-HOUR", start="2026-08-28")
bars = reader.read_bars("00700.XHKG", "1-HOUR", start="2026-08-28")

print(raw.index[:3].tolist())                       # as stored: local close times, labelled UTC
for bar in bars[:3]:
    print(bar.bar_type, bar.timestamp, bar.close)   # ts_event: the true UTC close
```

```text
[Timestamp('2026-08-28 10:30:00+0000', tz='UTC'), Timestamp('2026-08-28 11:30:00+0000', tz='UTC'), Timestamp('2026-08-28 12:00:00+0000', tz='UTC')]
00700.XHKG-1-HOUR-LAST-EXTERNAL 2026-08-28 02:30:00+00:00 456.2
00700.XHKG-1-HOUR-LAST-EXTERNAL 2026-08-28 03:30:00+00:00 456.0
00700.XHKG-1-HOUR-LAST-EXTERNAL 2026-08-28 04:00:00+00:00 457.6
```

The third bar is the half hour before the Hong Kong lunch break, 11:30 to 12:00 local time.

## The data catalog

`ParquetDataCatalog` (`midfreq.data.persistence`), after NautilusTrader's catalog, keeps one
directory per bar type and one JSON file per instrument definition:

```text
catalog/
  data/bar/00700.XHKG-1-HOUR-LAST-EXTERNAL/<first ts_event>-<last ts_event>.parquet
  instruments/00700.XHKG.json
```

Rows hold OHLCV as float64 and `ts_event` and `ts_init` as int64 UTC nanoseconds. A write merges
with what is stored (a re-fetched bar replaces the stored one) and rewrites the bar type's file
through a temporary file and an atomic rename, so an interrupted write never leaves a
half-written file. A new catalog gets a `.gitignore`, so market data is not committed by
accident.

The `fetch` command fills it through a venue's connector; without `--client` (or
`--connector`) the venue's default connector is used, and a bare `--end` date means the whole of
that day. `migrate` copies the price cache into the catalog: it only reads `data/` and refuses a
catalog inside it.

<!-- include: packages/midfreq-data/README.md#command-line strip-title -->

## Account clients

<!-- include: packages/midfreq-data/README.md#account-clients strip-title -->

The dashboard shows each account beside the bot's own record, so that the two can be checked
against each other; [Live trading](live_trading.md#two-sources-never-merged) describes the
reconciliation and the snapshots the trading VM publishes for the hosted dashboard.
