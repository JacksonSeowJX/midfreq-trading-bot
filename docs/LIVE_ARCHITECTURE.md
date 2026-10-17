# Live trading architecture: the trading node

Status: being built (October-November 2026) on branch `feature/live-trading`. The forward test
keeps running on the original engine (`midfreq.live.live_engine`) until it ends on about
13 November 2026; nothing here changes how that engine trades.

## Why

The original live path is a single-strategy runner: one process subscribes to its own data,
decides, sends each order and waits for it (`order_gateway.place_and_confirm`: place, poll 30 s,
cancel), and writes its own state file. It traded the forward test correctly, but the test also
showed what it lacks:

| Missing | Seen in October 2026 |
|---|---|
| An order management system (order state machine, journal, retry or re-price, fills pushed by the broker) | A Lam Research buy (15 Oct) was cancelled unfilled after 30 s with no retry and no reason recorded; send and fill times are not logged |
| One market data service shared by every strategy | The Hong Kong roster had to be paused: two sessions subscribing separately exceed the 100-instrument quote quota; limit prices use the last price, never the bid or ask |
| Reference data (calendars, lots and ticks, corporate actions) | The launcher knows weekdays, not holidays; a dividend reached the broker's account but not the bot's ledger |
| Pre-trade risk and a kill switch outside the strategy | Only the strategy's own drawdown halt |
| Structured operations logs and alerts | `print()` only; failures are found by reading logs |

## Principles

1. **Python only.** The trading node is pure Python. (The C++ code in `midfreq-engine` is the
   backtester's core for research and is not part of the live path or the VM release.)
2. **Venues only through connectors.** Only `midfreq.data.adapters.<venue>` imports a venue's SDK
   (Moomoo's `moomoo`, `ccxt`, ...). A new venue is a new connector and nothing else changes.
   Checked by `tests/integration/test_venue_sdk_boundary.py`.
3. **One thread owns the state.** Connectors call back on their own threads; those callbacks only
   put items on the node's queue. The OMS, risk, positions, market data and strategies run on the
   node's single event-loop thread, so they need no locks, and a session replays exactly from
   the same queue items with a test clock.
4. **The same strategy code as the backtests.** Candle strategies (`midfreq.strategies.candle_strategies`)
   run unchanged through an adapter, as in the backtester; event-driven strategies
   (`midfreq.strategies.trading.Strategy`) run natively.
5. **Paper unless told otherwise.** Every execution client starts in `paper` or `sim`; `live`
   needs an explicit setting, as today (`config/connections.yaml`).
6. **Compatible records.** The node writes the same `state_<strategy>.json` and
   `session_*.jsonl` formats as the original engine, so the dashboard, the forward-test record and
   a switch between engines carry over.
7. **Additive.** `midfreq.live` (the original engine) is not modified; it is retired only after
   the new node has traded on paper and replayed the recorded sessions identically.

## Components

```
                 ReferenceData (instruments, lots/ticks, calendars, corporate actions)
                        │
 Connector streams ──> MarketDataService ──bus──> StrategyHost ──> orders ──> OMS ──> PreTradeRisk
 (DataClient.subscribe_*)  (one subscription,     (candle strategies          (state machine,     │
                            bar finality,          via adapter; native        journal, retries,   ▼
                            cross-sections,        Strategy API)              reconciliation) ExecutionClient
                            stalls)                     ▲                           │          (per venue)
                                                        └──── PositionKeeper <──fills┘◄──── events (venue threads → queue)
                                                              (positions, cash, P&L; shrink-only reconciliation)
 Ops: structured logs (console + JSON), heartbeat, alerts · SessionLog (session_*.jsonl) · StateStore (state_*.json)
```

| Component | Module | Responsibility |
|---|---|---|
| **Connectors** | `midfreq.data.adapters.<venue>` | `ExecutionClient` (submit, cancel, fills as events, open orders and fills for reconciliation), live streams on `DataClient` (`subscribe_bars`, `subscribe_quotes`, stream status, subscription limit). Contracts: `adapters/base.py`. |
| **MarketDataService** | `midfreq.trading.marketdata` | One subscription per instrument whatever the number of strategies (reference counted, within the venue's limit); bar finality (a bar is final on a newer push or when its window plus a grace has elapsed, as the original engine decides); cross-sections per universe (complete, or at least 90% when the window elapses); quotes cache; stall detection (no bar for 1.25 intervals: resubscribe once, then stop cleanly); warm-up from the local store only (never the broker's metered history); the session's bars written to the store at stop. |
| **ReferenceData** | `midfreq.trading.refdata` | Instruments (lot, tick, currency) from each connector's `InstrumentProvider`, cached; trading calendars per venue (sessions, lunch breaks, holidays and early closes from `config/venues.json`); corporate actions (dividends, splits) from a file source and, where a venue reports them, its cash flows. |
| **StrategyHost** | `midfreq.trading.strategy_host` | Runs strategies on bus events. `LiveLegacyAdapter` gives a candle strategy a portfolio whose `execute_trade` creates an `Order` for the OMS instead of trading directly, with the decision candle's close as `reference_price`, exactly what the strategy decided in a backtest. |
| **OMS** | `midfreq.trading.oms` | The one owner of order state (`OrderStatus` state machine in `midfreq.core.model`); unique client order ids; routing by the instrument's venue; journal of every order event (`orders_<date>.jsonl`, append-only); policies for orders left unfilled (re-price from the quote, retry up to N times, then cancel, each step logged with the venue's reply); reconciliation with the venue's open orders and fills at start-up and after reconnects. |
| **PreTradeRisk** | `midfreq.trading.risk` | Before every order: trading session open (calendar), instrument known, quantity a whole lot, price within a band of the last price (fat finger), order value and position limits per strategy, cash available; kill switch (`halt`/`resume`) that denies every new order. Denials become `OrderDenied`. |
| **PositionKeeper** | `midfreq.trading.positions` | Positions, average price, cash and realised P&L per strategy from fills; corporate-action cash (dividends) booked; reconciliation with the broker's positions that only ever shrinks a strategy's claim (the original engine's rule). |
| **TradingNode** | `midfreq.trading.node` | Wiring and lifecycle: load config and reference data → connect connectors → restore state and reconcile → warm up → subscribe → run the event loop with timers (finality, stalls, OMS policies, reconciliation every 5 min, heartbeat) → stop: trading off, unsubscribe, finalise, persist, disconnect. Modes: `shadow` (orders are logged, never sent), `sim` (a simulated venue), `paper`, `live`. |
| **Ops** | `midfreq.trading.ops` | Python `logging` with a console format (`SGT time | ET time | LEVEL | component | message`) and a JSON-lines file (`ops_<session>.jsonl`: UTC time, level, component, session, cross-section, order ids); heartbeat every 5 min; alert sinks (email, or Cloud Logging reading the JSON file) behind configuration, off by default. |

### Threads and the queue

Connector callback → `EventQueue.put(item)` → the node's loop takes items in order and dispatches:
`BarUpdate` → MarketDataService; `QuoteTick` → cache; `OrderEvent` → OMS → PositionKeeper (fills)
→ bus; `StreamStatus` → MarketDataService and ops. Timers are queue items too. A strategy's
`execute_trade` therefore returns before the fill; the adapter books the strategy's portfolio
when the fill event arrives (and only then), which is what the original engine moved to after
5 October (orders booked when filled, not when accepted).

### Records on disk (`MIDFREQ_ROOT/live_sessions/`)

- `state_<strategy>.json`: same format as today (positions, cash within the cap, rebalance clock).
- `session_<stamp>_<strategy>_<pid>_<n>.jsonl`: same events as today (start, candle, trade, stall, end).
- `orders_<date>.jsonl` (new): every order event with wall-clock times in UTC and the venue's ids and messages.
- `ops_<stamp>.jsonl` (new): the structured operations log.

## Testing (after the build)

Built first, then tested; the tests decide what gets fixed.

- **Execution contract suite**, every `ExecutionClient` against a scriptable fake venue: fill;
  partial fills; rejection; cancel; cancel racing a fill; duplicate push; disconnect mid-order and
  reconciliation after reconnect; paper by default, live refused without explicit configuration.
- **Unit tests** per component (OMS state machine and policies, risk checks, position keeping and
  shrink-only reconciliation, bar finality and cross-sections, calendars).
- **Replay equivalence**: the node in `sim` mode, fed the candles the recorded October sessions
  received, makes the same decisions (same baskets at the same decision candles) as the original
  engine did.
- **Boundary test**: no venue SDK outside the connectors.
- **End to end on paper** (with approval): the Hong Kong paper account during Hong Kong hours,
  a few stocks, while the US forward test is not running, so it cannot touch the forward test's
  account or exceed the quote quota.

## Rollout

1. Build and test on `feature/live-trading`; merge to `main` when the full suite, the replay
   equivalence and the boundary test pass.
2. Release with the new node installed but not scheduled; shadow and paper tests on Hong Kong.
3. After the forward test ends (about 13 November 2026), switch the US strategy to the node in
   a release, with `ops/vm/startup_check.sh` extended to the node; the original engine stays
   installable for rollback for one release, then is retired.

## Work split (parallel build)

| Part | Owns | Must not edit |
|---|---|---|
| A: OMS, risk, positions, journal | `midfreq/trading/{oms,risk,positions,journal}.py` and their tests | connectors, node |
| B: Moomoo connector, live | `midfreq/data/adapters/moomoo/{execution,streaming}.py`, the Moomoo connector's `execution_client()` and stream methods, `tests/support/fake_moomoo.py` | `adapters/base.py` (fixed), other connectors |
| C: market data and reference data | `midfreq/trading/{marketdata,refdata,calendar,corporate_actions}.py`, holiday data in `config/venues.json` (additive) | connectors, OMS |
| D: ccxt connector, simulated venue, contract suite | `midfreq/data/adapters/ccxt/{execution,streaming}.py`, `midfreq/trading/testing/`, `packages/midfreq-data/tests/test_execution_contract.py`, `tests/support/fake_ccxt.py` | Moomoo connector, OMS |
| E: node, strategies, records, ops | `midfreq/trading/{node,strategy_host,state,session_log,ops,shadow,config,cli,replay}.py`, `ops/run_node.sh`, `config/node_*.yaml` | the components above (uses them through `midfreq.trading.interfaces`) |

The contracts they share are fixed in `midfreq/data/adapters/base.py` (connectors),
`midfreq/trading/interfaces.py` (components) and `tests/support/venue_driver.py` (fake venues for
the execution contract suite); a part that needs a contract changed says so
instead of changing it.
