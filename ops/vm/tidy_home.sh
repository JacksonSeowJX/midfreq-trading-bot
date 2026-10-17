#!/bin/bash
# One-time tidy of the VM's home folder into the release layout (ops/README.md, cutover).
#
# Prints what it would do; changes nothing unless run with --apply. It only touches the
# fixed list of items below, known from an inventory of the VM on 16 Oct 2026; anything
# else in the home folder is listed as "left alone" for a human to decide.
#
#   one-off scripts, old logs  -> one tarball ~/midfreq/archive/legacy-home-<date>.tar.gz, then removed
#   cron_sp100.log, memlog.txt -> ~/midfreq/logs/            (the new crontab writes there)
#   ~/bin, backups, crontab.bak -> ~/midfreq/backups/
#   the OpenD installer archive -> deleted (the unpacked program that runs stays; Moomoo has it)
#   ~/midfreq-trading-bot       -> left alone: removed by hand a week after the cutover
#
# Refuses to run while a session is running, or while the crontab still uses ~/bin or the
# old clone (install ops/vm/crontab.txt first, so nothing is writing to what moves).
# Usage: tidy_home.sh [--apply]

set -euo pipefail
H=${TIDY_HOME:-$HOME}               # for testing
M="$H/midfreq"
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
STAMP=$(date +%F)

SCRIPTS="order_gateway_fix.py us_flatten.py us_fee_probe.py us_fee_calibrate.py refetch_stale.py quota.py pos.py hq.py"
OLD_LOGS="backfill_hsi.log cron_roster.log cross_sectional_hsi.log flatten_hk.log gapfill.log hk_fee.log order_path_test.log sp100_refresh.log spread_hk.log preflight.log"
TO_LOGS="cron_sp100.log memlog.txt"
TO_BACKUPS="bin backup_20261015_phase0 backup_20261015_deploykey backup_cutover crontab.bak.20261009b"
INSTALLER="opend/moomoo_OpenD_10.8.6818_Ubuntu18.04.tar.gz"
KNOWN="$SCRIPTS $OLD_LOGS $TO_LOGS $TO_BACKUPS opend midfreq midfreq-trading-bot"

if [ -z "${TIDY_HOME:-}" ]; then
    pgrep -f '[r]un_live|[m]idfreq\.trading\.cli run|[m]idfreq-node run' >/dev/null && { echo "a session is running; tidy afterwards" >&2; exit 1; }
    if crontab -l 2>/dev/null | grep -v '^#' | grep -q "$H/bin/\|midfreq-trading-bot"; then
        echo "the crontab still uses ~/bin or the old clone; install ops/vm/crontab.txt first" >&2; exit 1
    fi
fi

do_() { echo "  $*"; [ "$APPLY" = 1 ] && "$@"; return 0; }
present() { local out=""; for f in $1; do [ -e "$H/$f" ] && out="$out $f"; done; echo "$out"; }

[ "$APPLY" = 1 ] && echo "Tidying $H" || echo "Plan for $H (dry run; --apply to do it)"
do_ mkdir -p "$M/logs" "$M/archive" "$M/backups"

legacy=$(present "$SCRIPTS $OLD_LOGS")
if [ -n "$legacy" ]; then
    echo "archive and remove:$legacy"
    do_ tar -czf "$M/archive/legacy-home-$STAMP.tar.gz" -C "$H" $legacy
    [ "$APPLY" = 1 ] && tar -tzf "$M/archive/legacy-home-$STAMP.tar.gz" >/dev/null   # readable before removing
    for f in $legacy; do do_ rm -f "$H/$f"; done
fi
for f in $(present "$TO_LOGS"); do
    if [ -e "$M/logs/$f" ]; then do_ sh -c "cat '$H/$f' '$M/logs/$f' > '$M/logs/$f.tmp' && mv '$M/logs/$f.tmp' '$M/logs/$f' && rm '$H/$f'"
    else do_ mv "$H/$f" "$M/logs/$f"; fi
done
for f in $(present "$TO_BACKUPS"); do do_ mv "$H/$f" "$M/backups/$f"; done
[ -e "$H/$INSTALLER" ] && do_ rm -f "$H/$INSTALLER"

left=""
for f in "$H"/* ; do
    b=$(basename "$f"); [ -e "$f" ] || continue
    case " $KNOWN " in *" $b "*) ;; *) left="$left $b" ;; esac
done
[ -n "$left" ] && echo "left alone (not on the list):$left"
[ "$APPLY" = 1 ] && echo "done" || true
