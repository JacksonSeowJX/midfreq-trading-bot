#!/bin/bash
# One line per minute: time, RAM used/available, swap used, and the three
# largest processes. Added after the 2026-09-28 S&P 100 session stalled and
# the VM stopped answering SSH with no surviving evidence of why. Keeps the
# last ~10 days (14,400 lines), so it cannot fill the disk.
LOG=~/memlog.txt
{
  printf '%s ' "$(date '+%F %T')"
  free -m | awk '/^Mem:/{printf "ram_used=%s ram_avail=%s ", $3, $7} /^Swap:/{printf "swap_used=%s ", $3}'
  ps -eo rss,comm --sort=-rss | awk 'NR>1 && NR<=4 {printf "%s=%dMB ", $2, $1/1024}'
  echo
} >> "$LOG"
tail -n 14400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
