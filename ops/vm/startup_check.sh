#!/bin/bash
# Start the forward test from an installed release for a few minutes, on a COPY of the runtime,
# then check that it came up and shut down cleanly. Run by hand on the VM after a deploy and
# before the schedule uses the release (ops/README.md, cutover and Deploy workflow).
#
# The real runtime (~/midfreq/runtime) is never written: the session runs with MIDFREQ_ROOT set
# to a copy, and the real state files' checksums are compared before and after. It refuses to
# run while a session is running or while the US market is open (09:00-16:30 New York time on
# a weekday), because the copy trades on the same paper account: with the market closed no
# candle completes, so no rebalance can come due.
#
# Usage: ops/vm/startup_check.sh [minutes]        (default 5)
#   MIDFREQ_HOME (default ~/midfreq): runs $MIDFREQ_HOME/current against $MIDFREQ_HOME/runtime.
# Prints PASS or FAIL and exits 0 or 1. The check's console log is kept in
# $MIDFREQ_HOME/checks/startup_<stamp>.log; the copy itself is removed.

set -uo pipefail
MINUTES=${1:-5}
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
RELEASE="$HOME_DIR/current"
RUNTIME="$HOME_DIR/runtime"
CHECKS="$HOME_DIR/checks"
STAMP=$(date +%Y%m%d_%H%M%S)
COPY="$CHECKS/startup_$STAMP"

fail() { echo "FAIL: $*"; exit 1; }

[ -x "$RELEASE/ops/run_sp100_forward_test.sh" ] || fail "no installed release at $RELEASE"
[ -d "$RUNTIME/config" ] || fail "no runtime at $RUNTIME"
pgrep -af '[r]un_live|[m]idfreq\.trading\.cli run|[m]idfreq-node run' >/dev/null && fail "a trading session is running"
"$RELEASE/.venv/bin/python" - <<'PYEOF' || fail "the US market is open (or about to be); run this outside 09:00-16:30 New York time"
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo("America/New_York"))
mins = now.hour * 60 + now.minute
sys.exit(1 if now.weekday() < 5 and 9 * 60 <= mins <= 16 * 60 + 30 else 0)
PYEOF

echo "release: $(readlink -f "$RELEASE")"
echo "runtime: $RUNTIME (copied to $COPY)"
mkdir -p "$CHECKS"
real_state() { sha256sum "$RUNTIME"/live_sessions/state_*.json "$RUNTIME"/config/*.json 2>/dev/null; }
BEFORE=$(real_state)
mkdir -p "$COPY" && cp -a "$RUNTIME/config" "$RUNTIME/data" "$RUNTIME/live_sessions" "$COPY/" || fail "could not copy the runtime"

LOG="$CHECKS/startup_$STAMP.log"
echo "starting the forward test from the release for $MINUTES minutes..."
MIDFREQ_ROOT="$COPY" "$RELEASE/ops/run_sp100_forward_test.sh" "$MINUTES" > "$LOG" 2>&1
STATUS=$?
rm -rf "$COPY"

# what a healthy session prints (as in live_sessions/console_sp100_reversal_*.log)
problems=()
need() { grep -qF -- "$1" "$LOG" || problems+=("missing: $2"); }
need "Connected to Moomoo trade context (PAPER) on 127.0.0.1:11111" "paper trade context on OpenD"
need "[resume] restored positions:" "the strategy's positions restored"
need "[resume] rebalance clock at" "the rebalance clock restored"
need "Connected to Moomoo OpenD on 127.0.0.1:11111" "quote connection to OpenD"
need "trading enabled" "warm-up complete"
need "Started live 1h candle streaming for" "price stream subscribed"
need "Session duration reached." "the session reached its end"
need "SESSION SUMMARY" "the session summary"
need "Trades submitted:  0" "no trades (none may be submitted with the market closed)"
need "Disconnected from Moomoo OpenD." "a clean disconnect"
grep -qE "Traceback|NOT FILLED|\(order [0-9]+" "$LOG" && problems+=("an error or an order appears in the log")
[ "$STATUS" -eq 0 ] || problems+=("the launcher exited with status $STATUS")
[ "$(real_state)" = "$BEFORE" ] || problems+=("the real runtime's state or config changed")

echo
grep -E "trade context|\[resume\]|\[warmup\]|Started live|duration reached|Trades submitted|Open positions|^  US\." "$LOG" \
    | grep -v "^Started live.*\[" | cut -c1-160
grep -oE "Started live 1h candle streaming for [0-9]+ symbols" "$LOG"
echo "log: $LOG"
if [ ${#problems[@]} -eq 0 ]; then
    echo "PASS: the release starts, restores the strategy, streams, and shuts down cleanly"
    exit 0
fi
printf 'FAIL: %s\n' "${problems[@]}"
exit 1
