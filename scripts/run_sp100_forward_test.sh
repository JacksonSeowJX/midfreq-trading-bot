#!/bin/bash
# Forward test of the one strategy-universe combination that passed the
# project's two-configuration validation standard: cross-sectional
# reversal on the S&P 100.
#
# Reads its configuration from config/sp100_forward_test.json so the
# parameters being traded live are recorded in one place the dashboard
# and the report can both cite, rather than living in this script.
#
# Differences from run_daily_candidates.sh, which this does NOT replace:
#   - US market hours (09:30-16:00 ET), not HK
#   - equal-dollar sizing across the basket, not a flat share count:
#     a fixed 100 shares put $2,529 into T and $125,540 into LLY on this
#     same universe, so the basket tracked share price instead of the
#     ranking (2026-09-08 audit, defect 2)
#   - US commission (0.005%/side), not the HK 0.16% that includes stamp duty
#
# Both rosters together exceed the account's 100-unit subscription quota,
# so the HK roster cron is paused while this runs.
#
# Usage: ./scripts/run_sp100_forward_test.sh [duration_minutes]
# Requires: OpenD running and logged in.

set -u
cd "$(dirname "$0")/.."

PY=${PYTHON:-/opt/anaconda3/bin/python}
CFG=config/sp100_forward_test.json
LOG_DIR=live_sessions
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)

if [ ! -f "$CFG" ]; then
  echo "Missing $CFG — run scripts/pick_sp100_live_config.py first."
  exit 1
fi

read -r LOOKBACK TOP_N REBAL NSYM <<<"$($PY - <<'PYEOF'
import json
c = json.load(open('config/sp100_forward_test.json'))
p = c['params']
print(p['lookback'], p['top_n'], p['rebalance_every'], len(c['symbols']))
PYEOF
)"

SYMBOLS=$($PY -c "import json;print(' '.join(json.load(open('$CFG'))['symbols']))")

# Duration: minutes until the US close (16:00 ET), unless overridden.
if [ $# -ge 1 ]; then
  DURATION=$1
else
  DURATION=$($PY - <<'PYEOF'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
et = datetime.now(ZoneInfo("America/New_York"))
close = et.replace(hour=16, minute=0, second=0, microsecond=0)
if et >= close:
    close += timedelta(days=1)
print(max(0, int((close - et).total_seconds() // 60)))
PYEOF
)
  if [ "$DURATION" -le 0 ]; then
    echo "US market is closed. Pass a duration explicitly to override."
    exit 1
  fi
fi

echo "S&P 100 cross-sectional reversal forward test"
echo "  symbols        : $NSYM"
echo "  lookback       : $LOOKBACK candles"
echo "  basket size    : $TOP_N"
echo "  rebalance every: $REBAL candles"
echo "  duration       : ${DURATION} min"
echo "  log            : $LOG_DIR/console_sp100_reversal_${STAMP}.log"
echo

$PY -u run_live.py \
    --strategy "Cross-Sectional Reversal" \
    --symbols $SYMBOLS \
    --timeframe 1h \
    --duration "$DURATION" \
    --sizing equal-dollar --basket-size "$TOP_N" \
    --stop-loss 5 --max-drawdown 15 \
    --params lookback="$LOOKBACK" top_n="$TOP_N" rebalance_every="$REBAL" \
    2>&1 | tee "$LOG_DIR/console_sp100_reversal_${STAMP}.log"

echo
echo "--- session summary ---"
grep -A 8 "SESSION SUMMARY" "$LOG_DIR/console_sp100_reversal_${STAMP}.log" | head -9
