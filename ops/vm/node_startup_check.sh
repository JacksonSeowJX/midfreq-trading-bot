#!/bin/bash
# The trading node's startup check, the counterpart of startup_check.sh for midfreq.trading
# (docs/NODE_RUNBOOK.md). Starts the node from an installed release in SHADOW mode for a few
# minutes, on a COPY of the runtime, then checks its console log and its ops_*.jsonl: OpenD
# reached, the paper account read, the strategy's state restored, warm-up, subscriptions, no
# order created, no ERROR record, a clean stop; and that the real runtime is unchanged.
#
# Shadow mode sends nothing: every order would go to the ShadowExecutionClient, which logs it.
# The check also refuses to run while a session is running (the original engine or a node), or
# while a market the configuration trades is open (30 minutes before its first open to 30 minutes
# after its last close, on its weekdays): with the market closed no bar completes, so no decision
# can come due, and the check's subscriptions never compete with a session's for the account's
# 100-quote quota. --market names the market whose hours decide instead.
#
# By default the node also reads the configured paper account (shadow.read_account, read-only:
# balance and positions), so the log shows the account was reached and the state is restored
# against the broker's positions, as a paper session restores it. --no-account skips that.
#
# Usage: ops/vm/node_startup_check.sh [minutes] [options]      (minutes: default 5, at least 2)
#   --config FILE   the node configuration, relative to the runtime (default config/node_sp100.yaml)
#   --market M      US, HK or a venue MIC (XNYS, XHKG, ...): the market whose hours gate the check
#                   (default: every venue the configuration trades)
#   --release R     a release version under $MIDFREQ_HOME/releases, or a release folder
#                   (default $MIDFREQ_HOME/current; a staged release is checked with --release X.Y.Z)
#   --runtime DIR   the runtime to copy (default $MIDFREQ_HOME/runtime)
#   --no-account    do not read the broker account
#   MIDFREQ_HOME (default ~/midfreq); CHECK_NOW (an ISO time with offset, for testing the gate)
# Prints PASS or FAIL and exits 0 or 1. Kept in $MIDFREQ_HOME/checks/: node_startup_<stamp>.log
# (the console) and node_startup_<stamp>_records/ (the node's records: ops_*.jsonl, session log,
# journals, the launcher's dry run); the copy itself is removed.

set -uo pipefail
MINUTES=5
CONFIG=config/node_sp100.yaml
MARKET=
RELEASE_ARG=
READ_ACCOUNT=1
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
RUNTIME="$HOME_DIR/runtime"
CHECKS="$HOME_DIR/checks"
STAMP=$(date +%Y%m%d_%H%M%S)

fail() { echo "FAIL: $*"; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG=${2:-}; shift 2 || fail "--config needs a file" ;;
        --market) MARKET=${2:-}; shift 2 || fail "--market needs a market" ;;
        --release) RELEASE_ARG=${2:-}; shift 2 || fail "--release needs a version or a folder" ;;
        --runtime) RUNTIME=${2:-}; shift 2 || fail "--runtime needs a folder" ;;
        --no-account) READ_ACCOUNT=0; shift ;;
        -h|--help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        [0-9]*) MINUTES=$1; shift ;;
        *) fail "unknown argument $1 (see --help)" ;;
    esac
done
[[ "$MINUTES" =~ ^[0-9]+$ ]] && [ "$MINUTES" -ge 2 ] || fail "minutes must be a whole number, at least 2"
case "$CONFIG" in /*|"") fail "--config is a path relative to the runtime, such as config/node_sp100.yaml" ;; esac
if [ -z "$RELEASE_ARG" ]; then RELEASE="$HOME_DIR/current"
elif [ -d "$RELEASE_ARG" ]; then RELEASE=$RELEASE_ARG
else RELEASE="$HOME_DIR/releases/$RELEASE_ARG"; fi
PY="$RELEASE/.venv/bin/python"
NODE="$RELEASE/.venv/bin/midfreq-node"

[ -x "$PY" ] || fail "no installed release at $RELEASE"
[ -x "$NODE" ] || fail "the release at $RELEASE has no midfreq-node: the midfreq-trading package is not installed"
[ -d "$RUNTIME/config" ] || fail "no runtime at $RUNTIME"
[ -f "$RUNTIME/$CONFIG" ] || fail "the runtime has no $CONFIG (the release ships one in $RELEASE/defaults/config/)"
SESSIONS='[r]un_live|[m]idfreq\.trading\.cli run|[m]idfreq-node run'
pids=$(pgrep -f "$SESSIONS" | paste -sd, -)
[ -z "$pids" ] || fail "a trading session is running: $(ps -o pid=,args= -p "$pids" | cut -c1-140 | paste -sd';' -)"

# The configuration, read by the release itself (nothing is connected, nothing written): its
# accounts must be paper and OpenD's trading environment simulate; the markets it trades must
# be closed; and what a healthy start must restore.
PRE=$(cd "$RUNTIME" && MIDFREQ_ROOT="$RUNTIME" "$PY" - "$CONFIG" "$MARKET" 2>&1 <<'PYEOF'
import json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from midfreq.core.venues.spec import default_venues
from midfreq.trading.config import ConfigError, instrument_for_symbol, load_config, resolve_strategy_class

path, market = sys.argv[1], sys.argv[2].strip().upper()
try:
    cfg = load_config(path, mode="shadow")
except ConfigError as e:
    sys.exit(f"the configuration is invalid: {e}")
venues = default_venues()
problems, out = [], [f"node {cfg.node.name}"]
base = cfg.node.records_dir or "live_sessions"            # as TradingNode._default_records_dir
out.append(f"records {base.rstrip('/')}/shadow")
seed = cfg.node.seed_state_from or "live_sessions"         # as TradingNode._seed_state_dir (shadow)
for aid, acc in sorted(cfg.accounts.items()):
    out.append(f"account {aid} {acc.connector} {acc.environment}")
    if acc.environment != "paper":
        problems.append(f"account {aid} says environment {acc.environment!r}: this check is for paper accounts")
try:
    from midfreq.core.connections import get_connection
    for name in sorted({acc.connector for acc in cfg.accounts.values()}):
        try:
            conn = get_connection(name)
        except KeyError:
            continue
        env = str(conn.get("trading_env", "simulate")).lower()
        out.append(f"gateway {name} {conn.get('host', '?')}:{conn.get('port', '?')} {env}")
        if env in ("real", "live"):
            problems.append(f"the {name} connection says trading_env {env!r} (config/connections.yaml or "
                            f"MIDFREQ_{name.upper()}_TRADING_ENV): real money; refusing")
except ImportError:
    pass
instruments, traded = set(), set()
for s in cfg.strategies:
    syms = cfg.universes[s.universe].symbols
    for sym in syms:
        iid = instrument_for_symbol(sym, venues)
        instruments.add(str(iid))
        traded.add(str(iid.venue))
    if s.kind != "legacy":
        continue                                            # only candle strategies keep a state file
    slug = s.state_name or resolve_strategy_class(s.cls)[0].__name__.lower()
    state = os.path.join(seed, f"state_{slug}.json")
    if os.path.exists(state):
        with open(state) as f:
            saved = json.load(f)
        held = sum(1 for p in (saved.get("positions") or {}).values() if float(p.get("qty", 0)) > 0)
        out.append(f"state {s.id} {state} present {held} {int(saved.get('cross_section_count') or 0)}")
    else:
        out.append(f"state {s.id} {state} absent 0 0")
out.append(f"instruments {len(instruments)}")
gate = {"US": ["XNYS", "XNAS"], "HK": ["XHKG"]}.get(market, [market]) if market else sorted(traded)
now_utc = datetime.fromisoformat(os.environ["CHECK_NOW"]) if os.environ.get("CHECK_NOW") else datetime.now(ZoneInfo("UTC"))
for v in gate:
    try:
        spec = venues.get(v)
    except KeyError:
        sys.exit(f"unknown market {v!r}: use US, HK or a venue MIC from config/venues.json")
    now = now_utc.astimezone(ZoneInfo(spec.timezone))
    if spec.is_24_7:
        out.append(f"market {v} open {now:%a_%H:%M} {spec.timezone} 24/7")
        continue
    first, last = spec.sessions[0].open, spec.sessions[-1].close
    local = now.replace(tzinfo=None)
    start = datetime.combine(local.date(), first) - timedelta(minutes=30)
    end = datetime.combine(local.date(), last) + timedelta(minutes=30)
    is_open = local.weekday() in spec.weekdays and start <= local <= end
    out.append(f"market {v} {'open' if is_open else 'closed'} {now:%a_%H:%M} {spec.timezone} {first:%H:%M}-{last:%H:%M}")
if problems:
    sys.exit("; ".join(problems))
print("\n".join(out))
PYEOF
) || fail "$(tail -3 <<<"${PRE:-the release could not read $CONFIG}")"
NAME=$(awk '$1=="node"{print $2}' <<<"$PRE")
REC=$(awk '$1=="records"{print $2}' <<<"$PRE")
INSTRUMENTS=$(awk '$1=="instruments"{print $2}' <<<"$PRE")
open_markets=$(awk '$1=="market" && $3=="open"{printf "%s (%s %s, %s) ", $2, $4, $5, $6}' <<<"$PRE")
[ -z "$open_markets" ] || fail "a market the check is gated on is open (or within 30 minutes of it): ${open_markets}run this outside its hours, or name another market with --market"
case "$REC" in ""|/*|*..*) fail "unexpected records folder '$REC' in $CONFIG" ;; esac

echo "release: $(readlink -f "$RELEASE" 2>/dev/null || echo "$RELEASE")"
echo "runtime: $RUNTIME"
echo "config : $CONFIG ($NAME), shadow mode, $MINUTES minutes$([ "$READ_ACCOUNT" = 1 ] && echo ", reading the paper account")"
awk '$1=="market"{print "market : " $2 " " $3 " (" $4 " " $5 ", hours " $6 ")"} $1=="gateway"{print "gateway: " $2 " " $3 ", trading environment " $4}' <<<"$PRE"

mkdir -p "$CHECKS"
COPY="$CHECKS/node_startup_$STAMP"
LOG="$CHECKS/node_startup_$STAMP.log"
RECORDS="$CHECKS/node_startup_${STAMP}_records"
MARK="$CHECKS/.node_startup_$STAMP.mark"
if command -v sha256sum >/dev/null; then SHA=(sha256sum); else SHA=(shasum -a 256); fi
real_state() {
    (cd "$RUNTIME" && { find config -type f; find live_sessions -name 'state_*.json' -type f; } 2>/dev/null | sort \
        | while IFS= read -r f; do "${SHA[@]}" "$f"; done)
}
BEFORE=$(real_state)
touch "$MARK"
trap 'rm -rf "$COPY"; rm -f "$MARK"' EXIT
mkdir -p "$COPY" || fail "could not make $COPY"
for d in config data live_sessions; do
    [ -d "$RUNTIME/$d" ] && { cp -a "$RUNTIME/$d" "$COPY/" || fail "could not copy the runtime's $d"; }
done
echo "copied to $COPY"
# Earlier shadow records in the runtime would be resumed instead of the real state: start clean.
rm -rf "${COPY:?}/$REC"

RUN_CONFIG=$CONFIG
if [ "$READ_ACCOUNT" = 1 ]; then
    RUN_CONFIG=config/node_startup_check.yaml
    (cd "$COPY" && "$PY" - "$CONFIG" "$RUN_CONFIG" <<'PYEOF'
import sys
import yaml
src, dst = sys.argv[1:3]
with open(src) as f:
    raw = yaml.safe_load(f) or {}
raw["shadow"] = {**(raw.get("shadow") or {}), "read_account": True}
with open(dst, "w") as f:
    f.write(f"# written by node_startup_check.sh: {src} with shadow.read_account on\n")
    yaml.safe_dump(raw, f, sort_keys=False)
PYEOF
    ) || fail "could not write the check's configuration"
fi

problems=()
notes=()
# The launcher the schedule would use, resolved and checked without starting anything.
LAUNCHER="$RELEASE/ops/run_node.sh"
if [ -x "$LAUNCHER" ]; then
    LAUNCH_OUT=$(DRY_RUN=1 NODE_CONFIG="$CONFIG" NODE_MODE=shadow MIDFREQ_ROOT="$COPY" PYTHON= "$LAUNCHER" 5 2>&1)
    lstatus=$?
    if [ "$lstatus" -ne 0 ] || ! grep -qE "would run: .*midfreq\.trading\.cli run --config $CONFIG --mode shadow --duration 5" <<<"$LAUNCH_OUT"; then
        problems+=("the launcher's dry run failed (status $lstatus; see launcher_dry_run.txt)")
    fi
else
    LAUNCH_OUT="no $LAUNCHER"
    problems+=("the release has no ops/run_node.sh, the node's launcher (ops/node_release.patch)")
fi

echo "starting the node from the release in shadow mode for $MINUTES minutes..."
LIMIT=$(( (MINUTES + 10) * 60 ))
TIMEOUT=()
command -v timeout >/dev/null && TIMEOUT=(timeout --kill-after=60 "$LIMIT")
(cd "$COPY" && MIDFREQ_ROOT="$COPY" PYTHONUNBUFFERED=1 ${TIMEOUT[@]+"${TIMEOUT[@]}"} \
    "$NODE" run --config "$RUN_CONFIG" --mode shadow --duration "$MINUTES") > "$LOG" 2>&1
STATUS=$?

mkdir -p "$RECORDS"
[ -d "$COPY/$REC" ] && cp -a "$COPY/$REC/." "$RECORDS/"
printf '%s\n' "$LAUNCH_OUT" > "$RECORDS/launcher_dry_run.txt"
rm -rf "$COPY"

# What a healthy start and stop log (midfreq.trading.node, strategy_host, shadow, ops).
need() { grep -qF -- "$1" "$LOG" || problems+=("missing: $2"); }
need "TRADING NODE $NAME " "the node's banner"
need "SHADOW mode" "shadow mode in the banner"
need "shadow execution for account" "the shadow execution client (orders are logged, never sent)"
need "connecting " "the connector's connection to OpenD"
grep -qE "stream (connected|reconnected)" "$LOG" || problems+=("missing: the market data stream's connection to OpenD")
need "trading enabled" "trading enabled after warm-up"
need "running in shadow mode:" "the subscriptions made and the node running"
need "Session duration reached." "the session reaching its end"
need "stopping (duration)" "a stop at the end of the duration (not a stall, a signal or an error)"
need "SESSION SUMMARY" "the session summary"

if [ "$READ_ACCOUNT" = 1 ]; then
    holds=$(grep -oE "account holds [0-9,]+(\.[0-9]+)?" "$LOG" | head -1)
    case "$holds" in
        "") problems+=("missing: the paper account's balance (capital capped at ... (account holds ...))") ;;
        "account holds 0"|"account holds 0.00") problems+=("the paper account reads as empty ($holds): OpenD not logged in, or the wrong account") ;;
    esac
    grep -qF "[resume] broker positions unavailable" "$LOG" && problems+=("the broker's positions could not be read at start-up")
    grep -q "state says .* but broker holds" "$LOG" && problems+=("bot and broker disagree: the restore shrank a position to the broker's (see [resume] in the log)")
fi

while read -r _ sid path presence held clock; do
    if [ "$presence" = present ]; then
        need "[resume] raw state on disk:" "$sid's saved state read ($path)"
        [ "$clock" -gt 0 ] && need "[resume] rebalance clock at $clock cross-sections" "$sid's rebalance clock restored at $clock"
        [ "$held" -gt 0 ] && need "[resume] restored positions:" "$sid's $held positions restored"
    else
        notes+=("$sid has no saved state ($path): a first run starts flat")
    fi
done < <(grep '^state ' <<<"$PRE")

warm=$(grep -oE "[0-9]+ of [0-9]+ symbols from the local store" "$LOG")
[ -n "$warm" ] || problems+=("missing: warm-up from the local store")
while read -r got _ want _; do
    [ -z "$got" ] || [ "$got" = "$want" ] || problems+=("warm-up found history for only $got of $want symbols")
done <<<"$warm"
running=$(grep -oE "running in shadow mode: [0-9]+ strategies, [0-9]+ instruments" "$LOG" | grep -oE "[0-9]+ instruments" | cut -d' ' -f1)
[ -z "$running" ] || [ "$running" = "$INSTRUMENTS" ] || problems+=("$running instruments subscribed, the configuration has $INSTRUMENTS")

# No order: with the market closed none may even be decided.
sent=$(grep -oE "orders sent [0-9]+" "$LOG")
[ -n "$sent" ] || problems+=("missing: the strategies' order counts in the session summary")
grep -q "orders sent [1-9]" <<<"$sent" && problems+=("orders were created ($(paste -sd, - <<<"$sent"))")
grep -qE "SHADOW (BUY|SELL) " "$LOG" && problems+=("the shadow execution client received orders (SHADOW BUY/SELL lines)")
grep -qE "\| oms \| sent " "$LOG" && problems+=("the OMS sent orders")
cat "$RECORDS"/shadow_*.jsonl 2>/dev/null | grep -q . && problems+=("the shadow journal records orders (shadow_*.jsonl)")
grep -qE '"kind": ?"order"' "$RECORDS"/orders_*.jsonl 2>/dev/null && problems+=("the order journal records orders (orders_*.jsonl)")

# No error.
grep -q "Traceback (most recent call last)" "$LOG" && problems+=("a Python traceback in the console log")
nerr=$(grep -cE "\| (ERROR|CRITICAL) +\| " "$LOG")
[ "$nerr" -eq 0 ] || problems+=("$nerr ERROR or CRITICAL lines in the console log")
OPS=$(ls -t "$RECORDS"/ops_*.jsonl 2>/dev/null | head -1)
if [ -z "$OPS" ]; then
    problems+=("missing: the node's operations log (ops_*.jsonl)")
else
    OPS_SUMMARY=$("$PY" - "$OPS" <<'PYEOF'
import collections, json, sys
levels, bad, n, errors, ended = collections.Counter(), 0, 0, [], False
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        if not line.strip():
            continue
        n += 1
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        levels[rec.get("level", "?")] += 1
        if rec.get("level") in ("ERROR", "CRITICAL"):
            errors.append(f"{rec.get('component')}: {rec.get('msg')}"[:150])
        ended = ended or rec.get("msg") == "Session duration reached."
print(f"{n} {bad} {levels['ERROR'] + levels['CRITICAL']} {'yes' if ended else 'no'} "
      + ",".join(f"{k}={v}" for k, v in sorted(levels.items())))
for e in errors[:3]:
    print(e)
PYEOF
)
    read -r ops_n ops_bad ops_err ops_end ops_levels <<<"$(head -1 <<<"$OPS_SUMMARY")"
    [ "${ops_bad:-1}" -eq 0 ] || problems+=("$ops_bad unreadable lines in $OPS")
    [ "${ops_err:-1}" -eq 0 ] || problems+=("$ops_err ERROR or CRITICAL records in the ops log: $(tail -n +2 <<<"$OPS_SUMMARY" | paste -sd';' -)")
    [ "${ops_end:-no}" = yes ] || problems+=("the ops log does not reach the end of the session")
fi

if [ "$STATUS" -eq 124 ] || [ "$STATUS" -eq 137 ]; then
    problems+=("the node did not stop within $((MINUTES + 10)) minutes and was killed")
elif [ "$STATUS" -ne 0 ]; then
    problems+=("the node exited with status $STATUS")
fi
[ "$(real_state)" = "$BEFORE" ] || problems+=("the real runtime's state files or config changed")
written=$(find "$RUNTIME" -type f -newer "$MARK" ! -name 'broker_*.json' ! -name '.broker_*' 2>/dev/null | head -5 | paste -sd' ' -)
[ -z "$written" ] || problems+=("files in the real runtime were written during the check: $written")

echo
grep -E "TRADING NODE|execution|account holds|\[resume\]|no state of its own|\[warmup\] complete|board lots|stream (connected|reconnected)|running in shadow|duration reached|orders sent|heartbeat" "$LOG" \
    | cut -c1-200
[ -n "$OPS" ] && echo "ops log: $OPS (${ops_n:-?} records: ${ops_levels:-?})"
echo "log: $LOG"
for n in ${notes[@]+"${notes[@]}"}; do echo "note: $n"; done
if [ ${#problems[@]} -eq 0 ]; then
    echo "PASS: the release starts the node, restores the strategy, subscribes, sends nothing and stops cleanly"
    exit 0
fi
echo "--- last lines of the log ---"
tail -15 "$LOG" | cut -c1-200
printf 'FAIL: %s\n' "${problems[@]}"
exit 1
