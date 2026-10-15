#!/bin/bash
# Daily VM maintenance, run by cron at 17:05 SGT (ops/vm/crontab.txt), when no session runs.
#
#   1. Once a month, archive the operations logs (~/midfreq/logs/*.log) into ~/midfreq/archive
#      as <name>.<date>.log.gz and empty them; keep the newest 12 per log. Copy-then-truncate,
#      so a process appending to a log (cron's >>) carries on writing to the same file.
#   2. Delete moomoo SDK debug logs older than 36 hours (~1.4 GB per session) and OpenD's own
#      logs older than 14 days. They are debug output, not records, so they are not archived.
#   3. Append one health line to ~/midfreq/logs/health.log: OpenD, disk, the latest session.
#
# The trading record (~/midfreq/runtime/live_sessions) is never touched.
# Environment, for testing: MIDFREQ_HOME, OPEND_LOG_DIR, MAINTAIN_DATE (YYYY-MM-DD).

set -uo pipefail      # no -e: one failing step must not skip the others
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
LOGS="$HOME_DIR/logs"
ARCHIVE="$HOME_DIR/archive"
KEEP=12
OPEND_LOG=${OPEND_LOG_DIR:-$HOME/.com.moomoo.OpenD/Log}
TODAY=${MAINTAIN_DATE:-$(date +%F)}
MONTH=${TODAY:0:7}
mkdir -p "$LOGS" "$ARCHIVE"
say() { echo "$(date '+%F %T') $*"; }

# 1. monthly archive: the first run in a new month archives everything logged before it
if [ ! -e "$LOGS/.archived-$MONTH" ]; then
    for f in "$LOGS"/*.log; do
        [ -s "$f" ] || continue
        name=$(basename "$f" .log)
        dest="$ARCHIVE/$name.$TODAY.log.gz"
        if gzip -c "$f" > "$dest"; then
            : > "$f"
            say "archived $name -> $(basename "$dest")"
        else
            rm -f "$dest"; say "ERROR: could not archive $f"
        fi
        ls -1 "$ARCHIVE/$name".*.log.gz 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
            rm -f "$old"; say "removed old archive $(basename "$old")"
        done
    done
    rm -f "$LOGS"/.archived-*
    touch "$LOGS/.archived-$MONTH"
fi

# 2. retention of OpenD and SDK debug logs
if [ -d "$OPEND_LOG" ]; then
    n_py=$(find "$OPEND_LOG" -maxdepth 1 -name 'py_*.log' -mmin +2160 -print -delete | wc -l)
    n_ft=$(find "$OPEND_LOG" -maxdepth 1 -name '*.ftlog' -mtime +14 -print -delete | wc -l)
    [ "$n_py$n_ft" = 00 ] || say "deleted $n_py SDK logs (>36 h) and $n_ft OpenD logs (>14 days)"
fi

# 3. health line
opend=$( (exec 3<>/dev/tcp/127.0.0.1/11111) 2>/dev/null && echo up || echo DOWN )
disk=$(df -P / | awk 'NR==2 {print $5}')
latest=$(ls -t "$HOME_DIR"/runtime/live_sessions/console_*.log 2>/dev/null | head -1)
if [ -z "$latest" ]; then
    session="no session yet"
elif grep -q 'Traceback\|CRITICAL' "$latest"; then
    session="CRASH in $(basename "$latest")"
else
    session="ok $(basename "$latest")"
fi
release=$(basename "$(readlink "$HOME_DIR/current" 2>/dev/null || echo none)")
echo "$(date '+%F %T') opend=$opend disk=$disk release=$release session=$session" >> "$LOGS/health.log"
say "health: opend=$opend disk=$disk release=$release session=$session"
