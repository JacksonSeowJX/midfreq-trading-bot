# Development

## Setup

The repository is a [uv](https://docs.astral.sh/uv/) workspace whose members are the six
packages and `apps/api`. One command creates `.venv/` with every member installed in editable
mode:

```bash
uv sync
```

The workspace resolves for Python 3.12, on which the project was developed. Third-party
libraries are held at the versions every reported result was produced with, through
`constraint-dependencies` in the root [`pyproject.toml`](repo:pyproject.toml) and the lock file
`uv.lock` (for example pandas 2.2.2 and numpy 1.26.4). The `dev` dependency group adds pytest,
import-linter and httpx.

### Configuration and data

- **Connections.** Brokers' and exchanges' addresses are in
  [`config/connections.yaml`](repo:config/connections.yaml); moomoo OpenD defaults to
  `127.0.0.1:11111` and the paper-trading environment. Any value can be overridden with an
  environment variable `MIDFREQ_<NAME>_<KEY>`, for example `MIDFREQ_MOOMOO_PORT=11112`, and
  `MIDFREQ_CONNECTIONS` points at another file. API keys never go in that file: an entry names
  the environment variables that hold them, read from the environment or an untracked `.env`.
- **Folders.** `config/`, `data/`, `results/` and `live_sessions/` are found through
  `midfreq.core.paths`: the repository in a checkout, or the folder `MIDFREQ_ROOT` names.
- **Price cache.** `data/` is not in the repository. The dashboard's research and results views
  work from a fresh clone, because they read `results/` and `live_sessions/`; running a new
  backtest needs the cache, which the scripts in [`scripts/data/`](repo:scripts/data) download
  through OpenD:

    ```bash
    uv run python scripts/data/backfill_data.py        # the configured Hong Kong stocks
    uv run python scripts/data/backfill_sp100.py       # the S&P 100
    ```

## Tests

```bash
uv run pytest            # every package's tests, apps/api/tests and tests/
uv run lint-imports      # packages import only the layers below them
```

| Where | What |
|---|---|
| `packages/*/tests/` | each package's own tests, for example `uv run pytest -q packages/midfreq-data` |
| `apps/api/tests/` | the API, in temporary folders only |
| `tests/integration/` | cross-package tests, among them the equivalence of the venue-based engine's legacy adapter with the candle backtester for all 13 strategies |
| `tests/e2e/` | end-to-end checks: the Streamlit dashboard's smoke test, the session close, the deploy gate and the VM's schedule |
| `tests/slow/` | the same equivalence on the real price cache; opt-in: `MIDFREQ_SLOW=1 MIDFREQ_DATA_DIR=/path/to/data uv run pytest tests/slow -q -s` |
| `tests/support/` | shared helpers (fake connectors, synthetic markets), importable from any package's tests |

The default suite is hermetic. Tests use fake transports, fake brokers, temporary folders and
synthetic data: they never touch the network, a broker gateway, or the real `data/` and
`live_sessions/` folders (the opt-in real-data test only reads `data/`).

The live trading path can also be checked without a broker, by replaying the recorded sessions
through the real engine against a simulated one (this needs the price cache):

```bash
uv run python scripts/live/replay_live_sessions.py
```

### The web app

The dashboard front end needs Node 22.12 or later. In development it runs beside the API:

```bash
uv run uvicorn midfreq.api.main:app --reload --port 8000    # the API, in one terminal
cd apps/web && npm ci && npm run dev                         # http://localhost:5173, forwards /api
```

`npm test -- --run`, `npm run typecheck`, `npm run lint` and `npm run build` are the checks CI
runs. See [`apps/web/README.md`](repo:apps/web/README.md).

### Changing the API's contract

The API's models in `apps/api/src/midfreq/api/schemas.py` are the contract with the web app.
After changing them, export the contract, regenerate the web app's types and commit both:

```bash
uv run python -m midfreq.api.export_openapi        # writes apps/api/openapi.json
cd apps/web && npm run gen:api && npm run typecheck  # regenerates src/api/schema.d.ts
```

An API test fails while `openapi.json` is stale, and CI fails while `schema.d.ts` does not match
it. This site's [API reference](api/reference.md) picks up the new contract on its next build.

## Reproducing the report's key numbers

These need the price cache:

```bash
# Cost sensitivity of the S&P 100 result (report Section 4.9)
uv run python research/studies/cross_sectional_cost_sensitivity.py --end-date 2026-08-21

# Probability of Backtest Overfitting, 0.274 (report Section 4.11)
uv run python research/studies/pbo_sp100_reversal.py --end-date 2026-08-21

# White's Reality Check, p = 0.094 and 0.098 (report Section 4.11)
uv run python research/studies/reality_check_study.py --end-date "2026-08-28 16:00" --n-boot 20000
```

Each study writes a timestamped CSV or JSON file to `results/`; see
[`research/README.md`](repo:research/README.md).

## Continuous integration and releases

Every push and pull request runs the `research`, `vm` and `web` jobs of
[`ci.yml`](repo:.github/workflows/ci.yml). A release is versioned with `ops/set_version.sh`,
built and tested by the Release workflow, and deployed to the trading VM by hand. Both are
described under [Deployment](deployment.md#continuous-integration), with the
[release](deployment.md#releases) and [deploy](deployment.md#deploys) procedures.

## This documentation site

The site is [MkDocs](https://www.mkdocs.org/) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme. Its sources are
[`site_docs/`](repo:site_docs) and [`mkdocs.yml`](repo:mkdocs.yml). The toolchain is pinned in
`site_docs/requirements.txt` and kept out of the uv workspace, so it runs in a throwaway
environment:

```bash
uvx --with-requirements site_docs/requirements.txt mkdocs serve            # http://127.0.0.1:8000/midfreq-trading-bot/
uvx --with-requirements site_docs/requirements.txt mkdocs build --strict   # as the Pages workflow does
```

The build writes `site/`, which git ignores. Pages that show a repository document include it
when the site is built, through the hook `site_docs/hooks/repo_docs.py`, rather than copying it:

| In a page | Becomes |
|---|---|
| `<!-- include: docs/DASHBOARD_GUIDE.md -->` | the file's text; options after the path: `strip-title` drops its first level-1 heading, `app-links` sends its links to dashboard routes (`/live`, `/backtest?...`) to the hosted dashboard |
| `[text](repo:path/in/the/repository)` | a link to the page that shows that file, or else to the file on GitHub |
| `<!-- openapi-routes: tag; tag -->` | a table of the API's routes, by tag, from `apps/api/openapi.json` |

Links inside an included file are written relative to that file, as GitHub renders them, and
are rewritten for the site: to the page that includes the target, to a copy of an image published
with the site, or to the file on GitHub. A link to a path that does not exist fails the strict
build, as does a broken anchor. Mermaid diagrams are written in fenced `mermaid` code blocks.

To add a page, create it in `site_docs/` and add it to `nav` in `mkdocs.yml`.
