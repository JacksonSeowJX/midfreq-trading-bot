#!/bin/bash
# Daily forward-test roster — launches all validated strategy candidates
# as parallel live paper sessions until HK market close.
#
# Candidates come from the walk-forward research (results/*.csv):
#   Bollinger Bands @ HK.09888 1h  (best consistency, 75%)
#   RSI             @ HK.09888 1h  (best OOS return among singles)
#   Regime Switch   @ HK.00700 1h  (rescues the stock singles fail on)
#
# Usage: ./scripts/run_daily_candidates.sh [duration_minutes]
#   Run any time during market hours (09:30-12:00, 13:00-16:00 HKT).
#   Default duration: until ~16:01 today.
#
# Requires: OpenD running + logged in.

set -u
cd "$(dirname "$0")/.."

PY=${PYTHON:-/opt/anaconda3/bin/python}
LOG_DIR=live_sessions
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)

# Duration: minutes until 16:01, unless overridden
if [ $# -ge 1 ]; then
  DURATION=$1
else
  NOW_S=$(date +%s)
  CLOSE_S=$(date -j -f "%H:%M" "16:01" +%s 2>/dev/null || date -d "16:01" +%s)
  DURATION=$(( (CLOSE_S - NOW_S) / 60 ))
  if [ "$DURATION" -le 0 ]; then
    echo "Market is closed (or <1 min left). Pass a duration explicitly to override."
    exit 1
  fi
fi

echo "Launching daily candidates for ${DURATION} minutes (logs: $LOG_DIR/console_*_${STAMP}.log)"

$PY -u run_live.py --strategy "Bollinger Bands" --symbols HK.09888 --timeframe 1h \
    --duration "$DURATION" --qty 100 --stop-loss 3 --params bb_period=14 num_std=2.5 \
    > "$LOG_DIR/console_bollinger_09888_${STAMP}.log" 2>&1 &

$PY -u run_live.py --strategy "RSI" --symbols HK.09888 --timeframe 1h \
    --duration "$DURATION" --qty 100 --stop-loss 3 --params rsi_period=18 oversold=29 overbought=84 \
    > "$LOG_DIR/console_rsi_09888_${STAMP}.log" 2>&1 &

$PY -u run_live.py --strategy "Regime Switch" --symbols HK.00700 --timeframe 1h \
    --duration "$DURATION" --qty 100 --stop-loss 3 \
    > "$LOG_DIR/console_regime_00700_${STAMP}.log" 2>&1 &

wait
echo "All sessions finished. Summaries:"
for f in "$LOG_DIR"/console_*_"${STAMP}".log; do
  echo "--- $f"
  grep -A 8 "SESSION SUMMARY" "$f" | head -9
done
