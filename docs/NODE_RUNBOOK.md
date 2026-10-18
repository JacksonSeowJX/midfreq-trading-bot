# Trading node runbook

How the trading node (`midfreq.trading`, docs/LIVE_ARCHITECTURE.md) goes from a release to the VM
in two steps:

- **A. A Hong Kong paper test** (optional): the Hong Kong paper account, during Hong Kong hours, on
  six stocks; it exercises the order path on a separate account before the US rebalance.
- **B. The US forward test on the node** from Monday 19 October 2026: release 0.1.2, the checks, the
  switch, what to watch, and rollback to the session runner.

Both are done by hand on the VM (`gcloud compute ssh trading-bot --zone asia-southeast1-b`).
Times are SGT, the VM's clock; Hong Kong time is the same. The release model, the Deploy workflow
and the cutover this follows are in ops/README.md.

The pieces:

| File | What it is |
|---|---|
| `config/node_hk_paper.yaml` | Part A's configuration: Cross-Sectional Reversal on 5-minute bars of six Hong Kong stocks, HK$100,000 of the HK paper account, records in `live_sessions/hk_paper/` |
| `config/node_sp100.yaml` | The forward test's strategy on the node (`mode: shadow` in the file; the schedule passes `NODE_MODE=paper` after the switch) |
| `ops/run_node.sh` | The node's launcher (`NODE_CONFIG`, `NODE_MODE`, `DRY_RUN=1`, `--scheduled`); the console goes to `live_sessions/console_node_<stamp>.log` |
| `ops/vm/node_startup_check.sh` | The node's counterpart of `startup_check.sh`: the node from a release, in shadow mode, for a few minutes on a copy of a runtime; PASS or FAIL |
| `ops/vm/crontab.txt` | Runs the node for the US session (`run_node.sh`, `NODE_MODE=paper`); the session runner's line is kept as `#ROLLBACK ` |

## What `node_startup_check.sh` checks

```
ops/vm/node_startup_check.sh [minutes] [--config FILE] [--market US|HK|MIC] [--release X.Y.Z|DIR]
                             [--runtime DIR] [--no-account]
```

It refuses (FAIL, nothing started) while any session runs (`run_live` or the node), while a
market the configuration trades is open (30 minutes before its open to 30 minutes after its
close, weekdays; `--market` names another market to gate on), when an account in the
configuration is not `environment: paper`, or when `connections.yaml` (or
`MIDFREQ_MOOMOO_TRADING_ENV`) says `real`. Otherwise it copies the runtime (`config/`, `data/`,
`live_sessions/`) to `~/midfreq/checks/node_startup_<stamp>/`, runs the release's dry run of
`ops/run_node.sh`, then `midfreq-node run --mode shadow --duration <minutes>` against the copy
with `shadow.read_account` on (the paper account is read, never traded; `--no-account` skips it),
and requires:

- the banner in SHADOW mode and the shadow execution client ("orders are logged, never sent");
- OpenD reached: the connector connected and the market data stream connected;
- the paper account read: `capital capped at ... (account holds X)` with X not zero;
- the strategy's state restored, when it has one: the raw state read, the rebalance clock at the
  file's `cross_section_count`, its positions restored, with no position shrunk to the broker's
  (a shrink means bot and broker disagree);
- warm-up from the local store for every symbol; trading enabled; the configuration's number of
  instruments subscribed;
- a clean stop: "Session duration reached.", "stopping (duration)", the session summary, exit 0;
- no order: `orders sent 0` for every strategy, no SHADOW BUY/SELL line, no OMS `sent`, no order in
  `shadow_*.jsonl` or `orders_*.jsonl`;
- no ERROR or CRITICAL record in the console or in `ops_*.jsonl` (every line valid JSON, reaching
  the end of the session), no traceback;
- the real runtime unchanged: checksums of `config/*` and `live_sessions/state_*.json`, and no file
  under it written during the check (the 5-minute broker snapshots excepted).

It keeps `~/midfreq/checks/node_startup_<stamp>.log` and `..._records/` (the ops log, session
log, journals and the launcher's dry run) and removes the copy.

---

## A. Hong Kong paper test, end to end

**What it shows.** The node has traded only against fakes and a simulated venue. This run sends
real orders to the Moomoo paper gateway for one Hong Kong day: orders and their pushes, fills,
the unfilled-order policy (re-price, cancel), board lots, reconciliation of books and orders with
the broker, the records, and a clean stop. It is the node's first paper trading, so it needs
Jackson's approval and a day chosen by Jackson.

**Why it cannot touch the forward test.**

- Hours: Hong Kong trades 09:30-12:00 and 13:00-16:00 SGT; the node stops at 16:02. The US
  session runs from 21:35 (22:35 after the US clock change on 1 Nov) to about 04:01 (05:01) SGT.
- Quote quota: the node subscribes 6 instruments (5-minute bars, no quotes); the US session's 98
  are only held in the evening. OpenD frees the 6 when the node disconnects.
- Account: the only account in the configuration is `moomoo-hk` (`options: {market: HK}`), so the
  execution client opens a trade context filtered to Hong Kong; the US paper account is never
  opened. `trading_env: simulate` comes from `connections.yaml`, and in paper mode the node
  refuses a client that does not report paper.
- Files: the test runs with its own `MIDFREQ_ROOT`, `~/midfreq/hk_paper_test`. Nothing under
  `~/midfreq/runtime` (the forward test's state, bar store and records) is written.
- Code: the node runs from a *staged* release folder. `~/midfreq/current` stays 0.1.1, so the
  forward test's sessions run exactly the code they ran before. The crontab is not touched.

**Never** on Monday 19 October 2026 (Chung Yeung Festival, a Hong Kong holiday) or another day
in `config/venues.json`'s XHKG holidays, and never in the evening.

### The test in brief

| | |
|---|---|
| Stocks | HK.09988 Alibaba, HK.03690 Meituan, HK.01810 Xiaomi, HK.01299 AIA, HK.00939 CCB, HK.01398 ICBC (liquid, in `config/symbols.json`, 5-minute files in the store, 3 or more board lots per basket slot; not 00700 or 09888, which the paused roster traded in this account) |
| Strategy | Cross-Sectional Reversal, lookback 5, top_n 2, rebalance_every 6 on 5-minute bars: a rebalance every 30 minutes (about 10:00, 10:30, ..., 15:30; the 12:00 one trades at 13:00), ten in a day, the first two buys at about 10:00 |
| Capital | HK$100,000 of the paper account (about US$12,800); two slots of about HK$49,800 |
| Limits | order and position HK$60,000 each, gross HK$120,000, price band 3%, session open required, cash checked; strategy halts its rebalances after a 5% drawdown |
| Orders | DAY limits at the last price; unfilled after 20 s: cancelled and re-sent 0.3% through the latest price, up to twice, then the rest cancelled |
| Duration | 09:15 to 16:02 SGT (the launcher's default: two minutes after the 16:00 close) |
| Records | `~/midfreq/hk_paper_test/live_sessions/`: `console_node_<stamp>.log`, and in `hk_paper/` the state, session log, `orders_<date>.jsonl`, `ops_<stamp>_hk_paper.jsonl` |

### A1. Prerequisites (days before)

1. **Merged and green.** `feature/live-trading` and `feature/node-deploy` merged to `main` with
   the full suite passing. CI's `research` and `vm` jobs green (`vm` builds the bundle and runs the installer's smoke test on Python 3.10 with
   `requirements-vm.txt`, which now checks both node configurations).
2. **Release** (laptop), e.g. 0.1.2:

   ```bash
   ./ops/set_version.sh 0.1.2 && git commit -am "Release 0.1.2" && git push origin main
   # Actions -> Release -> Run workflow, version 0.1.2; wait for it to pass
   ```

3. **Stage it on the VM, not make it current**, when no session runs (09:00-21:00 SGT on a
   weekday, or a weekend): Actions -> Deploy -> `stage`, `0.1.2`, approve (or
   `./ops/deploy.sh v0.1.2 --stage`). The installer must say `smoke test passed` and
   `staged only`. Then on the VM:

   ```bash
   readlink ~/midfreq/current                      # releases/0.1.1: the forward test is unchanged
   ls ~/midfreq/releases/0.1.2/.venv/bin/midfreq-node ~/midfreq/releases/0.1.2/ops/run_node.sh
   ```

4. **The test's own root** (VM, once):

   ```bash
   R=~/midfreq/releases/0.1.2
   T=~/midfreq/hk_paper_test
   mkdir -p $T/live_sessions $T/data
   cp -a ~/midfreq/runtime/config $T/                  # connections.yaml: 127.0.0.1:11111, simulate
   cp $R/defaults/config/{node_hk_paper.yaml,venues.json,corporate_actions.json} $T/config/
   for c in 09988 03690 01810 01299 00939 01398; do cp -a ~/midfreq/runtime/data/HK_$c $T/data/; done
   grep trading_env $T/config/connections.yaml         # simulate
   ls $T/data/HK_*/5m.parquet                          # six files (warm-up; missing ones only warn)
   ```

   The store's Hong Kong 5-minute bars end in July/August 2026 (the laptop's copy). That is
   expected: the node never fills them from the broker's metered history, and with lookback 5 and
   rebalance_every 6 every ranking uses only bars received during the session.

5. **The HK paper account** (the dashboard's Live page, or the snapshot the VM writes every 5
   minutes): cash of at least HK$100,000, and the holdings before the test, written down.

   ```bash
   $R/.venv/bin/python - ~/midfreq/runtime/live_sessions/broker_moomoo-hk.json <<'EOF'
   import json, sys
   a = json.load(open(sys.argv[1]))
   print(a["as_of"], a["snapshot"])
   for p in a["positions"]:
       print(p["symbol"], p["quantity"], p["average_price"])
   EOF
   ```

   If one of the six stocks is already held (a leftover of the roster), note its quantity: the
   node never adopts it, and the books-versus-broker comparison subtracts it.

6. **Startup check in shadow, on the test root** (VM, with `R` and `T` as in step 4; outside
   09:00-16:30 SGT on a weekday, e.g. the evening before at 17:30, or the morning before 09:00;
   never while the US session runs, which the check refuses anyway):

   ```bash
   $R/ops/vm/node_startup_check.sh 5 --release 0.1.2 --runtime $T --config config/node_hk_paper.yaml
   ```

   It must print PASS with `note: hk-xsr has no saved state ...: a first run starts flat`, the
   HK paper account's assets in `account holds ...`, `6 of 6 symbols from the local store` and
   `running in shadow mode: 1 strategies, 6 instruments`. And the launcher, dry:

   ```bash
   cd $T && DRY_RUN=1 NODE_CONFIG=config/node_hk_paper.yaml MIDFREQ_ROOT=$T $R/ops/run_node.sh
   ```

   `mode : paper`, `market : Asia/Hong_Kong, close 16:00`, and
   `[dry run] would run: .../releases/0.1.2/.venv/bin/python -u -m midfreq.trading.cli run --config config/node_hk_paper.yaml --duration N`
   (N: the minutes to 16:02; 407 at 09:15).

### A2. The test day

**09:00, before the start.**

```bash
pgrep -af "[r]un_live|[m]idfreq.trading.cli run" || echo "no session"   # no session
systemctl is-active opend && ss -ltn | grep -q ':11111 ' && echo "OpenD up"
readlink ~/midfreq/current                                               # releases/0.1.1
sha256sum ~/midfreq/runtime/live_sessions/state_crosssectionalreversal.json > ~/hk_test_us_state.sha256
```

**09:15, start** (in the background, so a dropped SSH connection does not stop it):

```bash
cd ~/midfreq/hk_paper_test
nohup bash -c 'MIDFREQ_ROOT=$HOME/midfreq/hk_paper_test NODE_CONFIG=config/node_hk_paper.yaml \
    $HOME/midfreq/releases/0.1.2/ops/run_node.sh; echo "launcher exit status $?"' \
    > live_sessions/launcher_$(date +%Y%m%d).out 2>&1 &
sleep 60; L=$(ls -t live_sessions/console_node_*.log | head -1); echo $L
```

**09:16-09:30, the start-up** (`grep -v heartbeat $L | head -60`), in this order:

- `TRADING NODE hk_paper — PAPER mode`, `records: .../hk_paper_test/live_sessions/hk_paper`
- `connecting moomoo`, `moomoo HK execution client connected (paper)`,
  `account moomoo-hk: paper execution (MoomooExecutionClient)`
- `board lots: {...}` naming all six (no `board lot unknown`)
- `capital capped at 100,000.00 (account holds ...)` (the HK paper account's total)
- `[warmup] ...: the store is stale ...` warnings (expected), then
  `[warmup] complete — 6 of 6 symbols from the local store` and
  `rebalance clock resumed at 0 cross-sections, next rebalance due at 6 (rebalance_every=6)`
- `trading enabled`, `running in paper mode: 1 strategies, 6 instruments`, `stream connected: ...`
- possibly `moomoo HK ...: deal queries are not supported ...; fills will be taken from order status`:
  expected on some paper accounts (synthetic fills from the order's dealt quantity).

Anything else at WARNING or above before 09:30, or no `running in paper mode` line by 09:25: stop
(A3) and investigate. A restart later in the day works: it resumes the state the first start
saved (the rebalance clock, any positions, reconciled with the broker). Restart only once the
cause is understood.

**09:35 onwards, what to watch.**

```bash
tail -f $L | grep -v '| heartbeat:'                                  # everything but heartbeats
grep -E '\| (WARNING|ERROR|CRITICAL) ' $L                            # anything not routine
grep -E '\| oms +\| ' $L | tail -20                                  # sent, re-priced, families done
grep -E '"level": "(ERROR|CRITICAL)"' live_sessions/hk_paper/ops_*.jsonl | wc -l   # 0
```

- Every 5 minutes: `cross-section hk6 <time>: all 6 instruments`. A `gap` or `missing` now and
  then is logged as a WARNING; a `STALL` is not routine.
- About 10:00, the first rebalance: two strategy lines `[...] BUY <qty> HK.<code> limit <price>
  (live ..., decided at ...)`, each followed by the OMS's `sent hk-xsr-<date>-<seq>: BUY ...
  (venue order id ...)`, then either the fill (`[...] BUY <qty> HK.<code> @ <price> (order ...)`
  and `family ... done after 1 attempt(s)`) or, 20 s later, `re-price ... (attempt 2 of 3)` after
  the venue confirmed the cancel. Quantities are whole board lots.
- Then every 30 minutes: sells of what left the basket first, then buys. 12:00's rebalance is
  held over lunch (`final while the market is closed: held`) and trades just after 13:00.
- Every 5 minutes a heartbeat: `open orders 0` between rebalances, `held` at most two names.
- Every 2 minutes the node reconciles with the broker: silent when they agree; a `reconcile ...`
  WARNING is an adjustment, an ERROR `unexplained` is a mismatch to investigate.

Mid-day books against the broker (any time between rebalances; the snapshot is at most 5 minutes
old):

```bash
$HOME/midfreq/releases/0.1.2/.venv/bin/python - <<'EOF'
import json, os
H = os.path.expanduser("~/midfreq")
bot = json.load(open(f"{H}/hk_paper_test/live_sessions/hk_paper/state_hk_paper_xsr.json"))["positions"]
acct = json.load(open(f"{H}/runtime/live_sessions/broker_moomoo-hk.json"))
broker = {p["symbol"]: p["quantity"] for p in acct["positions"]}      # OpenD's codes, as the state's
print("snapshot", acct["as_of"])
for s in ("HK.09988", "HK.03690", "HK.01810", "HK.01299", "HK.00939", "HK.01398"):
    print(s, "bot", bot.get(s, {}).get("qty", 0), "broker", broker.get(s, 0))
EOF
```

### A3. Stopping

- **Normally** the node stops itself at 16:02: trading off, open orders cancelled, the 16:00 bar
  finalised with trading off, state saved, the session's bars written to the test root's store,
  session end, disconnect.
- **Early** (anything wrong, or Jackson's call):

  ```bash
  pkill -TERM -f "[m]idfreq.trading.cli run --config config/node_hk_paper.yaml"
  sleep 60; pgrep -af "[m]idfreq.trading.cli run" || echo stopped
  ```

  SIGTERM is the node's kill switch: it stops trading at once, cancels its working orders
  (`cancelling N open orders before shutdown`) and saves its state. Only if it is still there
  after two minutes: `pkill -KILL -f "[m]idfreq.trading.cli run"`, then in the Moomoo app (paper,
  Hong Kong) cancel any working order in the six stocks and write down what was filled.
- **Deadline**: at 16:10 `pgrep -af "[m]idfreq.trading.cli run"` must print nothing. A node still
  holding subscriptions at 21:35 would leave the US session short of quota.
- Do not deploy, switch or roll back a release while the test runs: `deploy.sh` and the Deploy
  workflow's gate look only for `run_live` and would not see the node.

### A4. After the close: pass criteria

```bash
cd ~/midfreq/hk_paper_test; L=$(ls -t live_sessions/console_node_*.log | head -1)
tail -3 live_sessions/launcher_*.out                        # launcher exit status 0
grep -A 8 "SESSION SUMMARY" $L
grep -cE '"level": "(ERROR|CRITICAL)"' live_sessions/hk_paper/ops_*.jsonl
sha256sum -c ~/hk_test_us_state.sha256                      # the forward test's state: OK
$HOME/midfreq/releases/0.1.2/.venv/bin/python - live_sessions/hk_paper <<'EOF'
import collections, glob, json, os, sys
last = {}
for path in sorted(glob.glob(os.path.join(sys.argv[1], "orders_*.jsonl"))):
    for line in open(path):
        rec = json.loads(line)
        if rec.get("order"):
            last[rec["order"]["client_order_id"]] = rec["order"]
roots = {o["tags"].get("root", cid) for cid, o in last.items()}
print(len(last), "orders in", len(roots), "families:", dict(collections.Counter(o["status"] for o in last.values())))
for cid, o in sorted(last.items()):
    print(cid, o["side"], o["quantity"], o["instrument_id"], o["status"], "filled", o["filled_qty"],
          "@", o["avg_px"], "venue", o["tags"].get("venue_order_id"), o["reason"] or "")
EOF
```

The test **passes** when all of these hold:

1. **Start-up** as in A2, before 09:30: paper HK execution, the six board lots, 6 of 6 warmed up,
   6 instruments subscribed, the stream connected.
2. **Data**: a cross-section of all 6 instruments nearly every 5 minutes of the sessions, no
   STALL stop, the 12:00 cross-section held over lunch and fed after 13:00.
3. **Decisions on the clock**: the first rebalance at about 10:00 with two buys, then one every
   30 minutes; never more than two names held (heartbeats, state file).
4. **Orders**: every order the strategy sent is in `orders_<date>.jsonl` with a venue order id;
   the number of families equals the summary's `orders sent`; every order ends FILLED or
   CANCELED, or DENIED for a re-price that pre-trade risk refused (no SUBMITTED, ACCEPTED or
   PARTIALLY_FILLED left after 16:02); no order REJECTED by
   the venue (a rejection for price, lot or permission is a FAIL to investigate: HK limit prices
   are rounded to `venues.json`'s 0.001 tick, which Moomoo paper accepted for the original
   engine's 2-decimal limits); quantities whole board lots.
5. **The unfilled-order policy**, wherever a first order rested: the cancel confirmed before the
   child was sent (never two working orders in one family), at most two re-prices, the rest
   cancelled with `unfilled after N attempts`, each step journaled with the venue's reply.
6. **Books equal the broker**: after 16:05, the state file's positions in the six stocks equal the
   HK paper account's holdings in them (less anything written down in A1.5), quantity for
   quantity; no `reconcile ... unexplained`; the state's cash equals HK$100,000 less buys plus
   sells less commissions (the session log's trades).
7. **Logs**: zero ERROR or CRITICAL records in `ops_*.jsonl`, no traceback in the console.
8. **Clean stop**: `Session duration reached.`, `stopping (duration)`, the summary,
   `[cache] wrote ... bars for 6 instruments`, launcher exit status 0, no node process at 16:10.
9. **Isolation**: the forward test's state checksum unchanged, and that evening's US session
   starts as usual (`tail ~/midfreq/logs/cron_sp100.log`; its console log shows 98 symbols
   streaming).

Not failures: the stale warm-up warnings; synthetic fills on a paper account without deal
queries; a re-priced buy denied by pre-trade risk for cash (`buy needs up to ...`, the kind of
denial in LIVE_ARCHITECTURE.md's open issue 2), which leaves that slot empty until the next
rebalance; a rare cross-section with a missing instrument. Each is still worth a line in the
test's notes.

Record the result: on the laptop,
`gcloud compute scp --recurse --zone asia-southeast1-b trading-bot:midfreq/hk_paper_test/live_sessions ./live_sessions/hk_paper_test_<YYYYMMDD>`
(the folder is git-ignored with the rest of `live_sessions/`).

### A5. Cleanup

- **Positions.** The strategy ends the day holding up to two of the six (paper money). Either
  - keep them for a **second day**, which tests the restore and the reconciliation against real
    holdings: the evening before, `node_startup_check.sh` as in A1.6 (it now restores
    `state_hk_paper_xsr.json` and checks it against the account), then the same start command; or
  - **close them**: sell them in the Moomoo app (paper, Hong Kong) after the test. The state file
    still claims them; delete the test root (below), or a later start shrinks the claims to the
    broker's zero (`[resume] ... broker holds 0`).
- **Files.** `tar -czf ~/midfreq/archive/hk_paper_test_<YYYYMMDD>.tar.gz -C ~/midfreq hk_paper_test && rm -rf ~/midfreq/hk_paper_test`,
  and `rm -rf ~/midfreq/checks/node_startup_*` once their results are noted; `rm ~/hk_test_us_state.sha256`.
- **Release.** The staged 0.1.2 stays installed and not current
  (`~/midfreq/releases/0.1.2/ops/vm/install_release.sh --status`); it is replaced by the switch
  release in part B. Nothing in the crontab or the runtime was changed, so nothing else to undo.

---

## B. The US forward test on the node: Sunday 18 to Wednesday 21 October 2026

The node runs the US session from Monday 19 October (paper account, `config/node_sp100.yaml`,
`state_name: crosssectionalreversal`, records in `~/midfreq/runtime/live_sessions/`). Monday's
session places no orders (the rebalance clock goes 84 -> 91); the first orders on the node are at
the next rebalance, clock 95 = Tuesday 13:30 ET = **Wednesday 01:30 SGT**. Nothing in this part may
run while a session is running (21:35-04:05 SGT on weekdays).

### B0. Sunday: release and stage (laptop, approval in GitHub)

```bash
# on main, after the node's pull request is merged
./ops/set_version.sh 0.1.2
git add pyproject.toml packages/*/pyproject.toml uv.lock && git commit -m "Release 0.1.2" && git push origin main
# Actions -> Release -> Run workflow, version 0.1.2; wait for it to pass
# Actions -> Deploy -> action "stage", version 0.1.2; approve. It installs 0.1.2 next to 0.1.1 and runs
# its smoke test; `current` stays 0.1.1, so nothing that runs changes.
```

### B1. Sunday: configuration and checks on the VM (market closed)

```bash
gcloud compute ssh trading-bot --zone asia-southeast1-b
ls ~/midfreq/releases; readlink ~/midfreq/current          # 0.1.1 0.1.2; current -> releases/0.1.1
R=~/midfreq/releases/0.1.2/defaults/config; C=~/midfreq/runtime/config
cp -n $R/node_*.yaml $R/corporate_actions.json $C/           # -n: never overwrite the runtime's own files
diff $C/venues.json $R/venues.json                           # expected: only the holiday and early-close fields
cp $C/venues.json ~/midfreq/backups/venues.json.20261018 && cp $R/venues.json $C/venues.json   # 0.1.1 reads it too
~/midfreq/releases/0.1.2/ops/vm/node_startup_check.sh 10 --release 0.1.2 --market US --no-account   # must print PASS
```

Shadow mode executes orders in a simulator, and the node refuses to reconcile a simulated executor
against the real paper account, so the shadow check runs with `--no-account`. The broker side is
checked by running Monday's real configuration (paper) for five minutes on a copy of the runtime
while the market is closed: no candle arrives and the next rebalance is 11 candles away, so it
cannot trade.

```bash
T=~/midfreq/checks/node_paper_$(date +%Y%m%d_%H%M%S); mkdir -p $T
cp -a ~/midfreq/runtime/config ~/midfreq/runtime/data ~/midfreq/runtime/live_sessions $T/
MIDFREQ_ROOT=$T NODE_CONFIG=config/node_sp100.yaml NODE_MODE=paper ~/midfreq/releases/0.1.2/ops/run_node.sh 5 > $T.log 2>&1
grep -E "execution client connected \(paper\)|reconciliation|restored positions|98 of 98|orders sent 0" $T.log   # all present; no ERROR in $T/live_sessions/ops_*.jsonl
```

PASS means: paper account connected, state restored (clock 84, AMAT 74, MU 35, cash 33,310.91, nothing
shrunk), warm-up 98 of 98, 98 instruments subscribed, a clean stop, zero orders, zero ERROR records,
and the real runtime unchanged. Anything else: stop here, the forward test stays on 0.1.1.

### B2. Monday 17:10-20:30 SGT: switch

```bash
# VM: backups
cp -p ~/midfreq/runtime/live_sessions/state_crosssectionalreversal.json ~/midfreq/backups/state_crosssectionalreversal.20261019.json
crontab -l > ~/midfreq/backups/crontab.20261019.txt          # the session runner's schedule
# laptop: Actions -> Deploy -> action "switch", version 0.1.2; approve  (current -> releases/0.1.2)
# VM: the release's schedule (the node runs the US session) and its deploy gate
crontab ~/midfreq/current/ops/vm/crontab.txt && crontab -l | grep -n 'run_node\|ROLLBACK'
cp ~/midfreq/bin/deploy_gate.sh ~/midfreq/backups/deploy_gate.sh.20261019
# the gate is not in the release bundle: copy it from the repository (laptop), then check the diff
#   gcloud compute scp --zone asia-southeast1-b ops/vm/deploy_gate.sh trading-bot:midfreq/bin/deploy_gate.sh.new
cd ~/midfreq/bin && diff deploy_gate.sh deploy_gate.sh.new; chmod 755 deploy_gate.sh.new && mv deploy_gate.sh.new deploy_gate.sh; cd
DRY_RUN=1 FAKE_ET=2026-10-19T09:35 MIDFREQ_ROOT=$HOME/midfreq/runtime NODE_CONFIG=config/node_sp100.yaml NODE_MODE=paper \
    ~/midfreq/current/ops/run_node.sh --scheduled             # gate open, paper, duration 386, configuration valid
~/midfreq/current/ops/vm/maintain.sh && tail -1 ~/midfreq/logs/health.log   # release=0.1.2 opend=up
ls -l ~/midfreq/runtime/live_sessions/broker_*.json          # still refreshed every 5 minutes from 0.1.2
```

### B3. Monday's session (no orders expected)

```bash
tail -f $(ls -t ~/midfreq/runtime/live_sessions/console_*.log | head -1)
```

- **21:35-21:45 SGT:** started by cron; paper account connected; state restored (clock 84, AMAT 74,
  MU 35, cash 33,310.91); warm-up 98 of 98; 98 subscribed; trading on; no ERROR in `ops_*.jsonl`.
- **22:30 SGT:** the first bar of the day arrives and the clock moves to 85 (one per hour after that).
- **If nothing has started by 21:40 SGT:** go to B5 and start the session runner by hand.
- **After the close (about 04:05 SGT):** session end logged; clock 91; no orders; the state file is
  valid JSON; on the laptop `./ops/fetch_live_logs.sh` brings the session in and the dashboard's forward
  test shows 16 sessions with the holdings unchanged.

### B4. The first rebalance on the node: Wednesday 01:30 SGT (Tuesday 13:30 ET)

Watch `orders_<date>.jsonl` and the console: the decision on the 13:30 ET candle, sells first, then
buys sized on the cash they raise; each order FILLED, or re-priced (at most twice) and then filled or
cancelled with the venue's reply logged. Afterwards the bot's holdings equal the broker's
(`broker_moomoo-us.json`), and the dashboard shows the trades with their decision candle.

### B5. Rollback to the session runner (takes effect from the next session)

```bash
# laptop: Actions -> Deploy -> action "rollback" (current -> releases/0.1.1); approve
# VM:
crontab ~/midfreq/backups/crontab.20261019.txt               # the session runner's schedule
cp ~/midfreq/backups/deploy_gate.sh.20261019 ~/midfreq/bin/deploy_gate.sh
```

The state file has the same format under both engines, so the session runner resumes whatever the
node saved; restore `state_crosssectionalreversal.20261019.json` only if the node's file is damaged
and no trade has happened since the backup. If Monday's node session never started, start the
session runner by hand for the rest of the session (it runs to the close):

```bash
crontab ~/midfreq/backups/crontab.20261019.txt
MIDFREQ_ROOT=$HOME/midfreq/runtime nohup ~/midfreq/releases/0.1.1/ops/run_sp100_forward_test.sh >> ~/midfreq/logs/cron_sp100.log 2>&1 &
```

## C. The Hong Kong session on the node: from Tuesday 20 October 2026

What runs. `config/node_hk.yaml`: the node on the Hong Kong paper account (`moomoo-hk`) during Hong
Kong hours, started by the schedule at 09:35 SGT on weekdays (`NODE_MODE=paper`) and stopped at
16:02. It trades the ML Direction Classifier on HSBC (HK.00005) with the settings in
`config/hk_forward_test.json`, chosen by `scripts/live/pick_hk_live_config.py`. It is an
infrastructure test of a second market, not a validated strategy: the Hong Kong search of 18 Oct
2026 (`research/studies/hk_*.py`) found nothing fit to trade. It keeps everything apart from the US
session: another account, its own state file (`state_hk_ml_00005.json`) and its own records folder,
`live_sessions/hk/` (the order journal is one file per UTC day, which the two would otherwise share).
The two sessions never overlap, so each has the account's whole quote quota.

### C1. Install (after the release that carries it is current)

```bash
gcloud compute ssh trading-bot --zone asia-southeast1-b
R=~/midfreq/current/defaults/config; C=~/midfreq/runtime/config
cp -n $R/node_hk.yaml $R/hk_forward_test.json $C/         # -n: never overwrite the runtime's own files
crontab -l > ~/midfreq/backups/crontab.$(date +%Y%m%d).txt
crontab ~/midfreq/current/ops/vm/crontab.txt && crontab -l | grep -n 'run_node'   # 09:35 HK and 21:35 US
```

The node warms up from the runtime's candle store, so HSBC's hourly file must be current (refresh it
as on 18 Oct: `MoomooProvider().get_historical_data('HK.00005', HOUR_1, ...)` appended with
`DataStorage.append_data`, a backup of the file first). After each paper session the node appends the
session's bars itself.

### C2. Checks

- Before the first session, with the market closed: `node_startup_check.sh 10 --release <version>
  --config config/node_hk.yaml --market HK --no-account` must print PASS.
- During Hong Kong hours, without sending anything: the same check in shadow mode for 70 minutes
  sees an hourly bar close and the classifier decide.
- Each session: `tail -f ~/midfreq/runtime/live_sessions/console_node_*.log` (the newest), the
  launcher's lines in `~/midfreq/logs/cron_hk.log`, the records in `~/midfreq/runtime/live_sessions/hk/`
  and the Hong Kong broker panel of the dashboard.

### C3. Stopping it

Comment the `node_hk.yaml` line in a copy of the crontab and install that, or roll back the release.
SIGTERM to a running node stops trading at once and cancels its working orders, as in the US.

