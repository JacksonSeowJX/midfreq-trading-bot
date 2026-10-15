#!/bin/bash
# Publish the broker accounts as snapshots for the dashboards, run by cron every 5 minutes
# (ops/vm/crontab.txt). Only this VM reaches OpenD, so it reads every account with
# `publish: true` in runtime/config/connections.yaml (python -m midfreq.data.snapshots) and
# writes runtime/live_sessions/broker_<id>.json, which sync_logs.sh uploads with the record.
#
# Read-only, and gentle: per account one connection to OpenD and four queries, one account
# after the other. It never touches a running session. An account that cannot be read keeps its
# previous snapshot (the dashboards show its age). Silent on success; failures are logged with
# the time. Does nothing when no account has `publish: true`.
# Environment, for testing: MIDFREQ_HOME.

set -uo pipefail
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
ROOT="$HOME_DIR/runtime"
LIMIT=240             # seconds; a run never reaches the next one (each read is bounded anyway)

bounded() { if command -v timeout >/dev/null; then timeout "$LIMIT" "$@"; else "$@"; fi; }

status=0
out=$(MIDFREQ_ROOT="$ROOT" bounded "$HOME_DIR/current/.venv/bin/python" -m midfreq.data.snapshots \
      --out-dir "$ROOT/live_sessions" 2>&1) || status=$?
[ "$status" -eq 0 ] && exit 0
[ "$status" -eq 124 ] && out="${out:+$out
}stopped after ${LIMIT}s"
while IFS= read -r line; do
    echo "$(date '+%F %T') $line"
done <<< "${out:-exit status $status}"
exit "$status"
