# midfreq-live

Live paper trading through Moomoo OpenD. Depends on `midfreq-core`, `midfreq-data` and
`midfreq-strategies`; nothing imports it except `apps/live_runner` and the scripts.

| Module | What it holds |
|---|---|
| `midfreq.live.live_engine` | `LiveTradingEngine` (streams candles into a strategy, logs each session to `live_sessions/`) and `LivePortfolio` (routes trades to the broker, books only confirmed fills, keeps per-strategy state between sessions) |
| `midfreq.live.order_gateway` | `OrderGateway`, `MoomooPaperGateway` (marketable limit orders, waits for the fill, cancels any remainder) and `moomoo_settings()` |

## Connection settings

`moomoo_settings()` reads the `moomoo` entry of `config/connections.yaml` (and its
`MIDFREQ_MOOMOO_*` overrides) and returns the OpenD host, port, `TrdEnv` and `SecurityFirm`.
Missing values fall back to `127.0.0.1:11111`, SIMULATE, FUTUSECURITIES. `TrdEnv.REAL` is used
only when `trading_env` is set to `real` explicitly; an unrecognised value raises rather than
being guessed. The scripts that only make sense on the paper account
(`scripts/live/test_order_path.py`, `flatten_hk_orphans.py`, `scripts/calibration/calibrate_hk_fee.py`)
refuse to run when it is set to `real`.

## Running

    uv run python apps/live_runner/run_live.py --list
    ./ops/run_sp100_forward_test.sh --scheduled        # as cron runs it; see ops/README.md

## Tests

    uv run pytest -q -p no:cacheprovider packages/midfreq-live tests/e2e
    uv run python scripts/live/replay_live_sessions.py  # replays the recorded sessions; needs the bar cache

The tests never connect to OpenD: the engine is driven with a fake gateway and the order
gateway with a scripted trade context and clock. `session_log_dir` must point at a temporary
folder in tests, since its default is the repository's `live_sessions/`.
