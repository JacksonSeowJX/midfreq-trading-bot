# midfreq-api: the dashboard's HTTP API

Read-only JSON for the web dashboard (`apps/web`), over the same data the Streamlit dashboard
(`apps/dashboard/app.py`) reads directly.

```
uv run uvicorn midfreq.api.main:app --reload      # http://127.0.0.1:8000/docs
uv run pytest -q -p no:cacheprovider apps/api      # tests (temporary folders only)
uv run python -m midfreq.api.export_openapi        # rewrite openapi.json after changing schemas.py
```

## Two sources, never merged

Live trading is shown from two independent sources, side by side, so they can be reconciled:

- **The bot's own record**: the session logs (`live_sessions/session_*.jsonl`) and console logs
  (`console_*.log`) the bot writes, read through a `RecordStore` (`store.py`). Today that is a
  local folder (`LocalDirRecordStore`); a store over the GCS bucket the VM uploads to is the
  planned second implementation and plugs in at `record_store_for()`.
- **Each broker or exchange account**, as the venue reports it: a read-only `AccountClient` from
  the connectors (`midfreq.data.adapters.accounts`), for each entry in the `accounts` section of
  `config/connections.yaml`, through an `AccountProvider` (`account_provider.py`).

The API never talks to the trading bot process.

Only the trading VM reaches OpenD, so it also publishes each account with `publish: true` every
5 minutes as `broker_<id>.json` beside the session logs (`midfreq.data.snapshots`, run by
`ops/vm/broker_snapshot.sh`); the record store lists and reads them (`broker_snapshots()`,
`read_broker_snapshot()`). An account is then served with `kind` `live` (read through its gateway
now) or `snapshot` (the published file, `as_of` its read time; the web app shows the age), by
`MIDFREQ_DASHBOARD_ACCOUNTS` (`Settings.accounts_enabled`, `Settings.snapshot_accounts_only`):

- `on` (default): live; an account whose gateway cannot be read is served from its snapshot if
  there is one (`available`, with the live failure as `reason`);
- `snapshots` (the hosted dashboard): only the configured accounts with a snapshot, from the file;
  no gateway is ever probed;
- `off`: no accounts.

A snapshot that cannot be read is 503 with the reason. See ops/README.md, Broker snapshots.

## Routes

| Route | Source |
|---|---|
| `GET /api/health` | version, and which sources exist (`results`, `live_sessions`, `data_cache`) |
| `GET /api/forward-test?market=US` | `midfreq.research.forward_test.load` over the record; 404 if there is none |
| `GET /api/sessions?market=&limit=` | session summaries, newest first; `crashed` = its console log has a Traceback or CRITICAL line |
| `GET /api/sessions/{id}` | one session: the broker's totals at start and end, its trades, event counts; 404 for an id not in the record |
| `GET /api/accounts` | configured accounts with an availability probe (all probes in parallel, bounded by `probe_timeout`) and `kind` (`live` or `snapshot`, above) |
| `GET /api/accounts/{id}?order_days=` | snapshot, positions, orders, live or from the published snapshot; 404 unknown id, 503 if it cannot be read now (or within `account_timeout`) and has no readable snapshot |
| `GET /api/results/overview?current_only=` | `midfreq.research.results_index`: every result held to the two-configuration standard, plus the Reality Check |
| `GET /api/research/files` | CSV and JSON files in `results/`, newest first, with the research view's labels |
| `GET /api/research/files/{name}?limit=` | one file: CSV as columns and rows (NaN as null, at most `limit` rows), JSON parsed |
| `GET /api/strategies` | `STRATEGY_REGISTRY` parameters with label, default, min, max, step |
| `GET /api/universes` | universes declared in `config/symbols.json` |
| `GET /api/candles?symbol=&timeframe=&start=&end=&limit=` | the candle cache (`data/`): the most recent `limit` in [start, end]; 404 if not cached |

The response models are in `schemas.py`, the contract with the web app; `openapi.json` is
generated from them and a test fails when it is stale. The routes are in `routers/` (`record`,
`accounts`, `research`, `reference`); `main.py` only assembles them.

Times are UTC. The bot stamps `session_start` and `session_end` with the VM's clock and no zone
(Singapore time, `Settings.record_timezone`); they are converted. Candle and trade times are
returned as the bot and the cache recorded them.

## Settings

`Settings` (`settings.py`) holds the results folder, the record source (`live_sessions`), the
candle cache folder, the config folder, the built web app folder, the record's time zone, the
account timeouts and the CORS origins. The default comes from `midfreq.core.paths` (so
`MIDFREQ_ROOT` moves everything). Routes get it through the `get_settings` dependency; tests and
other deployments replace it with `create_app(Settings(...))` or
`app.dependency_overrides[get_settings]`, and replace the record store or the accounts the same
way (`deps.py`: `get_record_store`, `get_account_provider`).

## The web app

CORS allows the Vite dev server (`http://localhost:5173`, `http://127.0.0.1:5173`). When
`apps/web/dist` holds a build, the API also serves it at `/`, with every non-file path falling
back to `index.html`; `/api/...` is never answered from there.
