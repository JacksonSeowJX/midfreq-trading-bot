# Live trading architecture: the trading node

Status: built, integrated and safety-reviewed on branch `feature/live-trading` (October 2026):
the five parts below are merged, wired on their real classes and tested (see Testing). From
Monday 19 October 2026 the node takes over the US S&P 100 forward test on the Moomoo US paper
account (`config/node_sp100.yaml`, mode paper, release 0.1.2), continuing the original engine's
state file and records; the original engine (`midfreq.live.live_engine`) stays installable. The
node has not yet run against a real OpenD.

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
   needs an explicit setting in `config/connections.yaml` (an option alone is refused, for
   Moomoo as for ccxt) and `environment: live` on the node's account.
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
                            bar finality,          via adapter; native        journal, families,  ▼
                            cross-sections,        Strategy API)              re-pricing,      ExecutionClient
                            stalls)                     ▲                     reconciliation)     (per venue)
                                                        └──── PositionKeeper <──fills┘◄──── events (venue threads → queue)
                                                              (books per strategy; shrink-only reconciliation)
 Ops: structured logs (console + JSON), heartbeat, alerts · SessionLog (session_*.jsonl) · StateStore (state_*.json)
```

| Component | Module | Responsibility |
|---|---|---|
| **Connectors** | `midfreq.data.adapters.<venue>` | `ExecutionClient` (submit, cancel, fills and refused cancels as events, connection status, open orders and fills for reconciliation), live streams on `DataClient` (`subscribe_bars`, `subscribe_quotes`, stream status, subscription limit). Contracts: `adapters/base.py`. |
| **MarketDataService** | `midfreq.trading.data` | One subscription per bar type whatever the number of strategies (reference counted, within the venue's limit, counted in subscriptions); bar finality; cross-sections per universe (complete, or at least 90% when the window elapses); quotes; stall detection (no bar update for 1.25 intervals of session time: resubscribe once, then `fatal_stall`); warm-up from the local store only (never the broker's metered history); the session's bars written to the store at stop (paper and live only). |
| **ReferenceData** | `midfreq.trading.refdata` | Instruments (lot, tick, currency) from each connector's `InstrumentProvider`, cached, the venue's lot rule when a provider cannot answer; trading calendars per venue (sessions, lunch breaks, holidays and early closes from `config/venues.json`); corporate actions (dividends, splits) from `config/corporate_actions.json` and, where a venue reports them, its cash flows. |
| **StrategyHost** | `midfreq.trading.strategy` | Runs strategies on bus events. `LiveLegacyAdapter` gives a candle strategy a portfolio whose `execute_trade` sends a DAY limit order to the OMS, with the decision candle's close as `reference_price`, and waits for its outcome (see below). Native strategies get bars on their own topic and cross-sections as `data.slices`. |
| **OMS** | `midfreq.trading.execution` | The one owner of order state (`OrderStatus` state machine); unique client order ids `<strategy>-<YYYYMMDD>-<seq>`; routing by the instrument's venue; the journal (`orders_<date>.jsonl`, append-only, written before an order is sent); order families and the unfilled-order policy (re-price from the quote or last price, then cancel, each step journaled with the venue's reply); refused cancels; reconciliation with the venue's open orders and fills at start-up, every 5 minutes and after reconnects. |
| **PreTradeRisk** | `midfreq.trading.risk` | Before every order: kill switch, quantity a whole lot, trading session open (calendar), a price to value it, price band of the last price (fat finger), order and position notionals, no short selling, cash available at the worst case. Denials become `OrderDenied`. |
| **PositionKeeper** | `midfreq.trading.portfolio` | Books per strategy (positions, average price, cash, realised P&L, commissions, dividends) from fills, with Portfolio's arithmetic to the bit; fee estimates for fills whose venue reports none; fees in another currency converted; corporate actions; reconciliation with the broker's positions that only ever shrinks a strategy's claim. |
| **TradingNode** | `midfreq.trading.node` | Wiring and lifecycle: build the components → connect → reference data → restore every strategy's state and reconcile → session start → warm up → subscribe → the event loop with timers (finality and policies every second, reconciliation every 5 min, heartbeat) → stop: trading off, cancel what works, finalise, save state, write bars, session end, disconnect. Modes: `shadow` (orders logged, never sent), `sim` (a simulated venue, for replays), `paper`, `live`. |
| **Ops** | `midfreq.trading.ops` | Python `logging` with a console format (`SGT time | ET time | LEVEL | component | message`) and a JSON-lines file (`ops_<session>.jsonl`); heartbeat every 5 min; alert sinks behind configuration, off by default. |

### Threads and the queue

Connector callback → `EventQueue.put(item)` → the node's loop takes items in order and dispatches:
`BarUpdate`, `QuoteTick`, `StreamStatus` → `MarketDataService.dispatch` (the quote also into the
cache; a data stream's 'reconnected' also reconciles); `OrderEvent` → `OMS.handle_event` (which
journals and applies it, books fills in the PositionKeeper and publishes it); `ExecutionStatus`
(an execution client's connection change, tagged with its account) → the ops log, and on
'reconnected' a reconciliation of orders and books. Timers are queue items too.

### Bus topics

Closed bars are published on the engine's own topic `data.bars.<bar_type>`
(`interfaces.TOPIC_BAR`), so a native `Strategy.subscribe_bars(bar_type)` receives the
MarketDataService's bars with no bridge, exactly as in a backtest. Cross-sections
(`data.cross_section.<universe>`) have no counterpart in the Strategy API; the StrategyHost
republishes each as a `LiveTimeSlice` on `data.slices`. Order events go to
`events.order.<strategy_id>`, fills also to `events.fills`, stream events (connects, gaps,
stalls) to `events.stream`.

### How a candle strategy trades (`confirm_fills`)

A candle strategy calls `portfolio.execute_trade(...)` and reads `portfolio.cash` and
`portfolio.positions` straight after. Cross-Sectional Reversal sizes its buys on the cash its
sells have just raised, and stops buying once it holds `top_n` names. So the adapter waits for
each order's outcome before `execute_trade` returns, processing only order events and the OMS's
timers meanwhile (bars arriving are held and processed afterwards, in order), and books the
strategy's portfolio from the fills, one booking per fill. That is what the original engine did
(place, confirm, then book), and it is why the node's decisions match the original engine's:
without it a rebalance's buys are sized before its sells have filled, and the baskets differ
(`test_without_confirming_fills_the_baskets_differ_but_the_books_hold`).

The outcome is the outcome of the order's *family*: when the OMS re-prices an unfilled order it
cancels it and sends a child order with a new client order id (tags `parent`, `root`,
`attempt`; `oms.family(root)`), and the wait follows the family until no order of it is working
and none is to come (`OrderManager.family_done`); the children's fills are booked as the
strategy's trade, under the decision candle the strategy decided at. The OMS may also replace
the adapter's own order id with one in its format when it accepts the order; the adapter finds
its order by the object the OMS updated in place. Orders are DAY orders: a Moomoo GTC order
would outlive the session. `confirm_timeout_s` must cover the unfilled-order policy's whole
budget (`check-config` checks it).

### Bar finality (the MarketDataService)

A forming bar becomes final on a newer push for the same bar type, when the venue marks it
closed, or by the clock: its close plus a grace (`finality_grace_s`, 30 s for the S&P 100), but
only once its stream has delivered a push received after that close; a stream that has not (it
lags) gets a late grace of 10 minutes, so its last updates to the bar are not cut off. A bar
that **ends a session** (Hong Kong's 12:00 lunch break, the close, an early close) waits instead
for the next session's open plus the grace: the original engine made such a bar final only on
the next push or at shutdown with trading off, so no decision is ever taken on it while the
market is shut. At `stop()` every forming bar whose window has elapsed is made final (trading is
already off by then). A cross-section is published once complete. An incomplete one (at least
90% of the universe) is decided only when a later interval has a final bar, the moment the
original strategy closed it (on the next interval's first candle), and never by the clock; the
session's last one, if incomplete, stays undecided at stop, as in the original. Below 90%: a
`gap` StreamEvent and no decision.

### Records on disk (`MIDFREQ_ROOT/live_sessions/`)

- `state_<strategy>.json`: same format as today (positions, cash within the cap, rebalance clock).
- `session_<stamp>_<strategy>_<pid>_<n>.jsonl`: same events as today (start, candle, trade, stall, end).
- `orders_<date>.jsonl` (new): every order and order event with wall-clock times in UTC, the
  venue's ids and messages, and each step of the unfilled-order policy.
- `ops_<stamp>.jsonl` (new): the structured operations log.

Shadow and sim runs write to `live_sessions/shadow/` and `live_sessions/sim/`, and never write
bars to the store.

## Contracts settled at integration (17 October 2026)

1. **Refused cancel.** `OrderCancelRejected(OrderEvent)` with `reason` (`midfreq.core.model.events`,
   additive). The Moomoo, ccxt, simulated, shadow and replay clients emit it when the venue
   refuses a cancel (Moomoo and ccxt after a fresh query of the order, so a fill that won the race
   is reported first). It changes no order state. The OMS stops waiting for that cancel, asks the
   venue for the order's fills and state, and applies what the venue says; if the order still
   works, a re-price waits `wait_s` again before it tries again, a final cancel is checked and
   re-sent after `cancel_timeout_s`, and after more than `max_cancel_retries` refusals the policy
   stops for the family and leaves the order to the strategy's wait and to reconciliation.
2. **Unknown commission.** `OrderFilled.commission` stays a float. A venue that reports no fee
   sets `tags["commission_unknown"] = True` (Moomoo always; ccxt for a trade without a fee; the
   OMS for a reconciled `FillReport` whose `commission` is None). The PositionKeeper books the
   venue's fee model's estimate when the tag is set, and only then: a reported 0 stays 0.
3. **Fill key.** A fill is identified by (venue, the venue's order id or else the client order
   id, trade id), in the OMS, its journal and reconciliation: OKX numbers trades per instrument,
   not per account, so a trade id alone could make two of our fills one.
4. **Fee currency.** `OrderFilled.currency` is the currency the fee was charged in. When it is not
   the instrument's quote currency the PositionKeeper converts it at the fill price (OKX charges a
   spot buy's fee in the base asset: 0.0005 BTC at 60,000 USDT books 30 USDT); a fee in any other
   currency books the fee model's estimate, with a warning. Every booking keeps the venue's own
   amount and currency (`PositionLedger.bookings()`).
5. **Subscription limit unit.** `DataClient.subscription_limit()` counts subscriptions, one per
   instrument and data type, as Moomoo charges its quota (98 instruments' hourly bars are 98;
   their quotes 98 more). The MarketDataService counts the same way by default.
6. **Execution status.** `ExecutionClient.set_status_handler(handler)` (optional; a no-op unless
   the client reports) delivers `StreamStatus` 'disconnected' and 'reconnected' (once the client
   has caught up). Moomoo reports its trade context's drops and returns; ccxt its order stream's
   or its polls' failures and the first REST answer after them; the simulated venue its link.
   The node logs them and reconciles on 'reconnected'.
7. **Interfaces follow the real APIs.** `MarketDataService` describes LiveMarketDataService's
   inbound API (`stream_handlers`, `dispatch`, `on_bar_update`/`on_quote`/`on_stream_status`/
   `on_timer`, `resubscribe`, `last_price`, `fatal_stall`); `PositionKeeper` includes
   `avg_price`, `snapshot`, `restore`, `apply_corporate_action`; `OrderManager` includes `family`
   and `family_done`. The node calls these names only (no alternative spellings); its test
   stand-ins implement the same protocols.
8. **Wiring** (next section). The unfilled-order policy comes from the configuration; the
   preset `original_engine` (wait 30 s, no re-price, cancel, wait 30 s for the cancel, no retry)
   is what `midfreq-node replay` uses to reproduce the original engine, and the forward-test
   configuration sets 2 re-prices, the fix for the 15 October Lam Research order, in effect only
   once the node trades the strategy.
9. **The contract suite's conventions.** Every factory returns an unconnected client, accepts
   `environment='live'` (which must fail at construction or connect), reports fills under the
   trade id the driver gave, and lists submissions with symbol, side, quantity, price and order
   type.

## State, records and modes

- The mode defaults to **shadow**; paper and live are asked for explicitly (and live also needs
  `environment: live` on the account and `trading_env: real` in `config/connections.yaml`).
- In paper and live a candle strategy must name its state file (`state_name`); the node and
  check-config refuse a configuration without one, so no strategy can take another's state (an
  HK paper test of Cross-Sectional Reversal would otherwise have taken the US forward test's
  `state_crosssectionalreversal.json`). A saved state holding positions on venues the strategy does
  not trade is refused at start-up. The S&P 100 configuration names the forward test's file.
- One node per records folder: an exclusive `flock` on `<records>/.node.lock`.
- Shadow and sim never write bars into the real store (`data/`), whatever `persist` says.
- The state file is written atomically, synced, 0644. The journal repairs a torn last line.
- At start-up the OMS reconciles orders with the venue before the strategies resume; a strategy
  then books into its saved state every fill the journal applied after the state was saved (a
  crash between the two, or a fill made while the node was down), before the shrink-only resume.
  A rebalance is saved as done before its first order leaves, so a crash in the middle of it does
  not make the next session run it again.

## Configuration and wiring

`config/node_<name>.yaml` (`midfreq.trading.config`) names the node, its connectors, accounts,
universes and strategies, and one section per component. The default components are the
factories of `midfreq.trading.node.components`, which build the real classes with their real
constructor arguments:

| Section | Builds | Settings |
|---|---|---|
| `refdata` | `ReferenceDataService` (the connectors' InstrumentProviders; none in sim) | `corporate_actions`, `venues_path` |
| `journal` | `OrderJournal` in the records folder | `directory`, `fsync` |
| `positions` | `PositionLedger` (capital caps from the strategies; fee models from `venues.json`, a strategy's `commission_rate` taking precedence) | `default_capital`, `keep_bookings` |
| `risk` | `PreTradeRiskEngine` (reference price: the latest pushed price; lots and ticks: reference data; sessions: the calendar) | `RiskLimits` fields, `require_session_open`, `strategies: {id: {...}}`; a strategy's `fee_rate` defaults to its commission rate |
| `oms` | `OrderManagementSystem` on the accounts' execution clients (re-priced from the quote, else the last price) | `unfilled_order` (`UnfilledOrderPolicy` fields or `original_engine`), `reconcile_lookback_s`, `reconcile_grace_s`, `journal_lookback_days` |
| `marketdata` | `LiveMarketDataService` on each venue's data client (the store: the node's `store_dir`, else `data/`) | `min_cross_section_share`, `finality_grace_s`, `limit_unit`, `persist` |

An unknown setting in any section is an error (`midfreq-node check-config` lists every
problem), so a misspelt limit is never silently not applied. `components:` in the configuration,
or the `components=` argument, overrides a factory (tests use stand-ins written to the same
protocols). For a candle strategy the PositionKeeper's book is made equal to the strategy's own
portfolio after it is resumed and after each reconciliation (the original engine's rules govern
that portfolio and its state file); between those, both book the same fills.

The connectors, as built:

- **Moomoo** (`adapters/moomoo`). Orders go in OpenD's `remark` (our client order id), so pushes
  that overtake `place_order`'s reply are matched. Fills come from deals, one per deal id; where
  OpenD answers that deal queries are unsupported (some paper accounts), or a fill the order's
  status reports has no deal after `fill_grace` (5 s), a **synthetic fill** is booked from the
  order's dealt quantity and average price (trade id `<order id>#<qty>`,
  `tags["synthetic_fill"]`), the same id the reconciliation's `fills()` gives it. A safety-net
  poll runs behind the pushes. Streams: K-line pushes (stamped with the bar's end) and order-book
  quotes on their own quote context, re-subscribed after reconnects; an unsubscribe is deferred
  until OpenD's one-minute minimum has passed.
- **ccxt** (`adapters/ccxt`, OKX first). **Watch mode** (ccxt.pro): `watch_orders` and
  `watch_my_trades` on a background asyncio thread, with REST re-reads of open orders after an
  error, on resuming and every `resync_interval`. **Poll mode** (no ccxt.pro, or
  `updates='poll'`): `fetch_order` and `fetch_my_trades` for the open orders every
  `poll_interval`. Either way an order's trades are fetched before its end event, so a fill
  always precedes the cancel of the rest; client order ids are made to fit OKX's rule.
- **Simulated venue** (`midfreq.trading.testing.sim_venue`): deterministic matching on a test
  clock, latency and partial fills on a scheduler, the contract suite's fourth venue.

## Testing

Built first, then tested; the tests decided what was fixed. On 17 October 2026, on
`feature/live-trading`:

- **Whole repository** (`uv run pytest`): 1381 pass (44 skipped: slow tests needing the real
  `data/` cache and a Linux-only check, as on main).
- **Execution contract suite** (`packages/midfreq-data/tests/test_execution_contract.py`): 13
  scenarios (fill; partial fills; rejection; cancel; cancel racing a fill; refused cancel;
  repeated push; disconnect mid-order and reconciliation; connection status; venue order id;
  events to the handler only; paper by default; live refused) against Moomoo, ccxt in watch mode,
  ccxt in poll mode and the simulated venue: 52 pass, none skipped.
- **The takeover from the real state** (`test_from_the_real_state_the_first_node_rebalance_matches_the_original`):
  from the forward test's state of 17 October (clock 84, AMAT 74, MU 35, cash 33,310.91), a
  synthetic Monday (no rebalance, 84 -> 91) and Tuesday (rebalance due at 95, Tuesday 13:30): the
  node and the original engine send the same orders (sell AMAT 74 and MU 35, buy AMT 246, ABBV
  185, NKE 233), at the same prices, and end with the same clock, holdings, cash and state.
- **Replay equivalence on the real components** (`test_replay_equivalence_with_the_real_components`):
  the node, built with its default factories from `config/node_sp100.yaml` (no stand-in), fed the
  candles of the 13 recorded October sessions, makes the same decisions as the original engine
  fed the same candles through its replay harness: the same orders (symbol, side, quantity, fill
  price) on the same decision candles, the same rebalance clock every session, the same broker
  holdings and cash, with a clean broker (the recordings twice over: 26 sessions, 39 trades, 8
  rebalances), every 4th order unfilled, partial fills, and a crash mid-session. No order was
  denied by pre-trade risk, and the PositionKeeper's book equalled the strategy's own after every
  session.
- **Boundary**: no venue SDK outside the connectors; `lint-imports`: the package layers hold.
- **Python 3.10** (the VM's): the core, data and trading packages' tests and the integration
  tests (799) pass on Python 3.10.22 with `ops/vm/requirements-vm.txt` (plus ccxt 4.5.88).
- **Safety review of 17 October**: each finding has a test that reproduces it (fails before the
  fix, passes after); see the section below.
- **End to end on paper** (with approval, not yet run): the Hong Kong paper account during Hong
  Kong hours, a few stocks, while the US forward test is not running, so it cannot touch the
  forward test's account or exceed the quote quota.

## Safety review (17 October 2026)

Fixed, each with a reproducing test: adapter orders take their id from the OMS (C1); fill reports
booked only up to the venue's cumulative filled quantity, cumulative Moomoo reports as increments
(C2); venue fills on a rejected order always applied, and an order the venue still has open
reopened and cancelled at the venue (C3); shadow and sim never write the real bar store (C4);
the components wired on their real constructors, positions before risk (H1); the wait follows the
re-priced family (H2); state files and records as above (H3); venue ids learned from reports kept
(H4); trading off halts risk and pauses re-pricing, the calendar gates children (H5); position
reconciliation skips instruments with orders in flight (H6); account clients carry their
environment and a mismatch is not reconciled against (H7); the clock never cuts a lagging stream,
incomplete cross-sections on the original's timing (H8); the reconcile grace covers the clients'
window (M1); a node kill switch (M2); crash mid-rebalance (M3); ccxt does no network under its
lock (M4); subscriptions in batches, none to a venue that is down (M5); foreign items on the shared
account are INFO and errors are not repeated (M6); ccxt bars never closed by the clock (M7);
base-asset fees reduce the position (M8); the state file synced and 0644 (M9); SIGTERM ends a wait
for a fill (M10); the journal repairs a torn line (M11); ambiguous Moomoo send errors looked up by
remark (S1); HKEX's spread table (S2); an empty position list with holdings is a failed read
(S3); Moomoo's refreshed queries kept within OpenD's 10 per 30 s (S4).

## Known open issues

1. **The recorded live trades are not reproduced by either engine.** Fed the recorded candles with
   the real bar cache as warm-up, the original engine (through its own replay harness) and the
   node agree with each other exactly, and both differ from the trades the live sessions recorded
   at the same 7 decision times (the first rebalance falls on 1 October 10:30 in replay, 2
   October 16:00 live). So the recordings do not hold everything the live sessions saw (the
   rebalance clock and warm-up at each start, at least). Equivalence is established as "the same
   decisions as the original engine on the same inputs", not "the recorded trades".
2. **Risk's cash check is off for the S&P 100 strategy** (Jackson's decision): the strategy sizes
   against its own cash at the unrounded price, as the original engine did, and a cent of limit
   rounding would otherwise deny a buy the original sent. Other candle strategies keep the check.
3. **Corporate actions are not applied.** The reference data serves them and the PositionKeeper
   can book them, but no step of the node's day applies them, and the candle strategy's portfolio
   (which the state file holds) has no dividend booking.
4. **Two books per candle strategy.** The strategy's portfolio (the original rules, the state file)
   and the PositionKeeper's book are kept equal by copying the first into the second after
   resume and reconciliation; one book would be simpler.
5. **Sim mode is for replays.** `midfreq-node run --mode sim` needs a test clock or recorded data
   and refuses otherwise; there is no sim mode on live market data (shadow covers that).
6. **Shadow mode books nothing by default** (`fill_mode: none`): its strategy never holds a basket,
   so shadow decisions diverge after the first rebalance. `fill_mode: quote` needs quotes, which
   cost quote quota.
7. **ccxt's reconnects** are reported from REST answers (a quiet websocket only shows it is back
   when it next delivers); a fee charged in a third currency (OKX's OKB discount) is estimated, not
   converted.
8. **Not yet built for rollout**: a Hong Kong paper configuration (`config/node_hk_paper.yaml`)
   and runbook for the end-to-end test; alert sinks configured; journal files are named by
   wall-clock day, so a replay writes all its days into one file.
9. **Never run against a real OpenD.** Every Moomoo path (orders, deals, synthetic fills, K-line
   and order-book streams, the quote quota, lot sizes) is tested against the fake OpenD only.
10. **HKEX's spread table** in `config/venues.json` is the long-standing one; HKEX narrowed some
    bands in 2025: check it before Hong Kong trades.

## Rollout

1. Build and test on `feature/live-trading`; merge to `main` when the full suite, the replay
   equivalence and the boundary test pass (done on the branch; the merge is Jackson's).
2. Release with the new node installed but not scheduled; shadow and paper tests on Hong Kong
   (approval needed for the paper end-to-end test).
3. After the forward test ends (about 13 November 2026), switch the US strategy to the node in
   a release, with `ops/vm/startup_check.sh` extended to the node; the original engine stays
   installable for rollback for one release, then is retired.

## Work split (parallel build, October 2026)

| Part | Built |
|---|---|
| A: OMS, risk, positions, journal | `midfreq/trading/{execution,risk,portfolio}/` |
| B: Moomoo connector, live | `midfreq/data/adapters/moomoo/{execution,streaming}.py`, the connector's `execution_client()` and streams, `tests/support/fake_moomoo.py` |
| C: market data and reference data | `midfreq/trading/{data,refdata}/`, holidays in `config/venues.json` |
| D: ccxt connector, simulated venue, contract suite | `midfreq/data/adapters/ccxt/{execution,streaming}.py`, `midfreq/trading/testing/`, the execution contract suite, `tests/support/fake_{ccxt,sim}.py` |
| E: node, strategies, records, ops | `midfreq/trading/{node,strategy,persistence,ops,config,replay}/`, `execution/shadow.py`, `cli.py`, `ops/run_node.sh`, `config/node_*.yaml` |
| Integration | `midfreq/trading/node/components.py`, the contracts above, the wiring and the tests that join the parts |

The contracts they share are `midfreq/data/adapters/base.py` (connectors),
`midfreq/trading/core/` (components) and `tests/support/venue_driver.py` (fake venues for
the execution contract suite).
