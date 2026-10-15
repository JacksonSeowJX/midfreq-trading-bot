# apps/web: the dashboard front end

A trading-terminal style dashboard for the bot, in React. It reads everything through the
dashboard API in `apps/api` and never touches the repository's files or the trading process
itself. It replaces the Streamlit dashboard (`apps/dashboard`) one view at a time.

| View | What it shows | Source |
|---|---|---|
| **Live** | The broker account (snapshot, positions, recent orders), the S&P 100 forward test (KPIs, equity curve, trades, holdings), session history with a detail drawer, and account equity over time. Refreshes every 60 s. An account the VM publishes (kind `snapshot`) is badged *Snapshot from the VM* with its age, and warns once it is more than 15 minutes old. | `/api/accounts*`, `/api/forward-test`, `/api/sessions*` |
| **Overview** | Every result against the two-configuration standard: summary, a sortable and filterable table (passes highlighted), Config A vs B, pass rate by family, and *Corrected for the search* (Reality Check p-values, PBO). | `/api/results/overview` |
| **Research files** | Any file in `results/`: a sortable table for CSV (with the quick chart where it is obvious) or a collapsible tree for JSON. | `/api/research/files*` |
| **Backtest** | One strategy over one period: KPIs, the price as candles with the strategy's indicators (over the price or in a lower pane) and buy/sell markers, the equity curve against the starting capital, and the trades. | `/api/jobs*`, `/api/strategies`, `/api/universes` |
| **Compare** | Two to eight strategies on the same data: a metrics table (best of each column marked) and their equity curves overlaid. | same |
| **Optimize** | Grid search (every combination, best first, and a heatmap of the objective when two parameters vary) or walk-forward (each window's chosen parameters and out-of-sample return, with the mean). Allowed only where the API allows it: a local install, not the hosted site. | same |

## Running it

Needs Node 22 or later. From the repository root:

```bash
uv run uvicorn midfreq.api.main:app --reload --port 8000   # the API, in one terminal

cd apps/web
npm ci                                                      # once, or after package-lock.json changes
npm run dev                                                 # http://localhost:5173
```

The dev server forwards `/api` to `http://127.0.0.1:8000`. To use an API elsewhere, set
`MIDFREQ_API_URL` (for example `MIDFREQ_API_URL=http://127.0.0.1:8765 npm run dev`).
Without an API the app still loads and says the API is unreachable.

| Script | Does |
|---|---|
| `npm run dev` | dev server with hot reload |
| `npm run build` | type-checks, then builds to `dist/` |
| `npm run preview` | serves `dist/` (also proxies `/api`) |
| `npm test` | vitest, in watch mode; `npm test -- --run` runs once |
| `npm run typecheck` | `tsc` only |
| `npm run lint` | ESLint |
| `npm run gen:api` | regenerates `src/api/schema.d.ts` from `../api/openapi.json` |

CI (`.github/workflows/ci.yml`, job `web`) runs `npm ci`, checks that `gen:api` produces no
change, then runs typecheck, lint, tests and build.

## Architecture

**Typed client.** `apps/api/src/midfreq/api/schemas.py` is the contract. The API exports it as
`apps/api/openapi.json`, `openapi-typescript` turns that into `src/api/schema.d.ts` (committed),
and `openapi-fetch` (`src/api/client.ts`) is typed against it. A route or field the API does
not declare will not compile. `src/api/types.ts` gives the response models short names, and
`src/api/queries.ts` has one TanStack Query hook per route. Live data refetches every 60 s and
when the tab regains focus; research data is cached for 5 minutes.

**When the contract changes.** Re-export the spec, then regenerate the types:

```bash
uv run python -m midfreq.api.export_openapi        # repo root: writes apps/api/openapi.json
cd apps/web && npm run gen:api && npm run typecheck
```

Commit `schema.d.ts` with the change. `gen:api` passes `--empty-objects-unknown`, so the API's
free-form `Dict[str, Any]` fields become `Record<string, unknown>`, not unusable `never`s.

**Two sources, never merged.** On the Live view the *broker account* (blue, what the broker
reports through its connector) and the *bot record* (orange, what the bot wrote to its session
logs) are separate panels, each labelled. They are kept apart on purpose so that one can be
checked against the other. An unavailable account (OpenD not running, no API key) is listed
greyed out with its reason. A 503 from the account route is shown the same way, and the bot
record still loads.

**Errors and empty states.** Every query goes through `QueryState` (`src/components/states.tsx`):
a skeleton until the first answer, then data. A failure shows a clear message: API unreachable
(with the command to start it), 501 not implemented yet, 503 unavailable with the API's reason,
or a view-specific 404 such as "No HK forward test recorded". If a background refresh fails,
the last data stays on screen under a banner saying when it is from.

**Runs (Backtest, Compare, Optimize).** The three pages share one form (`features/runs/RunForm.tsx`)
with the Streamlit sidebar's controls and limits: strategy and its parameters (min, max, step
from `/api/strategies`), the market from the top bar, a searchable symbol among those with
cached candles at the chosen timeframe, the pair for Pairs Trading, the universe for the
cross-sectional (basket) strategies, which rank the whole universe rather than one stock,
timeframe, dates, capital, commission (the market's default from `/api/jobs/capabilities`,
shown in percent), slippage, and a folded *Risk & sizing* section. Each page remembers its last
inputs in `localStorage`. Pressing Run validates the inputs, posts the job (202), and puts its
id in the URL (`/backtest?job=…`), so a run can be linked to and survives a reload. The job is
polled every second while queued or running (progress bar and message), then shows its result;
leaving the page stops the polling, not the run. *Recent runs* (`GET /api/jobs`) reopens
earlier results. Errors say what to do: 403 (this deployment does not allow the kind; Optimize
runs only on a local install, with the commands), 422 (each problem beside its input, whether
FastAPI's list or the API's one-sentence check), 429 (a run is already in progress, linked),
and a failed job's own error. A kind missing from the capabilities keeps its nav item, and its
page explains that it runs on a local install.

**Same origin.** All calls go to `/api` on the page's own origin, so the same build works
behind the dev proxy and when hosted (for example on Cloud Run, with the API serving `dist/`
or a proxy in front). The host must answer unknown non-`/api` paths with `index.html`, because
routing happens in the browser.

### Stack

Vite, React 19, TypeScript (strict, `noUncheckedIndexedAccess`), Tailwind CSS 4,
shadcn/ui-style components on Radix primitives (only the ones used, in `src/components/ui`),
TanStack Query, React Router, TradingView `lightweight-charts` for equity curves and the
backtest's candles, ECharts (tree-shaken, loaded only with Overview, Research and Optimize) for
research charts and the optimize heatmap, `lucide-react` icons. The run pages load on demand.
Tests use Vitest, Testing Library and MSW.

### Layout of the code

```
src/
  api/          schema.d.ts (generated), client.ts, types.ts, queries.ts
  components/   layout/ (shell, nav, top bar), ui/ (primitives), charts/, DataTable,
                KpiCard, SourcePanel, states (loading, empty, error)
  features/     live/, overview/, research/, runs/ (Backtest, Compare, Optimize: the
                shared form in form.ts + RunForm.tsx, the job flow, results/), placeholders/:
                one folder per view, with logic.ts holding each view's pure functions
  lib/          formatting, theme and market preferences
  test/         typed fixtures, MSW handlers, render helpers
  routes.tsx    the route table
```

### Look

The palette is the Streamlit dashboard's (warm light surface, orange accent), with a dark
variant stepped from the same hues. Dark mode follows the system until it is chosen in the top
bar. Tokens live in `src/index.css`, and canvas charts read the same values from
`src/components/charts/palette.ts`. Tables are dense, with tabular figures and right-aligned
numbers. P&L is green or red and always carries its sign too, because green and red alone are
not distinguishable for colour-blind readers. Series colours (Compare's equity curves, the
indicators) come in a fixed order checked for colour-blind separation on both themes
(`SERIES_COLORS` in `palette.ts`); a strategy keeps its colour whatever its rank, and a legend
or table always names it.

## Tests

`npm test -- --run`. The tests run against MSW handlers (`src/test/handlers.ts`) that serve
fixtures typed from `schema.d.ts`, so a contract change that breaks the fixtures fails to
compile. Canvas charts cannot draw in jsdom and are stubbed. The tests cover the data around
them: formatting, KPI cards, the trades and holdings tables, the account-unavailable states and
the VM snapshot badge, the session drawer, the overview filters and the Reality Check figures,
the research viewer, routing, the API-down and 501 states, and for the runs: the form
(parameters, validation, symbol search, pair and universe, risk section, remembered inputs),
the job states (queued, running, done, failed; 403, 422, 429; deep links and recent runs; the
tests shorten the poll interval through `JOB_POLL`) and each result view from fixtures.

## Dependencies

`package-lock.json` is committed and CI installs with `npm ci`. npm 10 can crash with
`Cannot read properties of null (reading 'edgesOut')` when it adds packages that bring
optional peer dependencies (Vitest does). If that happens, add the package with npm 11
(`npx npm@11 install -D <pkg>`). The lockfile format is the same, and `npm ci` on npm 10 works.
