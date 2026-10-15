#!/bin/bash
# Upload the bot's record (~/midfreq/runtime/live_sessions) to the hosted dashboard's bucket.
# Run by cron every 2 minutes (ops/vm/crontab.txt); the dashboard mirrors the bucket.
#
# It uses its own gcloud configuration (CLOUDSDK_CONFIG) logged in as the uploader service
# account, which may only read and write objects in that one bucket, so the VM user's own
# gcloud login is never touched. Nothing is ever deleted from the bucket.
#
# Configured at the cutover (ops/README.md): ~/midfreq/secrets/uploader.json (the account's key,
# mode 600) and ~/midfreq/secrets/bucket (one line, gs://<bucket>/live_sessions). Without them
# this does nothing.

set -euo pipefail
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
SRC="$HOME_DIR/runtime/live_sessions"
SECRETS="$HOME_DIR/secrets"
[ -f "$SECRETS/uploader.json" ] && [ -f "$SECRETS/bucket" ] && [ -d "$SRC" ] || exit 0

export CLOUDSDK_CONFIG="$SECRETS/gcloud"
if [ ! -f "$CLOUDSDK_CONFIG/.activated" ]; then
    mkdir -p "$CLOUDSDK_CONFIG"
    gcloud auth activate-service-account --key-file "$SECRETS/uploader.json" --quiet
    touch "$CLOUDSDK_CONFIG/.activated"
fi
gcloud storage rsync "$SRC" "$(head -1 "$SECRETS/bucket")" --quiet --exclude "(^|/)\\."   # skip temporary files (snapshots write .broker_*.tmp first)
