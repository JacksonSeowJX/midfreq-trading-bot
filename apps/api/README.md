# midfreq-api: the dashboard's HTTP API

Read-only JSON for the web dashboard (`apps/web`), over the results files, the live session logs,
the broker snapshots and the store of saved runs.

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
| `GET /api/auth/config` | whether sign-in is on, and the Firebase web configuration (`apiKey`, `authDomain`, `projectId`); public |
| `GET /api/auth/me` | the signed-in account: `email`, `email_verified`, `allowed`, and `reason` when not allowed; 401 without a valid token |
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
| `GET /api/runs?kind=&strategy=&limit=` | saved runs (the run store), newest first: kind, source, strategy, universe, dates, commit and the headline metrics; 422 for an unknown kind |
| `GET /api/runs/{id}` | one saved run: its summary, `meta` (run.json: how it was run) and its table names; 404 if there is none |
| `GET /api/runs/{id}/tables/{name}?format=json\|csv\|parquet&limit=` | one table: JSON `{run_id, name, columns, rows, total}` (NaN as null, at most `limit` rows), or the whole table as a file download, `<id>_<name>.csv` or `.parquet` |
| `DELETE /api/runs/{id}` | 204; 403 where `Settings.runs_deletable` is off (the hosted site); 404 if there is none |

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
way (`deps.py`: `get_record_store`, `get_account_provider`, `get_run_store`).

## Saved runs

Every job that finishes (backtest, compare, optimize) is saved to the run store
(`midfreq.backtest.runstore`: a folder, `MIDFREQ_RUNS` or `<repo>/runs`, or a `gs://` bucket on
the hosted site), and the job then reports it as `run_id`. `saved_runs.record_for_job` builds the
record from the job's request, plan and result, in the shapes runstore's `record_from_*` helpers
give the runs scripts save (its docstring lists them): kind `backtest`, `compare`, `grid_search`
or `walk_forward`, source `api`; a walk-forward's windows table has the chosen parameters, the
in-sample figures the job has (`is_<objective>`, `is_return_pct`) and every out-of-sample one
(`oos_<metric>`).

The save runs in the API process after the work, in a thread of its own: the job stays
`running` ("Saving the run") until it returns, so the web app polls until `run_id` is there. A
save that fails is logged and the job is `done` without a `run_id`; one that takes longer than
`JobRunner.save_timeout` (120 s) leaves the job done, and sets `run_id` if it lands later. Saving
never fails a job.

`get_run_store` makes the store once per process (`default_run_store()`, imported when first
needed). A store that cannot be made (no credentials for the bucket) is an `UnavailableRunStore`:
the Run history routes answer 503 with the reason, jobs still run unsaved, and the next request
tries again. A store call that raises is 503 too; `KeyError` is 404. Ids and table names other
than letters, digits, `_` and `-` are 404 without asking the store. Tests use
`conftest.MemoryRunStore`, which round-trips tables through Parquet as a real store does, and one
test saves every kind into a real `LocalRunStore` in a temporary folder.

## Sign-in

Off by default: on a laptop or the VM nothing needs a token. The hosted dashboard turns it on
(`ops/dashboard/setup_auth.sh`; ops/README.md, Sign-in). Visitors sign in with Google through
Firebase Authentication (Google Identity Platform) in the web app, and the API checks the ID
token (`auth.py`). Passwords and credentials are never held here.

- With `Settings.auth == "firebase"`, an app-wide dependency (`require_access`, in `create_app`)
  checks every route except `PUBLIC_ROUTES`: `/api/health`, `/api/auth/config` and the web app's
  files (which hold no data). A new route is private unless it is listed there. It runs before
  the route's own dependencies, so a refused request reads nothing.
- `Authorization: Bearer <Firebase ID token>`, verified with google-auth
  (`google.oauth2.id_token.verify_firebase_token`, from the `gcs` extra; no other dependency).
  Google's signing keys are cached for their `Cache-Control` max-age. The token's audience and
  issuer must be the project, it must not have expired, and it must name a subject.
- Answers: 401 without a token, or with one that does not verify (`WWW-Authenticate: Bearer`);
  403 when the email is not verified, or is not on the access list ("Awaiting approval: ...",
  `WWW-Authenticate: Bearer error="insufficient_scope"`); 503 when Google's keys cannot be
  fetched. `/api/auth/me` needs a valid token but not access, so the web app can tell a
  signed-in account that it is awaiting approval.
- The access list: an email is allowed when it is in `MIDFREQ_AUTH_ALLOWED_EMAILS`, or when the
  part after its `@` is exactly one of `MIDFREQ_AUTH_ALLOWED_DOMAINS` (case-insensitive; a
  subdomain must be listed itself). Both lists are set on the service, never committed.

| Variable | |
|---|---|
| `MIDFREQ_AUTH` | `off` (default) or `firebase`; any other value stops the server from starting |
| `MIDFREQ_AUTH_PROJECT` | the Firebase (GCP) project id: the tokens' audience; required with `firebase` |
| `MIDFREQ_AUTH_ALLOWED_EMAILS`, `MIDFREQ_AUTH_ALLOWED_DOMAINS` | comma-separated; empty admits nobody |
| `MIDFREQ_AUTH_WEB_API_KEY` | the Firebase web API key, handed to the web app by `/api/auth/config` (public by design) |
| `MIDFREQ_AUTH_DOMAIN` | the Firebase `authDomain`; default `<project>.firebaseapp.com` |

Tests replace the verifier with `app.dependency_overrides[get_token_verifier]` (`tests/test_auth.py`).

## The web app

CORS allows the Vite dev server (`http://localhost:5173`, `http://127.0.0.1:5173`). When
`apps/web/dist` holds a build, the API also serves it at `/`, with every non-file path falling
back to `index.html`; `/api/...` is never answered from there.
