# Testing

```bash
uv run pytest            # every package's tests, apps/api/tests and tests/
uv run lint-imports      # packages import only the layers below them
```

The suite collects 1,423 tests. On Python 3.12 with `uv.lock`, 1,379 pass and 44 are skipped by
default: 43 that need the real price cache (opt-in, below) and one Linux-only check of the VM's
schedule, which runs in CI.

| Where | Tests | What |
|---|---|---|
| `packages/midfreq-core/tests/` | 83 | the domain model, market data types, venues, the portfolio and the risk rules, connection settings |
| `packages/midfreq-engine/tests/` | 16 | the C++ engine's Python face (its C++ unit tests run under CMake, in CI's `engine` job) |
| `packages/midfreq-data/tests/` | 343 | the connectors and the connector contract, the execution contract, Moomoo's live client and ccxt's execution client, the cache and the catalog, the account clients and broker snapshots |
| `packages/midfreq-strategies/tests/` | 23 | the candle strategies and their registry |
| `packages/midfreq-backtest/tests/` | 241 | the candle backtester, the venue engine and its fills, fill and slippage models, the event-driven strategies, the run store, the C++ engine's integration and parity |
| `packages/midfreq-live/tests/` | 30 | the session runner and its order gateway |
| `packages/midfreq-trading/tests/` | 315 | the trading node: OMS, journal, pre-trade risk, position ledger, market data, reference data and calendars, the strategy host, shadow mode, the simulated venue, configuration and the command, records compatibility, the node's wiring, and replay equivalence |
| `packages/midfreq-research/tests/` | 7 | the results index and the forward-test read-back |
| `apps/api/tests/` | 235 | the API, in temporary folders only, with the sign-in verifier and the run store replaced |
| `tests/integration/` | 58 | cross-package tests: the equivalence of the venue engine's legacy adapter with the candle backtester for all 13 strategies, and the venue SDK boundary |
| `tests/e2e/` | 29 | end-to-end checks: the session close, the deploy gate and the VM's schedule |
| `tests/slow/` | 43 | the adapter's and the C++ engine's equivalence on the real price cache; opt-in |
| `tests/support/` | | shared helpers (fake connectors and venues, synthetic markets), importable from any package's tests |

The counts are those `uv run pytest --collect-only` reports for each folder.

pytest is configured in the root `pyproject.toml`: the test paths, `--import-mode=importlib`,
and `tests/support` on the path, so `from mft_fakes import FakeBinanceTransport` works in any
package's tests.

## Hermetic by default

The default suite uses fake transports, fake brokers, temporary folders and synthetic data. It
never touches the network, a broker gateway, or the real `data/` and `live_sessions/` folders,
so it runs the same on a laptop, in CI and on a fresh clone. Two consequences for new tests:

- `LiveTradingEngine`'s `session_log_dir` must point at a temporary folder, since its default is
  the repository's `live_sessions/`.
- `DataStorage` creates folders when it loads a missing symbol, so a test that needs bars writes
  them to a temporary cache rather than reading `data/`.

## The connector contract

[`packages/midfreq-data/tests/test_connector_contract.py`](repo:packages/midfreq-data/tests/test_connector_contract.py)
holds one `Case` per connector, each with a fake transport that speaks the source's own format,
and runs seven checks on every case: symbol round trips, pagination with no gaps or duplicates,
close times in true UTC across a daylight-saving change and the Hong Kong lunch break, the
instrument provider's rules, refusal of an unsupported interval and venue, retry of a rate limit
after a wait, and the capability flags. [Add a connector](../tutorials/add_a_connector.md#6-pass-the-contract-tests)
walks through adding a case.

## The execution contract

[`packages/midfreq-data/tests/test_execution_contract.py`](repo:packages/midfreq-data/tests/test_execution_contract.py)
runs the same 13 scenarios against every execution client, each wired to a fake of its venue
through a driver that plays the venue's side (`tests/support/venue_driver.py`): a fill; partial
fills summing to the order; a rejection with the venue's reason; a cancel; a cancel racing a fill
(the fill first, then the cancel of the rest); a refused cancel, with the order still working; a
repeated push producing no second event; a disconnect mid-order, then reconciliation after the
reconnect; the connection status reported; the venue's order id recorded; events reaching the
handler only, the order itself left to the OMS; paper by default; and live refused without
explicit configuration. The venues are Moomoo (`fake_moomoo.py`), ccxt with websocket updates
and ccxt polling (`fake_ccxt.py`), and the trading node's simulated venue (`fake_sim.py`): 52
tests, none skipped. [Add a connector](../tutorials/add_a_connector.md#trading-through-the-connector)
shows how a new client joins it.

## The trading node

The node's tests run it on its real components with a simulated venue and a test clock, so they
are deterministic and touch no broker. Among them:

- **Replay equivalence** (`packages/midfreq-trading/tests/test_replay_equivalence.py`): the node,
  built from `config/node_sp100.yaml` with its default components, fed the candles of the recorded
  October sessions, makes the same decisions as the session runner fed the same candles through
  its replay harness (the same orders, rebalance clock, holdings and cash after every session),
  also with unfilled orders, partial fills and a crash mid-session; and, from the forward test's
  real state file, both send the same orders at the next rebalance.
- **Records compatibility** (`test_records_compat.py`): the state file and the session log lines
  the node writes are in the session runner's exact format, and each path resumes a state file
  the other wrote.
- **The boundary**: [`tests/integration/test_venue_sdk_boundary.py`](repo:tests/integration/test_venue_sdk_boundary.py)
  fails if any package but the connectors imports a venue SDK
  ([Integrations](../integrations/index.md#trading-through-connectors)).
- **Python 3.10.** CI's `vm` job runs the packages' and the cross-package tests against the
  installed release on the VM's interpreter and versions ([CI workflows](ci.md#ci)).

## Real data, opt-in

The legacy adapter's equivalence with the candle backtester is also checked on the real cache:
all 13 strategies on five Hong Kong and five US stocks, the full S&P 100 history, and the forward
test's configuration on the window it was chosen on. The test only reads the cache:

```bash
MIDFREQ_SLOW=1 MIDFREQ_DATA_DIR=/path/to/data uv run pytest tests/slow -q -s
```

## The live path

Both live paths can be checked without a broker, by replaying the recorded sessions against a
simulated broker (this needs the price cache):

```bash
uv run python scripts/live/replay_live_sessions.py                          # the session runner
uv run midfreq-node replay --config config/node_sp100.yaml --data-dir data      # the trading node
```

A change to the live path is replayed this way, and checked for equivalence with the backtester,
before it is released to the trading VM. Before a release trades, the startup checks run it on
the VM against a copy of the runtime
([Releases and deployment](releases_and_deployment.md#startup-checks)). The one piece the replay cannot exercise, the real
order calls, is checked by `scripts/live/test_order_path.py` on the Hong Kong paper account
during market hours: a marketable buy and sell must fill, and a buy far below the market must be
cancelled with nothing filled. It refuses to run when the connection is set to the real
environment.

## The web app

```bash
cd apps/web
npm test -- --run        # Vitest, with the API mocked by MSW
npm run typecheck
npm run lint
```

## Packages and layers

`uv run lint-imports` checks the import-linter contracts in the root `pyproject.toml`: that each
package imports only the layers below it, and that packages sharing a layer do not import each
other ([Architecture](../concepts/architecture.md#packages-and-their-layers)).
