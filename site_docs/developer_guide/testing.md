# Testing

```bash
uv run pytest            # every package's tests, apps/api/tests and tests/
uv run lint-imports      # packages import only the layers below them
```

| Where | What |
|---|---|
| `packages/*/tests/` | each package's own tests, for example `uv run pytest -q packages/midfreq-data` |
| `apps/api/tests/` | the API, in temporary folders only, with the sign-in verifier and the run store replaced |
| `tests/integration/` | cross-package tests, among them the equivalence of the venue engine's legacy adapter with the candle backtester for all 13 strategies |
| `tests/e2e/` | end-to-end checks: the Streamlit dashboard's smoke test, the session close, the deploy gate and the VM's schedule |
| `tests/slow/` | the same equivalence on the real price cache; opt-in |
| `tests/support/` | shared helpers (fake connectors, synthetic markets), importable from any package's tests |

pytest is configured in the root `pyproject.toml`: the test paths, `--import-mode=importlib`,
and `tests/support` on the path, so `from mft_fakes import FakeBinanceTransport` works in any
package's tests.

## Hermetic by default

The default suite uses fake transports, fake brokers, temporary folders and synthetic data. It
never touches the network, a broker gateway, or the real `data/` and `live_sessions/` folders,
so it runs the same on a laptop, in CI and on a fresh clone, in under a minute. Two
consequences for new tests:

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

## Real data, opt-in

The legacy adapter's equivalence with the candle backtester is also checked on the real cache:
all 13 strategies on five Hong Kong and five US stocks, the full S&P 100 history, and the forward
test's configuration on the window it was chosen on. The test only reads the cache:

```bash
MIDFREQ_SLOW=1 MIDFREQ_DATA_DIR=/path/to/data uv run pytest tests/slow -q -s
```

## The live path

The live trading path can be checked without a broker, by replaying the recorded sessions
through the real engine against a simulated broker (this needs the price cache):

```bash
uv run python scripts/live/replay_live_sessions.py
```

A change to the live path is replayed this way, and checked for equivalence with the backtester,
before it is released to the trading VM. The one piece the replay cannot exercise, the real
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
