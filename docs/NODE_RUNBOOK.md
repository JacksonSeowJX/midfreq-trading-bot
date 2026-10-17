# Trading node runbook

How the trading node (`midfreq.trading`, docs/LIVE_ARCHITECTURE.md) goes from a release to the VM
in two steps:

- **A. The first end-to-end paper test**: the Hong Kong paper account, during Hong Kong hours,
  on six stocks, while the S&P 100 forward test keeps trading on the original engine.
- **B. The switch of the US strategy** from the original engine to the node, after the forward
  test ends (about 13 November 2026).

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
| `ops/node_release.patch` | Makes releases carry the node (seven wheels, the launcher, the checks, the node in the installer's smoke test). Applied once, on main, after the integration is merged |

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

1. **Merged and green.** `feature/live-trading` merged to `main` with the full suite passing, and
   `ops/node_release.patch` applied in the same or the next commit
   (`git apply ops/node_release.patch && git rm ops/node_release.patch`). CI's `research` and `vm`
   jobs green (`vm` builds the bundle and runs the installer's smoke test on Python 3.10 with
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

## B. Switching the US strategy to the node (after the forward test)

After the forward test ends (about 13 November 2026) and its final numbers are taken. The
strategy keeps trading the same paper account, the same configuration
(`config/sp100_forward_test.json`) and the same state file: the node reads and writes
`live_sessions/state_crosssectionalreversal.json` and `session_*.jsonl` in the original engine's
formats, so the switch continues the rebalance clock and positions, and a rollback hands them
back. Modelled on ops/README.md's cutover; the switch is made when no session runs (weekend, or
05:30-21:00 SGT on a weekday).

### B0. Before the switch release

1. **Part A passed**, and what it found is fixed.
2. **Open issues decided** (LIVE_ARCHITECTURE.md, Known open issues): the cent of rounding that
   can deny a cash-constrained buy (size against the rounded limit, or
   `risk.strategies.sp100-xsr.check_cash: false`); corporate actions not applied (as with the
   original engine; dividends reach the account, not the strategy's cash); alerts (off by default:
   `node.email_alerts` and the `MIDFREQ_ALERT_*` variables on the VM, if wanted).
3. **Code for the switch release** (on main, tested):
   - `ops/vm/crontab.txt`: the forward test's line replaced by the node's, the original kept
     commented for one release:

     ```
     # S&P 100 strategy on the trading node (paper). Fires at 21:35 and 22:35 because the US open
     # moves with daylight saving; the launcher proceeds only within 30 minutes after the open.
     35 21,22 * * 1-5  MIDFREQ_ROOT=$HOME/midfreq/runtime NODE_CONFIG=config/node_sp100.yaml NODE_MODE=paper $HOME/midfreq/current/ops/run_node.sh --scheduled >> $HOME/midfreq/logs/cron_sp100.log 2>&1
     # The original engine, for rollback during this release: remove "#ORIGINAL " and comment out the line above.
     #ORIGINAL 35 21,22 * * 1-5  MIDFREQ_ROOT=$HOME/midfreq/runtime $HOME/midfreq/current/ops/run_sp100_forward_test.sh --scheduled >> $HOME/midfreq/logs/cron_sp100.log 2>&1
     ```

     and `tests/e2e/test_vm_schedule.py::test_the_session_fires_at_both_us_open_times_on_weekdays`
     looking for `run_node.sh`, with `NODE_MODE=paper` and `NODE_CONFIG=config/node_sp100.yaml`.
     `config/node_sp100.yaml` keeps `mode: shadow`, so a run by hand never trades; only the
     schedule says paper. The cron log stays `cron_sp100.log`, which maintain.sh archives.
   - **Session detection.** `ops/deploy.sh`, `ops/vm/deploy_gate.sh`, `ops/vm/tidy_home.sh`,
     `ops/fetch_live_logs.sh` and `ops/vm/startup_check.sh` find a session with
     `pgrep -f '[r]un_live'`, which does not match the node (`python -m midfreq.trading.cli run`):
     deploys would no longer wait for a session to end. Use
     `'[r]un_live|[m]idfreq\.trading\.cli run'`, as `node_startup_check.sh` does. `deploy_gate.sh`
     is installed by hand: after the release, copy it to `~/midfreq/bin/deploy_gate.sh` again
     (ops/README.md, Deploy workflow).

### B1. Release and deploy

```bash
# laptop
./ops/set_version.sh X.Y.Z && git commit -am "Release X.Y.Z" && git push origin main
# Actions -> Release -> Run workflow, X.Y.Z; wait for it to pass
./ops/fetch_live_logs.sh                       # the record up to the switch
```

Deploy (Actions -> Deploy -> `deploy`, `X.Y.Z`, approve; or `./ops/deploy.sh vX.Y.Z`) when no
session runs. `current` then points to X.Y.Z, but the crontab on the VM is still the old one, so
the next session would still run the original engine (from X.Y.Z): nothing trades on the node
until B3. The installer must report `smoke test passed` and
`127.0.0.1:11111, trading environment SIMULATE`.

The node's configuration files in the runtime. The cutover's `cp -n` never overwrites, so a
runtime keeps whatever copy it first received: `venues.json` is there from before the node (no
holidays), and `node_sp100.yaml` and `corporate_actions.json` are missing. Look at what the
release would change, then take the release's three:

```bash
cd ~/midfreq/runtime/config
for f in node_sp100.yaml venues.json corporate_actions.json; do
    [ -e $f ] && { echo "== $f: runtime -> X.Y.Z"; diff $f ~/midfreq/current/defaults/config/$f; }
done
# expected: only the release's own changes (venues.json's holidays and early closes). Anything
# else was edited on the VM: carry it into the new copy after the cp below.
mkdir -p ~/midfreq/backups/config_before_node && cp -a venues.json ~/midfreq/backups/config_before_node/
cp ~/midfreq/current/defaults/config/{node_sp100.yaml,venues.json,corporate_actions.json} .
```

(`venues.json`'s new holiday fields are additive: the original engine reads the file as before.
`sp100_forward_test.json` and `connections.yaml` are the VM's own and stay.)

### B2. Checks, market closed

On the VM, outside 09:00-16:30 New York time (a weekend, or 05:30-20:30 SGT on a weekday):

```bash
~/midfreq/current/ops/vm/startup_check.sh 5          # the original engine from X.Y.Z: PASS (the rollback path)
~/midfreq/current/ops/vm/node_startup_check.sh 10    # the node, shadow, on a copy of the runtime: PASS
```

The node check must show `[resume] raw state on disk: {...}` with the forward test's positions,
`[resume] rebalance clock at <the state file's cross_section_count> cross-sections`,
`[resume] restored positions: {...}` with no position shrunk, the US paper account in
`account holds ...`, `98 of 98 symbols from the local store` and
`running in shadow mode: 1 strategies, 98 instruments`.

The launcher as the schedule will start it, dry (a date of the next session, 09:35:01 New York
time):

```bash
cd ~/midfreq/runtime && DRY_RUN=1 FAKE_ET=2026-11-16T09:35:01 NODE_CONFIG=config/node_sp100.yaml NODE_MODE=paper \
    MIDFREQ_ROOT=$HOME/midfreq/runtime ~/midfreq/current/ops/run_node.sh --scheduled
```

It must print `[gate] Mon 2026-11-16 09:35 EST: market open, running`, `mode : paper`,
`duration : 386 min`, `configuration config/node_sp100.yaml: valid` with
`account moomoo-us: connector moomoo, environment paper`, every component `ok`, and
`[dry run] would run: .../current/.venv/bin/python -u -m midfreq.trading.cli run --config config/node_sp100.yaml --mode paper --duration 386`.
With `FAKE_ET=2026-11-16T10:35:01` it must print `not the open, skipping` and nothing else.

Backups, then go on only if all of the above passed:

```bash
D=$(date +%Y%m%d)
cp -a ~/midfreq/runtime/live_sessions/state_crosssectionalreversal.json ~/midfreq/backups/state_crosssectionalreversal.before_node_$D.json
crontab -l > ~/midfreq/backups/crontab.before_node_$D.txt
```

### B3. Schedule

```bash
crontab ~/midfreq/current/ops/vm/crontab.txt && crontab -l | grep -E 'run_node|run_sp100'
```

One active line with `run_node.sh ... NODE_MODE=paper`, and the original's line starting
`#ORIGINAL`.

### B4. The first sessions

**At the open + 5 minutes** (22:40 SGT in November; 21:40 under US daylight saving):

```bash
pgrep -af "[m]idfreq.trading.cli run"                     # one node, --mode paper --duration 386
tail -8 ~/midfreq/logs/cron_sp100.log                     # [gate] ... market open, running; mode : paper
L=$(ls -t ~/midfreq/runtime/live_sessions/console_node_*.log | head -1); grep -v heartbeat $L | head -50
```

The start-up, in order: `TRADING NODE sp100 — PAPER mode`; `moomoo US execution client connected
(paper)` and `account moomoo-us: paper execution (MoomooExecutionClient)`; `capital capped at
100,000.00 (account holds ...)`; `[resume] raw state on disk`, `[resume] rebalance clock at <N>`,
`[resume] restored positions: {...}` equal to the broker's (no `state says ... but broker holds`);
`[warmup] complete — 98 of 98`; `trading enabled`; `running in paper mode: 1 strategies, 98
instruments`; `stream connected`. No WARNING other than these: `grep -E '\| (WARNING|ERROR|CRITICAL) ' $L`.

**During the session**: every hour `cross-section sp100-xsr <time>: all 98 instruments` (or at
least 89, with the missing ones named); heartbeats every 5 minutes with `open orders 0` between
rebalances; at a rebalance (the state's `next_rebalance_at`), sells and then buys, each `sent`,
filled or re-priced (`re-price ... (attempt 2 of 3)`), `family ... done`, and the strategy's
`[...] SELL/BUY <qty> US.<sym> @ <price> (order ...)`; zero ERROR records:
`grep -cE '"level": "(ERROR|CRITICAL)"' ~/midfreq/runtime/live_sessions/ops_*_sp100.jsonl`.

**After the close** (05:02 SGT in November) and the next morning (laptop: `./ops/fetch_live_logs.sh`):

- `Session duration reached.`, `stopping (duration)`, the summary, and
  `[cache] wrote ... bars for 98 instruments` in the console; the launcher's exit status 0;
- 7 new candles per symbol, the rebalance clock (`cross_section_count` in the state file)
  advanced by 7, as after every original-engine session;
- bot positions equal the broker's (the state file against `broker_moomoo-us.json`), as checked
  after the forward test's sessions;
- every order in `orders_<date>.jsonl` final, with venue order ids; zero ERROR records;
- the dashboard's Live page shows the session (the same `session_*.jsonl` format).

Keep checking this way for the first week. Then, in a later release: drop the `#ORIGINAL` line;
one release after that, retire the original engine (`midfreq-live`, `run_sp100_forward_test.sh`,
`startup_check.sh`), as LIVE_ARCHITECTURE.md's rollout says.

Calendar notes: the launcher's `--scheduled` gate knows weekdays, not holidays, as the original
did. On Thanksgiving (26 Nov 2026) the node starts, its calendar says closed, no bar comes and no
order can be sent; it idles until its duration ends. On 27 Nov (13:00 close) it stops trading at
13:00 New York time and runs on idle to 16:02.

### B5. Rollback to the original engine

Any time while the original engine is still in the release (until the `#ORIGINAL` line is
removed):

1. **If a node session is running and misbehaving**, stop it cleanly (it stops trading, cancels its
   working orders and saves its state):

   ```bash
   pkill -TERM -f "[m]idfreq.trading.cli run"; sleep 60; pgrep -af "[m]idfreq.trading.cli run" || echo stopped
   ```

   Only if it is still there after two minutes: `pkill -KILL -f "[m]idfreq.trading.cli run"`, then
   cancel any working order in the Moomoo app (paper, US) and write down what filled.
2. **The schedule** back: `crontab ~/midfreq/backups/crontab.before_node_<D>.txt && crontab -l`
   (or edit the crontab: comment out the node's line, remove `#ORIGINAL ` from the other).
3. **The state.** The node writes the original engine's format, so the original engine resumes
   from it, clock and positions included. With the market closed:
   `~/midfreq/current/ops/vm/startup_check.sh 5` must PASS. Its resume shrinks any claim the
   broker does not hold, but never adopts a holding the state does not claim (a fill the node did
   not book): compare the state file with `broker_moomoo-us.json` and the orders journal,
   and correct by hand (sell the surplus in the app, or, only if nothing traded since, restore
   `~/midfreq/backups/state_crosssectionalreversal.before_node_<D>.json`).
4. **If the release itself is at fault** (both engines): Actions -> Deploy -> `rollback` (or
   `./ops/deploy.sh --rollback`) returns `current` to the previous release, and step 2's crontab
   runs the original engine from it. The runtime's refreshed node files do not affect the original
   engine; `~/midfreq/backups/config_before_node/` has the previous copies.
5. Record what happened (the console, `ops_*.jsonl` and `orders_*.jsonl` of the failed sessions)
   before the next attempt.
