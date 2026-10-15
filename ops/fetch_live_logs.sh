#!/bin/bash
# Copy the live session files from the VM to this checkout, and report the VM's health.
#
# Until 15 Oct 2026 the VM pushed live_sessions/ to GitHub every weekday at 16:15 SGT
# and pulled main at 09:10 SGT, so whatever was on main went live that night. Those two
# cron jobs are now disabled (migration phase 0): the VM runs its current code and never
# talks to GitHub. Run this instead to bring the session logs here for the dashboard and
# the report. It only reads from the VM.
#
#   - new and changed files in the VM's live_sessions/ are copied here; nothing here is
#     deleted. The VM's state file replaces this checkout's copy, as postclose did.
#   - nothing is committed: commit live_sessions/ yourself to keep a record in git.
#   - health, as preflight used to log it: session running, OpenD listening on 11111,
#     crashes in the latest session's console logs, free disk.
#
# Usage: ./ops/fetch_live_logs.sh
# Requires: gcloud, logged in to the project that has the VM.

set -euo pipefail
VM=${VM:-trading-bot}
ZONE=${ZONE:-asia-southeast1-b}
REMOTE='~/midfreq-trading-bot'
cd "$(dirname "$0")/.."
DEST=live_sessions
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

vm() { gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command "$1" 2>"$TMP/ssh_err"; }

echo "Copying live_sessions/ from $VM ..."
if ! vm "cd $REMOTE && tar czf - live_sessions" > "$TMP/live_sessions.tgz"; then
    grep -v '^Warning' "$TMP/ssh_err" >&2 || true
    echo "could not reach $VM" >&2
    exit 1
fi
tar xzf "$TMP/live_sessions.tgz" -C "$TMP"
mkdir -p "$DEST"
new=0; updated=0
for src in "$TMP"/live_sessions/*; do
    f=$(basename "$src")
    if [ ! -e "$DEST/$f" ]; then
        cp -p "$src" "$DEST/$f"; new=$((new + 1)); echo "  new      $f"
    elif ! cmp -s "$src" "$DEST/$f"; then
        cp -p "$src" "$DEST/$f"; updated=$((updated + 1)); echo "  updated  $f"
    fi
done
echo "$new new, $updated updated, in $DEST/"

echo
echo "VM health:"
vm "cd $REMOTE
pgrep -f '[r]un_live.py' >/dev/null && echo '  session:  running now' || echo '  session:  not running'
nc -z 127.0.0.1 11111 2>/dev/null && echo '  OpenD:    listening on 11111' || echo '  OpenD:    NOT listening on 11111'
stamp=\$(ls -t live_sessions/console_*.log 2>/dev/null | head -1 | grep -oE '[0-9]{8}_[0-9]{6}')
crashed=\$(grep -l 'Traceback\|CRITICAL' live_sessions/console_*_\"\$stamp\".log 2>/dev/null || true)
[ -n \"\$crashed\" ] && echo \"  WARNING:  latest session logged a crash: \$crashed\" || echo \"  crashes:  none in the latest session (\$stamp)\"
echo \"  disk:     \$(df -h / | awk 'NR==2 {print \$5 \" used, \" \$4 \" free\"}')\"
echo \"  launcher: \$(tail -1 ~/cron_sp100.log 2>/dev/null)\"" || { grep -v '^Warning' "$TMP/ssh_err" >&2; exit 1; }
