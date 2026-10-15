# Environment

## The workspace

The repository is a [uv](https://docs.astral.sh/uv/) workspace whose members are the six
packages in `packages/` and the dashboard's API in `apps/api`. One command creates `.venv/` with
every member installed in editable mode:

```bash
uv sync --python 3.12
```

The root `pyproject.toml` is not itself a package (`[tool.uv] package = false`); it depends on
every member with every optional extra, so a checkout has all the connectors' SDKs. The
workspace resolves for Python 3.12 only. Third-party libraries are held at the versions every
reported result was produced with, through `constraint-dependencies` in the root
`pyproject.toml` and the lock file `uv.lock`: among them pandas 2.2.2, numpy 1.26.4, pyarrow
16.1.0, the moomoo SDK 10.2.6208, ib-insync 0.9.86, yfinance 1.2.0 and ccxt 4.5.88. The `dev`
group adds pytest, import-linter, httpx, google-auth and requests.

| Package | Its own `pyproject.toml` declares |
|---|---|
| `midfreq-core` | numpy, pandas, pydantic, pyyaml, python-dotenv, pytz |
| `midfreq-data` | core, pandas, pyarrow, requests; extras `moomoo`, `ibkr`, `yfinance`, `crypto` |
| `midfreq-strategies` | core, numpy, pandas; extra `hmm` (hmmlearn, scikit-learn) |
| `midfreq-backtest` | core, data, strategies, pandas |
| `midfreq-live` | core, data, strategies, pandas, the moomoo SDK; the `midfreq-live` command |
| `midfreq-research` | core, data, strategies, backtest, numpy, pandas, statsmodels |

Each is built with hatchling into a wheel of its `src/midfreq/` portion of the namespace.

## Configuration

| File | Holds |
|---|---|
| [`config/connections.yaml`](repo:config/connections.yaml) | every broker, gateway and exchange connection (moomoo OpenD defaults to `127.0.0.1:11111` and the paper environment), and the read-only accounts the dashboard shows |
| [`config/venues.json`](repo:config/venues.json) | the venues: timezone, sessions, currency, lot rule, fees, connectors and their symbology |
| [`config/symbols.json`](repo:config/symbols.json) | the markets' symbols and the named universes (`hk_live`, `us_15`, `sp100`, `hsi`) |
| [`config/sp100_forward_test.json`](repo:config/sp100_forward_test.json) | the forward test's strategy, parameters, symbols and how they were chosen |

- **Overrides.** Any connection setting can be overridden with an environment variable
  `MIDFREQ_<NAME>_<KEY>`, for example `MIDFREQ_MOOMOO_PORT=11112`, and `MIDFREQ_CONNECTIONS`
  points at another connections file.
- **Secrets.** API keys never go in a configuration file: an entry names the environment
  variables that hold them, read from the environment or an untracked `.env`
  (`midfreq.core.connections.get_secret`).
- **Folders.** `config/`, `data/`, `results/`, `live_sessions/` and `catalog/` are found through
  `midfreq.core.paths`: the folder `MIDFREQ_ROOT` names, else the repository. Pointing
  `MIDFREQ_ROOT` at a copy is how a run is kept away from the real folders.

<!-- include: packages/midfreq-core/README.md#connection-settings level=3 -->

## The web app

The dashboard's front end, `apps/web`, is React and TypeScript, built with Vite. It needs Node
22.12 or later. In development it runs beside the API, with hot reload:

```bash
uv run uvicorn midfreq.api.main:app --reload --port 8000    # the API, in one terminal
cd apps/web && npm ci && npm run dev                         # http://localhost:5173, forwards /api
```

`npm test -- --run`, `npm run typecheck`, `npm run lint` and `npm run build` are the checks CI
runs. See [`apps/web/README.md`](repo:apps/web/README.md).

## Changing the API's contract

The API's models in `apps/api/src/midfreq/api/schemas.py` are its contract with the web app.
After changing them, export the contract, regenerate the web app's types and commit both:

```bash
uv run python -m midfreq.api.export_openapi          # writes apps/api/openapi.json
cd apps/web && npm run gen:api && npm run typecheck  # regenerates src/api/schema.d.ts
```

An API test fails while `openapi.json` is stale, and CI fails while `schema.d.ts` does not match
it. This site's [HTTP API reference](../api/http/reference.md) shows the new contract from its
next build.
