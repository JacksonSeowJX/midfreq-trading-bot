#!/bin/bash
# Launcher for the trading node (midfreq.trading, docs/LIVE_ARCHITECTURE.md), the counterpart of
# ops/run_sp100_forward_test.sh for the original engine. Not in any crontab.
#
# Usage: ./ops/run_node.sh [--scheduled] [duration_minutes]
#   NODE_CONFIG  the node configuration (default config/node_sp100.yaml)
#   NODE_MODE    shadow | sim | paper | live: overrides the configuration's node.mode
#   DRY_RUN=1    resolve everything, check the configuration, print the command, start nothing
#   FAKE_ET      a New York time (YYYY-MM-DDTHH:MM) to test the gate and the duration
#
# The duration defaults to two minutes after the close of the configuration's first market
# (the venue's last session close in config/venues.json, in its own time zone), so the session's
# last candle is finalised before the node stops; trading is already off by then.
#
# Requires: OpenD running and logged in (paper and live; shadow needs it for market data).

set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-$HERE/.venv/bin/python}
NODE=midfreq.trading.cli
cd "${MIDFREQ_ROOT:-$HERE}" || exit 1
export MIDFREQ_ROOT=$(pwd)
CFG=${NODE_CONFIG:-config/node_sp100.yaml}
LOG_DIR=live_sessions
STAMP=$(date +%Y%m%d_%H%M%S)

if [ ! -f "$CFG" ]; then
  echo "Missing $CFG."
  exit 1
fi

# The market the configuration trades: its first strategy's first symbol's venue.
read -r TZ_NAME CLOSE_HHMM OPEN_HHMM CFG_MODE <<<"$("$PY" - "$CFG" <<'PYEOF'
import sys
from midfreq.trading.config import load_config, instrument_for_symbol
from midfreq.core.venues.spec import default_venues
cfg = load_config(sys.argv[1], check=False)
s = cfg.strategies[0]
iid = instrument_for_symbol(cfg.universes[s.universe].symbols[0])
spec = default_venues().get(iid.venue)
if spec.is_24_7:
    print(spec.timezone, "none", "none", cfg.mode)
else:
    print(spec.timezone, spec.sessions[-1].close.strftime("%H:%M"), spec.sessions[0].open.strftime("%H:%M"), cfg.mode)
PYEOF
)"
if [ -z "${TZ_NAME:-}" ]; then
  echo "Cannot read $CFG (run: $PY -m $NODE check-config --config $CFG)."
  exit 1
fi
# Shadow and sim sessions keep their records (and this console log) out of live_sessions/ itself,
# where the dashboard reads the real sessions.
EFFECTIVE_MODE=${NODE_MODE:-$CFG_MODE}
case "$EFFECTIVE_MODE" in
  shadow|sim) LOG_DIR="live_sessions/$EFFECTIVE_MODE" ;;
esac

# --scheduled: proceed only within 30 minutes after the market's open on a weekday (cron can
# then fire at both possible local times around a daylight-saving change).
if [ "${1:-}" = "--scheduled" ]; then
  shift
  if ! "$PY" - "$TZ_NAME" "$OPEN_HHMM" <<'PYEOF'
import os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz, open_hm = ZoneInfo(sys.argv[1]), sys.argv[2]
now = datetime.now(tz)
if os.environ.get("FAKE_ET"):
    now = datetime.fromisoformat(os.environ["FAKE_ET"]).replace(tzinfo=tz)
if open_hm == "none":
    sys.exit(0)
oh, om = map(int, open_hm.split(":"))
mins = now.hour * 60 + now.minute
ok = now.weekday() < 5 and oh * 60 + om <= mins <= oh * 60 + om + 30
print(f"[gate] {now:%a %Y-%m-%d %H:%M %Z}: {'market open, running' if ok else 'not the open, skipping'}")
sys.exit(0 if ok else 1)
PYEOF
  then
    exit 0
  fi
fi

if [ $# -ge 1 ]; then
  DURATION=$1
else
  DURATION=$("$PY" - "$TZ_NAME" "$CLOSE_HHMM" <<'PYEOF'
import os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
tz, close_hm = ZoneInfo(sys.argv[1]), sys.argv[2]
if close_hm == "none":
    print(0); sys.exit()
now = datetime.now(tz)
if os.environ.get("FAKE_ET"):
    now = datetime.fromisoformat(os.environ["FAKE_ET"]).replace(tzinfo=tz)
ch, cm = map(int, close_hm.split(":"))
close = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
if now >= close:
    close += timedelta(days=1)
print(max(0, int((close - now).total_seconds() // 60)) + 2)
PYEOF
)
  if [ "$DURATION" -le 0 ]; then
    echo "No close to run until (24/7 market or closed). Pass a duration explicitly."
    exit 1
  fi
fi

MODE_ARGS=()
if [ -n "${NODE_MODE:-}" ]; then
  MODE_ARGS=(--mode "$NODE_MODE")
fi
CONSOLE="$LOG_DIR/console_node_${STAMP}.log"

echo "Trading node"
echo "  config   : $CFG"
echo "  mode     : $EFFECTIVE_MODE"
echo "  market   : $TZ_NAME, close $CLOSE_HHMM"
echo "  duration : ${DURATION} min"
echo "  log      : $CONSOLE"
echo

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "[dry run] working directory: $(pwd)"
  "$PY" -m "$NODE" check-config --config "$CFG" ${MODE_ARGS[@]+"${MODE_ARGS[@]}"} \
    || { echo "[dry run] configuration check failed"; exit 1; }
  echo "[dry run] would run: $PY -u -m $NODE run --config $CFG ${MODE_ARGS[*]-} --duration $DURATION"
  exit 0
fi

mkdir -p "$LOG_DIR"
"$PY" -u -m "$NODE" run --config "$CFG" ${MODE_ARGS[@]+"${MODE_ARGS[@]}"} --duration "$DURATION" \
    2>&1 | tee "$CONSOLE"
STATUS=${PIPESTATUS[0]}

echo
echo "--- session summary ---"
grep -A 8 "SESSION SUMMARY" "$CONSOLE" | head -9
exit "$STATUS"
